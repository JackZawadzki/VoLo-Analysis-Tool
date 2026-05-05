"""Scope filter -> document set + bundle preview.

Within one dimension, multiple values are OR ('Energy OR Mobility').
Across dimensions, all must match (AND).

Result ordering is deterministic: (source_id, occurred_at, document_id).
That's important for prompt-cache stability — same scope must produce
byte-identical bundles run-to-run.
"""

from __future__ import annotations

import hashlib
from typing import Any

from . import database
from .models import BundleDocSummary, BundlePreview, ScopeFilter


_DIM_MAP: dict[str, str] = {
    "verticals": "vertical",
    "stages": "stage",
    "company_types": "company_type",
    "value_chains": "value_chain",
    "sectors": "sector",
    "themes": "theme",
    # Legacy alias — old threads may filter on 'technologies'; map to theme
    # so they don't 0-out on us. v2 tag rows are deleted in init() anyway,
    # so this is mostly cosmetic for stale scope_json.
    "technologies": "theme",
    # Secondary / advanced filters (not in main scope UI)
    "co_types": "co_type",
    "companies": "company",
    "meeting_types": "meeting_type",
    "document_types": "document_type",
}


def _build_query(scope: ScopeFilter) -> tuple[str, list[Any]]:
    where: list[str] = ["1=1"]
    params: list[Any] = []

    if scope.sources:
        placeholders = ",".join("?" * len(scope.sources))
        where.append(f"d.source_id IN ({placeholders})")
        params.extend(scope.sources)

    if scope.date_from:
        where.append("(d.occurred_at IS NULL OR d.occurred_at >= ?)")
        params.append(scope.date_from)
    if scope.date_to:
        where.append("(d.occurred_at IS NULL OR d.occurred_at <= ?)")
        params.append(scope.date_to)

    for attr, dim in _DIM_MAP.items():
        values: list[str] = getattr(scope, attr)
        if not values:
            continue
        placeholders = ",".join("?" * len(values))
        where.append(
            f"""EXISTS (
                SELECT 1 FROM cc_tags t
                WHERE t.document_id = d.id
                  AND t.dimension = ?
                  AND t.value IN ({placeholders})
            )"""
        )
        params.append(dim)
        params.extend(values)

    sql = f"""
        SELECT d.id, d.source_id, d.title, d.occurred_at, d.folder_path, d.body_tokens
        FROM cc_documents d
        WHERE {' AND '.join(where)}
        ORDER BY d.source_id, COALESCE(d.occurred_at, ''), d.id
    """
    return sql, params


def preview(scope: ScopeFilter, *, token_budget: int = 500_000) -> BundlePreview:
    sql, params = _build_query(scope)
    docs: list[BundleDocSummary] = []
    per_source: dict[str, int] = {}
    total_tokens = 0
    total_segments = 0
    truncated = False

    with database.cursor() as c:
        c.execute(sql, params)
        rows = c.fetchall()
        for row in rows:
            c.execute("SELECT COUNT(*) AS n FROM cc_segments WHERE document_id = ?", (row["id"],))
            seg_count = c.fetchone()["n"] or 0
            doc_tokens = row["body_tokens"] or 0
            if total_tokens + doc_tokens > token_budget:
                truncated = True
                continue
            total_tokens += doc_tokens
            total_segments += seg_count
            per_source[row["source_id"]] = per_source.get(row["source_id"], 0) + 1
            docs.append(BundleDocSummary(
                document_id=row["id"],
                source_id=row["source_id"],
                title=row["title"],
                occurred_at=row["occurred_at"],
                folder_path=row["folder_path"],
                matched_segments=seg_count,
                tokens=doc_tokens,
            ))

    return BundlePreview(
        total_documents=len(docs),
        total_segments=total_segments,
        total_tokens=total_tokens,
        truncated=truncated,
        per_source=per_source,
        documents=docs,
    )


def bundle_hash(scope: ScopeFilter) -> str:
    p = preview(scope)
    h = hashlib.sha256()
    for doc in p.documents:
        h.update(str(doc.document_id).encode())
        h.update(b"\x00")
    return h.hexdigest()


def list_dimensions() -> dict[str, list[str]]:
    """Distinct tag values per dimension across the shared corpus."""
    out: dict[str, list[str]] = {}
    with database.cursor() as c:
        c.execute(
            """
            SELECT DISTINCT t.dimension, t.value
            FROM cc_tags t
            ORDER BY t.dimension, t.value
            """
        )
        for row in c.fetchall():
            out.setdefault(row["dimension"], []).append(row["value"])
    return out
