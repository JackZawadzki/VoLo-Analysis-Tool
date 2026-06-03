"""
Two-pass agentic derisking scorer.

The pipeline mirrors how a senior analyst actually works:

  PASS 1 — RECONNAISSANCE
    The LLM sees ONLY metadata: filenames, modification dates, mime types,
    folder types, and Granola note titles + attendees + brief summaries.
    NO full text yet. It then:
      • Identifies the IC / DD memo (the original investment thesis)
      • Maps every material to the derisking dimensions it bears on
      • Marks each as primary / reference / skip
      • Flags dimensions with no good evidence ("evidence_gaps")
    Output: a manifest. Cheap and fast.

  PASS 2 — SCORING
    For materials Pass 1 flagged as primary, we now download + extract
    full text. The LLM gets:
      • The IC memo (baseline thesis)
      • Per dimension: only the materials curated by Pass 1
      • The operator's most recent human-imported score (anchor)
    For each dimension it scores -1 / 0 / +1 / null with reasoning and
    evidence quotes that cite specific material IDs. Score is relative
    to the IC thesis, not absolute.

The manifest is persisted alongside the scores so the UI can show the
full audit trail: which files informed each dimension, what was skipped,
and the recon pass's per-dimension rationale.

If the LLM picked too few primaries (or recon failed entirely), we fall
back to "use everything" — the worst case is the previous monolithic
behavior, never silent score drift.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Optional

from .derisking import DIMENSIONS, DIMENSION_KEYS, score_company
from .drive_scan import (
    _list_files_recursive,
    _download_and_extract_text,
)

logger = logging.getLogger(__name__)


# ── Budget knobs ─────────────────────────────────────────────────────────────
# Pass 1 sees only metadata so we don't budget that — it's a tiny prompt.
# Pass 2 needs to fit IC memo + per-dimension primaries; budget guards
# against runaway token usage on companies with bloated DD folders.
MAX_INVENTORY_ITEMS         = 80      # cap files+notes seen by recon
MAX_CHARS_IC_MEMO           = 80_000
MAX_CHARS_PER_PRIMARY_FILE  = 25_000
MAX_CHARS_PER_GRANOLA_NOTE  = 4_000
MAX_GRANOLA_NOTES           = 12      # recon sees up to this many notes
MAX_PASS2_TOTAL_CHARS       = 180_000  # safety net, well under 200K context
# When recon picks too few primaries we fall back to "use everything"
MIN_PRIMARIES_FOR_RECON_USE = 3


# ── Material ID convention ────────────────────────────────────────────────────
# Stable string IDs the LLM can refer back to in evidence citations:
#   drive:<file_id>     for a Google Drive file
#   granola:<note_id>   for a Granola note
def _drive_id(file_id: str) -> str:
    return f"drive:{file_id}"


def _granola_id(note_id: str) -> str:
    return f"granola:{note_id}"


# ── Inventory: collect metadata only (no text yet) ───────────────────────────
def _list_drive_inventory(conn, service, company_id: int) -> list[dict]:
    """List metadata for every file in every linked Drive folder.

    Returns one dict per file with stable id, name, folder_type, modified
    date, mime type, and webViewLink (so the UI can deep-link to it later).
    No text extraction here — that happens after Pass 1 picks primaries.
    """
    folders = conn.execute(
        "SELECT * FROM pr_company_folders WHERE company_id=?", (company_id,)
    ).fetchall()
    out: list[dict] = []
    for folder in folders:
        try:
            files = _list_files_recursive(service, folder["drive_folder_id"])
        except Exception as e:
            logger.warning(f"Drive list failed for {folder['drive_folder_name']}: {e}")
            continue
        for f in files:
            out.append({
                "id": _drive_id(f["id"]),
                "source": "drive",
                "drive_file_id": f["id"],
                "name": f.get("name", "(unnamed)"),
                "folder_type": folder["folder_type"],
                "folder_name": folder["drive_folder_name"],
                "modified": f.get("modifiedTime"),
                "mime_type": f.get("mimeType"),
                "size": f.get("size"),
                "url": f.get("webViewLink") or _drive_view_url(f["id"]),
            })
    # Sort newest first so the LLM sees recent materials at the top
    out.sort(key=lambda m: m.get("modified") or "", reverse=True)
    return out


def _drive_view_url(file_id: str) -> str:
    """Best-effort web link to a Drive file. The official `webViewLink`
    field is the right answer when present; this fallback is what Drive
    serves for files lookup-able by id."""
    return f"https://drive.google.com/file/d/{file_id}/view"


def _list_granola_inventory(conn, company_id: int) -> list[dict]:
    """Metadata for the most-recent Granola notes attached to this company."""
    rows = conn.execute(
        """SELECT granola_note_id, note_title, note_summary, note_url,
                  attendees_json, note_created_at, note_updated_at,
                  match_method, match_confidence
             FROM pr_granola_notes
            WHERE company_id=?
         ORDER BY COALESCE(note_updated_at, note_created_at, fetched_at) DESC
            LIMIT ?""",
        (company_id, MAX_GRANOLA_NOTES),
    ).fetchall()
    out: list[dict] = []
    for r in rows:
        d = dict(r) if hasattr(r, "keys") else {}
        try:
            attendees = json.loads(d.get("attendees_json") or "[]")
            att_str = ", ".join(
                a.get("name") or a.get("email") or "" for a in attendees if isinstance(a, dict)
            )[:160]
        except Exception:
            att_str = ""
        out.append({
            "id": _granola_id(d["granola_note_id"]),
            "source": "granola",
            "granola_note_id": d["granola_note_id"],
            "name": d.get("note_title") or "(untitled)",
            "date": d.get("note_updated_at") or d.get("note_created_at"),
            "attendees": att_str,
            "match_method": d.get("match_method"),
            "match_confidence": d.get("match_confidence"),
            "url": d.get("note_url") or "",
            # Recon sees the summary preview, not the full note text
            "summary_preview": (d.get("note_summary") or "")[:600],
        })
    return out


# ── Pass 1 — Reconnaissance ──────────────────────────────────────────────────
def _build_recon_prompt(company: dict, inventory: list[dict]) -> str:
    """Compose the metadata-only recon prompt."""

    # Compact one-line-per-material rendering — the LLM doesn't need full
    # JSON, just enough to make a routing decision.
    lines: list[str] = []
    for m in inventory[:MAX_INVENTORY_ITEMS]:
        if m["source"] == "drive":
            lines.append(
                f"  [{m['id']}] {m['name']!r}  "
                f"folder={m['folder_type']}  modified={m.get('modified', '?')}  "
                f"mime={(m.get('mime_type') or '').split('/')[-1] or '?'}"
            )
        else:
            lines.append(
                f"  [{m['id']}] note: {m['name']!r}  "
                f"date={(m.get('date') or '?')[:10]}  "
                f"match={m.get('match_method')}/{m.get('match_confidence')}  "
                f"attendees=({m.get('attendees', '')[:100]})\n"
                f"      summary: {m.get('summary_preview', '')[:300]}"
            )

    inventory_block = "\n".join(lines) if lines else "  (no materials available)"

    return f"""\
