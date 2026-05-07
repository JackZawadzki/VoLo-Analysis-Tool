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


def _note_in_allowed_folders(folder_membership: list, allowed: set[str]) -> bool:
    """True if any of the note's folder_membership entries is in `allowed`.

    Granola's folder_membership is a list of {name, id, ...}. We check
    every entry, not just the first, because a single note can live in
    multiple folders (e.g. "Portco Updates" + a company-specific folder).
    """
    if not folder_membership:
        return False
    for f in folder_membership:
        if not isinstance(f, dict):
            continue
        if _normalize(f.get("name") or "") in allowed:
            return True
    return False


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
    include_transcripts: bool = False,
) -> dict:
    """Pull recent Granola notes from the allowed folders and link matched
    notes to portfolio companies.

    Args:
        conn: existing sqlite3 connection (host DB; pr_* tables live here).
        user_id: caller's user id, recorded on the audit row. Optional.
        allowed_folders: override the folder allowlist (defaults to env or
            the Investment Committee / Portco Updates / Screening +
            Rapid Fire trio).
        cursor: ISO timestamp; only fetch notes updated after this. None =
            fetch everything (full first sync).
        include_transcripts: forwarded to GranolaConnector — keep False
            unless you need full transcript text in the body.

    Returns a dict suitable as a JSON API response:
        {
          "status": "success" | "partial" | "failed",
          "notes_fetched": int,                # total notes Granola returned
          "notes_in_scope": int,               # count after folder filter
          "associations_new": int,             # rows inserted to pr_granola_notes
          "associations_updated": int,         # rows updated (re-sync of existing)
          "associations_unmatched": int,       # in-scope notes with no company match
          "allowed_folders": [...],
          "next_cursor": str | None,
        }
    """
    from ..volomind.connectors.granola import GranolaConnector

    allowed = _resolve_allowed_folders(allowed_folders)
    if not allowed:
        raise ValueError("granola_sync requires at least one allowed folder")

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
    last_seen_cursor = cursor
    error_summary = ""
    status = "success"

    try:
        connector = GranolaConnector(
            config={"include_transcripts": include_transcripts},
            cursor=cursor,
        )
        for raw in connector.list_documents():
            notes_fetched += 1
            metadata = raw.source_metadata or {}
            folder_membership = metadata.get("folder_membership") or []
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
                  SET finished_at      = datetime('now'),
                      status           = ?,
                      notes_fetched    = ?,
                      associations_new = ?,
                      associations_skip= ?,
                      error_summary    = ?
                WHERE id = ?""",
            (status, notes_fetched, associations_new, associations_unmatched,
             error_summary, sync_id),
        )
        conn.commit()

    return {
        "status": status,
        "notes_fetched": notes_fetched,
        "notes_in_scope": notes_in_scope,
        "associations_new": associations_new,
        "associations_updated": associations_updated,
        "associations_unmatched": associations_unmatched,
        "allowed_folders": sorted(allowed),
        "next_cursor": last_seen_cursor,
        "error": error_summary or None,
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
