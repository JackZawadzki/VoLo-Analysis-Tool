"""
Standalone document ingestion for Portfolio Review.

Replicates VoLo Mind's transfer pattern (one Drive subfolder = one company,
extract text from PDF/DOCX/PPTX/XLSX, idempotent upsert) but keeps Portfolio
Review self-contained — it does NOT require VoLo Mind to be enabled. Documents
land in pr_documents, which the two-pass LLM (`portfolio_update.py`) reads.

Two entry points:
  • discover_companies()  — list the portfolio root's subfolders and upsert one
                            pr_companies row per folder (the source of truth for
                            "what is in the portfolio").
  • ingest_company()      — walk a company's folder(s), download + extract every
                            supported file into pr_documents.

Granola notes already synced into pr_granola_notes are mirrored into
pr_documents (source='granola') so the LLM sees docs + notes in one place.

Drive/Granola access needs per-user OAuth + API keys, so this runs in the
deployed environment; locally we seed pr_documents directly (see seed_demo).
"""

from __future__ import annotations

import hashlib
import logging
from typing import Optional

from .drive_scan import (
    list_subfolders, _list_files_recursive, _classify_file,
    _download_and_extract_text, _normalize_name,
)

logger = logging.getLogger(__name__)


def _content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _est_tokens(text: str) -> int:
    return max(1, len(text or "") // 4)


def _upsert_document(conn, company_id: int, *, source: str, source_doc_id: str,
                     title: str, doc_type: str, mime_type: str, source_url: str,
                     folder_path: str, body_text: str, occurred_at: Optional[str],
                     source_modified: Optional[str]) -> tuple[int, bool]:
    """Idempotent on (company_id, source, source_doc_id). Returns (id, changed)."""
    chash = _content_hash(body_text)
    row = conn.execute(
        "SELECT id, content_hash FROM pr_documents "
        "WHERE company_id=? AND source=? AND source_doc_id=?",
        (company_id, source, source_doc_id),
    ).fetchone()
    if row:
        if row["content_hash"] == chash:
            return row["id"], False
        conn.execute(
            """UPDATE pr_documents SET title=?, doc_type=?, mime_type=?, source_url=?,
               folder_path=?, body_text=?, body_tokens=?, content_hash=?,
               occurred_at=?, source_modified=?, fetched_at=datetime('now')
               WHERE id=?""",
            (title, doc_type, mime_type, source_url, folder_path, body_text,
             _est_tokens(body_text), chash, occurred_at, source_modified, row["id"]),
        )
        return row["id"], True
    cur = conn.execute(
        """INSERT INTO pr_documents (company_id, source, source_doc_id, title, doc_type,
           mime_type, source_url, folder_path, body_text, body_tokens, content_hash,
           occurred_at, source_modified)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (company_id, source, source_doc_id, title, doc_type, mime_type, source_url,
         folder_path, body_text, _est_tokens(body_text), chash, occurred_at, source_modified),
    )
    return cur.lastrowid, True


# ── Company discovery (folder = company) ──────────────────────────────────────
def discover_companies(conn, service, root_folder_id: str, default_fund: str = "Fund I") -> dict:
    """List immediate subfolders of the portfolio root and upsert a pr_companies
    row per folder. The Drive folder is the source of truth for the portfolio
    roster — NOT the deal-report library."""
    subfolders = list_subfolders(service, root_folder_id)
    created, updated = 0, 0
    for f in subfolders:
        name = (f.get("name") or "").strip()
        if not name:
            continue
        existing = conn.execute("SELECT id FROM pr_companies WHERE name=?", (name,)).fetchone()
        if existing:
            cid = existing["id"]
            updated += 1
        else:
            cur = conn.execute(
                "INSERT INTO pr_companies (name, fund) VALUES (?, ?)", (name, default_fund))
            cid = cur.lastrowid
            created += 1
        # Link the folder (folder_type='current'); idempotent on (company, folder_type)
        link = conn.execute(
            "SELECT id FROM pr_company_folders WHERE company_id=? AND folder_type='current'", (cid,)
        ).fetchone()
        if not link:
            conn.execute(
                """INSERT INTO pr_company_folders
                   (company_id, folder_type, drive_folder_id, drive_folder_name, parent_folder_id, match_confidence)
                   VALUES (?, 'current', ?, ?, ?, 'exact')""",
                (cid, f["id"], name, root_folder_id))
    conn.commit()
    return {"subfolders": len(subfolders), "companies_created": created,
            "companies_updated": updated}


# ── Document ingestion for one company ────────────────────────────────────────
def ingest_company(conn, service, company_id: int, max_files: int = 60) -> dict:
    """Walk every linked Drive folder for one company, extract text from each
    supported file, and upsert into pr_documents. Also mirrors Granola notes."""
    folders = conn.execute(
        "SELECT * FROM pr_company_folders WHERE company_id=?", (company_id,)).fetchall()
    seen, inserted, changed, skipped = 0, 0, 0, 0
    for fol in folders:
        try:
            files = _list_files_recursive(service, fol["drive_folder_id"])
        except Exception as e:
            logger.warning("List failed for folder %s: %s", fol["drive_folder_name"], e)
            continue
        for fmeta in files[:max_files]:
            seen += 1
            text = _download_and_extract_text(service, fmeta)
            if not text:
                skipped += 1
                continue
            _, was_changed = _upsert_document(
                conn, company_id,
                source="drive", source_doc_id=fmeta["id"],
                title=fmeta.get("name", ""),
                doc_type=_classify_file(fmeta) or "other",
                mime_type=fmeta.get("mimeType", ""),
                source_url=fmeta.get("webViewLink", ""),
                folder_path=fol.get("drive_folder_name", ""),
                body_text=text,
                occurred_at=(fmeta.get("modifiedTime") or "")[:10] or None,
                source_modified=fmeta.get("modifiedTime"),
            )
            inserted += 1
            changed += 1 if was_changed else 0
    granola = mirror_granola_to_documents(conn, company_id)
    conn.commit()
    return {"files_seen": seen, "documents_upserted": inserted,
            "documents_changed": changed, "skipped": skipped, "granola": granola}


def mirror_granola_to_documents(conn, company_id: int) -> int:
    """Copy already-synced Granola notes (pr_granola_notes) into pr_documents so
    the LLM reads docs + notes from one table."""
    try:
        notes = conn.execute(
            "SELECT granola_note_id, note_title, note_summary, note_url, note_updated_at "
            "FROM pr_granola_notes WHERE company_id=?", (company_id,)).fetchall()
    except Exception:
        return 0
    n = 0
    for nt in notes:
        body = nt["note_summary"] or nt["note_title"] or ""
        if not body:
            continue
        _upsert_document(
            conn, company_id,
            source="granola", source_doc_id=nt["granola_note_id"],
            title=nt["note_title"] or "Granola note", doc_type="note",
            mime_type="text/granola", source_url=nt["note_url"] or "",
            folder_path="Granola", body_text=body,
            occurred_at=(nt["note_updated_at"] or "")[:10] or None,
            source_modified=nt["note_updated_at"],
        )
        n += 1
    return n
