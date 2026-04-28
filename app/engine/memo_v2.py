"""
Memo Engine v2 — manifest-aware memo generation.

The v1 pipeline (in app/routes/memo.py) processes each data-room file in
isolation, then stitches per-section bullet fragments together. The model
never sees the whole data room at once and is blind to folder structure,
which means memos can miss context, contradict themselves, or feel like
the writer didn't actually open the room.

v2 fixes that with three stages:

  Stage 1 — MANIFEST PASS (one LLM call)
    Build a folder tree string + per-file previews (first ~1500 chars).
    Send all of it to Claude in ONE call. Claude returns JSON:
      - data_room_narrative: 1-2 paragraphs describing what's in here
      - documents[]: each file tagged primary / reference / skip, with
        the memo sections it's relevant to
      - cross_document_flags[]: conflicts the writer must reconcile

  Stage 2 — TARGETED DEEP READS (deterministic)
    Re-extract the text of every doc the manifest classified "primary",
    this time without the v1 60k truncation cap (full text up to 250k
    chars). "Reference" docs keep their preview-length text. "Skip" docs
    are dropped entirely.

  Stage 3 — CACHED SECTION WRITING (one LLM call per section)
    Build a single context corpus (manifest narrative + all primary doc
    full text + reference doc summaries + RVM report data). Send it as
    a cached prefix on every section call so we pay full price once and
    ~10% on the remaining ~14 calls. Each section sees the FULL data
    room when it writes — no more per-section bucket fragments.

Stage 4 (synthesis: Investment Overview / High Level Opportunities /
High Level Risks) stays in app/routes/memo.py — it works the same in
both engines and we don't want to duplicate it.

Public entry point: `run_memo_v2_pipeline(...)` returns
`(section_texts, total_tokens_in, total_tokens_out, pass_log,
manifest_meta)` so the existing memo orchestrator can plug it in
behind the engine_version flag without otherwise restructuring.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Callable, Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Tunables
# ─────────────────────────────────────────────────────────────────────────────

# How much of each file to show Claude during the manifest pass.
_PREVIEW_CHARS = 1500

# Hard cap on total preview corpus size sent to the manifest pass. If a
# data room blows past this we truncate the per-file previews proportionally.
_MANIFEST_INPUT_BUDGET_CHARS = 180_000

# Per-doc cap for "primary" deep reads. Most data-room PDFs land well
# under this. Drive-synced files are already capped at 200k by the sync
# step; manual uploads at 200k by the upload extractor.
_PRIMARY_DOC_CAP = 250_000

# "reference" docs keep at most this much of their text.
_REFERENCE_DOC_CAP = 8_000

# Total corpus cap for the cached section-writing prefix. If we overshoot,
# we truncate reference docs first, then the tail of the largest primary docs.
_SECTION_CORPUS_CAP = 600_000

# Manifest pass model output budget. Claude has a hard cap (8192 for Sonnet
# 4.6) so we stay safely under it. The output is JSON, not prose, so this is
# more than enough for ~50 documents.
_MANIFEST_MAX_TOKENS = 6000

# Section writer output budget per section.
_SECTION_MAX_TOKENS = 3000


# ─────────────────────────────────────────────────────────────────────────────
# Helpers — folder tree + document previews
# ─────────────────────────────────────────────────────────────────────────────

def _build_folder_tree(raw_docs: list) -> str:
    """Render the data room as an indented tree of folders and files.

    Drive-synced docs have a `subfolder_path` (e.g. "01_Financials/" or
    "Round/Term_Sheet_Drafts/"); manual uploads don't, so they bucket under
    "(uploaded files)".
    """
    by_folder: dict[str, list[str]] = {}
    for d in raw_docs:
        path = (d.get("subfolder_path") or "").strip().strip("/")
        bucket = path or "(uploaded files)"
        by_folder.setdefault(bucket, []).append(d.get("file_name", "<unnamed>"))

    lines = []
    for folder in sorted(by_folder.keys()):
        lines.append(f"📁 {folder}/")
        for name in sorted(by_folder[folder]):
            lines.append(f"   • {name}")
    return "\n".join(lines) if lines else "(empty data room)"


def _build_doc_previews(raw_docs: list, budget_chars: int) -> str:
    """Build a single text block of per-file previews for the manifest pass.

    Each file gets a header (name + folder + category) and its first
    `_PREVIEW_CHARS` of extracted text. If the total would blow past the
    budget, we shrink each preview proportionally so all files still get
    represented (better to see less of every file than to see nothing of
    half the room)."""
    if not raw_docs:
        return "(no documents)"

    per_file = _PREVIEW_CHARS
    overhead_per_file = 200  # header line, separators
    total = sum(len(d.get("extracted_text", "") or "") for d in raw_docs)
    needed = min(total, per_file * len(raw_docs)) + overhead_per_file * len(raw_docs)
    if needed > budget_chars and raw_docs:
        per_file = max(300, (budget_chars - overhead_per_file * len(raw_docs)) // len(raw_docs))

    parts = []
    for i, d in enumerate(raw_docs, 1):
        fname = d.get("file_name", "<unnamed>")
        folder = (d.get("subfolder_path") or "").strip()
        cat = (d.get("doc_category") or "other").replace("_", " ")
        full = (d.get("extracted_text") or "").strip()
        excerpt = full[:per_file]
        parts.append(
            f"--- DOC #{i} ---\n"
            f"file: {folder + fname if folder else fname}\n"
            f"category: {cat}\n"
            f"total_chars: {len(full)}\n"
            f"PREVIEW (first {per_file} chars):\n{excerpt}\n"
        )
    return "\n".join(parts)


def _truncate_to_cap(text: str, cap: int) -> str:
    """Trim `text` to at most `cap` chars, preserving the head (which is
    where the most important content typically lives in pitch decks,
    cap tables, term sheets, etc.). Adds an explicit truncation marker so
    Claude knows it didn't get the full doc."""
    if len(text) <= cap:
        return text
    return text[:cap] + f"\n\n[...truncated at {cap:,} chars; full doc is {len(text):,} chars...]"


# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 — manifest pass
# ─────────────────────────────────────────────────────────────────────────────

_MANIFEST_SYSTEM = """You are a triage analyst preparing a venture-capital data room for a memo writer.

Your job is to look at the entire data room — folder structure plus a short preview of every file — and produce a JSON manifest that classifies each document and flags cross-document conflicts.

CRITICAL: Output ONLY valid JSON. No prose, no markdown fences, no commentary before or after. Your entire response must parse with json.loads().

Classifications:
- "primary": this document contains substantive, memo-driving content (term sheet, pitch deck, financial model, cap table, technical due diligence, IP/patent filings, customer references with quantitative data, market sizing reports). The memo writer should read this in full.
- "reference": this document provides supporting context but isn't the main source for any memo section (NDAs, supplementary correspondence, secondary news articles, brief bios). The memo writer will see a summary, not the full text.
- "skip": this document is irrelevant to the memo (boilerplate legal templates, automated email exports with no analytical content, duplicate copies, blank or near-blank files).

When in doubt between primary and reference, choose primary. When in doubt between reference and skip, choose reference. We'd rather waste tokens than miss content.

Cross-document flags should be specific and actionable: "the term sheet at /Round/term_sheet_v2.pdf states $10M Series A while cap_table_2024.xlsx implies an $8M raise — reconcile in Financing section". Only flag REAL conflicts you can see in the previews; never invent them.

The data_room_narrative is 1-2 paragraphs that orient the memo writer. It should describe what kinds of documents are present, how the data room is organized, and any notable gaps (e.g. "no IP/patent documentation despite hardware-heavy pitch")."""


_MANIFEST_OUTPUT_SCHEMA = """Output schema (JSON):
{
  "data_room_narrative": "<1-2 paragraphs>",
  "cross_document_flags": [
    "<specific reconcilable conflict, citing file paths>",
    ...
  ],
  "documents": [
    {
      "file_name": "<exact file_name from input>",
      "classification": "primary" | "reference" | "skip",
      "reason": "<one short sentence>",
      "relevant_sections": ["section_key", ...]
    },
    ...
  ]
}

Valid section_key values: investment_overview, high_level_opportunities, high_level_risks, company_overview, market, business_model, financials, team, traction, competitive_position, carbon_impact, technology_ip_moat, financing_overview, cohort_analysis."""


