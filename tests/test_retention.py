"""Finished jobs and their files are deleted after the retention window."""

import os
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from app.config import settings
from app.orchestrator.job_store import JobStore
from app.orchestrator.retention import sweep
from app.pipeline.stages import JobStatus


@pytest.fixture
async def store(tmp_path, monkeypatch) -> AsyncIterator[JobStore]:
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "jobs_db", str(tmp_path / "jobs.db"))
    s = JobStore()
    await s.init()
    yield s
    await s.close()


async def _job(store: JobStore, job_id: str, status: str, reports: Path, age_days: float) -> Path:
    pdf = reports / f"{job_id}.pdf"
    pdf.write_bytes(b"%PDF")
    await store.create_job(job_id, "https://x.example.com", "u@example.com")
    await store.complete_job(job_id, pdf_path=str(pdf), docx_path=None, status=status)
    await store.start_stage(job_id, "bi", "profile", 1)
    await store._db.execute(
        f"UPDATE jobs SET updated_at = datetime('now', '-{int(age_days * 24)} hours') WHERE job_id = ?",
        job_id,
    )
    await store._db.commit()
    old = time.time() - age_days * 86400
    os.utime(pdf, (old, old))
    return pdf


class TestRetention:
    async def test_old_finished_jobs_and_their_files_go(self, store: JobStore, tmp_path: Path) -> None:
        pdf = await _job(store, "old", JobStatus.COMPLETED, tmp_path, age_days=40)
        await sweep(store, 30, tmp_path)
        assert await store.get_job("old") is None
        assert await store.get_stages("old") == []
        assert not pdf.exists()

    async def test_recent_jobs_stay(self, store: JobStore, tmp_path: Path) -> None:
        pdf = await _job(store, "new", JobStatus.COMPLETED, tmp_path, age_days=2)
        await sweep(store, 30, tmp_path)
        assert await store.get_job("new") is not None
        assert pdf.exists()

    async def test_unfinished_jobs_are_never_purged(self, store: JobStore, tmp_path: Path) -> None:
        await _job(store, "running", JobStatus.RUNNING, tmp_path, age_days=90)
        await sweep(store, 30, tmp_path)
        assert await store.get_job("running") is not None

    async def test_an_old_file_a_live_job_still_needs_is_kept(
        self, store: JobStore, tmp_path: Path
    ) -> None:
        """Resent last week, created last month: the row is recent, the file is not."""
        pdf = await _job(store, "resent", JobStatus.COMPLETED, tmp_path, age_days=2)
        old = time.time() - 60 * 86400
        os.utime(pdf, (old, old))
        await sweep(store, 30, tmp_path)
        assert pdf.exists()

    async def test_orphaned_old_files_are_swept(self, store: JobStore, tmp_path: Path) -> None:
        orphan = tmp_path / "orphan.docx"
        orphan.write_bytes(b"x")
        old = time.time() - 60 * 86400
        os.utime(orphan, (old, old))
        keep = tmp_path / "notes.txt"
        keep.write_text("not a report")
        os.utime(keep, (old, old))
        await sweep(store, 30, tmp_path)
        assert not orphan.exists()
        assert keep.exists()

    async def test_zero_days_disables_it(self, store: JobStore, tmp_path: Path) -> None:
        pdf = await _job(store, "old", JobStatus.COMPLETED, tmp_path, age_days=400)
        await sweep(store, 0, tmp_path)
        assert pdf.exists()
        assert await store.get_job("old") is not None
