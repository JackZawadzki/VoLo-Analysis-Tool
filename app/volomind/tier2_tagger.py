"""Tier 2 LLM tagger — vertical / sector / stage / value_chain classification.

Per-company synthesis. For each distinct company in the corpus, gather its
top documents (memos and decks first, fall back to other doc types), send
to Anthropic Haiku with a structured-output prompt, validate the response
against VoLo's hardcoded taxonomy, and write llm_auto-source tags onto
every document of that company.

Why per-company, not per-document:
- Vertical/sector/stage are properties of a COMPANY, not individual files
- A financial spreadsheet doesn't say "Series B" but the IC memo does
- One LLM call per company is ~30x cheaper than per-doc

Cost: ~$0.01 per company with Haiku 4.5 ($1/MTok input, ~5–10K input tokens
per company). 30 companies → ~$0.30 one-time. Negligible.

Idempotency: clears prior tier2-v1 tags before writing new ones. Safe to
re-run when new documents arrive for an already-classified company —
the tags get refreshed, no duplicates.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

from . import database


TAGGER_VERSION = "tier2-v3"  # bumped — added sector cascade + simplified themes


# Volo Earth Ventures investment taxonomy. Single source of truth.
#
# Designed for INVESTOR-relevant scoping, not document-metadata filtering.
# Each dimension answers a question an investor would actually ask:
#   - Vertical: which corner of the climate-tech universe?
#   - Stage:    where in the funding lifecycle?
#   - Company Type: what kind of business model?
#   - Value Chain / Position: where in the production-to-deployment chain?
#   - Technology / Theme: which cross-cutting climate-tech theme?
#
# Values must be stable — Tier 2 LLM tags every knowledge object (companies,
# memos, meeting notes, patents, market maps) into these exact strings, so
# renaming a value invalidates all prior tags. Add new values freely;
# rename existing ones only if you're prepared to re-run Tier 2.
VOLO_TAXONOMY: dict[str, Any] = {
    "verticals": ["Energy", "Buildings", "Industry", "Mobility"],
    "stages": [
        "Pre-Seed", "Seed", "Series A", "Series B", "Series C",
        "Growth", "Public / Incumbent",
    ],
    "company_types": [
        "Software",
        "Hardware",
        "Hardware-Enabled Software",
        "Materials",
        "Infrastructure / Project Developer",
        "Marketplace / Network",
        "Financing / Insurance",
        "Services",
    ],
    "value_chains": [
        "Raw Materials",
        "Materials Processing / Refining",
        "Component / Equipment Manufacturing",
        "System Integration",
        "Project Development / Deployment",
        "Operations & Maintenance",
        "Monitoring / Measurement / Verification",
        "Optimization / Control Software",
        "Asset Management",
        "Market / Grid / System Operations",
        "Financing / Risk / Insurance",
        "Carbon Accounting / Reporting",
        "Circularity / Recycling / End-of-Life",
        "Workforce / Services",
    ],
    # Sectors are CASCADING per vertical — what specific kind of company
    # within that vertical. Tier 2 picks 1-3 sectors that match the chosen
    # vertical(s). UI shows sector chips only when a vertical is selected.
    "sectors": {
        "Energy": [
            "Solar", "Wind", "Geothermal", "Hydropower",
            "Storage / Batteries", "Hydrogen", "Nuclear / SMR",
            "Grid / Transmission", "Biofuels / Bioenergy",
            "Carbon Capture", "Fusion",
        ],
        "Buildings": [
            "HVAC", "Heat Pumps", "Insulation / Envelope",
            "Lighting / Controls", "Smart Building", "Embodied Carbon",
            "Building-Integrated Renewables",
        ],
        "Industry": [
            "Steel", "Cement / Concrete", "Chemicals", "Plastics",
            "Mining / Metals", "Process Heat", "Industrial Hydrogen",
            "Direct Air Capture", "Industrial AI",
        ],
        "Mobility": [
            "EVs / Powertrains", "Charging Infrastructure", "Batteries",
            "Aviation", "Maritime / Shipping", "Rail", "Logistics / Freight",
            "Autonomy", "Micromobility", "Hydrogen Fuel Cells",
        ],
    },
    # Cross-cutting climate-tech themes — span multiple verticals. Always
    # shown, regardless of which vertical(s) are selected.
    "themes": [
        "Electrification",
        "Carbon Management",
        "Critical Minerals",
        "Circular Economy",
        "Climate Adaptation",
        "Industrial Decarbonization",
    ],
}


# Prioritize docs by document_type when picking the ~10K-token sample we
# feed to Haiku per company. Memos and decks carry the strategic content;
# financial spreadsheets and technical specs are noisier signal for these
# specific dimensions.
_DOC_TYPE_PRIORITY = [
    "memo", "deck", "market", "diligence", "team",
    "technical", "customer", "legal", "financial",
]


# --- Prompt ----------------------------------------------------------------

def _build_prompt(company_name: str, docs_text: str) -> str:
    sectors_block = "\n".join(
        f"  {v}: {', '.join(VOLO_TAXONOMY['sectors'][v])}"
        for v in VOLO_TAXONOMY["verticals"]
    )
    return f"""You are classifying a knowledge object for VoLo Earth Ventures, a climate-tech VC. The classification will be used by an investor to scope diligence questions.