You are doing reconnaissance for a derisking review of {company['name']} \
({company.get('sector') or 'sector unknown'}, {company.get('commercial_status') or 'status unknown'}, \
{company.get('fund') or 'fund unknown'}).

Below is the full inventory of materials we have for this company. You see \
ONLY metadata — filenames, dates, mime types, folder type, and (for Granola \
notes) brief summary previews. You will NOT read full text in this pass.

Your job:
  1. Identify the IC / DD memo — the original investment thesis. There may \
not be one; if so, return null.
  2. For each material, decide which derisking dimensions it bears on, and \
mark it primary (worth deep-reading), reference (background), or skip \
(irrelevant to derisking).
  3. For each dimension, list the material IDs that should drive scoring.
  4. Flag any dimension that has no good evidence ("evidence_gaps").

DERISKING DIMENSIONS:
  - rapid_innovation_adopt  — Has market adoption accelerated since IC?
  - business_model          — Are unit economics / GTM proving out?
  - technology              — Has tech risk reduced (deployments, TRL, certs)?
  - incentive_management    — Stability of gov incentives / regulatory env?
  - team                    — Strengthening / weakening of leadership?
  - product_growth          — Customer traction, revenue growth, retention?
  - ip_and_data             — Defensibility through patents / data moat?

ROUTING HEURISTICS (use your judgment, these are starting points):
  - The IC memo is automatically primary for ALL dimensions (baseline thesis).
  - Board decks and investor updates are usually primary for product_growth, \
