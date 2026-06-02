"""
DD Financial Analysis — scenario-based P&L modeling engine.

Teams build conservative / base / best-case financial projections,
tune assumptions from DD findings, and compare implied valuations
against the Monte Carlo simulation output.
"""

import math
from typing import Optional


# ── Default assumption templates by stage ──────────────────────────
_STAGE_DEFAULTS = {
    "Pre-Seed": {
        "projection_years": 10,
        "revenue_y1_m": 0.0,
        "revenue_cagr_pct": 150.0,
        "gross_margin_start_pct": 30.0,
        "gross_margin_end_pct": 60.0,
        "opex_rd_pct_rev": 80.0,
        "opex_sm_pct_rev": 40.0,
        "opex_ga_pct_rev": 25.0,
        "opex_rd_end_pct_rev": 20.0,
        "opex_sm_end_pct_rev": 15.0,
        "opex_ga_end_pct_rev": 8.0,
        "da_pct_rev": 3.0,
        "capex_pct_rev": 5.0,
        "nwc_pct_rev": 10.0,
        "tax_rate_pct": 0.0,
        "terminal_growth_pct": 3.0,
        "discount_rate_pct": 35.0,
        "exit_multiple_type": "ev_revenue",
        "exit_multiple": 8.0,
    },
    "Seed": {
        "projection_years": 10,
        "revenue_y1_m": 0.5,
        "revenue_cagr_pct": 120.0,
        "gross_margin_start_pct": 35.0,
        "gross_margin_end_pct": 65.0,
        "opex_rd_pct_rev": 60.0,
        "opex_sm_pct_rev": 35.0,
        "opex_ga_pct_rev": 20.0,
        "opex_rd_end_pct_rev": 18.0,
        "opex_sm_end_pct_rev": 12.0,
        "opex_ga_end_pct_rev": 7.0,
        "da_pct_rev": 3.0,
        "capex_pct_rev": 5.0,
        "nwc_pct_rev": 10.0,
        "tax_rate_pct": 0.0,
        "terminal_growth_pct": 3.0,
        "discount_rate_pct": 30.0,
        "exit_multiple_type": "ev_revenue",
        "exit_multiple": 8.0,
    },
    "Series A": {
        "projection_years": 8,
        "revenue_y1_m": 3.0,
        "revenue_cagr_pct": 80.0,
        "gross_margin_start_pct": 45.0,
        "gross_margin_end_pct": 65.0,
        "opex_rd_pct_rev": 40.0,
        "opex_sm_pct_rev": 30.0,
        "opex_ga_pct_rev": 15.0,
        "opex_rd_end_pct_rev": 15.0,
        "opex_sm_end_pct_rev": 12.0,
        "opex_ga_end_pct_rev": 7.0,
        "da_pct_rev": 3.0,
        "capex_pct_rev": 5.0,
        "nwc_pct_rev": 10.0,
        "tax_rate_pct": 0.0,
        "terminal_growth_pct": 3.0,
        "discount_rate_pct": 25.0,
        "exit_multiple_type": "ev_ebitda",
        "exit_multiple": 15.0,
    },
    "Series B": {
        "projection_years": 7,
        "revenue_y1_m": 15.0,
        "revenue_cagr_pct": 60.0,
        "gross_margin_start_pct": 50.0,
        "gross_margin_end_pct": 65.0,
        "opex_rd_pct_rev": 25.0,
        "opex_sm_pct_rev": 25.0,
        "opex_ga_pct_rev": 12.0,
        "opex_rd_end_pct_rev": 12.0,
        "opex_sm_end_pct_rev": 10.0,
        "opex_ga_end_pct_rev": 6.0,
        "da_pct_rev": 3.0,
        "capex_pct_rev": 5.0,
        "nwc_pct_rev": 8.0,
        "tax_rate_pct": 10.0,
        "terminal_growth_pct": 3.0,
        "discount_rate_pct": 20.0,
        "exit_multiple_type": "ev_ebitda",
        "exit_multiple": 12.0,
    },
}

