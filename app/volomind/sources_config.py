"""VoLo Mind source registry — edit this file to manage sources.

This is intentionally admin-only infrastructure. End users see the resulting
UI (active sources + roadmap) but cannot add, edit, or delete entries here.

────────────────────────────────────────────────────────────────────
TWO LISTS:
  SOURCES — real connectors (Granola, Drive). Reconciled to cc_sources
            on startup. Only entries with enabled=True show in the UI.
  ROADMAP — aspirational future data sources without a connector yet.
            Pure UI labels. Listed under "Coming Soon" in the sidebar.
────────────────────────────────────────────────────────────────────

To enable a real source:
  1. Find its entry in SOURCES below.
  2. Set enabled=True.
  3. Fill in any required config fields (e.g. root_folder_id for gdrive).
  4. Make sure the matching env vars are set in Replit Secrets:
       - granola → GRANOLA_API_KEY (Enterprise key, not Personal)
       - gdrive_admin → admin user has connected Drive via the IC memo flow
  5. Redeploy. The source row gets created on startup; an admin can then
     click "sync" in the UI to trigger ingestion.

To hide a real source from the UI:
  - Set enabled=False (keeps any DB data, just hides it from the sidebar).

To add a roadmap item (no connector yet):
  - Append a dict to ROADMAP with label + description.
"""

from __future__ import annotations

from typing import Any, Optional, TypedDict


class SourceDefinition(TypedDict, total=False):
    source_id: str           # 'granola' | 'gdrive_admin'
    label: str               # human-readable name in the UI
    config: dict[str, Any]   # connector-specific config
    enabled: bool            # True = reconcile to DB + show in UI
    description: str         # short blurb for the UI


class RoadmapItem(TypedDict, total=False):
    label: str
    description: str


# Real connectors — flip enabled=True when credentials/config are ready.
SOURCES: list[SourceDefinition] = [
    {
        "source_id": "granola",
        "label": "Volo earth team Granola",
        "config": {},  # uses GRANOLA_API_KEY env var
        "enabled": False,
        "description": "Team meeting notes from Granola workspace.",
    },
    {
        "source_id": "gdrive_admin",
        "label": "Portfolio Company Information",
        "config": {
            "root_folder_id": "0ABh0_KkvJonSUk9PVA",
            "co_type": "portfolio",
            # Parallel sync workers (default 8). Higher = faster but heavier on
            # Drive API quota. Bound to [1, 32] in routes.py.
            "parallel_workers": 8,
        },
        "enabled": True,
        "description": "Volo's portfolio company dataroom.",
    },
    {
        "source_id": "gdrive_admin",
        "label": "Investment Diligence",
        "config": {
            "root_folder_id": "",
            "co_type": "potential",
        },
        "enabled": False,
        "description": "Deal flow and screening dataroom.",
    },
]


# Aspirational future data sources — pure UI labels, no connector behind them.
# Add or remove freely. Listed under "Coming Soon" in the sidebar.
ROADMAP: list[RoadmapItem] = [
    {
        "label": "Patent database",
        "description": "USPTO + Google Patents search across sectors.",
    },
    {
        "label": "Industry data",
        "description": "BloombergNEF, IEA, McKinsey reports for market sizing.",
    },
]


def get_enabled() -> list[SourceDefinition]:
    """Return only SOURCES entries with enabled=True. Used by the reconciler."""
    return [s for s in SOURCES if s.get("enabled")]


def get_roadmap() -> list[RoadmapItem]:
    """Return the aspirational roadmap list."""
    return list(ROADMAP)


def find_by_label(label: str) -> Optional[SourceDefinition]:
    """Look up a SOURCES definition by label. Returns None if absent."""
    for s in SOURCES:
        if s.get("label") == label:
            return s
    return None
