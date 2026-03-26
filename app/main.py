"""
VoLo Earth Ventures — Quantitative Underwriting Engine
FastAPI application serving the simulation engine and web interface.
"""

import time
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass
from fastapi import FastAPI, Request, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from typing import Optional
from pydantic import BaseModel
import numpy as np


def numpy_safe_json(content):
    """Recursively convert numpy types in a dict to native Python types."""
    if isinstance(content, dict):
        return {k: numpy_safe_json(v) for k, v in content.items()}
    if isinstance(content, list):
        return [numpy_safe_json(v) for v in content]
    if isinstance(content, (np.integer,)):
        return int(content)
    if isinstance(content, (np.floating,)):
        v = float(content)
        if np.isnan(v) or np.isinf(v):
            return 0.0
        return v
    if isinstance(content, float):
        if np.isnan(content) or np.isinf(content):
            return 0.0
        return content
    if isinstance(content, np.ndarray):
        return numpy_safe_json(content.tolist())
    return content

from .data.loader import load_all
from .engine.monte_carlo import run_simulation
from .engine.adoption import DEFAULT_BASS_PARAMS, bass_diffusion_cumulative
from .engine.fund_model import simulate_fund
from .engine.portfolio import (
    VCSimulator,
    load_strategy, load_benchmarks, strategy_from_dict, deal_from_dict,
)
from .engine.portfolio.config import strategy_to_dict
from .engine.portfolio.benchmarks import benchmarks_to_dict
from .engine.valuation_comps import load_vebitda, get_comps_for_archetype

app = FastAPI(title="VoLo Underwriting Engine", version="0.1.0")

BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parent                      # volo-engine/
SOURCES_DIR = REPO_ROOT / "data" / "sources"      # volo-engine/data/sources/
CONFIGS_DIR = REPO_ROOT / "configs"               # volo-engine/configs/
DEAL_MOIC_SIM_COUNT = 5000                        # deal-level sims for MOIC distribution in fund model

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# ── Mount RVM integration routers ─────────────────────────────────────────────
from .auth import router as auth_router
from .routes.resources import router as resources_router
from .routes.extraction import router as extraction_router
from .routes.deal_pipeline import router as deal_pipeline_router
from .routes.memo import router as memo_router
from .routes.drive import router as drive_router
from .routes.dd_analysis import router as dd_analysis_router
from .routes.fund_deployment import router as fund_deployment_router

app.include_router(auth_router)
app.include_router(resources_router)
app.include_router(extraction_router)
app.include_router(deal_pipeline_router)
app.include_router(memo_router)
app.include_router(drive_router)
app.include_router(dd_analysis_router)
app.include_router(fund_deployment_router)

# Load data on startup
DATA_STORE = {}


@app.on_event("startup")
async def startup():
    from .database import startup as db_startup
    db_startup()
    print("[VoLo Engine] RVM database initialized")

    DATA_STORE.update(load_all())
    print(f"[VoLo Engine] Data loaded: {len(DATA_STORE.get('carta_rounds', {}))} sectors, "
          f"{len(DATA_STORE.get('atb_lcoe', {}))} ATB technologies")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})








@app.get("/api/scurve-atlas")
async def scurve_atlas():
    """Generate S-curves for all archetypes using mean Bass parameters, normalized to % of TAM."""
    horizon = 25
    years = list(range(horizon + 1))
    t = np.arange(0, horizon + 1, dtype=float)
    result = {}
    rng = np.random.default_rng(42)

    archetype_labels = {
        "utility_solar": "Utility Solar PV",
        "commercial_solar": "Commercial / C&I Solar",
        "residential_solar": "Residential Solar",
        "onshore_wind": "Onshore Wind",
        "offshore_wind": "Offshore Wind",
        "geothermal": "Geothermal",
        "battery_storage_utility": "Battery Storage",
        "nuclear_smr": "Nuclear / SMR",
        "ev_electrification": "EV / Electrification",
        "climate_software": "Climate SaaS",
        "industrial_decarb": "Industrial Decarb",
        "ai_ml": "AI / Machine Learning",
        "custom": "Custom / Novel",
        "base_capital_intensive": "Base: Capital Intensive",
        "base_software": "Base: Software Only",
        "base_sw_hw_hybrid": "Base: SW/HW Hybrid",
        "base_hard_tech": "Base: Hard Tech",
    }

    for key, params in DEFAULT_BASS_PARAMS.items():
        p_mean, p_std = params["p"]
        q_mean, q_std = params["q"]

        # Median curve (mean parameters)
        median_curve = bass_diffusion_cumulative(t, p_mean, q_mean, 1.0)
        median_pct = [round(float(v) * 100, 2) for v in median_curve]

        # Uncertainty band: draw 200 samples and take p25/p75
        n_band = 200
        p_draws = np.clip(rng.normal(p_mean, p_std, n_band), 0.0005, 0.05)
        q_draws = np.clip(rng.normal(q_mean, q_std, n_band), 0.02, 0.8)
        band = np.zeros((n_band, horizon + 1))
        for i in range(n_band):
            band[i] = bass_diffusion_cumulative(t, p_draws[i], q_draws[i], 1.0) * 100

        p25 = [round(float(np.percentile(band[:, yr], 25)), 2) for yr in range(horizon + 1)]
        p75 = [round(float(np.percentile(band[:, yr], 75)), 2) for yr in range(horizon + 1)]

        # Instantaneous adoption rate for peak timing
        rate = np.diff(median_curve, prepend=0)
        peak_year = int(np.argmax(rate))

        result[key] = {
            "label": archetype_labels.get(key, key.replace("_", " ").title()),
            "maturity": params["maturity"],
            "inflection_year": params["inflection_year"],
            "p_mean": round(p_mean, 5),
            "q_mean": round(q_mean, 3),
            "median": median_pct,
            "p25": p25,
            "p75": p75,
            "peak_adoption_year": peak_year,
        }

    return JSONResponse(content={"years": years, "archetypes": result})