Company / object name: {company_name}

Documents about this object (excerpts from IC memos, decks, meeting notes, market maps, etc.):

{docs_text}

Classify into VoLo's taxonomy. Return ONLY valid JSON, no preamble:

{{
  "verticals": [1-2 entries from {VOLO_TAXONOMY['verticals']}],
  "stage": one of {VOLO_TAXONOMY['stages']} or null,
  "company_types": [1-2 entries from the company_type values],
  "value_chains": [max 4 entries from the value_chain values],
  "sectors": [max 3 entries — must belong to one of the chosen verticals],
  "themes": [max 3 entries from the theme values],
  "rationale": "one short sentence explaining the classification"
}}

Valid company_types:
{', '.join(VOLO_TAXONOMY['company_types'])}

Valid value_chain positions:
{', '.join(VOLO_TAXONOMY['value_chains'])}

Valid sectors (must belong to chosen vertical):
{sectors_block}

Valid cross-cutting themes:
{', '.join(VOLO_TAXONOMY['themes'])}

CLASSIFICATION PHILOSOPHY: Reasonably inclusive — tag any category the company
genuinely operates in or has clear evidence supporting, but don't tag for
tangential or speculative connections. The goal is that an investor scoping
"Energy + Solar + Series A" finds every company that genuinely fits, not
every company with a passing mention.

Per-dimension guidance:
- "verticals" — usually 1. Tag two only when the company genuinely spans
  (e.g. industrial mobility, building-integrated PV). Default to one.
- "stage" — most recent confirmed funding round. Null if unclear.
- "company_types" — usually 1. Tag two when the business model genuinely
  spans both (e.g. hardware company with a substantial SaaS revenue stream
  = "Hardware" AND "Hardware-Enabled Software").
- "value_chains" — tag every position the company DIRECTLY operates in.
  A battery cell maker = "Component / Equipment Manufacturing" (and
  "Materials Processing / Refining" if they refine their own cathode/anode).
  Be precise: where does revenue come from?
- "sectors" — pick the SPECIFIC sector(s) within the chosen vertical(s).
  A solar developer = ["Solar"]. A heat-pump SaaS = ["Heat Pumps"].
  An EV battery maker spanning Mobility + Industry = ["Batteries",
  "Mining / Metals"]. Sectors must belong to one of the chosen verticals.
- "themes" — cross-cutting climate-tech themes that touch the company.
  A solar developer that sells residential systems = ["Electrification"]
  (since it electrifies homes). Skip a theme if no clear connection.

Rules:
- Use ONLY the exact strings from the lists above. Do not invent values.
- Empty lists are fine when nothing fits. Don't force-tag if there's no signal.
- Return ONLY the JSON object. No markdown fences. No explanatory text outside.
"""


# --- LLM call --------------------------------------------------------------

def classify_company(company_name: str, doc_excerpts: list[str]) -> Optional[dict]:
    """Call Anthropic Haiku to classify one company. Returns parsed JSON or None.

    Never raises — failures are logged and return None so the batch caller
    can keep going on remaining companies.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print("[tier2] ANTHROPIC_API_KEY not set — skipping classification", flush=True)
        return None

    try:
        from anthropic import Anthropic
    except ImportError:
        print("[tier2] anthropic SDK not installed — skipping", flush=True)
        return None

    if not doc_excerpts:
        return None

    # Cap each excerpt and total budget so we stay under ~10K input tokens
    capped = []
    total = 0
    for excerpt in doc_excerpts:
        chunk = excerpt[:1500]  # ~375 tokens per chunk max
        if total + len(chunk) > 35000:  # ~9K tokens total
            break
        capped.append(chunk)
        total += len(chunk)
    docs_text = "\n\n---\n\n".join(capped)

    prompt = _build_prompt(company_name, docs_text)
    model = os.environ.get("ANTHROPIC_TAGGER_MODEL", "claude-haiku-4-5-20251001")

    try:
        client = Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in response.content if hasattr(b, "text")).strip()
        return _parse_json_response(text)
    except Exception as e:
        print(f"[tier2] classify failed for {company_name}: {e}", flush=True)
        return None


