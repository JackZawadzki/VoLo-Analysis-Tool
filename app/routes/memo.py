"""
Investment Memo routes — generate comprehensive investment memos from report data + data room documents.

POST   /api/memo/templates              — create a memo template
GET    /api/memo/templates              — list templates
GET    /api/memo/templates/{id}         — get template
PUT    /api/memo/templates/{id}         — update template
DELETE /api/memo/templates/{id}         — delete template

POST   /api/memo/documents/upload       — upload data room document(s)
GET    /api/memo/documents/{session_id} — list documents for a session
DELETE /api/memo/documents/{doc_id}     — remove a document

POST   /api/memo/generate               — generate memo (LLM call)
GET    /api/memo/history                — list generated memos
GET    /api/memo/history/{id}           — get a generated memo
DELETE /api/memo/history/{id}           — delete a generated memo
GET    /api/memo/history/{id}/docx      — export as Word document
GET    /api/memo/reports                — list available deal reports for selection
"""

import io
import json
import logging
import os
import re
import tempfile
import time
import uuid
from pathlib import Path
from typing import Optional, List

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from ..auth import CurrentUser, get_current_user, decode_token
from ..database import get_db, get_model_preferences, MODEL_DEFAULTS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/memo", tags=["memo"])

# ── Upload directory ─────────────────────────────────────────────────────────
UPLOAD_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "memo_uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Maximum file size: 50 MB
MAX_FILE_SIZE = 50 * 1024 * 1024

# Allowed file extensions for data room documents
ALLOWED_EXTENSIONS = {
    '.pdf', '.docx', '.doc', '.xlsx', '.xls', '.csv', '.txt',
    '.pptx', '.ppt', '.md', '.html', '.json', '.png', '.jpg', '.jpeg'
}

DOC_CATEGORIES = [
    'financial_model', 'pitch_deck', 'term_sheet', 'cap_table',
    'legal', 'ip_patent', 'customer_reference', 'market_research',
    'technical_diligence', 'team_bios', 'board_materials', 'other'
]


# ═══════════════════════════════════════════════════════════════════════════════
#  DEFAULT VoLo TEMPLATE — auto-seeded for new users
# ═══════════════════════════════════════════════════════════════════════════════

VOLO_DEFAULT_TEMPLATE_NAME = "VoLo Earth Ventures — Standard IC Memo"

VOLO_DEFAULT_TEMPLATE = """# Investment Overview

(LOGO) One-Liner

| Field | Value |
|-------|-------|
| VEV Focus Area | |
| Investment Stage & Vehicle | |
| Key Terms | |
| Capital Amount | |
| Expected VEV Equity | |
| Board Participation | |

## Portfolio Themes
- Low cost, next gen energy storage
- Uses abundant materials
- Digitization of high skill activities
- System-level enabling technology
- Can be sold with zero-dollar year 1 premium
- Increase productivity and profitability of at-scale manufacturing base
- Rapidly scalable after achieving derisking milestone(s)

# High Level Opportunities
- [Key investment thesis points]

# High Level Risks
- [Key risk factors]

# Company Overview
- Set a clear tone: What is the company's 'Aha' or 'breakthrough'?
- Contextualize Opportunity: Why does the market need this? How big is the market, what is the gap, and why now?
- Why this categorical approach to the problem? What bottlenecks do they solve?
- Differentiation
- Validate: Traction + Defensibility (high level; tech details go in Traction)
- Entice: Preview 'how big this can get'? Why can they uniquely succeed?

# Market
- Start broad, with total demand and market size
- Describe market dynamics of current market composition by major segments
- Serviceable market — where does this company have a right to win?

# Business Model
- How does the company make money? Unit Economics
- Scalability — map of capex needs and financing plans
- Broad GTM
- Highlight relevant strengths from RVM
- Forward Projections

# Team
- Quick overview of relevant background (operating + founding experience, combined skills)
- Execution to date in relation to VoLo's experience with the team
- Additional signals that they will be able to execute against venture trajectory/return
- Include relevant factors of RVM

# Traction
| Category | Details |
|----------|---------|
| Technical | - |
| Commercial | - |
| Customer Feedback | - |

# Competitive Position
- Broad framework to categorize competition: zoom out → in
- Quick overview on why/where other categories are limited; include incumbents
- Lift & contextualize competitive landscape slide (if provided)

# Carbon Impact
- Theory of Change
- Carbon Economics: Include image from RVM or Carbon calc

# Technology: Overview, IP and Moat
- Technical brief: differentiation, what is derisked to date
- Derisking ahead: remaining categorical step changes, how VoLo got comfortable
- Highlight relevant strengths from RVM

# Financing Overview: Round and Exit Planning

## Deal Structure
Overview & VoLo Role — include total raise
VoLo Earth Value-add and Additionality
Committed Syndicate

## Capital Position and Funding History
| Metric | Value |
|--------|-------|
| Total Raised to Date | |
| Current Cash in Bank | |
| Current Burn Rate | |
| Runway Provided by Financing | |

## Round Pricing
- Price, Implied multiple (TTM x NTM)
- Pricing & Comps: public comps chart, comps discussion (describe categories & rationale)
- Private Comps

## Exit Planning
- Basic MOIC with revenue multiple range x discounted revenue projection (3-5 years)
- Acquisition: likely acquirers, strategic positioning
- IPO potential (if applicable)
- Downside Protection
- RVM Exit screenshot with assumptions

## Key Milestones & Use of Funds
- High level milestones for follow-on round accountability
- Brief discussion of feasibility

## Growth Investor Insights
"""


