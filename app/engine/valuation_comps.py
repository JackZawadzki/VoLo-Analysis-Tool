"""
Public company EV/EBITDA valuation multiples from Damodaran / PubComps dataset.

Source: Aswath Damodaran, Stern NYU — https://pages.stern.nyu.edu/~adamodar/New_Home_Page/data.html
Data: Enterprise value multiples for US public companies by industry, updated Jan 2026.

Used in VoLo engine for:
  - IPO exit valuation multiples (use positive-EBITDA-firm averages)
  - Acquisition exit multiples (apply empirical haircut to IPO comps)

Acquisition discount rationale:
  Koeplin, Sarin & Shapiro (2000) "The Private Company Discount" found
  private company acquisition multiples averaged ~20-30% below public comps.
  Officer (2007) "The price of corporate liquidity" found an average discount
  of ~15-30% in acquisition transactions. We use 20% as a conservative central
  estimate, consistent with these findings and common industry practice.
"""

from pathlib import Path
from typing import Optional

ACQUISITION_DISCOUNT = 0.20

RELEVANT_INDUSTRIES = {
    "green_renewable_energy": {
        "key": "Green & Renewable Energy",
        "volo_label": "Green & Renewable Energy",
        "archetypes": ["utility_solar", "commercial_solar", "residential_solar",
                       "onshore_wind", "offshore_wind", "battery_storage_utility", "geothermal"],
    },
    "power": {
        "key": "Power",
        "volo_label": "Power / Utilities",
        "archetypes": ["utility_solar", "onshore_wind", "offshore_wind", "nuclear_smr", "geothermal"],
    },
    "software_sys_app": {
        "key": "Software (System & Application)",
        "volo_label": "Software (SaaS / System)",
        "archetypes": ["climate_software"],
    },
    "software_internet": {
        "key": "Software (Internet)",
        "volo_label": "Software (Internet / Platform)",
        "archetypes": ["climate_software"],
    },
    "steel": {
        "key": "Steel",
        "volo_label": "Steel",
        "archetypes": ["industrial_decarb"],
    },
    "metals_mining": {
        "key": "Metals & Mining",
        "volo_label": "Metals & Mining",
        "archetypes": ["industrial_decarb", "geothermal"],
    },
    "precious_metals": {
        "key": "Precious Metals",
        "volo_label": "Precious Metals",
        "archetypes": [],
    },
    "transportation": {
        "key": "Transportation",
        "volo_label": "Transportation",
        "archetypes": ["ev_electrification"],
    },
    "auto_truck": {
        "key": "Auto & Truck",
        "volo_label": "Auto & Truck",
        "archetypes": ["ev_electrification"],
    },
    "electrical_equipment": {
        "key": "Electrical Equipment",
        "volo_label": "Electrical Equipment",
        "archetypes": ["battery_storage_utility", "ev_electrification"],
    },
    "engineering_construction": {
        "key": "Engineering/Construction",
        "volo_label": "Engineering & Construction",
        "archetypes": ["geothermal", "nuclear_smr", "industrial_decarb"],
    },
    "environmental_waste": {
        "key": "Environmental & Waste Services",
        "volo_label": "Environmental & Waste Services",
        "archetypes": ["industrial_decarb"],
    },
    "chemical_specialty": {
        "key": "Chemical (Specialty)",
        "volo_label": "Chemical (Specialty)",
        "archetypes": ["industrial_decarb"],
    },
    "oilfield_svcs": {
        "key": "Oilfield Svcs/Equip.",
        "volo_label": "Oilfield Services & Equipment",
        "archetypes": ["geothermal"],
    },
    "oil_gas_production": {
        "key": "Oil/Gas (Production and Exploration)",
        "volo_label": "Oil & Gas (E&P)",
        "archetypes": ["geothermal"],
    },
    "machinery": {
        "key": "Machinery",
        "volo_label": "Machinery",
        "archetypes": ["industrial_decarb"],
    },
    "coal_related": {
        "key": "Coal & Related Energy",
        "volo_label": "Coal & Related Energy",
        "archetypes": [],
    },
    "total_market": {
        "key": "Total Market (without financials)",
        "volo_label": "Total Market (ex-Financials)",
        "archetypes": [],
    },
}

