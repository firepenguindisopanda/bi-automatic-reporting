import json
import logging

from app.config import settings
from app.orchestrator.database import Database

logger = logging.getLogger(__name__)


class JobStore:
    def __init__(self, db_path: str = "") -> None:
        self._db_path = db_path or settings.jobs_db
        self._db = Database()

    async def init(self) -> None:
        await self._db.init()

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
    ) -> None:
        await self._db.execute(
            "UPDATE jobs SET status = 'complete', artifact_json = ?, "
            "pdf_path = ?, docx_path = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE job_id = ?",
            artifact_json, pdf_path, docx_path, job_id,
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
            import asyncpg
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

    async def create_market_research_job(self, job_id: str, market_query: str) -> dict[str, str]:
        await self._db.execute(
            "INSERT INTO market_research_jobs (job_id, market_query) VALUES (?, ?)",
            job_id, market_query,
        )
        await self._db.commit()
        return {"job_id": job_id, "market_query": market_query, "status": "pending"}

    async def get_market_research_job(self, job_id: str) -> dict[str, str | None] | None:
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
