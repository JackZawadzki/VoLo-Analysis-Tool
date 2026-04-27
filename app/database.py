"""
SQLite database layer for the VoLo RVM integration.

Provides schema init, migrations, seed data, and a connection helper
compatible with FastAPI's sync endpoint model (uvicorn thread pool).
"""

import os
import shutil
import sqlite3
from pathlib import Path

# DB location.
#
# Default lives inside the source tree at <repo>/data/rvm.db, which is fine for
# local development. On Replit Reserved-VM Deployments (and any host where each
# redeploy replaces the source tree), the source-tree path gets WIPED on every
# redeploy — taking the database with it. To survive redeploys, set
# VOLO_DB_PATH to a path *outside* the source tree, e.g.:
#     VOLO_DB_PATH=/home/runner/.volo/rvm.db   (Replit Reserved VM)
# The directory is created automatically on first run.
_LEGACY_DB_DIR  = Path(__file__).resolve().parent.parent / "data"
_LEGACY_DB_PATH = _LEGACY_DB_DIR / "rvm.db"

_env_db_path = os.environ.get("VOLO_DB_PATH", "").strip()
if _env_db_path:
    DB_PATH = _env_db_path
    DB_DIR  = Path(DB_PATH).parent
    DB_DIR.mkdir(parents=True, exist_ok=True)
    # One-time migration: if a legacy in-source DB exists but the new
    # location is empty, copy it over so existing accounts/reports survive.
    if _LEGACY_DB_PATH.exists() and not Path(DB_PATH).exists():
        try:
            shutil.copy2(_LEGACY_DB_PATH, DB_PATH)
            print(f"[VoLo Engine] Migrated DB: {_LEGACY_DB_PATH} -> {DB_PATH}", flush=True)
        except OSError as e:
            print(f"[VoLo Engine] WARN: could not migrate legacy DB: {e}", flush=True)
else:
    DB_DIR  = _LEGACY_DB_DIR
    DB_PATH = str(_LEGACY_DB_PATH)
DB_PATH = str(DB_PATH)

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
    sections_json   TEXT    NOT NULL DEFAULT '{}',
    memo_session_id TEXT    NOT NULL DEFAULT '',
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

