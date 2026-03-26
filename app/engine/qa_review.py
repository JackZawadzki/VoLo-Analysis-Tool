"""
QA Review Engine
================
Runs a two-pass quality assurance check on a completed deal report + memo.

Pass 1 — Deterministic rules (no LLM):
  - Ownership math
  - Post-money consistency
  - Carbon attribution math
  - Follow-on concentration accounting
  - IRR / MOIC / hold-period plausibility
  - Missing / thin data sections
  - Fund concentration check

Pass 2 — LLM-powered (Claude):
  - Mines all numbers from memo markdown text
  - Cross-references against authoritative report data
  - Flags internal inconsistencies (same metric cited differently across sections)

Returns a structured findings list, each with:
  { id, severity, category, title, detail, location }

Severity levels: "error" | "warning" | "info"
Categories: "number_discrepancy" | "internal_consistency" | "logic" | "missing_section"
"""

import os
import re
import json
import math
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── tolerance for floating-point comparisons ────────────────────────────────
_REL_TOL   = 0.02   # 2% relative tolerance before flagging a discrepancy
_ABS_TOL   = 0.05   # 0.05 absolute (for small numbers like % fractions)

# ── minimum word count to consider a section "present" ──────────────────────
_MIN_SECTION_WORDS = 60


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _safe(val, default=0.0):
    try:
        v = float(val)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def _pct_diff(a, b):
    """Relative difference |a-b|/max(|b|,1e-9)."""
    return abs(a - b) / max(abs(b), 1e-9)


