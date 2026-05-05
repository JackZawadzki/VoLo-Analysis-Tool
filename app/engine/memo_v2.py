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
import os
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Refiant context-window adaptation
# ─────────────────────────────────────────────────────────────────────────────
# Refiant / Qwen models have a smaller context window than Claude. The
# default Claude path sends a single 600k-char corpus per section call;
# Refiant can't fit that in one call.
#
# When the model is Refiant, we transparently switch to a map-reduce
# pattern:
#   • Split the corpus into smaller chunks (each fitting the Refiant
#     budget).
#   • For each chunk, ask Refiant: "extract every fact relevant to
#     section X." Returns a bullet brief.
#   • Combine all per-chunk briefs and ask Refiant once more: "write
#     the section from this brief."
#
# Output quality stays close to the single-call cached path because the
# model still sees every primary doc, just in pieces. Cost: N+1 calls
# per section instead of 1 (where N = number of chunks). The pipeline
# takes longer but produces a complete memo.
#
# CLAUDE PATH IS UNTOUCHED — these helpers fire only when the model id
# starts with "qwen".

# Per-call input budget for Refiant (chars in system + user prompt).
# Default 700k chars ≈ 175k tokens, sized for qwen-rfnt's 262k token window
# with comfortable headroom for system prompt, instructions, and output.
# The chunked map-reduce path only triggers when the corpus exceeds this —
# for typical data rooms the standard cached single-call path runs instead.
# Override via env var for other Refiant-served models with smaller windows.
_REFIANT_INPUT_BUDGET_DEFAULT = 700_000


def _is_refiant_model(model: str) -> bool:
    """The pipeline only adapts when the user has explicitly chosen a
    Refiant/Qwen model — Claude callers see exactly the legacy behavior."""
    return (model or "").startswith("qwen")


def _get_refiant_input_budget() -> int:
    raw = (os.environ.get("VOLO_REFIANT_INPUT_BUDGET_CHARS") or "").strip()
    if raw.isdigit():
        n = int(raw)
        if n >= 20_000:  # sanity floor — anything below 20k chars per
                         # call is too small to do useful chunking
            return n
    return _REFIANT_INPUT_BUDGET_DEFAULT


def _split_corpus_into_chunks(corpus: str, target_chars: int) -> list[str]:
    """Split the corpus into chunks ≤ target_chars, preferring to break
    at markdown section boundaries (## headings). A single section
    larger than target_chars is hard-split mid-section. Empty input
    returns []."""
    if not corpus or not corpus.strip():
        return []
    if len(corpus) <= target_chars:
        return [corpus]

    # Find positions of major section headings (## ...) — these are the
    # natural break points (one heading per primary doc / per region).
    boundaries = [0]
    for m in re.finditer(r"\n(?=##\s)", corpus):
        boundaries.append(m.start() + 1)
    boundaries.append(len(corpus))

    chunks: list[str] = []
    cursor = 0
    for i in range(1, len(boundaries)):
        next_pos = boundaries[i]
        # If the current accumulated chunk plus the next section would
        # exceed the budget, close the chunk at the last heading we hit.
        if next_pos - cursor > target_chars and boundaries[i - 1] > cursor:
            chunks.append(corpus[cursor:boundaries[i - 1]])
            cursor = boundaries[i - 1]
        # If even a single section is bigger than target, hard-split it
        # by char count rather than dropping content.
        while next_pos - cursor > target_chars:
            cut = cursor + target_chars
            chunks.append(corpus[cursor:cut] + "\n\n[...chunk continues in next part]")
            cursor = cut
    if cursor < len(corpus):
        chunks.append(corpus[cursor:])
    return [c for c in chunks if c.strip()]


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

# Hard safety ceiling on corpus size — only fires on pathological uploads
# (gigabyte-scale data rooms, accidental video transcripts, etc.) and
# truncates with an explicit marker. The router below sends any corpus
# above the model's window to extract-once-write-many BEFORE this cap is
# hit, so in normal operation no document content is ever silently
# discarded. 20MB ≈ 5M tokens — well above any realistic VC data room.
_CORPUS_HARD_CAP = 20_000_000

# Backward-compat alias retained in case downstream code references it.
# Functionally equivalent to _CORPUS_HARD_CAP after the routing rewrite.
_SECTION_CORPUS_CAP = _CORPUS_HARD_CAP

# Manifest pass model output budget. Claude has a hard cap (8192 for Sonnet
# 4.6) so we stay safely under it. The output is JSON, not prose, so this is
# more than enough for ~50 documents.
_MANIFEST_MAX_TOKENS = 6000

# Section writer output budget per section.
_SECTION_MAX_TOKENS = 3000