# Default fundraising plans by stage and scenario
_FUNDRAISING_DEFAULTS = {
    "Pre-Seed": {
        "conservative": [
            {"label": "Seed", "year": 2, "dilution_pct": 20.0, "needs_bridge": True, "bridge_dilution_pct": 10.0, "bridge_delay_years": 0.5},
            {"label": "Series A", "year": 4, "dilution_pct": 20.0, "needs_bridge": True, "bridge_dilution_pct": 8.0, "bridge_delay_years": 0.5},
            {"label": "Series B", "year": 6, "dilution_pct": 18.0, "needs_bridge": False, "bridge_dilution_pct": 0, "bridge_delay_years": 0},
        ],
        "base": [
            {"label": "Seed", "year": 2, "dilution_pct": 20.0, "needs_bridge": False, "bridge_dilution_pct": 0, "bridge_delay_years": 0},
            {"label": "Series A", "year": 4, "dilution_pct": 20.0, "needs_bridge": False, "bridge_dilution_pct": 0, "bridge_delay_years": 0},
            {"label": "Series B", "year": 6, "dilution_pct": 18.0, "needs_bridge": False, "bridge_dilution_pct": 0, "bridge_delay_years": 0},
        ],
        "best_case": [
            {"label": "Seed", "year": 1, "dilution_pct": 18.0, "needs_bridge": False, "bridge_dilution_pct": 0, "bridge_delay_years": 0},
            {"label": "Series A", "year": 3, "dilution_pct": 18.0, "needs_bridge": False, "bridge_dilution_pct": 0, "bridge_delay_years": 0},
        ],
    },
    "Seed": {
        "conservative": [
            {"label": "Series A", "year": 3, "dilution_pct": 22.0, "needs_bridge": True, "bridge_dilution_pct": 8.0, "bridge_delay_years": 0.5},
            {"label": "Series B", "year": 5, "dilution_pct": 18.0, "needs_bridge": True, "bridge_dilution_pct": 6.0, "bridge_delay_years": 0.5},
        ],
        "base": [
            {"label": "Series A", "year": 2, "dilution_pct": 20.0, "needs_bridge": False, "bridge_dilution_pct": 0, "bridge_delay_years": 0},
            {"label": "Series B", "year": 4, "dilution_pct": 18.0, "needs_bridge": False, "bridge_dilution_pct": 0, "bridge_delay_years": 0},
        ],
        "best_case": [
            {"label": "Series A", "year": 2, "dilution_pct": 18.0, "needs_bridge": False, "bridge_dilution_pct": 0, "bridge_delay_years": 0},
            {"label": "Series B", "year": 4, "dilution_pct": 15.0, "needs_bridge": False, "bridge_dilution_pct": 0, "bridge_delay_years": 0},
        ],
    },
    "Series A": {
        "conservative": [
            {"label": "Series B", "year": 3, "dilution_pct": 20.0, "needs_bridge": True, "bridge_dilution_pct": 8.0, "bridge_delay_years": 0.5},
            {"label": "Series C", "year": 5, "dilution_pct": 15.0, "needs_bridge": False, "bridge_dilution_pct": 0, "bridge_delay_years": 0},
        ],
        "base": [
            {"label": "Series B", "year": 2, "dilution_pct": 18.0, "needs_bridge": False, "bridge_dilution_pct": 0, "bridge_delay_years": 0},
            {"label": "Series C", "year": 4, "dilution_pct": 15.0, "needs_bridge": False, "bridge_dilution_pct": 0, "bridge_delay_years": 0},
        ],
        "best_case": [
            {"label": "Series B", "year": 2, "dilution_pct": 15.0, "needs_bridge": False, "bridge_dilution_pct": 0, "bridge_delay_years": 0},
        ],
    },
    "Series B": {
        "conservative": [
            {"label": "Series C", "year": 2, "dilution_pct": 15.0, "needs_bridge": True, "bridge_dilution_pct": 6.0, "bridge_delay_years": 0.5},
            {"label": "Series D", "year": 4, "dilution_pct": 12.0, "needs_bridge": False, "bridge_dilution_pct": 0, "bridge_delay_years": 0},
        ],
        "base": [
            {"label": "Series C", "year": 2, "dilution_pct": 12.0, "needs_bridge": False, "bridge_dilution_pct": 0, "bridge_delay_years": 0},
        ],
        "best_case": [
            {"label": "Series C", "year": 2, "dilution_pct": 10.0, "needs_bridge": False, "bridge_dilution_pct": 0, "bridge_delay_years": 0},
        ],
    },
}

# Scenario multipliers applied to the base-stage defaults
_SCENARIO_ADJUSTMENTS = {
    "conservative": {
        "revenue_y1_m_mult": 0.7,
        "revenue_cagr_pct_add": -30.0,
        "gross_margin_end_pct_add": -10.0,
        "exit_multiple_mult": 0.75,
        "discount_rate_pct_add": 5.0,
        "time_to_launch_years_add": 2,  # Conservative: expect longer to get to revenue
    },
    "base": {
        "time_to_launch_years_add": 1,  # Base: modest certification / sales cycle delay
    },
    "best_case": {
        "revenue_y1_m_mult": 1.3,
        "revenue_cagr_pct_add": 20.0,
        "gross_margin_end_pct_add": 5.0,
        "exit_multiple_mult": 1.25,
        "discount_rate_pct_add": -3.0,
        "time_to_launch_years_add": 0,  # Best case: on track, no delay
    },
}


def get_default_assumptions(entry_stage: str, scenario: str = "base") -> dict:
    """Return a full assumption set for the given stage and scenario."""
    stage_key = entry_stage if entry_stage in _STAGE_DEFAULTS else "Seed"
    base = dict(_STAGE_DEFAULTS[stage_key])
    adj = _SCENARIO_ADJUSTMENTS.get(scenario, {})

    if "revenue_y1_m_mult" in adj:
        base["revenue_y1_m"] *= adj["revenue_y1_m_mult"]
    if "revenue_cagr_pct_add" in adj:
        base["revenue_cagr_pct"] = max(10.0, base["revenue_cagr_pct"] + adj["revenue_cagr_pct_add"])
    if "gross_margin_end_pct_add" in adj:
        base["gross_margin_end_pct"] += adj["gross_margin_end_pct_add"]
    if "exit_multiple_mult" in adj:
        base["exit_multiple"] *= adj["exit_multiple_mult"]
    if "discount_rate_pct_add" in adj:
        base["discount_rate_pct"] += adj["discount_rate_pct_add"]
    if "time_to_launch_years_add" in adj:
        base["time_to_launch_years"] = base.get("time_to_launch_years", 0) + adj["time_to_launch_years_add"]

    base["scenario"] = scenario

    # Attach default fundraising plan
    stage_fp = _FUNDRAISING_DEFAULTS.get(stage_key, _FUNDRAISING_DEFAULTS["Seed"])
    base["fundraising_plan"] = stage_fp.get(scenario, stage_fp.get("base", []))

    return base


def _lerp(start: float, end: float, t: float) -> float:
    """Linear interpolation, t in [0, 1]."""
    return start + (end - start) * min(max(t, 0.0), 1.0)


_NA_TOKENS = {"", "n/a", "na", "none", "null", "-"}


def _is_na(value) -> bool:
    """True if a value represents 'not applicable / unknown' (None, '', 'N/A', etc.)."""
    if value is None:
        return True
    if isinstance(value, str) and value.strip().lower() in _NA_TOKENS:
        return True
    return False


