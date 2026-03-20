"""
Technology adoption curve engine using Bass diffusion model.
Produces families of S-curves with parameter uncertainty.
"""

from typing import Optional, Tuple
import numpy as np


def bass_diffusion_cumulative(t, p, q, m):
    """
    Cumulative Bass diffusion: fraction of market m adopted by time t.
    p = innovation coefficient (external influence)
    q = imitation coefficient (word-of-mouth)
    m = market potential (TAM asymptote)
    """
    exp_term = np.exp(-(p + q) * t)
    return m * (1 - exp_term) / (1 + (q / p) * exp_term)


def bass_diffusion_rate(t, p, q, m):
    """Instantaneous adoption rate at time t."""
    f = bass_diffusion_cumulative(t, p, q, m) / m
    return m * (p + q * f) * (1 - f)


# Default Bass parameters by technology archetype, calibrated from
# historical deployment patterns. (p, q) pairs with uncertainty (std dev).
DEFAULT_BASS_PARAMS = {
    "utility_solar": {
        "p": (0.005, 0.002),    # (mean, std) — moderate external push
        "q": (0.38, 0.08),      # strong imitation — cost declines drive adoption
        "inflection_year": 2018, # past inflection, mature growth phase
        "maturity": "growth",
    },
    "commercial_solar": {
        "p": (0.004, 0.002),
        "q": (0.30, 0.08),
        "inflection_year": 2020,
        "maturity": "growth",
    },
    "residential_solar": {
        "p": (0.006, 0.003),
        "q": (0.25, 0.07),
        "inflection_year": 2022,
        "maturity": "early_growth",
    },
    "onshore_wind": {
        "p": (0.004, 0.002),
        "q": (0.35, 0.08),
        "inflection_year": 2016,
        "maturity": "growth",
    },
    "offshore_wind": {
        "p": (0.002, 0.001),
        "q": (0.20, 0.08),
        "inflection_year": 2028,
        "maturity": "pre_inflection",
    },
    "geothermal": {
        "p": (0.001, 0.0005),
        "q": (0.12, 0.05),
        "inflection_year": 2032,
        "maturity": "nascent",
    },
    "battery_storage_utility": {
        "p": (0.008, 0.003),
        "q": (0.45, 0.10),
        "inflection_year": 2025,
        "maturity": "inflection",
    },
    "nuclear_smr": {
        "p": (0.001, 0.0005),
        "q": (0.08, 0.04),
        "inflection_year": 2038,
        "maturity": "nascent",
    },
    "ev_electrification": {
        "p": (0.006, 0.002),
        "q": (0.40, 0.10),
        "inflection_year": 2026,
        "maturity": "inflection",
    },
    "climate_software": {
        "p": (0.010, 0.004),
        "q": (0.35, 0.10),
        "inflection_year": 2027,
        "maturity": "early_growth",
    },
    "industrial_decarb": {
        "p": (0.002, 0.001),
        "q": (0.15, 0.06),
        "inflection_year": 2030,
        "maturity": "pre_inflection",
    },
    "ai_ml": {
        "p": (0.015, 0.006),
        "q": (0.50, 0.12),
        "inflection_year": 2024,
        "maturity": "inflection",
    },
    "custom": {
        "p": (0.003, 0.001),
        "q": (0.20, 0.08),
        "inflection_year": 2030,
        "maturity": "pre_inflection",
    },
    # --- Base archetypes: broad category defaults for truly novel tech ---
    "base_capital_intensive": {
        "p": (0.001, 0.0005),
        "q": (0.10, 0.04),
        "inflection_year": 2035,
        "maturity": "nascent",
    },
    "base_software": {
        "p": (0.012, 0.005),
        "q": (0.40, 0.12),
        "inflection_year": 2026,
        "maturity": "early_growth",
    },
    "base_sw_hw_hybrid": {
        "p": (0.005, 0.002),
        "q": (0.28, 0.09),
        "inflection_year": 2028,
        "maturity": "pre_inflection",
    },
    "base_hard_tech": {
        "p": (0.002, 0.001),
        "q": (0.14, 0.06),
        "inflection_year": 2032,
        "maturity": "nascent",
    },
}

