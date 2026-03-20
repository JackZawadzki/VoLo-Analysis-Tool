"""
Deal Report Orchestrator.

Chains Monte Carlo simulation + carbon assessment + S-curve comparison +
sensitivity analysis + founder projection overlay + fund impact into a
unified deal report. All values are computed — zero hallucination.

Valuation comps from Damodaran are used to derive exit multiple ranges
for the Monte Carlo, not just displayed decoratively.
"""

import time
import logging
import numpy as np
from typing import Optional, Tuple

from .monte_carlo import run_simulation
from .adoption import DEFAULT_BASS_PARAMS, bass_diffusion_cumulative
from .valuation_comps import get_comps_for_archetype
from .market_sizing import get_market_sizing
from .rvm_carbon import (
    CompanyModel, VolumeInputs, OperatingCarbonInputs,
    EmbodiedCarbonInputs, PortfolioInputs,
    build_carbon_intermediates, compute_portfolio_outputs,
    get_risk_divisor_for_trl, get_carbon_defaults,
)
from .position_sizing import optimize_position_size

logger = logging.getLogger(__name__)


def generate_deal_report(
    company_name: str,
    archetype: str,
    tam_millions: float,
    trl: int,
    entry_stage: str,
    check_size_millions: float,
    pre_money_millions: float,
    sector_profile: str = "Energy + Deep Tech",
    carta_data: dict = None,
    penetration_share: Tuple[float, float] = (0.01, 0.05),
    exit_multiple_range: Tuple[float, float] = None,
    exit_year_range: Tuple[int, int] = (5, 10),
    n_simulations: int = 5000,
    random_seed: Optional[int] = None,
    volume: dict = None,
    op_carbon: dict = None,
    emb_carbon: dict = None,
    portfolio: dict = None,
    risk_divisor: int = None,
    founder_revenue_projections: list = None,
    founder_volume_projections: list = None,
    founder_tam_claim: float = None,
    extraction_source: str = None,
    extraction_confidence: dict = None,
    custom_bass_p: Optional[Tuple[float, float]] = None,
    custom_bass_q: Optional[Tuple[float, float]] = None,
    custom_maturity: Optional[str] = None,
    custom_inflection_year: Optional[int] = None,
    comps_data: dict = None,
    technology_description: str = None,
    financial_model: dict = None,
    fund_size_m: float = 100.0,
    n_deals: int = 25,
    mgmt_fee_pct: float = 2.0,
    reserve_pct: float = 30.0,
    max_concentration_pct: float = 15.0,
    round_size_m: float = None,
    committed_deals: list = None,
    deal_commitment_type: str = "first_check",
    deal_follow_on_year: int = 2,
) -> dict:
    start = time.time()
    report = {}
    committed_deals = committed_deals or []

    if carta_data is None:
        carta_data = {}
    if volume is None:
        volume = {}
    if op_carbon is None:
        op_carbon = {}
    if emb_carbon is None:
        emb_carbon = {}
    if portfolio is None:
        portfolio = {}

    # ── Auto-derive risk divisor from TRL ─────────────────────────────────────
    if risk_divisor is None:
        risk_divisor = get_risk_divisor_for_trl(trl)

    # ── Derive exit multiples from comps if not provided ──────────────────────
    valuation = {}
    comps_derived_multiples = None
    if comps_data:
        valuation = get_comps_for_archetype(comps_data, archetype)
        if valuation.get("acq_ev_ebitda_range") and exit_multiple_range is None:
            acq_range = valuation["acq_ev_ebitda_range"]
            ipo_mean = valuation.get("ipo_ev_ebitda_mean")
            floor = max(6.0, acq_range[0] * 0.8)
            ceiling = acq_range[1] if ipo_mean is None else max(acq_range[1], ipo_mean)
            exit_multiple_range = (round(floor, 1), round(min(ceiling, 60.0), 1))
            comps_derived_multiples = exit_multiple_range

    if exit_multiple_range is None:
        exit_multiple_range = (12.0, 30.0)

    effective_round_size = round_size_m if round_size_m and round_size_m > 0 else check_size_millions
    post_money = pre_money_millions + effective_round_size
    entry_ownership = check_size_millions / post_money if post_money > 0 else 0

    # ── Auto-fill carbon defaults from archetype ──────────────────────────────
    carbon_defaults = get_carbon_defaults(archetype)
    if not op_carbon.get("displaced_resource"):
        op_carbon["displaced_resource"] = carbon_defaults.get("displaced_resource", "US electricity")
    if not op_carbon.get("baseline_lifetime_prod"):
        op_carbon["baseline_lifetime_prod"] = carbon_defaults.get("baseline_lifetime_prod", 1.0)
    if not op_carbon.get("range_improvement"):
        op_carbon["range_improvement"] = carbon_defaults.get("range_improvement", 1.0)

    # Auto-fill portfolio from deal terms
    if not portfolio.get("volo_pct"):
        portfolio["volo_pct"] = entry_ownership
    if not portfolio.get("volo_investment"):
        portfolio["volo_investment"] = check_size_millions * 1_000_000

    # ── Pre-compute founder revenue in $M for the simulation ────────────────
    # If a financial model was uploaded, extract revenue by fiscal year and
    # convert to $M.  These become the anchor trajectory for the Monte Carlo.
    _sim_founder_rev_m = None
    if financial_model and isinstance(financial_model, dict):
        fm_fin = financial_model.get("financials", {})
        fm_years = financial_model.get("fiscal_years", [])
        fm_rev = fm_fin.get("revenue", {})
        if fm_rev and fm_years:
            rev_list = []
            for y in fm_years:
                val = fm_rev.get(str(y)) or fm_rev.get(y) or fm_rev.get(
                    int(y) if isinstance(y, str) else str(y)) or 0
                if isinstance(val, dict):
                    val = val.get("value", 0) or 0
                rev_list.append(float(val or 0) / 1_000_000)
            if any(v > 0 for v in rev_list):
                _sim_founder_rev_m = rev_list
    # Fallback: use wizard-provided founder projections (already in $M)
    if (not _sim_founder_rev_m) and founder_revenue_projections:
        if any(float(v or 0) > 0 for v in founder_revenue_projections):
            _sim_founder_rev_m = [float(v) for v in founder_revenue_projections]

    # ── Section 1: Monte Carlo simulation ─────────────────────────────────────
    sim = run_simulation(
        archetype=archetype,
        tam_millions=tam_millions,
        trl=trl,
        entry_stage=entry_stage,
        check_size_millions=check_size_millions,
        pre_money_millions=pre_money_millions,
        sector_profile=sector_profile,
        carta_data=carta_data,
        penetration_share=penetration_share,
        exit_multiple_range=exit_multiple_range,
        exit_year_range=exit_year_range,
        n_simulations=n_simulations,
        random_seed=random_seed,
        custom_bass_p=custom_bass_p,
        custom_bass_q=custom_bass_q,
        custom_maturity=custom_maturity,
        custom_inflection_year=custom_inflection_year,
        founder_revenue_projections_m=_sim_founder_rev_m,
        round_size_m=round_size_m,
    )

    # ── Section 2: Carbon assessment ──────────────────────────────────────────
    carbon = _run_carbon_assessment(
        company_name, volume, op_carbon, emb_carbon,
        portfolio, risk_divisor,
    )

    # ── Section 3: S-curve data ───────────────────────────────────────────────
    scurve = _build_scurve_data(archetype, tam_millions, custom_bass_p, custom_bass_q)

    # ── Auto-fill founder projections from financial model if not provided ───
    scenario_revenue = {}
    if financial_model and isinstance(financial_model, dict):
        fm_fin = financial_model.get("financials", {})
        fm_years = financial_model.get("fiscal_years", [])
        if (not founder_revenue_projections or all(v == 0 for v in founder_revenue_projections)):
            fm_rev = fm_fin.get("revenue", {})
            if fm_rev and fm_years:
                founder_revenue_projections = [
                    (fm_rev.get(str(y), fm_rev.get(y, 0)) or 0) / 1_000_000
                    for y in fm_years
                ]
        if (not founder_volume_projections or all(v == 0 for v in founder_volume_projections)):
            fm_units = financial_model.get("units", {})
            if fm_units and fm_years:
                first_unit_key = next(iter(fm_units), None)
                if first_unit_key:
                    series = fm_units[first_unit_key]
                    founder_volume_projections = []
                    for y in fm_years:
                        entry = series.get(str(y), series.get(y, 0))
                        val = entry.get("value", 0) if isinstance(entry, dict) else (entry or 0)
                        founder_volume_projections.append(val)

        # Extract bear/bull scenario revenue for cone overlay
        fm_scenarios = financial_model.get("scenarios")
        if fm_scenarios and isinstance(fm_scenarios, dict):
            for sc_name in ("bear", "bull"):
                sc_data = fm_scenarios.get(sc_name, {})
                sc_rev = sc_data.get("financials", {}).get("revenue", {})
                if sc_rev and fm_years:
                    scenario_revenue[sc_name] = [
                        (sc_rev.get(str(y), sc_rev.get(y, 0)) or 0) / 1_000_000
                        for y in fm_years
                    ]

    # ── Section 4: Founder projection comparison ──────────────────────────────
    raw_rev_traj = sim.get("revenue_trajectories", {})
    pctls = raw_rev_traj.get("percentiles", {})
    normalized_rev = {
        "median": pctls.get("p50", []),
        "p25": pctls.get("p25", []),
        "p75": pctls.get("p75", []),
        "p10": pctls.get("p10", []),
        "p90": pctls.get("p90", []),
        "source": raw_rev_traj.get("source", "scurve_derived"),
        "forward_look_years": raw_rev_traj.get("forward_look_years"),
        "forward_confidence": raw_rev_traj.get("forward_confidence"),
    }
    founder_comparison = _build_founder_comparison(
        founder_revenue_projections, founder_volume_projections,
        normalized_rev, scurve,
    )
    if scenario_revenue:
        founder_comparison["scenario_revenue"] = scenario_revenue

    # ── Section 5: Sensitivity analysis ───────────────────────────────────────
    sensitivity = _compute_sensitivity(
        archetype=archetype, tam_millions=tam_millions, trl=trl,
        entry_stage=entry_stage, check_size_millions=check_size_millions,
        pre_money_millions=pre_money_millions, sector_profile=sector_profile,
        carta_data=carta_data, penetration_share=penetration_share,
        exit_multiple_range=exit_multiple_range, exit_year_range=exit_year_range,
        random_seed=random_seed or 42,
        base_moic=sim.get("moic_unconditional", {}).get("expected", 0),
        base_p3x=sim.get("probability", {}).get("gt_3x", 0),
        round_size_m=round_size_m,
    )

    # ── Section 6: Market sizing ──────────────────────────────────────────────
    market = get_market_sizing(archetype, tam_millions)

    # ── Section 7: Portfolio impact ────────────────────────────────────────────
    portfolio_impact = _run_portfolio_impact(
        company_name=company_name,
        check_size_millions=check_size_millions,
        survival_rate=sim.get("summary", {}).get("survival_rate", 0.3),
        moic_conditional_mean=sim.get("moic_conditional", {}).get("mean", 3.0),
        exit_year_range=exit_year_range,
        committed_deals=committed_deals,
        deal_commitment_type=deal_commitment_type,
        deal_follow_on_year=deal_follow_on_year,
    )

    # ── Assemble report ───────────────────────────────────────────────────────
    report["deal_overview"] = {
        "company_name": company_name,
        "technology_description": technology_description,
        "archetype": archetype,
        "trl": trl,
        "trl_label": sim.get("trl_impact", {}).get("label", f"TRL {trl}"),
        "entry_stage": entry_stage,
        "check_size_millions": check_size_millions,
        "round_size_millions": effective_round_size,
        "pre_money_millions": pre_money_millions,
        "post_money_millions": post_money,
        "entry_ownership_pct": round(entry_ownership * 100, 2),
        "sector_profile": sector_profile,
        "tam_millions": tam_millions,
        "penetration_share": list(penetration_share) if penetration_share else [0.01, 0.05],
        "exit_multiple_range": list(exit_multiple_range),
        "exit_year_range": list(exit_year_range) if exit_year_range else [5, 10],
        "fund_size_m": fund_size_m,
        "comps_derived_multiples": comps_derived_multiples is not None,
        "extraction_source": extraction_source,
        "extraction_confidence": extraction_confidence,
    }

    survival = sim.get("summary", {}).get("survival_rate")
    meaningful_exit = sim.get("summary", {}).get("meaningful_exit_rate")

    report["hero_metrics"] = {
        "expected_moic": sim.get("moic_unconditional", {}).get("expected"),
        "p_gt_3x": sim.get("probability", {}).get("gt_3x"),
        "expected_irr": sim.get("expected_irr"),
        "survival_rate": survival,
    }

    report["simulation"] = {
        "outcome_breakdown": sim.get("outcome_breakdown"),
        "moic_unconditional": sim.get("moic_unconditional"),
        "moic_conditional": sim.get("moic_conditional"),
        "moic_meaningful": sim.get("moic_meaningful"),
        "irr_conditional": sim.get("irr_conditional"),
        "expected_irr": sim.get("expected_irr"),
        "probability": sim.get("probability"),
        "moic_histogram": sim.get("moic_histogram"),
        "revenue_trajectories": normalized_rev,
        "variance_drivers": sim.get("variance_drivers"),
        "variance_explanations": sim.get("variance_explanations"),
        "n_simulations": n_simulations,
        "survival_rate": survival,
        "meaningful_exit_rate": meaningful_exit,
    }

    report["founder_comparison"] = founder_comparison

    report["sensitivity"] = sensitivity

    report["carbon_impact"] = carbon
    report["carbon_impact"]["risk_divisor_used"] = risk_divisor
    report["carbon_impact"]["risk_divisor_source"] = f"Auto-derived from TRL {trl}"

    report["valuation_context"] = valuation
    report["valuation_context"]["exit_multiples_used"] = list(exit_multiple_range)
    if comps_derived_multiples:
        report["valuation_context"]["multiples_source"] = "Derived from Damodaran industry comps"
    else:
        report["valuation_context"]["multiples_source"] = "User-specified"

    report["adoption_analysis"] = {
        "archetype": archetype,
        "scurve": scurve,
        "adoption_info": sim.get("adoption"),
        "adoption_curve": sim.get("adoption_curve"),
    }

    report["market_sizing"] = market

    report["risk_summary"] = {
        "trl_impact": sim.get("trl_impact"),
        "dilution": sim.get("dilution"),
        "variance_drivers": sim.get("variance_drivers"),
        "variance_explanations": sim.get("variance_explanations"),
    }

    report["portfolio_impact"] = portfolio_impact

    # ── Section 8: Position sizing optimization ────────────────────────────
    raw_moic = sim.get("_raw_moic", [])
    if raw_moic and len(raw_moic) > 100:
        try:
            sizing = optimize_position_size(
                moic_distribution=raw_moic,
                check_size_m=check_size_millions,
                pre_money_m=pre_money_millions,
                fund_size_m=fund_size_m,
                n_deals=n_deals,
                mgmt_fee_pct=mgmt_fee_pct,
                reserve_pct=reserve_pct,
                max_concentration_pct=max_concentration_pct,
                entry_stage=entry_stage,
                company_name=company_name,
                survival_rate=sim.get("summary", {}).get("survival_rate", 0.3),
                moic_conditional_mean=sim.get("moic_conditional", {}).get("mean", 3.0),
                exit_year_range=exit_year_range,
                round_size_m=round_size_m,
                committed_deals=committed_deals,
                deal_commitment_type=deal_commitment_type,
                deal_follow_on_year=deal_follow_on_year,
            )
            report["position_sizing"] = {
                "has_data": True,
                **sizing,
            }
        except Exception as e:
            logger.warning("Position sizing failed: %s", e)
            report["position_sizing"] = {"has_data": False, "error": str(e)}
    else:
        report["position_sizing"] = {"has_data": False}

    if financial_model and isinstance(financial_model, dict):
        fm_provenance = []
        for metric_name, metric_data in financial_model.get("financials", {}).items():
            if isinstance(metric_data, dict):
                for yr, val in metric_data.items():
                    fm_provenance.append({
                        "metric": metric_name,
                        "year": yr,
                        "value_usd": val,
                    })

        fm_scenarios = financial_model.get("scenarios")
        fm_detected = financial_model.get("detected_scenarios", ["base"])
        has_multi_scenario = fm_scenarios and len(fm_detected) > 1

        report["financial_model"] = {
            "has_data": True,
            "financials": financial_model.get("financials", {}),
            "units": financial_model.get("units", {}),
            "fiscal_years": financial_model.get("fiscal_years", []),
            "model_summary": financial_model.get("model_summary", {}),
            "provenance_summary": fm_provenance[:50],
            "file_name": financial_model.get("file_name", ""),
            "scale_info": financial_model.get("scale_info", ""),
            "records_count": financial_model.get("records_count", 0),
            "scenarios": fm_scenarios if has_multi_scenario else None,
            "detected_scenarios": fm_detected,
            "has_multi_scenario": has_multi_scenario,
            "primary_scenario": financial_model.get("primary_scenario", "base"),
        }
    else:
        report["financial_model"] = {"has_data": False}

    report["audit_trail"] = {
        "extraction_source": extraction_source,
        "extraction_confidence": extraction_confidence,
        "n_simulations": n_simulations,
        "random_seed": random_seed,
        "exit_multiple_range": list(exit_multiple_range),
        "risk_divisor": risk_divisor,
        "computation_time_ms": round((time.time() - start) * 1000, 1),
        "financial_model_file": financial_model.get("file_name", "") if financial_model else "",
        "financial_model_scale": financial_model.get("scale_info", "") if financial_model else "",
    }

    report["_raw_moic"] = sim.get("_raw_moic", [])
    report["_raw_exit_years"] = sim.get("_raw_exit_years", [])

    return report