def _num(assumptions: dict, key: str, default: float) -> float:
    """Read a numeric assumption, treating N/A (None/''/'N/A') or unparseable as the default."""
    v = assumptions.get(key, default)
    if _is_na(v):
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def build_pnl_projection(assumptions: dict, custom_revenues: Optional[list] = None) -> dict:
    """
    Build a full P&L projection from an assumption set.

    Supports a pre-commercial launch delay: `time_to_launch_years` prepends
    zero-revenue years where the company burns cash on R&D/G&A before
    commercial sales begin. The commercial revenue model then starts after
    the launch period, and the entire P&L (including DCF) spans the full
    timeline.

    Returns a dict with year-by-year arrays for every line item plus
    summary valuation metrics.
    """
    n_commercial = int(_num(assumptions, "projection_years", 10))
    pre_launch = int(_num(assumptions, "time_to_launch_years", 0))
    rev_y1 = _num(assumptions, "revenue_y1_m", 1.0)
    cagr = _num(assumptions, "revenue_cagr_pct", 100.0) / 100.0

    gm_start = _num(assumptions, "gross_margin_start_pct", 40.0) / 100.0
    gm_end = _num(assumptions, "gross_margin_end_pct", 65.0) / 100.0

    # Operating expenses: support a collapsed single "opex_pct_rev" (total OpEx as
    # % of revenue, start → end) OR the legacy R&D / S&M / G&A breakdown. When the
    # collapsed field is provided, it is mapped onto R&D and S&M/G&A are zeroed so
    # the rest of the P&L loop (and the `total_opex` line) stays correct.
    if not _is_na(assumptions.get("opex_pct_rev")):
        rd_start = _num(assumptions, "opex_pct_rev", 60.0) / 100.0
        rd_end = _num(assumptions, "opex_end_pct_rev", 25.0) / 100.0
        sm_start = sm_end = ga_start = ga_end = 0.0
    else:
        rd_start = _num(assumptions, "opex_rd_pct_rev", 50.0) / 100.0
        sm_start = _num(assumptions, "opex_sm_pct_rev", 30.0) / 100.0
        ga_start = _num(assumptions, "opex_ga_pct_rev", 15.0) / 100.0
        rd_end = _num(assumptions, "opex_rd_end_pct_rev", 15.0) / 100.0
        sm_end = _num(assumptions, "opex_sm_end_pct_rev", 12.0) / 100.0
        ga_end = _num(assumptions, "opex_ga_end_pct_rev", 7.0) / 100.0

    # Absolute operating-spend plan ($M/yr), independent of realized revenue. When
    # opex_commercial_m is provided, OpEx follows this plan (a modest year-over-year
    # ramp) instead of "% of revenue" — so a low-revenue case keeps its committed spend
    # rather than auto-shrinking, which is what made cash-to-breakeven behave backwards
    # (a weaker case looked CHEAPER). Falls back to the %-of-revenue path when absent.
    use_abs_opex = not _is_na(assumptions.get("opex_commercial_m"))
    opex_commercial_m = _num(assumptions, "opex_commercial_m", 0.0)
    opex_prelaunch_m = _num(assumptions, "opex_prelaunch_m", 0.0)
    opex_ramp = _num(assumptions, "opex_ramp_pct", 15.0) / 100.0   # modest annual growth

    da_pct = _num(assumptions, "da_pct_rev", 3.0) / 100.0
    capex_pct = _num(assumptions, "capex_pct_rev", 5.0) / 100.0
    nwc_pct = _num(assumptions, "nwc_pct_rev", 10.0) / 100.0
    tax_rate = _num(assumptions, "tax_rate_pct", 0.0) / 100.0
    terminal_g = _num(assumptions, "terminal_growth_pct", 3.0) / 100.0
    wacc = _num(assumptions, "discount_rate_pct", 25.0) / 100.0
    # Exit multiple may be N/A — in that case the exit valuation falls back to a
    # Gordon-growth terminal value so the DCF (and an implied MOIC) still resolve.
    exit_mult_na = _is_na(assumptions.get("exit_multiple"))
    exit_mult = _num(assumptions, "exit_multiple", 10.0)
    exit_type = assumptions.get("exit_multiple_type", "ev_revenue") or "ev_revenue"

    # Total projection = pre-launch + commercial years
    n_total = pre_launch + n_commercial
    years = list(range(1, n_total + 1))

    # ── Build commercial revenue trajectory first ──────────────────
    commercial_revenue = []
    for i in range(n_commercial):
        if custom_revenues and i < len(custom_revenues) and custom_revenues[i] is not None:
            commercial_revenue.append(float(custom_revenues[i]))
        elif i == 0:
            commercial_revenue.append(rev_y1)
        else:
            decay_factor = max(0.0, 1.0 - (i / n_commercial))
            yr_growth = terminal_g + (cagr - terminal_g) * decay_factor
            commercial_revenue.append(commercial_revenue[-1] * (1.0 + yr_growth))

    # ── Full revenue: pre-launch zeros + commercial ────────────────
    revenue = [0.0] * pre_launch + commercial_revenue

    # ── Pre-launch burn rate (absolute $M/yr for R&D + G&A) ───────
    # During pre-launch, the company has no revenue but still spends on R&D + G&A.
    # With an absolute plan, use it directly (revenue-independent). Otherwise fall back
    # to the legacy heuristic that references Year-1 commercial revenue.
    if use_abs_opex:
        prelaunch_rd_burn = opex_prelaunch_m
        prelaunch_ga_burn = 0.0
    else:
        prelaunch_rd_burn = rev_y1 * rd_start if rev_y1 > 0 else 0.5
        prelaunch_ga_burn = rev_y1 * ga_start * 0.5 if rev_y1 > 0 else 0.15
    prelaunch_capex_burn = rev_y1 * capex_pct if rev_y1 > 0 else 0.1

    # Build P&L arrays
    cogs, gross_profit, gross_margin_pct_arr = [], [], []
    opex_rd, opex_sm, opex_ga, total_opex = [], [], [], []
    ebitda, ebitda_margin_pct = [], []
    da, ebit, interest, ebt, taxes, net_income = [], [], [], [], [], []
    capex_arr, delta_nwc, fcf = [], [], []

    prev_nwc = 0.0
    for i, rev in enumerate(revenue):
        is_prelaunch = i < pre_launch
        # commercial_idx: position within the commercial phase
        commercial_idx = max(0, i - pre_launch)
        # t: progress through the commercial phase [0, 1]
        t = commercial_idx / max(n_commercial - 1, 1) if not is_prelaunch else 0.0

        if is_prelaunch:
            # Pre-launch: no revenue, fixed burn on R&D + G&A
            cogs_val = 0.0
            gp = 0.0
            rd = prelaunch_rd_burn
            sm = 0.0  # No sales activity yet
            ga = prelaunch_ga_burn
            tot_opex = rd + sm + ga
            ebitda_val = -tot_opex
            da_val = 0.0
            ebit_val = -tot_opex
            interest_val = 0.0
            ebt_val = ebit_val
            tax_val = 0.0
            ni = ebt_val
            capex_val = prelaunch_capex_burn
            nwc_val = 0.0
            dnwc = 0.0
            fcf_val = ni - capex_val
            gm_val = 0.0
        else:
            gm = _lerp(gm_start, gm_end, t)
            cogs_val = rev * (1.0 - gm)
            gp = rev * gm

            if use_abs_opex:
                # Absolute plan: commercial-year-1 spend, ramping modestly each year,
                # independent of revenue. Booked as R&D so total_opex stays correct.
                rd = opex_commercial_m * ((1.0 + opex_ramp) ** commercial_idx)
                sm = 0.0
                ga = 0.0
            else:
                rd = rev * _lerp(rd_start, rd_end, t)
                sm = rev * _lerp(sm_start, sm_end, t)
                ga = rev * _lerp(ga_start, ga_end, t)
            tot_opex = rd + sm + ga

            ebitda_val = gp - tot_opex
            da_val = rev * da_pct
            ebit_val = ebitda_val - da_val
            interest_val = 0.0
            ebt_val = ebit_val - interest_val
            tax_val = max(0.0, ebt_val * tax_rate) if ebt_val > 0 else 0.0
            ni = ebt_val - tax_val

            capex_val = rev * capex_pct
            nwc_val = rev * nwc_pct
            dnwc = nwc_val - prev_nwc
            prev_nwc = nwc_val
            fcf_val = ni + da_val - capex_val - dnwc
            gm_val = gm * 100

        cogs.append(round(cogs_val, 3))
        gross_profit.append(round(gp, 3))
        gross_margin_pct_arr.append(round(gm_val, 1))
        opex_rd.append(round(rd, 3))
        opex_sm.append(round(sm, 3))
        opex_ga.append(round(ga, 3))
        total_opex.append(round(tot_opex, 3))
        ebitda.append(round(ebitda_val, 3))
        ebitda_margin_pct.append(round((ebitda_val / rev * 100) if rev > 0 else (0 if not is_prelaunch else -100), 1))
        da.append(round(da_val, 3))
        ebit.append(round(ebit_val, 3))
        interest.append(0.0)
        ebt.append(round(ebt_val, 3))
        taxes.append(round(tax_val, 3))
        net_income.append(round(ni, 3))
        capex_arr.append(round(capex_val, 3))
        delta_nwc.append(round(dnwc, 3))
        fcf.append(round(fcf_val, 3))

    # ── Valuation ──────────────────────────────────────────────────
    # Terminal value based on final commercial year metrics
    terminal_rev = revenue[-1]
    terminal_ebitda = ebitda[-1]

    if exit_mult_na:
        # Exit multiple unknown → Gordon-growth terminal value off final FCF so the
        # DCF and an implied exit value still resolve. Guard wacc > g.
        spread = max(wacc - terminal_g, 0.02)
        terminal_value = max(fcf[-1], 0.0) * (1.0 + terminal_g) / spread
    elif exit_type == "ev_ebitda":
        # Floor at 0 so an absolute opex plan can't drive a negative exit valuation
        # for a very-low-revenue case (fixed spend > gross profit at exit).
        terminal_value = max(terminal_ebitda, 0.0) * exit_mult
    else:
        terminal_value = terminal_rev * exit_mult

    # DCF of projected FCFs (discounting from year 1 including pre-launch)
    pv_fcf = []
    cumulative_discount = 1.0
    for i, f in enumerate(fcf):
        cumulative_discount *= (1.0 + wacc)
        pv_fcf.append(f / cumulative_discount)

    pv_fcf_total = sum(pv_fcf)
    pv_terminal = terminal_value / ((1.0 + wacc) ** n_total)

    enterprise_value = pv_fcf_total + pv_terminal

    # Exit-year multiples approach (more common for VC)
    exit_year_ev = terminal_value
    exit_year_idx = n_total - 1

    # Enterprise value if the investor exits in EACH year (so an explicit Exit
    # Year choice actually re-values the company at that year, not just the last).
    _spread = max(wacc - terminal_g, 0.02)
    ev_by_year = []
    for i in range(n_total):
        if exit_mult_na:
            ev_by_year.append(round(max(fcf[i], 0.0) * (1.0 + terminal_g) / _spread, 3))
        elif exit_type == "ev_ebitda":
            ev_by_year.append(round(max(ebitda[i], 0.0) * exit_mult, 3))
        else:
            ev_by_year.append(round(revenue[i] * exit_mult, 3))

    # ── Breakeven & funding need (commercial-year indexed) ─────────
    cumulative_fcf, run_sum = [], 0.0
    for f in fcf:
        run_sum += f
        cumulative_fcf.append(round(run_sum, 3))
    peak_cumulative_burn = round(abs(min(0.0, min(cumulative_fcf))), 3)

    def _first_year(arr, predicate):
        for i, v in enumerate(arr):
            if i >= pre_launch and predicate(v):
                return (i - pre_launch) + 1  # 1-indexed commercial year
        return None

    ebitda_breakeven_year = _first_year(ebitda, lambda v: v >= 0)
    fcf_breakeven_year = _first_year(fcf, lambda v: v >= 0)

    return {
        "years": years,
        "n_years": n_total,
        "n_commercial_years": n_commercial,
        "pre_launch_years": pre_launch,
        "revenue": [round(r, 3) for r in revenue],
        "cogs": cogs,
        "gross_profit": gross_profit,
        "gross_margin_pct": gross_margin_pct_arr,
        "opex_rd": opex_rd,
        "opex_sm": opex_sm,
        "opex_ga": opex_ga,
        "total_opex": total_opex,
        "ebitda": ebitda,
        "ebitda_margin_pct": ebitda_margin_pct,
        "da": da,
        "ebit": ebit,
        "interest": interest,
        "ebt": ebt,
        "taxes": taxes,
        "net_income": net_income,
        "capex": capex_arr,
        "delta_nwc": delta_nwc,
        "fcf": fcf,
        "pv_fcf": [round(p, 3) for p in pv_fcf],
        "pv_fcf_total": round(pv_fcf_total, 3),
        "terminal_value": round(terminal_value, 3),
        "pv_terminal": round(pv_terminal, 3),
        "enterprise_value": round(enterprise_value, 3),
        "exit_year_ev": round(exit_year_ev, 3),
        "ev_by_year": ev_by_year,
        "exit_multiple_na": exit_mult_na,
        "cumulative_fcf": cumulative_fcf,
        "peak_cumulative_burn": peak_cumulative_burn,
        "ebitda_breakeven_year": ebitda_breakeven_year,
        "fcf_breakeven_year": fcf_breakeven_year,
    }


