"""Table definitions, rendered per SQL dialect.

SQLite and PostgreSQL differ in exactly two places for this schema: the
autoincrement primary key, and the timestamp column type plus its default.
Everything else is shared, so the DDL is written once and parameterised.
"""


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


# Best-effort column additions for databases created by an earlier version.
# Each runs inside its own try/except; "already exists" is the expected outcome.
BACKFILL_COLUMNS: list[tuple[str, str, str]] = [
    ("market_research_jobs", "pdf_path", "TEXT"),
    ("market_research_jobs", "docx_path", "TEXT"),
]
