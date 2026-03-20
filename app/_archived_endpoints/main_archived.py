"""
ARCHIVED API ENDPOINTS — VoLo Engine
=====================================

This file contains all unused/deprecated API endpoints that were previously in main.py.
These endpoints are no longer active but are kept here for reference and potential future use.

Archived endpoints:
1. /api/simulate (SimulationRequest + _extract_custom_bass helper)
2. /api/archetypes
3. /api/carbon-defaults/{archetype}
4. /api/sectors
5. /api/fund-simulate (FundSimulationRequest)
6. /api/valuation-comps
7. /api/valuation-comps/all
8. /api/market-sizing
9. /api/market-sizing/defaults
10. /api/position-sizing (PositionSizingRequest)
11. /api/portfolio-config
12. /api/portfolio-simulate (PortfolioSimRequest)
13. /api/portfolio-deal-impact (DealImpactRequest)
14. /api/data-status

Dependencies included:
- Pydantic models referenced by these endpoints
- Helper functions (_extract_custom_bass)
- Startup events and configuration loading (VALUATION_COMPS, PORTFOLIO_CFG)
"""

# ================================================================
#  SIMULATION ENDPOINT
# ================================================================

class SimulationRequest(BaseModel):
    company_name: str = "Unnamed Deal"
    archetype: str = "utility_solar"
    tam_millions: float = 50000
    trl: int = 5
    entry_stage: str = "Seed"
    check_size_millions: float = 2.0
    pre_money_millions: float = 15.0
    sector_profile: str = "Energy + Deep Tech"
    penetration_low: float = 0.005
    penetration_high: float = 0.03
    exit_multiple_low: float = 12.0
    exit_multiple_high: float = 30.0
    exit_year_min: int = 5
    exit_year_max: int = 10
    n_simulations: int = 5000
    random_seed: Optional[int] = 42
    custom_bass_p_mean: Optional[float] = None
    custom_bass_p_std: Optional[float] = None
    custom_bass_q_mean: Optional[float] = None
    custom_bass_q_std: Optional[float] = None
    custom_maturity: Optional[str] = None
    custom_inflection_year: Optional[int] = None


def _extract_custom_bass(req) -> dict:
    """Build custom Bass kwargs from request fields if provided."""
    kwargs = {}
    if req.custom_bass_p_mean is not None and req.custom_bass_q_mean is not None:
        kwargs["custom_bass_p"] = (req.custom_bass_p_mean, req.custom_bass_p_std or req.custom_bass_p_mean * 0.3)
        kwargs["custom_bass_q"] = (req.custom_bass_q_mean, req.custom_bass_q_std or req.custom_bass_q_mean * 0.2)
    if req.custom_maturity is not None:
        kwargs["custom_maturity"] = req.custom_maturity
    if req.custom_inflection_year is not None:
        kwargs["custom_inflection_year"] = req.custom_inflection_year
    return kwargs


@app.post("/api/simulate")
async def simulate(req: SimulationRequest):
    start = time.time()

    try:
        result = run_simulation(
            archetype=req.archetype,
            tam_millions=req.tam_millions,
            trl=req.trl,
            entry_stage=req.entry_stage,
            check_size_millions=req.check_size_millions,
            pre_money_millions=req.pre_money_millions,
            sector_profile=req.sector_profile,
            carta_data=DATA_STORE.get("carta_rounds", {}),
            penetration_share=(req.penetration_low, req.penetration_high),
            exit_multiple_range=(req.exit_multiple_low, req.exit_multiple_high),
            exit_year_range=(req.exit_year_min, req.exit_year_max),
            n_simulations=req.n_simulations,
            random_seed=req.random_seed,
            **_extract_custom_bass(req),
        )
    except Exception as e:
        return JSONResponse(content={"error": f"Simulation failed: {e}"}, status_code=500)

    result["company_name"] = req.company_name
    result["computation_time_ms"] = round((time.time() - start) * 1000, 1)

    response = {k: v for k, v in result.items() if not k.startswith("_raw")}
    return JSONResponse(content=numpy_safe_json(response))


# ================================================================
#  ARCHETYPE & SECTOR ENDPOINTS
# ================================================================

@app.get("/api/archetypes")
async def get_archetypes():
    return JSONResponse(content=DATA_STORE.get("archetypes", {}))


@app.get("/api/carbon-defaults/{archetype}")
async def get_carbon_archetype_defaults(archetype: str):
    """Return carbon model defaults for a given archetype."""
    from .engine.rvm_carbon import get_carbon_defaults
    defaults = get_carbon_defaults(archetype)
    return JSONResponse(content=defaults)


