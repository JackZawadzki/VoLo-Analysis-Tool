"""Pydantic output schema for the banker-agent extraction.

Design philosophy:
  - NO hardcoded enums of categories, sheet roles, or business types.
  - Labels are preserved verbatim from the source sheet.
  - Every data point carries a (sheet, cell) back-reference.
  - The agent emits labels and cell references; deterministic code fills in values.

The output is deliberately flexible: a SaaS model, a project-finance model, a
battery-manufacturing model, and a hard-tech milestone tracker all produce the
same shape. Downstream consumers (memo pipeline, analyst review) can filter
by soft category tags or by sheet.

VoLo-ready packaging (v0.2+):
  Every ExtractedModel also contains a `financial_model` section shaped to match
  VoLo's extract_financials.py output exactly. That makes the banker a drop-in
  replacement for the existing extractor at the DealPipelineRequest.financial_model
  ingest point. The `enrichment` section carries the banker's unique granular
  output (labeled line items, assumptions, narratives) for consumption by the
  memo pipeline once wired up.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


# ── Leaf types ─────────────────────────────────────────────────────────

class CellRef(BaseModel):
    """A single-cell provenance reference."""
    sheet: str
    address: str  # e.g. "F14"


class RangeRef(BaseModel):
    """A rectangular or linear cell range."""
    sheet: str
    range: str  # e.g. "C12:I12" or "A1:H40"


# ── Agent-emitted (pre-fill) shapes ─────────────────────────────────────
#
# These are what the LLM agent emits. They never contain numeric values.
# A deterministic post-pass reads the referenced cells and produces the
# value-filled versions below.

class LineItemDraft(BaseModel):
    """Line item as identified by the agent (no values yet)."""
    model_config = ConfigDict(extra="forbid")

    label: str = Field(description="The label string as it appears in the sheet, verbatim.")
    label_cell: CellRef = Field(description="Where the label text lives.")
    values_range: RangeRef = Field(description="The range of numeric cells (one per period).")
    period_header_range: RangeRef = Field(
        description="The range of cells above values_range that name each period "
                    "(e.g. '2026', '2027', 'Y1', 'Q1', 'Pilot')."
    )

    # Soft, agent-assigned tags. Free strings. Not an enum.
    # Examples the agent might use: "revenue", "cost_of_goods", "opex", "capex",
    # "headcount", "assumption", "cash", "debt_layer", "milestone", "schedule",
    # "kpi", "unit_economic", etc. The agent is free to invent tags.
    category: Optional[str] = Field(default=None, description="Soft category tag. Free string.")
    subcategory: Optional[str] = Field(default=None)

    unit: Optional[str] = Field(
        default=None,
        description="Unit of the values: 'USD', 'USD_M', 'USD_K', 'people', '%', "
                    "'units', 'MWh', 'years', etc. Free string.",
    )
    notes: Optional[str] = Field(default=None, description="Any extraction caveats.")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class AssumptionDraft(BaseModel):
    """A named scalar (not time-indexed)."""
    model_config = ConfigDict(extra="forbid")

    label: str
    label_cell: CellRef
    value_cell: CellRef
    unit: Optional[str] = None
    category: Optional[str] = Field(
        default=None,
        description="Soft tag, e.g. 'unit_price', 'cogs', 'churn_rate', 'staff_ratio', "
                    "'growth_assumption', 'financing_term'.",
    )
    notes: Optional[str] = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class NarrativeBlock(BaseModel):
    """Free-text content that's not a time series or scalar.

    Catches milestones, memos inside the sheet, commentary rows, etc.
    """
    model_config = ConfigDict(extra="forbid")

    title: Optional[str] = None
    text: str
    source_sheet: str
    source_range: Optional[str] = None
    category: Optional[str] = Field(default=None, description="e.g. 'milestone', 'caveat', 'methodology'.")


class SheetCharacterization(BaseModel):
    """Agent's free-text description of what a sheet contains."""
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = Field(description="One-to-three sentence summary of the sheet's content.")
    soft_tags: list[str] = Field(
        default_factory=list,
        description="Free tags the agent finds useful — e.g. ['income_statement', 'annual'].",
    )
    period_axis: Optional[str] = Field(
        default=None,
        description="If the sheet has a time axis: describe it. E.g. 'annual 2026-2033', "
                    "'monthly Jan-Dec 2026', 'stage: pilot/seed/series-A/series-B'.",
    )


