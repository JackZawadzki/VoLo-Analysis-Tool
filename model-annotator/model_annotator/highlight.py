"""Highlight engine — which values in a derived row jump out, and why.

This is the cell-level analogue of the analyst's red-font flags: for a derived
metric series, it returns the periods whose values are notable, each with a
short comment explaining the standout (shown on hover in the report) and a
severity tier for color. Rules are metric-family-aware with a statistical
fallback so even LLM-directed metrics get sensible flags.

Every comment is generated from the computed value itself — no external claim.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Callable, Optional

from .schema import Severity

EPS = 1e-9


@dataclass
class Highlight:
    period: str
    value: float
    comment: str
    severity: Severity


def _pct(v: float) -> str:
    return f"{v:.0%}" if abs(v) >= 0.01 or v == 0 else f"{v:.1%}"


def _num(v: float) -> str:
    if abs(v) >= 1000:
        return f"{v:,.0f}"
    return f"{v:,.2f}".rstrip("0").rstrip(".")


def _valid(series: dict[str, Optional[float]], periods: list[str]) -> list[tuple[str, float]]:
    return [(p, series[p]) for p in periods if series.get(p) is not None]


# ---------------------------------------------------------------------------
# Family rules: (matches(metric_id) -> bool, rule(valid_points) -> {period: (comment, severity)})
# ---------------------------------------------------------------------------

def _rule_grant_share(points):
    out = {}
    for p, v in points:
        if v > 0.30:
            sev = Severity.high if v > 0.5 else Severity.medium
            out[p] = (f"Grants are {_pct(v)} of revenue here — only {_pct(1 - v)} is commercial, "
                      "non-repeatable revenue.", sev)
    return out


def _rule_negative_growth_after_positive(points):
    out = {}
    prev = None
    for p, v in points:
        if v < -0.02 and prev is not None and prev > 0:
            out[p] = (f"Commercial trajectory turns down {_pct(v)} after growing — a decline the "
                      "headline total can mask.", Severity.high)
        prev = v
    return out


def _rule_growth(points):
    """Flag what an analyst actually circles: a sign-flip/whiplash, a turn
    negative, and the single biggest spike — not every early-year doubling."""
    out = {}
    prev = None
    for p, v in points:
        if prev is not None:
            if (prev > 0.5 and v < -0.2) or (prev < -0.2 and v > 0.5):
                out[p] = (f"Growth whipsaws from {_pct(prev)} to {_pct(v)} — usually a one-off "
                          "(grant, single project), not the underlying business.", Severity.medium)
            elif abs(prev) > EPS and abs(v / prev) > 8 and abs(v) > 2.0:
                out[p] = (f"Growth jumps {abs(v / prev):.0f}x vs the prior period ({_pct(prev)} → "
                          f"{_pct(v)}).", Severity.medium)
        prev = v
    # a single negative period after the line was growing (commercial stall)
    prev = None
    for p, v in points:
        if v < -0.02 and prev is not None and prev > 0.05 and p not in out:
            out[p] = (f"Turns down {_pct(v)} after growing — a stall the cumulative total hides.",
                      Severity.medium)
        prev = v
    # the single largest spike, only if it's a genuine >100% jump and not noise
    spikes = [(p, v) for p, v in points if v > 1.0 and p not in out]
    if spikes:
        p, v = max(spikes, key=lambda kv: kv[1])
        out[p] = (f"Largest single-period jump: {_pct(v)}. What concrete event drives it — new "
                  "contracts, timing, a one-off?", Severity.medium)
    return out


def _rule_ex_grants_growth(points):
    """The commercial growth story once grants are stripped out: declines,
    whipsaws/spikes, AND an explicit 'this is erratic' call when the rate swings
    all over the place (no steady trajectory under the grants)."""
    out = {}
    out.update(_rule_growth(points))                       # whipsaws, spikes, stalls
    out.update(_rule_negative_growth_after_positive(points))  # declines take priority (high sev)
    vals = [v for _, v in points]
    if len(vals) >= 3:
        sign_flips = sum(1 for i in range(1, len(vals)) if (vals[i] > 0) != (vals[i - 1] > 0))
        spread = max(vals) - min(vals)
        if sign_flips >= 2 or spread > 5.0:               # repeated flips, or >500pp range
            # anchor on the first period so this whole-series verdict leads the flag
            out[points[0][0]] = (f"Ex-grant growth is all over the place — it swings from "
                                 f"{_pct(min(vals))} to {_pct(max(vals))} across the plan, so there's "
                                 "no steady commercial trajectory underneath the grant revenue.",
                                 Severity.high)
    return out


def _rule_runway(points):
    out = {}
    for p, v in points:
        if 0 <= v < 3.0:
            out[p] = (f"Only {v:.1f} months of runway entering this period — a financing cliff.",
                      Severity.high)
        elif v < 0:
            out[p] = (f"Runway is negative ({v:.1f}) — the model is already past empty here.",
                      Severity.high)
    return out


def _rule_margin(points):
    out = {}
    # P2-3 stage-awareness: RED means broken/contradictory, not coherent pre-scale
    # economics. If this SAME margin turns positive by the end of the modeled
    # horizon, the early negative periods are the expected ramp — note them quietly
    # (medium). Only a margin still negative at the end of the plan is structural.
    turns_positive = bool(points) and points[-1][1] is not None and points[-1][1] > 0
    for p, v in points:
        if v < 0:
            if turns_positive:
                out[p] = (f"Negative margin ({_pct(v)}) during the ramp — it turns positive later "
                          "in the plan, so this is stage-appropriate rather than structural.",
                          Severity.medium)
            else:
                out[p] = (f"Negative margin ({_pct(v)}) — selling below cost, and the plan never "
                          "shows it turning positive.", Severity.high)
        elif v > 0.95:
            out[p] = (f"Margin of {_pct(v)} is near-100% — check whether this is grant/subsidy "
                      "revenue carried at no cost.", Severity.medium)
    return out


def _rule_opex_over_rev(points):
    out = {}
    for p, v in points:
        if v > 1.0:
            out[p] = (f"Operating expenses are {_num(v)}x revenue here — pre-scale, every dollar of "
                      "revenue is outspent.", Severity.medium)
    return out


def _rule_effective_tax(points):
    out = {}
    for p, v in points:
        if v < -0.01:
            out[p] = (f"Negative effective tax rate ({_pct(v)}) — a tax benefit booked as income; "
                      "rarely monetizable in cash for an early-stage company.", Severity.medium)
        elif v > 0.45:
            out[p] = (f"Effective tax rate of {_pct(v)} is above statutory — worth reconciling.",
                      Severity.medium)
    return out


def _rule_share(points):
    out = {}
    for p, v in points:
        if v > 0.5:
            out[p] = (f"One line carries {_pct(v)} of revenue here — concentration risk.",
                      Severity.medium)
    return out


def _rule_conversion(points):
    out = {}
    for p, v in points:
        if v < 0:
            out[p] = (f"Negative cash conversion ({_num(v)}) — EBITDA isn't turning into cash here.",
                      Severity.medium)
    return out


_FAMILY_RULES: list[tuple[Callable[[str], bool], Callable]] = [
    (lambda m: m == "grant_share_of_revenue", _rule_grant_share),
    (lambda m: m == "revenue_ex_grants_growth", _rule_ex_grants_growth),
    (lambda m: m.endswith("_growth"), _rule_growth),
    (lambda m: m.startswith("runway_"), _rule_runway),
    (lambda m: "margin" in m, _rule_margin),
    (lambda m: m == "opex_over_revenue", _rule_opex_over_rev),
    (lambda m: m == "effective_tax_rate", _rule_effective_tax),
    (lambda m: m.startswith("segment_share") or m == "terminal_concentration", _rule_share),
    (lambda m: m == "cash_conversion", _rule_conversion),
]


def _statistical_fallback(points) -> dict:
    """Generic standout detection for any series with no family rule:
    robust outliers (MAD) and the single max/min when the spread is wide."""
    out: dict[str, tuple[str, Severity]] = {}
    vals = [v for _, v in points]
    if len(vals) < 4:
        return out
    med = statistics.median(vals)
    devs = [abs(v - med) for v in vals]
    mad = statistics.median(devs) or (statistics.pstdev(vals) if len(vals) > 1 else 0)
    if mad <= EPS:
        return out
    for p, v in points:
        z = abs(v - med) / mad
        if z > 4.0:
            direction = "far above" if v > med else "far below"
            out[p] = (f"{_num(v)} is {direction} the series norm ({_num(med)}) — an outlier worth "
                      "a second look.", Severity.medium)
    return out


def compute_highlights(metric_id: str, series: dict[str, Optional[float]],
                       periods: list[str]) -> list[Highlight]:
    points = _valid(series, periods)
    if not points:
        return []
    chosen: dict[str, tuple[str, Severity]] = {}
    matched = False
    for matches, rule in _FAMILY_RULES:
        if matches(metric_id):
            matched = True
            for p, (comment, sev) in rule(points).items():
                chosen.setdefault(p, (comment, sev))
    if not matched:
        for p, (comment, sev) in _statistical_fallback(points).items():
            chosen.setdefault(p, (comment, sev))
    val = dict(points)
    return [Highlight(period=p, value=val[p], comment=c, severity=s)
            for p, (c, s) in sorted(chosen.items(), key=lambda kv: periods.index(kv[0]))]
