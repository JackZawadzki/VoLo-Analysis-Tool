"""Banker Agent — LLM tool-use loop over a financial model.

The agent's job is to:
  1. List sheets and preview each.
  2. Decide which cells are line-item labels, assumption labels, period headers.
  3. Emit an ExtractionDraft (labels + cell refs, NO numeric values).

A deterministic post-pass then reads the referenced cells and produces the
final ExtractedModel. The agent never transcribes a number, which eliminates
the worst failure mode of pure-LLM extraction.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from anthropic import Anthropic
from pydantic import ValidationError

from .excel_tools import ExcelWorkbook
from .schemas import (
    Assumption,
    AssumptionDraft,
    CanonicalMetricMap,
    CellRef,
    ExtractedModel,
    ExtractionDraft,
    FinancialModelMetric,
    FinancialModelScope,
    LineItem,
    LineItemDraft,
    NarrativeBlock,
    RangeRef,
    ReconciliationCheck,
    RequiredCore,
    SheetCharacterization,
    VoLoFinancialModel,
)

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are Banker Agent, an expert at reading financial models and extracting
structured data from them.

Your task: given access to a workbook via tool calls, produce a complete
extraction that captures every meaningful line item, assumption, and narrative.

Rules you MUST follow:

1. **Preserve labels verbatim.** If the sheet says "Hardware Revenue (1 MW)",
   that string goes in the output exactly as-is. Do not normalize, reword, or
   map to a canonical name.

2. **Never transcribe numeric values into your output.** Your output contains
   only (sheet, cell) references. A downstream deterministic step reads the
   cells and fills values. This is critical — numeric transcription from an
   LLM is unreliable.

3. **Categories are soft.** Tag line items with a short lowercase string like
   "revenue", "cogs", "opex", "capex", "cash", "headcount", "assumption",
   "kpi", "schedule", "milestone", etc. Invent new tags when the data doesn't
   fit existing ones. Never force a line into a category that doesn't match.

4. **Units matter.** For every line item and assumption, note the unit if you
   can infer it: "USD", "USD_M", "USD_K", "people", "%", "units", "MWh",
   "years", etc. Leave unit null if unclear.

5. **Every row has a period axis or it's an assumption.** If the row has
   values across columns that represent periods (years, quarters, stages),
   it's a line_item. If it's a single scalar, it's an assumption.

6. **Characterize every sheet.** Write a one-to-three sentence description
   of what the sheet contains. Note the period axis if it has one.

7. **Required core fields are optional.** If the workbook states TAM, TRL,
   check size, pre-money, etc., capture them in required_core. If not, set
   them to null and note why in notes_on_missing.

8. **Canonical metrics map — fill this for downstream ingestion.** After you
   extract all line_items, fill `canonical_metrics_map` by identifying which
   of your extracted line_item labels (if any) represents each of these VoLo
   canonical metrics:

     revenue, cogs, gross_profit, operating_expenses, ebitda,
     operating_income, net_income, capex

   Rules:
   - Reference ONLY labels you have already extracted as line_items.
   - Prefer explicit TOTAL rows over sub-category rows. For example, if the
     model has "Hardware Revenue (1 MW)", "Service Revenue", and "Total Revenue",
     set canonical_metrics_map.revenue = "Total Revenue" (never a sub-line).
   - If a canonical metric isn't present in the model (e.g. no EBITDA row),
     set it to null. Do not invent or compute.
   - "operating_income" is usually labeled "Operating Profit" or "Operating Income".
   - "operating_expenses" is usually a "Total Operating Costs" / "Total Opex" row.
   - "capex" is usually on a Cash Flow sheet (e.g. "Capital Expenditures").

9. **Primary financials sheet.** Set `primary_financials_sheet` to the name of
   the sheet that the canonical_metrics_map is primarily drawn from. Usually
   the Income Statement or P&L sheet. If the model doesn't have one, leave null.

10. **Prefer the broadest-scope P&L sheet.** Many workbooks contain multiple
    P&L-style sheets at different scopes — a combined/consolidated view AND
    one or more single-facility or single-segment views. For a venture deal
    analysis you MUST pick the broadest scope available.

    Prefer sheets whose names contain any of these tokens:
      "Combined", "Unified", "Consolidated", "Consolidation", "Group",
      "Total", "Summary", "Overview".

    Avoid (deprioritize) sheets whose names contain:
      "L1", "L2", "Line 1", "Line 2", "Facility", "Plant", "Standalone",
      or a specific city/place name (e.g. "Smyrna", "Bowling Green",
      "Muskegon", "Kentucky") when a broader sheet exists.

    Example: if the workbook has BOTH `FS_Combined` and `FS - Annual`, you
    MUST pick `FS_Combined` — it is the multi-facility consolidated view,
    while `FS - Annual` is a single-facility projection. Same for
    `FS_Smyrna_L1+L2`, `FS_Bowling Green`, etc. — these are single-plant
    views and should never be `primary_financials_sheet` when a
    Combined/Consolidated sheet is available. Pull `canonical_metrics_map`
    labels from the broadest sheet, not from a plant-level derivative.

    If no consolidated sheet exists and only single-facility sheets are
    available, pick the most representative one and note this in
    `notes_on_missing` so the downstream consumer knows the extraction is
    narrower than the company as a whole.

Work pattern:

  1. Call list_sheets to see what's there.
  2. For each sheet, call preview_sheet(rows=30) to see the top of the data.
  3. Call read_range as needed to see deeper or wider content.
  4. Call find_label if you need to locate specific labels quickly.
  5. When you have enough information, emit a single JSON output matching
     the ExtractionDraft schema. Do NOT emit anything else — no prose, no
     explanation outside the JSON.

Output format:

Return a single JSON object inside a ```json fenced block. No other text.
The JSON must conform exactly to the ExtractionDraft schema provided in the
user message.
"""