def compute_deal_returns(
    pnl: dict,
    check_size_m: float,
    pre_money_m: float,
    round_size_m: Optional[float] = None,
    exit_year: Optional[int] = None,
    dilution_per_round_pct: float = 20.0,
    future_rounds: int = 2,
    fundraising_plan: Optional[list] = None,
    prob_success: Optional[float] = None,
    recovery_moic: float = 0.0,
) -> dict:
    """
    Given a P&L projection, compute implied MOIC / IRR for the investor.

    `prob_success` (0-1, or None = 100%) weights the success-case return against a
    failure outcome worth `recovery_moic`× the invested capital, producing a
    probability-adjusted ("expected") MOIC / IRR alongside the conditional figures.

    Uses exit-year enterprise value, applies estimated dilution from
    future funding rounds (including potential bridge rounds), and
    calculates investor's share of proceeds.

    fundraising_plan: optional list of dicts, each describing a future round:
        {
            "label": "Series A",           # display label
            "year": 3,                     # year from investment (absolute)
            "dilution_pct": 20.0,          # % of company sold to new investors
            "needs_bridge": false,         # whether a bridge is expected first
            "bridge_dilution_pct": 8.0,    # additional dilution from bridge note
            "bridge_delay_years": 0.5,     # extra time added by bridge fundraise
        }
    If fundraising_plan is provided, it replaces the simple
    dilution_per_round_pct × future_rounds model.
    """
    post_money = pre_money_m + (round_size_m or check_size_m)
    ownership = check_size_m / post_money

    # ── Dilution walkthrough ──────────────────────────────────────
    dilution_schedule = []
    total_bridge_delay = 0.0

    if fundraising_plan and len(fundraising_plan) > 0:
        # Detailed round-by-round dilution
        running_ownership = ownership
        for rnd in fundraising_plan:
            label = rnd.get("label", "Round")
            rnd_dilution = float(rnd.get("dilution_pct", 20.0)) / 100.0
            needs_bridge = rnd.get("needs_bridge", False)
            bridge_dilution = float(rnd.get("bridge_dilution_pct", 8.0)) / 100.0
            bridge_delay = float(rnd.get("bridge_delay_years", 0.5))

            pre_round_ownership = running_ownership

            # Bridge round happens first (if applicable)
            if needs_bridge:
                running_ownership *= (1.0 - bridge_dilution)
                total_bridge_delay += bridge_delay
                dilution_schedule.append({
                    "label": f"{label} Bridge",
                    "type": "bridge",
                    "dilution_pct": round(bridge_dilution * 100, 1),
                    "delay_years": round(bridge_delay, 1),
                    "ownership_before_pct": round(pre_round_ownership * 100, 2),
                    "ownership_after_pct": round(running_ownership * 100, 2),
                })
                pre_round_ownership = running_ownership

            # Priced round
            running_ownership *= (1.0 - rnd_dilution)
            dilution_schedule.append({
                "label": label,
                "type": "priced",
                "year": rnd.get("year"),
                "dilution_pct": round(rnd_dilution * 100, 1),
                "delay_years": 0,
                "ownership_before_pct": round(pre_round_ownership * 100, 2),
                "ownership_after_pct": round(running_ownership * 100, 2),
            })

        exit_ownership = running_ownership
        dilution_factor = exit_ownership / ownership if ownership > 0 else 0.0
    else:
        # Simple model fallback
        dilution_factor = (1.0 - dilution_per_round_pct / 100.0) ** future_rounds
        exit_ownership = ownership * dilution_factor

    exit_idx = (int(exit_year) - 1) if exit_year else (pnl["n_years"] - 1)
    exit_idx = max(0, min(exit_idx, pnl["n_years"] - 1))

    # Value the company at the CHOSEN exit year (falls back to terminal-year EV).
    ev_arr = pnl.get("ev_by_year")
    exit_ev = ev_arr[exit_idx] if (ev_arr and 0 <= exit_idx < len(ev_arr)) else pnl["exit_year_ev"]
    investor_proceeds = exit_ev * exit_ownership

    moic = investor_proceeds / check_size_m if check_size_m > 0 else 0.0
    # Hold period includes bridge delays
    hold_years = (exit_idx + 1) + total_bridge_delay
    irr = (moic ** (1.0 / hold_years) - 1.0) * 100.0 if moic > 0 and hold_years > 0 else 0.0

    # ── Probability-of-success adjustment ─────────────────────────
    # Expected MOIC = p × success MOIC + (1 − p) × recovery. None → 100% (no haircut).
    p = 1.0 if _is_na(prob_success) else max(0.0, min(1.0, float(prob_success)))
    rec = max(0.0, float(recovery_moic or 0.0))
    expected_moic = p * moic + (1.0 - p) * rec
    expected_irr = (
        (expected_moic ** (1.0 / hold_years) - 1.0) * 100.0
        if expected_moic > 0 and hold_years > 0 else 0.0
    )

    return {
        "check_size_m": round(check_size_m, 3),
        "pre_money_m": round(pre_money_m, 3),
        "post_money_m": round(post_money, 3),
        "entry_ownership_pct": round(ownership * 100, 2),
        "dilution_per_round_pct": round(dilution_per_round_pct, 1),
        "future_rounds": future_rounds,
        "dilution_factor": round(dilution_factor, 4),
        "exit_ownership_pct": round(exit_ownership * 100, 2),
        "exit_year": exit_idx + 1,
        "hold_years_total": round(hold_years, 1),
        "bridge_delay_years": round(total_bridge_delay, 1),
        "exit_ev_m": round(exit_ev, 3),
        "investor_proceeds_m": round(investor_proceeds, 3),
        "moic": round(moic, 2),
        "irr_pct": round(irr, 1),
        "prob_success_pct": round(p * 100, 1),
        "recovery_moic": round(rec, 2),
        "expected_moic": round(expected_moic, 2),
        "expected_irr_pct": round(expected_irr, 1),
        "dcf_enterprise_value_m": round(pnl["enterprise_value"], 3),
        "exit_multiple_na": pnl.get("exit_multiple_na", False),
        "peak_cumulative_burn_m": pnl.get("peak_cumulative_burn"),
        "ebitda_breakeven_year": pnl.get("ebitda_breakeven_year"),
        "fcf_breakeven_year": pnl.get("fcf_breakeven_year"),
        "dilution_schedule": dilution_schedule,
    }