def seed_default_template(owner_id: int):
    """Ensure the default VoLo template exists for a user. Idempotent."""
    conn = get_db()
    try:
        existing = conn.execute(
            "SELECT id FROM memo_templates WHERE owner_id=? AND name=?",
            (owner_id, VOLO_DEFAULT_TEMPLATE_NAME),
        ).fetchone()
        if existing:
            return existing["id"]
        cur = conn.execute(
            """INSERT INTO memo_templates (owner_id, name, description, content, is_default)
               VALUES (?, ?, ?, ?, 1)""",
            (owner_id, VOLO_DEFAULT_TEMPLATE_NAME,
             "Standard VoLo Earth Ventures Investment Committee memo template",
             VOLO_DEFAULT_TEMPLATE),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
#  TEMPLATE CRUD
# ═══════════════════════════════════════════════════════════════════════════════

class TemplateCreate(BaseModel):
    name: str
    description: str = ""
    content: str = ""


class TemplateUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    content: Optional[str] = None


@router.post("/templates")
async def create_template(req: TemplateCreate, user: CurrentUser = Depends(get_current_user)):
    conn = get_db()
    try:
        cur = conn.execute(
            """INSERT INTO memo_templates (owner_id, name, description, content)
               VALUES (?, ?, ?, ?)""",
            (user.id, req.name, req.description, req.content),
        )
        conn.commit()
        return {"id": cur.lastrowid, "name": req.name}
    finally:
        conn.close()


@router.get("/templates")
async def list_templates(user: CurrentUser = Depends(get_current_user)):
    # Auto-seed default template if user has none
    seed_default_template(user.id)
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, name, description, is_default, created_at, updated_at FROM memo_templates WHERE owner_id=? ORDER BY is_default DESC, updated_at DESC",
            (user.id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.get("/templates/{template_id}")
async def get_template(template_id: int, user: CurrentUser = Depends(get_current_user)):
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM memo_templates WHERE id=? AND owner_id=?",
            (template_id, user.id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Template not found")
        return dict(row)
    finally:
        conn.close()


@router.put("/templates/{template_id}")
async def update_template(template_id: int, req: TemplateUpdate, user: CurrentUser = Depends(get_current_user)):
    conn = get_db()
    try:
        existing = conn.execute(
            "SELECT id FROM memo_templates WHERE id=? AND owner_id=?",
            (template_id, user.id),
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Template not found")

        updates = []
        params = []
        if req.name is not None:
            updates.append("name=?")
            params.append(req.name)
        if req.description is not None:
            updates.append("description=?")
            params.append(req.description)
        if req.content is not None:
            updates.append("content=?")
            params.append(req.content)
        if updates:
            updates.append("updated_at=datetime('now')")
            params.extend([template_id, user.id])
            conn.execute(
                f"UPDATE memo_templates SET {', '.join(updates)} WHERE id=? AND owner_id=?",
                params,
            )
            conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@router.delete("/templates/{template_id}")
async def delete_template(template_id: int, user: CurrentUser = Depends(get_current_user)):
    conn = get_db()
    try:
        conn.execute("DELETE FROM memo_templates WHERE id=? AND owner_id=?", (template_id, user.id))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
#  DOCUMENT UPLOAD & MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════

def _extract_text_from_file(file_path: Path, file_name: str) -> str:
    """Best-effort text extraction from uploaded files."""
    ext = Path(file_name).suffix.lower()
    text = ""

    try:
        if ext in ('.txt', '.md', '.csv', '.json', '.html'):
            text = file_path.read_text(errors='replace')[:100_000]

        elif ext == '.pdf':
            try:
                import fitz  # PyMuPDF
                doc = fitz.open(str(file_path))
                pages = []
                for page in doc:
                    pages.append(page.get_text())
                text = "\n\n".join(pages)[:200_000]
                doc.close()
            except ImportError:
                text = "[PDF text extraction requires PyMuPDF]"

        elif ext == '.docx':
            try:
                from docx import Document
                doc = Document(str(file_path))
                paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
                text = "\n".join(paragraphs)[:200_000]
            except ImportError:
                text = "[DOCX text extraction requires python-docx]"

        elif ext in ('.xlsx', '.xls'):
            try:
                import openpyxl
                wb = openpyxl.load_workbook(str(file_path), data_only=True, read_only=True)
                parts = []
                for ws in wb.worksheets[:5]:  # Limit to first 5 sheets
                    parts.append(f"=== Sheet: {ws.title} ===")
                    row_count = 0
                    for row in ws.iter_rows(values_only=True):
                        vals = [str(c) if c is not None else '' for c in row]
                        if any(v.strip() for v in vals):
                            parts.append('\t'.join(vals))
                            row_count += 1
                            if row_count > 200:
                                parts.append(f"... (truncated, {ws.max_row} total rows)")
                                break
                wb.close()
                text = "\n".join(parts)[:200_000]
            except ImportError:
                text = "[Excel text extraction requires openpyxl]"

        elif ext == '.pptx':
            try:
                from pptx import Presentation
                prs = Presentation(str(file_path))
                parts = []
                for i, slide in enumerate(prs.slides):
                    parts.append(f"=== Slide {i+1} ===")
                    for shape in slide.shapes:
                        if shape.has_text_frame:
                            for para in shape.text_frame.paragraphs:
                                if para.text.strip():
                                    parts.append(para.text.strip())
                text = "\n".join(parts)[:200_000]
            except ImportError:
                text = "[PPTX text extraction requires python-pptx]"

    except Exception as e:
        text = f"[Extraction error: {str(e)[:200]}]"

    return text


@router.post("/documents/upload")
async def upload_documents(
    files: List[UploadFile] = File(...),
    session_id: str = Form(""),
    category: str = Form("other"),
    user: CurrentUser = Depends(get_current_user),
):
    if not session_id:
        session_id = str(uuid.uuid4())[:12]

    results = []
    conn = get_db()
    try:
        for file in files:
            ext = Path(file.filename or "unknown").suffix.lower()
            if ext not in ALLOWED_EXTENSIONS:
                results.append({"file": file.filename, "error": f"File type {ext} not allowed"})
                continue

            # Read file content
            content = await file.read()
            if len(content) > MAX_FILE_SIZE:
                results.append({"file": file.filename, "error": "File exceeds 50MB limit"})
                continue

            # Save to disk
            safe_name = re.sub(r'[^\w\-.]', '_', file.filename or 'unnamed')
            dest_dir = UPLOAD_DIR / session_id
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest_path = dest_dir / safe_name

            # Handle name collision
            counter = 1
            while dest_path.exists():
                stem = Path(safe_name).stem
                dest_path = dest_dir / f"{stem}_{counter}{ext}"
                counter += 1

            dest_path.write_bytes(content)

            # Extract text
            extracted = _extract_text_from_file(dest_path, safe_name)

            # Insert DB record
            cur = conn.execute(
                """INSERT INTO memo_documents
                   (owner_id, memo_session_id, file_name, file_type, file_size, extracted_text, doc_category, file_path)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (user.id, session_id, file.filename, ext, len(content), extracted, category, str(dest_path)),
            )
            conn.commit()

            results.append({
                "id": cur.lastrowid,
                "file": file.filename,
                "size": len(content),
                "extracted_chars": len(extracted),
                "category": category,
            })

        return {"session_id": session_id, "documents": results}
    finally:
        conn.close()


@router.get("/documents/{session_id}")
async def list_documents(session_id: str, user: CurrentUser = Depends(get_current_user)):
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, file_name, file_type, file_size, doc_category, uploaded_at FROM memo_documents WHERE owner_id=? AND memo_session_id=? ORDER BY uploaded_at",
            (user.id, session_id),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: int, user: CurrentUser = Depends(get_current_user)):
    conn = get_db()
    try:
        row = conn.execute("SELECT file_path FROM memo_documents WHERE id=? AND owner_id=?", (doc_id, user.id)).fetchone()
        if row and row["file_path"]:
            try:
                Path(row["file_path"]).unlink(missing_ok=True)
            except Exception:
                pass
        conn.execute("DELETE FROM memo_documents WHERE id=? AND owner_id=?", (doc_id, user.id))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
#  REPORT DATA — List available reports for attaching to memos
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/reports")
async def list_reports_for_memo(user: CurrentUser = Depends(get_current_user)):
    """Return a lightweight list of deal reports the user can pull into memo generation."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, company_name, archetype, entry_stage, status, created_at FROM deal_reports WHERE owner_id=? ORDER BY created_at DESC LIMIT 50",
            (user.id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
#  MEMO GENERATION — LLM call
# ═══════════════════════════════════════════════════════════════════════════════

class MemoGenerateRequest(BaseModel):
    report_id: Optional[int] = None
    template_id: Optional[int] = None
    template_text: Optional[str] = None          # Inline template override
    session_id: Optional[str] = None             # data room document session (manual uploads)
    library_id: Optional[int] = None             # deal document library (Google Drive sync)
    additional_instructions: str = ""
    company_name: str = ""
    links: List[str] = []                        # URLs / reference links
    model_override: Optional[str] = None         # Force a specific model


def _build_report_context(report_row) -> str:
    """Build a structured context string from a deal report."""
    if not report_row:
        return ""

    report = json.loads(report_row["report_json"] or "{}")
    inputs = json.loads(report_row["inputs_json"] or "{}")
    extraction = json.loads(report_row["extraction_json"] or "{}")

    parts = [f"# DEAL REPORT: {report_row['company_name']}"]
    parts.append(f"Archetype: {report_row['archetype']}")
    parts.append(f"Entry Stage: {report_row['entry_stage']}")

    # Input parameters
    if inputs:
        parts.append("\n## INPUT PARAMETERS")
        for k, v in inputs.items():
            if v and k not in ('volume', 'op_carbon', 'emb_carbon', 'portfolio'):
                parts.append(f"- {k}: {v}")

    # Simulation results
    sim = report.get("simulation", {})
    if sim:
        parts.append("\n## SIMULATION RESULTS")
        hero = sim.get("hero_metrics", {})
        if hero:
            parts.append(f"- Expected MOIC: {hero.get('expected_moic', 'N/A')}x")
            parts.append(f"- P(>3x): {hero.get('p_gt_3x', 'N/A')}")
            parts.append(f"- Expected IRR: {hero.get('expected_irr', 'N/A')}")
            parts.append(f"- Survival Rate: {hero.get('survival_rate', 'N/A')}")

        prob = sim.get("probability_buckets", {})
        if prob:
            parts.append(f"- P(Total Loss): {prob.get('total_loss', 'N/A')}")
            parts.append(f"- P(>1x): {prob.get('gt_1x', 'N/A')}")
            parts.append(f"- P(>5x): {prob.get('gt_5x', 'N/A')}")
            parts.append(f"- P(>10x): {prob.get('gt_10x', 'N/A')}")

        moic_cond = sim.get("moic_conditional", {})
        if moic_cond:
            parts.append(f"- MOIC Conditional Mean: {moic_cond.get('mean', 'N/A')}x")
            parts.append(f"- MOIC Conditional P50: {moic_cond.get('p50', 'N/A')}x")

    # Adoption
    adoption = report.get("adoption", {})
    if adoption:
        parts.append("\n## MARKET ADOPTION")
        scurve = adoption.get("scurve", {})
        if scurve:
            parts.append(f"- Bass p (innovation): {scurve.get('bass_p_mean', 'N/A')}")
            parts.append(f"- Bass q (imitation): {scurve.get('bass_q_mean', 'N/A')}")
        div = adoption.get("divergence_table", [])
        if div:
            parts.append("- Divergence Table (Founder vs Sim):")
            for row in div[:8]:
                parts.append(f"  Year {row.get('year','?')}: Founder ${row.get('founder_rev_m','?')}M, Sim Med ${row.get('sim_median_m','?')}M, Divergence {row.get('divergence_pct','?')}%, In Band: {row.get('in_band','?')}")

    # Carbon impact
    carbon = report.get("carbon", {})
    co = carbon.get("outputs", {})
    if co:
        parts.append("\n## CARBON IMPACT")
        parts.append(f"- Total Lifecycle tCO2: {co.get('company_tonnes', 'N/A')}")
        parts.append(f"- VoLo Pro-Rata: {co.get('volo_prorata', 'N/A')}")
        parts.append(f"- Risk-Adjusted: {co.get('volo_risk_adj', 'N/A')}")
        parts.append(f"- t/$ (Risk-Adj): {co.get('risk_adj_tpd', 'N/A')}")

    # Portfolio impact
    pi = report.get("portfolio_impact", {})
    if pi:
        parts.append("\n## PORTFOLIO IMPACT")
        parts.append(f"- TVPI Base Mean: {pi.get('tvpi_base_mean', 'N/A')}x")
        parts.append(f"- TVPI New Mean: {pi.get('tvpi_new_mean', 'N/A')}x")
        parts.append(f"- TVPI Lift: {pi.get('tvpi_mean_lift', 'N/A')}x")
        parts.append(f"- IRR Base Mean: {pi.get('irr_base_mean', 'N/A')}")
        parts.append(f"- IRR New Mean: {pi.get('irr_new_mean', 'N/A')}")

    # Sensitivity
    sens = report.get("sensitivity", {})
    tornado = sens.get("tornado", [])
    if tornado:
        parts.append("\n## SENSITIVITY ANALYSIS (Top Drivers)")
        for t in tornado[:6]:
            parts.append(f"- {t.get('param', '?')}: Low {t.get('moic_low', '?')}x / High {t.get('moic_high', '?')}x")

    # Check size optimization
    opt = report.get("check_optimization", {})
    best = opt.get("best_check", {})
    if best:
        parts.append("\n## CHECK SIZE OPTIMIZATION")
        parts.append(f"- Optimal Check: ${best.get('check_m', 'N/A')}M")
        parts.append(f"- Implied Ownership: {best.get('ownership_pct', 'N/A')}%")
        parts.append(f"- Fund P50 Impact: {best.get('fund_p50_pct_chg', 'N/A')}")

    # Extraction data
    if extraction:
        records = extraction.get("records", [])
        if records:
            parts.append("\n## FINANCIAL MODEL EXTRACTION")
            for rec in records[:20]:
                parts.append(f"- {rec.get('metric','')} ({rec.get('period','')}): {rec.get('value','')} [source: {rec.get('sheet','')}, row {rec.get('row','')}]")

    # Valuation comps
    comps = report.get("valuation_comps", {})
    if comps:
        parts.append("\n## VALUATION CONTEXT")
        archetype_comps = comps.get("archetype_comps", {})
        if archetype_comps:
            ev_rev = archetype_comps.get("ev_revenue", {})
            ev_eb = archetype_comps.get("ev_ebitda", {})
            if ev_rev:
                parts.append(f"- EV/Revenue: {ev_rev.get('median', 'N/A')}x (median)")
            if ev_eb:
                parts.append(f"- EV/EBITDA: {ev_eb.get('median', 'N/A')}x (median)")

    # Risk assessment
    risk = report.get("risk_assessment", sim.get("risk_assessment", {}))
    if risk:
        parts.append("\n## RISK ASSESSMENT")
        if isinstance(risk, dict):
            for k, v in risk.items():
                if isinstance(v, str):
                    parts.append(f"- {k}: {v[:300]}")
                elif isinstance(v, list):
                    for item in v[:5]:
                        parts.append(f"- {item}")

    return "\n".join(parts)


def _load_raw_documents(session_id: str, owner_id: int) -> list:
    """Load raw extracted text from all uploaded documents for a session."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, file_name, doc_category, extracted_text FROM memo_documents WHERE owner_id=? AND memo_session_id=? ORDER BY doc_category, uploaded_at",
            (owner_id, session_id),
        ).fetchall()
        return [dict(r) for r in rows if (r["extracted_text"] or "").strip()]
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
#  MEMO SECTION DEFINITIONS — the canonical structure
# ═══════════════════════════════════════════════════════════════════════════════

MEMO_SECTIONS = [
    {
        "key": "investment_overview",
        "title": "Investment Overview",
        "is_synthesis": True,
        "guidance": (
            "One-liner summary of the deal. Then a quick-reference table with: VEV Focus Area, "
            "Investment Stage & Vehicle, Key Terms, Capital Amount, Expected VEV Equity, Board Participation. "
            "Follow with 'Portfolio Themes' — a short bulleted list of why this fits VoLo's thesis "
            "(e.g. low cost next-gen energy storage, rapidly scalable, system-level enabling tech). "
            "This section is written LAST after all other sections are complete."
        ),
    },
    {
        "key": "high_level_opportunities",
        "title": "High Level Opportunities",
        "is_synthesis": True,
        "guidance": (
            "3-6 bullet points capturing the strongest reasons to invest. "
            "Each bullet should be a concise, compelling statement backed by data from the memo sections. "
            "Think: team pedigree, market tailwinds, technical moat, unit economics, carbon impact. "
            "Written LAST from all completed sections."
        ),
    },
    {
        "key": "high_level_risks",
        "title": "High Level Risks",
        "is_synthesis": True,
        "guidance": (
            "3-6 bullet points capturing the most important risks and concerns. "
            "Each bullet should be specific and actionable (not vague). Reference quantified risks "
            "from the sensitivity analysis where possible. Include technology, market, execution, "
            "financing, and regulatory risks. Written LAST from all completed sections."
        ),
    },
    {
        "key": "company_overview",
        "title": "Company Overview",
        "is_synthesis": False,
        "guidance": (
            "Set a clear tone: What is the company's 'Aha' or 'breakthrough'? "
            "Contextualize the opportunity — why does the market need this, how big is the market, "
            "what is the gap, and why now? Why this categorical approach to the problem? What bottlenecks "
            "do they solve? Describe differentiation. Validate with traction and defensibility (high level — "
            "tech details go in Traction). Entice: preview 'how big this can get'. Insert team background, "
            "market size, or notable channel partners for scale if relevant. Reinforce vision."
        ),
        "report_fields": ["inputs"],
    },
    {
        "key": "market",
        "title": "Market",
        "is_synthesis": False,
        "guidance": (
            "Start broad with total demand and market size (TAM). Describe market dynamics and current "
            "composition by major segments. Then narrow to serviceable market (SAM/SOM) — where does this "
            "company have a right to win? Reference the Bass diffusion S-curve model parameters and adoption "
            "timeline from the RVM. Compare founder TAM claims to independent sources."
        ),
        "report_fields": ["inputs", "adoption"],
    },
    {
        "key": "business_model",
        "title": "Business Model",
        "is_synthesis": False,
        "guidance": (
            "How does the company make money? Cover unit economics in detail. Discuss scalability — "
            "map of capex needs and financing plans. Describe the broad go-to-market strategy. "
            "Highlight relevant strengths from the RVM (include RVM output image reference if available). "
            "Cover forward projections — compare founder projections vs simulation median, flag divergences, "
            "include divergence table data."
        ),
        "report_fields": ["extraction", "inputs", "simulation", "adoption"],
    },
    {
        "key": "team",
        "title": "Team",
        "is_synthesis": False,
        "guidance": (
            "Quick overview of relevant background — operating + founding experience, combined skills of "
            "founding team. Discuss execution to date in relation to VoLo's experience with the team. "
            "Provide additional signals that they will be able to execute against a venture trajectory/return. "
            "Include relevant factors from the RVM. Key hires needed."
        ),
        "report_fields": [],
    },
    {
        "key": "traction",
        "title": "Traction",
        "is_synthesis": False,
        "guidance": (
            "Organize into three subsections: Technical traction (TRL level, product milestones, IP), "
            "Commercial traction (revenue, pipeline, LOIs, partnerships), and Customer Feedback "
            "(references, NPS, testimonials). Be specific with data points."
        ),
        "report_fields": ["extraction"],
    },
    {
        "key": "competitive_position",
        "title": "Competitive Position",
        "is_synthesis": False,
        "guidance": (
            "Provide a broad framework to categorize competition — zoom out then zoom in. "
            "Quick overview on why/where other categories are limited. Include incumbents where relevant. "
            "If the company provided a competitive landscape slide, lift and contextualize it. "
            "Discuss barriers to entry and moat sustainability."
        ),
        "report_fields": ["inputs"],
    },
    {
        "key": "carbon_impact",
        "title": "Carbon Impact",
        "is_synthesis": False,
        "guidance": (
            "Explain the Theory of Change — what emissions does this technology displace and how? "
            "Present Carbon Economics: total lifecycle tCO2 avoided, VoLo pro-rata share, risk-adjusted "
            "tonnes, t/$ efficiency. Include reference to RVM carbon calculator output. "
            "Compare to VoLo's portfolio benchmarks. Connect to the climate thesis."
        ),
        "report_fields": ["carbon"],
    },
    {
        "key": "technology_ip_moat",
        "title": "Technology: Overview, IP and Moat",
        "is_synthesis": False,
        "guidance": (
            "Technical brief including: differentiation (how the tech works, why it's better), "
            "what is derisked to date, derisking ahead — remaining categorical step changes and how "
            "VoLo got comfortable with these. Discuss IP/patent landscape and freedom to operate. "
            "Highlight relevant strengths from the RVM."
        ),
        "report_fields": ["inputs"],
    },
    {
        "key": "financing_overview",
        "title": "Financing Overview: Round and Exit Planning",
        "is_synthesis": False,
        "guidance": (
            "DEAL STRUCTURE: Overview of the round and VoLo's role. Include total raise. "
            "Describe VoLo Earth value-add and additionality. List committed syndicate members.\n\n"
            "CAPITAL POSITION: Total raised to date, current cash in bank, current burn rate, "
            "runway provided by this financing.\n\n"
            "ROUND PRICING: Price, implied multiple (TTM x NTM), comps discussion with public comps "
            "chart (describe categories of comps and rationale with EV/Revenue and weighting). "
            "Include private comps.\n\n"
            "EXIT PLANNING: Basic MOIC with revenue multiple range x discounted revenue projection in "
            "3-5 years. Discuss likely acquirers and strategic positioning. IPO potential if applicable. "
            "Downside protection. Include RVM Exit screenshot reference and assumptions on VoLo check, "
            "subsequent rounds, EBITDA.\n\n"
            "KEY MILESTONES & USE OF FUNDS: High-level milestones to hold company accountable for in "
            "follow-on round. Brief discussion of feasibility.\n\n"
            "GROWTH INVESTOR INSIGHTS: What would make this attractive to growth-stage investors?"
        ),
        "report_fields": ["inputs", "simulation", "valuation_comps", "check_optimization", "portfolio_impact"],
    },
    {
        "key": "recommendation",
        "title": "Investment Recommendation",
        "is_synthesis": True,
        "guidance": (
            "Clear recommendation: Invest / Pass / Conditional. Summarize the bull case and bear case. "
            "State proposed terms (check size, board seat, key conditions). Quantitative highlights: "
            "MOIC, IRR, P(>3x), carbon t/$, portfolio impact. Identify follow-up diligence items. "
            "This section is written LAST after all other sections."
        ),
    },
]

# Section keys for non-synthesis (written in pass 2) vs synthesis (written in pass 3)
_DATA_SECTIONS = [s for s in MEMO_SECTIONS if not s["is_synthesis"]]
_SYNTHESIS_SECTIONS = [s for s in MEMO_SECTIONS if s["is_synthesis"]]


# ═══════════════════════════════════════════════════════════════════════════════
#  WRITING STYLE GUIDE — derived from high-quality reference memos
# ═══════════════════════════════════════════════════════════════════════════════

_STYLE_GUIDE = """
WRITING STYLE — emulate the style of a top-tier venture capital diligence memo:

VOICE & TONE:
- Write with conviction and intellectual authority, but remain grounded in evidence
- Use a confident, declarative voice — avoid hedging language like "it seems", "perhaps", "it could be argued"
- Be direct about both strengths AND weaknesses; credibility comes from balanced honesty, not cheerleading
- Maintain a tone that is professional yet engaging — this should read like a compelling narrative, not a dry report
- Write for a sophisticated but diverse audience: IC members may include deep technologists, financial experts, and generalist partners

NARRATIVE STRUCTURE:
- Open each section with a strong orienting statement that frames WHY this topic matters for the investment thesis
- Build from context to specifics: set the macro landscape first, then zoom into the company's specific position
- Connect every technical or market fact back to its investment implication — never leave data uninterpreted
- Use transitional sentences that connect ideas across paragraphs; the memo should flow as a coherent story
- Close sections with a forward-looking statement about what this means for the deal or remaining diligence

TECHNICAL DEPTH WITH ACCESSIBILITY:
- Explain complex technical concepts clearly enough for a non-specialist, then immediately layer on the expert-level detail
- Use parenthetical clarifications to define jargon inline: "quasi-axisymmetric (QA) stellarator", "technology readiness level (TRL)"
- When describing technology, always connect the mechanism to its commercial advantage: "planar magnets... making the system modular, resilient, and far easier to scale"
- Quantify everything possible: use specific numbers, percentages, dollar amounts, timelines, and comparisons
- Use analogies sparingly but effectively to make complex concepts intuitive

DATA-DRIVEN ARGUMENTATION:
- Lead with the strongest quantitative evidence — dollar amounts, growth rates, probability metrics, multiples
- Cite specific data points with attribution: "Princeton ZERO Lab analysis suggests...", "According to the company's financial model..."
- Frame quantitative findings in context: not just "MOIC of 4.2x" but what that means relative to the fund's targets
- Present ranges and scenarios rather than single-point estimates where uncertainty exists
- Include third-party validation wherever available: peer-reviewed research, government data, independent analyses

COMPETITIVE & MARKET FRAMING:
- Position the company within its competitive landscape explicitly — don't just describe in isolation
- Use direct, specific comparisons: "Unlike CFS, which requires complex 3D magnets, Thea uses planar coils..."
- Acknowledge the competitive strengths of alternatives before explaining why the company's approach is differentiated
- Frame market dynamics as investment tailwinds or headwinds with supporting evidence

RISK & OBJECTIVITY:
- Identify specific, concrete risks — avoid vague "the market could change" statements
- For each risk, assess severity and proximity, then propose a specific mitigant or monitoring plan
- Use language like "Gaps / Concerns" and "While [strength], [counterpoint]" to signal balanced analysis
- Clearly distinguish between risks that are company-specific vs. industry-wide
- Note what the company CANNOT yet prove and what additional diligence would be needed

FORMATTING DISCIPLINE:
- Use prose paragraphs for analytical narrative; use bullet lists only for catalogs of discrete items (customer types, milestones, risk factors)
- Bold key terms and company names for scannability but don't overdo it
- Keep paragraphs focused on a single idea — typically 3-5 sentences
- Subheadings should be descriptive and create a scannable outline
"""


# ═══════════════════════════════════════════════════════════════════════════════
#  PASS 1 — DOCUMENT EXTRACTION: read each doc and allocate facts to sections
# ═══════════════════════════════════════════════════════════════════════════════

_EXTRACTION_SYSTEM = """You are a data room analyst for VoLo Earth Ventures. Your job is to read a document
and extract the key facts, figures, and insights that are relevant to an investment memo.

You will be given:
1. A document from the data room
2. A list of memo sections

For EACH memo section, extract the relevant information from this document using the format below.
If the document has nothing relevant to a section, write "No relevant information in this document."

EXTRACTION GUIDELINES — what to pull and how to tag it:

1. QUANTITATIVE DATA (always include):
   - Specific numbers: revenue ($12.3M ARR), growth rates (127% YoY), margins (62% gross)
   - Dates and timelines: "first commercial plant targeted for 2030"
   - Comparative metrics: "3x faster than incumbent approach", "$250M NOAK vs $1.24B FOAK"
   - Financial terms: round size, valuation, ownership, check size, liquidation preferences

2. ATTRIBUTED CLAIMS (tag the source):
   - Who says it and how credible: "Per Princeton ZERO Lab techno-economic analysis..."
   - Third-party validation vs company claims: mark "[company claim]" vs "[independent]" vs "[peer-reviewed]"
   - Customer/partner evidence: "LOI from [utility name]", "pilot with [partner]"

3. INVESTMENT IMPLICATIONS (brief annotation):
   - After key facts, add a short "→" annotation on what it means for the deal
   - Example: "Magnet production at 1/day, line of sight to 1/hour → path to mass manufacturing validated"
   - Example: "D-D fuel for pilot, D-T for commercial → fuel switch remains an undeRisked engineering step"

4. COMPETITIVE CONTEXT (when present):
   - Direct comparisons to named competitors or alternative approaches
   - What makes this approach better/worse/different and why
   - Industry benchmarks or standard performance metrics

5. RISKS & GAPS (flag explicitly):
   - Anything the document acknowledges as unproven, untested, or uncertain
   - Missing data that would be expected (e.g. no unit economics provided)
   - Assumptions that underpin projections (tag as "[assumption]")

Output format — use exactly these section headers:

[SECTION_KEY]
- Fact or data point with specifics [source tag if applicable]
  → Investment implication (one line)
- Another fact with numbers, names, dates
- Risk/gap: what's missing or unproven [flag]

Repeat for each section. Be thorough but avoid repetition. Extract the RAW MATERIAL that a
skilled memo writer needs to construct a compelling, data-driven narrative."""

_EXTRACTION_SECTIONS_LIST = "\n".join(
    f"- [{s['key']}] {s['title']}: {s['guidance'][:200]}"
    for s in _DATA_SECTIONS
)


def _pass1_extract_document(client, model: str, doc: dict, max_chars: int = 60_000) -> dict:
    """
    Pass 1: Send a single document to the LLM and get back per-section fact extractions.
    Returns {section_key: "extracted bullet points..."}.
    """
    text = doc["extracted_text"][:max_chars]
    cat = doc["doc_category"].replace('_', ' ').title()
    fname = doc["file_name"]

    user_msg = f"""# DOCUMENT: [{cat}] {fname}
(File size: {len(doc['extracted_text'])} chars)

{text}

---

# MEMO SECTIONS TO EXTRACT INTO:
{_EXTRACTION_SECTIONS_LIST}

Extract all relevant information from this document into the appropriate sections.
Include quantitative data, attributed claims, investment implications, competitive context, and risk flags."""

    try:
        response = client.messages.create(
            model=model,
            max_tokens=6000,
            system=_EXTRACTION_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
        )
        reply = "".join(b.text for b in response.content if b.type == "text")
        tokens_in = response.usage.input_tokens if response.usage else 0
        tokens_out = response.usage.output_tokens if response.usage else 0
    except Exception as e:
        logger.error(f"Pass 1 extraction failed for {fname}: {e}")
        return {"_error": str(e), "_tokens_in": 0, "_tokens_out": 0}

    # Parse the reply into section buckets
    sections = {}
    current_key = None
    current_lines = []
    for line in reply.split('\n'):
        stripped = line.strip()
        # Check for section header like [company_overview] or [COMPANY_OVERVIEW]
        bracket_match = re.match(r'^\[(\w+)\]', stripped)
        if bracket_match:
            if current_key and current_lines:
                sections[current_key] = '\n'.join(current_lines)
            current_key = bracket_match.group(1).lower()
            rest = stripped[bracket_match.end():].strip()
            current_lines = [rest] if rest else []
        elif current_key:
            current_lines.append(line)

    if current_key and current_lines:
        sections[current_key] = '\n'.join(current_lines)

    sections["_tokens_in"] = tokens_in
    sections["_tokens_out"] = tokens_out
    sections["_source"] = fname
    return sections


def _build_citation_index(raw_docs: list) -> dict:
    """
    Build a numbered citation index from uploaded documents.
    Returns {filename: citation_number} and a formatted legend string.
    """
    index = {}
    legend_parts = []
    for i, doc in enumerate(raw_docs, 1):
        fname = doc["file_name"]
        cat = doc["doc_category"].replace('_', ' ').title()
        index[fname] = i
        legend_parts.append(f"[{i}] [{cat}] {fname}")
    legend = "\n".join(legend_parts)
    return {"map": index, "legend": legend, "count": len(raw_docs)}


def _aggregate_section_briefs(all_extractions: list, section_key: str,
                               citation_index: dict = None) -> str:
    """Combine extractions from all documents for a single section, with citation numbers."""
    parts = []
    cite_map = citation_index.get("map", {}) if citation_index else {}
    for ext in all_extractions:
        source = ext.get("_source", "Unknown")
        text = ext.get(section_key, "").strip()
        if text and "no relevant information" not in text.lower():
            cite_num = cite_map.get(source, "?")
            parts.append(f"### Source [{cite_num}]: {source}\n{text}")
    return "\n\n".join(parts) if parts else ""


# ═══════════════════════════════════════════════════════════════════════════════
#  PASS 2 — SECTION WRITING: write each section from its aggregated brief
# ═══════════════════════════════════════════════════════════════════════════════

_SECTION_WRITER_SYSTEM = f"""You are VoLo Earth Ventures' Investment Committee memo writer.
You are writing ONE section of an investment memorandum.

Rules:
1. Write in professional, data-driven prose — cite specific numbers, percentages, and dollar amounts
2. Be thorough but avoid padding — every sentence should add value
3. Balance the bull case and bear case — credibility comes from honest assessment, not advocacy
4. Use Markdown formatting: ### for sub-sections, **bold** for emphasis, bullet lists only for catalogs of discrete items
5. Do NOT include the section title as a header — it will be added automatically
6. Target 400-800 words per section (more for Financing Overview and Business Model, less for shorter sections)
7. Do NOT reference other sections or say "as discussed in..." — each section stands alone
8. If information is missing or insufficient, explicitly note it as a diligence gap requiring follow-up
9. Open with a strong orienting statement that frames why this topic matters for the investment thesis
10. Connect every fact back to its investment implication — never leave data uninterpreted

{_STYLE_GUIDE}"""


def _get_report_fields_for_section(report_context: str, section: dict) -> str:
    """Extract the relevant portions of the report context for a given section."""
    if not report_context:
        return ""

    # The report context has ## headers like "## SIMULATION RESULTS", "## CARBON IMPACT", etc.
    field_mapping = {
        "inputs": ["INPUT PARAMETERS", "DEAL REPORT:"],
        "simulation": ["SIMULATION RESULTS"],
        "adoption": ["MARKET ADOPTION"],
        "carbon": ["CARBON IMPACT"],
        "portfolio_impact": ["PORTFOLIO IMPACT"],
        "sensitivity": ["SENSITIVITY ANALYSIS"],
        "valuation_comps": ["VALUATION CONTEXT"],
        "check_optimization": ["CHECK SIZE OPTIMIZATION"],
        "extraction": ["FINANCIAL MODEL EXTRACTION"],
    }

    wanted_headers = set()
    for rf in section.get("report_fields", []):
        for hdr in field_mapping.get(rf, []):
            wanted_headers.add(hdr)

    if not wanted_headers:
        return ""

    # Parse report_context into sections by ## headers
    parts = []
    current_header = ""
    current_lines = []
    for line in report_context.split('\n'):
        if line.startswith('## '):
            if current_header and current_lines:
                if any(h in current_header for h in wanted_headers):
                    parts.append('\n'.join(current_lines))
            current_header = line
            current_lines = [line]
        elif line.startswith('# '):
            # Top-level header — always include if "DEAL REPORT:" is wanted
            if current_header and current_lines:
                if any(h in current_header for h in wanted_headers):
                    parts.append('\n'.join(current_lines))
            current_header = line
            current_lines = [line]
            if any(h in line for h in wanted_headers):
                pass  # will be included
        else:
            current_lines.append(line)

    if current_header and current_lines:
        if any(h in current_header for h in wanted_headers):
            parts.append('\n'.join(current_lines))

    return '\n\n'.join(parts)


def _pass2_write_section(client, model: str, section: dict, brief: str,
                         report_slice: str, template_guidance: str,
                         company_name: str, links: list,
                         citation_legend: str = "") -> dict:
    """Pass 2: Write a single memo section from its aggregated brief + report data."""

    user_parts = [f"# SECTION: {section['title']}"]
    user_parts.append(f"## Section Purpose\n{section['guidance']}")

    if template_guidance:
        user_parts.append(f"## Template Guidance for This Section\n{template_guidance}")

    if citation_legend:
        user_parts.append(f"## Source Document Index\n{citation_legend}\n\nWhen citing information from these documents, use inline citations like [1], [2], etc. matching the numbers above. Place citations at the end of the sentence or clause they support. Use [RVM] when citing quantitative report data. You may combine citations: [1][3] or [1, RVM].")

    if report_slice:
        user_parts.append(f"## Quantitative Report Data [RVM]\n{report_slice}")

    if brief:
        user_parts.append(f"## Extracted Data Room Facts\n{brief}")
    else:
        user_parts.append("## Extracted Data Room Facts\nNo data room documents contained information for this section.")

    if links:
        user_parts.append("## Reference Links\n" + "\n".join(f"- {l}" for l in links))

    user_parts.append(f"\nWrite the '{section['title']}' section of the investment memo for {company_name or 'this company'}. Be thorough and data-driven. Cite your sources using [n] notation.")

    user_msg = "\n\n".join(user_parts)

    try:
        response = client.messages.create(
            model=model,
            max_tokens=3000,
            system=_SECTION_WRITER_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
        )
        text = "".join(b.text for b in response.content if b.type == "text")
        tokens_in = response.usage.input_tokens if response.usage else 0
        tokens_out = response.usage.output_tokens if response.usage else 0
        return {"text": text.strip(), "tokens_in": tokens_in, "tokens_out": tokens_out}
    except Exception as e:
        logger.error(f"Pass 2 section write failed for {section['key']}: {e}")
        return {"text": f"*[Generation failed for this section: {str(e)[:200]}]*", "tokens_in": 0, "tokens_out": 0}


# ═══════════════════════════════════════════════════════════════════════════════
#  PASS 3 — SYNTHESIS: Exec Summary & Recommendation from all sections
# ═══════════════════════════════════════════════════════════════════════════════

_SYNTHESIS_SYSTEM = f"""You are VoLo Earth Ventures' Investment Committee memo writer.
You are writing a SYNTHESIS section that draws from the entire investment memo.

Rules:
1. Synthesize across all sections — do not just summarize one part
2. For the Investment Overview: write a compelling one-liner, populate the deal terms table, and list portfolio themes as concise bullets
3. For High Level Opportunities / Risks: write 3-6 specific, evidence-backed bullet points — each should be punchy and data-driven
4. For the Investment Recommendation: give a clear verdict (Invest / Pass / Conditional) with the bull and bear case
5. Include the most important quantitative highlights: MOIC, IRR, P(>3x), carbon t/$, portfolio impact
6. Do NOT include the section title as a header — it will be added automatically
7. Write with conviction and intellectual authority — this should read like a narrative that commands attention
8. Frame this as a generational opportunity or a thoughtful pass — avoid lukewarm language

{_STYLE_GUIDE}"""


def _pass3_synthesize(client, model: str, section: dict, all_section_texts: dict,
                      report_context: str, company_name: str,
                      additional_instructions: str) -> dict:
    """Pass 3: Write synthesis sections (Exec Summary, Recommendation) from all completed sections."""

    # Build a condensed version of all sections
    digest_parts = []
    for s in MEMO_SECTIONS:
        if s["is_synthesis"]:
            continue
        text = all_section_texts.get(s["key"], "")
        if text:
            # Truncate each section to ~800 chars for the digest
            truncated = text[:800] + ("..." if len(text) > 800 else "")
            digest_parts.append(f"### {s['title']}\n{truncated}")

    digest = "\n\n".join(digest_parts)

    # Also include key quantitative highlights from the report
    quant_summary = ""
    if report_context:
        # Pull the hero metrics and headline numbers
        for line in report_context.split('\n'):
            if any(k in line for k in ['Expected MOIC', 'P(>3x)', 'Expected IRR', 'Survival Rate',
                                        'Total Lifecycle tCO2', 'Risk-Adjusted', 'TVPI',
                                        'Optimal Check', 'Fund P50 Impact']):
                quant_summary += line + '\n'

    user_parts = [f"# SYNTHESIS SECTION: {section['title']}"]
    user_parts.append(f"## Purpose\n{section['guidance']}")

    if quant_summary:
        user_parts.append(f"## Key Quantitative Highlights\n{quant_summary}")

    user_parts.append(f"## Memo Sections Written So Far\n{digest}")

    if additional_instructions:
        user_parts.append(f"## Additional Instructions\n{additional_instructions}")

    user_parts.append(f"\nWrite the '{section['title']}' section for {company_name or 'this company'}.")

    user_msg = "\n\n".join(user_parts)

    try:
        response = client.messages.create(
            model=model,
            max_tokens=2000,
            system=_SYNTHESIS_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
        )
        text = "".join(b.text for b in response.content if b.type == "text")
        tokens_in = response.usage.input_tokens if response.usage else 0
        tokens_out = response.usage.output_tokens if response.usage else 0
        return {"text": text.strip(), "tokens_in": tokens_in, "tokens_out": tokens_out}
    except Exception as e:
        logger.error(f"Pass 3 synthesis failed for {section['key']}: {e}")
        return {"text": f"*[Synthesis failed: {str(e)[:200]}]*", "tokens_in": 0, "tokens_out": 0}


# ═══════════════════════════════════════════════════════════════════════════════
#  TEMPLATE PARSING — extract per-section guidance from user template
# ═══════════════════════════════════════════════════════════════════════════════

def _parse_template_sections(template_text: str) -> dict:
    """
    Parse user template into per-section guidance.
    Returns {section_key: "template guidance text"}.
    Uses fuzzy matching on section titles with keyword fallback.
    """
    if not template_text:
        return {}

    # Build mapping from lowercase title fragments to section keys
    title_map = {}
    for s in MEMO_SECTIONS:
        title_lower = s["title"].lower()
        title_map[title_lower] = s["key"]

    # Additional keyword → section key mappings for the VoLo template
    _KEYWORD_MAP = {
        "investment overview": "investment_overview",
        "portfolio themes": "investment_overview",
        "high level opportunities": "high_level_opportunities",
        "opportunities": "high_level_opportunities",
        "high level risks": "high_level_risks",
        "company overview": "company_overview",
        "company": "company_overview",
        "market": "market",
        "business model": "business_model",
        "unit economics": "business_model",
        "team": "team",
        "traction": "traction",
        "competitive position": "competitive_position",
        "competitive": "competitive_position",
        "competition": "competitive_position",
        "carbon impact": "carbon_impact",
        "carbon": "carbon_impact",
        "technology": "technology_ip_moat",
        "ip and moat": "technology_ip_moat",
        "moat": "technology_ip_moat",
        "financing overview": "financing_overview",
        "financing": "financing_overview",
        "round and exit": "financing_overview",
        "exit planning": "financing_overview",
        "deal structure": "financing_overview",
        "round pricing": "financing_overview",
        "capital position": "financing_overview",
        "recommendation": "recommendation",
        "investment recommendation": "recommendation",
    }

    def _match_header(header_text: str) -> Optional[str]:
        ht = header_text.lower().strip()
        # Exact title match
        if ht in title_map:
            return title_map[ht]
        # Keyword map match
        for phrase, key in _KEYWORD_MAP.items():
            if phrase in ht or ht in phrase:
                return key
        # Fallback: single long word match
        for s in MEMO_SECTIONS:
            for word in s["title"].lower().split():
                if len(word) > 4 and word in ht:
                    return s["key"]
        return None

    # Split template by markdown headers (# ## ### or underline-style)
    sections = {}
    current_key = None
    current_lines = []

    for line in template_text.split('\n'):
        # Match markdown headers or pandoc-style underline headers
        header_match = re.match(r'^#{1,3}\s+(.+)', line)
        if not header_match:
            # Also catch pandoc's underline-style: "Title\n====" or "Title\n----"
            # (already converted by pandoc — the title becomes "# Title")
            pass
        if header_match:
            if current_key and current_lines:
                sections[current_key] = '\n'.join(current_lines).strip()
            header_text = header_match.group(1).strip()
            current_key = _match_header(header_text)
            current_lines = []
        elif current_key is not None:
            current_lines.append(line)

    if current_key and current_lines:
        sections[current_key] = '\n'.join(current_lines).strip()

    return sections


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN GENERATE ENDPOINT — orchestrates the 3-pass pipeline
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/generate")
async def generate_memo(req: MemoGenerateRequest, user: CurrentUser = Depends(get_current_user)):
    import anthropic
    start_time = time.time()

    # ── Resolve model ──
    prefs = get_model_preferences(user.id)
    model = req.model_override or prefs.get("memo_generation", MODEL_DEFAULTS["memo_generation"])
    is_refiant = model.startswith("qwen")

    if is_refiant:
        api_key = os.environ.get("REFIANT_API_KEY", "")
        if not api_key:
            raise HTTPException(status_code=500, detail="REFIANT_API_KEY not configured. Set it in .env or environment.")
        client = anthropic.Anthropic(api_key=api_key, base_url="https://api.refiant.ai/v1")
    else:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not configured. Set it in .env or environment.")
        client = anthropic.Anthropic(api_key=api_key)

    # ── Gather raw inputs ──
    conn = get_db()
    try:
        # Report
        report_context = ""
        company_name = req.company_name
        report_id = req.report_id
        if report_id:
            row = conn.execute(
                "SELECT * FROM deal_reports WHERE id=? AND owner_id=?",
                (report_id, user.id),
            ).fetchone()
            if row:
                report_context = _build_report_context(row)
                if not company_name:
                    company_name = row["company_name"]

        # Template
        template_text = req.template_text or ""
        template_id = req.template_id
        if not template_text and template_id:
            tpl = conn.execute(
                "SELECT content FROM memo_templates WHERE id=? AND owner_id=?",
                (template_id, user.id),
            ).fetchone()
            if tpl:
                template_text = tpl["content"]
    finally:
        conn.close()

    # Load raw documents — from session uploads and/or deal library
    raw_docs = []
    if req.session_id:
        raw_docs = _load_raw_documents(req.session_id, user.id)
    if req.library_id:
        from .drive import load_library_documents
        library_docs = load_library_documents(req.library_id, user.id)
        raw_docs.extend(library_docs)

    links = req.links or []
    template_sections = _parse_template_sections(template_text)
    total_tokens_in = 0
    total_tokens_out = 0
    pass_log = []

    # ── Build citation index ──
    citation_index = _build_citation_index(raw_docs) if raw_docs else {"map": {}, "legend": "", "count": 0}

    # ═════════════════════════════════════════════════════════════════════════
    #  PASS 1: Extract facts from each document into section buckets
    # ═════════════════════════════════════════════════════════════════════════
    all_extractions = []
    if raw_docs:
        for doc in raw_docs:
            extraction = _pass1_extract_document(client, model, doc)
            all_extractions.append(extraction)
            total_tokens_in += extraction.get("_tokens_in", 0)
            total_tokens_out += extraction.get("_tokens_out", 0)
            pass_log.append({"pass": 1, "doc": doc["file_name"],
                             "tokens": extraction.get("_tokens_in", 0) + extraction.get("_tokens_out", 0)})

    # ═════════════════════════════════════════════════════════════════════════
    #  PASS 2: Write each data section from its aggregated brief + report slice
    # ═════════════════════════════════════════════════════════════════════════
    section_texts = {}
    for section in _DATA_SECTIONS:
        brief = _aggregate_section_briefs(all_extractions, section["key"], citation_index)
        report_slice = _get_report_fields_for_section(report_context, section)
        tpl_guidance = template_sections.get(section["key"], "")

        result = _pass2_write_section(
            client, model, section, brief, report_slice,
            tpl_guidance, company_name, links,
            citation_legend=citation_index["legend"]
        )
        section_texts[section["key"]] = result["text"]
        total_tokens_in += result["tokens_in"]
        total_tokens_out += result["tokens_out"]
        pass_log.append({"pass": 2, "section": section["key"],
                         "tokens": result["tokens_in"] + result["tokens_out"]})

    # ═════════════════════════════════════════════════════════════════════════
    #  PASS 3: Synthesize cross-cutting sections (Exec Summary, Recommendation)
    # ═════════════════════════════════════════════════════════════════════════
    for section in _SYNTHESIS_SECTIONS:
        result = _pass3_synthesize(
            client, model, section, section_texts,
            report_context, company_name,
            req.additional_instructions
        )
        section_texts[section["key"]] = result["text"]
        total_tokens_in += result["tokens_in"]
        total_tokens_out += result["tokens_out"]
        pass_log.append({"pass": 3, "section": section["key"],
                         "tokens": result["tokens_in"] + result["tokens_out"]})

    # ═════════════════════════════════════════════════════════════════════════
    #  ASSEMBLE final memo in section order
    # ═════════════════════════════════════════════════════════════════════════
    memo_parts = [f"# Investment Memorandum: {company_name or 'Deal Analysis'}\n"]
    memo_parts.append(f"*VoLo Earth Ventures — Confidential*\n")

    for section in MEMO_SECTIONS:
        text = section_texts.get(section["key"], "")
        if text:
            memo_parts.append(f"## {section['title']}\n\n{text}\n")

    # Appendix: data sources with citation numbers
    if raw_docs or links or report_context:
        memo_parts.append("## Appendix: Sources\n")
        if raw_docs:
            memo_parts.append("### Documents Reviewed")
            cite_map = citation_index.get("map", {})
            for doc in raw_docs:
                cat = doc["doc_category"].replace('_', ' ').title()
                num = cite_map.get(doc["file_name"], "?")
                memo_parts.append(f"- **[{num}]** [{cat}] {doc['file_name']}")
            memo_parts.append("")
        if report_context:
            memo_parts.append("- **[RVM]** VoLo Return Validation Model — Deal Report\n")
        if links:
            memo_parts.append("### Reference Links")
            for l in links:
                memo_parts.append(f"- {l}")
            memo_parts.append("")

    memo_md = "\n".join(memo_parts)
    elapsed = time.time() - start_time

    # ── Convert to HTML ──
    memo_html = _markdown_to_html(memo_md)

    # ── Save to DB ──
    conn = get_db()
    try:
        cur = conn.execute(
            """INSERT INTO generated_memos
               (owner_id, report_id, template_id, company_name, memo_markdown, memo_html,
                model_used, input_token_count, output_token_count, generation_time_s)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user.id, report_id, template_id, company_name, memo_md, memo_html,
             model, total_tokens_in, total_tokens_out, elapsed),
        )
        conn.commit()
        memo_id = cur.lastrowid
    finally:
        conn.close()

    # Build citation metadata for frontend popovers
    citations_meta = {}
    if raw_docs:
        cite_map = citation_index.get("map", {})
        for doc in raw_docs:
            num = cite_map.get(doc["file_name"], 0)
            cat = doc["doc_category"].replace('_', ' ').title()
            # Build brief excerpt from extracted text (first ~500 chars)
            excerpt = (doc.get("extracted_text") or "")[:500].strip()
            if len(doc.get("extracted_text", "")) > 500:
                excerpt += "..."
            citations_meta[str(num)] = {
                "number": num,
                "file_name": doc["file_name"],
                "category": cat,
                "excerpt": excerpt,
            }
    if report_context:
        citations_meta["RVM"] = {
            "number": "RVM",
            "file_name": "VoLo Return Validation Model",
            "category": "Deal Report",
            "excerpt": report_context[:500] + ("..." if len(report_context) > 500 else ""),
        }

    return {
        "id": memo_id,
        "company_name": company_name,
        "memo_markdown": memo_md,
        "memo_html": memo_html,
        "model_used": model,
        "input_tokens": total_tokens_in,
        "output_tokens": total_tokens_out,
        "generation_time_s": round(elapsed, 2),
        "citations": citations_meta,
        "pipeline": {
            "documents_processed": len(raw_docs),
            "sections_written": len(section_texts),
            "passes": pass_log,
            "total_llm_calls": len(pass_log),
        },
    }


def _markdown_to_html(md: str) -> str:
    """Lightweight markdown-to-HTML conversion."""
    html = md

    # Headers
    html = re.sub(r'^#### (.+)$', r'<h4>\1</h4>', html, flags=re.MULTILINE)
    html = re.sub(r'^### (.+)$', r'<h3>\1</h3>', html, flags=re.MULTILINE)
    html = re.sub(r'^## (.+)$', r'<h2>\1</h2>', html, flags=re.MULTILINE)
    html = re.sub(r'^# (.+)$', r'<h1>\1</h1>', html, flags=re.MULTILINE)

    # Bold and italic
    html = re.sub(r'\*\*\*(.+?)\*\*\*', r'<strong><em>\1</em></strong>', html)
    html = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', html)
    html = re.sub(r'\*(.+?)\*', r'<em>\1</em>', html)

    # Lists
    html = re.sub(r'^- (.+)$', r'<li>\1</li>', html, flags=re.MULTILINE)
    html = re.sub(r'(<li>.*</li>\n?)+', lambda m: '<ul>' + m.group(0) + '</ul>', html)

    # Numbered lists
    html = re.sub(r'^\d+\. (.+)$', r'<li>\1</li>', html, flags=re.MULTILINE)

    # Tables (basic pipe tables)
    lines = html.split('\n')
    in_table = False
    table_lines = []
    result = []
    for line in lines:
        if '|' in line and line.strip().startswith('|'):
            if not in_table:
                in_table = True
                table_lines = []
            table_lines.append(line)
        else:
            if in_table:
                result.append(_convert_table(table_lines))
                in_table = False
                table_lines = []
            result.append(line)
    if in_table:
        result.append(_convert_table(table_lines))
    html = '\n'.join(result)

    # Paragraphs — wrap non-tag text blocks
    html = re.sub(r'^(?!<[hul1-9ol]|<li|<table|<tr|<th|<td)(.+)$', r'<p>\1</p>', html, flags=re.MULTILINE)

    # Clean up empty paragraphs
    html = re.sub(r'<p>\s*</p>', '', html)

    # ── Inline citations: convert [1], [2], [RVM], [1, 3], etc. into clickable spans ──
    def _cite_replace(m):
        raw = m.group(1)  # e.g. "1" or "RVM" or "1, 3"
        parts = [p.strip() for p in raw.split(',')]
        spans = []
        for p in parts:
            css_class = "memo-cite"
            data_cite = p
            if p.upper() == "RVM":
                css_class += " memo-cite-rvm"
            spans.append(f'<span class="{css_class}" data-cite="{data_cite}" title="Source [{p}] — click to view">[{p}]</span>')
        return ''.join(spans)

    # Match [n], [RVM], [n, m], but NOT markdown links [text](url) or bold **[text]**
    html = re.sub(r'(?<!\()\[(\d+(?:\s*,\s*\d+)*(?:\s*,\s*RVM)?|RVM(?:\s*,\s*\d+)*)\](?!\()', _cite_replace, html)

    return html


def _convert_table(lines):
    """Convert markdown pipe table to HTML."""
    if len(lines) < 2:
        return '\n'.join(lines)

    html = '<table class="memo-table">'

    # Header row
    cells = [c.strip() for c in lines[0].strip('|').split('|')]
    html += '<thead><tr>' + ''.join(f'<th>{c}</th>' for c in cells) + '</tr></thead>'

    # Body rows (skip separator line)
    html += '<tbody>'
    for line in lines[2:]:
        cells = [c.strip() for c in line.strip('|').split('|')]
        html += '<tr>' + ''.join(f'<td>{c}</td>' for c in cells) + '</tr>'
    html += '</tbody></table>'

    return html


# ═══════════════════════════════════════════════════════════════════════════════
#  MEMO HISTORY
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/history")
async def list_memos(user: CurrentUser = Depends(get_current_user)):
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT id, company_name, model_used, input_token_count, output_token_count,
                      generation_time_s, status, created_at
               FROM generated_memos WHERE owner_id=? ORDER BY created_at DESC LIMIT 50""",
            (user.id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.get("/history/{memo_id}")
async def get_memo(memo_id: int, user: CurrentUser = Depends(get_current_user)):
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM generated_memos WHERE id=? AND owner_id=?",
            (memo_id, user.id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Memo not found")
        return dict(row)
    finally:
        conn.close()


@router.delete("/history/{memo_id}")
async def delete_memo(memo_id: int, user: CurrentUser = Depends(get_current_user)):
    conn = get_db()
    try:
        conn.execute("DELETE FROM generated_memos WHERE id=? AND owner_id=?", (memo_id, user.id))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
#  DOCX EXPORT
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/history/{memo_id}/docx")
async def export_memo_docx(memo_id: int, token: Optional[str] = Query(None)):
    """Export memo as .docx. Supports token via query param for direct browser download."""
    if not token:
        raise HTTPException(status_code=401, detail="Token required")
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    user = CurrentUser(uid=int(payload["sub"]), username=payload["user"], role=payload["role"])
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT company_name, memo_markdown FROM generated_memos WHERE id=? AND owner_id=?",
            (memo_id, user.id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Memo not found")
    finally:
        conn.close()

    try:
        from docx import Document
        from docx.shared import Inches, Pt, RGBColor
        from docx.enum.text import WD_ALIGN_PARAGRAPH
    except ImportError:
        raise HTTPException(status_code=500, detail="python-docx not installed")

    doc = Document()

    # Style setup
    style = doc.styles['Normal']
    font = style.font
    font.name = 'Calibri'
    font.size = Pt(11)

    # Title
    title = doc.add_heading(f"Investment Memorandum: {row['company_name']}", level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    # Subtitle
    sub = doc.add_paragraph("VoLo Earth Ventures — Confidential")
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    sub.runs[0].font.color.rgb = RGBColor(0x58, 0x60, 0x69)
    sub.runs[0].font.size = Pt(12)

    doc.add_paragraph("")  # spacer

    # Parse markdown into doc
    lines = row["memo_markdown"].split('\n')
    for line in lines:
        line = line.rstrip()
        if line.startswith('#### '):
            doc.add_heading(line[5:], level=4)
        elif line.startswith('### '):
            doc.add_heading(line[4:], level=3)
        elif line.startswith('## '):
            doc.add_heading(line[3:], level=2)
        elif line.startswith('# '):
            doc.add_heading(line[2:], level=1)
        elif line.startswith('- '):
            doc.add_paragraph(line[2:], style='List Bullet')
        elif re.match(r'^\d+\. ', line):
            doc.add_paragraph(re.sub(r'^\d+\. ', '', line), style='List Number')
        elif line.strip():
            # Handle bold/italic inline
            p = doc.add_paragraph()
            _add_formatted_text(p, line)
        # Skip empty lines

    # Save to temp file
    tmp = tempfile.NamedTemporaryFile(suffix='.docx', delete=False)
    doc.save(tmp.name)
    tmp.close()

    safe_name = re.sub(r'[^\w\-.]', '_', row["company_name"] or "memo")
    return FileResponse(
        tmp.name,
        media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        filename=f"Investment_Memo_{safe_name}.docx",
    )


def _add_formatted_text(paragraph, text):
    """Parse bold/italic markdown inline and add runs to paragraph."""
    # Split on bold markers
    parts = re.split(r'(\*\*\*.+?\*\*\*|\*\*.+?\*\*|\*.+?\*)', text)
    for part in parts:
        if part.startswith('***') and part.endswith('***'):
            run = paragraph.add_run(part[3:-3])
            run.bold = True
            run.italic = True
        elif part.startswith('**') and part.endswith('**'):
            run = paragraph.add_run(part[2:-2])
            run.bold = True
        elif part.startswith('*') and part.endswith('*'):
            run = paragraph.add_run(part[1:-1])
            run.italic = True
        else:
            paragraph.add_run(part)