TOOLS_SPEC = [
    {
        "name": "list_sheets",
        "description": "List all sheets in the workbook with row/column counts.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "preview_sheet",
        "description": (
            "Return the top-left N rows x M cols of a sheet as a grid. "
            "Each cell returns {address, value, formula, data_type}. "
            "Use this to understand a sheet's structure."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sheet": {"type": "string"},
                "rows": {"type": "integer", "default": 30, "minimum": 1, "maximum": 200},
                "cols": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "required": ["sheet"],
        },
    },
    {
        "name": "read_range",
        "description": (
            "Read a cell range (e.g. 'C12:I20' or 'A1:Z50'). Use for detail work "
            "once you know where to look."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sheet": {"type": "string"},
                "range": {"type": "string", "description": "Excel-style range, e.g. 'C12:I12'."},
            },
            "required": ["sheet", "range"],
        },
    },
    {
        "name": "read_cell",
        "description": "Read a single cell. Returns value, formula (if any), and data type.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sheet": {"type": "string"},
                "address": {"type": "string", "description": "e.g. 'F14'."},
            },
            "required": ["sheet", "address"],
        },
    },
    {
        "name": "find_label",
        "description": (
            "Search every sheet for cells whose text matches a regex. "
            "Returns a list of (sheet, address, text). Useful for locating "
            "specific labels across the workbook."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "A regex."},
                "case_insensitive": {"type": "boolean", "default": True},
                "max_hits": {"type": "integer", "default": 100, "minimum": 1, "maximum": 500},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "get_named_ranges",
        "description": "Return workbook-level defined names (named ranges), if any.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
]


@dataclass
class AgentResult:
    draft: ExtractionDraft
    turns: int
    tokens_in: int
    tokens_out: int


def _dispatch_tool(wb: ExcelWorkbook, name: str, args: dict[str, Any]) -> Any:
    """Route a tool call to the Excel reader."""
    if name == "list_sheets":
        return wb.list_sheets()
    if name == "preview_sheet":
        return wb.preview_sheet(
            sheet=args["sheet"],
            rows=args.get("rows", 30),
            cols=args.get("cols"),
        )
    if name == "read_range":
        return wb.read_range(sheet=args["sheet"], range_str=args["range"])
    if name == "read_cell":
        return wb.read_cell(sheet=args["sheet"], address=args["address"])
    if name == "find_label":
        return wb.find_label(
            pattern=args["pattern"],
            case_insensitive=args.get("case_insensitive", True),
            max_hits=args.get("max_hits", 100),
        )
    if name == "get_named_ranges":
        return wb.get_named_ranges()
    return {"error": f"unknown tool: {name}"}


