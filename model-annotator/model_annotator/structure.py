"""Structural analysis: period axes, units, cell census, color conventions,
and provisional sheet roles.

Everything here is deterministic and heuristic-transparent: each detection
records its evidence and a confidence, and low-confidence inferences surface
as limitations rather than silent assumptions.
"""
from __future__ import annotations

import datetime as _dt
import logging
import re
import statistics
from dataclasses import dataclass, field
from typing import Any, Optional

from .ingest import CellRecord, SheetData, WorkbookData, a1, is_number
from .schema import (
    CellKind,
    Granularity,
    InputColorConvention,
    Orientation,
    PeriodAxis,
    SheetRole,
    UnitsInfo,
    format_ref,
)

log = logging.getLogger(__name__)

SCAN_BAND = 14          # rows/cols scanned for period headers
MIN_RUN = 3             # minimum consecutive periods to accept an axis

_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")

_RE_YEAR_STR = re.compile(r"^(?:FY|CY)?\s*((?:19|20)\d{2})\s*[EAP]?$", re.I)
_RE_YEAR_N = re.compile(r"^Year\s*(\d{1,3})$", re.I)
_RE_QUARTER = re.compile(r"^(?:Q([1-4])[\s\-'/]*((?:19|20)?\d{2})|((?:19|20)?\d{2})[\s\-'/]*Q([1-4])|([1-4])Q[\s\-'/]*((?:19|20)?\d{2}))$", re.I)
_RE_MONTH_STR = re.compile(r"^(" + "|".join(_MONTHS) + r")[a-z]*[\s\-'/]*((?:19|20)?\d{2})$", re.I)


@dataclass
class PeriodToken:
    """One recognized period header cell."""
    row: int
    col: int
    label: str
    kind: str          # year | date | year_n | quarter | month
    sort_key: float    # comparable within kind


def _full_year(two_or_four: str) -> int:
    y = int(two_or_four)
    if y < 100:
        y += 2000 if y < 70 else 1900
    return y