# Per-model corpus-size thresholds. Below the threshold a memo runs the
# cached single-call-per-section path (writer sees the full corpus on
# every section). At or above the threshold, the corpus exceeds what fits
# in one model call, so the memo routes to extract-once-write-many — a
# single map pass reads every chunk of the corpus into bullet buckets,
# then 14 parallel synth calls write each section from its bucket. The
# extract pass guarantees every byte of every document is read by the
# model exactly once; nothing is silently truncated.
#
# Sized for each model's context window, leaving headroom for system
# prompt (~700 tokens), section instructions (~3K), output budget (~3K),
# and tokenizer-variance margin:
#   • Claude (Sonnet 4.6 / Opus 4.7): 200K context → 700K chars (~175K tokens)
#   • Qwen (qwen-rfnt):              262K context → 800K chars (~200K tokens)
_CLAUDE_WINDOW_CHARS = 700_000
_QWEN_RFNT_WINDOW_CHARS = 800_000


def _model_window_chars(model: str) -> int:
    """Return the corpus-size threshold for `model`. Above this the memo
    routes to extract-once-write-many; below it, the cached single-call
    path runs."""
    if _is_refiant_model(model):
        return _QWEN_RFNT_WINDOW_CHARS
    return _CLAUDE_WINDOW_CHARS

# Concurrency caps for parallel section writing. The 14 section calls are
# independent (each takes the same corpus + a different section instruction),
# so they fan out across threads. Conservative defaults respect typical
# provider rate limits; SDK retries handle transient 429s.
_SECTION_PARALLELISM_CLAUDE = int(os.environ.get("VOLO_MEMO_PARALLELISM_CLAUDE", "4"))
_SECTION_PARALLELISM_REFIANT = int(os.environ.get("VOLO_MEMO_PARALLELISM_REFIANT", "4"))

# Concurrency cap for the extract step in the Qwen oversize-fallback path
# (extract-once-write-many). Each call processes a single corpus chunk
# (~175K tokens) and outputs categorized briefs; 4-way parallel is the
# practical sweet spot.
_REFIANT_EXTRACT_PARALLELISM = int(os.environ.get("VOLO_MEMO_PARALLELISM_REFIANT", "4"))


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


def _extract_manifest_json(reply: str) -> Optional[dict]:
    """Pull a JSON object out of an LLM reply, tolerating common formatting
    drift (markdown fences, leading/trailing prose, partial fences). Returns
    the parsed dict, or None if no valid JSON object can be recovered.

    Tries, in order: raw parse, fence-stripped parse, brace-balanced
    substring parse. Rejects non-dict top-level values (e.g. a JSON array)
    so downstream `.get(...)` calls can't AttributeError."""

    candidates = []

    stripped = reply.strip()
    if stripped:
        candidates.append(stripped)

    # Strip any markdown fences anywhere in the reply, not just at the start.
    fence_stripped = re.sub(r"```(?:json)?\s*|\s*```", "", stripped, flags=re.IGNORECASE).strip()
    if fence_stripped and fence_stripped != stripped:
        candidates.append(fence_stripped)

    # Brace-balanced extraction: find the first `{` that opens a complete
    # object. Handles prose before/after, including a partial fence the
    # regex above didn't catch.
    for source in (stripped, fence_stripped):
        start = source.find("{")
        if start == -1:
            continue
        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(source)):
            ch = source[i]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
            else:
                if ch == '"':
                    in_string = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        candidates.append(source[start : i + 1])
                        break

    for cand in candidates:
        try:
            parsed = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
        # JSON array / scalar at top level — the manifest schema is an
        # object, so reject and try the next candidate.

    return None


