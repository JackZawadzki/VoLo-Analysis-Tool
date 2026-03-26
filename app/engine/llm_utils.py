"""
LLM client utilities.

Provides `make_llm_client(is_refiant, api_key)` which returns either:
  - A real `anthropic.Anthropic` client (Claude models), or
  - A `RefiantClient` shim that wraps `openai.OpenAI` but exposes the same
    `client.messages.create(...)` interface as the Anthropic SDK.

This lets all call sites use the exact same pattern:

    client = make_llm_client(is_refiant, api_key)
    response = client.messages.create(
        model=model,
        max_tokens=2048,
        system="...",
        messages=[...],
    )
    text = "".join(b.text for b in response.content if b.type == "text")
    tokens_in  = response.usage.input_tokens
    tokens_out = response.usage.output_tokens

For Refiant/QWEN the shim converts to OpenAI's chat.completions.create() format
and wraps the response object to look identical to Anthropic's.

TOOLS NOTE: Refiant/QWEN supports function-calling through the OpenAI tool format.
When tools are passed the shim converts them from Anthropic tool schema to OpenAI
function schema, and converts tool_use results back to Anthropic block format.
"""

import logging
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

REFIANT_BASE_URL = "https://api.refiant.ai/v1"


# ── Anthropic-compatible response wrappers ──────────────────────────────────

class _TextBlock:
    type = "text"
    def __init__(self, text: str):
        self.text = text

class _ToolUseBlock:
    type = "tool_use"
    def __init__(self, name: str, input_: dict, id_: str):
        self.name = name
        self.input = input_
        self.id = id_

class _Usage:
    def __init__(self, input_tokens: int, output_tokens: int):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens

class _Response:
    """Anthropic-compatible response object wrapping an OpenAI response."""
    def __init__(self, oai_resp):
        self.model = oai_resp.model
        choice = oai_resp.choices[0] if oai_resp.choices else None
        finish = (choice.finish_reason if choice else None) or "end_turn"
        self.stop_reason = "tool_use" if finish == "tool_calls" else "end_turn"
        self.content: List[Any] = []
        if choice:
            msg = choice.message
            # Plain text content
            if msg.content:
                self.content.append(_TextBlock(msg.content))
            # Tool calls → convert to Anthropic tool_use blocks
            if getattr(msg, "tool_calls", None):
                import json
                for tc in msg.tool_calls:
                    try:
                        inp = json.loads(tc.function.arguments or "{}")
                    except Exception:
                        inp = {}
                    self.content.append(
                        _ToolUseBlock(name=tc.function.name, input_=inp, id_=tc.id)
                    )
        u = oai_resp.usage
        self.usage = _Usage(
            input_tokens=getattr(u, "prompt_tokens", 0) if u else 0,
            output_tokens=getattr(u, "completion_tokens", 0) if u else 0,
        )


# ── Tool schema conversion ───────────────────────────────────────────────────

def _anthropic_tools_to_openai(tools: list) -> list:
    """Convert Anthropic tool definitions to OpenAI function format."""
    result = []
    for t in tools:
        result.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {}),
            },
        })
    return result


def _build_openai_messages(system: Optional[str], messages: list) -> list:
    """Merge Anthropic-style (system, messages[]) into a flat OpenAI messages list.

    Also converts any tool_result content blocks in user turns to the OpenAI
    tool message format.
    """
    import json
    oai = []
    if system:
        oai.append({"role": "system", "content": system})

    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")

        # Anthropic sometimes sends content as a list of blocks
        if isinstance(content, list):
            # Check if this is a tool_result message from an agentic loop
            tool_results = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"]
            text_blocks  = [b for b in content if isinstance(b, dict) and b.get("type") == "text"]
            tool_uses    = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]

            if tool_results:
                # Each tool_result becomes a separate OpenAI "tool" message
                for tr in tool_results:
                    result_content = tr.get("content", "")
                    if isinstance(result_content, list):
                        result_content = " ".join(
                            b.get("text", "") for b in result_content if isinstance(b, dict)
                        )
                    oai.append({
                        "role": "tool",
                        "tool_call_id": tr.get("tool_use_id", ""),
                        "content": str(result_content),
                    })
                continue  # Don't also add as a user message

            if tool_uses:
                # Assistant message with tool calls — rebuild as OpenAI assistant+tool_calls
                text = " ".join(b.get("text", "") for b in text_blocks)
                tc_list = []
                for tu in tool_uses:
                    tc_list.append({
                        "id": tu.get("id", "call_0"),
                        "type": "function",
                        "function": {
                            "name": tu["name"],
                            "arguments": json.dumps(tu.get("input", {})),
                        },
                    })
                oai.append({
                    "role": "assistant",
                    "content": text or None,
                    "tool_calls": tc_list,
                })
                continue

            # Plain list of text blocks → join into string
            content = " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)

        oai.append({"role": role, "content": content})

    return oai


# ── Messages shim ────────────────────────────────────────────────────────────

class _RefiantMessages:
    def __init__(self, oai_client):
        self._oai = oai_client

    def create(self, *, model: str, max_tokens: int, system: Optional[str] = None,
               messages: list, tools: Optional[list] = None, **kwargs) -> _Response:
        oai_messages = _build_openai_messages(system, messages)
        kwargs_out: dict = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": oai_messages,
        }
        if tools:
            kwargs_out["tools"] = _anthropic_tools_to_openai(tools)
            kwargs_out["tool_choice"] = "auto"

        try:
            resp = self._oai.chat.completions.create(**kwargs_out)
            return _Response(resp)
        except Exception as e:
            logger.error("Refiant API call failed: %s", e)
            raise


# ── Public factory ────────────────────────────────────────────────────────────

class RefiantClient:
    """Drop-in replacement for `anthropic.Anthropic` that hits the Refiant endpoint."""
    def __init__(self, api_key: str):
        import openai
        oai = openai.OpenAI(api_key=api_key, base_url=REFIANT_BASE_URL)
        self.messages = _RefiantMessages(oai)


def make_llm_client(is_refiant: bool, api_key: str):
    """Return an LLM client.

    Returns a `RefiantClient` for QWEN/Refiant models, otherwise a real
    `anthropic.Anthropic` client.  Both expose `.messages.create(...)`.
    """
    if is_refiant:
        return RefiantClient(api_key=api_key)
    import anthropic
    return anthropic.Anthropic(api_key=api_key)
