"""
Duplicate + version resolution.

Rules (plan, Section 5 / Guiding Principles):
  * one current copy of each document; superseded versions go to 14_Historical,
    never deleted;
  * exact byte-duplicates are collapsed to one kept copy and cross-referenced,
    the rest preserved in Historical (nothing is ever lost);
  * internal / external / redacted are *different documents*, not versions of one
    another -- so confidentiality is part of the version-family key (set in crawl);
  * "which version is current" is genuinely hard: we propose a current version but
    flag real conflicts (same family, same day, different content) for a human.

Sets each record's disposition: PLACED | SUPERSEDED | DUPLICATE | PENDING.
"""

from __future__ import annotations

import os
import re

from . import config as C
from .classify import version_family_key
from .models import FileRecord

# Periodic series: each dated instance is its own document, never superseded by
# the next one. (Cap tables / memos / models, by contrast, have one current version.)
SERIES_BUCKETS = {"06", "07", "08"}


def _version_number(name: str) -> int:
    m = re.search(r"\bv(\d+)", name.lower())
    if m:
        return int(m.group(1))
    m = re.search(r"\((\d+)\)", name)
    if m:
        return int(m.group(1))
    return 0


def _currency_key(r: FileRecord) -> tuple:
    """Higher tuple => more likely to be the current version."""
    stage_rank = C.STAGE_RANK.get(r.stage, 1)            # unlabeled ~ between draft and final
    return (stage_rank, _version_number(r.filename), r.doc_date or "", r.mtime_iso or "", r.file_id)


def _kept_among_duplicates(group: list[FileRecord]) -> FileRecord:
    # Prefer the company-filed copy over a theme-folder copy, then a resolved
    # company, then the current-most attributes, then a stable id.
    def key(r: FileRecord):
        return (
            1 if r.origin_kind == "company" else 0,
            1 if r.company not in ("", "UNKNOWN") else 0,
            _currency_key(r),
        )
    return sorted(group, key=key, reverse=True)[0]


def resolve(records: list[FileRecord]) -> dict:
    """Annotate records in place; return a small report dict."""
    conflicts: list[dict] = []

    # ---- 1. exact duplicates by content hash --------------------------------
    by_hash: dict[str, list[FileRecord]] = {}
    for r in records:
        if r.sha256:
            by_hash.setdefault(r.sha256, []).append(r)

    duplicate_ids: set[str] = set()
    for h, group in by_hash.items():
        if len(group) <= 1:
            continue
        kept = _kept_among_duplicates(group)
        for r in group:
            if r is kept:
                continue
            r.disposition = "DUPLICATE"
            r.duplicate_of = kept.file_id
            r.current = False
            r.flags.append(f"exact-duplicate-of:{kept.file_id}")
            duplicate_ids.add(r.file_id)

    # ---- 2. version families among the remaining (non-duplicate) records ----
    remaining = [r for r in records if r.file_id not in duplicate_ids]
    families: dict[str, list[FileRecord]] = {}
    for r in remaining:
        # Only genuine *classification* uncertainty goes to 15/Pending and stands
        # alone. A doc whose bucket is known but is merely flagged for a human
        # (image-only, company-unresolved, version-conflict) is still placed and
        # version-folded normally -- it just keeps needs_review for the reports.
        if r.bucket_code == C.PENDING_CODE:
            r.disposition = "PENDING"
            r.current = True
            continue
        keep_date = r.bucket_code in SERIES_BUCKETS
        scope = os.path.dirname(r.source_rel)   # versions are scoped to their folder
        r.version_family = version_family_key(
            r.company, r.bucket_code, r.confidentiality, r.filename,
            doc_date=r.doc_date, keep_date=keep_date, scope=scope)
        families.setdefault(r.version_family, []).append(r)

    for fam, group in families.items():
        if len(group) == 1:
            group[0].disposition = "PLACED"
            group[0].current = True
            continue
        ordered = sorted(group, key=_currency_key, reverse=True)
        current = ordered[0]
        current.disposition = "PLACED"
        current.current = True
        for r in ordered[1:]:
            r.disposition = "SUPERSEDED"
            r.current = False
            r.superseded_by = current.file_id

        # Conflict check: top two share the same date + stage but differ in content.
        a, b = ordered[0], ordered[1]
        if (a.doc_date == b.doc_date and a.stage == b.stage and a.sha256 != b.sha256):
            a.needs_review = True
            a.flags.append("version-conflict")
            b.flags.append("version-conflict")
            conflicts.append({
                "family": fam, "company": a.company, "bucket": a.bucket_code,
                "files": [a.file_id, b.file_id], "date": a.doc_date,
            })

    # Anything still without a disposition (shouldn't happen) -> PENDING.
    for r in records:
        if not r.disposition:
            r.disposition = "PENDING"
            r.current = True

    return {
        "n_records": len(records),
        "n_duplicates": len(duplicate_ids),
        "n_superseded": sum(1 for r in records if r.disposition == "SUPERSEDED"),
        "n_pending": sum(1 for r in records if r.disposition == "PENDING"),
        "n_placed": sum(1 for r in records if r.disposition == "PLACED"),
        "conflicts": conflicts,
    }
