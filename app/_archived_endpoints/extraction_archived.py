"""
ARCHIVED: Unused extraction endpoints.
- Web enrichment system (company website scraping + Claude enrichment)
- Company-scoped financial model extraction
- Financial model CRUD operations
- Pipeline status tracking

These endpoints are no longer used by the frontend.
Kept for reference if they need to be revived.

Date archived: 2026-03-17
"""

import json
import logging
import os
import re
import secrets
import tempfile
import threading
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

logger = logging.getLogger(__name__)

from ..auth import CurrentUser, get_current_user, get_optional_user
from ..database import get_db

router = APIRouter(tags=["extraction-archived"])

_MAX_PIPELINE_RUNS = 200
_pipeline_runs: dict = {}

# ── Shared resource list for enrichment ──────────────────────────────────────

_ENRICH_RESOURCES = (
    "Electricity, Natural Gas, Diesel, Gasoline, Propane, Coal, Heating Oil, "
    "Wood/Biomass, Steam, Hydrogen, Refrigerant, Water, Waste, Steel, Cement, Aluminum"
)


# ── Claude extraction schemas (enrichment only) ──────────────────────────────

_ENRICH_SCHEMA = """{
  "name": "string | null",
  "website": "string | null",
  "stage": "one of: Pre-Revenue | Revenue | Commercial | Established | null",
  "commercial_launch_yr": "integer | null",
  "submarket": "string | null",
  "description": "string | null — 2-4 sentence product description",
  "tam_10y": "number in millions USD | null",
  "displaced_resource": "string — one of the 16 built-in types, or null",
  "volo_investment": "number in USD | null",
  "volo_pct": "number 0-1 | null",
  "funding_summary": "string | null",
  "recent_news": ["string — concise bullet per news item, up to 5"],
  "product_description": "string | null",
  "climate_claims": "string | null — specific CO2/GHG displacement or efficiency claims",
  "key_customers": "string | null",
  "competitive_advantages": "string | null",
  "team_highlights": "string | null",
  "revenue_signals": "string | null",
  "trl_estimate": "integer 1-9 | null — estimated Technology Readiness Level",
  "confidence": {
    "overall": "float 0-1 overall enrichment confidence",
    "fields": "dict mapping field names to float 0-1 confidence scores"
  },
  "notes": "string | null"
}"""


# ── Claude API callers (enrichment only) ─────────────────────────────────────

_ENRICH_ENERGY_CONTEXT = """
DOMAIN-SPECIFIC EXTRACTION (Energy / Industrial Tech):
If this company operates in the energy/industrial sector, also extract:
- trl_estimate: Technology Readiness Level 1-9 based on:
  * TRL 1-3: Basic research / lab proof
  * TRL 4-5: Validated in lab/relevant environment
  * TRL 6-7: Demonstrated / prototype
  * TRL 8-9: Qualified system / operational
- LCOE claims ($/MWh or equivalent cost metrics)
- Capacity factor claims (%)
- Specific CO2/GHG displacement or efficiency claims
- Deployment/capacity data (MW, GWh, tonnes, units installed)
- Resource type being displaced (fossil fuel, grid electricity, etc.)
Map these into the appropriate schema fields.
"""


def _call_claude_enrich(text: str, company_name: str, website_url: str, news_pages: list) -> dict:
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY not set")

    news_summary = (
        f"News/blog pages scraped: {', '.join(news_pages)}" if news_pages
        else "No news pages were found."
    )

    prompt = f"""You are a climate-tech investment analyst at VoLo Earth, a venture fund.
You have scraped the following web content for the company "{company_name}".
Website: {website_url or "(unknown)"}
{news_summary}

--- WEB CONTENT START ---
{text[:65000]}
--- WEB CONTENT END ---

Using ONLY information found in the web content above, extract the fields below.
Return EXACTLY one JSON object matching this schema (no extra keys, no markdown fences):
{_ENRICH_SCHEMA}

For displaced_resource, you MUST use exactly one of:
{_ENRICH_RESOURCES}
Or null if unclear.

{_ENRICH_ENERGY_CONTEXT}

IMPORTANT:
- For confidence scores, rate 0.0 (no evidence found) to 1.0 (explicitly stated in text)
- For trl_estimate, use the standard 1-9 TRL scale based on product maturity signals
- For recent_news, pull concise bullet-point summaries of actual news items found
- Provide rich detail in all fields where evidence exists
- Include data_freshness: estimate how recent the scraped content is based on dates found
"""

    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = msg.content[0].text.strip()
    raw = re.sub(r"^```[a-z]*\n?", "", raw).rstrip("`").strip()
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            result = json.loads(m.group())
        else:
            raise ValueError("Could not parse JSON from AI enrichment response")

    result = _auto_map_to_rvm(result)
    return result


