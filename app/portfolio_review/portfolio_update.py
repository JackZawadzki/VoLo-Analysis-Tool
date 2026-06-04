"""
Per-document extraction -> company synthesis (MAP then REDUCE).

Rationale: so nothing important is missed, every file and every note is read ON
ITS OWN (the "map" step) and turned into a compact structured "signal sheet" —
the metrics it reports, risks it flags, milestones, status, and per-dimension
derisking signals, each with a verbatim quote + date. Those per-document signals
are cached on the document (keyed by its content hash), so a re-sync only
re-reads files that are NEW or CHANGED — which is also the basis for the
"new since last review" flag.

The "reduce" step then synthesizes ALL of a company's signal sheets (compact, so
nothing is dropped to a token budget) into the company update: the latest
traction metrics, the 7 derisking dimension scores with evidence, a headline,
and a narrative. Because the reduce sees a digest of EVERY document, a number
that changed or a risk flagged in any single file flows through mechanically.

Models: a cheap/fast model maps each document; a stronger model does the
synthesis. Override via PORTFOLIO_MAP_MODEL / PORTFOLIO_REDUCE_MODEL.
"""

from __future__ import annotations

import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .derisking import DIMENSIONS, DIMENSION_KEYS, score_company

logger = logging.getLogger(__name__)

MAP_MODEL = os.environ.get("PORTFOLIO_MAP_MODEL", "claude-haiku-4-5-20251001")
REDUCE_MODEL = os.environ.get("PORTFOLIO_REDUCE_MODEL", "claude-sonnet-4-6")
FALLBACK_MODEL = "claude-sonnet-4-6"
DEFAULT_MODEL = REDUCE_MODEL  # back-compat: the route passes `model=` for reduce

MAX_DOCS = 150               # safety cap on docs processed per run
MAX_CHARS_PER_DOC = 24_000   # per-document text fed to the map step
MAX_MAP_WORKERS = 6          # parallel map calls
MAX_SIGNAL_CHARS = 240_000   # safety cap on the reduce input


# ── LLM plumbing ──────────────────────────────────────────────────────────────
def _client():
    from anthropic import Anthropic
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    return Anthropic(api_key=key)


def _call(client, model: str, prompt: str, max_tokens: int) -> str:
    msg = client.messages.create(
        model=model, max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}])
    return "".join(b.text for b in msg.content if hasattr(b, "text"))


def _call_fb(client, model: str, prompt: str, max_tokens: int) -> str:
    """Call `model`; on any error (e.g. model id unavailable) retry once with
    the known-good fallback model."""
    try:
        return _call(client, model, prompt, max_tokens)
    except Exception as e:
        if model != FALLBACK_MODEL:
            logger.warning("model %s failed (%s); falling back to %s", model, e, FALLBACK_MODEL)
            return _call(client, FALLBACK_MODEL, prompt, max_tokens)
        raise