def _build_founder_comparison(
    founder_revenue, founder_volumes, sim_revenue_trajectories, scurve_data
) -> dict:
    """
    Compare founder's stated projections against simulated trajectories.
    Returns year-by-year divergence (signed %) rather than an opaque 0-100 score.
    """
    result = {"has_data": False, "revenue": None, "volumes": None}

    if founder_revenue and sim_revenue_trajectories:
        sim_rev = sim_revenue_trajectories
        n = min(len(founder_revenue), len(sim_rev.get("median", [])))
        if n > 0:
            fr = [v if v else 0 for v in founder_revenue[:n]]
            sm = sim_rev["median"][:n]
            sp25 = sim_rev.get("p25", sm)[:n]
            sp75 = sim_rev.get("p75", sm)[:n]
            sp10 = sim_rev.get("p10", sp25)[:n]
            sp90 = sim_rev.get("p90", sp75)[:n]

            divergence = []
            for i in range(n):
                med = sm[i] if i < len(sm) else 0
                pct_diff = ((fr[i] - med) / med * 100) if med != 0 else 0
                in_band = (sp25[i] if i < len(sp25) else 0) <= fr[i] <= (sp75[i] if i < len(sp75) else 0)
                divergence.append({
                    "year": i + 1,
                    "founder": round(fr[i], 2),
                    "simulated_median": round(med, 2),
                    "simulated_p25": round(sp25[i] if i < len(sp25) else 0, 2),
                    "simulated_p75": round(sp75[i] if i < len(sp75) else 0, 2),
                    "divergence_pct": round(pct_diff, 1),
                    "in_band": in_band,
                })

            avg_div = sum(d["divergence_pct"] for d in divergence) / len(divergence) if divergence else 0
            in_band_pct = sum(1 for d in divergence if d["in_band"]) / len(divergence) * 100 if divergence else 0

            position = "within band"
            if avg_div > 30:
                position = "significantly above simulated median"
            elif avg_div > 10:
                position = "above simulated median"
            elif avg_div < -30:
                position = "significantly below simulated median"
            elif avg_div < -10:
                position = "below simulated median"

            narrative = (
                f"Founder projects revenue that is on average {abs(avg_div):.0f}% "
                f"{'above' if avg_div >= 0 else 'below'} the simulated median. "
                f"{in_band_pct:.0f}% of projected years fall within the p25-p75 simulation band."
            )

            result["revenue"] = {
                "year_by_year": divergence,
                "avg_divergence_pct": round(avg_div, 1),
                "in_band_pct": round(in_band_pct, 1),
                "position": position,
                "narrative": narrative,
                "simulated_band": {
                    "p10": [round(v, 2) for v in (sp10 if isinstance(sp10, list) else [])],
                    "p25": [round(v, 2) for v in (sp25 if isinstance(sp25, list) else [])],
                    "median": [round(v, 2) for v in (sm if isinstance(sm, list) else [])],
                    "p75": [round(v, 2) for v in (sp75 if isinstance(sp75, list) else [])],
                    "p90": [round(v, 2) for v in (sp90 if isinstance(sp90, list) else [])],
                },
            }
            result["has_data"] = True

    if founder_volumes and scurve_data and scurve_data.get("median"):
        n = min(len(founder_volumes), len(scurve_data["median"]) - 1)
        if n > 0:
            fv = founder_volumes[:n]
            sm = scurve_data["median"][1:n+1]
            sp25 = scurve_data["p25"][1:n+1]
            sp75 = scurve_data["p75"][1:n+1]

            vol_div = []
            for i in range(n):
                med = sm[i] if i < len(sm) else 0
                pct_diff = ((fv[i] - med) / med * 100) if med != 0 else 0
                in_band = (sp25[i] if i < len(sp25) else 0) <= fv[i] <= (sp75[i] if i < len(sp75) else 0)
                vol_div.append({
                    "year": i + 1,
                    "founder": round(fv[i], 2),
                    "simulated_median": round(med, 2),
                    "divergence_pct": round(pct_diff, 1),
                    "in_band": in_band,
                })

            result["volumes"] = {"year_by_year": vol_div}
            result["has_data"] = True

    return result


