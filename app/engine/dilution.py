"""
Probabilistic dilution simulation engine powered by Carta financing data.
Simulates ownership decay through multiple financing rounds with
stage-by-stage exit events (acquisition, IPO, secondary) and partial-loss
outcomes (acqui-hire, asset sale).

TRL modulates survival, capital intensity, round count, and exit multiples.

ASSUMPTIONS NEEDING CARTA DATA (marked with [ASSUMPTION]):
- Stage-by-stage exit probability (acq/IPO rate per stage)
- Exit valuation multiples on last post-money by stage
- Partial-loss recovery rates (acqui-hire % of last post-money)
- Down-round frequency and magnitude by stage
- Revenue at raise benchmarks by stage (to tie exit to revenue)
"""

from typing import Optional
import numpy as np


STAGE_ORDER = ["Pre-Seed", "Seed", "Series A", "Series B", "Series C", "Series D", "Series E+"]

TRL_MODIFIERS = {
    1: {"survival_penalty": 0.35, "capital_multiplier": 1.8, "extra_bridge_prob": 0.50, "exit_multiple_discount": 0.55},
    2: {"survival_penalty": 0.30, "capital_multiplier": 1.6, "extra_bridge_prob": 0.45, "exit_multiple_discount": 0.60},
    3: {"survival_penalty": 0.25, "capital_multiplier": 1.4, "extra_bridge_prob": 0.40, "exit_multiple_discount": 0.65},
    4: {"survival_penalty": 0.20, "capital_multiplier": 1.25, "extra_bridge_prob": 0.30, "exit_multiple_discount": 0.75},
    5: {"survival_penalty": 0.15, "capital_multiplier": 1.15, "extra_bridge_prob": 0.20, "exit_multiple_discount": 0.85},
    6: {"survival_penalty": 0.12, "capital_multiplier": 1.08, "extra_bridge_prob": 0.12, "exit_multiple_discount": 0.90},
    7: {"survival_penalty": 0.08, "capital_multiplier": 1.03, "extra_bridge_prob": 0.06, "exit_multiple_discount": 0.95},
    8: {"survival_penalty": 0.05, "capital_multiplier": 1.00, "extra_bridge_prob": 0.03, "exit_multiple_discount": 0.98},
    9: {"survival_penalty": 0.02, "capital_multiplier": 0.95, "extra_bridge_prob": 0.01, "exit_multiple_discount": 1.00},
}

# [ASSUMPTION] Stage-specific exit probabilities and outcomes.
# These represent the chance a company exits (acq/IPO) AT or AFTER a given stage,
# rather than raising the next round. Need Carta exit data to calibrate.
STAGE_EXIT_PARAMS = {
    "Pre-Seed": {"exit_prob": 0.02, "acq_mult_on_postmoney": (0.5, 2.0), "partial_recovery": 0.10},
    "Seed":     {"exit_prob": 0.05, "acq_mult_on_postmoney": (0.8, 3.0), "partial_recovery": 0.15},
    "Series A": {"exit_prob": 0.10, "acq_mult_on_postmoney": (1.0, 4.0), "partial_recovery": 0.20},
    "Series B": {"exit_prob": 0.15, "acq_mult_on_postmoney": (1.2, 5.0), "partial_recovery": 0.25},
    "Series C": {"exit_prob": 0.20, "acq_mult_on_postmoney": (1.5, 6.0), "partial_recovery": 0.30},
    "Series D": {"exit_prob": 0.25, "acq_mult_on_postmoney": (2.0, 8.0), "partial_recovery": 0.30},
    "Series E+":{"exit_prob": 0.30, "acq_mult_on_postmoney": (2.0, 10.0),"partial_recovery": 0.30},
}

# [ASSUMPTION] When a company fails to graduate, what fraction get a
# partial-loss outcome (acqui-hire, asset sale) vs total write-off.
# Need Carta data on non-graduating company outcomes.
FAIL_OUTCOME_PROBS = {
    "total_loss": 0.50,
    "partial_recovery": 0.15,
    "bridge_survives": 0.25,
    "late_exit_small": 0.10,
}


def get_trl_modifiers(trl):
    return TRL_MODIFIERS.get(trl, TRL_MODIFIERS[5])


def _percentile_to_lognormal(p10, p50, p90):
    if p50 is None or p50 <= 0:
        return None, None
    if p10 is None or p10 <= 0:
        p10 = p50 * 0.3
    if p90 is None or p90 <= 0:
        p90 = p50 * 3.0
    mu = np.log(p50)
    sigma = (np.log(p90) - np.log(p10)) / (2 * 1.2816)
    sigma = max(sigma, 0.1)
    return mu, sigma


