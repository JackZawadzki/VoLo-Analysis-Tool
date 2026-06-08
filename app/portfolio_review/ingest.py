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
import json
import logging
import os
import re
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
# Not every subfolder under the portfolio root is a company. The team convention
# is to prefix cross-cutting / admin folders with "_" (e.g. "_Cap Tables",
# "_Financials", "_Portco Meetings", "_SPAs"). We skip those, plus template /
# dummy folders. Override the skipped prefixes via PORTFOLIO_DRIVE_SKIP_PREFIXES.
_SKIP_PREFIXES = tuple(
    p for p in (s.strip() for s in os.environ.get("PORTFOLIO_DRIVE_SKIP_PREFIXES", "_").split(","))
    if p
) or ("_",)
_SKIP_NAME_SUBSTRINGS = ("template", "(dummy)", "ktf dummy")


def is_company_folder(name: str) -> bool:
    """True if a Drive subfolder name looks like an actual portfolio company
    (not an admin / cross-cutting / template folder)."""
    n = (name or "").strip()
    if not n or n.startswith(_SKIP_PREFIXES):
        return False
    low = n.lower()
    return not any(s in low for s in _SKIP_NAME_SUBSTRINGS)


def classify_company_folders(names: list[str]) -> dict[str, bool]:
    """Decide which Drive subfolder names are actual portfolio COMPANIES vs
    organizational / admin / thematic folders, using one cheap LLM call over all
    the names. Falls back to the name heuristic (is_company_folder) when the LLM
    is unavailable. A "_"-prefixed or template/dummy folder is never a company
    regardless of the model (belt-and-suspenders). Returns {name: is_company}."""
    clean = [n for n in (names or []) if n and n.strip()]
    heur = {n: is_company_folder(n) for n in clean}
    if not clean:
        return heur
    try:
        from anthropic import Anthropic
        key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not key:
            return heur
        model = os.environ.get("PORTFOLIO_MAP_MODEL", "claude-haiku-4-5-20251001")
        prompt = (
            "These are subfolder names under a venture fund's 'Portfolio Company Information' "
            "Drive folder. Classify each as a PORTFOLIO COMPANY (a startup the fund invested in) "
            "or NOT a company (an organizational, administrative, thematic, or template folder — "
            "e.g. 'Cap Tables', '2024 Projections & Updates', 'Board Best Practices', "
            "'Fundraising Updates', 'Portfolio Operations Team Folder', template/dummy folders).\n"
            'Return ONLY JSON: {"companies": ["<exact names that ARE companies>"]}.\n\n'
            "FOLDERS:\n" + "\n".join(f"- {n}" for n in clean))
        client = Anthropic(api_key=key)
        msg = client.messages.create(model=model, max_tokens=2000,
                                     messages=[{"role": "user", "content": prompt}])
        raw = "".join(b.text for b in msg.content if hasattr(b, "text")).strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```\s*$", "", raw)
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        companies = set(json.loads(m.group()).get("companies", [])) if m else set()
        if not companies:
            return heur
        # The LLM decides; the heuristic vetoes obvious admin folders ("_", template).
        return {n: (n in companies) and is_company_folder(n) for n in clean}
    except Exception as e:
        logger.warning("folder classification LLM failed (%s); using name heuristic", e)
        return heur


def discover_companies(conn, service, root_folder_id: str, default_fund: str = "Fund I") -> dict:
    """List immediate subfolders of the portfolio root and record one
    pr_companies row per folder, with an LLM deciding which are portfolio
    COMPANIES (excluded=0) vs organizational folders (excluded=1).

    NON-DESTRUCTIVE: folders judged non-company are HIDDEN, never deleted, so no
    documents are ever lost and the classification is reversible (manual override
    via the exclude toggle). The Drive folder is the roster's source of truth —
    NOT deal_reports. Document extraction is per-company, on demand."""
    subfolders = list_subfolders(service, root_folder_id)
    classification = classify_company_folders([(f.get("name") or "").strip() for f in subfolders])
    created, updated, excluded = 0, 0, 0
    excluded_names = []
    for f in subfolders:
        name = (f.get("name") or "").strip()
        if not name:
            continue
        is_co = bool(classification.get(name, is_company_folder(name)))
        flag = 0 if is_co else 1
        if not is_co and len(excluded_names) < 20:
            excluded_names.append(name)
        existing = conn.execute("SELECT id FROM pr_companies WHERE name=?", (name,)).fetchone()
        if existing:
            cid = existing["id"]
            conn.execute("UPDATE pr_companies SET excluded=? WHERE id=?", (flag, cid))
            updated += 1 if is_co else 0
        else:
            cur = conn.execute(
                "INSERT INTO pr_companies (name, fund, excluded) VALUES (?, ?, ?)",
                (name, default_fund, flag))
            cid = cur.lastrowid
            created += 1 if is_co else 0
        excluded += 0 if is_co else 1
        link = conn.execute(
            "SELECT id FROM pr_company_folders WHERE company_id=? AND folder_type='current'", (cid,)
        ).fetchone()
        if not link:
            conn.execute(
                """INSERT INTO pr_company_folders
                   (company_id, folder_type, drive_folder_id, drive_folder_name, parent_folder_id, match_confidence)
                   VALUES (?, 'current', ?, ?, ?, 'auto')""",
                (cid, f["id"], name, root_folder_id))
    conn.commit()
    return {"subfolders": len(subfolders), "companies_created": created,
            "companies_updated": updated, "excluded": excluded,
            "excluded_examples": excluded_names}


# ── Document ingestion for one company ────────────────────────────────────────
# Per-company file cap. Default 0 = NO CAP — sync every file in the folder. Set
# PORTFOLIO_MAX_FILES_PER_COMPANY to impose a limit. The folder is walked
# RECURSIVELY; only text-extractable files (PDF/DOCX/PPTX/XLSX/TXT/…) are pulled
# — images/videos with no readable text are skipped.
_MAX_FILES_PER_COMPANY = int(os.environ.get("PORTFOLIO_MAX_FILES_PER_COMPANY", "0") or 0)


# Per-request wall-clock budget. ingest_company downloads new files until this
# many seconds elapse, then returns remaining/done so the caller loops — so no
# single HTTP request can time out, regardless of how many/large the files are.
_INGEST_TIME_BUDGET_S = int(os.environ.get("PORTFOLIO_INGEST_TIME_BUDGET_S", "40") or 40)


def ingest_company(conn, service, company_id: int, max_files: int = _MAX_FILES_PER_COMPANY,
                   time_budget_s: int = _INGEST_TIME_BUDGET_S) -> dict:
    """Walk a company's linked Drive folder(s) recursively and pull each supported
    file into pr_documents. Skips files already pulled at the same Drive
    modifiedTime (resumable + cheap re-sync). Downloads new files only until
    `time_budget_s` elapses, then returns {remaining, done} so the caller can
    loop without any single request timing out. Mirrors Granola notes once done."""
    import time
    start = time.monotonic()
    folders = conn.execute(
        "SELECT * FROM pr_company_folders WHERE company_id=?", (company_id,)).fetchall()

    # 1. List every file across the company's folders.
    all_files = []
    for fol in folders:
        fname = fol["drive_folder_name"]
        try:
            files = _list_files_recursive(service, fol["drive_folder_id"])
        except Exception as e:
            logger.warning("List failed for folder %s: %s", fname, e)
            continue
        for fmeta in (files if not max_files else files[:max_files]):
            all_files.append((fmeta, fname))

    # 2. Keep only files that are new or changed since the last pull.
    todo = []
    for fmeta, fname in all_files:
        prev = conn.execute(
            "SELECT source_modified FROM pr_documents "
            "WHERE company_id=? AND source='drive' AND source_doc_id=?",
            (company_id, fmeta["id"])).fetchone()
        if prev and prev["source_modified"] and prev["source_modified"] == fmeta.get("modifiedTime"):
            continue
        todo.append((fmeta, fname))

    # 3. Download + extract until the time budget is hit (always at least one).
    inserted, skipped_no_text, processed = 0, 0, 0
    for fmeta, fname in todo:
        if processed and (time.monotonic() - start) > time_budget_s:
            break
        text = _download_and_extract_text(service, fmeta)
        processed += 1
        if not text:
            skipped_no_text += 1
            continue
        _upsert_document(
            conn, company_id,
            source="drive", source_doc_id=fmeta["id"],
            title=fmeta.get("name", ""),
            doc_type=_classify_file(fmeta) or "other",
            mime_type=fmeta.get("mimeType", ""),
            source_url=fmeta.get("webViewLink", ""),
            folder_path=fname,
            body_text=text,
            occurred_at=(fmeta.get("modifiedTime") or "")[:10] or None,
            source_modified=fmeta.get("modifiedTime"))
        inserted += 1
    conn.commit()

    remaining = len(todo) - processed
    granola = mirror_granola_to_documents(conn, company_id) if remaining == 0 else 0
    conn.commit()
    return {"files_total": len(all_files), "needed": len(todo),
            "documents_upserted": inserted, "skipped_no_text": skipped_no_text,
            "remaining": remaining, "done": remaining == 0, "granola": granola}


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
