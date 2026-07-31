"""Tests for schema creation and dialect rendering."""

from collections.abc import AsyncIterator

import pytest

from app.config import settings
from app.orchestrator.database import Database
from app.orchestrator.schema import ddl

EXPECTED_TABLES = {"jobs", "briefs", "market_research_jobs", "job_stages", "deliveries"}


@pytest.fixture
async def db(tmp_path, monkeypatch) -> AsyncIterator[Database]:
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "jobs_db", str(tmp_path / "test.db"))
    database = Database()
    await database.init()
    yield database
    await database.close()


async def _table_names(db: Database) -> set[str]:
    rows = await db.fetch("SELECT name FROM sqlite_master WHERE type = 'table'")
    return {r["name"] for r in rows}


async def _columns(db: Database, table: str) -> set[str]:
    rows = await db.fetch(f"PRAGMA table_info({table})")
    return {r["name"] for r in rows}


class TestSchema:
    async def test_creates_every_table(self, db: Database) -> None:
        assert EXPECTED_TABLES <= await _table_names(db)

    async def test_init_is_idempotent(self, db: Database) -> None:
        # init() short-circuits on an open connection, so exercise the migration directly.
        await db._migrate()
        await db._migrate()
        assert EXPECTED_TABLES <= await _table_names(db)

    async def test_job_stages_columns(self, db: Database) -> None:
        assert await _columns(db, "job_stages") >= {
            "job_id", "job_kind", "stage", "seq", "status",
            "started_at", "ended_at", "duration_ms", "model", "prompt_version",
            "attempts", "prompt_tokens", "completion_tokens",
            "completeness", "confidence", "critic_note", "warnings",
            "artifact_json", "error", "error_kind",
        }

    async def test_deliveries_columns(self, db: Database) -> None:
        assert await _columns(db, "deliveries") >= {
            "job_id", "provider", "to_email", "status",
            "attempts", "message_id", "last_error",
        }

    async def test_one_stage_row_per_job_and_stage(self, db: Database) -> None:
        await db.execute(
            "INSERT INTO job_stages (job_id, stage, seq, status) VALUES (?, ?, ?, ?)",
            "job1", "profile", 1, "running",
        )
        await db.commit()
        with pytest.raises(Exception, match="UNIQUE"):
            await db.execute(
                "INSERT INTO job_stages (job_id, stage, seq, status) VALUES (?, ?, ?, ?)",
                "job1", "profile", 1, "running",
            )

    async def test_legacy_events_column_retained(self, db: Database) -> None:
        # Kept for one release so a rollback can still read old jobs.
        assert "events" in await _columns(db, "jobs")


class TestDialectRendering:
    def test_same_statement_count(self) -> None:
        assert len(ddl(is_pg=True)) == len(ddl(is_pg=False))

    def test_postgres_uses_serial_and_timestamps(self) -> None:
        sql = "\n".join(ddl(is_pg=True))
        assert "SERIAL PRIMARY KEY" in sql
        assert "AUTOINCREMENT" not in sql
        assert "datetime('now')" not in sql

    def test_sqlite_uses_autoincrement_and_text_timestamps(self) -> None:
        sql = "\n".join(ddl(is_pg=False))
        assert "INTEGER PRIMARY KEY AUTOINCREMENT" in sql
        assert "SERIAL" not in sql
        assert "datetime('now')" in sql

    def test_every_statement_is_idempotent(self) -> None:
        for statement in ddl(is_pg=True) + ddl(is_pg=False):
            assert "IF NOT EXISTS" in statement