# ================================================================
#  VALUATION COMPS (Damodaran VEBITDA PubComps)
# ================================================================

VALUATION_COMPS = {}


@app.on_event("startup")
async def load_valuation_comps():
    vebitda_path = SOURCES_DIR / "VEBITDA - PubComps.xls"
    if vebitda_path.exists():
        VALUATION_COMPS.update(load_vebitda(str(vebitda_path)))
        n = len(VALUATION_COMPS.get("relevant", {}))
        print(f"[VoLo Engine] Valuation comps loaded: {n} relevant industries from Damodaran")
    else:
        print(f"[VoLo Engine] Warning: VEBITDA file not found at {vebitda_path}")








# ================================================================
#  PORTFOLIO SIMULATOR (Joe's Engine Integration)
# ================================================================

PORTFOLIO_CFG = {}  # loaded on startup


@app.on_event("startup")
async def load_portfolio_configs():
    try:
        PORTFOLIO_CFG["strategy"] = load_strategy(str(CONFIGS_DIR / "strategy.json"))
        PORTFOLIO_CFG["benchmarks"] = load_benchmarks(str(CONFIGS_DIR / "carta_benchmarks.json"))
        print(f"[VoLo Engine] Portfolio configs loaded: strategy + Carta benchmarks")
    except Exception as e:
        print(f"[VoLo Engine] Warning: Could not load portfolio configs: {e}")










# ================================================================
#  MODEL BENCHMARK — API KEYS
# ================================================================

@app.get("/api/keys")
async def get_api_keys():
    """Return masked API keys from .env for the benchmark UI."""
    import os
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    refiant_key = os.environ.get("REFIANT_API_KEY", "")
    return JSONResponse(content={
        "anthropic": anthropic_key,
        "refiant": refiant_key,
        "anthropic_set": bool(anthropic_key),
        "refiant_set": bool(refiant_key),
    })


# ================================================================
#  MODEL PREFERENCES (per-task model selection)
# ================================================================

from .database import (
    get_model_preferences, set_model_preference,
    MODEL_DEFAULTS, VALID_MODELS, VALID_TASKS,
)


import datetime as _dt
import os
import json as _json
import glob as _glob

from .auth import get_current_user, require_admin, CurrentUser


class ModelPreferenceUpdate(BaseModel):
    task_key: str
    model_key: str


@app.get("/api/model-preferences")
async def get_model_prefs(user: CurrentUser = Depends(get_current_user)):
    """Return per-task model selections for the current user."""
    prefs = get_model_preferences(user.id)
    return JSONResponse(content={
        "preferences": prefs,
        "defaults": MODEL_DEFAULTS,
        "valid_models": sorted(VALID_MODELS),
        "valid_tasks": sorted(VALID_TASKS),
    })


@app.post("/api/model-preferences")
async def update_model_pref(req: ModelPreferenceUpdate, user: CurrentUser = Depends(get_current_user)):
    """Set the model for a specific task."""
    if req.task_key not in VALID_TASKS:
        return JSONResponse(content={"error": f"Invalid task: {req.task_key}. Valid: {sorted(VALID_TASKS)}"}, status_code=400)
    if req.model_key not in VALID_MODELS:
        return JSONResponse(content={"error": f"Invalid model: {req.model_key}. Valid: {sorted(VALID_MODELS)}"}, status_code=400)
    set_model_preference(user.id, req.task_key, req.model_key)
    return JSONResponse(content={"ok": True, "task_key": req.task_key, "model_key": req.model_key})


# ================================================================
#  REFERENCES CATALOG
# ================================================================

