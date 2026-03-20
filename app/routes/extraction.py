"""
Document extraction routes: pitch deck, web enrichment, financial model pipeline.
"""

import io
import json
import logging
import os
import secrets
import tempfile

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

logger = logging.getLogger(__name__)

from ..auth import CurrentUser, get_current_user, get_optional_user
from ..database import get_db, get_model_preferences, MODEL_DEFAULTS
from ..engine.extraction import (
    extract_text_pdf_with_ocr,
    extract_text_pptx_enhanced,
    extract_tables_from_pdf,
    two_pass_extract,
)

router = APIRouter(tags=["extraction"])

# ── Shared resource list for prompts ──────────────────────────────────────────

_RESOURCES_LIST = (
    "US electricity, Global electricity, Gas to Electricity, Diesel, Gasoline, "
    "Natural Gas, Natural Gas (CCGT), Gas Turbine (CCGT), Limestone, "
    "Limestone calcination, Crushed Limestone, Li-ion Battery embodied, "
    "Li-ion Battery EV, Battery Cathode NMC62, Nickel, Polypropylene"
)


# ── Text extraction helpers ───────────────────────────────────────────────────

def _extract_text_docx(file_bytes: bytes) -> str:
    try:
        from docx import Document
        doc = Document(io.BytesIO(file_bytes))
        parts = []
        for para in doc.paragraphs:
            t = para.text.strip()
            if t:
                parts.append(t)
        for table in doc.tables:
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells]
                parts.append(" | ".join(cells))
        return "\n".join(parts)[:80000]
    except ImportError:
        raise ValueError("python-docx not installed for .docx support")


def _extract_text_url(url: str) -> str:
    import requests as _req
    from bs4 import BeautifulSoup
    resp = _req.get(
        url, timeout=15,
        headers={"User-Agent": "Mozilla/5.0 (compatible; VoLo-bot/1.0)"},
    )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()
    return soup.get_text(separator="\n", strip=True)[:80000]




# ── Routes: Pitch deck / URL extraction ───────────────────────────────────────

@router.post("/api/extract")
async def extract_from_source(
    file: UploadFile = File(None),
    url: str = Form(None),
    user: CurrentUser = Depends(get_optional_user),
):
    try:
        text = ""
        source_name = ""
        tables = []

        if file and file.filename:
            data = await file.read()
            fname = file.filename.lower()
            source_name = file.filename
            if fname.endswith(".pdf"):
                text = extract_text_pdf_with_ocr(data)
                tables = extract_tables_from_pdf(data)
            elif fname.endswith((".pptx", ".ppt")):
                text = extract_text_pptx_enhanced(data)
            elif fname.endswith(".docx"):
                text = _extract_text_docx(data)
            else:
                raise HTTPException(400, "Unsupported type — upload PDF, PPTX, or DOCX")
        elif url:
            url = url.strip()
            source_name = url
            text = _extract_text_url(url)
        else:
            raise HTTPException(400, "No file or URL provided")

        if not text.strip():
            raise HTTPException(422, "No readable text found in source")

        # Resolve per-user model preference for extraction
        extraction_model = MODEL_DEFAULTS["extraction"]
        if user and user.id:
            prefs = get_model_preferences(user.id)
            extraction_model = prefs.get("extraction", extraction_model)

        result = two_pass_extract(text, source_name, tables=tables, model=extraction_model)
        return result

    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"Extraction failed: {exc}")


# ── Routes: Standalone financial model extraction (wizard flow) ───────────────

