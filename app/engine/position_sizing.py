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


def compute_followon_ownership(
    prior_investments: list,
    followon_pre_money_m: float,
    followon_round_size_m: float,
) -> dict:
    """
    Deterministic cap-table dilution walk across all prior rounds.

    Returns the *diluted* ownership VoLo holds going INTO the follow-on round
    (``own_pre_followon``) — i.e. after every prior round has diluted every
    earlier one — plus the pro-rata check needed to hold that stake flat
    through the new round.

    Standard priced-round dilution: a round raising ``R`` at pre-money ``P``
    (post = P+R) multiplies every existing holder's fractional ownership by
    ``P/(P+R)``; a participant investing ``c`` gains ``c/(P+R)``::

        own_after = own_before * P/(P+R) + c/(P+R)

    This replaces the previous additive ``Σ entry_ownership`` (which ignored
    intervening dilution and overstated the combined stake).

    Prior rounds are walked in chronological order (by ``year``, then input
    order). Convertibles use their resolved ``effective_pre_m``. Every division
    is guarded; a round with non-positive post-money contributes no change.

    Pro-rata / target ownership (P = follow-on pre, R = follow-on round,
    post = P+R, o = ``own_pre_followon``):
      - own(c)          = o * P/post + c/post   = own_no_followon + c/post
      - pro-rata c      = o * R                  (holds ownership flat)
      - target o_t      = c = o_t*post − o*P     (reaches a chosen ownership)
    """
    priced_priors = [
        p for p in (prior_investments or [])
        if float(p.get("check_m", 0) or 0) > 0
    ]
    # Stable chronological sort — same-year ties keep input order.
    priors = sorted(priced_priors, key=lambda p: float(p.get("year", 0) or 0))

    own = 0.0
    trajectory = []
    for p in priors:
        check_m = float(p.get("check_m", 0) or 0)
        pre_m = float(p.get("effective_pre_m") or p.get("pre_money_m") or 0)
        round_m = float(p.get("round_size_m") or 0) or check_m
        post_m = pre_m + round_m
        # This round's standalone ownership. Prefer the already-resolved
        # ``ownership`` so the walk matches the per-round entry ownership shown
        # elsewhere in the report — for priced rounds this equals check/post,
        # but for convertibles it is check/effective_pre, which differs.
        _resolved_own = p.get("ownership")
        this_own = (
            float(_resolved_own) if _resolved_own is not None
            else (check_m / post_m if post_m > 0 else 0.0)
        )
        dilution = (pre_m / post_m) if post_m > 0 else 1.0
        own = own * dilution + this_own  # dilute prior holdings, add this round
        trajectory.append({
            "year": p.get("year"),
            "stage": p.get("stage", ""),
            "check_m": round(check_m, 4),
            "pre_money_m": round(pre_m, 4),
            "round_size_m": round(round_m, 4),
            "ownership_after_pct": round(own * 100, 4),
        })

    own_pre_followon = own  # ownership entering the follow-on (pre new-round dilution)

    fo_pre = float(followon_pre_money_m or 0)
    fo_round = float(followon_round_size_m or 0)
    fo_post = fo_pre + fo_round

    own_no_followon = (own_pre_followon * fo_pre / fo_post) if fo_post > 0 else own_pre_followon
    pro_rata_check_m = own_pre_followon * fo_round if fo_round > 0 else 0.0

    naive_additive = sum(float(p.get("ownership", 0) or 0) for p in priors)

    return {
        "own_pre_followon": own_pre_followon,
        "own_pre_followon_pct": round(own_pre_followon * 100, 4),
        "own_no_followon": own_no_followon,
        "own_no_followon_pct": round(own_no_followon * 100, 4),
        "pro_rata_check_m": round(pro_rata_check_m, 4),
        "naive_additive_ownership_pct": round(naive_additive * 100, 4),
        "followon_pre_money_m": round(fo_pre, 4),
        "followon_post_money_m": round(fo_post, 4),
        "trajectory": trajectory,
        "n_prior_rounds": len(priors),
    }


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
    prior_exposure_m: float = 0.0,
) -> dict:
    """
    Full position sizing analysis.

    Primary: Fund-performance grid optimizer ($250K increments) — scores each
    check size by the % improvement it produces in fund-level TVPI P10/P50/P90.
    Secondary: Kelly criterion for academic comparison.
    Tertiary: Fund constraint sizing for structural bounds.

    ``prior_exposure_m`` is capital VoLo has ALREADY deployed to this company
    (sum of prior rounds, for follow-ons). When > 0, the concentration limit is
    enforced against *total* exposure (prior + new), so the recommended NEW
    check can never push cumulative exposure past the concentration cap. It is
    0 for first checks, leaving that path's behaviour unchanged.
    """
    kelly = kelly_from_moic_distribution(moic_distribution, check_size_m, fund_size_m)
    constraints = fund_constraint_sizing(fund_size_m, n_deals, mgmt_fee_pct,
                                         reserve_pct, max_concentration_pct)

    constrained_max = constraints["max_check_m"]
    exposure_adjusted_max = constrained_max

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

    # Cumulative-exposure cap: the NEW check must fit within the concentration
    # limit on TOTAL exposure (prior + new), not the new check alone. Without
    # this, a follow-on could be sized up to the full concentration cap on top
    # of capital already in the company, silently breaching cumulative limits.
    if prior_exposure_m and prior_exposure_m > 0:
        exposure_adjusted_max = max(constraints["max_check_m"] - prior_exposure_m, 0.25)
        constrained_max = min(constrained_max, exposure_adjusted_max)

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
            "prior_exposure_m": round(prior_exposure_m, 2) if prior_exposure_m else 0,
            "exposure_adjusted_max_m": round(exposure_adjusted_max, 2),
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


