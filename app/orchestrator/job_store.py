import json
import logging
from typing import Any

from app.config import settings
from app.orchestrator.database import Database
from app.pipeline.events import job_events
from app.pipeline.stages import JobStatus

logger = logging.getLogger(__name__)

LEGACY_QUEUED = "pending"
"""What earlier versions wrote for a job waiting in the queue."""

ACTIVE_STATUSES = ("queued", LEGACY_QUEUED, "running", "processing", "scraping", "analyzing")
"""Every spelling of "not finished" that has ever been written.

At startup none of these can be true: the queue and the workers live in this
process, so a job left in any of them belongs to a process that is gone.
"""

LEGACY_COMPLETE = "complete"
"""What earlier versions wrote instead of ``JobStatus.COMPLETED``.

Rows with this value are still in deployed databases, and every reader - the
SSE terminal check, the download guards, the frontend's status union - only
knows ``completed``. Normalising on the way out means no migration and one
spelling everywhere above this line.
"""


def _normalise(row: dict[str, Any] | None) -> dict[str, Any] | None:
    """Map legacy status spellings onto the canonical ones."""
    if row is not None:
        if row.get("status") == LEGACY_COMPLETE:
            row["status"] = str(JobStatus.COMPLETED)
        elif row.get("status") == LEGACY_QUEUED:
            row["status"] = str(JobStatus.QUEUED)
    return row


