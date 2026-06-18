"""Report narrative generation (templates + optional LLM polish).

All facts come in computed; this module only arranges words around them. The
optional LLM polish path re-verifies every numeral (llm.py) and falls back to
the template text on any mismatch.
"""
from __future__ import annotations

import logging
from typing import Optional

from .llm import LLMClient, polish_with_verification
from .schema import Report, Severity, TieOutStatus

log = logging.getLogger(__name__)


def _fmt(v: Optional[float], pct: bool = False) -> str:
    if v is None:
        return "n/a"
    if pct:
        return f"{v:.0%}"
    if abs(v) >= 1000:
        return f"{v:,.0f}"
    return f"{v:,.2f}".rstrip("0").rstrip(".")


def read_this_first(report: Report) -> str:
    """The 3-sentence orientation: what the company does per the model, the
    headline numbers, and the structural quirks."""
    wm = report.workbook_map

    # Sentence 1 — what the model says the company does
    segs = sorted({rm.instance for rm in wm.row_mappings
                   if rm.canonical_id == "revenue_segment" and rm.instance and not rm.demoted})
    caps = sorted({rm.instance for rm in wm.row_mappings
                   if rm.canonical_id == "capacity" and rm.instance and not rm.demoted})
    periods = wm.period_axis.periods if wm.period_axis else []
    span = f"{periods[0]}–{periods[-1]}" if periods else "an unknown horizon"
    gran = wm.period_axis.granularity.value if wm.period_axis else "unknown"
    what = f"The model projects {span} ({gran}) on sheet {wm.primary_statement_sheet!r}"
    if segs:
        what += f", selling {', '.join(segs[:4])}"
    elif caps:
        what += f", building capacity ({', '.join(caps[:3])})"
    what += "."
    plan = report.analysis_plan
    if plan is not None and plan.source == "llm" and plan.rationale:
        what = (f"Business read ({plan.archetype}): {plan.rationale} " + what)

    # Sentence 2 — headline numbers
    by_id = {m.metric_id: m for m in report.derived_metrics}
    parts = []
    rev_rows = [rm for rm in wm.row_mappings if rm.canonical_id == "revenue_total" and not rm.demoted
                and rm.sheet == wm.primary_statement_sheet]
    units = ""
    if wm.primary_statement_sheet and wm.primary_statement_sheet in wm.units:
        units = wm.units[wm.primary_statement_sheet].label
    if "revenue_growth" in by_id and periods:
        pass  # levels read better than growth here
    rev_metric = by_id.get("revenue_ex_grants") or None
    peak = by_id.get("peak_cumulative_consumption")
    if peak is not None and peak.scalar is not None and peak.scalar < 0:
        parts.append(f"peak cumulative cash consumption is {_fmt(abs(peak.scalar))} {units}".rstrip())
    cushion = by_id.get("cushion_ratio")
    if cushion is not None and cushion.scalar is not None:
        parts.append(f"external capital covers it {cushion.scalar:.1f}x at the trough")
    runway = by_id.get("runway_forward_months")
    if runway is not None and runway.series:
        vals = [v for v in runway.series.values() if v is not None and v >= 0]
        if vals:
            parts.append(f"forward runway bottoms at {min(vals):.1f} months")
    headline = ("Headlines: " + "; ".join(parts) + ".") if parts else \
        "Headline cash metrics could not be computed from the mapped rows."

    # Sentence 3 — structural quirks
    quirks = []
    if wm.has_macros:
        quirks.append("macros present (not executed)")
    if wm.hidden_sheets:
        quirks.append(f"{len(wm.hidden_sheets)} hidden sheet(s) included in analysis")
    if any(t.status == TieOutStatus.unfalsifiable for t in report.tie_outs):
        quirks.append("an auto-raise mechanism makes the cash check unfalsifiable")
    low_units = [s for s, u in wm.units.items() if u.confidence < 0.6
                 and s == wm.primary_statement_sheet]
    if low_units:
        quirks.append(f"units on {low_units[0]!r} are inferred, not declared")
    wc = next((m for m in report.derived_metrics if m.metric_id == "working_capital_presence"), None)
    if wc is not None and wc.scalar == 0:
        quirks.append("cash-basis model with no working capital")
    quirk_s = ("Structurally: " + "; ".join(quirks) + ".") if quirks else \
        "No unusual structural mechanics were detected."

    return f"{what} {headline} {quirk_s}"


