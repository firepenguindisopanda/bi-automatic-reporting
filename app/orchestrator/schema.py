"""Table definitions, rendered per SQL dialect.

SQLite and PostgreSQL differ in exactly two places for this schema: the
autoincrement primary key, and the timestamp column type plus its default.
Everything else is shared, so the DDL is written once and parameterised.

Changes after the base tables are numbered migrations (``MIGRATIONS``), applied
once each and recorded in ``schema_migrations``.
"""

from collections.abc import Callable
from dataclasses import dataclass, field


def ddl(is_pg: bool) -> list[str]:
    """CREATE statements for every table and index, in dependency order.

    All statements are ``IF NOT EXISTS`` and safe to run on every startup.
    """
    serial = "SERIAL PRIMARY KEY" if is_pg else "INTEGER PRIMARY KEY AUTOINCREMENT"
    ts = "TIMESTAMP" if is_pg else "TEXT"
    now = "CURRENT_TIMESTAMP" if is_pg else "(datetime('now'))"

    return [
        f"""CREATE TABLE IF NOT EXISTS jobs (
            job_id TEXT PRIMARY KEY,
            url TEXT NOT NULL,
            email TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            error TEXT,
            artifact_json TEXT,
            pdf_path TEXT,
            docx_path TEXT,
            events TEXT DEFAULT '[]',
            created_at {ts} NOT NULL DEFAULT {now},
            updated_at {ts} NOT NULL DEFAULT {now}
        )""",
        f"""CREATE TABLE IF NOT EXISTS briefs (
            id {serial},
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
            created_at {ts} NOT NULL DEFAULT {now},
            updated_at {ts} NOT NULL DEFAULT {now},
            UNIQUE(job_id, session_id)
        )""",
        f"""CREATE TABLE IF NOT EXISTS market_research_jobs (
            job_id TEXT PRIMARY KEY,
            market_query TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            result_json TEXT,
            error TEXT,
            pdf_path TEXT,
            docx_path TEXT,
            created_at {ts} NOT NULL DEFAULT {now},
            updated_at {ts} NOT NULL DEFAULT {now}
        )""",
        # Replaces the jobs.events JSON blob: one row per stage, updated in
        # place on transition rather than read-modify-written as a whole.
        f"""CREATE TABLE IF NOT EXISTS job_stages (
            id {serial},
            job_id TEXT NOT NULL,
            job_kind TEXT NOT NULL DEFAULT 'bi',
            stage TEXT NOT NULL,
            seq INTEGER NOT NULL,
            status TEXT NOT NULL,
            started_at {ts},
            ended_at {ts},
            duration_ms INTEGER,
            model TEXT,
            prompt_version TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            prompt_tokens INTEGER,
            completion_tokens INTEGER,
            completeness REAL,
            confidence REAL,
            confidence_before_retry REAL,
            retry_confidence REAL,
            critic_note TEXT,
            warnings TEXT,
            artifact_json TEXT,
            error TEXT,
            error_kind TEXT,
            created_at {ts} NOT NULL DEFAULT {now},
            updated_at {ts} NOT NULL DEFAULT {now},
            UNIQUE(job_id, stage)
        )""",
        f"""CREATE TABLE IF NOT EXISTS deliveries (
            id {serial},
            job_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            to_email TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            message_id TEXT,
            last_error TEXT,
            created_at {ts} NOT NULL DEFAULT {now},
            updated_at {ts} NOT NULL DEFAULT {now}
        )""",
        "CREATE INDEX IF NOT EXISTS idx_job_stages_job ON job_stages (job_id, seq)",
        # Powers the rolling-median ETA per (stage, model).
        "CREATE INDEX IF NOT EXISTS idx_job_stages_model ON job_stages (stage, model)",
        "CREATE INDEX IF NOT EXISTS idx_deliveries_job ON deliveries (job_id)",
    ]


@dataclass(frozen=True)
class Migration:
    """One numbered step from an older schema to a newer one.

    Applied in order and recorded in ``schema_migrations``, so each runs once
    per database. Every step is also idempotent - ``IF NOT EXISTS`` DDL, and
    columns added only when absent - because databases created before this
    table existed have some of these changes already and no record of them.

    A failure raises. The previous approach wrapped each ALTER in a bare
    ``except: pass`` on the theory that "already exists" was the only possible
    error, which also hid every error that was not that.
    """

    version: int
    description: str
    statements: Callable[[bool], list[str]] = field(default=lambda is_pg: [])
    columns: tuple[tuple[str, str, str], ...] = ()
    """(table, column, type) to add when the column is missing."""


def _v3_indexes(is_pg: bool) -> list[str]:
    return [
        # Per-recipient daily caps count recent rows for one address.
        "CREATE INDEX IF NOT EXISTS idx_jobs_email_created ON jobs (email, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_deliveries_to_created ON deliveries (to_email, created_at)",
        # The ETA reads recent successful durations per (stage, model).
        "CREATE INDEX IF NOT EXISTS idx_job_stages_eta ON job_stages (stage, model, status)",
        # Retention sweeps by age.
        "CREATE INDEX IF NOT EXISTS idx_jobs_updated ON jobs (updated_at)",
        "CREATE INDEX IF NOT EXISTS idx_mr_jobs_updated ON market_research_jobs (updated_at)",
    ]


MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "base tables", statements=ddl),
    Migration(
        2,
        "render paths on market research; retry arbitration scores",
        columns=(
            ("market_research_jobs", "pdf_path", "TEXT"),
            ("market_research_jobs", "docx_path", "TEXT"),
            # The score before a critic re-run and the re-run's own score. Both
            # stay NULL for a stage that never retried - absent is not the same
            # as unchanged.
            ("job_stages", "confidence_before_retry", "REAL"),
            ("job_stages", "retry_confidence", "REAL"),
        ),
    ),
    Migration(
        3,
        "retry counts; indexes for recipient caps, ETA and retention",
        statements=_v3_indexes,
        columns=(
            ("jobs", "retries", "INTEGER NOT NULL DEFAULT 0"),
            ("market_research_jobs", "retries", "INTEGER NOT NULL DEFAULT 0"),
        ),
    ),
)

SCHEMA_VERSION = MIGRATIONS[-1].version


def migrations_table(is_pg: bool) -> str:
    ts = "TIMESTAMP" if is_pg else "TEXT"
    now = "CURRENT_TIMESTAMP" if is_pg else "(datetime('now'))"
    return (
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version INTEGER PRIMARY KEY, "
        "description TEXT NOT NULL, "
        f"applied_at {ts} NOT NULL DEFAULT {now})"
    )
