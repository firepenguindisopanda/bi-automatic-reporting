import logging
from typing import Any

import aiosqlite

from app.config import settings
from app.orchestrator.schema import BACKFILL_COLUMNS, ddl

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
        else:
            self._conn = await aiosqlite.connect(self._path)
            self._conn.row_factory = aiosqlite.Row
            logger.info("Connected to SQLite at %s", self._path)

        await self._migrate()

    async def _migrate(self) -> None:
        """Create every table and index. Idempotent - runs on each startup."""
        for statement in ddl(self._pg):
            await self._exec(statement)
        for table, column, coltype in BACKFILL_COLUMNS:
            try:
                await self._exec(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
            except Exception:
                pass  # column already present - expected on every run after the first
        await self.commit()

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

    @property
    def now_expr(self) -> str:
        """SQL expression for the current timestamp in the active dialect."""
        return "CURRENT_TIMESTAMP" if self._pg else "(datetime('now'))"

    async def close(self) -> None:
        if self._pg and self._pool is not None:
            await self._pool.close()
            self._pool = None
        elif self._conn is not None:
            await self._conn.close()
            self._conn = None
        self._pg = False
