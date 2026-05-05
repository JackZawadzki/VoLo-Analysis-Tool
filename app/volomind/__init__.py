"""VoLo Mind — scope-filtered chatbot over shared internal corpus.

Beta feature for the Underwriting Engine. Fully isolated from existing
underwriting / IC memo / DDR functionality:

- Separate SQLite database file (data/volomind.db, override VOLOMIND_DB_PATH)
- Separate routes mounted at /api/volomind/*
- Separate frontend assets (static/volomind.js, static/volomind.css)
- Master kill-switch via VOLOMIND_ENABLED env var (default: disabled)

When VOLOMIND_ENABLED is unset or "false", this module is a complete no-op:
no router is mounted, no DB file is created, no migrations run, no nav link
appears in the UI.

Failure of any volomind subsystem must NOT crash the host application — all
init paths are wrapped in try/except and degrade to a "feature unavailable"
state surfaced via the /api/volomind/health endpoint.
"""

from __future__ import annotations

import os


def is_enabled() -> bool:
    """Return True iff the VoLo Mind feature is enabled.

    Default is False — the feature is opt-in and must be explicitly enabled
    via the VOLOMIND_ENABLED environment variable.
    """
    val = os.environ.get("VOLOMIND_ENABLED", "").strip().lower()
    return val in ("1", "true", "yes", "on")


def get_router():
    """Lazy-build and return the FastAPI router. Returns None on import failure
    so the host app can boot even if volomind has missing deps or syntax errors.
    """
    if not is_enabled():
        return None
    try:
        from .routes import router
        return router
    except Exception as e:
        # Don't crash the app on volomind import failure — log and continue.
        print(f"[VoLoMind] WARN: router import failed, feature disabled: {e}", flush=True)
        return None


def init() -> bool:
    """Run startup-time initialization (schema, etc.). Returns True on success.

    Wrapped in try/except so a volomind init failure never breaks the host
    app's startup. Caller in main.py logs the result and continues either way.
    """
    if not is_enabled():
        return False
    try:
        from . import database
        database.init()
        return True
    except Exception as e:
        print(f"[VoLoMind] WARN: init failed, feature will be unavailable: {e}", flush=True)
        return False


__all__ = ["is_enabled", "get_router", "init"]
