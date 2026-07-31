import json
import logging
from typing import Any

from app.config import settings
from app.orchestrator.database import Database

logger = logging.getLogger(__name__)


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
            "INSERT INTO jobs (job_id, url, email) VALUES (?, ?, ?)",
            job_id, url, email,
        )
        await self._db.commit()
        return {"job_id": job_id, "url": url, "email": email, "status": "pending"}

    async def get_job(self, job_id: str) -> dict[str, str] | None:
        return await self._db.fetchrow(
            "SELECT * FROM jobs WHERE job_id = ?", job_id,
        )

    async def update_status(
        self,
        job_id: str,
        status: str,
        error: str | None = None,
    ) -> None:
        await self._db.execute(
            "UPDATE jobs SET status = ?, error = ?, updated_at = CURRENT_TIMESTAMP WHERE job_id = ?",
            status, error, job_id,
        )
        await self._db.commit()

    async def complete_job(
        self,
        job_id: str,
        artifact_json: str | None = None,
        pdf_path: str | None = None,
        docx_path: str | None = None,
        status: str = "complete",
    ) -> None:
        await self._db.execute(
            f"UPDATE jobs SET status = ?, artifact_json = ?, "
            f"pdf_path = ?, docx_path = ?, "
            f"updated_at = {self._db.now_expr} WHERE job_id = ?",
            status, artifact_json, pdf_path, docx_path, job_id,
        )
        await self._db.commit()

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

    async def add_event(self, job_id: str, event_type: str, agent: str) -> None:
        row = await self._db.fetchrow(
            "SELECT events FROM jobs WHERE job_id = ?", job_id,
        )
        events = json.loads(row["events"]) if row and row.get("events") else []
        events.append({"type": event_type, "agent": agent})
        await self._db.execute(
            "UPDATE jobs SET events = ?, updated_at = CURRENT_TIMESTAMP WHERE job_id = ?",
            json.dumps(events), job_id,
        )
        await self._db.commit()

    # --- Stage rows --------------------------------------------------------
    # One row per (job, stage), updated in place. Replaces the read-modify-write
    # of the jobs.events JSON blob, which dropped events under concurrency.

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
            f"completion_tokens = ?, completeness = ?, confidence = ?, critic_note = ?, "
            f"warnings = ?, artifact_json = ?, error = ?, error_kind = ?, updated_at = {now} "
            f"WHERE job_id = ? AND stage = ?",
            status, duration_ms, model, prompt_version, attempts, prompt_tokens,
            completion_tokens, completeness, confidence, critic_note,
            json.dumps(warnings or []), artifact_json, error, error_kind,
            job_id, stage,
        )
        await self._db.commit()

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
        """Durations of recent successful runs, newest first. Powers the ETA."""
        rows = await self._db.fetch(
            "SELECT duration_ms FROM job_stages WHERE stage = ? AND model = ? "
            "AND status = 'succeeded' AND duration_ms IS NOT NULL "
            "ORDER BY id DESC LIMIT ?",
            stage, model, limit,
        )
        return [int(r["duration_ms"]) for r in rows]

    async def mark_interrupted_jobs(self) -> int:
        """Jobs left running by a crash are not running now. Called at startup."""
        affected = await self._db.execute(
            "UPDATE jobs SET status = 'interrupted', "
            "error = 'The server restarted while this job was running', "
            f"updated_at = {self._db.now_expr} WHERE status IN ('running', 'scraping', 'analyzing')"
        )
        await self._db.execute(
            f"UPDATE job_stages SET status = 'failed', error_kind = 'internal', "
            f"error = 'Interrupted by server restart', updated_at = {self._db.now_expr} "
            f"WHERE status = 'running'"
        )
        await self._db.execute(
            "UPDATE market_research_jobs SET status = 'interrupted', "
            "error = 'The server restarted while this job was running', "
            f"updated_at = {self._db.now_expr} WHERE status IN ('running', 'processing', 'pending')"
        )
        await self._db.commit()
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

    async def create_market_research_job(self, job_id: str, market_query: str) -> dict[str, str]:
        await self._db.execute(
            "INSERT INTO market_research_jobs (job_id, market_query) VALUES (?, ?)",
            job_id, market_query,
        )
        await self._db.commit()
        return {"job_id": job_id, "market_query": market_query, "status": "pending"}

    async def get_market_research_job(self, job_id: str) -> dict[str, Any] | None:
        return await self._db.fetchrow(
            "SELECT * FROM market_research_jobs WHERE job_id = ?", job_id,
        )

    async def update_market_research_status(self, job_id: str, status: str, error: str | None = None) -> None:
        await self._db.execute(
            "UPDATE market_research_jobs SET status = ?, error = ?, updated_at = CURRENT_TIMESTAMP WHERE job_id = ?",
            status, error, job_id,
        )
        await self._db.commit()

    async def complete_market_research(
        self,
        job_id: str,
        result_json: str,
        pdf_path: str | None = None,
        docx_path: str | None = None,
    ) -> None:
        await self._db.execute(
            "UPDATE market_research_jobs SET status = 'complete', result_json = ?, "
            "pdf_path = ?, docx_path = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE job_id = ?",
            result_json, pdf_path, docx_path, job_id,
        )
        await self._db.commit()