class RequiredCore(BaseModel):
    """Fields that a downstream VC engine (e.g. Monte Carlo) typically needs.

    All fields optional — the agent fills what it finds in the model and
    flags the rest for analyst input. This is the ONE place where we declare
    a 'hope to find' list, but the schema never refuses on missing fields.

    Maps directly to the VoLo tool's DealPipelineRequest top-level fields.
    """
    model_config = ConfigDict(extra="forbid")

    archetype: Optional[str] = Field(default=None, description="Technology archetype (free string).")
    tam_millions: Optional[float] = None
    trl: Optional[int] = Field(default=None, ge=1, le=9)
    sector_profile: Optional[str] = None
    entry_stage: Optional[str] = Field(default=None, description="e.g. 'pre_seed', 'seed', 'a', 'b'.")
    check_size_millions: Optional[float] = None
    pre_money_millions: Optional[float] = None
    round_size_millions: Optional[float] = None

    # For each field above, did the agent find it in the workbook?
    found_in_model: dict[str, bool] = Field(default_factory=dict)
    sources: dict[str, CellRef] = Field(default_factory=dict)
    notes_on_missing: dict[str, str] = Field(default_factory=dict)


# ── VoLo-compatible financial_model view ───────────────────────────────
#
# These shapes match what VoLo's app/engine/extract_financials.py emits so
# the banker output can drop straight into DealPipelineRequest.financial_model.

class CanonicalMetricMap(BaseModel):
    """Agent-emitted map from VoLo's 8 canonical metric names to the label of
    the line_item that represents each metric in THIS model.

    If the model doesn't contain a particular metric, leave the field null.
    The agent must reference labels it has already extracted as line_items —
    do NOT invent labels.
    """
    model_config = ConfigDict(extra="forbid")

    revenue: Optional[str] = Field(default=None, description="Label of the line_item that is total revenue.")
    cogs: Optional[str] = Field(default=None, description="Label of the line_item that is total cost of goods sold.")
    gross_profit: Optional[str] = Field(default=None)
    operating_expenses: Optional[str] = Field(default=None, description="Label of the line_item that is total operating expenses / opex.")
    ebitda: Optional[str] = Field(default=None)
    operating_income: Optional[str] = Field(default=None, description="Label of the line_item for operating income / operating profit.")
    net_income: Optional[str] = Field(default=None)
    capex: Optional[str] = Field(default=None, description="Label of the line_item that is total capital expenditures.")


class FinancialModelMetric(BaseModel):
    """A single canonical metric, shaped to match VoLo's extract_financials output."""
    label: Optional[str] = None
    synonym_matched: str
    source_sheet: Optional[str] = None
    source_row_excel_addr: Optional[str] = None
    unit: Optional[str] = None
    values: dict[str, Optional[float]] = Field(default_factory=dict)


class FinancialModelScope(BaseModel):
    """Matches VoLo's extract_financials.py `scope` nested block exactly."""
    sheet: Optional[str] = None
    scope_description: Optional[str] = None
    years_covered: list[str] = Field(default_factory=list)


class VoLoFinancialModel(BaseModel):
    """Drop-in replacement for VoLo's extract_financials.py output.

    Shape exactly matches what `_adapt_new_extractor_response()` in
    VoLo's app/routes/extraction.py consumes:

        status, scope.{sheet, scope_description, years_covered},
        metrics.<name>.{values, unit, source_row_excel_addr, label, ...},
        verification.{checks_passed, warnings, errors},
        selection_rationale

    With this shape, the banker's output can be fed to the existing adapter
    directly — no VoLo-side code changes required just to CONSUME it.
    """
    model_config = ConfigDict(extra="forbid")

    status: str = "ok"
    scope: FinancialModelScope = Field(default_factory=FinancialModelScope)
    selection_rationale: Optional[str] = None
    metrics: dict[str, Optional[FinancialModelMetric]] = Field(default_factory=dict)
    verification: dict[str, Any] = Field(
        default_factory=lambda: {"ok": True, "checks_passed": [], "errors": [], "warnings": []}
    )
    extractor_version: str = "banker-0.2"
    candidates_considered: list[str] = Field(default_factory=list)


# ── Pre-fill envelope (what the agent returns) ──────────────────────────