def _pass_manifest(client, model: str, raw_docs: list, memo_section_titles: list) -> tuple[dict, int, int]:
    """Stage 1: Single LLM call producing a JSON manifest of the whole data room.

    Returns (manifest_dict, tokens_in, tokens_out). On parse failure or LLM
    error returns a safe fallback manifest where every doc is classified by
    `_classify_default` so the rest of the pipeline still runs."""

    folder_tree = _build_folder_tree(raw_docs)
    # Refiant has a smaller window than Claude. Shrink the per-file preview
    # budget so the manifest call fits — system prompt + schema + overhead
    # claim ~12k chars, leaving the rest for previews. Claude path keeps
    # the full 180k budget unchanged.
    if _is_refiant_model(model):
        manifest_budget = max(20_000, _get_refiant_input_budget() - 12_000)
    else:
        manifest_budget = _MANIFEST_INPUT_BUDGET_CHARS
    previews = _build_doc_previews(raw_docs, manifest_budget)
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

    manifest = _extract_manifest_json(reply)
    if manifest is None:
        logger.error(
            "v2 manifest pass returned unparseable JSON; first 500 chars: %s",
            reply[:500],
        )
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
            # Doc was classified primary/reference but has no extractable text —
            # likely an image-only PDF or a sync that returned empty. Log so we
            # know what's missing from the corpus instead of failing silently.
            logger.warning(
                "memo_v2: dropping %r from corpus despite classification=%s "
                "(extracted_text is empty — likely image-only PDF or extraction failure)",
                name, cls,
            )
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

    # Hard safety ceiling — only fires on pathological uploads. The
    # orchestrator routes any corpus above the model's window to
    # extract-once-write-many before truncation would matter, so in
    # normal operation this branch never executes.
    if len(corpus) > _CORPUS_HARD_CAP:
        logger.warning(
            "v2 corpus exceeds hard safety cap (%d > %d chars); truncating. "
            "This is unusual — investigate the data room contents.",
            len(corpus), _CORPUS_HARD_CAP,
        )
        corpus = corpus[:_CORPUS_HARD_CAP] + f"\n\n[...corpus truncated at hard safety cap {_CORPUS_HARD_CAP:,} chars...]"

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


# ─────────────────────────────────────────────────────────────────────────────


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
    progress_cb: Optional[Callable[[int, str], None]] = None,
) -> dict:
    """Stage 3: Write a single memo section. The corpus is sent with
    cache_control so subsequent section calls hit the cache.

    Returns {text, tokens_in, tokens_out, cache_creation, cache_read}.

    Refiant branch: when the model is Refiant/Qwen and the corpus
    overflows its smaller context window, transparently switch to a
    map-reduce path (extract per-chunk briefs, then synthesize).
    Claude path is unchanged.
    """

    # ── Single-section safety fallback ──────────────────────────────────
    # Reached when this function is called directly (not via the batch
    # orchestrator) — i.e. revise-section / edit-section flows. The
    # batch orchestrator already routes oversize corpora to
    # extract-once-write-many. This guard handles the same case for
    # single-section callers, regardless of model: route through
    # extract-once with a one-element sections list. Same code path as
    # the batch orchestrator; preserves the "every byte read once"
    # guarantee. Threshold matches the orchestrator's cutoff for
    # consistency between callers.
    if len(corpus) > _model_window_chars(model):
        eo_results = _extract_once_write_many(
            client=client, model=model, sections=[section], corpus=corpus,
            citation_legend=citation_legend,
            template_sections={section["key"]: template_guidance},
            company_name=company_name, links=links,
            additional_instructions=additional_instructions,
            progress_cb=progress_cb, pct_start=0, pct_span=0,
        )
        extract_pass = eo_results.pop("_extract_pass_tokens", None)
        r = eo_results.get(section["key"]) or {
            "text": "*[Generation failed for this section: no result returned]*",
            "tokens_in": 0, "tokens_out": 0,
            "cache_creation": 0, "cache_read": 0,
        }
        # Roll the extract-pass token cost into the returned result so
        # callers see a complete accounting in the same shape as the
        # cached path.
        if extract_pass:
            r["tokens_in"] = r.get("tokens_in", 0) + extract_pass.get("tokens_in", 0)
            r["tokens_out"] = r.get("tokens_out", 0) + extract_pass.get("tokens_out", 0)
        return r

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
# Parallel section-writer orchestrator (Stage 3)
#
# Replaces the original sequential per-section loop. Routes each memo run by
# (model, corpus_size) — same logic for Claude and Qwen, only the per-model
# window threshold differs. The decision is purely about whether the corpus
# fits a single model call; nothing model-specific beyond that.
#
#   ── In-window corpus (corpus ≤ model window) ──
#     Cached single-call-per-section, parallelized.
#       • Claude: section #0 runs serially first to create the prompt
#         cache, then sections #1..N fan out in parallel and read the
#         cache (10% billing). 14× cache-creation cost is avoided.
#       • Qwen: no cache to warm, all sections fan out immediately. The
#         shim silently drops cache_control; Qwen receives the full
#         corpus + section instruction in one call. Each section sees
#         the entire data room, identical to Claude.
#     Wall-clock falls ~3–4× vs. sequential. Token usage unchanged for
#     Claude, modest improvement for Qwen (parallelism, no caching to
#     amortize). Output is byte-equivalent to the pre-PR path on Claude
#     because each section call is the same prompt.
#
#   ── Oversize corpus (corpus > model window) ──
#     Extract-once-write-many. The corpus is split into chunks; ONE map
#     pass reads every chunk exactly once and emits JSON-keyed bullets
#     for ALL sections at once. Then 14 parallel synth calls write each
#     section from its consolidated bullet bucket.
#       • Crucially: every byte of every document gets read by the model
#         in the extract pass. Nothing is silently truncated. The router
#         engages this path BEFORE the corpus would hit any safety cap.
#       • The synth step sees a comprehensive bullet brief of facts (not
#         raw documents). Slightly less rich than full-corpus access,
#         but every fact propagates through — this path is strictly
#         better than the prior behavior of silently truncating the tail
#         of the corpus at 600K chars.
#       • Used by both Claude and Qwen. Same code path; the only
#         difference is the threshold (Claude 700K chars, Qwen 800K).
# ─────────────────────────────────────────────────────────────────────────────


