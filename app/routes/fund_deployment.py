"""
Fund II Deployment routes — deployment cadence tracking and sector allocation.

GET  /api/fund/config           — return fund parameters and quarterly plan
GET  /api/fund/companies        — get company-sector assignments
POST /api/fund/companies        — save company-sector assignments
GET  /api/fund/summary          — get deployment summary stats
"""

import json
import logging
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import CurrentUser, get_current_user
from ..database import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/fund", tags=["fund-deployment"])


# ── Fund II Static Configuration ────────────────────────────────────────────

FUND_II_CONFIG = {
    "name": "Fund II",
    "aum_m": 130.0,
    "mgmt_fee_pct": 0.20,
    "mgmt_fee_m": 26.0,
    "deployed_at_entry_pct": 0.55,
    "deployed_at_entry_m": 57.2,
    "reserves_pct": 0.45,
    "reserves_m": 46.8,
    "total_investable_m": 104.0,
    "target_company_count": 22,
    "avg_check_size_m": 2.6,
    "entry_ownership_pct": 55,
    "total_quarters": 28,
    "current_quarter": 11,
}

# Quarter labels: Q1=3Q23, Q2=4Q23, ..., Q11=1Q26
QUARTER_LABELS = [
    "3Q23", "4Q23", "1Q24", "2Q24", "3Q24", "4Q24",
    "1Q25", "2Q25", "3Q25", "4Q25", "1Q26", "2Q26",
    "3Q26", "4Q26", "1Q27", "2Q27", "3Q27", "4Q27",
    "1Q28", "2Q28", "3Q28", "4Q28", "1Q29", "2Q29",
    "3Q29", "4Q29", "1Q30", "2Q30",
]

# Planned: 1.1 deals/qtr, $2.86M NIC/qtr, RC starts Q7
PLANNED_NI = [1.1] * 28
PLANNED_NIC_M = [2.86] * 28
PLANNED_RC_M = [0.0] * 6 + [2.127] * 22  # Starts Q7 (index 6)

# Actual through Q11 (index 0–10)
ACTUAL_NI = [1, 1, 1, 0, 1, 1, 0, 1, 2, 1, 3, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None]
ACTUAL_NIC_M = [1.0, 0.75, 3.5, 0, 0.35, 5.0, 0, 4.5, 0, 5.0, 8.8, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None]
ACTUAL_RC_PLANNED_M = [1.0, 0.75, 0.5, 0, 0, 0, 0, 1.5, 0, 0, 7.5, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None]
ACTUAL_RC_DEPLOYED_M = [0, 0, 0, 0, 0.75, 0, 0.5, 0, 0, 0, 6.2, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None]

# Updated plan: starts Q9 (index 8), NI=1.111/qtr, NIC=$3.116M, RC=$2.446M
UPDATED_NI = [None] * 8 + [1.111] * 20
UPDATED_NIC_M = [None] * 8 + [3.116] * 20
UPDATED_RC_M = [None] * 8 + [2.446] * 20
UPDATED_AVG_CHECK_M = [None] * 8 + [2.804] * 20

