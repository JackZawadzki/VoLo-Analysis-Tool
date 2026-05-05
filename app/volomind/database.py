"""VoLo Mind — separate SQLite database.

Isolated from the host app's rvm.db on purpose:
- A schema-migration bug in this file can never touch production tables.
- Catastrophic data loss recovery is a single `rm` of the db file.
- WAL/lock contention is isolated.

Path resolution mirrors the host app's VOLO_DB_PATH pattern so Replit
Reserved-VM redeploys can persist data outside the source tree:
- Default: <repo>/data/volomind.db
- Override: VOLOMIND_DB_PATH=/home/runner/.volo/volomind.db (any absolute path)

Tables are prefixed `cc_` for legacy compatibility with the standalone
volo-context-chat repo this code is ported from. The schema is built fresh
here — no shared FKs to host-app tables. user_id columns are soft integer
references (no FK) since they point at users in a different database.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_DB = _REPO_ROOT / "data" / "volomind.db"


def _resolve_db_path() -> Path:
    env = os.environ.get("VOLOMIND_DB_PATH", "").strip()
    if env:
        p = Path(env)
    else:
        p = _DEFAULT_DB
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


DB_PATH: Path = _resolve_db_path()


_SCHEMA = """
-- Sources: registered data sources (granola, gdrive_admin, ...)
CREATE TABLE IF NOT EXISTS cc_sources (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id       TEXT NOT NULL,            -- 'granola' | 'gdrive_admin'
    label           TEXT NOT NULL,            -- human label, e.g. 'Volo earth team Granola'
    config_json     TEXT NOT NULL DEFAULT '{}',
    cursor          TEXT,                     -- incremental sync cursor (source-defined)
    last_synced_at  TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Companies: shared entity layer for portfolio + potential investments.
CREATE TABLE IF NOT EXISTS cc_companies (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    co_type         TEXT,                     -- 'portfolio' | 'potential' | NULL
    drive_folder_id TEXT,                     -- top-level Drive folder id when source=gdrive
    source_pk       INTEGER REFERENCES cc_sources(id) ON DELETE SET NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(name)
);
CREATE INDEX IF NOT EXISTS ix_cc_companies_name ON cc_companies(name);

-- Documents: ingested content. Shared (no owner_id).
CREATE TABLE IF NOT EXISTS cc_documents (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    source_pk            INTEGER NOT NULL REFERENCES cc_sources(id) ON DELETE CASCADE,
    source_id            TEXT NOT NULL,
    source_doc_id        TEXT NOT NULL,
    source_url           TEXT,
    title                TEXT NOT NULL,
    body_text            TEXT NOT NULL,
    body_tokens          INTEGER NOT NULL DEFAULT 0,
    content_hash         TEXT NOT NULL,
    occurred_at          TEXT,
    folder_path          TEXT,
    author               TEXT,
    attendees_json       TEXT,
    source_metadata_json TEXT NOT NULL DEFAULT '{}',
    source_updated_at    TEXT,
    fetched_at           TEXT NOT NULL DEFAULT (datetime('now')),
    company_id           INTEGER REFERENCES cc_companies(id) ON DELETE SET NULL,
    UNIQUE(source_pk, source_doc_id)
);
CREATE INDEX IF NOT EXISTS ix_cc_documents_source ON cc_documents(source_id);
CREATE INDEX IF NOT EXISTS ix_cc_documents_occurred_at ON cc_documents(occurred_at);
CREATE INDEX IF NOT EXISTS ix_cc_documents_company ON cc_documents(company_id);

-- Segments: sub-document chunks (markdown headers split per-section).
CREATE TABLE IF NOT EXISTS cc_segments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id     INTEGER NOT NULL REFERENCES cc_documents(id) ON DELETE CASCADE,
    segment_index   INTEGER NOT NULL,
    segment_label   TEXT,
    body_text       TEXT NOT NULL,
    body_tokens     INTEGER NOT NULL DEFAULT 0,
    content_hash    TEXT NOT NULL,
    UNIQUE(document_id, segment_index)
);
CREATE INDEX IF NOT EXISTS ix_cc_segments_doc ON cc_segments(document_id);

-- Tags: scope filter dimensions. source = 'rule' | 'llm_auto' | 'manual'.
CREATE TABLE IF NOT EXISTS cc_tags (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id     INTEGER NOT NULL REFERENCES cc_documents(id) ON DELETE CASCADE,
    segment_id      INTEGER REFERENCES cc_segments(id) ON DELETE CASCADE,
    dimension       TEXT NOT NULL,
    value           TEXT NOT NULL,
    source          TEXT NOT NULL,
    confidence      REAL,
    tagger_version  TEXT NOT NULL DEFAULT 'v1',
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(document_id, segment_id, dimension, value, source, tagger_version)
);
CREATE INDEX IF NOT EXISTS ix_cc_tags_lookup ON cc_tags(dimension, value);
CREATE INDEX IF NOT EXISTS ix_cc_tags_doc ON cc_tags(document_id);

