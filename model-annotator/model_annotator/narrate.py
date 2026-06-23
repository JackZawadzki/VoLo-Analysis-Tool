"""Report narrative generation (templates + optional LLM polish).

All facts come in computed; this module only arranges words around them. The
optional LLM polish path re-verifies every numeral (llm.py) and falls back to
the template text on any mismatch.
"""
from __future__ import annotations

import logging
import re
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
    "You write the executive summary at the top of a venture analyst's model-diligence report. "
    "You are given the computed facts: a business read, the flagged issues (worst first), and the "
    "key quantitative reads the calculations produced. Write 3-4 tight sentences that tell a partner "
    "WHAT THE ANALYSIS REVEALED ABOUT THIS COMPANY: what it is and where it's headed, the most "
    "important thing the flags expose (lead with the worst), and the 2-3 reads that most affect the "
    "decision (mix, survival/runway, margin, concentration). Specific and investor-facing, not a list "
    "of metric names. Keep every number EXACTLY as given; introduce no new numbers. Reply with the "
    "paragraph only.")


def _summary_numerals_ok(polished: str, facts_text: str, periods: list[str]) -> bool:
    """Tolerant numeral check for the EXECUTIVE SUMMARY only (finding narratives
    stay strict): allow any in-range year and small rounding of a given figure,
    so the model can write real prose ('troughs at ~$0.6M in 2028') without being
    rejected — it still may not invent an unrelated number."""
    from .llm import extract_numerals

    def fl(s):
        try:
            return float(s)
        except ValueError:
            return None
    allowed = [fl(x) for x in extract_numerals(facts_text)]
    allowed = [a for a in allowed if a is not None]
    yrs = [int(p[:4]) for p in periods if p[:4].isdigit()]
    lo, hi = (min(yrs), max(yrs)) if yrs else (0, 0)
    for tok in extract_numerals(polished):
        v = fl(tok)
        if v is None:                                   # cell ref / odd token — require exact
            if tok not in extract_numerals(facts_text):
                return False
            continue
        if lo and lo <= v <= hi and v == int(v):        # any year in the model span
            continue
        if any(abs(v - a) <= 0.03 * max(abs(a), 1.0) for a in allowed):   # rounding of a given figure
            continue
        return False
    return True


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

    # the reads that most change the decision: growth, mix, survival, margin, concentration
    by = {m.metric_id: m for m in report.derived_metrics}

    def _last(series):
        vals = [v for v in (series or {}).values() if v is not None]
        return vals[-1] if vals else None

    trends: list[str] = []
    cagr = by.get("revenue_cagr")
    if cagr and cagr.scalar is not None:
        trends.append(f"revenue compounds at {cagr.scalar:.0%} over the horizon")
    # revenue mix shift — usually the real story
    mix_best = None
    for mid, m in by.items():
        if mid.startswith("revenue_mix_") and m.series:
            vals = [v for v in m.series.values() if v is not None]
            if len(vals) >= 2 and (max(vals) - min(vals)) >= 0.20:
                if mix_best is None or (max(vals) - min(vals)) > mix_best[0]:
                    mix_best = (max(vals) - min(vals), mid.replace("revenue_mix_", "").replace("_", "-"), vals[-1])
    if mix_best:
        trends.append(f"{mix_best[1]} revenue rises to {mix_best[2]:.0%} of the top line")
    # survival
    def _money(v):
        a = abs(v)
        return (f"{v/1e6:.1f}M" if a >= 1e6 else f"{v/1e3:.0f}K" if a >= 1e3 else f"{v:.0f}")
    mc = by.get("minimum_cash")
    if mc and mc.scalar is not None:
        trends.append(f"cash troughs around {_money(mc.scalar)}" + (" and goes negative" if mc.scalar < 0 else ""))
    rw = by.get("runway_forward_months") or by.get("runway_same_period_months")
    if rw and rw.series:
        rvals = [v for v in rw.series.values() if v is not None]
        if rvals and min(rvals) < 12:
            lo = min(rvals)
            trends.append(f"runway tightens to ~{lo:.1f} month{'s' if lo >= 2 else ''} at its low point")
    # profitability + concentration
    em = by.get("ebitda_margin")
    if em and _last(em.series) is not None:
        lbl = em.label.replace(" margin", "").lower()
        trends.append(f"{lbl} margin ends near {_last(em.series):.0%}")
    grant = by.get("grant_share_of_revenue")
    if grant and grant.series:
        peak = max((v for v in grant.series.values() if v is not None), default=None)
        if peak and peak > 0.30:
            trends.append(f"grants supply up to {peak:.0%} of revenue")
    conc = by.get("terminal_concentration")
    if conc and conc.scalar is not None and conc.scalar > 0.5:
        trends.append(f"one segment is {conc.scalar:.0%} of terminal revenue")

    # deterministic template (the LLM polishes this into prose; numbers are fixed)
    article = "an" if what[:1].lower() in "aeiou" else "a"
    parts = [f"This is {article} {what} spanning {span}."]
    if flags:
        parts.append("The analysis flags: " + "; ".join(flags) + ".")
    else:
        parts.append("No critical or high-severity issues surfaced in the checks run.")
    if trends:
        parts.append("Key reads: " + "; ".join(trends) + ".")
    template = " ".join(parts)

    summary = ExecutiveSummary(text=template, source="template", headline_flags=flags)

    if llm is not None and llm.available:
        # Give the model the full year list (so it may cite the trough/raise year)
        # and the business read, so it can describe THIS company. Trust score is
        # deliberately withheld (removed from the product).
        business = (plan.rationale if plan and getattr(plan, "rationale", None) and plan.source == "llm"
                    else what)
        facts = (f"Business: {business}\nHorizon: {span} (years available to cite: {', '.join(periods)})\n"
                 f"Flags (worst first): {flags or 'none'}\nKey reads: {trends or 'n/a'}")
        polished = llm._call(_SUMMARY_SYSTEM, facts, max_tokens=1024, effort="low")
        from .llm import numerals_consistent
        if polished:
            # belt-and-suspenders: trust score is gone from the product, so drop any
            # sentence that mentions it even if the model reintroduces the phrase
            sents = re.split(r'(?<=[.!?])\s+', polished.strip())
            polished = " ".join(s for s in sents if "trust score" not in s.lower()).strip()
        if polished and _summary_numerals_ok(polished, facts + " " + template, periods):
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
