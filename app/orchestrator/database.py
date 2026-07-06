import logging
from typing import Any

import aiosqlite

from app.config import settings

logger = logging.getLogger(__name__)


def _pg(sql: str) -> str:
    i = 0
    parts: list[str] = []
    for c in sql:
        if c == "?":
            i += 1
            parts.append(f"${i}")
        else:
            parts.append(c)
    return "".join(parts)


class Database:
    def __init__(self) -> None:
        self._url = settings.database_url
        self._path = settings.jobs_db
        self._pool: Any = None
        self._conn: Any = None
        self._pg = False

    async def init(self) -> None:
        if self._pool is not None or self._conn is not None:
            return
        if self._url:
            import asyncpg
            self._pg = True
            self._pool = await asyncpg.create_pool(
                self._url, min_size=1, max_size=5,
                statement_cache_size=0,
            )
            logger.info("Connected to PostgreSQL via %s", self._url.split("@")[-1].split("?")[0])
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """CREATE TABLE IF NOT EXISTS jobs (
                        job_id TEXT PRIMARY KEY,
                        url TEXT NOT NULL,
                        email TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'pending',
                        error TEXT,
                        artifact_json TEXT,
                        pdf_path TEXT,
                        docx_path TEXT,
                        events TEXT DEFAULT '[]',
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )"""
                )
                await conn.execute(
                    """CREATE TABLE IF NOT EXISTS briefs (
                        id SERIAL PRIMARY KEY,
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
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(job_id, session_id)
                    )"""
                )
                await conn.execute(
                    """CREATE TABLE IF NOT EXISTS market_research_jobs (
                        job_id TEXT PRIMARY KEY,
                        market_query TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'pending',
                        result_json TEXT,
                        error TEXT,
                        pdf_path TEXT,
                        docx_path TEXT,
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )"""
                )
                for col in ("pdf_path", "docx_path"):
                    try:
                        await conn.execute(
                            f"ALTER TABLE market_research_jobs ADD COLUMN {col} TEXT"
                        )
                    except Exception:
                        pass
        else:
            self._conn = await aiosqlite.connect(self._path)
            self._conn.row_factory = aiosqlite.Row
            logger.info("Connected to SQLite at %s", self._path)
            await self._exec(
                """CREATE TABLE IF NOT EXISTS jobs (
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
                )"""
            )
            await self._exec(
                """CREATE TABLE IF NOT EXISTS briefs (
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
                )"""
            )
            await self._exec(
                """CREATE TABLE IF NOT EXISTS market_research_jobs (
                    job_id TEXT PRIMARY KEY,
                    market_query TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    result_json TEXT,
                    error TEXT,
                    pdf_path TEXT,
                    docx_path TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                )"""
            )
            for col in ("pdf_path", "docx_path"):
                try:
                    await self._exec(
                        f"ALTER TABLE market_research_jobs ADD COLUMN {col} TEXT"
                    )
                except Exception:
                    pass

    async def _exec(self, sql: str, *args: Any) -> Any:
        if self._pg:
            sql = _pg(sql)
            async with self._pool.acquire() as conn:
                return await conn.execute(sql, *args)
        cursor = await self._conn.execute(sql, args or None)
        return cursor

    async def execute(self, sql: str, *args: Any) -> int:
        if self._pg:
            async with self._pool.acquire() as conn:
                result = await conn.execute(_pg(sql), *args)
            parts = str(result).split()
            if len(parts) >= 2:
                try:
                    return int(parts[-1])
                except (ValueError, IndexError):
                    pass
            return 0
        cursor = await self._conn.execute(sql, args or None)
        return cursor.rowcount if hasattr(cursor, "rowcount") else 0

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        if self._pg:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(_pg(sql), *args)
            return dict(row) if row else None
        cursor = await self._conn.execute(sql, args or None)
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        if self._pg:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(_pg(sql), *args)
            return [dict(r) for r in rows]
        cursor = await self._conn.execute(sql, args or None)
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def commit(self) -> None:
        if not self._pg:
            await self._conn.commit()

    @property
    def is_pg(self) -> bool:
        return self._pg

    async def close(self) -> None:
        if self._pg:
            await self._pool.close()
        else:
            await self._conn.close()