@app.get("/api/sectors")
async def get_sectors():
    sectors = {}
    for sector, stages in DATA_STORE.get("carta_rounds", {}).items():
        sectors[sector] = {
            "stages": list(stages.keys()),
            "seed_grad_rate": stages.get("Seed", {}).get("graduation_rate"),
            "seed_median_round": stages.get("Seed", {}).get("round_size", {}).get("p50"),
        }
    return JSONResponse(content=sectors)


# ================================================================
#  FUND SIMULATION ENDPOINT
# ================================================================

class FundSimulationRequest(BaseModel):
    fund_size_m: float = 100.0
    n_deals: int = 25
    avg_check_m: float = 2.0
    management_fee_pct: float = 0.02
    carry_pct: float = 0.20
    hurdle_rate: float = 0.08
    fund_life_years: int = 10
    investment_period_years: int = 4
    recycling_rate: float = 0.10
    n_fund_sims: int = 2000
    random_seed: Optional[int] = 42
    archetype: str = "battery_storage_utility"
    tam_millions: float = 80000
    trl: int = 5
    entry_stage: str = "Seed"
    check_size_millions: float = 2.0
    pre_money_millions: float = 15.0
    sector_profile: str = "Energy + Deep Tech"
    penetration_low: float = 0.002
    penetration_high: float = 0.02
    exit_multiple_low: float = 12.0
    exit_multiple_high: float = 30.0
    exit_year_min: int = 5
    exit_year_max: int = 10
    custom_bass_p_mean: Optional[float] = None
    custom_bass_p_std: Optional[float] = None
    custom_bass_q_mean: Optional[float] = None
    custom_bass_q_std: Optional[float] = None
    custom_maturity: Optional[str] = None
    custom_inflection_year: Optional[int] = None


@app.post("/api/fund-simulate")
async def fund_simulate(req: FundSimulationRequest):
    start = time.time()

    try:
        deal_result = run_simulation(
            archetype=req.archetype,
            tam_millions=req.tam_millions,
            trl=req.trl,
            entry_stage=req.entry_stage,
            check_size_millions=req.check_size_millions,
            pre_money_millions=req.pre_money_millions,
            sector_profile=req.sector_profile,
            carta_data=DATA_STORE.get("carta_rounds", {}),
            penetration_share=(req.penetration_low, req.penetration_high),
            exit_multiple_range=(req.exit_multiple_low, req.exit_multiple_high),
            exit_year_range=(req.exit_year_min, req.exit_year_max),
            n_simulations=DEAL_MOIC_SIM_COUNT,
            random_seed=req.random_seed,
            **_extract_custom_bass(req),
        )

        moic_all = deal_result.get("_raw_moic", [])
        exit_years_all = deal_result.get("_raw_exit_years", [])

        result = simulate_fund(
            fund_size_m=req.fund_size_m,
            n_deals=req.n_deals,
            avg_check_m=req.avg_check_m,
            management_fee_pct=req.management_fee_pct,
            carry_pct=req.carry_pct,
            hurdle_rate=req.hurdle_rate,
            fund_life_years=req.fund_life_years,
            investment_period_years=req.investment_period_years,
            deal_moic_distribution=moic_all,
            deal_exit_year_distribution=exit_years_all,
            recycling_rate=req.recycling_rate,
            n_simulations=req.n_fund_sims,
            random_seed=req.random_seed,
        )
    except Exception as e:
        return JSONResponse(content={"error": f"Fund simulation failed: {e}"}, status_code=500)

    result["computation_time_ms"] = round((time.time() - start) * 1000, 1)
    result["deal_summary"] = {
        "archetype": req.archetype,
        "trl": req.trl,
        "entry_stage": req.entry_stage,
        "sector_profile": req.sector_profile,
    }

    return JSONResponse(content=numpy_safe_json(result))


# ================================================================
#  VALUATION COMPS ENDPOINTS
# ================================================================

@app.get("/api/valuation-comps")
async def get_valuation_comps(archetype: Optional[str] = None):
    """Return valuation comps. If archetype given, return filtered for that archetype."""
    if not VALUATION_COMPS:
        return JSONResponse(content={"error": "Valuation comps not loaded"}, status_code=500)
    if archetype:
        comps = get_comps_for_archetype(VALUATION_COMPS, archetype)
        comps["source"] = VALUATION_COMPS.get("source")
        comps["acquisition_discount"] = VALUATION_COMPS.get("acquisition_discount")
        comps["acquisition_discount_citation"] = VALUATION_COMPS.get("acquisition_discount_citation")
        return JSONResponse(content=numpy_safe_json(comps))
    safe = {k: v for k, v in VALUATION_COMPS.items() if k != "all_industries"}
    return JSONResponse(content=numpy_safe_json(safe))