-- One-time codes for email verification (6-digit on signup)
-- and password reset (opaque token in an email link).
-- Expired / used codes stay for audit history (see last_used_at).
CREATE TABLE IF NOT EXISTS auth_tokens (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    purpose       TEXT    NOT NULL,       -- 'verify_email' | 'password_reset'
    code_hash     TEXT    NOT NULL,       -- hash of code/token (never store plaintext)
    expires_at    TEXT    NOT NULL,
    used_at       TEXT,                   -- when successfully consumed
    attempt_count INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_auth_tokens_user ON auth_tokens(user_id, purpose);

-- Per-user activity log. Every login, registration, and deal-report
-- run gets a row here so Jack/Joseph can see "who did what when"
-- without having to grep application logs.
CREATE TABLE IF NOT EXISTS user_activity (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    email      TEXT,                       -- denormalized so entries survive user deletion
    event      TEXT    NOT NULL,           -- 'register' | 'verify' | 'login' | 'login_failed'
                                           -- | 'logout' | 'password_reset_requested'
                                           -- | 'password_reset_completed' | 'deal_report'
                                           -- | 'memo_generated' | 'ddr_started' | etc.
    detail     TEXT    NOT NULL DEFAULT '',
    ip_address TEXT    NOT NULL DEFAULT '',
    user_agent TEXT    NOT NULL DEFAULT '',
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_user_activity_user ON user_activity(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_user_activity_event ON user_activity(event, created_at DESC);

-- Team-shared analyst notes per company. Keyed by a normalized company name
-- (case-insensitive, whitespace-collapsed) so notes group with library
-- artifacts ("Mitra Chem", "mitra chem", "  Mitra  Chem ") all share one doc.
-- Updated optimistically: every save bumps `version`; the client sends the
-- version it loaded, and a stale write returns 409 so users can refresh
-- and merge instead of silently overwriting a colleague's work.
CREATE TABLE IF NOT EXISTS deal_notes (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    company_key              TEXT    UNIQUE NOT NULL,
    company_name             TEXT    NOT NULL,
    content                  TEXT    NOT NULL DEFAULT '',
    version                  INTEGER NOT NULL DEFAULT 0,
    last_edited_by           INTEGER REFERENCES users(id) ON DELETE SET NULL,
    last_edited_by_username  TEXT    NOT NULL DEFAULT '',
    last_edited_at           TEXT    NOT NULL DEFAULT (datetime('now')),
    created_at               TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_deal_notes_company ON deal_notes(company_key);

-- Append-only revision log so a careless save never destroys earlier work.
-- One row per save; the live `deal_notes` row holds the latest snapshot.
CREATE TABLE IF NOT EXISTS deal_notes_history (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    company_key       TEXT    NOT NULL,
    content           TEXT    NOT NULL,
    version           INTEGER NOT NULL,
    edited_by         INTEGER REFERENCES users(id) ON DELETE SET NULL,
    edited_by_username TEXT   NOT NULL DEFAULT '',
    edited_at         TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_deal_notes_history_company ON deal_notes_history(company_key, version DESC);
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


# ════════════════════════════════════════════════════════════════════════════
#  BACKEND DETECTION & POSTGRES COMPATIBILITY SHIM
#  ──────────────────────────────────────────────────────────────────────────
#  Replit deployments don't persist the local filesystem across redeploys
#  (their docs explicitly state this for Reserved VM Deployments). For
#  production we fall back to a managed Postgres database via DATABASE_URL.
#  Local development continues to use SQLite — no setup change required.
#
#  The shim below makes the rest of the codebase work IDENTICALLY against
#  either backend: every existing `conn.execute("SELECT ... WHERE x=?", ...)`
#  call, every `cur.lastrowid`, every `row["col"]` keeps working unchanged.
#  ════════════════════════════════════════════════════════════════════════════
import re as _re

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
USE_POSTGRES = bool(DATABASE_URL)

if USE_POSTGRES:
    import psycopg2          # noqa: F401  (installed via requirements.txt)
    import psycopg2.extras
    IntegrityError = psycopg2.IntegrityError
    print("[VoLo Engine] DB backend: Postgres (DATABASE_URL set)", flush=True)
else:
    IntegrityError = sqlite3.IntegrityError
    print(f"[VoLo Engine] DB backend: SQLite ({DB_PATH})", flush=True)


def _translate_sql_to_pg(sql: str) -> str:
    """Translate SQLite-flavored SQL into Postgres-flavored SQL.

    Conservative, in-place substitutions. The same SQL strings keep working
    against SQLite (we only invoke this when USE_POSTGRES is True).
    """
    # INSERT OR IGNORE -> INSERT ... ON CONFLICT DO NOTHING
    if _re.search(r"\bINSERT\s+OR\s+IGNORE\b", sql, _re.IGNORECASE):
        sql = _re.sub(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", "INSERT INTO", sql, flags=_re.IGNORECASE)
        if "ON CONFLICT" not in sql.upper() and "RETURNING" not in sql.upper():
            sql = sql.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    # Placeholders
    sql = sql.replace("?", "%s")
    # Datetime
    sql = sql.replace("datetime('now')", "NOW()")
    # Auto-increment integer pkey  (SERIAL / BIGSERIAL produce IDs)
    sql = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
    # Binary blob column type
    sql = _re.sub(r"\bBLOB\b", "BYTEA", sql)
    # Stricter SQLite begin -> standard begin
    sql = _re.sub(r"\bBEGIN\s+IMMEDIATE\b", "BEGIN", sql, flags=_re.IGNORECASE)
    return sql


class _PgCursor:
    """Wraps a psycopg2 DictCursor so it behaves like a sqlite3.Cursor for the
    parts of the API our codebase touches: fetchone/fetchall, rowcount,
    lastrowid (eagerly resolved via INSERT ... RETURNING id)."""

    __slots__ = ("_cur", "_lastrowid", "_lastrowid_consumed")

    def __init__(self, cur, is_insert_with_returning: bool):
        self._cur = cur
        self._lastrowid = None
        self._lastrowid_consumed = False
        if is_insert_with_returning and cur.description:
            try:
                row = cur.fetchone()
                if row is not None and "id" in row:
                    self._lastrowid = row["id"]
                self._lastrowid_consumed = True
            except psycopg2.ProgrammingError:
                pass

    def fetchone(self):
        if self._lastrowid_consumed:
            self._lastrowid_consumed = False
            return None
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    @property
    def rowcount(self):
        return self._cur.rowcount

    @property
    def lastrowid(self):
        return self._lastrowid

    @property
    def description(self):
        return self._cur.description

    def __iter__(self):
        return iter(self._cur)


class _PgConnection:
    """Wraps a psycopg2 connection so the codebase's sqlite3-style usage
    (`conn.execute(sql, params)`, `conn.commit()`, etc.) works unchanged."""

    __slots__ = ("_conn",)

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=()):
        # Fast path: detect insert before translation so we can append
        # RETURNING id for lastrowid support.
        is_insert = sql.lstrip().upper().startswith("INSERT")
        translated = _translate_sql_to_pg(sql)
        is_insert_with_returning = False
        if is_insert and "ON CONFLICT DO NOTHING" not in translated.upper() \
                     and "RETURNING" not in translated.upper():
            translated = translated.rstrip().rstrip(";") + " RETURNING id"
            is_insert_with_returning = True
        cur = self._conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute(translated, params or ())
        return _PgCursor(cur, is_insert_with_returning)

    def executescript(self, sql_script: str):
        # SQLite's executescript runs multiple statements separated by ';'.
        # psycopg2 handles multi-statement strings fine; just translate first.
        translated = _translate_sql_to_pg(sql_script)
        cur = self._conn.cursor()
        cur.execute(translated)

    def commit(self): self._conn.commit()
    def rollback(self): self._conn.rollback()
    def close(self): self._conn.close()

    # Pass-through for code that does `with conn:` block-commit semantics
    def __enter__(self): return self
    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self._conn.commit()
        else:
            self._conn.rollback()


def get_db():
    """Return a new connection with row_factory / dict-cursor enabled.

    Local dev (no DATABASE_URL): a real sqlite3.Connection, identical to
    historical behavior (bit-for-bit).
    Production (DATABASE_URL set): a psycopg2 connection wrapped so the
    sqlite3-style API the codebase relies on continues to work unchanged.
    """
    if USE_POSTGRES:
        conn = psycopg2.connect(DATABASE_URL)
        return _PgConnection(conn)
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _split_sql_statements(script: str) -> list:
    """Naive split of a multi-statement SQL script on top-level semicolons.

    Skips semicolons inside SQL string literals. Good enough for the
    DDL-only _SCHEMA_SQL we feed it (no string literals contain `;`).
    """
    out, buf, in_str = [], [], False
    for ch in script:
        if ch == "'":
            in_str = not in_str
        if ch == ";" and not in_str:
            stmt = "".join(buf).strip()
            if stmt:
                out.append(stmt)
            buf = []
        else:
            buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


def init_db():
    """Create all tables if they don't exist (works on both backends).

    On Postgres we run statements one-at-a-time so a single failure (e.g.
    a constraint that doesn't apply cleanly to existing data, or a type
    mismatch in a FK reference) doesn't roll back the entire schema and
    leave later tables (like deal_notes) un-created.
    """
    if USE_POSTGRES:
        conn_raw = psycopg2.connect(DATABASE_URL)
        try:
            statements = _split_sql_statements(_translate_sql_to_pg(_SCHEMA_SQL))
            n_ok, n_skip = 0, 0
            for stmt in statements:
                cur = conn_raw.cursor()
                try:
                    cur.execute(stmt)
                    conn_raw.commit()
                    n_ok += 1
                except Exception as e:
                    conn_raw.rollback()
                    n_skip += 1
                    # Log the first 90 chars of the failing statement so
                    # we can see in the deployment logs which CREATE failed.
                    head = stmt.replace("\n", " ").strip()[:90]
                    print(f"[VoLo Engine] schema stmt skipped ({type(e).__name__}: {e}): {head}...", flush=True)
                finally:
                    cur.close()
            print(f"[VoLo Engine] init_db (Postgres): {n_ok} statements ran, {n_skip} skipped", flush=True)
        finally:
            conn_raw.close()
        return
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
    # Add memo_session_id column so generated memos can be re-linked to the
    # data-room upload session that produced them (used for image embed +
    # docx export). Older DBs (and a fresh Postgres deploy) don't have this
    # because it was never in the base schema until now.
    migrations.append("ALTER TABLE generated_memos ADD COLUMN memo_session_id TEXT NOT NULL DEFAULT ''")
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
            # Postgres aborts the txn on failure (e.g. column already exists);
            # rollback so subsequent migrations can proceed. Harmless on SQLite.
            try: conn.rollback()
            except Exception: pass
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
