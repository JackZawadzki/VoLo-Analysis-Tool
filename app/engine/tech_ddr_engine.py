"""
tech_ddr_engine.py
==================
AI analysis engine for the **Technical / Deep-Tech Due Diligence Report**.

Unlike the standard DDR (ddr_engine.py) — which is a single Claude + web_search
call oriented around claims verification and competitive landscape — this engine
is built for science-heavy deals in domains the investment team does NOT
specialize in. It:

  * reads each uploaded document IN FULL (no 60k-char truncation), including
    long research papers,
  * runs Claude **Opus** in **three passes**:
      Pass 1 — read every paper/deck and extract structured technical notes,
      Pass 2 — web_search to ground novelty, datasets, commercial + manufacturing
               context, and to curate a "Related Research" reading list,
      Pass 3 — synthesize the final report JSON,
  * treats the analyst's free-text *innovation hypothesis* as a claim to TEST,
    not to flatter.

COST + COMPLETION GUARANTEES (so a run can't hang or run away on spend):
  * Hard cumulative INPUT-token budget (the variable cost driver) — once hit,
    research stops and we synthesize with what we have.
  * Deterministic OUTPUT caps (bounded calls × max_tokens) — output cost is
    capped by construction.
  * web_search is capped via `max_uses`, and the agentic research loop is
    bounded by a fixed iteration ceiling.
  * An overall wall-clock DEADLINE with graceful degradation — partial passes
    still flow through to a synthesized report; Pass 3 always runs (it's one
    bounded call), so the job ALWAYS reaches a terminal state.
  * Bounded, short retry backoff so a flaky API can't stall the run.
Quality is prioritized UP TO the limits: the engine spends freely on thorough
analysis until it nears the ceilings. All ceilings are env-overridable
(TECH_DDR_*). Defaults keep a run <= ~$15 and well under 20 minutes (the route
adds a hard 20-minute backstop on top).

The standard DDR engine is left completely untouched.
"""

import json
import os
import re
import time

from pypdf import PdfReader
from anthropic import Anthropic, RateLimitError, APIStatusError

# ── Constants ────────────────────────────────────────────────────────────────

# Opus 4.8 — the latest Opus (what Claude Code itself runs on). Env-overridable
# so you can pin a different model (e.g. "claude-opus-4-7") without a code change
# or redeploy if a run ever 404s on the ID.
MODEL = os.environ.get("TECH_DDR_MODEL", "claude-opus-4-8")


