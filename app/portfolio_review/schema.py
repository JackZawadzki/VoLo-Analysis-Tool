"""
SQL schema for the portfolio_review module.

All tables are prefixed `pr_` to avoid collision with the underwriting
tool's existing `companies`/`reports` tables. Schema is applied via
`init_schema(conn)` from app.database during app startup.
"""

PR_SCHEMA_SQL = """
-- Portfolio companies (firm-level — distinct from per-user underwriting models)
CREATE TABLE IF NOT EXISTS pr_companies (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT    NOT NULL UNIQUE,
    fund                TEXT    NOT NULL DEFAULT 'Fund I',     -- Fund I | Fund II | AGM | SPV
    brief_description   TEXT    NOT NULL DEFAULT '',
    sector              TEXT    NOT NULL DEFAULT '',
    submarket           TEXT    NOT NULL DEFAULT '',
    business_model      TEXT    NOT NULL DEFAULT '',
    hw_sw               TEXT    NOT NULL DEFAULT '',           -- HW | SW | Both
    commercial_status   TEXT    NOT NULL DEFAULT '',           -- Pre-Rev | Pilot | Commercial | Hyperscale
    ceo_name            TEXT    NOT NULL DEFAULT '',
    ceo_email           TEXT    NOT NULL DEFAULT '',
    cfo_name            TEXT    NOT NULL DEFAULT '',
    cfo_email           TEXT    NOT NULL DEFAULT '',
    address             TEXT    NOT NULL DEFAULT '',
    website             TEXT    NOT NULL DEFAULT '',
    fume_date           TEXT,                                  -- ISO date when company expects to run out of cash
    first_year_revenue  TEXT,
    hyperscale          INTEGER NOT NULL DEFAULT 0,
    notable_partners    TEXT    NOT NULL DEFAULT '',
    next_round_expect   TEXT    NOT NULL DEFAULT '',
    created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_pr_companies_fund ON pr_companies(fund);

-- Investments — every transaction (initial, follow-on, conversion)
CREATE TABLE IF NOT EXISTS pr_investments (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id               INTEGER NOT NULL REFERENCES pr_companies(id) ON DELETE CASCADE,
    investment_date          TEXT,                              -- ISO date
    original_or_conversion   TEXT    NOT NULL DEFAULT 'O',      -- O | C
    investment_amount        REAL,
    round_label              TEXT    NOT NULL DEFAULT '',       -- Pre-Seed | Seed | A | B | ...
    round_size               REAL,
    round_lead               TEXT    NOT NULL DEFAULT '',
    pre_money                REAL,
    post_money               REAL,
    board_seat               TEXT    NOT NULL DEFAULT 'No',     -- Yes | Observer | No
    board_member             TEXT    NOT NULL DEFAULT '',
    deal_lead                TEXT    NOT NULL DEFAULT '',
    notes                    TEXT    NOT NULL DEFAULT '',
    participated             INTEGER NOT NULL DEFAULT 1,
    created_at               TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_pr_investments_company ON pr_investments(company_id);
CREATE INDEX IF NOT EXISTS idx_pr_investments_date    ON pr_investments(investment_date);

-- Per-period financials
CREATE TABLE IF NOT EXISTS pr_financials (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      INTEGER NOT NULL REFERENCES pr_companies(id) ON DELETE CASCADE,
    period          TEXT    NOT NULL,                           -- '2023' | '2024 Est' | 'FY2024'
    revenue         REAL,
    growth_rate     REAL,
    ebitda          REAL,
    employees       INTEGER,
    runway_months   REAL,
    cash_on_hand    REAL,
    notes           TEXT    NOT NULL DEFAULT '',
    UNIQUE(company_id, period)
);
CREATE INDEX IF NOT EXISTS idx_pr_financials_company ON pr_financials(company_id);

-- Valuation snapshots (Carta marks, 409As, etc.)
CREATE TABLE IF NOT EXISTS pr_valuations (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id               INTEGER NOT NULL REFERENCES pr_companies(id) ON DELETE CASCADE,
    as_of_date               TEXT    NOT NULL,
    valuation_post_money     REAL,
    nav                      REAL,
    cost_basis               REAL,
    ownership_pct            REAL,
    mark_source              TEXT    NOT NULL DEFAULT 'Carta',
    notes                    TEXT    NOT NULL DEFAULT '',
    UNIQUE(company_id, as_of_date)
);
CREATE INDEX IF NOT EXISTS idx_pr_valuations_company ON pr_valuations(company_id);

-- IRR / returns snapshots
CREATE TABLE IF NOT EXISTS pr_returns (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      INTEGER NOT NULL REFERENCES pr_companies(id) ON DELETE CASCADE,
    as_of_date      TEXT    NOT NULL,
    cost            REAL,
    proceeds        REAL,
    interest        REAL,
    fmv             REAL,
    total_value     REAL,
    gain_loss       REAL,
    multiple        REAL,
    irr             REAL,
    UNIQUE(company_id, as_of_date)
);
CREATE INDEX IF NOT EXISTS idx_pr_returns_company ON pr_returns(company_id);

-- Board seats (current state)
CREATE TABLE IF NOT EXISTS pr_board_seats (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      INTEGER NOT NULL REFERENCES pr_companies(id) ON DELETE CASCADE,
    seat_type       TEXT    NOT NULL DEFAULT 'Director',        -- Director | Observer
    board_member    TEXT    NOT NULL,
    active          INTEGER NOT NULL DEFAULT 1,
    started_on      TEXT,
    ended_on        TEXT,
    notes           TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_pr_board_seats_company ON pr_board_seats(company_id);

-- Follow-on events (bridges, priced rounds, conversions)
CREATE TABLE IF NOT EXISTS pr_follow_ons (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id          INTEGER NOT NULL REFERENCES pr_companies(id) ON DELETE CASCADE,
    event_date          TEXT,
    event_type          TEXT    NOT NULL DEFAULT 'Priced',      -- Bridge | Priced | Convert
    amount_invested     REAL,
    externally_priced   INTEGER NOT NULL DEFAULT 0,
    round_label         TEXT    NOT NULL DEFAULT '',
    pre_money           REAL,
    post_money          REAL,
    notes               TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_pr_follow_ons_company ON pr_follow_ons(company_id);

-- Comments — polymorphic. Attach to a company, a section, or a specific metric cell.
-- entity_type:  'company' | 'section' | 'investment' | 'metric'
-- entity_key:   for 'company' use company_id as text; for 'section' use slug
--               (e.g. 'returns'); for 'metric' use 'company:42:irr' style.
CREATE TABLE IF NOT EXISTS pr_comments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    entity_type     TEXT    NOT NULL,
    entity_key      TEXT    NOT NULL,
    parent_id       INTEGER REFERENCES pr_comments(id) ON DELETE CASCADE,
    body            TEXT    NOT NULL,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    deleted         INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_pr_comments_entity  ON pr_comments(entity_type, entity_key);
CREATE INDEX IF NOT EXISTS idx_pr_comments_user    ON pr_comments(user_id);
CREATE INDEX IF NOT EXISTS idx_pr_comments_created ON pr_comments(created_at);

-- Maps each portfolio company to one or more Drive folders.
-- folder_type distinguishes the purpose — typically each company has both
-- a 'current' folder (ongoing materials) and a 'diligence' folder (the DD
-- package from investment time, used as the baseline).
CREATE TABLE IF NOT EXISTS pr_company_folders (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id         INTEGER NOT NULL REFERENCES pr_companies(id) ON DELETE CASCADE,
    folder_type        TEXT    NOT NULL DEFAULT 'current',       -- current | diligence | board_pack | other
    drive_folder_id    TEXT    NOT NULL,
    drive_folder_name  TEXT    NOT NULL,
    parent_folder_id   TEXT    NOT NULL DEFAULT '',
    match_confidence   TEXT    NOT NULL DEFAULT 'auto',          -- exact | fuzzy | manual
    last_scanned_at    TEXT,
    created_at         TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(company_id, folder_type)
);
CREATE INDEX IF NOT EXISTS idx_pr_company_folders_company ON pr_company_folders(company_id);

-- Traction snapshots — AI-extracted commercial status + revenue per company.
-- One row per scan run; the UI shows the latest by company_id.
CREATE TABLE IF NOT EXISTS pr_traction_snapshots (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id          INTEGER NOT NULL REFERENCES pr_companies(id) ON DELETE CASCADE,
    scanned_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    commercial_status   TEXT    NOT NULL DEFAULT '',             -- Pre-Rev | Pilot | Commercial | Hyperscale
    revenue_current     REAL,
    revenue_prior       REAL,
    revenue_period      TEXT    NOT NULL DEFAULT '',             -- e.g., 'FY2024 vs FY2023'
    revenue_growth_pct  REAL,                                     -- decimal: 0.40 = 40%
    arr_current         REAL,
    customer_count      INTEGER,
    runway_months       REAL,
    notable_milestones  TEXT    NOT NULL DEFAULT '',
    summary             TEXT    NOT NULL DEFAULT '',
    -- Baseline-vs-current comparison fields, populated when DD folder exists.
    baseline_status     TEXT    NOT NULL DEFAULT '',             -- commercial status at DD
    baseline_revenue    REAL,                                     -- revenue at DD (most recent FY)
    baseline_summary    TEXT    NOT NULL DEFAULT '',             -- 1-2 sentences from DD pack
    change_vs_baseline  TEXT    NOT NULL DEFAULT '',             -- AI narrative on what's changed
    source_files        TEXT    NOT NULL DEFAULT '[]',           -- JSON array {name, folder_type}
    model_used          TEXT    NOT NULL DEFAULT '',
    confidence          TEXT    NOT NULL DEFAULT 'medium',       -- low | medium | high
    raw_response        TEXT    NOT NULL DEFAULT ''              -- full LLM response for audit
);
CREATE INDEX IF NOT EXISTS idx_pr_traction_company ON pr_traction_snapshots(company_id, scanned_at DESC);

-- Derisking scorecard — ports the logic from the Fund I 2025 / Fund II 2025
-- tabs of the Derisking Quadrants workbook. Each row is one period's
-- assessment. Score per dimension: +1 (substantially derisked for current
-- stage), -1 (remains a major risk), 0 (neutral / partial), NULL (not scored).
CREATE TABLE IF NOT EXISTS pr_derisking_scores (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id               INTEGER NOT NULL REFERENCES pr_companies(id) ON DELETE CASCADE,
    period                   TEXT    NOT NULL,                    -- 'FY2025' | 'Q1 2026'
    fund                     TEXT    NOT NULL DEFAULT 'Fund I',
    rapid_innovation_adopt   REAL,                                 -- -1 | 0 | 1
    business_model           REAL,
    technology               REAL,
    incentive_management     REAL,
    team                     REAL,
    product_growth           REAL,
    ip_and_data              REAL,
    is_exited                INTEGER NOT NULL DEFAULT 0,
    total_score              REAL,                                 -- SUM of the 7 dimensions
    quartile                 INTEGER,                              -- 1..4
    notes                    TEXT    NOT NULL DEFAULT '',
    scored_at                TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(company_id, period)
);
CREATE INDEX IF NOT EXISTS idx_pr_derisking_company ON pr_derisking_scores(company_id, period);

-- Excel import audit — tracks each sync run for diagnostics
CREATE TABLE IF NOT EXISTS pr_imports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER REFERENCES users(id) ON DELETE SET NULL,
    source_file     TEXT    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'success',         -- success | partial | failed
    rows_imported   INTEGER NOT NULL DEFAULT 0,
    rows_skipped    INTEGER NOT NULL DEFAULT 0,
    error_summary   TEXT    NOT NULL DEFAULT '',
    started_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    finished_at     TEXT
);

-- Granola notes linked to portfolio companies. One row per (note × company)
-- association — a single note can be linked to multiple companies if it
-- mentions several portfolio names or has attendees from several. Filled
-- by `granola_sync.run_granola_sync()` which iterates the volomind
-- GranolaConnector and matches notes to pr_companies via:
--   1. Attendee email == ceo_email or cfo_email on pr_companies (high confidence)
--   2. Company name appears in note title (medium confidence)
-- The `match_method` column records which heuristic fired so the UI can
-- surface confidence to the analyst.
CREATE TABLE IF NOT EXISTS pr_granola_notes (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id        INTEGER NOT NULL REFERENCES pr_companies(id) ON DELETE CASCADE,
    granola_note_id   TEXT    NOT NULL,                          -- Granola's stable id
    note_title        TEXT    NOT NULL DEFAULT '',
    note_summary      TEXT    NOT NULL DEFAULT '',               -- Granola's summary_markdown
    note_url          TEXT    NOT NULL DEFAULT '',               -- web link to the note
    attendees_json    TEXT    NOT NULL DEFAULT '[]',             -- JSON array of {name, email}
    note_created_at   TEXT,                                       -- ISO timestamp from Granola
    note_updated_at   TEXT,
    match_method      TEXT    NOT NULL DEFAULT 'manual',         -- attendee_email | title_match | manual
    match_confidence  TEXT    NOT NULL DEFAULT 'medium',         -- low | medium | high
    fetched_at        TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(company_id, granola_note_id)
);
CREATE INDEX IF NOT EXISTS idx_pr_granola_company ON pr_granola_notes(company_id, note_updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_pr_granola_note    ON pr_granola_notes(granola_note_id);

-- Granola sync audit — tracks each pull and what was matched.
CREATE TABLE IF NOT EXISTS pr_granola_syncs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id           INTEGER REFERENCES users(id) ON DELETE SET NULL,
    started_at        TEXT    NOT NULL DEFAULT (datetime('now')),
    finished_at       TEXT,
    status            TEXT    NOT NULL DEFAULT 'success',        -- success | partial | failed
    notes_fetched     INTEGER NOT NULL DEFAULT 0,
    associations_new  INTEGER NOT NULL DEFAULT 0,
    associations_skip INTEGER NOT NULL DEFAULT 0,
    error_summary     TEXT    NOT NULL DEFAULT ''
);

-- Per-user incremental sync state. After a successful Granola pull we
-- persist the high-water-mark timestamp here so the next sync passes
-- it to the connector as `updated_after` and only fetches notes that
-- have changed since. Same pattern volomind uses in cc_sources.cursor.
--
-- Source values:
--   'granola'         — Granola notes incremental cursor (ISO 8601 timestamp)
--   'drive_discover'  — last successful Drive folder discovery (timestamp;
--                       informational only — discovery is already idempotent)
--   'drive_workbook'  — last workbook re-import timestamp (informational)
--
-- Reserved one row per (owner, source) so cursor advances monotonically
-- per user; clearing the row forces a full re-sync next click.
-- NOTE: column is `cursor_value`, not `cursor`. `cursor` is a reserved
-- keyword in Postgres; using it unquoted in DDL aborts the schema apply on
-- the production Postgres backend (SQLite is permissive and accepts it).
CREATE TABLE IF NOT EXISTS pr_sync_state (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source        TEXT    NOT NULL,
    cursor_value  TEXT    NOT NULL DEFAULT '',
    last_run_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    last_status   TEXT    NOT NULL DEFAULT '',
    UNIQUE(owner_id, source)
);
CREATE INDEX IF NOT EXISTS idx_pr_sync_state_owner ON pr_sync_state(owner_id, source);
"""


