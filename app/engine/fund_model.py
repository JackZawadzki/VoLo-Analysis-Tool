"""
Fund-level performance simulation (BETA).

Takes deal-level MOIC/exit-year distributions and projects portfolio outcomes
under realistic fund economics: management fees, carried interest waterfall,
recycling, and J-curve timing.

Designed to be easily replaceable with actual portfolio data later.
"""

from typing import Optional, List
import numpy as np

# Fee estimation constants
# During the investment period, ~80% of committed capital is subject to fees
# (the remainder is reserved for follow-ons and expenses).
FEE_EFFECTIVE_COMMITMENT_RATIO = 0.8
# Post-investment-period fees typically step down to ~75% of the IP rate.
POST_IP_FEE_STEP_DOWN = 0.75


def simulate_fund(
    fund_size_m: float,
    n_deals: int,
    avg_check_m: float,
    management_fee_pct: float,
    carry_pct: float,
    hurdle_rate: float,
    fund_life_years: int,
    investment_period_years: int,
    deal_moic_distribution: List[float],
    deal_exit_year_distribution: List[float],
    recycling_rate: float = 0.0,
    n_simulations: int = 2000,
    random_seed: Optional[int] = None,
) -> dict:
    """
    Simulate fund-level performance across n_simulations portfolio draws.

    Each simulation:
    1. Deploys capital across n_deals over the investment period
    2. Draws MOIC and exit year for each deal from the provided distributions
    3. Computes gross cash flows by year
    4. Applies management fees (% of committed during IP, % of invested after)
    5. Runs a European carry waterfall: return capital → preferred return → carry split
    6. Computes DPI, TVPI, Net IRR over time

    deal_moic_distribution: array of deal-level MOIC outcomes (from deal screening)
    deal_exit_year_distribution: array of exit years (from deal screening)
    """
    rng = np.random.default_rng(random_seed)

    total_investable = fund_size_m * (1 - management_fee_pct * investment_period_years * FEE_EFFECTIVE_COMMITMENT_RATIO)
    actual_deployment = min(avg_check_m * n_deals, total_investable)
    reserve_ratio = max(0, 1.0 - actual_deployment / fund_size_m)

    moic_pool = np.array(deal_moic_distribution, dtype=float)
    exit_year_pool = np.array(deal_exit_year_distribution, dtype=float)

    # Per-simulation tracking
    fund_gross_moic = np.zeros(n_simulations)
    fund_net_moic = np.zeros(n_simulations)
    fund_dpi_final = np.zeros(n_simulations)
    fund_tvpi_final = np.zeros(n_simulations)
    fund_net_irr = np.zeros(n_simulations)
    fund_gross_irr = np.zeros(n_simulations)

    # Time-series: DPI and TVPI by year (for J-curve)
    dpi_by_year = np.zeros((n_simulations, fund_life_years + 1))
    tvpi_by_year = np.zeros((n_simulations, fund_life_years + 1))

    # Target probability tracking
    n_years = fund_life_years + 1

    for sim in range(n_simulations):
        # --- Deploy deals over investment period ---
        deal_moics = rng.choice(moic_pool, size=n_deals, replace=True)
        deal_exits = rng.choice(exit_year_pool, size=n_deals, replace=True).astype(int)
        deal_exits = np.clip(deal_exits, 1, fund_life_years)

        # Stagger deployment: deals invested in years 0 through investment_period
        deploy_years = rng.integers(0, investment_period_years, size=n_deals)

        # Actual exit year = deploy_year + holding_period
        actual_exit_years = deploy_years + deal_exits
        actual_exit_years = np.clip(actual_exit_years, 1, fund_life_years)

        check_sizes = np.full(n_deals, avg_check_m)
        total_invested = check_sizes.sum()

        # --- Management fees ---
        annual_fees = np.zeros(n_years)
        for yr in range(n_years):
            if yr < investment_period_years:
                annual_fees[yr] = fund_size_m * management_fee_pct
            else:
                # Post-investment period: fee on remaining invested capital
                still_invested = sum(
                    check_sizes[j] for j in range(n_deals)
                    if actual_exit_years[j] > yr
                )
                annual_fees[yr] = still_invested * management_fee_pct * POST_IP_FEE_STEP_DOWN

        cumulative_fees = np.cumsum(annual_fees)

        # --- Gross cash flows by year ---
        gross_distributions_by_year = np.zeros(n_years)
        for j in range(n_deals):
            exit_yr = actual_exit_years[j]
            proceeds = check_sizes[j] * deal_moics[j]
            # Recycling: reinvest a portion of early proceeds
            if recycling_rate > 0 and exit_yr <= investment_period_years:
                reinvest = proceeds * recycling_rate
                proceeds -= reinvest
                # Simplified: reinvested capital gets redeployed with another MOIC draw
                reinvest_moic = rng.choice(moic_pool)
                reinvest_exit = min(exit_yr + rng.integers(2, 5), fund_life_years)
                gross_distributions_by_year[reinvest_exit] += reinvest * reinvest_moic
            gross_distributions_by_year[exit_yr] += proceeds

        cum_gross_dist = np.cumsum(gross_distributions_by_year)
        total_gross = cum_gross_dist[-1]

        # --- Gross MOIC ---
        fund_gross_moic[sim] = total_gross / total_invested if total_invested > 0 else 0

        # --- Net waterfall (European-style) ---
        total_distributions = total_gross
        lp_capital = fund_size_m
        preferred_return = lp_capital * ((1 + hurdle_rate) ** fund_life_years - 1)

        # Step 1: Return LP capital
        remaining = total_distributions - cumulative_fees[-1]
        lp_gets = 0.0
        gp_carry = 0.0

        if remaining <= 0:
            lp_gets = max(remaining + cumulative_fees[-1], 0)
            gp_carry = 0.0
        elif remaining <= lp_capital:
            lp_gets = remaining
            gp_carry = 0.0
        elif remaining <= lp_capital + preferred_return:
            lp_gets = remaining
            gp_carry = 0.0
        else:
            # Above preferred: GP gets carry on excess
            excess = remaining - lp_capital - preferred_return
            # GP catch-up (simplified)
            catchup = min(excess, gp_carry_catchup(excess, carry_pct))
            gp_carry = catchup + (excess - catchup) * carry_pct
            lp_gets = remaining - gp_carry

        net_to_lp = lp_gets
        fund_dpi_final[sim] = net_to_lp / fund_size_m
        fund_net_moic[sim] = net_to_lp / fund_size_m

        # TVPI = DPI at end (no remaining NAV at fund end)
        fund_tvpi_final[sim] = fund_dpi_final[sim]

        # --- Time-series DPI/TVPI ---
        cum_net_dist = 0.0
        for yr in range(n_years):
            gross_this_year = gross_distributions_by_year[yr]
            fees_this_year = annual_fees[yr]
            net_this_year = max(gross_this_year - fees_this_year, 0)
            cum_net_dist += net_this_year

            dpi_by_year[sim, yr] = cum_net_dist / fund_size_m

            # TVPI includes unrealized NAV of remaining deals
            remaining_nav = 0.0
            for j in range(n_deals):
                if actual_exit_years[j] > yr:
                    # Mark at cost until exit (conservative)
                    remaining_nav += check_sizes[j]
            tvpi_by_year[sim, yr] = (cum_net_dist + remaining_nav) / fund_size_m

        # --- IRR calculation ---
        # Cash flows: negative at deployment, positive at distributions
        cf = np.zeros(n_years)
        cf[0] = -fund_size_m  # LP commitment drawn at inception (simplified)
        for yr in range(n_years):
            cf[yr] += gross_distributions_by_year[yr] - annual_fees[yr]

        fund_gross_irr[sim] = _xirr_approx(cf)
        net_cf = cf.copy()
        if gp_carry > 0:
            net_cf[-1] -= gp_carry
        fund_net_irr[sim] = _xirr_approx(net_cf)

    # --- Aggregate results ---
    def pct_dist(arr):
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

    # J-curve percentiles
    jcurve_dpi = {}
    jcurve_tvpi = {}
    for p in [10, 25, 50, 75, 90]:
        jcurve_dpi[f"p{p}"] = [
            round(float(np.percentile(dpi_by_year[:, yr], p)), 3)
            for yr in range(n_years)
        ]
        jcurve_tvpi[f"p{p}"] = [
            round(float(np.percentile(tvpi_by_year[:, yr], p)), 3)
            for yr in range(n_years)
        ]

    # Target probabilities
    dpi_targets = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]
    irr_targets = [0.0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30]

    dpi_probs = {
        f"{t}x": round(float((fund_dpi_final >= t).mean()), 4)
        for t in dpi_targets
    }
    irr_probs = {
        f"{int(t*100)}%": round(float((fund_net_irr >= t).mean()), 4)
        for t in irr_targets
    }

    return {
        "fund_params": {
            "fund_size_m": fund_size_m,
            "n_deals": n_deals,
            "avg_check_m": avg_check_m,
            "total_deployed_m": round(avg_check_m * n_deals, 1),
            "management_fee_pct": management_fee_pct,
            "carry_pct": carry_pct,
            "hurdle_rate": hurdle_rate,
            "fund_life_years": fund_life_years,
            "investment_period_years": investment_period_years,
            "recycling_rate": recycling_rate,
        },
        "gross_moic": pct_dist(fund_gross_moic),
        "net_moic": pct_dist(fund_net_moic),
        "dpi": pct_dist(fund_dpi_final),
        "tvpi": pct_dist(fund_tvpi_final),
        "net_irr": pct_dist(fund_net_irr),
        "gross_irr": pct_dist(fund_gross_irr),
        "jcurve": {
            "years": list(range(n_years)),
            "dpi": jcurve_dpi,
            "tvpi": jcurve_tvpi,
        },
        "target_probabilities": {
            "dpi": dpi_probs,
            "irr": irr_probs,
        },
        "deal_stats": {
            "n_deals_per_fund": n_deals,
            "moic_pool_median": round(float(np.median(moic_pool)), 2),
            "moic_pool_mean": round(float(np.mean(moic_pool)), 2),
            "pct_zeros": round(float((moic_pool == 0).mean()) * 100, 1),
            "exit_year_median": round(float(np.median(exit_year_pool)), 1),
        },
        "n_simulations": n_simulations,
    }