def _emit_section_progress(
    progress_cb: Optional[Callable[[int, str], None]],
    completed: int,
    total: int,
    pct_start: int,
    pct_span: int,
    label: str,
) -> None:
    """Push a progress update into the memo job's status dict.

    Safe to call from worker threads — `progress_cb` ultimately writes via
    `_MEMO_LOCK` (see _run_memo_background in routes/memo.py)."""
    if not progress_cb:
        return
    pct = pct_start + int((completed / max(1, total)) * pct_span)
    try:
        progress_cb(min(pct, pct_start + pct_span), f"{label} ({completed}/{total})")
    except Exception:
        pass


def _parallel_cached_section_writers(
    *,
    client,
    model: str,
    sections: list,
    corpus: str,
    citation_legend: str,
    template_sections: dict,
    company_name: str,
    links: list,
    additional_instructions: str,
    progress_cb: Optional[Callable[[int, str], None]],
    pct_start: int,
    pct_span: int,
) -> dict:
    """Run `_pass_section_writer_cached` for every section in `sections`,
    parallelized.

    For Claude: runs section #0 serially first so the prompt cache is
    created before any other call hits the API. Subsequent sections fan
    out and read the cache (10% billing) instead of each creating their
    own copy (which would 14× the cache-creation cost).

    For Qwen: no cache to warm; fans out everything immediately.

    Returns {section_key: result_dict} where each result_dict matches the
    shape returned by `_pass_section_writer_cached`."""
    is_refiant = _is_refiant_model(model)
    max_workers = _SECTION_PARALLELISM_REFIANT if is_refiant else _SECTION_PARALLELISM_CLAUDE
    n_total = len(sections)

    def _run_one(section_dict: dict) -> tuple[str, dict]:
        return section_dict["key"], _pass_section_writer_cached(
            client=client,
            model=model,
            section=section_dict,
            corpus=corpus,
            citation_legend=citation_legend,
            template_guidance=template_sections.get(section_dict["key"], ""),
            company_name=company_name,
            links=links,
            additional_instructions=additional_instructions,
        )

    results: dict = {}
    completed = 0

    if not is_refiant and sections:
        # Cache-warmup: section #0 alone first.
        _emit_section_progress(
            progress_cb, completed, n_total, pct_start, pct_span,
            "Seeding prompt cache",
        )
        try:
            sk, r = _run_one(sections[0])
            results[sk] = r
        except Exception as e:
            logger.exception(f"v2 section warmup crashed for {sections[0]['key']}: {e}")
            results[sections[0]["key"]] = {
                "text": f"*[Generation failed for this section: {str(e)[:200]}]*",
                "tokens_in": 0, "tokens_out": 0,
                "cache_creation": 0, "cache_read": 0,
            }
        completed += 1
        _emit_section_progress(
            progress_cb, completed, n_total, pct_start, pct_span,
            "Writing sections in parallel",
        )
        rest = sections[1:]
    else:
        rest = sections

    if rest:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = {ex.submit(_run_one, s): s for s in rest}
            for fut in as_completed(futs):
                s = futs[fut]
                try:
                    sk, r = fut.result()
                    results[sk] = r
                except Exception as e:
                    logger.exception(f"v2 parallel section crashed for {s['key']}: {e}")
                    results[s["key"]] = {
                        "text": f"*[Generation failed for this section: {str(e)[:200]}]*",
                        "tokens_in": 0, "tokens_out": 0,
                        "cache_creation": 0, "cache_read": 0,
                    }
                completed += 1
                _emit_section_progress(
                    progress_cb, completed, n_total, pct_start, pct_span,
                    "Writing sections in parallel",
                )

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Oversize-corpus path: extract-once-write-many
#
# Runs whenever the data room exceeds the chosen model's window. Used by
# both Claude and Qwen — the same code path because `client.messages.create`
# is identical for both via the Refiant shim. Guarantees every byte of
# every document is read by the model exactly once during the extract
# pass; nothing is silently truncated.
# ─────────────────────────────────────────────────────────────────────────────

