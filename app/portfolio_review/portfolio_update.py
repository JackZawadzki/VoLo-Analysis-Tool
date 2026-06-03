"""
Two-pass agentic company update.

Reads a company's documents from pr_documents (Drive files + Granola notes,
ingested by `ingest.py`) and produces a structured update that mirrors the
firm's existing portfolio-review format:
  • Traction  — commercial status, revenue, runway, milestones, what changed
  • Derisking — the 7 dimensions scored +1 / 0 / −1, with reasoning + evidence

Pass 1 (recon)   sees only document METADATA (title, type, date, snippet) and
                 picks the handful of documents worth reading in full, and flags
                 evidence gaps. Cheap.
Pass 2 (extract) reads the selected documents' full text and emits the
                 structured update, with every derisking score citing the
                 document ids that justify it.

Persists: pr_company_updates (the run + audit trail), pr_traction_snapshots,
and pr_derisking_scores (evaluator='llm'). The score itself is relative to the
firm's thesis, not absolute.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Optional

from .derisking import DIMENSIONS, DIMENSION_KEYS, score_company

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-4-6"

MAX_INVENTORY = 80          # docs shown to the recon pass
MAX_SELECTED = 14           # docs read in full by pass 2
MAX_CHARS_PER_DOC = 18_000
MAX_PASS2_CHARS = 160_000
SNIPPET_CHARS = 320


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
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in msg.content if hasattr(b, "text"))


def _parse_json(raw: str) -> dict:
    s = (raw or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```\s*$", "", s)
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", s, re.DOTALL)
        if m:
            return json.loads(m.group())
        raise ValueError(f"LLM did not return JSON: {s[:300]}")


# ── Prompts ───────────────────────────────────────────────────────────────────
_RECON_PROMPT = """You are triaging documents for a venture firm's portfolio review of {company}.
Below is an INVENTORY of the documents we hold (metadata + a short snippet only).

Pick the documents most worth reading in full to assess (a) current traction
(revenue, customers, runway, milestones) and (b) the 7 derisking dimensions:
{dims}.

Prefer the most recent board decks, investor updates, financial models, and
meeting notes. Return ONLY JSON:
{{
  "selected_doc_ids": [<up to {max_selected} integer ids, most useful first>],
  "rationale": {{ "<doc_id>": "<why, one short phrase>" }},
  "evidence_gaps": ["<dimensions or metrics with weak/no evidence>"]
}}

INVENTORY:
{inventory}
"""

_EXTRACT_PROMPT = """You are writing the portfolio-review update for {company} for a venture firm.
Use ONLY the documents below; each is tagged with [id]. Cite ids as evidence.

Return ONLY JSON with this exact shape:
{{
  "headline": "<=12 words: the single most important change since last review",
  "summary": "<2-4 sentences on overall traction and trajectory>",
  "traction": {{
    "commercial_status": "Pre-Rev" | "Pilot" | "Commercial" | "Hyperscale",
    "revenue_current": <USD number or null>,
    "revenue_prior": <USD number or null>,
    "revenue_period": "<e.g. 'FY2025 vs FY2024'>",
    "revenue_growth_pct": <decimal, 0.40 = 40%, or null>,
    "arr_current": <USD or null>,
    "customer_count": <int or null>,
    "runway_months": <number or null>,
    "fundraising_status": "<e.g. 'Raising Series B' or '' if none>",
    "notable_milestones": "<1-3 sentences>",
    "change_vs_baseline": "<1-2 sentences on what changed since investment>",
    "confidence": "low" | "medium" | "high"
  }},
  "derisking": {{
    "<dimension_key>": {{ "score": -1 | 0 | 1, "reasoning": "<one sentence>", "evidence": [<doc ids>] }}
  }}
}}

Derisking dimensions (use these exact keys), score +1 = substantially derisked
for the company's stage, -1 = remains a major risk, 0 = neutral/partial:
{dims_detail}