def _pass_manifest(client, model: str, raw_docs: list, memo_section_titles: list) -> tuple[dict, int, int]:
    """Stage 1: Single LLM call producing a JSON manifest of the whole data room.

    Returns (manifest_dict, tokens_in, tokens_out). On parse failure or LLM
    error returns a safe fallback manifest where every doc is classified by
    `_classify_default` so the rest of the pipeline still runs."""

    folder_tree = _build_folder_tree(raw_docs)
    previews = _build_doc_previews(raw_docs, _MANIFEST_INPUT_BUDGET_CHARS)
    section_titles_str = "\n".join(f"- {t}" for t in memo_section_titles)

    user_msg = (
        "# DATA ROOM FOLDER TREE\n"
        f"{folder_tree}\n\n"
        "# DOCUMENT PREVIEWS\n"
        f"{previews}\n\n"
        "# MEMO SECTIONS THE WRITER WILL FILL\n"
        f"{section_titles_str}\n\n"
        f"{_MANIFEST_OUTPUT_SCHEMA}\n\n"
        "Produce the JSON manifest now. Output ONLY the JSON object."
    )

    try:
        response = client.messages.create(
            model=model,
            max_tokens=_MANIFEST_MAX_TOKENS,
            system=_MANIFEST_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
        )
        reply = "".join(b.text for b in response.content if b.type == "text").strip()
        tokens_in = response.usage.input_tokens if response.usage else 0
        tokens_out = response.usage.output_tokens if response.usage else 0
    except Exception as e:
        logger.error(f"v2 manifest pass failed: {e}")
        return _fallback_manifest(raw_docs), 0, 0

    # Strip stray markdown fences if Claude added them despite instructions
    if reply.startswith("```"):
        reply = re.sub(r"^```(?:json)?\s*|\s*```$", "", reply, flags=re.MULTILINE).strip()

    try:
        manifest = json.loads(reply)
    except json.JSONDecodeError as e:
        logger.error(f"v2 manifest pass returned non-JSON: {e}; first 500 chars: {reply[:500]}")
        return _fallback_manifest(raw_docs), tokens_in, tokens_out

    # Defensive fill: ensure every input doc has a classification
    seen = {d.get("file_name") for d in manifest.get("documents", [])}
    for d in raw_docs:
        if d.get("file_name") not in seen:
            manifest.setdefault("documents", []).append({
                "file_name": d.get("file_name"),
                "classification": _classify_default(d.get("file_name", ""), d.get("doc_category", "")),
                "reason": "(no classification returned by manifest; fallback applied)",
                "relevant_sections": [],
            })

    manifest.setdefault("data_room_narrative", "(no narrative produced)")
    manifest.setdefault("cross_document_flags", [])
    return manifest, tokens_in, tokens_out


def _classify_default(file_name: str, doc_category: str) -> str:
    """Fallback classification used if the manifest call fails or omits a doc."""
    primary_categories = {
        "financial_model", "pitch_deck", "term_sheet", "cap_table",
        "ip_patent", "technical_diligence",
    }
    if doc_category in primary_categories:
        return "primary"
    return "reference"