# TRL → revenue onset lag (years) and annual failure probability
TRL_PARAMETERS = {
    1: {"revenue_lag": (7, 2), "annual_failure_prob": 0.35, "label": "Basic principles observed"},
    2: {"revenue_lag": (6, 2), "annual_failure_prob": 0.30, "label": "Technology concept formulated"},
    3: {"revenue_lag": (5, 1.5), "annual_failure_prob": 0.25, "label": "Experimental proof of concept"},
    4: {"revenue_lag": (4, 1.5), "annual_failure_prob": 0.20, "label": "Technology validated in lab"},
    5: {"revenue_lag": (3, 1.0), "annual_failure_prob": 0.15, "label": "Technology validated in relevant environment"},
    6: {"revenue_lag": (2.5, 0.8), "annual_failure_prob": 0.12, "label": "Technology demonstrated in relevant environment"},
    7: {"revenue_lag": (1.5, 0.5), "annual_failure_prob": 0.08, "label": "System prototype demonstration"},
    8: {"revenue_lag": (0.8, 0.3), "annual_failure_prob": 0.05, "label": "System complete and qualified"},
    9: {"revenue_lag": (0.3, 0.2), "annual_failure_prob": 0.02, "label": "Actual system proven in operation"},
}


def generate_adoption_trajectories(
    archetype: str,
    tam_millions: float,
    n_simulations: int = 1000,
    horizon_years: int = 15,
    rng: Optional[np.random.Generator] = None,
    custom_p: Optional[Tuple[float, float]] = None,
    custom_q: Optional[Tuple[float, float]] = None,
    custom_maturity: Optional[str] = None,
    custom_inflection_year: Optional[int] = None,
) -> dict:
    """
    Generate n_simulations adoption curve trajectories.

    Uses archetype lookup by default. If custom_p and custom_q are provided,
    bypasses the lookup entirely — enabling novel/user-defined technology types.
    """
    if rng is None:
        rng = np.random.default_rng()

    if custom_p is not None and custom_q is not None:
        p_mean, p_std = custom_p
        q_mean, q_std = custom_q
        maturity = custom_maturity or "pre_inflection"
        inflection_year = custom_inflection_year or 2028
    else:
        params = DEFAULT_BASS_PARAMS.get(archetype, DEFAULT_BASS_PARAMS["utility_solar"])
        p_mean, p_std = params["p"]
        q_mean, q_std = params["q"]
        maturity = params["maturity"]
        inflection_year = params["inflection_year"]

    p_draws = np.clip(rng.normal(p_mean, p_std, n_simulations), 0.0005, 0.05)
    q_draws = np.clip(rng.normal(q_mean, q_std, n_simulations), 0.02, 0.8)

    years = np.arange(0, horizon_years + 1, dtype=float)
    trajectories = np.zeros((n_simulations, len(years)))

    for i in range(n_simulations):
        trajectories[i] = bass_diffusion_cumulative(years, p_draws[i], q_draws[i], tam_millions)

    return {
        "years": years.tolist(),
        "trajectories": trajectories,
        "params_used": np.column_stack([p_draws, q_draws]),
        "metadata": {
            "archetype": archetype,
            "tam_millions": tam_millions,
            "maturity": maturity,
            "inflection_year": inflection_year,
        },
    }


def compute_company_revenue(
    adoption_trajectories: np.ndarray,
    penetration_share: Tuple[float, float],
    price_per_unit_m: float,
    trl: int,
    n_simulations: int,
    horizon_years: int,
    rng: Optional[np.random.Generator] = None,
) -> np.ndarray:
    """
    Given market adoption trajectories, compute company revenue paths.
    penetration_share: (low, high) range for uniform draw.
    Returns (n_simulations, horizon_years+1) revenue in $M.

    NOTE: this is the FALLBACK path used only when no founder projections are
    supplied.  When founder projections exist, compute_founder_anchored_revenue
    should be called instead.
    """
    if rng is None:
        rng = np.random.default_rng()

    trl_params = TRL_PARAMETERS.get(trl, TRL_PARAMETERS[5])
    lag_mean, lag_std = trl_params["revenue_lag"]
    lags = np.clip(rng.normal(lag_mean, lag_std, n_simulations), 0, 10).astype(int)

    shares = rng.uniform(penetration_share[0], penetration_share[1], n_simulations)

    revenue = np.zeros_like(adoption_trajectories)
    for i in range(n_simulations):
        lag = lags[i]
        annual_adoption = np.diff(adoption_trajectories[i], prepend=0)
        annual_adoption = np.maximum(annual_adoption, 0)
        if lag > 0 and lag < len(annual_adoption):
            annual_adoption[:lag] = 0
        revenue[i] = np.cumsum(annual_adoption * shares[i] * price_per_unit_m)

    return revenue