def _compute_sensitivity(
    archetype, tam_millions, trl, entry_stage, check_size_millions,
    pre_money_millions, sector_profile, carta_data, penetration_share,
    exit_multiple_range, exit_year_range, random_seed,
    base_moic, base_p3x, round_size_m=None,
) -> dict:
    """
    Perturb each key input +/-20% and re-run a fast simulation to measure
    impact on Expected MOIC and P(>3x). Returns tornado chart data.
    """
    fast_n = 1000
    perturbations = []

    inputs_to_test = [
        ("TAM", "tam_millions", tam_millions, 0.2),
        ("Check Size", "check_size_millions", check_size_millions, 0.2),
        ("Pre-Money", "pre_money_millions", pre_money_millions, 0.2),
        ("Penetration Low", "pen_low", penetration_share[0], 0.3),
        ("Penetration High", "pen_high", penetration_share[1], 0.3),
        ("Exit Multiple Low", "mult_low", exit_multiple_range[0], 0.25),
        ("Exit Multiple High", "mult_high", exit_multiple_range[1], 0.25),
    ]

    for label, key, base_val, delta_pct in inputs_to_test:
        if base_val == 0:
            continue

        for direction in [-1, 1]:
            perturbed_val = base_val * (1 + direction * delta_pct)

            kwargs = dict(
                archetype=archetype, tam_millions=tam_millions, trl=trl,
                entry_stage=entry_stage, check_size_millions=check_size_millions,
                pre_money_millions=pre_money_millions, sector_profile=sector_profile,
                carta_data=carta_data, penetration_share=penetration_share,
                exit_multiple_range=exit_multiple_range, exit_year_range=exit_year_range,
                n_simulations=fast_n, random_seed=random_seed,
                round_size_m=round_size_m,
            )

            if key == "tam_millions":
                kwargs["tam_millions"] = perturbed_val
            elif key == "check_size_millions":
                kwargs["check_size_millions"] = perturbed_val
            elif key == "pre_money_millions":
                kwargs["pre_money_millions"] = max(1, perturbed_val)
            elif key == "pen_low":
                kwargs["penetration_share"] = (max(0.001, perturbed_val), penetration_share[1])
            elif key == "pen_high":
                kwargs["penetration_share"] = (penetration_share[0], max(0.002, perturbed_val))
            elif key == "mult_low":
                kwargs["exit_multiple_range"] = (max(1, perturbed_val), exit_multiple_range[1])
            elif key == "mult_high":
                kwargs["exit_multiple_range"] = (exit_multiple_range[0], max(2, perturbed_val))

            try:
                result = run_simulation(**kwargs)
                new_moic = result.get("moic_unconditional", {}).get("expected", 0)
                new_p3x = result.get("probability", {}).get("gt_3x", 0)
            except Exception as exc:
                logger.debug("Sensitivity perturbation failed for %s (%s): %s", label, direction, exc)
                new_moic = base_moic
                new_p3x = base_p3x

            perturbations.append({
                "input": label,
                "direction": "up" if direction > 0 else "down",
                "delta_pct": round(direction * delta_pct * 100),
                "base_value": round(base_val, 4),
                "perturbed_value": round(perturbed_val, 4),
                "moic_change": round(new_moic - base_moic, 3),
                "p3x_change": round((new_p3x or 0) - (base_p3x or 0), 4),
            })

    # Build tornado data: for each input, take the max absolute MOIC change
    tornado = {}
    for p in perturbations:
        key = p["input"]
        if key not in tornado:
            tornado[key] = {"input": key, "moic_down": 0, "moic_up": 0, "p3x_down": 0, "p3x_up": 0}
        if p["direction"] == "up":
            tornado[key]["moic_up"] = p["moic_change"]
            tornado[key]["p3x_up"] = p["p3x_change"]
        else:
            tornado[key]["moic_down"] = p["moic_change"]
            tornado[key]["p3x_down"] = p["p3x_change"]

    tornado_list = sorted(
        tornado.values(),
        key=lambda x: abs(x["moic_up"]) + abs(x["moic_down"]),
        reverse=True,
    )

    top_driver = tornado_list[0]["input"] if tornado_list else "N/A"
    top_impact = tornado_list[0]["moic_up"] if tornado_list else 0
    narrative = (
        f"Expected MOIC is most sensitive to {top_driver}, where a 20% change "
        f"produces a {abs(top_impact):.2f}x change in expected return."
    ) if tornado_list else ""

    return {
        "perturbations": perturbations,
        "tornado": tornado_list,
        "narrative": narrative,
        "base_moic": base_moic,
        "base_p3x": base_p3x,
    }


