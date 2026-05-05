"""SourceConnector interface.

A connector knows how to (a) list documents from a source, (b) yield each as
a RawDocument, and (c) describe an incremental cursor so we don't re-pull
everything on every sync. Everything downstream is source-agnostic.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from ..models import RawDocument


@dataclass
class SyncResult:
    fetched: int = 0
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    new_cursor: Optional[str] = None


class SourceConnector(ABC):
    source_id: str = "unset"

    def __init__(self, *, config: dict[str, Any], cursor: Optional[str] = None):
        self.config = config
        self.cursor = cursor

    @abstractmethod
    def list_documents(self) -> Iterable[RawDocument]:
        """Yield RawDocuments updated since `self.cursor`."""

    @abstractmethod
    def next_cursor(self) -> Optional[str]:
        """Cursor to persist after a successful sync."""
