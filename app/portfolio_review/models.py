"""
Pydantic models for the portfolio_review API.

These mirror the SQL schema in `schema.py` and are used as request/response
bodies in `routes.py`. Keeping them as plain Pydantic (not ORM-bound) keeps
the module compatible with the rest of the app, which uses raw sqlite3.
"""

from __future__ import annotations

from typing import Optional, Literal
from pydantic import BaseModel, Field


# ── Sections (canonical list) ─────────────────────────────────────────────────
# Slugs match the URL path: /portfolio-review/{slug}
SECTIONS = [
    {"slug": "inputs",        "num": "01", "name": "Inputs",                "color": "#4472C4"},
    {"slug": "carta-source",  "num": "02", "name": "Carta Source",          "color": "#70AD47"},
    {"slug": "returns",       "num": "03", "name": "Returns & IRR",         "color": "#FFC000"},
    {"slug": "valuation",     "num": "04", "name": "Valuation & Ownership", "color": "#C00000"},
    {"slug": "composition",   "num": "05", "name": "Portfolio Composition", "color": "#7030A0"},
    {"slug": "traction",      "num": "06", "name": "Traction & Status",     "color": "#ED7D31"},
    {"slug": "derisking",     "num": "07", "name": "Derisking Scorecard",   "color": "#7c3aed"},
    {"slug": "follow-on",     "num": "08", "name": "Follow-On Activity",    "color": "#5B9BD5"},
    {"slug": "governance",    "num": "09", "name": "Governance",            "color": "#00B0F0"},
    {"slug": "summary",       "num": "10", "name": "Summary & Output",      "color": "#548235"},
    {"slug": "directory",     "num": "11", "name": "Directory",             "color": "#BFBFBF"},
    {"slug": "archive",       "num": "12", "name": "Archive & WIP",         "color": "#808080"},
]
SECTION_SLUGS = {s["slug"] for s in SECTIONS}


# ── Companies ─────────────────────────────────────────────────────────────────
class CompanyIn(BaseModel):
    name: str
    fund: str = "Fund I"
    brief_description: str = ""
    sector: str = ""
    submarket: str = ""
    business_model: str = ""
    hw_sw: str = ""
    commercial_status: str = ""
    ceo_name: str = ""
    ceo_email: str = ""
    cfo_name: str = ""
    cfo_email: str = ""
    address: str = ""
    website: str = ""
    fume_date: Optional[str] = None
    first_year_revenue: Optional[str] = None
    hyperscale: bool = False
    notable_partners: str = ""
    next_round_expect: str = ""


class Company(CompanyIn):
    id: int
    created_at: str
    updated_at: str


# ── Investments ───────────────────────────────────────────────────────────────
class InvestmentIn(BaseModel):
    company_id: int
    investment_date: Optional[str] = None
    original_or_conversion: Literal["O", "C"] = "O"
    investment_amount: Optional[float] = None
    round_label: str = ""
    round_size: Optional[float] = None
    round_lead: str = ""
    pre_money: Optional[float] = None
    post_money: Optional[float] = None
    board_seat: str = "No"
    board_member: str = ""
    deal_lead: str = ""
    notes: str = ""
    participated: bool = True


class Investment(InvestmentIn):
    id: int
    created_at: str


# ── Financials / Valuations / Returns ─────────────────────────────────────────
class FinancialIn(BaseModel):
    company_id: int
    period: str
    revenue: Optional[float] = None
    growth_rate: Optional[float] = None
    ebitda: Optional[float] = None
    employees: Optional[int] = None
    runway_months: Optional[float] = None
    cash_on_hand: Optional[float] = None
    notes: str = ""


class Financial(FinancialIn):
    id: int


class ValuationIn(BaseModel):
    company_id: int
    as_of_date: str
    valuation_post_money: Optional[float] = None
    nav: Optional[float] = None
    cost_basis: Optional[float] = None
    ownership_pct: Optional[float] = None
    mark_source: str = "Carta"
    notes: str = ""


class Valuation(ValuationIn):
    id: int


class ReturnIn(BaseModel):
    company_id: int
    as_of_date: str
    cost: Optional[float] = None
    proceeds: Optional[float] = None
    interest: Optional[float] = None
    fmv: Optional[float] = None
    total_value: Optional[float] = None
    gain_loss: Optional[float] = None
    multiple: Optional[float] = None
    irr: Optional[float] = None


class Return(ReturnIn):
    id: int


class BoardSeatIn(BaseModel):
    company_id: int
    seat_type: Literal["Director", "Observer"] = "Director"
    board_member: str
    active: bool = True
    started_on: Optional[str] = None
    ended_on: Optional[str] = None
    notes: str = ""


class BoardSeat(BoardSeatIn):
    id: int


class FollowOnIn(BaseModel):
    company_id: int
    event_date: Optional[str] = None
    event_type: Literal["Bridge", "Priced", "Convert"] = "Priced"
    amount_invested: Optional[float] = None
    externally_priced: bool = False
    round_label: str = ""
    pre_money: Optional[float] = None
    post_money: Optional[float] = None
    notes: str = ""


class FollowOn(FollowOnIn):
    id: int


# ── Comments ──────────────────────────────────────────────────────────────────
EntityType = Literal["company", "section", "investment", "metric"]


class CommentIn(BaseModel):
    entity_type: EntityType
    entity_key: str = Field(..., description="e.g. company id, section slug, or 'company:42:irr'")
    body: str
    parent_id: Optional[int] = None


class Comment(BaseModel):
    id: int
    user_id: int
    user_username: Optional[str] = None
    entity_type: str
    entity_key: str
    parent_id: Optional[int]
    body: str
    created_at: str
    updated_at: str


# ── Import audit ──────────────────────────────────────────────────────────────
class ImportRun(BaseModel):
    id: int
    user_id: Optional[int]
    source_file: str
    status: str
    rows_imported: int
    rows_skipped: int
    error_summary: str
    started_at: str
    finished_at: Optional[str]