def _extract_json(text: str) -> Optional[dict]:
    """Pull a JSON object from the model's text response."""
    import re

    # Prefer fenced block
    match = re.search(r"```(?:json)?\s*\n(\{.*\})\n```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Fallback: first brace-balanced object
    if "{" not in text:
        return None
    start = text.index("{")
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def run_agent(
    workbook_path: str | Path,
    *,
    model: str = "claude-sonnet-4-5",
    max_turns: int = 30,
    api_key: Optional[str] = None,
    extra_context: Optional[str] = None,
    progress_callback: Optional[Any] = None,
) -> AgentResult:
    """Run the extraction agent on a workbook.

    Returns an AgentResult containing the validated ExtractionDraft.
    Callers should then run `fill_values()` to produce the final ExtractedModel.

    If `progress_callback` is provided, it will be called with a short human-
    readable string at each significant phase (workbook load, per-turn status,
    completion). Any exceptions raised by the callback are swallowed so they
    don't disrupt the extraction itself.
    """
    def _emit(msg: str) -> None:
        if progress_callback is None:
            return
        try:
            progress_callback(msg)
        except Exception:
            logger.exception("progress_callback raised; ignoring")

    api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    _emit("Loading workbook...")
    wb = ExcelWorkbook(workbook_path)
    sheets = wb.list_sheets()
    _emit(f"Workbook loaded — {len(sheets)} sheet(s): " + ", ".join(s["name"] for s in sheets[:6]))

    client = Anthropic(api_key=api_key)

    # Build the user turn. Include the schema so the model knows exactly what
    # shape to emit.
    user_prompt = _build_user_prompt(Path(workbook_path).name, extra_context)

    messages: list[dict[str, Any]] = [
        {"role": "user", "content": user_prompt}
    ]

    total_tokens_in = 0
    total_tokens_out = 0

    for turn in range(max_turns):
        logger.info("Agent turn %d", turn + 1)
        _emit(f"Agent turn {turn + 1}: inspecting the workbook...")

        # Token-budget safety net. Claude Sonnet 4.5 caps input at 200k
        # tokens. On complex multi-sheet workbooks (Mitra: 32 sheets) the
        # agent accumulates tool_result payloads across turns that can
        # easily blow past that. If the estimated input is approaching
        # the ceiling, collapse older tool_result messages in place so the
        # conversation stays under budget while preserving the most
        # recent context the agent needs to finish.
        _prune_messages_if_needed(messages)

        # Streaming is required when max_tokens > 8192 per the Anthropic SDK.
        # We don't actually need the intermediate chunks — we just accumulate the
        # final message so the rest of the loop works identically.
        with client.messages.stream(
            model=model,
            max_tokens=32000,  # big enough for full extractions on 6+ sheet models
            system=SYSTEM_PROMPT,
            tools=TOOLS_SPEC,
            messages=messages,
        ) as stream:
            for _ in stream.text_stream:
                pass
            resp = stream.get_final_message()
        total_tokens_in += resp.usage.input_tokens
        total_tokens_out += resp.usage.output_tokens

        # Append assistant response to conversation
        messages.append({"role": "assistant", "content": resp.content})

        # Collect tool uses in this response
        tool_uses = [b for b in resp.content if b.type == "tool_use"]

        if not tool_uses:
            # Final response — extract JSON from text blocks
            _emit("Agent finished inspection. Parsing structured output...")
            text = "".join(b.text for b in resp.content if b.type == "text")
            obj = _extract_json(text)
            if obj is None:
                raise RuntimeError(
                    f"Agent finished without emitting JSON. Raw response:\n{text[:2000]}"
                )
            try:
                draft = ExtractionDraft.model_validate(obj)
            except ValidationError as exc:
                raise RuntimeError(
                    f"Agent output failed schema validation:\n{exc}\n\n"
                    f"Raw JSON (first 2000 chars):\n{json.dumps(obj)[:2000]}"
                ) from exc
            _emit(f"Identified {len(draft.line_items)} line items and {len(draft.assumptions)} assumptions.")
            return AgentResult(
                draft=draft,
                turns=turn + 1,
                tokens_in=total_tokens_in,
                tokens_out=total_tokens_out,
            )

        # Dispatch tool uses and send results
        tool_results = []
        for use in tool_uses:
            try:
                result = _dispatch_tool(wb, use.name, use.input)
                payload = json.dumps(result, default=str)
            except Exception as exc:
                payload = json.dumps({"error": str(exc)})
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": use.id,
                "content": payload,
            })
        messages.append({"role": "user", "content": tool_results})

    raise RuntimeError(f"Agent did not finish within {max_turns} turns")


