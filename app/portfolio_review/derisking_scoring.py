"""
LLM-driven derisking scorer.

For one company, this:
  1. Pulls the IC memo / diligence materials from the linked Drive `diligence`
     folder — these define the *original thesis* and the risks that existed
     at investment time.
  2. Pulls the most recent board deck + 1-2 investor updates from the
     `current` (or `board_pack`) Drive folder — current state of the business.
  3. Pulls the most recent 5-8 Granola meeting notes attached to the company
     (Investment Committee / Portco Updates / Screening) — qualitative
     discussion not captured in formal materials.
  4. Pulls the most recent operator-imported `human` derisking score (if any)
     as a reference baseline.
  5. Sends everything to Claude with a structured prompt asking for +1/0/-1
     on each of the 7 dimensions, with reasoning and concrete evidence
     citations per dimension.
  6. Persists a new pr_derisking_scores row with evaluator='llm' and a
     period string suffixed " LLM" so it sits alongside human scores.

The scoring rubric is deliberately COMPARATIVE — Claude is told the IC memo
is the baseline and the board decks/notes are the current state, and asked
"has this dimension materially derisked since IC?" That framing matches how
the partners actually use the workbook: the +1/0/-1 isn't an absolute, it's
a delta against the original concern.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from typing import Any, Optional

from .derisking import DIMENSIONS, DIMENSION_KEYS, score_company
from .drive_scan import (
    _list_files_recursive,
    _download_and_extract_text,
    _classify_file,
)

logger = logging.getLogger(__name__)


# How much text per source to include. The total prompt budget is ~150K
# chars (~40K tokens) — we leave headroom for the system prompt and JSON
# output. The IC memo is the most information-dense source so it gets the
# largest budget.
MAX_CHARS_IC_MEMO       = 80_000   # ~30 pages of memo text
MAX_CHARS_BOARD_DECK    = 30_000   # 1-2 most recent board decks combined
MAX_CHARS_INVESTOR      = 20_000   # 1-2 recent investor updates
MAX_CHARS_GRANOLA       = 25_000   # last 5-8 meeting notes combined
MAX_GRANOLA_NOTES       = 8

# Filename patterns that indicate "this is an IC / DD memo" rather than
# a generic diligence reference doc. We pick these PREFERENTIALLY from the
# diligence folder so the LLM sees the actual investment thesis, not a
# random pitch deck.
IC_MEMO_PATTERNS = re.compile(
    r"\b(ic\b|investment\s*committee|investment\s*memo|"
    r"dd\s*memo|diligence\s*memo|"
    r"investment\s*recommendation|term\s*sheet\s*memo)\b",
    re.I,
)


def _pick_ic_memo_files(files: list[dict]) -> list[dict]:
    """From a diligence-folder file listing, pick the files that look like
    actual IC memos. Falls back to any DOCX/PDF/GDOC if no name matches."""
    by_pattern = [f for f in files if IC_MEMO_PATTERNS.search(f.get("name", ""))]
    if by_pattern:
        return sorted(by_pattern, key=lambda f: f.get("modifiedTime", ""), reverse=True)[:2]

    # Fallback: text-bearing docs only (skip spreadsheets and images), most recent first
    candidates = [
        f for f in files
        if f.get("mimeType") in (
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.google-apps.document",
        )
    ]
    return sorted(candidates, key=lambda f: f.get("modifiedTime", ""), reverse=True)[:1]


def _pick_current_state_files(files: list[dict]) -> list[dict]:
    """From a current/board_pack folder listing, pick the most recent
    deck and 1-2 investor updates. Reuses drive_scan's classifier."""
    classified: dict[str, list[dict]] = {"deck": [], "board": [], "investor": []}
    for f in files:
        kind = _classify_file(f)
        if kind in classified:
            classified[kind].append(f)
    out: list[dict] = []
    # Prefer board updates over generic decks (they're closer to derisking signal)
    for kind, n in (("board", 2), ("deck", 1), ("investor", 2)):
        ranked = sorted(classified[kind], key=lambda f: f.get("modifiedTime", ""), reverse=True)
        out.extend(ranked[:n])
    return out


