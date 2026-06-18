"""Sensitivity / tornado — perturb the model's OWN input cells within ranges.

Built off the Excel's actual cells (not a reduced-form re-projection): each
driver is one or more real model cells; each output is a quantity the tool owns
the closed form for, so the recompute is exact without recomputing the whole
workbook (which is impossible on models with unsupported functions, VBA, or
hardcoded outputs — those limits are reported separately).

Two output types ship:
  - valuation_pv : PV = multiple x metric_exit / (1+rate)^horizon
  - linear_sum   : output = sum(coef_i x driver_i)   (e.g. EBITDA = rev - cogs - opex)

The report embeds each tornado's drivers + formula so the analyst can drag the
ranges and watch it update live; this module computes the server-side default.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .mapping import LineItem, MappingResult
from .metrics import find_scalar_assumptions
from .schema import SensitivityDriver, Tornado
from .structure import StructureResult

DEFAULT_PCT = 0.20            # ±20% default range on each driver


@dataclass
class _DriverSpec:
    key: str
    label: str
    refs: list[str]
    base: float
    coef: float               # weight in a linear_sum output (+1 revenue, -1 cost)
    low_pct: float = -DEFAULT_PCT
    high_pct: float = DEFAULT_PCT
    unit: str = ""
    # explicit low/high override (e.g. benchmark bounds for a multiple)
    low_val: Optional[float] = None
    high_val: Optional[float] = None


def _terminal(it: Optional[LineItem]) -> Optional[tuple[str, float, str]]:
    """(period, value, ref) at the last period the row has a number."""
    if it is None:
        return None
    for p in reversed(it.periods):
        v = it.value_for(p)
        if v is not None:
            return p, float(v), it.ref_for(p)
    return None


def _mk_driver(spec: _DriverSpec) -> tuple[SensitivityDriver, float, float]:
    low = spec.low_val if spec.low_val is not None else spec.base * (1 + spec.low_pct)
    high = spec.high_val if spec.high_val is not None else spec.base * (1 + spec.high_pct)
    lp = (low - spec.base) / spec.base if abs(spec.base) > 1e-9 else spec.low_pct
    hp = (high - spec.base) / spec.base if abs(spec.base) > 1e-9 else spec.high_pct
    drv = SensitivityDriver(
        key=spec.key, label=spec.label, input_refs=spec.refs, base=spec.base,
        low=low, high=high, low_pct=round(lp, 4), high_pct=round(hp, 4),
        output_low=0.0, output_high=0.0, swing=0.0, unit=spec.unit)
    return drv, low, high


def _linear_output(specs: list[_DriverSpec], overrides: dict[str, float]) -> float:
    return sum(s.coef * overrides.get(s.key, s.base) for s in specs)


def build_sensitivities(
    wbd,
    structure: StructureResult,
    mapping: MappingResult,
    benchmarks: dict,
    archetype: str = "default",
    periods: Optional[list[str]] = None,
) -> list[Tornado]:
    out: list[Tornado] = []
    sheet = mapping.primary_sheet
    if sheet is None:
        return out
    if periods is None:
        ax = structure.primary_axis(sheet)
        periods = list(ax.periods) if ax else []

    def U() -> str:
        u = structure.units.get(sheet)
        return u.label if u else "model units"

    # ---- Tornado 1: terminal EBITDA = revenue - COGS - opex ----------------
    rev = _terminal(mapping.get("revenue_total", sheet))
    cogs = _terminal(mapping.get("cogs_total", sheet))
    opex = _terminal(mapping.get("opex_total", sheet))
    if rev and (cogs or opex):
        specs: list[_DriverSpec] = [
            _DriverSpec("revenue", f"Terminal revenue ({rev[0]})", [rev[2]], rev[1], +1.0, unit=U())]
        if cogs:
            specs.append(_DriverSpec("cogs", f"Terminal COGS ({cogs[0]})", [cogs[2]], abs(cogs[1]), -1.0, unit=U()))
        if opex:
            specs.append(_DriverSpec("opex", f"Terminal opex ({opex[0]})", [opex[2]], abs(opex[1]), -1.0, unit=U()))
        base_out = _linear_output(specs, {})
        drivers = []
        for s in specs:
            drv, low, high = _mk_driver(s)
            drv.output_low = _linear_output(specs, {s.key: low})
            drv.output_high = _linear_output(specs, {s.key: high})
            drv.swing = abs(drv.output_high - drv.output_low)
            drivers.append(drv)
        drivers.sort(key=lambda d: -d.swing)
        out.append(Tornado(
            output_key="terminal_ebitda", output_label=f"Terminal EBITDA ({rev[0]})",
            output_unit=U(), output_base=base_out, formula="linear_sum",
            formula_note="EBITDA = revenue − COGS − opex (terminal period), from the model's own rows",
            drivers=drivers,
            caveats=["Holds the cost/revenue structure fixed; each driver moves independently."]))

    # ---- Tornado 2: present value of exit ----------------------------------
    pv = build_valuation_tornado(wbd, structure, mapping, benchmarks, archetype, periods)
    if pv is not None:
        out.append(pv)
    return out


def build_valuation_tornado(wbd, structure, mapping, benchmarks, archetype, periods) -> Optional[Tornado]:
    from .metrics import find_scalar_assumptions, _years_between
    sheet = mapping.primary_sheet
    u = structure.units.get(sheet)
    units_label = u.label if u else "model units"

    ebitda_mult = find_scalar_assumptions(wbd, structure, r"ebitda\s*multiple|multiple.*ebitda|x\s*ebitda", (0.5, 100))
    dr = find_scalar_assumptions(wbd, structure, r"discount\s*rate|hurdle|wacc", (0.01, 0.99))
    exit_year = find_scalar_assumptions(wbd, structure, r"exit\s*year|valuation\s*year", (1990, 2100))
    ebitda = mapping.get("ebitda_or_ebit", sheet)
    if not ebitda_mult or not ebitda or not periods:
        return None

    # exit-year EBITDA
    yr = str(int(exit_year[0][1])) if exit_year else None
    exit_p = None
    if yr:
        exit_p = next((p for p in periods if p.startswith(yr)), None)
    if exit_p is None:
        for p in reversed(periods):
            if ebitda.value_for(p) is not None:
                exit_p = p
                break
    if exit_p is None:
        return None
    e_exit = ebitda.value_for(exit_p)
    if e_exit is None or e_exit <= 0:
        return None

    mref, mval, mlabel = ebitda_mult[0]
    rate_ref = rate_val = None
    if dr:
        # prefer the present-value-to-today rate ("current year to exit") over a
        # DCF-at-exit rate when the model carries both (Lithios uses 40%/yr today)
        pref = [t for t in dr if re.search(r"current|to\s*exit|today|present", t[2], re.I)]
        rate_ref, rate_val, _ = (pref[0] if pref else dr[0])
    # horizon
    base_year = None
    cur = find_scalar_assumptions(wbd, structure, r"current\s*year|valuation\s*(date|year)|base\s*year", (1990, 2100))
    if cur:
        base_year = int(cur[0][1])
    else:
        m0 = re.match(r"(\d{4})", periods[0])
        base_year = int(m0.group(1)) if m0 else None
    me = re.match(r"(\d{4})", exit_p)
    horizon = float(int(me.group(1)) - base_year) if (me and base_year is not None) else 0.0

    bm = (benchmarks or {}).get("ev_ebitda_multiple") or {}
    rng = bm.get(archetype) or bm.get("default") or {}

    def pv(mult, metric, rate, h):
        denom = (1 + rate) ** h if (rate is not None and h) else 1.0
        return mult * metric / denom

    rate_for_base = rate_val if rate_val is not None else 0.0
    base_pv = pv(mval, e_exit, rate_for_base, horizon)

    specs = []
    # multiple driver: benchmark low/high if available, else ±20%
    msp = _DriverSpec("multiple", f"Exit EBITDA multiple ({mlabel})", [mref], mval, 0.0, unit="x")
    if rng:
        msp.low_val, msp.high_val = float(rng.get("low", mval * 0.8)), float(rng.get("high", mval * 1.2))
    specs.append(("multiple", msp))
    esp = _DriverSpec("metric", f"Exit-year EBITDA ({exit_p})", [ebitda.ref_for(exit_p)], e_exit, 0.0, unit=units_label)
    specs.append(("metric", esp))
    if rate_val is not None:
        rsp = _DriverSpec("rate", "Discount rate", [rate_ref], rate_val, 0.0, low_pct=-0.25, high_pct=0.25, unit="fraction")
        specs.append(("rate", rsp))

    drivers = []
    for key, s in specs:
        drv, low, high = _mk_driver(s)
        def out_with(val):
            return pv(val if key == "multiple" else mval,
                      val if key == "metric" else e_exit,
                      val if key == "rate" else rate_for_base,
                      horizon)
        drv.output_low = out_with(low)
        drv.output_high = out_with(high)
        drv.swing = abs(drv.output_high - drv.output_low)
        drivers.append(drv)
    drivers.sort(key=lambda d: -d.swing)
    note = f"PV = multiple × EBITDA[{exit_p}] / (1+discount)^{horizon:g}"
    return Tornado(
        output_key="valuation_pv", output_label=f"Present value of {exit_p} exit",
        output_unit=units_label, output_base=base_pv, formula="valuation_pv",
        formula_note=note, drivers=drivers,
        caveats=[f"Discounts {horizon:g} years from {base_year} at the model's own rate.",
                 "Uses the model's typed exit multiple and discount-rate cells; "
                 "EBITDA at exit is the model's computed value, flexed as a driver."])