business_model, team, technology.
  - Granola IC / Portco-update meeting notes (especially with the CEO/CFO as \
attendee) are primary for whatever was discussed — judge from the summary.
  - Financial models are primary for business_model only.
  - 409As / cap tables / data rooms / contract folders are usually skip or \
reference unless they specifically inform a dimension.
  - "skip" means truly irrelevant. Prefer "reference" over "skip" when unsure.

INVENTORY ({len(inventory)} items):
{inventory_block}

OUTPUT — strict JSON, no markdown, no preamble:

{{
  "ic_memo_id": "<material id of the IC memo, or null>",
  "ic_memo_rationale": "<one sentence on why this one>",
  "materials": [
    {{
      "id": "<material id>",
      "kind": "primary" | "reference" | "skip",
      "dimensions": ["team", "technology"],
      "rationale": "<one short sentence>"
    }}
  ],
  "by_dimension": {{
    "rapid_innovation_adopt": {{"primary": ["<id>", "<id>"], "rationale": "<one sentence>"}},
    "business_model":         {{"primary": [...], "rationale": "..."}},
    "technology":             {{...}},
    "incentive_management":   {{...}},
    "team":                   {{...}},
    "product_growth":         {{...}},
    "ip_and_data":            {{...}}
  }},
  "evidence_gaps": ["<dimension keys with no decent evidence>"]
}}

Output only the JSON object.
"""


def _enrich_material(entry: dict, inv_lookup: dict) -> dict:
    """Attach metadata fields (name, url, source, modified, folder_type)
    from the inventory onto a manifest material entry so downstream
    consumers (UI chips, audit panel) don't need to re-join."""
    src = inv_lookup.get(entry["id"], {})
    return {
        **entry,
        "name": src.get("name", ""),
        "source": src.get("source", ""),
        "url": src.get("url", ""),
        "modified": src.get("modified") or src.get("date"),
        "folder_type": src.get("folder_type"),
    }


def _normalize_manifest(parsed: dict, inventory: list[dict]) -> dict:
    """Defensive normalization of Pass 1 output. Fills in any missing
    dimension keys, dedupes ids, clamps to known materials, and enriches
    each material entry with display metadata (name, url) so the UI can
    render audit chips without re-joining against the inventory."""
    valid_ids = {m["id"] for m in inventory}
    inv_lookup = {m["id"]: m for m in inventory}
    out: dict[str, Any] = {
        "ic_memo_id": parsed.get("ic_memo_id") if parsed.get("ic_memo_id") in valid_ids else None,
        "ic_memo_rationale": parsed.get("ic_memo_rationale", "") or "",
        "materials": [],
        "by_dimension": {},
        "evidence_gaps": [],
    }
    seen_in_materials: set[str] = set()
    for m in (parsed.get("materials") or []):
        if not isinstance(m, dict):
            continue
        mid = m.get("id")
        if mid not in valid_ids or mid in seen_in_materials:
            continue
        seen_in_materials.add(mid)
        out["materials"].append(_enrich_material({
            "id": mid,
            "kind": m.get("kind") if m.get("kind") in ("primary", "reference", "skip") else "reference",
            "dimensions": [d for d in (m.get("dimensions") or []) if d in DIMENSION_KEYS],
            "rationale": (m.get("rationale") or "")[:500],
        }, inv_lookup))
    # Ensure the IC memo always appears in the materials list, even if
    # the LLM forgot to include it explicitly.
    if out["ic_memo_id"] and out["ic_memo_id"] not in seen_in_materials:
        out["materials"].insert(0, _enrich_material({
            "id": out["ic_memo_id"],
            "kind": "primary",
            "dimensions": list(DIMENSION_KEYS),
            "rationale": "IC memo / DD baseline (auto-injected for all dimensions)",
        }, inv_lookup))
    by_dim = parsed.get("by_dimension") or {}
    for k, _ in DIMENSIONS:
        cell = by_dim.get(k) or {}
        if not isinstance(cell, dict):
            cell = {}
        primary = [pid for pid in (cell.get("primary") or []) if pid in valid_ids]
        # IC memo is implicitly primary for all dims
        if out["ic_memo_id"] and out["ic_memo_id"] not in primary:
            primary.insert(0, out["ic_memo_id"])
        out["by_dimension"][k] = {
            "primary": primary,
            "rationale": (cell.get("rationale") or "")[:500],
        }
    out["evidence_gaps"] = [
        g for g in (parsed.get("evidence_gaps") or []) if g in DIMENSION_KEYS
    ]
    return out