def _finding(fid, severity, category, title, detail, location="report"):
    return {
        "id": fid,
        "severity": severity,
        "category": category,
        "title": title,
        "detail": detail,
        "location": location,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Pass 1 — Deterministic Rules
# ─────────────────────────────────────────────────────────────────────────────

def _check_ownership_math(report: dict, inputs: dict) -> list:
    findings = []
    ov = report.get("deal_overview", {})

    check_m     = _safe(inputs.get("check_size_millions") or ov.get("check_size_millions"))
    pre_m       = _safe(inputs.get("pre_money_millions")  or ov.get("pre_money_millions"))
    round_m     = _safe(inputs.get("round_size_millions") or ov.get("round_size_millions"))
    stored_own  = _safe(ov.get("entry_ownership_pct"))  # stored as percent (e.g. 11.76)

    if check_m <= 0 or pre_m <= 0:
        return findings

    post_m = pre_m + round_m if round_m > 0 else pre_m
    computed_own = (check_m / post_m) * 100 if post_m > 0 else 0

    if stored_own > 0 and _pct_diff(stored_own, computed_own) > _REL_TOL:
        findings.append(_finding(
            "ownership_math",
            "error",
            "number_discrepancy",
            "Ownership % doesn't match check/post-money math",
            f"Stored: {stored_own:.2f}% | Computed: {check_m}M ÷ ${post_m}M = {computed_own:.2f}%. "
            f"Difference: {abs(stored_own - computed_own):.2f}pp.",
            "deal_terms",
        ))
    return findings


def _check_postmoney(report: dict, inputs: dict) -> list:
    findings = []
    ov = report.get("deal_overview", {})

    pre_m   = _safe(inputs.get("pre_money_millions")  or ov.get("pre_money_millions"))
    round_m = _safe(inputs.get("round_size_millions") or ov.get("round_size_millions"))
    check_m = _safe(inputs.get("check_size_millions") or ov.get("check_size_millions"))

    if pre_m <= 0 or round_m <= 0:
        return findings

    # If check_m > round_m — VoLo is putting in more than the whole round
    if check_m > round_m * 1.01:
        findings.append(_finding(
            "check_exceeds_round",
            "error",
            "logic",
            "Check size exceeds round size",
            f"Check ${check_m}M > round size ${round_m}M. VoLo cannot invest more than the full round.",
            "deal_terms",
        ))
    return findings


def _check_carbon_math(report: dict) -> list:
    findings = []
    carbon = report.get("carbon_impact", {})
    if not carbon.get("has_data"):
        return findings

    co = carbon.get("outputs", {})
    ov = report.get("deal_overview", {})
    entry_own = _safe(ov.get("entry_ownership_pct")) / 100.0

    company_t  = _safe(co.get("company_tonnes"))
    volo_t     = _safe(co.get("volo_prorata"))
    risk_adj_t = _safe(co.get("volo_risk_adj"))
    risk_div   = _safe(carbon.get("risk_divisor_used"), 1)

    # VoLo prorata should ≈ company × ownership
    if company_t > 0 and entry_own > 0:
        expected_prorata = company_t * entry_own
        if _pct_diff(volo_t, expected_prorata) > _REL_TOL + 0.01:
            findings.append(_finding(
                "carbon_prorata_math",
                "warning",
                "number_discrepancy",
                "Carbon pro-rata attribution doesn't match ownership",
                f"Company: {company_t:,.0f} tCO₂ × {entry_own*100:.1f}% = {expected_prorata:,.0f} expected; "
                f"stored {volo_t:,.0f} tCO₂.",
                "carbon_impact",
            ))

    # Risk-adjusted should ≈ prorata / risk_divisor
    if volo_t > 0 and risk_div > 0:
        expected_risk = volo_t / risk_div
        if _pct_diff(risk_adj_t, expected_risk) > _REL_TOL + 0.01:
            findings.append(_finding(
                "carbon_risk_adj_math",
                "warning",
                "number_discrepancy",
                "Risk-adjusted carbon doesn't match prorata ÷ risk divisor",
                f"VoLo prorata {volo_t:,.0f} ÷ risk divisor {risk_div:.2f} = {expected_risk:,.0f} expected; "
                f"stored {risk_adj_t:,.0f} tCO₂.",
                "carbon_impact",
            ))
    return findings


def _check_moic_irr_plausibility(report: dict) -> list:
    findings = []
    sim = report.get("simulation", {})
    hero = report.get("hero_metrics", {}) or sim.get("hero_metrics", {})
    if not hero:
        return findings

    moic = _safe(hero.get("expected_moic"))
    irr  = _safe(hero.get("expected_irr"))   # stored as fraction (e.g. 0.22)
    surv = _safe(hero.get("survival_rate"))   # fraction

    # IRR stored as fraction, show as %
    irr_pct = irr * 100 if irr < 1 else irr

    # MOIC and IRR directional sanity: a 0.5x expected MOIC with 40% IRR is implausible
    if moic > 0 and irr_pct > 0:
        # Very rough heuristic: at typical 7yr hold, 3x ≈ 17% IRR, 5x ≈ 26% IRR, 10x ≈ 39% IRR
        # If MOIC < 1 but IRR > 15% (or vice versa), flag it
        if moic < 1.0 and irr_pct > 10:
            findings.append(_finding(
                "moic_irr_mismatch",
                "warning",
                "logic",
                "Expected MOIC < 1x but IRR > 10% — check simulation logic",
                f"Expected MOIC {moic:.2f}x (loss) with Expected IRR {irr_pct:.1f}%. "
                "These are directionally inconsistent.",
                "simulation",
            ))
        if moic > 5.0 and irr_pct < 10:
            findings.append(_finding(
                "moic_irr_mismatch_high",
                "warning",
                "logic",
                "Expected MOIC > 5x but IRR < 10% — implies very long hold period",
                f"Expected MOIC {moic:.2f}x with only {irr_pct:.1f}% IRR implies a very extended hold. "
                "Verify exit timing assumptions.",
                "simulation",
            ))

    # Survival vs loss probability
    prob = sim.get("probability", sim.get("probability_buckets", {}))
    total_loss = _safe(prob.get("total_loss"))
    if surv > 0 and total_loss > 0:
        implied_surv = 1 - total_loss
        if _pct_diff(surv, implied_surv) > 0.05:
            findings.append(_finding(
                "survival_loss_mismatch",
                "warning",
                "number_discrepancy",
                "Survival rate inconsistent with total-loss probability",
                f"Survival rate {surv*100:.1f}% vs 1 − P(total loss) = {implied_surv*100:.1f}%. "
                "These should sum to 1.",
                "simulation",
            ))
    return findings


def _check_concentration(report: dict, inputs: dict) -> list:
    findings = []
    ps = report.get("position_sizing", {})
    fo = report.get("followon_position_sizing", {})
    ov = report.get("deal_overview", {})

    fund_size  = _safe(inputs.get("fund_size_m") or ps.get("fund_constraints", {}).get("fund_size_m"))
    mgmt_fee   = _safe(inputs.get("mgmt_fee_pct") or 2.0) / 100
    reserve    = _safe(inputs.get("reserve_pct") or 15) / 100
    max_conc   = _safe(inputs.get("max_concentration_pct") or 15) / 100
    check_m    = _safe(inputs.get("check_size_millions") or ov.get("check_size_millions"))
    n_deals    = _safe(inputs.get("n_deals") or 22)

    if fund_size <= 0 or check_m <= 0:
        return findings

    mgmt_years  = 10
    investable  = fund_size * (1 - mgmt_fee * mgmt_years) * (1 - reserve)
    conc_cap    = investable * max_conc

    # For follow-on, total exposure = prior + follow-on check
    if fo.get("has_data"):
        comb = fo.get("combined", {})
        total_exp = _safe(comb.get("total_invested_m"))
        if total_exp > conc_cap * 1.05:
            findings.append(_finding(
                "concentration_breach",
                "error",
                "logic",
                "Total follow-on exposure breaches concentration limit",
                f"Total exposure ${total_exp:.2f}M > concentration cap ${conc_cap:.2f}M "
                f"({max_conc*100:.0f}% of ${investable:.1f}M investable capital).",
                "position_sizing",
            ))
    elif check_m > conc_cap * 1.05:
        findings.append(_finding(
            "concentration_breach",
            "warning",
            "logic",
            "Check size exceeds fund concentration limit",
            f"Check ${check_m:.2f}M > concentration cap ${conc_cap:.2f}M "
            f"({max_conc*100:.0f}% of ~${investable:.1f}M investable).",
            "position_sizing",
        ))
    return findings


def _check_missing_sections(report: dict) -> list:
    findings = []
    sections = {
        "simulation":          ("Simulation / MOIC results", "error"),
        "carbon_impact":       ("Carbon impact assessment", "warning"),
        "position_sizing":     ("Position sizing analysis", "warning"),
        "adoption_analysis":   ("Market adoption S-curve", "info"),
        "financial_model":     ("Founder financial model", "info"),
        "portfolio_impact":    ("Fund portfolio impact", "info"),
    }
    for key, (label, sev) in sections.items():
        section = report.get(key, {})
        if not section.get("has_data", True if key == "simulation" else False):
            findings.append(_finding(
                f"missing_{key}",
                sev,
                "missing_section",
                f"No data: {label}",
                f"The '{label}' section has no data (has_data=false). "
                f"{'Upload a financial model in Step 1.' if key == 'financial_model' else 'Check inputs and re-run if needed.'}",
                key,
            ))
    return findings


def _check_followon_blended_math(report: dict) -> list:
    findings = []
    fo = report.get("followon_position_sizing", {})
    if not fo.get("has_data"):
        return findings

    comb    = fo.get("combined", {})
    priors  = fo.get("prior_investments", [])
    fo_inv  = fo.get("followon_investment", {})
    rec_fo  = _safe(fo.get("recommended_followon_check_m"))

    total_prior = sum(_safe(p.get("check_m")) for p in priors)
    stored_total = _safe(comb.get("total_invested_m"))
    expected_total = total_prior + rec_fo

    if stored_total > 0 and _pct_diff(stored_total, expected_total) > _REL_TOL:
        findings.append(_finding(
            "followon_total_math",
            "warning",
            "number_discrepancy",
            "Follow-on combined total doesn't match prior + follow-on",
            f"Prior(s) ${total_prior:.2f}M + follow-on ${rec_fo:.2f}M = ${expected_total:.2f}M expected; "
            f"stored total ${stored_total:.2f}M.",
            "followon_position_sizing",
        ))
    return findings


# ─────────────────────────────────────────────────────────────────────────────
# Pass 2 — LLM Number Mining
# ─────────────────────────────────────────────────────────────────────────────

def _build_qa_prompt(report: dict, inputs: dict, memo_markdown: str) -> str:
    """Build the prompt for LLM-powered QA analysis."""

    ov   = report.get("deal_overview", {})
    sim  = report.get("simulation", {})
    hero = report.get("hero_metrics", {}) or sim.get("hero_metrics", {})
    ps   = report.get("position_sizing", {})
    fo   = report.get("followon_position_sizing", {})
    carb = report.get("carbon_impact", {})
    co   = carb.get("outputs", {})
    fm   = report.get("financial_model", {})

    # Build authoritative fact sheet
    facts = []
    facts.append(f"Company: {ov.get('company_name','')}")
    facts.append(f"Entry stage: {ov.get('entry_stage','')}")
    facts.append(f"Check size: ${_safe(ov.get('check_size_millions')):.2f}M")
    facts.append(f"Pre-money: ${_safe(ov.get('pre_money_millions')):.2f}M")
    facts.append(f"Round size: ${_safe(ov.get('round_size_millions')):.2f}M")
    facts.append(f"Entry ownership: {_safe(ov.get('entry_ownership_pct')):.2f}%")
    facts.append(f"TRL: {ov.get('trl','')}")
    facts.append(f"TAM: ${_safe(ov.get('tam_millions')):.0f}M")

    if hero:
        facts.append(f"Expected MOIC: {_safe(hero.get('expected_moic')):.2f}x")
        irr = _safe(hero.get('expected_irr'))
        facts.append(f"Expected IRR: {(irr*100 if irr < 1 else irr):.1f}%")
        surv = _safe(hero.get('survival_rate'))
        facts.append(f"Survival rate: {(surv*100 if surv < 1 else surv):.1f}%")
        p3x = _safe(hero.get('p_gt_3x'))
        facts.append(f"P(>3x): {(p3x*100 if p3x < 1 else p3x):.1f}%")

    if carb.get("has_data"):
        facts.append(f"Carbon - Company lifetime tCO2: {_safe(co.get('company_tonnes')):,.0f}")
        facts.append(f"Carbon - VoLo pro-rata tCO2: {_safe(co.get('volo_prorata')):,.0f}")
        facts.append(f"Carbon - Risk-adjusted tCO2: {_safe(co.get('volo_risk_adj')):,.0f}")
        facts.append(f"Carbon - $/tCO2 avoided: ${_safe(co.get('cost_per_tonne')):.2f}")

    if ps.get("has_data"):
        rec = _safe(ps.get("recommended_check_m") or ps.get("grid_search", {}).get("optimal", {}).get("check_m"))
        facts.append(f"Recommended check size: ${rec:.2f}M")

    if fo.get("has_data"):
        facts.append(f"Recommended follow-on: ${_safe(fo.get('recommended_followon_check_m')):.2f}M")
        comb = fo.get("combined", {})
        facts.append(f"Total exposure (prior + follow-on): ${_safe(comb.get('total_invested_m')):.2f}M")

    fact_str = "\n".join(facts)

    # Truncate memo to keep prompt manageable
    memo_excerpt = memo_markdown[:6000] if memo_markdown else "(no memo text)"

    return f"""You are a rigorous investment memo QA analyst. Your job is to find discrepancies between
the authoritative quantitative data and the text of the investment memo.

## AUTHORITATIVE DATA (ground truth — these numbers are correct)
{fact_str}

## MEMO TEXT (to audit)
{memo_excerpt}

## YOUR TASK
1. Extract every specific number (dollar amounts, percentages, multiples, tonnes, counts) from the memo text.
2. Cross-reference each number against the authoritative data above.
3. Flag any number in the memo that contradicts the authoritative data.
4. Flag any metric cited with different values in different parts of the memo.
5. Flag any logical claim in the memo that contradicts the quantitative data (e.g. "de-risked technology" when TRL=3).

Respond ONLY with a JSON array of findings. Each finding must have:
- "id": short snake_case identifier
- "severity": "error" | "warning" | "info"
- "category": "number_discrepancy" | "internal_consistency" | "logic"
- "title": one-line summary (max 10 words)
- "detail": explanation citing the memo text and the correct value
- "location": section name where the issue appears in the memo

If there are NO discrepancies, return an empty array [].
Do not wrap in markdown fences. Return only valid JSON."""


def _run_llm_qa(report: dict, inputs: dict, memo_markdown: str) -> list:
    """Call Claude to run the text-based QA check. Returns a list of findings."""
    try:
        import anthropic
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            logger.warning("QA LLM pass skipped: ANTHROPIC_API_KEY not set")
            return []

        client = anthropic.Anthropic(api_key=api_key)
        prompt = _build_qa_prompt(report, inputs, memo_markdown)

        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",   # fast + cheap for QA scanning
            max_tokens=2048,
            system="You are a financial document QA analyst. Return only valid JSON arrays.",
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in resp.content if b.type == "text").strip()

        # Strip markdown fences if model added them
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text)

        parsed = json.loads(text)
        if isinstance(parsed, list):
            # Validate each finding has required keys
            valid = []
            for f in parsed:
                if all(k in f for k in ("id", "severity", "category", "title", "detail")):
                    f.setdefault("location", "memo")
                    valid.append(f)
            return valid
        return []
    except Exception as e:
        logger.warning(f"QA LLM pass failed: {e}")
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