def _parse_json(raw: str) -> Any:
    s = (raw or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```\s*$", "", s)
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        m = re.search(r"[\{\[].*[\}\]]", s, re.DOTALL)
        if m:
            return json.loads(m.group())
        raise ValueError(f"LLM did not return JSON: {s[:200]}")


# ── Prompts ───────────────────────────────────────────────────────────────────
_DIM_KEYS = ", ".join(DIMENSION_KEYS)

_MAP_PROMPT = """Extract structured signals from ONE document for {company}'s portfolio review.
Capture every concrete fact a VC would track. Do NOT infer beyond the text. Use null / [] when absent.
Return ONLY JSON:
{{
  "doc_date": "<the date this doc reflects (YYYY-MM or YYYY-MM-DD) or null>",
  "summary": "<1-2 sentences>",
  "metrics": [{{"name":"revenue|arr|runway_months|cash|customers|burn|growth_pct|headcount|other","label":"<as written>","value":<number or null>,"unit":"USD|months|count|pct|other","period":"<e.g. FY2025>","quote":"<verbatim>"}}],
  "status": {{"commercial_status":"Pre-Rev|Pilot|Commercial|Hyperscale or null","fundraising":"<e.g. 'Raising Series B' or null>"}},
  "milestones": [{{"text":"<what happened>","quote":"<verbatim>"}}],
  "risks": [{{"text":"<the risk>","severity":"low|medium|high","dimension":"<one of: {dims} or null>","quote":"<verbatim>"}}],
  "derisking_signals": [{{"dimension":"<one of: {dims}>","direction":"up|down|flat","reason":"<one phrase>","quote":"<verbatim>"}}]
}}

DOCUMENT [{doc_id}] "{title}" ({doc_type} - {source} - {date}):
{text}
"""

_REDUCE_PROMPT = """Write {company}'s portfolio-review update from the per-document signal sheets below.
Every file and note has ALREADY been read individually — the JSON is their digest, nothing was skipped.
Synthesize. Use the MOST RECENT value for each metric. Cite the [doc ids] that justify each derisking score.
Return ONLY JSON:
{{
  "headline": "<=12 words: the single most important change since last review",
  "summary": "<2-4 sentences on traction + trajectory>",
  "traction": {{
    "commercial_status": "Pre-Rev|Pilot|Commercial|Hyperscale",
    "revenue_current": <USD or null>, "revenue_prior": <USD or null>,
    "revenue_period": "<e.g. 'FY2025 vs FY2024'>", "revenue_growth_pct": <decimal, 0.4 = 40%, or null>,
    "arr_current": <USD or null>, "customer_count": <int or null>, "runway_months": <number or null>,
    "fundraising_status": "<e.g. 'Raising Series B' or ''>",
    "notable_milestones": "<1-3 sentences>", "change_vs_baseline": "<1-2 sentences vs investment>",
    "confidence": "low|medium|high"
  }},
  "derisking": {{ "<dimension_key>": {{"score": -1|0|1, "reasoning": "<one sentence>", "evidence": [<doc ids>]}} }},
  "evidence_gaps": ["<dimensions/metrics with weak or no evidence>"]
}}

Derisking dimensions (use these exact keys; +1 = substantially derisked for the company's stage, -1 = remains a major risk):
{dims_detail}

SIGNAL SHEETS (one per document):
{sheets}
"""


# ── MAP: per-document signal extraction (cached on the document) ──────────────
def _map_one(client, company_name: str, doc: dict) -> dict:
    text = (doc.get("body_text") or "")[:MAX_CHARS_PER_DOC]
    prompt = _MAP_PROMPT.format(
        company=company_name, dims=_DIM_KEYS, doc_id=doc["id"],
        title=doc.get("title") or "Untitled", doc_type=doc.get("doc_type") or "other",
        source=doc.get("source") or "drive", date=doc.get("occurred_at") or "n.d.", text=text)
    try:
        out = _parse_json(_call_fb(client, MAP_MODEL, prompt, 1500))
        return out if isinstance(out, dict) else {}
    except Exception as e:
        logger.warning("map failed for doc %s: %s", doc.get("id"), e)
        return {"summary": "", "metrics": [], "risks": [], "milestones": [],
                "derisking_signals": [], "status": {}, "_error": str(e)}


def ensure_extractions(conn, client, company_id: int, company_name: str) -> tuple[list[dict], int]:
    """Map every document that hasn't been mapped at its current content hash.
    Caches the result on pr_documents.extract_json. Returns (docs_with_extraction,
    n_newly_mapped). LLM calls run in parallel; DB writes stay on this thread."""
    rows = conn.execute(
        "SELECT id, source, title, doc_type, occurred_at, body_text, content_hash, "
        "extract_json, extracted_hash FROM pr_documents WHERE company_id=? "
        "ORDER BY COALESCE(occurred_at,'') DESC, id DESC LIMIT ?",
        (company_id, MAX_DOCS)).fetchall()
    docs = [dict(r) for r in rows]

    todo = [d for d in docs if not d.get("extract_json")
            or (d.get("extracted_hash") or "") != (d.get("content_hash") or "")]

    mapped: dict[int, dict] = {}
    if todo:
        workers = min(MAX_MAP_WORKERS, len(todo))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            results = list(ex.map(lambda dd: _map_one(client, company_name, dd), todo))
        for d, res in zip(todo, results):
            mapped[d["id"]] = res
            conn.execute(
                "UPDATE pr_documents SET extract_json=?, extracted_hash=?, extract_model=?, "
                "extracted_at=datetime('now') WHERE id=?",
                (json.dumps(res), d.get("content_hash") or "", MAP_MODEL, d["id"]))
        conn.commit()

    out = []
    for d in docs:
        if d["id"] in mapped:
            ext = mapped[d["id"]]
        else:
            try:
                ext = json.loads(d.get("extract_json") or "{}")
            except Exception:
                ext = {}
        out.append({"id": d["id"], "title": d.get("title"), "doc_type": d.get("doc_type"),
                    "source": d.get("source"), "date": d.get("occurred_at"), "extraction": ext})
    return out, len(todo)


# ── REDUCE: synthesize all signal sheets into the company update ──────────────
def reduce_company(client, company_name: str, docs_ext: list[dict], model: str) -> dict:
    sheets, total = [], 0
    for d in docs_ext:
        sheet = {"doc_id": d["id"], "title": d["title"], "date": d["date"],
                 "type": d["doc_type"], "source": d["source"], **(d["extraction"] or {})}
        blob = json.dumps(sheet, default=str)
        if total + len(blob) > MAX_SIGNAL_CHARS:
            break
        sheets.append(blob)
        total += len(blob)
    dims_detail = "\n".join(f"  - {k}: {lbl}" for k, lbl in DIMENSIONS)
    prompt = _REDUCE_PROMPT.format(
        company=company_name, dims_detail=dims_detail, sheets="[\n" + ",\n".join(sheets) + "\n]")
    out = _parse_json(_call_fb(client, model, prompt, 3000))
    return out if isinstance(out, dict) else {}


# ── Public entry point ────────────────────────────────────────────────────────
def generate_update(conn, company_id: int, *, period: str = "2026 LLM",
                    model: str = DEFAULT_MODEL) -> dict:
    company = conn.execute("SELECT id, name, fund FROM pr_companies WHERE id=?",
                           (company_id,)).fetchone()
    if not company:
        raise ValueError(f"company {company_id} not found")
    cname, cfund = company["name"], (company["fund"] or "Fund I")

    n_docs = conn.execute("SELECT COUNT(*) AS n FROM pr_documents WHERE company_id=?",
                          (company_id,)).fetchone()["n"]
    if not n_docs:
        raise ValueError(f"No documents synced for {cname}. Click 'Sync & update' to pull its Drive folder first.")

    client = _client()
    docs_ext, n_new = ensure_extractions(conn, client, company_id, cname)   # MAP
    data = reduce_company(client, cname, docs_ext, model)                    # REDUCE

    traction = data.get("traction", {}) or {}
    derisk = data.get("derisking", {}) or {}
    scores = {k: (derisk.get(k, {}) or {}).get("score") for k in DIMENSION_KEYS}
    res = score_company({k: (0 if v is None else v) for k, v in scores.items()})
    used_ids = [d["id"] for d in docs_ext]
    extract_raw = json.dumps(data)[:50_000]

    # ── Persist: traction snapshot ───────────────────────────────────────────
    src_files = [{"id": d["id"], "title": d["title"], "source": d["source"]} for d in docs_ext]
    conn.execute(
        """INSERT INTO pr_traction_snapshots
        (company_id, commercial_status, revenue_current, revenue_prior, revenue_period,
         revenue_growth_pct, arr_current, customer_count, runway_months,
         notable_milestones, summary, change_vs_baseline, fundraising_status,
         source_files, model_used, confidence, raw_response)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (company_id, traction.get("commercial_status") or "", traction.get("revenue_current"),
         traction.get("revenue_prior"), traction.get("revenue_period") or "",
         traction.get("revenue_growth_pct"), traction.get("arr_current"),
         traction.get("customer_count"), traction.get("runway_months"),
         traction.get("notable_milestones") or "", data.get("summary") or "",
         traction.get("change_vs_baseline") or "", traction.get("fundraising_status") or "",
         json.dumps(src_files), model, traction.get("confidence") or "medium", extract_raw))

    # ── Persist: derisking score (replace prior LLM row for this period) ──────
    reasoning = {k: {"score": (derisk.get(k, {}) or {}).get("score"),
                     "reasoning": (derisk.get(k, {}) or {}).get("reasoning", ""),
                     "evidence": (derisk.get(k, {}) or {}).get("evidence", [])}
                 for k in DIMENSION_KEYS}
    evidence_summary = "; ".join(
        f"{lbl}: {reasoning[k]['reasoning']}" for k, lbl in DIMENSIONS if reasoning[k]["reasoning"])
    manifest = {"evidence_gaps": data.get("evidence_gaps", []),
                "n_docs": len(docs_ext), "n_newly_mapped": n_new,
                "map_model": MAP_MODEL, "reduce_model": model}
    conn.execute("DELETE FROM pr_derisking_scores WHERE company_id=? AND period=?",
                 (company_id, period))
    conn.execute(
        f"""INSERT INTO pr_derisking_scores (company_id, period, fund,
            {', '.join(DIMENSION_KEYS)}, is_exited, total_score, quartile,
            evaluator, model_used, reasoning_json, evidence_summary, manifest_json)
            VALUES (?,?,?,{','.join(['?'] * 7)},?,?,?,?,?,?,?,?)""",
        (company_id, period, cfund,
         *[(0 if scores[k] is None else scores[k]) for k in DIMENSION_KEYS],
         0, res["total_score"], res["quartile"], "llm", model,
         json.dumps(reasoning), evidence_summary[:2000], json.dumps(manifest)))

    # ── Persist: the update wrapper (audit trail) ────────────────────────────
    cur = conn.execute(
        """INSERT INTO pr_company_updates
        (company_id, period, status, headline, summary, model_used, n_docs_seen,
         n_docs_used, manifest_json, evidence_json)
        VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (company_id, period, "success", data.get("headline", ""), data.get("summary", ""),
         f"{MAP_MODEL} -> {model}", len(docs_ext), len(used_ids), json.dumps(manifest),
         json.dumps({k: reasoning[k]["evidence"] for k in DIMENSION_KEYS})))
    conn.commit()

    return {
        "update_id": cur.lastrowid, "company_id": company_id, "company_name": cname,
        "headline": data.get("headline", ""), "summary": data.get("summary", ""),
        "n_docs": len(docs_ext), "n_newly_mapped": n_new,
        "evidence_gaps": data.get("evidence_gaps", []),
        "derisking_total": res["total_score"], "quartile": res["quartile"],
        "commercial_status": traction.get("commercial_status"),
        "map_model": MAP_MODEL, "reduce_model": model,
    }
