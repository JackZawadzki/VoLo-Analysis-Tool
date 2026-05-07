"""Granola → Portfolio Review bridge.

Reuses the existing volomind GranolaConnector to pull team meeting notes,
filters to the folders that matter for portfolio monitoring, and links each
note to one or more `pr_companies` rows so the Portfolio Review tab can
surface relevant meeting context per company.

Folder filter
─────────────
The VoLo Earth Granola workspace has many folders. For portfolio
monitoring we only care about three by default:

    • Investment Committee
    • Portco Updates
    • Screening + Rapid Fire Meeting

A note is included if it appears in ANY of the allowed folders (Granola
notes can have multi-folder membership). Override the allowlist via the
PORTFOLIO_GRANOLA_FOLDERS env var (comma-separated), or pass a custom
list to `run_granola_sync()`.

Match heuristics
────────────────
For each included note, we attempt to associate it with a portfolio
company via two cheap, deterministic checks (no LLM call):

    1. attendee_email — any attendee email matches a `pr_companies.ceo_email`
       or `pr_companies.cfo_email` (high confidence).
    2. title_match — a `pr_companies.name` appears as a whole-word
       substring of the note title (medium confidence).

A note can match multiple companies (e.g. a "Portco Updates" round-up
covering 5 companies); in that case it is associated with each. Notes
that match no company are dropped — they aren't relevant to portfolio
review.

Re-running the sync is idempotent: associations are upserted by
(company_id, granola_note_id), so existing links update their summary
text rather than duplicating.

Auth model
──────────
Same as the volomind GranolaConnector — uses GRANOLA_API_KEY (Enterprise
key from Replit Secrets). No per-user OAuth needed for Granola because
the Enterprise key already scopes to the team workspace.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from typing import Iterable, Optional

logger = logging.getLogger(__name__)


# Default folder allowlist — override via PORTFOLIO_GRANOLA_FOLDERS env var
# (comma-separated). Names are matched case-insensitively after stripping
# whitespace, so "investment committee" / "Investment  Committee" / etc.
# all work.
_DEFAULT_ALLOWED_FOLDERS = (
    "Investment Committee",
    "Portco Updates",
    "Screening + Rapid Fire Meeting",
)


def _resolve_allowed_folders(custom: Optional[Iterable[str]] = None) -> set[str]:
    """Resolve the active folder allowlist (lowercased, normalized)."""
    if custom is not None:
        names = list(custom)
    else:
        env = os.environ.get("PORTFOLIO_GRANOLA_FOLDERS", "").strip()
        names = [n for n in (s.strip() for s in env.split(",")) if n] if env else list(_DEFAULT_ALLOWED_FOLDERS)
    return {_normalize(n) for n in names if n}


def _normalize(s: str) -> str:
    """Lowercase + collapse whitespace for forgiving folder name matching."""
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _folder_entry_names(entry) -> list[str]:
    """Extract every plausible "folder name" from one folder_membership entry.

    Granola's API has historically returned folder_membership as
    [{"id": ..., "name": "..."}], but we don't want to break if they
    rename the field to "title" or "folder_name" or start returning bare
    strings. We try several common shapes and return everything we find;
    the caller normalizes + matches against the allowlist.
    """
    if entry is None:
        return []
    if isinstance(entry, str):
        return [entry]
    if isinstance(entry, dict):
        out: list[str] = []
        # Direct fields first
        for key in ("name", "title", "folder_name", "label"):
            v = entry.get(key)
            if isinstance(v, str) and v.strip():
                out.append(v)
        # Sometimes the shape is { "folder": { "name": "..." } }
        nested = entry.get("folder")
        if isinstance(nested, dict):
            v = nested.get("name") or nested.get("title")
            if isinstance(v, str) and v.strip():
                out.append(v)
        return out
    return []


def _note_in_allowed_folders(folder_membership: list, allowed: set[str]) -> bool:
    """True if any of the note's folder_membership entries is in `allowed`.

    Granola's folder_membership is a list of {name, id, ...}. We check
    every entry, not just the first, because a single note can live in
    multiple folders (e.g. "Portco Updates" + a company-specific folder).
    Robust to a few alternative shapes — see _folder_entry_names.
    """
    if not folder_membership:
        return False
    for f in folder_membership:
        for name in _folder_entry_names(f):
            if _normalize(name) in allowed:
                return True
    return False


def _all_folder_names_from_entry(entry) -> list[str]:
    """Like _folder_entry_names but used by the diagnostic counter — returns
    the un-normalized names so the audit response shows what Granola
    actually called the folder, not the lowercased form."""
    return _folder_entry_names(entry)


def _matched_folder_names(folder_membership: list, allowed: set[str]) -> list[str]:
    """Return the original (un-normalized) folder names that matched the
    allowlist — used for audit/debugging in the response payload."""
    matched: list[str] = []
    if not folder_membership:
        return matched
    for f in folder_membership:
        if not isinstance(f, dict):
            continue
        name = (f.get("name") or "").strip()
        if name and _normalize(name) in allowed:
            matched.append(name)
    return matched


# ── Company matching ─────────────────────────────────────────────────────────


def _load_company_lookup(conn: sqlite3.Connection) -> dict:
    """Build the in-memory lookup tables used for company matching.

    Returns:
        {
          "by_email": {lowercased_email: company_id, ...},
          "by_name":  [(company_id, lowercased_name, original_name), ...],
        }
    The list-of-tuples for names is intentional — substring matching needs
    to iterate, and the count of portfolio companies is small (<200) so a
    linear scan is fine and avoids regex pre-compilation cost.
    """
    rows = conn.execute(
        "SELECT id, name, ceo_email, cfo_email FROM pr_companies"
    ).fetchall()
    by_email: dict[str, int] = {}
    by_name: list[tuple] = []
    for r in rows:
        cid = r["id"] if hasattr(r, "keys") else r[0]
        name = (r["name"] if hasattr(r, "keys") else r[1]) or ""
        ceo = (r["ceo_email"] if hasattr(r, "keys") else r[2]) or ""
        cfo = (r["cfo_email"] if hasattr(r, "keys") else r[3]) or ""
        for em in (ceo, cfo):
            em = em.strip().lower()
            if em:
                by_email[em] = cid
        if name.strip():
            by_name.append((cid, name.strip().lower(), name.strip()))
    return {"by_email": by_email, "by_name": by_name}


def _match_companies(
    *,
    title: str,
    attendees: list,
    attendee_emails: list,
    lookup: dict,
) -> list[tuple[int, str, str]]:
    """Return [(company_id, match_method, confidence), ...] for one note.

    A note can match multiple companies. Order: attendee-email matches
    first (high confidence), then title matches (medium). Dedupes on
    company_id — if the same company matches both ways, we keep the
    higher-confidence record."""
    matched: dict[int, tuple[str, str]] = {}

    # Pass 1 — attendee emails (high confidence)
    for em in attendee_emails:
        em = (em or "").strip().lower()
        if em and em in lookup["by_email"]:
            matched[lookup["by_email"][em]] = ("attendee_email", "high")

    # Pass 2 — name in title (medium confidence)
    title_norm = (title or "").lower()
    if title_norm:
        for cid, name_lc, _orig in lookup["by_name"]:
            if cid in matched:
                continue
            # Whole-word substring — guards against e.g. "Aria" matching "Pariah"
            if re.search(rf"\b{re.escape(name_lc)}\b", title_norm):
                matched[cid] = ("title_match", "medium")

    return [(cid, m, c) for cid, (m, c) in matched.items()]


# ── Per-user cursor persistence ──────────────────────────────────────────────


def get_stored_cursor(conn: sqlite3.Connection, *, user_id: Optional[int]) -> Optional[str]:
    """Read the most recent successful sync's high-water-mark timestamp
    for this user. Returns None on first sync (no row yet) or if the
    user_id is missing — both are treated as "fetch everything"."""
    if not user_id:
        return None
    row = conn.execute(
        "SELECT cursor FROM pr_sync_state WHERE owner_id=? AND source='granola'",
        (user_id,),
    ).fetchone()
    if row is None:
        return None
    cursor = row["cursor"] if hasattr(row, "keys") else row[0]
    return cursor or None


def set_stored_cursor(
    conn: sqlite3.Connection,
    *,
    user_id: Optional[int],
    cursor: Optional[str],
    status: str,
) -> None:
    """Persist the high-water-mark cursor after a sync.

    Cursor advances ONLY on success — failed/partial syncs update the
    last_run_at + last_status fields but leave the cursor untouched
    (including on the first-ever failed sync, where the new row is
    inserted with an empty cursor).

    Two SQL paths because SQLite's UPSERT can't conditionally branch
    the INSERT side; the prior single-statement form silently wrote a
    bad cursor on the first failed sync.
    """
    if not user_id:
        return
    advance = bool(cursor) and status == "success"
    if advance:
        # Success path — write or advance the cursor.
        conn.execute(
            """INSERT INTO pr_sync_state (owner_id, source, cursor, last_run_at, last_status)
               VALUES (?, 'granola', ?, datetime('now'), ?)
               ON CONFLICT(owner_id, source) DO UPDATE SET
                   cursor      = excluded.cursor,
                   last_run_at = excluded.last_run_at,
                   last_status = excluded.last_status""",
            (user_id, cursor, status),
        )
    else:
        # Failure / partial / no-cursor-returned path. Record the
        # attempt but leave the cursor field empty on first run, and
        # untouched on subsequent runs (the UPDATE clause omits the
        # cursor column entirely).
        conn.execute(
            """INSERT INTO pr_sync_state (owner_id, source, cursor, last_run_at, last_status)
               VALUES (?, 'granola', '', datetime('now'), ?)
               ON CONFLICT(owner_id, source) DO UPDATE SET
                   last_run_at = excluded.last_run_at,
                   last_status = excluded.last_status""",
            (user_id, status),
        )
    conn.commit()


def reset_stored_cursor(conn: sqlite3.Connection, *, user_id: Optional[int]) -> None:
    """Force the next sync to fetch everything from scratch. Useful if
    the operator wants to re-run a full sync (e.g. after fixing folder
    names) without manually deleting a row."""
    if not user_id:
        return
    conn.execute(
        "DELETE FROM pr_sync_state WHERE owner_id=? AND source='granola'",
        (user_id,),
    )
    conn.commit()


def _extract_attendee_emails(note: dict) -> list[str]:
    """Pull the email field out of Granola's attendee list. Format:
    [{name: "...", email: "..."}], occasionally just a list of strings."""
    out: list[str] = []
    for a in note.get("attendees") or []:
        if isinstance(a, dict):
            em = a.get("email")
            if em:
                out.append(str(em).strip().lower())
        elif isinstance(a, str) and "@" in a:
            out.append(a.strip().lower())
    return out


# ── Main entry point ─────────────────────────────────────────────────────────


def run_granola_sync(
    conn: sqlite3.Connection,
    *,
    user_id: Optional[int] = None,
    allowed_folders: Optional[Iterable[str]] = None,
    cursor: Optional[str] = None,
    include_transcripts: bool = True,
    reset: bool = False,
) -> dict:
    """Pull recent Granola notes from the allowed folders and link matched
    notes to portfolio companies.

    Incremental by default: reads the high-water-mark cursor from
    pr_sync_state for this user, asks Granola only for notes updated
    after it, and writes the new high-water-mark on success. First-ever
    sync (no row in pr_sync_state) fetches everything; subsequent syncs
    are fast incremental pulls.

    Args:
        conn: sqlite3 connection (host DB; pr_* tables live here).
        user_id: caller's user id. Required for cursor persistence
            (cursor is stored per-user). If None, this acts as a
            stateless one-shot sync.
        allowed_folders: override the folder allowlist (defaults to env
            or the Investment Committee / Portco Updates / Screening +
            Rapid Fire trio).
        cursor: explicit ISO timestamp override. None = read from
            pr_sync_state. Pass to force a custom starting point.
        include_transcripts: forwarded to GranolaConnector. Default True
            (matches volomind) so notes that only have a transcript (no
            summary_markdown) aren't silently dropped by _to_raw_document.
        reset: if True, ignores the stored cursor and runs a full
            re-sync. The new high-water-mark is written on success.
            Use to recover after fixing folder names or after a failed
            sync left the cursor in a confusing state.

    Returns:
        {status, notes_fetched, notes_in_scope, associations_new,
         associations_updated, associations_unmatched, allowed_folders,
         next_cursor, used_cursor, used_reset, diagnostics, error}
    """
    from ..volomind.connectors.granola import GranolaConnector

    allowed = _resolve_allowed_folders(allowed_folders)
    if not allowed:
        raise ValueError("granola_sync requires at least one allowed folder")

    # Cursor resolution priority: explicit `cursor` arg > stored cursor
    # > None (full sync). `reset=True` skips both and starts fresh.
    if reset:
        effective_cursor = None
    elif cursor is not None:
        effective_cursor = cursor
    else:
        effective_cursor = get_stored_cursor(conn, user_id=user_id)

    audit_row = conn.execute(
        "INSERT INTO pr_granola_syncs (user_id) VALUES (?)",
        (user_id,),
    )
    sync_id = audit_row.lastrowid
    conn.commit()

    lookup = _load_company_lookup(conn)
    notes_fetched = 0
    notes_in_scope = 0
    associations_new = 0
    associations_updated = 0
    associations_unmatched = 0
    last_seen_cursor = effective_cursor
    error_summary = ""
    status = "success"

    # Diagnostic accumulators — surfaced in the response so we can debug
    # "0 fetched" without adding ad-hoc logging. seen_folders is the set
    # of folder names Granola actually returned in folder_membership; if
    # this set is non-empty but doesn't match the allowlist, we know the
    # folder filter is the bottleneck. notes_with_no_folders counts
    # notes that arrived with empty folder_membership (which would be
    # silently dropped by the allowlist filter).
    seen_folders: set[str] = set()
    notes_with_no_folders = 0
    sample_folder_membership_shapes: list = []

    try:
        connector = GranolaConnector(
            config={"include_transcripts": include_transcripts},
            cursor=effective_cursor,
        )
        for raw in connector.list_documents():
            notes_fetched += 1
            metadata = raw.source_metadata or {}
            folder_membership = metadata.get("folder_membership") or []

            # Diagnostic: capture every folder name we see, plus the
            # raw shape of the first few entries, so the API response
            # tells the operator exactly what Granola is returning.
            for entry in folder_membership:
                for nm in _all_folder_names_from_entry(entry):
                    seen_folders.add(nm)
            if not folder_membership:
                notes_with_no_folders += 1
            elif len(sample_folder_membership_shapes) < 3:
                sample_folder_membership_shapes.append(folder_membership)

            if not _note_in_allowed_folders(folder_membership, allowed):
                continue
            notes_in_scope += 1

            # Reconstruct minimal note dict for matching helpers (the
            # connector returns a normalized RawDocument; we want the
            # original attendee shapes back, which it doesn't expose
            # directly — fall back to the strings it did set).
            note_for_match = {
                "attendees": [{"email": _maybe_email(a)} for a in raw.attendees],
            }
            attendee_emails = _extract_attendee_emails(note_for_match)

            matches = _match_companies(
                title=raw.title or "",
                attendees=raw.attendees or [],
                attendee_emails=attendee_emails,
                lookup=lookup,
            )
            if not matches:
                associations_unmatched += 1
                continue

            attendees_json = json.dumps(
                [{"name": a} for a in (raw.attendees or [])][:50],
                ensure_ascii=False,
            )

            for cid, method, conf in matches:
                cur = conn.execute(
                    """INSERT INTO pr_granola_notes
                       (company_id, granola_note_id, note_title, note_summary,
                        note_url, attendees_json, note_created_at,
                        note_updated_at, match_method, match_confidence)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(company_id, granola_note_id) DO UPDATE SET
                           note_title       = excluded.note_title,
                           note_summary     = excluded.note_summary,
                           note_url         = excluded.note_url,
                           attendees_json   = excluded.attendees_json,
                           note_updated_at  = excluded.note_updated_at,
                           match_method     = excluded.match_method,
                           match_confidence = excluded.match_confidence,
                           fetched_at       = datetime('now')""",
                    (
                        cid,
                        raw.source_doc_id,
                        raw.title or "",
                        raw.body_text or "",
                        raw.source_url or "",
                        attendees_json,
                        _iso_or_none(raw.occurred_at),
                        _iso_or_none(raw.source_updated_at),
                        method,
                        conf,
                    ),
                )
                # `cur.rowcount` is 1 for both inserts and updates with
                # SQLite's UPSERT, so we infer "new" by checking if the
                # row existed pre-insert. Cheap query, small table.
                if _was_pre_existing(conn, cid, raw.source_doc_id):
                    associations_updated += 1
                else:
                    associations_new += 1

            if raw.source_updated_at:
                iso = _iso_or_none(raw.source_updated_at)
                if iso and (last_seen_cursor is None or iso > last_seen_cursor):
                    last_seen_cursor = iso

        conn.commit()
    except Exception as e:
        logger.exception("granola_sync failed")
        error_summary = str(e)[:500]
        status = "failed"
    finally:
        conn.execute(
            """UPDATE pr_granola_syncs
                  SET finished_at         = datetime('now'),
                      status              = ?,
                      notes_fetched       = ?,
                      associations_new    = ?,
                      associations_skip   = ?,
                      associations_updated = ?,
                      error_summary       = ?
                WHERE id = ?""",
            (status, notes_fetched, associations_new, associations_unmatched,
             associations_updated, error_summary, sync_id),
        )
        conn.commit()

    # Persist the high-water-mark cursor (only advances on success — see
    # set_stored_cursor). Subsequent syncs read from here and only fetch
    # notes updated after this timestamp.
    set_stored_cursor(
        conn,
        user_id=user_id,
        cursor=last_seen_cursor,
        status=status,
    )

    return {
        "status": status,
        "notes_fetched": notes_fetched,
        "notes_in_scope": notes_in_scope,
        "associations_new": associations_new,
        "associations_updated": associations_updated,
        "associations_unmatched": associations_unmatched,
        "allowed_folders": sorted(allowed),
        "next_cursor": last_seen_cursor,
        "used_cursor": effective_cursor,        # what was actually used as the sync starting point
        "used_reset": bool(reset),
        "error": error_summary or None,
        # Diagnostics — exposed so an operator can see exactly why a sync
        # came back with 0 in-scope notes without spelunking through logs.
        "diagnostics": {
            # Every distinct folder name Granola returned across all
            # fetched notes. Compare to allowed_folders — if your folder
            # appears here under a different spelling, fix the env var
            # PORTFOLIO_GRANOLA_FOLDERS.
            "seen_folders": sorted(seen_folders),
            # Notes that arrived with empty folder_membership. These are
            # silently dropped by the allowlist filter today; a high count
            # here means many Granola notes aren't in any folder.
            "notes_with_no_folders": notes_with_no_folders,
            # Up to 3 raw folder_membership values, untouched, so the
            # operator can verify the JSON shape matches what
            # _folder_entry_names knows how to parse.
            "sample_folder_membership_shapes": sample_folder_membership_shapes,
        },
    }


# ── Diagnostic-only: raw API probe ───────────────────────────────────────────


def probe_granola_api(*, limit: int = 5) -> dict:
    """One-shot raw call to Granola's /v1/notes endpoint, bypassing the
    connector entirely. Used to answer "did Granola return any notes at
    all?" — independent of what the connector does to them.

    Returns:
        {
          "ok": True/False,
          "status_code": HTTP status from Granola,
          "raw_count": number of summaries in the first page,
          "sample_note_ids": first few note ids returned,
          "sample_owners": owners of the first few notes (Personal vs
              Enterprise key tell — Personal will only show one user),
          "first_detail_folder_membership": folder_membership of the
              first note's full detail (so we can verify the field shape),
          "error": str or None,
        }
    """
    import httpx
    api_key = (os.environ.get("GRANOLA_API_KEY") or "").strip()
    api_base = (
        os.environ.get("GRANOLA_API_BASE")
        or "https://public-api.granola.ai/v1"
    ).rstrip("/")
    if not api_key:
        return {
            "ok": False, "status_code": 0, "raw_count": 0,
            "sample_note_ids": [], "sample_owners": [],
            "first_detail_folder_membership": None,
            "error": "GRANOLA_API_KEY not set in environment",
        }
    try:
        with httpx.Client(
            base_url=api_base,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            },
            timeout=30.0,
        ) as client:
            resp = client.get("/notes", params={"page_size": limit})
            if resp.status_code != 200:
                snippet = resp.text[:300] if resp.text else ""
                return {
                    "ok": False, "status_code": resp.status_code,
                    "raw_count": 0, "sample_note_ids": [],
                    "sample_owners": [],
                    "first_detail_folder_membership": None,
                    "error": f"GET /notes returned {resp.status_code}: {snippet}",
                }
            page = resp.json()
            summaries = page.get("notes") or []
            sample_ids = [s.get("id") for s in summaries[:limit] if s.get("id")]
            sample_owners = []
            for s in summaries[:limit]:
                owner = s.get("owner") or {}
                em = owner.get("email") or owner.get("name")
                if em:
                    sample_owners.append(em)
            # Fetch the first note's full detail to inspect folder_membership
            first_fm = None
            if sample_ids:
                d = client.get(f"/notes/{sample_ids[0]}")
                if d.status_code == 200:
                    first_fm = d.json().get("folder_membership")
            return {
                "ok": True, "status_code": 200,
                "raw_count": len(summaries),
                "has_more": bool(page.get("hasMore")),
                "sample_note_ids": sample_ids,
                "sample_owners": sample_owners,
                "first_detail_folder_membership": first_fm,
                "api_base_used": api_base,
                "error": None,
            }
    except Exception as e:
        return {
            "ok": False, "status_code": 0, "raw_count": 0,
            "sample_note_ids": [], "sample_owners": [],
            "first_detail_folder_membership": None,
            "error": f"{type(e).__name__}: {str(e)[:300]}",
        }


# ── Tiny helpers ─────────────────────────────────────────────────────────────


def _was_pre_existing(conn, company_id: int, granola_note_id: str) -> bool:
    """We just upserted; check if `created_at` < `fetched_at` to decide
    whether this row was new or updated. Cheaper than a SELECT before the
    INSERT because most associations are net-new on first run."""
    row = conn.execute(
        """SELECT (julianday(fetched_at) - julianday(coalesce(note_updated_at, fetched_at))) > 0
             FROM pr_granola_notes
            WHERE company_id=? AND granola_note_id=?""",
        (company_id, granola_note_id),
    ).fetchone()
    if row is None:
        return False
    return bool(row[0])


def _maybe_email(s) -> str:
    """RawDocument.attendees is a list of strings (name or email). Pull
    out anything that looks like an email; otherwise return empty so the
    email-match pass simply skips it."""
    s = str(s or "").strip()
    return s if "@" in s else ""


def _iso_or_none(dt) -> Optional[str]:
    if dt is None:
        return None
    try:
        return dt.isoformat()
    except Exception:
        return str(dt)
