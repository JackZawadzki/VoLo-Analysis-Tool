"""Chat engine: assemble scoped corpus + history -> Refiant -> persisted reply.

Uses the host app's REFIANT_API_KEY / REFIANT_API_BASE / REFIANT_MODEL env
vars directly via the openai SDK pointed at Refiant's base_url. Same model
already wired into the existing /api/chat for deal-context conversations.

Bundle ordering is deterministic (same scope -> same prompt bytes) so prompt
caching, when supported, can hit on follow-up messages in the same thread.
"""

from __future__ import annotations

import json
import os
from typing import Optional

from . import database, scope as scope_mod
from .models import ScopeFilter


_DEFAULT_TOKEN_BUDGET = 500_000

# Sensible defaults for the Refiant API_BASE and MODEL, so admins only need
# to set REFIANT_API_KEY in Replit Secrets. These match the values the host
# app's IC-memo Deal Agent uses; the .env.example documents both. If
# Refiant ever changes their endpoint/model, override here or via env var.
_DEFAULT_REFIANT_API_BASE = "https://api.refiant.ai/v1"
_DEFAULT_REFIANT_MODEL = "qwen-rfnt"


def _refiant_api_base() -> str:
    return (os.environ.get("REFIANT_API_BASE") or _DEFAULT_REFIANT_API_BASE).strip()


def _refiant_model() -> str:
    return (os.environ.get("REFIANT_MODEL") or _DEFAULT_REFIANT_MODEL).strip()


def is_configured() -> bool:
    """Only REFIANT_API_KEY is strictly required — BASE and MODEL fall back
    to constants if unset. Mirrors the host app's IC-memo chat behavior."""
    return bool(os.environ.get("REFIANT_API_KEY", "").strip())


def _client():
    try:
        from openai import OpenAI
    except ImportError as e:
        raise RuntimeError(
            "openai SDK not installed. pip install openai"
        ) from e
    api_key = os.environ.get("REFIANT_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "REFIANT_API_KEY is not set in Replit Secrets. The Deal Agent "
            "(IC-memo chat) uses the same secret — if that works, this should too."
        )
    return OpenAI(api_key=api_key, base_url=_refiant_api_base())


def _format_doc_block(row) -> str:
    parts = [f"--- DOCUMENT id={row['id']}"]
    parts.append(f"TITLE: {row['title']}")
    if row["source_id"]:
        parts.append(f"SOURCE: {row['source_id']}")
    if row["occurred_at"]:
        parts.append(f"DATE: {row['occurred_at']}")
    if row["folder_path"]:
        parts.append(f"FOLDER: {row['folder_path']}")
    if row["attendees_json"]:
        try:
            attendees = json.loads(row["attendees_json"])
            if attendees:
                parts.append(f"ATTENDEES: {', '.join(attendees)}")
        except (ValueError, TypeError):
            pass
    parts.append("")
    parts.append(row["body_text"] or "")
    return "\n".join(parts)


def build_system_prompt(scope: ScopeFilter, *, token_budget: int = _DEFAULT_TOKEN_BUDGET) -> tuple[str, list[int], int]:
    """Assemble the system prompt from all in-scope documents.

    Returns (prompt, included_document_ids, estimated_tokens).
    """
    sql, params = scope_mod._build_query(scope)
    preface = (
        "You are a research analyst for VoLo Earth Ventures, a climate-focused "
        "venture capital firm. Below are the documents currently in scope for "
        "this conversation — meeting notes, dataroom files, and other source "
        "material from VoLo's internal knowledge base. Cite documents by their "
        "TITLE when referencing them. If the answer is not supported by the "
        "in-scope documents, say so explicitly rather than speculating.\n\n"
    )
    blocks: list[str] = []
    included: list[int] = []
    used_tokens = len(preface) // 4

    with database.cursor() as c:
        c.execute(sql, params)
        rows = c.fetchall()
        for row in rows:
            block = _format_doc_block(row)
            block_tokens = max(1, len(block) // 4)
            if used_tokens + block_tokens > token_budget:
                continue
            blocks.append(block)
            included.append(row["id"])
            used_tokens += block_tokens

    body = preface + "\n\n".join(blocks) if blocks else (preface + "(No documents matched the current scope.)")
    return body, included, used_tokens


def _load_history(thread_id: int) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    with database.cursor() as c:
        c.execute(
            "SELECT role, content FROM cc_chat_messages WHERE thread_id = ? ORDER BY id",
            (thread_id,),
        )
        for row in c.fetchall():
            if row["role"] in ("user", "assistant", "system"):
                out.append({"role": row["role"], "content": row["content"]})
    return out


def _prepare_call(thread_id: int, owner_id: int, user_message: str):
    with database.cursor() as c:
        c.execute(
            "SELECT * FROM cc_chat_threads WHERE id = ? AND owner_id = ?",
            (thread_id, owner_id),
        )
        thread = c.fetchone()
        if thread is None:
            raise LookupError("thread not found")
        scope = ScopeFilter(**json.loads(thread["scope_json"]))
        model = thread["model_key"] or os.environ.get("REFIANT_MODEL", "").strip()
        if not model:
            raise RuntimeError("REFIANT_MODEL not configured")

    system_prompt, _included, _est = build_system_prompt(scope)
    history = _load_history(thread_id)
    messages = [{"role": "system", "content": system_prompt}, *history, {"role": "user", "content": user_message}]

    with database.cursor() as c:
        c.execute(
            "INSERT INTO cc_chat_messages (thread_id, role, content) VALUES (?, 'user', ?)",
            (thread_id, user_message),
        )
        user_msg_id = c.lastrowid
    return model, messages, user_msg_id


def _persist_assistant(thread_id: int, content: str, tokens_in, tokens_out) -> int:
    with database.cursor() as c:
        c.execute(
            """
            INSERT INTO cc_chat_messages (thread_id, role, content, tokens_in, tokens_out)
            VALUES (?, 'assistant', ?, ?, ?)
            """,
            (thread_id, content, tokens_in, tokens_out),
        )
        assistant_msg_id = c.lastrowid
        c.execute(
            "UPDATE cc_chat_threads SET updated_at = datetime('now') WHERE id = ?",
            (thread_id,),
        )
    return assistant_msg_id


def send(thread_id: int, owner_id: int, user_message: str) -> dict:
    """Blocking one-shot. Used by the non-streaming send endpoint."""
    model, messages, user_msg_id = _prepare_call(thread_id, owner_id, user_message)
    client = _client()
    response = client.chat.completions.create(model=model, messages=messages)
    reply = response.choices[0].message.content or ""
    usage = response.usage
    tokens_in = getattr(usage, "prompt_tokens", None) if usage else None
    tokens_out = getattr(usage, "completion_tokens", None) if usage else None
    assistant_msg_id = _persist_assistant(thread_id, reply, tokens_in, tokens_out)
    return {
        "assistant_message_id": assistant_msg_id,
        "user_message_id": user_msg_id,
        "reply": reply,
        "model": model,
        "prompt_tokens": tokens_in,
        "completion_tokens": tokens_out,
    }
