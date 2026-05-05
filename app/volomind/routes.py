"""VoLo Mind FastAPI routes.

All routes live under /api/volomind/*. Two auth tiers:
  - Read endpoints: any authenticated user (Depends(get_current_user))
  - Write/admin endpoints: admin only (Depends(require_admin))

Every route is wrapped in try/except returning JSON error responses — never
raw 500 stack traces. The host app's other tabs are unaffected by failures
in this module.
"""

from __future__ import annotations

import concurrent.futures
import json
import threading
from typing import Any, Iterable, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import chat_engine, database, scope as scope_mod
from .connectors import get_connector
from .ingest import normalize
from .models import (
    BundlePreview,
    ChatMessage,
    ChatMessageIn,
    ChatThread,
    ChatThreadCreate,
    ScopeFilter,
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
            # Latest sync run for live status / progress
            c.execute(
                "SELECT id, status, fetched, inserted, last_error, started_at, completed_at "
                "FROM cc_sync_runs WHERE source_pk = ? ORDER BY id DESC LIMIT 1",
                (row["id"],),
            )
            run = c.fetchone()
            sync_status = dict(run) if run else None
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
                "sync_status": sync_status,
            })

    # Roadmap entries — aspirational future sources, no connector yet.
    roadmap = []
    for item in sources_config.get_roadmap():
        roadmap.append({
            "label": item["label"],
            "description": item.get("description", ""),
        })

    return {"active": active, "roadmap": roadmap}


# In-memory registry of active sync threads, keyed by source_pk.
# Used to prevent two concurrent syncs of the same source.
_sync_threads: dict[int, threading.Thread] = {}
_sync_lock = threading.Lock()


# Persist progress every N docs so a container restart doesn't waste hours.
_PROGRESS_FLUSH_EVERY = 50

# Default thread-pool size for connectors that opt into parallel ingestion
# via SUPPORTS_PARALLEL = True. Override per-source via config.parallel_workers.
# 8 workers is a sweet spot for I/O-bound Drive sync: enough to overlap
# downloads with extraction, low enough to stay under Drive's per-user
# rate limits and to bound peak memory (≤ 8 in-flight files at ~5MB each).
_DEFAULT_PARALLEL_WORKERS = 8


def _iter_documents(connector, cfg: dict[str, Any]) -> Iterable:
    """Yield RawDocuments from a connector. Uses ThreadPoolExecutor for
    connectors that opt in via SUPPORTS_PARALLEL = True; falls back to
    the connector's sequential list_documents() otherwise.

    Order preservation: executor.map() yields results in input order.
    Combined with iter_file_metadata() returning files sorted by
    modifiedTime ASC, the cursor advances monotonically — flushing the
    cursor at every Nth doc is safe because all prior files are already
    consumed by the time we observe doc N.
    """
    if not getattr(connector, "SUPPORTS_PARALLEL", False):
        # Granola, future Notion/Slack connectors etc. that don't (yet)
        # split into iter_metadata + process_file.
        for raw in connector.list_documents():
            yield raw
        return

    max_workers = int(cfg.get("parallel_workers", _DEFAULT_PARALLEL_WORKERS))
    max_workers = max(1, min(max_workers, 32))  # sane bounds

    executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=max_workers,
        thread_name_prefix="volomind-sync-worker",
    )
    try:
        # executor.map preserves input ordering, so the consumer sees docs
        # in the same modifiedTime ASC order they were yielded by
        # iter_file_metadata. None results (skipped files) are filtered.
        for raw in executor.map(
            connector.process_file,
            connector.iter_file_metadata(),
        ):
            if raw is None:
                continue
            yield raw
    finally:
        executor.shutdown(wait=True)


def _create_sync_run(source_pk: int, started_by: int) -> int:
    with database.cursor() as c:
        c.execute(
            "INSERT INTO cc_sync_runs (source_pk, started_by, status) VALUES (?, ?, 'running')",
            (source_pk, started_by),
        )
        return c.lastrowid


def _update_sync_run(run_id: int, **fields: Any) -> None:
    if not fields:
        return
    fields = {**fields, "updated_at": "__now__"}
    sets = []
    values: list[Any] = []
    for k, v in fields.items():
        if v == "__now__":
            sets.append(f"{k} = datetime('now')")
        else:
            sets.append(f"{k} = ?")
            values.append(v)
    values.append(run_id)
    with database.cursor() as c:
        c.execute(f"UPDATE cc_sync_runs SET {', '.join(sets)} WHERE id = ?", values)


def _finish_sync_run(run_id: int, status: str, **extra: Any) -> None:
    extra = {**extra, "status": status, "completed_at": "__now__"}
    _update_sync_run(run_id, **extra)


