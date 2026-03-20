"""
TAM / SAM / SOM market sizing framework.

Provides both archetype-level defaults and calculable derivations:

  TAM (Total Addressable Market)
    The full revenue opportunity if 100% of the addressable market adopted.
    User-provided or pulled from archetype defaults.

  SAM (Serviceable Addressable Market)
    The portion of TAM the company can realistically serve given
    geography, customer segment, regulatory access, and technology readiness.
    Default: archetype-based percentage of TAM.

  SOM (Serviceable Obtainable Market)
    The portion of SAM the company can capture in its planning horizon
    given competition, go-to-market capacity, and sales cycle length.
    Ties directly to the penetration_share used in Monte Carlo.

Sources for defaults:
  - IEA World Energy Outlook 2025 (energy TAMs)
  - BloombergNEF, Wood Mackenzie (battery, EV, solar)
  - Grand View Research, Markets & Markets (software, industrial)
  - NREL ATB 2024 (technology-specific sizing)
"""

MARKET_SIZING_DEFAULTS = {
    "utility_solar": {
        "tam_m": 120_000,
        "tam_source": "Global utility solar market ~$120B by 2030 (BloombergNEF NEO 2024)",
        "sam_pct": 25,
        "sam_rationale": "US + EU accessible markets ~25% of global. Assumes no China/India direct sales.",
        "som_pct_of_sam": 3.0,
        "som_rationale": "Achievable market share for a differentiated component/service startup over 5-7yr horizon.",
        "geography_filter": "US + EU-accessible",
        "segment_notes": "Utility-scale projects >1MW. Excludes distributed generation.",
    },
    "commercial_solar": {
        "tam_m": 45_000,
        "tam_source": "Global C&I solar market ~$45B (Grand View Research 2024)",
        "sam_pct": 35,
        "sam_rationale": "Fragmented market — startups can serve US mid-market C&I (~35% of global).",
        "som_pct_of_sam": 5.0,
        "som_rationale": "Higher share achievable in niche verticals (e.g. ag-solar, warehouse rooftop).",
        "geography_filter": "North America",
        "segment_notes": "Commercial & Industrial rooftop and ground-mount.",
    },
    "residential_solar": {
        "tam_m": 35_000,
        "tam_source": "Global residential solar ~$35B (Wood Mackenzie 2024)",
        "sam_pct": 40,
        "sam_rationale": "US residential market well-developed. ~40% of global is US-addressable.",
        "som_pct_of_sam": 4.0,
        "som_rationale": "Consumer acquisition costs limit rapid scaling. 4% achievable with strong brand.",
        "geography_filter": "US residential",
        "segment_notes": "Rooftop PV + storage bundles.",
    },
    "onshore_wind": {
        "tam_m": 100_000,
        "tam_source": "Global onshore wind market ~$100B (IEA WEO 2025, GWEC)",
        "sam_pct": 20,
        "sam_rationale": "Dominated by large OEMs. Startup-addressable via components/software ~20%.",
        "som_pct_of_sam": 2.0,
        "som_rationale": "Component startups typically capture 1-3% of addressable supply chain.",
        "geography_filter": "US + Northern Europe",
        "segment_notes": "Components, software, and services (not full turbine OEM).",
    },
    "offshore_wind": {
        "tam_m": 60_000,
        "tam_source": "Global offshore wind market ~$60B projected (GWEC Market Intelligence 2024)",
        "sam_pct": 15,
        "sam_rationale": "US offshore wind nascent. EU more established. ~15% of global addressable.",
        "som_pct_of_sam": 2.0,
        "som_rationale": "Capital-intensive, project-based. Low market share ceiling for startups.",
        "geography_filter": "US East Coast + EU North Sea",
        "segment_notes": "Subsea, installation, and O&M technology layers.",
    },
    "geothermal": {
        "tam_m": 25_000,
        "tam_source": "Global geothermal market ~$25B addressable (Rystad Energy, DOE GeoVision)",
        "sam_pct": 30,
        "sam_rationale": "US has strongest EGS regulatory support and resource base. ~30% global.",
        "som_pct_of_sam": 5.0,
        "som_rationale": "Fewer competitors in next-gen EGS. Higher share achievable for technology leaders.",
        "geography_filter": "Western US, Iceland, East Africa",
        "segment_notes": "Enhanced Geothermal Systems (EGS) and next-gen approaches.",
    },
    "battery_storage_utility": {
        "tam_m": 80_000,
        "tam_source": "Global battery storage market ~$80B by 2030 (BloombergNEF, Navigant)",
        "sam_pct": 30,
        "sam_rationale": "US grid storage market ~30% of global. Strong IRA tailwinds.",
        "som_pct_of_sam": 4.0,
        "som_rationale": "Differentiated chemistry/software can capture 3-5% of US grid market.",
        "geography_filter": "US + Australia",
        "segment_notes": "Grid-scale storage and co-located solar+storage.",
    },
    "nuclear_smr": {
        "tam_m": 150_000,
        "tam_source": "Global nuclear market ~$150B (IAEA, NEA). SMR subset ~$30B by 2035.",
        "sam_pct": 10,
        "sam_rationale": "Very few markets have regulatory pathway for SMR. US + Canada + UK ~10%.",
        "som_pct_of_sam": 3.0,
        "som_rationale": "Winner-take-most dynamic. 3% achievable if design gains regulatory approval.",
        "geography_filter": "US, Canada, UK",
        "segment_notes": "Small modular reactors. Extremely long development cycles.",
    },
    "ev_electrification": {
        "tam_m": 500_000,
        "tam_source": "Global EV + charging infrastructure ~$500B by 2030 (BloombergNEF EVO 2024)",
        "sam_pct": 20,
        "sam_rationale": "US EV market ~20% of global. Charging + fleet + software layers.",
        "som_pct_of_sam": 2.0,
        "som_rationale": "Highly competitive. Software/infra plays can capture 2% of US market.",
        "geography_filter": "US",
        "segment_notes": "Charging networks, fleet software, and EV components (not OEM).",
    },
    "climate_software": {
        "tam_m": 30_000,
        "tam_source": "Climate/ESG/grid software ~$30B by 2028 (Markets & Markets, Gartner)",
        "sam_pct": 50,
        "sam_rationale": "SaaS sells globally from US base. ~50% of market accessible.",
        "som_pct_of_sam": 8.0,
        "som_rationale": "SaaS dynamics allow faster share capture. 5-10% achievable in verticals.",
        "geography_filter": "Global (SaaS)",
        "segment_notes": "Carbon accounting, grid management, ESG compliance, energy trading.",
    },
    "industrial_decarb": {
        "tam_m": 200_000,
        "tam_source": "Industrial decarbonization ~$200B (IEA Net Zero 2025, McKinsey)",
        "sam_pct": 15,
        "sam_rationale": "Process innovation targets specific subsectors. ~15% addressable by startups.",
        "som_pct_of_sam": 2.0,
        "som_rationale": "Slow adoption cycles in heavy industry. 1-3% share over 7-10yr horizon.",
        "geography_filter": "US + EU industrial base",
        "segment_notes": "Cement, steel, chemicals, heavy manufacturing process improvements.",
    },
    "ai_ml": {
        "tam_m": 300_000,
        "tam_source": "Global AI/ML market ~$300B by 2027 (IDC, Gartner, Grand View Research 2024)",
        "sam_pct": 30,
        "sam_rationale": "US + EU enterprise AI spend ~30% of global. Excludes hyperscaler internal R&D.",
        "som_pct_of_sam": 5.0,
        "som_rationale": "Vertical AI / applied ML startups can capture 3-7% of addressable segment within 5yr.",
        "geography_filter": "US + EU enterprise",
        "segment_notes": "Enterprise AI/ML platforms, vertical AI applications, inference infrastructure, AI-native SaaS.",
    },
    "custom": {
        "tam_m": 50_000,
        "tam_source": "User-defined — enter your own TAM for this novel technology.",
        "sam_pct": 25,
        "sam_rationale": "User-defined — estimate what portion of TAM is accessible given geography, segment, and regulatory constraints.",
        "som_pct_of_sam": 3.0,
        "som_rationale": "User-defined — estimate realistic market share in the planning horizon.",
        "geography_filter": "User-defined",
        "segment_notes": "Custom / novel technology archetype. All parameters are user-editable.",
    },
    "base_capital_intensive": {
        "tam_m": 100_000,
        "tam_source": "Base archetype — capital-intensive infrastructure. Adjust TAM to your specific market.",
        "sam_pct": 15,
        "sam_rationale": "Capital-intensive markets tend to have high regulatory and geographic barriers. ~15% addressable.",
        "som_pct_of_sam": 2.0,
        "som_rationale": "Slow sales cycles and project-based revenue limit early share capture.",
        "geography_filter": "Varies — typically regionally constrained",
        "segment_notes": "Infrastructure, heavy industry, large-scale energy, mining, chemicals. Long build cycles, high capex.",
    },
    "base_software": {
        "tam_m": 30_000,
        "tam_source": "Base archetype — pure software / SaaS. Adjust TAM to your specific vertical.",
        "sam_pct": 50,
        "sam_rationale": "Software sells globally from a single base. ~50% of TAM typically addressable.",
        "som_pct_of_sam": 8.0,
        "som_rationale": "SaaS dynamics allow faster share capture through product-led growth and low marginal cost.",
        "geography_filter": "Global (SaaS)",
        "segment_notes": "Enterprise or vertical SaaS, data platforms, analytics. Low capex, high margins.",
    },
    "base_sw_hw_hybrid": {
        "tam_m": 60_000,
        "tam_source": "Base archetype — software + hardware hybrid. Adjust TAM to your specific market.",
        "sam_pct": 25,
        "sam_rationale": "Hardware constraints limit geographic reach; software extends it. ~25% accessible.",
        "som_pct_of_sam": 4.0,
        "som_rationale": "Faster than pure hardware but slower than pure SaaS. 3-5% achievable in 5-7yr horizon.",
        "geography_filter": "Varies — hardware limits reach",
        "segment_notes": "IoT, robotics, smart grid, connected devices. Software recurring revenue on hardware base.",
    },
    "base_hard_tech": {
        "tam_m": 80_000,
        "tam_source": "Base archetype — hard tech / deep science. Adjust TAM to your specific domain.",
        "sam_pct": 15,
        "sam_rationale": "Deep science markets are often niche until proven at scale. ~15% addressable initially.",
        "som_pct_of_sam": 2.0,
        "som_rationale": "Very long R&D and regulatory timelines. Low initial share but potential for dominance.",
        "geography_filter": "Varies — often US/EU with regulatory pathway",
        "segment_notes": "Advanced materials, novel chemistry, fusion, biotech, quantum. Extremely long development cycles.",
    },
}