# Master reference registry — each entry describes a data source used in the analysis.
# "file" entries auto-detect last-modified from data/sources/; "hardcoded" entries use a manual date.
REFERENCE_CATALOG = [
    {
        "id": "carta_rounds",
        "name": "Carta Insights — Fund Forecasting Profiles",
        "provider": "Carta",
        "description": "Round sizing by stage/sector (p10-p90 percentiles), pre/post-money valuations, ESOP metrics, graduation rates, and time-to-graduation.",
        "file": "Carta Insights_Fund Forecasting Profiles.xlsx",
        "url": "https://carta.com/blog/startup-financing-data/",
        "category": "Financing & Valuation",
        "used_by": ["monte_carlo.py", "dilution.py", "loader.py"],
    },
    {
        "id": "nrel_atb",
        "name": "NREL Annual Technology Baseline 2024 v3",
        "provider": "National Renewable Energy Laboratory (NREL)",
        "description": "LCOE projections by technology and cost case, deployment cost benchmarks, representative technology classes, capacity factors, and CAPEX curves.",
        "file": "Annual Tech Baseline 2024_v3_Workbook.xlsx",
        "url": "https://atb.nrel.gov/",
        "category": "Technology Cost",
        "used_by": ["loader.py", "adoption.py"],
    },
    {
        "id": "damodaran_comps",
        "name": "Damodaran EV/EBITDA Public Comps",
        "provider": "Aswath Damodaran, Stern NYU",
        "description": "EV/EBITDA multiples for 97 US industries — used for IPO and acquisition exit multiples with 20% acquisition discount.",
        "file": "VEBITDA - PubComps.xls",
        "url": "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/data.html",
        "category": "Valuation Multiples",
        "used_by": ["valuation_comps.py"],
    },
    {
        "id": "lazard_lcoe",
        "name": "Lazard Levelized Cost of Energy+ (LCOE+)",
        "provider": "Lazard",
        "description": "Energy cost benchmarks (solar, wind, geothermal, nuclear, gas, coal, battery storage) in $/MWh ranges.",
        "file": "lazards-lcoeplus-june-2025-_vf.pdf",
        "url": "https://www.lazard.com/research-insights/levelized-cost-of-energyplus/",
        "category": "Technology Cost",
        "used_by": ["loader.py"],
    },
    {
        "id": "doe_electrification",
        "name": "DOE Electrification Pathways Data Appendix",
        "provider": "U.S. Department of Energy",
        "description": "Electrification pathways for EV and industrial technology deployment scenarios.",
        "file": "data-appendix-electrification-pathways-063025.xlsx",
        "url": "https://www.energy.gov/eere/analysis/electrification-futures-study",
        "category": "Technology Deployment",
        "used_by": ["loader.py"],
    },
    {
        "id": "bass_diffusion",
        "name": "Bass Diffusion Adoption Parameters",
        "provider": "NREL ATB + Market Data (calibrated)",
        "description": "Innovation (p) and imitation (q) coefficients for 12 technology archetypes, inflection years, and maturity stages. Calibrated from historical deployment patterns.",
        "file": None,
        "hardcoded_in": "engine/adoption.py",
        "url": None,
        "category": "Market Adoption",
        "used_by": ["adoption.py", "monte_carlo.py"],
        "manual_date": "2025-01-15",
    },
    {
        "id": "ebitda_margins",
        "name": "EBITDA Margin Ramp Model by TRL",
        "provider": "SaaS Capital, Bessemer Cloud Index, Battery Ventures, NREL/DOE",
        "description": "TRL-dependent EBITDA margin start/end/ramp parameters. TRL 1 ranges from -20% to +20% over 10 years; TRL 9 from 18% to 32% over 2 years.",
        "file": None,
        "hardcoded_in": "engine/monte_carlo.py",
        "url": None,
        "category": "Financial Modeling",
        "used_by": ["monte_carlo.py"],
        "manual_date": "2025-01-15",
    },
    {
        "id": "cambridge_exits",
        "name": "Venture Exit Year Distribution",
        "provider": "Cambridge Associates",
        "description": "Exit year probability weighting — venture exits cluster around years 4-7 from entry.",
        "file": None,
        "hardcoded_in": "engine/monte_carlo.py",
        "url": "https://www.cambridgeassociates.com/",
        "category": "Exit Modeling",
        "used_by": ["monte_carlo.py"],
        "manual_date": "2023-12-01",
    },
    {
        "id": "market_sizing",
        "name": "TAM/SAM/SOM Defaults by Archetype",
        "provider": "BloombergNEF, Wood Mackenzie, IEA, Rystad, McKinsey, Gartner, Grand View Research",
        "description": "Total addressable market sizing for 12 technology archetypes (e.g., Utility Solar $120B, EV Electrification $500B, AI/ML $300B).",
        "file": None,
        "hardcoded_in": "engine/market_sizing.py",
        "url": None,
        "category": "Market Sizing",
        "used_by": ["market_sizing.py", "monte_carlo.py"],
        "manual_date": "2025-02-01",
    },
    {
        "id": "carbon_intensity",
        "name": "Carbon Intensity & Impact Model",
        "provider": "VoLo Earth Proprietary (RVM 1.19)",
        "description": "Carbon intensity (tCO2/unit) by displaced resource, TRL-to-risk divisor mapping, archetype-specific baseline production and range improvement factors.",
        "file": None,
        "hardcoded_in": "engine/rvm_carbon.py",
        "url": None,
        "category": "Carbon Impact",
        "used_by": ["rvm_carbon.py"],
        "manual_date": "2023-01-19",
    },
    {
        "id": "private_discount",
        "name": "Private Company Acquisition Discount",
        "provider": "Koeplin, Sarin & Shapiro (2000); Officer (2007)",
        "description": "20% acquisition haircut to IPO multiples based on academic research (15-30% range documented).",
        "file": None,
        "hardcoded_in": "engine/valuation_comps.py",
        "url": None,
        "category": "Valuation Multiples",
        "used_by": ["valuation_comps.py"],
        "manual_date": "2025-01-15",
    },
    {
        "id": "carta_benchmarks",
        "name": "Carta TVPI Fund Benchmarks",
        "provider": "Carta",
        "description": "Fund TVPI percentiles (p10, p50, p75, p90) by fund age for portfolio-level performance overlay and convergence-driven sizing.",
        "file": None,
        "hardcoded_in": "configs/carta_benchmarks.json",
        "url": "https://carta.com/blog/startup-financing-data/",
        "category": "Fund Performance",
        "used_by": ["portfolio/benchmarks.py", "fund_model.py"],
        "manual_date": "2025-02-26",
    },
]


