"""
IRR Method Comparison: Per-Path Geometric IRR vs. Cashflow-Based IRR

The current engine computes Expected IRR as:
  1. Per simulation: IRR_i = MOIC_i^(1/hold_years) - 1  (or -100% if total loss)
  2. Expected IRR = mean(IRR_i) across all 5,000 paths

This script compares that to alternative approaches:
  A. "Portfolio Cashflow IRR" — aggregate all simulated cashflows into a single
     time series (as if you invested in every path) and solve for the single IRR.
  B. "Expected Return IRR" — compute E[MOIC] and E[hold_years] first, then derive
     IRR from those expectations:  E[MOIC]^(1/E[hold]) - 1
  C. "Median IRR" — just use the median instead of the mean
  D. "Log-mean IRR" — geometric mean of (1+IRR) across all paths

Run:  cd volo-engine-local && source .venv/bin/activate && python -m tests.irr_comparison
"""

import sys, os, time
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.data.loader import load_all
from app.engine.monte_carlo import run_simulation
from app.engine.portfolio.irr import irr_newton


def main():
    print("\n" + "=" * 70)
    print("  IRR Method Comparison — VoLo Underwriting Engine")
    print("=" * 70)

    # Match the deal from the screenshot:
    # Series B, Geothermal, TRL 9, $2M check, $35M round, $250M pre-money
    deal_params = {
        "archetype": "geothermal",
        "tam_millions": 50000,
        "trl": 9,
        "entry_stage": "Series B",
        "check_size_millions": 2.0,
        "pre_money_millions": 250.0,
        "sector_profile": "Energy + Deep Tech",
        "penetration_low": 0.01,
        "penetration_high": 0.05,
        "exit_multiple_low": 5.0,
        "exit_multiple_high": 15.0,
        "exit_year_min": 5,
        "exit_year_max": 10,
        "n_simulations": 5000,
        "random_seed": 42,
        "round_size_m": 35.0,
    }

    print(f"\n  Deal: Geothermal / Series B / TRL 9")
    print(f"  Check: ${deal_params['check_size_millions']}M into ${deal_params['pre_money_millions']}M pre")
    print(f"  Round: ${deal_params['round_size_m']}M")
    post_money = deal_params['pre_money_millions'] + deal_params['round_size_m']
    ownership = deal_params['check_size_millions'] / post_money
    print(f"  Post-money: ${post_money}M → Entry ownership: {ownership:.1%}")
    print(f"  Simulations: {deal_params['n_simulations']:,}")

    # Load data
    print("\n  Loading data sources...")
    data = load_all()

    # Run simulation
    print("  Running Monte Carlo simulation...")
    t0 = time.time()
    result = run_simulation(
        archetype=deal_params["archetype"],
        tam_millions=deal_params["tam_millions"],
        trl=deal_params["trl"],
        entry_stage=deal_params["entry_stage"],
        check_size_millions=deal_params["check_size_millions"],
        pre_money_millions=deal_params["pre_money_millions"],
        sector_profile=deal_params["sector_profile"],
        carta_data=data.get("carta_rounds", {}),
        penetration_share=(deal_params["penetration_low"], deal_params["penetration_high"]),
        exit_multiple_range=(deal_params["exit_multiple_low"], deal_params["exit_multiple_high"]),
        exit_year_range=(deal_params["exit_year_min"], deal_params["exit_year_max"]),
        n_simulations=deal_params["n_simulations"],
        random_seed=deal_params["random_seed"],
        round_size_m=deal_params["round_size_m"],
    )
    elapsed = time.time() - t0
    print(f"  Done in {elapsed:.1f}s")

    # Extract raw arrays from result
    moic_all = np.array(result["_raw_moic"])
    exit_years_all = np.array(result["_raw_exit_years"])
    n = len(moic_all)
    check = deal_params["check_size_millions"]

    survival_rate = result["summary"]["survival_rate"]
    expected_moic = result["moic_unconditional"]["expected"]
    current_engine_irr = result["expected_irr"]

    print(f"\n  Survival rate: {survival_rate:.1%}")
    print(f"  Expected MOIC (unconditional): {expected_moic:.2f}x")
    print(f"  Conditional MOIC median: {result['moic_conditional']['median']:.2f}x")
    print(f"  Engine expected IRR: {current_engine_irr:.1%}")

    # ─────────────────────────────────────────────────────────────
    # Current method: per-path geometric IRR, then mean
    # ─────────────────────────────────────────────────────────────
    irr_per_path = np.full(n, -1.0)
    for i in range(n):
        if moic_all[i] > 0:
            hold = max(float(exit_years_all[i]), 0.5)
            irr_per_path[i] = min(moic_all[i] ** (1.0 / hold) - 1.0, 10.0)
        else:
            irr_per_path[i] = -1.0  # total loss = -100%

    current_expected_irr = float(irr_per_path.mean())

    # ─────────────────────────────────────────────────────────────
    # Method A: Portfolio Cashflow IRR
    # Build a single cashflow vector as if you made all 5,000 investments
    # ─────────────────────────────────────────────────────────────
    max_year = int(exit_years_all.max()) + 1
    cashflows = np.zeros(max_year + 1)
    cashflows[0] = -check * n  # invest in all paths at year 0

    for i in range(n):
        exit_yr = int(exit_years_all[i])
        proceeds = moic_all[i] * check
        if exit_yr <= max_year:
            cashflows[exit_yr] += proceeds

    portfolio_irr = irr_newton(cashflows, guess=0.05)

    # ─────────────────────────────────────────────────────────────
    # Method B: Expected-value IRR
    # IRR derived from E[MOIC] and E[hold_years]
    # ─────────────────────────────────────────────────────────────
    positive_mask = moic_all > 0
    if positive_mask.any():
        # Weighted average hold period (total losses have no meaningful hold)
        avg_hold_all = float(exit_years_all.mean())  # unconditional
        avg_hold_survivors = float(exit_years_all[positive_mask].mean())
    else:
        avg_hold_all = 7.0
        avg_hold_survivors = 7.0

    # Using unconditional E[MOIC] and unconditional E[hold]
    if expected_moic > 0:
        ev_irr_unconditional = expected_moic ** (1.0 / avg_hold_all) - 1.0
    else:
        ev_irr_unconditional = -1.0

    # Using unconditional E[MOIC] and conditional E[hold] (survivors only)
    if expected_moic > 0:
        ev_irr_hybrid = expected_moic ** (1.0 / avg_hold_survivors) - 1.0
    else:
        ev_irr_hybrid = -1.0

    # ─────────────────────────────────────────────────────────────
    # Method C: Median IRR (unconditional)
    # ─────────────────────────────────────────────────────────────
    median_irr = float(np.median(irr_per_path))

    # ─────────────────────────────────────────────────────────────
    # Method D: Log-mean (geometric mean) IRR
    # geo_mean = exp(mean(ln(1+IRR))) - 1
    # Note: ln(1 + (-1)) = ln(0) = -inf, so total losses dominate
    # We handle this by using ln(max(1+IRR, 0.001))
    # ─────────────────────────────────────────────────────────────
    growth_factors = 1.0 + irr_per_path  # 0 for total losses
    growth_factors_clipped = np.maximum(growth_factors, 1e-6)
    log_mean_irr = float(np.exp(np.mean(np.log(growth_factors_clipped))) - 1.0)

    # ─────────────────────────────────────────────────────────────
    # Method E: Dollar-weighted expected return
    # Invest $1 in each path, compute total return, annualize
    # ─────────────────────────────────────────────────────────────
    total_invested = check * n
    total_returned = float((moic_all * check).sum())
    overall_moic = total_returned / total_invested
    dollar_weighted_irr = overall_moic ** (1.0 / avg_hold_all) - 1.0

    # ─────────────────────────────────────────────────────────────
    # Method F: Conditional Expected IRR (survivors only)
    # ─────────────────────────────────────────────────────────────
    if positive_mask.any():
        conditional_expected_irr = float(irr_per_path[positive_mask].mean())
        conditional_median_irr = float(np.median(irr_per_path[positive_mask]))
    else:
        conditional_expected_irr = -1.0
        conditional_median_irr = -1.0

    # ─────────────────────────────────────────────────────────────
    # Print comparison
    # ─────────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("  IRR COMPARISON RESULTS")
    print("=" * 70)

    print(f"\n  {'Method':<55} {'IRR':>10}")
    print(f"  {'─' * 55} {'─' * 10}")

    methods = [
        ("CURRENT: Mean of per-path IRRs (total loss = -100%)", current_expected_irr),
        ("A. Portfolio cashflow IRR (NPV=0 solve)", portfolio_irr),
        ("B1. E[MOIC]^(1/E[hold_all]) - 1", ev_irr_unconditional),
        ("B2. E[MOIC]^(1/E[hold_survivors]) - 1", ev_irr_hybrid),
        ("C. Median IRR (unconditional)", median_irr),
        ("D. Geometric mean IRR (log-space)", log_mean_irr),
        ("E. Dollar-weighted: total_return^(1/avg_hold) - 1", dollar_weighted_irr),
        ("F1. Conditional mean IRR (survivors only)", conditional_expected_irr),
        ("F2. Conditional median IRR (survivors only)", conditional_median_irr),
    ]

    for name, val in methods:
        if np.isnan(val):
            print(f"  {name:<55} {'N/A':>10}")
        else:
            print(f"  {name:<55} {val:>9.1%}")

    print(f"\n  {'─' * 66}")
    print(f"\n  Key inputs:")
    print(f"    E[MOIC] (unconditional)      = {expected_moic:.2f}x")
    print(f"    E[hold] (all paths)          = {avg_hold_all:.1f} years")
    print(f"    E[hold] (survivors only)     = {avg_hold_survivors:.1f} years")
    print(f"    Survival rate                = {survival_rate:.1%}")
    print(f"    Total loss paths             = {(~positive_mask).sum():,} / {n:,}")
    print(f"    Positive return paths        = {positive_mask.sum():,} / {n:,}")

    # ─────────────────────────────────────────────────────────────
    # Interpretation
    # ─────────────────────────────────────────────────────────────
    print(f"\n  {'─' * 66}")
    print(f"\n  INTERPRETATION:")
    print(f"  The current method averages per-path IRRs where every dead deal")
    print(f"  counts as -100%. This is mathematically valid but pessimistic")
    print(f"  because averaging rates of return is not the same as the return")
    print(f"  on the average dollar.")
    print(f"")
    print(f"  Methods A and E (cashflow-based and dollar-weighted) answer a")
    print(f"  different question: 'If I invested in this deal profile {n:,}")
    print(f"  times, what annualized return would I earn on my total capital?'")
    print(f"  This is arguably more relevant for a fund manager deciding")
    print(f"  whether to make the investment.")
    print(f"")
    if not np.isnan(portfolio_irr):
        delta = portfolio_irr - current_expected_irr
        print(f"  The cashflow-based IRR ({portfolio_irr:.1%}) differs from the current")
        print(f"  expected IRR ({current_expected_irr:.1%}) by {delta:+.1%} — a {abs(delta/current_expected_irr)*100:.0f}% difference.")
    print()


if __name__ == "__main__":
    main()
