"""Tests for the bounded job queue."""

import asyncio

import pytest

from app.config import settings
from app.orchestrator import runner as job_runner


@pytest.fixture(autouse=True)
async def _clean_queue():
    await job_runner.stop_workers()
    yield
    await job_runner.stop_workers()


class TestSequencing:
    async def test_concurrency_one_runs_jobs_sequentially(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "job_concurrency", 1)
        overlap = {"max": 0, "current": 0}
        order: list[str] = []

        async def job(name: str) -> None:
            overlap["current"] += 1
            overlap["max"] = max(overlap["max"], overlap["current"])
            await asyncio.sleep(0.02)
            order.append(name)
            overlap["current"] -= 1

        for name in ("a", "b", "c"):
            await job_runner.enqueue(name, job(name))

        for _ in range(200):
            if len(order) == 3:
                break
            await asyncio.sleep(0.01)

        assert order == ["a", "b", "c"]
        assert overlap["max"] == 1, "jobs overlapped despite concurrency 1"

    async def test_higher_concurrency_overlaps(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "job_concurrency", 3)
        overlap = {"max": 0, "current": 0}
        done = asyncio.Event()
        finished = []

        async def job() -> None:
            overlap["current"] += 1
            overlap["max"] = max(overlap["max"], overlap["current"])
            await asyncio.sleep(0.05)
            overlap["current"] -= 1
            finished.append(1)
            if len(finished) == 3:
                done.set()

        for i in range(3):
            await job_runner.enqueue(f"job-{i}", job())
        await asyncio.wait_for(done.wait(), timeout=5)
        assert overlap["max"] > 1


class TestBackpressure:
    async def test_queue_reports_position(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "job_concurrency", 1)
        release = asyncio.Event()

        async def blocker() -> None:
            await release.wait()

        async def quick() -> None:
            return None

        await job_runner.enqueue("running", blocker())
        await asyncio.sleep(0.01)  # let the worker pick it up
        await job_runner.enqueue("waiting-1", quick())
        position = job_runner.queue_position("waiting-1")
        assert position is not None
        release.set()

    async def test_full_queue_is_rejected(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "job_concurrency", 1)
        monkeypatch.setattr(settings, "job_queue_max", 2)
        release = asyncio.Event()

        async def blocker() -> None:
            await release.wait()

        async def quick() -> None:
            return None

        await job_runner.enqueue("running", blocker())
        await asyncio.sleep(0.01)
        await job_runner.enqueue("q1", quick())
        await job_runner.enqueue("q2", quick())

        with pytest.raises(job_runner.QueueFullError):
            await job_runner.enqueue("q3", quick())
        release.set()

    async def test_rejected_coroutine_is_closed(self, monkeypatch) -> None:
        """A rejected job must not leave an un-awaited coroutine warning."""
        monkeypatch.setattr(settings, "job_concurrency", 1)
        monkeypatch.setattr(settings, "job_queue_max", 1)
        release = asyncio.Event()

        async def blocker() -> None:
            await release.wait()

        async def quick() -> None:
            return None

        await job_runner.enqueue("running", blocker())
        await asyncio.sleep(0.01)
        await job_runner.enqueue("q1", quick())
        rejected = quick()
        with pytest.raises(job_runner.QueueFullError):
            await job_runner.enqueue("q2", rejected)
        assert asyncio.iscoroutine(rejected)
        release.set()


class TestResilience:
    async def test_failing_job_does_not_kill_the_worker(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "job_concurrency", 1)
        survived = asyncio.Event()

        async def boom() -> None:
            raise RuntimeError("job exploded")

        async def after() -> None:
            survived.set()

        await job_runner.enqueue("boom", boom())
        await job_runner.enqueue("after", after())
        await asyncio.wait_for(survived.wait(), timeout=5)

    async def test_workers_are_held_not_garbage_collected(self, monkeypatch) -> None:
        """The old code discarded create_task handles, so jobs could vanish."""
        monkeypatch.setattr(settings, "job_concurrency", 2)
        await job_runner.start_workers()
        assert len(job_runner._workers) == 2
        assert all(not t.done() for t in job_runner._workers)

    async def test_start_workers_is_idempotent(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "job_concurrency", 2)
        await job_runner.start_workers()
        await job_runner.start_workers()
        assert len(job_runner._workers) == 2

    async def test_stats_reports_state(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "job_concurrency", 2)
        await job_runner.start_workers()
        stats = job_runner.stats()
        assert stats["workers"] == 2
        assert stats["capacity"] == settings.job_queue_max