@app.get("/api/references")
async def get_references():
    """Return all reference sources with file metadata (last modified, size)."""
    results = []
    for ref in REFERENCE_CATALOG:
        entry = {**ref}
        # Resolve file metadata if it has a file in data/sources/
        if ref.get("file"):
            fp = SOURCES_DIR / ref["file"]
            if fp.exists():
                stat = fp.stat()
                entry["file_exists"] = True
                entry["file_size"] = stat.st_size
                entry["last_modified"] = _dt.datetime.fromtimestamp(stat.st_mtime).isoformat()
            else:
                entry["file_exists"] = False
                entry["file_size"] = 0
                entry["last_modified"] = None
        else:
            entry["file_exists"] = None  # hardcoded, no external file
            entry["last_modified"] = ref.get("manual_date")
        results.append(entry)
    return JSONResponse(content={"references": results})


@app.post("/api/references/refresh/{ref_id}")
async def refresh_reference(ref_id: str, user: CurrentUser = Depends(get_current_user)):
    """Placeholder for refreshing a specific reference — returns instructions
    on where to obtain the latest version of the data source."""
    ref = next((r for r in REFERENCE_CATALOG if r["id"] == ref_id), None)
    if not ref:
        return JSONResponse(content={"error": f"Unknown reference: {ref_id}"}, status_code=404)

    refresh_instructions = {
        "carta_rounds": {
            "steps": [
                "Visit https://carta.com/blog/startup-financing-data/ or request updated data via Carta Data team.",
                "Download the latest 'Fund Forecasting Profiles' Excel workbook.",
                "Replace data/sources/Carta Insights_Fund Forecasting Profiles.xlsx with the new file.",
                "Restart the server to reload data."
            ],
            "auto_available": False,
        },
        "nrel_atb": {
            "steps": [
                "Visit https://atb.nrel.gov/ and navigate to the latest Annual Technology Baseline.",
                "Download the Summary workbook (XLSX).",
                "Replace data/sources/Annual Tech Baseline 2024_v3_Workbook.xlsx with the new file.",
                "Restart the server to reload data."
            ],
            "auto_available": False,
        },
        "damodaran_comps": {
            "steps": [
                "Visit https://pages.stern.nyu.edu/~adamodar/New_Home_Page/data.html",
                "Download 'Value of Firms in different sectors' → EV/EBITDA multiples spreadsheet.",
                "Replace data/sources/VEBITDA - PubComps.xls with the new file.",
                "Restart the server to reload data."
            ],
            "auto_available": False,
        },
        "lazard_lcoe": {
            "steps": [
                "Visit https://www.lazard.com/research-insights/levelized-cost-of-energyplus/",
                "Download the latest LCOE+ PDF report.",
                "Replace data/sources/lazards-lcoeplus-june-2025-_vf.pdf with the new file.",
                "Update the hardcoded Lazard LCOE values in app/data/loader.py → load_lazard_lcoe()."
            ],
            "auto_available": False,
        },
        "doe_electrification": {
            "steps": [
                "Visit https://www.energy.gov/eere/analysis/electrification-futures-study",
                "Download the latest data appendix spreadsheet.",
                "Replace data/sources/data-appendix-electrification-pathways-063025.xlsx.",
                "Restart the server to reload data."
            ],
            "auto_available": False,
        },
    }

    info = refresh_instructions.get(ref_id, {
        "steps": [
            f"This reference ({ref['name']}) is hardcoded in {ref.get('hardcoded_in', 'the engine')}.",
            "To update, edit the source file directly in the Developer Console.",
            "Search for the relevant constants and update with newer data."
        ],
        "auto_available": False,
    })

    return JSONResponse(content={
        "ref_id": ref_id,
        "name": ref["name"],
        "provider": ref["provider"],
        "url": ref.get("url"),
        "refresh": info,
    })


# ================================================================
#  AI AGENT CHAT (Anthropic Claude + Tool Use)
# ================================================================

class ChatRequest(BaseModel):
    message: str
    report_context: Optional[dict] = None
    deal_params: Optional[dict] = None
    conversation_history: Optional[list] = None


# ── Tool definitions for the agentic chat ────────────────────────────────────

AGENT_TOOLS = [
    {
        "name": "modify_deal_parameter",
        "description": "Modify a deal parameter and explain the impact. Use this when the user asks to change check size, TRL, exit multiples, TAM, entry stage, penetration share, or any other simulation input. Returns a parameter change instruction the frontend will apply.",
        "input_schema": {
            "type": "object",
            "properties": {
                "parameter": {
                    "type": "string",
                    "description": "Parameter name: check_size_millions, pre_money_millions, tam_millions, trl, entry_stage, archetype, exit_multiple_low, exit_multiple_high, penetration_low, penetration_high, sector_profile, n_simulations"
                },
                "value": {
                    "description": "New value for the parameter (number or string depending on parameter)"
                },
                "reasoning": {
                    "type": "string",
                    "description": "Brief explanation of why this change is being made and expected impact"
                }
            },
            "required": ["parameter", "value", "reasoning"]
        }
    },
    {
        "name": "run_scenario_comparison",
        "description": "Run the current deal with modified parameters to compare scenarios. Use when the user asks 'what if' questions like 'what if TRL was 7' or 'what happens with a $5M check'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "scenario_name": {
                    "type": "string",
                    "description": "Short name for this scenario"
                },
                "parameter_changes": {
                    "type": "object",
                    "description": "Dict of parameter_name: new_value pairs to override"
                }
            },
            "required": ["scenario_name", "parameter_changes"]
        }
    },
    {
        "name": "explain_metric",
        "description": "Provide a detailed explanation of a specific metric from the deal report. Use when the user asks 'what does P(Loss) mean' or 'explain the MOIC distribution'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "metric_name": {
                    "type": "string",
                    "description": "The metric to explain: moic, irr, ev, survival_rate, p_loss, p_gt3x, position_sizing, carbon_impact, ebitda_margin, dilution, bass_diffusion, trl_impact, kelly_criterion"
                }
            },
            "required": ["metric_name"]
        }
    },
    {
        "name": "suggest_optimal_terms",
        "description": "Analyze the current deal and suggest optimal deal terms (check size, valuation, stage) based on the simulation results. Use when the user asks for recommendations.",
        "input_schema": {
            "type": "object",
            "properties": {
                "optimize_for": {
                    "type": "string",
                    "description": "What to optimize: fund_returns, risk_adjusted, carbon_impact, balanced"
                }
            },
            "required": ["optimize_for"]
        }
    },
]