ARCHETYPE_TO_COMPS = {
    "utility_solar": ["green_renewable_energy", "power", "electrical_equipment"],
    "commercial_solar": ["green_renewable_energy", "power"],
    "residential_solar": ["green_renewable_energy", "electrical_equipment"],
    "onshore_wind": ["green_renewable_energy", "power"],
    "offshore_wind": ["green_renewable_energy", "power", "engineering_construction"],
    "geothermal": ["green_renewable_energy", "oilfield_svcs", "oil_gas_production", "engineering_construction", "metals_mining"],
    "battery_storage_utility": ["green_renewable_energy", "electrical_equipment"],
    "nuclear_smr": ["power", "engineering_construction"],
    "ev_electrification": ["auto_truck", "transportation", "electrical_equipment"],
    "climate_software": ["software_sys_app", "software_internet"],
    "industrial_decarb": ["chemical_specialty", "environmental_waste", "machinery", "steel", "metals_mining"],
    "ai_ml": ["software_sys_app", "software_internet"],
    "custom": ["total_market"],
    "base_capital_intensive": ["engineering_construction", "power", "oilfield_svcs", "metals_mining"],
    "base_software": ["software_sys_app", "software_internet"],
    "base_sw_hw_hybrid": ["electrical_equipment", "software_sys_app"],
    "base_hard_tech": ["chemical_specialty", "engineering_construction", "metals_mining"],
}


def load_vebitda(filepath: Optional[str] = None) -> dict:
    """
    Parse the VEBITDA PubComps .xls file into structured dict.
    Returns all industries and the curated relevant subset.
    """
    if filepath is None:
        filepath = str(
            Path(__file__).resolve().parent.parent.parent
            / "data" / "sources" / "VEBITDA - PubComps.xls"
        )

    p = Path(filepath)
    if not p.exists():
        return {"error": f"File not found: {filepath}", "all_industries": {}, "relevant": {}}

    try:
        import xlrd
    except ImportError:
        return {"error": "xlrd not installed", "all_industries": {}, "relevant": {}}

    wb = xlrd.open_workbook(filepath)
    ws = wb.sheet_by_name("Industry Averages")

    def safe_float(v):
        return round(float(v), 2) if isinstance(v, (int, float)) and v else None

    all_industries = {}
    for r in range(9, ws.nrows):
        name = ws.cell_value(r, 0)
        if not name or name == "Industry Name":
            continue

        n_firms = ws.cell_value(r, 1)
        ev_ebitdard_pos = ws.cell_value(r, 2)
        ev_ebitda_pos = ws.cell_value(r, 3)
        ev_ebit_pos = ws.cell_value(r, 4)
        ev_ebit_1t_pos = ws.cell_value(r, 5)
        ev_ebitdard_all = ws.cell_value(r, 6)
        ev_ebitda_all = ws.cell_value(r, 7)
        ev_ebit_all = ws.cell_value(r, 8)
        ev_ebit_1t_all = ws.cell_value(r, 9)

        all_industries[name] = {
            "n_firms": int(n_firms) if isinstance(n_firms, (int, float)) and n_firms else 0,
            "positive_ebitda": {
                "ev_ebitdard": safe_float(ev_ebitdard_pos),
                "ev_ebitda": safe_float(ev_ebitda_pos),
                "ev_ebit": safe_float(ev_ebit_pos),
                "ev_ebit_after_tax": safe_float(ev_ebit_1t_pos),
            },
            "all_firms": {
                "ev_ebitdard": safe_float(ev_ebitdard_all),
                "ev_ebitda": safe_float(ev_ebitda_all),
                "ev_ebit": safe_float(ev_ebit_all),
                "ev_ebit_after_tax": safe_float(ev_ebit_1t_all),
            },
        }

    relevant = {}
    for slug, info in RELEVANT_INDUSTRIES.items():
        industry_name = info["key"]
        if industry_name in all_industries:
            ind = all_industries[industry_name]
            ipo_mult = ind["positive_ebitda"]["ev_ebitda"]
            acq_mult = round(ipo_mult * (1 - ACQUISITION_DISCOUNT), 2) if ipo_mult else None
            relevant[slug] = {
                "label": info["volo_label"],
                "industry_name": industry_name,
                "n_firms": ind["n_firms"],
                "ipo_ev_ebitda": ipo_mult,
                "acq_ev_ebitda": acq_mult,
                "ipo_ev_ebit": ind["positive_ebitda"]["ev_ebit"],
                "all_firms_ev_ebitda": ind["all_firms"]["ev_ebitda"],
                "archetypes": info["archetypes"],
            }

    return {
        "source": "Aswath Damodaran, Stern NYU — EV Multiples, Jan 2026",
        "acquisition_discount": ACQUISITION_DISCOUNT,
        "acquisition_discount_citation": (
            "Koeplin, Sarin & Shapiro (2000) 'The Private Company Discount', "
            "Journal of Applied Corporate Finance. Officer (2007) 'The price of "
            "corporate liquidity', Journal of Financial Economics. "
            "Average discount range: 15-30%; we use 20% as a conservative central estimate."
        ),
        "n_total_industries": len(all_industries),
        "relevant": relevant,
        "all_industries": all_industries,
    }


