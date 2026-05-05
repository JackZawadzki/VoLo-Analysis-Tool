"""VoLo Mind FastAPI routes.

All routes live under /api/volomind/*. Two auth tiers:
  - Read endpoints: any authenticated user (Depends(get_current_user))
  - Write/admin endpoints: admin only (Depends(require_admin))

Every route is wrapped in try/except returning JSON error responses — never
raw 500 stack traces. The host app's other tabs are unaffected by failures
in this module.
"""

from __future__ import annotations

import json
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import chat_engine, database, scope as scope_mod
from .connectors import SyncResult, get_connector
from .ingest import normalize
from .models import (
    BundlePreview,
    ChatMessage,
    ChatMessageIn,
    ChatThread,
    ChatThreadCreate,
    ScopeFilter,
    SyncOut,
)
from ..auth import CurrentUser, get_current_user, require_admin


router = APIRouter(prefix="/api/volomind", tags=["volomind"])


# --- Health ----------------------------------------------------------------

@router.get("/health")
async def health():
    """Cheap liveness check used by the frontend tab to decide whether to
    render normally or show a 'temporarily unavailable' fallback."""
    try:
        with database.cursor() as c:
            c.execute("SELECT 1")
        return JSONResponse(content={
            "ok": True,
            "chat_configured": chat_engine.is_configured(),
        })
    except Exception as e:
        return JSONResponse(
            content={"ok": False, "error": str(e)},
            status_code=503,
        )


# --- Sources ---------------------------------------------------------------

@router.get("/sources")
async def list_sources(user: CurrentUser = Depends(get_current_user)):
    """Return active sources (DB rows + counts) and coming-soon sources
    (from sources_config.py). Source management is config-file-driven —
    there is no add/delete via the UI. Edit app/volomind/sources_config.py
    + redeploy to add a source.
    """
    from . import sources_config

    active = []
    with database.cursor() as c:
        c.execute("SELECT * FROM cc_sources ORDER BY id")
        rows = c.fetchall()
        for row in rows:
            c.execute(
                "SELECT COUNT(*) AS n FROM cc_documents WHERE source_pk = ?",
                (row["id"],),
            )
            n = c.fetchone()["n"] or 0
            # Pull description from config if present
            definition = sources_config.find_by_label(row["label"])
            description = definition.get("description", "") if definition else ""
            active.append({
                "id": row["id"],
                "source_id": row["source_id"],
                "label": row["label"],
                "cursor": row["cursor"],
                "last_synced_at": row["last_synced_at"],
                "document_count": n,
                "description": description,
                "status": "active",
            })

    # Roadmap entries — aspirational future sources, no connector yet.
    roadmap = []
    for item in sources_config.get_roadmap():
        roadmap.append({
            "label": item["label"],
            "description": item.get("description", ""),
        })

    return {"active": active, "roadmap": roadmap}


@router.post("/sources/{source_pk}/sync", response_model=SyncOut)
async def sync_source(source_pk: int, user: CurrentUser = Depends(require_admin)):
    """Admin-triggered sync. Foreground/blocking — long-running."""
    with database.cursor() as c:
        c.execute("SELECT * FROM cc_sources WHERE id = ?", (source_pk,))
        row = c.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="source not found")
        cfg = json.loads(row["config_json"] or "{}")
        cursor_val = row["cursor"]
        connector_id = row["source_id"]
        connector_cls = get_connector(connector_id)

    # Inject the admin's user_id so the gdrive connector can find their stored OAuth.
    if connector_id == "gdrive_admin":
        cfg = {**cfg, "admin_user_id": user.id}

    connector = connector_cls(config=cfg, cursor=cursor_val)
    result = SyncResult()

    try:
        for raw in connector.list_documents():
            result.fetched += 1
            try:
                _, changed = normalize.upsert_document(
                    source_pk=source_pk,
                    source_id=connector_id,
                    raw=raw,
                )
                if changed:
                    result.inserted += 1
                else:
                    result.skipped += 1
            except Exception as e:
                result.errors.append(f"{raw.source_doc_id}: {e}")
    except (httpx.HTTPError, RuntimeError, FileNotFoundError, ValueError) as e:
        result.errors.append(f"connector error: {e}")

    result.new_cursor = connector.next_cursor()
    if result.new_cursor:
        with database.cursor() as c:
            c.execute(
                "UPDATE cc_sources SET cursor = ?, last_synced_at = datetime('now') WHERE id = ?",
                (result.new_cursor, source_pk),
            )
    elif result.fetched > 0:
        with database.cursor() as c:
            c.execute(
                "UPDATE cc_sources SET last_synced_at = datetime('now') WHERE id = ?",
                (source_pk,),
            )

    return SyncOut(**result.__dict__)


