"""Tests for job status spelling and queue back-pressure.

Two bugs live here, both found by running the app rather than reading it:

- Market research wrote ``'complete'`` while every reader - the SSE terminal
  check, the download guards, the frontend's status union - knew only
  ``'completed'``. A finished research job showed a dead end: no downloads,
  no link to the findings, and a progress stream that never closed.
- A full queue raised ``QueueFullError`` that nobody caught, so back-pressure
  surfaced as a 500 and left the job row behind at 'pending' forever.
"""

import pytest

import app.orchestrator.runner as job_runner
from app.orchestrator.bi_pipeline import BIPipeline
from app.orchestrator.job_store import JobStore
from app.orchestrator.market_research import MarketResearchService
from app.pipeline.stages import TERMINAL_JOB_STATUSES, JobStatus


@pytest.fixture
async def store() -> JobStore:
    s = JobStore()
    await s.init()
    return s


class TestCompletedSpelling:
    async def test_finished_research_uses_the_canonical_status(self, store: JobStore) -> None:
        await store.create_market_research_job("j1", "Developer tools")
        await store.complete_market_research("j1", "{}", pdf_path="a.pdf", docx_path="a.docx")
        row = await store.get_market_research_job("j1")
        assert row is not None
        assert row["status"] == JobStatus.COMPLETED

    async def test_finished_research_is_terminal(self, store: JobStore) -> None:
        """The SSE stream closes on this check; 'complete' never matched it."""
        await store.create_market_research_job("j2", "Developer tools")
        await store.complete_market_research("j2", "{}")
        row = await store.get_market_research_job("j2")
        assert row is not None
        assert row["status"] in TERMINAL_JOB_STATUSES

    async def test_legacy_research_rows_are_normalised(self, store: JobStore) -> None:
        """Rows written by an earlier version are still in deployed databases."""
        await store.create_market_research_job("j3", "q")
        await store._db.execute(
            "UPDATE market_research_jobs SET status = 'complete' WHERE job_id = ?", "j3"
        )
        await store._db.commit()
        row = await store.get_market_research_job("j3")
        assert row is not None
        assert row["status"] == JobStatus.COMPLETED

    async def test_legacy_bi_rows_are_normalised(self, store: JobStore) -> None:
        await store.create_job("j4", "https://example.com", "a@b.co")
        await store._db.execute("UPDATE jobs SET status = 'complete' WHERE job_id = ?", "j4")
        await store._db.commit()
        row = await store.get_job("j4")
        assert row is not None
        assert row["status"] == JobStatus.COMPLETED

    async def test_completing_a_bi_job_defaults_to_the_canonical_status(
        self, store: JobStore
    ) -> None:
        await store.create_job("j5", "https://example.com", "a@b.co")
        await store.complete_job("j5", pdf_path="a.pdf")
        row = await store.get_job("j5")
        assert row is not None
        assert row["status"] == JobStatus.COMPLETED


class TestQueueFullRollback:
    """A refused submission must not leave a job nobody will ever run."""

    @pytest.fixture
    def refuse(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def full(job_id: str, coro) -> int:  # noqa: ANN001
            coro.close()
            raise job_runner.QueueFullError("The job queue is full. Try again shortly.")

        monkeypatch.setattr(job_runner, "enqueue", full)

    async def test_bi_submission_leaves_no_row(self, refuse: None) -> None:
        pipeline = BIPipeline()
        await pipeline.init()
        with pytest.raises(job_runner.QueueFullError):
            await pipeline.submit(url="https://example.com", email="a@b.co")
        assert await pipeline._store._db.fetch("SELECT job_id FROM jobs") == []
        await pipeline.close()

    async def test_research_submission_leaves_no_row(self, refuse: None) -> None:
        service = MarketResearchService()
        await service._store.init()
        with pytest.raises(job_runner.QueueFullError):
            await service.submit("Developer tools")
        rows = await service._store._db.fetch("SELECT job_id FROM market_research_jobs")
        assert rows == []
        await service._store.close()