@app.get("/api/valuation-comps/all")
async def get_all_valuation_comps():
    """Return all industry multiples from Damodaran dataset."""
    if not VALUATION_COMPS:
        return JSONResponse(content={"error": "Valuation comps not loaded"}, status_code=500)
    return JSONResponse(content=numpy_safe_json(VALUATION_COMPS))


# ================================================================
#  MARKET SIZING ENDPOINTS
# ================================================================

@app.get("/api/market-sizing")
async def market_sizing(archetype: str = "battery_storage_utility",
                        tam_m: Optional[float] = None,
                        sam_pct: Optional[float] = None,
                        som_pct: Optional[float] = None):
    """Compute TAM→SAM→SOM with defaults and optional overrides."""
    result = get_market_sizing(archetype, tam_m, sam_pct, som_pct)
    return JSONResponse(content=numpy_safe_json(result))


@app.get("/api/market-sizing/defaults")
async def market_sizing_defaults():
    """Return default market sizing for all archetypes."""
    return JSONResponse(content=numpy_safe_json(get_all_market_sizing_defaults()))


# ================================================================
#  POSITION SIZING ENDPOINT
# ================================================================

class PositionSizingRequest(BaseModel):
    archetype: str = "battery_storage_utility"
    tam_millions: float = 80000
    trl: int = 5
    entry_stage: str = "Seed"
    check_size_millions: float = 2.0
    pre_money_millions: float = 15.0
    sector_profile: str = "Energy + Deep Tech"
    penetration_low: float = 0.005
    penetration_high: float = 0.03
    exit_multiple_low: float = 12.0
    exit_multiple_high: float = 30.0
    exit_year_min: int = 5
    exit_year_max: int = 10
    fund_size_m: float = 100.0
    n_deals: int = 25
    mgmt_fee_pct: float = 2.0
    reserve_pct: float = 30.0
    max_concentration_pct: float = 15.0
    n_simulations: int = 3000
    random_seed: Optional[int] = 42
    custom_bass_p_mean: Optional[float] = None
    custom_bass_p_std: Optional[float] = None
    custom_bass_q_mean: Optional[float] = None
    custom_bass_q_std: Optional[float] = None
    custom_maturity: Optional[str] = None
    custom_inflection_year: Optional[int] = None


@app.post("/api/position-sizing")
async def position_sizing(req: PositionSizingRequest):
    """Run Monte Carlo then compute optimal check size via Kelly + constraints."""
    start = time.time()

    try:
        result = run_simulation(
            archetype=req.archetype,
            tam_millions=req.tam_millions,
            trl=req.trl,
            entry_stage=req.entry_stage,
            check_size_millions=req.check_size_millions,
            pre_money_millions=req.pre_money_millions,
            sector_profile=req.sector_profile,
            carta_data=DATA_STORE.get("carta_rounds", {}),
            penetration_share=(req.penetration_low, req.penetration_high),
            exit_multiple_range=(req.exit_multiple_low, req.exit_multiple_high),
            exit_year_range=(req.exit_year_min, req.exit_year_max),
            n_simulations=req.n_simulations,
            random_seed=req.random_seed,
            **_extract_custom_bass(req),
        )

        moic_distribution = result.get("_raw_moic", [])
        sizing = optimize_position_size(
            moic_distribution=moic_distribution,
            check_size_m=req.check_size_millions,
            pre_money_m=req.pre_money_millions,
            fund_size_m=req.fund_size_m,
            n_deals=req.n_deals,
            mgmt_fee_pct=req.mgmt_fee_pct,
            reserve_pct=req.reserve_pct,
            max_concentration_pct=req.max_concentration_pct,
            entry_stage=req.entry_stage,
            survival_rate=result.get("summary", {}).get("survival_rate", 0.3),
            moic_conditional_mean=result.get("moic_conditional", {}).get("mean", 3.0),
            exit_year_range=(req.exit_year_min, req.exit_year_max),
        )
    except Exception as e:
        return JSONResponse(content={"error": f"Position sizing failed: {e}"}, status_code=500)

    sizing["computation_time_ms"] = round((time.time() - start) * 1000, 1)
    sizing["deal_params"] = {
        "archetype": req.archetype,
        "trl": req.trl,
        "entry_stage": req.entry_stage,
        "n_simulations": req.n_simulations,
    }

    return JSONResponse(content=numpy_safe_json(sizing))


# ================================================================
#  PORTFOLIO CONFIGURATION & SIMULATION ENDPOINTS
# ================================================================