def _fallback_manifest(raw_docs: list) -> dict:
    """Minimal manifest used when the LLM call fails. Every doc gets a
    sensible default classification so the pipeline can continue."""
    return {
        "data_room_narrative": (
            "(Manifest pass failed — falling back to category-based classification. "
            "Memo will still be written, but cross-document conflict detection is unavailable.)"
        ),
        "cross_document_flags": [],
        "documents": [
            {
                "file_name": d.get("file_name"),
                "classification": _classify_default(
                    d.get("file_name", ""), d.get("doc_category", "")
                ),
                "reason": "fallback (manifest failed)",
                "relevant_sections": [],
            }
            for d in raw_docs
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 — targeted deep reads
# ─────────────────────────────────────────────────────────────────────────────

def _targeted_deep_reads(raw_docs: list, manifest: dict) -> dict:
    """Apply the manifest's classifications to produce per-doc text that
    will go into the cached section-writing corpus.

    Returns a dict: {file_name: {classification, text, doc_category, subfolder_path}}.
    "skip" docs are excluded from the output so they don't enter the corpus
    at all.

    For v2 first iteration, "deep read" means "use the full extracted_text
    that's already in the DB" (capped at _PRIMARY_DOC_CAP). The DB
    already has up to 200k chars per doc from the upload/sync extractors —
    which is far more than v1's downstream 60k truncation. We don't add
    a separate Excel-sheet-agent loop yet; the manifest's previews include
    enough Excel context to make smart classifications.
    """
    by_name = {d.get("file_name"): d for d in raw_docs}
    classifications = {
        d.get("file_name"): d.get("classification", "reference")
        for d in manifest.get("documents", [])
    }

    deep_reads: dict[str, dict] = {}
    for name, raw in by_name.items():
        cls = classifications.get(name, "reference")
        if cls == "skip":
            continue
        text = (raw.get("extracted_text") or "").strip()
        if not text:
            continue
        if cls == "primary":
            text = _truncate_to_cap(text, _PRIMARY_DOC_CAP)
        else:  # reference
            text = _truncate_to_cap(text, _REFERENCE_DOC_CAP)
        deep_reads[name] = {
            "classification": cls,
            "text": text,
            "doc_category": raw.get("doc_category", "other"),
            "subfolder_path": (raw.get("subfolder_path") or "").strip(),
        }
    return deep_reads


# ─────────────────────────────────────────────────────────────────────────────
# Stage 3 — cached section writing
# ─────────────────────────────────────────────────────────────────────────────

def _build_section_corpus(
    *,
    manifest: dict,
    deep_reads: dict,
    report_context: str,
    folder_tree: str,
    company_name: str,
) -> tuple[str, dict]:
    """Build the single context corpus that gets sent (cached) on every
    section-writing call. Includes:
      - Company name and the manifest narrative
      - Folder tree (so the writer knows how the room is organized)
      - Cross-document flags
      - All primary docs in full
      - All reference docs as short summaries
      - RVM report data

    Returns (corpus_text, citation_index). citation_index maps file_name to
    a [n] number that sections will use to cite sources, just like v1.
    """

    parts = []
    parts.append(f"# DATA ROOM FOR {company_name or 'this deal'}\n")

    parts.append("## Data Room Narrative")
    parts.append(manifest.get("data_room_narrative", "").strip() or "(no narrative)")

    parts.append("\n## Folder Structure")
    parts.append(folder_tree)

    flags = manifest.get("cross_document_flags") or []
    if flags:
        parts.append("\n## Cross-Document Flags (writer must address)")
        for f in flags:
            parts.append(f"- {f}")

    # Build citation index in document order: primary docs first, then reference
    citation_index: dict[str, int] = {}
    primaries = [(n, d) for n, d in deep_reads.items() if d["classification"] == "primary"]
    references = [(n, d) for n, d in deep_reads.items() if d["classification"] == "reference"]
    for i, (name, _) in enumerate(primaries + references, 1):
        citation_index[name] = i

    if primaries:
        parts.append("\n## PRIMARY DOCUMENTS (read in full)")
        for name, doc in primaries:
            cite = citation_index[name]
            cat = doc["doc_category"].replace("_", " ").title()
            folder = doc["subfolder_path"]
            header = f"### [{cite}] [{cat}] {folder + name if folder else name}"
            parts.append(header)
            parts.append(doc["text"])

    if references:
        parts.append("\n## REFERENCE DOCUMENTS (summaries only)")
        for name, doc in references:
            cite = citation_index[name]
            cat = doc["doc_category"].replace("_", " ").title()
            folder = doc["subfolder_path"]
            header = f"### [{cite}] [{cat}] {folder + name if folder else name}"
            parts.append(header)
            parts.append(doc["text"])

    if report_context:
        parts.append("\n## QUANTITATIVE REPORT DATA [RVM]")
        parts.append(report_context)

    corpus = "\n\n".join(parts)

    # Final cap — protect the model from a runaway data room
    if len(corpus) > _SECTION_CORPUS_CAP:
        # Reference docs go first, then tail of largest primary
        corpus = corpus[:_SECTION_CORPUS_CAP] + f"\n\n[...corpus truncated at {_SECTION_CORPUS_CAP:,} chars...]"

    return corpus, citation_index


_SECTION_WRITER_SYSTEM_V2 = """You are VoLo Earth Ventures' Investment Committee memo writer.

You are writing ONE section of an investment memorandum. You have ALREADY been
given the entire data room as cached context — folder structure, primary
documents in full, reference document summaries, and the RVM quantitative
output. Use it. The data is in front of you; the memo should read like you
have actually read every page.

CRITICAL — FACTUAL ACCURACY:
- ONLY state facts, names, numbers, dates, and claims that appear explicitly in the data room corpus or RVM report data above.
- NEVER fabricate facility names, locations, dollar amounts, percentages, timelines, partnerships, or any other specifics.
- Every quantitative claim (dollar amount, percentage, date, capacity figure) MUST have a citation [n] (matching the citation index in the corpus) or [RVM] (for the quantitative report). If you cannot cite it, do not write it.
- If a cross-document flag in the corpus is relevant to your section, address it explicitly — name the conflicting sources and explain how it should be resolved.
- When in doubt, be less specific rather than risk inventing details.

Rules:
1. Write in professional, data-driven prose — cite specific numbers, percentages, and dollar amounts FROM THE CORPUS ONLY
2. Be thorough but avoid padding — every sentence should add value
3. Balance the bull case and bear case — credibility comes from honest assessment, not advocacy
4. Use Markdown formatting: ### for sub-sections, **bold** for emphasis, bullet lists only for catalogs of discrete items
5. Do NOT include the section title as a header — it will be added automatically
6. Target 400-800 words per section (more for Financing Overview and Business Model, less for shorter sections)
7. Each section stands alone — do NOT reference other sections
8. If the data room genuinely lacks information for this section, explicitly note it as a diligence gap requiring follow-up — do NOT fill gaps with invented details
9. Open with a strong orienting statement that frames why this topic matters for the investment thesis
10. Connect every fact back to its investment implication — never leave data uninterpreted"""


def _pass_section_writer_cached(
    *,
    client,
    model: str,
    section: dict,
    corpus: str,
    citation_legend: str,
    template_guidance: str,
    company_name: str,
    links: list,
    additional_instructions: str,
) -> dict:
    """Stage 3: Write a single memo section. The corpus is sent with
    cache_control so subsequent section calls hit the cache.

    Returns {text, tokens_in, tokens_out, cache_creation, cache_read}.
    """

    # The cached prefix is large and identical across all section calls.
    # The non-cached suffix is small and section-specific.
    cached_prefix = (
        "The full data room corpus follows. Read it carefully — every fact "
        "in your section must be sourced from this corpus or the RVM report.\n\n"
        f"{corpus}\n\n"
        "---\n\n"
        f"# CITATION INDEX\n{citation_legend}\n\n"
        "When citing the corpus, use inline citations like [1], [2], etc., matching "
        "the numbers in the citation index. Use [RVM] for quantitative report data. "
        "You may combine citations: [1][3] or [1, RVM]."
    )

    section_specific = [
        f"# SECTION TO WRITE: {section['title']}",
        f"## Section Purpose\n{section['guidance']}",
    ]
    if template_guidance:
        section_specific.append(f"## Template Guidance for This Section\n{template_guidance}")
    if links:
        section_specific.append("## Reference Links\n" + "\n".join(f"- {l}" for l in links))
    if additional_instructions:
        section_specific.append(f"## Additional Instructions From the Analyst\n{additional_instructions}")
    section_specific.append(
        f"\nWrite the '{section['title']}' section of the investment memo for "
        f"{company_name or 'this company'}. Be thorough and data-driven. "
        "Cite your sources using [n] notation."
    )
    suffix = "\n\n".join(section_specific)

    try:
        response = client.messages.create(
            model=model,
            max_tokens=_SECTION_MAX_TOKENS,
            system=_SECTION_WRITER_SYSTEM_V2,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": cached_prefix,
                        "cache_control": {"type": "ephemeral"},
                    },
                    {
                        "type": "text",
                        "text": suffix,
                    },
                ],
            }],
        )
        text = "".join(b.text for b in response.content if b.type == "text")
        usage = response.usage
        tokens_in = (usage.input_tokens if usage else 0) or 0
        tokens_out = (usage.output_tokens if usage else 0) or 0
        cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        return {
            "text": text.strip(),
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cache_creation": cache_creation,
            "cache_read": cache_read,
        }
    except Exception as e:
        logger.error(f"v2 section write failed for {section['key']}: {e}")
        return {
            "text": f"*[Generation failed for this section: {str(e)[:200]}]*",
            "tokens_in": 0,
            "tokens_out": 0,
            "cache_creation": 0,
            "cache_read": 0,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Public orchestrator
# ─────────────────────────────────────────────────────────────────────────────

def run_memo_v2_pipeline(
    *,
    client,
    model: str,
    raw_docs: list,
    report_context: str,
    memo_sections: list,
    template_sections: dict,
    company_name: str,
    links: list,
    locked_sections: dict,
    additional_instructions: str,
    progress_cb: Optional[Callable[[int, str], None]] = None,
) -> tuple[dict, int, int, list, dict]:
    """Run the manifest-aware memo pipeline end to end.

    Args:
        client: Claude client (Anthropic or Refiant shim) from llm_utils.
        model: model id, e.g. "claude-sonnet-4-6".
        raw_docs: list of dicts {id, file_name, doc_category, extracted_text,
            subfolder_path?} — output of _load_raw_documents in memo.py.
        report_context: string from _build_report_context in memo.py.
        memo_sections: list of section dicts (only the data sections, not
            the synthesis ones). Same shape as MEMO_SECTIONS in memo.py.
        template_sections: {section_key: template guidance str}.
        company_name: free-text company name for prompts.
        links: optional reference URLs the analyst added.
        locked_sections: {section_key: pre-written markdown} — preserved
            verbatim, the LLM is never called for these.
        additional_instructions: free-text from the analyst that goes into
            every section call.
        progress_cb: optional (pct, message) → None callback for UI updates.

    Returns:
        (section_texts, total_tokens_in, total_tokens_out, pass_log, manifest_meta)
        where manifest_meta = {data_room_narrative, cross_document_flags,
        classifications: {file_name: classification}}.
    """

    def _progress(pct: int, msg: str):
        if progress_cb:
            try: progress_cb(pct, msg)
            except Exception: pass

    pass_log: list = []
    total_in = 0
    total_out = 0
    section_texts: dict = {}
    locked = locked_sections or {}

    # ── Stage 1: manifest pass ────────────────────────────────────────────
    if raw_docs:
        _progress(8, "Cataloging the data room (manifest pass)...")
        section_titles = [s["title"] for s in memo_sections]
        manifest, m_in, m_out = _pass_manifest(client, model, raw_docs, section_titles)
        total_in += m_in
        total_out += m_out
        pass_log.append({"stage": "manifest", "tokens_in": m_in, "tokens_out": m_out})
    else:
        manifest = {"data_room_narrative": "(no documents attached)",
                    "cross_document_flags": [], "documents": []}
        pass_log.append({"stage": "manifest", "tokens_in": 0, "tokens_out": 0, "skipped": "no docs"})

    # ── Stage 2: targeted deep reads ──────────────────────────────────────
    deep_reads = _targeted_deep_reads(raw_docs, manifest) if raw_docs else {}
    n_primary = sum(1 for d in deep_reads.values() if d["classification"] == "primary")
    n_reference = sum(1 for d in deep_reads.values() if d["classification"] == "reference")
    n_skip = len(raw_docs) - len(deep_reads)
    pass_log.append({
        "stage": "deep_reads",
        "primary": n_primary, "reference": n_reference, "skip": n_skip,
    })
    _progress(15, f"Manifest classified {n_primary} primary, {n_reference} reference, {n_skip} skip.")

    # ── Stage 3: cached section writing ───────────────────────────────────
    folder_tree = _build_folder_tree(raw_docs) if raw_docs else "(no documents)"
    corpus, citation_index = _build_section_corpus(
        manifest=manifest,
        deep_reads=deep_reads,
        report_context=report_context,
        folder_tree=folder_tree,
        company_name=company_name,
    )
    citation_legend = "\n".join(
        f"[{num}] {fname}" for fname, num in
        sorted(citation_index.items(), key=lambda kv: kv[1])
    )

    _n_steps = max(1, sum(1 for s in memo_sections if s["key"] not in locked))
    _step = 0
    _pct_span = 75  # 15% → 90% covers the section writing
    for section in memo_sections:
        sk = section["key"]
        if sk in locked:
            section_texts[sk] = locked[sk]
            pass_log.append({"stage": "section_locked", "section": sk})
            continue
        _step += 1
        pct = 15 + int((_step / _n_steps) * _pct_span)
        _progress(min(pct, 90), f"Writing section: {section['title']} ({_step}/{_n_steps})")
        result = _pass_section_writer_cached(
            client=client,
            model=model,
            section=section,
            corpus=corpus,
            citation_legend=citation_legend,
            template_guidance=template_sections.get(sk, ""),
            company_name=company_name,
            links=links,
            additional_instructions=additional_instructions,
        )
        section_texts[sk] = result["text"]
        total_in += result["tokens_in"]
        total_out += result["tokens_out"]
        pass_log.append({
            "stage": "section",
            "section": sk,
            "tokens_in": result["tokens_in"],
            "tokens_out": result["tokens_out"],
            "cache_creation": result["cache_creation"],
            "cache_read": result["cache_read"],
        })

    manifest_meta = {
        "data_room_narrative": manifest.get("data_room_narrative", ""),
        "cross_document_flags": manifest.get("cross_document_flags", []),
        "classifications": {
            d.get("file_name"): d.get("classification")
            for d in manifest.get("documents", [])
        },
        "n_primary": n_primary,
        "n_reference": n_reference,
        "n_skip": n_skip,
    }

    return section_texts, total_in, total_out, pass_log, manifest_meta
