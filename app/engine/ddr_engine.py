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
    """
    messages = [{"role": "user", "content": prompt}]
    final_text = ""

    while True:
        for attempt in range(5):
            try:
                response = client.messages.create(
                    model=MODEL,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    tools=[WEB_SEARCH_TOOL],
                    messages=messages,
                )
                break
            except RateLimitError:
                if attempt < 4:
                    time.sleep(60)
                else:
                    raise
            except APIStatusError as e:
                if e.status_code == 529 and attempt < 4:
                    time.sleep(30)
                else:
                    raise

        for block in response.content:
            if hasattr(block, "text"):
                final_text = block.text

        # Count both client-side tool_use and server-side web_search calls
        tool_calls = [b for b in response.content
                      if b.type in ("tool_use", "server_tool_use")]
        if on_progress and tool_calls:
            on_progress(len(tool_calls))

        if response.stop_reason == "end_turn":
            break

        messages.append({"role": "assistant", "content": response.content})
        tool_results = [
            {"type": "tool_result", "tool_use_id": b.id, "content": ""}
            for b in response.content if b.type == "tool_use"
        ]
        if tool_results:
            messages.append({"role": "user", "content": tool_results})
        else:
            break

    return final_text


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

DATA LABELING — label every claim as:
- "COMPANY CLAIM (Unverified)" — only from the pitch deck, no independent confirmation
- "VERIFIED: [Source]" — confirmed by independent third party
- "PARTIALLY VERIFIED: Company claims X, [Source] indicates Y"

OUTCOME COMPARABLES — reference real companies with known valuations:
- "If the efficiency claims are accurate, this could compete with [Company] which holds X% of the market, valued at $Y"
- Use: IEA, Bloomberg NEF, Bain, McKinsey, CB Insights, Crunchbase, PitchBook

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
                "sources": ["Crunchbase"]
            }}
        ],
        "market_leaders": [
            {{
                "name": "Real large incumbent",
                "market_position": "e.g. '35% market share in offshore wind'",
                "valuation_or_revenue": "e.g. '$18B market cap'",
                "description": "1-2 sentences: what they do, threat to this company",
                "sources": ["Bloomberg"]
            }}
        ],
        "competitive_risks": ["Specific risk 1", "Specific risk 2"],
        "potential_acquirers": ["Company that might acquire — and why"]
    }},

    "claims": [
        {{
            "type": "TECHNOLOGY",
            "claim": "Exact quoted claim from the deck",
            "verification_status": "VERIFIED / UNVERIFIED / PARTIALLY VERIFIED",
            "source_label": "COMPANY CLAIM (Unverified) / VERIFIED: [Source]",
            "what_needs_investigation": "Specific test or data source that could verify this",
            "sources": ["Source 1"]
        }},
        {{
            "type": "MARKET",
            "claim": "Exact quoted claim from the deck",
            "verification_status": "VERIFIED / UNVERIFIED / PARTIALLY VERIFIED",
            "source_label": "COMPANY CLAIM (Unverified) / VERIFIED: [Source]",
            "what_needs_investigation": "Specific data source that would verify this",
            "sources": ["Source 1"]
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

    raw_text = _agentic_call(
        client, prompt,
        max_tokens=16000, temperature=0.2,
        on_progress=on_progress,
    )
    return _extract_json(raw_text)