def run_scenario_analysis(
    scenarios: dict,
    check_size_m: float,
    pre_money_m: float,
    round_size_m: Optional[float] = None,
    exit_year: Optional[int] = None,
    dilution_per_round_pct: float = 20.0,
    future_rounds: int = 2,
    custom_revenues: Optional[dict] = None,
    fundraising_plans: Optional[dict] = None,
) -> dict:
    """
    Run full scenario analysis across multiple assumption sets.

    scenarios: dict of {scenario_name: assumptions_dict}
    custom_revenues: optional dict of {scenario_name: [rev_y1, rev_y2, ...]}
    fundraising_plans: optional dict of {scenario_name: [round_dicts]}
        Each round dict: {label, year, dilution_pct, needs_bridge,
                          bridge_dilution_pct, bridge_delay_years}

    Returns consolidated results with P&L, returns, and comparison metrics.
    """
    results = {}
    for name, assumptions in scenarios.items():
        rev_override = (custom_revenues or {}).get(name)
        pnl = build_pnl_projection(assumptions, custom_revenues=rev_override)

        # Extract fundraising plan: explicit per-scenario plan > assumptions > global
        fp = None
        if fundraising_plans and name in fundraising_plans:
            fp = fundraising_plans[name]
        elif "fundraising_plan" in assumptions:
            fp = assumptions["fundraising_plan"]

        returns = compute_deal_returns(
            pnl,
            check_size_m=check_size_m,
            pre_money_m=pre_money_m,
            round_size_m=round_size_m,
            exit_year=exit_year,
            dilution_per_round_pct=dilution_per_round_pct,
            future_rounds=future_rounds,
            fundraising_plan=fp,
        )
        results[name] = {
            "assumptions": assumptions,
            "pnl": pnl,
            "returns": returns,
        }

    # Build comparison summary
    comparison = {
        "scenarios": list(results.keys()),
        "exit_ev": {k: v["returns"]["exit_ev_m"] for k, v in results.items()},
        "moic": {k: v["returns"]["moic"] for k, v in results.items()},
        "irr": {k: v["returns"]["irr_pct"] for k, v in results.items()},
        "exit_year_revenue": {
            k: v["pnl"]["revenue"][-1] for k, v in results.items()
        },
        "exit_year_ebitda": {
            k: v["pnl"]["ebitda"][-1] for k, v in results.items()
        },
        "exit_year_ebitda_margin": {
            k: v["pnl"]["ebitda_margin_pct"][-1] for k, v in results.items()
        },
        "dcf_ev": {
            k: v["pnl"]["enterprise_value"] for k, v in results.items()
        },
        "hold_years": {
            k: v["returns"]["hold_years_total"] for k, v in results.items()
        },
        "bridge_delay": {
            k: v["returns"]["bridge_delay_years"] for k, v in results.items()
        },
        "exit_ownership": {
            k: v["returns"]["exit_ownership_pct"] for k, v in results.items()
        },
    }

    return {
        "scenarios": results,
        "comparison": comparison,
    }


