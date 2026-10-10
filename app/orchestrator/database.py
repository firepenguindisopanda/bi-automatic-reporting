import logging
from typing import Any

import aiosqlite

from app.config import settings
from app.orchestrator.schema import MIGRATIONS, migrations_table

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
        """Bring the schema up to date. Safe on every startup; raises on failure."""
        await self._exec(migrations_table(self._pg))
        row = await self.fetchrow("SELECT MAX(version) AS v FROM schema_migrations")
        current = int(row["v"]) if row and row.get("v") is not None else 0

        for migration in MIGRATIONS:
            if migration.version <= current:
                continue
            for statement in migration.statements(self._pg):
                await self._exec(statement)
            for table, column, coltype in migration.columns:
                if column not in await self.columns(table):
                    await self._exec(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
            await self._exec(
                "INSERT INTO schema_migrations (version, description) VALUES (?, ?)",
                migration.version, migration.description,
            )
            await self.commit()
            logger.info("Applied schema migration %d: %s", migration.version, migration.description)

    async def columns(self, table: str) -> set[str]:
        """Column names of ``table`` in the active dialect."""
        if self._pg:
            rows = await self.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = ?",
                table,
            )
            return {str(r["column_name"]) for r in rows}
        rows = await self.fetch(f"PRAGMA table_info({table})")
        return {str(r["name"]) for r in rows}

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

    def ago_expr(self, hours: int) -> str:
        """SQL for "``hours`` ago", comparable with this dialect's timestamp columns.

        Computed by the database rather than passed in, so it is measured on the
        same clock and in the same representation that wrote ``created_at``.
        """
        hours = int(hours)
        if self._pg:
            return f"(CURRENT_TIMESTAMP - INTERVAL '{hours} hours')"
        return f"(datetime('now', '-{hours} hours'))"

    async def close(self) -> None:
        if self._pg and self._pool is not None:
            await self._pool.close()
            self._pool = None
        elif self._conn is not None:
            await self._conn.close()
            self._conn = None
        self._pg = False
