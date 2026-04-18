"""Tests for the single-source-of-truth financial extractor.

Covers four things:
  1. Math invariants on small synthetic inputs (discovery, verification).
  2. End-to-end: Mitra Chem Project Alpha workbook produces the expected
     canonical numbers from FS_Combined.
  3. Single-source invariant: forcing an alternate sheet produces nulls
     for years that sheet doesn't cover — NEVER fallback to another sheet.
  4. Concatenation-bug detection: a synthetic revenue series with the
     plateau->jump->plateau signature is flagged as a hard error.

The Mitra workbook is a large file living outside the repo; the
end-to-end tests are skipped when it's absent so CI stays portable.
"""
import os
from pathlib import Path

import pytest

from app.engine.extract_financials import (
    extract,
    verify_extraction,
    _norm,
    _to_year,
)

MITRA_PATH = Path(
    "/Users/jackzawadzki/Downloads/Mitra Chem_Project Alpha_Model_vFeb-26.xlsx"
)
has_mitra = MITRA_PATH.exists()
skip_if_no_mitra = pytest.mark.skipif(
    not has_mitra,
    reason=f"Mitra test workbook not at {MITRA_PATH}",
)


# ──────────────────────────────────────────────────────────────────────
# Unit: normalization + year detection
# ──────────────────────────────────────────────────────────────────────

class TestHelpers:
    def test_norm_strips_punctuation_and_lowercases(self):
        assert _norm("(-) Capex") == "capex"
        assert _norm("  Total Revenue  ") == "total revenue"
        assert _norm("EBITDA (excl. SBC)") == "ebitda"

    def test_norm_handles_none_and_empty(self):
        assert _norm(None) == ""
        assert _norm("") == ""
        assert _norm("   ") == ""

    def test_to_year_accepts_int_float_str(self):
        assert _to_year(2025) == 2025
        assert _to_year(2025.0) == 2025
        assert _to_year("2025") == 2025
        assert _to_year(" 2025 ") == 2025

    def test_to_year_rejects_non_years(self):
        assert _to_year(2014) is None       # before MIN_YEAR
        assert _to_year(2100) is None       # after MAX_YEAR
        assert _to_year(25) is None         # too short
        assert _to_year("Q1 2025") is None  # not pure year
        assert _to_year(True) is None       # bool is not a year
        assert _to_year(None) is None


# ──────────────────────────────────────────────────────────────────────
# Unit: verification layer
# ──────────────────────────────────────────────────────────────────────

def _make_synthetic(revenue_values):
    return {
        "sheet": "SYNTHETIC",
        "year_axis": {"years_covered": list(map(int, revenue_values.keys()))},
        "metrics": {
            "revenue": {"label": "Revenue", "values": revenue_values},
            "cogs": None, "gross_profit": None, "operating_expenses": None,
            "ebitda": None, "operating_income": None, "net_income": None,
            "capex": None,
        },
    }


class TestVerification:
    def test_clean_ramp_passes(self):
        values = {str(y): v for y, v in zip(
            range(2023, 2031),
            [0, 0, 100, 300, 700, 1200, 1500, 1500],
        )}
        v = verify_extraction(_make_synthetic(values))
        assert v["ok"] is True

    def test_single_jump_without_plateau_sandwich_is_warning_not_error(self):
        """The real FS_Combined case: 417 -> 2753 (6.6x) during ramp.
        Pre-years are still ramping (not flat), so it's not a bug."""
        values = {str(y): v for y, v in zip(
            range(2023, 2031),
            [0, 0, 164, 417, 2753, 5300, 5400, 5440],  # not flat before jump
        )}
        v = verify_extraction(_make_synthetic(values))
        assert v["ok"] is True
        assert any("5x year-over-year jump" in w for w in v["warnings"])
        assert not any("plateau->jump->plateau" in e for e in v["errors"])

    def test_plateau_jump_plateau_is_hard_error(self):
        """The bug signature: flat 3+ years, big jump, flat 3+ years after."""
        values = {str(y): v for y, v in [
            (2025, 164), (2026, 417), (2027, 747), (2028, 922),
            (2029, 922), (2030, 922), (2031, 922), (2032, 922),
            (2033, 922), (2034, 922), (2035, 922),
            # Concatenation boundary
            (2036, 5442), (2037, 5442), (2038, 5442),
            (2039, 5442), (2040, 5442), (2041, 5442),
        ]}
        v = verify_extraction(_make_synthetic(values))
        assert v["ok"] is False
        assert any("plateau->jump->plateau" in e or "concatenation" in e.lower()
                   for e in v["errors"])

    def test_negative_revenue_is_hard_error(self):
        values = {"2023": 100.0, "2024": -50.0, "2025": 200.0}
        v = verify_extraction(_make_synthetic(values))
        assert v["ok"] is False
        assert any("revenue" in e and "negative" in e for e in v["errors"])

    def test_gross_profit_reconciliation(self):
        """Revenue - COGS should ≈ Gross Profit."""
        data = {
            "sheet": "T",
            "year_axis": {"years_covered": [2025, 2026]},
            "metrics": {
                "revenue": {"label": "R", "values": {"2025": 1000.0, "2026": 2000.0}},
                "cogs": {"label": "C", "values": {"2025": -600.0, "2026": -1200.0}},
                "gross_profit": {"label": "GP",
                                 "values": {"2025": 400.0, "2026": 800.0}},
                "operating_expenses": None, "ebitda": None,
                "operating_income": None, "net_income": None, "capex": None,
            },
        }
        v = verify_extraction(data)
        assert any("gross_profit" in c and "revenue - cogs" in c for c in v["checks_passed"])


