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

# Opus 4.8 — the latest Opus (what Claude Code itself runs on). Env-overridable.
# If the API rejects this ID (e.g. the account/SDK doesn't expose 4.8 yet), the
# engine probes at run start and AUTO-FALLS-BACK to FALLBACK_MODEL — so a run
# always uses the best AVAILABLE Opus instead of dying on an unknown model name.
MODEL = os.environ.get("TECH_DDR_MODEL", "claude-opus-4-8")
FALLBACK_MODEL = os.environ.get("TECH_DDR_FALLBACK_MODEL", "claude-opus-4-7")


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
    "You are a technical due-diligence analyst at a venture fund (VoLo Earth "
    "Ventures) preparing a REPEATABLE diligence brief on a deep-tech deal that is "
    "often OUTSIDE the firm's core expertise. A FOUNDER has come to the firm with "
    "(a) their own research paper(s) and (b) a claim about the product/company they "
    "intend to build from that research. Your job is to get an investor quickly and "
    "accurately ACQUAINTED with the technology and to frame the diligence — NOT to "
    "perform academic peer review, and NEVER to make an investment recommendation. "
    "You explain what it is, how it works (for a smart non-specialist), how it "
    "compares to prior work, what literature / datasets / patents / benchmarks to "
    "evaluate it against, what it could become commercially, the scale-up risks, "
    "and exactly what evidence to request next.\n\n"
    "Principles: prioritize practical venture diligence over academic critique; do "
    "NOT be dismissive just because a theory is abstract or early; do NOT accept a "
    "theory-to-product leap without evidence; always distinguish 'interesting "
    "science' from 'commercially validated technology'; when the deal is outside "
    "your expertise, FIRST identify the relevant published literature, datasets and "
    "benchmarks, then reason. Be neutral, specific, and evidence-driven; cite "
    "sources with units. NEVER give an investment recommendation or verdict (no "
    "'pass / monitor / invest', no 'we should…', no buy/sell framing) — the brief "
    "exists to inform and to frame the diligence, not to advise."
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


def _resolve_model(client: Anthropic, preferred: str, fallback: str) -> str:
    """Probe the preferred model with a 1-token call; if the API rejects the ID
    (model not found / no access), fall back. Costs a fraction of a cent and
    guarantees the run uses the best AVAILABLE Opus instead of failing on an
    unknown model name."""
    if not fallback or fallback == preferred:
        return preferred
    try:
        client.messages.create(
            model=preferred, max_tokens=1,
            messages=[{"role": "user", "content": "ok"}],
        )
        return preferred
    except APIStatusError as e:
        if getattr(e, "status_code", None) in (403, 404):
            print(f"[TechDDR] model '{preferred}' unavailable ({e.status_code}); "
                  f"using fallback '{fallback}'", flush=True)
            return fallback
        return preferred
    except Exception as e:
        msg = str(e).lower()
        if "model" in msg and ("not_found" in msg or "not found" in msg or "404" in msg):
            print(f"[TechDDR] model '{preferred}' not found; using fallback '{fallback}'", flush=True)
            return fallback
        return preferred


def _last_text(response) -> str:
    text = ""
    for block in response.content:
        if hasattr(block, "text"):
            text = block.text
    return text


def _plain_call(client: Anthropic, prompt: str, max_tokens: int, budget: _Budget,
                model: str = MODEL) -> str:
    """A single, tool-free Opus call (records token usage). NOTE: `temperature`
    is intentionally not sent — Opus 4.8 deprecated it (returns 400)."""
    resp = _create_with_backoff(
        client, model=model, max_tokens=max_tokens,
        system=_SYSTEM, messages=[{"role": "user", "content": prompt}],
    )
    budget.record(resp)
    return _last_text(resp)