# --- Drive admin status ----------------------------------------------------

@router.get("/admin/drive-status")
async def admin_drive_status(user: CurrentUser = Depends(require_admin)):
    """Whether the admin has connected Google Drive (via the IC memo flow).

    VoLo Mind's admin Drive sync reuses the admin's stored OAuth credentials.
    If they haven't connected Drive yet, point them at the IC Memo tab to do so.
    """
    try:
        from ..routes.drive import _get_user_drive_status
        return JSONResponse(content=_get_user_drive_status(user.id))
    except Exception as e:
        return JSONResponse(
            content={"connected": False, "error": str(e)},
            status_code=200,
        )


# --- Scope -----------------------------------------------------------------

@router.get("/scope/dimensions")
async def list_dimensions(user: CurrentUser = Depends(get_current_user)):
    try:
        return JSONResponse(content=scope_mod.list_dimensions())
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


@router.post("/scope/preview", response_model=BundlePreview)
async def preview_scope(scope: ScopeFilter, user: CurrentUser = Depends(get_current_user)):
    return scope_mod.preview(scope)


# --- Chat threads ----------------------------------------------------------

def _row_to_thread(row) -> ChatThread:
    return ChatThread(
        id=row["id"],
        title=row["title"],
        scope=ScopeFilter(**json.loads(row["scope_json"])),
        bundle_hash=row["bundle_hash"],
        model_key=row["model_key"],
        created_at=row["created_at"],
    )


@router.get("/chat/threads", response_model=list[ChatThread])
async def list_threads(user: CurrentUser = Depends(get_current_user)):
    with database.cursor() as c:
        c.execute(
            "SELECT * FROM cc_chat_threads WHERE owner_id = ? ORDER BY updated_at DESC",
            (user.id,),
        )
        return [_row_to_thread(r) for r in c.fetchall()]


@router.post("/chat/threads", response_model=ChatThread)
async def create_thread(payload: ChatThreadCreate, user: CurrentUser = Depends(get_current_user)):
    bundle_hash = scope_mod.bundle_hash(payload.scope)
    with database.cursor() as c:
        c.execute(
            """
            INSERT INTO cc_chat_threads (owner_id, title, scope_json, bundle_hash, model_key)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                user.id,
                payload.title,
                payload.scope.model_dump_json(),
                bundle_hash,
                payload.model_key,
            ),
        )
        new_id = c.lastrowid
        c.execute("SELECT * FROM cc_chat_threads WHERE id = ?", (new_id,))
        return _row_to_thread(c.fetchone())


@router.delete("/chat/threads/{thread_id}", status_code=204)
async def delete_thread(thread_id: int, user: CurrentUser = Depends(get_current_user)):
    with database.cursor() as c:
        c.execute(
            "DELETE FROM cc_chat_threads WHERE id = ? AND owner_id = ?",
            (thread_id, user.id),
        )
        if c.rowcount == 0:
            raise HTTPException(status_code=404, detail="thread not found")


@router.get("/chat/threads/{thread_id}/messages", response_model=list[ChatMessage])
async def list_messages(thread_id: int, user: CurrentUser = Depends(get_current_user)):
    with database.cursor() as c:
        c.execute(
            "SELECT * FROM cc_chat_threads WHERE id = ? AND owner_id = ?",
            (thread_id, user.id),
        )
        if c.fetchone() is None:
            raise HTTPException(status_code=404, detail="thread not found")
        c.execute(
            "SELECT * FROM cc_chat_messages WHERE thread_id = ? ORDER BY id",
            (thread_id,),
        )
        return [
            ChatMessage(
                id=r["id"], thread_id=r["thread_id"], role=r["role"],
                content=r["content"], created_at=r["created_at"],
            )
            for r in c.fetchall()
        ]


@router.post("/chat/threads/{thread_id}/messages", response_model=ChatMessage)
async def send_message(
    thread_id: int,
    payload: ChatMessageIn,
    user: CurrentUser = Depends(get_current_user),
):
    if not chat_engine.is_configured():
        raise HTTPException(
            status_code=501,
            detail="Refiant client not configured. REFIANT_API_KEY/BASE/MODEL must be set.",
        )
    try:
        result = chat_engine.send(thread_id, user.id, payload.content)
    except LookupError:
        raise HTTPException(status_code=404, detail="thread not found")
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"model call failed: {e}")

    with database.cursor() as c:
        c.execute(
            "SELECT * FROM cc_chat_messages WHERE id = ?",
            (result["assistant_message_id"],),
        )
        row = c.fetchone()
    return ChatMessage(
        id=row["id"], thread_id=row["thread_id"], role=row["role"],
        content=row["content"], created_at=row["created_at"],
    )
