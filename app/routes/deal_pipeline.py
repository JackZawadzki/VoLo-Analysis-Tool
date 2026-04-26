"""
Deal Pipeline routes — the unified workflow endpoint.

POST /api/deal-pipeline/run     — run full analysis and generate report
GET  /api/deal-pipeline/reports — list saved reports
GET  /api/deal-pipeline/report/{id} — get a saved report
DELETE /api/deal-pipeline/report/{id}
GET  /api/deal-pipeline/report/{id}/pdf — export as PDF
GET  /api/deal-pipeline/export-csv — export all reports as CSV
POST /api/deal-pipeline/upload-portfolio — upload existing portfolio holdings
"""

import csv
import io
import json
import logging
import os
import tempfile
from typing import Optional, List

import numpy as np
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from ..auth import CurrentUser, get_current_user
from ..database import get_db
from ..engine.deal_report import generate_deal_report

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/deal-pipeline", tags=["deal-pipeline"])


class DealPipelineRequest(BaseModel):
    company_name: str = "Unnamed Deal"
    technology_description: Optional[str] = None
    archetype: str = "utility_solar"
    tam_millions: float = 50000
    trl: int = 5
    entry_stage: str = "Seed"
    check_size_millions: float = 2.0
    pre_money_millions: float = 15.0
    sector_profile: str = "Energy + Deep Tech"
    penetration_low: float = 0.01
    penetration_high: float = 0.05
    exit_multiple_low: Optional[float] = None
    exit_multiple_high: Optional[float] = None
    exit_year_min: int = 5
    exit_year_max: int = 10
    n_simulations: int = 5000
    random_seed: Optional[int] = None
    volume: dict = {}
    op_carbon: dict = {}
    emb_carbon: dict = {}
    portfolio: dict = {}
    risk_divisor: Optional[int] = None
    founder_revenue_projections: List[float] = []
    founder_volume_projections: List[float] = []
    founder_tam_claim: Optional[float] = None
    financial_model: Optional[dict] = None
    extraction_source: Optional[str] = None
    extraction_confidence: Optional[dict] = None
    extraction_data: Optional[dict] = None   # Full deck/FM extraction object for persistence
    custom_bass_p_mean: Optional[float] = None
    custom_bass_p_std: Optional[float] = None
    custom_bass_q_mean: Optional[float] = None
    custom_bass_q_std: Optional[float] = None
    custom_maturity: Optional[str] = None
    custom_inflection_year: Optional[int] = None
    fund_size_m: float = 100.0
    n_deals: int = 25
    mgmt_fee_pct: float = 2.0
    reserve_pct: float = 30.0
    max_concentration_pct: float = 15.0
    round_size_m: Optional[float] = None
    deal_commitment_type: str = "first_check"
    deal_follow_on_year: int = 2
    # Follow-on optimization parameters
    investment_type: str = "first"  # "first" or "followon"
    # Structured multi-round list (new, preferred)
    prior_investments: Optional[List[dict]] = None
    # Legacy flat params (preserved for backward compatibility)
    prior_first_check_m: Optional[float] = None
    prior_first_pre_money_m: Optional[float] = None
    prior_first_round_size_m: Optional[float] = None
    prior_first_entry_year: Optional[int] = None
    prior_first_entry_stage: Optional[str] = None
    followon_round_size_m_actual: Optional[float] = None
    followon_fund_year: Optional[int] = None
    entry_year: Optional[int] = None        # Calendar year of this specific investment
    fund_vintage_year: Optional[int] = None  # Fund Year 1 calendar year — master anchor for all charts
    save: bool = True