def _build_fallback_manifest(inventory: list[dict]) -> dict:
    """When recon fails (or picks too few primaries), score against
    everything we have. Mirrors the original single-pass behavior so we
    never silently produce a degraded score with hidden inputs."""
    all_ids = [m["id"] for m in inventory]
    inv_lookup = {m["id"]: m for m in inventory}
    # Try to identify the IC memo via filename heuristic
    ic_id: Optional[str] = None
    IC_PATTERNS = re.compile(
        r"\b(ic\b|investment\s*committee|investment\s*memo|"
        r"dd\s*memo|diligence\s*memo|investment\s*recommendation)\b", re.I
    )
    for m in inventory:
        if m["source"] == "drive" and IC_PATTERNS.search(m.get("name", "")):
            ic_id = m["id"]
            break

    primary_ids = all_ids[:25]
    primary_set = set(primary_ids)
    by_dim = {
        k: {
            "primary": list(primary_ids),
            "rationale": "(fallback: recon unavailable, scoring against all materials)",
        }
        for k, _ in DIMENSIONS
    }
    # In fallback, the same id can't be 'primary' in by_dimension and
    # 'reference' in materials — assign each material the kind that
    # matches whether it's used for scoring.
    materials = [
        _enrich_material({
            "id": m["id"],
            "kind": "primary" if m["id"] in primary_set else "reference",
            "dimensions": list(DIMENSION_KEYS) if m["id"] in primary_set else [],
            "rationale": "fallback (no recon)",
        }, inv_lookup)
        for m in inventory
    ]
    return {
        "ic_memo_id": ic_id,
        "ic_memo_rationale": "filename heuristic match" if ic_id else "no IC memo identified",
        "materials": materials,
        "by_dimension": by_dim,
        "evidence_gaps": [],
        "fallback": True,
    }


# ── Pass 2 — Scoring ─────────────────────────────────────────────────────────
def _fetch_text_for_ids(service, conn, inventory: list[dict],
                         needed_ids: set[str]) -> dict[str, dict]:
    """For each material id we need full text for, download/extract once.

    Returns {material_id: {text, name, source, modified, url, ...}}.

    Granola note text is fetched from the DB (we already pulled summaries
    on sync); Drive files are downloaded + parsed via drive_scan helpers.
    """
    by_id = {m["id"]: m for m in inventory}
    out: dict[str, dict] = {}

    # Drive: only download what's needed (one HTTP round-trip per file)
    for mid in needed_ids:
        if mid not in by_id:
            continue
        m = by_id[mid]
        if m["source"] == "drive":
            file_meta = {
                "id": m["drive_file_id"],
                "name": m["name"],
                "mimeType": m.get("mime_type") or "",
            }
            try:
                text = _download_and_extract_text(service, file_meta) or ""
            except Exception as e:
                logger.warning(f"text extract failed for {m['name']}: {e}")
                text = ""
            out[mid] = {**m, "text": text[:MAX_CHARS_PER_PRIMARY_FILE]}
        elif m["source"] == "granola":
            # Pull the full note summary from DB (no API call needed)
            row = conn.execute(
                "SELECT note_summary FROM pr_granola_notes WHERE granola_note_id=?",
                (m["granola_note_id"],),
            ).fetchone()
            note_text = (row["note_summary"] if row else "") or ""
            out[mid] = {**m, "text": note_text[:MAX_CHARS_PER_GRANOLA_NOTE]}
    return out