# ════════════════════════════════════════════════════════════════════
# DD interactive underwriting engine (v2): N/A-tolerant, deterministic,
# with a Conservative→Best sensitivity / impact decomposition.
# ════════════════════════════════════════════════════════════════════

# Analyst-tunable drivers eligible for the sensitivity / impact decomposition.
# Only fields that actually differ between the Conservative and Best cases (and
# are not N/A in either) get decomposed.
_DD_DRIVERS = [
    ("revenue_y1_m", "Year-1 Revenue"),
    ("revenue_cagr_pct", "Revenue CAGR"),
    ("time_to_launch_years", "Time to Launch"),
    ("gross_margin_end_pct", "Gross Margin"),
    ("opex_commercial_m", "Operating Spend (commercial)"),
    ("opex_prelaunch_m", "Operating Spend (pre-launch)"),
    ("capex_pct_rev", "Capex Intensity"),
    ("exit_multiple", "Exit Multiple"),
    ("discount_rate_pct", "Discount Rate"),
    ("terminal_growth_pct", "Terminal Growth"),
    ("prob_success_pct", "Probability of Success"),
    ("dilution_per_round_pct", "Dilution per Round"),
    ("exit_year", "Exit Year"),
]


def _dd_metric_value(returns: dict, metric: str) -> float:
    return {
        "moic": returns.get("moic", 0.0),
        "expected_moic": returns.get("expected_moic", 0.0),
        "irr": returns.get("irr_pct", 0.0),
        "expected_irr": returns.get("expected_irr_pct", 0.0),
        "dcf_value": returns.get("dcf_enterprise_value_m", 0.0),
        "cash_breakeven": returns.get("peak_cumulative_burn_m", 0.0) or 0.0,
    }.get(metric, returns.get("moic", 0.0))


