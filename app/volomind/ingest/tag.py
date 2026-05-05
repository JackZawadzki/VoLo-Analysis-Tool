"""Tier 1 rule-based tagger.

Cheap, deterministic, no LLM. Pulls signals from:
  - folder_path        -> meeting_type (Granola), document_type (Drive)
  - title (filename)   -> document_type (loose-PDF fallback), company (Granola titles)
  - source_metadata    -> company, co_type (Drive: structurally derived)
  - occurred_at        -> recency bucket
  - attendees_json     -> meeting_size (Granola)

Tier 2 LLM classifier (vertical/sector/stage/value_chain) is a separate
module — Tier 1 alone covers structural dimensions.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from .. import database


TAGGER_VERSION = "rule-v2"


# --- Granola folder name -> meeting_type tag --------------------------------

FOLDER_TO_MEETING_TYPE = {
    "screening + rapid fire": "screening",
    "screening rapid fire": "screening",
    "investment committee": "ic",
    "investment opportunities": "deal",
    "investment network": "network",
    "headroom projects": "research",
    "deal flow events": "events",
}


def _meeting_type_from_folder(folder_path: Optional[str]) -> Optional[str]:
    if not folder_path:
        return None
    key = folder_path.strip().lower()
    if key in FOLDER_TO_MEETING_TYPE:
        return FOLDER_TO_MEETING_TYPE[key]
    head = key.split("/")[0]
    return FOLDER_TO_MEETING_TYPE.get(head)


# --- Document type ---------------------------------------------------------

_FOLDER_DOCUMENT_TYPE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(financial|financials|model|forecast|p\s*&?\s*l|cap\s*table|projection)", re.I), "financial"),
    (re.compile(r"\b(legal|contract|agreement|term\s*sheet|nda|sha|spa|loi|moa|mou)", re.I), "legal"),
    (re.compile(r"\b(tech|technology|engineering|whitepaper|patent|spec|product|r\s*&\s*d)", re.I), "technical"),
    (re.compile(r"\b(market|tam|sam|industry\s*research|competitive|landscape)", re.I), "market"),
    (re.compile(r"\b(memo|recommendation|ic\b|investment\s*committee|analysis|notes)", re.I), "memo"),
    (re.compile(r"\b(deck|pitch|presentation|slides)", re.I), "deck"),
    (re.compile(r"\b(customer|reference|case\s*stud|testimonial|pilot)", re.I), "customer"),
    (re.compile(r"\b(team|management|founder|hr|hiring|org\b)", re.I), "team"),
    (re.compile(r"\b(due\s*diligence|\bdd\b)", re.I), "diligence"),
]

_FILENAME_DOCUMENT_TYPE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(deck|pitch|presentation|slides)\b", re.I), "deck"),
    (re.compile(r"\b(memo|recommendation)\b", re.I), "memo"),
    (re.compile(r"\b(model|forecast|projection|pnl|p\s*&?\s*l|cap\s*table|financials?)\b", re.I), "financial"),
    (re.compile(r"\b(term\s*sheet|spa|sha|nda|loi|moa|mou|stock\s*purchase|side\s*letter)\b", re.I), "legal"),
    (re.compile(r"\b(whitepaper|technical|patent|spec)\b", re.I), "technical"),
    (re.compile(r"\b(tam|sam|market\s*research|competitive)\b", re.I), "market"),
    (re.compile(r"\b(reference|case\s*stud|testimonial)\b", re.I), "customer"),
]


def _document_type(folder_path: Optional[str], title: Optional[str]) -> Optional[str]:
    if folder_path:
        for pat, dt in _FOLDER_DOCUMENT_TYPE_PATTERNS:
            if pat.search(folder_path):
                return dt
    if title:
        for pat, dt in _FILENAME_DOCUMENT_TYPE_PATTERNS:
            if pat.search(title):
                return dt
    return None


# --- Recency ---------------------------------------------------------------

def _recency_bucket(occurred_at: Optional[str]) -> Optional[str]:
    if not occurred_at:
        return None
    from datetime import datetime, timezone
    try:
        dt = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    now = datetime.now(timezone.utc)
    days = (now - dt.replace(tzinfo=dt.tzinfo or timezone.utc)).days
    if days <= 30:
        return "last_30d"
    if days <= 90:
        return "last_90d"
    if days <= 365:
        return "last_year"
    return "older"


# --- Granola title -> company name -----------------------------------------

COMPANY_TITLE_PATTERNS = [
    re.compile(r"^(?P<company>[^<>|\-:]+?)\s*(?:<>|\|\||\|)\s*volo\b", re.I),
    re.compile(r"^volo\s*(?:<>|\|\||\|)\s*(?P<company>[^<>|\-:]+?)\s*$", re.I),
    re.compile(r"^series\s+[a-z0-9-]+\s*[—\-:]\s*(?P<company>.+?)\s*$", re.I),
    re.compile(r"^(?P<company>[^<>|]+?)\s+(?:meeting|intro|call|deep\s*dive)\s*$", re.I),
]


def _companies_from_title(title: Optional[str]) -> list[str]:
    if not title:
        return []
    for pat in COMPANY_TITLE_PATTERNS:
        m = pat.match(title.strip())
        if m:
            name = m.group("company").strip(" -—:|<>")
            if name and name.lower() != "volo":
                return [name]
    return []


# --- Attendees -> meeting_size ---------------------------------------------

def _attendee_count_tag(attendees_json: Optional[str]) -> Optional[str]:
    if not attendees_json:
        return None
    try:
        n = len(json.loads(attendees_json))
    except (ValueError, TypeError):
        return None
    if n == 0:
        return None
    if n <= 2:
        return "1on1"
    if n <= 5:
        return "small"
    return "group"


# --- Tag assembly ----------------------------------------------------------

def _parse_metadata(raw_json: Optional[str]) -> dict[str, Any]:
    if not raw_json:
        return {}
    try:
        result = json.loads(raw_json)
        return result if isinstance(result, dict) else {}
    except (ValueError, TypeError):
        return {}


def _tags_for_document(row) -> list[tuple[str, str]]:
    tags: list[tuple[str, str]] = []
    metadata = _parse_metadata(row["source_metadata_json"])

    mt = _meeting_type_from_folder(row["folder_path"])
    if mt:
        tags.append(("meeting_type", mt))

    dt = _document_type(row["folder_path"], row["title"])
    if dt:
        tags.append(("document_type", dt))

    rb = _recency_bucket(row["occurred_at"])
    if rb:
        tags.append(("recency", rb))

    metadata_company = metadata.get("company_name")
    if metadata_company:
        tags.append(("company", str(metadata_company).strip()))
    else:
        for company in _companies_from_title(row["title"]):
            tags.append(("company", company))

    co_type = metadata.get("co_type")
    if co_type:
        tags.append(("co_type", str(co_type).strip()))

    ac = _attendee_count_tag(row["attendees_json"])
    if ac:
        tags.append(("meeting_size", ac))

    return tags


_DOC_SELECT_COLS = (
    "id, title, folder_path, occurred_at, attendees_json, source_metadata_json"
)


def retag_document(document_id: int) -> list[tuple[str, str]]:
    with database.cursor() as c:
        c.execute(
            f"SELECT {_DOC_SELECT_COLS} FROM cc_documents WHERE id = ?",
            (document_id,),
        )
        row = c.fetchone()
        if row is None:
            return []
        c.execute(
            "DELETE FROM cc_tags WHERE document_id = ? AND source = 'rule' AND tagger_version = ?",
            (document_id, TAGGER_VERSION),
        )
        c.execute(
            "DELETE FROM cc_tags WHERE document_id = ? AND source = 'rule' AND tagger_version != ?",
            (document_id, TAGGER_VERSION),
        )
        pairs = _tags_for_document(row)
        for dimension, value in pairs:
            c.execute(
                """
                INSERT OR IGNORE INTO cc_tags
                    (document_id, segment_id, dimension, value, source, confidence, tagger_version)
                VALUES (?, NULL, ?, ?, 'rule', NULL, ?)
                """,
                (document_id, dimension, value, TAGGER_VERSION),
            )
        return pairs


def retag_all() -> dict[str, int]:
    counts: dict[str, int] = {}
    with database.cursor() as c:
        c.execute("SELECT id FROM cc_documents")
        ids = [r["id"] for r in c.fetchall()]
    for doc_id in ids:
        for dimension, _value in retag_document(doc_id):
            counts[dimension] = counts.get(dimension, 0) + 1
    return counts
