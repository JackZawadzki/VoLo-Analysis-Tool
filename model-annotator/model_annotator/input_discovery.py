"""Genuine-assumption discovery for sensitivity analysis.

The tornado must flex the ASSUMPTIONS a human typed into the model — prices,
volumes, cost rates, tax, financing — not unit-scale factors, enum markers,
years, or subtotal echoes. This module supplies the generic, deterministic
building blocks:

  * classify_type(): label ∧ number_format ∧ magnitude → an InputType carrying a
    context-appropriate range (±% for level inputs, ±percentage-POINTS for rates
    — a ±20% move on 3% inflation is invisible; ±1.5pp is the real band) and
    validity guards (prices ≥ 0, rates boxed, integers rounded).
  * genuineness_score(): weighted structural predicates that keep genuine inputs
    and sink non-inputs (scale factors, years, placeholders, subtotals).
  * is_enum_key(): small integers consumed by equality tests (IF(x=1), MATCH,
    lookup keys) are switches, not quantities — flexing them is meaningless.

Everything is keyed to detected conditions — never to sheet names or addresses.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# ---------------------------------------------------------------------------
# Input types and context-appropriate ranges
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class InputType:
    name: str          # 'unit_price' | 'tax_rate' | ...
    mode: str          # 'pct' (multiply) | 'pp' (shift percentage points)
    low: float         # pct: fraction of base; pp: absolute delta (rate units)
    high: float
    lo_clamp: Optional[float] = None   # validity box (after the move)
    hi_clamp: Optional[float] = None
    integer: bool = False              # round the flexed value (counts, years of life)


# order matters: first match in the cascade wins
_TYPE_RULES: list[tuple[re.Pattern, InputType]] = [
    (re.compile(r"tax\s*rate|nol|tax\s*credit", re.I),
     InputType("tax_rate", "pp", -0.05, 0.05, 0.0, 0.6)),
    (re.compile(r"inflation|escalat|indexation|cola\b", re.I),
     InputType("inflation_rate", "pp", -0.015, 0.015, 0.0, 0.3)),
    # a cost escalator ("salary yearly increase") is an inflation-like band, not
    # a revenue-growth band — ±15pp on a 5% raise would model salary CUTS
    (re.compile(r"(salar|wage|cost|price)\w*\s+(yearly\s+|annual\s+)?(increase|growth)", re.I),
     InputType("cost_escalator", "pp", -0.02, 0.02, 0.0, 0.3)),
    (re.compile(r"margin", re.I),
     InputType("margin_pct", "pp", -0.10, 0.10, 0.0, 1.0)),
    (re.compile(r"growth|increase|ramp|cagr", re.I),
     InputType("growth_rate", "pp", -0.15, 0.15, -1.0, 3.0)),
    (re.compile(r"discount\s*rate|wacc|hurdle", re.I),
     InputType("discount_rate", "pp", -0.05, 0.05, 0.01, 0.8)),
    (re.compile(r"interest|coupon|apr\b|pik\b", re.I),
     InputType("interest_rate", "pp", -0.02, 0.02, 0.0, 0.4)),
    (re.compile(r"utili[sz]ation|attach|conversion|penetration|churn|retention|yield", re.I),
     InputType("adoption_rate", "pp", -0.10, 0.10, 0.0, 1.0)),
    (re.compile(r"pric(e|ing)|asp\b|€/|\$/|per unit|/unit|rate card|fee\b|royalt", re.I),
     InputType("unit_price", "pct", -0.15, 0.15, 0.0, None)),
    (re.compile(r"volume|units?\b|quantit|capacity|kwh|mwh|throughput|orders?\b|customers?\b|clients?\b|deals?\b", re.I),
     InputType("unit_volume", "pct", -0.25, 0.25, 0.0, None)),
    (re.compile(r"capex|capital expenditure|equipment|tooling|build.?out", re.I),
     InputType("capex", "pct", -0.20, 0.20, None, None)),   # sign preserved via pct
    (re.compile(r"raise|funding|equity|investment|tranche|round\b|proceeds|issuance", re.I),
     InputType("fundraise", "pct", -0.25, 0.25, 0.0, None)),
    (re.compile(r"useful life|amorti[sz]ation period|depreciation period|years?\b", re.I),
     InputType("useful_life", "pct", -0.20, 0.20, 1.0, None, True)),
    (re.compile(r"salar|headcount|fte\b|employees|hires?\b|staff", re.I),
     InputType("headcount_cost", "pct", -0.15, 0.15, 0.0, None)),
    (re.compile(r"cost|cogs|opex|expense|spend|material|freight|overhead|r&d|sg&a", re.I),
     InputType("cost", "pct", -0.15, 0.15, 0.0, None)),
    (re.compile(r"multiple|\bx\b", re.I),
     InputType("exit_multiple", "pct", -0.30, 0.30, 0.1, None)),
]

_GENERIC_PCT = InputType("generic_rate", "pp", -0.10, 0.10, 0.0, 1.0)
_GENERIC_AMT = InputType("generic_amount", "pct", -0.20, 0.20, None, None)
# a normalized deployment/phasing curve (monthly fractions summing to ~1/yr) is
# flexed as a TIME SHIFT (commercialization slips k months), never ±pp — moving
# every month by ±10pp would swing a normalized year by ±120% and break the
# model's own sum-to-1 checks
PHASING = InputType("phasing_curve", "shift", 0.0, 6.0, 0.0, None)


def classify_type(label: str, number_format: str, value: float,
                  curve_hint: bool = False) -> InputType:
    """Priority cascade over label semantics, then number-format/magnitude.
    Falls back to a generic ±20% amount — never fails."""
    lab = label or ""
    fmt = number_format or ""
    if curve_hint:
        return PHASING
    for rx, itype in _TYPE_RULES:
        if rx.search(lab):
            # a genuine RATE is bounded ~[0,1.5]: a large magnitude with no %
            # format is not a rate no matter what the label says ("Volume growth"
            # holding 3.7e6 is a volume). Skip pp/clamped rules there so its range
            # doesn't collapse against the [0,1] box.
            if itype.mode == "pp" and abs(value) > 1.5 and "%" not in fmt:
                continue
            # a %-formatted 'price adjustment' style cell is still a rate
            if itype.mode == "pct" and "%" in fmt and -1.5 <= value <= 1.5:
                return _GENERIC_PCT
            return itype
    if "%" in fmt and -1.5 <= value <= 1.5:
        return _GENERIC_PCT
    return _GENERIC_AMT


def ranged_vals(itype: InputType, base: float) -> tuple[float, float]:
    """The flexed (low, high) values for one cell, mode-aware and guard-clamped.
    Guards CLAMP rather than discard, so a rate near its box edge still flexes
    inside validity (no negative prices, margins > 100%, funding < 0)."""
    if itype.mode == "pp":
        lo, hi = base + itype.low, base + itype.high
    else:
        lo, hi = base * (1.0 + itype.low), base * (1.0 + itype.high)
        if base < 0:                      # sign-preserving for negative lines (capex outflows)
            lo, hi = base * (1.0 + itype.high), base * (1.0 + itype.low)
    if itype.lo_clamp is not None:
        lo, hi = max(lo, itype.lo_clamp), max(hi, itype.lo_clamp)
    if itype.hi_clamp is not None:
        lo, hi = min(lo, itype.hi_clamp), min(hi, itype.hi_clamp)
    if itype.integer:
        lo, hi = float(round(lo)), float(round(hi))
    return (lo, hi) if lo <= hi else (hi, lo)


# ---------------------------------------------------------------------------
# Genuineness scoring — keep real assumptions, sink artifacts
# ---------------------------------------------------------------------------

_SCALE_WORD = re.compile(r"\b(factor|scal(e|ing)|multiplier|divisor|conversion|thousands?|millions?|'?000s?|\bmm\b|\bmn\b)\b", re.I)
_SEMANTIC = re.compile(r"price|rate|margin|growth|cost|tax|volume|units?|capacity|life|salar|capex|"
                       r"fund|raise|equity|fee|royalt|discount|inflation|utili[sz]ation|churn|"
                       r"customers?|clients?|headcount|kwh|mwh|credit|expense|spend|revenue|contract", re.I)
_OUTPUTISH = re.compile(r"\btotal\b|\bnet\b|gross profit|cumulative|balance|\bactuals?\b|subtotal|check\b", re.I)
_ASSUME_SHEET = re.compile(r"input|assumption|driver|global|scenario|lever|setting", re.I)
_PLACEHOLDER = re.compile(r"^(#?\d+|[a-z]|x+|tbd|n/?a|—|-)$|#\d+\s*$", re.I)


def is_scale_factor(label: str, value: float) -> bool:
    """A pure unit-scale multiplier: a round power of ten and/or a scale-word
    label with no business semantics. Flexing it rescales every output by the
    same fraction — a meaningless 'driver' that must never headline a tornado."""
    v = abs(value)
    power_of_ten = v >= 1000 and abs(v - round(v)) < 1e-9 and float(f"{v:.0e}"[0]) == 1 \
        and set(f"{int(round(v))}") <= {"0", "1"}
    has_scale_word = bool(_SCALE_WORD.search(label or ""))
    has_semantics = bool(_SEMANTIC.search(label or ""))
    canonical = v in (1000.0, 1e6, 1e9, 0.001, 1e-6)
    # a canonical scale value + a scale word wins even if the label also carries a
    # business word ("Units in thousands" is a units-of-measure note, not a volume)
    return (power_of_ten and has_scale_word) or (power_of_ten and not has_semantics) \
        or (has_scale_word and canonical) \
        or (has_scale_word and not has_semantics and canonical)


def genuineness_score(label: str, number_format: str, value: float,
                      in_cone: bool, in_rev_and_eb: bool, in_rev_only: bool,
                      has_input_fill: bool, on_assumption_sheet: bool) -> float:
    """Weighted structural predicates (scored, not gated): positive evidence the
    cell is a human-entered assumption, negative for artifacts. Keep score > 0."""
    lab = label or ""
    fmt = number_format or ""
    s = 0.0
    s += 3.0 if in_cone else -6.0                                  # P1: feeds a key output
    if in_rev_and_eb:
        s += 1.5                                                   # P2: cross-cutting lever
    elif in_rev_only:
        s += 0.8
    if has_input_fill:
        s += 2.0                                                   # P3: model's own input color
    if on_assumption_sheet:
        s += 1.2                                                   # P4
    if _SEMANTIC.search(lab):
        s += 1.8                                                   # P5: semantic label
    elif "%" in fmt or "$" in fmt or "€" in fmt:
        s += 0.6
    if "%" in fmt and 0.0 <= value <= 1.5:
        s += 0.8                                                   # P7: a plausible rate
    if is_scale_factor(lab, value):
        s -= 5.0                                                   # N1: unit-scale multiplier
    if _OUTPUTISH.search(lab):
        s -= 2.5                                                   # N2: output/subtotal echo
    if abs(value - round(value)) < 1e-9 and 1990 <= value <= 2100 and "%" not in fmt \
            and not re.search(r"life|multiple|price", lab, re.I):
        s -= 3.0                                                   # N3: a stray year
    core = fmt.split(";")[0] if fmt else ""
    if re.search(r"[ymd]", re.sub(r"\[[^\]]*\]|\"[^\"]*\"", "", core), re.I) and "%" not in core:
        s -= 4.0                                                   # N4: date-formatted
    if not lab.strip() or _PLACEHOLDER.match(lab.strip()):
        s -= 2.0                                                   # N5: no recoverable label
    return s


def sheet_is_assumptionish(name: str, role_value: Optional[str]) -> bool:
    return role_value == "assumptions" or bool(_ASSUME_SHEET.search(name or ""))


# ---------------------------------------------------------------------------
# Enum/marker keys — integers consumed by equality tests, not arithmetic
# ---------------------------------------------------------------------------

_EQ_USE = re.compile(r"(?:=\s*(?:C\d)|IF\s*\(|MATCH\s*\(|LOOKUP)", re.I)


def _enum_use_in(formula: str, cellpart: str, sheet: str) -> Optional[bool]:
    """Does this formula consume the cell as a SWITCH/CODE rather than a quantity?
    True = equality/branch-argument use; False = arithmetic use; None = no ref."""
    f = formula.replace("$", "")
    # the cell may be referenced bare (H867) or sheet-qualified ('Global Inputs'!H867)
    pats = [rf"(?<![A-Z0-9_.!]){re.escape(cellpart)}(?![0-9])",
            rf"'?{re.escape(sheet)}'?!{re.escape(cellpart)}(?![0-9])"]
    hit_eq = hit_arith = False
    for pat in pats:
        for m in re.finditer(pat, f, re.I):
            before = f[:m.start()].rstrip()
            after = f[m.end():].lstrip()
            if before.endswith("=") or after.startswith("="):
                hit_eq = True                       # IF(x=CELL / CELL=3
            elif (before[-1:] in (",", "(") and after[:1] in (",", ")")) and \
                    re.search(r"\b(IF|CHOOSE|MATCH|LOOKUP|XLOOKUP|SWITCH|INDEX)\s*\(", f, re.I):
                hit_eq = True                       # passed whole as a branch arm / lookup key
            elif before[-1:] in "+-*/^&<>" or after[:1] in "+-*/^&<>":
                hit_arith = True                    # genuine arithmetic input
    if hit_eq and not hit_arith:
        return True
    if hit_arith:
        return False
    return None


def is_enum_key(graph, wbd, node: int, value: float) -> bool:
    """A small integer whose dependents only COMPARE or BRANCH on it (IF(x=2),
    CHOOSE(x,...), MATCH, lookup keys) is a scenario switch/unit code, not a
    quantity — a ±25% flex of '3' into 2.25 silently reroutes CHOOSE() branches
    (verified: a units-of-measure enum flexed this way selected a ×1,000,000
    GWh branch and blew EBITDA to ±1e12). Follows one pass-through hop, since
    codes are often copied ('=Inputs!H867') before the CHOOSE consumes them."""
    if abs(value) > 20 or abs(value - round(value)) > 1e-9:
        return False
    from .graph import decode

    def _cell_of(nd):
        si, r, c = decode(nd)
        sn = graph.sheet_names[si]
        sd = wbd.sheets.get(sn)
        return sn, (sd.cell(r, c) if sd else None)

    src_sheet = graph.sheet_names[decode(node)[0]]
    src_part = graph.node_ref(node).split("!")[-1].replace("$", "")
    checked = eq_hits = 0
    for d in list(graph.dependents.get(node, ()))[:12]:
        try:
            dep_sheet, rec = _cell_of(d)
            f = rec.formula if rec else None
        except Exception:
            continue
        if not f:
            continue
        use = _enum_use_in(f, src_part, src_sheet)
        if use is None:
            continue
        checked += 1
        if use:
            eq_hits += 1
            continue
        # pass-through hop: '=Inputs!H867' style copies — inspect what consumes the COPY
        if re.fullmatch(r"=\s*(?:'[^']+'|[A-Za-z0-9_. ]+)?!?\$?[A-Z]{1,3}\$?\d+\s*", f):
            dep_part = graph.node_ref(d).split("!")[-1].replace("$", "")
            sub_eq = sub_n = 0
            for d2 in list(graph.dependents.get(d, ()))[:12]:
                try:
                    _s2, rec2 = _cell_of(d2)
                    f2 = rec2.formula if rec2 else None
                except Exception:
                    continue
                if not f2:
                    continue
                use2 = _enum_use_in(f2, dep_part, dep_sheet)
                if use2 is None:
                    continue
                sub_n += 1
                sub_eq += bool(use2)
            if sub_n and sub_eq >= max(1, sub_n // 2):
                eq_hits += 1
    return checked > 0 and eq_hits >= max(1, checked // 2)