def _run_carbon_assessment(
    company_name, volume, op_carbon, emb_carbon, portfolio_inputs, risk_divisor,
) -> dict:
    try:
        launch_year = int(volume.get("commercial_launch_yr", 2024))
        year_vols = volume.get("year_volumes", [0.0] * 10)
        year_vols = (year_vols + [0.0] * 10)[:10]

        company = CompanyModel(
            company_name=company_name,
            stage=volume.get("stage", "Portfolio"),
            risk_adjustment_divisor=risk_divisor,
            volume=VolumeInputs(
                unit_definition=volume.get("unit_definition", ""),
                unit_service_life_yrs=int(volume.get("unit_service_life_yrs", 10)),
                tam_10y=float(volume.get("tam_10y", 0)),
                tam_units=volume.get("tam_units", ""),
                sam_10y=float(volume.get("sam_10y", 0)),
                sam_pct_of_tam=float(volume.get("sam_pct_of_tam", 0)),
                sam_explanation=volume.get("sam_explanation", ""),
                annual_retention_rate=float(volume.get("annual_retention_rate", 1.0)),
                commercial_launch_yr=launch_year,
                year_volumes=[float(x) for x in year_vols],
                n_ll_years=int(volume.get("n_ll_years", 10)),
            ),
            operating_carbon=OperatingCarbonInputs(
                displaced_resource=op_carbon.get("displaced_resource", "Global electricity"),
                baseline_lifetime_prod=float(op_carbon.get("baseline_lifetime_prod", 1.0)),
                specific_production_units=op_carbon.get("specific_production_units", ""),
                range_improvement=float(op_carbon.get("range_improvement", 1.0)),
                ci_year1_override=_opt_float(op_carbon.get("ci_year1_override")),
                ci_annual_decline=_opt_float(op_carbon.get("ci_annual_decline")),
            ),
            embodied_carbon=EmbodiedCarbonInputs(
                displaced_resource=emb_carbon.get("displaced_resource") or None,
                baseline_production=float(emb_carbon.get("baseline_production", 0.0)),
                specific_production_units=emb_carbon.get("specific_production_units", ""),
                range_improvement=float(emb_carbon.get("range_improvement", 0.0)),
                ci_year1_override=_opt_float(emb_carbon.get("ci_year1_override")),
                ci_annual_decline=_opt_float(emb_carbon.get("ci_annual_decline")),
            ),
            portfolio=PortfolioInputs(
                volo_pct=float(portfolio_inputs.get("volo_pct", 0.0)),
                volo_investment=float(portfolio_inputs.get("volo_investment", 0.0)),
            ),
        )

        inter = build_carbon_intermediates(company)
        out = compute_portfolio_outputs(company, inter)

        def _f(v):
            return round(v, 4) if v is not None else None

        return {
            "intermediates": {
                "jd": _f(inter.displaced_volume_per_unit),
                "operating_ci_series": [_f(x) for x in (inter.operating_ci_series or [])],
                "annual_operating": [_f(x) for x in (inter.annual_operating_impact or [])],
                "annual_embodied": [_f(x) for x in (inter.annual_embodied_impact or [])],
                "annual_lifecycle": [_f(x) for x in (inter.annual_lifecycle_impact or [])],
                "total_operating": _f(inter.total_operating_impact),
                "total_embodied": _f(inter.total_embodied_impact),
                "total_lifecycle": _f(inter.total_lifecycle_impact),
            },
            "outputs": {
                "company_tonnes": _f(out.company_tonnes),
                "volo_prorata": _f(out.volo_tonnes_prorata),
                "volo_risk_adj": _f(out.volo_tonnes_risk_adjusted),
                "tonnes_per_dollar": _f(out.volo_tonnes_per_dollar),
                "risk_adj_tpd": _f(out.risk_adj_tonnes_per_dollar),
            },
        }
    except Exception as e:
        return {"intermediates": {}, "outputs": {}, "error": str(e)}