# System prompt for the synthesis step in the extract-once-write-many path.
# Mirrors _SECTION_WRITER_SYSTEM_V2 in tone/structure, but sets the correct
# expectation about input shape — the model receives a curated bullet brief
# extracted from the data room, NOT the raw corpus. Same factual-accuracy
# rules apply, just framed for the brief-as-source case so the model doesn't
# expect documents that aren't there.
_SECTION_WRITER_SYSTEM_FROM_BRIEF = """You are VoLo Earth Ventures' Investment Committee memo writer.

You are writing ONE section of an investment memorandum. You have been given a CURATED BRIEF of every fact relevant to your section, extracted in a prior pass over the entire data room. The brief is exhaustive — if a fact is not in your brief, it is not in the data room. Use the brief faithfully; the memo should read like you have read every page (because the extractor has, for you).

CRITICAL — FACTUAL ACCURACY:
- ONLY state facts, names, numbers, dates, and claims that appear in the consolidated brief or the RVM report data.
- NEVER fabricate facility names, locations, dollar amounts, percentages, timelines, partnerships, or any other specifics.
- Every quantitative claim (dollar amount, percentage, date, capacity figure) MUST have a citation [n] (matching the citation index) or [RVM] (for the quantitative report). If you cannot cite it, do not write it.
- The brief preserves the original citation numbers — pass them through verbatim. Do not invent new citation numbers.
- When in doubt, be less specific rather than risk inventing details. If the brief is empty or thin for this section, mark the gap explicitly as a diligence follow-up rather than padding with invented content.

Rules:
1. Write in professional, data-driven prose — cite specific numbers, percentages, and dollar amounts FROM THE BRIEF ONLY
2. Be thorough but avoid padding — every sentence should add value
3. Balance the bull case and bear case — credibility comes from honest assessment, not advocacy
4. Use Markdown formatting: ### for sub-sections, **bold** for emphasis, bullet lists only for catalogs of discrete items
5. Do NOT include the section title as a header — it will be added automatically
6. Target 400-800 words per section (more for Financing Overview and Business Model, less for shorter sections)
7. Each section stands alone — do NOT reference other sections
8. If the brief genuinely lacks information for this section, explicitly note it as a diligence gap requiring follow-up — do NOT fill gaps with invented details
9. Open with a strong orienting statement that frames why this topic matters for the investment thesis
10. Connect every fact back to its investment implication — never leave data uninterpreted"""


_MULTISECTION_EXTRACT_SYSTEM = """You are a venture-capital diligence assistant.

You are given ONE chunk of a data room and a list of memo sections that need to be written. Your job is to extract every fact, quote, number, partnership, customer, risk, and quantitative datum from this chunk and bucket each one under the memo section(s) it is relevant to.

Rules:
- A fact may belong to MULTIPLE sections — repeat it under each section_key it serves.
- Each bullet starts with a citation [n] using the citation index, states the fact concisely (one sentence), and includes exact dollar amounts, percentages, and dates when present.
- Skip sections with no relevant facts (use an empty array []).
- Output ONLY valid JSON in the exact schema specified. Do not include markdown fences, prose, or commentary.

Output schema:
{"sections": {"<section_key>": ["[n] fact one", "[n] fact two", ...], ...}}

The output JSON MUST contain a key for every section_key listed in the user message, even if its array is empty."""