def _collect_drive_evidence(conn, service, company_id: int) -> tuple[str, str, list[dict]]:
    """Download + extract text from each company's diligence and current
    folders. Returns (ic_memo_block, current_state_block, source_files_meta).

    Each block is a single string ready to drop into the prompt; the meta
    list is for audit (saved into pr_derisking_scores.source_files).
    """
    folders = conn.execute(
        "SELECT * FROM pr_company_folders WHERE company_id=?", (company_id,)
    ).fetchall()
    if not folders:
        return "", "", []

    ic_memo_blocks: list[str] = []
    current_blocks: list[str] = []
    source_files: list[dict] = []
    ic_chars_used = 0
    current_chars_used = 0

    for folder in folders:
        ftype = folder["folder_type"]
        try:
            files = _list_files_recursive(service, folder["drive_folder_id"])
        except Exception as e:
            logger.warning(f"Drive list failed for folder {folder['drive_folder_name']}: {e}")
            continue

        if ftype == "diligence":
            picked = _pick_ic_memo_files(files)
            char_budget_per_file = MAX_CHARS_IC_MEMO
            target_block = ic_memo_blocks
            char_tracker = "ic"
        elif ftype in ("current", "board_pack"):
            picked = _pick_current_state_files(files)
            char_budget_per_file = MAX_CHARS_BOARD_DECK
            target_block = current_blocks
            char_tracker = "current"
        else:
            continue

        for f in picked:
            text = _download_and_extract_text(service, f)
            if not text:
                continue
            text = text[:char_budget_per_file]

            # Enforce overall budget per source family
            if char_tracker == "ic":
                if ic_chars_used + len(text) > MAX_CHARS_IC_MEMO:
                    text = text[: max(0, MAX_CHARS_IC_MEMO - ic_chars_used)]
                    if not text:
                        break
                ic_chars_used += len(text)
            else:
                if current_chars_used + len(text) > MAX_CHARS_BOARD_DECK + MAX_CHARS_INVESTOR:
                    text = text[: max(0, MAX_CHARS_BOARD_DECK + MAX_CHARS_INVESTOR - current_chars_used)]
                    if not text:
                        break
                current_chars_used += len(text)

            tag = f.get("modifiedTime", "?")
            target_block.append(f"--- {f['name']} (modified {tag}) ---\n{text}")
            source_files.append({
                "name": f["name"],
                "folder_type": ftype,
                "modified": f.get("modifiedTime"),
                "char_count": len(text),
            })

    return "\n\n".join(ic_memo_blocks), "\n\n".join(current_blocks), source_files


def _collect_granola_evidence(conn, company_id: int) -> tuple[str, list[dict]]:
    """Pull the most recent Granola notes attached to this company.
    Returns (notes_block, audit_meta). Notes are tagged with date + match
    method so the LLM knows which are confirmed (CEO/CFO attended) vs.
    matched only by title."""
    rows = conn.execute(
        """SELECT note_title, note_summary, note_url, attendees_json,
                  note_created_at, note_updated_at, match_method, match_confidence
             FROM pr_granola_notes
            WHERE company_id=?
         ORDER BY COALESCE(note_updated_at, note_created_at, fetched_at) DESC
            LIMIT ?""",
        (company_id, MAX_GRANOLA_NOTES),
    ).fetchall()
    if not rows:
        return "", []

    blocks: list[str] = []
    meta: list[dict] = []
    chars_used = 0
    for r in rows:
        d = dict(r) if hasattr(r, "keys") else {}
        title = d.get("note_title", "") or "(untitled)"
        date = d.get("note_updated_at") or d.get("note_created_at") or ""
        summary = d.get("note_summary", "") or ""
        method = d.get("match_method", "manual")
        conf = d.get("match_confidence", "medium")
        try:
            attendees = json.loads(d.get("attendees_json") or "[]")
            att_str = ", ".join(
                a.get("name") or a.get("email") or "" for a in attendees if isinstance(a, dict)
            )[:200]
        except Exception:
            att_str = ""

        block = (
            f"--- [{date[:10]}] {title} "
            f"(match: {method}/{conf}; attendees: {att_str}) ---\n"
            f"{summary}"
        )
        if chars_used + len(block) > MAX_CHARS_GRANOLA:
            break
        blocks.append(block)
        chars_used += len(block)
        meta.append({"title": title, "date": date, "match": method, "confidence": conf})

    return "\n\n".join(blocks), meta