def _search_call(client: Anthropic, prompt: str, max_tokens: int, budget: _Budget,
                 model: str = MODEL, on_search=None, link_sink=None) -> str:
    """An agentic Opus + web_search call. Bounded by RESEARCH_MAX_ITERS, the
    web_search max_uses, and the token/time budget. (`temperature` is not sent —
    Opus 4.8 deprecated it.)"""
    messages = [{"role": "user", "content": prompt}]
    final_text = ""
    for _ in range(RESEARCH_MAX_ITERS):
        if budget.exhausted():
            break
        resp = _create_with_backoff(
            client, model=model, max_tokens=max_tokens,
            system=_SYSTEM, tools=[WEB_SEARCH_TOOL], messages=messages,
        )
        budget.record(resp)
        final_text = _last_text(resp) or final_text

        # Harvest REAL source links straight from the web_search results
        # (search-engine provided, with titles) — reliable, named citations
        # that don't depend on the model writing a correct URL from memory.
        if link_sink is not None:
            for _b in resp.content:
                if getattr(_b, "type", None) == "web_search_tool_result":
                    for _r in (getattr(_b, "content", None) or []):
                        _u = getattr(_r, "url", None)
                        if _u:
                            link_sink.append({"title": getattr(_r, "title", None) or _u, "url": _u})

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