def _build_scurve_data(archetype, tam_millions, custom_p=None, custom_q=None) -> dict:
    horizon = 15
    t = np.arange(0, horizon + 1, dtype=float)
    rng = np.random.default_rng(42)

    if custom_p and custom_q:
        p_mean, p_std = custom_p
        q_mean, q_std = custom_q
    elif archetype in DEFAULT_BASS_PARAMS:
        params = DEFAULT_BASS_PARAMS[archetype]
        p_mean, p_std = params["p"]
        q_mean, q_std = params["q"]
    else:
        p_mean, p_std = 0.005, 0.002
        q_mean, q_std = 0.30, 0.08

    n_samples = 500
    p_draws = np.clip(rng.normal(p_mean, p_std, n_samples), 0.0005, 0.05)
    q_draws = np.clip(rng.normal(q_mean, q_std, n_samples), 0.02, 0.8)
    curves = np.zeros((n_samples, horizon + 1))
    for i in range(n_samples):
        curves[i] = bass_diffusion_cumulative(t, p_draws[i], q_draws[i], tam_millions)

    return {
        "years": list(range(horizon + 1)),
        "p10": [round(float(v), 2) for v in np.percentile(curves, 10, axis=0)],
        "p25": [round(float(v), 2) for v in np.percentile(curves, 25, axis=0)],
        "median": [round(float(v), 2) for v in np.percentile(curves, 50, axis=0)],
        "p75": [round(float(v), 2) for v in np.percentile(curves, 75, axis=0)],
        "p90": [round(float(v), 2) for v in np.percentile(curves, 90, axis=0)],
        "bass_p_mean": p_mean,
        "bass_q_mean": q_mean,
    }