class JobStore:
    def __init__(self, db_path: str = "") -> None:
        self._db_path = db_path or settings.jobs_db
        self._db = Database()

    async def init(self) -> None:
        await self._db.init()

    async def close(self) -> None:
        await self._db.close()

    async def create_job(self, job_id: str, url: str, email: str) -> dict[str, str]:
        await self._db.execute(
            "INSERT INTO jobs (job_id, url, email, status) VALUES (?, ?, ?, ?)",
            job_id, url, email, JobStatus.QUEUED,
        )
        await self._db.commit()
        job_events.notify(job_id)
        return {"job_id": job_id, "url": url, "email": email, "status": JobStatus.QUEUED}

    async def get_job(self, job_id: str) -> dict[str, str] | None:
        row = await self._db.fetchrow("SELECT * FROM jobs WHERE job_id = ?", job_id)
        return _normalise(row)

    async def delete_job(self, job_id: str) -> None:
        """Remove a job row. Used to undo a submission the queue refused."""
        await self._db.execute("DELETE FROM jobs WHERE job_id = ?", job_id)
        await self._db.commit()

    async def update_status(
        self,
        job_id: str,
        status: str,
        error: str | None = None,
    ) -> None:
        await self._db.execute(
            f"UPDATE jobs SET status = ?, error = ?, updated_at = {self._db.now_expr} WHERE job_id = ?",
            status, error, job_id,
        )
        await self._db.commit()
        job_events.notify(job_id)

    async def requeue_for_retry(self, job_id: str) -> None:
        """Put a failed or interrupted job back in the queue, counting the retry."""
        await self._db.execute(
            f"UPDATE jobs SET status = ?, error = NULL, retries = retries + 1, "
            f"updated_at = {self._db.now_expr} WHERE job_id = ?",
            JobStatus.QUEUED, job_id,
        )
        await self._db.commit()
        job_events.notify(job_id)

    async def complete_job(
        self,
        job_id: str,
        artifact_json: str | None = None,
        pdf_path: str | None = None,
        docx_path: str | None = None,
        status: str = JobStatus.COMPLETED,
    ) -> None:
        await self._db.execute(
            f"UPDATE jobs SET status = ?, artifact_json = ?, "
            f"pdf_path = ?, docx_path = ?, "
            f"updated_at = {self._db.now_expr} WHERE job_id = ?",
            status, artifact_json, pdf_path, docx_path, job_id,
        )
        await self._db.commit()
        job_events.notify(job_id)

    async def create_brief(
        self,
        job_id: str,
        session_id: str,
        data: dict[str, str],
    ) -> dict[str, object]:
        cols = ", ".join(data.keys())
        placeholders = ", ".join(["?"] * len(data))
        values = list(data.values())

        if self._db.is_pg:
            set_clause = ", ".join(f"{k} = EXCLUDED.{k}" for k in data)
            sql = (
                f"INSERT INTO briefs (job_id, session_id, {cols}) "
                f"VALUES ($1, $2, {', '.join(f'${i+3}' for i in range(len(data)))}) "
                f"ON CONFLICT (job_id, session_id) DO UPDATE SET {set_clause}, "
                f"updated_at = CURRENT_TIMESTAMP"
            )
            async with self._db._pool.acquire() as conn:
                await conn.execute(sql, job_id, session_id, *values)
        else:
            sql = f"INSERT OR REPLACE INTO briefs (job_id, session_id, {cols}) VALUES (?, ?, {placeholders})"
            await self._db.execute(sql, job_id, session_id, *values)
            await self._db.commit()

        return await self.get_brief(job_id) or {}

    async def get_brief(self, job_id: str) -> dict[str, object] | None:
        return await self._db.fetchrow(
            "SELECT * FROM briefs WHERE job_id = ?", job_id,
        )

    async def delete_brief(self, job_id: str) -> bool:
        affected = await self._db.execute(
            "DELETE FROM briefs WHERE job_id = ?", job_id,
        )
        await self._db.commit()
        return affected > 0

    # --- Stage rows --------------------------------------------------------
    # One row per (job, stage), updated in place. Replaces the read-modify-write
    # of the jobs.events JSON blob, which dropped events under concurrency. The
    # column itself stays so a rollback can still read old jobs.

    async def start_stage(self, job_id: str, job_kind: str, stage: str, seq: int) -> None:
        now = self._db.now_expr
        await self._db.execute(
            f"INSERT INTO job_stages (job_id, job_kind, stage, seq, status, started_at, attempts) "
            f"VALUES (?, ?, ?, ?, 'running', {now}, 0) "
            f"ON CONFLICT (job_id, stage) DO UPDATE SET "
            f"status = 'running', started_at = {now}, ended_at = NULL, duration_ms = NULL, "
            f"error = NULL, error_kind = NULL, updated_at = {now}",
            job_id, job_kind, stage, seq,
        )
        await self._db.commit()
        job_events.notify(job_id)

    async def finish_stage(
        self,
        job_id: str,
        stage: str,
        status: str,
        *,
        model: str | None = None,
        prompt_version: str | None = None,
        attempts: int = 0,
        duration_ms: int | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        completeness: float | None = None,
        confidence: float | None = None,
        confidence_before_retry: float | None = None,
        retry_confidence: float | None = None,
        critic_note: str | None = None,
        warnings: list[str] | None = None,
        artifact_json: str | None = None,
        error: str | None = None,
        error_kind: str | None = None,
    ) -> None:
        now = self._db.now_expr
        await self._db.execute(
            f"UPDATE job_stages SET status = ?, ended_at = {now}, duration_ms = ?, "
            f"model = ?, prompt_version = ?, attempts = ?, prompt_tokens = ?, "
            f"completion_tokens = ?, completeness = ?, confidence = ?, "
            f"confidence_before_retry = ?, retry_confidence = ?, critic_note = ?, "
            f"warnings = ?, artifact_json = ?, error = ?, error_kind = ?, updated_at = {now} "
            f"WHERE job_id = ? AND stage = ?",
            status, duration_ms, model, prompt_version, attempts, prompt_tokens,
            completion_tokens, completeness, confidence,
            confidence_before_retry, retry_confidence, critic_note,
            json.dumps(warnings or []), artifact_json, error, error_kind,
            job_id, stage,
        )
        await self._db.commit()
        job_events.notify(job_id)

    async def get_stages(self, job_id: str) -> list[dict[str, Any]]:
        return await self._db.fetch(
            "SELECT * FROM job_stages WHERE job_id = ? ORDER BY seq", job_id,
        )

    async def get_stage_artifact(self, job_id: str, stage: str) -> str | None:
        row = await self._db.fetchrow(
            "SELECT artifact_json FROM job_stages WHERE job_id = ? AND stage = ?", job_id, stage,
        )
        return row.get("artifact_json") if row else None

    async def recent_stage_durations(self, stage: str, model: str, limit: int = 20) -> list[int]:
        """Durations of recent successful runs, newest first. Powers the ETA.

        Mechanical stages (scrape, render, deliver) record no model, which is
        stored as NULL. They are asked for with ``model=""``, and ``model = ''``
        never matches NULL - so those stages never found their own history and
        the estimate vanished once only they were left to run.
        """
        model_clause = "model IS NULL" if not model else "model = ?"
        args: tuple[Any, ...] = (stage, limit) if not model else (stage, model, limit)
        rows = await self._db.fetch(
            f"SELECT duration_ms FROM job_stages WHERE stage = ? AND {model_clause} "
            "AND status = 'succeeded' AND duration_ms IS NOT NULL "
            "ORDER BY id DESC LIMIT ?",
            *args,
        )
        return [int(r["duration_ms"]) for r in rows]

    async def mark_interrupted_jobs(self) -> int:
        """Settle jobs a previous process left unfinished. Called at startup.

        Queued jobs count too. The queue is in memory, so a job waiting in it
        when the process stopped will never be picked up; left alone it read
        "queued" for ever while every open page polled it.
        """
        active = ", ".join(f"'{s}'" for s in ACTIVE_STATUSES)
        message = "'The server restarted before this job finished'"
        affected = await self._db.execute(
            f"UPDATE jobs SET status = 'interrupted', error = {message}, "
            f"updated_at = {self._db.now_expr} WHERE status IN ({active})"
        )
        await self._db.execute(
            f"UPDATE job_stages SET status = 'failed', error_kind = 'internal', "
            f"error = 'Interrupted by server restart', updated_at = {self._db.now_expr} "
            f"WHERE status = 'running'"
        )
        affected += await self._db.execute(
            f"UPDATE market_research_jobs SET status = 'interrupted', error = {message}, "
            f"updated_at = {self._db.now_expr} WHERE status IN ({active})"
        )
        await self._db.commit()
        job_events.notify_all()
        return affected

    async def record_delivery(
        self,
        job_id: str,
        provider: str,
        to_email: str,
        status: str,
        attempts: int,
        message_id: str | None = None,
        last_error: str | None = None,
    ) -> None:
        """Append a delivery attempt so failures are visible after the fact."""
        await self._db.execute(
            "INSERT INTO deliveries (job_id, provider, to_email, status, attempts, "
            "message_id, last_error) VALUES (?, ?, ?, ?, ?, ?, ?)",
            job_id, provider, to_email, status, attempts, message_id, last_error,
        )
        await self._db.commit()

    async def get_deliveries(self, job_id: str) -> list[dict[str, Any]]:
        return await self._db.fetch(
            "SELECT * FROM deliveries WHERE job_id = ? ORDER BY id", job_id,
        )

    async def count_jobs_for_recipient(self, email: str, hours: int) -> int:
        """BI jobs addressed to ``email`` created in the last ``hours``."""
        row = await self._db.fetchrow(
            f"SELECT COUNT(*) AS n FROM jobs WHERE LOWER(email) = LOWER(?) "
            f"AND created_at >= {self._db.ago_expr(hours)}",
            email,
        )
        return int(row["n"]) if row else 0

    async def count_deliveries_to_recipient(self, email: str, hours: int) -> int:
        """Send attempts to ``email`` in the last ``hours``, across every job."""
        row = await self._db.fetchrow(
            f"SELECT COUNT(*) AS n FROM deliveries WHERE LOWER(to_email) = LOWER(?) "
            f"AND created_at >= {self._db.ago_expr(hours)}",
            email,
        )
        return int(row["n"]) if row else 0

    async def create_market_research_job(self, job_id: str, market_query: str) -> dict[str, str]:
        await self._db.execute(
            "INSERT INTO market_research_jobs (job_id, market_query, status) VALUES (?, ?, ?)",
            job_id, market_query, JobStatus.QUEUED,
        )
        await self._db.commit()
        job_events.notify(job_id)
        return {"job_id": job_id, "market_query": market_query, "status": JobStatus.QUEUED}

    async def get_market_research_job(self, job_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchrow(
            "SELECT * FROM market_research_jobs WHERE job_id = ?", job_id,
        )
        return _normalise(row)

    async def delete_market_research_job(self, job_id: str) -> None:
        """Remove a job row. Used to undo a submission the queue refused."""
        await self._db.execute("DELETE FROM market_research_jobs WHERE job_id = ?", job_id)
        await self._db.commit()

    async def update_market_research_status(self, job_id: str, status: str, error: str | None = None) -> None:
        await self._db.execute(
            f"UPDATE market_research_jobs SET status = ?, error = ?, updated_at = {self._db.now_expr} "
            "WHERE job_id = ?",
            status, error, job_id,
        )
        await self._db.commit()
        job_events.notify(job_id)

    async def requeue_market_research_for_retry(self, job_id: str) -> None:
        await self._db.execute(
            f"UPDATE market_research_jobs SET status = ?, error = NULL, retries = retries + 1, "
            f"updated_at = {self._db.now_expr} WHERE job_id = ?",
            JobStatus.QUEUED, job_id,
        )
        await self._db.commit()
        job_events.notify(job_id)

    async def complete_market_research(
        self,
        job_id: str,
        result_json: str,
        pdf_path: str | None = None,
        docx_path: str | None = None,
        status: str = JobStatus.COMPLETED,
        error: str | None = None,
    ) -> None:
        await self._db.execute(
            "UPDATE market_research_jobs SET status = ?, error = ?, result_json = ?, "
            "pdf_path = ?, docx_path = ?, "
            f"updated_at = {self._db.now_expr} WHERE job_id = ?",
            status, error, result_json, pdf_path, docx_path, job_id,
        )
        await self._db.commit()
        job_events.notify(job_id)