def _fetch_prior_human_score(conn, company_id: int) -> Optional[dict]:
    """Get the most recent operator-imported (human) derisking score for
    this company, if any. Used as an anchor in the prompt: 'partners
    scored this 2 in 2024 — is that still right?'"""
    row = conn.execute(
        """SELECT period, fund, rapid_innovation_adopt, business_model, technology,
                  incentive_management, team, product_growth, ip_and_data,
                  is_exited, total_score, quartile, scored_at
             FROM pr_derisking_scores
            WHERE company_id=? AND COALESCE(evaluator,'human')='human'
         ORDER BY scored_at DESC
            LIMIT 1""",
        (company_id,),
    ).fetchone()
    if not row:
        return None
    return dict(row) if hasattr(row, "keys") else None


# ── Prompt construction ─────────────────────────────────────────────────────
DIMENSION_GUIDE = """\
1. **Rapid innovation and adoption** — Has the underlying market adoption
   curve accelerated or decelerated since IC? Examples of derisking:
   tailwinds from policy/customers, faster-than-expected pilot conversion,
   inbound demand. Examples of risk: incumbent moves, slower adoption,
   policy reversal.

2. **Business model** — Are the unit economics, pricing, and go-to-market
   proving out? Examples of derisking: signed contracts at target margins,
   repeat revenue, lower CAC than modeled. Examples of risk: discounting
   pressure, longer sales cycles than planned, GM compression.

3. **Technology** — Has technical risk reduced through deployments, pilots,
   TRL advancement, certifications? Examples of derisking: completed pilots,
   third-party validation, scaling milestones hit. Examples of risk:
   schedule slips on key tech milestones, performance below spec.

4. **Incentive management** — Stability of government incentives, regulatory
   environment, and tax credit exposure. Examples of derisking: incentives
   secured / locked in / safe-harbored. Examples of risk: pending policy
   changes, reliance on uncertain credits, IRA/IIJA exposure.

5. **Team** — Strengthening or weakening of leadership and key hires.
   Examples of derisking: key technical / commercial hires, founder
   alignment, board strength. Examples of risk: founder departure,
   commercial leadership gap, equity / retention issues.

6. **Product and growth** — Customer traction, revenue growth, retention.
   Examples of derisking: new logos, expansion within accounts, ARR growth,
   strong NRR. Examples of risk: stalled growth, churn, missed plan.

7. **IP and Data** — Defensibility through patents, trade secrets, data
   moats, regulatory barriers. Examples of derisking: granted patents,
   accumulated proprietary data, regulatory approvals others lack.
   Examples of risk: competitor IP filings, eroding data advantage,
   discovery of prior art.
"""


SCORING_RUBRIC = """\
+1 — MATERIALLY DERISKED since IC. Concrete evidence of progress against
     the original concern (e.g. customer signed, pilot completed, key
     hire announced, regulation locked in). The original IC concern is
     substantially answered.

 0 — NO MATERIAL CHANGE, or mixed signals where derisking and new risk
     roughly cancel. Use this as the default unless you have evidence
     either way.

-1 — STILL AT RISK or a NEW RISK has emerged. Original concerns persist
     unaddressed, or material new concerns surfaced (founder departure,
     missed milestone, customer churn, policy change against them).

null — Cannot be assessed from the materials provided. Use this rather
       than guessing; it tells the operator to gather more information.
"""