# Default company data — sourced directly from Excel rows 22-23.
# Verticals from row 23; "?" means the cell was blank in the spreadsheet (user to complete).
# Types (Software/Hardware/HE-SaaS) from row 27 "Vertical" — row was blank, so "?" for user to fill.
# Notes on Q9: 2 NI# recorded but no NIC amount or company name entered in spreadsheet yet.
# Notes on Q5: row 22 col G reads "hestia, archetype" with 1 NI# — Hestia is the new investment;
#              Archetype appears as context (co-investor or small follow-on note), NIC = $350K total.
# Notes on Q11 reserves: row 23 col M records reserve follow-ons (Magrathea $2M, XGS $1.5M,
#              Reframe $1.5M, Cambium $2.5M) deployed same quarter — listed separately below.
DEFAULT_COMPANIES = [
    # ── New investments (NIC) — from row 22 company names + row 23 industry + row 15 NIC ──
    # Q1  3Q23 — Magrathea $1.0M; sector not assigned in spreadsheet
    {"quarter": 1,  "label": "3Q23", "name": "Magrathea",    "nic_m": 1.00,  "vertical": "?",        "type": "?"},
    # Q2  4Q23 — Archetype $750K; row 23 col D = AI
    {"quarter": 2,  "label": "4Q23", "name": "Archetype",    "nic_m": 0.75,  "vertical": "AI",       "type": "?"},
    # Q3  1Q24 — XGS $3.5M; row 23 col E = Energy
    {"quarter": 3,  "label": "1Q24", "name": "XGS",          "nic_m": 3.50,  "vertical": "Energy",   "type": "?"},
    # Q5  3Q24 — Hestia $350K (row 22 col G: "hestia, archetype", NI#=1); row 23 col G = Energy
    {"quarter": 5,  "label": "3Q24", "name": "Hestia",       "nic_m": 0.35,  "vertical": "Energy",   "type": "?"},
    # Q6  4Q24 — Cambium $5.0M; row 23 col H = Buildings
    {"quarter": 6,  "label": "4Q24", "name": "Cambium",      "nic_m": 5.00,  "vertical": "Buildings","type": "?"},
    # Q8  2Q25 — Reframe $4.5M; row 23 col J = buildings
    {"quarter": 8,  "label": "2Q25", "name": "Reframe",      "nic_m": 4.50,  "vertical": "Buildings","type": "?"},
    # Q9  3Q25 — 2 NI# recorded, $0 NIC, no company name in spreadsheet — placeholder for user
    {"quarter": 9,  "label": "3Q25", "name": "TBD",          "nic_m": 0.00,  "vertical": "?",        "type": "?"},
    {"quarter": 9,  "label": "3Q25", "name": "TBD",          "nic_m": 0.00,  "vertical": "?",        "type": "?"},
    # Q10 4Q25 — Texture $5.0M (row 22 col L: "texture =5"); row 23 col L = Energy
    {"quarter": 10, "label": "4Q25", "name": "Texture",      "nic_m": 5.00,  "vertical": "Energy",   "type": "?"},
    # Q11 1Q26 — Refiant $4.3M, Fusion SPV $2.5M, NXLite $2.0M; sectors not assigned in spreadsheet
    {"quarter": 11, "label": "1Q26", "name": "Refiant",      "nic_m": 4.30,  "vertical": "?",        "type": "?"},
    {"quarter": 11, "label": "1Q26", "name": "Fusion SPV",   "nic_m": 2.50,  "vertical": "?",        "type": "?"},
    {"quarter": 11, "label": "1Q26", "name": "NXLite",       "nic_m": 2.00,  "vertical": "?",        "type": "?"},
    # ── Reserve follow-ons deployed Q11 (row 23 col M note) — RC not NIC ──────────────────
    {"quarter": 11, "label": "1Q26", "name": "Magrathea (reserve)",  "nic_m": 0.00, "rc_m": 2.00,  "vertical": "?",        "type": "?", "is_reserve": True},
    {"quarter": 11, "label": "1Q26", "name": "XGS (reserve)",        "nic_m": 0.00, "rc_m": 1.50,  "vertical": "Energy",   "type": "?", "is_reserve": True},
    {"quarter": 11, "label": "1Q26", "name": "Reframe (reserve)",    "nic_m": 0.00, "rc_m": 1.50,  "vertical": "Buildings","type": "?", "is_reserve": True},
    {"quarter": 11, "label": "1Q26", "name": "Cambium (reserve)",    "nic_m": 0.00, "rc_m": 2.50,  "vertical": "Buildings","type": "?", "is_reserve": True},
]


# ── Request / Response Models ─────────────────────────────────────────────────

class CompanyUpdate(BaseModel):
    name: str
    quarter: int
    nic_m: float
    vertical: str  # Energy, Buildings, AI, Transportation, etc.
    type: str      # Software, Hardware, HE-SaaS