def _extract_multisection_brief(
    *,
    client,
    model: str,
    sections: list,
    chunk: str,
    chunk_idx: int,
    total_chunks: int,
    citation_legend: str,
    company_name: str,
) -> tuple[dict, int, int]:
    """Single map call over one corpus chunk: extract facts categorized by
    all memo sections at once.

    Returns ({section_key: [bullet, ...]}, tokens_in, tokens_out). On
    parse failure returns ({}, t_in, t_out) so the caller can keep going
    with whatever other chunks succeeded."""
    section_listing = "\n".join(
        f"- key=`{s['key']}` · title={s['title']!r} · purpose={s.get('guidance', '')[:300]}"
        for s in sections
    )
    section_keys = [s["key"] for s in sections]
    user_msg = (
        f"You are reading PART {chunk_idx + 1} of {total_chunks} of the data "
        f"room for {company_name or 'this company'}.\n\n"
        f"# CITATION INDEX (use these numbers when citing)\n{citation_legend}\n\n"
        f"# MEMO SECTIONS THAT NEED FACTS\n{section_listing}\n\n"
        f"# DATA ROOM CHUNK\n{chunk}\n\n"
        f"# YOUR TASK\nExtract every relevant fact from this chunk and bucket "
        f"each under the memo section_key(s) it serves. Output the JSON now. "
        f"It MUST contain these keys: {section_keys}"
    )
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=8000,
            system=_MULTISECTION_EXTRACT_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
        )
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        usage = resp.usage
        t_in = (usage.input_tokens if usage else 0) or 0
        t_out = (usage.output_tokens if usage else 0) or 0
    except Exception as e:
        logger.warning(
            "v2 multisection extract failed (chunk=%d/%d): %s",
            chunk_idx + 1, total_chunks, e,
        )
        return {}, 0, 0

    # Robust JSON parse: tolerate code fences, leading/trailing junk.
    parsed: dict = {}
    try:
        body = text
        if body.startswith("```"):
            body = re.sub(r"^```(?:json)?\s*", "", body)
            body = re.sub(r"\s*```$", "", body)
        m = re.search(r"\{.*\}", body, re.DOTALL)
        if m:
            obj = json.loads(m.group(0))
            sections_obj = obj.get("sections")
            if isinstance(sections_obj, dict):
                for sk in section_keys:
                    bullets = sections_obj.get(sk, [])
                    if isinstance(bullets, list):
                        parsed[sk] = [str(b).strip() for b in bullets if str(b).strip()]
    except Exception as e:
        logger.warning(
            "v2 multisection extract JSON parse failed (chunk=%d/%d): %s; first 300 chars: %s",
            chunk_idx + 1, total_chunks, e, text[:300],
        )
        return {}, t_in, t_out

    return parsed, t_in, t_out


def _synthesize_section_from_bullets(
    *,
    client,
    model: str,
    section: dict,
    bullets: list,
    citation_legend: str,
    template_guidance: str,
    company_name: str,
    links: list,
    additional_instructions: str,
) -> dict:
    """Reduce step: write one memo section from its consolidated bullet list.

    Same end output as the cached single-call path — the only difference
    is the writer sees a fact-bullet brief instead of the raw corpus."""
    consolidated = (
        "\n".join(bullets)
        if bullets
        else "(no relevant facts were extracted from the data room for this section)"
    )
    user_parts = [
        f"# CITATION INDEX\n{citation_legend}",
        f"# CONSOLIDATED BRIEF (every fact in the data room relevant to '{section['title']}')\n{consolidated}",
        f"# SECTION TO WRITE: {section['title']}",
        f"## Section Purpose\n{section['guidance']}",
    ]
    if template_guidance:
        user_parts.append(f"## Template Guidance for This Section\n{template_guidance}")
    if links:
        user_parts.append("## Reference Links\n" + "\n".join(f"- {l}" for l in links))
    if additional_instructions:
        user_parts.append(f"## Additional Instructions From the Analyst\n{additional_instructions}")
    user_parts.append(
        f"\nWrite the '{section['title']}' section of the investment memo for "
        f"{company_name or 'this company'}. Be thorough and data-driven. "
        "Cite your sources using [n] notation. Use [RVM] for quantitative report data."
    )
    user_msg = "\n\n".join(user_parts)

    try:
        resp = client.messages.create(
            model=model,
            max_tokens=_SECTION_MAX_TOKENS,
            system=_SECTION_WRITER_SYSTEM_FROM_BRIEF,
            messages=[{"role": "user", "content": user_msg}],
        )
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        usage = resp.usage
        return {
            "text": text,
            "tokens_in": (usage.input_tokens if usage else 0) or 0,
            "tokens_out": (usage.output_tokens if usage else 0) or 0,
            "cache_creation": 0,
            "cache_read": 0,
        }
    except Exception as e:
        logger.error(f"v2 synth from bullets failed for {section['key']}: {e}")
        return {
            "text": f"*[Generation failed for this section: {str(e)[:200]}]*",
            "tokens_in": 0,
            "tokens_out": 0,
            "cache_creation": 0,
            "cache_read": 0,
        }