# ── Simulator archetype → Damodaran archetype bridge ──────────────────────
# The VCSimulator uses 6 generic archetypes (software.saas, hardware.infra, …)
# while the Damodaran comps use VoLo-specific archetypes (climate_software,
# geothermal, …).  This mapping provides a reasonable bridge so the Monte Carlo
# can pull real public-comp EV/EBITDA multiples for each simulated company.

SIMULATOR_ARCHETYPE_TO_DAMODARAN = {
    # Software archetypes → climate/AI software comps
    "software.networked": "climate_software",   # acq ~21.9x
    "software.saas":      "climate_software",   # acq ~21.9x
    "software.market":    "climate_software",   # acq ~21.9x
    # Hardware archetypes → energy infrastructure comps
    "hardware.infra":     "base_capital_intensive",  # acq ~9.9x (power, E&C, oilfield, mining)
    "hardware.consumer":  "ev_electrification",      # acq ~22.6x (auto, transport, elec equip)
    "hardware.modular":   "industrial_decarb",       # acq ~10.9x (chem, enviro, machinery, steel)
}


def get_simulator_ev_ebitda_multiples(comps_data: dict) -> dict:
    """
    Return {simulator_archetype: acq_ev_ebitda_mean} for all 6 simulator
    archetypes, using Damodaran public-comp acquisition multiples.

    Falls back to strategy.json defaults if comps data is unavailable.
    """
    result = {}
    for sim_arch, dam_arch in SIMULATOR_ARCHETYPE_TO_DAMODARAN.items():
        comp = get_comps_for_archetype(comps_data, dam_arch)
        mult = comp.get("acq_ev_ebitda_mean")
        if mult is None:
            # Fallback: 16x software, 11x hardware
            mult = 16.0 if sim_arch.startswith("software") else 11.0
        result[sim_arch] = mult
    return result


def get_comps_for_archetype(comps_data: dict, archetype: str) -> dict:
    """
    Given loaded comps data and an archetype, return the matching industries
    with IPO and acquisition multiples.
    """
    relevant = comps_data.get("relevant", {})
    archetype_slugs = ARCHETYPE_TO_COMPS.get(archetype, [])

    matches = []
    ipo_multiples = []
    acq_multiples = []

    for slug in archetype_slugs:
        if slug in relevant:
            comp = relevant[slug]
            matches.append(comp)
            if comp["ipo_ev_ebitda"]:
                ipo_multiples.append(comp["ipo_ev_ebitda"])
            if comp["acq_ev_ebitda"]:
                acq_multiples.append(comp["acq_ev_ebitda"])

    ipo_mean = round(sum(ipo_multiples) / len(ipo_multiples), 2) if ipo_multiples else None
    acq_mean = round(sum(acq_multiples) / len(acq_multiples), 2) if acq_multiples else None
    ipo_range = [round(min(ipo_multiples), 2), round(max(ipo_multiples), 2)] if ipo_multiples else None
    acq_range = [round(min(acq_multiples), 2), round(max(acq_multiples), 2)] if acq_multiples else None

    return {
        "archetype": archetype,
        "n_comps": len(matches),
        "matches": matches,
        "industries": matches,
        "ipo_ev_ebitda_mean": ipo_mean,
        "ipo_ev_ebitda_range": ipo_range,
        "acq_ev_ebitda_mean": acq_mean,
        "acq_ev_ebitda_range": acq_range,
        "suggested_exit_multiple_range": acq_range,
    }
