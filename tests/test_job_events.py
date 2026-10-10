"""The SSE stream sleeps until its job changes, rather than polling once a second."""

import asyncio

from app.api.progress import stream_progress
from app.models.bi import JobProgress
from app.pipeline.events import JobEvents


class TestJobEvents:
    async def test_wait_returns_at_once_when_already_behind(self) -> None:
        events = JobEvents()
        events.notify("j")
        assert await events.wait("j", seen=0, timeout=10) == 1

    async def test_notify_wakes_a_waiter(self) -> None:
        events = JobEvents()
        waiter = asyncio.create_task(events.wait("j", seen=0, timeout=10))
        await asyncio.sleep(0)
        events.notify("j")
        assert await asyncio.wait_for(waiter, timeout=1) == 1

    async def test_every_waiter_on_a_job_wakes(self) -> None:
        events = JobEvents()
        waiters = [asyncio.create_task(events.wait("j", seen=0, timeout=10)) for _ in range(3)]
        await asyncio.sleep(0)
        events.notify("j")
        assert await asyncio.wait_for(asyncio.gather(*waiters), timeout=1) == [1, 1, 1]

    async def test_other_jobs_do_not_wake_it(self) -> None:
        events = JobEvents()
        assert await events.wait("j", seen=0, timeout=0.05) == 0
        events.notify("other")
        assert await events.wait("j", seen=0, timeout=0.05) == 0

    async def test_tracking_is_bounded(self, monkeypatch) -> None:
        import app.pipeline.events as module

        monkeypatch.setattr(module, "MAX_TRACKED_JOBS", 3)
        events = JobEvents()
        for i in range(10):
            events.notify(f"j{i}")
        assert len(events._versions) == 3
        assert events.version("j9") == 1
        assert events.version("j0") == 0


class TestStreamWakesOnChange:
    async def test_a_notification_is_what_moves_the_stream(self) -> None:
        events = JobEvents()
        state = {"status": "running"}
        reads = {"n": 0}

        async def load() -> JobProgress:
            reads["n"] += 1
            return JobProgress(job_id="j", status=state["status"])

        seen = events.version("j")

        async def wait_for_change(timeout: float) -> None:
            nonlocal seen
            seen = await events.wait("j", seen, timeout)

        async def finish_soon() -> None:
            await asyncio.sleep(0.05)
            state["status"] = "completed"
            events.notify("j")

        finisher = asyncio.create_task(finish_soon())
        chunks = [c async for c in stream_progress(load, wait_for_change)]
        await finisher

        assert chunks[-1].startswith("event: done")
        # One read up front, one after the notification. A one-second poll
        # would have read again and again while nothing changed.
        assert reads["n"] == 2