def run_qa_review(
    report: dict,
    inputs: dict,
    memo_markdown: Optional[str] = None,
    run_llm: bool = True,
) -> dict:
    """
    Run a complete QA review on a deal report.

    Parameters
    ----------
    report          : the full report dict (from report_json in the DB)
    inputs          : the inputs dict (from inputs_json in the DB)
    memo_markdown   : optional memo text to also audit
    run_llm         : whether to run the LLM pass (disable for fast/offline mode)

    Returns
    -------
    {
      "findings": [...],
      "summary": { "errors": int, "warnings": int, "infos": int, "total": int },
      "llm_pass_ran": bool,
    }
    """
    findings = []

    # Pass 1: deterministic rule checks
    findings += _check_ownership_math(report, inputs)
    findings += _check_postmoney(report, inputs)
    findings += _check_carbon_math(report)
    findings += _check_moic_irr_plausibility(report)
    findings += _check_concentration(report, inputs)
    findings += _check_missing_sections(report)
    findings += _check_followon_blended_math(report)

    # Pass 2: LLM-powered text mining (only if memo text is available)
    llm_ran = False
    if run_llm and memo_markdown and memo_markdown.strip():
        llm_findings = _run_llm_qa(report, inputs, memo_markdown)
        # Deduplicate by id (prefer LLM finding if same id as deterministic)
        existing_ids = {f["id"] for f in findings}
        for lf in llm_findings:
            if lf["id"] not in existing_ids:
                findings.append(lf)
                existing_ids.add(lf["id"])
        llm_ran = True

    # Severity ordering: error > warning > info
    _order = {"error": 0, "warning": 1, "info": 2}
    findings.sort(key=lambda f: _order.get(f.get("severity", "info"), 2))

    summary = {
        "errors":   sum(1 for f in findings if f.get("severity") == "error"),
        "warnings": sum(1 for f in findings if f.get("severity") == "warning"),
        "infos":    sum(1 for f in findings if f.get("severity") == "info"),
        "total":    len(findings),
    }

    return {
        "findings": findings,
        "summary": summary,
        "llm_pass_ran": llm_ran,
    }
