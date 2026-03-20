"""
ARCHIVED: Unused deal pipeline endpoints.
- DELETE /report/{rid} — delete report
- GET /portfolio-holdings — list portfolio holdings
- DELETE /portfolio-holdings/{hid} — delete holding
- GET /fund/summary — fund summary

These endpoints are no longer used by the frontend.
Kept for reference if they need to be revived.

Date archived: 2026-03-17
"""

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from ..auth import CurrentUser, get_current_user
from ..database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/deal-pipeline", tags=["deal-pipeline-archived"])


@router.delete("/report/{rid}")
def delete_report(rid: int, user: CurrentUser = Depends(get_current_user)):
    """ARCHIVED: Delete a deal report."""
    db = get_db()
    try:
        row = db.execute("SELECT owner_id FROM deal_reports WHERE id=?", (rid,)).fetchone()
        if not row:
            raise HTTPException(404, "Not found")
        if row["owner_id"] != user.id and user.role != "admin":
            raise HTTPException(403, "Forbidden")
        db.execute("DELETE FROM deal_reports WHERE id=?", (rid,))
        db.commit()
        return {"ok": True}
    finally:
        db.close()


@router.get("/portfolio-holdings")
def list_portfolio_holdings(user: CurrentUser = Depends(get_current_user)):
    """ARCHIVED: List all uploaded portfolio holdings for the current user."""
    db = get_db()
    try:
        db.execute("""CREATE TABLE IF NOT EXISTS portfolio_holdings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER NOT NULL,
            company_name TEXT NOT NULL,
            archetype TEXT DEFAULT '',
            entry_stage TEXT DEFAULT '',
            check_size_m REAL DEFAULT 0,
            pre_money_m REAL DEFAULT 0,
            entry_date TEXT DEFAULT '',
            current_valuation_m REAL DEFAULT 0,
            ownership_pct REAL DEFAULT 0,
            trl INTEGER DEFAULT 5,
            status TEXT DEFAULT 'active',
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
        rows = db.execute(
            "SELECT * FROM portfolio_holdings WHERE owner_id=? ORDER BY uploaded_at DESC",
            (user.id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()


@router.delete("/portfolio-holdings/{hid}")
def delete_holding(hid: int, user: CurrentUser = Depends(get_current_user)):
    """ARCHIVED: Delete a portfolio holding."""
    db = get_db()
    try:
        row = db.execute("SELECT owner_id FROM portfolio_holdings WHERE id=?", (hid,)).fetchone()
        if not row:
            raise HTTPException(404, "Holding not found")
        if row["owner_id"] != user.id and user.role != "admin":
            raise HTTPException(403, "Forbidden")
        db.execute("DELETE FROM portfolio_holdings WHERE id=?", (hid,))
        db.commit()
        return {"ok": True}
    finally:
        db.close()


@router.get("/fund/summary")
def fund_summary(user: CurrentUser = Depends(get_current_user)):
    """ARCHIVED: Summary of the current fund state with committed deals."""
    from ..database import load_committed_deals
    committed = load_committed_deals(user.id)

    if not committed:
        return {
            "has_commitments": False,
            "n_committed": 0,
            "n_first_check": 0,
            "n_follow_on": 0,
            "total_invested_m": 0,
            "first_check_invested_m": 0,
            "follow_on_invested_m": 0,
            "remaining_slots": 20,
            "deals": [],
        }

    first_checks = [cd for cd in committed if cd.get("commitment_type", "first_check") == "first_check"]
    follow_ons = [cd for cd in committed if cd.get("commitment_type") == "follow_on"]
    total_invested = sum(cd["check_size_m"] for cd in committed)
    fc_invested = sum(cd["check_size_m"] for cd in first_checks)
    fo_invested = sum(cd["check_size_m"] for cd in follow_ons)

    deals = [
        {
            "id": cd["id"],
            "company_name": cd["company_name"],
            "check_size_m": cd["check_size_m"],
            "entry_stage": cd["entry_stage"],
            "commitment_type": cd.get("commitment_type", "first_check"),
            "follow_on_year": cd.get("follow_on_year", 0),
            "parent_id": cd.get("parent_id"),
            "slot_index": cd["slot_index"],
            "committed_at": cd["committed_at"],
        }
        for cd in committed
    ]

    return {
        "has_commitments": True,
        "n_committed": len(committed),
        "n_first_check": len(first_checks),
        "n_follow_on": len(follow_ons),
        "total_invested_m": round(total_invested, 2),
        "first_check_invested_m": round(fc_invested, 2),
        "follow_on_invested_m": round(fo_invested, 2),
        "remaining_slots": max(20 - len(first_checks), 1),
        "deals": deals,
    }