def _auto_map_to_rvm(enrichment: dict) -> dict:
    """
    Auto-suggest RVM carbon model inputs based on enrichment results:
    - Map displaced_resource to the closest built-in CI resource
    - Pre-fill volume forecast from deployment/capacity data
    - Map TAM figures
    """
    suggestions = {}

    rvm_resource_map = {
        "Electricity": "US electricity",
        "Natural Gas": "Natural Gas",
        "Diesel": "Diesel",
        "Gasoline": "Gasoline",
        "Coal": "US electricity",
        "Steam": "Natural Gas (CCGT)",
        "Hydrogen": "Natural Gas",
        "Steel": "Limestone",
        "Cement": "Limestone calcination",
        "Aluminum": "Nickel",
    }

    displaced = enrichment.get("displaced_resource")
    if displaced and displaced in rvm_resource_map:
        suggestions["rvm_displaced_resource"] = rvm_resource_map[displaced]

    tam = enrichment.get("tam_10y")
    if tam is not None and isinstance(tam, (int, float)):
        suggestions["rvm_tam_10y"] = tam

    if enrichment.get("climate_claims"):
        suggestions["rvm_climate_notes"] = enrichment["climate_claims"]

    trl = enrichment.get("trl_estimate")
    if trl is not None:
        if trl <= 3:
            suggestions["rvm_suggested_risk_divisor"] = 6
        elif trl <= 6:
            suggestions["rvm_suggested_risk_divisor"] = 3
        else:
            suggestions["rvm_suggested_risk_divisor"] = 1

    if suggestions:
        enrichment["_rvm_suggestions"] = suggestions

    return enrichment


# ── Web scraping helpers ──────────────────────────────────────────────────────

def _find_company_website(company_name: str):
    import requests as _req
    headers = {"User-Agent": "Mozilla/5.0 (compatible; VoLo-bot/1.0)"}

    try:
        r = _req.get(
            "https://api.duckduckgo.com/",
            params={"q": company_name, "format": "json", "no_redirect": 1},
            headers=headers, timeout=8,
        )
        data = r.json()
        url = data.get("AbstractURL") or data.get("Redirect")
        if url and url.startswith("http"):
            return url
    except Exception:
        logger.debug("DuckDuckGo API lookup failed for '%s'", company_name, exc_info=True)

    try:
        from bs4 import BeautifulSoup
        import urllib.parse as _up
        r = _req.get(
            "https://html.duckduckgo.com/html/",
            params={"q": f"{company_name} official website"},
            headers=headers, timeout=10,
        )
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.select("a.result__url"):
            href = a.get("href", "")
            qs = _up.parse_qs(_up.urlparse(href).query)
            if "uddg" in qs:
                return _up.unquote(qs["uddg"][0])
        for a in soup.select("a.result__a"):
            href = a.get("href", "")
            if href.startswith("http"):
                return href
    except Exception:
        logger.debug("DuckDuckGo HTML lookup failed for '%s'", company_name, exc_info=True)

    return None


_scrape_cache: dict = {}
_CACHE_TTL_SEC = 600