class CompanySaveRequest(BaseModel):
    companies: List[dict]


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/config")
def get_fund_config(user: CurrentUser = Depends(get_current_user)):
    """Return fund parameters and full quarterly timeline."""
    quarters = []
    for i in range(28):
        quarters.append({
            "q": i + 1,
            "label": QUARTER_LABELS[i],
            "planned_ni": PLANNED_NI[i],
            "planned_nic_m": PLANNED_NIC_M[i],
            "planned_rc_m": PLANNED_RC_M[i],
            "actual_ni": ACTUAL_NI[i],
            "actual_nic_m": ACTUAL_NIC_M[i],
            "actual_rc_planned_m": ACTUAL_RC_PLANNED_M[i],
            "actual_rc_deployed_m": ACTUAL_RC_DEPLOYED_M[i],
            "updated_ni": UPDATED_NI[i],
            "updated_nic_m": UPDATED_NIC_M[i],
            "updated_rc_m": UPDATED_RC_M[i],
            "updated_avg_check_m": UPDATED_AVG_CHECK_M[i],
        })
    return {
        "fund": FUND_II_CONFIG,
        "quarters": quarters,
        "quarter_labels": QUARTER_LABELS,
    }


@router.get("/companies")
def get_companies(user: CurrentUser = Depends(get_current_user)):
    """Return saved company-sector assignments, falling back to defaults."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT companies_json FROM fund_ii_companies WHERE owner_id = ? ORDER BY updated_at DESC LIMIT 1",
            (user.id,),
        ).fetchone()
        if rows:
            return {"companies": json.loads(rows["companies_json"]), "source": "saved"}
        return {"companies": DEFAULT_COMPANIES, "source": "default"}
    finally:
        conn.close()


@router.post("/companies")
def save_companies(req: CompanySaveRequest, user: CurrentUser = Depends(get_current_user)):
    """Save company-sector assignments."""
    conn = get_db()
    try:
        # Upsert
        existing = conn.execute(
            "SELECT id FROM fund_ii_companies WHERE owner_id = ?", (user.id,)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE fund_ii_companies SET companies_json = ?, updated_at = CURRENT_TIMESTAMP WHERE owner_id = ?",
                (json.dumps(req.companies), user.id),
            )
        else:
            conn.execute(
                "INSERT INTO fund_ii_companies (owner_id, companies_json) VALUES (?, ?)",
                (user.id, json.dumps(req.companies)),
            )
        conn.commit()
        return {"status": "ok", "saved": len(req.companies)}
    finally:
        conn.close()


@router.get("/summary")
def get_deployment_summary(user: CurrentUser = Depends(get_current_user)):
    """Return deployment gap analysis vs plan."""
    current_q = FUND_II_CONFIG["current_quarter"]

    # Actuals through current quarter
    actual_deals = sum(x for x in ACTUAL_NI[:current_q] if x is not None)
    actual_nic = sum(x for x in ACTUAL_NIC_M[:current_q] if x is not None)
    actual_rc = sum(x for x in ACTUAL_RC_DEPLOYED_M[:current_q] if x is not None)

    # Planned through current quarter
    plan_deals = sum(PLANNED_NI[:current_q])
    plan_nic = sum(PLANNED_NIC_M[:current_q])
    plan_rc = sum(PLANNED_RC_M[:current_q])

    # Updated plan remaining
    updated_nic_remaining = sum(x for x in UPDATED_NIC_M[current_q:] if x is not None)
    updated_rc_remaining = sum(x for x in UPDATED_RC_M[current_q:] if x is not None)

    return {
        "current_quarter": current_q,
        "current_quarter_label": QUARTER_LABELS[current_q - 1],
        "actual_deals": actual_deals,
        "actual_nic_m": round(actual_nic, 2),
        "actual_rc_deployed_m": round(actual_rc, 2),
        "plan_deals": plan_deals,
        "plan_nic_m": round(plan_nic, 2),
        "plan_rc_m": round(plan_rc, 2),
        "deal_gap": round(plan_deals - actual_deals, 1),
        "nic_gap_m": round(plan_nic - actual_nic, 2),
        "rc_gap_m": round(plan_rc - actual_rc, 2),
        "updated_nic_remaining_m": round(updated_nic_remaining, 2),
        "updated_rc_remaining_m": round(updated_rc_remaining, 2),
        "pct_nic_deployed": round(actual_nic / FUND_II_CONFIG["deployed_at_entry_m"] * 100, 1),
    }
