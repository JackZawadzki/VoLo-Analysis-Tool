"""VoLo Engine Acceptance Tests — golden-path behavior only.

These tests are the safety net for refactoring. They capture the engine's
current behavior so changes that break core math, data loading, or app
startup are caught immediately.

Three tiers:
  1. Smoke        — app imports, engine modules load
  2. Invariants   — deterministic math produces expected properties
  3. Reproducible — seeded stochastic runs produce identical outputs

Run:
    pytest tests/test_acceptance.py -v
"""
import numpy as np
import pytest


# ──────────────────────────────────────────────────────────────────────
# Tier 1 — Smoke: the app and engine load
# ──────────────────────────────────────────────────────────────────────

class TestSmoke:
    def test_app_module_imports(self):
        from app import main
        assert main.app is not None

    def test_all_engine_modules_import(self):
        from app.engine import (
            adoption, monte_carlo, fund_model, dilution,
            position_sizing, market_sizing, valuation_comps,
            rvm_carbon, deal_report,
        )
        # If any import raises, the test fails automatically.
        assert all([adoption, monte_carlo, fund_model, dilution,
                    position_sizing, market_sizing, valuation_comps,
                    rvm_carbon, deal_report])

    def test_http_root_serves(self):
        """Root endpoint returns 200 and renders the SPA template."""
        from starlette.testclient import TestClient
        from app.main import app
        client = TestClient(app)
        r = client.get("/")
        assert r.status_code == 200
        assert len(r.text) > 1000, "Homepage suspiciously small"


# ──────────────────────────────────────────────────────────────────────
# Tier 2 — Math invariants on deterministic functions
# ──────────────────────────────────────────────────────────────────────

class TestBassDiffusion:
    def test_cumulative_at_zero_is_zero(self):
        from app.engine.adoption import bass_diffusion_cumulative
        assert bass_diffusion_cumulative(0, p=0.03, q=0.38, m=1000) == pytest.approx(0.0, abs=1e-6)

    def test_cumulative_is_monotonic(self):
        from app.engine.adoption import bass_diffusion_cumulative
        vals = [bass_diffusion_cumulative(t, p=0.03, q=0.38, m=1000) for t in range(0, 20)]
        for a, b in zip(vals, vals[1:]):
            assert b + 1e-9 >= a, f"Adoption decreased: {a} -> {b}"

    def test_cumulative_approaches_market_size(self):
        """At large t, Bass diffusion saturates at m."""
        from app.engine.adoption import bass_diffusion_cumulative
        m = 1000
        assert bass_diffusion_cumulative(50, p=0.03, q=0.38, m=m) == pytest.approx(m, rel=0.01)

    def test_rate_is_nonnegative(self):
        from app.engine.adoption import bass_diffusion_rate
        for t in range(0, 20):
            assert bass_diffusion_rate(t, p=0.03, q=0.38, m=1000) >= -1e-9


class TestMarketSizing:
    def test_defaults_has_known_archetypes(self):
        from app.engine.market_sizing import get_all_defaults
        d = get_all_defaults()
        assert "utility_solar" in d
        assert len(d) >= 5, "Expected at least 5 archetypes in defaults"

    def test_market_sizing_returns_tam_sam_som(self):
        from app.engine.market_sizing import get_market_sizing
        r = get_market_sizing("utility_solar")
        assert r["tam_m"] > 0
        assert r["sam_m"] > 0
        assert r["som_m"] > 0
        # SAM should not exceed TAM; SOM should not exceed SAM.
        assert r["sam_m"] <= r["tam_m"]
        assert r["som_m"] <= r["sam_m"]


class TestDilutionTRL:
    """TRL modifiers should have the expected shape and directional properties."""

    def test_trl_modifiers_have_expected_keys(self):
        from app.engine.dilution import get_trl_modifiers
        m = get_trl_modifiers(5)
        for k in ("survival_penalty", "capital_multiplier",
                  "extra_bridge_prob", "exit_multiple_discount"):
            assert k in m, f"TRL modifier missing key: {k}"

    def test_higher_trl_is_no_riskier(self):
        """TRL 9 (commercial) should not be penalized more than TRL 1 (concept)."""
        from app.engine.dilution import get_trl_modifiers
        low = get_trl_modifiers(1)
        high = get_trl_modifiers(9)
        assert high["survival_penalty"] <= low["survival_penalty"]
        assert high["capital_multiplier"] <= low["capital_multiplier"]


class TestCarbon:
    def test_carbon_defaults_nonempty(self):
        from app.engine.rvm_carbon import get_carbon_defaults
        d = get_carbon_defaults("utility_solar")
        assert isinstance(d, dict)
        assert len(d) > 0

    def test_risk_divisor_returns_positive_int(self):
        from app.engine.rvm_carbon import get_risk_divisor_for_trl
        for trl in range(1, 10):
            v = get_risk_divisor_for_trl(trl)
            assert isinstance(v, (int, float))
            assert v > 0


class TestValuationComps:
    def test_vebitda_loads_with_expected_keys(self):
        from app.engine.valuation_comps import load_vebitda
        data = load_vebitda()
        assert isinstance(data, dict)
        assert "source" in data
        assert "acquisition_discount" in data

    def test_acquisition_discount_is_bounded(self):
        """BCG / Kengelbach et al. — acquisition discount typically 10–30%."""
        from app.engine.valuation_comps import load_vebitda
        data = load_vebitda()
        d = data["acquisition_discount"]
        assert 0.0 < d < 0.5


# ──────────────────────────────────────────────────────────────────────
# Tier 3 — Stochastic reproducibility: seeded RNG produces identical outputs
# ──────────────────────────────────────────────────────────────────────

class TestAdoptionTrajectoriesReproducible:
    """Two runs with identical seeds must produce identical trajectories."""

    ARCHETYPE = "utility_solar"
    TAM = 1000
    N_SIMS = 25
    HORIZON = 10
    SEED = 42

    def _run(self, seed):
        from app.engine.adoption import generate_adoption_trajectories
        rng = np.random.default_rng(seed)
        return generate_adoption_trajectories(
            self.ARCHETYPE, self.TAM,
            n_simulations=self.N_SIMS,
            horizon_years=self.HORIZON,
            rng=rng,
        )

    def test_same_seed_identical_trajectories(self):
        r1 = self._run(self.SEED)
        r2 = self._run(self.SEED)
        np.testing.assert_array_equal(r1["trajectories"], r2["trajectories"])
        np.testing.assert_array_equal(r1["params_used"], r2["params_used"])

    def test_different_seed_differs(self):
        r1 = self._run(self.SEED)
        r2 = self._run(self.SEED + 1)
        assert not np.array_equal(r1["trajectories"], r2["trajectories"])

    def test_trajectory_shape(self):
        r = self._run(self.SEED)
        # shape: (n_sims, horizon_years + 1)
        assert r["trajectories"].shape == (self.N_SIMS, self.HORIZON + 1)
        # All trajectories start at zero.
        np.testing.assert_array_equal(
            r["trajectories"][:, 0],
            np.zeros(self.N_SIMS),
        )

    def test_trajectories_bounded_by_tam(self):
        """No adoption trajectory should exceed the TAM it's calibrated against
        by more than a modest overshoot margin."""
        r = self._run(self.SEED)
        max_adopt = r["trajectories"].max()
        # Allow generous overshoot — Bass can briefly exceed m for some param sets,
        # but 10x TAM is clearly a bug.
        assert max_adopt < 10 * self.TAM, f"Adoption {max_adopt} >> TAM {self.TAM}"
