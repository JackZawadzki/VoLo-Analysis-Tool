"""Document -> Segments.

Granola Rapid Fire notes can cover 5-10 deals in one note. Splitting on
markdown headers gives per-deal sub-units that can be tagged independently.
A document with no headers becomes one segment covering the whole body.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from .. import database


HEADER_RE = re.compile(r"^(#{2,3})\s+(.+?)\s*$", re.MULTILINE)


@dataclass
class _Chunk:
    label: str | None
    body: str


def split_markdown(body: str) -> list[_Chunk]:
    if not body or not body.strip():
        return [_Chunk(label=None, body=body or "")]

    matches = list(HEADER_RE.finditer(body))
    if not matches:
        return [_Chunk(label=None, body=body.strip())]

    chunks: list[_Chunk] = []
    first = matches[0]
    preamble = body[: first.start()].strip()
    if preamble:
        chunks.append(_Chunk(label=None, body=preamble))

    for i, m in enumerate(matches):
        label = m.group(2).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        section = body[start:end].strip()
        if section:
            chunks.append(_Chunk(label=label, body=section))

    return chunks


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _tokens(text: str) -> int:
    return max(1, len(text) // 4) if text else 0


def recompute_for_document(document_id: int, body: str) -> None:
    chunks = split_markdown(body)
    with database.cursor() as c:
        c.execute("DELETE FROM cc_segments WHERE document_id = ?", (document_id,))
        for idx, chunk in enumerate(chunks):
            c.execute(
                """
                INSERT INTO cc_segments
                    (document_id, segment_index, segment_label, body_text, body_tokens, content_hash)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (document_id, idx, chunk.label, chunk.body, _tokens(chunk.body), _hash(chunk.body)),
            )