def trust_sentence(report: Report) -> str:
    score = report.workbook_map.trust_score
    failed = [t for t in report.tie_outs if t.status == TieOutStatus.failed and t.material]
    if failed:
        worst = failed[0]
        return (f"Trust score {score:.2f}: the model violates its own arithmetic "
                f"({worst.label}; worst residual {worst.max_abs_residual:,.2f}). "
                "Findings below are reported with reduced confidence.")
    if score >= 0.95:
        return f"Trust score {score:.2f}: the model's internal arithmetic checks out."
    return f"Trust score {score:.2f}."


_SUMMARY_SYSTEM = (
    "You write the one-paragraph executive summary at the top of a venture analyst's "
    "model-diligence report. You are given the computed facts (business read, the flagged "
    "issues with severity, and key trends). Write 3-5 sentences: what the model is, the most "
    "important flags (lead with the worst), and the general trajectory. Neutral, specific, "
    "investor-facing. Keep every number/percentage EXACTLY as given; introduce no new numbers. "
    "Reply with the paragraph only.")


def build_executive_summary(report: Report, llm: Optional[LLMClient] = None):
    """A short summary of the model + flags + trends. LLM-polished if available,
    deterministic template otherwise (numbers always come from computed facts)."""
    from .schema import ExecutiveSummary, Severity

    wm = report.workbook_map
    plan = report.analysis_plan
    periods = wm.period_axis.periods if wm.period_axis else []
    span = f"{periods[0]}–{periods[-1]}" if periods else "an unspecified horizon"
    gran = wm.period_axis.granularity.value if wm.period_axis else ""
    what = (plan.archetype if plan and plan.archetype and plan.source == "llm"
            else (f"{gran} model".strip() or "model"))

    ranked = report.sorted_findings()
    flags = [f"{f.title}" for f in ranked if f.severity in (Severity.critical, Severity.high)][:4]

    # trends from the metrics
    by = {m.metric_id: m for m in report.derived_metrics}
    trends: list[str] = []
    cagr = by.get("revenue_cagr")
    if cagr and cagr.scalar is not None:
        trends.append(f"revenue compounds at {cagr.scalar:.0%} over the horizon")
    gm = by.get("gross_margin")
    if gm and gm.series:
        vals = [v for v in gm.series.values() if v is not None]
        if vals:
            trends.append(f"gross margin ends near {vals[-1]:.0%}")
    grant = by.get("grant_share_of_revenue")
    if grant and grant.series:
        peak = max((v for v in grant.series.values() if v is not None), default=None)
        if peak and peak > 0.30:
            trends.append(f"grants supply up to {peak:.0%} of revenue")

    # deterministic template
    article = "an" if what[:1].lower() in "aeiou" else "a"
    parts = [f"This is {article} {what} spanning {span} (trust score {wm.trust_score:.2f})."]
    if flags:
        parts.append("Key flags: " + "; ".join(flags) + ".")
    else:
        parts.append("No critical or high-severity flags surfaced.")
    if trends:
        parts.append("Trajectory: " + "; ".join(trends) + ".")
    template = " ".join(parts)

    summary = ExecutiveSummary(text=template, source="template", headline_flags=flags)

    if llm is not None and llm.available:
        facts = (f"Business: {what}\nHorizon: {span}\nTrust score: {wm.trust_score:.2f}\n"
                 f"Flags (worst first): {flags or 'none'}\nTrends: {trends or 'n/a'}")
        polished = llm._call(_SUMMARY_SYSTEM, facts, max_tokens=400, effort="low")
        from .llm import numerals_consistent
        if polished and numerals_consistent(polished, facts + " " + template):
            summary.text = polished.strip()
            summary.source = "llm"
    return summary


def polish_report(report: Report, llm: LLMClient) -> int:
    """Optionally polish finding narratives in place. Returns count polished.

    Every numeral in polished text is verified against the template text;
    mismatches fall back silently (anti-hallucination rule 3).
    """
    n = 0
    if not llm.available:
        return n
    for f in report.findings:
        if f.severity in (Severity.critical, Severity.high, Severity.medium):
            polished, ok = polish_with_verification(llm, f.narrative)
            if ok:
                f.narrative = polished
                n += 1
    return n