# Character-budget proxy for Claude's 200K input-token cap. ~3 characters per
# token for English + JSON content is a conservative ratio, so ~480K chars
# corresponds to roughly 160K tokens, leaving headroom for the system prompt,
# tool schemas, and the current turn's assistant output budget.
_INPUT_CHAR_BUDGET = 480_000
# When pruning, keep this many most-recent messages intact. The initial user
# turn (index 0) is always preserved since it contains the extraction schema.
_PRUNE_KEEP_RECENT = 6
_PRUNED_NOTICE = (
    "[earlier tool result pruned to stay within the model's input budget — "
    "re-call the relevant tool if you still need these cells]"
)


def _estimate_message_chars(messages: list[dict[str, Any]]) -> int:
    """Rough byte-size proxy for the total input payload. We don't need
    Claude-accurate tokenization; a character count over-estimates tokens
    consistently enough to serve as a safety threshold."""
    total = 0
    for m in messages:
        c = m.get("content")
        if isinstance(c, str):
            total += len(c)
        elif isinstance(c, list):
            for block in c:
                if isinstance(block, dict):
                    # tool_result / tool_use / text blocks all serialize to
                    # JSON-ish payloads. Measure the string form conservatively.
                    total += len(str(block))
                else:
                    # SDK content block objects — .text, .input, etc.
                    total += len(str(getattr(block, "text", "") or ""))
                    total += len(str(getattr(block, "input", "") or ""))
                    total += len(str(getattr(block, "content", "") or ""))
    return total


def _prune_messages_if_needed(messages: list[dict[str, Any]]) -> None:
    """In-place: if the conversation is getting too big, collapse older
    tool_result payloads to short placeholders. Preserves the initial user
    turn and the most recent _PRUNE_KEEP_RECENT messages verbatim.

    The agent will see the placeholder text, understand those tool results
    were discarded to save context, and can re-query tools for any cells
    it still needs.
    """
    if len(messages) <= _PRUNE_KEEP_RECENT + 1:
        return
    if _estimate_message_chars(messages) < _INPUT_CHAR_BUDGET:
        return

    # Indices 1 .. (len - _PRUNE_KEEP_RECENT) are eligible for pruning.
    prunable_end = len(messages) - _PRUNE_KEEP_RECENT
    for i in range(1, prunable_end):
        m = messages[i]
        c = m.get("content")
        # Only prune user-role tool_result messages (these carry the bulky
        # preview_sheet / read_range payloads). Assistant messages hold the
        # agent's reasoning and tool_use plans — keep those intact.
        if m.get("role") != "user" or not isinstance(c, list):
            continue
        new_blocks = []
        for block in c:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                new_blocks.append({
                    "type": "tool_result",
                    "tool_use_id": block.get("tool_use_id"),
                    "content": _PRUNED_NOTICE,
                })
            else:
                new_blocks.append(block)
        m["content"] = new_blocks
        # Stop as soon as we're back under budget.
        if _estimate_message_chars(messages) < _INPUT_CHAR_BUDGET:
            break