def _opt_float(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _run_portfolio_impact(
    company_name: str,
    check_size_millions: float,
    survival_rate: float,
    moic_conditional_mean: float,
    exit_year_range: Tuple[int, int] = (5, 10),
    committed_deals: list = None,
    deal_commitment_type: str = "first_check",
    deal_follow_on_year: int = 2,
) -> dict:
    """Run portfolio-level deal impact using VCSimulator.

    When committed_deals is provided, the baseline portfolio includes
    those deals (replacing random company slots), so the impact is
    measured against the *running* fund — not a generic baseline.
    """
    try:
        from pathlib import Path
        from .portfolio.simulator import VCSimulator
        from .portfolio.config import DealConfig, load_strategy
        from .portfolio.benchmarks import load_benchmarks

        configs_dir = Path(__file__).resolve().parent.parent.parent / "configs"
        strategy_path = configs_dir / "strategy.json"
        bench_path = configs_dir / "carta_benchmarks.json"

        if not strategy_path.exists():
            return {"error": "Portfolio strategy config not found", "has_data": False}

        cfg = load_strategy(str(strategy_path))
        bench = load_benchmarks(str(bench_path)) if bench_path.exists() else None

        exit_lo, exit_hi = exit_year_range
        exit_mode = (exit_lo + exit_hi) / 2.0

        deal = DealConfig(
            name=company_name or "Proposed Deal",
            cap_multiple=max(moic_conditional_mean, 1.0),
            success_prob=min(max(survival_rate, 0.01), 0.99),
            failure_multiple=0.0,
            exit_year_mode="triangular",
            exit_year=int(exit_mode),
            exit_year_triangular={
                "low": float(exit_lo),
                "mode": exit_mode,
                "high": float(exit_hi),
            },
            check_size=check_size_millions * 1_000_000,
            follow_on_allowed=True,
            metric=cfg.mode,
        )

        sim = VCSimulator(cfg, bench)
        result = sim.deal_impact(deal, n_portfolios=2000, seed=int(cfg.seed),
                                 committed_deals=committed_deals or [],
                                 deal_commitment_type=deal_commitment_type,
                                 deal_follow_on_year=deal_follow_on_year)
        result["has_data"] = True

        n_committed = result.get("n_committed_deals", 0)
        narrative_parts = []
        if n_committed > 0:
            narrative_parts.append(
                f"Portfolio baseline includes {n_committed} committed deal{'s' if n_committed > 1 else ''}"
            )

        tvpi_lift = result.get("tvpi_mean_lift", 0)
        irr_lift = result.get("irr_mean_lift", 0)
        if tvpi_lift > 0:
            narrative_parts.append(
                f"Adding this deal lifts expected portfolio TVPI by +{tvpi_lift:.3f}x "
                f"(from {result['tvpi_base_mean']:.2f}x to {result['tvpi_new_mean']:.2f}x)"
            )
        else:
            narrative_parts.append(
                f"This deal reduces expected portfolio TVPI by {tvpi_lift:.3f}x "
                f"(from {result['tvpi_base_mean']:.2f}x to {result['tvpi_new_mean']:.2f}x)"
            )
        if irr_lift != 0:
            narrative_parts.append(
                f"IRR shifts by {irr_lift*100:+.1f}pp "
                f"(median {result['irr_base_p50']*100:.1f}% to {result['irr_new_p50']*100:.1f}%)"
            )
        result["narrative"] = ". ".join(narrative_parts) + "."
        return result

    except Exception as exc:
        logger.warning("Portfolio impact failed: %s", exc)
        return {"error": str(exc), "has_data": False}
