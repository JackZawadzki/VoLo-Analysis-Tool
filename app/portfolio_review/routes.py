"""
FastAPI routes for the portfolio_review module.

URL structure:
    GET  /portfolio-review/                       — index page (section list)
    GET  /portfolio-review/{section_slug}         — per-section page
    GET  /portfolio-review/company/{company_id}   — company detail page

JSON API (all under /api/portfolio-review):
    GET    /api/portfolio-review/companies                   — list companies
    POST   /api/portfolio-review/companies                   — create company
    GET    /api/portfolio-review/companies/{id}              — get company + investments + returns
    PUT    /api/portfolio-review/companies/{id}              — update company

    GET    /api/portfolio-review/returns?as_of=YYYY-MM-DD    — list returns
    GET    /api/portfolio-review/board-seats                 — list active board seats
    GET    /api/portfolio-review/sections                    — section catalog (slug, name, color)
    GET    /api/portfolio-review/dashboard                   — aggregate roll-up across sections

    GET    /api/portfolio-review/comments?entity_type=...&entity_key=...
    POST   /api/portfolio-review/comments
    DELETE /api/portfolio-review/comments/{id}               — soft delete (own comments only)

    POST   /api/portfolio-review/import                      — re-run Excel sync from server-side path (admin only)
    POST   /api/portfolio-review/import-upload               — multipart upload of an .xlsx workbook (admin only)
    GET    /api/portfolio-review/imports                     — list recent import runs

    GET    /api/portfolio-review/derisking                   — latest score per company
    POST   /api/portfolio-review/derisking/import            — upload Derisking Quadrants .xlsx
    POST   /api/portfolio-review/derisking/llm-score/{id}    — Claude scores one company against IC memo + decks + notes
    GET    /api/portfolio-review/derisking/by-stage          — companies grouped by current stage with YoY delta
    GET    /api/portfolio-review/derisking/{id}              — full score + per-dimension reasoning for one company
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from ..auth import CurrentUser, get_current_user, get_optional_user
from ..database import get_db
from .models import (
    SECTIONS, SECTION_SLUGS,
    CompanyIn, Company, InvestmentIn,
    CommentIn, Comment, EntityType,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["portfolio_review"])

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


# ── Helpers ───────────────────────────────────────────────────────────────────
def _row_to_dict(row) -> dict:
    """Convert sqlite3.Row to a plain dict."""
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def _section_for_slug(slug: str) -> Optional[dict]:
    return next((s for s in SECTIONS if s["slug"] == slug), None)


# ── HTML pages ────────────────────────────────────────────────────────────────
@router.get("/portfolio-review/", response_class=HTMLResponse)
def pr_index(request: Request, user: CurrentUser = Depends(get_optional_user)):
    """Section landing page with overview metrics."""
    conn = get_db()
    try:
        # Aggregate counts for the dashboard tile
        n_companies = conn.execute("SELECT COUNT(*) FROM pr_companies").fetchone()[0]
        n_investments = conn.execute("SELECT COUNT(*) FROM pr_investments").fetchone()[0]
        n_board_seats = conn.execute("SELECT COUNT(*) FROM pr_board_seats WHERE active=1").fetchone()[0]
        last_import_row = conn.execute(
            "SELECT * FROM pr_imports ORDER BY id DESC LIMIT 1"
        ).fetchone()
        last_import = _row_to_dict(last_import_row)
    finally:
        conn.close()

    return templates.TemplateResponse(request, "index.html", {
        "sections": SECTIONS,
        "n_companies": n_companies,
        "n_investments": n_investments,
        "n_board_seats": n_board_seats,
        "last_import": last_import,
        "user": user.model_dump() if user else None,
    })


@router.get("/portfolio-review/{section_slug}", response_class=HTMLResponse)
def pr_section_page(section_slug: str, request: Request,
                    user: CurrentUser = Depends(get_optional_user)):
    section = _section_for_slug(section_slug)
    if not section:
        raise HTTPException(404, f"Unknown section: {section_slug}")
    return templates.TemplateResponse(request, "section.html", {
        "section": section,
        "sections": SECTIONS,
        "active_section_slug": section_slug,
        "user": user.model_dump() if user else None,
    })


@router.get("/portfolio-review/company/{company_id}", response_class=HTMLResponse)
def pr_company_page(company_id: int, request: Request,
                    user: CurrentUser = Depends(get_optional_user)):
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM pr_companies WHERE id=?", (company_id,)).fetchone()
        if not row:
            raise HTTPException(404, f"Company {company_id} not found")
        company = _row_to_dict(row)
    finally:
        conn.close()
    return templates.TemplateResponse(request, "company.html", {
        "company": company,
        "sections": SECTIONS,
        "user": user.model_dump() if user else None,
    })


# ── JSON API ──────────────────────────────────────────────────────────────────
api = APIRouter(prefix="/api/portfolio-review", tags=["portfolio_review_api"])


@api.get("/sections")
def api_sections():
    return {"sections": SECTIONS}


@api.get("/companies")
def api_list_companies(fund: Optional[str] = None,
                       user: CurrentUser = Depends(get_current_user)):
    conn = get_db()
    try:
        if fund:
            rows = conn.execute(
                "SELECT * FROM pr_companies WHERE fund=? ORDER BY name", (fund,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM pr_companies ORDER BY name").fetchall()
        return {"companies": [_row_to_dict(r) for r in rows]}
    finally:
        conn.close()


@api.post("/companies")
def api_create_company(body: CompanyIn, user: CurrentUser = Depends(get_current_user)):
    conn = get_db()
    try:
        # Reject duplicate names
        existing = conn.execute("SELECT id FROM pr_companies WHERE name=?", (body.name,)).fetchone()
        if existing:
            raise HTTPException(409, f"Company '{body.name}' already exists")
        cur = conn.execute(
            """INSERT INTO pr_companies
            (name, fund, brief_description, sector, submarket, business_model,
             hw_sw, commercial_status, ceo_name, ceo_email, cfo_name, cfo_email,
             address, website, fume_date, first_year_revenue, hyperscale,
             notable_partners, next_round_expect)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (body.name, body.fund, body.brief_description, body.sector, body.submarket,
             body.business_model, body.hw_sw, body.commercial_status, body.ceo_name,
             body.ceo_email, body.cfo_name, body.cfo_email, body.address, body.website,
             body.fume_date, body.first_year_revenue, 1 if body.hyperscale else 0,
             body.notable_partners, body.next_round_expect),
        )
        conn.commit()
        new_id = cur.lastrowid
        row = conn.execute("SELECT * FROM pr_companies WHERE id=?", (new_id,)).fetchone()
        return _row_to_dict(row)
    finally:
        conn.close()