def _dd_run_one(assumptions: dict, deal_params: dict, recovery_moic: float) -> dict:
    """Run the deterministic model for one assumption set; returns the returns dict."""
    pnl = build_pnl_projection(assumptions, custom_revenues=assumptions.get("custom_revenues"))
    ps = assumptions.get("prob_success_pct")
    return compute_deal_returns(
        pnl,
        check_size_m=float(deal_params.get("check_size_m", 2.0)),
        pre_money_m=float(deal_params.get("pre_money_m", 15.0)),
        round_size_m=deal_params.get("round_size_m"),
        exit_year=deal_params.get("exit_year") or assumptions.get("exit_year"),
        dilution_per_round_pct=_num(assumptions, "dilution_per_round_pct", 20.0),
        future_rounds=int(_num(assumptions, "future_rounds", 2)),
        fundraising_plan=assumptions.get("fundraising_plan"),
        prob_success=(None if _is_na(ps) else _num(assumptions, "prob_success_pct", 100.0) / 100.0),
        recovery_moic=_num(assumptions, "recovery_moic", recovery_moic),
    ), pnl


def _dd_shapley(active_keys: list, base: dict, target: dict, deal_params: dict,
                recovery_moic: float, metric: str) -> dict:
    """
    Shapley-value attribution of the Base→target move across `active_keys`.

    Coalition value f(S) = the metric when the levers in S are set to their `target`
    value and the rest stay at Base. Each lever's Shapley value is its marginal
    contribution f(S∪{i}) − f(S) averaged over every ordering of the levers — the
    unique attribution where Σ φ = f(all) − f(none) (efficiency), so the bars
    reconcile to the move with no leftover "interactions" bucket.
    """
    from math import factorial
    from itertools import combinations
    n = len(active_keys)
    if n == 0:
        return {}
    cache = {}

    def f(S):
        fs = frozenset(S)
        if fs in cache:
            return cache[fs]
        a = dict(base)
        for k in fs:
            a[k] = target[k]
        ret, _ = _dd_run_one(a, deal_params, recovery_moic)
        val = _dd_metric_value(ret, metric)
        cache[fs] = val
        return val

    phi = {k: 0.0 for k in active_keys}
    for k in active_keys:
        rest = [x for x in active_keys if x != k]
        for r in range(len(rest) + 1):
            w = factorial(r) * factorial(n - r - 1) / factorial(n)
            for S in combinations(rest, r):
                phi[k] += w * (f(frozenset(S) | {k}) - f(frozenset(S)))
    return phi


def _dd_decompose(scenarios: dict, deal_params: dict, results: dict,
                  recovery_moic: float, metric: str) -> dict:
    """
    First-order Conservative→Best decomposition (deterministic, exact).

    For each tunable driver that differs between the Conservative and Best cases,
    measure the metric swing when ONLY that driver moves from its Conservative to
    its Best value (all others held at Base). The 'sensitivity' is the size of that
    swing; the 'impact' is its share of the total movement, with the leftover
    (driver interactions) reported explicitly rather than smeared into the bars.
    """
    cons = scenarios.get("conservative")
    best = scenarios.get("best_case")
    base = dict(scenarios.get("base") or {})
    if not cons or not best or not base:
        return {"available": False, "reason": "Need Conservative, Base and Best cases."}

    metric_cons = _dd_metric_value(results["conservative"]["returns"], metric)
    metric_base = _dd_metric_value(results["base"]["returns"], metric)
    metric_best = _dd_metric_value(results["best_case"]["returns"], metric)
    total_swing = metric_best - metric_cons

    drivers = []
    first_order_sum = 0.0
    sum_down = 0.0   # Σ Base→Conservative contributions
    sum_up = 0.0     # Σ Base→Best contributions
    for key, label in _DD_DRIVERS:
        cv, bv = cons.get(key), best.get(key)
        if _is_na(cv) or _is_na(bv):
            continue  # N/A in a bound → not a tunable driver here
        if abs(_num(cons, key, 0.0) - _num(best, key, 0.0)) < 1e-9:
            continue  # no variation between cases
        # Hold everything at Base; move ONLY this lever to its conservative / best value.
        low_a = dict(base); low_a[key] = cv
        high_a = dict(base); high_a[key] = bv
        low_ret, _ = _dd_run_one(low_a, deal_params, recovery_moic)
        high_ret, _ = _dd_run_one(high_a, deal_params, recovery_moic)
        v_low = _dd_metric_value(low_ret, metric)
        v_high = _dd_metric_value(high_ret, metric)
        swing = v_high - v_low                 # Conservative→Best span (tornado)
        contrib_down = v_low - metric_base      # Base→Conservative bridge step
        contrib_up = v_high - metric_base       # Base→Best bridge step
        first_order_sum += swing
        sum_down += contrib_down
        sum_up += contrib_up
        drivers.append({
            "key": key, "label": label,
            "value_conservative": cv, "value_base": base.get(key), "value_best": bv,
            "metric_low": round(v_low, 3), "metric_high": round(v_high, 3),
            "swing": round(swing, 3), "abs_swing": round(abs(swing), 3),
            "contrib_down": round(contrib_down, 3), "contrib_up": round(contrib_up, 3),
        })

    interaction = total_swing - first_order_sum
    denom = sum(d["abs_swing"] for d in drivers) + abs(interaction)
    for d in drivers:
        d["impact_pct"] = round(100.0 * d["abs_swing"] / denom, 1) if denom > 1e-9 else 0.0
    drivers.sort(key=lambda d: d["abs_swing"], reverse=True)

    return {
        "available": len(drivers) > 0,
        "metric": metric,
        "drivers": drivers,
        "metric_conservative": round(metric_cons, 3),
        "metric_base": round(metric_base, 3),
        "metric_best": round(metric_best, 3),
        "total_swing": round(total_swing, 3),
        "first_order_sum": round(first_order_sum, 3),
        "interaction": round(interaction, 3),
        "interaction_pct": round(100.0 * abs(interaction) / denom, 1) if denom > 1e-9 else 0.0,
        # Bridge residuals: contributions + residual = (case outcome − base outcome)
        "interaction_down": round((metric_cons - metric_base) - sum_down, 3),
        "interaction_up": round((metric_best - metric_base) - sum_up, 3),
    }