def _deep_scrape(base_url: str, max_news: int = 5):
    import requests as _req
    from bs4 import BeautifulSoup
    import urllib.parse as _up
    import time

    cache_key = base_url
    if cache_key in _scrape_cache:
        entry = _scrape_cache[cache_key]
        if time.time() - entry["ts"] < _CACHE_TTL_SEC:
            return entry["text"], entry["news"]

    NEWS_KW = {
        "news", "press", "blog", "media", "update", "announcement",
        "release", "insight", "story", "article", "post",
    }
    ABOUT_KW = {"about", "team", "technology", "product", "solution", "how-it-works", "our-story"}
    headers = {"User-Agent": "Mozilla/5.0 (compatible; VoLo-bot/1.0)"}

    def _fetch_with_retry(url, max_retries=3):
        """Fetch URL with exponential backoff retry."""
        for attempt in range(max_retries + 1):
            try:
                r = _req.get(url, timeout=15, headers=headers)
                r.raise_for_status()
                soup = BeautifulSoup(r.text, "html.parser")
                for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
                    tag.decompose()
                return soup, soup.get_text(separator="\n", strip=True)
            except _req.exceptions.HTTPError as e:
                if e.response is not None and e.response.status_code == 403:
                    return None, ""
                if attempt < max_retries:
                    wait = (2 ** attempt) * 0.5
                    time.sleep(wait)
                else:
                    return None, ""
            except _req.exceptions.ConnectionError:
                if attempt < max_retries:
                    time.sleep(2 ** attempt)
                else:
                    return None, ""
            except Exception:
                if attempt < max_retries:
                    time.sleep(1 * (attempt + 1))
                else:
                    return None, ""

    parsed_base = _up.urlparse(base_url)
    base_domain = parsed_base.netloc

    main_soup, main_text = _fetch_with_retry(base_url)
    all_text_parts = [f"=== MAIN PAGE: {base_url} ===\n{main_text}"]
    news_urls = []
    about_urls = []

    if main_soup:
        for a in main_soup.find_all("a", href=True):
            href = _up.urljoin(base_url, a["href"])
            parsed = _up.urlparse(href)
            if parsed.netloc != base_domain:
                continue
            path_lower = parsed.path.lower()
            if any(kw in path_lower for kw in NEWS_KW):
                if href not in news_urls and href != base_url:
                    news_urls.append(href)
            if any(kw in path_lower for kw in ABOUT_KW):
                if href not in about_urls and href != base_url:
                    about_urls.append(href)
            if len(news_urls) >= max_news * 3:
                break

    for aurl in about_urls[:3]:
        time.sleep(0.3)
        _, atext = _fetch_with_retry(aurl)
        if atext.strip():
            all_text_parts.append(f"\n=== ABOUT/TECH: {aurl} ===\n{atext}")

    scraped_news = []
    for nurl in news_urls[:max_news * 3]:
        if len(scraped_news) >= max_news:
            break
        time.sleep(0.4)
        _, ntext = _fetch_with_retry(nurl)
        if ntext.strip():
            all_text_parts.append(f"\n=== NEWS/BLOG: {nurl} ===\n{ntext}")
            scraped_news.append(nurl)

    combined = "\n".join(all_text_parts)[:80000]

    _scrape_cache[cache_key] = {"text": combined, "news": scraped_news, "ts": time.time()}

    return combined, scraped_news


# ── Routes: Web enrichment ────────────────────────────────────────────────────

@router.post("/api/companies/{cid}/enrich")
def enrich_company(cid: int, data: dict = None, user: CurrentUser = Depends(get_current_user)):
    if data is None:
        data = {}
    db = get_db()
    try:
        row = db.execute("SELECT id, owner_id, name FROM companies WHERE id=?", (cid,)).fetchone()
        if not row:
            raise HTTPException(404, "Company not found")
        if row["owner_id"] != user.id and user.role != "admin":
            raise HTTPException(403, "Forbidden")

        company_name = row["name"]
        url = data.get("url", "").strip() or None

        if not url:
            url = _find_company_website(company_name)
        if not url:
            raise HTTPException(422, f"Could not find a website for '{company_name}'. Please provide a URL.")

        text, news_pages = _deep_scrape(url)
        if not text.strip():
            raise HTTPException(422, f"Could not scrape content from {url}")

        result = _call_claude_enrich(text, company_name, url, news_pages)
        result["_source_url"] = url
        result["_news_pages"] = news_pages
        return result

    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"Enrichment failed: {exc}")
    finally:
        db.close()