def _extract_once_write_many(
    *,
    client,
    model: str,
    sections: list,
    corpus: str,
    citation_legend: str,
    template_sections: dict,
    company_name: str,
    links: list,
    additional_instructions: str,
    progress_cb: Optional[Callable[[int, str], None]],
    pct_start: int,
    pct_span: int,
) -> dict:
    """Oversize-corpus path: one parallel map pass over corpus chunks
    extracts facts for ALL sections at once, then parallel synthesis calls
    write each section from its bullet bucket.

    Used by both Claude and Qwen whenever the data room exceeds the
    chosen model's window. The map pass reads every byte of every
    document exactly once and buckets facts under the section_key(s)
    they serve — so no information is silently truncated. The synth
    step then writes each section from its consolidated bullet brief.

    A single-section direct caller (revise / edit flows) routes here too
    via `_pass_section_writer_cached`'s safety fallback, with a
    one-element sections list.

    Returns {section_key: result_dict} matching the cached writer's shape."""
    budget = _get_refiant_input_budget()
    chunk_target = max(20_000, budget - 12_000)
    chunks = _split_corpus_into_chunks(corpus, chunk_target)
    n_chunks = max(1, len(chunks))
    n_sections = len(sections)

    # Map split: progress 0–40% of the section span goes to extraction,
    # 40–100% to synthesis. Tweakable; keeps the bar moving smoothly.
    map_span = pct_span * 40 // 100
    reduce_span = pct_span - map_span

    extracted: dict = defaultdict(list)
    total_in = 0
    total_out = 0
    completed = 0

    _emit_section_progress(
        progress_cb, completed, n_chunks, pct_start, map_span,
        "Reading data room (single pass)",
    )

    def _run_extract(idx_chunk: tuple[int, str]):
        i, ch = idx_chunk
        return _extract_multisection_brief(
            client=client, model=model, sections=sections, chunk=ch,
            chunk_idx=i, total_chunks=n_chunks,
            citation_legend=citation_legend, company_name=company_name,
        )

    with ThreadPoolExecutor(max_workers=_REFIANT_EXTRACT_PARALLELISM) as ex:
        futs = [ex.submit(_run_extract, (i, ch)) for i, ch in enumerate(chunks)]
        for fut in as_completed(futs):
            try:
                parsed, t_in, t_out = fut.result()
            except Exception as e:
                logger.exception(f"extract worker crashed: {e}")
                parsed, t_in, t_out = {}, 0, 0
            for sk, bullets in parsed.items():
                extracted[sk].extend(bullets)
            total_in += t_in
            total_out += t_out
            completed += 1
            _emit_section_progress(
                progress_cb, completed, n_chunks, pct_start, map_span,
                "Reading data room (single pass)",
            )

    # Reduce: parallel synthesis from per-section bullet buckets.
    results: dict = {}
    completed = 0
    reduce_pct_start = pct_start + map_span

    _emit_section_progress(
        progress_cb, completed, n_sections, reduce_pct_start, reduce_span,
        "Writing sections in parallel",
    )

    def _run_synth(s: dict) -> tuple[str, dict]:
        return s["key"], _synthesize_section_from_bullets(
            client=client, model=model, section=s,
            bullets=extracted.get(s["key"], []),
            citation_legend=citation_legend,
            template_guidance=template_sections.get(s["key"], ""),
            company_name=company_name, links=links,
            additional_instructions=additional_instructions,
        )

    with ThreadPoolExecutor(max_workers=_SECTION_PARALLELISM_REFIANT) as ex:
        futs = {ex.submit(_run_synth, s): s for s in sections}
        for fut in as_completed(futs):
            s = futs[fut]
            try:
                sk, r = fut.result()
                results[sk] = r
                total_in += r["tokens_in"]
                total_out += r["tokens_out"]
            except Exception as e:
                logger.exception(f"synth worker crashed for {s['key']}: {e}")
                results[s["key"]] = {
                    "text": f"*[Generation failed for this section: {str(e)[:200]}]*",
                    "tokens_in": 0, "tokens_out": 0,
                    "cache_creation": 0, "cache_read": 0,
                }
            completed += 1
            _emit_section_progress(
                progress_cb, completed, n_sections, reduce_pct_start, reduce_span,
                "Writing sections in parallel",
            )

    # Return per-section results plus an aggregate-tokens overlay so the
    # orchestrator can attribute map-step tokens to a single pass_log entry.
    # The orchestrator pulls this via the special "_extract_pass_tokens" key
    # and removes it before iterating section results.
    if sections:
        synth_in = sum(r["tokens_in"] for r in results.values())
        synth_out = sum(r["tokens_out"] for r in results.values())
        results["_extract_pass_tokens"] = {
            "tokens_in": max(0, total_in - synth_in),
            "tokens_out": max(0, total_out - synth_out),
            "n_chunks": n_chunks,
        }

    return results