def _build_user_prompt(file_name: str, extra_context: Optional[str]) -> str:
    """Compose the initial user turn with the schema and instructions."""
    schema_json = json.dumps(ExtractionDraft.model_json_schema(), indent=2)
    ctx = f"\n\nAdditional context from the analyst:\n{extra_context}" if extra_context else ""
    return (
        f"Extract structured data from the workbook `{file_name}`.\n\n"
        f"Use the tools to explore. When done, emit a single JSON object "
        f"conforming to the ExtractionDraft schema below.\n\n"
        f"ExtractionDraft JSON Schema:\n```json\n{schema_json}\n```\n"
        f"{ctx}"
    )


# ── Deterministic value-fill pass ───────────────────────────────────────

def fill_values(draft: ExtractionDraft, workbook_path: str | Path, model_used: str) -> ExtractedModel:
    """Read the cells referenced by the draft and produce the final filled model.

    This is where numeric values enter the output — read deterministically
    from the source cells, never transcribed by the LLM.

    Also builds the VoLo-compatible `financial_model` section by resolving
    the agent's canonical_metrics_map to actual filled line_items.
    """
    wb = ExcelWorkbook(workbook_path)

    filled_line_items: list[LineItem] = []
    for li in draft.line_items:
        period_keys, period_values = _read_periods(
            wb, li.period_header_range, li.values_range
        )
        filled_line_items.append(LineItem(
            label=li.label,
            label_cell=li.label_cell,
            values_range=li.values_range,
            values_by_period=dict(zip(period_keys, period_values)),
            period_order=list(period_keys),
            category=li.category,
            subcategory=li.subcategory,
            unit=li.unit,
            notes=li.notes,
            confidence=li.confidence,
        ))

    filled_assumptions: list[Assumption] = []
    for a in draft.assumptions:
        cell = wb.read_cell(a.value_cell.sheet, a.value_cell.address)
        raw_val = cell.get("value")
        val: Optional[float | str | bool]
        if isinstance(raw_val, (int, float, bool, str)):
            val = raw_val
        else:
            val = None
        filled_assumptions.append(Assumption(
            label=a.label,
            label_cell=a.label_cell,
            value_cell=a.value_cell,
            value=val,
            unit=a.unit,
            category=a.category,
            notes=a.notes,
            confidence=a.confidence,
        ))

    # Reconciliation: for now just verify every cell reference resolves.
    recon: list[ReconciliationCheck] = []
    try:
        for li in filled_line_items:
            wb.read_cell(li.label_cell.sheet, li.label_cell.address)
        recon.append(ReconciliationCheck(
            description="All line-item label cells resolve",
            passed=True,
        ))
    except Exception as exc:
        recon.append(ReconciliationCheck(
            description="All line-item label cells resolve",
            passed=False,
            detail=str(exc),
        ))

    # Build the VoLo-compatible financial_model view
    financial_model = _build_financial_model(
        filled_line_items,
        canonical_map=draft.canonical_metrics_map,
        primary_sheet=draft.primary_financials_sheet,
    )

    return ExtractedModel(
        source_file=str(workbook_path),
        model_used=model_used,
        financial_model=financial_model,
        deal_input_fields=draft.required_core,
        sheets=draft.sheets,
        line_items=filled_line_items,
        assumptions=filled_assumptions,
        narratives=draft.narratives,
        reconciliation_checks=recon,
        canonical_metrics_map=draft.canonical_metrics_map,
        primary_financials_sheet=draft.primary_financials_sheet,
        agent_notes=draft.agent_notes,
    )