# ── Routes: Financial model extraction pipeline ───────────────────────────────

@router.post("/api/companies/{cid}/extract-financial-model")
async def extract_financial_model(
    cid: int,
    file: UploadFile = File(...),
    fy_end_month: int = Form(12),
    user: CurrentUser = Depends(get_current_user),
):
    db = get_db()
    try:
        row = db.execute("SELECT id, owner_id, name FROM companies WHERE id=?", (cid,)).fetchone()
        if not row:
            raise HTTPException(404, "Company not found")
        if row["owner_id"] != user.id and user.role != "admin":
            raise HTTPException(403, "Forbidden")
    finally:
        db.close()

    import os as _os
    ext = _os.path.splitext(file.filename)[1].lower()
    if ext not in (".xlsx", ".xlsm", ".xls", ".csv"):
        raise HTTPException(400, f"Unsupported file type: {ext}")

    company_name = row["name"]
    upload_dir = _os.path.join(tempfile.gettempdir(), f"fm_upload_{cid}_{secrets.token_hex(4)}")
    _os.makedirs(upload_dir, exist_ok=True)
    input_path = _os.path.join(upload_dir, file.filename)

    content = await file.read()
    with open(input_path, "wb") as f:
        f.write(content)

    out_dir = _os.path.join(
        _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
        "data", "fm_output", f"{cid}_{secrets.token_hex(4)}"
    )
    _os.makedirs(out_dir, exist_ok=True)

    run_id = secrets.token_hex(8)
    if len(_pipeline_runs) >= _MAX_PIPELINE_RUNS:
        completed = [k for k, v in _pipeline_runs.items() if v["status"] != "running"]
        for k in completed[:len(completed) // 2]:
            del _pipeline_runs[k]
    _pipeline_runs[run_id] = {"status": "running", "company_id": cid, "result": None, "error": None}

    def run_in_background():
        try:
            from ..engine.financial_pipeline import run_pipeline
            result = run_pipeline(
                input_path=input_path,
                company_id=str(cid),
                fy_end_month=fy_end_month,
                out_dir=out_dir,
            )

            db2 = get_db()
            try:
                source_run = {
                    "run_id": run_id,
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "extractor_version": "financial-pipeline@2.0.0",
                    "mode": "EXTRACT",
                }
                company_meta = {"company_id": str(cid), "company_name": company_name}
                db2.execute(
                    """INSERT INTO financial_models
                       (company_id, file_name, source_run, company_meta, fiscal_calendar,
                        records, failures, events, raw_json)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (
                        cid, file.filename,
                        json.dumps(source_run),
                        json.dumps(company_meta),
                        json.dumps({"fy_end_month": fy_end_month}),
                        json.dumps(result.get("records", [])),
                        json.dumps(result.get("failures", [])),
                        json.dumps([]),
                        json.dumps({
                            "model_summary": result.get("model_summary", {}),
                            "sensitivity_results": result.get("sensitivity_results", []),
                            "evidence_paths": result.get("evidence_paths", []),
                            "financials": result.get("financials", {}),
                            "units": result.get("units", {}),
                            "fiscal_years": result.get("fiscal_years", []),
                            "output_dir": out_dir,
                        }),
                    ),
                )
                db2.commit()
            finally:
                db2.close()

            _pipeline_runs[run_id] = {
                "status": "completed",
                "company_id": cid,
                "result": {
                    "records_count": result.get("records_count", 0),
                    "failures_count": result.get("failures_count", 0),
                    "model_summary": result.get("model_summary", {}),
                    "financials": result.get("financials", {}),
                    "units": result.get("units", {}),
                    "fiscal_years": result.get("fiscal_years", []),
                    "evidence_paths": result.get("evidence_paths", []),
                    "output_dir": out_dir,
                },
                "error": None,
            }
        except Exception as exc:
            import traceback
            _pipeline_runs[run_id] = {
                "status": "failed",
                "company_id": cid,
                "result": None,
                "error": str(exc),
                "traceback": traceback.format_exc(),
            }

    thread = threading.Thread(target=run_in_background, daemon=True)
    thread.start()

    return {"run_id": run_id, "status": "running"}


@router.get("/api/pipeline-status/{run_id}")
def get_pipeline_status(run_id: str, user: CurrentUser = Depends(get_current_user)):
    run = _pipeline_runs.get(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    return run


# ── Financial model CRUD ──────────────────────────────────────────────────────

def _fm_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "company_id": row["company_id"],
        "file_name": row["file_name"],
        "source_run": json.loads(row["source_run"] or "{}"),
        "company_meta": json.loads(row["company_meta"] or "{}"),
        "fiscal_calendar": json.loads(row["fiscal_calendar"] or "{}"),
        "records": json.loads(row["records"] or "[]"),
        "failures": json.loads(row["failures"] or "[]"),
        "events": json.loads(row["events"] or "[]"),
        "raw_json": json.loads(row["raw_json"] or "{}"),
        "uploaded_at": row["uploaded_at"],
    }


@router.get("/api/companies/{cid}/financial-models")
def list_financial_models(cid: int, user: CurrentUser = Depends(get_current_user)):
    db = get_db()
    try:
        row = db.execute("SELECT id, owner_id FROM companies WHERE id=?", (cid,)).fetchone()
        if not row:
            raise HTTPException(404, "Company not found")
        if row["owner_id"] != user.id and user.role != "admin":
            raise HTTPException(403, "Forbidden")
        rows = db.execute(
            "SELECT * FROM financial_models WHERE company_id=? ORDER BY uploaded_at DESC",
            (cid,),
        ).fetchall()
        return [_fm_to_dict(r) for r in rows]
    finally:
        db.close()


@router.post("/api/companies/{cid}/financial-models")
def upload_financial_model(cid: int, data: dict, user: CurrentUser = Depends(get_current_user)):
    db = get_db()
    try:
        row = db.execute("SELECT id, owner_id FROM companies WHERE id=?", (cid,)).fetchone()
        if not row:
            raise HTTPException(404, "Company not found")
        if row["owner_id"] != user.id and user.role != "admin":
            raise HTTPException(403, "Forbidden")

        source_run = data.get("source_run", {})
        company_meta = data.get("company", {})
        fiscal_calendar = data.get("fiscal_calendar", {})
        records = data.get("records", [])
        failures = data.get("failures", [])
        events = data.get("events", [])
        file_name = source_run.get("run_id", "") or company_meta.get("company_id", "") or "uploaded_model"

        cur = db.execute(
            """INSERT INTO financial_models
               (company_id, file_name, source_run, company_meta, fiscal_calendar,
                records, failures, events, raw_json)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                cid, file_name,
                json.dumps(source_run),
                json.dumps(company_meta),
                json.dumps(fiscal_calendar),
                json.dumps(records),
                json.dumps(failures),
                json.dumps(events),
                json.dumps(data),
            ),
        )
        db.commit()
        fm = db.execute("SELECT * FROM financial_models WHERE id=?", (cur.lastrowid,)).fetchone()
        return _fm_to_dict(fm)
    finally:
        db.close()


@router.delete("/api/companies/{cid}/financial-models/{fid}")
def delete_financial_model(cid: int, fid: int, user: CurrentUser = Depends(get_current_user)):
    db = get_db()
    try:
        row = db.execute("SELECT id, owner_id FROM companies WHERE id=?", (cid,)).fetchone()
        if not row:
            raise HTTPException(404, "Company not found")
        if row["owner_id"] != user.id and user.role != "admin":
            raise HTTPException(403, "Forbidden")
        fm = db.execute(
            "SELECT id FROM financial_models WHERE id=? AND company_id=?", (fid, cid)
        ).fetchone()
        if not fm:
            raise HTTPException(404, "Financial model not found")
        db.execute("DELETE FROM financial_models WHERE id=?", (fid,))
        db.commit()
        return {"ok": True}
    finally:
        db.close()
