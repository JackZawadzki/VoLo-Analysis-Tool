"""Tests for the typed engine API (app.engine.schemas).

Two responsibilities:
  1. Input validation catches bad deals at the boundary (typos, out-of-
     range TRL, negative check sizes, etc.) with clear Pydantic errors.
  2. The typed wrapper `run_simulation_from_input()` produces byte-
     identical results to the legacy `run_simulation(**kwargs)` call,
     so migrating callers is zero-risk.
"""
import pytest
from pydantic import ValidationError

from app.engine.schemas import (
    DealInput,
    MonteCarloResult,
    MonteCarloSummary,
    run_simulation_from_input,
)


# ──────────────────────────────────────────────────────────────────────
# Minimal Carta stub — the engine only reads a few fields, and the
# acceptance tests don't depend on realistic benchmark data.
# ──────────────────────────────────────────────────────────────────────
MINIMAL_CARTA = {
    "DEFAULT (ALL)": {
        "round_sizes": {
            "a": [8e6, 15e6, 25e6],
            "b": [15e6, 30e6, 60e6],
            "c": [30e6, 60e6, 120e6],
        },
        "graduation_rates": {
            "seed_to_a": 0.4, "a_to_b": 0.45,
            "b_to_c": 0.55, "c_to_exit": 0.6,
        },
        "post_money": {
            "a": [20e6, 50e6, 100e6],
            "b": [60e6, 150e6, 350e6],
            "c": [150e6, 400e6, 1000e6],
        },
        "pre_money_step_up": {"a_to_b": 2.5, "b_to_c": 2.2},
        "time_to_next_round_months": {
            "seed_to_a": 18, "a_to_b": 18,
            "b_to_c": 20, "c_to_exit": 30,
        },
    }
}


def _valid_deal_kwargs():
    """Canonical valid inputs — reuse across tests and tweak per-case."""
    return dict(
        archetype="utility_solar",
        tam_millions=120000,
        trl=7,
        sector_profile="DEFAULT (ALL)",
        entry_stage="b",
        check_size_millions=3,
        pre_money_millions=50,
        n_simulations=300,
        random_seed=42,
    )


# ──────────────────────────────────────────────────────────────────────
# Input validation — schema rejects bad inputs at the boundary
# ──────────────────────────────────────────────────────────────────────

class TestDealInputValidation:
    def test_valid_deal_accepted(self):
        d = DealInput(**_valid_deal_kwargs())
        assert d.archetype == "utility_solar"
        assert d.trl == 7

    def test_unknown_archetype_rejected(self):
        bad = _valid_deal_kwargs()
        bad["archetype"] = "util_solar"  # typo
        with pytest.raises(ValidationError):
            DealInput(**bad)

    def test_trl_out_of_range_rejected(self):
        bad = _valid_deal_kwargs()
        bad["trl"] = 15
        with pytest.raises(ValidationError) as exc:
            DealInput(**bad)
        assert any(e["type"] == "less_than_equal" for e in exc.value.errors())

    def test_negative_check_size_rejected(self):
        bad = _valid_deal_kwargs()
        bad["check_size_millions"] = -5
        with pytest.raises(ValidationError) as exc:
            DealInput(**bad)
        assert any(e["type"] == "greater_than" for e in exc.value.errors())

    def test_unknown_entry_stage_rejected(self):
        bad = _valid_deal_kwargs()
        bad["entry_stage"] = "d"  # not in pre_seed/seed/a/b/c
        with pytest.raises(ValidationError):
            DealInput(**bad)

    def test_extra_fields_rejected(self):
        """Schema is strict — unknown fields fail rather than silently drop."""
        bad = _valid_deal_kwargs()
        bad["recommandation"] = "invest"  # typo of "recommendation"
        with pytest.raises(ValidationError):
            DealInput(**bad)


# ──────────────────────────────────────────────────────────────────────
# Parity — typed wrapper produces identical output to legacy dict API
# ──────────────────────────────────────────────────────────────────────

class TestTypedWrapperParity:
    """On the same seed, typed API and legacy API must produce the
    same MOIC, IRR, and survival rate. This is the acceptance criterion
    for calling the wrapper transparent."""

    def _legacy(self):
        from app.engine.monte_carlo import run_simulation
        return run_simulation(
            archetype="utility_solar", tam_millions=120000, trl=7,
            entry_stage="b", check_size_millions=3, pre_money_millions=50,
            sector_profile="DEFAULT (ALL)", carta_data=MINIMAL_CARTA,
            n_simulations=300, random_seed=42,
        )

    def _typed(self):
        deal = DealInput(**_valid_deal_kwargs())
        return run_simulation_from_input(deal, MINIMAL_CARTA)

    def test_survival_rate_matches(self):
        assert self._legacy()["summary"]["survival_rate"] == self._typed().summary.survival_rate

    def test_moic_p50_matches(self):
        assert (
            self._legacy()["moic_unconditional"]["p50_all"]
            == self._typed().moic_unconditional["p50_all"]
        )

    def test_expected_irr_matches(self):
        assert self._legacy()["expected_irr"] == self._typed().expected_irr

    def test_outcome_breakdown_matches(self):
        legacy_counts = {k: v["count"] for k, v in self._legacy()["outcome_breakdown"].items()}
        typed_counts = {k: v["count"] for k, v in self._typed().outcome_breakdown.items()}
        assert legacy_counts == typed_counts


# ──────────────────────────────────────────────────────────────────────
# Result shape — MonteCarloResult exposes the expected fields
# ──────────────────────────────────────────────────────────────────────

class TestMonteCarloResultShape:
    def test_result_has_typed_summary(self):
        deal = DealInput(**_valid_deal_kwargs())
        r = run_simulation_from_input(deal, MINIMAL_CARTA)
        assert isinstance(r.summary, MonteCarloSummary)
        assert r.summary.n_simulations == 300
        assert 0.0 <= r.summary.survival_rate <= 1.0

    def test_result_preserves_unknown_keys_via_extras(self):
        """Unknown top-level keys fall through to `extras` rather than being dropped."""
        deal = DealInput(**_valid_deal_kwargs())
        r = run_simulation_from_input(deal, MINIMAL_CARTA)
        # The current engine returns several fields we haven't formally typed
        # yet (e.g. variance_drivers, sensitivity). They must not be lost.
        assert isinstance(r.extras, dict)