def compute_founder_anchored_revenue(
    founder_rev_m: list,
    adoption_trajectories: np.ndarray,
    trl: int,
    n_simulations: int,
    horizon_years: int,
    rng: Optional[np.random.Generator] = None,
) -> np.ndarray:
    """
    Generate Monte Carlo revenue paths anchored on the founder's projections
    with the S-curve providing the uncertainty envelope.

    Each simulation path scales the founder's trajectory by a market-driven
    factor: paths where the Bass S-curve is faster than median → founder
    projections look conservative → scale up.  Paths where the market is
    slower → scale down.  On top of the market-driven scaling, execution noise
    captures company-specific risk (wider for lower TRL).

    For years beyond the founder's projection window, revenue is extrapolated
    at each path's S-curve-implied growth rate.

    Returns (n_simulations, horizon_years+1) revenue in $M.
    """
    if rng is None:
        rng = np.random.default_rng()

    n_years = horizon_years + 1
    n_founder = len(founder_rev_m)
    revenue = np.zeros((n_simulations, n_years))

    # ── Market-driven scaling factor ──────────────────────────────────────
    # Ratio of each sim's cumulative adoption to the median adoption at
    # a reference year.  This creates the fan: bullish market draws push the
    # company's revenue above the founder's base case and vice versa.
    ref_year = min(max(n_founder, 5), horizon_years)
    median_adoption = np.median(adoption_trajectories[:, ref_year])
    if median_adoption <= 0:
        median_adoption = 1.0
    market_scale = adoption_trajectories[:, ref_year] / median_adoption
    market_scale = np.clip(market_scale, 0.2, 5.0)

    # ── Execution noise calibrated by TRL ─────────────────────────────────
    # Higher TRL = more predictable execution = tighter noise band.
    _TRL_SIGMA = {
        1: 0.55, 2: 0.50, 3: 0.45, 4: 0.38, 5: 0.30,
        6: 0.24, 7: 0.18, 8: 0.12, 9: 0.08,
    }
    base_sigma = _TRL_SIGMA.get(trl, 0.30)

    # ── S-curve growth rates for extrapolation ────────────────────────────
    adoption_shifted = np.maximum(adoption_trajectories, 1e-6)
    market_growth = np.diff(adoption_shifted, axis=1) / adoption_shifted[:, :-1]
    market_growth = np.clip(market_growth, -0.5, 5.0)

    # ── Build founder base array, padded to full horizon ──────────────────
    founder_base = np.zeros(n_years)
    for t in range(min(n_founder, n_years)):
        founder_base[t] = max(founder_rev_m[t], 0.0)

    # Extrapolate beyond founder projections using median market growth rate
    median_growth = np.median(market_growth, axis=0)
    for t in range(n_founder, n_years):
        if founder_base[t - 1] > 0 and t - 1 < len(median_growth):
            g = max(median_growth[t - 1], 0.02)
            founder_base[t] = founder_base[t - 1] * (1 + g)
        elif founder_base[t - 1] > 0:
            founder_base[t] = founder_base[t - 1] * 1.05

    # ── Generate paths ────────────────────────────────────────────────────
    for i in range(n_simulations):
        ms = market_scale[i]
        for t in range(n_years):
            if founder_base[t] <= 0:
                revenue[i, t] = 0.0
                continue

            time_sigma = base_sigma * (1 + 0.05 * t)
            noise = rng.lognormal(-0.5 * time_sigma ** 2, time_sigma)

            if t < n_founder:
                revenue[i, t] = founder_base[t] * ms * noise
            else:
                if revenue[i, t - 1] > 0 and t - 1 < market_growth.shape[1]:
                    g = max(market_growth[i, t - 1], -0.3)
                    revenue[i, t] = revenue[i, t - 1] * (1 + g) * noise
                elif revenue[i, t - 1] > 0:
                    revenue[i, t] = revenue[i, t - 1] * 1.05 * noise
                else:
                    revenue[i, t] = founder_base[t] * ms * noise

    return revenue