# ---------------------------------------------------------------------------
# Follow-on check size optimizer
# ---------------------------------------------------------------------------

def optimize_followon_position(
    followon_moic_distribution: list,
    first_check_m: float,
    first_pre_money_m: float,
    first_round_size_m: float,
    first_entry_year: int,
    first_entry_stage: str,
    followon_pre_money_m: float,
    followon_round_size_m: float,
    followon_fund_year: int,
    fund_size_m: float = 100.0,
    n_deals: int = 25,
    mgmt_fee_pct: float = 2.0,
    reserve_pct: float = 30.0,
    max_concentration_pct: float = 15.0,
    company_name: str = "",
    survival_rate: float = 0.3,
    moic_conditional_mean: float = 3.0,
    exit_year_range: tuple = (5, 10),
    first_moic_distribution: list = None,
    committed_deals: list = None,
) -> dict:
    """
    Follow-on check size optimizer.

    Treats the first investment as a **fixed sunk cost** already committed
    to the portfolio, then sweeps follow-on check sizes to find the optimal
    additional investment at the new round's post-money valuation.

    The key insight: we evaluate each candidate follow-on check against the
    *combined* (blended) return of first + follow-on, but only optimise
    the follow-on amount since the first check is immutable.

    Returns both:
      - standalone follow-on analysis (TVPI impact of the follow-on alone)
      - blended analysis (combined first + follow-on return characteristics)

    Parameters
    ----------
    followon_moic_distribution : list
        MOIC distribution for the follow-on investment at the new round's
        post-money valuation.
    first_check_m : float
        Original first check size in $M (fixed, sunk cost).
    first_pre_money_m : float
        Pre-money valuation of the first round in $M.
    first_round_size_m : float
        Total round size of the first investment in $M.
    first_entry_year : int
        Fund year when the first investment was made (1-indexed).
    first_entry_stage : str
        Stage at first investment (e.g., "Seed", "Series A").
    followon_pre_money_m : float
        Pre-money valuation of the follow-on round in $M.
    followon_round_size_m : float
        Total follow-on round size in $M.
    followon_fund_year : int
        Fund year when the follow-on occurs (1-indexed).
    fund_size_m : float
        Total fund size in $M.
    first_moic_distribution : list, optional
        MOIC distribution for the first investment. If None, a simple
        binary model based on survival_rate and moic_conditional_mean
        is used.
    committed_deals : list, optional
        Other committed deals already in the portfolio.
    """
    from app.engine.portfolio.simulator import VCSimulator
    from app.engine.portfolio.config import DealConfig, strategy_from_dict

    # ---- Build the first investment as a committed deal (sunk cost) --------
    first_ownership = first_check_m / (first_pre_money_m + first_round_size_m)

    # Estimate first-check MOIC distribution if not provided
    if first_moic_distribution is not None and len(first_moic_distribution) > 100:
        first_moic_array = first_moic_distribution
    else:
        # Simple binary model: survive at conditional mean, or fail at 0
        rng = np.random.RandomState(123456)
        first_moic_array = []
        for _ in range(5000):
            if rng.random() < survival_rate:
                first_moic_array.append(
                    max(0.0, rng.lognormal(
                        np.log(moic_conditional_mean) - 0.5 * 0.8**2, 0.8
                    ))
                )
            else:
                first_moic_array.append(0.0)

    first_committed = {
        "name": f"{company_name} (First Check)" if company_name else "First Check",
        "check_size_m": first_check_m,
        "pre_money_m": first_pre_money_m,
        "round_size_m": first_round_size_m,
        "commitment_type": "first_check",
        "entry_year": first_entry_year,
        "entry_stage": first_entry_stage,
        "moic_distribution": first_moic_array,
    }

    # Combine with any other committed deals
    all_committed = list(committed_deals or [])
    all_committed.append(first_committed)

    # ---- Reserve budget for follow-on ----
    total_reserve = fund_size_m * (reserve_pct / 100.0)
    committed_fo_used = sum(
        cd.get("check_size_m", 0)
        for cd in all_committed
        if cd.get("commitment_type") == "follow_on"
    )
    remaining_reserve = max(total_reserve - committed_fo_used, 0.25)

    # Max follow-on constrained by reserve, round size, and concentration
    investable = fund_size_m * (1 - min(mgmt_fee_pct * 10, 25.0) / 100 - reserve_pct / 100)
    investable = max(investable, fund_size_m * 0.3)
    concentration_cap = investable * max_concentration_pct / 100

    # Follow-on can't exceed remaining reserve or round size
    max_followon = min(remaining_reserve, followon_round_size_m, concentration_cap)

    # Also cap combined (first + follow-on) at concentration limit
    combined_cap = concentration_cap - first_check_m
    if combined_cap > 0:
        max_followon = min(max_followon, combined_cap)
    else:
        max_followon = min(max_followon, 0.5)  # minimal floor if first check exceeds cap

    max_followon = max(max_followon, 0.25)  # always allow at least $250K

    # ---- Stage weights for follow-on (typically later stage) ----
    # Follow-ons skew toward median/upside since the company has de-risked
    followon_stage = _infer_followon_stage(first_entry_stage)
    sw = stage_weights(followon_stage)

    # ---- Run standalone follow-on grid search ----
    standalone_grid = grid_search_fund_performance(
        moic_distribution=followon_moic_distribution,
        fund_size_m=fund_size_m,
        check_size_m=first_check_m,  # reference: size of original check
        pre_money_m=followon_pre_money_m,
        max_check_m=max_followon,
        w_p10=sw["w_p10"],
        w_p50=sw["w_p50"],
        w_p90=sw["w_p90"],
        round_size_m=followon_round_size_m,
        company_name=company_name,
        survival_rate=survival_rate,
        moic_conditional_mean=moic_conditional_mean,
        exit_year_range=exit_year_range,
        committed_deals=all_committed,
        deal_commitment_type="follow_on",
        deal_follow_on_year=followon_fund_year,
    )

    # ---- Compute blended MOIC at each candidate follow-on size ----
    fo_moic_arr = np.array(followon_moic_distribution, dtype=float)
    fi_moic_arr = np.array(first_moic_array, dtype=float)

    # Align array lengths by sampling
    n_sims = min(len(fo_moic_arr), len(fi_moic_arr), 5000)
    rng2 = np.random.RandomState(999)
    fo_sample = rng2.choice(fo_moic_arr, size=n_sims, replace=True)
    fi_sample = rng2.choice(fi_moic_arr, size=n_sims, replace=True)

    blended_analysis = []
    step_m = 0.25
    fo_candidates = np.arange(step_m, max_followon + step_m / 2, step_m)

    for fo_check in fo_candidates:
        fo_check = round(float(fo_check), 2)
        first_proceeds = fi_sample * first_check_m
        followon_proceeds = fo_sample * fo_check
        total_invested = first_check_m + fo_check
        blended_moic = (first_proceeds + followon_proceeds) / total_invested

        blended_analysis.append({
            "followon_check_m": fo_check,
            "total_invested_m": round(total_invested, 2),
            "blended_moic_p10": round(float(np.percentile(blended_moic, 10)), 2),
            "blended_moic_p25": round(float(np.percentile(blended_moic, 25)), 2),
            "blended_moic_p50": round(float(np.percentile(blended_moic, 50)), 2),
            "blended_moic_p75": round(float(np.percentile(blended_moic, 75)), 2),
            "blended_moic_p90": round(float(np.percentile(blended_moic, 90)), 2),
            "blended_moic_mean": round(float(np.mean(blended_moic)), 2),
            "followon_standalone_moic_p50": round(float(np.percentile(fo_sample, 50)), 2),
            "pct_invested_followon": round(fo_check / total_invested * 100, 1),
        })

    # ---- Select optimal follow-on from grid search ----
    grid_optimal = standalone_grid.get("optimal")
    if grid_optimal:
        recommended_followon = grid_optimal["check_m"]
        sizing_method = standalone_grid["method"]
    else:
        # Fallback: pick follow-on size that maximises blended median MOIC
        if blended_analysis:
            best = max(blended_analysis, key=lambda x: x["blended_moic_p50"])
            recommended_followon = best["followon_check_m"]
            sizing_method = "Blended MOIC P50 maximization (grid search fallback)"
        else:
            recommended_followon = step_m
            sizing_method = "Minimum allocation (no viable grid points)"

    recommended_followon = max(recommended_followon, 0.25)

    # Find the blended stats at the recommended size
    recommended_blended = None
    for ba in blended_analysis:
        if abs(ba["followon_check_m"] - recommended_followon) < 0.01:
            recommended_blended = ba
            break

    # ---- Kelly for follow-on ----
    kelly_fo = kelly_from_moic_distribution(
        followon_moic_distribution, recommended_followon, fund_size_m
    )

    # ---- Ownership analysis ----
    followon_ownership = recommended_followon / (followon_pre_money_m + followon_round_size_m)
    combined_ownership_approx = first_ownership + followon_ownership  # simplified, ignores dilution

    return {
        "recommended_followon_check_m": round(recommended_followon, 2),
        "sizing_method": sizing_method,
        "first_investment": {
            "check_m": first_check_m,
            "pre_money_m": first_pre_money_m,
            "round_size_m": first_round_size_m,
            "entry_year": first_entry_year,
            "entry_stage": first_entry_stage,
            "ownership_pct": round(first_ownership * 100, 2),
            "status": "Fixed (sunk cost)",
        },
        "followon_investment": {
            "recommended_check_m": round(recommended_followon, 2),
            "pre_money_m": followon_pre_money_m,
            "round_size_m": followon_round_size_m,
            "fund_year": followon_fund_year,
            "inferred_stage": followon_stage,
            "ownership_pct": round(followon_ownership * 100, 2),
            "max_followon_m": round(max_followon, 2),
            "remaining_reserve_m": round(remaining_reserve, 2),
        },
        "combined": {
            "total_invested_m": round(first_check_m + recommended_followon, 2),
            "combined_ownership_pct": round(combined_ownership_approx * 100, 2),
            "blended_stats": recommended_blended,
        },
        "blended_curve": blended_analysis,
        "standalone_grid_search": standalone_grid,
        "kelly_reference": kelly_fo,
        "stage_weights_used": sw,
    }


