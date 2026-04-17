"""VoLo Engine — Pydantic schemas for the engine's public API.

These schemas define the typed contract at the engine/caller boundary.
Benefits:
  - Field typos fail at the boundary with clear errors, not 20 calls deep
  - Range/enum constraints enforced (TRL 1-9, archetype must be valid, etc.)
  - Self-documenting: the schema IS the API spec
  - IDE autocomplete works
  - JSON serialization for free (API endpoints, config files, logging)

Adoption strategy
-----------------
Schemas are introduced INCREMENTALLY via a wrapper pattern — the existing
positional-arg engine functions remain untouched, so no caller breaks.
New callers opt into typed API (e.g., `run_simulation_from_input(DealInput)`)
one at a time. Legacy dict/kwargs callers continue working indefinitely.

Nested output structures (outcome_breakdown, moic_unconditional, etc.) are
intentionally left as dicts for now — they can be tightened to sub-schemas
in later iterations without breaking this contract.
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field


# ──────────────────────────────────────────────────────────────────────
# Enums / canonical value sets
# ──────────────────────────────────────────────────────────────────────
#
# Archetypes are sourced from market_sizing.ARCHETYPE_DEFAULTS. Keeping this
# as a Literal (not Enum) makes it easy to serialize and compare against the
# raw strings already used throughout the engine.
ArchetypeLiteral = Literal[
    "utility_solar", "commercial_solar", "residential_solar",
    "onshore_wind", "offshore_wind",
    "geothermal", "battery_storage_utility", "nuclear_smr",
    "ev_electrification", "climate_software", "industrial_decarb", "ai_ml",
    "custom",
    "base_capital_intensive", "base_software",
    "base_sw_hw_hybrid", "base_hard_tech",
]

EntryStageLiteral = Literal["pre_seed", "seed", "a", "b", "c"]


# ──────────────────────────────────────────────────────────────────────
# Deal input — what goes INTO a Monte Carlo run
# ──────────────────────────────────────────────────────────────────────

class DealInput(BaseModel):
    """Typed input to the Monte Carlo deal simulation.

    Replaces the 17-arg positional signature of `run_simulation()` with a
    single validated object. All fields are validated on construction —
    e.g. a TRL of 12 or a negative check size fails immediately with a
    clear Pydantic error message, rather than producing garbage output.
    """
    model_config = ConfigDict(extra="forbid")

    # — Company / technology
    archetype: ArchetypeLiteral = Field(
        description="Technology archetype for Bass-diffusion calibration.",
    )
    tam_millions: float = Field(
        gt=0,
        description="Total addressable market in $M.",
    )
    trl: int = Field(
        ge=1, le=9,
        description="Technology Readiness Level (1=concept, 9=commercial).",
    )
    sector_profile: str = Field(
        description="Carta sector profile key; falls back to DEFAULT (ALL).",
    )

    # — Deal terms
    entry_stage: EntryStageLiteral = Field(
        description="Round type at entry.",
    )
    check_size_millions: float = Field(
        gt=0,
        description="VoLo's check size in $M.",
    )
    pre_money_millions: float = Field(
        gt=0,
        description="Pre-money valuation in $M.",
    )
    round_size_millions: Optional[float] = Field(
        default=None,
        gt=0,
        description="Total round size in $M (defaults to check_size if unset).",
    )

    # — Simulation parameters (sensible defaults match the legacy API)
    penetration_share_low: float = Field(default=0.01, gt=0, le=1)
    penetration_share_high: float = Field(default=0.05, gt=0, le=1)
    price_per_unit_m: float = Field(default=1.0, gt=0)
    exit_multiple_low: float = Field(default=12.0, gt=0)
    exit_multiple_high: float = Field(default=30.0, gt=0)
    exit_year_low: int = Field(default=5, ge=1)
    exit_year_high: int = Field(default=10, ge=1)
    n_simulations: int = Field(default=5000, ge=100, le=100_000)
    random_seed: Optional[int] = Field(default=None)

    # — Optional overrides (for custom archetypes / founder inputs)
    custom_bass_p_low: Optional[float] = Field(default=None, ge=0, le=1)
    custom_bass_p_high: Optional[float] = Field(default=None, ge=0, le=1)
    custom_bass_q_low: Optional[float] = Field(default=None, ge=0, le=1)
    custom_bass_q_high: Optional[float] = Field(default=None, ge=0, le=1)
    custom_maturity: Optional[str] = Field(default=None)
    custom_inflection_year: Optional[int] = Field(default=None)
    founder_revenue_projections_m: Optional[List[float]] = Field(
        default=None,
        description="Bottom-up revenue projections from the deck/model.",
    )

    # ── Derived helpers — make DealInput callable against the legacy
    #    positional API without duplicating the field list at call sites.
    def as_legacy_kwargs(self, carta_data: Dict[str, Any]) -> Dict[str, Any]:
        """Flatten to the kwargs shape accepted by `run_simulation()`."""
        kw: Dict[str, Any] = {
            "archetype": self.archetype,
            "tam_millions": self.tam_millions,
            "trl": self.trl,
            "entry_stage": self.entry_stage,
            "check_size_millions": self.check_size_millions,
            "pre_money_millions": self.pre_money_millions,
            "sector_profile": self.sector_profile,
            "carta_data": carta_data,
            "penetration_share": (self.penetration_share_low, self.penetration_share_high),
            "price_per_unit_m": self.price_per_unit_m,
            "exit_multiple_range": (self.exit_multiple_low, self.exit_multiple_high),
            "exit_year_range": (self.exit_year_low, self.exit_year_high),
            "n_simulations": self.n_simulations,
            "random_seed": self.random_seed,
        }
        if self.custom_bass_p_low is not None and self.custom_bass_p_high is not None:
            kw["custom_bass_p"] = (self.custom_bass_p_low, self.custom_bass_p_high)
        if self.custom_bass_q_low is not None and self.custom_bass_q_high is not None:
            kw["custom_bass_q"] = (self.custom_bass_q_low, self.custom_bass_q_high)
        if self.custom_maturity is not None:
            kw["custom_maturity"] = self.custom_maturity
        if self.custom_inflection_year is not None:
            kw["custom_inflection_year"] = self.custom_inflection_year
        if self.founder_revenue_projections_m is not None:
            kw["founder_revenue_projections_m"] = self.founder_revenue_projections_m
        if self.round_size_millions is not None:
            kw["round_size_m"] = self.round_size_millions
        return kw


# ──────────────────────────────────────────────────────────────────────
# Monte Carlo result — what comes OUT of a simulation run
# ──────────────────────────────────────────────────────────────────────

class MonteCarloSummary(BaseModel):
    """Top-line summary of a Monte Carlo run."""
    n_simulations: int
    n_positive_outcome: int
    n_meaningful_exit: int
    n_total_loss: int
    survival_rate: float
    meaningful_exit_rate: float
    random_seed: Optional[int] = None


class MonteCarloResult(BaseModel):
    """Typed envelope for `run_simulation()`'s output.

    Top-level fields are typed for safety and autocomplete; deeply-nested
    payloads (percentile tables, adoption curves, variance drivers, etc.)
    are left as dicts for now to keep this first refactor small. They can
    be migrated to dedicated sub-schemas in follow-up commits.
    """
    model_config = ConfigDict(arbitrary_types_allowed=True)

    summary: MonteCarloSummary
    outcome_breakdown: Dict[str, Any]
    inputs: Dict[str, Any]

    # Moic / IRR distributions
    moic_unconditional: Dict[str, Any]
    moic_conditional: Dict[str, Any]
    moic_meaningful: Optional[Dict[str, Any]] = None
    irr_conditional: Dict[str, Any]
    expected_irr: float
    probability: Dict[str, Any]

    # Engine sub-layer outputs
    dilution: Dict[str, Any]
    trl_impact: Dict[str, Any]
    ebitda_margin: Dict[str, Any]
    ev_at_exit: Dict[str, Any]
    adoption: Dict[str, Any]
    adoption_curve: Dict[str, Any]
    revenue_trajectories: Dict[str, Any]

    # Remaining fields (variance drivers, sensitivity, etc.) — allow passthrough
    # via extras rather than declaring every field we haven't audited yet.
    extras: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_engine_dict(cls, raw: Dict[str, Any]) -> "MonteCarloResult":
        """Wrap the existing dict-returning engine call in a typed result.

        Fields we have declared are extracted and validated; unknown keys
        fall through to `extras` so no data is dropped.
        """
        known = {
            "summary", "outcome_breakdown", "inputs",
            "moic_unconditional", "moic_conditional", "moic_meaningful",
            "irr_conditional", "expected_irr", "probability",
            "dilution", "trl_impact", "ebitda_margin", "ev_at_exit",
            "adoption", "adoption_curve", "revenue_trajectories",
        }
        extras = {k: v for k, v in raw.items() if k not in known}
        return cls(
            summary=MonteCarloSummary(**raw["summary"]),
            outcome_breakdown=raw["outcome_breakdown"],
            inputs=raw["inputs"],
            moic_unconditional=raw["moic_unconditional"],
            moic_conditional=raw["moic_conditional"],
            moic_meaningful=raw.get("moic_meaningful"),
            irr_conditional=raw["irr_conditional"],
            expected_irr=raw["expected_irr"],
            probability=raw["probability"],
            dilution=raw["dilution"],
            trl_impact=raw["trl_impact"],
            ebitda_margin=raw["ebitda_margin"],
            ev_at_exit=raw["ev_at_exit"],
            adoption=raw["adoption"],
            adoption_curve=raw["adoption_curve"],
            revenue_trajectories=raw["revenue_trajectories"],
            extras=extras,
        )


# ──────────────────────────────────────────────────────────────────────
# Typed API — opt-in wrapper around the legacy dict-based engine call
# ──────────────────────────────────────────────────────────────────────

def run_simulation_from_input(
    deal: DealInput,
    carta_data: Dict[str, Any],
) -> MonteCarloResult:
    """Typed entry point to the Monte Carlo engine.

    This is the recommended API for new code. It validates inputs before
    calling the engine and wraps the result in a typed model — you get
    Pydantic errors immediately on bad inputs, and autocomplete on the
    output.

    The legacy `run_simulation()` positional API remains unchanged and
    continues to work for existing callers. Migration is voluntary and
    incremental.
    """
    # Lazy import keeps `schemas` importable even during partial installs.
    from .monte_carlo import run_simulation

    raw = run_simulation(**deal.as_legacy_kwargs(carta_data))
    return MonteCarloResult.from_engine_dict(raw)