def apply_schema(conn) -> None:
    """Apply the portfolio_review schema to an existing connection.

    Two-phase apply for cross-backend safety:
      1. CREATE TABLE statements run together; commit once. On SQLite this
         is moot, but on Postgres it ensures the freshly-created tables are
         durable BEFORE the migration loop runs.
      2. Each additive migration runs in its own commit. On Postgres a
         single ALTER failing (e.g. 'column already exists') aborts the
         current transaction; without per-stmt commits, that abort would
         poison every later migration AND the CREATE TABLE work.
    """
    conn.executescript(PR_SCHEMA_SQL)
    conn.commit()

    # Additive migrations — keep these append-only so old DBs can catch up.
    _MIGRATIONS = [
        "ALTER TABLE pr_traction_snapshots ADD COLUMN deal_lead          TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE pr_traction_snapshots ADD COLUMN narrative_raw      TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE pr_traction_snapshots ADD COLUMN fundraising_status TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE pr_traction_snapshots ADD COLUMN deck_row_index     INTEGER",
        # Granola audit completeness — track existing-note refreshes alongside
        # net-new associations so the operator can see whether a sync added
        # anything vs. just reconfirmed prior matches.
        "ALTER TABLE pr_granola_syncs ADD COLUMN associations_updated INTEGER NOT NULL DEFAULT 0",
        # Rename `cursor` → `cursor_value` for local SQLite DBs that were
        # created before the Postgres-keyword fix. On Postgres this is a
        # no-op because the table never existed under the old schema (the
        # reserved-keyword error aborted the whole executescript, leaving
        # the CREATE TABLE above to create the renamed column on first
        # success — and this rename then fails harmlessly).
        "ALTER TABLE pr_sync_state RENAME COLUMN cursor TO cursor_value",
        # LLM-generated derisking scores. `evaluator` lets human-imported
        # rows (from the Excel workbook) and AI-generated rows coexist
        # under different `period` strings (e.g. '2025' vs '2025 LLM');
        # the JSON columns store per-dimension reasoning + evidence so the
        # operator can audit and override.
        "ALTER TABLE pr_derisking_scores ADD COLUMN evaluator        TEXT NOT NULL DEFAULT 'human'",
        "ALTER TABLE pr_derisking_scores ADD COLUMN model_used       TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE pr_derisking_scores ADD COLUMN reasoning_json   TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE pr_derisking_scores ADD COLUMN evidence_summary TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE pr_derisking_scores ADD COLUMN confidence       TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE pr_derisking_scores ADD COLUMN source_files     TEXT NOT NULL DEFAULT ''",
    ]
    for stmt in _MIGRATIONS:
        try:
            conn.execute(stmt)
            conn.commit()
        except Exception:
            # 'duplicate column' / 'column does not exist' / Postgres aborted
            # transaction — in all cases roll back so the next migration
            # starts with a clean transaction.
            try:
                conn.rollback()
            except Exception:
                pass
