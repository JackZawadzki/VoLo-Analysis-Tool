"""
SQLite database layer for the VoLo RVM integration.

Provides schema init, migrations, seed data, and a connection helper
compatible with FastAPI's sync endpoint model (uvicorn thread pool).
"""

import os
import sqlite3
from pathlib import Path

DB_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = str(DB_DIR / "rvm.db")

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    username           TEXT    UNIQUE NOT NULL,
    email              TEXT    UNIQUE NOT NULL,
    password_hash      TEXT    NOT NULL,
    role               TEXT    NOT NULL DEFAULT 'user',
    verified           INTEGER NOT NULL DEFAULT 0,
    verification_code  TEXT,
    created_at         TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS companies (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id            INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name                TEXT    NOT NULL,
    stage               TEXT    NOT NULL DEFAULT 'Portfolio',
    risk_divisor        INTEGER NOT NULL DEFAULT 3,
    is_portfolio        INTEGER NOT NULL DEFAULT 0,
    success_criterion   TEXT    NOT NULL DEFAULT '',
    website             TEXT    NOT NULL DEFAULT '',
    description         TEXT    NOT NULL DEFAULT '',
    volume_json         TEXT    NOT NULL DEFAULT '{}',
    op_carbon_json      TEXT    NOT NULL DEFAULT '{}',
    emb_carbon_json     TEXT    NOT NULL DEFAULT '{}',
    portfolio_json      TEXT    NOT NULL DEFAULT '{}',
    prescreen_json      TEXT    NOT NULL DEFAULT '{}',
    quality_json        TEXT    NOT NULL DEFAULT '{}',
    created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS success_criteria (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    UNIQUE NOT NULL,
    probability REAL    NOT NULL CHECK(probability >= 0 AND probability <= 1),
    description TEXT    NOT NULL DEFAULT '',
    is_builtin  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS displaced_resources (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT    UNIQUE NOT NULL,
    units          TEXT    NOT NULL DEFAULT '',
    base_ci        REAL    NOT NULL,
    base_year      INTEGER NOT NULL,
    ci_type        TEXT    NOT NULL DEFAULT 'linear',
    annual_decline REAL    NOT NULL DEFAULT 0,
    description    TEXT,
    is_builtin     INTEGER NOT NULL DEFAULT 0,
    created_by     INTEGER REFERENCES users(id),
    created_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS assessments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    intermediates   TEXT    NOT NULL,
    outputs         TEXT    NOT NULL,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS financial_models (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    file_name       TEXT    NOT NULL DEFAULT '',
    source_run      TEXT    NOT NULL DEFAULT '{}',
    company_meta    TEXT    NOT NULL DEFAULT '{}',
    fiscal_calendar TEXT    NOT NULL DEFAULT '{}',
    records         TEXT    NOT NULL DEFAULT '[]',
    failures        TEXT    NOT NULL DEFAULT '[]',
    events          TEXT    NOT NULL DEFAULT '[]',
    raw_json        TEXT    NOT NULL DEFAULT '{}',
    uploaded_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS deal_reports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    company_name    TEXT    NOT NULL,
    archetype       TEXT    NOT NULL DEFAULT '',
    entry_stage     TEXT    NOT NULL DEFAULT '',
    report_json     TEXT    NOT NULL DEFAULT '{}',
    inputs_json     TEXT    NOT NULL DEFAULT '{}',
    extraction_json TEXT    NOT NULL DEFAULT '{}',
    status          TEXT    NOT NULL DEFAULT 'completed',
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS model_preferences (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    task_key        TEXT    NOT NULL,
    model_key       TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(owner_id, task_key)
);

CREATE TABLE IF NOT EXISTS fund_commitments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    report_id       INTEGER REFERENCES deal_reports(id) ON DELETE SET NULL,
    parent_id       INTEGER REFERENCES fund_commitments(id) ON DELETE SET NULL,
    company_name    TEXT    NOT NULL,
    archetype       TEXT    NOT NULL DEFAULT '',
    entry_stage     TEXT    NOT NULL DEFAULT '',
    commitment_type TEXT    NOT NULL DEFAULT 'first_check',
    check_size_m    REAL    NOT NULL,
    pre_money_m     REAL    NOT NULL DEFAULT 0,
    ownership_pct   REAL    NOT NULL DEFAULT 0,
    survival_rate   REAL    NOT NULL DEFAULT 0.3,
    moic_cond_mean  REAL    NOT NULL DEFAULT 3.0,
    exit_year_low   INTEGER NOT NULL DEFAULT 5,
    exit_year_high  INTEGER NOT NULL DEFAULT 10,
    follow_on_year  INTEGER NOT NULL DEFAULT 0,
    moic_distribution_json TEXT NOT NULL DEFAULT '[]',
    slot_index      INTEGER NOT NULL DEFAULT 0,
    status          TEXT    NOT NULL DEFAULT 'active',
    committed_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS memo_templates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name            TEXT    NOT NULL,
    description     TEXT    NOT NULL DEFAULT '',
    content         TEXT    NOT NULL DEFAULT '',
    is_default      INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS memo_documents (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    memo_session_id TEXT    NOT NULL DEFAULT '',
    file_name       TEXT    NOT NULL,
    file_type       TEXT    NOT NULL DEFAULT '',
    file_size       INTEGER NOT NULL DEFAULT 0,
    extracted_text  TEXT    NOT NULL DEFAULT '',
    doc_category    TEXT    NOT NULL DEFAULT 'general',
    file_path       TEXT    NOT NULL DEFAULT '',
    uploaded_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS generated_memos (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    report_id       INTEGER REFERENCES deal_reports(id) ON DELETE SET NULL,
    template_id     INTEGER REFERENCES memo_templates(id) ON DELETE SET NULL,
    company_name    TEXT    NOT NULL DEFAULT '',
    memo_markdown   TEXT    NOT NULL DEFAULT '',
    memo_html       TEXT    NOT NULL DEFAULT '',
    model_used      TEXT    NOT NULL DEFAULT '',
    input_token_count INTEGER NOT NULL DEFAULT 0,
    output_token_count INTEGER NOT NULL DEFAULT 0,
    generation_time_s REAL  NOT NULL DEFAULT 0,
    status          TEXT    NOT NULL DEFAULT 'completed',
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS memo_revisions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    memo_id         INTEGER NOT NULL REFERENCES generated_memos(id) ON DELETE CASCADE,
    section_key     TEXT    NOT NULL,
    revision_type   TEXT    NOT NULL DEFAULT 'llm',
    old_text        TEXT    NOT NULL DEFAULT '',
    new_text        TEXT    NOT NULL DEFAULT '',
    instructions    TEXT    NOT NULL DEFAULT '',
    revised_by      TEXT    NOT NULL DEFAULT '',
    model_used      TEXT    NOT NULL DEFAULT '',
    tokens_in       INTEGER NOT NULL DEFAULT 0,
    tokens_out      INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS deal_document_libraries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    company_name    TEXT    NOT NULL DEFAULT '',
    drive_folder_id TEXT    NOT NULL DEFAULT '',
    drive_folder_url TEXT   NOT NULL DEFAULT '',
    last_synced_at  TEXT,
    sync_status     TEXT    NOT NULL DEFAULT 'never',
    doc_count       INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS deal_documents (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    library_id      INTEGER NOT NULL REFERENCES deal_document_libraries(id) ON DELETE CASCADE,
    owner_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    drive_file_id   TEXT    NOT NULL DEFAULT '',
    file_name       TEXT    NOT NULL,
    file_type       TEXT    NOT NULL DEFAULT '',
    file_size       INTEGER NOT NULL DEFAULT 0,
    mime_type       TEXT    NOT NULL DEFAULT '',
    subfolder_path  TEXT    NOT NULL DEFAULT '',
    doc_category    TEXT    NOT NULL DEFAULT 'other',
    extracted_text  TEXT    NOT NULL DEFAULT '',
    extraction_hash TEXT    NOT NULL DEFAULT '',
    drive_modified  TEXT    NOT NULL DEFAULT '',
    last_extracted  TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(library_id, drive_file_id)
);

CREATE TABLE IF NOT EXISTS ddr_reports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_name    TEXT    NOT NULL,
    filename        TEXT    NOT NULL,
    pdf_data        BLOB    NOT NULL,
    analysis_json   TEXT    NOT NULL DEFAULT '{}',
    generated_by    TEXT    NOT NULL,
    generated_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    file_size_bytes INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS dd_scenarios (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    report_id       INTEGER NOT NULL REFERENCES deal_reports(id) ON DELETE CASCADE,
    scenario_name   TEXT    NOT NULL DEFAULT 'base',
    assumptions_json TEXT   NOT NULL DEFAULT '{}',
    deal_params_json TEXT   NOT NULL DEFAULT '{}',
    notes           TEXT    NOT NULL DEFAULT '',
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""

_BUILTIN_RESOURCES = [
    ("US electricity", "tCO₂/MWh", 0.40, 2017, "linear", 0.40 / 30,
     "EIA US avg grid intensity, linear decline to zero over 30 steps"),
    ("Global electricity", "tCO₂/MWh", 0.48, 2011, "linear", 0.48 / 30,
     "IEA global avg grid intensity, linear decline to zero over 30 steps"),
    ("Gas to Electricity", "tCO₂/MWh", 0.144752, 2017, "linear", 0.40 / 30,
     "Net grid benefit of electrification vs gas"),
    ("Diesel", "tCO₂/gallon", 0.0102, 2022, "flat", 0.0,
     "Diesel combustion, flat emission factor"),
    ("Gasoline", "tCO₂/gallon", 0.0085, 2022, "flat", 0.0,
     "Gasoline combustion, flat emission factor"),
    ("Natural Gas", "tCO₂/MMBTU", 0.0742, 2022, "flat", 0.0,
     "EPA 2014 factor + 40% pipeline methane leakage"),
    ("Natural Gas (CCGT)", "tCO₂/MWh", 0.603, 2022, "flat", 0.0,
     "Natural gas combined cycle with 2% methane leakage"),
    ("Gas Turbine (CCGT)", "tCO₂/MWh", 0.603, 2022, "flat", 0.0,
     "Gas turbine / CCGT generation intensity"),
    ("Limestone", "tCO₂/tonne", 44 / 100 + 3 / 1000, 2022, "flat", 0.0,
     "Limestone for clinker: calcination + energy"),
    ("Limestone calcination", "tCO₂/tonne", 0.7857142857, 2022, "flat", 0.0,
     "Limestone emission from calcination only"),
    ("Crushed Limestone", "tCO₂/tonne", 0.002015929423, 2022, "flat", 0.0,
     "Crushed limestone embodied carbon"),
    ("Li-ion Battery embodied", "tCO₂/MWh", 66.0, 2011, "linear", 2.2,
     "BNEF 2021 battery mfg intensity"),
    ("Li-ion Battery EV", "tCO₂/MWh", 33.6, 2011, "linear", 1.6,
     "Li-ion EV operating intensity"),
    ("Battery Cathode NMC62", "tCO₂/kg", 0.00768, 2011, "linear", 0.000256,
     "NMC622 cathode embodied carbon"),
    ("Nickel", "tCO₂/tonne", 4.9, 2022, "flat", 0.0,
     "Nickel embodied carbon (BNEF)"),
    ("Polypropylene", "tCO₂/Mt", 1_600_000.0, 2022, "flat", 0.0,
     "Polypropylene incineration emissions"),
]

_BUILTIN_CRITERIA = [
    ("Pre-commercial", 0.20,
     "Company has not yet reached commercial revenue; technology / product risk remains high."),
    ("Commercial", 0.33,
     "Company is generating commercial revenue with a proven product; scale-up risk remains."),
]


def get_db() -> sqlite3.Connection:
    """Return a new SQLite connection with row_factory enabled."""
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """Create all tables if they don't exist."""
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(_SCHEMA_SQL)
    conn.commit()
    conn.close()


def migrate_db():
    """Apply additive migrations (ALTER TABLE ADD COLUMN) safely."""
    conn = get_db()
    migrations = [
        "ALTER TABLE displaced_resources ADD COLUMN units TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE companies ADD COLUMN is_portfolio INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE companies ADD COLUMN success_criterion TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE companies ADD COLUMN prescreen_json TEXT NOT NULL DEFAULT '{}'",
        "ALTER TABLE companies ADD COLUMN quality_json   TEXT NOT NULL DEFAULT '{}'",
        "ALTER TABLE companies ADD COLUMN website     TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE companies ADD COLUMN description TEXT NOT NULL DEFAULT ''",
        # fund_commitments table (created in _SCHEMA_SQL for new DBs; migration for existing)
        """CREATE TABLE IF NOT EXISTS fund_commitments (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            report_id       INTEGER REFERENCES deal_reports(id) ON DELETE SET NULL,
            parent_id       INTEGER REFERENCES fund_commitments(id) ON DELETE SET NULL,
            company_name    TEXT    NOT NULL,
            archetype       TEXT    NOT NULL DEFAULT '',
            entry_stage     TEXT    NOT NULL DEFAULT '',
            commitment_type TEXT    NOT NULL DEFAULT 'first_check',
            check_size_m    REAL    NOT NULL,
            pre_money_m     REAL    NOT NULL DEFAULT 0,
            ownership_pct   REAL    NOT NULL DEFAULT 0,
            survival_rate   REAL    NOT NULL DEFAULT 0.3,
            moic_cond_mean  REAL    NOT NULL DEFAULT 3.0,
            exit_year_low   INTEGER NOT NULL DEFAULT 5,
            exit_year_high  INTEGER NOT NULL DEFAULT 10,
            follow_on_year  INTEGER NOT NULL DEFAULT 0,
            moic_distribution_json TEXT NOT NULL DEFAULT '[]',
            slot_index      INTEGER NOT NULL DEFAULT 0,
            status          TEXT    NOT NULL DEFAULT 'active',
            committed_at    TEXT    NOT NULL DEFAULT (datetime('now'))
        )""",
        # Additive columns for existing fund_commitments tables
        "ALTER TABLE fund_commitments ADD COLUMN parent_id INTEGER REFERENCES fund_commitments(id) ON DELETE SET NULL",
        "ALTER TABLE fund_commitments ADD COLUMN commitment_type TEXT NOT NULL DEFAULT 'first_check'",
        "ALTER TABLE fund_commitments ADD COLUMN follow_on_year INTEGER NOT NULL DEFAULT 0",
        # memo tables
        """CREATE TABLE IF NOT EXISTS memo_templates (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name            TEXT    NOT NULL,
            description     TEXT    NOT NULL DEFAULT '',
            content         TEXT    NOT NULL DEFAULT '',
            is_default      INTEGER NOT NULL DEFAULT 0,
            created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
        )""",
        """CREATE TABLE IF NOT EXISTS memo_documents (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            memo_session_id TEXT    NOT NULL DEFAULT '',
            file_name       TEXT    NOT NULL,
            file_type       TEXT    NOT NULL DEFAULT '',
            file_size       INTEGER NOT NULL DEFAULT 0,
            extracted_text  TEXT    NOT NULL DEFAULT '',
            doc_category    TEXT    NOT NULL DEFAULT 'general',
            file_path       TEXT    NOT NULL DEFAULT '',
            uploaded_at     TEXT    NOT NULL DEFAULT (datetime('now'))
        )""",
        """CREATE TABLE IF NOT EXISTS generated_memos (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            report_id       INTEGER REFERENCES deal_reports(id) ON DELETE SET NULL,
            template_id     INTEGER REFERENCES memo_templates(id) ON DELETE SET NULL,
            company_name    TEXT    NOT NULL DEFAULT '',
            memo_markdown   TEXT    NOT NULL DEFAULT '',
            memo_html       TEXT    NOT NULL DEFAULT '',
            model_used      TEXT    NOT NULL DEFAULT '',
            input_token_count INTEGER NOT NULL DEFAULT 0,
            output_token_count INTEGER NOT NULL DEFAULT 0,
            generation_time_s REAL  NOT NULL DEFAULT 0,
            status          TEXT    NOT NULL DEFAULT 'completed',
            created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
        )""",
        # Deal document library (Google Drive sync)
        """CREATE TABLE IF NOT EXISTS deal_document_libraries (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            company_name    TEXT    NOT NULL DEFAULT '',
            drive_folder_id TEXT    NOT NULL DEFAULT '',
            drive_folder_url TEXT   NOT NULL DEFAULT '',
            last_synced_at  TEXT,
            sync_status     TEXT    NOT NULL DEFAULT 'never',
            doc_count       INTEGER NOT NULL DEFAULT 0,
            created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
        )""",
        """CREATE TABLE IF NOT EXISTS deal_documents (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            library_id      INTEGER NOT NULL REFERENCES deal_document_libraries(id) ON DELETE CASCADE,
            owner_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            drive_file_id   TEXT    NOT NULL DEFAULT '',
            file_name       TEXT    NOT NULL,
            file_type       TEXT    NOT NULL DEFAULT '',
            file_size       INTEGER NOT NULL DEFAULT 0,
            mime_type       TEXT    NOT NULL DEFAULT '',
            subfolder_path  TEXT    NOT NULL DEFAULT '',
            doc_category    TEXT    NOT NULL DEFAULT 'other',
            extracted_text  TEXT    NOT NULL DEFAULT '',
            extraction_hash TEXT    NOT NULL DEFAULT '',
            drive_modified  TEXT    NOT NULL DEFAULT '',
            last_extracted  TEXT,
            created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at      TEXT    NOT NULL DEFAULT (datetime('now')),
            UNIQUE(library_id, drive_file_id)
        )""",
        # model_preferences table
        """CREATE TABLE IF NOT EXISTS model_preferences (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            task_key        TEXT    NOT NULL,
            model_key       TEXT    NOT NULL,
            updated_at      TEXT    NOT NULL DEFAULT (datetime('now')),
            UNIQUE(owner_id, task_key)
        )""",
    ]
    # Add sections_json column to generated_memos for per-section editing
    migrations.append("ALTER TABLE generated_memos ADD COLUMN sections_json TEXT NOT NULL DEFAULT '{}'")
    # Auth: verified flag and verification code for email verification
    migrations.append("ALTER TABLE users ADD COLUMN verified INTEGER NOT NULL DEFAULT 1")
    migrations.append("ALTER TABLE users ADD COLUMN verification_code TEXT")
    # Shared DDR report storage
    migrations.append("""CREATE TABLE IF NOT EXISTS ddr_reports (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        company_name    TEXT    NOT NULL,
        filename        TEXT    NOT NULL,
        pdf_data        BLOB    NOT NULL,
        analysis_json   TEXT    NOT NULL DEFAULT '{}',
        generated_by    TEXT    NOT NULL,
        generated_at    TEXT    NOT NULL DEFAULT (datetime('now')),
        file_size_bytes INTEGER NOT NULL DEFAULT 0
    )""")
    # dd_scenarios table
    migrations.append("""CREATE TABLE IF NOT EXISTS dd_scenarios (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        report_id       INTEGER NOT NULL REFERENCES deal_reports(id) ON DELETE CASCADE,
        scenario_name   TEXT    NOT NULL DEFAULT 'base',
        assumptions_json TEXT   NOT NULL DEFAULT '{}',
        deal_params_json TEXT   NOT NULL DEFAULT '{}',
        notes           TEXT    NOT NULL DEFAULT '',
        created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
        updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
    )""")
    # fund_ii_companies table for sector assignments
    migrations.append("""CREATE TABLE IF NOT EXISTS fund_ii_companies (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        companies_json  TEXT    NOT NULL DEFAULT '[]',
        created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
        updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
    )""")
    # memo_revisions table
    migrations.append("""CREATE TABLE IF NOT EXISTS memo_revisions (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        memo_id         INTEGER NOT NULL REFERENCES generated_memos(id) ON DELETE CASCADE,
        section_key     TEXT    NOT NULL,
        revision_type   TEXT    NOT NULL DEFAULT 'llm',
        old_text        TEXT    NOT NULL DEFAULT '',
        new_text        TEXT    NOT NULL DEFAULT '',
        instructions    TEXT    NOT NULL DEFAULT '',
        revised_by      TEXT    NOT NULL DEFAULT '',
        model_used      TEXT    NOT NULL DEFAULT '',
        tokens_in       INTEGER NOT NULL DEFAULT 0,
        tokens_out      INTEGER NOT NULL DEFAULT 0,
        created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
    )""")
    for sql in migrations:
        try:
            conn.execute(sql)
            conn.commit()
        except Exception:
            pass
    conn.close()


def seed_resources():
    """Insert built-in CI resources if not present."""
    conn = get_db()
    for name, units, base_ci, base_year, ci_type, annual_decline, description in _BUILTIN_RESOURCES:
        conn.execute(
            """INSERT OR IGNORE INTO displaced_resources
               (name, units, base_ci, base_year, ci_type, annual_decline, description, is_builtin)
               VALUES (?,?,?,?,?,?,?,1)""",
            (name, units, base_ci, base_year, ci_type, annual_decline, description),
        )
    conn.commit()
    conn.close()


def seed_criteria():
    """Insert built-in success criteria if not present."""
    conn = get_db()
    for name, prob, desc in _BUILTIN_CRITERIA:
        conn.execute(
            """INSERT OR IGNORE INTO success_criteria
               (name, probability, description, is_builtin) VALUES (?,?,?,1)""",
            (name, prob, desc),
        )
    conn.commit()
    conn.close()


def load_committed_deals(owner_id: int) -> list:
    """Load active fund commitments for a user, returned as dicts."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM fund_commitments WHERE owner_id=? AND status='active' ORDER BY slot_index",
            (owner_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Model preference defaults ────────────────────────────────────────────────
# task_key → default model string
MODEL_DEFAULTS = {
    "extraction":       "claude-haiku-4-5-20251001",
    "deal_chat":        "claude-sonnet-4-20250514",
    "dev_agent":        "claude-haiku-4-5-20251001",
    "memo_generation":  "claude-sonnet-4-20250514",
}

# All valid model identifiers (Anthropic + Refiant)
VALID_MODELS = {
    "claude-haiku-4-5-20251001",
    "claude-sonnet-4-20250514",
    "claude-opus-4-20250514",
    "qwen-rfnt",
}

VALID_TASKS = set(MODEL_DEFAULTS.keys())


def get_model_preferences(owner_id: int) -> dict:
    """Return {task_key: model_key} for a user, merged with defaults."""
    prefs = dict(MODEL_DEFAULTS)
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT task_key, model_key FROM model_preferences WHERE owner_id=?",
            (owner_id,),
        ).fetchall()
        for r in rows:
            prefs[r["task_key"]] = r["model_key"]
    finally:
        conn.close()
    return prefs


def set_model_preference(owner_id: int, task_key: str, model_key: str):
    """Upsert a single model preference."""
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO model_preferences (owner_id, task_key, model_key, updated_at)
               VALUES (?, ?, ?, datetime('now'))
               ON CONFLICT(owner_id, task_key) DO UPDATE SET model_key=excluded.model_key, updated_at=excluded.updated_at""",
            (owner_id, task_key, model_key),
        )
        conn.commit()
    finally:
        conn.close()


def startup():
    """Full DB init sequence — call once on app startup."""
    init_db()
    migrate_db()
    seed_resources()
    seed_criteria()