def _numpy_clean(obj):
    """Recursively convert numpy types to native Python."""
    if isinstance(obj, dict):
        return {k: _numpy_clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_numpy_clean(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        v = float(obj)
        if np.isnan(v) or np.isinf(v):
            return 0.0
        return v
    if isinstance(obj, np.ndarray):
        return _numpy_clean(obj.tolist())
    if isinstance(obj, float) and (np.isnan(obj) or np.isinf(obj)):
        return 0.0
    return obj


@router.post("/run")
def run_pipeline(req: DealPipelineRequest, user: CurrentUser = Depends(get_current_user)):
    from ..main import DATA_STORE, VALUATION_COMPS
    from ..database import load_committed_deals

    # Load committed deals so the simulator runs against the running portfolio
    committed_deals = load_committed_deals(user.id)

    custom_bass_p = None
    custom_bass_q = None
    if req.custom_bass_p_mean is not None and req.custom_bass_q_mean is not None:
        custom_bass_p = (req.custom_bass_p_mean, req.custom_bass_p_std or req.custom_bass_p_mean * 0.3)
        custom_bass_q = (req.custom_bass_q_mean, req.custom_bass_q_std or req.custom_bass_q_mean * 0.2)

    exit_mult = None
    if req.exit_multiple_low is not None and req.exit_multiple_high is not None:
        exit_mult = (req.exit_multiple_low, req.exit_multiple_high)

    try:
        report = generate_deal_report(
            company_name=req.company_name,
            technology_description=req.technology_description,
            archetype=req.archetype,
            tam_millions=req.tam_millions,
            trl=req.trl,
            entry_stage=req.entry_stage,
            check_size_millions=req.check_size_millions,
            pre_money_millions=req.pre_money_millions,
            sector_profile=req.sector_profile,
            carta_data=DATA_STORE.get("carta_rounds", {}),
            penetration_share=(req.penetration_low, req.penetration_high),
            exit_multiple_range=exit_mult,
            exit_year_range=(req.exit_year_min, req.exit_year_max),
            n_simulations=req.n_simulations,
            random_seed=req.random_seed,
            volume=req.volume,
            op_carbon=req.op_carbon,
            emb_carbon=req.emb_carbon,
            portfolio=req.portfolio,
            risk_divisor=req.risk_divisor,
            founder_revenue_projections=req.founder_revenue_projections,
            founder_volume_projections=req.founder_volume_projections,
            founder_tam_claim=req.founder_tam_claim,
            extraction_source=req.extraction_source,
            extraction_confidence=req.extraction_confidence,
            custom_bass_p=custom_bass_p,
            custom_bass_q=custom_bass_q,
            custom_maturity=req.custom_maturity,
            custom_inflection_year=req.custom_inflection_year,
            comps_data=VALUATION_COMPS if VALUATION_COMPS else None,
            financial_model=req.financial_model,
            fund_size_m=req.fund_size_m,
            n_deals=req.n_deals,
            mgmt_fee_pct=req.mgmt_fee_pct,
            reserve_pct=req.reserve_pct,
            max_concentration_pct=req.max_concentration_pct,
            round_size_m=req.round_size_m,
            committed_deals=committed_deals,
            deal_commitment_type=req.deal_commitment_type,
            deal_follow_on_year=req.deal_follow_on_year,
            investment_type=req.investment_type,
            prior_investments=req.prior_investments,
            prior_first_check_m=req.prior_first_check_m,
            prior_first_pre_money_m=req.prior_first_pre_money_m,
            prior_first_round_size_m=req.prior_first_round_size_m,
            prior_first_entry_year=req.prior_first_entry_year,
            prior_first_entry_stage=req.prior_first_entry_stage,
            followon_round_size_m_actual=req.followon_round_size_m_actual,
            followon_fund_year=req.followon_fund_year,
            entry_year=req.entry_year,
            fund_vintage_year=req.fund_vintage_year,
        )
    except Exception as e:
        # Log full traceback to the deployment console so we can diagnose
        # exactly which line failed; surface a one-line summary to the
        # client (with the error type so 'str has no attribute get' style
        # messages are at least classifiable from the UI).
        import traceback
        tb = traceback.format_exc()
        logger.error(f"[deal-pipeline /run] Pipeline failed: {type(e).__name__}: {e}\n{tb}")
        raise HTTPException(500, f"Pipeline failed: {type(e).__name__}: {e}")

    cleaned = _numpy_clean(report)
    response = {k: v for k, v in cleaned.items() if not k.startswith("_raw")}

    if req.save:
        db = get_db()
        try:
            inputs_json = req.dict()
            inputs_json.pop("save", None)
            # Preserve _raw_moic in stored report (needed for fund commitment)
            # but strip from API response (too large for frontend).
            stored_report = dict(cleaned)
            raw_moic = report.get("_raw_moic")
            if raw_moic is not None:
                stored_report["_raw_moic"] = _numpy_clean(raw_moic)
            cur = db.execute(
                """INSERT INTO deal_reports
                   (owner_id, company_name, archetype, entry_stage,
                    report_json, inputs_json, extraction_json)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    user.id, req.company_name, req.archetype, req.entry_stage,
                    json.dumps(stored_report, default=str),
                    json.dumps(_numpy_clean(inputs_json), default=str),
                    json.dumps(req.extraction_confidence or {}, default=str),
                ),
            )
            db.commit()
            response["report_id"] = cur.lastrowid
        except Exception as exc:
            logger.warning("Failed to save deal report to database: %s", exc)
        finally:
            db.close()

    return response


@router.get("/reports")
def list_reports(user: CurrentUser = Depends(get_current_user)):
    db = get_db()
    try:
        if user.role == "admin":
            rows = db.execute(
                "SELECT id, company_name, archetype, entry_stage, status, created_at FROM deal_reports ORDER BY created_at DESC"
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT id, company_name, archetype, entry_stage, status, created_at FROM deal_reports WHERE owner_id=? ORDER BY created_at DESC",
                (user.id,),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()


@router.get("/report/{rid}")
def get_report(rid: int, user: CurrentUser = Depends(get_current_user)):
    """Fetch a deal report. Visible to any authenticated VoLo team member —
    the team library is shared so analysts can review each other's work."""
    db = get_db()
    try:
        row = db.execute(
            "SELECT r.*, u.username AS owner_username "
            "FROM deal_reports r LEFT JOIN users u ON u.id = r.owner_id "
            "WHERE r.id=?",
            (rid,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Report not found")
        return {
            "id": row["id"],
            "company_name": row["company_name"],
            "archetype": row["archetype"],
            "entry_stage": row["entry_stage"],
            "report": json.loads(row["report_json"]),
            "inputs": json.loads(row["inputs_json"]),
            "created_at": row["created_at"],
            "owner_username": row["owner_username"],
        }
    finally:
        db.close()


@router.get("/report/{rid}/source-data")
def get_report_source_data(rid: int, user: CurrentUser = Depends(get_current_user)):
    """Return the raw inputs that produced a deal report — deal terms,
    simulation parameters, pitch-deck extraction, and the extracted financial
    model. Used by the library folder modal so any team member can audit
    what the simulation actually consumed."""
    db = get_db()
    try:
        row = db.execute(
            "SELECT r.*, u.username AS owner_username "
            "FROM deal_reports r LEFT JOIN users u ON u.id = r.owner_id "
            "WHERE r.id=?",
            (rid,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Report not found")
    finally:
        db.close()

    def _safe_json(s, fallback):
        try:
            return json.loads(s) if s else fallback
        except (json.JSONDecodeError, TypeError):
            return fallback

    inputs = _safe_json(row["inputs_json"], {}) or {}
    extraction = _safe_json(row["extraction_json"], {}) or {}

    # Carve the inputs into "deal terms" (the core deal shape) and "simulation
    # params" (the Monte Carlo knobs) so the UI can render them as two
    # readable groups instead of one giant blob.
    DEAL_TERM_KEYS = {
        "archetype", "entry_stage", "company_name",
        "tam_millions", "trl", "check_size_millions", "pre_money_millions",
        "ownership_pct", "sector_profile",
    }
    deal_terms = {k: v for k, v in inputs.items() if k in DEAL_TERM_KEYS and v not in (None, "")}
    simulation_params = {k: v for k, v in inputs.items() if k not in DEAL_TERM_KEYS and v not in (None, "")}

    # Extract financial-model-specific fields from extraction_json. The
    # banker/extractor stores them under various keys depending on path
    # (banker-agent vs legacy); pull what's there without assuming presence.
    financial_model = {
        "financials":     extraction.get("financials") or {},
        "units":          extraction.get("units") or {},
        "fiscal_years":   extraction.get("fiscal_years") or [],
        "scale_info":     extraction.get("scale_info") or "",
        "model_summary":  extraction.get("model_summary") or {},
        "file_name":      extraction.get("file_name") or "",
    }

    # Everything else in extraction_json is treated as deck-level extraction
    # (company description, market claims, team, etc.). Strip out the
    # financial-model keys and a couple of internal diagnostics so the UI
    # doesn't show noise.
    _STRIP = {"financials", "units", "fiscal_years", "scale_info", "model_summary",
              "file_name", "_diagnostics", "scenarios", "detected_scenarios",
              "primary_scenario", "records_count", "failures_count", "status"}
    deck_extraction = {k: v for k, v in extraction.items()
                       if k not in _STRIP and v not in (None, "", [], {})}

    return {
        "id": row["id"],
        "company_name": row["company_name"],
        "owner_username": row["owner_username"],
        "created_at": row["created_at"],
        "deal_terms": deal_terms,
        "simulation_params": simulation_params,
        "deck_extraction": deck_extraction,
        "financial_model": financial_model,
    }


@router.get("/library")
def list_library(user: CurrentUser = Depends(get_current_user)):
    """Shared team library — every deal report, DDR, and IC memo across all
    VoLo analysts, grouped by company name (case-insensitive). Used by the
    Deal Pipeline tab to show what's already been analyzed.
    """
    db = get_db()
    try:
        deals = db.execute(
            """SELECT r.id, r.company_name, r.archetype, r.entry_stage,
                      r.status, r.created_at, u.username AS owner_username
               FROM deal_reports r
               LEFT JOIN users u ON u.id = r.owner_id
               ORDER BY r.created_at DESC"""
        ).fetchall()
        memos = db.execute(
            """SELECT m.id, m.company_name, m.report_id, m.created_at,
                      m.model_used, u.username AS owner_username
               FROM generated_memos m
               LEFT JOIN users u ON u.id = m.owner_id
               WHERE m.status = 'completed' OR m.status = ''
               ORDER BY m.created_at DESC"""
        ).fetchall()
        ddrs = db.execute(
            """SELECT id, company_name, filename, generated_by, generated_at,
                      file_size_bytes
               FROM ddr_reports
               ORDER BY generated_at DESC"""
        ).fetchall()
    finally:
        db.close()

    def _norm(name: str) -> str:
        # Group "Mitra Chem", "mitra chem", "  Mitra  Chem  " together.
        return " ".join((name or "").strip().split()).lower()

    groups: dict[str, dict] = {}
    for d in deals:
        key = _norm(d["company_name"])
        if not key:
            continue
        g = groups.setdefault(key, {
            "company_name": d["company_name"].strip(),
            "deal_reports": [], "memos": [], "ddrs": [],
            "latest_at": d["created_at"],
        })
        g["deal_reports"].append({
            "id": d["id"],
            "owner_username": d["owner_username"] or "unknown",
            "created_at": d["created_at"],
            "archetype": d["archetype"],
            "entry_stage": d["entry_stage"],
            "status": d["status"],
        })
        if (d["created_at"] or "") > (g["latest_at"] or ""):
            g["latest_at"] = d["created_at"]

    for m in memos:
        key = _norm(m["company_name"])
        if not key:
            continue
        g = groups.setdefault(key, {
            "company_name": (m["company_name"] or "").strip() or "Untitled",
            "deal_reports": [], "memos": [], "ddrs": [],
            "latest_at": m["created_at"],
        })
        g["memos"].append({
            "id": m["id"],
            "owner_username": m["owner_username"] or "unknown",
            "created_at": m["created_at"],
            "report_id": m["report_id"],
            "model_used": m["model_used"],
        })
        if (m["created_at"] or "") > (g["latest_at"] or ""):
            g["latest_at"] = m["created_at"]

    for d in ddrs:
        key = _norm(d["company_name"])
        if not key:
            continue
        g = groups.setdefault(key, {
            "company_name": (d["company_name"] or "").strip() or "Untitled",
            "deal_reports": [], "memos": [], "ddrs": [],
            "latest_at": d["generated_at"],
        })
        g["ddrs"].append({
            "id": d["id"],
            "filename": d["filename"],
            "generated_by": d["generated_by"] or "unknown",
            "generated_at": d["generated_at"],
            "file_size_bytes": d["file_size_bytes"],
        })
        if (d["generated_at"] or "") > (g["latest_at"] or ""):
            g["latest_at"] = d["generated_at"]

    # Newest companies first
    library = sorted(groups.values(), key=lambda g: g["latest_at"] or "", reverse=True)
    return {"companies": library}


# ════════════════════════════════════════════════════════════════════════════
#  TEAM-SHARED ANALYST NOTES
#  ───────────────────────────────────────────────────────────────────────────
#  One markdown-style notes doc per company (keyed by normalized name so it
#  attaches to the library group, not a specific deal_report row). Any team
#  member can read or edit. Optimistic concurrency via a `version` integer:
#  the client sends the version it loaded; a stale write returns 409 so
#  users can refresh and merge rather than overwrite a colleague's edits.
#  Every save appends to deal_notes_history — nothing is ever lost.
# ════════════════════════════════════════════════════════════════════════════

_DEFAULT_NOTES_TEMPLATE = """## Initial Take
_First read of the materials — gut reaction, where this fits the thesis._



## Bull Case
_What has to be true for this to be a 10×+ outcome?_
-


## Bear Case / Concerns
_What could kill this? What are we skeptical of?_
-


## Key Diligence Questions
_Open items we need answered before deciding._
-


## Reference Calls & Conversations
_Who we've talked to, when, and what they said._
-


## Open Action Items
_To-dos with owners._
- [ ]


## Decision Tracker
_Current stance and how it's evolved._

"""


def _company_key(name: str) -> str:
    """Normalize company names so 'Mitra Chem', 'mitra chem', 'Mitra  Chem '
    all collapse to the same notes doc. Matches the library grouping logic."""
    return " ".join((name or "").strip().split()).lower()


class NotesGetResponse(BaseModel):
    company_key: str
    company_name: str
    content: str
    version: int
    last_edited_by_username: str
    last_edited_at: str
    exists: bool


class NotesSaveRequest(BaseModel):
    company_name: str
    content: str
    expected_version: int  # version the client loaded; mismatch => 409


@router.get("/notes")
def get_notes(company: str, user: CurrentUser = Depends(get_current_user)):
    """Fetch the analyst-notes doc for a company. Returns the default template
    (with version=0, exists=False) if no doc has been created yet."""
    key = _company_key(company)
    if not key:
        raise HTTPException(status_code=400, detail="company name is required")
    db = get_db()
    try:
        row = db.execute(
            "SELECT company_name, content, version, last_edited_by_username, "
            "last_edited_at FROM deal_notes WHERE company_key=?",
            (key,),
        ).fetchone()
    finally:
        db.close()

    if row:
        return {
            "company_key": key,
            "company_name": row["company_name"],
            "content": row["content"] or "",
            "version": int(row["version"] or 0),
            "last_edited_by_username": row["last_edited_by_username"] or "",
            "last_edited_at": row["last_edited_at"] or "",
            "exists": True,
        }
    return {
        "company_key": key,
        "company_name": company.strip(),
        "content": _DEFAULT_NOTES_TEMPLATE,
        "version": 0,
        "last_edited_by_username": "",
        "last_edited_at": "",
        "exists": False,
    }


# Cap notes at 1 MB. Analysts paste in long quotes, transcripts, and full
# email threads — a 200KB cap was too tight. 1 MB is still safely bounded
# (well under any DB row limit) but practically unlimited for a working doc.
_MAX_NOTES_BYTES = 1 * 1024 * 1024


@router.put("/notes")
def save_notes(req: NotesSaveRequest, user: CurrentUser = Depends(get_current_user)):
    """Save the notes doc with optimistic-concurrency check.

    Returns 409 with the current state if the client's expected_version is
    stale (i.e. someone else saved between this client's load and save).
    """
    key = _company_key(req.company_name)
    if not key:
        raise HTTPException(status_code=400, detail="company name is required")
    if len(req.content.encode("utf-8")) > _MAX_NOTES_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Notes exceed {_MAX_NOTES_BYTES // 1024} KB. Trim and try again.",
        )

    db = get_db()
    try:
        # Wrap the read-then-write in a single transaction so two concurrent
        # saves can't both think they're at the same version.
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT version FROM deal_notes WHERE company_key=?", (key,)
        ).fetchone()
        current_version = int(row["version"] or 0) if row else 0

        if req.expected_version != current_version:
            # Someone else got there first. Rollback and return current state.
            db.execute("ROLLBACK")
            cur = db.execute(
                "SELECT company_name, content, version, last_edited_by_username, "
                "last_edited_at FROM deal_notes WHERE company_key=?",
                (key,),
            ).fetchone()
            return JSONResponse(
                status_code=409,
                content={
                    "detail": "Someone else edited these notes since you opened them.",
                    "current": {
                        "company_key": key,
                        "company_name": (cur["company_name"] if cur else req.company_name),
                        "content": (cur["content"] if cur else "") or "",
                        "version": int((cur["version"] if cur else 0) or 0),
                        "last_edited_by_username": (cur["last_edited_by_username"] if cur else "") or "",
                        "last_edited_at": (cur["last_edited_at"] if cur else "") or "",
                        "exists": cur is not None,
                    },
                },
            )

        new_version = current_version + 1
        if row:
            db.execute(
                "UPDATE deal_notes SET company_name=?, content=?, version=?, "
                "last_edited_by=?, last_edited_by_username=?, "
                "last_edited_at=datetime('now') WHERE company_key=?",
                (req.company_name.strip(), req.content, new_version,
                 user.id, user.username, key),
            )
        else:
            db.execute(
                "INSERT INTO deal_notes (company_key, company_name, content, version, "
                "last_edited_by, last_edited_by_username) VALUES (?,?,?,?,?,?)",
                (key, req.company_name.strip(), req.content, new_version,
                 user.id, user.username),
            )

        # Append to history — every save preserved.
        db.execute(
            "INSERT INTO deal_notes_history (company_key, content, version, "
            "edited_by, edited_by_username) VALUES (?,?,?,?,?)",
            (key, req.content, new_version, user.id, user.username),
        )
        db.commit()

        out = db.execute(
            "SELECT company_name, content, version, last_edited_by_username, "
            "last_edited_at FROM deal_notes WHERE company_key=?",
            (key,),
        ).fetchone()
    finally:
        db.close()

    return {
        "company_key": key,
        "company_name": out["company_name"],
        "content": out["content"] or "",
        "version": int(out["version"] or 0),
        "last_edited_by_username": out["last_edited_by_username"] or "",
        "last_edited_at": out["last_edited_at"] or "",
        "exists": True,
    }


@router.get("/notes/history")
def get_notes_history(company: str, user: CurrentUser = Depends(get_current_user)):
    """List past versions of a company's notes (newest first). Each entry
    includes who saved it and when, so the team can see the audit trail."""
    key = _company_key(company)
    if not key:
        raise HTTPException(status_code=400, detail="company name is required")
    db = get_db()
    try:
        rows = db.execute(
            "SELECT id, content, version, edited_by_username, edited_at "
            "FROM deal_notes_history WHERE company_key=? "
            "ORDER BY version DESC LIMIT 50",
            (key,),
        ).fetchall()
    finally:
        db.close()
    return {
        "company_key": key,
        "history": [
            {
                "id": r["id"],
                "version": int(r["version"] or 0),
                "edited_by_username": r["edited_by_username"] or "",
                "edited_at": r["edited_at"] or "",
                "content": r["content"] or "",
            }
            for r in rows
        ],
    }


class QAReviewRequest(BaseModel):
    memo_markdown: Optional[str] = None   # current memo text to audit (may be empty)
    run_llm: bool = True                  # set False for a fast offline-only check


@router.post("/report/{rid}/qa-review")
def run_qa_review_endpoint(rid: int, req: QAReviewRequest, user: CurrentUser = Depends(get_current_user)):
    """
    Run a quality-assurance review on a saved deal report.
    Pass 1 — deterministic math/logic checks against stored data.
    Pass 2 — Claude scans memo text for number discrepancies and internal inconsistencies.
    """
    from ..engine.qa_review import run_qa_review

    db = get_db()
    try:
        row = db.execute("SELECT * FROM deal_reports WHERE id=?", (rid,)).fetchone()
        if not row:
            raise HTTPException(404, "Report not found")
        if row["owner_id"] != user.id and user.role != "admin":
            raise HTTPException(403, "Forbidden")

        report = json.loads(row["report_json"] or "{}")
        inputs = json.loads(row["inputs_json"] or "{}")

        result = run_qa_review(
            report=report,
            inputs=inputs,
            memo_markdown=req.memo_markdown or "",
            run_llm=req.run_llm,
        )
        return result
    finally:
        db.close()


class FinancialEditRequest(BaseModel):
    """Manual edit to one or more financial metrics for a saved deal."""
    financials: dict  # e.g. {"ebitda": {"2024": -224770, "2025": -737027, ...}, "revenue": {...}}


@router.put("/report/{rid}/financials")
def update_report_financials(rid: int, req: FinancialEditRequest, user: CurrentUser = Depends(get_current_user)):
    """
    Manually edit extracted financial data for a deal report.
    Updates both inputs_json.financial_model.financials and report_json.financial_model.financials.
    """
    db = get_db()
    try:
        row = db.execute("SELECT * FROM deal_reports WHERE id=?", (rid,)).fetchone()
        if not row:
            raise HTTPException(404, "Report not found")
        if row["owner_id"] != user.id and user.role != "admin":
            raise HTTPException(403, "Forbidden")

        inputs = json.loads(row["inputs_json"])
        report = json.loads(row["report_json"])

        # Merge edits into inputs_json.financial_model.financials
        fm_inputs = inputs.get("financial_model", {})
        if not fm_inputs:
            fm_inputs = {"financials": {}, "fiscal_years": [], "units": {}}
            inputs["financial_model"] = fm_inputs
        fin = fm_inputs.setdefault("financials", {})
        all_years = set()
        for metric, series in req.financials.items():
            # Validate: series should be {year_str: number}
            cleaned = {}
            for yr_str, val in series.items():
                try:
                    yr_int = int(yr_str)
                    cleaned[str(yr_int)] = float(val) if val is not None else None
                    all_years.add(yr_int)
                except (ValueError, TypeError):
                    continue
            fin[metric] = cleaned

        # Update fiscal_years to include any new years
        existing_years = set(fm_inputs.get("fiscal_years", []))
        fm_inputs["fiscal_years"] = sorted(existing_years | all_years)

        # Tag as manually edited
        ms = fm_inputs.get("model_summary", {})
        ms["manually_edited"] = True
        ms["verified_statements"] = ms.get("verified_statements", "") + " [Manually corrected by user.]"
        fm_inputs["model_summary"] = ms

        # Mirror edits into report_json.financial_model
        fm_report = report.get("financial_model", {})
        if fm_report:
            rfin = fm_report.setdefault("financials", {})
            for metric, series in req.financials.items():
                cleaned = {}
                for yr_str, val in series.items():
                    try:
                        cleaned[str(int(yr_str))] = float(val) if val is not None else None
                    except (ValueError, TypeError):
                        continue
                rfin[metric] = cleaned
            fm_report["fiscal_years"] = fm_inputs["fiscal_years"]
            fm_report["has_data"] = True

        # Also update founder_revenue_projections if revenue was edited
        if "revenue" in req.financials:
            rev = fin["revenue"]
            sorted_years = sorted(rev.keys(), key=int)
            inputs["founder_revenue_projections"] = [
                round((rev.get(y, 0) or 0) / 1_000_000, 4) for y in sorted_years
            ]

        db.execute(
            "UPDATE deal_reports SET inputs_json=?, report_json=? WHERE id=?",
            (json.dumps(inputs, default=str), json.dumps(report, default=str), rid)
        )
        db.commit()

        return {
            "ok": True,
            "report_id": rid,
            "updated_metrics": list(req.financials.keys()),
            "fiscal_years": fm_inputs["fiscal_years"],
        }
    finally:
        db.close()


def _render_pdf_html(report: dict) -> str:
    """Render the deal report as a self-contained HTML string for PDF conversion."""
    from jinja2 import Environment, FileSystemLoader
    import os

    template_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates")
    env = Environment(loader=FileSystemLoader(template_dir))

    try:
        template = env.get_template("report_pdf.html")
        return template.render(report=report)
    except Exception:
        return _fallback_pdf_html(report)


def _fallback_pdf_html(r: dict) -> str:
    """Simple fallback if the Jinja template fails."""
    ov = r.get("deal_overview", {})
    hero = r.get("hero_metrics", {})
    sim = r.get("simulation", {})
    carbon = r.get("carbon_impact", {})
    carbon_out = carbon.get("outputs", {})
    founder = r.get("founder_comparison", {})

    def fmt(v, d=2):
        if v is None:
            return "N/A"
        return f"{v:,.{d}f}" if isinstance(v, (int, float)) else str(v)

    prob = sim.get("probability", {})
    moic = sim.get("moic_unconditional", {})

    company_name = ov.get('company_name', 'Deal Report')
    entry_stage = ov.get('entry_stage', '')
    archetype = ov.get('archetype', '')
    trl_val = ov.get('trl', '')
    check_str = fmt(ov.get('check_size_millions', 0), 1)
    expected_moic_str = fmt(hero.get('expected_moic'), 2)
    p_gt_3x_str = fmt((hero.get('p_gt_3x', 0) or 0) * 100, 1)
    volo_risk_adj_str = fmt(carbon_out.get('volo_risk_adj'), 0)
    risk_adj_tpd_str = fmt(carbon_out.get('risk_adj_tpd'), 4)

    expected_moic_val = fmt(moic.get('expected'), 2)
    p50_all_val = fmt(moic.get('p50_all'), 2)
    p_loss = fmt((prob.get('total_loss', 0) or 0) * 100, 1)
    p_1x = fmt((prob.get('gt_1x', 0) or 0) * 100, 1)
    p_3x = fmt((prob.get('gt_3x', 0) or 0) * 100, 1)
    p_5x = fmt((prob.get('gt_5x', 0) or 0) * 100, 1)
    p_10x = fmt((prob.get('gt_10x', 0) or 0) * 100, 1)
    surv = fmt((sim.get('survival_rate', 0) or 0) * 100, 1)

    co_tonnes = fmt(carbon_out.get('company_tonnes'), 2)
    volo_pro = fmt(carbon_out.get('volo_prorata'), 2)
    volo_ra = fmt(carbon_out.get('volo_risk_adj'), 2)
    tpd = fmt(carbon_out.get('tonnes_per_dollar'), 4)

    founder_narrative = founder.get("revenue", {}).get("narrative", "No founder projection data available.") if founder.get("has_data") else "No founder projection data available."
    founder_position = founder.get("revenue", {}).get("position", "N/A") if founder.get("has_data") else "N/A"

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>VoLo Deal Report — {company_name}</title>
<style>
body{{font-family:Inter,-apple-system,sans-serif;margin:40px;color:#1a2332;font-size:12px;line-height:1.6}}
h1{{font-size:24px;color:#5B7744;margin-bottom:4px}}
h2{{font-size:16px;color:#2C3E50;border-bottom:2px solid #E1E4E8;padding-bottom:6px;margin-top:28px}}
.hero{{display:flex;gap:24px;margin:20px 0}}
.hero-card{{flex:1;background:#f9fafb;border:1px solid #e1e4e8;border-radius:8px;padding:16px;text-align:center}}
.hero-card .val{{font-size:22px;font-weight:700;font-family:monospace}}
.hero-card .lbl{{font-size:10px;color:#586069;text-transform:uppercase}}
table{{width:100%;border-collapse:collapse;margin:12px 0;font-size:11px}}
th,td{{padding:6px 10px;border:1px solid #e1e4e8;text-align:left}}
th{{background:#f4f5f7;font-weight:600;font-size:10px;text-transform:uppercase}}
.footer{{margin-top:40px;padding-top:12px;border-top:1px solid #e1e4e8;font-size:9px;color:#8b949e;text-align:center}}
@page{{margin:1in}}
</style></head><body>
<h1>{company_name}</h1>
<p style="color:#586069">{entry_stage} &middot; {archetype} &middot; TRL {trl_val} &middot; ${check_str}M check</p>

<div class="hero">
<div class="hero-card"><div class="val">{expected_moic_str}x</div><div class="lbl">Expected MOIC</div></div>
<div class="hero-card"><div class="val">{p_gt_3x_str}%</div><div class="lbl">P(&gt;3x)</div></div>
<div class="hero-card"><div class="val">{volo_risk_adj_str}</div><div class="lbl">tCO2 Risk-Adj</div></div>
<div class="hero-card"><div class="val">{risk_adj_tpd_str}</div><div class="lbl">Risk-Adj t/$</div></div>
</div>

<h2>Return Analysis</h2>
<table><tr><th>Metric</th><th>Value</th></tr>
<tr><td>Expected MOIC (all paths)</td><td>{expected_moic_val}x</td></tr>
<tr><td>Median MOIC (all paths)</td><td>{p50_all_val}x</td></tr>
<tr><td>P(Total Loss)</td><td>{p_loss}%</td></tr>
<tr><td>P(&gt;1x)</td><td>{p_1x}%</td></tr>
<tr><td>P(&gt;3x)</td><td>{p_3x}%</td></tr>
<tr><td>P(&gt;5x)</td><td>{p_5x}%</td></tr>
<tr><td>P(&gt;10x)</td><td>{p_10x}%</td></tr>
<tr><td>Survival Rate</td><td>{surv}%</td></tr>
</table>

<h2>Carbon Impact</h2>
<table><tr><th>Metric</th><th>Value</th></tr>
<tr><td>Total Lifecycle CO2</td><td>{co_tonnes} t</td></tr>
<tr><td>VoLo Pro-Rata</td><td>{volo_pro} t</td></tr>
<tr><td>VoLo Risk-Adjusted</td><td>{volo_ra} t</td></tr>
<tr><td>Tonnes per Dollar</td><td>{tpd}</td></tr>
</table>

<h2>Founder Projection Comparison</h2>
<p>Position: <strong>{founder_position}</strong></p>
<p>{founder_narrative}</p>

<div class="footer">VoLo Earth Ventures — Quantitative Underwriting Engine &middot; Generated Report</div>
</body></html>"""


def _html_to_pdf(html: str, company_name: str) -> str:
    """Convert HTML to PDF. Tries weasyprint, falls back to just returning HTML as a file."""
    try:
        from weasyprint import HTML
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp_path = tmp.name
        HTML(string=html).write_pdf(tmp_path)
        return tmp_path
    except (ImportError, Exception) as exc:
        logger.debug("PDF generation unavailable, falling back to HTML: %s", exc)
        with tempfile.NamedTemporaryFile(suffix=".html", mode="w", encoding="utf-8", delete=False) as f:
            f.write(html)
            return f.name


# ── CSV Export ─────────────────────────────────────────────────────────────────

@router.get("/export-csv")
def export_csv(user: CurrentUser = Depends(get_current_user)):
    """Export all deal reports as a flat, CRM-ready CSV with every material field."""
    db = get_db()
    try:
        if user.role == "admin":
            rows = db.execute(
                "SELECT * FROM deal_reports ORDER BY created_at DESC"
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM deal_reports WHERE owner_id=? ORDER BY created_at DESC",
                (user.id,),
            ).fetchall()

        output = io.StringIO()
        writer = csv.writer(output)

        writer.writerow([
            # Deal Overview
            "Company",
            "Technology_Description",
            "Archetype",
            "Entry_Stage",
            "TRL",
            "TRL_Label",
            "Sector_Profile",
            # Deal Terms
            "Check_Size_M",
            "Round_Size_M",
            "PreMoney_M",
            "PostMoney_M",
            "Entry_Ownership_Pct",
            # Market
            "TAM_M",
            "Penetration_Low",
            "Penetration_High",
            "Exit_Multiple_Low",
            "Exit_Multiple_High",
            "Exit_Year_Low",
            "Exit_Year_High",
            "Comps_Derived_Multiples",
            # Fund
            "Fund_Size_M",
            # Hero Metrics
            "Expected_MOIC",
            "Expected_IRR",
            "Survival_Rate",
            "P_gt_1x",
            "P_gt_3x",
            "P_gt_5x",
            "P_gt_10x",
            "P_gt_20x",
            # MOIC Distribution (unconditional)
            "MOIC_P50",
            "MOIC_P75",
            "MOIC_P90",
            # MOIC Distribution (conditional on survival)
            "MOIC_Conditional_Mean",
            "MOIC_Conditional_Median",
            # Meaningful exits
            "Meaningful_Exit_Rate",
            # IRR
            "IRR_Conditional_Mean",
            "IRR_Conditional_Median",
            # Position Sizing
            "Recommended_Check_M",
            "Sizing_Method",
            "Kelly_Full_M",
            "Kelly_Half_M",
            "Optimizer_P10_Dollars",
            "Optimizer_P50_Dollars",
            "Optimizer_P90_Dollars",
            # Carbon Impact
            "tCO2_Company_Lifetime",
            "tCO2_VoLo_RiskAdj",
            "Tonnes_Per_Dollar_RiskAdj",
            # Portfolio Impact
            "TVPI_Base_Mean",
            "TVPI_With_Deal_Mean",
            "TVPI_Lift",
            "IRR_Base_P50",
            "IRR_New_P50",
            # Revenue Source
            "Revenue_Source",
            # Extraction
            "Extraction_Source",
            # Audit
            "N_Simulations",
            "Random_Seed",
            "Risk_Divisor",
            "Computation_Time_ms",
            "Financial_Model_File",
            # Metadata
            "Created_At",
        ])

        for row in rows:
            try:
                report = json.loads(row["report_json"])
            except (json.JSONDecodeError, TypeError):
                continue

            ov = report.get("deal_overview", {})
            hero = report.get("hero_metrics", {})
            sim = report.get("simulation", {})
            prob = sim.get("probability", {})
            moic_u = sim.get("moic_unconditional", {})
            moic_c = sim.get("moic_conditional", {})
            irr_c = sim.get("irr_conditional", {})
            carbon = report.get("carbon_impact", {})
            co = carbon.get("outputs", {})
            pi = report.get("portfolio_impact", {})
            ps = report.get("position_sizing", {})
            audit = report.get("audit_trail", {})
            rev_traj = sim.get("revenue_trajectories", {})
            pen = ov.get("penetration_share", [])
            emr = ov.get("exit_multiple_range", [])
            eyr = ov.get("exit_year_range", [])

            optimal = ps.get("optimal") or ps.get("optimal_blended") or {}
            kelly = ps.get("kelly_reference", {})
            constraints = ps.get("fund_constraints", {})

            writer.writerow([
                # Deal Overview
                ov.get("company_name", ""),
                ov.get("technology_description", ""),
                ov.get("archetype", ""),
                ov.get("entry_stage", ""),
                ov.get("trl", ""),
                ov.get("trl_label", ""),
                ov.get("sector_profile", ""),
                # Deal Terms
                ov.get("check_size_millions", ""),
                ov.get("round_size_millions", ""),
                ov.get("pre_money_millions", ""),
                ov.get("post_money_millions", ""),
                ov.get("entry_ownership_pct", ""),
                # Market
                ov.get("tam_millions", ""),
                pen[0] if len(pen) > 0 else "",
                pen[1] if len(pen) > 1 else "",
                emr[0] if len(emr) > 0 else "",
                emr[1] if len(emr) > 1 else "",
                eyr[0] if len(eyr) > 0 else "",
                eyr[1] if len(eyr) > 1 else "",
                ov.get("comps_derived_multiples", ""),
                # Fund
                ov.get("fund_size_m", ""),
                # Hero Metrics
                hero.get("expected_moic", ""),
                hero.get("expected_irr", ""),
                hero.get("survival_rate", ""),
                prob.get("gt_1x", ""),
                prob.get("gt_3x", ""),
                prob.get("gt_5x", ""),
                prob.get("gt_10x", ""),
                prob.get("gt_20x", ""),
                # MOIC unconditional
                moic_u.get("p50_all", ""),
                moic_u.get("p75_all", ""),
                moic_u.get("p90_all", ""),
                # MOIC conditional
                moic_c.get("mean", ""),
                moic_c.get("median", ""),
                # Meaningful exits
                sim.get("meaningful_exit_rate", ""),
                # IRR conditional
                irr_c.get("mean", ""),
                irr_c.get("median", ""),
                # Position Sizing
                ps.get("recommended_check_m", optimal.get("check_m", "")),
                ps.get("sizing_method", ""),
                kelly.get("optimal_check_m", ""),
                kelly.get("half_kelly_check_m", ""),
                optimal.get("p10_dollar", ""),
                optimal.get("p50_dollar", ""),
                optimal.get("p90_dollar", ""),
                # Carbon
                co.get("company_tonnes", ""),
                co.get("volo_risk_adj", ""),
                co.get("risk_adj_tpd", ""),
                # Portfolio Impact
                pi.get("tvpi_base_mean", ""),
                pi.get("tvpi_new_mean", ""),
                pi.get("tvpi_mean_lift", ""),
                pi.get("irr_base_p50", ""),
                pi.get("irr_new_p50", ""),
                # Revenue Source
                rev_traj.get("source", ""),
                # Extraction
                audit.get("extraction_source", ""),
                # Audit
                audit.get("n_simulations", ""),
                audit.get("random_seed", ""),
                audit.get("risk_divisor", ""),
                audit.get("computation_time_ms", ""),
                audit.get("financial_model_file", ""),
                # Metadata
                row["created_at"],
            ])

        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=volo_deal_pipeline_export.csv"},
        )
    finally:
        db.close()


# ── Portfolio Upload ───────────────────────────────────────────────────────────

@router.post("/upload-portfolio")
async def upload_portfolio(
    file: UploadFile = File(...),
    user: CurrentUser = Depends(get_current_user),
):
    """
    Upload existing portfolio holdings as CSV or JSON.

    CSV columns: company_name, archetype, entry_stage, check_size_m,
    pre_money_m, entry_date, current_valuation_m, ownership_pct, trl, status

    Returns parsed holdings ready for portfolio simulation.
    """
    ext = os.path.splitext(file.filename)[1].lower()
    content = await file.read()
    text = content.decode("utf-8")

    holdings = []

    if ext == ".csv":
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            holding = {
                "company_name": row.get("company_name", row.get("name", "")),
                "archetype": row.get("archetype", ""),
                "entry_stage": row.get("entry_stage", row.get("stage", "")),
                "check_size_m": _safe_float(row.get("check_size_m", row.get("check_m", 0))),
                "pre_money_m": _safe_float(row.get("pre_money_m", row.get("pre_money", 0))),
                "entry_date": row.get("entry_date", row.get("date", "")),
                "current_valuation_m": _safe_float(row.get("current_valuation_m", row.get("current_val", 0))),
                "ownership_pct": _safe_float(row.get("ownership_pct", row.get("ownership", 0))),
                "trl": int(_safe_float(row.get("trl", 5))),
                "status": row.get("status", "active"),
            }
            if holding["company_name"]:
                holdings.append(holding)

    elif ext == ".json":
        try:
            data = json.loads(text)
            if isinstance(data, list):
                holdings = data
            elif isinstance(data, dict) and "holdings" in data:
                holdings = data["holdings"]
            else:
                holdings = [data]
        except json.JSONDecodeError as e:
            raise HTTPException(400, f"Invalid JSON: {e}")

    else:
        raise HTTPException(400, f"Unsupported file type: {ext}. Upload .csv or .json")

    if not holdings:
        raise HTTPException(422, "No valid holdings found in file")

    # Store in database
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

        for h in holdings:
            db.execute(
                """INSERT INTO portfolio_holdings
                   (owner_id, company_name, archetype, entry_stage, check_size_m,
                    pre_money_m, entry_date, current_valuation_m, ownership_pct, trl, status)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    user.id,
                    h.get("company_name", ""),
                    h.get("archetype", ""),
                    h.get("entry_stage", ""),
                    h.get("check_size_m", 0),
                    h.get("pre_money_m", 0),
                    h.get("entry_date", ""),
                    h.get("current_valuation_m", 0),
                    h.get("ownership_pct", 0),
                    h.get("trl", 5),
                    h.get("status", "active"),
                ),
            )
        db.commit()

        total_invested = sum(h.get("check_size_m", 0) for h in holdings)
        total_current = sum(h.get("current_valuation_m", 0) for h in holdings)

        return {
            "status": "ok",
            "holdings_count": len(holdings),
            "holdings": holdings,
            "summary": {
                "total_invested_m": round(total_invested, 2),
                "total_current_valuation_m": round(total_current, 2),
                "gross_moic": round(total_current / total_invested, 2) if total_invested > 0 else 0,
                "n_active": sum(1 for h in holdings if h.get("status", "active") == "active"),
            },
        }
    finally:
        db.close()


def _safe_float(v, default=0.0):
    if v is None or v == "":
        return default
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


# ── Fund Commitment Management ─────────────────────────────────────────────


class CommitDealRequest(BaseModel):
    report_id: int
    commitment_type: str = "first_check"  # "first_check" or "follow_on"
    parent_id: Optional[int] = None       # parent commitment for follow-ons
    follow_on_year: int = 2               # year follow-on capital deploys (2-4)
    slot_index: Optional[int] = None      # auto-assign if omitted


@router.post("/fund/commit")
def commit_deal_to_fund(req: CommitDealRequest, user: CurrentUser = Depends(get_current_user)):
    """Commit a deal (from a saved report) to the running fund portfolio.

    Extracts the MOIC distribution and deal parameters from the report,
    stores them in fund_commitments so all future analyses run against
    the updated portfolio.
    """
    db = get_db()
    try:
        # Load the report
        row = db.execute("SELECT * FROM deal_reports WHERE id=?", (req.report_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Report not found")
        if row["owner_id"] != user.id and user.role != "admin":
            raise HTTPException(403, "Forbidden")

        report = json.loads(row["report_json"])
        inputs = json.loads(row["inputs_json"])
        sim = report.get("simulation", {})
        ov = report.get("deal_overview", {})
        hero = report.get("hero_metrics", {})

        # Extract MOIC distribution — stored in report_json by the pipeline
        raw_moic = report.get("_raw_moic", [])
        if not raw_moic:
            raw_moic = sim.get("_raw_moic", [])

        company_name = ov.get("company_name", row["company_name"])
        check_size_m = ov.get("check_size_millions", inputs.get("check_size_millions", 2.0))
        pre_money_m = ov.get("pre_money_millions", inputs.get("pre_money_millions", 15.0))
        ownership_pct = ov.get("entry_ownership_pct", 0)
        survival_rate = hero.get("survival_rate", sim.get("summary", {}).get("survival_rate", 0.3))
        moic_cond_mean = sim.get("moic_conditional", {}).get("mean", 3.0)
        exit_year_low = inputs.get("exit_year_min", 5)
        exit_year_high = inputs.get("exit_year_max", 10)

        # Auto-assign slot index
        if req.slot_index is not None:
            slot_index = req.slot_index
        else:
            max_slot = db.execute(
                "SELECT COALESCE(MAX(slot_index), -1) as mx FROM fund_commitments WHERE owner_id=? AND status='active'",
                (user.id,),
            ).fetchone()["mx"]
            slot_index = max_slot + 1

        # Validate commitment type
        ctype = req.commitment_type
        if ctype not in ("first_check", "follow_on"):
            raise HTTPException(400, "commitment_type must be 'first_check' or 'follow_on'")

        # Follow-ons need a valid parent or at least a matching company
        parent_id = req.parent_id
        if ctype == "follow_on" and parent_id:
            parent = db.execute(
                "SELECT id, company_name FROM fund_commitments WHERE id=? AND status='active'",
                (parent_id,),
            ).fetchone()
            if not parent:
                raise HTTPException(404, "Parent commitment not found")

        # Check for duplicate (same report+type already committed)
        existing = db.execute(
            "SELECT id FROM fund_commitments WHERE owner_id=? AND report_id=? AND commitment_type=? AND status='active'",
            (user.id, req.report_id, ctype),
        ).fetchone()
        if existing:
            raise HTTPException(409, f"Report #{req.report_id} is already committed as {ctype}")

        fo_year = max(1, min(req.follow_on_year, 10)) if ctype == "follow_on" else 0

        cur = db.execute(
            """INSERT INTO fund_commitments
               (owner_id, report_id, parent_id, company_name, archetype, entry_stage,
                commitment_type, check_size_m, pre_money_m, ownership_pct, survival_rate,
                moic_cond_mean, exit_year_low, exit_year_high,
                follow_on_year, moic_distribution_json, slot_index)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                user.id, req.report_id, parent_id, company_name,
                row["archetype"], row["entry_stage"],
                ctype, check_size_m, pre_money_m, ownership_pct,
                survival_rate, moic_cond_mean,
                exit_year_low, exit_year_high,
                fo_year, json.dumps(raw_moic), slot_index,
            ),
        )
        db.commit()

        return {
            "ok": True,
            "commitment_id": cur.lastrowid,
            "company_name": company_name,
            "commitment_type": ctype,
            "slot_index": slot_index,
            "check_size_m": check_size_m,
            "follow_on_year": fo_year,
        }
    finally:
        db.close()


@router.get("/fund/commitments")
def list_fund_commitments(user: CurrentUser = Depends(get_current_user)):
    """List all active fund commitments for the current user."""
    db = get_db()
    try:
        rows = db.execute(
            """SELECT fc.*, dr.company_name as report_company
               FROM fund_commitments fc
               LEFT JOIN deal_reports dr ON fc.report_id = dr.id
               WHERE fc.owner_id=? AND fc.status='active'
               ORDER BY fc.slot_index""",
            (user.id,),
        ).fetchall()

        commitments = []
        total_invested = 0
        for r in rows:
            d = dict(r)
            # Don't send the full MOIC distribution in the list response
            d.pop("moic_distribution_json", None)
            commitments.append(d)
            total_invested += d.get("check_size_m", 0)

        return {
            "commitments": commitments,
            "count": len(commitments),
            "total_invested_m": round(total_invested, 2),
        }
    finally:
        db.close()


@router.delete("/fund/commitment/{cid}")
def remove_fund_commitment(cid: int, user: CurrentUser = Depends(get_current_user)):
    """Remove a deal from the fund (mark as inactive)."""
    db = get_db()
    try:
        row = db.execute("SELECT owner_id FROM fund_commitments WHERE id=?", (cid,)).fetchone()
        if not row:
            raise HTTPException(404, "Commitment not found")
        if row["owner_id"] != user.id and user.role != "admin":
            raise HTTPException(403, "Forbidden")
        db.execute("UPDATE fund_commitments SET status='removed' WHERE id=?", (cid,))
        db.commit()
        return {"ok": True}
    finally:
        db.close()


