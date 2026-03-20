"""
Position sizing optimization for venture capital deals.

Primary method: Fund-Performance Grid Optimizer at $250k increments.
For each candidate check size, VCSimulator runs fund-level Monte Carlo
with and without the deal.  Three fund-level objectives are evaluated:

  1. % change in fund TVPI P10 — protect the fund's left tail
  2. % change in fund TVPI P50 — improve the fund's median outcome
  3. % change in fund TVPI P90 — capture fund upside convexity

A composite score blends these three objectives via min-max normalization
and configurable weights (default: 30% P10, 35% P50, 35% P90).

The optimizer sweeps every $250k increment from $250K to the fund-constrained
maximum, running the portfolio simulator at each step.

Secondary reference: Kelly Criterion (generalized N-outcome form) for comparison.

References:
  - Kelly, J.L. (1956) "A New Interpretation of Information Rate",
    Bell System Technical Journal
  - Thorp, E.O. (2006) "The Kelly Criterion in Blackjack, Sports Betting,
    and the Stock Market", Handbook of Asset and Liability Management
  - Bochman, A. (2018) "The Kelly Criterion: You Don't Know the Half of It",
    CFA Institute Enterprising Investor
  - Skiena, S. (2007) "The Kelly Criterion", CS691 Stony Brook Lecture
"""

import numpy as np
import logging
from typing import Optional

logger = logging.getLogger(__name__)

STAGE_WEIGHTS = {
    "Pre-Seed": {"w_p10": 0.40, "w_p50": 0.30, "w_p90": 0.30},
    "Seed":     {"w_p10": 0.35, "w_p50": 0.35, "w_p90": 0.30},
    "Series A": {"w_p10": 0.30, "w_p50": 0.35, "w_p90": 0.35},
    "Series B": {"w_p10": 0.25, "w_p50": 0.40, "w_p90": 0.35},
    "Growth":   {"w_p10": 0.20, "w_p50": 0.45, "w_p90": 0.35},
}


def stage_weights(entry_stage: str) -> dict:
    return STAGE_WEIGHTS.get(entry_stage, {"w_p10": 0.30, "w_p50": 0.35, "w_p90": 0.35})


def kelly_from_moic_distribution(moic_array, check_size_m: float, fund_size_m: float) -> dict:
    """
    Compute Kelly sizing directly from a MOIC distribution (from Monte Carlo).
    Converts MOICs to returns: return = MOIC - 1  (0x MOIC -> -1.0 return).
    Uses the N-outcome approximation: f* = E[x] / E[x^2].
    """
    moic = np.array(moic_array)
    returns = moic - 1.0

    ex = float(returns.mean())
    ex2 = float((returns ** 2).mean())

    if ex2 == 0:
        return {"error": "Zero variance in MOIC distribution"}

    f_star = max(0, ex / ex2)

    growth_rate = None
    clipped_returns = np.clip(returns, -0.9999, None)
    if f_star > 0:
        log_terms = np.log(1 + f_star * clipped_returns)
        growth_rate = float(log_terms.mean())

    optimal_check_m = round(f_star * fund_size_m, 2)
    half_kelly_check_m = round(f_star / 2 * fund_size_m, 2)

    fractions = np.linspace(0, min(f_star * 3, 1.0), 50)
    growth_curve = []
    for frac in fractions:
        if frac == 0:
            growth_curve.append(0)
        else:
            terms = np.log(1 + frac * clipped_returns)
            growth_curve.append(round(float(terms.mean()), 6))

    return {
        "kelly_fraction": round(float(f_star), 6),
        "half_kelly_fraction": round(float(f_star / 2), 6),
        "optimal_check_m": optimal_check_m,
        "half_kelly_check_m": half_kelly_check_m,
        "current_check_m": check_size_m,
        "current_fraction": round(check_size_m / fund_size_m, 6),
        "expected_return": round(ex, 4),
        "return_variance": round(ex2, 4),
        "expected_growth_rate": round(float(growth_rate), 6) if growth_rate is not None else None,
        "growth_curve": {
            "fractions": [round(float(f), 4) for f in fractions],
            "growth_rates": growth_curve,
        },
    }