def _classify_period_value(v: Any, number_format: str) -> Optional[tuple[str, str, float]]:
    """Return (canonical_label, kind, sort_key) if the value looks like a period header."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (_dt.datetime, _dt.date)):
        d = v.date() if isinstance(v, _dt.datetime) else v
        return (d.isoformat(), "date", d.toordinal())
    if is_number(v):
        if float(v).is_integer() and 1990 <= int(v) <= 2100:
            return (str(int(v)), "year", float(v))
        return None
    if isinstance(v, str):
        s = v.strip()
        m = _RE_YEAR_STR.match(s)
        if m:
            return (m.group(1), "year", float(m.group(1)))
        m = _RE_YEAR_N.match(s)
        if m:
            return (f"Year {int(m.group(1))}", "year_n", float(m.group(1)))
        m = _RE_QUARTER.match(s)
        if m:
            g = m.groups()
            if g[0] is not None:
                q, y = int(g[0]), _full_year(g[1])
            elif g[2] is not None:
                y, q = _full_year(g[2]), int(g[3])
            else:
                q, y = int(g[4]), _full_year(g[5])
            return (f"{y}Q{q}", "quarter", y * 4 + q)
        m = _RE_MONTH_STR.match(s)
        if m:
            mo = _MONTHS.index(m.group(1).lower()[:3]) + 1
            y = _full_year(m.group(2))
            return (f"{y}-{mo:02d}", "month", y * 12 + mo)
    return None


def _granularity_for(tokens: list[PeriodToken]) -> Granularity:
    kinds = {t.kind for t in tokens}
    if kinds <= {"year", "year_n"}:
        return Granularity.annual
    if "quarter" in kinds:
        return Granularity.quarterly
    if "month" in kinds:
        return Granularity.monthly
    if "date" in kinds:
        ordinals = sorted(t.sort_key for t in tokens)
        deltas = [b - a for a, b in zip(ordinals, ordinals[1:]) if b > a]
        if not deltas:
            return Granularity.unknown
        med = statistics.median(deltas)
        if med <= 45:
            return Granularity.monthly
        if med <= 135:
            return Granularity.quarterly
        if med <= 500:
            return Granularity.annual
    return Granularity.unknown


def _date_labels(tokens: list[PeriodToken], gran: Granularity) -> None:
    """Re-label raw ISO date tokens at the detected granularity."""
    for t in tokens:
        if t.kind != "date":
            continue
        d = _dt.date.fromordinal(int(t.sort_key))
        if gran == Granularity.annual:
            t.label = str(d.year)
        elif gran == Granularity.quarterly:
            t.label = f"{d.year}Q{(d.month - 1) // 3 + 1}"
        elif gran == Granularity.monthly:
            t.label = f"{d.year}-{d.month:02d}"


def _dedupe_labels(tokens: list[PeriodToken]) -> None:
    seen: dict[str, int] = {}
    for t in tokens:
        n = seen.get(t.label, 0)
        seen[t.label] = n + 1
        if n:
            t.label = f"{t.label} ({n + 1})"


def _runs_in_line(cells: list[tuple[int, int, CellRecord]]) -> list[list[PeriodToken]]:
    """Maximal runs of consecutive period-like cells along one row/column.

    Gaps of one blank cell are tolerated (models often skip a spacer column),
    but a non-period *value* breaks the run.
    """
    runs: list[list[PeriodToken]] = []
    current: list[PeriodToken] = []
    gap = 0
    for r, c, rec in cells:
        tok = _classify_period_value(rec.value, rec.number_format) if rec else None
        if tok:
            current.append(PeriodToken(row=r, col=c, label=tok[0], kind=tok[1], sort_key=tok[2]))
            gap = 0
        elif rec is None or rec.value is None:
            gap += 1
            if gap > 1 and current:
                runs.append(current)
                current = []
        else:
            if current:
                runs.append(current)
                current = []
            gap = 0
    if current:
        runs.append(current)
    return runs


def _monotone_prefix(tokens: list[PeriodToken]) -> list[PeriodToken]:
    """Trim a run to its longest STRICTLY increasing prefix.

    Strictness matters in practice: a repeated year (2022, 2022, ...) is the
    year-row of a monthly/quarterly two-row header, not an annual axis, and
    unrelated numbers that happen to look like years also break the sequence.
    """
    out: list[PeriodToken] = []
    for t in tokens:
        if out and t.sort_key <= out[-1].sort_key:
            break
        # mixed kinds (a year next to a quarter label) end the run too
        if out and t.kind != out[-1].kind and {t.kind, out[-1].kind} != {"year", "year_n"}:
            break
        out.append(t)
    return out


def _interleaved_axes(sd: SheetData, line, orientation) -> list[tuple[float, PeriodAxis]]:
    """Recover a single-granularity axis from a header that INTERLEAVES kinds.

    Real statements often lay out '2025 | Q1'26 Q2'26 Q3'26 Q4'26 | 2026 |
    Q1'27 .. | 2027 | 2028 2029 2030' on one row — the annual series the analyst
    reads is scattered across non-adjacent columns among the quarterly ones, so no
    contiguous same-kind run exists and detection finds nothing. Here we take, per
    granularity, the monotone same-kind subsequence across the whole line. Only
    fires when the line genuinely interleaves kinds (more period cells than the
    subsequence), so pure runs stay with the contiguous path above."""
    toks = []
    for r, c, rec in line:
        tp = _classify_period_value(rec.value, rec.number_format) if rec else None
        if tp:
            toks.append(PeriodToken(row=r, col=c, label=tp[0], kind=tp[1], sort_key=tp[2]))
    if len(toks) < MIN_RUN:
        return []
    by_kind: dict[str, list[PeriodToken]] = {}
    for t in toks:
        gk = "year" if t.kind in ("year", "year_n") else t.kind
        by_kind.setdefault(gk, []).append(t)
    out = []
    for _gk, ts in by_kind.items():
        mono = []
        for t in ts:                       # greedy strictly-increasing subsequence
            if not mono or t.sort_key > mono[-1].sort_key:
                mono.append(t)
        if len(mono) >= MIN_RUN and len(mono) < len(toks):
            out.append(_axis_from_run(sd, list(mono), orientation))
    return out


def detect_period_axes(sd: SheetData) -> list[PeriodAxis]:
    """All period axes on a sheet, best first. Empty list if none found."""
    candidates: list[tuple[float, PeriodAxis]] = []

    # Hypothesis A: periods in columns (scan top rows)
    for row in range(1, min(sd.max_row, SCAN_BAND) + 1):
        line = [(row, col, sd.cell(row, col)) for col in range(1, sd.max_col + 1)]
        for run in _runs_in_line(line):
            run = _monotone_prefix(run)
            if len(run) >= MIN_RUN:
                candidates.append(_axis_from_run(sd, run, Orientation.periods_in_columns))
        candidates.extend(_interleaved_axes(sd, line, Orientation.periods_in_columns))

    # Hypothesis B: periods in rows (scan left columns)
    for col in range(1, min(sd.max_col, SCAN_BAND) + 1):
        line = [(row, col, sd.cell(row, col)) for row in range(1, sd.max_row + 1)]
        for run in _runs_in_line(line):
            run = _monotone_prefix(run)
            if len(run) >= MIN_RUN:
                candidates.append(_axis_from_run(sd, run, Orientation.periods_in_rows))
        candidates.extend(_interleaved_axes(sd, line, Orientation.periods_in_rows))

    if not candidates:
        return []

    # Default hypothesis is periods-in-columns: a rows axis must clearly beat
    # the best columns axis (1.5x score) to override the default.
    col_axes = sorted((c for c in candidates if c[1].orientation == Orientation.periods_in_columns),
                      key=lambda x: -x[0])
    row_axes = sorted((c for c in candidates if c[1].orientation == Orientation.periods_in_rows),
                      key=lambda x: -x[0])
    ordered: list[tuple[float, PeriodAxis]]
    if col_axes and row_axes:
        ordered = col_axes + row_axes if row_axes[0][0] < 1.5 * col_axes[0][0] else row_axes + col_axes
    else:
        ordered = col_axes or row_axes

    # Prefer the earliest (leftmost/topmost) start among near-equal scores:
    # summary axes sit left of monthly detail blocks in real models.
    best_score = ordered[0][0]
    front = [ax for s, ax in ordered if s >= 0.6 * best_score and ax.orientation == ordered[0][1].orientation]
    rest = [ax for s, ax in ordered if ax not in front]
    front.sort(key=lambda ax: ax.header_cells[0] is None)  # stable; placeholder
    def _start_pos(ax: PeriodAxis) -> tuple[int, int]:
        from .schema import parse_ref
        sheet, cell = parse_ref(ax.header_cells[0])
        m = re.match(r"([A-Z]+)(\d+)", cell)
        col_letters, row_s = m.group(1), m.group(2)
        col_n = 0
        for ch in col_letters:
            col_n = col_n * 26 + (ord(ch) - 64)
        return (col_n, int(row_s)) if ax.orientation == Orientation.periods_in_columns else (int(row_s), col_n)
    front.sort(key=_start_pos)
    ordered_axes = front + rest

    # A sheet carrying both an annual summary block and a monthly/quarterly
    # detail block (yearly-left, monthly-right is a common layout) should lead
    # with the annual axis: that is the analyst-facing series. Purely
    # monthly/quarterly models have no annual axis and are untouched.
    annual = [ax for ax in ordered_axes if ax.granularity == Granularity.annual and len(ax.periods) >= 5]
    if annual and ordered_axes[0].granularity in (Granularity.monthly, Granularity.quarterly):
        rest2 = [ax for ax in ordered_axes if ax not in annual]
        ordered_axes = annual + rest2
    return ordered_axes


def _axis_from_run(sd: SheetData, run: list[PeriodToken], orientation: Orientation) -> tuple[float, PeriodAxis]:
    gran = _granularity_for(run)
    _date_labels(run, gran)
    _dedupe_labels(run)
    explicit_kinds = sum(1 for t in run if t.kind in ("year", "quarter", "month", "year_n"))
    score = len(run) + 2.0 * (explicit_kinds / len(run)) + (1.0 if gran != Granularity.unknown else 0.0)
    conf = min(0.95, 0.5 + 0.05 * len(run) + (0.2 if gran != Granularity.unknown else 0.0))
    axis = PeriodAxis(
        orientation=orientation,
        granularity=gran,
        header_cells=[format_ref(sd.name, a1(t.row, t.col)) for t in run],
        periods=[t.label for t in run],
        confidence=round(conf, 2),
    )
    return score, axis


# ---------------------------------------------------------------------------
# Units detection
# ---------------------------------------------------------------------------

_EXPLICIT_UNITS = [
    # Thousands. The lookbehinds keep "5,000 tpa" / "60000" from matching;
    # the grouped-digit form catches captions like "Summary Financials ($1,000s)".
    (re.compile(r"\bin\s+thousands\b|\$\s*000s?\b|(?<![\d,.])'000s?\b|\(\s*\$\s*k\s*\)|\$k\b"
                r"|US\$\s*thousands|\(\s*\$?\s*1?[,\s]?000s?\s*\)|\bin\s+\$\s*000s?\b", re.I),
     1_000.0, "$ thousands"),
    # Millions, incl. grouped "($1,000,000s)" and "($M)"/"($MM)".
    (re.compile(r"\bin\s+millions\b|\(\s*\$\s*mm?\s*\)|\$\s*mm\b|(?<![\d,.])\$m\b|US\$\s*millions"
                r"|\(\s*\$?\s*1?[,\s]?000[,\s]?000s?\s*\)", re.I),
     1_000_000.0, "$ millions"),
]

# A units marker in the left label/title columns that reads like a sheet caption
# ("Summary Financials ($1,000s)") applies to the whole sheet even if it appears
# only once and below the title band.
_CAPTION = re.compile(r"summary|financ|model|statement|projection|pro\s*forma|forecast|in\s*\$", re.I)


def detect_units(sd: SheetData) -> UnitsInfo:
    """Per-sheet unit scale with confidence and evidence.

    Explicit markers win. A trailing-comma number format (``#,##0,``) means
    values are STORED whole and merely displayed scaled — that is scale 1.
    """
    # Sheet-level markers must look sheet-level: a "($M)" inside one row label
    # half-way down the sheet describes that row, not the sheet (mixed-unit
    # sheets are a real trap). Accept a marker as sheet-wide only if it sits in
    # the title area (rows 1-4, cols 1-6) or recurs 3+ times in the header band.
    band = min(sd.max_row, 24)     # widened: real models caption units at row ~12
    hits: list[tuple[int, int, str, float, str]] = []
    for row in range(1, band + 1):
        for col in range(1, min(sd.max_col, 30) + 1):
            rec = sd.cell(row, col)
            # length guard: unit markers live in short header/title cells,
            # not in prose (where "5,000 tpa" style text causes false hits)
            if rec is None or not isinstance(rec.value, str) or len(rec.value) > 60:
                continue
            for rx, scale, label in _EXPLICIT_UNITS:
                if rx.search(rec.value):
                    hits.append((row, col, rec.value, scale, label))
    for row, col, text, scale, label in hits:
        # recurring markers must span 3+ distinct rows — markers repeated on a
        # single header row are per-column units (e.g. several "NPV ($M)" cols)
        rows_with_scale = {h[0] for h in hits if h[3] == scale}
        caption = col <= 3 and bool(_CAPTION.search(text))
        # a cell whose ENTIRE content is the denomination token ("$000", "in
        # thousands", "$M") in the top-left title block is an unambiguous sheet
        # denomination line, even a few rows below the title — this is exactly how
        # models caption units ("$000" on its own row under the plan name).
        denom_only = col <= 3 and row <= 12 and len(text.strip()) <= 16
        if (row <= 4 and col <= 6) or len(rows_with_scale) >= 3 or caption or denom_only:
            return UnitsInfo(
                scale=scale, label=label, confidence=0.95, explicit=True,
                evidence=f"{format_ref(sd.name, a1(row, col))} = {text!r}",
            )

    # Trailing-comma display format => stored whole units, displayed scaled.
    trailing_comma = 0
    numeric_cells = 0
    for _, _, rec in sd.iter_cells():
        if is_number(rec.value):
            numeric_cells += 1
            if re.search(r"[#0],(?:,|\s*[;\"']|$)", rec.number_format or ""):
                trailing_comma += 1
    if numeric_cells and trailing_comma / numeric_cells > 0.3:
        return UnitsInfo(
            scale=1.0, label="whole units (displayed in thousands via number format)",
            confidence=0.85, explicit=True,
            evidence=f"{trailing_comma}/{numeric_cells} numeric cells use a trailing-comma display format",
        )

    # No scaling caption and no trailing comma. If the modeler has explicitly
    # formatted the figures as currency/numbers (a "$" or accounting/number
    # format), they are final stored figures — read them as whole units with
    # real confidence, not a blind assumption. This is what separates a built
    # financial model from a bare grid, and it removes the false "unsure about
    # units" state that drove spurious cross-sheet unit flags. (Scale is 1.0
    # either way, so reported values are unchanged — only the confidence is.)
    money_fmt = 0
    for _, _, rec in sd.iter_cells():
        if is_number(rec.value) and abs(rec.value) >= 1:
            nf = rec.number_format or ""
            if not nf or nf == "General" or "%" in nf:
                continue
            # strip color codes ([Red]), locale tags ([$-409]) and quoted literals
            # ("$") BEFORE testing for date letters, else the 'd' in "[Red]" or a
            # "$" currency mark gets misread as a date/non-numeric format
            core = re.sub(r"\[[^\]]*\]", "", nf)
            core = re.sub(r'"[^"]*"', "", core)
            is_date = bool(re.search(r"[ymdhs]", core, re.I))
            if not is_date and re.search(r"[#0]", core):
                money_fmt += 1
    if numeric_cells and money_fmt / numeric_cells > 0.5:
        return UnitsInfo(
            scale=1.0, label="whole units", confidence=0.85, explicit=True,
            evidence=f"{money_fmt}/{numeric_cells} numeric cells carry explicit currency/number "
                     "formatting and there is no scaling caption",
        )

    return UnitsInfo(scale=1.0, label="whole units (assumed)", confidence=0.4, explicit=False,
                     evidence="no explicit unit marker found on this sheet")


# ---------------------------------------------------------------------------
# Cell census
# ---------------------------------------------------------------------------

@dataclass
class SheetCensus:
    kinds: dict[tuple[int, int], CellKind] = field(default_factory=dict)
    n_formula: int = 0
    n_typed_input: int = 0
    n_label: int = 0
    n_header: int = 0
    n_error: int = 0

    def kind(self, row: int, col: int) -> CellKind:
        return self.kinds.get((row, col), CellKind.empty)


def census_sheet(sd: SheetData, axes: list[PeriodAxis]) -> SheetCensus:
    header_coords: set[tuple[int, int]] = set()
    for ax in axes:
        for ref in ax.header_cells:
            m = re.match(r".*!([A-Z]+)(\d+)$", ref.replace("'", ""))
            if m:
                col_n = 0
                for ch in m.group(1):
                    col_n = col_n * 26 + (ord(ch) - 64)
                header_coords.add((int(m.group(2)), col_n))

    cen = SheetCensus()
    for r, c, rec in sd.iter_cells():
        if rec.value is None and rec.formula is None:
            continue
        if rec.is_error:
            kind = CellKind.error
            cen.n_error += 1
        elif rec.is_formula:
            kind = CellKind.formula
            cen.n_formula += 1
        elif (r, c) in header_coords:
            kind = CellKind.header
            cen.n_header += 1
        elif is_number(rec.value) or isinstance(rec.value, (_dt.date, _dt.datetime)) or isinstance(rec.value, bool):
            kind = CellKind.typed_input
            cen.n_typed_input += 1
        else:
            kind = CellKind.label
            cen.n_label += 1
        cen.kinds[(r, c)] = kind
    return cen


# ---------------------------------------------------------------------------
# Input color convention
# ---------------------------------------------------------------------------

_COLOR_WORDS = {
    "grey": ["theme:0", "rgb:FFD9D9D9", "rgb:FFF2F2F2", "rgb:FFBFBFBF", "rgb:FFE7E6E6"],
    "gray": ["theme:0", "rgb:FFD9D9D9", "rgb:FFF2F2F2", "rgb:FFBFBFBF", "rgb:FFE7E6E6"],
    "tan": ["theme:9", "rgb:FFFFE4C4", "rgb:FFFCE4D6"],
    "green": ["theme:6", "rgb:FF92D050", "rgb:FFC6EFCE", "rgb:FFE2EFDA"],
    "blue": ["theme:4", "rgb:FFDDEBF7", "rgb:FFBDD7EE"],
    "yellow": ["rgb:FFFFFF00", "rgb:FFFFF2CC"],
    "orange": ["rgb:FFFFC000", "rgb:FFFCE4D6"],
}

_RE_DECLARED = re.compile(
    r"\b(grey|gray|tan|green|blue|yellow|orange)\b.{0,50}\bcells?\b.{0,60}\b(input|assumption)", re.I)


def detect_input_convention(wbd: WorkbookData, censuses: dict[str, SheetCensus]) -> InputColorConvention:
    conv = InputColorConvention()

    # 1) Declared convention on a documentation-like sheet
    for sd in wbd.sheets.values():
        for r, c, rec in sd.iter_cells():
            if isinstance(rec.value, str) and len(rec.value) < 300:
                m = _RE_DECLARED.search(rec.value)
                if m:
                    conv.declared_in = format_ref(sd.name, a1(r, c))
                    conv.description = rec.value.strip()
                    conv.detected = True
                    color_word = m.group(1).lower()
                    conv.fill_key = _modal_input_fill(wbd, censuses, prefixes=_COLOR_WORDS.get(color_word))
                    break
        if conv.declared_in:
            break

    # 2) Statistical convention: modal fill among typed inputs
    if conv.fill_key is None:
        modal = _modal_input_fill(wbd, censuses, prefixes=None)
        if modal:
            conv.fill_key = modal
            conv.detected = True
            if not conv.description:
                conv.description = f"Most typed inputs carry fill {modal} (inferred convention)"

    # 3) Consistency: typed numeric cells WITHOUT the convention fill that sit
    #    in formula-dominated rows are convention violations (typed numbers
    #    hiding mid-formula-row).
    if conv.fill_key:
        violations: list[str] = []
        for name, sd in wbd.sheets.items():
            cen = censuses.get(name)
            if cen is None:
                continue
            row_kinds: dict[int, list[CellKind]] = {}
            for (r, c), k in cen.kinds.items():
                row_kinds.setdefault(r, []).append(k)
            for (r, c), k in cen.kinds.items():
                if k != CellKind.typed_input:
                    continue
                rec = sd.cell(r, c)
                if rec is None or not is_number(rec.value):
                    continue
                if rec.fill_key == conv.fill_key:
                    continue
                kinds = row_kinds.get(r, [])
                n_formula = sum(1 for x in kinds if x == CellKind.formula)
                if n_formula >= 4 and n_formula / max(len(kinds), 1) > 0.6:
                    violations.append(format_ref(name, a1(r, c)))
        conv.violations = sorted(violations)[:40]
        conv.consistent = len(violations) == 0
    return conv


def _modal_input_fill(wbd: WorkbookData, censuses: dict[str, SheetCensus],
                      prefixes: Optional[list[str]]) -> Optional[str]:
    from collections import Counter
    counts: Counter[str] = Counter()
    total = 0
    for name, sd in wbd.sheets.items():
        cen = censuses.get(name)
        if cen is None:
            continue
        for (r, c), k in cen.kinds.items():
            if k != CellKind.typed_input:
                continue
            rec = sd.cell(r, c)
            if rec is None or not is_number(rec.value):
                continue
            total += 1
            if rec.fill_key:
                counts[rec.fill_key] += 1
    if not counts or total < 10:
        return None
    if prefixes:
        for key, _ in counts.most_common():
            if any(key.startswith(p.split("/")[0]) for p in prefixes):
                return key
    key, n = counts.most_common(1)[0]
    return key if n / total >= 0.35 else None


# ---------------------------------------------------------------------------
# Provisional sheet roles (refined later with the formula graph)
# ---------------------------------------------------------------------------

_ROLE_KEYWORDS: dict[SheetRole, list[str]] = {
    SheetRole.statements: [
        "revenue", "cogs", "cost of goods", "gross profit", "gross margin", "ebitda", "ebit",
        "net income", "operating expenses", "opex", "cash flow", "ending cash", "income statement",
        "balance sheet", "pro forma", "p&l", "free cash flow", "beginning cash",
    ],
    SheetRole.assumptions: [
        "assumption", "input", "driver", "scenario", "toggle", "switch", "growth rate",
        "price per", "asp", "salary", "% of revenue",
    ],
    SheetRole.schedule: [
        "fid", "cod", "pipeline", "project", "hiring", "headcount", "production", "capacity",
        "build", "deployment", "installs", "units", "schedule", "ramp",
    ],
    SheetRole.cost_buildup: [
        "bom", "bill of materials", "cost breakdown", "unit cost", "cost per unit", "tier",
        "component", "raw material", "yield", "cost model", "cost curve",
    ],
    SheetRole.valuation: [
        "valuation", "multiple", "discount rate", "dcf", "exit", "irr", "npv", "terminal value",
        "cap table", "ownership", "dilution",
    ],
    SheetRole.documentation: [
        "documentation", "readme", "instructions", "legend", "change log", "disclaimer", "glossary",
    ],
}

_ROLE_NAME_HINTS: list[tuple[re.Pattern, SheetRole]] = [
    (re.compile(r"doc|readme|instructions|legend|cover", re.I), SheetRole.documentation),
    (re.compile(r"assum|input|in_out|driver", re.I), SheetRole.assumptions),
    (re.compile(r"p&l|pnl|pro.?forma|financ|statement|is_|cf_|cash", re.I), SheetRole.statements),
    (re.compile(r"valuation|returns|cap.?table|exit", re.I), SheetRole.valuation),
    (re.compile(r"cost|bom|curve", re.I), SheetRole.cost_buildup),
    (re.compile(r"project|schedule|pipeline|mfg|manufactur|production|hiring", re.I), SheetRole.schedule),
    (re.compile(r"chart|dash|graph|tornado|sensitivit|summary|output", re.I), SheetRole.presentation),
    (re.compile(r"scratch|temp|old|backup|copy", re.I), SheetRole.scratch),
]


def provisional_roles(wbd: WorkbookData, censuses: dict[str, SheetCensus]) -> dict[str, tuple[SheetRole, float]]:
    out: dict[str, tuple[SheetRole, float]] = {}
    for name, sd in wbd.sheets.items():
        scores: dict[SheetRole, float] = {r: 0.0 for r in SheetRole}
        for rx, role in _ROLE_NAME_HINTS:
            if rx.search(name):
                scores[role] += 3.0
        labels = [str(rec.value).lower() for _, _, rec in sd.iter_cells()
                  if isinstance(rec.value, str) and 2 < len(rec.value) < 80]
        text = " | ".join(labels[:3000])
        for role, kws in _ROLE_KEYWORDS.items():
            for kw in kws:
                if kw in text:
                    scores[role] += 1.0
        cen = censuses.get(name)
        if cen:
            populated = max(len(cen.kinds), 1)
            if cen.n_formula / populated > 0.5:
                scores[SheetRole.statements] += 1.0
                scores[SheetRole.schedule] += 0.5
            if cen.n_typed_input / populated > 0.4:
                scores[SheetRole.assumptions] += 1.5
        best = max((s, r) for r, s in scores.items() if r != SheetRole.unknown)
        runner = max((s for r, s in scores.items() if r not in (best[1], SheetRole.unknown)), default=0.0)
        if best[0] <= 0:
            out[name] = (SheetRole.unknown, 0.0)
        else:
            margin = (best[0] - runner) / best[0]
            out[name] = (best[1], round(min(0.95, 0.4 + 0.55 * margin), 2))
    return out


# ---------------------------------------------------------------------------
# Top-level structure pass
# ---------------------------------------------------------------------------

@dataclass
class StructureResult:
    axes: dict[str, list[PeriodAxis]] = field(default_factory=dict)        # all axes per sheet, best first
    units: dict[str, UnitsInfo] = field(default_factory=dict)
    censuses: dict[str, SheetCensus] = field(default_factory=dict)
    roles: dict[str, tuple[SheetRole, float]] = field(default_factory=dict)
    input_convention: InputColorConvention = field(default_factory=InputColorConvention)
    limitations: list[str] = field(default_factory=list)

    def primary_axis(self, sheet: str) -> Optional[PeriodAxis]:
        axes = self.axes.get(sheet) or []
        return axes[0] if axes else None


def build_structure(wbd: WorkbookData) -> StructureResult:
    res = StructureResult()
    for name, sd in wbd.sheets.items():
        res.axes[name] = detect_period_axes(sd)
        res.units[name] = detect_units(sd)
        res.censuses[name] = census_sheet(sd, res.axes[name])
        if res.units[name].confidence < 0.6 and res.censuses[name].n_formula > 20:
            res.limitations.append(
                f"Units on sheet '{name}' inferred with {res.units[name].confidence:.1f} confidence "
                f"({res.units[name].label}); treat absolute magnitudes there with care."
            )
    res.roles = provisional_roles(wbd, res.censuses)
    res.input_convention = detect_input_convention(wbd, res.censuses)
    return res