class PortfolioSimRequest(BaseModel):
    n_portfolios: int = 2000
    seed: Optional[int] = None
    fund_size: Optional[float] = None
    mgmt_fee_pct: Optional[float] = None
    reserve_pct: Optional[float] = None
    reserve_conc: Optional[float] = None
    nc: Optional[int] = None
    years: Optional[int] = None
    mode: Optional[str] = None
    ev_rev_mean_sw: Optional[float] = None
    ev_rev_mean_hw: Optional[float] = None


@app.get("/api/portfolio-config")
async def get_portfolio_config():
    """Return current portfolio strategy config and Carta benchmarks."""
    cfg = PORTFOLIO_CFG.get("strategy")
    bench = PORTFOLIO_CFG.get("benchmarks")
    result = {}
    if cfg:
        result["strategy"] = strategy_to_dict(cfg)
    if bench:
        result["benchmarks"] = benchmarks_to_dict(bench)
    return JSONResponse(content=numpy_safe_json(result))


@app.post("/api/portfolio-simulate")
async def portfolio_simulate(req: PortfolioSimRequest):
    """Run the portfolio-level Monte Carlo simulation."""
    start = time.time()

    cfg = PORTFOLIO_CFG.get("strategy")
    bench = PORTFOLIO_CFG.get("benchmarks")
    if cfg is None:
        return JSONResponse(content={"error": "Portfolio strategy not configured"}, status_code=500)

    cfg_dict = strategy_to_dict(cfg)
    if req.fund_size is not None:
        cfg_dict["fund_size"] = req.fund_size
    if req.mgmt_fee_pct is not None:
        cfg_dict["mgmt_fee_pct"] = req.mgmt_fee_pct
    if req.reserve_pct is not None:
        cfg_dict["reserve_pct"] = req.reserve_pct
    if req.reserve_conc is not None:
        cfg_dict["reserve_conc"] = req.reserve_conc
    if req.nc is not None:
        cfg_dict["nc"] = req.nc
    if req.years is not None:
        cfg_dict["years"] = req.years
        mark = cfg_dict.get("mark_stage_mult", [])
        if len(mark) != req.years:
            cfg_dict["mark_stage_mult"] = (mark + [1.0] * req.years)[:req.years]
    if req.mode is not None:
        cfg_dict["mode"] = req.mode
    if req.ev_rev_mean_sw is not None:
        cfg_dict["ev_rev"]["mean_sw"] = req.ev_rev_mean_sw
    if req.ev_rev_mean_hw is not None:
        cfg_dict["ev_rev"]["mean_hw"] = req.ev_rev_mean_hw
    if req.seed is not None:
        cfg_dict["seed"] = req.seed

    active_cfg = strategy_from_dict(cfg_dict)
    sim = VCSimulator(active_cfg, bench=bench)
    out = sim.run(n_portfolios=req.n_portfolios, seed=req.seed)

    ages = out["ages"].tolist()
    tvpi_ptiles = {
        "p10": out["p10"].tolist(),
        "p50": out["p50"].tolist(),
        "p75": out["p75"].tolist(),
        "p90": out["p90"].tolist(),
    }

    # IRR distribution
    irrs = out["irrs"]
    valid_irrs = irrs[np.isfinite(irrs)]

    # DPI percentiles
    dpi_matrix = out.get("dpi_matrix")
    dpi_ptiles = {}
    if dpi_matrix is not None:
        for p in [10, 50, 75, 90]:
            dpi_ptiles[f"p{p}"] = np.percentile(dpi_matrix, p, axis=0).tolist()

    # Terminal TVPI/IRR distributions for tables
    terminal_tvpi = out["tvpi_matrix"][:, -1]
    tvpi_dist = {
        "p5": round(float(np.percentile(terminal_tvpi, 5)), 3),
        "p10": round(float(np.percentile(terminal_tvpi, 10)), 3),
        "p25": round(float(np.percentile(terminal_tvpi, 25)), 3),
        "p50": round(float(np.percentile(terminal_tvpi, 50)), 3),
        "p75": round(float(np.percentile(terminal_tvpi, 75)), 3),
        "p90": round(float(np.percentile(terminal_tvpi, 90)), 3),
        "p95": round(float(np.percentile(terminal_tvpi, 95)), 3),
        "mean": round(float(np.mean(terminal_tvpi)), 3),
    }

    irr_dist = {}
    if len(valid_irrs) > 0:
        irr_dist = {
            "p5": round(float(np.percentile(valid_irrs, 5)), 4),
            "p10": round(float(np.percentile(valid_irrs, 10)), 4),
            "p25": round(float(np.percentile(valid_irrs, 25)), 4),
            "p50": round(float(np.percentile(valid_irrs, 50)), 4),
            "p75": round(float(np.percentile(valid_irrs, 75)), 4),
            "p90": round(float(np.percentile(valid_irrs, 90)), 4),
            "p95": round(float(np.percentile(valid_irrs, 95)), 4),
            "mean": round(float(np.nanmean(valid_irrs)), 4),
            "pct_valid": round(float(len(valid_irrs) / len(irrs) * 100), 1),
        }

    # Target probabilities
    tvpi_targets = [1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]
    irr_targets = [0.0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30]
    tvpi_probs = {f"{t}x": round(float((terminal_tvpi >= t).mean()), 4) for t in tvpi_targets}
    irr_probs = {}
    if len(valid_irrs) > 0:
        irr_probs = {f"{int(t*100)}%": round(float((valid_irrs >= t).mean()), 4) for t in irr_targets}

    # Benchmarks overlay
    bench_data = None
    if bench:
        bench_data = benchmarks_to_dict(bench)

    result = {
        "ages": ages,
        "tvpi_percentiles": tvpi_ptiles,
        "dpi_percentiles": dpi_ptiles,
        "tvpi_distribution": tvpi_dist,
        "irr_distribution": irr_dist,
        "target_probabilities": {
            "tvpi": tvpi_probs,
            "irr": irr_probs,
        },
        "benchmarks": bench_data,
        "strategy": {
            "fund_size": active_cfg.fund_size,
            "mgmt_fee_pct": active_cfg.mgmt_fee_pct,
            "reserve_pct": active_cfg.reserve_pct,
            "nc": active_cfg.nc,
            "years": active_cfg.years,
            "mode": active_cfg.mode,
        },
        "meta": {
            "n_portfolios": req.n_portfolios,
            "fail_rate": round(out["fail_rate"] * 100, 1),
            "hw_pct": round(out["hw_pct"] * 100, 1),
            "computation_time_ms": round((time.time() - start) * 1000, 1),
        },
    }

    return JSONResponse(content=numpy_safe_json(result))