@api.get("/companies/{company_id}")
def api_get_company(company_id: int, user: CurrentUser = Depends(get_current_user)):
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM pr_companies WHERE id=?", (company_id,)).fetchone()
        if not row:
            raise HTTPException(404, f"Company {company_id} not found")
        company = _row_to_dict(row)
        company["investments"] = [_row_to_dict(r) for r in conn.execute(
            "SELECT * FROM pr_investments WHERE company_id=? ORDER BY investment_date", (company_id,)
        ).fetchall()]
        company["returns"] = [_row_to_dict(r) for r in conn.execute(
            "SELECT * FROM pr_returns WHERE company_id=? ORDER BY as_of_date DESC", (company_id,)
        ).fetchall()]
        company["board_seats"] = [_row_to_dict(r) for r in conn.execute(
            "SELECT * FROM pr_board_seats WHERE company_id=? AND active=1", (company_id,)
        ).fetchall()]
        return company
    finally:
        conn.close()


@api.put("/companies/{company_id}")
def api_update_company(company_id: int, body: CompanyIn,
                       user: CurrentUser = Depends(get_current_user)):
    conn = get_db()
    try:
        existing = conn.execute("SELECT id FROM pr_companies WHERE id=?", (company_id,)).fetchone()
        if not existing:
            raise HTTPException(404, f"Company {company_id} not found")
        conn.execute(
            """UPDATE pr_companies SET
                name=?, fund=?, brief_description=?, sector=?, submarket=?,
                business_model=?, hw_sw=?, commercial_status=?, ceo_name=?,
                ceo_email=?, cfo_name=?, cfo_email=?, address=?, website=?,
                fume_date=?, first_year_revenue=?, hyperscale=?, notable_partners=?,
                next_round_expect=?, updated_at=datetime('now')
            WHERE id=?""",
            (body.name, body.fund, body.brief_description, body.sector, body.submarket,
             body.business_model, body.hw_sw, body.commercial_status, body.ceo_name,
             body.ceo_email, body.cfo_name, body.cfo_email, body.address, body.website,
             body.fume_date, body.first_year_revenue, 1 if body.hyperscale else 0,
             body.notable_partners, body.next_round_expect, company_id),
        )
        conn.commit()
        return {"updated": company_id}
    finally:
        conn.close()


