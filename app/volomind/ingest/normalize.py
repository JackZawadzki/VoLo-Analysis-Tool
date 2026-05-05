"""Normalize a RawDocument into a stored cc_documents row.

Idempotent on (source_pk, source_doc_id). Re-running a sync against the same
note updates the body in place and re-runs tagging only when content_hash
changed.

When source_metadata carries company_name (Drive structural) or the rule-tagger
later writes a `company` tag (Granola title regex), this module also upserts
a cc_companies row and links the doc via company_id. The dual paths converge
on a single shared company entity.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

from .. import database
from ..models import RawDocument
from . import segment as segment_mod
from . import tag as tag_mod


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _upsert_company(
    *,
    name: str,
    co_type: Optional[str],
    drive_folder_id: Optional[str],
    source_pk: int,
) -> int:
    name_clean = (name or "").strip()
    with database.cursor() as c:
        c.execute(
            "SELECT id, co_type, drive_folder_id FROM cc_companies WHERE name = ?",
            (name_clean,),
        )
        row = c.fetchone()
        if row is None:
            c.execute(
                """
                INSERT INTO cc_companies (name, co_type, drive_folder_id, source_pk)
                VALUES (?, ?, ?, ?)
                """,
                (name_clean, co_type, drive_folder_id, source_pk),
            )
            return c.lastrowid
        new_co_type = row["co_type"] or co_type
        new_folder_id = row["drive_folder_id"] or drive_folder_id
        if new_co_type != row["co_type"] or new_folder_id != row["drive_folder_id"]:
            c.execute(
                "UPDATE cc_companies SET co_type = ?, drive_folder_id = ? WHERE id = ?",
                (new_co_type, new_folder_id, row["id"]),
            )
        return row["id"]


def _resolve_company_id_from_metadata(*, source_pk: int, raw: RawDocument) -> Optional[int]:
    meta: dict[str, Any] = raw.source_metadata or {}
    name = meta.get("company_name")
    if not name:
        return None
    return _upsert_company(
        name=name,
        co_type=meta.get("co_type"),
        drive_folder_id=meta.get("company_drive_folder_id"),
        source_pk=source_pk,
    )


def _resolve_company_id_from_tags(*, document_id: int, source_pk: int) -> Optional[int]:
    with database.cursor() as c:
        c.execute(
            "SELECT DISTINCT value FROM cc_tags "
            "WHERE document_id = ? AND dimension = 'company'",
            (document_id,),
        )
        names = [r["value"] for r in c.fetchall() if r["value"]]
    if len(names) != 1:
        return None
    return _upsert_company(
        name=names[0],
        co_type=None,
        drive_folder_id=None,
        source_pk=source_pk,
    )


def upsert_document(
    *,
    source_pk: int,
    source_id: str,
    raw: RawDocument,
) -> tuple[int, bool]:
    body = raw.body_text or ""
    body_hash = content_hash(body)
    body_tokens = estimate_tokens(body)
    occurred_at = raw.occurred_at.isoformat() if raw.occurred_at else None
    source_updated_at = raw.source_updated_at.isoformat() if raw.source_updated_at else None
    attendees_json = json.dumps(raw.attendees) if raw.attendees else None
    source_metadata_json = json.dumps(raw.source_metadata, default=str)

    company_id = _resolve_company_id_from_metadata(source_pk=source_pk, raw=raw)

    with database.cursor() as c:
        c.execute(
            "SELECT id, content_hash FROM cc_documents WHERE source_pk = ? AND source_doc_id = ?",
            (source_pk, raw.source_doc_id),
        )
        row = c.fetchone()
        if row is None:
            c.execute(
                """
                INSERT INTO cc_documents (
                    source_pk, source_id, source_doc_id, source_url, title,
                    body_text, body_tokens, content_hash,
                    occurred_at, folder_path, author, attendees_json,
                    source_metadata_json, source_updated_at, company_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_pk, source_id, raw.source_doc_id, raw.source_url, raw.title,
                    body, body_tokens, body_hash,
                    occurred_at, raw.folder_path, raw.author, attendees_json,
                    source_metadata_json, source_updated_at, company_id,
                ),
            )
            document_id = c.lastrowid
            content_changed = True
        else:
            document_id = row["id"]
            content_changed = row["content_hash"] != body_hash
            c.execute(
                """
                UPDATE cc_documents SET
                    source_url = ?, title = ?, body_text = ?, body_tokens = ?,
                    content_hash = ?, occurred_at = ?, folder_path = ?, author = ?,
                    attendees_json = ?, source_metadata_json = ?, source_updated_at = ?,
                    company_id = ?, fetched_at = datetime('now')
                WHERE id = ?
                """,
                (
                    raw.source_url, raw.title, body, body_tokens,
                    body_hash, occurred_at, raw.folder_path, raw.author,
                    attendees_json, source_metadata_json, source_updated_at,
                    company_id, document_id,
                ),
            )

    if content_changed:
        segment_mod.recompute_for_document(document_id, body)
        tag_mod.retag_document(document_id)

    if company_id is None:
        company_id = _resolve_company_id_from_tags(document_id=document_id, source_pk=source_pk)
        if company_id is not None:
            with database.cursor() as c:
                c.execute(
                    "UPDATE cc_documents SET company_id = ? WHERE id = ?",
                    (company_id, document_id),
                )

    return document_id, content_changed