def _infer_followon_stage(first_stage: str) -> str:
    """Infer the follow-on investment stage from the first investment stage."""
    progression = {
        "Pre-Seed": "Seed",
        "Seed": "Series A",
        "Series A": "Series B",
        "Series B": "Growth",
        "Growth": "Growth",
    }
    return progression.get(first_stage, "Series A")


# ---------------------------------------------------------------------------
# Multi-prior follow-on optimizer (supports 1–2 prior investments,
# priced rounds and/or convertibles, combined concentration limit)
# ---------------------------------------------------------------------------

def optimize_followon_multi(
    followon_moic_distribution: list,
    prior_investments: list,
    followon_pre_money_m: float,
    followon_round_size_m: float,
    followon_fund_year: int,
    fund_size_m: float = 100.0,
    n_deals: int = 25,
    mgmt_fee_pct: float = 2.0,
    reserve_pct: float = 30.0,
    max_concentration_pct: float = 15.0,
    company_name: str = "",
    survival_rate: float = 0.3,
    moic_conditional_mean: float = 3.0,
    exit_year_range: tuple = (5, 10),
    committed_deals: list = None,
    is_pro_rata: bool = False,
    target_ownership_pct: Optional[float] = None,
) -> dict:
    """
    Follow-on optimizer for 1 or 2 prior investments (priced rounds or convertibles).

    All prior investments are treated as sunk cost.  Concentration limits are
    enforced against *total exposure* (sum of all priors + current follow-on).

    Sizing objective:
      - ``is_pro_rata=True``  → anchor the recommendation to the pro-rata check
        (hold current diluted ownership flat through the round), or to
        ``target_ownership_pct`` if supplied. Capped by concentration headroom.
      - otherwise            → fund-TVPI optimum, evaluated against the running
        fund that *already holds the prior rounds* (so the recommendation is a
        blended/marginal decision, not a standalone one).

    Ownership is computed with a real dilution walk (``compute_followon_ownership``),
    replacing the previous additive sum which overstated the combined stake.

    Parameters
    ----------
    prior_investments : list of dicts
        Each dict must contain at minimum:
          - check_m        : amount invested ($M)
          - stage          : entry stage string
          - year           : fund year at investment
          - effective_pre_m: effective pre-money used for ownership (already
                             computed for convertibles by deal_report._resolve)
          - ownership      : pre-computed ownership fraction
        Optional:
          - type           : "priced" | "safe" | "note"
          - cap_m          : valuation cap (for display only, already resolved)
          - discount_pct   : discount % (for display only, already resolved)
    followon_moic_distribution : list
        MOIC distribution for the follow-on investment at this round's valuation.
    """
    if not prior_investments:
        raise ValueError("prior_investments must contain at least one investment")

    rng_gen = np.random.RandomState(123456)

    # 1. Build MOIC estimates and committed-deal dicts for all prior investments
    prior_moic_arrays = []
    prior_committed = []
    total_prior_check = 0.0

    for i, inv in enumerate(prior_investments):
        check_m = float(inv.get("check_m", 0) or 0)
        if check_m <= 0:
            continue
        total_prior_check += check_m

        stage    = inv.get("stage", "Seed")
        year     = int(inv.get("year", 1) or 1)
        pre_m    = float(inv.get("effective_pre_m") or inv.get("pre_money_m") or 10)
        rnd_m    = float(inv.get("round_size_m") or check_m)

        # Use stored distribution if available, otherwise synthesise
        stored_dist = inv.get("_moic_distribution")
        if stored_dist and len(stored_dist) > 100:
            moic_arr = list(stored_dist)
        else:
            moic_arr = []
            for _ in range(5000):
                if rng_gen.random() < survival_rate:
                    moic_arr.append(max(0.0, rng_gen.lognormal(
                        np.log(max(moic_conditional_mean, 1.01)) - 0.5 * 0.8 ** 2,
                        0.8,
                    )))
                else:
                    moic_arr.append(0.0)

        prior_moic_arrays.append(np.array(moic_arr, dtype=float))
        prior_committed.append({
            "name": f"{company_name} Prior {i + 1}" if company_name else f"Prior {i + 1}",
            "check_size_m": check_m,
            "pre_money_m": pre_m,
            "round_size_m": rnd_m,
            "commitment_type": "first_check",
            "entry_year": year,
            "entry_stage": stage,
            "moic_distribution": moic_arr,
        })

    if total_prior_check <= 0:
        raise ValueError("No valid prior investments found (check_m must be > 0)")

    # 2. Merge with portfolio-level committed deals
    all_committed = list(committed_deals or [])
    all_committed.extend(prior_committed)

    # 3. Compute max follow-on respecting total exposure concentration limit
    total_reserve      = fund_size_m * (reserve_pct / 100.0)
    committed_fo_used  = sum(
        cd.get("check_size_m", 0)
        for cd in all_committed
        if cd.get("commitment_type") == "follow_on"
    )
    remaining_reserve  = max(total_reserve - committed_fo_used, 0.25)

    investable         = fund_size_m * (1 - min(mgmt_fee_pct * 10, 25.0) / 100 - reserve_pct / 100)
    investable         = max(investable, fund_size_m * 0.3)
    concentration_cap  = investable * max_concentration_pct / 100

    # Available headroom = concentration cap minus capital already deployed to this company
    concentration_remaining = max(concentration_cap - total_prior_check, 0.25)

    max_followon = min(remaining_reserve, followon_round_size_m, concentration_remaining)
    max_followon = max(max_followon, 0.25)

    # 4. Stage weights: based on most recent prior investment's stage
    latest_stage = prior_investments[-1].get("stage", "Seed") if prior_investments else "Seed"
    followon_stage = _infer_followon_stage(latest_stage)
    sw = stage_weights(followon_stage)

    # 5. Standalone grid search for the follow-on increment
    standalone_grid = grid_search_fund_performance(
        moic_distribution=followon_moic_distribution,
        fund_size_m=fund_size_m,
        check_size_m=max(total_prior_check, 0.1),   # reference = total prior size
        pre_money_m=followon_pre_money_m,
        max_check_m=max_followon,
        w_p10=sw["w_p10"],
        w_p50=sw["w_p50"],
        w_p90=sw["w_p90"],
        round_size_m=followon_round_size_m,
        company_name=company_name,
        survival_rate=survival_rate,
        moic_conditional_mean=moic_conditional_mean,
        exit_year_range=exit_year_range,
        committed_deals=all_committed,
        deal_commitment_type="follow_on",
        deal_follow_on_year=followon_fund_year,
    )

    # 6. Blended MOIC curves across all prior rounds + follow-on
    fo_moic_arr = np.array(followon_moic_distribution, dtype=float)
    n_sims = min(len(fo_moic_arr), 5000)
    rng2 = np.random.RandomState(999)
    fo_sample = rng2.choice(fo_moic_arr, size=n_sims, replace=True)

    prior_samples = [
        rng2.choice(arr, size=n_sims, replace=True)
        for arr in prior_moic_arrays
    ]

    step_m = 0.25
    fo_candidates = np.arange(step_m, max_followon + step_m / 2, step_m)
    blended_analysis = []

    for fo_check in fo_candidates:
        fo_check = round(float(fo_check), 2)

        # Sum up dollar proceeds from all prior rounds
        prior_proceeds = np.zeros(n_sims, dtype=float)
        for j, inv in enumerate(prior_investments):
            if j < len(prior_samples):
                prior_proceeds += prior_samples[j] * float(inv.get("check_m", 0))

        fo_proceeds    = fo_sample * fo_check
        total_invested = total_prior_check + fo_check
        blended_moic   = (prior_proceeds + fo_proceeds) / total_invested

        blended_analysis.append({
            "followon_check_m":             fo_check,
            "total_invested_m":             round(total_invested, 2),
            "blended_moic_p10":             round(float(np.percentile(blended_moic, 10)), 2),
            "blended_moic_p25":             round(float(np.percentile(blended_moic, 25)), 2),
            "blended_moic_p50":             round(float(np.percentile(blended_moic, 50)), 2),
            "blended_moic_p75":             round(float(np.percentile(blended_moic, 75)), 2),
            "blended_moic_p90":             round(float(np.percentile(blended_moic, 90)), 2),
            "blended_moic_mean":            round(float(np.mean(blended_moic)), 2),
            "followon_standalone_moic_p50": round(float(np.percentile(fo_sample, 50)), 2),
            "pct_invested_followon":        round(fo_check / total_invested * 100, 1),
            "total_prior_invested_m":       round(total_prior_check, 2),
            "n_prior_rounds":               len(prior_investments),
        })

    # 7. Ownership / pro-rata model (real dilution walk, not additive sum)
    own_model = compute_followon_ownership(
        prior_investments, followon_pre_money_m, followon_round_size_m
    )
    own_pre = own_model["own_pre_followon"]          # diluted ownership entering the round
    fo_post_money = own_model["followon_post_money_m"] or (followon_pre_money_m + followon_round_size_m)
    pro_rata_check = own_model["pro_rata_check_m"]    # $ to hold ownership flat

    def _ownership_for_check(c: float) -> float:
        if fo_post_money <= 0:
            return own_pre
        return own_pre * (followon_pre_money_m / fo_post_money) + max(c, 0.0) / fo_post_money

    def _check_for_target(o_target: float) -> float:
        if fo_post_money <= 0:
            return 0.0
        return max(o_target * fo_post_money - own_pre * followon_pre_money_m, 0.0)

    def _round_to_step(x: float, step: float = 0.25) -> float:
        return round(round(x / step) * step, 2)

    # 8. Pick the recommended follow-on size
    grid_optimal = standalone_grid.get("optimal")
    fund_tvpi_optimal_m = grid_optimal["check_m"] if grid_optimal else None
    pro_rata_capped = False

    if is_pro_rata:
        # Anchor to pro-rata (hold ownership flat) or to an explicit target.
        if target_ownership_pct and target_ownership_pct > 0:
            raw_anchor = _check_for_target(target_ownership_pct / 100.0)
            sizing_method = f"Target-ownership sizing — reach {target_ownership_pct:.1f}% post-round"
        else:
            raw_anchor = pro_rata_check
            sizing_method = f"Pro-rata maintenance — hold {own_pre * 100:.1f}% through the round"
        # Respect the concentration / reserve headroom.
        capped_anchor = min(raw_anchor, max_followon)
        pro_rata_capped = raw_anchor > max_followon + 1e-9
        recommended_followon = max(_round_to_step(capped_anchor), 0.25)
        if pro_rata_capped:
            sizing_method += " (capped by concentration limit)"
        selection_objective = "pro_rata" if not target_ownership_pct else "target_ownership"
    elif grid_optimal:
        # Fund-TVPI optimum, evaluated against the running fund that already
        # holds the prior rounds (blended/marginal decision).
        recommended_followon = max(grid_optimal["check_m"], 0.25)
        sizing_method = standalone_grid["method"]
        selection_objective = "fund_tvpi_with_priors_committed"
    elif blended_analysis:
        best = max(blended_analysis, key=lambda x: x["blended_moic_p50"])
        recommended_followon = max(best["followon_check_m"], 0.25)
        sizing_method = "Blended MOIC P50 maximization (grid search fallback)"
        selection_objective = "blended_moic_fallback"
    else:
        recommended_followon = 0.25
        sizing_method = "Minimum allocation (no viable grid points)"
        selection_objective = "min_allocation"

    # Find blended stats at the recommended size (nearest grid candidate)
    recommended_blended = min(
        blended_analysis,
        key=lambda ba: abs(ba["followon_check_m"] - recommended_followon),
        default=None,
    ) if blended_analysis else None

    # Kelly reference for the follow-on increment
    kelly_fo = kelly_from_moic_distribution(
        followon_moic_distribution, recommended_followon, fund_size_m
    )

    # Ownership at the follow-on round (diluted-correct)
    fo_marginal_ownership = recommended_followon / fo_post_money if fo_post_money > 0 else 0
    combined_ownership = _ownership_for_check(recommended_followon)

    # How the recommendation sits relative to pro-rata
    if pro_rata_check > 1e-9:
        if recommended_followon > pro_rata_check * 1.05:
            prorata_position = "super pro-rata"
        elif recommended_followon < pro_rata_check * 0.95:
            prorata_position = "sub pro-rata"
        else:
            prorata_position = "pro-rata"
    else:
        prorata_position = "n/a"

    return {
        "recommended_followon_check_m": round(recommended_followon, 2),
        "sizing_method": sizing_method,
        "prior_investments": [
            {
                "round_num":          i + 1,
                "type":               inv.get("type", "priced"),
                "check_m":            round(float(inv.get("check_m", 0)), 2),
                "stage":              inv.get("stage", ""),
                "year":               inv.get("year", 1),
                "cap_m":              inv.get("cap_m"),
                "discount_pct":       inv.get("discount_pct"),
                "effective_pre_m":    round(float(inv.get("effective_pre_m") or inv.get("pre_money_m", 0)), 2),
                "ownership_pct":      round(float(inv.get("ownership", 0)) * 100, 2),
                "status":             "Fixed (sunk cost)",
            }
            for i, inv in enumerate(prior_investments)
        ],
        "followon_investment": {
            "recommended_check_m": round(recommended_followon, 2),
            "pre_money_m":         followon_pre_money_m,
            "round_size_m":        followon_round_size_m,
            "fund_year":           followon_fund_year,
            "inferred_stage":      followon_stage,
            "ownership_pct":       round(fo_marginal_ownership * 100, 2),
            "max_followon_m":      round(max_followon, 2),
        },
        "combined": {
            "total_prior_m":           round(total_prior_check, 2),
            "n_prior_rounds":          len(prior_investments),
            "total_invested_m":        round(total_prior_check + recommended_followon, 2),
            # Diluted-correct post-round ownership (prior stake diluted by every
            # intervening round + the new check). Replaces the old additive sum.
            "total_ownership_pct_approx": round(combined_ownership * 100, 2),
            "combined_ownership_pct":     round(combined_ownership * 100, 2),
            "blended_stats":           recommended_blended,
        },
        # Pro-rata / ownership decision context.
        # followon_number = which follow-on this check is = number of prior
        # rounds (1 prior round → this is the 1st follow-on).
        "followon_number": own_model["n_prior_rounds"],
        "selection_objective": selection_objective,
        "prorata": {
            "is_pro_rata_mode":             is_pro_rata,
            "target_ownership_pct":         target_ownership_pct,
            "current_diluted_ownership_pct": own_model["own_pre_followon_pct"],
            "ownership_no_followon_pct":    own_model["own_no_followon_pct"],
            "pro_rata_check_m":             round(pro_rata_check, 2),
            "recommended_check_m":          round(recommended_followon, 2),
            "recommended_vs_pro_rata":      prorata_position,
            "pro_rata_exceeds_headroom":    pro_rata_capped,
            "fund_tvpi_optimal_check_m":    round(fund_tvpi_optimal_m, 2) if fund_tvpi_optimal_m else None,
        },
        "ownership_model": {
            "current_diluted_ownership_pct":  own_model["own_pre_followon_pct"],
            "combined_post_round_ownership_pct": round(combined_ownership * 100, 2),
            "naive_additive_ownership_pct":   own_model["naive_additive_ownership_pct"],
            "trajectory":                     own_model["trajectory"],
        },
        "fund_constraints": {
            "investable_capital_m":      round(investable, 2),
            "concentration_cap_m":       round(concentration_cap, 2),
            "total_prior_m":             round(total_prior_check, 2),
            "concentration_remaining_m": round(concentration_remaining, 2),
            "remaining_reserve_m":       round(remaining_reserve, 2),
            "max_followon_m":            round(max_followon, 2),
        },
        "blended_curve": blended_analysis,
        "standalone_grid_search": standalone_grid,
        "kelly_reference": kelly_fo,
        "stage_weights_used": sw,
    }