@router.post("/api/extract-model")
async def extract_financial_model_standalone(
    file: UploadFile = File(...),
    user: CurrentUser = Depends(get_optional_user),
):
    """Extract financial data from an Excel model without requiring a company record."""
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in (".xlsx", ".xlsm", ".xls", ".csv"):
        raise HTTPException(400, f"Unsupported file type: {ext}. Upload .xlsx, .xls, or .csv")

    upload_dir = os.path.join(tempfile.gettempdir(), f"fm_wiz_{secrets.token_hex(6)}")
    os.makedirs(upload_dir, exist_ok=True)
    input_path = os.path.join(upload_dir, file.filename)

    content = await file.read()
    with open(input_path, "wb") as f:
        f.write(content)

    out_dir = os.path.join(upload_dir, "output")
    os.makedirs(out_dir, exist_ok=True)

    try:
        from ..engine.financial_pipeline import run_pipeline
        result = run_pipeline(
            input_path=input_path,
            company_id="wizard",
            fy_end_month=12,
            out_dir=out_dir,
        )

        raw_years = result.get("fiscal_years", [])
        valid_years = sorted([y for y in raw_years if isinstance(y, (int, float)) and 2015 <= y <= 2050])

        def _year_ok(k):
            try:
                return 2015 <= int(float(k)) <= 2050
            except (ValueError, TypeError):
                return False

        records = result.get("records", [])
        failures = result.get("failures", [])
        from ..engine.financial_pipeline import UNIT_METRICS

        scale_info = "USD"

        # Keywords that indicate a consolidated / company-level sheet (highest priority)
        CONSOLIDATED_KEYWORDS = {"consolidated", "consol", "total", "combined", "group"}
        # Keywords that indicate a forecast / summary sheet (high priority)
        FORECAST_KEYWORDS = {"forecast", "outputs", "summary",
                             "projections", "pro forma", "proforma", "model",
                             "p&l", "income statement", "income_statement", "financials"}
        # Keywords that indicate a segment / geography / entity sheet (low priority)
        SEGMENT_KEYWORDS = {
            "north america", "europe", "asia", "apac", "emea", "latam",
            "norway", "usa", "uk", "germany", "japan", "china", "india",
            "canada", "australia", "france", "brazil", "mexico",
            "segment", "division", "region", "subsidiary", "entity",
        }

        def _sheet_priority(sheet_name):
            """Return priority score: higher = preferred. Consolidated > Forecast > Default > Segment."""
            sl = (sheet_name or "").lower()
            is_consolidated = any(kw in sl for kw in CONSOLIDATED_KEYWORDS)
            is_forecast = any(kw in sl for kw in FORECAST_KEYWORDS)
            is_segment = any(kw in sl for kw in SEGMENT_KEYWORDS)

            if is_consolidated:
                return 100 + (10 if is_forecast else 0)  # "Consolidated Forecast" = 110
            if is_segment:
                return 10  # Always low priority regardless of other keywords
            if is_forecast:
                return 80
            return 50  # Default/unknown sheets

        best_records = {}
        for r in records:
            fy = r.get("fiscal_year")
            if not _year_ok(fy):
                continue
            metric = r.get("metric")
            val = r.get("value_usd")
            if val is None:
                continue

            scenario = r.get("scenario", "base")

            prov = r.get("provenance", {})
            sheet = prov.get("source_sheet", "")

            key = (scenario, metric, fy)
            priority = _sheet_priority(sheet)
            existing = best_records.get(key)
            if existing is None:
                best_records[key] = {"val": val, "sheet": sheet, "priority": priority, "record": r}
            elif priority > existing["priority"]:
                best_records[key] = {"val": val, "sheet": sheet, "priority": priority, "record": r}

        # Build per-scenario financials and units
        all_scenarios_financials = {}
        all_scenarios_units = {}
        detected_scenarios = set()

        for (scenario, metric, fy), info in best_records.items():
            detected_scenarios.add(scenario)
            fy_str = str(fy)
            r = info["record"]
            if metric in UNIT_METRICS:
                all_scenarios_units.setdefault(scenario, {}).setdefault(metric, {})[fy_str] = {
                    "value": info["val"],
                    "unit_type": r.get("unit_type"),
                }
            else:
                all_scenarios_financials.setdefault(scenario, {}).setdefault(metric, {})[fy_str] = info["val"]

        def _clean_financials(fin_dict):
            """Remove margin_pct_annual, filter outliers relative to revenue."""
            rev_by_year = {}
            if "revenue" in fin_dict:
                rev_by_year = {k: abs(v) for k, v in fin_dict["revenue"].items() if v}
            rev_max = max(rev_by_year.values()) if rev_by_year else 0

            for metric in list(fin_dict.keys()):
                if metric == "revenue":
                    continue
                if metric == "margin_pct_annual":
                    del fin_dict[metric]
                    continue
                series = fin_dict[metric]
                vals = [v for v in series.values() if v is not None and v != 0]
                if not vals:
                    del fin_dict[metric]
                    continue
                cleaned = {}
                for k, v in series.items():
                    if v is None:
                        continue
                    absv = abs(v)
                    same_yr_rev = rev_by_year.get(k, 0)
                    if same_yr_rev > 0 and absv > same_yr_rev * 50:
                        continue
                    if same_yr_rev == 0 and rev_max > 0 and absv > rev_max * 50:
                        continue
                    if rev_max > 0 and absv > rev_max * 50:
                        continue
                    cleaned[k] = v
                fin_dict[metric] = cleaned
                if not cleaned:
                    del fin_dict[metric]
            return fin_dict

        # Clean each scenario
        for sc in list(all_scenarios_financials.keys()):
            all_scenarios_financials[sc] = _clean_financials(all_scenarios_financials[sc])

        # Primary = "base" scenario; fallback to scenario with most data
        primary_scenario = "base"
        if "base" not in all_scenarios_financials and all_scenarios_financials:
            primary_scenario = max(
                all_scenarios_financials,
                key=lambda s: sum(len(v) for v in all_scenarios_financials[s].values())
            )
        financials = all_scenarios_financials.get(primary_scenario, {})
        units = all_scenarios_units.get(primary_scenario, {})

        # Build scenarios output (bear/base/bull)
        scenarios_output = {}
        for sc in sorted(detected_scenarios):
            scenarios_output[sc] = {
                "financials": all_scenarios_financials.get(sc, {}),
                "units": all_scenarios_units.get(sc, {}),
            }

        summary = result.get("model_summary", {})
        clean_summary = {}
        for k, v in summary.items():
            if isinstance(v, dict):
                clean_summary[k] = json.dumps(v)
            elif isinstance(v, list):
                clean_summary[k] = ", ".join(str(x) for x in v)
            else:
                clean_summary[k] = v

        # Build extraction diagnostics: which sheets had which metrics, and priority scores
        _diag_sheets = {}
        for r in records:
            prov = r.get("provenance", {})
            sheet = prov.get("source_sheet", "?")
            metric = r.get("metric", "?")
            fy = r.get("fiscal_year")
            if sheet not in _diag_sheets:
                _diag_sheets[sheet] = {"priority": _sheet_priority(sheet), "metrics": {}}
            _diag_sheets[sheet]["metrics"].setdefault(metric, []).append(fy)

        _diag_best = {}
        for (sc, metric, fy), info in best_records.items():
            _diag_best.setdefault(metric, []).append({
                "fy": fy, "sheet": info["sheet"], "priority": info["priority"],
                "val": round(info["val"], 2) if info["val"] else None
            })

        return {
            "status": "ok",
            "file_name": file.filename,
            "records_count": len([r for r in records if _year_ok(r.get("fiscal_year", 0))]),
            "failures_count": result.get("failures_count", 0),
            "financials": financials,
            "units": units,
            "fiscal_years": valid_years,
            "scale_info": scale_info,
            "model_summary": clean_summary,
            "scenarios": scenarios_output if len(detected_scenarios) > 1 else None,
            "detected_scenarios": sorted(detected_scenarios),
            "primary_scenario": primary_scenario,
            "_diagnostics": {
                "sheets_processed": _diag_sheets,
                "best_record_sources": _diag_best,
                "pipeline_sheet_diagnostics": result.get("_sheet_diagnostics", []),
                "all_workbook_sheets": result.get("_all_sheets", []),
                "extraction_failures": [
                    {
                        "metric": f.get("metric"),
                        "sheet": f.get("provenance", {}).get("source_sheet"),
                        "fail_code": f.get("quality", {}).get("fail_code"),
                        "details": f.get("quality", {}).get("details", "")[:200],
                        "has_formulas": f.get("quality", {}).get("_has_formulas", False),
                    }
                    for f in failures
                ],
            },
        }
    except Exception as exc:
        import traceback
        traceback.print_exc()
        raise HTTPException(500, f"Financial model extraction failed: {exc}")