def _run_section_writers_batch(
    *,
    client,
    model: str,
    memo_sections: list,
    corpus: str,
    citation_legend: str,
    template_sections: dict,
    company_name: str,
    links: list,
    additional_instructions: str,
    locked: dict,
    progress_cb: Optional[Callable[[int, str], None]],
    pct_start: int = 15,
    pct_span: int = 75,
) -> tuple[dict, list, int, int]:
    """Top-level routing for Stage 3 section writing.

    Picks the right strategy based on model + corpus size, then returns
    (section_texts, pass_log_entries, total_tokens_in, total_tokens_out).
    Locked sections are passed through verbatim (no LLM call).

    Output ordering of section_texts and pass_log preserves the order of
    `memo_sections` regardless of which sections happen to finish first
    in the parallel pool."""
    pass_log: list = []
    section_texts: dict = {}
    locked = locked or {}

    # Pending = the sections that will actually hit the LLM. Locked
    # sections are inserted into final outputs further down, in memo
    # order, so pass_log keeps a linear timeline.
    pending = [s for s in memo_sections if s["key"] not in locked]

    if not pending:
        # All sections locked → populate verbatim and exit.
        for s in memo_sections:
            section_texts[s["key"]] = locked[s["key"]]
            pass_log.append({"stage": "section_locked", "section": s["key"]})
        return section_texts, pass_log, 0, 0

    # Routing decision: any corpus exceeding the model's window goes to
    # extract-once-write-many so every byte of every document is still
    # read at extract time. Below the threshold, the cached single-call
    # path runs (writer sees the full corpus on every section).
    window = _model_window_chars(model)
    use_extract_once = len(corpus) > window

    if use_extract_once:
        logger.info(
            "v2 routing: corpus %d chars > window %d for model %s — "
            "routing to extract-once-write-many to preserve full data room.",
            len(corpus), window, model,
        )
        results = _extract_once_write_many(
            client=client, model=model, sections=pending, corpus=corpus,
            citation_legend=citation_legend, template_sections=template_sections,
            company_name=company_name, links=links,
            additional_instructions=additional_instructions,
            progress_cb=progress_cb, pct_start=pct_start, pct_span=pct_span,
        )
    else:
        results = _parallel_cached_section_writers(
            client=client, model=model, sections=pending, corpus=corpus,
            citation_legend=citation_legend, template_sections=template_sections,
            company_name=company_name, links=links,
            additional_instructions=additional_instructions,
            progress_cb=progress_cb, pct_start=pct_start, pct_span=pct_span,
        )

    # Pull aside the extract-pass tokens (only present on the oversize path).
    # Logged before per-section entries since extract is a prerequisite of
    # all synth calls.
    extract_pass = results.pop("_extract_pass_tokens", None)
    if extract_pass:
        pass_log.append({
            "stage": "extract_pass_multisection",
            "n_chunks": extract_pass.get("n_chunks", 0),
            "tokens_in": extract_pass["tokens_in"],
            "tokens_out": extract_pass["tokens_out"],
        })

    # Single drain in original `memo_sections` order — produces a
    # deterministic section_texts dict and a pass_log timeline that
    # matches the on-page order of the memo, regardless of which
    # sections were locked or which finished first under parallelism.
    total_in = (extract_pass["tokens_in"] if extract_pass else 0)
    total_out = (extract_pass["tokens_out"] if extract_pass else 0)
    for s in memo_sections:
        sk = s["key"]
        if sk in locked:
            section_texts[sk] = locked[sk]
            pass_log.append({"stage": "section_locked", "section": sk})
            continue
        r = results.get(sk)
        if not r:
            # Shouldn't happen — guard for safety.
            section_texts[sk] = f"*[Generation failed for this section: no result returned]*"
            pass_log.append({
                "stage": "section", "section": sk,
                "tokens_in": 0, "tokens_out": 0,
                "cache_creation": 0, "cache_read": 0,
            })
            continue
        section_texts[sk] = r["text"]
        total_in += r["tokens_in"]
        total_out += r["tokens_out"]
        pass_log.append({
            "stage": "section",
            "section": sk,
            "tokens_in": r["tokens_in"],
            "tokens_out": r["tokens_out"],
            "cache_creation": r.get("cache_creation", 0),
            "cache_read": r.get("cache_read", 0),
        })

    return section_texts, pass_log, total_in, total_out


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
        _progress(8, "Reading through the data room...")
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

    # Section writing is parallelized inside the batch orchestrator. Routing
    # picks between (1) cached single-call-per-section with cache warmup for
    # Claude, (2) parallel cached single-call for Qwen with corpus-fits-window,
    # or (3) extract-once-write-many for Qwen with oversize corpus. All three
    # paths preserve the per-section quality of the legacy sequential loop.
    batch_texts, batch_pass_log, batch_in, batch_out = _run_section_writers_batch(
        client=client,
        model=model,
        memo_sections=memo_sections,
        corpus=corpus,
        citation_legend=citation_legend,
        template_sections=template_sections,
        company_name=company_name,
        links=links,
        additional_instructions=additional_instructions,
        locked=locked,
        progress_cb=progress_cb,
        pct_start=15,
        pct_span=75,
    )
    section_texts.update(batch_texts)
    pass_log.extend(batch_pass_log)
    total_in += batch_in
    total_out += batch_out

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
