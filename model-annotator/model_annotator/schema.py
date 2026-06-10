"""Pydantic output models for model-annotator.

This module is the contract between the pipeline phases and between this tool
and the downstream underwriting engine. Any change to the shapes here requires
a `schema_version` bump and a CHANGELOG entry.

Anti-hallucination invariant: every numeric value in these models traces to
either a workbook cell citation (``Sheet!A1``) or a Python computation whose
inputs are all cell citations. The ``inputs``/``evidence_cells`` fields are not
decorative — the citation validator re-reads the workbook and asserts they
exist and match.
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1.0"
TOOL_VERSION = "0.1.0"


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Severity(str, Enum):
    critical = "critical"
    high = "high"
    medium = "medium"
    info = "info"


SEVERITY_ORDER = {Severity.critical: 0, Severity.high: 1, Severity.medium: 2, Severity.info: 3}


class SheetRole(str, Enum):
    statements = "statements"
    assumptions = "assumptions"
    schedule = "schedule"
    cost_buildup = "cost_buildup"
    valuation = "valuation"
    presentation = "presentation"
    documentation = "documentation"
    scratch = "scratch"
    unknown = "unknown"


class Orientation(str, Enum):
    periods_in_columns = "periods_in_columns"
    periods_in_rows = "periods_in_rows"


class Granularity(str, Enum):
    annual = "annual"
    quarterly = "quarterly"
    monthly = "monthly"
    unknown = "unknown"

    @property
    def periods_per_year(self) -> int:
        return {"annual": 1, "quarterly": 4, "monthly": 12}.get(self.value, 1)


class TieOutStatus(str, Enum):
    passed = "pass"
    failed = "fail"
    not_applicable = "not_applicable"
    unfalsifiable = "unfalsifiable"


class CellKind(str, Enum):
    typed_input = "typed_input"
    formula = "formula"
    label = "label"
    header = "header"
    error = "error"
    empty = "empty"


# ---------------------------------------------------------------------------
# Canonical ontology
# ---------------------------------------------------------------------------

# Scalar (one row expected) canonical ids.
CANONICAL_IDS: frozenset[str] = frozenset({
    "revenue_total",
    "grant_or_subsidy_revenue",
    "cogs_total",
    "gross_profit",
    "gross_margin_pct",
    "opex_total",
    "ebitda_or_ebit",
    "depreciation",
    "interest_net",
    "taxes",
    "net_income",
    "cf_operating",
    "capex",
    "cf_investing",
    "debt_proceeds",
    "equity_proceeds",
    "cf_financing",
    "net_change_cash",
    "beginning_cash",
    "ending_cash",
    "balance_check",
    "headcount",
    "market_size",
    "market_share",
    "valuation_multiple",
    "discount_rate",
    "investment_in",
    "accounts_receivable",
    "accounts_payable",
    "inventory",
})

# Multi-instance canonical ids (each mapped row carries an `instance` label).
CANONICAL_MULTI_IDS: frozenset[str] = frozenset({
    "revenue_segment",
    "cogs_component",
    "opex_component",
    "units_sold",
    "asp",
    "capacity",          # instance distinguishes operational / contracted / nameplate ...
    "cost_tier",         # cost-buildup rows: instance = product/tier label
})

OPEX_SUBTAGS: frozenset[str] = frozenset({
    "capex_expensed", "rnd", "sm", "ga", "labor", "rent", "support", "other",
})

ALL_CANONICAL_IDS: frozenset[str] = CANONICAL_IDS | CANONICAL_MULTI_IDS


# ---------------------------------------------------------------------------
# Cell references
# ---------------------------------------------------------------------------

_SHEET_NEEDS_QUOTES = re.compile(r"[^A-Za-z0-9_.]")


def format_ref(sheet: str, cell: str) -> str:
    """Canonical ``Sheet!A1`` reference; quotes sheet names that need it."""
    if _SHEET_NEEDS_QUOTES.search(sheet):
        return f"'{sheet}'!{cell}"
    return f"{sheet}!{cell}"


def parse_ref(ref: str) -> tuple[str, str]:
    """Inverse of :func:`format_ref`. Returns ``(sheet, cell)``."""
    if "!" not in ref:
        raise ValueError(f"not a qualified cell reference: {ref!r}")
    sheet, cell = ref.rsplit("!", 1)
    if sheet.startswith("'") and sheet.endswith("'"):
        sheet = sheet[1:-1].replace("''", "'")
    return sheet, cell


class CellCitation(BaseModel):
    """A value claim tied to a specific workbook cell."""
    model_config = ConfigDict(frozen=True)

    ref: str = Field(description="Qualified reference, e.g. \"Pro_Forma!H16\"")
    value: Any = None
    label: Optional[str] = None


# ---------------------------------------------------------------------------
# Workbook map
# ---------------------------------------------------------------------------

class UnitsInfo(BaseModel):
    scale: float = Field(1.0, description="Multiplier to whole currency units: 1, 1e3, 1e6")
    label: str = "whole units"
    confidence: float = Field(ge=0.0, le=1.0)
    explicit: bool = False
    evidence: str = ""


class PeriodAxis(BaseModel):
    orientation: Orientation
    granularity: Granularity
    header_cells: list[str] = Field(default_factory=list, description="Qualified refs of period header cells, in order")
    periods: list[str] = Field(default_factory=list, description="Canonical period labels, in order")
    confidence: float = Field(ge=0.0, le=1.0)


class SheetMapEntry(BaseModel):
    name: str
    role: SheetRole = SheetRole.unknown
    role_confidence: float = 0.0
    visibility: str = "visible"  # visible | hidden | veryHidden
    n_rows: int = 0
    n_cols: int = 0
    n_formula_cells: int = 0
    n_typed_input_cells: int = 0
    n_label_cells: int = 0
    n_error_cells: int = 0
    period_axis: Optional[PeriodAxis] = None
    units: Optional[UnitsInfo] = None
    notes: list[str] = Field(default_factory=list)


class InputColorConvention(BaseModel):
    detected: bool = False
    fill_key: Optional[str] = None       # e.g. "theme:0/tint:-0.05" or "rgb:FFD9D9D9"
    description: str = ""
    declared_in: Optional[str] = None    # citation if a Documentation sheet declares it
    consistent: Optional[bool] = None
    violations: list[str] = Field(default_factory=list)  # refs of typed inputs without the fill, mid-formula-row


class RowMapping(BaseModel):
    canonical_id: str
    instance: Optional[str] = None       # segment / component / tier name for multi-ids
    subtags: list[str] = Field(default_factory=list)
    sheet: str
    row: int
    label_text: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: str = ""
    demoted: bool = False                # failed its mandatory verification
    cells: dict[str, str] = Field(default_factory=dict, description="period label -> qualified cell ref")


class WorkbookMap(BaseModel):
    sheets: list[SheetMapEntry] = Field(default_factory=list)
    period_axis: Optional[PeriodAxis] = Field(None, description="Primary axis (from the primary statement sheet)")
    primary_statement_sheet: Optional[str] = None
    units: dict[str, UnitsInfo] = Field(default_factory=dict, description="per-sheet units")
    row_mappings: list[RowMapping] = Field(default_factory=list)
    named_ranges: dict[str, str] = Field(default_factory=dict)
    has_macros: bool = False
    hidden_sheets: list[str] = Field(default_factory=list)
    input_color_convention: Optional[InputColorConvention] = None
    trust_score: float = Field(1.0, ge=0.0, le=1.0)


# ---------------------------------------------------------------------------
# Assumptions census
# ---------------------------------------------------------------------------

class AssumptionEntry(BaseModel):
    cell: str
    label: str
    value: Any = None
    impact_rank: int = 0
    dependent_statement_cells: int = 0
    flat_all_periods: Optional[bool] = None
    comment_text: Optional[str] = None


# ---------------------------------------------------------------------------
# Tie-outs
# ---------------------------------------------------------------------------

class TieOut(BaseModel):
    id: str
    label: str = ""
    status: TieOutStatus
    max_abs_residual: Optional[float] = None
    periods_checked: int = 0
    material: bool = False
    evidence: list[CellCitation] = Field(default_factory=list)
    detail: str = ""


# ---------------------------------------------------------------------------
# Derived metrics
# ---------------------------------------------------------------------------

class DerivedMetric(BaseModel):
    metric_id: str
    label: str
    applicability: str = Field("", description="Why this metric applied to this model")
    series: dict[str, Optional[float]] = Field(default_factory=dict, description="period label -> value")
    scalar: Optional[float] = Field(None, description="For single-valued metrics")
    inputs: list[str] = Field(default_factory=list, description="Qualified cell refs feeding the computation")
    computation: str = Field("", description="Human-readable Python expression of the computation")
    checkpoints: dict[str, Optional[float]] = Field(default_factory=dict, description="2-3 spot-verification values")
    units: Optional[str] = None
    notes: Optional[str] = None
    external_benchmark: bool = Field(False, description="True iff the metric uses benchmarks.yaml values")


# ---------------------------------------------------------------------------
# Findings / acquittals / clean checks
# ---------------------------------------------------------------------------

class QuantifiedImpact(BaseModel):
    metric: str
    as_modeled: Optional[float] = None
    as_corrected: Optional[float] = None
    basis: str = ""


class Finding(BaseModel):
    id: str
    category: str
    severity: Severity
    title: str
    narrative: str
    evidence_cells: list[str] = Field(default_factory=list)
    evidence_values: list[CellCitation] = Field(default_factory=list)
    quantified_impact: Optional[QuantifiedImpact] = None
    management_question: str = ""
    confidence: float = Field(ge=0.0, le=1.0)
    trust_degraded: bool = False
    supporting_echoes: list[str] = Field(
        default_factory=list,
        description="Downstream symptoms collapsed onto this cause (cells or short descriptions)",
    )


class Acquittal(BaseModel):
    candidate: str
    category: str
    decomposition: str
    resolution_cells: list[str] = Field(default_factory=list)


class CleanCheck(BaseModel):
    check: str
    result: str
    evidence: list[str] = Field(default_factory=list)


class UnmappedRow(BaseModel):
    sheet: str
    row: int
    label: str
    reason: str = ""


# ---------------------------------------------------------------------------
# Top-level report
# ---------------------------------------------------------------------------

class Report(BaseModel):
    schema_version: str = SCHEMA_VERSION
    tool_version: str = TOOL_VERSION
    source_file: str
    sha256: str
    analyzed_at: str
    llm_used: bool = False
    workbook_map: WorkbookMap
    assumptions_census: list[AssumptionEntry] = Field(default_factory=list)
    tie_outs: list[TieOut] = Field(default_factory=list)
    derived_metrics: list[DerivedMetric] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    acquittals: list[Acquittal] = Field(default_factory=list)
    clean_checks: list[CleanCheck] = Field(default_factory=list)
    unmapped: list[UnmappedRow] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    def sorted_findings(self) -> list[Finding]:
        return sorted(self.findings, key=lambda f: (SEVERITY_ORDER[f.severity], f.id))
