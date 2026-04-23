"""Live invocation of the banker agent with streaming progress.

Imports the banker agent from its separate project directory, runs it on an
uploaded xlsx file, and streams progress events back to the caller. Produces
a dict shaped to match `_adapt_new_extractor_response` output directly — no
Pydantic-in-the-middle, no banker_bridge, no intermediate file.

Callers iterate over the returned generator; each yielded item is either:
    {"type": "progress", "message": "..."}
    {"type": "result",   "data": {...VoLo response shape...}}
    {"type": "error",    "message": "..."}

The agent runs in a worker thread while the main thread drains the queue.
This keeps the HTTP handler responsive and lets us emit SSE/JSONL to the
browser as extraction progresses.
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import traceback
from typing import Any, Dict, Generator, Optional

logger = logging.getLogger(__name__)

def _import_banker():
    """Lazy-import the banker package bundled inside app.engine.banker.

    Deferred so that cold-start failures in Anthropic SDK or openpyxl don't
    block the server from booting. In practice all of these deps are already
    in requirements.txt, so this rarely errors.
    """
    from .banker.agent import run_agent, fill_values
    from .banker.schemas import ExtractedModel
    return run_agent, fill_values, ExtractedModel


def _extracted_model_to_volo_response(
    em,
    file_name: str,
    tokens_in: int = 0,
    tokens_out: int = 0,
    turns: int = 0,
) -> Dict[str, Any]:
    """Convert a banker ExtractedModel directly to VoLo's extraction response shape.

    This is the single adapter replacing `banker_bridge.extract_financial_model_section`
    plus `_adapt_new_extractor_response`. No intermediate schemas, no second
    translation step — one function, banker output → VoLo response dict.
    """
    fm = em.financial_model
    metrics = fm.metrics or {}
    scope = fm.scope

    financials: Dict[str, Dict[str, Any]] = {}
    units: Dict[str, str] = {}
    for canonical, m in metrics.items():
        if not m:
            continue
        vals = m.values or {}
        kept = {y: v for y, v in vals.items() if v is not None}
        if not kept:
            continue
        financials[canonical] = kept
        if m.unit:
            units[canonical] = m.unit

    rev_unit = units.get("revenue") or next(iter(units.values()), None) or "USD"
    scale_info = {
        "USD_M": "USD_M (in millions)",
        "USD_K": "USD_K (in thousands)",
        "USD_B": "USD_B (in billions)",
        "USD": "USD",
    }.get(rev_unit, rev_unit)

    years = list(scope.years_covered or [])
    diagnostics = {
        "extractor": "banker-agent",
        "chosen_sheet": scope.sheet,
        "scope_description": scope.scope_description,
        "selection_rationale": fm.selection_rationale,
        "verification_checks_passed": (fm.verification or {}).get("checks_passed", []),
        "verification_warnings": (fm.verification or {}).get("warnings", []),
        "verification_errors": (fm.verification or {}).get("errors", []),
        "candidates_considered": list(fm.candidates_considered or []),
        "metric_source_cells": {
            k: (m.source_row_excel_addr if m else None)
            for k, m in metrics.items()
        },
        "banker_agent_turns": turns,
        "banker_tokens_in": tokens_in,
        "banker_tokens_out": tokens_out,
        # Counts of enrichment data available; not consumed by the current
        # pipeline but surfaced for future memo integration.
        "banker_enrichment": {
            "n_sheets": len(em.sheets or []),
            "n_line_items": len(em.line_items or []),
            "n_assumptions": len(em.assumptions or []),
            "n_narratives": len(em.narratives or []),
        },
    }

    return {
        "status": fm.status or "ok",
        "file_name": file_name,
        "records_count": sum(len(v) for v in financials.values()),
        "failures_count": 0,
        "financials": financials,
        "units": units,
        "fiscal_years": years,
        "scale_info": scale_info,
        "model_summary": {
            "sheet": scope.sheet,
            "description": scope.scope_description,
            "years": f"{min(years)}–{max(years)}" if years else "",
            "metrics_extracted": sorted(financials.keys()),
        },
        "scenarios": None,
        "detected_scenarios": ["base"],
        "primary_scenario": "base",
        "_diagnostics": diagnostics,
    }


def run_banker_streaming(
    workbook_path: str | Path,
    file_name: Optional[str] = None,
    model: str = "claude-sonnet-4-5",
    max_turns: int = 30,
) -> Generator[Dict[str, Any], None, None]:
    """Run the banker agent on a workbook and yield progress + final events.

    Yields in order:
      - 1+ {"type": "progress", "message": ...} events as the agent runs
      - exactly one terminal event:
          * {"type": "result", "data": {...VoLo response shape...}} on success
          * {"type": "error",  "message": ...}                       on failure
    """
    path = Path(workbook_path)
    fname = file_name or path.name
    q: "queue.Queue[Dict[str, Any]]" = queue.Queue()
    _SENTINEL: Dict[str, Any] = {"_sentinel": True}

    def _progress(msg: str) -> None:
        q.put({"type": "progress", "message": msg})

    def _worker() -> None:
        try:
            run_agent, fill_values, _ = _import_banker()
        except Exception as exc:
            logger.exception("Failed to import banker package")
            q.put({"type": "error", "message": f"Banker package import failed: {exc}"})
            q.put(_SENTINEL)
            return

        try:
            _progress("Starting AI extraction...")
            result = run_agent(
                path,
                model=model,
                max_turns=max_turns,
                progress_callback=_progress,
            )
            _progress("Reading cell values deterministically...")
            em = fill_values(result.draft, path, model_used=model)
            _progress("Formatting output for VoLo pipeline...")
            adapted = _extracted_model_to_volo_response(
                em,
                file_name=fname,
                tokens_in=result.tokens_in,
                tokens_out=result.tokens_out,
                turns=result.turns,
            )
            q.put({"type": "result", "data": adapted})
        except Exception as exc:
            logger.exception("Banker agent failed")
            q.put({
                "type": "error",
                "message": f"{type(exc).__name__}: {exc}",
                "trace": traceback.format_exc(limit=5),
            })
        finally:
            q.put(_SENTINEL)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()

    # Drain the queue until the sentinel arrives. Terminal events (result/error)
    # are also yielded — callers decide when to stop based on event type.
    while True:
        try:
            evt = q.get(timeout=600)  # 10-minute hard ceiling
        except queue.Empty:
            yield {"type": "error", "message": "Banker extraction timed out after 10 minutes"}
            return
        if evt.get("_sentinel"):
            return
        yield evt
