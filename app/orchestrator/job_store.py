import json
import logging

import aiosqlite

from app.config import settings

logger = logging.getLogger(__name__)


class JobStore:
    def __init__(self, db_path: str = "") -> None:
        self._db_path = db_path or settings.jobs_db

    async def init(self) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    url TEXT NOT NULL,
                    email TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    error TEXT,
                    artifact_json TEXT,
                    pdf_path TEXT,
                    docx_path TEXT,
                    events TEXT DEFAULT '[]',
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS briefs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL REFERENCES jobs(job_id),
                    session_id TEXT NOT NULL,
                    client_name TEXT DEFAULT '',
                    client_country TEXT DEFAULT '',
                    client_language TEXT DEFAULT '',
                    client_website TEXT DEFAULT '',
                    client_description TEXT DEFAULT '',
                    target_audience_personas TEXT DEFAULT '',
                    brand_personality_matrix TEXT DEFAULT '',
                    unique_value_proposition TEXT DEFAULT '',
                    people_ask TEXT DEFAULT '',
                    customer_journey TEXT DEFAULT '',
                    customer_persona_trait TEXT DEFAULT '',
                    eeat_signal_integration TEXT DEFAULT '',
                    geo_tactic TEXT DEFAULT '',
                    call_to_action TEXT DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)
            await db.commit()
        logger.info("Job store initialized at %s", self._db_path)

    async def create_job(self, job_id: str, url: str, email: str) -> dict[str, str]:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT INTO jobs (job_id, url, email) VALUES (?, ?, ?)",
                (job_id, url, email),
            )
            await db.commit()
        return {"job_id": job_id, "url": url, "email": email, "status": "pending"}

    async def get_job(self, job_id: str) -> dict[str, str] | None:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM jobs WHERE job_id = ?",
                (job_id,),
            ) as cursor:
                row = await cursor.fetchone()
                if row is None:
                    return None
                return dict(row)

    async def update_status(
        self,
        job_id: str,
        status: str,
        error: str | None = None,
    ) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE jobs SET status = ?, error = ?, updated_at = datetime('now') WHERE job_id = ?",
                (status, error, job_id),
            )
            await db.commit()

    async def complete_job(
        self,
        job_id: str,
        artifact_json: str | None = None,
        pdf_path: str | None = None,
        docx_path: str | None = None,
    ) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE jobs SET status = 'complete', artifact_json = ?, "
                "pdf_path = ?, docx_path = ?, "
                "updated_at = datetime('now') WHERE job_id = ?",
                (artifact_json, pdf_path, docx_path, job_id),
            )
            await db.commit()

    async def create_brief(
        self,
        job_id: str,
        session_id: str,
        data: dict[str, str],
    ) -> dict[str, object]:
        cols = ", ".join(data.keys())
        placeholders = ", ".join(["?"] * len(data))
        values = list(data.values())
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                f"INSERT OR REPLACE INTO briefs (job_id, session_id, {cols}) VALUES (?, ?, {placeholders})",
                [job_id, session_id, *values],
            )
            await db.commit()
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM briefs WHERE job_id = ?",
                (job_id,),
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else {}

    async def get_brief(self, job_id: str) -> dict[str, object] | None:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM briefs WHERE job_id = ?",
                (job_id,),
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def delete_brief(self, job_id: str) -> bool:
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "DELETE FROM briefs WHERE job_id = ?",
                (job_id,),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def add_event(self, job_id: str, event_type: str, agent: str) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            async with db.execute(
                "SELECT events FROM jobs WHERE job_id = ?",
                (job_id,),
            ) as cursor:
                row = await cursor.fetchone()
            events = json.loads(row[0]) if row and row[0] else []
            events.append({"type": event_type, "agent": agent})
            await db.execute(
                "UPDATE jobs SET events = ?, updated_at = datetime('now') WHERE job_id = ?",
                (json.dumps(events), job_id),
            )
            await db.commit()