def fund_constraint_sizing(fund_size_m: float, n_deals: int, mgmt_fee_pct: float,
                           reserve_pct: float, max_concentration_pct: float = 15.0) -> dict:
    """
    Compute check size bounds from fund construction parameters.
    """
    mgmt_fee_total_pct = min(mgmt_fee_pct * 10, 25.0)

    investable_capital = fund_size_m * (1 - mgmt_fee_total_pct / 100 - reserve_pct / 100)
    investable_capital = max(investable_capital, fund_size_m * 0.3)

    avg_check = investable_capital / n_deals if n_deals > 0 else 0
    max_check = investable_capital * max_concentration_pct / 100
    min_check = avg_check * 0.3

    return {
        "fund_size_m": fund_size_m,
        "investable_capital_m": round(investable_capital, 2),
        "mgmt_fee_drag_pct": round(mgmt_fee_total_pct, 1),
        "reserve_pct": reserve_pct,
        "n_deals": n_deals,
        "avg_check_m": round(avg_check, 2),
        "max_check_m": round(max_check, 2),
        "min_check_m": round(min_check, 2),
        "max_concentration_pct": max_concentration_pct,
    }


def _pct_change(base, new):
    """Safe percentage change: (new - base) / |base|.  Returns 0 if base ~ 0."""
    if abs(base) < 1e-9:
        return 0.0
    return (new - base) / abs(base)


def _smooth(arr, window=5):
    """Centred rolling mean to suppress Monte-Carlo noise.

    Uses a uniform kernel of size `window` (must be odd).  Edge values
    are handled by shrinking the window symmetrically so the output is
    the same length as the input and the curve endpoints are preserved.
    """
    if len(arr) < window:
        return arr
    half = window // 2
    out = np.empty_like(arr)
    for i in range(len(arr)):
        lo = max(0, i - half)
        hi = min(len(arr), i + half + 1)
        out[i] = arr[lo:hi].mean()
    return out