def _build_financial_model(
    line_items: list[LineItem],
    canonical_map: CanonicalMetricMap,
    primary_sheet: Optional[str],
) -> VoLoFinancialModel:
    """Construct the VoLo-compatible financial_model view from filled line_items.

    Looks up each canonical metric's label in the extracted line_items and
    emits a VoLoFinancialModel whose `metrics` dict matches the shape VoLo's
    extract_financials.py produces. Missing metrics are null with a note.
    """
    # Index line items by label (verbatim match; labels were preserved by the agent).
    by_label: dict[str, LineItem] = {li.label: li for li in line_items}

    metrics: dict[str, Optional[FinancialModelMetric]] = {}
    notes: list[str] = []
    canonical_names = (
        "revenue", "cogs", "gross_profit", "operating_expenses",
        "ebitda", "operating_income", "net_income", "capex",
    )

    for canonical in canonical_names:
        label = getattr(canonical_map, canonical, None)
        if label is None:
            metrics[canonical] = None
            notes.append(f"{canonical}: not present in model")
            continue
        li = by_label.get(label)
        if li is None:
            metrics[canonical] = None
            notes.append(
                f"{canonical}: agent referenced label '{label}' but no matching "
                f"line_item exists (agent error)"
            )
            continue
        metrics[canonical] = FinancialModelMetric(
            label=li.label,
            synonym_matched=canonical,
            source_sheet=li.label_cell.sheet,
            source_row_excel_addr=li.label_cell.address,
            unit=li.unit,
            values=li.values_by_period,
        )

    # Gather the full set of periods from all canonical metrics we found.
    years: list[str] = []
    seen: set[str] = set()
    for m in metrics.values():
        if m is None:
            continue
        for p in m.values.keys():
            if p not in seen:
                seen.add(p)
                years.append(p)

    # Scope description
    primary_sheet_name = primary_sheet
    if primary_sheet_name is None:
        # Fall back: use the sheet that hosts the most canonical metrics.
        sheet_counts: dict[str, int] = {}
        for m in metrics.values():
            if m and m.source_sheet:
                sheet_counts[m.source_sheet] = sheet_counts.get(m.source_sheet, 0) + 1
        if sheet_counts:
            primary_sheet_name = max(sheet_counts, key=lambda k: sheet_counts[k])

    n_present = sum(1 for m in metrics.values() if m is not None)
    scope_desc = (
        f"{n_present}/{len(canonical_names)} canonical metrics resolved"
        + (f" from sheet '{primary_sheet_name}'" if primary_sheet_name else "")
    )
    rationale = (
        f"Banker agent identified {primary_sheet_name} as the primary financials sheet "
        f"and resolved {n_present} canonical metrics via label matching."
        if primary_sheet_name else
        "Banker agent resolved canonical metrics across multiple sheets."
    )

    return VoLoFinancialModel(
        status="ok" if n_present > 0 else "ok_with_warnings",
        scope=FinancialModelScope(
            sheet=primary_sheet_name,
            scope_description=scope_desc,
            years_covered=years,
        ),
        selection_rationale=rationale,
        metrics=metrics,
        verification={
            "ok": True,
            "checks_passed": [f"cells_resolved:{n_present}"],
            "errors": [],
            "warnings": notes,
        },
        extractor_version="banker-0.2",
        candidates_considered=[],
    )


def _read_periods(
    wb: ExcelWorkbook,
    header_range: RangeRef,
    values_range: RangeRef,
) -> tuple[list[str], list[Optional[float]]]:
    """Read period headers + values and align them.

    Returns (period_keys, period_values) as parallel lists.
    """
    headers = wb.read_range(header_range.sheet, header_range.range)
    values = wb.read_range(values_range.sheet, values_range.range)

    header_cells = _flatten_cells(headers)
    value_cells = _flatten_cells(values)

    period_keys: list[str] = []
    for hc in header_cells:
        hv = hc.get("value")
        period_keys.append(str(hv) if hv is not None else hc.get("address", "?"))

    period_values: list[Optional[float]] = []
    for vc in value_cells:
        vv = vc.get("value")
        if isinstance(vv, (int, float)) and not isinstance(vv, bool):
            period_values.append(float(vv))
        else:
            period_values.append(None)

    # Pad/truncate to same length
    n = min(len(period_keys), len(period_values))
    return period_keys[:n], period_values[:n]


def _flatten_cells(range_result: dict) -> list[dict]:
    """Flatten a read_range result to a list of cell dicts."""
    shape = range_result.get("shape")
    if shape == "single" or shape == "1d":
        return range_result.get("cells", [])
    # 2d
    grid = range_result.get("grid", [])
    out = []
    for row in grid:
        out.extend(row)
    return out