def _format_material_block(m: dict) -> str:
    """Render one material's full text into a labeled block for Pass 2."""
    if m["source"] == "drive":
        head = f"--- [{m['id']}] {m['name']!r} (folder={m['folder_type']}, modified={m.get('modified', '?')}) ---"
    else:
        head = (
            f"--- [{m['id']}] meeting note: {m['name']!r} "
            f"(date={(m.get('date') or '?')[:10]}, match={m.get('match_method')}/{m.get('match_confidence')}) ---"
        )
    return f"{head}\n{m.get('text', '') or '(text extraction unavailable)'}"


def _build_scoring_prompt(
    company: dict,
    period_label: str,
    manifest: dict,
    fetched: dict[str, dict],
    prior_human_score: Optional[dict],
) -> str:
    """Compose the Pass 2 scoring prompt using only manifest-curated materials."""

    ic_id = manifest.get("ic_memo_id")
    ic_block = "(no IC memo found in linked Drive — scoring will be conservative.)"
    if ic_id and ic_id in fetched:
        ic_text = (fetched[ic_id].get("text") or "")[:MAX_CHARS_IC_MEMO]
        ic_block = (
            f"--- [{ic_id}] {fetched[ic_id]['name']!r} (selected by recon: "
            f"{manifest.get('ic_memo_rationale', '')[:200]}) ---\n{ic_text}"
        )

    # Per-dimension blocks
    chars_used = len(ic_block)
    per_dim_blocks: list[str] = []
    for key, label in DIMENSIONS:
        cell = (manifest.get("by_dimension") or {}).get(key) or {}
        primary_ids = [pid for pid in (cell.get("primary") or []) if pid != ic_id]
        rationale = cell.get("rationale", "") or ""

        material_blocks: list[str] = []
        for mid in primary_ids:
            if mid not in fetched:
                continue
            blk = _format_material_block(fetched[mid])
            if chars_used + len(blk) > MAX_PASS2_TOTAL_CHARS:
                break
            material_blocks.append(blk)
            chars_used += len(blk)

        if material_blocks:
            per_dim_blocks.append(
                f"\n═══ {label}  (recon: {rationale[:300]}) ═══\n"
                + "\n\n".join(material_blocks)
            )
        elif key in (manifest.get("evidence_gaps") or []):
            per_dim_blocks.append(
                f"\n═══ {label} ═══\n"
                f"(evidence_gap: recon flagged this dimension as having no good materials. "
                f"Score=null with reasoning explaining the gap.)"
            )
        else:
            per_dim_blocks.append(
                f"\n═══ {label} ═══\n"
                f"(no primary materials assigned; rely on the IC memo's view of this dimension only.)"
            )

    prior_block = ""
    if prior_human_score:
        score_lines = [
            f"  • {label}: {prior_human_score.get(k) if prior_human_score.get(k) is not None else 'unscored'}"
            for k, label in DIMENSIONS
        ]
        prior_block = (
            f"\n[OPERATOR'S PRIOR SCORE — {prior_human_score.get('period', 'unknown')}, "
            f"total {prior_human_score.get('total_score', '?')}/Q{prior_human_score.get('quartile', '?')}]\n"
            + "\n".join(score_lines)
            + "\n\nThis is the partners' last formal scoring — useful as a baseline.\n"
        )

    return f"""\
You are scoring the {period_label} derisking review for {company['name']}.

Score each of the 7 dimensions -1, 0, +1 (or null if you can't tell from the \
materials). The score is a DELTA versus the IC thesis — has each dimension \
materially derisked since investment, stayed flat, or gotten worse?

SCORING:
+1  MATERIALLY DERISKED since IC. Concrete evidence: customer signed, pilot \
completed, key hire announced, regulation locked in, etc.
 0  No material change, or mixed signals that roughly cancel. Default unless \
you have specific evidence either way.
-1  Still at risk OR new risk emerged. Original concerns persist or worsened.
null Cannot be assessed from materials. Use this rather than guessing.

EVIDENCE CITATIONS:
For each dimension, cite 1-3 short concrete quotes / facts from the \
materials below. Each evidence item must include the material id it came \
from (e.g. "drive:abc123" or "granola:xyz789"). Don't invent quotes.

═══════════════════════════════════════════════════════════════════════════
[IC MEMO — baseline thesis]
═══════════════════════════════════════════════════════════════════════════
{ic_block}
{''.join(per_dim_blocks)}
{prior_block}
═══════════════════════════════════════════════════════════════════════════

OUTPUT — strict JSON, no markdown, no preamble:

{{
  "rapid_innovation_adopt":  {{"score": 1, "reasoning": "...", "evidence": [{{"quote": "...", "from_material_id": "drive:..."}}], "confidence": "high|medium|low"}},
  "business_model":          {{...}},
  "technology":              {{...}},
  "incentive_management":    {{"score": null, "reasoning": "Not addressed", "evidence": [], "confidence": "low"}},
  "team":                    {{...}},
  "product_growth":          {{...}},
  "ip_and_data":             {{...}},
  "evidence_summary":        "<2-3 sentence narrative of the most material changes since IC>",
  "is_exited":               false,
  "overall_confidence":      "high|medium|low"
}}

Output only the JSON object.
"""