def _dd_two_way_grid(base: dict, deal_params: dict, recovery_moic: float,
                     metric: str = "expected_moic") -> dict:
    """
    Excel-style data table: metric across Exit Multiple (x) × Revenue CAGR (y) —
    the two assumptions a multiple-based return actually responds to. (Discount
    rate is deliberately not an axis here: MOIC is exit-value based and does not
    depend on the discount rate, which only drives the DCF value.)
    """
    if not base or _is_na(base.get("exit_multiple")) or _is_na(base.get("revenue_cagr_pct")):
        return {"available": False}
    base_mult = _num(base, "exit_multiple", 10.0)
    base_cagr = _num(base, "revenue_cagr_pct", 90.0)
    x_axis = [round(base_mult * f, 2) for f in (0.6, 0.8, 1.0, 1.2, 1.4)]
    y_axis = [round(base_cagr + d, 1) for d in (-30, -15, 0, 15, 30) if base_cagr + d > 0]
    grid = []
    for cagr in y_axis:
        row = []
        for mult in x_axis:
            a = dict(base)
            a["exit_multiple"] = mult
            a["revenue_cagr_pct"] = cagr
            ret, _ = _dd_run_one(a, deal_params, recovery_moic)
            row.append(round(_dd_metric_value(ret, metric), 2))
        grid.append(row)
    return {
        "available": True, "metric": metric,
        "x_label": "Exit Multiple", "y_label": "Revenue CAGR (%)",
        "x_axis": x_axis, "y_axis": y_axis, "grid": grid,
        "base_x": base_mult, "base_y": base_cagr,
    }


def run_dd_analysis(scenarios: dict, deal_params: dict,
                    recovery_moic: float = 0.0, primary_metric: str = "expected_moic") -> dict:
    """
    Interactive DD underwriting engine. `scenarios` is
    {conservative|base|best_case: assumptions}, where any field may be N/A
    (None / '' / 'N/A'). Returns per-scenario P&L + returns, a comparison block,
    the Conservative→Best sensitivity/impact decomposition, and a two-way grid.
    """
    results = {}
    for name, a in scenarios.items():
        if a is None:
            continue
        ret, pnl = _dd_run_one(a, deal_params, recovery_moic)
        results[name] = {"assumptions": a, "pnl": pnl, "returns": ret}

    def col(getter):
        return {n: getter(v) for n, v in results.items()}

    comparison = {
        "scenarios": list(results.keys()),
        "moic": col(lambda v: v["returns"]["moic"]),
        "expected_moic": col(lambda v: v["returns"]["expected_moic"]),
        "irr_pct": col(lambda v: v["returns"]["irr_pct"]),
        "expected_irr_pct": col(lambda v: v["returns"]["expected_irr_pct"]),
        "exit_ev_m": col(lambda v: v["returns"]["exit_ev_m"]),
        "dcf_ev_m": col(lambda v: v["returns"]["dcf_enterprise_value_m"]),
        "exit_revenue_m": col(lambda v: v["pnl"]["revenue"][-1] if v["pnl"]["revenue"] else 0.0),
        "exit_ebitda_margin_pct": col(lambda v: v["pnl"]["ebitda_margin_pct"][-1] if v["pnl"]["ebitda_margin_pct"] else 0.0),
        "exit_ownership_pct": col(lambda v: v["returns"]["exit_ownership_pct"]),
        "hold_years": col(lambda v: v["returns"]["hold_years_total"]),
        "prob_success_pct": col(lambda v: v["returns"]["prob_success_pct"]),
        "peak_burn_m": col(lambda v: v["returns"].get("peak_cumulative_burn_m")),
        "fcf_breakeven_year": col(lambda v: v["returns"].get("fcf_breakeven_year")),
        "ebitda_breakeven_year": col(lambda v: v["returns"].get("ebitda_breakeven_year")),
    }

    sensitivity = _dd_decompose(scenarios, deal_params, results, recovery_moic, primary_metric)
    two_way = _dd_two_way_grid(scenarios.get("base") or {}, deal_params, recovery_moic, primary_metric)

    return {
        "scenarios": results,
        "comparison": comparison,
        "sensitivity": sensitivity,
        "two_way": two_way,
        "primary_metric": primary_metric,
    }


def trl_profile(trl: int) -> dict:
    """
    Principled starting-point assumptions implied by a Technology Readiness Level,
    derived from the SAME calibrated tables the deal-report Monte Carlo uses:
      • Probability of Success ← annual failure prob (adoption.TRL_PARAMETERS),
        compounded over a ~6-year hold.
      • Time to Commercial Launch ← revenue-lag mean (adoption.TRL_PARAMETERS).
      • Exit-multiple discount ← dilution.TRL_MODIFIERS.
    TRL is intentionally NOT a sensitivity driver (its effects are already captured
    by POS / Time-to-Launch / Exit Multiple) — it only seeds smart defaults.
    """
    from .adoption import TRL_PARAMETERS
    from .dilution import TRL_MODIFIERS

    trl = max(1, min(9, int(trl)))
    tp = TRL_PARAMETERS.get(trl, TRL_PARAMETERS.get(5, {}))
    tm = TRL_MODIFIERS.get(trl, TRL_MODIFIERS.get(5, {}))

    afp = float(tp.get("annual_failure_prob", 0.15))
    hold = 6
    pos_base = (1.0 - afp) ** hold * 100.0  # P(survive the hold), %
    pos = {
        "conservative": round(max(5.0, pos_base - 15.0)),
        "base": round(max(5.0, min(95.0, pos_base))),
        "best_case": round(min(95.0, pos_base + 15.0)),
    }

    lag_mean = float(tp.get("revenue_lag", (3, 1))[0])
    launch_base = round(lag_mean)
    launch = {
        "conservative": launch_base + 1,
        "base": launch_base,
        "best_case": max(0, launch_base - 1),
    }

    return {
        "trl": trl,
        "label": tp.get("label", f"TRL {trl}"),
        "prob_success_pct": pos,
        "time_to_launch_years": launch,
        "exit_multiple_discount": round(float(tm.get("exit_multiple_discount", 1.0)), 2),
    }
