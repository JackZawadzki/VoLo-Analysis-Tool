"""
DD Financial Analysis routes — scenario-based P&L modeling.

POST /api/dd/scenarios              — save/update a scenario set for a report
GET  /api/dd/scenarios/{report_id}  — get saved scenarios for a report
POST /api/dd/compute                — run scenario analysis (no save)
POST /api/dd/defaults               — get stage-appropriate default assumptions
DELETE /api/dd/scenario/{scenario_id} — delete a saved scenario
"""

import json
import logging
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import CurrentUser, get_current_user
from ..database import get_db
from ..engine.scenario_analysis import (
    get_default_assumptions,
    build_pnl_projection,
    compute_deal_returns,
    run_scenario_analysis,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/dd", tags=["dd-analysis"])


# ── Request / Response Models ──────────────────────────────────────

class FundraisingRound(BaseModel):
    label: str = "Series A"
    year: int = 2
    dilution_pct: float = 20.0
    needs_bridge: bool = False
    bridge_dilution_pct: float = 8.0
    bridge_delay_years: float = 0.5


class ScenarioAssumptions(BaseModel):
    scenario: str = "base"  # conservative, base, best_case
    time_to_launch_years: int = 0
    projection_years: int = 10
    revenue_y1_m: float = 1.0
    revenue_cagr_pct: float = 100.0
    custom_revenues: Optional[List[Optional[float]]] = None
    gross_margin_start_pct: float = 40.0
    gross_margin_end_pct: float = 65.0
    opex_rd_pct_rev: float = 50.0
    opex_sm_pct_rev: float = 30.0
    opex_ga_pct_rev: float = 15.0
    opex_rd_end_pct_rev: float = 15.0
    opex_sm_end_pct_rev: float = 12.0
    opex_ga_end_pct_rev: float = 7.0
    da_pct_rev: float = 3.0
    capex_pct_rev: float = 5.0
    nwc_pct_rev: float = 10.0
    tax_rate_pct: float = 0.0
    terminal_growth_pct: float = 3.0
    discount_rate_pct: float = 25.0
    exit_multiple_type: str = "ev_revenue"
    exit_multiple: float = 10.0
    fundraising_plan: Optional[List[dict]] = None


class DDComputeRequest(BaseModel):
    report_id: Optional[int] = None
    entry_stage: str = "Seed"
    check_size_m: float = 2.0
    pre_money_m: float = 15.0
    round_size_m: Optional[float] = None
    exit_year: Optional[int] = None
    dilution_per_round_pct: float = 20.0
    future_rounds: int = 2
    scenarios: dict  # {name: ScenarioAssumptions as dict}
    custom_revenues: Optional[dict] = None  # {name: [rev list]}
    fundraising_plans: Optional[dict] = None  # {name: [round dicts]}


class DDSaveRequest(BaseModel):
    report_id: int
    scenarios: dict  # {name: assumptions_dict}
    deal_params: dict  # check_size, pre_money, etc.
    notes: str = ""


class DDDefaultsRequest(BaseModel):
    entry_stage: str = "Seed"


# ── Endpoints ──────────────────────────────────────────────────────

@router.post("/defaults")
def get_defaults(req: DDDefaultsRequest, user: CurrentUser = Depends(get_current_user)):
    """Return default assumptions for all three scenarios given a stage."""
    return {
        "conservative": get_default_assumptions(req.entry_stage, "conservative"),
        "base": get_default_assumptions(req.entry_stage, "base"),
        "best_case": get_default_assumptions(req.entry_stage, "best_case"),
    }


@router.post("/compute")
def compute_scenarios(req: DDComputeRequest, user: CurrentUser = Depends(get_current_user)):
    """Run scenario analysis and return results without persisting."""
    try:
        result = run_scenario_analysis(
            scenarios=req.scenarios,
            check_size_m=req.check_size_m,
            pre_money_m=req.pre_money_m,
            round_size_m=req.round_size_m,
            exit_year=req.exit_year,
            dilution_per_round_pct=req.dilution_per_round_pct,
            future_rounds=req.future_rounds,
            custom_revenues=req.custom_revenues,
            fundraising_plans=req.fundraising_plans,
        )
        return {"status": "ok", "result": result}
    except Exception as e:
        logger.exception("DD compute failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/scenarios")
def save_scenarios(req: DDSaveRequest, user: CurrentUser = Depends(get_current_user)):
    """Save or update the scenario set for a deal report."""
    conn = get_db()
    try:
        # Check report exists and belongs to user
        row = conn.execute(
            "SELECT id FROM deal_reports WHERE id = ? AND owner_id = ?",
            (req.report_id, user.id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Report not found")

        # Upsert: delete old scenarios for this report, insert new
        conn.execute(
            "DELETE FROM dd_scenarios WHERE report_id = ? AND owner_id = ?",
            (req.report_id, user.id),
        )

        for name, assumptions in req.scenarios.items():
            conn.execute(
                """INSERT INTO dd_scenarios
                   (owner_id, report_id, scenario_name, assumptions_json,
                    deal_params_json, notes)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    user.id,
                    req.report_id,
                    name,
                    json.dumps(assumptions),
                    json.dumps(req.deal_params),
                    req.notes,
                ),
            )
        conn.commit()
        return {"status": "ok", "saved": len(req.scenarios)}
    finally:
        conn.close()


@router.get("/scenarios/{report_id}")
def get_scenarios(report_id: int, user: CurrentUser = Depends(get_current_user)):
    """Get saved scenarios for a deal report."""
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT id, scenario_name, assumptions_json, deal_params_json,
                      notes, created_at, updated_at
               FROM dd_scenarios
               WHERE report_id = ? AND owner_id = ?
               ORDER BY scenario_name""",
            (report_id, user.id),
        ).fetchall()

        scenarios = {}
        deal_params = {}
        notes = ""
        for row in rows:
            scenarios[row["scenario_name"]] = json.loads(row["assumptions_json"])
            if not deal_params:
                deal_params = json.loads(row["deal_params_json"])
                notes = row["notes"]

        return {
            "report_id": report_id,
            "scenarios": scenarios,
            "deal_params": deal_params,
            "notes": notes,
            "count": len(scenarios),
        }
    finally:
        conn.close()


@router.delete("/scenario/{scenario_id}")
def delete_scenario(scenario_id: int, user: CurrentUser = Depends(get_current_user)):
    """Delete a specific saved scenario."""
    conn = get_db()
    try:
        result = conn.execute(
            "DELETE FROM dd_scenarios WHERE id = ? AND owner_id = ?",
            (scenario_id, user.id),
        )
        conn.commit()
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Scenario not found")
        return {"status": "ok"}
    finally:
        conn.close()