def _build_prompt(
    company_name: str,
    period_label: str,
    ic_memo_block: str,
    current_state_block: str,
    granola_block: str,
    prior_human_score: Optional[dict],
) -> str:
    """Compose the user prompt for Claude."""

    prior_block = ""
    if prior_human_score:
        # Render the prior human-imported score as a reference, not a constraint
        score_lines = []
        for k, label in DIMENSIONS:
            v = prior_human_score.get(k)
            score_lines.append(f"  • {label}: {v if v is not None else 'unscored'}")
        prior_block = (
            f"\n[OPERATOR'S PRIOR SCORE — "
            f"{prior_human_score.get('period', 'unknown period')}, "
            f"total {prior_human_score.get('total_score', '?')}/Q{prior_human_score.get('quartile', '?')}]\n"
            + "\n".join(score_lines)
            + "\n\nThis is the partners' last formal scoring — useful as a baseline. "
            "Your job is to assess whether each dimension has changed since this "
            "scoring (which itself was relative to the IC thesis).\n"
        )

    return f"""\
You are a senior partner at VoLo Earth Ventures performing the {period_label} \
derisking review for {company_name}.

The firm tracks 7 derisking dimensions. For each dimension, score whether \
the company has materially derisked since the original IC memo (the baseline \
thesis), based on the current state of the business.

DIMENSIONS:
{DIMENSION_GUIDE}

SCORING:
{SCORING_RUBRIC}

OUTPUT FORMAT — strict JSON, no markdown, no preamble:

{{
  "rapid_innovation_adopt":  {{"score": 1, "reasoning": "...", "evidence": ["..."], "confidence": "high|medium|low"}},
  "business_model":          {{"score": 0, "reasoning": "...", "evidence": ["..."], "confidence": "..."}},
  "technology":              {{"score": -1, "reasoning": "...", "evidence": ["..."], "confidence": "..."}},
  "incentive_management":    {{"score": null, "reasoning": "Not addressed in materials", "evidence": [], "confidence": "low"}},
  "team":                    {{"score": 1, "reasoning": "...", "evidence": ["..."], "confidence": "..."}},
  "product_growth":          {{"score": 0, "reasoning": "...", "evidence": ["..."], "confidence": "..."}},
  "ip_and_data":             {{"score": 1, "reasoning": "...", "evidence": ["..."], "confidence": "..."}},
  "evidence_summary":        "<2-3 sentence narrative of the most material changes since IC>",
  "is_exited":               false,
  "overall_confidence":      "high|medium|low"
}}

RULES:
- "evidence" must be 1-3 short concrete quotes / facts FROM the materials below.
  Don't fabricate quotes. If a dimension can't be assessed, score=null with
  evidence=[] and a brief reasoning.
- Default to score=0 unless you have specific evidence either way.
- Score relative to the IC thesis, not against an absolute standard.
- "overall_confidence" reflects how good the evidence base is overall — if
  you only have a stale board deck, call it "low".

═══════════════════════════════════════════════════════════════════════════
[IC MEMO / DILIGENCE MATERIALS — the baseline thesis, what we believed at investment]
═══════════════════════════════════════════════════════════════════════════
{ic_memo_block or '(No IC memo or diligence materials available — score conservatively.)'}

═══════════════════════════════════════════════════════════════════════════
[CURRENT STATE — recent board decks + investor updates]
═══════════════════════════════════════════════════════════════════════════
{current_state_block or '(No current-state materials available — score conservatively.)'}

═══════════════════════════════════════════════════════════════════════════
[GRANOLA MEETING NOTES — qualitative discussion from IC, portco update, and screening meetings]
═══════════════════════════════════════════════════════════════════════════
{granola_block or '(No meeting notes available.)'}
{prior_block}
═══════════════════════════════════════════════════════════════════════════

Output only the JSON object. Do not include any text before or after it.
"""


# ── LLM call + JSON parse ───────────────────────────────────────────────────
# Default model per provider. The frontend toggle sends `provider=anthropic`
# or `provider=refiant` and the endpoint resolves the actual model id from
# these defaults (or the operator can override the model explicitly).
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-6"
DEFAULT_REFIANT_MODEL   = "qwen3-coder-plus"  # Refiant's general-purpose QWEN tier