AGENT_SYSTEM_PROMPT = """You are the VoLo Earth Ventures AI Deal Agent, embedded in the Deal Intelligence Platform.
You have deep expertise in venture capital, climate-tech investing, Monte Carlo simulation, and financial modeling.

You have access to tools that let you:
1. **Modify deal parameters** — change check size, TRL, valuation, multiples etc. and have the frontend update in real-time
2. **Run scenario comparisons** — test "what if" scenarios against the current deal
3. **Explain metrics** — provide detailed explanations of any simulation output
4. **Suggest optimal terms** — recommend deal terms based on analysis

IMPORTANT BEHAVIORS:
- When the user asks to change a parameter, USE the modify_deal_parameter tool. Don't just describe the change.
- When the user asks "what if", USE run_scenario_comparison to model it.
- Be quantitative. Reference specific numbers from the deal context.
- Be concise — 2-3 sentences per response unless explaining something complex.
- When suggesting changes, explain the expected impact on MOIC, IRR, and risk metrics.
- If you use a tool, also include a brief natural language summary of what you did and why.

PARAMETER MAPPING (frontend field IDs):
- check_size_millions → wiz-check-size
- pre_money_millions → wiz-pre-money
- tam_millions → wiz-tam
- trl → wiz-trl
- entry_stage → wiz-entry-stage
- archetype → wiz-archetype
- exit_multiple_low → wiz-mult-low
- exit_multiple_high → wiz-mult-high
- penetration_low → wiz-pen-low
- penetration_high → wiz-pen-high
- sector_profile → wiz-sector"""


def _build_context_summary(report_context: dict, deal_params: dict = None) -> str:
    """Build a concise context block from deal report data."""
    ctx = report_context or {}
    sim = ctx.get("simulation", {})
    hero = ctx.get("hero_metrics", {})
    ov = ctx.get("deal_overview", {})
    ev = sim.get("ev_at_exit", {})
    ebm = sim.get("ebitda_margin", {})
    moic = sim.get("moic_unconditional", {})
    prob = sim.get("probability", {})
    ps = ctx.get("position_sizing", {})
    carbon = ctx.get("carbon_impact", {})
    risk = ctx.get("risk_summary", {})

    lines = [
        "[DEAL REPORT CONTEXT]",
        f"Company: {ov.get('company_name', 'N/A')}",
        f"Archetype: {ov.get('archetype', 'N/A')} | TRL: {ov.get('trl', 'N/A')} | Stage: {ov.get('entry_stage', 'N/A')}",
        f"TAM: ${ov.get('tam_millions', 0):.0f}M | Check: ${ov.get('check_size_millions', 0):.1f}M | Pre-money: ${ov.get('pre_money_millions', 0):.1f}M",
        "",
        f"MOIC (Unconditional): P10={moic.get('p10_all', 0):.2f}x | P50={moic.get('p50_all', 0):.2f}x | P90={moic.get('p90_all', 0):.2f}x | Mean={moic.get('mean_all', 0):.2f}x",
        f"Expected IRR: {hero.get('expected_irr_pct', 'N/A')}%",
        f"Survival Rate: {sim.get('summary', {}).get('survival_rate', 0)*100:.1f}%",
        f"P(Loss): {prob.get('loss', 0)*100:.1f}% | P(>3x): {prob.get('gt_3x', 0)*100:.1f}% | P(>10x): {prob.get('gt_10x', 0)*100:.1f}%",
        "",
        f"EV at Exit: Mean=${ev.get('mean_m', 0):.1f}M | P50=${ev.get('p50_m', 0):.1f}M | P90=${ev.get('p90_m', 0):.1f}M",
        f"Exit Revenue: ${ev.get('exit_revenue_mean_m', 0):.1f}M | EBITDA Margin: {ev.get('exit_margin_mean_pct', 0):.1f}% | Multiple: {ev.get('exit_multiple_mean', 0):.1f}x",
        f"EBITDA Margin Ramp: {(ebm.get('margin_start', 0)*100):.0f}% to {(ebm.get('margin_end', 0.25)*100):.0f}% over {ebm.get('ramp_years', 6)} years",
    ]

    if ps:
        opt = ps.get("grid_search", {})
        if opt:
            best = opt.get("optimal", {})
            lines.append(f"Optimal Check: ${best.get('check_m', 0):.1f}M (composite score {best.get('composite_score', 0):.3f})")

    if carbon:
        ci = carbon.get("intermediates", {})
        co = carbon.get("outputs", {})
        lines.append(f"Carbon: {co.get('company_tonnes', 0):.0f} tCO2 avoided | VoLo pro-rata: {co.get('volo_prorata', 0):.0f} tCO2")

    if deal_params:
        lines.append(f"\n[CURRENT INPUT PARAMETERS]\n{_json.dumps(deal_params, default=str)[:2000]}")

    return "\n".join(lines)


