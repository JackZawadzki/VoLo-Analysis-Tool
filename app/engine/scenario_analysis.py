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
    n_commercial = int(assumptions.get("projection_years", 10))
    pre_launch = int(assumptions.get("time_to_launch_years", 0))
    rev_y1 = float(assumptions.get("revenue_y1_m", 1.0))
    cagr = float(assumptions.get("revenue_cagr_pct", 100.0)) / 100.0

    gm_start = float(assumptions.get("gross_margin_start_pct", 40.0)) / 100.0
    gm_end = float(assumptions.get("gross_margin_end_pct", 65.0)) / 100.0

    rd_start = float(assumptions.get("opex_rd_pct_rev", 50.0)) / 100.0
    sm_start = float(assumptions.get("opex_sm_pct_rev", 30.0)) / 100.0
    ga_start = float(assumptions.get("opex_ga_pct_rev", 15.0)) / 100.0
    rd_end = float(assumptions.get("opex_rd_end_pct_rev", 15.0)) / 100.0
    sm_end = float(assumptions.get("opex_sm_end_pct_rev", 12.0)) / 100.0
    ga_end = float(assumptions.get("opex_ga_end_pct_rev", 7.0)) / 100.0

    da_pct = float(assumptions.get("da_pct_rev", 3.0)) / 100.0
    capex_pct = float(assumptions.get("capex_pct_rev", 5.0)) / 100.0
    nwc_pct = float(assumptions.get("nwc_pct_rev", 10.0)) / 100.0
    tax_rate = float(assumptions.get("tax_rate_pct", 0.0)) / 100.0
    terminal_g = float(assumptions.get("terminal_growth_pct", 3.0)) / 100.0
    wacc = float(assumptions.get("discount_rate_pct", 25.0)) / 100.0
    exit_mult = float(assumptions.get("exit_multiple", 10.0))
    exit_type = assumptions.get("exit_multiple_type", "ev_revenue")

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
    # During pre-launch, the company has no revenue but still spends on
    # R&D and G&A. We estimate burn from the Year 1 commercial revenue
    # as a reference point (what the team is spending to get to launch).
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

    if exit_type == "ev_ebitda":
        terminal_value = terminal_ebitda * exit_mult
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
) -> dict:
    """
    Given a P&L projection, compute implied MOIC / IRR for the investor.

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

    exit_idx = (exit_year - 1) if exit_year else (pnl["n_years"] - 1)
    exit_idx = max(0, min(exit_idx, pnl["n_years"] - 1))

    exit_ev = pnl["exit_year_ev"]
    investor_proceeds = exit_ev * exit_ownership

    moic = investor_proceeds / check_size_m if check_size_m > 0 else 0.0
    # Hold period includes bridge delays
    hold_years = (exit_idx + 1) + total_bridge_delay
    irr = (moic ** (1.0 / hold_years) - 1.0) * 100.0 if moic > 0 and hold_years > 0 else 0.0

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
        "dcf_enterprise_value_m": round(pnl["enterprise_value"], 3),
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