def get_market_sizing(archetype: str, tam_override: float = None,
                      sam_pct_override: float = None,
                      som_pct_override: float = None) -> dict:
    """
    Compute TAM → SAM → SOM for a given archetype.

    All overrides are optional. When provided, they replace the defaults.
    Returns the full calculation chain with provenance.
    """
    defaults = MARKET_SIZING_DEFAULTS.get(archetype, {})

    tam = tam_override if tam_override is not None else defaults.get("tam_m", 50000)
    sam_pct = sam_pct_override if sam_pct_override is not None else defaults.get("sam_pct", 25)
    som_pct = som_pct_override if som_pct_override is not None else defaults.get("som_pct_of_sam", 3.0)

    sam = round(tam * sam_pct / 100, 2)
    som = round(sam * som_pct / 100, 2)

    tam_is_default = tam_override is None
    sam_is_default = sam_pct_override is None
    som_is_default = som_pct_override is None

    return {
        "archetype": archetype,
        "tam_m": tam,
        "tam_source": defaults.get("tam_source", "User-provided") if tam_is_default else "User override",
        "tam_is_default": tam_is_default,
        "sam_pct": sam_pct,
        "sam_m": sam,
        "sam_rationale": defaults.get("sam_rationale", "") if sam_is_default else "User override",
        "sam_is_default": sam_is_default,
        "som_pct_of_sam": som_pct,
        "som_m": som,
        "som_rationale": defaults.get("som_rationale", "") if som_is_default else "User override",
        "som_is_default": som_is_default,
        "geography_filter": defaults.get("geography_filter", ""),
        "segment_notes": defaults.get("segment_notes", ""),
        "calculation_chain": {
            "tam": f"${tam:,.0f}M",
            "sam": f"${tam:,.0f}M × {sam_pct}% = ${sam:,.0f}M",
            "som": f"${sam:,.0f}M × {som_pct}% = ${som:,.0f}M",
        },
        "implied_penetration_range": {
            "low_pct": round(som * 0.3 / tam * 100, 4),
            "mid_pct": round(som / tam * 100, 4),
            "high_pct": round(som * 2.0 / tam * 100, 4),
            "note": "SOM-implied penetration range for Monte Carlo: 0.3×SOM to 2×SOM as fraction of TAM."
        },
    }


def get_all_defaults() -> dict:
    """Return default market sizing for all archetypes."""
    return {
        arch: get_market_sizing(arch)
        for arch in MARKET_SIZING_DEFAULTS
    }