@app.post("/api/chat")
async def chat_endpoint(req: ChatRequest, user: CurrentUser = Depends(get_current_user)):
    """Agentic AI chat — uses tool-calling to modify deals and run scenarios."""
    import anthropic

    # Resolve model from user preferences
    from .engine.llm_utils import make_llm_client
    prefs = get_model_preferences(user.id)
    chat_model = prefs.get("deal_chat", MODEL_DEFAULTS["deal_chat"])
    is_refiant = chat_model.startswith("qwen")

    if is_refiant:
        api_key = os.environ.get("REFIANT_API_KEY", "")
        if not api_key:
            return JSONResponse(content={"error": "REFIANT_API_KEY not configured"}, status_code=500)
    else:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            return JSONResponse(content={"error": "ANTHROPIC_API_KEY not configured"}, status_code=500)
    client = make_llm_client(is_refiant, api_key)

    messages = []
    if req.conversation_history:
        for msg in req.conversation_history[-20:]:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})

    user_content = req.message
    if req.report_context:
        context_summary = _build_context_summary(req.report_context, req.deal_params)
        user_content = context_summary + "\n\n" + req.message

    messages.append({"role": "user", "content": user_content})

    try:
        response = client.messages.create(
            model=chat_model,
            max_tokens=2048,
            system=AGENT_SYSTEM_PROMPT,
            tools=AGENT_TOOLS,
            messages=messages,
        )

        # Process response — extract text and tool calls
        reply_text = ""
        tool_actions = []

        for block in response.content:
            if block.type == "text":
                reply_text += block.text
            elif block.type == "tool_use":
                tool_actions.append({
                    "tool": block.name,
                    "input": block.input,
                    "id": block.id,
                })

        return JSONResponse(content={
            "reply": reply_text,
            "tool_actions": tool_actions,
            "model": response.model,
            "stop_reason": response.stop_reason,
            "usage": {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
        })
    except Exception as e:
        return JSONResponse(content={"error": f"AI chat failed: {str(e)}"}, status_code=500)


# ================================================================
#  DEVELOPER TAB API (Admin-only file editing)
# ================================================================

EDITABLE_ROOT = BASE_DIR   # app/ directory
ALLOWED_EXTENSIONS = {".py", ".js", ".html", ".css", ".json", ".md", ".txt", ".yaml", ".yml", ".toml", ".cfg", ".ini", ".env"}
BLOCKED_PATHS = {"__pycache__", ".git", "node_modules", ".pyc"}


def _is_safe_path(filepath: str) -> bool:
    """Check that a path is within the editable root and not blocked."""
    resolved = Path(filepath).resolve()
    root = EDITABLE_ROOT.resolve()
    if not str(resolved).startswith(str(root)):
        return False
    for part in resolved.parts:
        if part in BLOCKED_PATHS:
            return False
    return True


class FileWriteRequest(BaseModel):
    path: str
    content: str


@app.get("/api/dev/tree")
async def dev_file_tree(user: CurrentUser = Depends(get_current_user)):
    """Return a flat list of editable files in the app directory."""
    tree = []
    root = str(EDITABLE_ROOT)
    for dirpath, dirnames, filenames in os.walk(root):
        # Skip blocked dirs
        dirnames[:] = [d for d in dirnames if d not in BLOCKED_PATHS]
        for fn in sorted(filenames):
            fp = os.path.join(dirpath, fn)
            ext = os.path.splitext(fn)[1].lower()
            if ext not in ALLOWED_EXTENSIONS:
                continue
            rel = os.path.relpath(fp, root)
            try:
                size = os.path.getsize(fp)
            except OSError:
                size = 0
            tree.append({"path": rel, "name": fn, "size": size, "ext": ext})
    tree.sort(key=lambda x: x["path"])
    return JSONResponse(content={"root": "app/", "files": tree})


@app.get("/api/dev/file")
async def dev_read_file(path: str, user: CurrentUser = Depends(get_current_user)):
    """Read a file's content. Path is relative to app/."""
    full = EDITABLE_ROOT / path
    if not _is_safe_path(str(full)):
        return JSONResponse(content={"error": "Path not allowed"}, status_code=403)
    if not full.exists():
        return JSONResponse(content={"error": "File not found"}, status_code=404)
    try:
        content = full.read_text(encoding="utf-8")
        return JSONResponse(content={"path": path, "content": content, "size": len(content)})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.post("/api/dev/file")
async def dev_write_file(req: FileWriteRequest, user: CurrentUser = Depends(get_current_user)):
    """Write content to a file. Path is relative to app/. Server auto-reloads on .py changes."""
    full = EDITABLE_ROOT / req.path
    if not _is_safe_path(str(full)):
        return JSONResponse(content={"error": "Path not allowed"}, status_code=403)
    try:
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(req.content, encoding="utf-8")
        return JSONResponse(content={"ok": True, "path": req.path, "size": len(req.content)})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.get("/api/dev/search")
async def dev_search_files(q: str, user: CurrentUser = Depends(get_current_user)):
    """Search file contents for a string. Returns matching lines."""
    import re
    results = []
    root = str(EDITABLE_ROOT)
    pattern = re.compile(re.escape(q), re.IGNORECASE)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in BLOCKED_PATHS]
        for fn in filenames:
            ext = os.path.splitext(fn)[1].lower()
            if ext not in ALLOWED_EXTENSIONS:
                continue
            fp = os.path.join(dirpath, fn)
            rel = os.path.relpath(fp, root)
            try:
                with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                    for i, line in enumerate(f, 1):
                        if pattern.search(line):
                            results.append({"file": rel, "line": i, "text": line.rstrip()[:200]})
                            if len(results) >= 100:
                                return JSONResponse(content={"query": q, "results": results, "truncated": True})
            except Exception:
                continue
    return JSONResponse(content={"query": q, "results": results, "truncated": False})