def gp_carry_catchup(excess, carry_pct):
    """GP catch-up amount in a standard waterfall."""
    # GP catches up to carry_pct / (1 - carry_pct) of LP preferred
    # Simplified: GP gets carry_pct / (1 - carry_pct) of the excess first
    if carry_pct >= 1:
        return excess
    catchup_ratio = carry_pct / (1 - carry_pct)
    return excess * min(catchup_ratio, 1.0)


def _xirr_approx(cashflows):
    """
    Approximate IRR using Newton's method on annual cash flows.
    cashflows[0] is typically negative (investment), rest are returns.
    """
    if len(cashflows) < 2:
        return 0.0
    total_in = abs(sum(c for c in cashflows if c < 0))
    total_out = sum(c for c in cashflows if c > 0)
    if total_in == 0:
        return 0.0
    if total_out == 0:
        return -1.0

    # Initial guess from simple MOIC
    n = len(cashflows) - 1
    moic = total_out / total_in if total_in > 0 else 0
    if moic <= 0:
        return -1.0
    rate = moic ** (1.0 / max(n, 1)) - 1.0
    rate = max(min(rate, 5.0), -0.99)

    for _ in range(100):
        npv = sum(cf / (1 + rate) ** t for t, cf in enumerate(cashflows))
        dnpv = sum(-t * cf / (1 + rate) ** (t + 1) for t, cf in enumerate(cashflows))
        if abs(dnpv) < 1e-12:
            break
        new_rate = rate - npv / dnpv
        new_rate = max(min(new_rate, 10.0), -0.99)
        if abs(new_rate - rate) < 1e-8:
            rate = new_rate
            break
        rate = new_rate

    return round(float(rate), 4)
