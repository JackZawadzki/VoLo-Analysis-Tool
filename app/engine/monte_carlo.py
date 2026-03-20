"""
Core Monte Carlo simulation engine. Combines adoption curves, revenue modeling,
EBITDA margin modeling, dilution simulation, and exit valuation into a joint
return distribution.

Exit valuation: EV = EBITDA × exit_multiple (EV/EBITDA basis).
EBITDA is computed as revenue × margin, where the margin ramps from a
TRL-dependent starting value toward a mature-company target over time.

Supports five outcome types per simulation path:
  1. FULL_EXIT    — company graduates through stages, exits at horizon via EBITDA×multiple
  2. STAGE_EXIT   — acquired/IPO at an intermediate stage (valued on post-money multiple)
  3. PARTIAL      — acqui-hire or asset sale (returns some capital, usually <1x)
  4. LATE_SMALL   — lingered then small exit
  5. TOTAL_LOSS   — complete write-off
"""

from typing import List, Optional, Tuple
import numpy as np
from .adoption import (
    generate_adoption_trajectories,
    compute_company_revenue,
    compute_founder_anchored_revenue,
    TRL_PARAMETERS,
)
from .dilution import (
    simulate_dilution_path, get_trl_modifiers,
    OUTCOME_FULL_EXIT, OUTCOME_STAGE_EXIT, OUTCOME_PARTIAL_RECOVERY,
    OUTCOME_LATE_SMALL_EXIT, OUTCOME_TOTAL_LOSS,
)


# ── EBITDA margin model ─────────────────────────────────────────────────────
# EBITDA margins ramp from an early negative/low value toward a mature target.
# Earlier TRL companies start with lower margins and take longer to ramp.
# At exit, EV = EBITDA × exit_multiple (EV/EBITDA valuation basis).
#
# Parameters: (margin_start, margin_end, ramp_years)
#   margin_start: EBITDA margin at founding (can be negative for pre-revenue)
#   margin_end:   mature EBITDA margin target
#   ramp_years:   years to reach margin_end from first revenue
#
# Sources: SaaS Capital benchmarks, Bessemer Cloud Index, Battery Ventures
# OpenCloud data. Deep-tech margins from NREL/DOE deployment cost curves.
EBITDA_MARGIN_BY_TRL = {
    1: (-0.20, 0.20, 10),   # Lab concept — deeply negative, long ramp
    2: (-0.15, 0.20, 9),
    3: (-0.10, 0.22, 8),    # Proof of concept
    4: (-0.05, 0.22, 7),
    5: (0.00,  0.25, 6),    # Prototype validated
    6: (0.05,  0.25, 5),    # System demonstrated
    7: (0.08,  0.28, 4),    # Operating in environment
    8: (0.12,  0.30, 3),    # Qualified
    9: (0.18,  0.32, 2),    # Commercially deployed
}


def _compute_ebitda_margin(revenue_paths: np.ndarray, trl: int,
                           n_simulations: int, horizon: int,
                           rng: np.random.Generator) -> np.ndarray:
    """
    Compute EBITDA margin paths that ramp from margin_start to margin_end.
    Returns an array of shape (n_simulations, horizon+1) with margin values.
    EBITDA = revenue × margin at each year.
    """
    margin_start, margin_end, ramp_years = EBITDA_MARGIN_BY_TRL.get(
        trl, (0.00, 0.25, 6)
    )
    ramp_years = max(ramp_years, 1)

    # Year index array
    years = np.arange(horizon + 1)

    # Base margin ramp: linear from start to end over ramp_years
    base_ramp = margin_start + (margin_end - margin_start) * np.clip(
        years / ramp_years, 0.0, 1.0
    )

    # Add per-path noise: some companies execute faster/slower on margins
    # Lognormal noise centered at 1.0, with ~15% stdev
    margin_noise = rng.lognormal(0.0, 0.15, size=(n_simulations, 1))

    # Apply noise to the positive portion above the start
    margin_paths = margin_start + (base_ramp - margin_start) * margin_noise

    # Clip to reasonable range: can't go below -50% or above 45%
    margin_paths = np.clip(margin_paths, -0.50, 0.45)

    return margin_paths


