"""Tests for schema creation and dialect rendering."""

from collections.abc import AsyncIterator

import pytest

from app.config import settings
from app.orchestrator.database import Database
from app.orchestrator.job_store import JobStore
from app.orchestrator.schema import MIGRATIONS, SCHEMA_VERSION, Migration, ddl

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
            "confidence_before_retry", "retry_confidence",
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

    async def test_arbitration_columns_backfill_onto_a_legacy_database(
        self, tmp_path, monkeypatch
    ) -> None:
        """A database created before M2 must gain the columns, not break.

        The legacy schema is derived from the live DDL with the two new columns
        stripped out, so this cannot quietly drift into testing a shape that
        never existed.
        """
        import aiosqlite

        legacy_ddl = [
            statement.replace("            confidence_before_retry REAL,\n", "").replace(
                "            retry_confidence REAL,\n", ""
            )
            for statement in ddl(False)
        ]
        path = str(tmp_path / "legacy.db")
        async with aiosqlite.connect(path) as conn:
            for statement in legacy_ddl:
                await conn.execute(statement)
            await conn.execute(
                "INSERT INTO job_stages (job_id, stage, seq, status, confidence) "
                "VALUES ('old', 'profile', 1, 'succeeded', 71.0)"
            )
            await conn.commit()
            cursor = await conn.execute("PRAGMA table_info(job_stages)")
            before = {row[1] for row in await cursor.fetchall()}
        # Guard: if the legacy build already had them, the test proves nothing.
        assert "confidence_before_retry" not in before
        assert "retry_confidence" not in before

        monkeypatch.setattr(settings, "database_url", "")
        monkeypatch.setattr(settings, "jobs_db", path)
        database = Database()
        await database.init()
        try:
            assert await _columns(database, "job_stages") >= {
                "confidence_before_retry",
                "retry_confidence",
            }
            # The pre-existing row survives, and the new columns read NULL - a
            # stage that never retried must not look like one that retried to
            # no effect.
            row = await database.fetchrow(
                "SELECT confidence, confidence_before_retry, retry_confidence "
                "FROM job_stages WHERE job_id = 'old'"
            )
            assert row is not None
            assert row["confidence"] == 71.0
            assert row["confidence_before_retry"] is None
            assert row["retry_confidence"] is None
            # Idempotent: a second startup must not fail on the existing columns.
            await database._migrate()
        finally:
            await database.close()

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


class TestArbitrationPersistence:
    """The two arbitration scores have to survive the round trip.

    Adding the columns is not the same as writing them - the UPDATE in
    ``finish_stage`` is the part that actually makes the arbitration auditable.
    """

    @pytest.fixture
    async def store(self) -> JobStore:
        s = JobStore()
        await s.init()
        return s

    async def test_both_scores_round_trip(self, store: JobStore) -> None:
        await store.start_stage("j-arb", "bi", "swot", 4)
        await store.finish_stage(
            "j-arb", "swot", "succeeded",
            confidence=80.0, confidence_before_retry=30.0, retry_confidence=80.0,
        )
        row = next(s for s in await store.get_stages("j-arb") if s["stage"] == "swot")
        assert row["confidence"] == 80.0
        assert row["confidence_before_retry"] == 30.0
        assert row["retry_confidence"] == 80.0

    async def test_a_stage_that_never_retried_stores_null(self, store: JobStore) -> None:
        """Not 0.0, and not a copy of ``confidence`` - absent means absent."""
        await store.start_stage("j-plain", "bi", "profile", 1)
        await store.finish_stage("j-plain", "profile", "succeeded", confidence=85.0)
        row = next(s for s in await store.get_stages("j-plain") if s["stage"] == "profile")
        assert row["confidence"] == 85.0
        assert row["confidence_before_retry"] is None
        assert row["retry_confidence"] is None

    async def test_recorder_forwards_the_metrics_it_is_given(self, store: JobStore) -> None:
        """StageRecorder is the only caller in production, so it must pass them."""
        from app.analysis.runner import StageMetrics
        from app.orchestrator.stage_recorder import StageRecorder

        metrics = StageMetrics(
            model="m", prompt_version="v/1", attempts=2,
            confidence=80.0, confidence_before_retry=30.0, retry_confidence=80.0,
        )
        recorder = StageRecorder(store, "j-rec", kind="bi")
        await recorder.start("swot")
        await recorder.succeed("swot", metrics)
        row = next(s for s in await store.get_stages("j-rec") if s["stage"] == "swot")
        assert row["confidence_before_retry"] == 30.0
        assert row["retry_confidence"] == 80.0


class TestMigrations:
    async def test_every_migration_is_recorded(self, db: Database) -> None:
        rows = await db.fetch("SELECT version FROM schema_migrations ORDER BY version")
        assert [r["version"] for r in rows] == [m.version for m in MIGRATIONS]
        assert rows[-1]["version"] == SCHEMA_VERSION

    async def test_retry_counts_exist(self, db: Database) -> None:
        assert "retries" in await _columns(db, "jobs")
        assert "retries" in await _columns(db, "market_research_jobs")

    async def test_versions_are_unique_and_ascending(self) -> None:
        versions = [m.version for m in MIGRATIONS]
        assert versions == sorted(set(versions))

    async def test_a_failing_migration_raises_instead_of_being_swallowed(
        self, tmp_path, monkeypatch
    ) -> None:
        import app.orchestrator.database as database_module

        broken = (*MIGRATIONS, Migration(99, "broken", statements=lambda is_pg: ["NOT VALID SQL"]))
        monkeypatch.setattr(database_module, "MIGRATIONS", broken)
        monkeypatch.setattr(settings, "database_url", "")
        monkeypatch.setattr(settings, "jobs_db", str(tmp_path / "broken.db"))
        database = Database()
        try:
            with pytest.raises(Exception, match="syntax"):
                await database.init()
        finally:
            await database.close()

    async def test_a_legacy_database_gains_a_migration_history(self, tmp_path, monkeypatch) -> None:
        """Created before the table existed: every step re-applies harmlessly."""
        import aiosqlite

        path = str(tmp_path / "legacy.db")
        async with aiosqlite.connect(path) as conn:
            for statement in ddl(False):
                await conn.execute(statement)
            await conn.commit()
        monkeypatch.setattr(settings, "database_url", "")
        monkeypatch.setattr(settings, "jobs_db", path)
        database = Database()
        await database.init()
        try:
            row = await database.fetchrow("SELECT MAX(version) AS v FROM schema_migrations")
            assert row is not None and row["v"] == SCHEMA_VERSION
        finally:
            await database.close()
