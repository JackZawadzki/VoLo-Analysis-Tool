"""Granola Personal/Enterprise API connector.

Endpoints (verified against docs.granola.ai 2026-05):
  GET /v1/notes              — paginated list of note metadata only
  GET /v1/notes/{note_id}    — full note: summary_markdown, transcript[],
                               attendees[], folder_membership[], calendar_event

Two-pass sync: list ids, then fetch each detail. ~N+1 API calls per sync,
bounded by 5 RPS sustained / 25 per 5s burst / 300 per minute rate limits.

API key flavor:
- Personal API key: notes you own + notes shared with you (limited).
- Enterprise API key: full team workspace (admin-generated).
The connector is identical for both — Granola scopes the key on their side.

Cursor: max(updated_at). Next sync passes `updated_after=<cursor>`.
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Any, Iterable, Optional

import httpx

from ..models import RawDocument
from .base import SourceConnector


_DEFAULT_PAGE_SIZE = 30      # API max
_RATE_DELAY_SECONDS = 0.21   # ~4.7 RPS, under the 5 RPS sustained ceiling
_MAX_RETRIES = 5
_DEFAULT_BASE_URL = "https://public-api.granola.ai/v1"


class GranolaConnector(SourceConnector):
    source_id = "granola"

    def __init__(self, *, config: dict[str, Any], cursor: Optional[str] = None):
        super().__init__(config=config, cursor=cursor)
        self._api_key = self.config.get("api_key") or os.environ.get("GRANOLA_API_KEY", "").strip()
        self._base = (
            self.config.get("api_base")
            or os.environ.get("GRANOLA_API_BASE", _DEFAULT_BASE_URL)
        ).rstrip("/")
        self._include_transcripts = bool(self.config.get("include_transcripts", True))
        self._latest_seen: Optional[str] = cursor

    def _client(self) -> httpx.Client:
        if not self._api_key:
            raise RuntimeError(
                "GRANOLA_API_KEY is not set. Add an Enterprise key to Replit "
                "Secrets, then click sync again."
            )
        return httpx.Client(
            base_url=self._base,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Accept": "application/json",
            },
            timeout=30.0,
        )

    def _get(self, client: httpx.Client, path: str, *, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        time.sleep(_RATE_DELAY_SECONDS)
        for attempt in range(_MAX_RETRIES):
            resp = client.get(path, params=params)
            if resp.status_code != 429:
                resp.raise_for_status()
                return resp.json()
            retry_after = resp.headers.get("Retry-After")
            try:
                wait = float(retry_after) if retry_after else 2 ** attempt
            except ValueError:
                wait = 2 ** attempt
            time.sleep(min(wait, 30))
        resp.raise_for_status()
        return {}

    def list_documents(self) -> Iterable[RawDocument]:
        with self._client() as client:
            cursor_token: Optional[str] = None
            while True:
                params: dict[str, Any] = {"page_size": _DEFAULT_PAGE_SIZE}
                if self.cursor:
                    params["updated_after"] = self.cursor
                if cursor_token:
                    params["cursor"] = cursor_token

                page = self._get(client, "/notes", params=params)
                summaries = page.get("notes") or []

                for summary in summaries:
                    note_id = summary.get("id")
                    if not note_id:
                        continue
                    try:
                        detail = self._get(client, f"/notes/{note_id}")
                    except httpx.HTTPStatusError:
                        continue
                    doc = _to_raw_document(detail, include_transcript=self._include_transcripts)
                    if doc is None:
                        continue
                    if doc.source_updated_at:
                        ts = doc.source_updated_at.isoformat()
                        if self._latest_seen is None or ts > self._latest_seen:
                            self._latest_seen = ts
                    yield doc

                if not page.get("hasMore"):
                    break
                cursor_token = page.get("cursor")
                if not cursor_token:
                    break

    def next_cursor(self) -> Optional[str]:
        return self._latest_seen


def _to_raw_document(note: dict[str, Any], *, include_transcript: bool) -> Optional[RawDocument]:
    summary_md = note.get("summary_markdown") or note.get("summary_text") or ""
    transcript = note.get("transcript") or []

    body_parts: list[str] = []
    if summary_md.strip():
        body_parts.append(summary_md.strip())
    if include_transcript and transcript:
        rendered = _render_transcript(transcript)
        if rendered:
            body_parts.append("# Transcript\n" + rendered)
    body = "\n\n".join(body_parts)
    if not body.strip():
        return None

    folder_path = _first_folder_name(note.get("folder_membership") or [])
    occurred = _occurred_at(note)
    updated = _parse_dt(note.get("updated_at"))

    attendees: list[str] = []
    for a in note.get("attendees") or []:
        if isinstance(a, dict):
            label = a.get("name") or a.get("email")
            if label:
                attendees.append(label)

    owner = note.get("owner") or {}
    author = owner.get("email") or owner.get("name")

    cal = note.get("calendar_event") or {}
    return RawDocument(
        source_doc_id=note["id"],
        title=note.get("title") or "(untitled)",
        body_text=body,
        source_url=note.get("web_url"),
        occurred_at=occurred,
        folder_path=folder_path,
        author=author,
        attendees=attendees,
        source_metadata={
            "granola_note_id": note["id"],
            "folder_membership": note.get("folder_membership") or [],
            "calendar_event_id": cal.get("calendar_event_id"),
            "transcript_segment_count": len(transcript),
        },
        source_updated_at=updated,
    )


def _render_transcript(items: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for item in items:
        speaker = item.get("speaker") or {}
        label = speaker.get("diarization_label") or speaker.get("source") or "Speaker"
        text = (item.get("text") or "").strip()
        if not text:
            continue
        start = item.get("start_time") or ""
        ts_short = start[11:19] if len(start) >= 19 else start
        lines.append(f"**{label}** [{ts_short}] {text}")
    return "\n".join(lines)


def _first_folder_name(folders: list[dict[str, Any]]) -> Optional[str]:
    if not folders:
        return None
    name = (folders[0] or {}).get("name")
    return str(name).strip() if name else None


def _occurred_at(note: dict[str, Any]) -> Optional[datetime]:
    cal = note.get("calendar_event") or {}
    scheduled = cal.get("scheduled_start_time")
    if scheduled:
        dt = _parse_dt(scheduled)
        if dt:
            return dt
    return _parse_dt(note.get("created_at"))


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
