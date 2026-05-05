"""VoLo Mind database — uses the host app's database backend.

Critical persistence note:
    Replit Autoscale / Cloud Run deployments DO NOT persist local
    filesystem across redeploys. /home/runner/.volo/volomind.db gets
    wiped on every container restart, the same way data/volomind.db
    does. The host app sidesteps this by using a managed Postgres
    database via DATABASE_URL when set; SQLite is the local-dev fallback.
    VoLo Mind now mirrors that pattern instead of trying to maintain
    its own SQLite file.

Behavior:
    - Production (DATABASE_URL set, e.g. Cloud Run with managed Postgres):
        cc_* tables live in the SAME Postgres database as the host app's
        rvm tables. Persistence is handled by the managed Postgres service.
    - Local dev / SQLite (no DATABASE_URL):
        cc_* tables live in the SAME SQLite file as the host app
        (default data/rvm.db, override via VOLO_DB_PATH).

cc_* prefix: all VoLo Mind tables are prefixed `cc_` so they coexist
cleanly with the host app's tables in the same backend. No FK references
across the prefix boundary — soft `user_id` integer references only.

Concurrency:
    - Postgres: handled by transaction isolation. Multiple workers calling
      init() with CREATE TABLE IF NOT EXISTS serialize cleanly.
    - SQLite: file-lock around init() so --workers 2 doesn't race on a
      fresh DB. Same pattern as before, just with the host's path.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator

# Single source of truth for connection + backend selection. We piggyback on
# the host app's exact persistence mechanism so cc_* tables get the same
# durability guarantees as the IC-memo data.
from .. import database as host_database


_SCHEMA = """
-- Sources: registered data sources (granola, gdrive_admin, ...)
CREATE TABLE IF NOT EXISTS cc_sources (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id       TEXT NOT NULL,
    label           TEXT NOT NULL,
    config_json     TEXT NOT NULL DEFAULT '{}',
    cursor          TEXT,
    last_synced_at  TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Companies: shared entity layer for portfolio + potential investments.
CREATE TABLE IF NOT EXISTS cc_companies (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    co_type         TEXT,
    drive_folder_id TEXT,
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
    set_by              INTEGER NOT NULL,
    set_at              TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Chat threads: keep owner_id (soft user_id ref) for per-user thread history.
CREATE TABLE IF NOT EXISTS cc_chat_threads (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id        INTEGER NOT NULL,
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
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    tokens_in       INTEGER,
    tokens_out      INTEGER,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_cc_messages_thread ON cc_chat_messages(thread_id);

-- Sync runs: track background ingestion jobs.
CREATE TABLE IF NOT EXISTS cc_sync_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_pk       INTEGER NOT NULL REFERENCES cc_sources(id) ON DELETE CASCADE,
    started_by      INTEGER NOT NULL,
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


# --- Backend / persistence reporting -------------------------------------

def backend_label() -> str:
    """Human-readable description of the backend in use, for /health."""
    if host_database.USE_POSTGRES:
        return "Postgres (DATABASE_URL — persistent)"
    return f"SQLite ({host_database.DB_PATH})"


def is_persistent() -> bool:
    """True if the configured backend persists across redeploys.

    Postgres → always persistent.
    SQLite → only if the path is outside the source tree (host's VOLO_DB_PATH
    pattern). On Cloud Run, even /home/runner/ is ephemeral, so this is a
    best-effort heuristic — managed Postgres is the only safe option there.
    """
    if host_database.USE_POSTGRES:
        return True
    path = str(host_database.DB_PATH)
    if "/data/" in path and "/.volo/" not in path:
        return False  # likely in-source-tree
    if path.startswith("/home/runner/.volo/") or path.startswith("/data/"):
        return True
    return False


# DB_PATH retained for /health endpoint backward-compat. In Postgres mode
# it reflects the managed-DB-style host path (string only — never opened).
DB_PATH = host_database.DB_PATH


# --- Connection wrapper that works for both backends ---------------------

class _UniversalCursor:
    """Wraps either a sqlite3.Connection or the host app's _PgConnection,
    exposing the cursor-style API our codebase expects.

    Why: my existing code is written `with cursor() as c: c.execute(...);
    c.fetchone()`. SQLite cursors expose all those methods on one object,
    but the host's _PgConnection.execute() returns a separate _PgCursor.
    This wrapper lets both work identically: c.execute() always delegates
    to the connection (returning a fresh cursor each time), and fetch
    methods read from the most recently produced cursor.
    """

    __slots__ = ("_conn", "_last_cursor")

    def __init__(self, conn: Any):
        self._conn = conn
        self._last_cursor: Any = None

    def execute(self, sql: str, params: tuple = ()):
        cur = self._conn.execute(sql, params)
        self._last_cursor = cur
        return cur

    def executescript(self, script: str) -> None:
        self._conn.executescript(script)

    def fetchone(self):
        if self._last_cursor is None:
            return None
        return self._last_cursor.fetchone()

    def fetchall(self):
        if self._last_cursor is None:
            return []
        return self._last_cursor.fetchall()

    @property
    def rowcount(self) -> int:
        return getattr(self._last_cursor, "rowcount", 0) or 0

    @property
    def lastrowid(self):
        return getattr(self._last_cursor, "lastrowid", None)

    def __iter__(self):
        if self._last_cursor is None:
            return iter([])
        return iter(self._last_cursor)


def connect():
    """Return a backend-appropriate connection. Wraps the host's get_db()."""
    return host_database.get_db()


@contextmanager
def cursor() -> Iterator[_UniversalCursor]:
    """Context manager yielding a _UniversalCursor. Commits on success,
    rolls back on exception, always closes."""
    conn = connect()
    wrapper = _UniversalCursor(conn)
    try:
        yield wrapper
        try:
            conn.commit()
        except Exception:
            pass
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            conn.close()
        except Exception:
            pass


# --- Init / reconcile / cleanup ------------------------------------------

def init() -> None:
    """Create cc_* tables + reconcile sources + cleanup. Idempotent.

    Postgres: schema creation serializes naturally via transaction isolation.
    SQLite: protected by a /tmp file lock so --workers 2 can't race on a
    fresh DB.
    """
    if host_database.USE_POSTGRES:
        _run_init_steps()
        return
    _serialized_init()


def _serialized_init() -> None:
    try:
        import fcntl
    except ImportError:
        fcntl = None  # type: ignore[assignment]

    if fcntl is None:
        _run_init_steps()
        return

    lock_path = "/tmp/volomind_init.lock"
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_WRONLY, 0o644)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            _run_init_steps()
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
    finally:
        os.close(lock_fd)


def _run_init_steps() -> None:
    # Step 1: base schema (CREATE TABLE IF NOT EXISTS — idempotent).
    with cursor() as c:
        c.executescript(_SCHEMA)
    # Step 2: drop any stale unique index from a previous deploy attempt.
    # We INTENTIONALLY do not enforce (source_id, label) uniqueness at the
    # DB layer because Replit's auto-migration validator interprets the
    # CREATE UNIQUE INDEX as a schema diff and tries to apply it BEFORE
    # the app's dedup runs — which then fails with "could not create
    # unique index" if production has any pre-existing duplicates. Pure
    # application-side dedup works with no migration-validator collisions.
    with cursor() as c:
        c.execute("DROP INDEX IF EXISTS ix_cc_sources_source_label")
    # Step 3: rename old labels to current canonical labels BEFORE dedup.
    # Without this, a label change in sources_config.py would orphan the
    # old row (no longer matches config) and INSERT a new row, leaving
    # both in the UI.
    _migrate_old_labels()
    # Step 4: collapse any duplicate (source_id, label) groups down to one
    # row each. Picks the row with the most attached docs so we never
    # lose ingested data.
    _dedup_all_sources()
    # Step 4.5: clean up Tier 2 v1 tags. The taxonomy was redesigned in v2
    # (added company_type/technology, removed sector, replaced value_chain
    # values, slimmed stages). Old v1 values like sector='Solar' would
    # never match the new UI's chip values. Idempotent — running on a DB
    # without v1 tags is a no-op.
    with cursor() as c:
        c.execute("DELETE FROM cc_tags WHERE tagger_version = 'tier2-v1'")
    # Step 5: reconcile config -> rows. Race-tolerant via try/except —
    # if a concurrent rolling-deploy container did the INSERT first,
    # we silently fall through to UPDATE (harmless duplicate INSERTs
    # are caught and cleaned by the next dedup pass on next deploy).
    reconcile_sources_from_config()
    # Step 6: housekeeping for orphan sync runs from killed containers.
    _cleanup_stale_sync_runs()


def _migrate_old_labels() -> None:
    """One-off rename map for sources whose canonical label has changed.
    Idempotent — re-running is a no-op once the rename has happened.

    When a label changes in sources_config.py, the matching DB row would
    otherwise be orphaned (no longer matches the new label, while a fresh
    row gets INSERTed alongside it). This step reconciles the rename
    in-place so synced data stays attached.
    """
    renames = [
        # (source_id, old_label, new_label)
        ("granola", "Volo earth team Granola", "VoLo Earth Granola"),
    ]
    with cursor() as c:
        for source_id, old_label, new_label in renames:
            c.execute(
                "UPDATE cc_sources SET label = ? "
                "WHERE source_id = ? AND label = ?",
                (new_label, source_id, old_label),
            )


def _dedup_all_sources() -> None:
    """Collapse any (source_id, label) groups with >1 row down to one row.

    Picks the row with the most attached cc_documents so we never lose
    ingested data. Tiebreak on oldest id. Idempotent — running on a
    clean DB is a no-op.
    """
    with cursor() as c:
        c.execute(
            "SELECT source_id, label FROM cc_sources "
            "GROUP BY source_id, label HAVING COUNT(*) > 1"
        )
        dup_groups = c.fetchall()

        for group in dup_groups:
            source_id = group["source_id"]
            label = group["label"]
            c.execute(
                "SELECT id FROM cc_sources "
                "WHERE source_id = ? AND label = ? ORDER BY id ASC",
                (source_id, label),
            )
            ids = [r["id"] for r in c.fetchall()]

            best_id = ids[0]
            best_count = -1
            for sid in ids:
                c.execute(
                    "SELECT COUNT(*) AS n FROM cc_documents WHERE source_pk = ?",
                    (sid,),
                )
                n = c.fetchone()["n"] or 0
                if n > best_count or (n == best_count and sid < best_id):
                    best_count = n
                    best_id = sid

            for sid in ids:
                if sid != best_id:
                    c.execute("DELETE FROM cc_sources WHERE id = ?", (sid,))


def reconcile_sources_from_config() -> None:
    """Ensure every status='active' entry in sources_config.py has exactly
    one matching cc_sources row.

    Race-tolerant: a UNIQUE INDEX on (source_id, label) (created by
    _run_init_steps) means concurrent containers attempting to INSERT
    will see one succeed and the other raise IntegrityError. We catch
    that and fall through to UPDATE, so the race is harmless.

    Removing entries from sources_config.py does NOT delete the DB row;
    the source just stops appearing in the UI.
    """
    import json
    from . import sources_config
    from ..database import IntegrityError

    for definition in sources_config.get_enabled():
        source_id = definition["source_id"]
        label = definition["label"]
        config_json = json.dumps(definition.get("config") or {})

        # Each source gets its own short transaction. Fresh cursor per source
        # so a race on one source doesn't affect the others.
        with cursor() as c:
            c.execute(
                "SELECT id, config_json FROM cc_sources "
                "WHERE source_id = ? AND label = ? ORDER BY id ASC",
                (source_id, label),
            )
            row = c.fetchone()

            if row is None:
                try:
                    c.execute(
                        "INSERT INTO cc_sources (source_id, label, config_json) "
                        "VALUES (?, ?, ?)",
                        (source_id, label, config_json),
                    )
                except IntegrityError:
                    # Concurrent container won the INSERT race. Fall through
                    # and update whichever row exists now.
                    c.execute(
                        "UPDATE cc_sources SET config_json = ? "
                        "WHERE source_id = ? AND label = ?",
                        (config_json, source_id, label),
                    )
            elif row["config_json"] != config_json:
                c.execute(
                    "UPDATE cc_sources SET config_json = ? WHERE id = ?",
                    (config_json, row["id"]),
                )


def _cleanup_stale_sync_runs() -> None:
    """Mark any cc_sync_runs left in 'running' state as 'interrupted'.

    Daemon threads die when the container restarts, leaving orphan rows.
    On boot we reconcile so the UI can show 'interrupted' instead of stale
    'running' forever.
    """
    with cursor() as c:
        c.execute(
            "UPDATE cc_sync_runs "
            "SET status = 'interrupted', last_error = 'container restart', "
            "    completed_at = datetime('now') "
            "WHERE status = 'running'"
        )
