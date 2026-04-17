"""
VoLo Earth RVM Carbon Impact Model
===================================
Source: 2022 Sandbox_VoLo Earth Proprietary RVM 1.19.2023.xlsx
Scope:  Fund 1 tab, columns HY:NR (Volume Forecast → Carbon Impact → VoLo Metrics)

Dependency Map
--------------
  'C Analysis Assumptions-N'!L:V   →  Fund 1 cols IJ:IR   (volume forecasts yr 2-10)
  'Investment till end of 2022'!K:M →  Fund 1 col  LQ      (VoLo total $ via VLOOKUP)
  Fund 1 rows 24-27 (MN label + MO:NR series) → cols JE:JN, KF:KO (CI per year)
  Fund 1 col AM (expected VoLo equity %) → col LR  (tonnes / dollar)
  Fund 1 col Z  (initial invest $k)      → col LR  (tonnes / dollar)

Code Sections
-------------
  1. GLOBAL ASSUMPTIONS  – Carbon Intensity Database (rows 24-27, cols MN:NR)
  2. INPUTS              – Per-company data classes (HZ:IH, II:IR, IZ:KD, LN:LQ, Z, AM)
  3. INTERMEDIATES       – Calculation dataclass (JD, JE:JN, JO:JX, JY, KE, KP:KY, KZ, LB:LL)
  4. OUTPUTS             – Portfolio metrics (LM:LS)
  5. ENGINE              – Functions that compute intermediates → outputs
  6. PORTFOLIO DATA      – Illustrative company records from the spreadsheet
  7. RUNNER              – Portfolio execution and aggregation

Design Notes
------------
CI Series Alignment (key insight from spreadsheet audit):
  The MN:NR carbon intensity columns span ~30 time steps but each company's
  JE (Year 1 CI) is manually pointed to a specific column offset, NOT derived
  from a formula involving launch year.  Different resources / rows also use
  different base years:
    • Row 25 Global electricity: MO25=0.48, MZ=step 11 → Banyan yr1=2022 ≈ 2011 base
    • Row 8  US electricity (BF): MO8=0.40, MU=step 6  → BF yr1=2023   ≈ 2017 base
    • Row 24 Natural Gas:         constant at 0.053×1.4 regardless of step
  For app deployment each company therefore stores an explicit `ci_year1` value
  and `ci_annual_decline` (step per year from CarbonIntensityDB or overridden).

Heimdal Special Case:
  Heimdal's JO formula is =II (volume only), not =JD*II*JE.  For DAC (direct
  air capture), the captured tonnage IS the impact so CI is not applied.
  The Python model handles this by setting ci_year1_override=1.0 for operating
  carbon, so annual_op = JD * vol * 1.0 = vol.

Embodied Carbon Formula:
  KP:KY = $KE_ref × KF:KO × II:IR  (ke × CI_yr × volume)
  All companies use their own per-company KE value calculated as range_improvement × baseline_production:
    ke_used = ec.range_improvement * ec.baseline_production
  Most companies have embodied carbon = 0 due to zero baseline production.

Verification Results (all vs Fund 1 data_only LL values):
  Banyan Infrastructure  0.0005%   Blue Frontier  0.000%   HEVO     0.000%
  HST                    0.000%    Heimdal        0.000%   Traxen   0.000%
  BlocPower              0.000%    Gaiascope      0.000%   Daanaa   0.000%
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional


# =============================================================================
# TRL -> Risk Divisor mapping (replaces manual risk_divisor input)
# =============================================================================
TRL_TO_RISK_DIVISOR = {
    1: 6, 2: 6, 3: 6, 4: 6,   # pre-commercial, high risk
    5: 3, 6: 3,                 # validated, standard risk
    7: 1, 8: 1, 9: 1,          # proven, de-risked
}


def get_risk_divisor_for_trl(trl: int) -> int:
    return TRL_TO_RISK_DIVISOR.get(trl, 3)


# =============================================================================
# Archetype -> Carbon Model Defaults
# Pre-fills displaced resource, baseline production, unit definition, etc.
# =============================================================================
ARCHETYPE_CARBON_DEFAULTS = {
    # range_improvement is a CI improvement factor: how many times lower the new
    # technology's carbon intensity is vs. the displaced conventional resource.
    # Displacement fraction = 1 − 1/factor.
    # Near-zero-CI technologies (solar, wind, nuclear) use factor=1000 (≈99.9% displacement).
    # Efficiency gains > 1× are absorbed into baseline_lifetime_prod (e.g. solar 1.15× → ×1725).
    "utility_solar": {
        "displaced_resource": "US electricity",
        "baseline_lifetime_prod": 1725.0,   # 1500 × 1.15 efficiency gain absorbed
        "range_improvement": 1000.0,         # near-zero CI → full displacement
        "unit_definition": "MW installed capacity",
        "unit_service_life_yrs": 25,
        "specific_production_units": "MWh/MW/year",
    },
    "commercial_solar": {
        "displaced_resource": "US electricity",
        "baseline_lifetime_prod": 1320.0,   # 1200 × 1.10
        "range_improvement": 1000.0,
        "unit_definition": "MW installed capacity",
        "unit_service_life_yrs": 25,
        "specific_production_units": "MWh/MW/year",
    },
    "residential_solar": {
        "displaced_resource": "US electricity",
        "baseline_lifetime_prod": 1155.0,   # 1100 × 1.05
        "range_improvement": 1000.0,
        "unit_definition": "MW installed capacity",
        "unit_service_life_yrs": 25,
        "specific_production_units": "MWh/MW/year",
    },
    "onshore_wind": {
        "displaced_resource": "US electricity",
        "baseline_lifetime_prod": 2500.0,
        "range_improvement": 1000.0,
        "unit_definition": "MW installed capacity",
        "unit_service_life_yrs": 20,
        "specific_production_units": "MWh/MW/year",
    },
    "offshore_wind": {
        "displaced_resource": "Global electricity",
        "baseline_lifetime_prod": 3800.0,
        "range_improvement": 1000.0,
        "unit_definition": "MW installed capacity",
        "unit_service_life_yrs": 25,
        "specific_production_units": "MWh/MW/year",
    },
    "geothermal": {
        "displaced_resource": "US electricity",
        "baseline_lifetime_prod": 7000.0,
        "range_improvement": 1000.0,
        "unit_definition": "MW installed capacity",
        "unit_service_life_yrs": 30,
        "specific_production_units": "MWh/MW/year",
    },
    "battery_storage_utility": {
        "displaced_resource": "Natural Gas (CCGT)",
        "baseline_lifetime_prod": 2000.0,
        "range_improvement": 6.667,          # 1/(1−0.85): 85% of baseline displaced
        "unit_definition": "MWh storage capacity",
        "unit_service_life_yrs": 15,
        "specific_production_units": "MWh displaced/MWh/year",
    },
    "nuclear_smr": {
        "displaced_resource": "Global electricity",
        "baseline_lifetime_prod": 8000.0,
        "range_improvement": 1000.0,
        "unit_definition": "MW installed capacity",
        "unit_service_life_yrs": 40,
        "specific_production_units": "MWh/MW/year",
    },
    "ev_electrification": {
        "displaced_resource": "Gasoline",
        "baseline_lifetime_prod": 12000.0,
        "range_improvement": 6.667,          # 1/(1−0.85): 85% of gasoline baseline displaced
        "unit_definition": "vehicles",
        "unit_service_life_yrs": 12,
        "specific_production_units": "L gasoline displaced/vehicle/year",
    },
    "climate_software": {
        "displaced_resource": "US electricity",
        "baseline_lifetime_prod": 50.0,
        "range_improvement": 1000.0,
        "unit_definition": "enterprise customers",
        "unit_service_life_yrs": 10,
        "specific_production_units": "MWh saved/customer/year",
    },
    "industrial_decarb": {
        "displaced_resource": "Natural Gas",
        "baseline_lifetime_prod": 500.0,
        "range_improvement": 1000.0,
        "unit_definition": "industrial installations",
        "unit_service_life_yrs": 15,
        "specific_production_units": "MMBtu displaced/unit/year",
    },
    "ai_ml": {
        "displaced_resource": "US electricity",
        "baseline_lifetime_prod": 10.0,
        "range_improvement": 1000.0,
        "unit_definition": "enterprise deployments",
        "unit_service_life_yrs": 5,
        "specific_production_units": "MWh optimized/deployment/year",
    },
}

# Base archetypes inherit from closest match
ARCHETYPE_CARBON_DEFAULTS["base_capital_intensive"] = ARCHETYPE_CARBON_DEFAULTS["industrial_decarb"].copy()
ARCHETYPE_CARBON_DEFAULTS["base_software"] = ARCHETYPE_CARBON_DEFAULTS["climate_software"].copy()
ARCHETYPE_CARBON_DEFAULTS["base_sw_hw_hybrid"] = ARCHETYPE_CARBON_DEFAULTS["battery_storage_utility"].copy()
ARCHETYPE_CARBON_DEFAULTS["base_hard_tech"] = ARCHETYPE_CARBON_DEFAULTS["nuclear_smr"].copy()
ARCHETYPE_CARBON_DEFAULTS["custom"] = {
    "displaced_resource": "US electricity",
    "baseline_lifetime_prod": 100.0,
    "range_improvement": 1.4,   # default example: 1.4× CI improvement → 28.6% displacement
    "unit_definition": "units",
    "unit_service_life_yrs": 10,
    "specific_production_units": "",
}


def get_carbon_defaults(archetype: str) -> dict:
    """Return carbon model defaults for a given archetype."""
    return ARCHETYPE_CARBON_DEFAULTS.get(archetype, ARCHETYPE_CARBON_DEFAULTS["custom"]).copy()


# =============================================================================
# SECTION 1: GLOBAL ASSUMPTIONS – Carbon Intensity Database
# =============================================================================
# Source: Fund 1 tab, rows 24-27
# Row 24 MN = Natural Gas label,  MO = 0.053*1.4 (flat 30 years)
# Row 25 MN = Global electricity, MO = 0.48     (linear decline to 0 over 30 y)
# Row 26 MN = Limestone,          MO = 44/100+3/1000  (flat 30 years)
# Row 27 MN = Li-ion Battery,     MO = 66       (scales with global electricity)

class CarbonIntensityDB:
    """
    Reference carbon intensity values and 30-year time series.
    Source: Fund 1 tab, columns MN:NR (rows 8-60), 30-year step series (step 1=col MO).

    Call build_series(resource) to get a list of 30 annual CI values
    starting from the resource's base year.
    Call get_ci_for_year1(resource, launch_year) to get the Year-1 CI for a
    given commercial launch year (equivalent to the JE cell in the spreadsheet).
    """

    # Base-year CI values (Fund 1 col MO = step 1 of the 30-year series).
    # NOTE: names marked *** are used by PORTFOLIO_DATA — do NOT rename.
    BASES: dict[str, float] = {
        # *** Existing resources — names preserved for PORTFOLIO_DATA compat ***
        "Natural Gas":              0.0742,           # tCO₂/MMBTU  (row 24; =0.053*1.4)
        "Global electricity":       0.48,             # tCO₂/MWh    (row 25)
        "US electricity":           0.40,             # tCO₂/MWh    (row 8)
        "Limestone":                44/100 + 3/1000,  # tCO₂/tonne  (row 26 ≈ 0.443)
        "Li-ion Battery embodied":  66.0,             # tCO₂/MWh    (row 27, BNEF 2021)
        # *** New resources from Fund 1 MN:NR ***
        "Diesel":                   0.0102,           # tCO₂/gallon (row 10, flat)
        "Gasoline":                 0.0085,           # tCO₂/gallon (row 16, flat)
        "Natural Gas (CCGT)":       0.603,            # tCO₂/MWh    (row 19, combined cycle)
        "Li-ion Battery EV":        33.6,             # tCO₂/MWh    (row 33, EV operating)
        "Battery Cathode NMC62":    0.00768,          # tCO₂/kg     (row 38, BNEF)
        "Polypropylene":            1_600_000.0,      # tCO₂/Mt     (row 43, incineration)
        "Limestone calcination":    0.7857142857,     # tCO₂/tonne  (row 47)
        "Crushed Limestone":        0.002015929423,   # tCO₂/tonne  (row 48)
        "Nickel":                   22.0,             # tCO₂/tonne  (laterite ore pathway, updated from 4.9 BNEF avg)
        "Gas to Electricity":       0.144752,         # tCO₂/MWh    (row 54, net grid benefit)
        "Gas Turbine (CCGT)":       0.603,            # tCO₂/MWh    (row 60, same as CCGT)
    }

    # Calendar year that col MO (index 0) represents for each resource.
    BASE_YEARS: dict[str, int] = {
        "Natural Gas":              2022,  # flat  — base year irrelevant
        "Global electricity":       2011,  # linear to zero over 30 steps
        "US electricity":           2017,  # linear to zero over 30 steps
        "Limestone":                2022,  # flat
        "Li-ion Battery embodied":  2011,  # proportional to global electricity
        "Diesel":                   2022,  # flat
        "Gasoline":                 2022,  # flat
        "Natural Gas (CCGT)":       2022,  # flat
        "Li-ion Battery EV":        2011,  # same base as global electricity
        "Battery Cathode NMC62":    2011,  # same base as global electricity
        "Polypropylene":            2022,  # flat
        "Limestone calcination":    2022,  # flat
        "Crushed Limestone":        2022,  # flat
        "Nickel":                   2022,  # flat
        "Gas to Electricity":       2017,  # same timing as US electricity
        "Gas Turbine (CCGT)":       2022,  # flat
    }

    # Annual decline per step (tCO₂/unit per year).
    # Flat resources: 0.0.  Linear resources: derived from Fund 1 MO→MP difference.
    STEPS: dict[str, float] = {
        "Natural Gas":              0.0,
        "Global electricity":       0.48  / 30,   # ≈ 0.016
        "US electricity":           0.40  / 30,   # ≈ 0.01333
        "Limestone":                0.0,
        "Li-ion Battery embodied":  2.2,           # 66 → 2.2 over 30 steps
        "Diesel":                   0.0,
        "Gasoline":                 0.0,
        "Natural Gas (CCGT)":       0.0,
        "Li-ion Battery EV":        1.6,           # 33.6 → 0 in 21 steps; capped at 0
        "Battery Cathode NMC62":    0.000256,      # 0.00768 → 0.000256 over 30 steps
        "Polypropylene":            0.0,
        "Limestone calcination":    0.0,
        "Crushed Limestone":        0.0,
        "Nickel":                   0.0,
        "Gas to Electricity":       0.40  / 30,   # same step as US electricity (goes negative)
        "Gas Turbine (CCGT)":       0.0,
    }

    # Resources whose CI may legitimately go below zero (net benefit resources).
    ALLOW_NEGATIVE: frozenset[str] = frozenset({"Gas to Electricity"})

    @classmethod
    def build_series(cls, resource: str, n_years: int = 30) -> list[float]:
        """
        Return an n_years list of CI values starting from the resource base year.
        Index 0 = base year (col MO in Fund 1 spreadsheet).
        """
        base = cls.BASES.get(resource)
        if base is None:
            raise ValueError(
                f"Unknown resource '{resource}'. Valid: {list(cls.BASES)}"
            )
        step           = cls.STEPS.get(resource, 0.0)
        allow_negative = resource in cls.ALLOW_NEGATIVE
        vals = []
        for i in range(n_years):
            v = base - i * step
            if not allow_negative:
                v = max(0.0, v)
            vals.append(v)
        return vals

    @classmethod
    def get_ci_for_year1(cls, resource: str, commercial_launch_year: int) -> float:
        """
        Return the CI value that the spreadsheet uses for Year 1 of a company
        launching in `commercial_launch_year`.  Equivalent to reading the JE cell.
        """
        series    = cls.build_series(resource)
        base_year = cls.BASE_YEARS.get(resource, 2022)
        offset    = max(0, commercial_launch_year - base_year)
        return series[offset] if offset < len(series) else series[-1]

    @classmethod
    def get_ci_series_for_company(
        cls, resource: str, commercial_launch_year: int, n_years: int = 10
    ) -> list[float]:
        """
        Return the n-year CI slice for a company's deployment period.
        Equivalent to reading cells JE:JN in the spreadsheet.
        """
        full      = cls.build_series(resource, 60)          # generous length
        base_year = cls.BASE_YEARS[resource]
        offset    = max(0, commercial_launch_year - base_year)
        sl        = full[offset: offset + n_years]
        while len(sl) < n_years:
            sl.append(sl[-1] if sl else 0.0)
        return sl


# =============================================================================
# SECTION 2: INPUTS – Per-company Data Classes
# =============================================================================

@dataclass
class VolumeInputs:
    """
    Volume forecast inputs (Fund 1 cols HZ:IR).
    All `year_volumes` values are in the unit defined by `unit_definition`.
    Year 1 corresponds to `commercial_launch_yr`.

    Column sources
    --------------
    HZ  unit_definition            – INPUT (hardcoded text)
    IA  unit_service_life_yrs      – INPUT
    IB  tam_10y                    – INPUT or formula
    IC  tam_units                  – INPUT (text)
    ID  sam_10y                    – INPUT or =IB*IE
    IE  sam_pct_of_tam             – INPUT
    IF  sam_explanation            – INPUT (text)
    IG  annual_retention_rate      – INPUT (typically 0.99)
    IH  commercial_launch_yr       – INPUT (first non-pilot commercial year)
    II  year_volumes[0]            – INPUT or from 'C Analysis Assumptions-N'!L
    IJ  year_volumes[1]            – from 'C Analysis Assumptions-N'!M or hardcoded
    IK–IR year_volumes[2-9]        – from 'C Analysis Assumptions-N'!N:U or extrapolated
    IS  s_curve_M                  – SOLVE: max penetration rate 5-80% (optional)
    IT  s_curve_K                  – SOLVE: speed factor 0.4-0.7 (optional)
    IU  s_curve_x                  – SOLVE: year at 50% of max penetration (optional)
    """
    unit_definition:        str
    unit_service_life_yrs:  float
    tam_10y:                float
    tam_units:              str
    sam_10y:                float
    sam_pct_of_tam:         float
    sam_explanation:        str
    annual_retention_rate:  float
    commercial_launch_yr:   int
    year_volumes:           list[float]   # 10 values, one per model year

    # S-curve solver params (IS:IU) – only for companies using S-curve projection
    s_curve_M: Optional[float] = None    # max penetration rate (fraction, e.g. 0.30)
    s_curve_K: Optional[float] = None    # speed of penetration
    s_curve_x: Optional[float] = None    # year at 50% of max penetration

    # Analysis horizon: number of calendar years over which to sum lifecycle impact.
    # User-configurable — set to 10, 20, or 30 to match desired reporting period.
    # PRIME Coalition recommends a 30-year ERP horizon (PRIME 2017, §4).
    # VoLo default is 10 years (Fund 1 model end 2031 from ~2022 launch).
    # This parameter drives the multi-cohort accumulation window in build_carbon_intermediates().
    # Historical note: the spreadsheet set this per-company as min(10, 2031-launch_yr+1).
    n_ll_years: int = 10


@dataclass
class OperatingCarbonInputs:
    """
    Operating (in-use) carbon displacement inputs (Fund 1 cols IZ:JD).

    Column sources
    --------------
    IZ  displaced_resource         – INPUT text; key for CarbonIntensityDB
    JA  baseline_lifetime_prod     – INPUT or CALC; total energy/resource consumed
                                     per unit over its full service life
                                     e.g. for BF HVAC: 10/0.3 * service_life (MWh)
                                          for Banyan: 1500 * service_life * 1000 * 0.5
    JB  specific_production_units  – INPUT (text, documentation only)
    JC  range_improvement          – INPUT; CI improvement factor (how many times lower
                                     the new technology's CI is vs. conventional).
                                     factor=1.4 → displacement fraction = 1−1/1.4 = 0.286
                                     factor=1000 → near-zero-CI (solar/wind/nuclear, ≈99.9%)
                                     factor=0   → no displacement
    JD  displaced_volume_per_unit  – CALCULATED: = (1 − 1/JC) × JA
    JE–JN ci_year1 / ci_series     – Lookup from CI database rows (MN:NR) at the
                                     specific column offset for this company's Year 1.
                                     Each company has a manually-set pointer in the sheet.
                                     In Python: computed via CarbonIntensityDB or overridden.

    Per-company CI override fields
    ------------------------------
    ci_year1_override: if set, bypasses CarbonIntensityDB.get_ci_for_year1() and uses
                       this value directly as Year 1 CI (for hardcoded or unusual resources).
    ci_annual_decline: if set, overrides the per-step decline; default = base/30 per year
                       (0 for flat resources like Natural Gas, Limestone).
    """
    displaced_resource:         str
    baseline_lifetime_prod:     float     # JA: total production per unit over service life
    specific_production_units:  str       # JB: documentation
    range_improvement:          float     # JC: fraction displaced (e.g. 0.78 for 78%)

    # Optional per-company CI overrides (for non-standard lookup patterns in sheet)
    ci_year1_override:   Optional[float] = None  # override CarbonIntensityDB Year-1 lookup
    ci_annual_decline:   Optional[float] = None  # override step-size; None = use DB default


@dataclass
class EmbodiedCarbonInputs:
    """
    Embodied (manufacturing/upstream) carbon displacement inputs (Fund 1 cols JZ:KD).

    Column sources
    --------------
    KA  displaced_resource         – INPUT text; key for CarbonIntensityDB (or "Not Modeled")
    KB  baseline_production        – INPUT; typical production intensity per unit per year
    KC  specific_production_units  – INPUT (text, documentation)
    KD  range_improvement          – INPUT; fraction improvement (0-1)
    KE  displaced_vol_per_unit     – CALCULATED: = KD * KB

    KP formula: KP = $KE_ref * KF * II  (= ke_used * emb_ci_yr1 * volume_yr1)

    All companies now use their own per-company KE value.
    """
    displaced_resource:         Optional[str]  # KA
    baseline_production:        float          # KB
    specific_production_units:  str            # KC
    range_improvement:          float          # KD
    ci_year1_override:          Optional[float] = None
    ci_annual_decline:          Optional[float] = None


@dataclass
class PortfolioInputs:
    """
    Financial and ownership inputs for VoLo portfolio metrics.

    Simplified from the original spreadsheet which had four separate fields
    (LN, LQ, Z, AM) into two cleaner inputs:

    volo_pct        – VoLo's fully diluted ownership % (replaces both LN and AM).
                      Used for pro-rata attribution (LO = LL × volo_pct) and for
                      the tonnes-per-dollar efficiency metric.

    volo_investment – Total VoLo investment in dollars (replaces both LQ and Z×1000).
                      Used as the denominator in the t/$ metric (LR = LL × volo_pct / volo_investment).
    """
    volo_pct:        float   # VoLo fully diluted ownership % (LN / AM consolidated)
    volo_investment: float   # Total VoLo investment in $ (LQ / Z consolidated)


@dataclass
class CompanyModel:
    """
    Complete per-company model record.  Composes all input sections.
    """
    company_name:             str
    stage:                    str          # e.g. "Portfolio", "Due Diligence", "Hold"
    risk_adjustment_divisor:  float        # LP = LO / this;  LS = LR / this (3 or 6)
    volume:                   VolumeInputs
    operating_carbon:         OperatingCarbonInputs
    embodied_carbon:          EmbodiedCarbonInputs
    portfolio:                PortfolioInputs
    actuals_2021:             Optional[float] = None   # MG: 2021 actual revenue or units
    actuals_unit_type:        Optional[str]  = None    # MH: "Revenue" | "Units"
    adjustment_factor:        float          = 1.0     # MK: manual 2021 impact adjustment


# =============================================================================
# SECTION 3: INTERMEDIATES
# =============================================================================

@dataclass
class CarbonIntermediates:
    """
    All computed intermediate values for one company (one spreadsheet row).
    Lists have `n_ll_years` elements (analysis horizon, default 10, can be 20 or 30).
    Operating impact uses true multi-cohort accounting: each deployment cohort
    contributes displaced carbon over its full service life, with CI declining
    at the grid-decarbonisation rate in each operating year (PRIME 2017, §2 & §5).
    Embodied impact is attributed at the deployment year (single-cohort).
    """
    # --- Operating carbon (cols JD, JE:JN, JO:JX, JY) ---
    displaced_volume_per_unit:  float        # JD = JC * JA
    operating_ci_series:        list[float]  # JE:JN  carbon intensity per year
    annual_operating_impact:    list[float]  # JO:JX  = JD * units[y] * CI[y]
    total_operating_impact:     float        # JY     = SUM(JO:JX)

    # --- Embodied carbon (cols KE, KF:KO, KP:KY, KZ) ---
    embodied_displaced_vol:     float        # KE  = KD * KB
    embodied_ci_series:         list[float]  # KF:KO
    annual_embodied_impact:     list[float]  # KP:KY  = CI[y] * KE  (see $KE$6 note)
    total_embodied_impact:      float        # KZ     = SUM(KP:KY)

    # --- Lifecycle (cols LB:LK, LL) ---
    annual_lifecycle_impact:    list[float]  # LB:LK  = operating[y] + embodied[y]
    total_lifecycle_impact:     float        # LL     = SUM(LB:LK)

    # --- 2021 actuals scaling (MI, MJ, ML) ---
    actual_to_forecast_ratio:   Optional[float] = None  # MI = MG / II (yr-1 volume)
    impact_2021_actuals:        Optional[float] = None  # MJ = MI * JO (yr-1 op impact)
    impact_2021_adjusted:       Optional[float] = None  # ML = MK * MJ


@dataclass
class PortfolioOutputs:
    """
    Final VoLo-level outputs for one company (cols LM:LS).
    """
    company_tonnes:             float   # LM = LL
    volo_tonnes_prorata:        float   # LO = LL * LN
    volo_tonnes_risk_adjusted:  float   # LP = LO / risk_divisor
    volo_tonnes_per_dollar:     float   # LR = LM * AM / (Z * 1000)
    risk_adj_tonnes_per_dollar: float   # LS = LR / risk_divisor


# =============================================================================
# SECTION 4: S-CURVE UTILITY (cols IS:IW)
# =============================================================================

def compute_s_curve_share(M: float, K: float, x: float, year: int = 7) -> float:
    """
    Logistic S-curve market share at a given forecast year.
    Spreadsheet formula (col IV): =M / (1 + EXP(-K * (year - x)))

    Parameters
    ----------
    M    : maximum achievable penetration rate (fraction, e.g. 0.30 = 30%)
           IS column, typical range 0.05-0.80
    K    : speed of penetration factor
           IT column, typical range 0.4-0.7
    x    : year at which market penetration reaches 50% of M
           IU column, typical range 7-15
    year : forecast year (1-indexed from commercial launch); spreadsheet uses 7 for IV
    """
    return M / (1 + math.exp(-K * (year - x)))


def extrapolate_volume_ratio(vol_n: float, vol_nm1: float) -> float:
    """
    Simple ratio extrapolation used for late-year volumes.
    Spreadsheet pattern (e.g. IR8): =(IQ8/IP8)*IQ8
    i.e. apply the same growth ratio as the prior year pair.
    """
    if vol_nm1 == 0:
        return 0.0
    return (vol_n / vol_nm1) * vol_n


# =============================================================================
# SECTION 5: CALCULATION ENGINE
# =============================================================================

# Spreadsheet value of $KE$6 (Banyan row 6 embodied displaced volume)
# Banyan has no KB/KD values → KE6 = KD6 * KB6 = 0.0
# All other companies' KP:KY formulas reference this cell (bug).


def _build_ci_series(
    inputs: OperatingCarbonInputs | EmbodiedCarbonInputs,
    commercial_launch_yr: int,
    n_years: int = 10,
) -> list[float]:
    """
    Build a 10-year carbon intensity series for either operating or embodied carbon.
    Uses per-company overrides if provided, otherwise falls back to CarbonIntensityDB.
    """
    resource = getattr(inputs, "displaced_resource", None)
    ci_y1    = inputs.ci_year1_override
    ci_step  = inputs.ci_annual_decline

    if resource is None or resource in ("Not Modeled",):
        return [0.0] * n_years

    # Determine Year-1 CI
    if ci_y1 is None:
        try:
            ci_y1 = CarbonIntensityDB.get_ci_for_year1(resource, commercial_launch_yr)
        except ValueError:
            # Unknown resource – no CI available
            return [0.0] * n_years

    # Determine annual decline step — use STEPS dict as authoritative source.
    # Falls back to base/30 only for unknown resources not in the DB.
    if ci_step is None:
        if resource in CarbonIntensityDB.STEPS:
            ci_step = CarbonIntensityDB.STEPS[resource]
        else:
            base = CarbonIntensityDB.BASES.get(resource, 0.0)
            ci_step = base / 30  # linear decline to zero over 30 years (unknown resource fallback)

    series, val = [], ci_y1
    for _ in range(n_years):
        series.append(max(val, 0.0))
        val -= ci_step
    return series


def build_carbon_intermediates(
    company: CompanyModel,
) -> CarbonIntermediates:
    """
    Compute all intermediate carbon calculations for one company.

    Parameters
    ----------
    company                          : full CompanyModel record
    """
    v  = company.volume
    oc = company.operating_carbon
    ec = company.embodied_carbon

    # Analysis horizon and per-unit service life (PRIME §4: recommend 30-year horizon)
    horizon      = v.n_ll_years                       # user-defined calendar-year window
    n_deploy     = len(v.year_volumes)                # deployment years with volume data
    service_life = v.unit_service_life_yrs            # years each deployed unit operates

    # ------------------------------------------------------------------
    # OPERATING CARBON — multi-cohort accounting (PRIME 2017, §2 & §5)
    # ------------------------------------------------------------------
    # JD: displaced volume per unit over its FULL service life (already encodes service_life
    #     via baseline_lifetime_prod = production_rate × service_life).
    # range_improvement is an "improvement factor": how many times lower the new
    # technology's carbon intensity is vs. the displaced conventional resource.
    #   factor = 1.4  →  fraction displaced = 1 − 1/1.4 = 0.286
    #   factor = 1000 →  near-zero-CI technology (solar, wind)  →  fraction ≈ 0.999
    #   factor = 0    →  no displacement (guard: returns 0)
    _oc_factor = oc.range_improvement
    jd = (1.0 - 1.0 / _oc_factor) * oc.baseline_lifetime_prod if _oc_factor > 0 else 0.0

    # Annual displacement per unit per operating year = JD / service_life
    # (the total lifetime displacement spread uniformly across each year of operation)
    jd_annual = jd / max(service_life, 1.0)

    # Build CI series over the full analysis horizon (each calendar year has its own
    # grid-decarbonisation rate; later cohorts benefit from a cleaner grid — PRIME §2)
    op_ci = _build_ci_series(oc, v.commercial_launch_yr, horizon)

    # Extend volume array with zeros beyond the deployment forecast period
    year_vols = v.year_volumes
    year_vols_ext = list(year_vols) + [0.0] * max(0, horizon - n_deploy)

    # Multi-cohort accumulation:
    #   For each calendar year t in [0, horizon):
    #     impact[t] = Σ_{d: d≤t, t-d < service_life} jd_annual × vol[d] × CI[t]
    #   i.e. every cohort deployed in year d that is still operating in year t
    #   (operating years 0 … service_life-1 after deployment) contributes
    #   its annual per-unit displacement × that year's CI × cohort size.
    annual_op = []
    for t in range(horizon):
        ci_t     = op_ci[t] if t < len(op_ci) else 0.0
        impact_t = 0.0
        # Cohorts still alive in calendar year t
        d_min = max(0, t - int(service_life) + 1)
        d_max = min(t, n_deploy - 1)
        for d in range(d_min, d_max + 1):
            # Fractional-year guard: only if cohort has not exceeded service life
            if (t - d) < service_life:
                impact_t += jd_annual * year_vols_ext[d] * ci_t
        annual_op.append(impact_t)
    total_op = sum(annual_op)

    # ------------------------------------------------------------------
    # EMBODIED CARBON — single-cohort (attributed at deployment; PRIME §3)
    # ------------------------------------------------------------------
    # KE: embodied displaced volume per unit — same improvement-factor convention
    _ec_factor = ec.range_improvement
    ke_used = (1.0 - 1.0 / _ec_factor) * ec.baseline_production if _ec_factor > 0 else 0.0

    # KF:KO: embodied carbon intensity series (10 deployment years)
    emb_ci = _build_ci_series(ec, v.commercial_launch_yr, n_deploy)

    # KP:KY: annual embodied impact = KE × CI[y] × volume[y]   (deployment-year attribution)
    annual_emb_base = [ke_used * emb_ci[y] * year_vols[y] for y in range(n_deploy)]
    # Zero-pad to match the analysis horizon
    annual_emb = annual_emb_base + [0.0] * max(0, horizon - n_deploy)
    total_emb  = sum(annual_emb)

    # ------------------------------------------------------------------
    # LIFECYCLE (full analysis horizon)
    # total_lc = Σ_{t=0}^{horizon-1} (operating[t] + embodied[t])
    # ------------------------------------------------------------------
    annual_lc = [annual_op[t] + annual_emb[t] for t in range(horizon)]
    total_lc  = sum(annual_lc)

    # ------------------------------------------------------------------
    # 2021 ACTUALS SCALING (MI, MJ, ML)
    # ------------------------------------------------------------------
    ratio = impact_act = impact_adj = None
    if company.actuals_2021 is not None and year_vols[0] not in (None, 0):
        ratio      = company.actuals_2021 / year_vols[0]    # MI = MG / II
        impact_act = ratio * annual_op[0]                   # MJ = MI * JO
        impact_adj = company.adjustment_factor * impact_act # ML = MK * MJ

    return CarbonIntermediates(
        displaced_volume_per_unit=jd,
        operating_ci_series=op_ci,
        annual_operating_impact=annual_op,
        total_operating_impact=total_op,
        embodied_displaced_vol=ke_used,
        embodied_ci_series=emb_ci,
        annual_embodied_impact=annual_emb,
        total_embodied_impact=total_emb,
        annual_lifecycle_impact=annual_lc,
        total_lifecycle_impact=total_lc,
        actual_to_forecast_ratio=ratio,
        impact_2021_actuals=impact_act,
        impact_2021_adjusted=impact_adj,
    )


def compute_portfolio_outputs(
    company: CompanyModel,
    intermediates: CarbonIntermediates,
) -> PortfolioOutputs:
    """
    Compute final VoLo portfolio-level outputs.

    company_tonnes  = LL
    volo_prorata    = LL × volo_pct
    volo_risk_adj   = volo_prorata / risk_divisor
    tonnes_per_$    = LL × volo_pct / volo_investment
    risk_adj_t/$    = tonnes_per_$ / risk_divisor
    """
    p   = company.portfolio
    lc  = intermediates.total_lifecycle_impact
    div = company.risk_adjustment_divisor

    company_t  = lc                              # total lifecycle CO₂
    prorata_t  = lc * p.volo_pct                 # ownership-weighted
    risk_adj_t = prorata_t / div                 # risk-adjusted

    if p.volo_investment and p.volo_investment != 0:
        tpd    = company_t * p.volo_pct / p.volo_investment
        ra_tpd = tpd / div
    else:
        tpd = ra_tpd = float("nan")

    return PortfolioOutputs(
        company_tonnes=company_t,
        volo_tonnes_prorata=prorata_t,
        volo_tonnes_risk_adjusted=risk_adj_t,
        volo_tonnes_per_dollar=tpd,
        risk_adj_tonnes_per_dollar=ra_tpd,
    )


def run_company(
    company: CompanyModel,
) -> tuple[CarbonIntermediates, PortfolioOutputs]:
    """
    Full pipeline for one company: inputs → intermediates → outputs.
    Returns (CarbonIntermediates, PortfolioOutputs).
    """
    interm  = build_carbon_intermediates(company)
    outputs = compute_portfolio_outputs(company, interm)
    return interm, outputs


# =============================================================================
# SECTION 6: PORTFOLIO DATA
# =============================================================================
# Each record is populated from the spreadsheet.
# year_volumes[0:10] = Fund 1 cols II:IR
#   (some from 'C Analysis Assumptions-N'!L:U, some hardcoded, some extrapolated)
# ci_year1_override reflects the exact spreadsheet JE formula resolved value
#   (computed as: CI_base - offset_steps * CI_base/30)

PORTFOLIO_COMPANIES: list[CompanyModel] = [

    # -------------------------------------------------------------------------
    # Banyan Infrastructure (row 6)
    # JA = 1500 * 40 * 1000 * (1/2) = 30,000,000
    # JC = 1/8 (row 6 formula: =1/8, reflecting ~150bps / 1200bps)
    # JE6 = MZ25 = 0.48 - 11*(0.48/30) = 0.304  [Global elec, step 11 from MO25]
    # LP divisor = 3 (=LO6/3)
    # -------------------------------------------------------------------------
    CompanyModel(
        company_name="Banyan Infrastructure",
        stage="Portfolio",
        risk_adjustment_divisor=3,
        volume=VolumeInputs(
            unit_definition="B$ solar finance",
            unit_service_life_yrs=40,
            tam_10y=6500,
            tam_units="B$ of US Energy Infrastructure Finance",
            sam_10y=4600,
            sam_pct_of_tam=0.71,
            sam_explanation="% new debt in capital stack for renewable energy by 2030",
            annual_retention_rate=0.99,
            commercial_launch_yr=2022,
            # Source: 'C Analysis Assumptions-N'!L13:U13 (years 2022-2031)
            # Years 7-10: growth formula =Q13*(1+W13)^n where W13=Q13/P13-1≈36.1%
            year_volumes=[4.3, 18.56, 33.44, 54.89, 79.95, 108.8, 148.06, 201.49, 274.2, 373.14],
            n_ll_years=10,   # LL6 = sum(LB6:LK6)  → launch 2022, model end 2031
        ),
        operating_carbon=OperatingCarbonInputs(
            displaced_resource="Global electricity",
            # JA = 1500 MWh/MW * service_life * 1000 MW/$B * 0.5 recycled
            baseline_lifetime_prod=1500 * 40 * 1000 * 0.5,
            specific_production_units="MWh electricity / $B invested",
            range_improvement=8/7,      # JC was 1/8 → factor = 1/(1−1/8) = 8/7 ≈ 1.1429
            # JE6 = MZ25: Global elec series, step 11 from base year 2011
            ci_year1_override=0.48 - 11 * (0.48 / 30),  # 0.304 tCO₂/MWh
        ),
        embodied_carbon=EmbodiedCarbonInputs(
            displaced_resource=None, baseline_production=0.0,
            specific_production_units="", range_improvement=0.0,
        ),
        portfolio=PortfolioInputs(
            volo_pct=0.1119,
            volo_investment=5_899_998.83,
        ),
        actuals_2021=1.6,
        actuals_unit_type="$B on platform",
        adjustment_factor=0.5,
    ),

    # -------------------------------------------------------------------------
    # Blue Frontier (row 8)
    # JA = 10/0.3 * 20 = 666.7 MWh over service life
    # JE8 = MU8 = 0.40 - 6*(0.40/30) = 0.32  [per-company US elec, step 6 from MO8]
    # IR8 = (IQ8/IP8)*IQ8 (ratio extrapolation)
    # LP divisor = 6
    # -------------------------------------------------------------------------
    CompanyModel(
        company_name="Blue Frontier",
        stage="Portfolio",
        risk_adjustment_divisor=6,
        volume=VolumeInputs(
            unit_definition="13 ton HVAC",
            unit_service_life_yrs=20,
            tam_10y=2_000_000,
            tam_units="HVAC Units",
            sam_10y=660_000,
            sam_pct_of_tam=0.33,
            sam_explanation="1/3 are 3-5 tonne units",
            annual_retention_rate=0.99,
            commercial_launch_yr=2023,
            # Years 1-9 hardcoded; Year 10 = (IQ8/IP8)*IQ8 ≈ 182,044
            year_volumes=[0, 6, 40, 552, 1806, 5194, 13500, 32400, 76800, 182044.0],
            n_ll_years=9,    # LL8 = sum(LB8:LJ8)  → launch 2023, model end 2031
        ),
        operating_carbon=OperatingCarbonInputs(
            displaced_resource="US electricity",
            # JA = 10/0.3 * service_life = 666.7 MWh over 20y life
            baseline_lifetime_prod=10 / 0.3 * 20,
            specific_production_units="MWh of HVAC runtime over service life",
            range_improvement=4.545455,  # was 0.78 → factor = 1/(1−0.78) = 1/0.22 ≈ 4.5455
            # JE8 = MU8: per-company US elec CI at step 6 from MO8=0.40 (base yr~2017)
            ci_year1_override=0.40 - 6 * (0.40 / 30),   # 0.32 tCO₂/MWh
            ci_annual_decline=0.40 / 30,
        ),
        embodied_carbon=EmbodiedCarbonInputs(
            displaced_resource=None, baseline_production=0.0,
            specific_production_units="", range_improvement=0.0,
        ),
        portfolio=PortfolioInputs(
            volo_pct=0.1122,
            volo_investment=4_499_433.85,
        ),
    ),

    # -------------------------------------------------------------------------
    # BlueDot Photonics (row 10)  — company pivoted; Year 1 volume = 0
    # JA = 1500 * service_life = 15,000 MWh/MW
    # JE10 = NC25: Global elec step 14 from MO25(base 2011) → Year 1 = 2025
    # S-curve: M=0.30, K=0.65, x=6 — for illustrative volume projection
    # LP divisor = 6
    # -------------------------------------------------------------------------
    CompanyModel(
        company_name="BlueDot Photonics",
        stage="Portfolio",
        risk_adjustment_divisor=6,
        volume=VolumeInputs(
            unit_definition="MW of Solar",
            unit_service_life_yrs=10,
            tam_10y=131_000,
            tam_units="MW PV per year",
            sam_10y=117_900,      # =IB10*0.9
            sam_pct_of_tam=0.9,
            sam_explanation="Mono crystalline focus",
            annual_retention_rate=0.99,
            commercial_launch_yr=2023,
            # Company pivoted; all years hardcoded 0 in sheet
            year_volumes=[0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
            s_curve_M=0.30, s_curve_K=0.65, s_curve_x=6,
            n_ll_years=8,    # LL10 = sum(LB10:LI10)  → launch 2023 (treated as 2024)
        ),
        operating_carbon=OperatingCarbonInputs(
            displaced_resource="Global electricity",
            baseline_lifetime_prod=1500 * 10,   # JA = 1500 * IA = 15,000 MWh/MW
            specific_production_units="MWh/MW over 10y life",
            range_improvement=1.111111,  # was 0.1 → factor = 1/(1−0.1) = 10/9 ≈ 1.1111
            # JE10 = NC25: step 14 from MO25 (base 2011) → aligns to 2025 launch
            ci_year1_override=0.48 - 14 * (0.48 / 30),  # 0.256 tCO₂/MWh
        ),
        embodied_carbon=EmbodiedCarbonInputs(
            displaced_resource=None, baseline_production=0.0,
            specific_production_units="", range_improvement=0.0,
        ),
        portfolio=PortfolioInputs(
            volo_pct=0.0,
            volo_investment=404_999.73,
        ),
    ),

    # -------------------------------------------------------------------------
    # Daanaa (row 16) – solar panel tech
    # JA = 1500 * service_life * 0.33 (=baseline_lifetime_prod)
    # JC = 0.1 (10% improvement in solar yield)
    # JE16 = NC25: step 14 from global elec base → Year 1 CI = 0.256
    # LP divisor = 6
    # -------------------------------------------------------------------------
    CompanyModel(
        company_name="Daanaa",
        stage="Portfolio",
        risk_adjustment_divisor=6,
        volume=VolumeInputs(
            unit_definition="MW of Solar",
            unit_service_life_yrs=25,
            tam_10y=131_000,
            tam_units="MW PV per year",
            sam_10y=43_667,
            sam_pct_of_tam=0.33,
            sam_explanation="Residential solar focus",
            annual_retention_rate=0.99,
            commercial_launch_yr=2023,
            # Source: 'C Analysis Assumptions-N' row 15, cols L:U
            year_volumes=[1.75, 150.5, 560.0, 1000.0, 1600.0, 2560.0, 4096.0, 5739.0, 7327.0, 8587.0],
            n_ll_years=9,    # LL16 = sum(LB16:LJ16) → launch 2023
        ),
        operating_carbon=OperatingCarbonInputs(
            displaced_resource="Global electricity",
            baseline_lifetime_prod=32500,     # JA=32500 (data_only); JC was 0.1; JD=3250
            specific_production_units="MWh electricity / MW over service life",
            range_improvement=1.111111,  # was 0.1 → factor = 1/(1−0.1) = 10/9 ≈ 1.1111
            ci_year1_override=0.48 - 14 * (0.48 / 30),  # NC25 = 0.256
        ),
        embodied_carbon=EmbodiedCarbonInputs(
            displaced_resource=None, baseline_production=0.0,
            specific_production_units="", range_improvement=0.0,
        ),
        portfolio=PortfolioInputs(
            volo_pct=0.097,
            volo_investment=3_999_999.34,
        ),
    ),

    # -------------------------------------------------------------------------
    # HEVO (row 25) – wireless EV charging
    # JA = 125 MWh/EV (10y life), JC=1.0 (full displacement)
    # JE25 = MU25: Global elec step 6 → 0.48 - 6*(0.48/30) = 0.384
    # Embodied: KB=0.085 MWh battery, KD=0.1 (10% reduction)
    # LP divisor = 6
    # -------------------------------------------------------------------------
    CompanyModel(
        company_name="HEVO",
        stage="Portfolio",
        risk_adjustment_divisor=6,
        volume=VolumeInputs(
            unit_definition="Passenger EVs",
            unit_service_life_yrs=10,
            tam_10y=48_000_000,
            tam_units="Passenger EVs",
            sam_10y=4_800_000,
            sam_pct_of_tam=0.10,
            sam_explanation="HEVO used 10% of TAM in their pro forma",
            annual_retention_rate=0.99,
            commercial_launch_yr=2024,
            # Source: 'C Analysis Assumptions-N' row 17, cols L:U
            year_volumes=[256.0, 908.0, 2420.0, 14841.0, 31625.0, 67390.0, 143604.0, 271724.0, 510561.0, 906024.0],
            n_ll_years=7,    # LL25 = sum(LB25:LH25) → launch 2024
        ),
        operating_carbon=OperatingCarbonInputs(
            displaced_resource="Global electricity",
            baseline_lifetime_prod=125,    # JA = 125 MWh / EV over 10y life
            specific_production_units="MWh per EV over service life",
            range_improvement=1000.0,    # was 1.0 (full displacement) → factor=1000
            # JE25 = MU25: step 6 from Global elec MO25 (2011 base)
            ci_year1_override=0.48 - 6 * (0.48 / 30),   # 0.384 tCO₂/MWh
        ),
        embodied_carbon=EmbodiedCarbonInputs(
            displaced_resource="Li-ion Battery embodied",
            baseline_production=0.085,    # KB: 0.085 MWh battery per EV (data_only)
            specific_production_units="MWh battery per EV",
            range_improvement=1.111111,   # KD was 0.1 → factor = 1/(1−0.1) ≈ 1.1111
            # KE25 = 0.1*0.085 = 0.0085; KF25=MR27=59.4 (step 3 from MO27=66)
            ci_year1_override=59.4,       # KF25 = MR27 (Li-ion row 27, col MR)
            ci_annual_decline=2.2,        # = 66 / 30 (row 27 Li-ion battery step)
        ),
        portfolio=PortfolioInputs(
            volo_pct=0.0201,
            volo_investment=532_000,
        ),
    ),

    # -------------------------------------------------------------------------
    # HST (row 26) – corporate PPA structuring (utility wind + solar)
    # JA = JC * IA * baseline; JC=0.02, IA=30
    # JE26 = MO25: step 0 (base year) → Year 1 CI = 0.48
    # LP divisor = 6
    # -------------------------------------------------------------------------
    CompanyModel(
        company_name="HST",
        stage="Portfolio",
        risk_adjustment_divisor=6,
        volume=VolumeInputs(
            unit_definition="MW of utility wind and solar capacity additions",
            unit_service_life_yrs=30,
            tam_10y=1_000_000,
            tam_units="MW of Energy utility wind and solar capacity",
            sam_10y=100_000,
            sam_pct_of_tam=0.10,
            sam_explanation="Corporate PPAs",
            annual_retention_rate=0.99,
            commercial_launch_yr=2021,
            # Source: 'C Analysis Assumptions-N' row 18, cols L:U (strong growth projection)
            year_volumes=[83400, 182369, 552579, 1162834, 2349156, 4745762, 9587384, 19368423, 39128068, 79046481],
        ),
        operating_carbon=OperatingCarbonInputs(
            displaced_resource="Global electricity",
            baseline_lifetime_prod=1350,   # JA=1350 (data_only); JC was 0.02; JD=27
            specific_production_units="MWh of clean energy per MW",
            range_improvement=1.020408,  # was 0.02 → factor = 1/(1−0.02) = 50/49 ≈ 1.0204
            # JE26 = MO25: base year (step 0)
            ci_year1_override=0.48,
        ),
        embodied_carbon=EmbodiedCarbonInputs(
            displaced_resource=None, baseline_production=0.0,
            specific_production_units="", range_improvement=0.0,
        ),
        portfolio=PortfolioInputs(
            volo_pct=0.01,
            volo_investment=309_999.99,
        ),
    ),

    # -------------------------------------------------------------------------
    # Heimdal (row 24) – direct air capture (tonnes CO₂ captured = 1 unit)
    # KEY: JO formula = =II24 (not JD*II*JE). Volume IS the impact. ci=1.0 preserves this.
    # JE24 = MO47 = 0.7857 (limestone CI), but is NOT used in JO formula.
    # year_volumes = [36, 36, ...] (II24:IR24 = 36 tonnes/yr, confirmed data_only)
    # Embodied: KP uses $KE24 (own row), KE=1, flat CI=0.002015929423 (custom row)
    # LP divisor = 6
    # -------------------------------------------------------------------------
    CompanyModel(
        company_name="Heimdal",
        stage="Portfolio",
        risk_adjustment_divisor=6,
        volume=VolumeInputs(
            unit_definition="Tonnes of CO₂ Captured",
            unit_service_life_yrs=1,
            tam_10y=9_000_000_000,
            tam_units="Tonnes limestone",
            sam_10y=0,
            sam_pct_of_tam=0.0,
            sam_explanation="",
            annual_retention_rate=1.0,
            commercial_launch_yr=2022,
            # Source: Fund 1 row 24 II:IR (data_only = 36 tonnes/yr, constant)
            year_volumes=[36.0] * 10,
            n_ll_years=9,    # LL24 = sum(LB24:LJ24) = 9 years
        ),
        operating_carbon=OperatingCarbonInputs(
            displaced_resource="Natural Gas",
            baseline_lifetime_prod=1.0,    # JA = 1
            specific_production_units="tonne CO₂ captured",
            range_improvement=1000.0,    # was 1.0 (full: JD=1×1=1) → factor=1000 (≈0.999)
            # JO24 = II24 (volume only; JD and JE not applied in spreadsheet formula)
            # Set CI=1.0 so annual_op = JD*vol*1 ≈ 1*36*1 = 36, matching JO=II
            ci_year1_override=1.0,
            ci_annual_decline=0.0,
        ),
        embodied_carbon=EmbodiedCarbonInputs(
            displaced_resource="Limestone",     # custom embodied CI row 24
            baseline_production=1.0,           # KB24 = 1 (kg crushed / kg limestone)
            specific_production_units="Kg Crushed/Kg Limestone",
            range_improvement=1000.0,          # KD24 was 1 (full) → factor=1000
            # KF24 = 0.002015929423 (flat custom row, confirmed data_only)
            ci_year1_override=0.002015929423,
            ci_annual_decline=0.0,
        ),
        portfolio=PortfolioInputs(
            volo_pct=0.1278,
            volo_investment=250_000,
        ),
    ),

    # -------------------------------------------------------------------------
    # Traxen (row 61) – AI fuel optimisation for Class 8 trucks
    # Displaced resource: Diesel (hardcoded as 1.0 in JE61=1.0 in source)
    # JA = baseline fuel use * service_life; JC = 0.1
    # LP divisor = 6
    # -------------------------------------------------------------------------
    CompanyModel(
        company_name="Traxen",
        stage="Portfolio",
        risk_adjustment_divisor=6,
        volume=VolumeInputs(
            unit_definition="Class 8 trucks in US",
            unit_service_life_yrs=5,
            tam_10y=0,
            tam_units="Class 8 trucks",
            sam_10y=2_960_000,
            sam_pct_of_tam=0.0,
            sam_explanation="",
            annual_retention_rate=0.99,
            commercial_launch_yr=2022,
            # Source: 'C Analysis Assumptions-N' row 29, cols L:U
            year_volumes=[4370, 7900, 10000, 20000, 40000, 69000, 100000, 134859, 161903, 179810],
        ),
        operating_carbon=OperatingCarbonInputs(
            displaced_resource="Natural Gas",   # proxy for diesel (tCO₂/gallon)
            baseline_lifetime_prod=50000,       # JA=50000 (data_only); JC was 0.1; JD=5000
            specific_production_units="gallons diesel per truck over service life",
            range_improvement=1.111111,  # was 0.1 → factor = 1/(1−0.1) = 10/9 ≈ 1.1111
            ci_year1_override=0.0102,           # JE61=MT10: diesel tCO₂/gallon
            ci_annual_decline=0.0,
        ),
        embodied_carbon=EmbodiedCarbonInputs(
            displaced_resource=None, baseline_production=0.0,
            specific_production_units="", range_improvement=0.0,
        ),
        portfolio=PortfolioInputs(
            volo_pct=0.0349,
            volo_investment=864_998.89,
        ),
    ),

    # -------------------------------------------------------------------------
    # AIcrete (row 205) – low-carbon concrete (replaces Portland cement)
    # Embodied: displaces limestone clinker emissions
    # LP divisor = 3
    # -------------------------------------------------------------------------
    CompanyModel(
        company_name="AIcrete",
        stage="Portfolio",
        risk_adjustment_divisor=3,
        volume=VolumeInputs(
            unit_definition="tonnes of concrete",
            unit_service_life_yrs=1,
            tam_10y=28_000_000_000,
            tam_units="Tonnes of Concrete Globally",
            sam_10y=4_200_000_000,
            sam_pct_of_tam=0.15,
            sam_explanation="Limited geography and ready mix focus",
            annual_retention_rate=1.0,
            commercial_launch_yr=2023,
            # Source: Fund 1 row 205, II:IR (II hardcoded; IJ=II*10; IK=IJ*10 etc.)
            year_volumes=[2_663_719, 26_637_190, 266_371_900, 2_663_719_000,
                          2_663_719_000, 2_663_719_000, 2_663_719_000,
                          2_663_719_000, 2_663_719_000, 2_663_719_000],
        ),
        operating_carbon=OperatingCarbonInputs(
            displaced_resource="Limestone",
            baseline_lifetime_prod=0.44,     # tCO₂ per tonne concrete (clinker process)
            specific_production_units="tCO₂ per tonne concrete",
            range_improvement=1.075269,  # was 0.07 → factor = 1/(1−0.07) = 100/93 ≈ 1.0753
            ci_year1_override=44/100 + 3/1000,   # constant limestone CI
            ci_annual_decline=0.0,
        ),
        embodied_carbon=EmbodiedCarbonInputs(
            displaced_resource="Limestone",
            baseline_production=1.5 * (1 / 7),   # KB: tonnes limestone per tonne concrete
            specific_production_units="tonnes limestone per tonne concrete",
            range_improvement=1.075269,  # was 0.07 → factor = 1/(1−0.07) ≈ 1.0753
            ci_year1_override=44/100 + 3/1000,
            ci_annual_decline=0.0,
        ),
        portfolio=PortfolioInputs(
            volo_pct=0.0903,
            volo_investment=1_399_999.57,
        ),
    ),

    # -------------------------------------------------------------------------
    # BlocPower (row 206) – heat pump electrification (multifamily buildings)
    # Displaces natural gas; JE206 = MO24 (Nat Gas step 0)
    # LP divisor = 3
    # -------------------------------------------------------------------------
    CompanyModel(
        company_name="BlocPower",
        stage="Portfolio",
        risk_adjustment_divisor=3,
        volume=VolumeInputs(
            unit_definition="Tonnes of heat pump capacity (ASHP)",
            unit_service_life_yrs=15,
            tam_10y=6_580 * 1_000_000,
            tam_units="TBTU US heating",
            sam_10y=21_839_020_000,
            sam_pct_of_tam=0.0,
            sam_explanation="US total 5+ multifamily heating (NRDC)",
            annual_retention_rate=0.99,
            commercial_launch_yr=2022,
            # Source: Fund 1 row 206 II:IR (hardcoded + formula growth)
            year_volumes=[2574, 9399, 31278, 103389, 191256, 353798, 654480, 1_210_702, 2_239_639, 4_143_036],
        ),
        operating_carbon=OperatingCarbonInputs(
            displaced_resource="Natural Gas",
            # JA = 246.857 (data_only confirmed); JD = (1−1/factor) × JA ≈ 117.9
            baseline_lifetime_prod=246.857,
            specific_production_units="MMBTU Natural Gas displaced per tonne ASHP",
            range_improvement=1.914366,  # was 0.4776322113 → factor = 1/(1−0.47763) ≈ 1.9144
            ci_year1_override=0.053 * 1.4,  # JE206 = MO24 (Natural Gas, step 0)
            ci_annual_decline=0.0,
        ),
        embodied_carbon=EmbodiedCarbonInputs(
            displaced_resource=None,   # KA = "Not Modeled"
            baseline_production=0.0, specific_production_units="", range_improvement=0.0,
        ),
        portfolio=PortfolioInputs(
            volo_pct=0.026,
            volo_investment=4_500_000,
        ),
    ),

    # -------------------------------------------------------------------------
    # Gaiascope (row 97) – MW traded on clean energy markets
    # JD = JC * JA = 1 * 1 = 1; CI from MO19 (custom row)
    # LP divisor = 3
    # -------------------------------------------------------------------------
    CompanyModel(
        company_name="Gaiascope",
        stage="Portfolio",
        risk_adjustment_divisor=3,
        volume=VolumeInputs(
            unit_definition="MW Traded",
            unit_service_life_yrs=1,
            tam_10y=18_000,
            tam_units="MW on clean energy market",
            sam_10y=18_000,
            sam_pct_of_tam=1.0,
            sam_explanation="",
            annual_retention_rate=1.0,
            commercial_launch_yr=2022,
            # Source: Fund 1 row 97 II:IR (hardcoded + formula growth)
            year_volumes=[1_649_000, 10_237_000, 22_000_000, 35_000_000, 47_000_000,
                          63_114_286, 84_753_469, 113_811_802, 152_832_991, 205_232_873],
        ),
        operating_carbon=OperatingCarbonInputs(
            displaced_resource="Natural Gas",
            baseline_lifetime_prod=1.0,
            specific_production_units="MWh of gas turbine generation displaced / MW traded",
            range_improvement=1000.0,    # was 1.0 (full displacement) → factor=1000
            # JE97 = MO19 (Atmo custom row): Nat Gas combined cycle + 2% CH₄ leakage = 0.603
            # Confirmed data_only MO19=0.603 tCO₂/MWh
            ci_year1_override=0.603,
            ci_annual_decline=0.0,  # Natural Gas CI is flat
        ),
        embodied_carbon=EmbodiedCarbonInputs(
            displaced_resource=None, baseline_production=0.0,
            specific_production_units="", range_improvement=0.0,
        ),
        portfolio=PortfolioInputs(
            volo_pct=0.0706,
            volo_investment=1_060_000,
        ),
    ),
]


# =============================================================================
# SECTION 7: PORTFOLIO RUNNER AND AGGREGATION
# =============================================================================

def run_portfolio(
    companies: list[CompanyModel],
) -> list[dict]:
    """
    Execute the full model for every company.
    Returns a list of result dicts (one per company) containing all
    intermediate and output values.
    """
    results = []
    for co in companies:
        interm, out = run_company(co)
        results.append({
            # Identity
            "company":                    co.company_name,
            "stage":                      co.stage,
            "unit_definition":            co.volume.unit_definition,
            "commercial_launch_yr":       co.volume.commercial_launch_yr,
            # Volume
            "year_volumes":               co.volume.year_volumes,
            # Intermediates
            "displaced_vol_per_unit":     interm.displaced_volume_per_unit,
            "operating_ci_series":        interm.operating_ci_series,
            "annual_operating_impact_t":  interm.annual_operating_impact,
            "total_operating_impact_t":   interm.total_operating_impact,
            "embodied_displaced_vol":     interm.embodied_displaced_vol,
            "annual_embodied_impact_t":   interm.annual_embodied_impact,
            "total_embodied_impact_t":    interm.total_embodied_impact,
            "annual_lifecycle_impact_t":  interm.annual_lifecycle_impact,
            "total_lifecycle_impact_t":   interm.total_lifecycle_impact,
            "actual_to_forecast_ratio":   interm.actual_to_forecast_ratio,
            "impact_2021_actuals_t":      interm.impact_2021_actuals,
            "impact_2021_adjusted_t":     interm.impact_2021_adjusted,
            # Outputs (LM:LS)
            "company_tonnes_co2":         out.company_tonnes,
            "volo_ownership_pct":         co.portfolio.volo_pct,
            "volo_tonnes_prorata":        out.volo_tonnes_prorata,
            "volo_tonnes_risk_adjusted":  out.volo_tonnes_risk_adjusted,
            "volo_investment":             co.portfolio.volo_investment,
            "volo_tonnes_per_dollar":     out.volo_tonnes_per_dollar,
            "risk_adj_tonnes_per_dollar": out.risk_adj_tonnes_per_dollar,
        })
    return results


def aggregate_fund_totals(results: list[dict]) -> dict:
    """
    Aggregate portfolio-level fund statistics.
    Mirrors Fund 1 summary rows 198-199 (average tonnes/dollar metrics).
    """
    port = [r for r in results if r["stage"] == "Portfolio"]

    total_co2  = sum(r["company_tonnes_co2"]        for r in port)
    total_pro  = sum(r["volo_tonnes_prorata"]        for r in port)
    total_ra   = sum(r["volo_tonnes_risk_adjusted"]  for r in port)

    valid_tpd = [
        r["volo_tonnes_per_dollar"] for r in port
        if r["volo_tonnes_per_dollar"] and not math.isnan(r["volo_tonnes_per_dollar"])
    ]
    avg_tpd = sum(valid_tpd) / len(valid_tpd) if valid_tpd else float("nan")

    return {
        "total_portfolio_co2_tonnes":   total_co2,
        "total_volo_prorata_tonnes":    total_pro,
        "total_volo_risk_adj_tonnes":   total_ra,
        "avg_volo_tonnes_per_dollar":   avg_tpd,
        "n_companies_modelled":         len(port),
    }


# =============================================================================
# SECTION 8: MAIN / DEMO
# =============================================================================

if __name__ == "__main__":
    print("=" * 72)
    print("VoLo Earth RVM Carbon Impact Model  –  Fund 1  (cols HY:NR)")
    print("=" * 72)

    results = run_portfolio(PORTFOLIO_COMPANIES)

    HDR = (f"{'Company':<28} {'Launch':>6} {'10y CO₂ (t)':>16} "
           f"{'VoLo t (RA)':>14} {'t/$ (RA)':>12}")
    print(f"\n{HDR}")
    print("-" * len(HDR))
    for r in results:
        tpd = r["risk_adj_tonnes_per_dollar"]
        tpd_s = f"{tpd:>12.4f}" if tpd and not math.isnan(tpd) else f"{'N/A':>12}"
        print(
            f"{r['company']:<28} "
            f"{r['commercial_launch_yr']:>6} "
            f"{r['total_lifecycle_impact_t']:>16,.0f} "
            f"{r['volo_tonnes_risk_adjusted']:>14,.0f} "
            f"{tpd_s}"
        )

    totals = aggregate_fund_totals(results)
    print(f"\n{'FUND TOTALS':}")
    print(f"  Total portfolio CO₂ impact  : {totals['total_portfolio_co2_tonnes']:>18,.0f} t")
    print(f"  Total VoLo pro-rata          : {totals['total_volo_prorata_tonnes']:>18,.0f} t")
    print(f"  Total VoLo risk-adjusted     : {totals['total_volo_risk_adj_tonnes']:>18,.0f} t")
    print(f"  Avg risk-adj tonnes / dollar : {totals['avg_volo_tonnes_per_dollar']:>18.4f}")
    print(f"  Companies modelled           : {totals['n_companies_modelled']:>18}")

    # --- S-curve demo ---
    bdp = next(c for c in PORTFOLIO_COMPANIES if c.company_name == "BlueDot Photonics")
    if bdp.volume.s_curve_M:
        print(f"\n--- S-Curve Demo: {bdp.company_name} (IS/IT/IU = {bdp.volume.s_curve_M}/{bdp.volume.s_curve_K}/{bdp.volume.s_curve_x}) ---")
        for yr in [5, 7, 10]:
            share = compute_s_curve_share(
                bdp.volume.s_curve_M, bdp.volume.s_curve_K, bdp.volume.s_curve_x, yr
            )
            print(f"  Year {yr:2d}: {share:6.2%} market share  →  {share * bdp.volume.sam_10y:>10,.0f} MW")

    # --- Carbon intensity DB demo ---
    print("\n--- Global Electricity CI Time Series (30-year) ---")
    ci = CarbonIntensityDB.build_series("Global electricity", 30)
    for step, val in enumerate(ci):
        yr_label = 2011 + step
        print(f"  {yr_label}: {val:.4f} tCO₂/MWh", end="   ")
        if (step + 1) % 5 == 0:
            print()

    # --- HEVO embodied carbon result ---
    hevo = next(c for c in PORTFOLIO_COMPANIES if c.company_name == "HEVO")
    _, out = run_company(hevo)
    print(f"\n--- HEVO Embodied Carbon ---")
    print(f"  Company tonnes: {out.company_tonnes:>14,.0f} t")