# ── LLM provider routing ─────────────────────────────────────────────────────
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-6"
DEFAULT_REFIANT_MODEL   = "qwen3-coder-plus"


def resolve_model(provider: Optional[str], model: Optional[str]) -> tuple[str, bool]:
    """Resolve (model_id, is_refiant) from a provider toggle + optional override."""
    if model:
        return model, model.lower().startswith("qwen")
    p = (provider or "anthropic").lower()
    if p == "refiant":
        return DEFAULT_REFIANT_MODEL, True
    return DEFAULT_ANTHROPIC_MODEL, False


def _call_llm(prompt: str, *, model: str, is_refiant: bool, max_tokens: int = 4000) -> dict:
    """Send the prompt and parse JSON. Returns {data, raw_text, model}."""
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
        max_tokens=max_tokens,
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


# ── Persistence ──────────────────────────────────────────────────────────────
def _normalize_score(v: Any) -> Optional[float]:
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


def _fetch_prior_human_score(conn, company_id: int) -> Optional[dict]:
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


def _persist_score(
    conn,
    *,
    company_id: int,
    period: str,
    fund: str,
    parsed: dict,
    raw_text: str,
    model: str,
    manifest: dict,
    inventory: list[dict],
) -> dict:
    """Upsert one pr_derisking_scores row with evaluator='llm', plus
    the recon manifest. Returns the persisted row contents for the API."""
    by_inv_id = {m["id"]: m for m in inventory}

    scores: dict[str, Any] = {}
    reasoning: dict[str, Any] = {}
    for k, _ in DIMENSIONS:
        cell = parsed.get(k) or {}
        if isinstance(cell, dict):
            scores[k] = _normalize_score(cell.get("score"))
            # Resolve evidence material refs to richer objects so the UI
            # doesn't have to re-join against the manifest
            evidence_in = cell.get("evidence", []) or []
            evidence_out: list[dict] = []
            for e in evidence_in:
                if isinstance(e, dict):
                    mid = e.get("from_material_id") or e.get("material_id") or ""
                    src = by_inv_id.get(mid, {})
                    evidence_out.append({
                        "quote": (e.get("quote") or "")[:600],
                        "from_material_id": mid,
                        "material_name": src.get("name", ""),
                        "material_url": src.get("url", ""),
                        "material_source": src.get("source", ""),
                    })
                elif isinstance(e, str):
                    evidence_out.append({"quote": e[:600], "from_material_id": "",
                                         "material_name": "", "material_url": "",
                                         "material_source": ""})
            reasoning[k] = {
                "score": scores[k],
                "reasoning": (cell.get("reasoning") or "")[:1000],
                "evidence": evidence_out,
                "confidence": cell.get("confidence") or "",
                "primary_material_ids": (manifest.get("by_dimension") or {}).get(k, {}).get("primary") or [],
                "recon_rationale": (manifest.get("by_dimension") or {}).get(k, {}).get("rationale") or "",
            }
        else:
            scores[k] = _normalize_score(cell)
            reasoning[k] = {"score": scores[k], "reasoning": "", "evidence": [],
                            "confidence": "", "primary_material_ids": [],
                            "recon_rationale": ""}

    is_exited = bool(parsed.get("is_exited"))
    summary = score_company(scores, is_exited=is_exited)

    # Source files audit list — every primary material gets recorded
    primary_ids: set[str] = set()
    for cell in (manifest.get("by_dimension") or {}).values():
        for pid in (cell.get("primary") or []):
            primary_ids.add(pid)
    if manifest.get("ic_memo_id"):
        primary_ids.add(manifest["ic_memo_id"])
    source_files = [
        {
            "id": pid,
            "name": by_inv_id.get(pid, {}).get("name", ""),
            "source": by_inv_id.get(pid, {}).get("source", ""),
            "url": by_inv_id.get(pid, {}).get("url", ""),
            "modified": by_inv_id.get(pid, {}).get("modified") or by_inv_id.get(pid, {}).get("date"),
            "folder_type": by_inv_id.get(pid, {}).get("folder_type"),
        }
        for pid in primary_ids if pid in by_inv_id
    ]

    conn.execute(
        """INSERT INTO pr_derisking_scores
           (company_id, period, fund, rapid_innovation_adopt, business_model,
            technology, incentive_management, team, product_growth, ip_and_data,
            is_exited, total_score, quartile,
            evaluator, model_used, reasoning_json, evidence_summary,
            confidence, source_files, manifest_json, scored_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
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
          manifest_json=excluded.manifest_json,
          scored_at=datetime('now')""",
        (
            company_id, period, fund,
            scores["rapid_innovation_adopt"], scores["business_model"],
            scores["technology"], scores["incentive_management"], scores["team"],
            scores["product_growth"], scores["ip_and_data"],
            summary["is_exited"], summary["total_score"], summary["quartile"],
            "llm", model, json.dumps(reasoning),
            (parsed.get("evidence_summary") or "")[:2000],
            (parsed.get("overall_confidence") or "")[:20],
            json.dumps(source_files), json.dumps(manifest),
        ),
    )
    conn.commit()

    return {
        "company_id": company_id,
        "period": period,
        "evaluator": "llm",
        "scores": scores,
        "reasoning": reasoning,
        "manifest": manifest,
        "total_score": summary["total_score"],
        "quartile": summary["quartile"],
        "is_exited": summary["is_exited"],
        "evidence_summary": parsed.get("evidence_summary", ""),
        "overall_confidence": parsed.get("overall_confidence", ""),
        "model_used": model,
        "source_files": source_files,
        "raw_text": raw_text,
    }