def _run_sync_in_thread(source_pk: int, run_id: int, admin_user_id: int) -> None:
    """Background sync loop. Runs in a daemon thread.

    Updates cc_sync_runs every _PROGRESS_FLUSH_EVERY docs so the frontend
    can poll for live progress. Persists cursor incrementally so a kill
    mid-sync resumes from the last flush, not from scratch.
    """
    try:
        with database.cursor() as c:
            c.execute("SELECT * FROM cc_sources WHERE id = ?", (source_pk,))
            row = c.fetchone()
            if row is None:
                _finish_sync_run(run_id, "error", last_error="source not found")
                return
            cfg = json.loads(row["config_json"] or "{}")
            cursor_val = row["cursor"]
            connector_id = row["source_id"]

        if connector_id == "gdrive_admin":
            cfg = {**cfg, "admin_user_id": admin_user_id}

        try:
            connector_cls = get_connector(connector_id)
        except ValueError as e:
            _finish_sync_run(run_id, "error", last_error=str(e))
            return

        connector = connector_cls(config=cfg, cursor=cursor_val)

        fetched = 0
        inserted = 0
        skipped = 0
        errors: list[str] = []

        try:
            for raw in _iter_documents(connector, cfg):
                fetched += 1
                try:
                    _, changed = normalize.upsert_document(
                        source_pk=source_pk,
                        source_id=connector_id,
                        raw=raw,
                    )
                    if changed:
                        inserted += 1
                    else:
                        skipped += 1
                except Exception as e:
                    errors.append(f"{raw.source_doc_id}: {e}")

                if fetched % _PROGRESS_FLUSH_EVERY == 0:
                    _update_sync_run(
                        run_id,
                        fetched=fetched, inserted=inserted, skipped=skipped,
                    )
                    new_cursor = connector.next_cursor()
                    if new_cursor:
                        with database.cursor() as c:
                            c.execute(
                                "UPDATE cc_sources SET cursor = ? WHERE id = ?",
                                (new_cursor, source_pk),
                            )
        except (httpx.HTTPError, RuntimeError, FileNotFoundError, ValueError) as e:
            errors.append(f"connector error: {e}")

        new_cursor = connector.next_cursor()
        with database.cursor() as c:
            if new_cursor:
                c.execute(
                    "UPDATE cc_sources SET cursor = ?, last_synced_at = datetime('now') "
                    "WHERE id = ?",
                    (new_cursor, source_pk),
                )
            elif fetched > 0:
                c.execute(
                    "UPDATE cc_sources SET last_synced_at = datetime('now') WHERE id = ?",
                    (source_pk,),
                )

        _finish_sync_run(
            run_id,
            "complete",
            fetched=fetched, inserted=inserted, skipped=skipped,
            errors_json=json.dumps(errors[:200]),  # cap to keep row small
            last_error=(errors[-1] if errors else None),
        )
    except Exception as e:
        _finish_sync_run(run_id, "error", last_error=f"unhandled: {e}")
    finally:
        with _sync_lock:
            _sync_threads.pop(source_pk, None)


@router.post("/sources/{source_pk}/sync")
async def sync_source(source_pk: int, user: CurrentUser = Depends(require_admin)):
    """Admin-triggered sync — kicks off a background thread, returns immediately.

    Frontend polls GET /sources/{id}/sync-status for progress. Long-running
    syncs (e.g. 30K+ files) safely outlive any HTTP timeout.
    """
    with database.cursor() as c:
        c.execute("SELECT id FROM cc_sources WHERE id = ?", (source_pk,))
        if c.fetchone() is None:
            raise HTTPException(status_code=404, detail="source not found")

    with _sync_lock:
        existing = _sync_threads.get(source_pk)
        if existing is not None and existing.is_alive():
            raise HTTPException(
                status_code=409,
                detail="sync already in progress for this source",
            )

        run_id = _create_sync_run(source_pk, user.id)
        thread = threading.Thread(
            target=_run_sync_in_thread,
            args=(source_pk, run_id, user.id),
            daemon=True,
            name=f"volomind-sync-{source_pk}",
        )
        _sync_threads[source_pk] = thread
        thread.start()

    return JSONResponse(content={
        "ok": True,
        "sync_run_id": run_id,
        "status": "running",
    })


@router.get("/sources/{source_pk}/sync-status")
async def sync_status(source_pk: int, user: CurrentUser = Depends(get_current_user)):
    """Latest sync run for this source. Polled by the frontend during a sync."""
    with database.cursor() as c:
        c.execute(
            "SELECT * FROM cc_sync_runs WHERE source_pk = ? "
            "ORDER BY id DESC LIMIT 1",
            (source_pk,),
        )
        row = c.fetchone()
    if row is None:
        return JSONResponse(content={"status": "never_synced"})
    return JSONResponse(content={
        "id": row["id"],
        "status": row["status"],
        "fetched": row["fetched"],
        "inserted": row["inserted"],
        "skipped": row["skipped"],
        "started_at": row["started_at"],
        "updated_at": row["updated_at"],
        "completed_at": row["completed_at"],
        "last_error": row["last_error"],
    })


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