# ================================================================
#  DEV CODING AGENT — Claude-powered code assistant with tool-use
# ================================================================

DEV_AGENT_TOOLS = [
    {
        "name": "read_file",
        "description": "Read the full contents of a source file. Path is relative to app/ (e.g. 'main.py', 'static/app.js', 'engine/monte_carlo.py').",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to app/"}
            },
            "required": ["path"]
        }
    },
    {
        "name": "write_file",
        "description": "Write new contents to a source file, completely replacing it. Use for code modifications. Python files auto-reload the server via uvicorn --reload.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to app/"},
                "content": {"type": "string", "description": "The complete new file contents"},
                "description": {"type": "string", "description": "Brief description of what was changed and why"}
            },
            "required": ["path", "content", "description"]
        }
    },
    {
        "name": "edit_file",
        "description": "Make a targeted edit to a file by replacing a specific string. More precise than write_file for small changes. The old_string must match exactly (including whitespace).",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to app/"},
                "old_string": {"type": "string", "description": "Exact string to find and replace (must be unique in the file)"},
                "new_string": {"type": "string", "description": "Replacement string"},
                "description": {"type": "string", "description": "Brief description of the change"}
            },
            "required": ["path", "old_string", "new_string", "description"]
        }
    },
    {
        "name": "search_code",
        "description": "Search across all source files for a pattern (case-insensitive). Returns matching lines with file paths and line numbers.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search string or pattern"},
                "file_filter": {"type": "string", "description": "Optional file extension filter like '.py' or '.js'"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "list_files",
        "description": "List all editable source files in the project with their sizes.",
        "input_schema": {
            "type": "object",
            "properties": {},
        }
    },
]

DEV_AGENT_SYSTEM = """You are a senior full-stack developer embedded in the VoLo Earth Ventures Underwriting Engine.
You have direct access to read, search, and modify the application source code.

TECH STACK:
- Backend: Python 3.11 + FastAPI + uvicorn (hot-reload enabled)
- Frontend: Vanilla JS + Chart.js + HTML/CSS (no framework)
- Engine: Monte Carlo simulation (5000 paths), Bass diffusion adoption curves, EBITDA-based EV model
- Auth: JWT (PyJWT) + SQLite
- Templates: Jinja2 (app/templates/index.html)
- Static: app/static/app.js, app/static/styles.css

KEY FILES:
- main.py: FastAPI routes, API endpoints, agent chat
- engine/monte_carlo.py: Core simulation engine
- engine/adoption.py: Bass diffusion S-curves
- engine/fund_model.py: Fund-level TVPI modeling
- engine/rvm_carbon.py: Carbon impact calculations
- auth.py: JWT authentication, user management
- static/app.js: All frontend logic (~2400 lines)
- static/styles.css: All styles
- templates/index.html: Single-page HTML template
- data/loader.py: Data loading utilities

IMPORTANT BEHAVIORS:
- Use read_file FIRST to understand existing code before making changes.
- For small changes (< 20 lines), prefer edit_file over write_file.
- For large changes or new files, use write_file.
- Always explain what you changed and why.
- When editing Python, the server auto-reloads. When editing JS/CSS/HTML, tell the user to hard-refresh (Cmd+Shift+R).
- Be careful with main.py — syntax errors will crash the server.
- Test your reasoning about the code before making changes.
- If you're unsure about the structure, use search_code or read_file first.
"""


class DevChatRequest(BaseModel):
    message: str
    conversation_history: Optional[list] = None
    current_file: Optional[str] = None
    current_file_content: Optional[str] = None