@api.get("/returns")
def api_returns(as_of: Optional[str] = None,
                user: CurrentUser = Depends(get_current_user)):
    conn = get_db()
    try:
        if as_of:
            rows = conn.execute(
                """SELECT r.*, c.name AS company_name, c.fund
                   FROM pr_returns r JOIN pr_companies c ON c.id = r.company_id
                   WHERE r.as_of_date = ? ORDER BY r.multiple DESC NULLS LAST""",
                (as_of,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT r.*, c.name AS company_name, c.fund
                   FROM pr_returns r
                   JOIN pr_companies c ON c.id = r.company_id
                   ORDER BY r.as_of_date DESC, r.multiple DESC""",
            ).fetchall()
        return {"returns": [_row_to_dict(r) for r in rows]}
    finally:
        conn.close()


@api.get("/board-seats")
def api_board_seats(user: CurrentUser = Depends(get_current_user)):
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT b.*, c.name AS company_name, c.fund
               FROM pr_board_seats b JOIN pr_companies c ON c.id = b.company_id
               WHERE b.active=1
               ORDER BY c.name""",
        ).fetchall()
        return {"board_seats": [_row_to_dict(r) for r in rows]}
    finally:
        conn.close()


@api.get("/dashboard")
def api_dashboard(user: CurrentUser = Depends(get_current_user)):
    """Aggregate roll-up: portfolio totals, top holdings, IRR distribution."""
    conn = get_db()
    try:
        d = {}
        d["n_companies"] = conn.execute("SELECT COUNT(*) FROM pr_companies").fetchone()[0]
        d["n_investments"] = conn.execute("SELECT COUNT(*) FROM pr_investments").fetchone()[0]
        d["total_invested"] = conn.execute(
            "SELECT COALESCE(SUM(investment_amount), 0) FROM pr_investments WHERE participated=1"
        ).fetchone()[0]
        d["total_fmv"] = conn.execute(
            """SELECT COALESCE(SUM(fmv), 0) FROM pr_returns r WHERE r.as_of_date = (
                SELECT MAX(as_of_date) FROM pr_returns r2 WHERE r2.company_id = r.company_id
            )"""
        ).fetchone()[0]
        # Top 5 by FMV
        d["top_holdings"] = [_row_to_dict(r) for r in conn.execute(
            """SELECT c.name, c.fund, r.fmv, r.cost, r.multiple
               FROM pr_returns r JOIN pr_companies c ON c.id = r.company_id
               WHERE r.as_of_date = (SELECT MAX(as_of_date) FROM pr_returns r2 WHERE r2.company_id = r.company_id)
               ORDER BY r.fmv DESC LIMIT 5"""
        ).fetchall()]
        return d
    finally:
        conn.close()


# ── Comments ─────────────────────────────────────────────────────────────────
@api.get("/comments")
def api_list_comments(entity_type: EntityType, entity_key: str,
                      user: CurrentUser = Depends(get_current_user)):
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT c.*, u.username AS user_username
               FROM pr_comments c LEFT JOIN users u ON u.id = c.user_id
               WHERE c.entity_type=? AND c.entity_key=? AND c.deleted=0
               ORDER BY c.created_at ASC""",
            (entity_type, entity_key),
        ).fetchall()
        return {"comments": [_row_to_dict(r) for r in rows]}
    finally:
        conn.close()


@api.post("/comments")
def api_post_comment(body: CommentIn, user: CurrentUser = Depends(get_current_user)):
    if not body.body.strip():
        raise HTTPException(400, "Comment body cannot be empty")
    conn = get_db()
    try:
        cur = conn.execute(
            """INSERT INTO pr_comments
            (user_id, entity_type, entity_key, parent_id, body)
            VALUES (?, ?, ?, ?, ?)""",
            (user.id, body.entity_type, body.entity_key, body.parent_id, body.body.strip()),
        )
        conn.commit()
        new_id = cur.lastrowid
        row = conn.execute(
            """SELECT c.*, u.username AS user_username
               FROM pr_comments c LEFT JOIN users u ON u.id = c.user_id
               WHERE c.id = ?""",
            (new_id,),
        ).fetchone()
        return _row_to_dict(row)
    finally:
        conn.close()


@api.delete("/comments/{comment_id}")
def api_delete_comment(comment_id: int, user: CurrentUser = Depends(get_current_user)):
    conn = get_db()
    try:
        row = conn.execute("SELECT user_id FROM pr_comments WHERE id=?", (comment_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Comment not found")
        if row["user_id"] != user.id and user.role != "admin":
            raise HTTPException(403, "Can only delete your own comments")
        conn.execute("UPDATE pr_comments SET deleted=1, updated_at=datetime('now') WHERE id=?",
                     (comment_id,))
        conn.commit()
        return {"deleted": comment_id}
    finally:
        conn.close()


# ── Import / sync ─────────────────────────────────────────────────────────────
@api.post("/import")
def api_run_import(workbook_path: str = Query(..., description="Absolute path to the .xlsx file"),
                   user: CurrentUser = Depends(get_current_user)):
    if user.role != "admin":
        raise HTTPException(403, "Only admins can run import")
    from .loader import run_import
    conn = get_db()
    try:
        result = run_import(workbook_path, conn, user_id=user.id)
        return result
    except FileNotFoundError as e:
        raise HTTPException(400, f"File not found: {e}")
    finally:
        conn.close()


@api.post("/import-upload")
async def api_run_import_upload(file: UploadFile = File(...),
                                as_of: Optional[str] = Query(None),
                                user: CurrentUser = Depends(get_current_user)):
    """Import a workbook uploaded directly from the operator's machine.

    Mirrors `/import` (which takes a server-side path) and `/drive/sync`
    (which pulls from Drive) — same loader, same audit trail in pr_imports.
    The file is streamed to a tempfile, imported, then unlinked.
    """
    if user.role != "admin":
        raise HTTPException(403, "Only admins can run import")
    name = (file.filename or "").lower()
    if not name.endswith((".xlsx", ".xlsm")):
        raise HTTPException(400, "Only .xlsx / .xlsm workbooks are supported")
    import tempfile
    from .loader import run_import
    suffix = ".xlsm" if name.endswith(".xlsm") else ".xlsx"
    tmp = tempfile.NamedTemporaryFile(prefix="pr_upload_", suffix=suffix, delete=False)
    try:
        # Stream upload to tempfile so we don't hold the whole workbook in memory.
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            tmp.write(chunk)
        tmp.flush()
        tmp.close()
        conn = get_db()
        try:
            return run_import(tmp.name, conn, user_id=user.id, as_of_date=as_of)
        finally:
            conn.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Upload import failed")
        raise HTTPException(500, f"Upload import failed: {e}")
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


@api.get("/imports")
def api_list_imports(user: CurrentUser = Depends(get_current_user)):
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM pr_imports ORDER BY id DESC LIMIT 20"
        ).fetchall()
        return {"imports": [_row_to_dict(r) for r in rows]}
    finally:
        conn.close()


# ── Google Drive sync ────────────────────────────────────────────────────────
# Reuses the per-user OAuth flow already implemented in app/routes/drive.py.
# Each user signs in with their own Google account; we read whatever
# spreadsheets they personally have access to (My Drive + Shared Drives).
#
# Setup endpoints (delegate to the existing /api/drive routes):
#   GET  /api/drive/oauth/authorize       → redirect to Google consent
#   GET  /api/drive/connection-status     → check whether this user is connected
#   POST /api/drive/disconnect            → revoke + clear stored creds
@api.get("/drive/files")
def api_drive_files(q: Optional[str] = None,
                    user: CurrentUser = Depends(get_current_user)):
    """List the user's spreadsheets (xlsx/xls/Google Sheets) ordered by recency.
    Optional `q` filters by partial name match."""
    from ..routes.drive import _get_drive_service
    from .drive_sync import list_spreadsheets
    try:
        service = _get_drive_service(user.id)
        files = list_spreadsheets(service, name_contains=q)
        return {"files": files, "count": len(files)}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Drive list failed")
        raise HTTPException(500, f"Drive list failed: {e}")


@api.post("/drive/sync")
def api_drive_sync(file_id: str = Query(..., description="Drive file ID"),
                   as_of: Optional[str] = Query(None),
                   user: CurrentUser = Depends(get_current_user)):
    """Download a workbook from the user's Drive and import it into the
    portfolio_review tables. Reuses run_import for the actual loading."""
    from .drive_sync import sync_from_drive
    conn = get_db()
    try:
        result = sync_from_drive(file_id, conn, user_id=user.id, as_of_date=as_of)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Drive sync failed")
        raise HTTPException(500, f"Drive sync failed: {e}")
    finally:
        conn.close()


# Default parent folder for portfolio company materials. The env var lets ops
# override per-environment without code changes; the literal default points
# at the team's "Portfolio Company Information" Shared Drive so admins can
# trigger discovery without having to dig up the folder ID.
_DEFAULT_PORTFOLIO_PARENT_FOLDER_ID = os.environ.get(
    "PORTFOLIO_DRIVE_PARENT_FOLDER_ID",
    "0ABh0_KkvJonSUk9PVA",
)


# ── Traction & Status: Drive folder discovery + AI scans ──────────────────────
@api.post("/traction/discover-folders")
def api_traction_discover(parent_folder_id: Optional[str] = Query(None),
                          folder_type: str = Query("current", regex="^(current|diligence|board_pack|other)$"),
                          user: CurrentUser = Depends(get_current_user)):
    """Given a parent Drive folder, list all subfolders and link each to a
    portfolio company by name match. Run once per folder type — first for
    current materials, then again for the diligence parent folder.

    If `parent_folder_id` is omitted, falls back to
    PORTFOLIO_DRIVE_PARENT_FOLDER_ID (defaults to the team Portfolio Company
    Information Shared Drive). Reads use the caller's existing per-user
    Drive OAuth — admin must already have access to the Shared Drive."""
    from ..routes.drive import _get_drive_service
    from .drive_scan import list_subfolders, match_folders_to_companies
    folder_id = (parent_folder_id or _DEFAULT_PORTFOLIO_PARENT_FOLDER_ID).strip()
    if not folder_id:
        raise HTTPException(400, "No parent_folder_id provided and PORTFOLIO_DRIVE_PARENT_FOLDER_ID is not set.")
    conn = get_db()
    try:
        service = _get_drive_service(user.id)
        subfolders = list_subfolders(service, folder_id)
        result = match_folders_to_companies(conn, subfolders, folder_id, folder_type)
        result["total_subfolders"] = len(subfolders)
        result["folder_type"] = folder_type
        result["parent_folder_id"] = folder_id
        result["used_default"] = (parent_folder_id is None)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Folder discovery failed")
        raise HTTPException(500, f"Folder discovery failed: {e}")
    finally:
        conn.close()


@api.get("/traction/folders")
def api_traction_folders(user: CurrentUser = Depends(get_current_user)):
    """List every linked Drive folder per company (for the manage UI)."""
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT cf.*, c.name AS company_name, c.fund
               FROM pr_company_folders cf
               JOIN pr_companies c ON c.id = cf.company_id
               ORDER BY c.name, cf.folder_type"""
        ).fetchall()
        # Companies WITHOUT any folder linked
        unlinked = conn.execute(
            """SELECT id, name, fund FROM pr_companies
               WHERE id NOT IN (SELECT DISTINCT company_id FROM pr_company_folders)
               ORDER BY name"""
        ).fetchall()
        return {
            "folders": [_row_to_dict(r) for r in rows],
            "unlinked_companies": [_row_to_dict(r) for r in unlinked],
        }
    finally:
        conn.close()