def run_simulation(
    archetype: str,
    tam_millions: float,
    trl: int,
    entry_stage: str,
    check_size_millions: float,
    pre_money_millions: float,
    sector_profile: str,
    carta_data: dict,
    penetration_share: Tuple[float, float] = (0.01, 0.05),
    price_per_unit_m: float = 1.0,
    exit_multiple_range: Tuple[float, float] = (12.0, 30.0),
    exit_year_range: Tuple[int, int] = (5, 10),
    n_simulations: int = 5000,
    random_seed: Optional[int] = None,
    custom_bass_p: Optional[Tuple[float, float]] = None,
    custom_bass_q: Optional[Tuple[float, float]] = None,
    custom_maturity: Optional[str] = None,
    custom_inflection_year: Optional[int] = None,
    founder_revenue_projections_m: Optional[List[float]] = None,
    round_size_m: Optional[float] = None,
) -> dict:
    if check_size_millions <= 0:
        raise ValueError("check_size_millions must be positive")
    if pre_money_millions <= 0:
        raise ValueError("pre_money_millions must be positive")

    rng = np.random.default_rng(random_seed)

    effective_round_size = round_size_m if round_size_m and round_size_m > 0 else check_size_millions
    post_money = pre_money_millions + effective_round_size
    entry_ownership = check_size_millions / post_money

    # --- Layer 1: Adoption trajectories ---
    horizon = max(exit_year_range[1] + 2, 15)
    adoption = generate_adoption_trajectories(
        archetype=archetype,
        tam_millions=tam_millions,
        n_simulations=n_simulations,
        horizon_years=horizon,
        rng=rng,
        custom_p=custom_bass_p,
        custom_q=custom_bass_q,
        custom_maturity=custom_maturity,
        custom_inflection_year=custom_inflection_year,
    )

    # --- Layer 2: Revenue paths ---
    # If the user provided founder revenue projections (from a financial
    # model or manual input), those anchor the trajectory and the S-curve
    # provides the uncertainty envelope.  Otherwise fall back to the pure
    # S-curve-derived revenue model.
    _has_founder_rev = (
        founder_revenue_projections_m is not None
        and len(founder_revenue_projections_m) >= 2
        and any(v > 0 for v in founder_revenue_projections_m)
    )
    if _has_founder_rev:
        revenue_paths = compute_founder_anchored_revenue(
            founder_rev_m=founder_revenue_projections_m,
            adoption_trajectories=adoption["trajectories"],
            trl=trl,
            n_simulations=n_simulations,
            horizon_years=horizon,
            rng=rng,
        )
        revenue_source = "founder_anchored"
    else:
        revenue_paths = compute_company_revenue(
            adoption_trajectories=adoption["trajectories"],
            penetration_share=penetration_share,
            price_per_unit_m=price_per_unit_m,
            trl=trl,
            n_simulations=n_simulations,
            horizon_years=horizon,
            rng=rng,
        )
        revenue_source = "scurve_derived"

    # --- Layer 2b: EBITDA margin model ---
    # EBITDA margins ramp from TRL-dependent starting value toward maturity.
    # EBITDA = revenue × margin.  Exit EV = EBITDA × exit_multiple.
    ebitda_margin_paths = _compute_ebitda_margin(
        revenue_paths, trl, n_simulations, horizon, rng,
    )
    ebitda_paths = revenue_paths * ebitda_margin_paths

    # --- Layer 3: Dilution simulation (TRL-aware, with stage exits) ---
    sector_data = carta_data.get(sector_profile, carta_data.get("DEFAULT (ALL)", {}))
    dilution = simulate_dilution_path(
        entry_stage=entry_stage,
        entry_ownership=entry_ownership,
        sector_data=sector_data,
        trl=trl,
        n_simulations=n_simulations,
        rng=rng,
    )

    trl_mods = get_trl_modifiers(trl)
    exit_multiple_discount = trl_mods["exit_multiple_discount"]

    outcome_code = dilution["outcome_code"]
    # Dilution layer exit proceeds are in raw dollars (Carta data units).
    # Convert to millions to match check_size_millions and revenue units.
    exit_moic_raw = dilution["exit_moic_direct"] / 1e6

    # --- Layer 4: Exit valuation ---
    # Weighted exit year sampling: peak probability at years 5-6, tapering at extremes.
    # Venture exits cluster around years 4-7 (Cambridge Associates, 2023).
    lo, hi = exit_year_range
    possible_years = np.arange(lo, hi + 1)
    mid = (lo + hi) / 2.0
    raw_weights = np.exp(-0.5 * ((possible_years - mid) / max((hi - lo) / 3.0, 1.0)) ** 2)
    for i, yr in enumerate(possible_years):
        if 4 <= yr <= 7:
            raw_weights[i] *= 1.5
    exit_year_probs = raw_weights / raw_weights.sum()
    exit_years = rng.choice(possible_years, size=n_simulations, p=exit_year_probs)

    base_multiples = rng.uniform(
        exit_multiple_range[0], exit_multiple_range[1], n_simulations
    )
    exit_multiples = base_multiples * exit_multiple_discount

    # --- Forward-looking valuation (EBITDA basis) ---
    # VCs and acquirers value companies on FORWARD EBITDA expectations, not
    # trailing. The VC Method (Sahlman, HBS) works backward from projected exit
    # value. At growth/PE stages, NTM+ forward multiples are standard.
    #
    # EV = EBITDA × exit_multiple (EV/EBITDA basis).
    # EBITDA = revenue × margin (margin ramps per TRL via EBITDA_MARGIN_BY_TRL).
    #
    # We look ahead from the exit year by a TRL-dependent window (earlier TRL =
    # acquirer prices in more optionality) and apply a confidence discount.
    #
    # References:
    #   Sahlman, "A Method for Valuing High-Risk, Long-Term Investments" (HBS)
    #   Dealroom.co NTM methodology; Rule of X (growth × 3 + margin)

    # Forward-look window: how many years past exit the valuation references.
    FORWARD_LOOK_BY_TRL = {
        1: 6, 2: 5, 3: 5, 4: 4, 5: 3, 6: 3, 7: 2, 8: 1, 9: 1,
    }
    # Confidence discount: how much of the forward projection an acquirer credits.
    CONFIDENCE_BY_TRL = {
        1: 0.25, 2: 0.30, 3: 0.35, 4: 0.45, 5: 0.55,
        6: 0.65, 7: 0.80, 8: 0.90, 9: 0.95,
    }

    forward_look = FORWARD_LOOK_BY_TRL.get(trl, 3)
    confidence = CONFIDENCE_BY_TRL.get(trl, 0.55)

    # Revenue at exit year (trailing) and forward-projected
    trailing_revenue = np.array([
        revenue_paths[i, min(exit_years[i], horizon)]
        for i in range(n_simulations)
    ])
    forward_revenue = np.array([
        revenue_paths[i, min(exit_years[i] + forward_look, horizon)]
        for i in range(n_simulations)
    ])
    exit_revenue = np.maximum(
        trailing_revenue,
        forward_revenue * confidence,
    )

    # EBITDA at exit: apply margin at exit year to the valuation-basis revenue
    exit_margin = np.array([
        ebitda_margin_paths[i, min(exit_years[i], horizon)]
        for i in range(n_simulations)
    ])
    # For forward-look, use the forward margin (which is higher due to ramp)
    forward_margin = np.array([
        ebitda_margin_paths[i, min(exit_years[i] + forward_look, horizon)]
        for i in range(n_simulations)
    ])
    # Use the better of trailing margin×trailing revenue or forward margin×forward revenue
    trailing_ebitda = trailing_revenue * exit_margin
    forward_ebitda = forward_revenue * confidence * forward_margin
    exit_ebitda = np.maximum(trailing_ebitda, forward_ebitda)

    # Floor EBITDA at zero for valuation — negative EBITDA companies can still
    # exit via strategic acquisition but the EBITDA×multiple framework gives
    # them $0 EV; their value comes from the stage-exit / partial paths instead.
    exit_ebitda = np.maximum(exit_ebitda, 0.0)

    # EBITDA-based EV ceiling for non-full-exit paths.
    # Stage exits valued via Carta post-money can produce extreme outliers.
    # Cap at forward-looking EBITDA×multiple to keep results grounded.
    max_exit_mult = exit_multiple_range[1] * exit_multiple_discount
    cumulative_months = dilution["cumulative_time_months"]
    for i in range(n_simulations):
        if outcome_code[i] == OUTCOME_STAGE_EXIT:
            if exit_ebitda[i] > 0:
                ceiling = exit_ebitda[i] * max_exit_mult * 1.5
                exit_moic_raw[i] = min(exit_moic_raw[i], ceiling)
        elif outcome_code[i] in (OUTCOME_PARTIAL_RECOVERY, OUTCOME_LATE_SMALL_EXIT):
            months = cumulative_months[i]
            approx_year = min(int(months / 12.0), horizon) if months > 0 else 2
            ebitda_at_exit = ebitda_paths[i, approx_year]
            if ebitda_at_exit > 0:
                ceiling = ebitda_at_exit * max_exit_mult
                exit_moic_raw[i] = min(exit_moic_raw[i], ceiling)

    # --- Layer 5: Return computation per outcome type ---
    moic = np.full(n_simulations, 0.0)
    irr = np.full(n_simulations, -1.0)
    gross_proceeds = np.full(n_simulations, 0.0)
    ev_at_exit = np.full(n_simulations, 0.0)

    for i in range(n_simulations):
        oc = outcome_code[i]

        if oc == OUTCOME_FULL_EXIT:
            final_own = dilution["final_ownership"][i]
            if not np.isnan(final_own) and final_own > 0:
                ev = exit_ebitda[i] * exit_multiples[i]
                ev_at_exit[i] = ev
                proceeds = ev * final_own
                gross_proceeds[i] = proceeds
                moic[i] = proceeds / check_size_millions
                moic[i] = min(moic[i], 500.0)
                gross_proceeds[i] = min(gross_proceeds[i], 500.0 * check_size_millions)
            else:
                moic[i] = 0.0

        elif oc == OUTCOME_STAGE_EXIT:
            proceeds = exit_moic_raw[i]
            gross_proceeds[i] = proceeds
            moic[i] = proceeds / check_size_millions

        elif oc == OUTCOME_PARTIAL_RECOVERY:
            proceeds = exit_moic_raw[i]
            gross_proceeds[i] = proceeds
            moic[i] = proceeds / check_size_millions

        elif oc == OUTCOME_LATE_SMALL_EXIT:
            proceeds = exit_moic_raw[i]
            gross_proceeds[i] = proceeds
            moic[i] = proceeds / check_size_millions

        else:
            moic[i] = 0.0

        # IRR calculation
        if moic[i] > 0:
            if oc == OUTCOME_FULL_EXIT:
                hold_years = float(exit_years[i])
            else:
                months = dilution["cumulative_time_months"][i]
                hold_years = float(months / 12.0) if months > 0 else 3.0
            hold_years = max(hold_years, 0.5)
            irr[i] = min(moic[i] ** (1.0 / hold_years) - 1.0, 10.0)
        else:
            irr[i] = -1.0

    # --- Outcome breakdown ---
    outcome_counts = dilution["outcome_counts"]
    outcome_breakdown = {
        "full_exit": {
            "count": outcome_counts["full_exit"],
            "pct": round(outcome_counts["full_exit"] / n_simulations * 100, 1),
            "label": "Full graduation → revenue exit",
        },
        "stage_exit": {
            "count": outcome_counts["stage_exit"],
            "pct": round(outcome_counts["stage_exit"] / n_simulations * 100, 1),
            "label": "Mid-stage acquisition / IPO",
        },
        "partial_recovery": {
            "count": outcome_counts["partial_recovery"],
            "pct": round(outcome_counts["partial_recovery"] / n_simulations * 100, 1),
            "label": "Acqui-hire / asset sale",
        },
        "late_small_exit": {
            "count": outcome_counts["late_small_exit"],
            "pct": round(outcome_counts["late_small_exit"] / n_simulations * 100, 1),
            "label": "Late small exit",
        },
        "total_loss": {
            "count": outcome_counts["total_loss"],
            "pct": round(outcome_counts["total_loss"] / n_simulations * 100, 1),
            "label": "Total loss (write-off)",
        },
    }

    # --- Segment statistics ---
    positive_mask = moic > 0
    n_positive = int(positive_mask.sum())
    positive_moic = moic[positive_mask] if positive_mask.any() else np.array([0.0])
    positive_irr = irr[positive_mask] if positive_mask.any() else np.array([-1.0])

    # Meaningful exits: full + stage (exclude acqui-hires from "good exit" stats)
    meaningful_mask = np.isin(outcome_code, [OUTCOME_FULL_EXIT, OUTCOME_STAGE_EXIT]) & (moic > 0)
    n_meaningful = int(meaningful_mask.sum())
    meaningful_moic = moic[meaningful_mask] if meaningful_mask.any() else np.array([0.0])

    # --- Variance decomposition (among meaningful exits) ---
    variance_drivers = {}
    driver_explanations = {}
    analysis_mask = meaningful_mask if n_meaningful > 30 else positive_mask
    n_for_analysis = int(analysis_mask.sum())
    if n_for_analysis > 30:
        try:
            from scipy.stats import spearmanr
        except ImportError:
            def spearmanr(a, b):
                """Numpy-only Spearman rank correlation fallback."""
                import numpy as _np
                def _rank(x):
                    order = _np.argsort(x)
                    ranks = _np.empty_like(order, dtype=float)
                    ranks[order] = _np.arange(1, len(x) + 1, dtype=float)
                    return ranks
                ra, rb = _rank(a), _rank(b)
                d = ra - rb
                n = len(a)
                rho = 1 - 6 * _np.sum(d**2) / (n * (n**2 - 1))
                from collections import namedtuple
                R = namedtuple('SpearmanrResult', ['correlation', 'pvalue'])
                return R(rho, 0.0)
        analysis_moic = moic[analysis_mask]
        driver_inputs = {
            "adoption_speed": {
                "values": adoption["params_used"][analysis_mask, 1],
                "explanation": "The Bass imitation coefficient (q) controls how fast the S-curve rises. Higher q = faster market takeoff = more revenue by exit.",
            },
            "market_penetration": {
                "values": exit_revenue[analysis_mask],
                "explanation": "The company's captured share of market adoption at exit. Driven by competitive positioning, go-to-market, and product-market fit.",
            },
            "exit_multiple": {
                "values": exit_multiples[analysis_mask],
                "explanation": "EV/EBITDA multiple at exit. Driven by growth rate, market sentiment, and comparable transactions.",
            },
            "ebitda_margin": {
                "values": exit_margin[analysis_mask],
                "explanation": "EBITDA margin at exit year. Ramps from TRL-dependent starting value toward maturity. Higher margin = higher EBITDA = higher EV at same multiple.",
            },
            "dilution_severity": {
                "values": np.array([
                    1.0 - (dilution["final_ownership"][i] if not np.isnan(dilution["final_ownership"][i]) else entry_ownership)
                    for i in range(n_simulations) if analysis_mask[i]
                ]),
                "explanation": "Cumulative ownership erosion through subsequent financing rounds. Driven by capital intensity, number of rounds, and round-to-round step-ups.",
            },
            "exit_timing": {
                "values": exit_years[analysis_mask].astype(float),
                "explanation": "Year of exit event. Earlier exits have less dilution but less revenue; later exits capture more adoption but more dilution.",
            },
        }
        for name, info in driver_inputs.items():
            vals = info["values"]
            if len(vals) == len(analysis_moic):
                corr, pval = spearmanr(vals, analysis_moic)
                variance_drivers[name] = round(abs(corr), 3)
                driver_explanations[name] = {
                    "explanation": info["explanation"],
                    "correlation": round(float(corr), 3),
                    "direction": "positive" if corr > 0 else "negative",
                }

        total = sum(variance_drivers.values()) or 1
        variance_drivers = {k: round(v / total, 3) for k, v in variance_drivers.items()}

    def percentiles(arr):
        return {
            "p5": round(float(np.percentile(arr, 5)), 3),
            "p10": round(float(np.percentile(arr, 10)), 3),
            "p25": round(float(np.percentile(arr, 25)), 3),
            "p50": round(float(np.percentile(arr, 50)), 3),
            "p75": round(float(np.percentile(arr, 75)), 3),
            "p90": round(float(np.percentile(arr, 90)), 3),
            "p95": round(float(np.percentile(arr, 95)), 3),
            "mean": round(float(np.mean(arr)), 3),
        }

    # --- Unconditional probabilities (fund-level) ---
    prob_unconditional = {
        "total_loss": round(float((moic == 0).sum() / n_simulations), 4),
        "below_1x": round(float(((moic > 0) & (moic < 1.0)).sum() / n_simulations), 4),
        "gt_1x": round(float((moic >= 1.0).sum() / n_simulations), 4),
        "gt_3x": round(float((moic >= 3.0).sum() / n_simulations), 4),
        "gt_5x": round(float((moic >= 5.0).sum() / n_simulations), 4),
        "gt_10x": round(float((moic >= 10.0).sum() / n_simulations), 4),
        "gt_20x": round(float((moic >= 20.0).sum() / n_simulations), 4),
        "gt_50x": round(float((moic >= 50.0).sum() / n_simulations), 4),
    }

    expected_moic = round(float(moic.mean()), 3)

    # --- Expected IRR: cashflow-based (dollar-weighted) ---
    # Build aggregate cashflows across all simulated paths and solve for the
    # discount rate where NPV = 0.  This answers "what annualized return does
    # an investor earn deploying capital into this deal profile repeatedly?"
    # Unlike averaging per-path IRRs (where every total loss = -100% and
    # dominates the mean), this weights outcomes by actual dollars returned.
    _max_ey = int(exit_years.max()) + 1
    _agg_cf = np.zeros(_max_ey + 1)
    _agg_cf[0] = -check_size_millions * n_simulations
    for _i in range(n_simulations):
        _ey = int(exit_years[_i])
        _agg_cf[min(_ey, _max_ey)] += moic[_i] * check_size_millions
    _r = 0.10
    for _ in range(150):
        _t_idx = np.arange(len(_agg_cf))
        _disc = (1.0 + _r) ** _t_idx
        _npv = np.sum(_agg_cf / _disc)
        _dnpv = np.sum(-_t_idx * _agg_cf / _disc / (1.0 + _r))
        if abs(_dnpv) < 1e-12:
            break
        _r_new = _r - _npv / _dnpv
        if not np.isfinite(_r_new):
            _r = 0.0
            break
        _r_new = max(min(_r_new, 10.0), -0.99)
        if abs(_r_new - _r) < 1e-8:
            _r = _r_new
            break
        _r = _r_new
    expected_irr_all = round(float(_r), 3)

    # --- Revenue trajectory percentiles ---
    rev_percentiles = {}
    years_arr = list(range(horizon + 1))
    for p_val in [10, 25, 50, 75, 90]:
        rev_percentiles[f"p{p_val}"] = [
            round(float(np.percentile(revenue_paths[:, t], p_val)), 2)
            for t in range(horizon + 1)
        ]

    # --- MOIC histogram ---
    pos_moic = moic[moic > 0]
    if len(pos_moic) > 0:
        moic_max_display = min(float(np.percentile(pos_moic, 98)), 100)
        moic_hist_bins = np.concatenate([[0], np.linspace(0.01, moic_max_display, 35)])
        moic_hist_counts, _ = np.histogram(pos_moic, bins=moic_hist_bins)
    else:
        moic_hist_bins = np.linspace(0, 10, 35)
        moic_hist_counts = np.zeros(len(moic_hist_bins) - 1, dtype=int)

    # --- S-curve data for visualization ---
    adoption_curve_data = {
        "years": list(range(horizon + 1)),
        "p10": [round(float(np.percentile(adoption["trajectories"][:, t], 10)), 2)
                for t in range(horizon + 1)],
        "p25": [round(float(np.percentile(adoption["trajectories"][:, t], 25)), 2)
                for t in range(horizon + 1)],
        "p50": [round(float(np.percentile(adoption["trajectories"][:, t], 50)), 2)
                for t in range(horizon + 1)],
        "p75": [round(float(np.percentile(adoption["trajectories"][:, t], 75)), 2)
                for t in range(horizon + 1)],
        "p90": [round(float(np.percentile(adoption["trajectories"][:, t], 90)), 2)
                for t in range(horizon + 1)],
        "bass_p_mean": round(float(adoption["params_used"][:, 0].mean()), 5),
        "bass_q_mean": round(float(adoption["params_used"][:, 1].mean()), 3),
        "bass_p_std": round(float(adoption["params_used"][:, 0].std()), 5),
        "bass_q_std": round(float(adoption["params_used"][:, 1].std()), 3),
    }

    return {
        "summary": {
            "n_simulations": n_simulations,
            "n_positive_outcome": n_positive,
            "n_meaningful_exit": n_meaningful,
            "n_total_loss": outcome_counts["total_loss"],
            "survival_rate": round(float(positive_mask.mean()), 4),
            "meaningful_exit_rate": round(n_meaningful / n_simulations, 4),
            "random_seed": random_seed,
        },
        "outcome_breakdown": outcome_breakdown,
        "inputs": {
            "archetype": archetype,
            "tam_millions": tam_millions,
            "trl": trl,
            "trl_label": TRL_PARAMETERS.get(trl, {}).get("label", ""),
            "entry_stage": entry_stage,
            "check_size_millions": check_size_millions,
            "round_size_millions": effective_round_size,
            "pre_money_millions": pre_money_millions,
            "entry_ownership_pct": round(entry_ownership * 100, 2),
            "sector_profile": sector_profile,
            "penetration_share": list(penetration_share),
            "exit_multiple_range": list(exit_multiple_range),
            "exit_year_range": list(exit_year_range),
        },
        "moic_unconditional": {
            "expected": expected_moic,
            "p50_all": round(float(np.percentile(moic, 50)), 3),
            "p75_all": round(float(np.percentile(moic, 75)), 3),
            "p90_all": round(float(np.percentile(moic, 90)), 3),
            "p95_all": round(float(np.percentile(moic, 95)), 3),
        },
        "moic_conditional": percentiles(positive_moic),
        "moic_meaningful": percentiles(meaningful_moic) if n_meaningful > 0 else None,
        "irr_conditional": percentiles(positive_irr),
        "expected_irr": expected_irr_all,
        "probability": prob_unconditional,
        "dilution": dilution["stats"],
        "trl_impact": {
            "trl": trl,
            "label": TRL_PARAMETERS.get(trl, {}).get("label", ""),
            "revenue_lag_years": TRL_PARAMETERS.get(trl, {}).get("revenue_lag", (3, 1))[0],
            "survival_penalty": round(trl_mods["survival_penalty"] * 100, 1),
            "capital_intensity_mult": round(trl_mods["capital_multiplier"], 2),
            "extra_bridge_prob": round(trl_mods["extra_bridge_prob"] * 100, 1),
            "exit_multiple_discount": round(trl_mods["exit_multiple_discount"], 2),
            "effective_multiple_range": [
                round(exit_multiple_range[0] * exit_multiple_discount, 1),
                round(exit_multiple_range[1] * exit_multiple_discount, 1),
            ],
        },
        "ebitda_margin": {
            "margin_start": EBITDA_MARGIN_BY_TRL.get(trl, (0.0, 0.25, 6))[0],
            "margin_end": EBITDA_MARGIN_BY_TRL.get(trl, (0.0, 0.25, 6))[1],
            "ramp_years": EBITDA_MARGIN_BY_TRL.get(trl, (0.0, 0.25, 6))[2],
            "exit_margin_mean": round(float(exit_margin[positive_mask].mean()), 3) if n_positive > 0 else 0,
            "exit_margin_p50": round(float(np.percentile(exit_margin[positive_mask], 50)), 3) if n_positive > 0 else 0,
            "exit_ebitda_mean_m": round(float(exit_ebitda[positive_mask].mean()), 2) if n_positive > 0 else 0,
        },
        "ev_at_exit": {
            "mean_m": round(float(ev_at_exit[positive_mask].mean()), 2) if n_positive > 0 else 0,
            "p25_m": round(float(np.percentile(ev_at_exit[positive_mask], 25)), 2) if n_positive > 0 else 0,
            "p50_m": round(float(np.percentile(ev_at_exit[positive_mask], 50)), 2) if n_positive > 0 else 0,
            "p75_m": round(float(np.percentile(ev_at_exit[positive_mask], 75)), 2) if n_positive > 0 else 0,
            "p90_m": round(float(np.percentile(ev_at_exit[positive_mask], 90)), 2) if n_positive > 0 else 0,
            "exit_revenue_mean_m": round(float(exit_revenue[positive_mask].mean()), 2) if n_positive > 0 else 0,
            "exit_ebitda_mean_m": round(float(exit_ebitda[positive_mask].mean()), 2) if n_positive > 0 else 0,
            "exit_margin_mean_pct": round(float(exit_margin[positive_mask].mean()) * 100, 1) if n_positive > 0 else 0,
            "exit_multiple_mean": round(float(exit_multiples[positive_mask].mean()), 1) if n_positive > 0 else 0,
        },
        "adoption": {
            "maturity": adoption["metadata"]["maturity"],
            "inflection_year": adoption["metadata"]["inflection_year"],
        },
        "adoption_curve": adoption_curve_data,
        "revenue_trajectories": {
            "years": years_arr,
            "percentiles": rev_percentiles,
            "source": revenue_source,
            "forward_look_years": forward_look,
            "forward_confidence": confidence,
        },
        "moic_histogram": {
            "bins": [round(float(b), 2) for b in moic_hist_bins],
            "counts": moic_hist_counts.tolist(),
            "n_total_loss": int((moic == 0).sum()),
        },
        "variance_drivers": variance_drivers,
        "variance_explanations": driver_explanations,
        "_raw_moic": moic.tolist(),
        "_raw_exit_years": exit_years.tolist(),
    }
