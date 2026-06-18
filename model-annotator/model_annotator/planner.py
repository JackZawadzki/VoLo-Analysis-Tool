"""Analysis planner — the WHY layer (LLM-guided, deterministically executed).

The conditional metric library decides what to compute from the workbook's
STRUCTURE (what's mapped). This module adds the reasoning the spec asks for:
an LLM reads the structure — sheet names, row labels, sections, never cell
values — and decides what kind of business this is, which risks deserve
probing, and which ADDITIONAL relationships are worth computing for this
specific company (an industrial equipment maker is not a SaaS company).

Safety contract, unchanged: the LLM selects and explains; every requested
computation resolves to real workbook rows and runs in pure Python with full
provenance. Anything the LLM names that doesn't exist is dropped with a
recorded skip reason. With --no-llm a deterministic heuristic plan is used
and the pipeline behaves exactly as before.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

from .ingest import WorkbookData
from .llm import LLMClient
from .mapping import LineItem, MappingResult
from .schema import AnalysisPlan, CustomComputation, SheetRole
from .structure import StructureResult

log = logging.getLogger(__name__)

# metric families the planner may prioritize (informational ordering hint)
METRIC_FAMILIES = [
    "growth", "margins", "runway_and_burn", "cumulative_capital", "grants",
    "capex_and_depreciation", "segments_and_concentration", "capacity_and_market_share",
    "valuation", "taxes", "headcount", "working_capital", "unit_economics",
]

_ROW_TOKEN = re.compile(r"^(.+)!r(\d+)$")


# ---------------------------------------------------------------------------
# Digest: what the LLM is allowed to see (labels and shape — never values)
# ---------------------------------------------------------------------------

def build_digest(wbd: WorkbookData, structure: StructureResult, mapping: MappingResult) -> str:
    lines: list[str] = []
    lines.append("SHEETS:")
    for name in wbd.sheet_order:
        role, conf = structure.roles.get(name, (SheetRole.unknown, 0.0))
        ax = structure.primary_axis(name)
        axs = f"{ax.granularity.value} x{len(ax.periods)}" if ax else "no axis"
        lines.append(f"  {name} (role={role.value}, {axs})")
    lines.append(f"PRIMARY STATEMENTS: {mapping.primary_sheet}")
    lines.append("MAPPED ROWS (canonical_id <- row):")
    for it in mapping.mapped()[:70]:
        inst = f" [{it.instance}]" if it.instance else ""
        lines.append(f"  {it.canonical_id}{inst} <- {it.sheet}!r{it.index} {it.label.strip()[:60]!r}")
    unmapped = [it for it in mapping.items
                if it.canonical_id is None and it.n_numeric >= 2
                and it.sheet == mapping.primary_sheet]
    if unmapped:
        lines.append("UNMAPPED NUMERIC ROWS on the primary sheet (candidates for relationships):")
        for it in unmapped[:45]:
            lines.append(f"  {it.sheet}!r{it.index} {it.label.strip()[:70]!r} (section {it.section[:30]!r})")
    # documentation excerpts often state what the company does
    doc_bits: list[str] = []
    for name, sd in wbd.sheets.items():
        role, _ = structure.roles.get(name, (SheetRole.unknown, 0.0))
        if role != SheetRole.documentation:
            continue
        for _, _, rec in sd.iter_cells():
            if isinstance(rec.value, str) and 30 < len(rec.value) < 400:
                doc_bits.append(rec.value.strip())
            if len(doc_bits) >= 6:
                break
        break
    if doc_bits:
        lines.append("DOCUMENTATION EXCERPTS:")
        for b in doc_bits[:6]:
            lines.append(f"  - {b[:200]}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Plan construction
# ---------------------------------------------------------------------------

_PLAN_SYSTEM = """You are the analysis planner inside an automated financial-model diligence tool used by venture investors. You see ONLY the structure of a startup's Excel model (sheet names, row labels, sections) — never cell values. Your job is the WHY layer: decide what kind of business this model describes and which calculations matter for THIS company, the way a senior analyst would (an industrial equipment maker is judged differently than a SaaS company).

