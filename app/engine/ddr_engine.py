"""
ddr_engine.py
=============
AI analysis engine for Due Diligence Reports (DDR).
Integrated into the VoLo Engine from the standalone DDR V2 tool.

Uses a single Claude + web_search call to produce a comprehensive
due diligence JSON covering: company overview, status flags,
competitive landscape, claims assessment, unverified claims,
outcome magnitude, and sources.
"""

import os
import json
import re
import time

from pypdf import PdfReader
from anthropic import Anthropic, RateLimitError, APIStatusError

# ── Constants ────────────────────────────────────────────────────────────────

MODEL = "claude-sonnet-4-6"

WEB_SEARCH_TOOL = {
    "type": "web_search_20250305",
    "name": "web_search",
}


# ── PDF Extraction ───────────────────────────────────────────────────────────

def extract_pdf(path: str) -> str:
    """Extract text from a PDF file using pypdf."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"PDF not found: {path}")
    text = ""
    reader = PdfReader(path)
    for i, page in enumerate(reader.pages, 1):
        text += page.extract_text() + "\n\n"
    if len(text) > 60000:
        text = text[:60000]
    return text


# ── Agentic Loop ─────────────────────────────────────────────────────────────

def _agentic_call(client: Anthropic, prompt: str,
                  max_tokens: int = 16000, temperature: float = 0.2,
                  on_progress=None) -> str:
    """
    Run a single agentic Claude + web_search call.
    Loops until stop_reason == "end_turn" or no tool calls remain.
    Uses exponential backoff for rate limits and overloaded errors.
    """
    messages = [{"role": "user", "content": prompt}]
    final_text = ""
    web_sources = []  # real URLs harvested from web_search results
    pause_count = 0   # server-side web_search "pause_turn" resumes (capped below)

    while True:
        backoff_delays = [30, 60, 120, 240, 300]
        for attempt in range(5):
            try:
                # Stream and collect the final message. A DDR turn runs several
                # server-side web searches and a large JSON output, which can
                # exceed the SDK's 10-minute non-streaming ceiling (it raises
                # "Streaming is required..." pre-flight once max_tokens is large).
                # Streaming removes that ceiling; get_final_message() returns the
                # same Message shape the rest of this loop expects.
                with client.messages.stream(
                    model=MODEL,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    tools=[WEB_SEARCH_TOOL],
                    messages=messages,
                ) as stream:
                    response = stream.get_final_message()
                break
            except RateLimitError as e:
                wait = backoff_delays[min(attempt, len(backoff_delays) - 1)]
                print(f"[DDR] Rate limit hit (attempt {attempt+1}/5), waiting {wait}s: {e}")
                if attempt < 4:
                    time.sleep(wait)
                else:
                    raise RuntimeError(
                        "API rate limit exceeded after 5 retries. Please wait a few minutes and try again. "
                        "This happens when too many analyses run in quick succession."
                    )
            except APIStatusError as e:
                if e.status_code == 529 and attempt < 4:
                    wait = backoff_delays[min(attempt, len(backoff_delays) - 1)]
                    print(f"[DDR] API overloaded (attempt {attempt+1}/5), waiting {wait}s")
                    time.sleep(wait)
                elif "token" in str(e).lower() or "credit" in str(e).lower():
                    raise RuntimeError(
                        "API token/credit error. Please check that the ANTHROPIC_API_KEY has "
                        "sufficient credits and is not expired."
                    )
                else:
                    raise

        turn_texts = []
        for block in response.content:
            if hasattr(block, "text"):
                turn_texts.append(block.text)
            # Harvest REAL source URLs straight from the web_search tool
            # results — these come from the search engine, not the model, so
            # they are genuine working links (the reliable fix for broken or
            # hallucinated citations). The model's per-claim citations are a
            # safeguard layer on top of these.
            if getattr(block, "type", "") == "web_search_tool_result":
                for _r in (getattr(block, "content", None) or []):
                    _u = getattr(_r, "url", None)
                    if _u:
                        web_sources.append(
                            {"title": getattr(_r, "title", None) or _u, "url": _u})

        # Keep the JSON: accumulate ALL text blocks of this turn instead of
        # letting a trailing courtesy sentence overwrite the block that holds
        # the JSON. _extract_json spans first '{' to last '}', so surrounding
        # prose is harmless.
        if turn_texts:
            final_text = "\n".join(turn_texts)

        # Count both client-side tool_use and server-side web_search calls
        tool_calls = [b for b in response.content
                      if b.type in ("tool_use", "server_tool_use")]
        if on_progress and tool_calls:
            on_progress(len(tool_calls))

        if response.stop_reason == "end_turn":
            break

        messages.append({"role": "assistant", "content": response.content})

        # Server-side web_search pauses a long/looping turn with
        # stop_reason == "pause_turn" — it emits server_tool_use, NOT a
        # client-side tool_use, so the tool_results list below is empty. The
        # turn is NOT finished: resume by re-sending the accumulated messages
        # with no extra user turn. Previously this fell through to the
        # `else: break`, so the loop exited after one turn with only the
        # model's pre-JSON preamble → "No JSON found in response".
        if response.stop_reason == "pause_turn":
            pause_count += 1
            if pause_count > 8:
                break
            continue

        tool_results = [
            {"type": "tool_result", "tool_use_id": b.id, "content": ""}
            for b in response.content if b.type == "tool_use"
        ]
        if tool_results:
            messages.append({"role": "user", "content": tool_results})
        else:
            break

    # de-dupe harvested links by URL, preserving first-seen order
    _seen, _deduped = set(), []
    for _s in web_sources:
        if _s["url"] not in _seen:
            _seen.add(_s["url"])
            _deduped.append(_s)
    return final_text, _deduped


# ── JSON Extraction with Recovery ────────────────────────────────────────────

def _extract_json(raw_text: str) -> dict:
    """Parse JSON from Claude's response with multiple fallback strategies."""
    raw = raw_text.strip()
    raw = re.sub(r"```[a-z]*\s*\n?", "", raw)
    raw = re.sub(r"\n?\s*```", "", raw)

    json_match = re.search(r'\{[\s\S]*\}', raw)
    if not json_match:
        return {"company_name": "Unknown", "error": "No JSON found in response"}

    fragment = json_match.group()

    def _clean(s: str) -> str:
        s = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', s)
        return s

    # Attempt 1: direct parse
    try:
        return json.loads(fragment)
    except (json.JSONDecodeError, ValueError):
        pass

    # Attempt 2: clean control chars
    try:
        return json.loads(_clean(fragment))
    except (json.JSONDecodeError, ValueError):
        pass

    # Attempt 3: close open braces
    try:
        cleaned = _clean(fragment)
        open_b = cleaned.count("{") - cleaned.count("}")
        open_a = cleaned.count("[") - cleaned.count("]")
        patched = cleaned + ("]" * max(open_a, 0)) + ("}" * max(open_b, 0))
        return json.loads(patched)
    except (json.JSONDecodeError, ValueError):
        pass

    # Attempt 4: ASCII only
    try:
        clean = fragment.encode("ascii", errors="ignore").decode("ascii")
        return json.loads(clean)
    except (json.JSONDecodeError, ValueError):
        pass

    # Attempt 5: find first balanced JSON object
    try:
        depth = 0
        end_idx = 0
        for idx, ch in enumerate(fragment):
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    end_idx = idx + 1
                    break
        if end_idx > 0:
            return json.loads(_clean(fragment[:end_idx]))
    except (json.JSONDecodeError, ValueError):
        pass

    return {"company_name": "Unknown", "error": "JSON parse failed",
            "raw": raw_text[:2000]}


