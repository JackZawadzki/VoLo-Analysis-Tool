"""Sensitivity — built off the model's OWN input cells, presented the way the
DD financials tool presents it: a Shapley contribution bar (how much each input
drives the base→downside move) plus a two-way grid the analyst can drive
interactively (any two inputs at once, red→green).

Faithful and engine-free: each driver is a real model cell, each output is a
quantity the tool owns the closed form for, so the recompute is exact. To
"include every input that could affect the output", the income-statement output
is decomposed all the way to the model's line items — every mapped revenue
segment, COGS component, and opex component becomes its own driver, not just the
three totals.

Outputs:
  - linear_sum  : output = Σ coef·driver  (EBITDA = Σ segments − Σ COGS − Σ opex)
  - valuation_pv: PV = multiple × EBITDA_exit / (1+rate)^horizon
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from itertools import combinations
from math import factorial
from typing import Optional

from .mapping import LineItem, MappingResult
from .metrics import find_scalar_assumptions
from .schema import SensitivityDriver, Tornado
from .structure import StructureResult

DEFAULT_PCT = 0.20
MAX_SHAPLEY_DRIVERS = 12     # exact Shapley caps here; linear outputs use the closed form


@dataclass
class _Spec:
    key: str
    label: str
    refs: list[str]
    base: float
    coef: float                 # +1 adds to output, −1 subtracts (linear_sum)
    unit: str = ""
    low_pct: float = -DEFAULT_PCT
    high_pct: float = DEFAULT_PCT
    low_val: Optional[float] = None
    high_val: Optional[float] = None
    # which direction is "adverse" (lowers the output): -1 means low value is adverse
    adverse: str = "low"        # "low" | "high"

    def low(self) -> float:
        return self.low_val if self.low_val is not None else self.base * (1 + self.low_pct)

    def high(self) -> float:
        return self.high_val if self.high_val is not None else self.base * (1 + self.high_pct)

    def adverse_val(self) -> float:
        return self.low() if self.adverse == "low" else self.high()

    def favorable_val(self) -> float:
        return self.high() if self.adverse == "low" else self.low()


def _terminal(it: Optional[LineItem]):
    if it is None:
        return None
    for p in reversed(it.periods):
        v = it.value_for(p)
        if v is not None:
            return p, float(v), it.ref_for(p)
    return None


def _terminal_for(it: LineItem, period: str):
    v = it.value_for(period)
    return (period, float(v), it.ref_for(period)) if v is not None else None


# ---------------------------------------------------------------------------
# Output evaluators
# ---------------------------------------------------------------------------

def _eval_linear(specs: list[_Spec], overrides: dict[str, float]) -> float:
    return sum(s.coef * overrides.get(s.key, s.base) for s in specs)


def _eval_pv(specs_by_key: dict[str, _Spec], overrides: dict[str, float], horizon: float) -> float:
    def g(k):
        return overrides.get(k, specs_by_key[k].base) if k in specs_by_key else 0.0
    rate = g("rate") if "rate" in specs_by_key else 0.0
    return g("multiple") * g("metric") / ((1 + rate) ** horizon)


# ---------------------------------------------------------------------------
# Shapley contribution of each driver to the base→downside move
# ---------------------------------------------------------------------------

def _shapley_linear(specs: list[_Spec]) -> dict[str, float]:
    # linear output has no interactions: contribution = coef·(adverse − base)
    return {s.key: s.coef * (s.adverse_val() - s.base) for s in specs}


def _shapley_exact(specs: list[_Spec], eval_fn) -> dict[str, float]:
    keys = [s.key for s in specs]
    base = {s.key: s.base for s in specs}
    adv = {s.key: s.adverse_val() for s in specs}
    cache: dict[frozenset, float] = {}

    def f(S: frozenset) -> float:
        if S in cache:
            return cache[S]
        ov = dict(base)
        for k in S:
            ov[k] = adv[k]
        val = eval_fn(ov)
        cache[S] = val
        return val

    n = len(keys)
    phi = {k: 0.0 for k in keys}
    for k in keys:
        rest = [x for x in keys if x != k]
        for r in range(len(rest) + 1):
            w = factorial(r) * factorial(n - r - 1) / factorial(n)
            for S in combinations(rest, r):
                phi[k] += w * (f(frozenset(S) | {k}) - f(frozenset(S)))
    return phi


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def _mk_driver(s: _Spec, output_low: float, output_high: float, shapley: float) -> SensitivityDriver:
    lo, hi = s.low(), s.high()
    base = s.base
    lp = (lo - base) / base if abs(base) > 1e-9 else s.low_pct
    hp = (hi - base) / base if abs(base) > 1e-9 else s.high_pct
    return SensitivityDriver(
        key=s.key, label=s.label, input_refs=s.refs, base=base, low=lo, high=hi,
        low_pct=round(lp, 4), high_pct=round(hp, 4),
        output_low=output_low, output_high=output_high, swing=abs(output_high - output_low),
        shapley=shapley, coef=s.coef, unit=s.unit)


def _components(ctx_items, sheet, period, coef, key_prefix, unit) -> list[_Spec]:
    specs = []
    for it in ctx_items:
        if it.sheet != sheet:
            continue
        t = _terminal_for(it, period)
        if t is None:
            continue
        _, v, ref = t
        inst = (it.instance or it.label.strip())[:32]
        specs.append(_Spec(key=f"{key_prefix}:{it.index}", label=f"{inst} ({period})",
                           refs=[ref], base=abs(v) if coef < 0 else v, coef=coef, unit=unit,
                           adverse="high" if coef < 0 else "low"))
    return specs


def build_sensitivities(wbd, structure: StructureResult, mapping: MappingResult,
                        benchmarks: dict, archetype: str = "default",
                        periods: Optional[list[str]] = None) -> list[Tornado]:
    out: list[Tornado] = []
    sheet = mapping.primary_sheet
    if sheet is None:
        return out
    if periods is None:
        ax = structure.primary_axis(sheet)
        periods = list(ax.periods) if ax else []
    if not periods:
        return out
    units = structure.units.get(sheet)
    U = units.label if units else "model units"

    rev = mapping.get("revenue_total", sheet)
    tp = None
    if rev is not None:
        t = _terminal(rev)
        tp = t[0] if t else None

    # ---- Output 1: terminal EBITDA, decomposed to EVERY line item ----------
    if rev is not None and tp is not None:
        specs: list[_Spec] = []
        segs = [it for it in mapping.get_all("revenue_segment") if it.sheet == sheet]
        if segs:
            specs += _components(segs, sheet, tp, +1.0, "seg", U)
        else:
            rt = _terminal_for(rev, tp)
            if rt:
                specs.append(_Spec("revenue", f"Total revenue ({tp})", [rt[2]], rt[1], +1.0, U, adverse="low"))
        cogs_comp = [it for it in mapping.get_all("cogs_component") if it.sheet == sheet]
        if cogs_comp:
            specs += _components(cogs_comp, sheet, tp, -1.0, "cogs", U)
        else:
            ct = _terminal_for(mapping.get("cogs_total", sheet), tp) if mapping.get("cogs_total", sheet) else None
            if ct:
                specs.append(_Spec("cogs", f"Total COGS ({tp})", [ct[2]], abs(ct[1]), -1.0, U, adverse="high"))
        opex_comp = [it for it in mapping.get_all("opex_component") if it.sheet == sheet]
        if opex_comp:
            specs += _components(opex_comp, sheet, tp, -1.0, "opex", U)
        else:
            ot = _terminal_for(mapping.get("opex_total", sheet), tp) if mapping.get("opex_total", sheet) else None
            if ot:
                specs.append(_Spec("opex", f"Total opex ({tp})", [ot[2]], abs(ot[1]), -1.0, U, adverse="high"))

        if len(specs) >= 2:
            base_out = _eval_linear(specs, {})
            downside = _eval_linear(specs, {s.key: s.adverse_val() for s in specs})
            upside = _eval_linear(specs, {s.key: s.favorable_val() for s in specs})
            shap = _shapley_linear(specs)
            drivers = []
            for s in specs:
                ol = _eval_linear(specs, {s.key: s.low()})
                oh = _eval_linear(specs, {s.key: s.high()})
                drivers.append(_mk_driver(s, ol, oh, shap[s.key]))
            drivers.sort(key=lambda d: -abs(d.shapley))
            out.append(Tornado(
                output_key="terminal_ebitda", output_label=f"Terminal EBITDA ({tp})",
                output_unit=U, output_base=base_out, formula="linear_sum",
                formula_note="EBITDA = revenue − COGS − opex, decomposed to every mapped line item",
                drivers=drivers, downside=downside, upside=upside,
                caveats=["Every driver is one of the model's own line-item cells; "
                         "±20% default range, editable below."]))

    # ---- Output 2: present value of exit ----------------------------------
    pv = _valuation_tornado(wbd, structure, mapping, benchmarks, archetype, periods, U)
    if pv is not None:
        out.append(pv)
    return out


def _valuation_tornado(wbd, structure, mapping, benchmarks, archetype, periods, U) -> Optional[Tornado]:
    sheet = mapping.primary_sheet
    ebitda_mult = find_scalar_assumptions(wbd, structure, r"ebitda\s*multiple|multiple.*ebitda|x\s*ebitda", (0.5, 100))
    dr = find_scalar_assumptions(wbd, structure, r"discount\s*rate|hurdle|wacc", (0.01, 0.99))
    exit_year = find_scalar_assumptions(wbd, structure, r"exit\s*year|valuation\s*year", (1990, 2100))
    ebitda = mapping.get("ebitda_or_ebit", sheet)
    if not ebitda_mult or ebitda is None or not periods:
        return None
    yr = str(int(exit_year[0][1])) if exit_year else None
    exit_p = next((p for p in periods if p.startswith(yr)), None) if yr else None
    if exit_p is None:
        for p in reversed(periods):
            if ebitda.value_for(p) is not None:
                exit_p = p
                break
    if exit_p is None or ebitda.value_for(exit_p) is None or ebitda.value_for(exit_p) <= 0:
        return None
    e_exit = float(ebitda.value_for(exit_p))
    mref, mval, mlabel = ebitda_mult[0]
    rate_ref = rate_val = None
    if dr:
        pref = [t for t in dr if re.search(r"current|to\s*exit|today|present", t[2], re.I)]
        rate_ref, rate_val, _ = (pref[0] if pref else dr[0])
    cur = find_scalar_assumptions(wbd, structure, r"current\s*year|valuation\s*(date|year)|base\s*year", (1990, 2100))
    base_year = int(cur[0][1]) if cur else (int(re.match(r"(\d{4})", periods[0]).group(1)) if re.match(r"(\d{4})", periods[0]) else None)
    me = re.match(r"(\d{4})", exit_p)
    horizon = float(int(me.group(1)) - base_year) if (me and base_year is not None) else 0.0

    rng = (benchmarks or {}).get("ev_ebitda_multiple", {}).get(archetype) or \
          (benchmarks or {}).get("ev_ebitda_multiple", {}).get("default") or {}

    specs: list[_Spec] = [
        _Spec("multiple", f"Exit EBITDA multiple ({mlabel})", [mref], mval, 0.0, "x", adverse="low",
              low_val=float(rng.get("low", mval * 0.8)) if rng else None,
              high_val=float(rng.get("high", mval * 1.2)) if rng else None),
        _Spec("metric", f"Exit-year EBITDA ({exit_p})", [ebitda.ref_for(exit_p)], e_exit, 0.0, U, adverse="low"),
    ]
    if rate_val is not None:
        specs.append(_Spec("rate", "Discount rate", [rate_ref], rate_val, 0.0, "fraction",
                           low_pct=-0.25, high_pct=0.25, adverse="high"))
    sbk = {s.key: s for s in specs}

    def ev(ov):
        return _eval_pv(sbk, ov, horizon)

    base_out = ev({})
    downside = ev({s.key: s.adverse_val() for s in specs})
    upside = ev({s.key: s.favorable_val() for s in specs})
    shap = _shapley_exact(specs, ev)
    drivers = []
    for s in specs:
        ol = ev({s.key: s.low()})
        oh = ev({s.key: s.high()})
        drivers.append(_mk_driver(s, ol, oh, shap[s.key]))
    drivers.sort(key=lambda d: -abs(d.shapley))
    return Tornado(
        output_key="valuation_pv", output_label=f"Present value of {exit_p} exit",
        output_unit=U, output_base=base_out, formula="valuation_pv",
        formula_note=f"PV = multiple × EBITDA[{exit_p}] / (1+discount)^{horizon:g}",
        horizon=horizon, drivers=drivers, downside=downside, upside=upside,
        caveats=[f"Discounts {horizon:g} years at the model's own rate; multiple range is the "
                 f"{archetype.replace('_', ' ')} benchmark band.",
                 "Shapley bars attribute the base→downside move across the inputs, including interactions."])