def _execute_dev_tool(tool_name: str, tool_input: dict) -> str:
    """Execute a dev agent tool and return the result as a string."""
    import re as _re

    if tool_name == "read_file":
        path = tool_input.get("path", "")
        full = EDITABLE_ROOT / path
        if not _is_safe_path(str(full)):
            return f"ERROR: Path '{path}' is not allowed."
        if not full.exists():
            return f"ERROR: File '{path}' not found."
        try:
            content = full.read_text(encoding="utf-8")
            lines = content.count('\n') + 1
            return f"[{path}] ({lines} lines, {len(content)} chars)\n\n{content}"
        except Exception as e:
            return f"ERROR reading {path}: {e}"

    elif tool_name == "write_file":
        path = tool_input.get("path", "")
        content = tool_input.get("content", "")
        desc = tool_input.get("description", "No description")
        full = EDITABLE_ROOT / path
        if not _is_safe_path(str(full)):
            return f"ERROR: Path '{path}' is not allowed."
        try:
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content, encoding="utf-8")
            lines = content.count('\n') + 1
            return f"SUCCESS: Wrote {path} ({lines} lines, {len(content)} chars). {desc}"
        except Exception as e:
            return f"ERROR writing {path}: {e}"

    elif tool_name == "edit_file":
        path = tool_input.get("path", "")
        old_str = tool_input.get("old_string", "")
        new_str = tool_input.get("new_string", "")
        desc = tool_input.get("description", "No description")
        full = EDITABLE_ROOT / path
        if not _is_safe_path(str(full)):
            return f"ERROR: Path '{path}' is not allowed."
        if not full.exists():
            return f"ERROR: File '{path}' not found."
        try:
            content = full.read_text(encoding="utf-8")
            count = content.count(old_str)
            if count == 0:
                return f"ERROR: old_string not found in {path}. Make sure whitespace and indentation match exactly."
            if count > 1:
                return f"ERROR: old_string found {count} times in {path}. Provide a more unique string to match."
            new_content = content.replace(old_str, new_str, 1)
            full.write_text(new_content, encoding="utf-8")
            return f"SUCCESS: Edited {path}. {desc}"
        except Exception as e:
            return f"ERROR editing {path}: {e}"

    elif tool_name == "search_code":
        query = tool_input.get("query", "")
        file_filter = tool_input.get("file_filter", "")
        pattern = _re.compile(_re.escape(query), _re.IGNORECASE)
        results = []
        root = str(EDITABLE_ROOT)
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in BLOCKED_PATHS]
            for fn in filenames:
                ext = os.path.splitext(fn)[1].lower()
                if ext not in ALLOWED_EXTENSIONS:
                    continue
                if file_filter and ext != file_filter:
                    continue
                fp = os.path.join(dirpath, fn)
                rel = os.path.relpath(fp, root)
                try:
                    with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                        for i, line in enumerate(f, 1):
                            if pattern.search(line):
                                results.append(f"  {rel}:{i}  {line.rstrip()[:120]}")
                                if len(results) >= 50:
                                    return f"Search '{query}': {len(results)}+ matches (truncated)\n" + "\n".join(results)
                except Exception:
                    continue
        if not results:
            return f"Search '{query}': No matches found."
        return f"Search '{query}': {len(results)} matches\n" + "\n".join(results)

    elif tool_name == "list_files":
        tree = []
        root = str(EDITABLE_ROOT)
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in BLOCKED_PATHS]
            for fn in sorted(filenames):
                ext = os.path.splitext(fn)[1].lower()
                if ext not in ALLOWED_EXTENSIONS:
                    continue
                fp = os.path.join(dirpath, fn)
                rel = os.path.relpath(fp, root)
                size = os.path.getsize(fp)
                tree.append(f"  {rel} ({size / 1024:.1f} KB)")
        return f"Project files ({len(tree)} editable):\n" + "\n".join(tree)

    return f"ERROR: Unknown tool '{tool_name}'"


@app.post("/api/dev/chat")
async def dev_chat_endpoint(req: DevChatRequest, user: CurrentUser = Depends(get_current_user)):
    """Agentic coding assistant with full tool-use loop — reads, edits, and writes code."""
    import anthropic

    # Resolve model from user preferences
    from .engine.llm_utils import make_llm_client
    prefs = get_model_preferences(user.id)
    dev_model_pref = prefs.get("dev_agent", MODEL_DEFAULTS["dev_agent"])
    is_refiant = dev_model_pref.startswith("qwen")

    if is_refiant:
        api_key = os.environ.get("REFIANT_API_KEY", "")
        if not api_key:
            return JSONResponse(content={"error": "REFIANT_API_KEY not configured"}, status_code=500)
    else:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            return JSONResponse(content={"error": "ANTHROPIC_API_KEY not configured"}, status_code=500)
    client = make_llm_client(is_refiant, api_key)

    # Build messages from history
    messages = []
    if req.conversation_history:
        for msg in req.conversation_history[-10:]:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})

    # Build user message with optional file context (keep small to avoid rate limits)
    user_content = req.message
    if req.current_file and req.current_file_content:
        file_ctx = req.current_file_content[:5000]
        user_content = f"[Currently viewing: {req.current_file} (first ~5K chars)]\n```\n{file_ctx}\n```\n\n{req.message}"

    messages.append({"role": "user", "content": user_content})

    try:
        # Agentic loop — let Claude call tools iteratively
        all_actions = []
        final_text = ""
        max_iterations = 8  # safety limit

        for _iteration in range(max_iterations):
            response = client.messages.create(
                model=dev_model_pref,
                max_tokens=4096,
                system=DEV_AGENT_SYSTEM,
                tools=DEV_AGENT_TOOLS,
                messages=messages,
            )

            # Collect text blocks and tool calls
            text_parts = []
            tool_calls = []
            for block in response.content:
                if block.type == "text":
                    text_parts.append(block.text)
                elif block.type == "tool_use":
                    tool_calls.append(block)

            if text_parts:
                final_text += " ".join(text_parts)

            # If no tool calls, we're done
            if not tool_calls or response.stop_reason == "end_turn":
                break

            # Execute each tool call and build tool results
            messages.append({"role": "assistant", "content": response.content})

            tool_results = []
            for tc in tool_calls:
                result_str = _execute_dev_tool(tc.name, tc.input)
                all_actions.append({
                    "tool": tc.name,
                    "input": tc.input,
                    "result": result_str[:500],  # truncate for frontend display
                    "success": not result_str.startswith("ERROR"),
                })
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": result_str,
                })

            messages.append({"role": "user", "content": tool_results})

        return JSONResponse(content={
            "reply": final_text,
            "actions": all_actions,
            "model": response.model,
            "usage": {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
        })
    except Exception as e:
        return JSONResponse(content={"error": f"Dev agent failed: {str(e)}"}, status_code=500)