def grid_search_fund_performance(
    moic_distribution: list,
    fund_size_m: float,
    check_size_m: float,
    pre_money_m: float,
    max_check_m: float = 15.0,
    step_m: float = 0.25,
    w_p10: float = 0.30,
    w_p50: float = 0.35,
    w_p90: float = 0.35,
    round_size_m: float = None,
    company_name: str = "",
    survival_rate: float = 0.3,
    moic_conditional_mean: float = 3.0,
    exit_year_range: tuple = (5, 10),
    n_portfolio_sims: int = 2000,
    sample_every: int = 1,
    committed_deals: list = None,
    deal_commitment_type: str = "first_check",
    deal_follow_on_year: int = 2,
) -> dict:
    """
    Fund-performance grid optimizer at $250k increments.

    For each candidate check size c, runs VCSimulator with and without the
    deal to measure the % change in fund-level TVPI at P10, P50, and P90.
    The composite score is a weighted blend of these three % changes
    (min-max normalized across the grid).

    Parameters
    ----------
    sample_every : int
        Run the full portfolio sim every Nth grid step; interpolate between.
        Set to 1 to run at every step (slower but most accurate).
    n_portfolio_sims : int
        Number of portfolio simulations per grid step.
    """
    moic = np.array(moic_distribution, dtype=np.float64)
    if len(moic) == 0:
        return {"error": "Empty MOIC distribution"}

    # --- Deal-level metrics (informational, not used for scoring) ---
    base_round = round_size_m if round_size_m and round_size_m > 0 else check_size_m
    base_post_money = pre_money_m + base_round
    base_ownership = check_size_m / base_post_money if base_post_money > 0 else 0.1

    steps = np.arange(step_m, max_check_m + step_m / 2, step_m)
    grid_results = []

    for check in steps:
        if round_size_m and round_size_m > 0:
            post_money = pre_money_m + round_size_m
            ownership = check / post_money if post_money > 0 else 0
        else:
            post_money = pre_money_m + check
            ownership = check / post_money if post_money > 0 else 0
        scale = ownership / base_ownership if base_ownership > 0 else 1.0

        scaled_proceeds = moic * check_size_m * scale
        scaled_moic = scaled_proceeds / check

        p10_moic = float(np.percentile(scaled_moic, 10))
        p50_moic = float(np.percentile(scaled_moic, 50))
        p90_moic = float(np.percentile(scaled_moic, 90))
        ev_moic = float(scaled_moic.mean())

        p_loss = float((scaled_moic < 1.0).mean())
        p_gt3x = float((scaled_moic >= 3.0).mean())

        grid_results.append({
            "check_m": round(float(check), 3),
            "ownership_pct": round(ownership * 100, 2),
            "p10_moic": round(p10_moic, 3),
            "p50_moic": round(p50_moic, 3),
            "p90_moic": round(p90_moic, 3),
            "ev_moic": round(ev_moic, 3),
            "p_loss": round(p_loss, 4),
            "p_gt3x": round(p_gt3x, 4),
            "fund_pct": round(float(check / fund_size_m * 100), 2),
            # Fund-level fields populated below
            "fund_p10_base": None,
            "fund_p50_base": None,
            "fund_p90_base": None,
            "fund_p10_new": None,
            "fund_p50_new": None,
            "fund_p90_new": None,
            "fund_p10_pct_chg": None,
            "fund_p50_pct_chg": None,
            "fund_p90_pct_chg": None,
            "composite_score": 0.0,
        })

    if not grid_results:
        return {"error": "No valid grid steps", "grid": [], "optimal": None}

    # --- Fund-level portfolio simulation at sampled grid points ---
    fund_sim_ok = False
    try:
        from pathlib import Path
        from .portfolio.simulator import VCSimulator
        from .portfolio.config import DealConfig, load_strategy

        configs_dir = Path(__file__).resolve().parent.parent.parent / "configs"
        strategy_path = configs_dir / "strategy.json"
        if not strategy_path.exists():
            logger.warning("strategy.json not found; fund-level scoring unavailable")
            raise FileNotFoundError("strategy.json")

        cfg = load_strategy(str(strategy_path))
        sim = VCSimulator(cfg)

        exit_lo, exit_hi = exit_year_range
        exit_mode = (exit_lo + exit_hi) / 2.0
        seed = int(cfg.seed)

        # Determine which grid indices to sample
        indices_to_sample = list(range(0, len(grid_results), sample_every))
        if (len(grid_results) - 1) not in indices_to_sample:
            indices_to_sample.append(len(grid_results) - 1)

        sampled_data = {}
        for idx in indices_to_sample:
            g = grid_results[idx]
            # Scale the MOIC distribution for this candidate check size.
            # As check size changes within a fixed round, ownership changes,
            # so per-dollar returns (MOIC) change proportionally.
            ownership = g["ownership_pct"] / 100.0
            scale = ownership / base_ownership if base_ownership > 0 else 1.0
            scaled_moic_dist = (moic * check_size_m * scale / g["check_m"]).tolist()

            deal = DealConfig(
                name=company_name or "Deal",
                cap_multiple=max(moic_conditional_mean, 1.0),
                success_prob=min(max(survival_rate, 0.01), 0.99),
                failure_multiple=0.0,
                exit_year_mode="triangular",
                exit_year=int(exit_mode),
                exit_year_triangular={"low": float(exit_lo), "mode": exit_mode, "high": float(exit_hi)},
                check_size=g["check_m"] * 1_000_000,
                follow_on_allowed=True,
                metric=cfg.mode,
                moic_distribution=scaled_moic_dist,
            )
            try:
                impact = sim.deal_impact(deal, n_portfolios=n_portfolio_sims, seed=seed,
                                         committed_deals=committed_deals or [],
                                         deal_commitment_type=deal_commitment_type,
                                         deal_follow_on_year=deal_follow_on_year)
                sampled_data[idx] = {
                    "fund_p10_base": impact["tvpi_base_p10"],
                    "fund_p50_base": impact["tvpi_base_p50"],
                    "fund_p90_base": impact["tvpi_base_p90"],
                    "fund_p10_new": impact["tvpi_new_p10"],
                    "fund_p50_new": impact["tvpi_new_p50"],
                    "fund_p90_new": impact["tvpi_new_p90"],
                    "tvpi_lift": impact.get("tvpi_mean_lift", 0),
                    "tvpi_new_mean": impact.get("tvpi_new_mean", 0),
                }
            except Exception as e:
                logger.warning("Portfolio sim failed for check $%.2fM: %s", g["check_m"], e)
                sampled_data[idx] = None

        # Interpolate non-sampled points
        sorted_indices = sorted(k for k, v in sampled_data.items() if v is not None)
        if len(sorted_indices) >= 2:
            fund_sim_ok = True
            interp_keys = ["fund_p10_base", "fund_p50_base", "fund_p90_base",
                           "fund_p10_new", "fund_p50_new", "fund_p90_new",
                           "tvpi_lift", "tvpi_new_mean"]

            for i in range(len(grid_results)):
                if i in sampled_data and sampled_data[i] is not None:
                    for key in interp_keys:
                        grid_results[i][key] = round(sampled_data[i][key], 5)
                else:
                    # Linear interpolation between nearest sampled neighbours
                    lo_idx = max((j for j in sorted_indices if j <= i), default=sorted_indices[0])
                    hi_idx = min((j for j in sorted_indices if j >= i), default=sorted_indices[-1])
                    if lo_idx == hi_idx:
                        for key in interp_keys:
                            grid_results[i][key] = round(sampled_data[lo_idx][key], 5)
                    else:
                        frac = (i - lo_idx) / (hi_idx - lo_idx)
                        for key in interp_keys:
                            val = sampled_data[lo_idx][key] * (1 - frac) + sampled_data[hi_idx][key] * frac
                            grid_results[i][key] = round(val, 5)

            # Compute % change in fund P10, P50, P90 for each grid step
            for g in grid_results:
                g["fund_p10_pct_chg"] = round(_pct_change(g["fund_p10_base"], g["fund_p10_new"]), 5)
                g["fund_p50_pct_chg"] = round(_pct_change(g["fund_p50_base"], g["fund_p50_new"]), 5)
                g["fund_p90_pct_chg"] = round(_pct_change(g["fund_p90_base"], g["fund_p90_new"]), 5)

    except Exception as e:
        logger.warning("Fund-level simulation setup failed: %s", e)

    # --- Scoring ---
    if fund_sim_ok:
        # PRIMARY: Fund-performance scoring using % change in P10/P50/P90.
        # Use raw weighted sum (not per-component min-max) so that the
        # economic magnitude of each dimension is preserved.  A dimension
        # with negligible variation no longer dominates via noise.
        p10_chg = np.array([g["fund_p10_pct_chg"] for g in grid_results])
        p50_chg = np.array([g["fund_p50_pct_chg"] for g in grid_results])
        p90_chg = np.array([g["fund_p90_pct_chg"] for g in grid_results])

        # Smooth each component with a small centred rolling mean to
        # suppress Monte-Carlo sampling noise in fund TVPI percentiles.
        p10_chg = _smooth(p10_chg)
        p50_chg = _smooth(p50_chg)
        p90_chg = _smooth(p90_chg)

        raw_scores = w_p10 * p10_chg + w_p50 * p50_chg + w_p90 * p90_chg

        # Rescale to [0, 1] for display only (preserves relative shape)
        mn, mx = raw_scores.min(), raw_scores.max()
        rng = mx - mn if mx != mn else 1.0
        scores = (raw_scores - mn) / rng

        for i, g in enumerate(grid_results):
            g["composite_score"] = round(float(scores[i]), 4)

        best_idx = int(np.argmax(scores))
        optimal = grid_results[best_idx]
        method = "Fund-performance grid optimization at $250K increments"
        objectives = {
            "p10": f"Maximize % improvement in fund TVPI P10 (left tail) -- weight {w_p10:.0%}",
            "p50": f"Maximize % improvement in fund TVPI P50 (median) -- weight {w_p50:.0%}",
            "p90": f"Maximize % improvement in fund TVPI P90 (upside) -- weight {w_p90:.0%}",
        }
    else:
        # FALLBACK: Deal-level dollar-return scoring (when portfolio sim unavailable)
        dollar_profits = []
        for g in grid_results:
            check = g["check_m"]
            ownership = g["ownership_pct"] / 100.0
            scale = ownership / base_ownership if base_ownership > 0 else 1.0
            sp = moic * check_size_m * scale
            dollar_profits.append(sp - check)

        p10_vals = _smooth(np.array([float(np.percentile(dp, 10)) for dp in dollar_profits]))
        p50_vals = _smooth(np.array([float(np.percentile(dp, 50)) for dp in dollar_profits]))
        p90_vals = _smooth(np.array([float(np.percentile(dp, 90)) for dp in dollar_profits]))

        raw_scores = w_p10 * p10_vals + w_p50 * p50_vals + w_p90 * p90_vals

        mn, mx = raw_scores.min(), raw_scores.max()
        rng = mx - mn if mx != mn else 1.0
        scores = (raw_scores - mn) / rng

        for i, g in enumerate(grid_results):
            g["composite_score"] = round(float(scores[i]), 4)

        best_idx = int(np.argmax(scores))
        optimal = grid_results[best_idx]
        method = "Deal-level percentile fallback (fund sim unavailable)"
        objectives = {
            "p10": f"Maximize P10 dollar profit (fallback) -- weight {w_p10:.0%}",
            "p50": f"Maximize P50 dollar profit (fallback) -- weight {w_p50:.0%}",
            "p90": f"Maximize P90 dollar profit (fallback) -- weight {w_p90:.0%}",
        }

    return {
        "grid": grid_results,
        "optimal": optimal,
        "step_m": step_m,
        "n_steps": len(grid_results),
        "fund_sim_ok": fund_sim_ok,
        "method": method,
        "objectives": objectives,
        "weights": {"w_p10": w_p10, "w_p50": w_p50, "w_p90": w_p90},
    }