def resolve_model(provider: Optional[str], model: Optional[str]) -> tuple[str, bool]:
    """Resolve (model_id, is_refiant) from a provider toggle + optional override.

    Provider 'anthropic' (or unset) → Claude.  Provider 'refiant' → QWEN.
    An explicit model id always wins; the prefix decides the backend.
    """
    if model:
        return model, model.lower().startswith("qwen")
    p = (provider or "anthropic").lower()
    if p == "refiant":
        return DEFAULT_REFIANT_MODEL, True
    return DEFAULT_ANTHROPIC_MODEL, False


def _call_llm(prompt: str, *, model: str, is_refiant: bool) -> dict:
    """Send the prompt to the chosen LLM and parse the JSON response.

    Routes through `engine.llm_utils.make_llm_client` which exposes the
    same `.messages.create(...)` API for both Anthropic and Refiant/QWEN.
    Returns {data, raw_text, model}.
    """
    from ..engine.llm_utils import make_llm_client

    if is_refiant:
        api_key = os.environ.get("REFIANT_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("REFIANT_API_KEY not set — required when provider=refiant")
    else:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set — required when provider=anthropic")

    client = make_llm_client(is_refiant=is_refiant, api_key=api_key)
    resp = client.messages.create(
        model=model,
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text")

    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```\s*$", "", cleaned)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not m:
            raise ValueError(f"LLM response was not valid JSON: {cleaned[:500]}")
        data = json.loads(m.group())

    return {"data": data, "raw_text": raw, "model": model}


def _normalize_score(v: Any) -> Optional[float]:
    """Coerce any LLM-returned score to -1.0 / 0.0 / 1.0 / None."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f >= 0.5:
        return 1.0
    if f <= -0.5:
        return -1.0
    return 0.0


# ── Persistence ─────────────────────────────────────────────────────────────
def _persist_score(
    conn,
    *,
    company_id: int,
    period: str,
    fund: str,
    parsed: dict,
    raw_text: str,
    model: str,
    source_files: list[dict],
) -> dict:
    """Upsert one pr_derisking_scores row with evaluator='llm'.

    Returns the persisted row contents (matching what /derisking returns)."""
    # Pull dim scores out of the parsed payload, normalizing to -1/0/1/null
    scores: dict[str, Any] = {}
    reasoning: dict[str, Any] = {}
    for k, _ in DIMENSIONS:
        cell = parsed.get(k) or {}
        if isinstance(cell, dict):
            scores[k] = _normalize_score(cell.get("score"))
            reasoning[k] = {
                "score": scores[k],
                "reasoning": cell.get("reasoning", ""),
                "evidence": cell.get("evidence", []) or [],
                "confidence": cell.get("confidence", ""),
            }
        else:
            # Some old-style responses may return a bare number per key
            scores[k] = _normalize_score(cell)
            reasoning[k] = {"score": scores[k], "reasoning": "", "evidence": [], "confidence": ""}

    is_exited = bool(parsed.get("is_exited"))
    summary = score_company(scores, is_exited=is_exited)

    conn.execute(
        """INSERT INTO pr_derisking_scores
           (company_id, period, fund, rapid_innovation_adopt, business_model,
            technology, incentive_management, team, product_growth, ip_and_data,
            is_exited, total_score, quartile,
            evaluator, model_used, reasoning_json, evidence_summary,
            confidence, source_files, scored_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(company_id, period) DO UPDATE SET
          fund=excluded.fund,
          rapid_innovation_adopt=excluded.rapid_innovation_adopt,
          business_model=excluded.business_model,
          technology=excluded.technology,
          incentive_management=excluded.incentive_management,
          team=excluded.team,
          product_growth=excluded.product_growth,
          ip_and_data=excluded.ip_and_data,
          is_exited=excluded.is_exited,
          total_score=excluded.total_score,
          quartile=excluded.quartile,
          evaluator=excluded.evaluator,
          model_used=excluded.model_used,
          reasoning_json=excluded.reasoning_json,
          evidence_summary=excluded.evidence_summary,
          confidence=excluded.confidence,
          source_files=excluded.source_files,
          scored_at=datetime('now')""",
        (
            company_id, period, fund,
            scores["rapid_innovation_adopt"],
            scores["business_model"],
            scores["technology"],
            scores["incentive_management"],
            scores["team"],
            scores["product_growth"],
            scores["ip_and_data"],
            summary["is_exited"],
            summary["total_score"],
            summary["quartile"],
            "llm",
            model,
            json.dumps(reasoning),
            (parsed.get("evidence_summary") or "")[:2000],
            (parsed.get("overall_confidence") or "")[:20],
            json.dumps(source_files),
        ),
    )
    conn.commit()

    return {
        "company_id": company_id,
        "period": period,
        "evaluator": "llm",
        "scores": scores,
        "reasoning": reasoning,
        "total_score": summary["total_score"],
        "quartile": summary["quartile"],
        "is_exited": summary["is_exited"],
        "evidence_summary": parsed.get("evidence_summary", ""),
        "overall_confidence": parsed.get("overall_confidence", ""),
        "model_used": model,
        "source_files": source_files,
        "raw_text": raw_text,
    }


# ── Public entry point ──────────────────────────────────────────────────────
def score_company_with_llm(
    conn,
    *,
    company_id: int,
    user_id: int,
    period: str = "2025 LLM",
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> dict:
    """End-to-end: collect evidence → call the chosen LLM → persist → return result.

    `provider` is the operator-facing toggle ('anthropic' or 'refiant').
    `model` is an optional explicit override; if set, its prefix decides
    the backend ('qwen…' → Refiant, anything else → Anthropic).
    """
    from ..routes.drive import _get_drive_service

    resolved_model, is_refiant = resolve_model(provider, model)

    company = conn.execute(
        "SELECT id, name, fund FROM pr_companies WHERE id=?", (company_id,)
    ).fetchone()
    if not company:
        raise ValueError(f"Company {company_id} not found")
    company_name = company["name"]
    fund = company["fund"] or "Fund I"

    # 1. Drive evidence (IC memo + current-state materials)
    try:
        service = _get_drive_service(user_id)
        ic_block, current_block, drive_files = _collect_drive_evidence(conn, service, company_id)
    except Exception as e:
        # Drive is optional — we can still score from Granola alone, but the
        # operator should know the evidence base is degraded.
        logger.warning(f"Drive evidence collection failed for {company_name}: {e}")
        ic_block, current_block, drive_files = "", "", []

    # 2. Granola evidence
    granola_block, granola_meta = _collect_granola_evidence(conn, company_id)

    # 3. Prior human score as anchor
    prior_human = _fetch_prior_human_score(conn, company_id)

    # Refuse to score if we have NOTHING — better to fail loud than write
    # a hallucinated score row.
    if not (ic_block or current_block or granola_block):
        raise ValueError(
            f"No evidence available for {company_name}: "
            f"no Drive folders linked AND no Granola notes attached. "
            f"Run folder discovery + granola sync first, or import the operator workbook."
        )

    # 4. Build prompt + call the chosen LLM
    prompt = _build_prompt(
        company_name=company_name,
        period_label=period,
        ic_memo_block=ic_block,
        current_state_block=current_block,
        granola_block=granola_block,
        prior_human_score=prior_human,
    )
    resp = _call_llm(prompt, model=resolved_model, is_refiant=is_refiant)

    # 5. Persist
    source_files = drive_files + [{"source": "granola", **m} for m in granola_meta]
    result = _persist_score(
        conn,
        company_id=company_id,
        period=period,
        fund=fund,
        parsed=resp["data"],
        raw_text=resp["raw_text"],
        model=resp["model"],
        source_files=source_files,
    )
    result["company_name"] = company_name
    result["provider"] = "refiant" if is_refiant else "anthropic"
    result["evidence_base"] = {
        "drive_files": len(drive_files),
        "granola_notes": len(granola_meta),
        "had_ic_memo": bool(ic_block),
        "had_current_state": bool(current_block),
        "had_prior_human_score": prior_human is not None,
    }
    return result


# Re-exported for the route handler so it can show defaults in error messages
DEFAULT_MODEL = DEFAULT_ANTHROPIC_MODEL