# Outcome codes for each simulation
OUTCOME_RUNNING = 0
OUTCOME_FULL_EXIT = 1       # graduated through stages, exits at horizon
OUTCOME_STAGE_EXIT = 2      # acquired/IPO at intermediate stage
OUTCOME_PARTIAL_RECOVERY = 3 # acqui-hire or asset sale (returns some capital)
OUTCOME_LATE_SMALL_EXIT = 4  # lingered then small exit
OUTCOME_TOTAL_LOSS = 5       # complete write-off


def simulate_dilution_path(
    entry_stage: str,
    entry_ownership: float,
    sector_data: dict,
    trl: int = 5,
    n_simulations: int = 1000,
    exit_horizon_years: int = 10,
    rng: Optional[np.random.Generator] = None,
) -> dict:
    """
    Simulate ownership dilution paths from entry_stage through exit.

    At each stage transition, four things can happen:
    1. GRADUATE + EXIT: Company raises next round AND gets acquired/IPO'd
    2. GRADUATE + CONTINUE: Company raises next round and keeps going
    3. FAIL + PARTIAL: Company doesn't graduate but gets acqui-hired/asset-sold
    4. FAIL + BRIDGE: Company takes a bridge round, survives to try again
    5. FAIL + TOTAL LOSS: Company dies completely

    TRL modulates graduation rates, capital intensity, bridge probability.
    """
    if rng is None:
        rng = np.random.default_rng()

    trl_mods = get_trl_modifiers(trl)
    survival_penalty = trl_mods["survival_penalty"]
    capital_mult = trl_mods["capital_multiplier"]
    bridge_prob = trl_mods["extra_bridge_prob"]

    entry_idx = STAGE_ORDER.index(entry_stage) if entry_stage in STAGE_ORDER else 0

    ownership_paths = np.full((n_simulations, len(STAGE_ORDER)), np.nan)
    ownership_paths[:, entry_idx] = entry_ownership

    # Per-simulation tracking
    outcome_code = np.full(n_simulations, OUTCOME_RUNNING, dtype=int)
    exit_stage_idx = np.full(n_simulations, -1, dtype=int)
    exit_moic_direct = np.full(n_simulations, 0.0)  # direct MOIC from exit event
    last_postmoney = np.full(n_simulations, 0.0)
    total_raised = np.zeros(n_simulations)
    rounds_completed = np.zeros(n_simulations, dtype=int)
    bridge_rounds = np.zeros(n_simulations, dtype=int)
    cumulative_time_months = np.zeros(n_simulations)

    def trl_decay(stages_from_entry):
        return max(0, 1.0 - stages_from_entry * 0.3)

    for stage_idx in range(entry_idx, len(STAGE_ORDER) - 1):
        stage = STAGE_ORDER[stage_idx]
        next_stage = STAGE_ORDER[stage_idx + 1]
        stages_elapsed = stage_idx - entry_idx

        stage_data_next = sector_data.get(next_stage)
        current_stage_data = sector_data.get(stage, {})
        exit_params = STAGE_EXIT_PARAMS.get(stage, STAGE_EXIT_PARAMS["Seed"])

        if stage_data_next is None:
            break

        base_grad_rate = current_stage_data.get("graduation_rate")
        if base_grad_rate is None:
            base_grad_rate = 0.35

        decay = trl_decay(stages_elapsed)
        effective_grad_rate = base_grad_rate * (1.0 - survival_penalty * decay)
        effective_grad_rate = max(effective_grad_rate, 0.05)

        months_to_grad = current_stage_data.get("median_months_to_grad")
        if months_to_grad is None:
            months_to_grad = 18.0

        rs = stage_data_next.get("round_size", {})
        pm = stage_data_next.get("pre_money", {})
        esop = stage_data_next.get("median_esop") or 0.15

        rs_mu, rs_sigma = _percentile_to_lognormal(
            rs.get("p10"), rs.get("p50"), rs.get("p90")
        )
        pm_mu, pm_sigma = _percentile_to_lognormal(
            pm.get("p10"), pm.get("p50"), pm.get("p90")
        )

        if rs_mu is None or pm_mu is None:
            break

        effective_rs_mu = rs_mu + np.log(
            max(capital_mult * trl_decay(stages_elapsed) + (1 - trl_decay(stages_elapsed)), 0.9)
        )

        # Stage exit probability — increases with stage maturity
        stage_exit_prob = exit_params["exit_prob"]
        acq_mult_range = exit_params["acq_mult_on_postmoney"]
        partial_recovery_rate = exit_params["partial_recovery"]

        for i in range(n_simulations):
            if outcome_code[i] != OUTCOME_RUNNING:
                continue

            # Resolve current ownership — find last valid value in case of gaps
            current_own = ownership_paths[i, stage_idx]
            if np.isnan(current_own):
                valid = ownership_paths[i, :stage_idx + 1]
                valid = valid[~np.isnan(valid)]
                if len(valid) == 0:
                    outcome_code[i] = OUTCOME_TOTAL_LOSS
                    exit_stage_idx[i] = stage_idx
                    continue
                current_own = valid[-1]
                ownership_paths[i, stage_idx] = current_own

            graduates = rng.random() < effective_grad_rate

            if graduates:
                # --- Priced round ---
                round_size = np.exp(rng.normal(effective_rs_mu, rs_sigma))
                pre_money = np.exp(rng.normal(pm_mu, pm_sigma))
                pre_money = max(pre_money, round_size * 1.5)
                post_money = pre_money + round_size

                round_dilution = round_size / post_money
                esop_dilution = esop * 0.3
                new_own = current_own * (1 - round_dilution) * (1 - esop_dilution)

                # TRL extra bridge
                if rng.random() < bridge_prob * trl_decay(stages_elapsed):
                    extra_bridge = np.exp(rng.normal(rs_mu - 1.0, rs_sigma * 0.6))
                    extra_pre = pre_money * 0.85
                    extra_dilution = extra_bridge / (extra_pre + extra_bridge)
                    new_own = new_own * (1 - extra_dilution)
                    total_raised[i] += extra_bridge
                    bridge_rounds[i] += 1

                ownership_paths[i, stage_idx + 1] = max(new_own, 0.001)
                total_raised[i] += round_size
                rounds_completed[i] += 1
                last_postmoney[i] = post_money
                cumulative_time_months[i] += max(rng.normal(months_to_grad, months_to_grad * 0.3), 6)

                # --- Check for stage exit (acquisition/IPO after raising) ---
                if rng.random() < stage_exit_prob:
                    acq_mult = rng.uniform(acq_mult_range[0], acq_mult_range[1])
                    exit_val = post_money * acq_mult
                    own_at_exit = ownership_paths[i, stage_idx + 1]
                    exit_moic_direct[i] = (exit_val * own_at_exit)
                    outcome_code[i] = OUTCOME_STAGE_EXIT
                    exit_stage_idx[i] = stage_idx + 1

            else:
                # --- Did NOT graduate ---
                roll = rng.random()

                if roll < FAIL_OUTCOME_PROBS["total_loss"]:
                    outcome_code[i] = OUTCOME_TOTAL_LOSS
                    exit_stage_idx[i] = stage_idx
                    exit_moic_direct[i] = 0.0

                elif roll < (FAIL_OUTCOME_PROBS["total_loss"] +
                             FAIL_OUTCOME_PROBS["partial_recovery"]):
                    # [ASSUMPTION] Acqui-hire / asset sale returns a fraction
                    # of last known post-money valuation
                    recovery_val = last_postmoney[i] if last_postmoney[i] > 0 else (
                        np.exp(rng.normal(pm_mu - 0.5, pm_sigma)))
                    recovery_mult = rng.uniform(
                        partial_recovery_rate * 0.5,
                        partial_recovery_rate * 2.0
                    )
                    exit_moic_direct[i] = recovery_val * recovery_mult * current_own
                    outcome_code[i] = OUTCOME_PARTIAL_RECOVERY
                    exit_stage_idx[i] = stage_idx

                elif roll < (FAIL_OUTCOME_PROBS["total_loss"] +
                             FAIL_OUTCOME_PROBS["partial_recovery"] +
                             FAIL_OUTCOME_PROBS["bridge_survives"]):
                    # Bridge round — dilutive but survives to attempt next stage
                    bridge_size = np.exp(rng.normal(rs_mu - 0.7, rs_sigma * 0.8))
                    bridge_pre = np.exp(rng.normal(pm_mu - 0.3, pm_sigma * 0.8))
                    bridge_pre = max(bridge_pre, bridge_size * 1.2)
                    bridge_dilution = bridge_size / (bridge_pre + bridge_size)
                    bridged_own = max(current_own * (1 - bridge_dilution), 0.001)
                    ownership_paths[i, stage_idx] = bridged_own
                    # Propagate ownership forward so the next stage transition
                    # has a valid starting point (bridge doesn't advance the
                    # company but the loop does)
                    if stage_idx + 1 < len(STAGE_ORDER):
                        ownership_paths[i, stage_idx + 1] = bridged_own
                    total_raised[i] += bridge_size
                    bridge_rounds[i] += 1
                    last_postmoney[i] = bridge_pre + bridge_size
                    cumulative_time_months[i] += max(rng.normal(12, 4), 4)

                else:
                    # [ASSUMPTION] Lingered, then small exit
                    small_val = last_postmoney[i] if last_postmoney[i] > 0 else (
                        np.exp(rng.normal(pm_mu - 1.0, pm_sigma)))
                    small_mult = rng.uniform(0.3, 1.5)
                    exit_moic_direct[i] = small_val * small_mult * current_own
                    outcome_code[i] = OUTCOME_LATE_SMALL_EXIT
                    exit_stage_idx[i] = stage_idx

    # --- Finalize: companies still running at the end get a terminal exit ---
    for i in range(n_simulations):
        if outcome_code[i] == OUTCOME_RUNNING:
            valid = ownership_paths[i, ~np.isnan(ownership_paths[i, :])]
            if len(valid) > 0:
                outcome_code[i] = OUTCOME_FULL_EXIT
                # Terminal exit valuation handled by monte_carlo layer
            else:
                outcome_code[i] = OUTCOME_TOTAL_LOSS

    # Build final ownership array
    final_ownership = np.full(n_simulations, np.nan)
    for i in range(n_simulations):
        if outcome_code[i] in (OUTCOME_FULL_EXIT, OUTCOME_RUNNING):
            valid = ownership_paths[i, ~np.isnan(ownership_paths[i, :])]
            if len(valid) > 0:
                final_ownership[i] = valid[-1]

    alive_mask = outcome_code == OUTCOME_FULL_EXIT
    any_positive = np.isin(outcome_code, [
        OUTCOME_FULL_EXIT, OUTCOME_STAGE_EXIT,
        OUTCOME_PARTIAL_RECOVERY, OUTCOME_LATE_SMALL_EXIT
    ])

    outcome_counts = {
        "full_exit": int((outcome_code == OUTCOME_FULL_EXIT).sum()),
        "stage_exit": int((outcome_code == OUTCOME_STAGE_EXIT).sum()),
        "partial_recovery": int((outcome_code == OUTCOME_PARTIAL_RECOVERY).sum()),
        "late_small_exit": int((outcome_code == OUTCOME_LATE_SMALL_EXIT).sum()),
        "total_loss": int((outcome_code == OUTCOME_TOTAL_LOSS).sum()),
    }

    return {
        "ownership_paths": ownership_paths,
        "final_ownership": final_ownership,
        "alive_mask": alive_mask,
        "any_positive_mask": any_positive,
        "outcome_code": outcome_code,
        "exit_moic_direct": exit_moic_direct,
        "exit_stage_idx": exit_stage_idx,
        "survival_rate": float(alive_mask.sum() / n_simulations),
        "any_positive_rate": float(any_positive.sum() / n_simulations),
        "total_raised": total_raised,
        "rounds_completed": rounds_completed,
        "bridge_rounds": bridge_rounds,
        "cumulative_time_months": cumulative_time_months,
        "outcome_counts": outcome_counts,
        "trl_diagnostics": {
            "trl": trl,
            "survival_penalty": survival_penalty,
            "capital_multiplier": capital_mult,
            "extra_bridge_prob": bridge_prob,
            "exit_multiple_discount": trl_mods["exit_multiple_discount"],
        },
        "stats": {
            "ownership_p10": float(np.nanpercentile(final_ownership[alive_mask], 10)) if alive_mask.any() else 0,
            "ownership_p25": float(np.nanpercentile(final_ownership[alive_mask], 25)) if alive_mask.any() else 0,
            "ownership_p50": float(np.nanpercentile(final_ownership[alive_mask], 50)) if alive_mask.any() else 0,
            "ownership_p75": float(np.nanpercentile(final_ownership[alive_mask], 75)) if alive_mask.any() else 0,
            "ownership_p90": float(np.nanpercentile(final_ownership[alive_mask], 90)) if alive_mask.any() else 0,
            "median_rounds": float(np.median(rounds_completed[alive_mask])) if alive_mask.any() else 0,
            "median_bridge_rounds": float(np.median(bridge_rounds[alive_mask])) if alive_mask.any() else 0,
            "median_total_raised": float(np.median(total_raised[alive_mask])) if alive_mask.any() else 0,
        },
    }