# ── Public entry point ───────────────────────────────────────────────────────
def score_company_with_llm(
    conn,
    *,
    company_id: int,
    user_id: int,
    period: str = "2025 LLM",
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> dict:
    """End-to-end two-pass agentic scoring."""
    from ..routes.drive import _get_drive_service

    resolved_model, is_refiant = resolve_model(provider, model)

    company_row = conn.execute(
        "SELECT id, name, fund, sector, commercial_status FROM pr_companies WHERE id=?",
        (company_id,),
    ).fetchone()
    if not company_row:
        raise ValueError(f"Company {company_id} not found")
    company = dict(company_row) if hasattr(company_row, "keys") else {}
    company_name = company["name"]
    fund = company.get("fund") or "Fund I"

    # 1. Build inventory (metadata only)
    drive_inv: list[dict] = []
    try:
        service = _get_drive_service(user_id)
        drive_inv = _list_drive_inventory(conn, service, company_id)
    except Exception as e:
        logger.warning(f"Drive inventory failed for {company_name}: {e}")
        service = None

    granola_inv = _list_granola_inventory(conn, company_id)
    inventory = drive_inv + granola_inv

    if not inventory:
        raise ValueError(
            f"No materials available for {company_name}: no Drive folders linked "
            f"AND no Granola notes attached. Run folder discovery + Granola sync first."
        )

    # 2. PASS 1 — Recon (metadata only). On failure, fall back to "use everything".
    recon_meta: dict[str, Any] = {"used": False, "fallback": False, "error": None}
    manifest: dict
    try:
        recon_prompt = _build_recon_prompt(company, inventory)
        recon_resp = _call_llm(recon_prompt, model=resolved_model, is_refiant=is_refiant, max_tokens=2500)
        manifest = _normalize_manifest(recon_resp["data"], inventory)
        recon_meta["used"] = True
        recon_meta["raw"] = recon_resp["raw_text"][:5000]

        # Sanity check: did recon assign anything beyond the auto-injected
        # IC memo? `_normalize_manifest` injects the IC memo into every
        # dimension's primary list, so a count that includes it would mask
        # a useless recon (LLM picked nothing → still gets n_primary=7).
        # We want to know if recon did real work picking *non-IC* materials.
        ic_id = manifest.get("ic_memo_id")
        n_real_primaries = sum(
            len([pid for pid in (c.get("primary") or []) if pid != ic_id])
            for c in manifest.get("by_dimension", {}).values()
        )
        if n_real_primaries < MIN_PRIMARIES_FOR_RECON_USE:
            logger.info(
                f"Recon picked only {n_real_primaries} non-IC primaries — "
                f"falling back to full corpus"
            )
            manifest = _build_fallback_manifest(inventory)
            recon_meta["fallback"] = True
            recon_meta["fallback_reason"] = (
                f"recon picked only {n_real_primaries} non-IC primaries"
            )
    except Exception as e:
        logger.warning(f"Pass 1 recon failed for {company_name}: {e}")
        manifest = _build_fallback_manifest(inventory)
        recon_meta["fallback"] = True
        recon_meta["error"] = str(e)[:400]

    # 3. Fetch text for ALL materials referenced in the manifest as primary
    needed: set[str] = set()
    if manifest.get("ic_memo_id"):
        needed.add(manifest["ic_memo_id"])
    for cell in (manifest.get("by_dimension") or {}).values():
        for pid in cell.get("primary") or []:
            needed.add(pid)
    fetched = _fetch_text_for_ids(service, conn, inventory, needed) if service else {}
    # If Drive service was unavailable, still fetch Granola text (DB-only)
    if not service:
        fetched = _fetch_text_for_ids(None, conn, inventory, {n for n in needed if n.startswith("granola:")})

    # 4. PASS 2 — Score with curated context
    prior_human = _fetch_prior_human_score(conn, company_id)
    score_prompt = _build_scoring_prompt(
        company={"name": company_name, **company},
        period_label=period,
        manifest=manifest,
        fetched=fetched,
        prior_human_score=prior_human,
    )
    score_resp = _call_llm(score_prompt, model=resolved_model, is_refiant=is_refiant, max_tokens=4500)

    # 5. Persist with manifest attached
    manifest["recon_meta"] = recon_meta
    result = _persist_score(
        conn,
        company_id=company_id,
        period=period,
        fund=fund,
        parsed=score_resp["data"],
        raw_text=score_resp["raw_text"],
        model=resolved_model,
        manifest=manifest,
        inventory=inventory,
    )
    result["company_name"] = company_name
    result["provider"] = "refiant" if is_refiant else "anthropic"
    result["evidence_base"] = {
        "inventory_size": len(inventory),
        "drive_files_seen": len(drive_inv),
        "granola_notes_seen": len(granola_inv),
        "primaries_chosen": len(needed),
        "recon_used": recon_meta["used"],
        "recon_fallback": recon_meta["fallback"],
        "had_ic_memo": bool(manifest.get("ic_memo_id")),
        "had_prior_human_score": prior_human is not None,
        "evidence_gaps": manifest.get("evidence_gaps") or [],
    }
    return result


DEFAULT_MODEL = DEFAULT_ANTHROPIC_MODEL  # back-compat