@api.post("/traction/scan/{company_id}")
def api_traction_scan(company_id: int, user: CurrentUser = Depends(get_current_user)):
    """Run a fresh AI extraction for one company. Synchronous — takes ~10-30s."""
    from .drive_scan import scan_company
    conn = get_db()
    try:
        return scan_company(conn, company_id, user_id=user.id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Traction scan failed for company {company_id}")
        raise HTTPException(500, f"Scan failed: {e}")
    finally:
        conn.close()


@api.get("/traction")
def api_traction_list(user: CurrentUser = Depends(get_current_user)):
    """Latest snapshot per company. Groups output by fund and exposes the
    fundraising callout list separately for the deck-style UI."""
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT t.*, c.name AS company_name, c.fund, c.sector
               FROM pr_traction_snapshots t
               JOIN pr_companies c ON c.id = t.company_id
               WHERE t.id = (SELECT MAX(id) FROM pr_traction_snapshots t2 WHERE t2.company_id = t.company_id)
               ORDER BY c.fund, COALESCE(t.deck_row_index, 999), c.name"""
        ).fetchall()
        snapshots = [_row_to_dict(r) for r in rows]

        # Companies with no snapshot yet — show them as "Not yet scanned"
        unseen = conn.execute(
            """SELECT id, name, fund, sector FROM pr_companies
               WHERE id NOT IN (SELECT DISTINCT company_id FROM pr_traction_snapshots)
               ORDER BY fund, name"""
        ).fetchall()

        # Active fundraising list — scrubbed from snapshots that have it set
        fundraising = [
            {
                "company_id": s["company_id"],
                "company_name": s["company_name"],
                "fund": s["fund"],
                "fundraising_status": s["fundraising_status"],
            }
            for s in snapshots
            if s.get("fundraising_status")
        ]

        return {
            "snapshots": snapshots,
            "unscanned": [_row_to_dict(r) for r in unseen],
            "fundraising": fundraising,
        }
    finally:
        conn.close()


@api.get("/derisking")
def api_derisking_list(period: Optional[str] = None,
                       user: CurrentUser = Depends(get_current_user)):
    """Latest derisking score per company (or for a specific period if given).
    Joined with company name + fund. Sorted by quartile desc, then total desc."""
    conn = get_db()
    try:
        if period:
            rows = conn.execute(
                """SELECT d.*, c.name AS company_name, c.fund AS company_fund, c.sector
                   FROM pr_derisking_scores d JOIN pr_companies c ON c.id = d.company_id
                   WHERE d.period = ?
                   ORDER BY d.quartile DESC, d.total_score DESC, c.name""",
                (period,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT d.*, c.name AS company_name, c.fund AS company_fund, c.sector
                   FROM pr_derisking_scores d JOIN pr_companies c ON c.id = d.company_id
                   WHERE d.id = (SELECT MAX(id) FROM pr_derisking_scores d2 WHERE d2.company_id = d.company_id)
                   ORDER BY d.quartile DESC, d.total_score DESC, c.name""",
            ).fetchall()
        # Distinct periods for the period selector
        periods = [r[0] for r in conn.execute(
            "SELECT DISTINCT period FROM pr_derisking_scores ORDER BY period DESC"
        ).fetchall()]
        return {
            "scores": [_row_to_dict(r) for r in rows],
            "periods": periods,
            "selected_period": period,
        }
    finally:
        conn.close()