def _envint(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


# ── Hard ceilings (quality prioritized UP TO these limits) ──────────────────
# Cost: stop OPTIONAL work (document reads beyond the first, web research) once
# the estimated spend would leave less than the reserve below — so the always-on
# synthesis call still fits. Default keeps a full run <= ~$15.
MAX_COST_USD = float(os.environ.get("TECH_DDR_MAX_COST_USD") or 15.0)
_COST_RESERVE_USD = 5.0   # headroom for the last in-flight call + synthesis
# Time: overall wall-clock budget. The engine degrades gracefully near this; the
# route enforces a HARD 20-minute stop on top (see routes/tech_ddr.py).
MAX_RUN_SECONDS = _envint("TECH_DDR_MAX_SECONDS", 1000)
_TIME_RESERVE_SECONDS = 210   # reserve for the always-on synthesis call

# Web search: cap searches (bounds per-search fees + the size of results fed
# back as input). The research loop is also bounded by a fixed iteration ceiling.
WEB_SEARCH_MAX_USES = _envint("TECH_DDR_WEB_SEARCH_MAX_USES", 10)
RESEARCH_MAX_ITERS = _envint("TECH_DDR_RESEARCH_MAX_ITERS", 8)

# Per-call output ceilings (USD-bounding: output is the pricey side).
PASS1_OUT_TOKENS = 4000
PASS2_OUT_TOKENS = 6000
PASS3_OUT_TOKENS = 16000   # the final report — generous, for a thorough document

# Input size guards. A single real research paper is ~30–80k chars and fits in
# one call (so it is read IN FULL). These caps only bite on pathological inputs
# (e.g. a whole thesis, or many large PDFs at once) and prevent runaway cost.
MAX_DOC_CHARS = _envint("TECH_DDR_MAX_DOC_CHARS", 300_000)          # per document
MAX_TOTAL_CORPUS_CHARS = _envint("TECH_DDR_MAX_CORPUS_CHARS", 900_000)  # all docs
_SINGLE_DOC_CALL_CHARS = 240_000   # read in one call below this
_DOC_CHUNK_CHARS = 200_000
MAX_DOC_CHUNKS = 2                  # cap calls for one oversized document

# Per-call network timeout + a short, BOUNDED retry backoff (seconds).
_CALL_TIMEOUT = 300.0
_BACKOFF_DELAYS = [10, 30, 60]

# Approximate Opus pricing for the in-run cost estimate/guard (USD per token).
_PRICE_IN = 15.0 / 1_000_000
_PRICE_OUT = 75.0 / 1_000_000

WEB_SEARCH_TOOL = {
    "type": "web_search_20250305",
    "name": "web_search",
    "max_uses": WEB_SEARCH_MAX_USES,
}

_SYSTEM = (
    "You are a senior technical due-diligence analyst at a deep-tech venture fund "
    "(VoLo Earth Ventures). You assess frontier science and engineering startups in "
    "domains the investment team does NOT specialize in — so your job is to explain "
    "the science clearly, establish what is genuinely novel versus prior published "
    "work, and surface the commercial and manufacturing realities. You are rigorous "
    "and evidence-driven: you cite sources, quantify with units, and you never "
    "flatter. You actively TEST claims — including the analyst's own hypothesis "
    "about the innovation — against the evidence, and you say plainly when the "
    "evidence is thin or the real contribution differs from what was expected."
)


# ── Cost / time budget tracker ───────────────────────────────────────────────

class _Budget:
    """Tracks token spend + wall-clock against the configured ceilings."""

    def __init__(self):
        self.deadline = time.time() + MAX_RUN_SECONDS
        self.tokens_in = 0
        self.tokens_out = 0
        self.calls = 0

    def record(self, response):
        u = getattr(response, "usage", None)
        if u:
            self.tokens_in += getattr(u, "input_tokens", 0) or 0
            self.tokens_out += getattr(u, "output_tokens", 0) or 0
        self.calls += 1

    def time_left(self) -> float:
        return self.deadline - time.time()

    def exhausted(self) -> bool:
        """True when we should stop spending on OPTIONAL work (extra document
        reads, web research). Leaves time + $ headroom for the synthesis call,
        which always runs so the job reaches a terminal state."""
        return (self.time_left() <= _TIME_RESERVE_SECONDS
                or self.est_cost() >= (MAX_COST_USD - _COST_RESERVE_USD))

    def est_cost(self) -> float:
        return self.tokens_in * _PRICE_IN + self.tokens_out * _PRICE_OUT

    def summary(self) -> dict:
        return {
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "calls": self.calls,
            "est_cost_usd": round(self.est_cost(), 2),
        }


# ── PDF Extraction (full text, no truncation) ────────────────────────────────

def extract_pdf_full(path: str) -> str:
    """Extract the COMPLETE text of a PDF. Unlike the standard DDR engine this
    does not cap at 60k characters — the whole paper is read."""
    reader = PdfReader(path)
    parts = []
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception:
            parts.append("")  # one bad page must not abort the document
    return "\n\n".join(parts)


# ── Anthropic call helpers (shared, bounded backoff) ─────────────────────────

def _create_with_backoff(client: Anthropic, **kwargs):
    """messages.create with a SHORT, BOUNDED backoff for rate limit / overload."""
    last_err = None
    for attempt in range(4):
        try:
            return client.messages.create(**kwargs)
        except RateLimitError as e:
            last_err = e
            if attempt < 3:
                time.sleep(_BACKOFF_DELAYS[min(attempt, len(_BACKOFF_DELAYS) - 1)])
            else:
                raise RuntimeError("API rate limit exceeded after retries.")
        except APIStatusError as e:
            last_err = e
            if e.status_code == 529 and attempt < 3:
                time.sleep(_BACKOFF_DELAYS[min(attempt, len(_BACKOFF_DELAYS) - 1)])
            elif "token" in str(e).lower() or "credit" in str(e).lower():
                raise RuntimeError(
                    "API token/credit error. Check that ANTHROPIC_API_KEY has "
                    "sufficient credits and is not expired."
                )
            else:
                raise
    if last_err:
        raise last_err
    raise RuntimeError("Anthropic call failed without a specific error.")


def _last_text(response) -> str:
    text = ""
    for block in response.content:
        if hasattr(block, "text"):
            text = block.text
    return text


def _plain_call(client: Anthropic, prompt: str, max_tokens: int, budget: _Budget,
                temperature: float = 0.2) -> str:
    """A single, tool-free Opus call (records token usage)."""
    resp = _create_with_backoff(
        client, model=MODEL, max_tokens=max_tokens, temperature=temperature,
        system=_SYSTEM, messages=[{"role": "user", "content": prompt}],
    )
    budget.record(resp)
    return _last_text(resp)


def _search_call(client: Anthropic, prompt: str, max_tokens: int, budget: _Budget,
                 temperature: float = 0.3, on_search=None) -> str:
    """An agentic Opus + web_search call. Bounded by RESEARCH_MAX_ITERS, the
    web_search max_uses, and the token/time budget."""
    messages = [{"role": "user", "content": prompt}]
    final_text = ""
    for _ in range(RESEARCH_MAX_ITERS):
        if budget.exhausted():
            break
        resp = _create_with_backoff(
            client, model=MODEL, max_tokens=max_tokens, temperature=temperature,
            system=_SYSTEM, tools=[WEB_SEARCH_TOOL], messages=messages,
        )
        budget.record(resp)
        final_text = _last_text(resp) or final_text

        tool_calls = [b for b in resp.content
                      if getattr(b, "type", None) in ("tool_use", "server_tool_use")]
        if on_search and tool_calls:
            on_search(len(tool_calls))

        if resp.stop_reason == "end_turn":
            break

        messages.append({"role": "assistant", "content": resp.content})
        tool_results = [
            {"type": "tool_result", "tool_use_id": b.id, "content": ""}
            for b in resp.content if getattr(b, "type", None) == "tool_use"
        ]
        if tool_results:
            messages.append({"role": "user", "content": tool_results})
        else:
            break
    return final_text


# ── Robust JSON extraction ───────────────────────────────────────────────────

def _extract_json(raw_text: str) -> dict:
    raw = (raw_text or "").strip()
    raw = re.sub(r"```[a-z]*\s*\n?", "", raw)
    raw = re.sub(r"\n?\s*```", "", raw)

    match = re.search(r"\{[\s\S]*\}", raw)
    if not match:
        return {"error": "No JSON found in response", "raw": raw[:2000]}
    fragment = match.group()

    def _clean(s: str) -> str:
        return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", s)

    for candidate in (fragment, _clean(fragment)):
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            pass

    try:
        cleaned = _clean(fragment)
        open_b = cleaned.count("{") - cleaned.count("}")
        open_a = cleaned.count("[") - cleaned.count("]")
        patched = cleaned + ("]" * max(open_a, 0)) + ("}" * max(open_b, 0))
        return json.loads(patched)
    except (json.JSONDecodeError, ValueError):
        pass

    try:
        depth = 0
        end = 0
        for i, ch in enumerate(fragment):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end:
            return json.loads(_clean(fragment[:end]))
    except (json.JSONDecodeError, ValueError):
        pass

    return {"error": "JSON parse failed", "raw": raw_text[:2000]}


def _chunk(text: str, size: int) -> list:
    return [text[i:i + size] for i in range(0, len(text), size)] or [""]


# ── Pass 1: read each document in full ───────────────────────────────────────

_READ_PROMPT = """Read the following document IN FULL and extract structured technical notes for a deep-tech due-diligence report. The document may be a peer-reviewed paper, preprint, technical report, patent, or pitch deck.

Be precise and concrete:
- Capture quantitative results WITH units and conditions.
- Record the specific prior work / references the document builds on or compares against (author/year or title where given) — this is essential for a later novelty assessment.
- Note domain-specific terms a non-expert reader would need defined.
- Do NOT speculate beyond the document; this pass is pure comprehension.

{part_note}Document filename: {filename}

<document>
{doc_text}
</document>

Return JSON only, no prose, no markdown fences:
{{
  "doc_type": "research_paper | preprint | technical_report | patent | pitch_deck | other",
  "title": "best title for this document",
  "authors": "authors/affiliations if present, else empty",
  "year": "publication/created year if present, else empty",
  "core_contribution": "1-2 paragraphs: the central technical contribution or what the company claims to have built/discovered",
  "methods": "key methods, materials, architecture, or approach used",
  "key_results": [
    {{"result": "what was measured/achieved", "value": "number + unit", "conditions": "test conditions/caveats"}}
  ],
  "claims": ["notable explicit claims made in the document"],
  "prior_work_cited": ["specific prior approaches/papers referenced, with author/year or title if available"],
  "key_terms": [{{"term": "domain term", "plain_meaning": "short plain-language definition"}}],
  "data_availability": "any datasets, code, or supplementary data the document references (with names/links if given)",
  "limitations_stated": ["limitations or open problems the authors acknowledge"]
}}"""


def _read_document(client: Anthropic, filename: str, text: str, budget: _Budget) -> dict:
    text = text or ""
    if len(text) <= _SINGLE_DOC_CALL_CHARS:
        raw = _plain_call(
            client,
            _READ_PROMPT.format(part_note="", filename=filename, doc_text=text),
            max_tokens=PASS1_OUT_TOKENS, budget=budget, temperature=0.1,
        )
        notes = _extract_json(raw)
        notes.setdefault("filename", filename)
        return notes

    # Oversized single document: read in a CAPPED number of chunks, then merge
    # so nothing within the cap is dropped. (Per-doc text is already capped to
    # MAX_DOC_CHARS upstream, so this is at most MAX_DOC_CHUNKS calls.)
    chunks = _chunk(text, _DOC_CHUNK_CHARS)[:MAX_DOC_CHUNKS]
    partial = []
    for i, ch in enumerate(chunks):
        if budget.exhausted():
            break
        part_note = (
            f"NOTE: This is part {i + 1} of {len(chunks)} of one large document; "
            f"extract notes from this part.\n\n"
        )
        raw = _plain_call(
            client,
            _READ_PROMPT.format(part_note=part_note, filename=filename, doc_text=ch),
            max_tokens=PASS1_OUT_TOKENS, budget=budget, temperature=0.1,
        )
        partial.append(_extract_json(raw))

    if not partial:
        return {"filename": filename, "title": filename,
                "core_contribution": "(not read — budget reached)"}

    merged = {
        "filename": filename,
        "doc_type": partial[0].get("doc_type", "other"),
        "title": partial[0].get("title", filename),
        "authors": partial[0].get("authors", ""),
        "year": partial[0].get("year", ""),
        "core_contribution": " ".join(p.get("core_contribution", "") for p in partial if p.get("core_contribution")),
        "methods": " ".join(p.get("methods", "") for p in partial if p.get("methods")),
        "key_results": [r for p in partial for r in (p.get("key_results") or [])],
        "claims": [c for p in partial for c in (p.get("claims") or [])],
        "prior_work_cited": [w for p in partial for w in (p.get("prior_work_cited") or [])],
        "key_terms": [t for p in partial for t in (p.get("key_terms") or [])],
        "data_availability": " ".join(p.get("data_availability", "") for p in partial if p.get("data_availability")),
        "limitations_stated": [l for p in partial for l in (p.get("limitations_stated") or [])],
    }
    return merged


# ── Pass 2: ground in the literature + market via web_search ─────────────────

_RESEARCH_PROMPT = """You are grounding a technical due-diligence report in the external scientific literature and the market. Use web_search to find REAL, verifiable information — do up to {max_searches} targeted searches and cite what you find. Do not invent sources, numbers, or paper titles.

THE ANALYST'S FRAME OF REFERENCE (the commercial/application lens — orient ALL of your research around it; the papers are the raw science, this frame is what the technology is being commercialized FOR):
{innovation_hint}

STRUCTURED NOTES from the uploaded document(s) (the raw science):
{doc_notes}

Your research goals (focus the commercial, applications, and manufacturing research on the analyst's frame above, while grounding novelty in the science):
1. NOVELTY — find prior published work and competing technical approaches so a novelty assessment can be made. Who else has worked on this? What are the established methods and their limits?
2. DATA & BENCHMARKS — find public datasets, benchmarks, or standard figures of merit relevant to these claims.
3. COMMERCIAL — find the commercial landscape: companies pursuing this (with stage/funding if available), end markets, and rough market sizing from reputable sources.
4. MANUFACTURING — find what is known about manufacturing/scale-up for this class of technology (process maturity, cost drivers, supply chain, known bottlenecks).
5. RELATED RESEARCH — curate 5-10 HIGH-QUALITY papers (peer-reviewed or reputable preprints) that a diligencer should read to understand this subject more deeply. Provide titles and links/DOIs where findable. These are a READING LIST, distinct from the sources you used.

Return JSON only, no prose, no markdown fences:
{{
  "field_overview": "1-2 paragraphs orienting a non-expert in this scientific/engineering field",
  "prior_approaches": [
    {{"approach": "established/competing approach", "who": "groups/companies/authors", "limitation": "its key limitation", "source": "url or publication"}}
  ],
  "novelty_findings": "what the external literature suggests is genuinely new vs. already known about the subject — be specific and honest",
  "competing_companies": [
    {{"name": "real company", "stage": "stage/funding if known", "what": "what they do", "source": "url"}}
  ],
  "datasets_benchmarks": [
    {{"name": "dataset/benchmark", "description": "what it is", "link": "url if available"}}
  ],
  "commercial_context": "markets, applications, and sizing with sources",
  "manufacturing_context": "scale-up maturity, cost drivers, supply chain, bottlenecks with sources",
  "related_research": [
    {{"title": "paper title", "authors": "authors", "venue_year": "venue + year", "link": "doi/url if findable", "why_relevant": "one line on why a diligencer should read it"}}
  ],
  "sources": ["every url/source you actually used in this research"]
}}"""


def _research(client: Anthropic, innovation_hint: str, doc_notes: list,
              budget: _Budget, on_search=None) -> dict:
    prompt = _RESEARCH_PROMPT.format(
        max_searches=WEB_SEARCH_MAX_USES,
        innovation_hint=(innovation_hint.strip() or "(none provided — infer the innovation from the documents)"),
        doc_notes=json.dumps(doc_notes, ensure_ascii=False)[:120_000],
    )
    raw = _search_call(client, prompt, max_tokens=PASS2_OUT_TOKENS, budget=budget,
                       temperature=0.3, on_search=on_search)
    return _extract_json(raw) if raw else {}


# ── Pass 3: synthesize the final report ──────────────────────────────────────

_SYNTH_PROMPT = """Synthesize the FINAL Technical Due Diligence Report as a single JSON object. You are writing for an engineer or scientist who is NOT a specialist in this domain.

INPUTS
------
>>> THE ANALYST'S FRAME OF REFERENCE — READ FIRST; IT IS THE LENS FOR THE ENTIRE REPORT <<<
{innovation_hint}

CRITICAL: The uploaded papers are the raw science/discovery ONLY — on its own a scientific paper is just a paper. The analyst's frame above tells you WHAT THIS TECHNOLOGY APPLIES TO and the commercial thesis you are diligencing. ANCHOR the whole report in that frame: the innovation framing, the Applications, and the Commercial Implications must be built around what the analyst says it applies to — the science is the evidence used to SUPPORT, QUANTIFY, or (honestly) CHALLENGE that commercial framing. Do not reframe the deal around the paper's own stated purpose; reframe the paper's science around the analyst's commercial frame. If the science does not actually support the framing, say so plainly — but the analyst's frame is your starting point and orientation. (If no frame was provided, infer the most credible commercial application from the documents and state that you did so.)

Document notes (Pass 1 — the raw science):
{doc_notes}

External research (Pass 2):
{research}

REQUIREMENTS
------------
- ANCHOR everything in the analyst's frame of reference above: it defines the application and commercial thesis. Frame the innovation, applications, and commercial implications around it; use the papers' science as the supporting/contradicting evidence.
- Explain the technology in plain language first (use analogies and fundamentals), then add a deeper technical layer.
- Make the novelty assessment concrete and cite specific prior work from the research.
- Rate evidence strength honestly; flag what still needs independent verification.
- Cover commercial implications AND manufacturing scale-up / commercialization risk with severity-rated risks.
- Keep IC-grade rigor: include a claims table and a competitive landscape.
- Carry through 5-10 Related Research papers (with links where available). This reading list is SEPARATE from citations — do not merge them.
- Every non-trivial external statement should carry a "source". Do not fabricate sources or numbers. If external research was limited, say so and lean on the documents.

Return JSON only, no prose, no markdown fences:
{{
  "company_name": "subject company or technology name",
  "field": "scientific/engineering field",
  "innovation_summary": {{
    "one_liner": "the innovation in one sentence a non-expert can grasp, expressed in terms of the analyst's commercial frame",
    "inferred_innovation": "1-2 paragraphs: what the discovery is (from the papers) AND what it applies to per the analyst's frame of reference",
    "analyst_hypothesis": "restate the analyst's frame of reference / commercial thesis (or 'none provided')",
    "hypothesis_assessment": "MATCHES | EXCEEDS | PARTIALLY SUPPORTED | DIFFERS | UNVERIFIABLE",
    "hypothesis_explanation": "honest explanation of whether and how the science supports the analyst's commercial frame"
  }},
  "technology_explainer": {{
    "plain_language": "explain it to a smart engineer/scientist outside this domain",
    "technical_depth": "the deeper mechanism / physics / chemistry / architecture",
    "key_terms": [{{"term": "term", "definition": "plain definition"}}]
  }},
  "how_it_works": {{"summary": "overview", "steps": ["mechanism/step 1", "step 2"]}},
  "novelty_vs_prior_work": {{
    "summary": "overall novelty verdict",
    "whats_genuinely_new": ["specific novel element 1"],
    "comparisons": [
      {{"prior_approach": "named prior approach", "how_this_differs": "specific difference", "advantage": "if any", "source": "url/citation"}}
    ]
  }},
  "evidence_and_data": {{
    "key_results": [
      {{"claim": "result/claim", "evidence": "what supports it (and from which document/source)", "strength": "STRONG | MODERATE | WEAK", "source": "url/citation or document"}}
    ],
    "datasets": [{{"name": "dataset", "description": "what it is", "link": "url"}}],
    "open_questions": ["what still needs independent verification"]
  }},
  "commercial_implications": {{
    "summary": "commercial thesis",
    "applications": [{{"application": "use case", "market": "end market", "notes": "context"}}],
    "market_context": "sizing/dynamics with sources",
    "comparable_companies": [{{"name": "company", "context": "comparison", "valuation_or_revenue": "if known"}}]
  }},
  "manufacturing_scaleup_risk": {{
    "scale_up_path": "lab -> pilot -> commercial path as understood",
    "readiness": "maturity assessment (e.g. TRL or lab/pilot/commercial)",
    "key_risks": [{{"risk": "specific risk", "severity": "HIGH | MEDIUM | LOW", "mitigation": "possible mitigation"}}]
  }},
  "claims": [
    {{"type": "TECHNOLOGY | MARKET | MANUFACTURING | IP | TEAM", "claim": "claim", "verification_status": "VERIFIED | PARTIALLY VERIFIED | UNVERIFIED", "source_label": "COMPANY CLAIM (Unverified) | VERIFIED: [Source]", "what_needs_investigation": "concrete next step", "sources": ["source"]}}
  ],
  "competitive_landscape": {{
    "positioning_summary": "how the subject is positioned",
    "peer_competitors": [{{"name": "company", "stage": "stage", "description": "overlap + edge", "sources": ["source"]}}],
    "market_leaders": [{{"name": "incumbent", "market_position": "position", "description": "threat", "sources": ["source"]}}],
    "competitive_risks": ["risk"],
    "potential_acquirers": ["who and why"]
  }},
  "related_research": [
    {{"title": "paper title", "authors": "authors", "venue_year": "venue + year", "link": "doi/url", "why_relevant": "why read it"}}
  ],
  "conclusion": {{"summary": "balanced closing assessment", "what_must_be_proven": ["the key things to validate next"]}},
  "sources_consulted": 0
}}"""


def _synthesize(client: Anthropic, innovation_hint: str, doc_notes: list,
                research: dict, budget: _Budget) -> dict:
    prompt = _SYNTH_PROMPT.format(
        innovation_hint=(innovation_hint.strip() or "(none provided — infer the innovation from the documents)"),
        doc_notes=json.dumps(doc_notes, ensure_ascii=False)[:90_000],
        research=json.dumps(research, ensure_ascii=False)[:90_000],
    )
    raw = _plain_call(client, prompt, max_tokens=PASS3_OUT_TOKENS, budget=budget,
                      temperature=0.3)
    return _extract_json(raw)


def _partial_report(doc_notes: list, research: dict, innovation_hint: str,
                    reason: str) -> dict:
    """Best-effort report assembled from the document reads + research gathered
    BEFORE a failure, so a run never loses the work it already paid for. Renders
    through the same PDF generator, flagged as a PARTIAL report."""
    research = research if isinstance(research, dict) else {}
    docs = doc_notes or []
    title = next((d.get("title") for d in docs if d.get("title")), "") or "Technical DDR (Partial)"
    competing = research.get("competing_companies") or []
    return {
        "partial": True,
        "partial_reason": reason,
        "company_name": title,
        "field": (research.get("field_overview", "") or "")[:160],
        "innovation_summary": {
            "one_liner": "",
            "inferred_innovation": " ".join(
                d.get("core_contribution", "") for d in docs if d.get("core_contribution")
            )[:4000],
            "analyst_hypothesis": (innovation_hint or "").strip() or "(none provided)",
            "hypothesis_assessment": "UNVERIFIABLE",
            "hypothesis_explanation": (
                "Final synthesis did not complete, so the science was not fully "
                "evaluated against the analyst's commercial frame. The sections "
                "below are assembled directly from the document reads and the "
                "external research gathered so far."
            ),
        },
        "technology_explainer": {
            "plain_language": "",
            "technical_depth": " ".join(
                d.get("methods", "") for d in docs if d.get("methods")
            )[:4000],
            "key_terms": [t for d in docs for t in (d.get("key_terms") or [])][:30],
        },
        "how_it_works": {},
        "novelty_vs_prior_work": {
            "summary": research.get("novelty_findings", "") or "",
            "whats_genuinely_new": [],
            "comparisons": [
                {"prior_approach": a.get("approach", ""),
                 "how_this_differs": a.get("limitation", ""),
                 "source": a.get("source", "")}
                for a in (research.get("prior_approaches") or [])
            ],
        },
        "evidence_and_data": {
            "key_results": [
                {"claim": r.get("result", ""), "evidence": r.get("conditions", ""),
                 "strength": "", "source": d.get("filename", "")}
                for d in docs for r in (d.get("key_results") or [])
            ][:40],
            "datasets": research.get("datasets_benchmarks", []) or [],
            "open_questions": [l for d in docs for l in (d.get("limitations_stated") or [])][:20],
        },
        "commercial_implications": {
            "summary": research.get("commercial_context", "") or "",
            "applications": [],
            "market_context": "",
            "comparable_companies": [
                {"name": c.get("name", ""), "context": c.get("what", ""),
                 "valuation_or_revenue": c.get("stage", "")}
                for c in competing
            ],
        },
        "manufacturing_scaleup_risk": {
            "scale_up_path": research.get("manufacturing_context", "") or "",
            "readiness": "",
            "key_risks": [],
        },
        "claims": [],
        "competitive_landscape": {
            "positioning_summary": "",
            "peer_competitors": [
                {"name": c.get("name", ""), "stage": c.get("stage", ""),
                 "description": c.get("what", ""), "sources": [c.get("source", "")]}
                for c in competing
            ],
            "market_leaders": [],
            "competitive_risks": [],
            "potential_acquirers": [],
        },
        "related_research": research.get("related_research", []) or [],
        "conclusion": {
            "summary": (
                f"PARTIAL REPORT — the final synthesis did not complete ({reason}). "
                "Everything above was assembled directly from the document reads and "
                "the external research already gathered, so the diligence work is "
                "preserved; re-run to produce the fully synthesized report."
            ),
            "what_must_be_proven": [],
        },
        "sources_consulted": len(set(research.get("sources", []) or [])),
    }


# ── Public API ───────────────────────────────────────────────────────────────

def analyze_tech(api_key: str, docs: list, innovation_hint: str = "",
                 progress=None) -> dict:
    """Run the 3-pass technical DDR analysis under hard cost + time ceilings.

    Args:
      api_key: Anthropic API key.
      docs: list of {"filename": str, "text": str} — already-extracted documents.
      innovation_hint: the analyst's free-text hypothesis about the innovation.
      progress: optional callable(pct:int, msg:str) for UI progress.

    Returns the final report dict (see _SYNTH_PROMPT schema) with an attached
    "_usage" block, or {"error": ...}. Always returns within the configured
    wall-clock budget (graceful degradation; Pass 3 always runs).
    """
    def _p(pct, msg):
        if progress:
            try:
                progress(pct, msg)
            except Exception:
                pass

    # Assemble + CAP the corpus (per-doc and total). Real papers pass through
    # whole; only pathological inputs are trimmed (flagged via "truncated").
    capped, total, truncated = [], 0, False
    for d in (docs or []):
        t = (d.get("text") or "").strip()
        if not t:
            continue
        if len(t) > MAX_DOC_CHARS:
            t, truncated = t[:MAX_DOC_CHARS], True
        if total + len(t) > MAX_TOTAL_CORPUS_CHARS:
            room = MAX_TOTAL_CORPUS_CHARS - total
            if room <= 2000:
                truncated = True
                break
            t, truncated = t[:room], True
        capped.append({"filename": d.get("filename", "document.pdf"), "text": t})
        total += len(t)
    docs = capped
    if not docs:
        return {"error": "No readable text found in the uploaded document(s)."}

    client = Anthropic(api_key=api_key, max_retries=1, timeout=_CALL_TIMEOUT)
    budget = _Budget()

    # ── Pass 1: read each document in full (skips remaining docs if budget runs
    #            out; a single doc failing must not kill the run).
    n = len(docs)
    doc_notes = []
    for i, d in enumerate(docs):
        if budget.exhausted():
            break
        _p(15 + int(35 * i / n), f"Reading document {i + 1}/{n}: {d['filename']}")
        try:
            doc_notes.append(_read_document(client, d["filename"], d["text"], budget))
        except Exception as e:
            print(f"[TechDDR] read failed for {d['filename']}: {e}", flush=True)
    if not doc_notes:
        return {"error": "Could not read any document before the time/cost budget was reached."}

    # ── Pass 2: external grounding (OPTIONAL — skipped if budget exhausted).
    research = {}
    if budget.exhausted():
        _p(52, "Budget reached — skipping external research; synthesizing from the documents...")
    else:
        _p(52, f"Documents read. Researching prior work and the market ({len(doc_notes)} docs)...")
        search_seen = {"n": 0}

        def _on_search(k):
            search_seen["n"] += k
            _p(min(54 + search_seen["n"] * 2, 72),
               f"Researching literature, datasets & related work ({search_seen['n']} searches)...")

        try:
            research = _research(client, innovation_hint, doc_notes, budget, on_search=_on_search)
        except Exception as e:
            print(f"[TechDDR] research pass failed: {e}", flush=True)
            research = {}

    # ── Pass 3: synthesize. ALWAYS attempted (one bounded call). If it fails or
    #            exceptions, FALL BACK to a partial report assembled from the doc
    #            reads + research already gathered — the work is never lost.
    _p(74, "Synthesizing the technical due-diligence report...")
    report = None
    try:
        report = _synthesize(client, innovation_hint, doc_notes, research, budget)
    except Exception as e:
        print(f"[TechDDR] synthesis call failed: {e}", flush=True)
    if not report or report.get("error"):
        reason = (report or {}).get("error") or "the final synthesis step did not complete"
        print(f"[TechDDR] building PARTIAL report ({reason})", flush=True)
        report = _partial_report(doc_notes, research, innovation_hint, reason)
        _p(86, "Synthesis incomplete — saving a partial report from the work gathered...")

    report.setdefault("_research_sources",
                      research.get("sources", []) if isinstance(research, dict) else [])
    report["_doc_filenames"] = [d["filename"] for d in docs]
    report["_usage"] = {**budget.summary(), "docs_submitted": n,
                        "docs_read": len(doc_notes), "input_truncated": truncated}
    if not report.get("sources_consulted"):
        report["sources_consulted"] = len(set(report.get("_research_sources") or []))

    print(f"[TechDDR] usage: in={budget.tokens_in} out={budget.tokens_out} "
          f"calls={budget.calls} est=${budget.est_cost():.2f} "
          f"partial={bool(report.get('partial'))} time_left={int(budget.time_left())}s", flush=True)
    _p(88, "Partial report ready." if report.get("partial") else "Report ready.")
    return report