def _read_document(client: Anthropic, filename: str, text: str, budget: _Budget,
                   model: str = MODEL) -> dict:
    text = text or ""
    if len(text) <= _SINGLE_DOC_CALL_CHARS:
        raw = _plain_call(
            client,
            _READ_PROMPT.format(part_note="", filename=filename, doc_text=text),
            max_tokens=PASS1_OUT_TOKENS, budget=budget, model=model,
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
            max_tokens=PASS1_OUT_TOKENS, budget=budget, model=model,
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

_RESEARCH_PROMPT = """You are assembling the external evidence base for a venture diligence brief on a deep-tech deal that may be OUTSIDE the firm's expertise. Use web_search to find REAL, verifiable information — do up to {max_searches} targeted searches and cite what you find. Do not invent sources, numbers, paper titles, or patent numbers.

THE FOUNDER'S CLAIM (the person who did the research describes the product/company they want to build from it — this is what we are diligencing, NOT an internal analyst's idea):
{innovation_hint}

STRUCTURED NOTES from the founder's uploaded document(s) (the underlying science):
{doc_notes}

Because this deal may be outside our expertise, FIRST find the literature, datasets and benchmarks needed to evaluate the claim, then reason. Search goals:
1. PRIOR WORK & LITERATURE — the most relevant published work and competing approaches; for each, what it does, how mature it is, and what benchmark it sets.
2. PATENTS — relevant patents/filings in this space (numbers/assignees if findable).
3. DATASETS & BENCHMARKS — public datasets, benchmarks, and the standard figures of merit used to evaluate this kind of technology, with the value that represents 'good' (lab record / commercial bar).
4. COMMERCIAL — companies pursuing this (stage/funding if known), likely first markets/customers, adoption drivers, rough sizing from reputable sources.
5. MANUFACTURING — what is known about scale-up for this class of technology (process maturity, cost drivers, yield, supply chain, certification, bottlenecks).
6. RELATED RESEARCH — curate 5-10 high-quality papers a diligencer should read to go deeper (titles + links/DOIs). A READING LIST, distinct from the sources you cite.

Return JSON only, no prose, no markdown fences:
{{
  "field_overview": "1-2 paragraphs orienting a non-expert in this field",
  "prior_work": [
    {{"area": "sub-area / competing approach", "what_it_does": "...", "maturity": "lab / pilot / commercial / mature", "benchmark_it_sets": "metric + value it achieves", "source": "url"}}
  ],
  "patents": [
    {{"ref": "patent no. / assignee / title", "relevance": "why it matters for FTO or differentiation", "source": "url"}}
  ],
  "benchmark_metrics": [
    {{"metric": "figure of merit", "why_it_matters": "...", "good_value": "lab record / commercial bar with units", "source": "url"}}
  ],
  "datasets": [
    {{"name": "dataset/benchmark", "description": "what it is", "link": "url"}}
  ],
  "commercial_context": "first markets, customers, adoption drivers, sizing — with sources",
  "manufacturing_context": "scale-up maturity, cost/yield/supply-chain/certification risks — with sources",
  "competing_companies": [
    {{"name": "real company", "stage": "stage/funding if known", "what": "what they do", "source": "url"}}
  ],
  "related_research": [
    {{"title": "paper title", "authors": "authors", "venue_year": "venue + year", "link": "doi/url", "why_relevant": "one line"}}
  ],
  "sources": ["every url/source you actually used"]
}}"""


def _research(client: Anthropic, innovation_hint: str, doc_notes: list,
              budget: _Budget, model: str = MODEL, on_search=None) -> dict:
    prompt = _RESEARCH_PROMPT.format(
        max_searches=WEB_SEARCH_MAX_USES,
        innovation_hint=(innovation_hint.strip() or "(none provided — infer the innovation from the documents)"),
        doc_notes=json.dumps(doc_notes, ensure_ascii=False)[:120_000],
    )
    _harvested = []
    raw = _search_call(client, prompt, max_tokens=PASS2_OUT_TOKENS, budget=budget,
                       model=model, on_search=on_search, link_sink=_harvested)
    out = _extract_json(raw) if raw else {}
    if isinstance(out, dict):
        _seen, _dedup = set(), []
        for _s in _harvested:
            if _s["url"] not in _seen:
                _seen.add(_s["url"])
                _dedup.append(_s)
        out["web_sources"] = _dedup
    return out


# ── Pass 3: synthesize the final report ──────────────────────────────────────

_SYNTH_PROMPT = """Produce a REPEATABLE venture due-diligence brief on this deep-tech deal as a single JSON object. The reader is an investor and a technically-literate engineer/scientist who is NOT a specialist in this domain. The goal is to get them ACQUAINTED with the technology and to frame the diligence — answer: what is this, how does it compare to prior work, what could it become, what are the risks, and what must we verify next.

INPUTS
------
>>> THE FOUNDER'S CLAIM (the person who did the research; this is the product/company thesis we are diligencing — NOT an internal analyst's idea) <<<
{innovation_hint}

The founder's uploaded paper(s) are the underlying SCIENCE. The founder's claim above is what they say it BECOMES as a product. Treat the science as evidence for/against a venture-backable PRODUCT — not as something to peer-review. If no claim was provided, infer the most credible product from the documents and say you inferred it.

Document notes (the science):
{doc_notes}

External research (literature / patents / datasets / benchmarks / market):
{research}

BEHAVIOR (important)
--------------------
- Prioritize practical venture diligence over academic critique. Do NOT be dismissive just because the science is abstract or early.
- Do NOT accept a theory-to-product leap without evidence; clearly separate what is proven vs asserted vs missing.
- Always distinguish 'interesting science' from 'commercially validated technology'.
- Because this is outside our expertise, ground every comparison in the literature/benchmarks you were given.
- Do NOT repeat a point across sections — cover it once, in the most relevant section, with depth.
- Every non-trivial external statement carries a "source"; never fabricate sources or numbers.
- NEVER make an investment recommendation or verdict (no pass/monitor/invest, no 'we should…'). The brief informs and frames diligence only.

Return JSON only, no prose, no markdown fences:
{{
  "company_name": "the company/venture name, or the technology name if none is given",
  "field": "the scientific/engineering field",
  "confidence_level": "LOW | MEDIUM | HIGH — confidence that the claimed technology is real and as described (about the CLAIM/evidence, NOT an investment view)",
  "executive_summary": {{
    "technology": "1-2 sentences: what the technology is",
    "commercial_claim": "what the founder claims it becomes as a product",
    "main_diligence_concern": "the single most important thing to resolve",
    "confidence_note": "why the confidence level above is what it is"
  }},
  "technology_explanation": {{
    "summary": "explain the technology for a technically-literate non-specialist; avoid jargon where possible but do NOT oversimplify",
    "key_terms": [{{"term": "term", "definition": "plain definition"}}]
  }},
  "claimed_product": {{
    "what_they_are_building": "what the company is actually building / claiming to commercialize",
    "clarity": "CLEAR | PARTIALLY CLEAR | UNCLEAR",
    "notes": "if the product is unclear or under-specified, say so explicitly"
  }},
  "theory_to_product_bridge": {{
    "proven": ["what the underlying science actually establishes"],
    "asserted": ["what the founder asserts but has not yet shown"],
    "missing": ["what is absent that a product would require"],
    "must_be_demonstrated": ["specific things that must be shown experimentally to close the gap"]
  }},
  "prior_work_literature_map": [
    {{"area": "sub-area", "what_prior_work_does": "...", "maturity": "lab / pilot / commercial / mature", "benchmark_it_sets": "metric + value", "differentiation": "whether/how the company appears meaningfully differentiated", "sources": ["url"]}}
  ],
  "benchmarking_framework": [
    {{"metric": "the figure of merit that matters here", "why_it_matters": "...", "what_good_looks_like": "target vs existing literature/commercial, with units", "source": "url or ''"}}
  ],
  "commercial_implications": {{
    "what_it_could_become": "what the technology could become commercially if it works",
    "first_markets": ["likely first market(s)"],
    "customers": ["likely customers/buyers"],
    "adoption_drivers": ["what would drive adoption"],
    "why_it_matters_economically": "the economic significance if it works",
    "comparable_companies": [{{"name": "company", "context": "comparison", "scale_or_funding": "if known"}}]
  }},
  "manufacturing_scaleup_risk": {{
    "summary": "overview of the path from science to a manufacturable product",
    "risks": [{{"area": "Materials | Process control | Yield | Durability | Integration | Cost | Supply chain | Certification | Adoption", "risk": "specific risk", "severity": "HIGH | MEDIUM | LOW"}}]
  }},
  "key_diligence_questions": {{
    "prototype_data": ["practical, answerable requests for prototype/measured data"],
    "simulations": ["requested simulations/models"],
    "benchmark_comparisons": ["specific head-to-head comparisons to request"],
    "third_party_validation": ["independent validation to request"],
    "patent_filings": ["IP / freedom-to-operate items to request"],
    "manufacturing_process": ["process / scale-up details to request"],
    "customer_pilot_evidence": ["customer / pilot / LOI evidence to request"],
    "supplementary_datasets": ["datasets to request or evaluate against"]
  }},
  "related_research": [
    {{"title": "paper title", "authors": "authors", "venue_year": "venue + year", "link": "doi/url", "why_relevant": "why read it"}}
  ],
  "sources_consulted": 0
}}

Write formulas and symbols in clean, readable notation. You may use common Greek letters and units (e.g. k, mu, eta, degC, x10^-9) but PREFER spelled-out or ASCII forms; avoid exotic mathematical-script / blackboard Unicode and ASCII-art. Keep math minimal and in service of the diligence, not academic exposition."""


def _synthesize(client: Anthropic, innovation_hint: str, doc_notes: list,
                research: dict, budget: _Budget, model: str = MODEL) -> dict:
    prompt = _SYNTH_PROMPT.format(
        innovation_hint=(innovation_hint.strip() or "(none provided — infer the innovation from the documents)"),
        doc_notes=json.dumps(doc_notes, ensure_ascii=False)[:90_000],
        research=json.dumps(research, ensure_ascii=False)[:90_000],
    )
    raw = _plain_call(client, prompt, max_tokens=PASS3_OUT_TOKENS, budget=budget,
                      model=model)
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
    prior = research.get("prior_work") or []
    return {
        "partial": True,
        "partial_reason": reason,
        "company_name": title,
        "field": (research.get("field_overview", "") or "")[:160],
        "confidence_level": "LOW",
        "executive_summary": {
            "technology": " ".join(
                d.get("core_contribution", "") for d in docs if d.get("core_contribution")
            )[:1500],
            "commercial_claim": (innovation_hint or "").strip() or "(none provided)",
            "main_diligence_concern": (
                "This run did not finish — the brief below is assembled from the "
                "document reads and external research gathered so far."
            ),
            "confidence_note": f"Synthesis did not complete ({reason}).",
        },
        "technology_explanation": {
            "summary": " ".join(d.get("methods", "") for d in docs if d.get("methods"))[:4000],
            "key_terms": [t for d in docs for t in (d.get("key_terms") or [])][:30],
        },
        "claimed_product": {
            "what_they_are_building": (innovation_hint or "").strip() or "(not provided)",
            "clarity": "UNCLEAR",
            "notes": "Taken from the founder's claim; not yet evaluated against the science (synthesis incomplete).",
        },
        "theory_to_product_bridge": {
            "proven": [d.get("core_contribution", "") for d in docs if d.get("core_contribution")][:6],
            "asserted": [],
            "missing": [],
            "must_be_demonstrated": [l for d in docs for l in (d.get("limitations_stated") or [])][:15],
        },
        "prior_work_literature_map": [
            {"area": p.get("area", ""), "what_prior_work_does": p.get("what_it_does", ""),
             "maturity": p.get("maturity", ""), "benchmark_it_sets": p.get("benchmark_it_sets", ""),
             "differentiation": "", "sources": [p.get("source", "")]}
            for p in prior
        ],
        "benchmarking_framework": [
            {"metric": b.get("metric", ""), "why_it_matters": b.get("why_it_matters", ""),
             "what_good_looks_like": b.get("good_value", ""), "source": b.get("source", "")}
            for b in (research.get("benchmark_metrics") or [])
        ],
        "commercial_implications": {
            "what_it_could_become": research.get("commercial_context", "") or "",
            "first_markets": [],
            "customers": [],
            "adoption_drivers": [],
            "why_it_matters_economically": "",
            "comparable_companies": [
                {"name": c.get("name", ""), "context": c.get("what", ""),
                 "scale_or_funding": c.get("stage", "")}
                for c in competing
            ],
        },
        "manufacturing_scaleup_risk": {
            "summary": research.get("manufacturing_context", "") or "",
            "risks": [],
        },
        "key_diligence_questions": {
            "prototype_data": [],
            "simulations": [],
            "benchmark_comparisons": [],
            "third_party_validation": [],
            "patent_filings": [],
            "manufacturing_process": [],
            "customer_pilot_evidence": [],
            "supplementary_datasets": [d.get("name", "") for d in (research.get("datasets") or [])],
        },
        "related_research": research.get("related_research", []) or [],
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

    # Resolve the model up front: prefer MODEL (Opus 4.8), but if the API rejects
    # the ID, fall back automatically so the run never dies on the model name.
    model = _resolve_model(client, MODEL, FALLBACK_MODEL)
    _p(12, f"Using {model}. Reading documents...")

    # ── Pass 1: read each document in full (skips remaining docs if budget runs
    #            out; a single doc failing must not kill the run).
    n = len(docs)
    doc_notes = []
    read_errors = []
    for i, d in enumerate(docs):
        if budget.exhausted():
            break
        _p(15 + int(35 * i / n), f"Reading document {i + 1}/{n}: {d['filename']}")
        try:
            doc_notes.append(_read_document(client, d["filename"], d["text"], budget, model))
        except Exception as e:
            read_errors.append(str(e))
            print(f"[TechDDR] read failed for {d['filename']}: {e}", flush=True)
    if not doc_notes:
        # Surface the REAL cause (e.g. an API/model error) rather than blaming
        # the budget — this only fires when document reads actually failed.
        if read_errors:
            return {"error": f"The AI model call failed while reading the document(s): {read_errors[0][:300]}"}
        return {"error": "No document text could be read — try again, or use fewer/smaller PDFs."}

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
            research = _research(client, innovation_hint, doc_notes, budget, model, on_search=_on_search)
        except Exception as e:
            print(f"[TechDDR] research pass failed: {e}", flush=True)
            research = {}

    # ── Pass 3: synthesize. ALWAYS attempted (one bounded call). If it fails or
    #            exceptions, FALL BACK to a partial report assembled from the doc
    #            reads + research already gathered — the work is never lost.
    _p(74, "Synthesizing the technical due-diligence report...")
    report = None
    try:
        report = _synthesize(client, innovation_hint, doc_notes, research, budget, model)
    except Exception as e:
        print(f"[TechDDR] synthesis call failed: {e}", flush=True)
    if not report or report.get("error"):
        reason = (report or {}).get("error") or "the final synthesis step did not complete"
        print(f"[TechDDR] building PARTIAL report ({reason})", flush=True)
        report = _partial_report(doc_notes, research, innovation_hint, reason)
        _p(86, "Synthesis incomplete — saving a partial report from the work gathered...")

    report.setdefault("_research_sources",
                      research.get("sources", []) if isinstance(research, dict) else [])
    report.setdefault("web_sources",
                      research.get("web_sources", []) if isinstance(research, dict) else [])
    report["_doc_filenames"] = [d["filename"] for d in docs]
    report["_usage"] = {**budget.summary(), "model": model, "docs_submitted": n,
                        "docs_read": len(doc_notes), "input_truncated": truncated}
    if not report.get("sources_consulted"):
        report["sources_consulted"] = len(set(report.get("_research_sources") or []))

    print(f"[TechDDR] usage: in={budget.tokens_in} out={budget.tokens_out} "
          f"calls={budget.calls} est=${budget.est_cost():.2f} "
          f"partial={bool(report.get('partial'))} time_left={int(budget.time_left())}s", flush=True)
    _p(88, "Partial report ready." if report.get("partial") else "Report ready.")
    return report