def optimize_position_size(
    moic_distribution: list,
    check_size_m: float,
    pre_money_m: float,
    fund_size_m: float,
    n_deals: int = 25,
    mgmt_fee_pct: float = 2.0,
    reserve_pct: float = 30.0,
    max_concentration_pct: float = 15.0,
    entry_stage: str = "Seed",
    company_name: str = "",
    survival_rate: float = 0.3,
    moic_conditional_mean: float = 3.0,
    exit_year_range: tuple = (5, 10),
    round_size_m: float = None,
    committed_deals: list = None,
    deal_commitment_type: str = "first_check",
    deal_follow_on_year: int = 2,
) -> dict:
    """
    Full position sizing analysis.

    Primary: Fund-performance grid optimizer ($250K increments) — scores each
    check size by the % improvement it produces in fund-level TVPI P10/P50/P90.
    Secondary: Kelly criterion for academic comparison.
    Tertiary: Fund constraint sizing for structural bounds.
    """
    kelly = kelly_from_moic_distribution(moic_distribution, check_size_m, fund_size_m)
    constraints = fund_constraint_sizing(fund_size_m, n_deals, mgmt_fee_pct,
                                         reserve_pct, max_concentration_pct)

    constrained_max = constraints["max_check_m"]

    # For follow-on deals, cap at remaining reserve capital
    if deal_commitment_type == "follow_on":
        total_reserve = fund_size_m * (reserve_pct / 100.0)
        committed_fo_used = sum(
            cd.get("check_size_m", 0)
            for cd in (committed_deals or [])
            if cd.get("commitment_type") == "follow_on"
        )
        remaining_reserve = max(total_reserve - committed_fo_used, 0.25)
        constrained_max = min(constrained_max, remaining_reserve)

    if round_size_m is not None:
        constrained_max = min(constrained_max, round_size_m)
        min_floor = max(0.25, round_size_m * 0.05)
    else:
        min_floor = 0.25

    sw = stage_weights(entry_stage)

    grid = grid_search_fund_performance(
        moic_distribution=moic_distribution,
        fund_size_m=fund_size_m,
        check_size_m=check_size_m,
        pre_money_m=pre_money_m,
        max_check_m=constrained_max,
        w_p10=sw["w_p10"],
        w_p50=sw["w_p50"],
        w_p90=sw["w_p90"],
        round_size_m=round_size_m,
        company_name=company_name,
        survival_rate=survival_rate,
        moic_conditional_mean=moic_conditional_mean,
        exit_year_range=exit_year_range,
        committed_deals=committed_deals,
        deal_commitment_type=deal_commitment_type,
        deal_follow_on_year=deal_follow_on_year,
    )

    grid_optimal = grid.get("optimal")
    if grid_optimal:
        recommended = grid_optimal["check_m"]
        sizing_method = grid["method"]
    else:
        half_kelly = kelly.get("half_kelly_check_m", 0)
        recommended = min(half_kelly, constrained_max)
        recommended = max(recommended, constraints["min_check_m"])
        sizing_method = "Half-Kelly fallback (grid search produced no results)"

    recommended = max(recommended, min_floor)

    return {
        "recommended_check_m": round(recommended, 2),
        "sizing_method": sizing_method,
        "grid_search": grid,
        "kelly_reference": kelly,
        "fund_constraints": constraints,
        "stage_weights_used": sw,
        "round_size_m": round_size_m,
        "comparison": {
            "current_check_m": check_size_m,
            "optimizer_recommended_m": round(recommended, 2),
            "kelly_optimal_m": kelly.get("optimal_check_m", 0),
            "half_kelly_m": kelly.get("half_kelly_check_m", 0),
            "fund_avg_m": constraints["avg_check_m"],
            "fund_max_m": constrained_max,
        },
        "references": [
            {
                "author": "Kelly, J.L.",
                "year": 1956,
                "title": "A New Interpretation of Information Rate",
                "source": "Bell System Technical Journal",
            },
            {
                "author": "Bochman, A.",
                "year": 2018,
                "title": "The Kelly Criterion: You Don't Know the Half of It",
                "source": "CFA Institute Enterprising Investor",
                "url": "https://blogs.cfainstitute.org/investor/2018/06/14/the-kelly-criterion-you-dont-know-the-half-of-it/",
            },
            {
                "author": "Thorp, E.O.",
                "year": 2006,
                "title": "The Kelly Criterion in Blackjack, Sports Betting, and the Stock Market",
                "source": "Handbook of Asset and Liability Management",
            },
            {
                "author": "Skiena, S.",
                "year": 2007,
                "title": "The Kelly Criterion -- How To Manage Your Money When You Have an Edge",
                "source": "CS691 Stony Brook Lecture",
                "url": "https://www3.cs.stonybrook.edu/~skiena/691/2007/lectures/Kelly.pdf",
            },
        ],
    }