Reply with ONLY a JSON object, no prose, no code fences:
{
 "archetype": "<short free-form business read, e.g. 'industrial equipment with turnkey + service revenue'>",
 "benchmark_archetype": "<exactly one of: BENCHMARK_KEYS>",
 "rationale": "<=2 sentences: what this company does per the model and why that framing>",
 "risks_to_probe": ["<=5 short risk phrases specific to this archetype"],
 "priorities": ["<=5 from: METRIC_FAMILIES"],
 "custom_computations": [
   {"name": "<snake_case>", "numerator": "<Sheet!rN exactly as shown in the digest>",
    "denominator": "<Sheet!rN>", "rationale": "<why this ratio matters for this business>"}
 ]
}
custom_computations: at most 5, ONLY ratios that the standard library would miss and that matter for this archetype (e.g. consumables revenue per installed unit, service attach rate, cost per unit of capacity). Use ONLY row tokens that appear in the digest. If nothing extra is needed, return an empty list. You never compute numbers — a deterministic engine executes what you request and cites every cell."""


def heuristic_plan(mapping: MappingResult) -> AnalysisPlan:
    """The deterministic fallback: same behavior the tool had before the planner."""
    hardware = bool(mapping.get("capacity") or mapping.get_all("units_sold"))
    return AnalysisPlan(
        source="heuristic",
        archetype="hardware/industrial (capacity or unit rows present)" if hardware else "unclassified",
        benchmark_archetype="hardware_industrial" if hardware else "default",
        rationale="Structural heuristic: " +
                  ("capacity/unit rows mapped." if hardware else "no physical-economy rows mapped."),
    )


def build_analysis_plan(
    wbd: WorkbookData,
    structure: StructureResult,
    mapping: MappingResult,
    llm: LLMClient,
    benchmarks: dict,
) -> AnalysisPlan:
    fallback = heuristic_plan(mapping)
    if not llm.available or mapping.primary_sheet is None:
        return fallback

    # Only the archetype-bearing benchmark sections define valid archetypes;
    # meta/sensitivity sections (description, sources, ebitda_multiples, ...) must
    # NOT leak into the whitelist offered to the LLM.
    _ARCHETYPE_SECTIONS = ("ev_ebitda_multiple", "ev_revenue_multiple", "gross_margin_pct",
                           "discount_rate")
    bench_keys = sorted({k for name in _ARCHETYPE_SECTIONS
                         for k in (benchmarks.get(name) or {}).keys()
                         if k not in ("low", "typical", "high")} | {"default"})
    system = _PLAN_SYSTEM.replace("BENCHMARK_KEYS", ", ".join(bench_keys)) \
                         .replace("METRIC_FAMILIES", ", ".join(METRIC_FAMILIES))
    digest = build_digest(wbd, structure, mapping)
    raw = llm.complete_json(system, digest, max_tokens=1500)
    if raw is None:
        return fallback

    plan = AnalysisPlan(source="llm")
    plan.archetype = str(raw.get("archetype", ""))[:120] or fallback.archetype
    ba = str(raw.get("benchmark_archetype", "default"))
    plan.benchmark_archetype = ba if ba in bench_keys else fallback.benchmark_archetype
    plan.rationale = str(raw.get("rationale", ""))[:400]
    plan.risks_to_probe = [str(r)[:120] for r in (raw.get("risks_to_probe") or [])[:5]]
    plan.priorities = [p for p in (raw.get("priorities") or []) if p in METRIC_FAMILIES][:5]

    items_by_token = {f"{it.sheet}!r{it.index}": it for it in mapping.items}
    used_names: set[str] = set()
    for c in (raw.get("custom_computations") or [])[:5]:
        try:
            name = re.sub(r"[^a-z0-9_]+", "_", str(c.get("name", "ratio")).lower())[:40] or "ratio"
            num_tok = str(c.get("numerator", ""))
            den_tok = str(c.get("denominator", ""))
            rationale = str(c.get("rationale", ""))[:300]
        except Exception:
            continue
        # suffix colliding slugs so two computations don't share one metric_id
        base_name = name
        n = 2
        while name in used_names:
            name = f"{base_name}_{n}"
            n += 1
        used_names.add(name)
        comp = CustomComputation(
            name=name, metric_id=f"llm_directed::{name}",
            numerator_ref=num_tok, denominator_ref=den_tok, rationale=rationale)
        if num_tok not in items_by_token or den_tok not in items_by_token:
            comp.executed = False
            comp.skip_reason = "row token not found in the workbook digest; dropped (anti-hallucination)"
        plan.custom_computations.append(comp)
    return plan


# ---------------------------------------------------------------------------
# Deterministic execution of LLM-directed computations
# ---------------------------------------------------------------------------

def execute_plan_computations(plan: AnalysisPlan, mapping: MappingResult,
                              structure: StructureResult) -> list:
    """Run each validated custom computation in pure Python. Returns DerivedMetric list."""
    from .metrics import EPS, mk

    out = []
    if mapping.primary_sheet is None:
        return out
    items_by_token: dict[str, LineItem] = {f"{it.sheet}!r{it.index}": it for it in mapping.items}

    def _scale(sheet: str) -> float:
        u = structure.units.get(sheet)
        return u.scale if u else 1.0

    for comp in plan.custom_computations:
        if not comp.executed:
            continue
        num = items_by_token.get(comp.numerator_ref)
        den = items_by_token.get(comp.denominator_ref)
        if num is None or den is None:
            comp.executed = False
            comp.skip_reason = "row disappeared between planning and execution"
            continue
        # iterate the INTERSECTION of the two rows' own periods (they may live
        # on sheets of different granularity); skip clearly if they don't overlap
        common = [p for p in num.periods if p in set(den.periods)]
        if not common:
            comp.executed = False
            comp.skip_reason = ("the two rows have no shared periods (different granularity sheets); "
                                "not computed to avoid a misaligned ratio")
            continue
        # reconcile units across sheets so a $thousands ÷ $1 ratio isn't 1000x off
        cross = num.sheet != den.sheet
        ns, ds = _scale(num.sheet), _scale(den.sheet)
        series = {}
        for p in common:
            nv, dv = num.value_for(p), den.value_for(p)
            if nv is not None and dv is not None and abs(dv * ds) > EPS:
                series[p] = (nv * ns) / (dv * ds)
            else:
                series[p] = None
        if not any(v is not None for v in series.values()):
            comp.executed = False
            comp.skip_reason = "no overlapping numeric periods between the two rows"
            continue
        note = comp.rationale
        if cross:
            note += f" [cross-sheet: {num.sheet}÷{den.sheet}, units reconciled]"
        out.append(mk(
            comp.metric_id,
            f"LLM-directed: {comp.name.replace('_', ' ')}",
            applicability=f"requested by the analysis planner: {comp.rationale[:160]}",
            inputs=(num.refs + den.refs)[:60],
            computation=f"{num.label.strip()[:40]!r}[t] / {den.label.strip()[:40]!r}[t] "
                        "(LLM chose the ratio; Python computed it"
                        + (", cross-sheet units reconciled)" if cross else ")"),
            series=series,
            units="ratio",
            notes=note,
        ))
    return out