def _parse_json_response(text: str) -> Optional[dict]:
    """Strip markdown fences, extract the outermost JSON object."""
    text = text.strip()
    text = re.sub(r"^```[a-z]*\n?", "", text)
    text = re.sub(r"\n?```$", "", text)
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return None
    try:
        return json.loads(match.group())
    except json.JSONDecodeError:
        return None


# --- Validation against taxonomy ------------------------------------------

def _validate_classification(result: dict) -> dict:
    """Filter result down to values that exactly match VoLo's taxonomy.
    Drops anything outside the allowed lists. Returns a clean dict.

    Backward-compatible with single-value LLM outputs: if the LLM returns
    "vertical" instead of "verticals", we accept it as a one-element list.
    """
    out: dict[str, Any] = {}

    def _multi(val, allowed: list, cap: int) -> list:
        """Coerce val to a list filtered against allowed values, capped."""
        if isinstance(val, str):
            val = [val]
        if not isinstance(val, list):
            val = []
        return [v for v in val if v in allowed][:cap]

    # Verticals (multi, max 2). Accept legacy "vertical" key too.
    verticals = result.get("verticals") if "verticals" in result else result.get("vertical")
    out["verticals"] = _multi(verticals, VOLO_TAXONOMY["verticals"], 2)

    # Stage (single)
    stage = result.get("stage")
    out["stage"] = stage if stage in VOLO_TAXONOMY["stages"] else None

    # Company types (multi, max 2). Accept legacy "company_type" key too.
    cts = result.get("company_types") if "company_types" in result else result.get("company_type")
    out["company_types"] = _multi(cts, VOLO_TAXONOMY["company_types"], 2)

    # Value chains (multi, max 4)
    out["value_chains"] = _multi(
        result.get("value_chains"), VOLO_TAXONOMY["value_chains"], 4,
    )

    # Sectors (multi, max 3) — must belong to one of the chosen verticals
    valid_sector_pool = []
    for v in out["verticals"]:
        valid_sector_pool.extend(VOLO_TAXONOMY["sectors"].get(v, []))
    out["sectors"] = _multi(result.get("sectors"), valid_sector_pool, 3)

    # Themes (multi, max 3) — cross-cutting, no vertical dependency
    out["themes"] = _multi(
        result.get("themes"), VOLO_TAXONOMY["themes"], 3,
    )

    out["rationale"] = result.get("rationale", "")
    return out


# --- Document selection ----------------------------------------------------

def _gather_company_docs(company_name: str) -> list[str]:
    """Pull the top documents for a company, ordered by document_type priority
    (memo > deck > market > ...) then by recency."""
    with database.cursor() as c:
        # Build a CASE expression for document_type priority
        priority_cases = " ".join(
            f"WHEN dt.value = '{t}' THEN {i}"
            for i, t in enumerate(_DOC_TYPE_PRIORITY)
        )
        c.execute(
            f"""
            SELECT d.body_text
            FROM cc_documents d
            JOIN cc_tags t_company
              ON t_company.document_id = d.id
              AND t_company.dimension = 'company'
              AND t_company.value = ?
            LEFT JOIN cc_tags dt
              ON dt.document_id = d.id
              AND dt.dimension = 'document_type'
            ORDER BY
              CASE {priority_cases} ELSE 99 END ASC,
              d.occurred_at DESC NULLS LAST,
              d.id DESC
            LIMIT 12
            """,
            (company_name,),
        )
        rows = c.fetchall()
    return [r["body_text"] for r in rows if r["body_text"]]


# --- Tag writing ----------------------------------------------------------