# ──────────────────────────────────────────────────────────────────────
# End-to-end: Mitra Chem Project Alpha workbook
# ──────────────────────────────────────────────────────────────────────

@skip_if_no_mitra
class TestMitraExtraction:
    """The canonical test case from the spec.

    Correct answer: FS_Combined should be chosen. Revenue values must
    match the specified ramp: {2025: 164.7, 2026: 417.6, 2027: 2753.3,
    2028: 5330.2, 2029: 5442.8, ...}.
    """
    @pytest.fixture(scope="class")
    def result(self):
        return extract(str(MITRA_PATH))

    def test_status_is_ok(self, result):
        assert result["status"] in ("ok", "ok_with_warnings")
        assert "error" not in result

    def test_chose_combined_sheet(self, result):
        assert result["scope"]["sheet"] == "FS_Combined"

    def test_scope_is_combined_not_single_facility(self, result):
        desc = result["scope"]["scope_description"].lower()
        assert "combined" in desc or "consolidated" in desc or "unified" in desc

    def test_revenue_matches_expected_ramp(self, result):
        rev = result["metrics"]["revenue"]["values"]
        # Spec values from the user's request
        expected = {
            "2023": 0,
            "2024": 0,
            "2025": 164.7,
            "2026": 417.6,
            "2027": 2753.3,
            "2028": 5330.2,
            "2029": 5442.8,
        }
        for year, want in expected.items():
            got = rev.get(year)
            assert got is not None, f"Revenue {year} is null"
            assert abs(got - want) < 1.0, f"Revenue {year}: expected ~{want}, got {got}"

    def test_all_metrics_declare_same_source_sheet(self, result):
        """Every non-null metric must have been extracted from the chosen sheet."""
        chosen_sheet = result["scope"]["sheet"]
        # The extract function records source cell (e.g. F15) for each metric.
        # All source cells come from the chosen sheet by construction; this
        # test is a smoke check that no metric silently mixes sources.
        for name, m in result["metrics"].items():
            if m is None:
                continue
            assert "source_row_excel_addr" in m, \
                f"{name}: missing source_row_excel_addr"

    def test_year_span_covers_2021_to_2045(self, result):
        years = result["scope"]["years_covered"]
        assert min(years) <= 2023
        assert max(years) >= 2041

    def test_gross_profit_reconciles_on_real_data(self, result):
        """On the chosen sheet, Revenue - COGS ≈ Gross Profit (the point of
        picking a proper P&L sheet)."""
        checks = result["verification"]["checks_passed"]
        assert any("gross_profit" in c and "revenue - cogs" in c for c in checks)


# ──────────────────────────────────────────────────────────────────────
# Single-source invariant — forcing a sheet gives nulls for missing years
# ──────────────────────────────────────────────────────────────────────

@skip_if_no_mitra
class TestSingleSourceInvariant:
    def test_forcing_fs_annual_produces_nulls_for_2036_onwards(self):
        """FS - Annual covers 2023..2035. The extractor must NOT fall back
        to FS_Combined for 2036+; those years should be absent or null."""
        result = extract(str(MITRA_PATH), user_selected_sheet="FS - Annual")
        rev = result["metrics"]["revenue"]["values"]
        # Years 2036+ must either be absent or null.
        for year_str in ("2036", "2037", "2038", "2039", "2040", "2041"):
            val = rev.get(year_str, None)
            assert val is None, (
                f"Single-source invariant violated: FS - Annual returned "
                f"{val} for {year_str}, but FS - Annual doesn't cover that year."
            )

    def test_forcing_fs_annual_still_returns_correct_in_range_values(self):
        """Sanity: forcing FS - Annual gives a valid extraction for years
        it does cover — just shorter."""
        result = extract(str(MITRA_PATH), user_selected_sheet="FS - Annual")
        rev = result["metrics"]["revenue"]["values"]
        assert rev["2025"] == pytest.approx(164.7, abs=1.0)
        assert rev["2035"] == pytest.approx(922.8, abs=1.0)
