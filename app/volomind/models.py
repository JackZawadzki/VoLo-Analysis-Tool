"""Pydantic models shared across volomind routes/connectors."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


# --- Source-side: what connectors emit before normalization ---

class RawDocument(BaseModel):
    source_doc_id: str
    title: str
    body_text: str
    source_url: Optional[str] = None
    occurred_at: Optional[datetime] = None
    folder_path: Optional[str] = None
    author: Optional[str] = None
    attendees: list[str] = Field(default_factory=list)
    source_metadata: dict[str, Any] = Field(default_factory=dict)
    source_updated_at: Optional[datetime] = None


# --- Scope filter + bundle ---

class ScopeFilter(BaseModel):
    """Within a list = OR. Across lists = AND. Empty list = no filter on that dimension."""
    verticals: list[str] = Field(default_factory=list)
    sectors: list[str] = Field(default_factory=list)
    stages: list[str] = Field(default_factory=list)
    co_types: list[str] = Field(default_factory=list)
    value_chains: list[str] = Field(default_factory=list)
    companies: list[str] = Field(default_factory=list)
    meeting_types: list[str] = Field(default_factory=list)
    document_types: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    date_from: Optional[str] = None
    date_to: Optional[str] = None


class BundleDocSummary(BaseModel):
    document_id: int
    source_id: str
    title: str
    occurred_at: Optional[str]
    folder_path: Optional[str]
    matched_segments: int
    tokens: int


class BundlePreview(BaseModel):
    total_documents: int
    total_segments: int
    total_tokens: int
    truncated: bool
    per_source: dict[str, int]
    documents: list[BundleDocSummary]


# --- Chat ---

class ChatThreadCreate(BaseModel):
    title: str
    scope: ScopeFilter
    model_key: Optional[str] = None


class ChatThread(BaseModel):
    id: int
    title: str
    scope: ScopeFilter
    bundle_hash: Optional[str]
    model_key: Optional[str]
    created_at: str


class ChatMessageIn(BaseModel):
    content: str


class ChatMessage(BaseModel):
    id: int
    thread_id: int
    role: str
    content: str
    created_at: str


# --- Source registration ---

class SourceCreate(BaseModel):
    source_id: str
    label: str
    config: dict[str, Any] = Field(default_factory=dict)


class SourceOut(BaseModel):
    id: int
    source_id: str
    label: str
    cursor: Optional[str]
    last_synced_at: Optional[str]
    document_count: int


class SyncOut(BaseModel):
    fetched: int
    inserted: int
    updated: int
    skipped: int
    errors: list[str]
    new_cursor: Optional[str]