# ================================================================
#  PORTFOLIO DEAL IMPACT ENDPOINT
# ================================================================

class DealImpactRequest(BaseModel):
    name: str = "Proposed Deal"
    cap_multiple: float = 10.0
    success_prob: float = 0.30
    failure_multiple: float = 0.0
    exit_year_mode: str = "triangular"
    exit_year: int = 7
    exit_year_tri_low: float = 3.0
    exit_year_tri_mode: float = 7.0
    exit_year_tri_high: float = 10.0
    check_size: float = 3_000_000.0
    n_portfolios: int = 4000
    seed: Optional[int] = None


@app.post("/api/portfolio-deal-impact")
async def portfolio_deal_impact(req: DealImpactRequest):
    """Compute the marginal impact of adding a specific deal to the portfolio."""
    start = time.time()

    cfg = PORTFOLIO_CFG.get("strategy")
    bench = PORTFOLIO_CFG.get("benchmarks")
    if cfg is None:
        return JSONResponse(content={"error": "Portfolio strategy not configured"}, status_code=500)

    deal = deal_from_dict({
        "name": req.name,
        "cap_multiple": req.cap_multiple,
        "success_prob": req.success_prob,
        "failure_multiple": req.failure_multiple,
        "exit_year_mode": req.exit_year_mode,
        "exit_year": req.exit_year,
        "exit_year_triangular": {
            "low": req.exit_year_tri_low,
            "mode": req.exit_year_tri_mode,
            "high": req.exit_year_tri_high,
        },
        "check_size": req.check_size,
        "follow_on_allowed": False,
        "metric": cfg.mode,
    })

    sim = VCSimulator(cfg, bench=bench)
    impact = sim.deal_impact(deal, n_portfolios=req.n_portfolios, seed=req.seed)

    impact["computation_time_ms"] = round((time.time() - start) * 1000, 1)
    return JSONResponse(content=numpy_safe_json(impact))


# ================================================================
#  DATA STATUS ENDPOINT
# ================================================================

@app.get("/api/data-status")
async def data_status():
    return JSONResponse(content={
        "carta_sectors": list(DATA_STORE.get("carta_rounds", {}).keys()),
        "carta_fund_records": len(DATA_STORE.get("carta_funds", [])),
        "atb_technologies": list(DATA_STORE.get("atb_lcoe", {}).keys()),
        "lazard_lcoe_technologies": list(DATA_STORE.get("lazard_lcoe", {}).keys()),
        "archetypes": list(DATA_STORE.get("archetypes", {}).keys()),
    })