def _write_tags_for_company(company_name: str, classification: dict) -> int:
    """Write Tier 2 tags onto every document of this company. Replaces any
    prior tier2-v1 tags for these dimensions. Returns number of tags written."""
    with database.cursor() as c:
        c.execute(
            "SELECT DISTINCT document_id FROM cc_tags "
            "WHERE dimension = 'company' AND value = ?",
            (company_name,),
        )
        doc_ids = [r["document_id"] for r in c.fetchall()]

    if not doc_ids:
        return 0

    written = 0
    pairs: list[tuple[str, str]] = []
    for v in classification.get("verticals", []):
        pairs.append(("vertical", v))
    if classification.get("stage"):
        pairs.append(("stage", classification["stage"]))
    for ct in classification.get("company_types", []):
        pairs.append(("company_type", ct))
    for vc in classification.get("value_chains", []):
        pairs.append(("value_chain", vc))
    for s in classification.get("sectors", []):
        pairs.append(("sector", s))
    for th in classification.get("themes", []):
        pairs.append(("theme", th))

    if not pairs:
        return 0

    with database.cursor() as c:
        # Clear old tier2-v1 tags for these dimensions on these docs.
        for doc_id in doc_ids:
            for dimension, _value in pairs:
                c.execute(
                    "DELETE FROM cc_tags "
                    "WHERE document_id = ? AND dimension = ? "
                    "AND tagger_version = ?",
                    (doc_id, dimension, TAGGER_VERSION),
                )

        # Insert fresh tags. INSERT OR IGNORE in case of any unique-constraint
        # collision — host translator handles the SQLite/Postgres difference.
        for doc_id in doc_ids:
            for dimension, value in pairs:
                c.execute(
                    "INSERT OR IGNORE INTO cc_tags "
                    "(document_id, segment_id, dimension, value, source, "
                    "confidence, tagger_version) "
                    "VALUES (?, NULL, ?, ?, 'llm_auto', NULL, ?)",
                    (doc_id, dimension, value, TAGGER_VERSION),
                )
                written += 1
    return written


# --- Driver ---------------------------------------------------------------

def list_companies() -> list[str]:
    """All distinct companies that have at least one ingested document."""
    with database.cursor() as c:
        c.execute(
            "SELECT DISTINCT value AS name FROM cc_tags "
            "WHERE dimension = 'company' "
            "ORDER BY value"
        )
        return [r["name"] for r in c.fetchall() if r["name"]]


def run(*, only_companies: Optional[list[str]] = None,
        skip_already_classified: bool = True,
        progress_callback=None) -> dict:
    """Run Tier 2 across all (or specified) companies.

    Args:
        only_companies: if provided, only classify these. Else all in corpus.
        skip_already_classified: skip companies that already have a vertical
            tag from this tagger_version. Set False to force re-classify.
        progress_callback: optional fn(done: int, total: int, current: str)

    Returns: stats dict.
    """
    targets = only_companies or list_companies()
    stats = {
        "total": len(targets),
        "classified": 0,
        "skipped_already_classified": 0,
        "failed": 0,
        "tags_written": 0,
    }

    for i, company_name in enumerate(targets):
        if progress_callback:
            progress_callback(i, len(targets), company_name)

        if skip_already_classified and _is_already_classified(company_name):
            stats["skipped_already_classified"] += 1
            continue

        excerpts = _gather_company_docs(company_name)
        if not excerpts:
            stats["failed"] += 1
            continue

        raw_result = classify_company(company_name, excerpts)
        if raw_result is None:
            stats["failed"] += 1
            continue

        validated = _validate_classification(raw_result)
        n = _write_tags_for_company(company_name, validated)
        if n > 0:
            stats["classified"] += 1
            stats["tags_written"] += n
        else:
            stats["failed"] += 1

    if progress_callback:
        progress_callback(len(targets), len(targets), "")
    return stats


def _is_already_classified(company_name: str) -> bool:
    """True if this company already has a vertical tag from tier2-v1."""
    with database.cursor() as c:
        c.execute(
            "SELECT 1 FROM cc_tags t "
            "JOIN cc_documents d ON d.id = t.document_id "
            "JOIN cc_tags ct "
            "  ON ct.document_id = d.id "
            "  AND ct.dimension = 'company' "
            "  AND ct.value = ? "
            "WHERE t.dimension = 'vertical' "
            "AND t.tagger_version = ? "
            "LIMIT 1",
            (company_name, TAGGER_VERSION),
        )
        return c.fetchone() is not None