@api.post("/derisking/import")
async def api_derisking_import(file: UploadFile = File(...),
                                user: CurrentUser = Depends(get_current_user)):
    """Upload a Derisking Quadrants .xlsx and import all 'Fund X YYYY' tabs."""
    from .derisking import import_full_workbook
    if not file.filename.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(400, f"Expected .xlsx, got: {file.filename}")
    import tempfile
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx")
    try:
        tmp.write(await file.read())
        tmp.close()
        conn = get_db()
        try:
            return import_full_workbook(Path(tmp.name), conn)
        finally:
            conn.close()
    finally:
        Path(tmp.name).unlink(missing_ok=True)


@api.post("/derisking/llm-score/{company_id}")
def api_derisking_llm_score(company_id: int,
                            period: str = Query("2025 LLM",
                                description="Stored as the period string. Use a suffix like ' LLM' to keep these distinct from operator-imported scores."),
                            provider: Optional[str] = Query("anthropic",
                                regex="^(anthropic|refiant)$",
                                description="Which LLM backend to use. 'anthropic' = Claude (Sonnet 4.6 default), 'refiant' = QWEN via Refiant."),
                            model: Optional[str] = Query(None,
                                description="Optional explicit model id. Overrides `provider`; the prefix decides backend ('qwen...' → Refiant, else Anthropic)."),
                            user: CurrentUser = Depends(get_current_user)):
    """Run an LLM-driven derisking review for one company.

    The chosen LLM reads the IC memo / diligence materials, the most recent
    board decks + investor updates, and the most recent Granola meeting
    notes, then scores each of the 7 derisking dimensions with reasoning
    and evidence. Result is persisted to pr_derisking_scores with
    evaluator='llm'.

    Provider toggle: pass `provider=anthropic` (default) for Claude or
    `provider=refiant` for QWEN. `model` overrides both if set explicitly.

    Sync call — typical run is 30-60s for one company. Use a long client
    timeout (180s+).
    """
    if user.role != "admin":
        raise HTTPException(403, "Only admins can run LLM scoring")
    from .derisking_scoring import score_company_with_llm
    conn = get_db()
    try:
        return score_company_with_llm(
            conn,
            company_id=company_id,
            user_id=user.id,
            period=period,
            provider=provider,
            model=model,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("LLM derisking score failed")
        raise HTTPException(500, f"LLM scoring failed: {e}")
    finally:
        conn.close()


@api.get("/derisking/by-stage")
def api_derisking_by_stage(period: Optional[str] = Query(None,
                              description="Primary period (latest if omitted)."),
                           compare_to: Optional[str] = Query(None,
                              description="Earlier period to show side-by-side (for the YoY delta column)."),
                           user: CurrentUser = Depends(get_current_user)):
    """Return derisking scores grouped by current investment stage with
    optional period-over-period comparison.

    'Current stage' is derived from the latest pr_investments.round_label
    per company; companies with no investments are bucketed as 'Unstaged'.

    Response shape (frontend renders chips per row):
      {
        "stages": [
          {"stage": "Seed", "rows": [
              {"company_id": 1, "company_name": "...", "fund": "Fund I",
               "sector": "...", "current_stage": "Seed",
               "primary": {"period": "2025 LLM", "quartile": 4, "total": 5,
                           "evaluator": "llm", "model_used": "claude-sonnet-4-6",
                           "scored_at": "...", "evidence_summary": "..."},
               "compare": {"period": "FY2024", "quartile": 3, "total": 3,
                           "evaluator": "human", ...} | null,
               "delta_quartile": 1   # primary minus compare; null if either missing
              }, ...
          ]}, ...
        ],
        "primary_period": "2025 LLM",
        "compare_period": "FY2024",
        "available_periods": ["2025 LLM", "FY2025", "FY2024"]
      }
    """
    conn = get_db()
    try:
        # Available periods, newest first
        available = [r[0] for r in conn.execute(
            "SELECT DISTINCT period FROM pr_derisking_scores ORDER BY period DESC"
        ).fetchall()]
        if not available:
            return {"stages": [], "primary_period": None, "compare_period": None,
                    "available_periods": []}

        primary = period or available[0]
        compare = compare_to
        if compare is None and len(available) > 1:
            # Pick the next-most-recent period that's different from primary
            for p in available:
                if p != primary:
                    compare = p
                    break

        # One row per company with its primary score (or NULL) and current stage
        # derived from latest pr_investments.round_label.
        rows = conn.execute(
            """
            SELECT c.id          AS company_id,
                   c.name        AS company_name,
                   c.fund        AS company_fund,
                   c.sector      AS sector,
                   c.commercial_status AS commercial_status,
                   (SELECT i.round_label
                      FROM pr_investments i
                     WHERE i.company_id = c.id AND i.round_label != ''
                  ORDER BY COALESCE(i.investment_date, '') DESC, i.id DESC
                     LIMIT 1)    AS current_stage,
                   p.period      AS p_period,
                   p.quartile    AS p_quartile,
                   p.total_score AS p_total,
                   p.is_exited   AS p_is_exited,
                   COALESCE(p.evaluator, 'human') AS p_evaluator,
                   p.model_used  AS p_model_used,
                   p.scored_at   AS p_scored_at,
                   p.evidence_summary AS p_evidence_summary,
                   p.confidence  AS p_confidence,
                   cmp.period    AS c_period,
                   cmp.quartile  AS c_quartile,
                   cmp.total_score AS c_total,
                   COALESCE(cmp.evaluator, 'human') AS c_evaluator
              FROM pr_companies c
              LEFT JOIN pr_derisking_scores p
                ON p.company_id = c.id AND p.period = ?
              LEFT JOIN pr_derisking_scores cmp
                ON cmp.company_id = c.id AND cmp.period = ?
             ORDER BY c.fund, c.name
            """,
            (primary, compare or "__none__"),
        ).fetchall()

        # Bucket rows by stage. Order stages naturally from earliest to latest.
        STAGE_ORDER = [
            "Pre-Seed", "Seed", "Seed+", "Seed Extension",
            "A", "Series A", "A+",
            "B", "Series B", "B+",
            "C", "Series C", "D", "Growth",
        ]
        def stage_rank(s: str) -> tuple:
            if not s:
                return (99, "")
            for i, label in enumerate(STAGE_ORDER):
                if s.strip().lower() == label.lower():
                    return (i, s)
            return (50, s)  # unknown stages sort after known ones, alphabetically

        bucketed: dict[str, list[dict]] = {}
        for r in rows:
            d = _row_to_dict(r)
            stage = d.get("current_stage") or "Unstaged"
            primary_block = None
            if d.get("p_period"):
                primary_block = {
                    "period": d["p_period"],
                    "quartile": d["p_quartile"],
                    "total": d["p_total"],
                    "is_exited": bool(d.get("p_is_exited")),
                    "evaluator": d["p_evaluator"],
                    "model_used": d.get("p_model_used") or "",
                    "scored_at": d.get("p_scored_at"),
                    "evidence_summary": d.get("p_evidence_summary") or "",
                    "confidence": d.get("p_confidence") or "",
                }
            compare_block = None
            if d.get("c_period"):
                compare_block = {
                    "period": d["c_period"],
                    "quartile": d["c_quartile"],
                    "total": d["c_total"],
                    "evaluator": d["c_evaluator"],
                }
            delta_q = None
            if primary_block and compare_block \
               and primary_block["quartile"] is not None \
               and compare_block["quartile"] is not None:
                delta_q = primary_block["quartile"] - compare_block["quartile"]

            bucketed.setdefault(stage, []).append({
                "company_id": d["company_id"],
                "company_name": d["company_name"],
                "fund": d.get("company_fund") or "",
                "sector": d.get("sector") or "",
                "commercial_status": d.get("commercial_status") or "",
                "current_stage": stage,
                "primary": primary_block,
                "compare": compare_block,
                "delta_quartile": delta_q,
            })

        # Within each stage, rank by primary quartile desc → primary total desc
        # → company name. Companies with no primary score sink to the bottom.
        def row_sort_key(row: dict) -> tuple:
            p = row.get("primary") or {}
            q = p.get("quartile")
            t = p.get("total")
            return (
                0 if q is not None else 1,
                -(q or 0),
                -(t if isinstance(t, (int, float)) else 0),
                row["company_name"].lower(),
            )
        for stage_rows in bucketed.values():
            stage_rows.sort(key=row_sort_key)

        ordered_stages = sorted(bucketed.keys(), key=stage_rank)
        return {
            "stages": [
                {"stage": s, "rows": bucketed[s], "n": len(bucketed[s])}
                for s in ordered_stages
            ],
            "primary_period": primary,
            "compare_period": compare,
            "available_periods": available,
        }
    finally:
        conn.close()


@api.get("/derisking/{company_id}")
def api_derisking_company_detail(company_id: int,
                                 period: Optional[str] = Query(None),
                                 user: CurrentUser = Depends(get_current_user)):
    """Full derisking record for one company at a specific period — includes
    the LLM reasoning JSON for the per-dimension expand-row UI."""
    conn = get_db()
    try:
        if period:
            row = conn.execute(
                """SELECT d.*, c.name AS company_name, c.fund AS company_fund
                     FROM pr_derisking_scores d
                     JOIN pr_companies c ON c.id = d.company_id
                    WHERE d.company_id=? AND d.period=?""",
                (company_id, period),
            ).fetchone()
        else:
            row = conn.execute(
                """SELECT d.*, c.name AS company_name, c.fund AS company_fund
                     FROM pr_derisking_scores d
                     JOIN pr_companies c ON c.id = d.company_id
                    WHERE d.company_id=?
                 ORDER BY d.scored_at DESC LIMIT 1""",
                (company_id,),
            ).fetchone()
        if row is None:
            raise HTTPException(404, "No score found")
        d = _row_to_dict(row)
        try:
            d["reasoning"] = json.loads(d.get("reasoning_json") or "{}")
        except Exception:
            d["reasoning"] = {}
        try:
            d["source_files"] = json.loads(d.get("source_files") or "[]")
        except Exception:
            d["source_files"] = []
        try:
            d["manifest"] = json.loads(d.get("manifest_json") or "{}")
        except Exception:
            d["manifest"] = {}
        return d
    finally:
        conn.close()


@api.post("/traction/import-deck")
async def api_import_deck(file: UploadFile = File(...),
                          user: CurrentUser = Depends(get_current_user)):
    """Upload a Monthly All-Team PortCo Updates .pptx and import all rows."""
    from .deck_import import import_deck
    if not file.filename.lower().endswith((".pptx", ".ppt")):
        raise HTTPException(400, f"Expected .pptx, got: {file.filename}")
    import tempfile
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pptx")
    try:
        tmp.write(await file.read())
        tmp.close()
        conn = get_db()
        try:
            result = import_deck(Path(tmp.name), conn, source_name=file.filename)
            return result
        finally:
            conn.close()
    finally:
        Path(tmp.name).unlink(missing_ok=True)


# ── Granola → Portfolio bridge ────────────────────────────────────────────────
# Pulls team meeting notes from the configured folders (Investment Committee,
# Portco Updates, Screening + Rapid Fire Meeting by default; override via
# PORTFOLIO_GRANOLA_FOLDERS env var) and links each note to portfolio
# companies via attendee email + name-in-title heuristics. See
# `granola_sync.py` for the full design notes.
@api.post("/granola/sync")
def api_granola_sync(
    cursor: Optional[str] = Query(None, description="ISO timestamp override; only pull notes updated after this. Omit to use the per-user stored cursor (incremental). Pass an explicit value to override."),
    include_transcripts: bool = Query(True, description="Include full transcript text in note bodies. Default True matches volomind so notes that only have a transcript (no summary) aren't silently dropped."),
    reset: bool = Query(False, description="Force a full re-sync, ignoring the stored cursor. New cursor is written on success. Use to recover after fixing folder names or after a failed sync."),
    user: CurrentUser = Depends(get_current_user),
):
    """Pull Granola notes from allowed folders and link them to portfolio
    companies. Incremental by default — reads the stored per-user cursor
    so subsequent clicks only fetch notes updated since the last
    successful sync. Pass `?reset=true` to force a full re-sync."""
    from .granola_sync import run_granola_sync
    conn = get_db()
    try:
        return run_granola_sync(
            conn,
            user_id=user.id,
            cursor=cursor,
            include_transcripts=include_transcripts,
            reset=reset,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Granola sync failed")
        raise HTTPException(500, f"Granola sync failed: {e}")
    finally:
        conn.close()


@api.get("/granola/sync-state")
def api_granola_sync_state(user: CurrentUser = Depends(get_current_user)):
    """Return the current per-user cursor + last run metadata so the UI
    can show 'Last incremental sync at <time>; will pull notes updated
    after <cursor>'."""
    conn = get_db()
    try:
        row = conn.execute(
            """SELECT cursor_value, last_run_at, last_status
                 FROM pr_sync_state
                WHERE owner_id=? AND source='granola'""",
            (user.id,),
        ).fetchone()
        if row is None:
            return {
                "has_cursor": False,
                "cursor": None,
                "last_run_at": None,
                "last_status": None,
                "next_pull": "Will fetch ALL notes (no incremental cursor saved yet — first sync).",
            }
        d = _row_to_dict(row)
        cur = d.get("cursor_value") or ""
        return {
            "has_cursor": bool(cur),
            "cursor": cur or None,
            "last_run_at": d.get("last_run_at"),
            "last_status": d.get("last_status"),
            "next_pull": (
                f"Will fetch only notes updated after {cur}." if cur
                else "Will fetch ALL notes (cursor cleared)."
            ),
        }
    finally:
        conn.close()


@api.post("/granola/reset-cursor")
def api_granola_reset_cursor(user: CurrentUser = Depends(get_current_user)):
    """Clear the per-user stored cursor so the next sync runs in full.
    Equivalent to passing `?reset=true` on the next /sync call but
    persistent (subsequent syncs will start from scratch until one
    succeeds)."""
    from .granola_sync import reset_stored_cursor
    conn = get_db()
    try:
        reset_stored_cursor(conn, user_id=user.id)
        return {"ok": True, "message": "Granola cursor cleared. Next sync will fetch everything."}
    finally:
        conn.close()


@api.get("/granola/notes")
def api_granola_notes_for_company(
    company_id: int = Query(..., description="pr_companies.id"),
    limit: int = Query(50, ge=1, le=500),
    user: CurrentUser = Depends(get_current_user),
):
    """List Granola notes linked to one portfolio company, newest first."""
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT id, granola_note_id, note_title, note_url,
                      note_created_at, note_updated_at, match_method,
                      match_confidence, fetched_at
                 FROM pr_granola_notes
                WHERE company_id = ?
                ORDER BY coalesce(note_updated_at, fetched_at) DESC
                LIMIT ?""",
            (company_id, limit),
        ).fetchall()
        return {"company_id": company_id, "notes": [_row_to_dict(r) for r in rows]}
    finally:
        conn.close()


@api.get("/granola/probe")
def api_granola_probe(
    limit: int = Query(5, ge=1, le=20),
    user: CurrentUser = Depends(get_current_user),
):
    """Diagnostic probe — bypasses the connector and hits Granola's
    /v1/notes endpoint directly with the configured GRANOLA_API_KEY.
    Returns the raw response shape so we can see whether the API itself
    is returning notes (vs. the connector dropping them silently).

    Use when granola/sync reports notes_fetched=0 to figure out where
    the bottleneck is:
      • probe.raw_count == 0  →  Granola itself returned 0 notes (key
        scope or workspace is empty)
      • probe.raw_count > 0 but sync notes_fetched == 0  →  the
        connector is dropping every note (likely empty bodies / detail
        fetch failures)
      • probe.raw_count > 0 but notes_in_scope == 0  →  the folder
        filter is rejecting every note. Compare diagnostics.seen_folders
        on the sync response against your allowed_folders list.
    """
    from .granola_sync import probe_granola_api
    return probe_granola_api(limit=limit)


@api.get("/granola/syncs")
def api_granola_recent_syncs(
    limit: int = Query(20, ge=1, le=100),
    user: CurrentUser = Depends(get_current_user),
):
    """Return the most recent Granola sync runs for the diagnostics UI."""
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT id, started_at, finished_at, status, notes_fetched,
                      associations_new, associations_updated, associations_skip,
                      error_summary
                 FROM pr_granola_syncs
                ORDER BY started_at DESC
                LIMIT ?""",
            (limit,),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


# ── Combined router for main.py to mount ──────────────────────────────────────
def get_routers():
    """Return both the page router and the API router."""
    return [router, api]