class ExtractionDraft(BaseModel):
    """The agent's output, pre-value-fill.

    No numeric values are here. The deterministic fill step reads the cells
    referenced by each line_item / assumption / required_core source and
    produces the ExtractedModel below.
    """
    model_config = ConfigDict(extra="forbid")

    sheets: list[SheetCharacterization]
    line_items: list[LineItemDraft]
    assumptions: list[AssumptionDraft]
    narratives: list[NarrativeBlock] = Field(default_factory=list)
    required_core: RequiredCore
    canonical_metrics_map: CanonicalMetricMap = Field(
        default_factory=CanonicalMetricMap,
        description="Which extracted line_item represents each of VoLo's 8 canonical metrics.",
    )
    primary_financials_sheet: Optional[str] = Field(
        default=None,
        description="Which sheet the canonical_metrics_map is drawn from (usually the "
                    "Income Statement / P&L sheet). Used as the 'primary_sheet' in the "
                    "VoLo-compatible financial_model section.",
    )
    agent_notes: list[str] = Field(default_factory=list)


# ── Filled (post-deterministic) shapes ──────────────────────────────────

class LineItem(BaseModel):
    """A line item with values filled in.

    `values_by_period` maps each period header (string) to the numeric value
    read from the cell at that column. Period strings are taken verbatim from
    the period_header_range so '2026', 'Y1', 'Pilot', or 'Q3 2025' all work.
    """
    label: str
    label_cell: CellRef
    values_range: RangeRef
    values_by_period: dict[str, Optional[float]] = Field(default_factory=dict)
    period_order: list[str] = Field(
        default_factory=list,
        description="The period keys in left-to-right order as they appeared in the sheet.",
    )
    category: Optional[str] = None
    subcategory: Optional[str] = None
    unit: Optional[str] = None
    notes: Optional[str] = None
    confidence: float = 1.0


class Assumption(BaseModel):
    label: str
    label_cell: CellRef
    value_cell: CellRef
    value: Optional[float | str | bool] = None
    unit: Optional[str] = None
    category: Optional[str] = None
    notes: Optional[str] = None
    confidence: float = 1.0


class ReconciliationCheck(BaseModel):
    """A consistency check on extracted values."""
    description: str
    passed: bool
    detail: Optional[str] = None


class ExtractedModel(BaseModel):
    """Final, value-filled extraction output.

    Two-layer structure for VoLo-ready packaging:

      financial_model  — VoLo-compatible metric view (drop-in for extract_financials.py).
                         This is what DealPipelineRequest.financial_model expects.

      deal_input_fields — VoLo DealPipelineRequest top-level fields (archetype, TAM, TRL,
                          check size, etc.) the agent was able to resolve from the model.

      enrichment       — The banker's unique granular output (sheets, line_items,
                          assumptions, narratives) for the memo pipeline to consume once
                          wired up via a new optional MemoGenerateRequest field.

    Callers that only want the VoLo-compatible path can read `financial_model` and
    ignore everything else.
    """
    model_config = ConfigDict(extra="forbid")

    source_file: str
    extraction_version: str = "0.2.0"
    extracted_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    model_used: str = Field(description="LLM model identifier used for the extraction run.")

    # ── VoLo-compatible packaging ──
    financial_model: VoLoFinancialModel = Field(
        default_factory=VoLoFinancialModel,
        description="Drop-in replacement for VoLo's extract_financials.py output.",
    )
    deal_input_fields: RequiredCore = Field(
        description="Maps to DealPipelineRequest top-level fields.",
    )

    # ── Rich banker-specific enrichment ──
    # Kept flat (not nested under an `enrichment` key) to keep the JSON greppable
    # and to avoid rewriting the verification script. A future consumer that only
    # wants the VoLo bits can read `financial_model` + `deal_input_fields` and
    # ignore these.
    sheets: list[SheetCharacterization]
    line_items: list[LineItem]
    assumptions: list[Assumption]
    narratives: list[NarrativeBlock] = Field(default_factory=list)
    reconciliation_checks: list[ReconciliationCheck] = Field(default_factory=list)
    canonical_metrics_map: CanonicalMetricMap = Field(default_factory=CanonicalMetricMap)
    primary_financials_sheet: Optional[str] = None
    agent_notes: list[str] = Field(default_factory=list)

    # Usage / cost telemetry
    tokens_input: int = 0
    tokens_output: int = 0
    agent_turns: int = 0
