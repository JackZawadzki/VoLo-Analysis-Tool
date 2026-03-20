"""Strategy and deal configuration dataclasses for portfolio simulation."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional
import json

Mode = Literal["ev_rev", "ev_ebitda"]


@dataclass(frozen=True)
class EVRevConfig:
    mean_sw: float
    mean_hw: float
    log_sigma: float
    sigma_scale: float


@dataclass(frozen=True)
class EVEbitdaConfig:
    mean_sw: float
    mean_hw: float
    log_sigma: float
    sigma_scale: float
    ebitda_margin_start: float
    ebitda_margin_end: float
    ebitda_margin_ramp_years: int


@dataclass(frozen=True)
class FailureModelConfig:
    ycl_base_fail_p0: float
    ycl_slopes: Dict[str, Dict[str, float]]
    stage_base_fail: Dict[str, float]
    stage_thresholds_years_since_launch: Dict[str, float]


@dataclass(frozen=True)
class StrategyConfig:
    fund_size: float
    mgmt_fee_pct: float
    reserve_pct: float
    reserve_conc: float
    recycle_pct: float
    nc: int
    years: int
    seed: int
    mode: Mode
    min_check: float
    prelaunch_check_discount: float
    ownership_cap: float
    initial_ownership_target: float
    ev_rev: EVRevConfig
    ev_ebitda: EVEbitdaConfig
    mark_stage_mult: List[float]
    growth: Dict[str, float]
    failure_model: FailureModelConfig
    nav_cost_floor: float  # Active companies marked at least invested × this factor


@dataclass(frozen=True)
class DealConfig:
    name: str
    cap_multiple: float
    success_prob: float
    failure_multiple: float
    exit_year_mode: Literal["fixed", "triangular"]
    exit_year: int
    exit_year_triangular: Dict[str, float]
    check_size: float
    follow_on_allowed: bool
    metric: Mode
    # Optional: full MOIC distribution from Monte Carlo.
    # When provided, deal_impact samples from this instead of the
    # binary (success_prob, cap_multiple) model, giving realistic
    # deal-level variance and correct concentration risk.
    moic_distribution: Optional[List[float]] = field(default=None, repr=False)


def _parse_strategy_dict(d: dict) -> StrategyConfig:
    """Parse a raw dict into StrategyConfig."""
    years = int(d["years"])
    mark = d.get("mark_stage_mult", [0.6]*2 + [0.75] + [0.9] + [1.0]*(years-4))
    if len(mark) != years:
        mark = mark[:years] if len(mark) > years else mark + [1.0]*(years - len(mark))

    evr = d.get("ev_rev", {})
    eve = d.get("ev_ebitda", {})
    fm = d.get("failure_model", {})

    return StrategyConfig(
        fund_size=float(d.get("fund_size", 130_000_000)),
        mgmt_fee_pct=float(d.get("mgmt_fee_pct", 21.0)),
        reserve_pct=float(d.get("reserve_pct", 30.0)),
        reserve_conc=float(d.get("reserve_conc", 1.0)),
        recycle_pct=float(d.get("recycle_pct", 0.0)),
        nc=int(d.get("nc", 20)),
        years=years,
        seed=int(d.get("seed", 424242)),
        mode=d.get("mode", "ev_rev"),
        min_check=float(d.get("min_check", 500_000)),
        prelaunch_check_discount=float(d.get("prelaunch_check_discount", 0.3)),
        ownership_cap=float(d.get("ownership_cap", 0.6)),
        initial_ownership_target=float(d.get("initial_ownership_target", 0.10)),
        ev_rev=EVRevConfig(
            float(evr.get("mean_sw", 6.0)),
            float(evr.get("mean_hw", 3.5)),
            float(evr.get("log_sigma", 0.7)),
            float(evr.get("sigma_scale", 1.2)),
        ),
        ev_ebitda=EVEbitdaConfig(
            float(eve.get("mean_sw", 16.0)),
            float(eve.get("mean_hw", 11.0)),
            float(eve.get("log_sigma", 0.55)),
            float(eve.get("sigma_scale", 1.1)),
            float(eve.get("ebitda_margin_start", 0.05)),
            float(eve.get("ebitda_margin_end", 0.25)),
            int(eve.get("ebitda_margin_ramp_years", 6)),
        ),
        mark_stage_mult=[float(x) for x in mark],
        growth={k: float(v) for k, v in d.get("growth", {"mscale_sw": 1.3, "mscale_hw": 1.0}).items()},
        nav_cost_floor=float(d.get("nav_cost_floor", 0.0)),
        failure_model=FailureModelConfig(
            ycl_base_fail_p0=float(fm.get("ycl_base_fail_p0", 0.68)),
            ycl_slopes=fm.get("ycl_slopes", {
                "software": {"pos": 0.06, "neg": -0.05},
                "hardware": {"pos": 0.07, "neg": -0.04},
            }),
            stage_base_fail={k: float(v) for k, v in fm.get("stage_base_fail", {
                "prelaunch": 0.1, "early": 0.05, "growth": 0.03,
            }).items()},
            stage_thresholds_years_since_launch={k: float(v) for k, v in fm.get("stage_thresholds_years_since_launch", {
                "prelaunch_max": -0.1, "early_max": 2.0,
            }).items()},
        ),
    )


def load_strategy(path: str) -> StrategyConfig:
    with open(path, "r", encoding="utf-8") as f:
        d = json.load(f)
    return _parse_strategy_dict(d)


def strategy_from_dict(d: dict) -> StrategyConfig:
    return _parse_strategy_dict(d)


def _parse_deal_dict(d: dict) -> DealConfig:
    return DealConfig(
        name=str(d.get("name", "Unnamed Deal")),
        cap_multiple=float(d.get("cap_multiple", 10.0)),
        success_prob=float(d.get("success_prob", 0.3)),
        failure_multiple=float(d.get("failure_multiple", 0.0)),
        exit_year_mode=d.get("exit_year_mode", "fixed"),
        exit_year=int(d.get("exit_year", 7)),
        exit_year_triangular=d.get("exit_year_triangular", {"low": 3, "mode": 7, "high": 10}),
        check_size=float(d.get("check_size", 3_000_000.0)),
        follow_on_allowed=bool(d.get("follow_on_allowed", False)),
        metric=d.get("metric", "ev_rev"),
    )


def load_deal(path: str) -> DealConfig:
    with open(path, "r", encoding="utf-8") as f:
        d = json.load(f)
    return _parse_deal_dict(d)


def deal_from_dict(d: dict) -> DealConfig:
    return _parse_deal_dict(d)


def strategy_to_dict(cfg: StrategyConfig) -> dict:
    """Serialize StrategyConfig back to JSON-safe dict."""
    return {
        "fund_size": cfg.fund_size,
        "mgmt_fee_pct": cfg.mgmt_fee_pct,
        "reserve_pct": cfg.reserve_pct,
        "reserve_conc": cfg.reserve_conc,
        "recycle_pct": cfg.recycle_pct,
        "nc": cfg.nc,
        "years": cfg.years,
        "seed": cfg.seed,
        "mode": cfg.mode,
        "min_check": cfg.min_check,
        "prelaunch_check_discount": cfg.prelaunch_check_discount,
        "ownership_cap": cfg.ownership_cap,
        "initial_ownership_target": cfg.initial_ownership_target,
        "ev_rev": {
            "mean_sw": cfg.ev_rev.mean_sw,
            "mean_hw": cfg.ev_rev.mean_hw,
            "log_sigma": cfg.ev_rev.log_sigma,
            "sigma_scale": cfg.ev_rev.sigma_scale,
        },
        "ev_ebitda": {
            "mean_sw": cfg.ev_ebitda.mean_sw,
            "mean_hw": cfg.ev_ebitda.mean_hw,
            "log_sigma": cfg.ev_ebitda.log_sigma,
            "sigma_scale": cfg.ev_ebitda.sigma_scale,
            "ebitda_margin_start": cfg.ev_ebitda.ebitda_margin_start,
            "ebitda_margin_end": cfg.ev_ebitda.ebitda_margin_end,
            "ebitda_margin_ramp_years": cfg.ev_ebitda.ebitda_margin_ramp_years,
        },
        "mark_stage_mult": cfg.mark_stage_mult,
        "growth": cfg.growth,
        "nav_cost_floor": cfg.nav_cost_floor,
        "failure_model": {
            "ycl_base_fail_p0": cfg.failure_model.ycl_base_fail_p0,
            "ycl_slopes": cfg.failure_model.ycl_slopes,
            "stage_base_fail": cfg.failure_model.stage_base_fail,
            "stage_thresholds_years_since_launch": cfg.failure_model.stage_thresholds_years_since_launch,
        },
    }