DOCUMENTS:
{documents}
"""


# ── Data loading ──────────────────────────────────────────────────────────────
def _load_docs(conn, company_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT id, source, title, doc_type, occurred_at, body_text "
        "FROM pr_documents WHERE company_id=? ORDER BY COALESCE(occurred_at,'') DESC, id DESC",
        (company_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _inventory_block(docs: list[dict]) -> str:
    lines = []
    for d in docs[:MAX_INVENTORY]:
        snippet = re.sub(r"\s+", " ", (d.get("body_text") or "")[:SNIPPET_CHARS]).strip()
        lines.append(
            f"[{d['id']}] ({d.get('doc_type') or 'other'} · {d.get('source')} · "
            f"{d.get('occurred_at') or 'n.d.'}) {d.get('title') or 'Untitled'} — {snippet}")
    return "\n".join(lines)


def _documents_block(docs_by_id: dict[int, dict], selected: list[int]) -> tuple[str, list[int]]:
    blocks, used, total = [], [], 0
    for did in selected:
        d = docs_by_id.get(did)
        if not d:
            continue
        text = (d.get("body_text") or "")[:MAX_CHARS_PER_DOC]
        if total + len(text) > MAX_PASS2_CHARS:
            break
        blocks.append(
            f"--- [{did}] {d.get('title') or 'Untitled'} "
            f"({d.get('doc_type') or 'other'} · {d.get('occurred_at') or 'n.d.'}) ---\n{text}")
        used.append(did)
        total += len(text)
    return "\n\n".join(blocks), used


# ── Public entry point ────────────────────────────────────────────────────────
def generate_update(conn, company_id: int, *, period: str = "2026 LLM",
                    model: str = DEFAULT_MODEL) -> dict:
    company = conn.execute("SELECT id, name, fund FROM pr_companies WHERE id=?",
                           (company_id,)).fetchone()
    if not company:
        raise ValueError(f"company {company_id} not found")
    cname, cfund = company["name"], (company["fund"] or "Fund I")

    docs = _load_docs(conn, company_id)
    if not docs:
        raise ValueError(f"No documents ingested for {cname}. Run ingestion first.")
    docs_by_id = {d["id"]: d for d in docs}

    client = _client()
    dims_simple = ", ".join(lbl for _, lbl in DIMENSIONS)
    dims_detail = "\n".join(f"  - {k}: {lbl}" for k, lbl in DIMENSIONS)

    # Pass 1 — recon
    recon_raw = _call(client, model, _RECON_PROMPT.format(
        company=cname, dims=dims_simple, max_selected=MAX_SELECTED,
        inventory=_inventory_block(docs)), max_tokens=1200)
    try:
        recon = _parse_json(recon_raw)
    except Exception:
        recon = {}
    selected = [int(x) for x in (recon.get("selected_doc_ids") or []) if str(x).isdigit()]
    if len(selected) < 3:                       # recon failed/too sparse → use newest docs
        selected = [d["id"] for d in docs[:MAX_SELECTED]]

    # Pass 2 — extract
    doc_block, used_ids = _documents_block(docs_by_id, selected[:MAX_SELECTED])
    extract_raw = _call(client, model, _EXTRACT_PROMPT.format(
        company=cname, dims_detail=dims_detail, documents=doc_block), max_tokens=3000)
    data = _parse_json(extract_raw)

    traction = data.get("traction", {}) or {}
    derisk = data.get("derisking", {}) or {}
    scores = {k: (derisk.get(k, {}) or {}).get("score") for k in DIMENSION_KEYS}
    res = score_company({k: (0 if v is None else v) for k, v in scores.items()})

    # ── Persist ──────────────────────────────────────────────────────────────
    # traction snapshot
    src_files = [{"id": did, "title": docs_by_id[did]["title"],
                  "source": docs_by_id[did]["source"]} for did in used_ids if did in docs_by_id]
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

    # derisking score (replace prior LLM row for this period)
    reasoning = {k: {"score": (derisk.get(k, {}) or {}).get("score"),
                     "reasoning": (derisk.get(k, {}) or {}).get("reasoning", ""),
                     "evidence": (derisk.get(k, {}) or {}).get("evidence", [])}
                 for k in DIMENSION_KEYS}
    evidence_summary = "; ".join(
        f"{lbl}: {reasoning[k]['reasoning']}" for k, lbl in DIMENSIONS if reasoning[k]["reasoning"])
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
         json.dumps(reasoning), evidence_summary[:2000], json.dumps(recon)))

    # the update wrapper (audit trail)
    cur = conn.execute(
        """INSERT INTO pr_company_updates
        (company_id, period, status, headline, summary, model_used, n_docs_seen,
         n_docs_used, manifest_json, evidence_json)
        VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (company_id, period, "success", data.get("headline", ""), data.get("summary", ""),
         model, len(docs), len(used_ids), json.dumps(recon),
         json.dumps({k: reasoning[k]["evidence"] for k in DIMENSION_KEYS})))
    conn.commit()

    return {
        "update_id": cur.lastrowid, "company_id": company_id, "company_name": cname,
        "headline": data.get("headline", ""), "summary": data.get("summary", ""),
        "n_docs_seen": len(docs), "n_docs_used": len(used_ids),
        "evidence_gaps": recon.get("evidence_gaps", []),
        "derisking_total": res["total_score"], "quartile": res["quartile"],
        "commercial_status": traction.get("commercial_status"),
        "model": model,
    }