-- Tag overrides: schema only for now. Manual analyst corrections.
CREATE TABLE IF NOT EXISTS cc_tag_overrides (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id         INTEGER NOT NULL REFERENCES cc_documents(id) ON DELETE CASCADE,
    segment_id          INTEGER REFERENCES cc_segments(id) ON DELETE CASCADE,
    dimension           TEXT NOT NULL,
    add_values_json     TEXT NOT NULL DEFAULT '[]',
    remove_values_json  TEXT NOT NULL DEFAULT '[]',
    set_by              INTEGER NOT NULL,    -- soft user_id ref into rvm.db
    set_at              TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Chat threads: keep owner_id (soft user_id ref) for per-user thread history.
CREATE TABLE IF NOT EXISTS cc_chat_threads (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id        INTEGER NOT NULL,        -- soft user_id ref into rvm.db
    title           TEXT NOT NULL,
    scope_json      TEXT NOT NULL,
    bundle_hash     TEXT,
    model_key       TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_cc_threads_owner ON cc_chat_threads(owner_id);

CREATE TABLE IF NOT EXISTS cc_chat_messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id       INTEGER NOT NULL REFERENCES cc_chat_threads(id) ON DELETE CASCADE,
    role            TEXT NOT NULL,           -- 'user' | 'assistant' | 'system'
    content         TEXT NOT NULL,
    tokens_in       INTEGER,
    tokens_out      INTEGER,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_cc_messages_thread ON cc_chat_messages(thread_id);

-- Sync runs: track background ingestion jobs. Long syncs (e.g. 36K-file
-- Drive walk) run in a daemon thread that updates this row every ~50 docs
-- so the frontend can poll for progress without keeping an HTTP request
-- open. status: 'running' | 'complete' | 'error' | 'interrupted'.
CREATE TABLE IF NOT EXISTS cc_sync_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_pk       INTEGER NOT NULL REFERENCES cc_sources(id) ON DELETE CASCADE,
    started_by      INTEGER NOT NULL,        -- soft user_id ref into rvm.db
    status          TEXT NOT NULL DEFAULT 'running',
    fetched         INTEGER NOT NULL DEFAULT 0,
    inserted        INTEGER NOT NULL DEFAULT 0,
    skipped         INTEGER NOT NULL DEFAULT 0,
    errors_json     TEXT NOT NULL DEFAULT '[]',
    last_error      TEXT,
    started_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at    TEXT
);
CREATE INDEX IF NOT EXISTS ix_cc_sync_runs_source ON cc_sync_runs(source_pk);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def cursor() -> Iterator[sqlite3.Cursor]:
    conn = connect()
    try:
        yield conn.cursor()
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init() -> None:
    """Run all CREATE TABLE IF NOT EXISTS statements + reconcile sources from
    the config file + clean up stale sync runs. Idempotent.
    """
    with cursor() as c:
        c.executescript(_SCHEMA)
    reconcile_sources_from_config()
    _cleanup_stale_sync_runs()


def _cleanup_stale_sync_runs() -> None:
    """Mark any cc_sync_runs left in 'running' state as 'interrupted'.

    Daemon threads die when the container restarts, leaving orphan rows.
    On boot we reconcile so the UI can show "interrupted — click sync to
    resume" instead of stale "running" forever.
    """
    with cursor() as c:
        c.execute(
            "UPDATE cc_sync_runs "
            "SET status = 'interrupted', last_error = 'container restart', "
            "    completed_at = datetime('now') "
            "WHERE status = 'running'"
        )


def reconcile_sources_from_config() -> None:
    """Ensure every status='active' entry in sources_config.py has a matching
    cc_sources row. Looks up by (source_id, label). Updates config_json if it
    changed. Never deletes — removing entries from the config file just hides
    them from the UI; the DB row and its data persist.
    """
    import json
    from . import sources_config

    with cursor() as c:
        for definition in sources_config.get_enabled():
            source_id = definition["source_id"]
            label = definition["label"]
            config_json = json.dumps(definition.get("config") or {})

            row = c.execute(
                "SELECT id, config_json FROM cc_sources WHERE source_id = ? AND label = ?",
                (source_id, label),
            ).fetchone()

            if row is None:
                c.execute(
                    "INSERT INTO cc_sources (source_id, label, config_json) VALUES (?, ?, ?)",
                    (source_id, label, config_json),
                )
            elif row["config_json"] != config_json:
                c.execute(
                    "UPDATE cc_sources SET config_json = ? WHERE id = ?",
                    (config_json, row["id"]),
                )