# ── Analysis Prompt ──────────────────────────────────────────────────────────

_ANALYSIS_PROMPT = """You are conducting deep due diligence on a pitch deck. Your job is NOT to decide whether to invest. Your job is to:

1. Surface every significant claim the company makes — be exhaustive
2. Flag which claims are UNVERIFIED and need investigation
3. For each unverified claim, size the potential outcome IF it turns out to be true
4. Map the full competitive landscape at both peer scale and larger market scale
5. Build a rigorous technology benchmark with real competitor data

CONCISENESS GUIDANCE — This report targets 10-12 pages total. Be precise:
- company_overview: 1-2 paragraphs (not 2-3)
- Peer competitors: 2-4 entries, 1-2 sentences each for description
- Market leaders: 2-3 entries, 1-2 sentences each
- Claims: include only genuinely significant claims. Skip trivial or obvious ones.
- Unverified claims: include ONLY CRITICAL and HIGH priority claims (skip MEDIUM and LOW entirely).
  For each, keep investigation_steps to 1-2 concrete items.
- outcome_magnitude: 1 paragraph per scenario, not 2-3

THOROUGHNESS GUIDANCE:
- Focus on the claims an investment committee would actually care about
- Do not be vague — quote claims precisely from the deck and name real companies with known valuations
- Every unverified claim needs 1-2 concrete investigation steps naming specific data sources or tests
- All competitor names must be real companies with verifiable existence

DATA LABELING — assign every claim ONE verification level (this 5-level taxonomy REPLACES the old binary verified/unverified):
- "independently verified" — confirmed by an independent third party you cite (filing, court/patent record, reputable outlet, primary dataset)
- "supported by peer-reviewed literature" — the underlying SCIENTIFIC phenomenon is backed by peer-reviewed work. This validates the SCIENCE, NOT the company's specific formulation, measured device performance, production applicability, durability, or customer results — never overstate peer-reviewed science as product validation.
- "supported by credible secondary evidence" — corroborated by a credible secondary source short of independent verification
- "company-reported" — only from the company's own materials, no independent confirmation
- "unsupported or conflicting" — no support found, or sources conflict

CLAIM DISCIPLINE:
- CONSERVATIVE ATTRIBUTION — present company-reported technical performance, customer activity, partnerships, pilots, and traction as REPORTED ("the company reports", "management claims", "company materials indicate"), never as established fact, unless the level is "independently verified".
- COMMERCIAL-STAGE LADDER — use these exact terms and never upgrade a lower stage into a higher one: discussion < evaluation < pre-pilot < scoped pilot < paid pilot < qualification < commercial agreement < production deployment. Do NOT convert "pilot planning", "pre-pilot", "technical discussion", or "evaluation" into "commercial pilot".
- RECONCILE PRICING — if the materials contain multiple pricing bases (price per gram, cost per wafer, revenue per chip, value-based pricing, licensing revenue), FLAG the inconsistency in the relevant claim rather than blending them into one figure.
- IDENTITY & TRANSACTION CROSS-CHECK — for any acquisition, funding round, or named company, verify (via web_search) acquirer vs target, year, amount, HQ location, market cap, revenue, investor participation, and disambiguate similarly-named companies; if you cannot confirm a detail, say "unconfirmed" rather than stating it.

OUTCOME COMPARABLES — reference real companies with known valuations:
- "If the efficiency claims are accurate, this could compete with [Company] which holds X% of the market, valued at $Y"
- Use: IEA, Bloomberg NEF, Bain, McKinsey, CB Insights, Crunchbase, PitchBook

CITATIONS — every source must be a verifiable, clickable reference. Provide each
source as an OBJECT, never a bare name:
  {{"title": "Exact article / report / paper name", "url": "https://...", "publisher": "Bloomberg"}}
- The "url" MUST be a real link from your web_search results — a page you actually
  opened. NEVER invent, guess, or reconstruct a URL from memory. If you do not have a
  real URL for a source, OMIT the "url" field entirely and give just "title" +
  "publisher" so a reader can still find it by name.
- "title" is REQUIRED for every source — the report falls back to it whenever a link
  is missing or broken. Prefer the primary source (the actual filing, report, or
  paper) over an aggregator.

Pitch Deck:
{pitch_text}

Return comprehensive JSON:
{{
    "company_name": "Name",
    "industry": "Industry",
    "founded_year": 2020,

    "company_overview": {{
        "description": "1-2 concise paragraphs describing what the company does and what it claims",
        "stage": "Pre-revenue / Early revenue / Growth",
        "key_claims_summary": ["Top claim 1", "Top claim 2", "Top claim 3"]
    }},

    "status_flags": {{
        "overall_status": "HEALTHY / DISTRESSED / CRITICAL / UNKNOWN",
        "bankruptcy_insolvency": {{
            "status": "ACTIVE / IN ADMINISTRATION / BANKRUPTCY / NONE FOUND",
            "details": "Specific details if found",
            "sources": ["Court records", "News articles"]
        }},
        "recent_funding": {{
            "last_round": "Series A / €15M round / etc",
            "outcome": "SUCCESSFUL / FAILED / ONGOING / UNKNOWN",
            "amount_sought": 15000000,
            "amount_raised": 0,
            "date": "YYYY-MM-DD",
            "failure_reasons": "Why it failed if applicable",
            "sources": ["Crunchbase", "News"]
        }},
        "ip_status": {{
            "status": "CLEAR / DISPUTED / ENCUMBERED / UNKNOWN",
            "details": "Patents owned, licensed, or disputed",
            "sources": ["Patent office"]
        }},
        "active_litigation": {{
            "lawsuits": ["Case 1 if any"],
            "regulatory_actions": ["Action 1 if any"],
            "sources": ["Court records"]
        }},
        "notes": "Key facts IC should know for context"
    }},

    "competitive_landscape": {{
        "positioning_summary": "1-2 sentences on how the company positions itself",
        "peer_competitors": [
            {{
                "name": "Real company at similar stage",
                "stage": "Seed / Series A / Series B",
                "funding_raised_usd": 5000000,
                "description": "1-2 sentences: what they do, how they overlap, their edge vs this company",
                "sources": [{{"title": "Company funding profile", "url": "https://real-url-from-your-search", "publisher": "Crunchbase"}}]
            }}
        ],
        "market_leaders": [
            {{
                "name": "Real large incumbent",
                "market_position": "e.g. '35% market share in offshore wind'",
                "valuation_or_revenue": "e.g. '$18B market cap'",
                "description": "1-2 sentences: what they do, threat to this company",
                "sources": [{{"title": "Market position report", "url": "https://real-url-from-your-search", "publisher": "Bloomberg"}}]
            }}
        ],
        "competitive_risks": ["Specific risk 1", "Specific risk 2"],
        "potential_acquirers": ["Company that might acquire — and why"]
    }},

    "claims": [
        {{
            "type": "TECHNOLOGY",
            "claim": "Exact quoted claim from the deck",
            "verification_status": "independently verified | supported by peer-reviewed literature | supported by credible secondary evidence | company-reported | unsupported or conflicting",
            "source_label": "<the verification level above> — [Source name if any]",
            "what_needs_investigation": "Specific test or data source that could verify this",
            "sources": [{{"title": "Report or article name", "url": "https://real-url-from-your-search", "publisher": "Publisher"}}]
        }},
        {{
            "type": "MARKET",
            "claim": "Exact quoted claim from the deck",
            "verification_status": "independently verified | supported by peer-reviewed literature | supported by credible secondary evidence | company-reported | unsupported or conflicting",
            "source_label": "<the verification level above> — [Source name if any]",
            "what_needs_investigation": "Specific data source that would verify this",
            "sources": [{{"title": "Report or article name", "url": "https://real-url-from-your-search", "publisher": "Publisher"}}]
        }}
    ],

    "unverified_claims": [
        {{
            "claim": "Specific unverified claim — quote it precisely",
            "category": "Technology / Market / Financial / Team / Legal",
            "why_unverified": "What is specifically missing",
            "investigation_steps": ["Concrete step 1", "Concrete step 2"],
            "outcome_if_true": {{
                "description": "What it means if this claim holds up",
                "market_opportunity_usd": 5000000000,
                "comparable_companies": [
                    {{
                        "company": "Real named company",
                        "context": "Specific comparison",
                        "comparable_valuation_usd": 20000000000,
                        "market_share_potential": "5-15% of addressable market"
                    }}
                ],
                "outcome_magnitude": "HIGH / MEDIUM / LOW",
                "key_caveat": "The single most important condition for this outcome"
            }},
            "priority": "CRITICAL / HIGH"
        }}
    ],

    "outcome_magnitude": {{
        "if_all_claims_verified": {{
            "description": "1 paragraph: what the company could become",
            "addressable_market_usd": 50000000000,
            "realistic_market_share_pct": 5,
            "comparable_companies": ["Real Company A", "Real Company B"],
            "framing": "If the technology and market claims are accurate, this company could compete with [X] in the [Y] market"
        }},
        "if_core_tech_only_verified": {{
            "description": "1 paragraph: outcome if just the core technology works",
            "addressable_market_usd": 5000000000,
            "comparable_companies": ["Real smaller comp"],
            "framing": "Even with a smaller market, proven tech alone positions this similarly to [X]"
        }},
        "key_dependencies": ["Specific dependency 1", "Specific dependency 2"]
    }},

    "sources_consulted": 30
}}

WEB RESEARCH REQUIREMENTS:
You have access to web_search — use it to verify and enrich your analysis.
Do 6-7 searches covering:
  - Company name + "funding" / "crunchbase" / "news"
  - Competitor names + "valuation" / "market share"
  - Technology performance benchmarks relevant to company claims
  - "[company] litigation" / "bankruptcy" if relevant
Do NOT guess at numbers — search for real data first. Cite what you find.

IMPORTANT:
- ONLY include CRITICAL and HIGH priority unverified claims. Skip MEDIUM and LOW entirely.
- Keep descriptions concise — this report targets 8-10 pages total.
- Do not recommend whether to invest — only surface what is unverified and what it could mean.
- OVERVIEW-CLAIMS ALIGNMENT: the company_overview must NOT state as established fact any claim you mark below "independently verified". Describe such claims as company-reported in the overview.
- SIZED OUTCOMES NEED A DERIVATION: only populate a dollar figure (market_opportunity_usd, addressable_market_usd, comparable_valuation_usd) when it follows from an explicit, reviewable calculation you can state (e.g. units × price, or % of a cited market). Do NOT attach a bare "$X" opportunity to a claim without that basis — leave the figure null and describe the outcome qualitatively instead.
- NO UNSUPPORTED EXIT/ACQUISITION RANGES: only give a specific valuation or exit range when anchored to a NAMED comparable transaction, a stated revenue/EBITDA scenario, an explicit multiple, and timing/dilution assumptions. Otherwise keep it qualitative.
- After completing your web research, return the full JSON and nothing else — no markdown fences, no prose.
"""


# ── Public API ───────────────────────────────────────────────────────────────

def analyze(api_key: str, pitch_text: str, on_progress=None) -> dict:
    """
    Run the unified Claude + web_search analysis on pitch deck text.
    Returns all data needed for the DDR report.
    """
    client = Anthropic(api_key=api_key)
    prompt = _ANALYSIS_PROMPT.format(pitch_text=pitch_text[:60000])

    raw_text, web_sources = _agentic_call(
        client, prompt,
        max_tokens=32000, temperature=0.2,
        on_progress=on_progress,
    )
    result = _extract_json(raw_text)
    # Attach the real, search-engine-provided links as a verified citation pool
    # (the report renders these as guaranteed-clickable sources).
    if isinstance(result, dict) and not result.get("web_sources"):
        result["web_sources"] = web_sources
    return result
