/* ==========================================================
   VoLo Earth Ventures — Underwriting Engine
   Frontend application logic
   ========================================================== */


/* ═══════════════════════════════════════════════════════════
   INFO TOOLTIP SYSTEM — Glossary + Helper
   ═══════════════════════════════════════════════════════════ */

const _GLOSSARY = {
    // ── Deal Terms ─────────────────────────────────────────
    entry_stage: "The venture funding round at which VoLo is investing (e.g. Seed, Series A). Determines stage-specific survival probabilities and dilution assumptions in the simulation.",
    trl: "Technology Readiness Level (1-9 scale). Measures how mature the technology is, from basic research (TRL 1-3) through validation (4-6) to market-ready (7-9). Drives execution risk adjustments and revenue lag assumptions.",
    check_size: "The dollar amount VoLo is investing in this round. Used to calculate entry ownership and fund-level portfolio concentration.",
    round_size: "Total capital raised in this funding round from all investors. Check Size ÷ Round Size = VoLo's share of the round.",
    pre_money: "Company valuation before the new investment. Entry Ownership = Check Size ÷ (Pre-Money + Round Size).",
    post_money: "Company valuation after the investment. Post-Money = Pre-Money + Round Size. This is the baseline for calculating ownership dilution through future rounds.",
    entry_ownership: "VoLo's equity stake at entry, calculated as Check Size ÷ Post-Money Valuation. This gets diluted through subsequent funding rounds based on the dilution model.",

    // ── Market & Simulation ────────────────────────────────
    archetype: "Technology category that determines the Bass diffusion S-curve parameters (innovation rate p, imitation rate q), typical maturity timeline, and comparable company valuation multiples.",
    tam: "Total Addressable Market — the maximum annual revenue opportunity if the technology captured 100% of its target market. The S-curve models adoption as a fraction of TAM over time.",
    penetration_low: "Lower bound of the uniform distribution for market penetration share. Combined with TAM, determines the bear-case revenue potential. Typical range: 0.1% - 5%.",
    penetration_high: "Upper bound of the uniform distribution for market penetration share. Combined with TAM, determines the bull-case revenue potential. Typical range: 1% - 10%.",
    ev_ebitda_low: "Lower bound for the EV/EBITDA exit multiple range. If left blank, derived from public comparables (Damodaran/NYU sector data) with a 20% private-company haircut.",
    ev_ebitda_high: "Upper bound for the EV/EBITDA exit multiple range. The simulation draws uniformly from [low, high] to determine exit enterprise value for each Monte Carlo path.",
    sector_profile: "Industry classification that influences default assumptions for capital intensity, margin structure, and comparable company selection.",

    // ── Fund Parameters ────────────────────────────────────
    fund_size: "Total committed capital in the fund ($M). Used to calculate investable capital (after management fees and reserves), average check size, and concentration limits.",
    n_deals: "Target number of portfolio companies. Investable Capital ÷ Target Deals = average check size per deal.",
    mgmt_fee: "Annual management fee as a percentage of committed capital. Reduces investable capital available for deals. Typical range: 1.5% - 2.5%.",
    reserve_pct: "Percentage of investable capital held back for follow-on investments in existing portfolio companies. Higher reserves allow pro-rata participation in future rounds.",
    max_concentration: "Maximum percentage of investable capital that can go into a single deal. Prevents over-concentration risk. Typical range: 10% - 20%.",

    // ── Carbon Model ───────────────────────────────────────
    displaced_resource: "The carbon-intensive resource or energy source that this technology replaces (e.g., natural gas, diesel, grid electricity). Determines the baseline carbon intensity used in avoided emissions calculations.",
    volume_projections: "Projected number of units deployed per year (Y1-Y10). Combined with the carbon displacement chain to calculate annual avoided CO₂ emissions. Can be auto-filled from the financial model extraction.",
    baseline_production: "Lifetime production output per unit in the displaced resource's units. For example, annual MWh generation per MW of installed solar capacity.",
    range_improvement: "CI improvement factor: how many times lower the new technology's carbon intensity is vs. conventional. E.g. 1.4 → new tech has 1/1.4 ≈ 71% of conventional CI, so 28.6% is displaced. Use 1000 for near-zero-CI technologies (solar, wind, nuclear).",
    service_life: "Expected operating lifetime per unit in years. Determines cumulative avoided emissions over the full deployment horizon.",

    // ── Hero Metrics (Report) ──────────────────────────────
    expected_moic: "Expected Multiple on Invested Capital — the probability-weighted average return across all Monte Carlo simulation paths, including total-loss outcomes. This is the unconditional mean: E[MOIC] = P(survive) × E[MOIC|survive] + P(loss) × 0.",
    p_gt_3x: "Probability of achieving a 3x or greater return on invested capital. Calculated as the fraction of Monte Carlo paths where MOIC ≥ 3.0. A key threshold for venture fund economics.",
    expected_irr: "Expected Internal Rate of Return — the discount rate that makes the NPV of all simulated cashflows equal to zero. Calculated using dollar-weighted aggregation across all paths (not an average of per-path IRRs).",
    survival_rate: "Probability the company survives to a liquidity event (MOIC > 0). Driven by TRL-adjusted stage graduation rates from the dilution model — each funding stage has a base survival probability modified by technology maturity.",

    // ── Report Sections ────────────────────────────────────
    deal_metrics: "Monte Carlo simulation results showing the probability distribution of investment returns. Includes unconditional MOIC (counting total losses) and conditional MOIC (given the company survives to exit).",
    moic_unconditional: "MOIC including all outcomes — survivors and failures. Total-loss paths (MOIC = 0) pull the average down. This is the realistic expected return.",
    moic_conditional: "MOIC only for paths where the company survives to exit. Higher than unconditional because failures are excluded. Useful for understanding upside potential conditional on survival.",
    adoption_scurve: "Bass diffusion model showing technology adoption as a percentage of TAM over time. The S-shaped curve captures slow initial uptake, rapid growth through the inflection point, and saturation. Parameters (p, q) are calibrated from NREL ATB historical deployment data.",
    revenue_cone: "Monte Carlo simulation fan chart showing the range of plausible revenue outcomes (P10 to P90). Each path varies Bass parameters, market penetration, and execution timing. The cone width reflects uncertainty — wider = more uncertain.",
    founder_projections: "Revenue numbers extracted from the company's financial model — what management projects will happen. Overlaid on the simulation cone to assess whether the plan sits within the model's plausible range.",
    sim_median: "The 50th percentile (P50) of the Monte Carlo revenue distribution for each year. Half of simulated paths are above, half below. More conservative than the mean, which gets pulled up by outlier outcomes.",
    divergence: "Percentage difference between founder projections and the simulation median. Positive = founders project higher than the model. Used to assess whether management expectations are optimistic, realistic, or conservative.",
    portfolio_impact: "Simulated effect of adding this deal to the fund portfolio. Compares fund-level return metrics (TVPI, DPI, IRR) with and without this investment to measure marginal contribution.",
    carbon_impact: "Estimated CO₂ emissions avoided annually by the technology's deployment. Calculated by multiplying deployed units × production per unit × carbon intensity of the displaced resource.",
    sensitivity: "One-at-a-time perturbation analysis showing how each input variable affects the expected MOIC and P(>3x). Larger spreads indicate higher sensitivity — these are the assumptions that matter most.",
    valuation_context: "Public comparable company multiples (EV/Revenue, EV/EBITDA) from Damodaran/NYU sector data. Provides market context for the exit multiples used in the simulation.",
    ev_at_exit: "Projected enterprise value at the time of exit, calculated as EBITDA × exit multiple for each Monte Carlo path. Determines the proceeds available for distribution to equity holders.",
    risk_assessment: "Qualitative and quantitative risk analysis including dilution modeling, stage-specific mortality rates, and key risk factors identified from the pitch deck extraction.",
    check_optimization: "Grid search over possible check sizes showing the trade-off between ownership, fund concentration, portfolio diversification, and expected returns. Identifies the optimal investment amount.",
    financial_traceability: "Source data from the uploaded financial model showing extracted metrics (revenue, EBITDA, etc.) with provenance — which Excel sheet and cell each value came from.",
    audit_trail: "Complete record of all model inputs, assumptions, and configuration parameters used to generate this report. Enables reproducibility and review.",

    // ── Probability Bar ────────────────────────────────────
    total_loss: "Probability of losing the entire investment (MOIC = 0). Driven by stage-specific survival rates adjusted for TRL. For early-stage deals, typically 40-70%.",
    gt_1x: "Probability of returning at least the invested capital. P(MOIC ≥ 1x). The complement of total loss plus partial loss outcomes.",
    gt_3x: "Probability of a 3x+ return. The primary threshold for venture fund economics — a 3x return on a single deal roughly covers a portfolio of losses.",
    gt_5x: "Probability of a 5x+ return. Strong outperformance threshold.",
    gt_10x: "Probability of a 10x+ return. Exceptional outcome — potential fund-returner for smaller funds.",
    gt_20x: "Probability of a 20x+ return. Rare, outlier outcome. At this level, a single deal can return the entire fund.",

    // ── Divergence Table ───────────────────────────────────
    founder_revenue: "Annual revenue projected by the company's management team, extracted from the uploaded financial model.",
    sim_p25: "25th percentile of simulated revenue — only 25% of Monte Carlo paths produce revenue below this level. Represents a bearish but not worst-case outcome.",
    sim_p75: "75th percentile of simulated revenue — 75% of paths are below this level. Represents a bullish but not best-case outcome.",
    in_band: "Whether the founder projection falls within the P25-P75 simulation band for that year. 'Yes' = plausible according to the model. 'No' = founder expectations deviate significantly from model expectations.",

    // ── Portfolio Impact Metrics ─────────────────────────
    tvpi_mean: "Total Value to Paid-In capital (mean). Measures total fund value (realized + unrealized) as a multiple of invested capital across all simulation paths.",
    tvpi_p50: "Median (50th percentile) TVPI across portfolio simulations. Half of simulated fund outcomes are above this value.",
    tvpi_p75: "75th percentile TVPI — represents an optimistic but achievable fund outcome.",
    tvpi_delta: "Change in fund TVPI when this deal is added to the portfolio. Positive = the deal improves fund-level returns.",
    irr_mean_portfolio: "Mean fund-level IRR across portfolio simulations. Accounts for timing of cash flows across all deals in the fund.",
    irr_p50_portfolio: "Median fund-level IRR. More robust than mean IRR because it's less sensitive to outlier outcomes.",
    base_fund: "Fund performance metrics without this deal — the baseline portfolio of committed deals plus simulated filler companies.",
    with_deal: "Fund performance metrics after adding this deal to the portfolio, displacing simulated filler capital.",
    marginal_lift: "The incremental improvement (or reduction) in fund metrics attributable to adding this specific deal.",

    // ── Carbon Impact Metrics ────────────────────────────
    total_lifecycle_tco2: "Total potential carbon impact over the 10-year analysis horizon, summing annual operating and embodied avoided emissions across all projected deployment cohorts. Calculated per PRIME ERP methodology (Project Frame Pre-Investment Considerations). Mitigation definitions align with GIIN IRIS+ metrics.",
    volo_prorata: "Fund pro-rata share of forecasted carbon impact — total lifecycle tCO₂ × VoLo's entry ownership percentage. Attributed per the GHG Protocol.",
    risk_adjusted_carbon: "P-50 risk-adjusted cumulative carbon impact. Pro-rata tCO₂ adjusted for survival probability using likelihood-of-success curves fitted to each company's technical and commercial risk (TRL modifiers applied to Carta benchmark graduation rates). Reflects expected avoided emissions accounting for the probability the company reaches a meaningful exit.",
    carbon_tpd: "TCPI — Tonnes CO₂ to Paid In Capital. Risk-adjusted tonnes of CO₂ avoided per dollar invested. VoLo's primary cross-deal carbon efficiency metric — higher = more climate impact per investment dollar. Calculated as P-50 risk-adjusted tCO₂ ÷ VoLo check size.",
    annual_carbon_chart: "Year-by-year breakdown of avoided CO₂ emissions from the technology deployment, based on unit volume projections and per-unit carbon displacement calculations.",

    // ── Deal Metrics Charts ──────────────────────────────
    moic_distribution_chart: "Histogram of MOIC outcomes across surviving Monte Carlo paths. Shows the shape of the return distribution — where the mass sits and how fat the right tail is.",
    outcome_breakdown_chart: "Pie/bar chart decomposing simulation outcomes by type: full exit, stage exit, partial recovery, small late exit, and total loss. Shows the probability of each outcome category.",
    irr_conditional: "Internal Rate of Return calculated only for paths where the company survives to exit. Excludes total-loss outcomes to show the return profile conditional on survival.",
    variance_drivers: "Decomposition of what drives variation in MOIC outcomes. Shows how much each uncertain input (market size, penetration, exit multiple, etc.) contributes to total return variance.",

    // ── Check Size Optimization Metrics ──────────────────
    fund_optimized_check: "The check size that maximizes the composite score balancing fund TVPI impact across P10, P50, and P90 percentiles. Found by sweeping $250K increments across the fund-constrained range.",
    implied_ownership: "VoLo's equity stake at the optimized check size, calculated as Check Size ÷ Post-Money Valuation.",
    fund_p50_impact: "Percentage change in the fund's median (P50) TVPI when this deal is added at the optimized check size. Positive = the deal improves the fund's expected performance.",
    pct_of_fund: "The optimized check size as a percentage of investable capital. Measures portfolio concentration — higher means more fund-level exposure to this single deal.",
    fund_tvpi_impact_chart: "Chart showing how fund TVPI changes (Δ%) at P10, P50, and P90 as check size increases. Reveals the point of diminishing returns and concentration risk.",
    composite_score_chart: "Composite optimization score at each check size, weighting P10/P50/P90 fund TVPI impacts. The peak of this curve identifies the optimal check size.",
    check_own_pct: "VoLo's ownership percentage at each candidate check size in the optimization grid.",
    fund_delta_p10: "Percentage change in fund P10 TVPI (downside protection) when the deal is added at this check size.",
    fund_delta_p50: "Percentage change in fund median TVPI when the deal is added at this check size.",
    fund_delta_p90: "Percentage change in fund P90 TVPI (upside) when the deal is added at this check size.",
    opt_emoic: "Expected MOIC for the deal at this check size, from the standalone deal simulation.",
    opt_ploss: "Probability of total loss at this check size — driven by stage survival rates, not check size dependent.",
    opt_pgt3x: "Probability of achieving 3x+ return at this check size.",
    opt_score: "Composite optimization score combining normalized fund TVPI impacts. Higher = better risk-adjusted fund contribution.",

    // ── EV at Exit Sub-sections ──────────────────────────
    ev_distribution: "Distribution of enterprise values at exit across successful Monte Carlo paths. Shows the range of plausible exit sizes — from P25 (modest) to P90 (strong) outcomes.",
    ev_buildup: "Mean-path decomposition showing how EV is constructed: exit revenue × EBITDA margin = exit EBITDA × EV/EBITDA multiple = enterprise value.",

    // ── DD Financial Analysis ──────────────────────────────
    dd_revenue_y1: "Projected revenue in the first commercial year ($M). For pre-revenue companies, this is revenue in the first year after commercial launch — use 'Time to Commercial Launch' to shift the start date.",
    dd_revenue_cagr: "Compound Annual Growth Rate of revenue over the projection period. The model applies a decaying CAGR: growth starts at this rate and converges toward the terminal growth rate by the final year, reflecting natural market saturation.",
    dd_proj_years: "Number of years to project the P&L forward from the first commercial year. Longer horizons capture more of the terminal value but introduce greater uncertainty. Typical range: 7-12 years.",
    dd_custom_rev: "Override the CAGR-based revenue trajectory with specific year-by-year revenue figures from customer interviews, bottoms-up analysis, or management projections. Leave blank to use the CAGR model.",
    dd_time_to_launch: "Years until the company begins generating meaningful commercial revenue. Accounts for product development, testing, regulatory certification, manufacturing ramp, and initial sales cycles. During this period, the company burns cash with near-zero revenue. This shifts the entire revenue model and S-curve forward in time.",
    dd_gm_start: "Gross margin in Year 1 of commercial operations. Early-stage companies often have lower margins due to small-scale production, supplier pricing, and yield inefficiencies.",
    dd_gm_end: "Target gross margin at maturity. As the company scales, unit economics improve through manufacturing learning curves, supply chain optimization, and volume discounts. The model linearly interpolates from start to end.",
    dd_rd_start: "R&D spending as a percentage of revenue at the start of the projection. Early-stage companies typically invest heavily in product development relative to revenue.",
    dd_rd_end: "Target R&D spend as a percentage of revenue at scale. Even at maturity, climate-tech companies maintain meaningful R&D for next-gen products. The model linearly converges from start to end.",
    dd_sm_start: "Sales & Marketing as a percentage of revenue in Year 1. High initial spend reflects customer acquisition costs, channel development, and market education for new technologies.",
    dd_sm_end: "Target S&M spend as a percentage of revenue at scale. Decreases as brand recognition, repeat customers, and channel efficiency improve.",
    dd_ga_start: "General & Administrative costs as a percentage of revenue in Year 1. Includes executive team, finance, legal, HR. High as % of revenue early because fixed costs are spread over low revenue.",
    dd_ga_end: "Target G&A as a percentage of revenue at scale. Fixed-cost leverage means G&A grows slower than revenue, driving operating leverage.",
    dd_da_pct: "Depreciation & Amortization as a percentage of revenue. Reflects the capital intensity of the business — hardware/manufacturing companies have higher D&A than software.",
    dd_exit_type: "Whether the exit valuation is based on a revenue multiple (EV/Revenue) or an EBITDA multiple (EV/EBITDA). Pre-revenue or early-revenue companies typically use revenue multiples; profitable companies use EBITDA.",
    dd_exit_mult: "The multiple applied to the exit-year metric (Revenue or EBITDA) to calculate Enterprise Value at exit. Derived from public comparables for the sector — check the Valuation Context section of the deal report.",
    dd_discount: "Discount rate (WACC) used for the DCF valuation. For early-stage ventures, this is typically 25-40% reflecting high execution risk, illiquidity, and small-company premiums. Decreases for later-stage, de-risked companies.",
    dd_terminal_g: "Perpetual growth rate applied after the projection period for the terminal value calculation. Should not exceed long-run GDP growth (2-4%). Higher rates dramatically inflate the terminal value.",
    dd_tax: "Effective corporate tax rate. Most early-stage companies have zero effective tax due to NOL carryforwards. As the company becomes profitable, taxes reduce NOPAT and free cash flow.",
    dd_capex: "Capital expenditure as a percentage of revenue. Hardware and manufacturing businesses need 8-15%; software companies need 2-5%. Drives the gap between EBITDA and free cash flow.",
    dd_nwc: "Net working capital requirement as a percentage of revenue. Represents cash tied up in inventory, receivables, and payables. Changes in NWC consume (or release) cash as the business grows.",
    dd_dilution_rounds: "Number of future equity funding rounds expected between now and exit. Each round dilutes existing investors. Typical: 2-3 rounds for Seed, 1-2 for Series A, 0-1 for Series B+.",
    dd_dilution_pct: "Dilution per future funding round as a percentage of post-money. New investors typically take 15-25% of the company in each round. Compounds multiplicatively across rounds.",

    // ── DD Fundraising Timeline ──────────────────────────────
    dd_fundraising_plan: "Model future fundraising rounds and potential bridge financing between rounds. Bridges add extra dilution (typically 5-12% via convertible notes) and extend the timeline, both of which reduce the investor's IRR.",
    dd_round_label: "Name of the planned fundraising round (e.g., Series A, Series B). Used for display in the dilution waterfall and return calculations.",
    dd_round_year: "Expected year (from time of investment) when this round closes. If a bridge is needed first, the actual timeline may extend by the bridge delay period.",
    dd_round_dilution: "Percentage of the company sold to new investors in this priced round. Typical ranges: Seed 15-25%, Series A 18-25%, Series B 15-20%, Series C+ 10-18%.",
    dd_needs_bridge: "Whether the company will need bridge financing before this round. Bridges are common when milestones take longer than expected — the company needs runway but hasn't hit metrics for the next priced round.",
    dd_bridge_dilution: "Additional dilution from the bridge note/SAFE. Convertible bridges typically add 5-12% dilution through discount rates, valuation caps, or warrant coverage. This dilution hits existing investors BEFORE the priced round.",
    dd_bridge_delay: "Extra time (in years) added to the fundraising timeline when a bridge is needed. Typically 0.3-1.0 years. This extends the total hold period, which directly reduces IRR even if MOIC is unchanged.",
    dd_dilution_waterfall: "Shows how your ownership percentage changes through each fundraising event (bridges and priced rounds). Entry ownership starts at check_size / post_money, then each event multiplicatively reduces it.",
};

/**
 * Generate an info-tip icon + popover HTML for a glossary term.
 * Usage in template literals:  `<label>TAM ($M) ${infoTip('tam')}</label>`
 * Or with custom text:        `${infoTip('custom_key', 'Custom explanation text')}`
 */
function infoTip(key, customText) {
    const text = customText || _GLOSSARY[key] || '';
    if (!text) return '';
    // Escape HTML in the tooltip text
    const escaped = text.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    return `<span class="info-tip" onclick="this.classList.toggle('active')" onmouseleave="this.classList.remove('active')"><i class="info-tip-icon">i</i><span class="info-tip-body">${escaped}</span></span>`;
}

// Make available globally for inline HTML onclick handlers
window.infoTip = infoTip;


// Tab switching — supports both nav-link and dropdown items
function switchTab(tab) {
    document.querySelectorAll('.nav-link').forEach(l => l.classList.remove('active'));
    document.querySelectorAll('.nav-dropdown-item').forEach(l => l.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));

    const el = document.getElementById(`tab-${tab}`);
    if (el) el.classList.add('active');

    // Highlight the correct nav link
    const link = document.querySelector(`.nav-link[data-tab="${tab}"]`) ||
                 document.querySelector(`.nav-dropdown-item[data-tab="${tab}"]`);
    if (link) {
        if (link.classList.contains('nav-dropdown-item')) {
            const parent = link.closest('.nav-dropdown');
            if (parent) parent.querySelector('.nav-dropdown-trigger').classList.add('active');
        }
        link.classList.add('active');
    }

    if (tab === 'pipeline') { wizLoadReports(); wizLoadResources(); }
}

document.querySelectorAll('.nav-link[data-tab]').forEach(link => {
    link.addEventListener('click', (e) => { e.preventDefault(); switchTab(link.dataset.tab); });
});
document.querySelectorAll('.nav-dropdown-item[data-tab]').forEach(link => {
    link.addEventListener('click', (e) => { e.preventDefault(); switchTab(link.dataset.tab); });
});

const VOLO = {
    green: 'rgba(91, 119, 68, 1)',
    greenLight: 'rgba(91, 119, 68, 0.15)',
    greenFaint: 'rgba(91, 119, 68, 0.07)',
    sage: 'rgba(139, 158, 107, 1)',
    blueSteel: 'rgba(157, 181, 196, 1)',
    blueSteelLight: 'rgba(157, 181, 196, 0.2)',
    blueSteelFaint: 'rgba(157, 181, 196, 0.08)',
    slate: 'rgba(44, 62, 80, 1)',
    red: 'rgba(220, 53, 69, 0.5)',
    redBorder: 'rgba(220, 53, 69, 0.8)',
    grid: 'rgba(225, 228, 232, 0.6)',
};

// Archetype-specific scenario defaults
const ARCHETYPE_DEFAULTS = {
    utility_solar: {
        pen_low: 0.1, pen_high: 1.0, mult_low: 4, mult_high: 10,
        exit_min: 5, exit_max: 10, tam: 120000,
        note: 'Mature market. Compressed multiples, moderate penetration achievable.',
    },
    commercial_solar: {
        pen_low: 0.2, pen_high: 2.0, mult_low: 5, mult_high: 12,
        exit_min: 5, exit_max: 9, tam: 45000,
        note: 'Fragmented C&I market. Higher penetration ceiling for niche plays.',
    },
    residential_solar: {
        pen_low: 0.3, pen_high: 3.0, mult_low: 5, mult_high: 14,
        exit_min: 4, exit_max: 8, tam: 35000,
        note: 'Consumer-facing. Higher multiples possible for platform/brand plays.',
    },
    onshore_wind: {
        pen_low: 0.1, pen_high: 0.8, mult_low: 4, mult_high: 10,
        exit_min: 6, exit_max: 11, tam: 100000,
        note: 'Mature market with large incumbents. Component/service startups more typical.',
    },
    offshore_wind: {
        pen_low: 0.05, pen_high: 0.5, mult_low: 5, mult_high: 12,
        exit_min: 7, exit_max: 12, tam: 60000,
        note: 'Pre-inflection. Capital intensive, longer time-to-revenue.',
    },
    geothermal: {
        pen_low: 0.1, pen_high: 1.5, mult_low: 6, mult_high: 15,
        exit_min: 7, exit_max: 13, tam: 25000,
        note: 'Nascent market. EGS / next-gen approaches have high upside but long timelines.',
    },
    battery_storage_utility: {
        pen_low: 0.2, pen_high: 2.0, mult_low: 5, mult_high: 15,
        exit_min: 5, exit_max: 10, tam: 80000,
        note: 'Rapid growth phase. Strong tailwinds from grid modernization and renewables.',
    },
    nuclear_smr: {
        pen_low: 0.02, pen_high: 0.3, mult_low: 6, mult_high: 18,
        exit_min: 8, exit_max: 15, tam: 150000,
        note: 'Very long development cycles. Regulatory risk. Enormous TAM if successful.',
    },
    ev_electrification: {
        pen_low: 0.3, pen_high: 3.0, mult_low: 6, mult_high: 18,
        exit_min: 4, exit_max: 9, tam: 500000,
        note: 'At inflection point. Massive TAM. Software/infra layers more venture-scale.',
    },
    climate_software: {
        pen_low: 0.5, pen_high: 5.0, mult_low: 8, mult_high: 20,
        exit_min: 4, exit_max: 8, tam: 30000,
        note: 'SaaS-like dynamics. Higher multiples, faster exits, lower capital intensity.',
    },
    industrial_decarb: {
        pen_low: 0.1, pen_high: 1.0, mult_low: 5, mult_high: 12,
        exit_min: 6, exit_max: 12, tam: 200000,
        note: 'Huge but slow-moving market. Process innovation timelines vary widely.',
    },
    ai_ml: {
        pen_low: 0.3, pen_high: 4.0, mult_low: 10, mult_high: 25,
        exit_min: 3, exit_max: 7, tam: 300000,
        note: 'At inflection. Extremely fast adoption, high multiples, winner-take-most dynamics in verticals.',
    },
    custom: {
        pen_low: 0.2, pen_high: 2.0, mult_low: 5, mult_high: 12,
        exit_min: 5, exit_max: 10, tam: 50000,
        note: 'Custom / novel technology. All parameters are user-defined — set Bass diffusion params below.',
    },
    base_capital_intensive: {
        pen_low: 0.05, pen_high: 0.5, mult_low: 5, mult_high: 12,
        exit_min: 7, exit_max: 14, tam: 100000,
        note: 'Base archetype: capital intensive. Slow adoption, long build cycles, project-based revenue. Think infrastructure, energy, mining.',
    },
    base_software: {
        pen_low: 0.5, pen_high: 5.0, mult_low: 8, mult_high: 20,
        exit_min: 4, exit_max: 8, tam: 30000,
        note: 'Base archetype: pure software. Fast adoption, high margins, network effects. SaaS / platform dynamics.',
    },
    base_sw_hw_hybrid: {
        pen_low: 0.2, pen_high: 2.0, mult_low: 6, mult_high: 15,
        exit_min: 5, exit_max: 10, tam: 60000,
        note: 'Base archetype: software + hardware hybrid. Moderate adoption speed. IoT, robotics, smart grid, connected devices.',
    },
    base_hard_tech: {
        pen_low: 0.05, pen_high: 0.8, mult_low: 5, mult_high: 14,
        exit_min: 7, exit_max: 13, tam: 80000,
        note: 'Base archetype: hard tech / deep science. Very slow adoption, long R&D cycles. Advanced materials, novel chemistry, biotech.',
    },
};

// ================================================================
//  S-CURVE ATLAS
// ================================================================

let atlasOverlayChart = null;
let atlasGridCharts = {};
let atlasData = null;

const ATLAS_COLORS = [
    { border: 'rgba(91, 119, 68, 1)',   bg: 'rgba(91, 119, 68, 0.12)' },
    { border: 'rgba(59, 130, 246, 1)',   bg: 'rgba(59, 130, 246, 0.12)' },
    { border: 'rgba(239, 68, 68, 1)',    bg: 'rgba(239, 68, 68, 0.12)' },
    { border: 'rgba(139, 92, 246, 1)',   bg: 'rgba(139, 92, 246, 0.12)' },
    { border: 'rgba(245, 158, 11, 1)',   bg: 'rgba(245, 158, 11, 0.12)' },
    { border: 'rgba(16, 185, 129, 1)',   bg: 'rgba(16, 185, 129, 0.12)' },
    { border: 'rgba(236, 72, 153, 1)',   bg: 'rgba(236, 72, 153, 0.12)' },
    { border: 'rgba(99, 102, 241, 1)',   bg: 'rgba(99, 102, 241, 0.12)' },
    { border: 'rgba(234, 179, 8, 1)',    bg: 'rgba(234, 179, 8, 0.12)' },
    { border: 'rgba(20, 184, 166, 1)',   bg: 'rgba(20, 184, 166, 0.12)' },
    { border: 'rgba(168, 85, 247, 1)',   bg: 'rgba(168, 85, 247, 0.12)' },
];

const MATURITY_CLASS = {
    nascent: 'nascent',
    pre_inflection: 'pre-inflection',
    inflection: 'inflection',
    early_growth: 'early-growth',
    growth: 'growth',
};

async function loadScurveAtlas() {
    const btn = document.getElementById('atlas-load-btn');
    btn.classList.add('loading');
    btn.textContent = 'Loading...';

    try {
        const res = await fetch('/api/scurve-atlas');
        if (!res.ok) throw new Error('Server error ' + res.status);
        atlasData = await res.json();
        renderAtlas();
    } catch (err) {
        console.error('Atlas load failed:', err);
        showToast('Failed to load S-curve data: ' + err.message, 'error');
    } finally {
        btn.classList.remove('loading');
        btn.textContent = 'Load S-Curves';
    }
}

function renderAtlas() {
    if (!atlasData) return;

    document.getElementById('atlas-placeholder').style.display = 'none';
    document.getElementById('atlas-content').style.display = 'block';

    const viewMode = document.getElementById('atlas-view').value;
    const filter = document.getElementById('atlas-filter').value;

    const archetypes = Object.entries(atlasData.archetypes)
        .filter(([_, v]) => filter === 'all' || v.maturity === filter);

    if (viewMode === 'overlay') {
        document.getElementById('atlas-overlay-section').style.display = 'block';
        document.getElementById('atlas-grid-section').style.display = 'none';
        renderAtlasOverlay(archetypes);
    } else {
        document.getElementById('atlas-overlay-section').style.display = 'none';
        document.getElementById('atlas-grid-section').style.display = 'block';
        renderAtlasGrid(archetypes);
    }

    renderAtlasParamsTable(archetypes);
}

function renderAtlasOverlay(archetypes) {
    const ctx = document.getElementById('chart-atlas-overlay').getContext('2d');
    if (atlasOverlayChart) atlasOverlayChart.destroy();

    const years = atlasData.years;
    const labels = years.map(y => `Year ${y}`);

    // Sort archetypes by terminal median value (highest first = top curve to bottom)
    const sorted = [...archetypes].sort((a, b) => {
        const aEnd = a[1].median[a[1].median.length - 1] || 0;
        const bEnd = b[1].median[b[1].median.length - 1] || 0;
        return bEnd - aEnd;
    });

    const datasets = [];

    sorted.forEach(([key, data], idx) => {
        const c = ATLAS_COLORS[idx % ATLAS_COLORS.length];

        datasets.push({
            label: data.label,
            data: data.median,
            borderColor: c.border,
            borderWidth: 2,
            fill: false,
            pointRadius: 0,
            pointHoverRadius: 6,
            pointHoverBackgroundColor: c.border,
            tension: 0.4,
        });

        datasets.push({
            label: `${data.label} band`,
            data: data.p75,
            borderColor: 'transparent',
            backgroundColor: c.bg,
            fill: '+1',
            pointRadius: 0,
        });
        datasets.push({
            label: `${data.label} band low`,
            data: data.p25,
            borderColor: 'transparent',
            backgroundColor: 'transparent',
            fill: false,
            pointRadius: 0,
        });
    });

    atlasOverlayChart = new Chart(ctx, {
        type: 'line',
        data: { labels, datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'nearest', intersect: false, axis: 'xy' },
            plugins: {
                legend: {
                    labels: {
                        filter: (item) => !item.text.includes('band'),
                        font: { size: 10 },
                        usePointStyle: true,
                        pointStyle: 'line',
                        padding: 14,
                    },
                    position: 'right',
                },
                tooltip: {
                    filter: (item) => !item.dataset.label.includes('band'),
                    displayColors: true,
                    callbacks: {
                        title: () => '',
                        label: (item) => item.dataset.label,
                    },
                    bodyFont: { size: 12, weight: '600' },
                },
            },
            scales: {
                x: {
                    grid: { display: false },
                    ticks: { font: { size: 10 }, color: '#8B949E' },
                    title: { display: true, text: 'Year from t=0', font: { size: 11, weight: '600' }, color: '#586069' },
                },
                y: {
                    grid: { color: VOLO.grid },
                    ticks: { font: { size: 10 }, color: '#8B949E', callback: v => v + '%' },
                    title: { display: true, text: 'Market Penetration (% of TAM)', font: { size: 11, weight: '600' }, color: '#586069' },
                    min: 0,
                    max: 100,
                },
            },
        },
    });
}

function renderAtlasGrid(archetypes) {
    const container = document.getElementById('atlas-grid');

    Object.values(atlasGridCharts).forEach(c => c.destroy());
    atlasGridCharts = {};

    container.innerHTML = archetypes.map(([key, data], idx) => `
        <div class="card chart-card">
            <div class="card-header">
                <h3>${data.label} <span class="maturity-badge ${MATURITY_CLASS[data.maturity] || ''}">${data.maturity.replace('_', ' ')}</span></h3>
            </div>
            <div class="card-body chart-container">
                <canvas id="chart-atlas-${key}"></canvas>
            </div>
            <div class="card-footer">
                p=${data.p_mean} · q=${data.q_mean} · inflection ~${data.inflection_year} · peak adoption year ${data.peak_adoption_year}
            </div>
        </div>
    `).join('');

    archetypes.forEach(([key, data], idx) => {
        const ctx = document.getElementById(`chart-atlas-${key}`).getContext('2d');
        const c = ATLAS_COLORS[idx % ATLAS_COLORS.length];
        const labels = atlasData.years.map(y => `Year ${y}`);

        atlasGridCharts[key] = new Chart(ctx, {
            type: 'line',
            data: {
                labels,
                datasets: [
                    {
                        label: 'P75',
                        data: data.p75,
                        borderColor: 'transparent',
                        backgroundColor: c.bg,
                        fill: '+1',
                        pointRadius: 0,
                    },
                    {
                        label: 'Median',
                        data: data.median,
                        borderColor: c.border,
                        borderWidth: 2.5,
                        fill: false,
                        pointRadius: 0,
                        tension: 0.4,
                    },
                    {
                        label: 'P25',
                        data: data.p25,
                        borderColor: 'transparent',
                        backgroundColor: c.bg,
                        fill: '-1',
                        pointRadius: 0,
                    },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        filter: (item) => item.dataset.label === 'Median',
                        callbacks: { label: (item) => `${item.parsed.y.toFixed(1)}%` },
                    },
                },
                scales: {
                    x: {
                        grid: { display: false },
                        ticks: { font: { size: 8 }, color: '#8B949E', maxTicksLimit: 6 },
                    },
                    y: {
                        grid: { color: VOLO.grid },
                        ticks: { font: { size: 8 }, color: '#8B949E', callback: v => v + '%' },
                        min: 0,
                        max: 100,
                    },
                },
            },
        });
    });
}

function renderAtlasParamsTable(archetypes) {
    const tbody = document.querySelector('#atlas-params-table tbody');
    tbody.innerHTML = archetypes.map(([key, data], idx) => {
        const c = ATLAS_COLORS[idx % ATLAS_COLORS.length];
        const qpRatio = (data.q_mean / data.p_mean).toFixed(0);
        const finalPen = data.median[data.median.length - 1];
        const matClass = MATURITY_CLASS[data.maturity] || '';
        return `
            <tr>
                <td>
                    <span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:${c.border};margin-right:8px;vertical-align:middle"></span>
                    ${data.label}
                </td>
                <td>${data.p_mean}</td>
                <td>${data.q_mean}</td>
                <td>${qpRatio}x</td>
                <td>Year ${data.peak_adoption_year}</td>
                <td>${data.inflection_year}</td>
                <td><span class="maturity-badge ${matClass}">${data.maturity.replace('_', ' ')}</span></td>
                <td>${finalPen.toFixed(1)}%</td>
            </tr>
        `;
    }).join('');
}

// Re-render atlas when controls change
document.getElementById('atlas-view').addEventListener('change', renderAtlas);
document.getElementById('atlas-filter').addEventListener('change', renderAtlas);


/* ==========================================================
   RVM INTEGRATION — Auth, Companies, Resources, Extraction
   ========================================================== */

let _rvmToken = localStorage.getItem('rvm_token') || '';
let _rvmUser = null;

function _rvmHeaders() {
    const h = {'Content-Type': 'application/json'};
    if (_rvmToken) h['Authorization'] = 'Bearer ' + _rvmToken;
    return h;
}

// ── Auth ─────────────────────────────────────────────────────────────────────

const _AUTH_TAB_IDS = ['login', 'register', 'verify', 'forgot', 'reset'];

function showAuthTab(tab) {
    const tabs = document.querySelector('.auth-tabs');
    // Hide the tab bar when on sub-flows (verify/forgot/reset) — those aren't
    // user-switchable and the Login/Register tabs would just confuse.
    if (tabs) tabs.style.display = (tab === 'login' || tab === 'register') ? '' : 'none';
    _AUTH_TAB_IDS.forEach(t => {
        const form = document.getElementById('auth-' + t + '-form');
        if (!form) return;
        form.classList.remove('auth-hidden');
        form.style.display = (t === tab) ? 'block' : 'none';
    });
    const loginTab = document.getElementById('auth-tab-login');
    const regTab = document.getElementById('auth-tab-register');
    if (loginTab) loginTab.className = 'auth-tab' + (tab === 'login' ? ' active' : '');
    if (regTab) regTab.className = 'auth-tab' + (tab === 'register' ? ' active' : '');
    const errEl = document.getElementById('auth-error');
    if (errEl) errEl.style.display = 'none';
}

// ── Auth popup dialogs ───────────────────────────────────────────────
// _authError and _authInfo both render a real centered popup modal
// with a single dismiss button. The older behavior of inline red
// text under the form was easy to miss — users reported clicking
// Register with an @gmail.com address and seeing "nothing happen"
// because the inline error was below the fold on their screen.
// We still update the legacy #auth-error div for accessibility /
// screen readers, but the modal is what the user actually sees.

function _showAuthPopup(msg, kind /* 'error' | 'info' */) {
    const accent = kind === 'error' ? '#b54700' : '#2d6a3f';
    // Remove any previous popup before creating a new one.
    const prev = document.getElementById('auth-popup-overlay');
    if (prev) prev.remove();
    const overlay = document.createElement('div');
    overlay.id = 'auth-popup-overlay';
    overlay.style.cssText = (
        'position:fixed;inset:0;background:rgba(15,25,18,0.55);' +
        'display:flex;align-items:center;justify-content:center;' +
        'z-index:10000;animation:fadeIn 120ms ease-out;'
    );
    const card = document.createElement('div');
    card.style.cssText = (
        'background:#fff;border-radius:10px;padding:24px 28px;max-width:420px;' +
        'box-shadow:0 8px 32px rgba(0,0,0,0.25);border-top:4px solid ' + accent + ';'
    );
    const title = document.createElement('div');
    title.style.cssText = 'font-size:0.82rem;font-weight:700;letter-spacing:0.06em;text-transform:uppercase;color:' + accent + ';margin-bottom:8px;';
    title.textContent = kind === 'error' ? 'Unable to continue' : 'Info';
    const body = document.createElement('div');
    body.style.cssText = 'font-size:0.95rem;line-height:1.5;color:#1a1a1a;margin-bottom:18px;';
    body.textContent = msg;
    const btn = document.createElement('button');
    btn.textContent = 'OK';
    btn.style.cssText = (
        'background:' + accent + ';color:#fff;border:none;border-radius:4px;' +
        'padding:8px 22px;font-size:0.9rem;font-weight:600;cursor:pointer;' +
        'float:right;'
    );
    btn.onclick = () => overlay.remove();
    card.appendChild(title);
    card.appendChild(body);
    card.appendChild(btn);
    overlay.appendChild(card);
    overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };
    document.body.appendChild(overlay);
    // Focus the dismiss button so Enter/Escape dismisses cleanly.
    try { btn.focus(); } catch (_) {}
    // Escape dismisses
    const esc = (e) => {
        if (e.key === 'Escape') { overlay.remove(); document.removeEventListener('keydown', esc); }
    };
    document.addEventListener('keydown', esc);
}

function _authError(msg) {
    // Show a blocking popup — this is the primary UX for auth errors now.
    _showAuthPopup(msg, 'error');
    // Keep the inline div up-to-date for accessibility tools / screen readers.
    const el = document.getElementById('auth-error');
    if (el) { el.textContent = msg; el.style.color = '#b54700'; el.style.display = ''; }
}

function _authInfo(msg) {
    // Non-blocking info popup (e.g. "code sent"). Auto-dismisses after a
    // few seconds to avoid stacking, but still shows as an actual dialog
    // so the user sees the confirmation clearly.
    _showAuthPopup(msg, 'info');
    const overlay = document.getElementById('auth-popup-overlay');
    setTimeout(() => { if (overlay && overlay.parentNode) overlay.remove(); }, 4500);
    const el = document.getElementById('auth-error');
    if (el) { el.textContent = msg; el.style.color = '#2d6a3f'; el.style.display = ''; }
}

async function doLogin() {
    const e = document.getElementById('login-email').value.trim();
    const p = document.getElementById('login-password').value;
    if (!e || !p) return _authError('Please enter your email and password');
    try {
        const r = await fetch('/api/auth/login', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({email:e, password:p})});
        const d = await r.json();
        if (!r.ok) {
            // Special-case: if the backend says "not verified yet", bounce
            // the user into the verify screen for this email automatically.
            const msg = d.detail || d.error || 'Login failed';
            if (r.status === 403 && /verified/i.test(msg)) {
                _pendingVerifyEmail = e;
                const lbl = document.getElementById('verify-email-label');
                if (lbl) lbl.textContent = e;
                showAuthTab('verify');
                return _authInfo('Account not verified — enter the code we emailed you.');
            }
            return _authError(msg);
        }
        _rvmToken = d.token;
        _rvmUser = d.user;
        localStorage.setItem('rvm_token', d.token);
        _onAuthSuccess();
    } catch(e) { _authError('Network error'); }
}

let _pendingVerifyEmail = '';

let _registerInFlight = false;
async function doRegister() {
    // Guard against double-submit — if the user somehow clicks twice (rapid
    // double-click, Enter key in a field, etc.) we silently ignore the
    // second click while the first is still running. Prevents a race where
    // a second submit could happen after the popup fires.
    if (_registerInFlight) return;
    _registerInFlight = true;
    const _btn = document.querySelector('#auth-register-form .auth-submit');
    if (_btn) { _btn.disabled = true; _btn.style.opacity = '0.6'; }
    const _release = () => {
        _registerInFlight = false;
        if (_btn) { _btn.disabled = false; _btn.style.opacity = ''; }
    };
    try {
    const u = document.getElementById('reg-username').value.trim();
    const e = document.getElementById('reg-email').value.trim().toLowerCase();
    const p = document.getElementById('reg-password').value;
    if (!u || !e || p.length < 8) { _authError('Fill all fields (password 8+ chars)'); return; }
    // Client-side domain check — backend enforces this too (defense in
    // depth) but we short-circuit here so there's zero network round-trip
    // and zero chance of a race where a code could be dispatched.
    if (!/^[^@\s]+@voloearth\.com$/i.test(e)) {
        _authError('You need a @voloearth.com email to sign up. This tool is restricted to VoLo Earth team members.');
        return;
    }
    // IMPORTANT: clear any leftover session before registering. Without
    // this, a user who registers while they already have a valid JWT in
    // localStorage (e.g. from a previous account) would skip the verify
    // screen — _checkAuth would happily hand them the old session.
    _rvmToken = '';
    _rvmUser = null;
    localStorage.removeItem('rvm_token');
    try {
        const r = await fetch('/api/auth/register', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({username:u, email:e, password:p})});
        const d = await r.json();
        if (!r.ok) return _authError(d.detail || d.error || 'Registration failed');
        // Backend returns {status:"verification_sent"|"verification_required",
        // email, message} — it does NOT log the user in until they prove
        // they own the email.
        const statusStr = String(d.status || '');
        if (statusStr.startsWith('verification') || !d.token) {
            _pendingVerifyEmail = d.email || e;
            const lbl = document.getElementById('verify-email-label');
            if (lbl) lbl.textContent = _pendingVerifyEmail;
            showAuthTab('verify');
            return _authInfo(d.message || 'Check your inbox for a 6-digit code.');
        }
        // Legacy path: if the server still issues a token directly, honor it.
        _rvmToken = d.token;
        _rvmUser = d.user;
        localStorage.setItem('rvm_token', d.token);
        _onAuthSuccess();
    } catch(err) { _authError('Network error'); }
    } finally {
        _release();
    }
}

async function doVerifyEmail() {
    const code = (document.getElementById('verify-code').value || '').trim();
    if (!code || code.length < 4) return _authError('Enter the 6-digit code from your email');
    const email = _pendingVerifyEmail
        || document.getElementById('verify-email-label')?.textContent?.trim();
    if (!email) return _authError('No email on file for verification. Register again.');
    try {
        const r = await fetch('/api/auth/verify-email', {
            method: 'POST', headers: {'Content-Type':'application/json'},
            body: JSON.stringify({email, code}),
        });
        const d = await r.json();
        if (!r.ok) return _authError(d.detail || d.error || 'Verification failed');
        _rvmToken = d.token;
        _rvmUser = d.user;
        localStorage.setItem('rvm_token', d.token);
        _pendingVerifyEmail = '';
        _onAuthSuccess();
    } catch(e) { _authError('Network error'); }
}

async function doResendCode() {
    const email = _pendingVerifyEmail
        || document.getElementById('verify-email-label')?.textContent?.trim();
    if (!email) return _authError('No email on file. Register again.');
    try {
        const r = await fetch('/api/auth/resend-code', {
            method: 'POST', headers: {'Content-Type':'application/json'},
            body: JSON.stringify({email}),
        });
        if (!r.ok) return _authError('Could not resend code. Try registering again.');
        _authInfo('A new code was sent to ' + email + '.');
    } catch(e) { _authError('Network error'); }
}

async function doForgotPassword() {
    const email = (document.getElementById('forgot-email').value || '').trim();
    if (!email) return _authError('Enter your email');
    try {
        const r = await fetch('/api/auth/forgot-password', {
            method: 'POST', headers: {'Content-Type':'application/json'},
            body: JSON.stringify({email}),
        });
        // Server always returns 200 with a generic message (no leak of whether
        // the email exists) — so show the same thing regardless.
        _authInfo('If an account exists for ' + email + ', a reset link was sent. Check your inbox.');
    } catch(e) { _authError('Network error'); }
}

async function doResetPassword() {
    const p1 = document.getElementById('reset-password').value;
    const p2 = document.getElementById('reset-password-confirm').value;
    if (!p1 || p1.length < 8) return _authError('Password must be at least 8 characters');
    if (p1 !== p2) return _authError('Passwords do not match');
    const params = new URLSearchParams(window.location.search);
    const token = params.get('reset_token');
    if (!token) return _authError('Reset link is missing its token. Request a new reset email.');
    try {
        const r = await fetch('/api/auth/reset-password', {
            method: 'POST', headers: {'Content-Type':'application/json'},
            body: JSON.stringify({token, new_password: p1}),
        });
        const d = await r.json();
        if (!r.ok) return _authError(d.detail || d.error || 'Reset failed — link may have expired.');
        _rvmToken = d.token;
        _rvmUser = d.user;
        localStorage.setItem('rvm_token', d.token);
        // Clean the reset_token out of the URL so a refresh doesn't replay it.
        try { history.replaceState({}, '', window.location.pathname); } catch(_) {}
        _onAuthSuccess();
    } catch(e) { _authError('Network error'); }
}

// On page load, if a ?reset_token=... is in the URL, show the reset form.
(function _handleResetTokenInUrl() {
    try {
        const params = new URLSearchParams(window.location.search);
        if (params.get('reset_token')) {
            // Defer until DOM is ready in case this file loads before the
            // auth overlay is rendered.
            const show = () => {
                document.getElementById('auth-overlay').style.display = 'flex';
                showAuthTab('reset');
            };
            if (document.readyState === 'loading') {
                document.addEventListener('DOMContentLoaded', show);
            } else {
                show();
            }
        }
    } catch(_) {}
})();

function doLogout() {
    _rvmToken = '';
    _rvmUser = null;
    localStorage.removeItem('rvm_token');
    document.getElementById('auth-overlay').style.display = 'flex';
    document.getElementById('user-badge').style.display = 'none';
    document.getElementById('logout-btn').style.display = 'none';
    const devLink = document.getElementById('nav-dev-link');
    if (devLink) devLink.style.display = 'none';
}

function _onAuthSuccess() {
    document.getElementById('auth-overlay').style.display = 'none';
    document.getElementById('user-badge').textContent = _rvmUser.username + (_rvmUser.role === 'admin' ? ' (admin)' : '');
    document.getElementById('user-badge').style.display = '';
    document.getElementById('logout-btn').style.display = '';
    // Show developer tab for all authenticated users
    _showDevTab();
    wizLoadReports();
    wizLoadResources();
    loadModelPreferences();
}

async function _checkAuth() {
    if (!_rvmToken) {
        document.getElementById('auth-overlay').style.display = 'flex';
        return;
    }
    try {
        const r = await fetch('/api/auth/me', {headers: {'Authorization': 'Bearer ' + _rvmToken}});
        if (r.ok) {
            _rvmUser = await r.json();
            _onAuthSuccess();
        } else {
            _rvmToken = '';
            localStorage.removeItem('rvm_token');
            document.getElementById('auth-overlay').style.display = 'flex';
        }
    } catch(e) {
        document.getElementById('auth-overlay').style.display = 'flex';
    }
}

_checkAuth();

/* ==========================================================
   DEAL PIPELINE — Wizard, Report Renderer, PDF Export
   ========================================================== */

let _wizStep = 1;
let _wizExtraction = null;
let _wizFmData = null;
let _wizReport = null;
let _wizReportId = null;
let _wizReportCharts = {};
let _wizSavedInputs = null;   // Persisted inputs from a loaded report

function wizGoStep(n) {
    _wizStep = n;
    for (let i = 1; i <= 4; i++) {
        const panel = document.getElementById(`wiz-step-${i}`);
        if (panel) panel.style.display = i === n ? '' : 'none';
    }
    document.querySelectorAll('.wizard-step').forEach(s => {
        const sn = parseInt(s.dataset.step);
        s.classList.toggle('active', sn === n);
        s.classList.toggle('completed', sn < n);
    });
    // Scroll the pipeline tab back to the top so the new step is visible
    const _pipelineTab = document.getElementById('tab-pipeline');
    if (_pipelineTab) _pipelineTab.scrollTop = 0;
    window.scrollTo({ top: 0, behavior: 'smooth' });
    if (n === 1) {
        // Clear saved inputs when starting a new deal
        _wizSavedInputs = null;
        _wizReportId = null;
        // Show loaded-data banner if FM or extraction data is already in memory
        _wizUpdateLoadedBanner();
    }
    if (n === 3) {
        _wizPopulateVolumes();
        // After volume inputs exist, re-apply ALL saved config (volumes,
        // carbon params, deal terms …) so nothing is stale / default.
        if (_wizSavedInputs) {
            wizRestoreConfig(_wizSavedInputs);
        }
    }
}

// Navigate backward to a step already visited, WITHOUT clearing any state.
// Called by clicking completed step indicators. Only allows going back (n < _wizStep).
function wizNavBack(n) {
    if (n >= _wizStep || n < 1) return; // can only go back, not forward
    _wizStep = n;
    for (let i = 1; i <= 4; i++) {
        const panel = document.getElementById(`wiz-step-${i}`);
        if (panel) panel.style.display = i === n ? '' : 'none';
    }
    document.querySelectorAll('.wizard-step').forEach(s => {
        const sn = parseInt(s.dataset.step);
        s.classList.toggle('active', sn === n);
        s.classList.toggle('completed', sn < n);
    });
    const _pipelineTab = document.getElementById('tab-pipeline');
    if (_pipelineTab) _pipelineTab.scrollTop = 0;
    window.scrollTo({ top: 0, behavior: 'smooth' });
    // Re-apply config if navigating back to step 3
    if (n === 3) {
        _wizPopulateVolumes();
        if (_wizSavedInputs) wizRestoreConfig(_wizSavedInputs);
    }
    // Show loaded-data banner if navigating back to step 1
    if (n === 1) _wizUpdateLoadedBanner();
}

// Show or hide the "data already in memory" banner on step 1
function _wizUpdateLoadedBanner() {
    const banner = document.getElementById('wiz-loaded-banner');
    if (!banner) return;
    const hasDeck = !!((_wizExtraction) && (_wizExtraction._source || _wizExtraction.name || _wizExtraction.revenue_projections));
    const hasFm   = !!(_wizFmData && _wizFmData.has_data);
    if (hasDeck || hasFm) {
        const parts = [];
        if (hasDeck) {
            const src = _wizExtraction._source || _wizExtraction.name || 'deck';
            parts.push(`Deck: ${src}`);
        }
        if (hasFm) {
            const fm = _wizFmData;
            const fmName = fm.model_summary?.file_name || fm.model_summary?.company_name || 'financial model';
            parts.push(`Model: ${fmName}`);
        }
        document.getElementById('wiz-loaded-banner-text').textContent = `Data already loaded — ${parts.join(' · ')}`;
        banner.style.display = 'flex';
    } else {
        banner.style.display = 'none';
    }
}

// Skip upload step when data is already in memory
function wizSkipUpload() {
    if (!_wizExtraction && !_wizFmData) { showToast('No data in memory — please upload files.'); return; }
    wizPopulateReview();
    wizGoStep(2);
}

function wizLoadResources() {
    if (!_rvmToken) return;
    fetch('/api/resources', {headers: _rvmHeaders()})
        .then(r => r.json())
        .then(data => {
            const sel = document.getElementById('wiz-displaced-resource');
            if (sel) sel.innerHTML = '<option value="">— None —</option>' + data.map(r => `<option value="${r.name}">${r.name}</option>`).join('');
        }).catch(() => {});
}

// ── Team Deal Library ─────────────────────────────────────────────────
// Shared library of every deal report, DDR, and IC memo across all VoLo
// analysts. Replaces the old per-user "Recent Reports" mini-bar.
let _libCompanies = [];   // raw library payload from /api/deal-pipeline/library
let _libQuery = "";       // current search filter

function wizLoadReports() {
    if (!_rvmToken) return;
    fetch('/api/deal-pipeline/library', { headers: _rvmHeaders() })
        .then(r => r.ok ? r.json() : { companies: [] })
        .then(data => {
            _libCompanies = (data && data.companies) || [];
            _wizRenderLibrary();
        })
        .catch(() => {
            _libCompanies = [];
            _wizRenderLibrary();
        });
}

function _libEscape(s) {
    return String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function _libFmtDate(iso) {
    if (!iso) return '';
    // Backend stores UTC timestamps via SQLite datetime('now'). Append 'Z' so
    // the browser parses it as UTC instead of local time (which would give
    // negative diffs for timestamps within the last few hours).
    const isoStr = String(iso).replace(' ', 'T');
    const d = new Date(/[zZ]|[+-]\d{2}:?\d{2}$/.test(isoStr) ? isoStr : isoStr + 'Z');
    if (isNaN(d.getTime())) return String(iso).split('T')[0] || String(iso);
    const diffMs = Date.now() - d.getTime();
    const diff = Math.floor(diffMs / (1000 * 60 * 60 * 24));
    if (diff <= 0) return 'today';
    if (diff === 1) return 'yesterday';
    if (diff < 7)   return `${diff}d ago`;
    if (diff < 30)  return `${Math.floor(diff/7)}w ago`;
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: diff > 365 ? 'numeric' : undefined });
}

function _libCollectAuthors(group) {
    const set = new Set();
    (group.deal_reports || []).forEach(d => set.add(d.owner_username));
    (group.memos        || []).forEach(m => set.add(m.owner_username));
    (group.ddrs         || []).forEach(d => set.add(d.generated_by));
    return [...set].filter(Boolean);
}

function _wizRenderLibrary() {
    const list = document.getElementById('wiz-reports-list');
    const summaryEl = document.getElementById('lib-summary');
    if (!list) return;

    const q = (_libQuery || '').trim().toLowerCase();
    const filtered = !q ? _libCompanies
        : _libCompanies.filter(g => (g.company_name || '').toLowerCase().includes(q));

    if (summaryEl) {
        const totalCompanies = _libCompanies.length;
        const totalArtifacts = _libCompanies.reduce((acc, g) =>
            acc + (g.deal_reports?.length || 0) + (g.ddrs?.length || 0) + (g.memos?.length || 0), 0);
        if (totalCompanies === 0) {
            summaryEl.textContent = '';
        } else if (q) {
            summaryEl.textContent = `${filtered.length} of ${totalCompanies} ${totalCompanies === 1 ? 'company' : 'companies'} match "${_libEscape(q)}"`;
        } else {
            summaryEl.textContent = `${totalCompanies} ${totalCompanies === 1 ? 'company' : 'companies'} · ${totalArtifacts} ${totalArtifacts === 1 ? 'artifact' : 'artifacts'}`;
        }
    }

    if (!_libCompanies.length) {
        list.innerHTML = `
            <div class="lib-empty">
                <div class="lib-empty-title">No deals analyzed yet</div>
                <div class="lib-empty-text">Once anyone on the team runs a deal report, DDR, or IC memo, it will appear here for everyone to browse.</div>
            </div>`;
        return;
    }
    if (!filtered.length) {
        list.innerHTML = `
            <div class="lib-empty">
                <div class="lib-empty-title">No matches</div>
                <div class="lib-empty-text">No companies in the library match your search.</div>
            </div>`;
        return;
    }

    list.innerHTML = filtered.map((g, idx) => {
        const nDeal = g.deal_reports?.length || 0;
        const nDdr  = g.ddrs?.length         || 0;
        const nMemo = g.memos?.length        || 0;
        const authors = _libCollectAuthors(g);
        const authorText = authors.length ? authors.join(', ') : 'unknown';

        const chips = [];
        if (nDeal) chips.push(`<span class="lib-chip lib-chip-deal"><span class="lib-chip-dot"></span>Deal Report${nDeal>1?` ×${nDeal}`:''}</span>`);
        if (nDdr)  chips.push(`<span class="lib-chip lib-chip-ddr"><span class="lib-chip-dot"></span>DDR${nDdr>1?` ×${nDdr}`:''}</span>`);
        if (nMemo) chips.push(`<span class="lib-chip lib-chip-memo"><span class="lib-chip-dot"></span>IC Memo${nMemo>1?` ×${nMemo}`:''}</span>`);
        if (!chips.length) chips.push(`<span class="lib-chip lib-chip-empty">No artifacts</span>`);

        return `
            <div class="lib-card" onclick="wizOpenFolder(${idx})" role="button" tabindex="0">
                <div class="lib-card-header">
                    <h4 class="lib-card-name">${_libEscape(g.company_name)}</h4>
                    <div class="lib-card-username">${_libEscape(authorText)}</div>
                </div>
                <div class="lib-chips">${chips.join('')}</div>
                <div class="lib-card-meta">
                    <span class="lib-card-date">${_libFmtDate(g.latest_at)}</span>
                    <span class="lib-card-arrow">&rsaquo;</span>
                </div>
            </div>`;
    }).join('');
}

// ── Folder modal ──────────────────────────────────────────────────────
function wizOpenFolder(idx) {
    const g = _libCompanies[idx];
    if (!g) return;
    const modal = document.getElementById('lib-folder-modal');
    const nameEl = document.getElementById('lib-modal-company');
    const metaEl = document.getElementById('lib-modal-meta');
    const bodyEl = document.getElementById('lib-modal-body');
    if (!modal || !nameEl || !bodyEl) return;

    nameEl.textContent = g.company_name;

    const authors = _libCollectAuthors(g);
    const totalArtifacts = (g.deal_reports?.length || 0) + (g.ddrs?.length || 0) + (g.memos?.length || 0);
    metaEl.innerHTML = `${totalArtifacts} ${totalArtifacts === 1 ? 'artifact' : 'artifacts'} · contributors: <strong>${_libEscape(authors.join(', ') || 'unknown')}</strong>`;

    const html = [];

    if (g.deal_reports?.length) {
        html.push('<div class="lib-art-group">');
        html.push('<div class="lib-art-grouptitle">Deal Reports</div>');
        html.push('<div class="lib-art-list">');
        g.deal_reports.forEach(d => {
            html.push(`
                <div class="lib-art-item">
                    <div class="lib-art-icon lib-art-deal" onclick="wizOpenFolderDeal(${d.id})">&#9974;</div>
                    <div class="lib-art-info" onclick="wizOpenFolderDeal(${d.id})">
                        <div class="lib-art-title">Deal Report${d.archetype ? ` · ${_libEscape(d.archetype)}` : ''}${d.entry_stage ? ` · ${_libEscape(d.entry_stage)}` : ''}</div>
                        <div class="lib-art-sub">By <strong>${_libEscape(d.owner_username)}</strong> · ${_libFmtDate(d.created_at)}</div>
                    </div>
                    <button class="lib-art-srcbtn" onclick="event.stopPropagation(); libShowSourceData(${d.id});" title="View raw inputs the model used">Source data</button>
                    <button class="lib-art-delbtn" onclick="event.stopPropagation(); libDeleteDealReport(${d.id});" title="Delete this deal report" aria-label="Delete">&#128465;</button>
                    <span class="lib-art-arrow" onclick="wizOpenFolderDeal(${d.id})">&rsaquo;</span>
                </div>`);
        });
        html.push('</div></div>');
    }

    if (g.ddrs?.length) {
        html.push('<div class="lib-art-group">');
        html.push('<div class="lib-art-grouptitle">Due Diligence Reports</div>');
        html.push('<div class="lib-art-list">');
        g.ddrs.forEach(d => {
            // Show KB for files under 1 MB and MB otherwise — "0.0 MB" for a
            // 50 KB PDF was misleading.
            const _bytes = d.file_size_bytes || 0;
            const sizeStr = (_bytes <= 0)
                ? '— size unknown'
                : (_bytes < 1024 * 1024
                    ? (_bytes / 1024).toFixed(0) + ' KB'
                    : (_bytes / (1024 * 1024)).toFixed(1) + ' MB');
            const fnAttr = JSON.stringify(d.filename || 'DDR_Report.pdf').replace(/"/g, '&quot;');
            html.push(`
                <div class="lib-art-item">
                    <div class="lib-art-icon lib-art-ddr" onclick="wizOpenFolderDdr(${d.id}, ${fnAttr})">&#128209;</div>
                    <div class="lib-art-info" onclick="wizOpenFolderDdr(${d.id}, ${fnAttr})">
                        <div class="lib-art-title">${_libEscape(d.filename || 'DDR Report')}</div>
                        <div class="lib-art-sub">By <strong>${_libEscape(d.generated_by)}</strong> · ${_libFmtDate(d.generated_at)} · ${sizeStr}</div>
                    </div>
                    <button class="lib-art-delbtn" onclick="event.stopPropagation(); libDeleteDdr(${d.id});" title="Delete this DDR" aria-label="Delete">&#128465;</button>
                    <span class="lib-art-arrow" onclick="wizOpenFolderDdr(${d.id}, ${fnAttr})">&darr;</span>
                </div>`);
        });
        html.push('</div></div>');
    }

    if (g.memos?.length) {
        html.push('<div class="lib-art-group">');
        html.push('<div class="lib-art-grouptitle">IC Memos</div>');
        html.push('<div class="lib-art-list">');
        g.memos.forEach(m => {
            html.push(`
                <div class="lib-art-item">
                    <div class="lib-art-icon lib-art-memo" onclick="wizOpenFolderMemo(${m.id})">&#128221;</div>
                    <div class="lib-art-info" onclick="wizOpenFolderMemo(${m.id})">
                        <div class="lib-art-title">Investment Memo${m.model_used ? ` · ${_libEscape(m.model_used)}` : ''}</div>
                        <div class="lib-art-sub">By <strong>${_libEscape(m.owner_username)}</strong> · ${_libFmtDate(m.created_at)}</div>
                    </div>
                    <button class="lib-art-delbtn" onclick="event.stopPropagation(); libDeleteMemo(${m.id});" title="Delete this memo" aria-label="Delete">&#128465;</button>
                    <span class="lib-art-arrow" onclick="wizOpenFolderMemo(${m.id})">&rsaquo;</span>
                </div>`);
        });
        html.push('</div></div>');
    }

    if (!html.length) {
        html.push('<div class="lib-empty"><div class="lib-empty-text">No artifacts yet for this company.</div></div>');
    }

    bodyEl.innerHTML = html.join('');
    modal.style.display = 'flex';
    document.body.style.overflow = 'hidden';

    document.addEventListener('keydown', _wizFolderEsc);

    // Load the team-shared analyst notes for this company.
    libNotesLoad(g.company_name);
}

function wizCloseFolder() {
    const modal = document.getElementById('lib-folder-modal');
    if (modal) modal.style.display = 'none';
    document.body.style.overflow = '';
    document.removeEventListener('keydown', _wizFolderEsc);
    // Reset notes state so reopening a different folder starts clean.
    _libNotes = { company: null, version: 0, exists: false, editing: false, lastEditedBy: '', lastEditedAt: '' };
}

// ─── Library: artifact deletion (deal report / DDR / memo / notes) ─────────
// All delete actions go through a single confirm dialog and refresh the
// library on success so the UI stays consistent.

async function _libDeleteWithConfirm({ url, method = 'DELETE', label, body = null, currentCompanyName = null }) {
    if (!confirm(`Permanently delete this ${label}?\n\nThis can't be undone.`)) return;
    try {
        const opts = { method, headers: _rvmHeaders() };
        if (body) {
            opts.headers = { ...opts.headers, 'Content-Type': 'application/json' };
            opts.body = JSON.stringify(body);
        }
        const r = await fetch(url, opts);
        if (!r.ok) {
            const err = await r.json().catch(() => ({ detail: r.statusText }));
            throw new Error(err.detail || ('HTTP ' + r.status));
        }
        showToast(label.charAt(0).toUpperCase() + label.slice(1) + ' deleted.');
        // Reload library + reopen the same folder if it still has artifacts.
        const stayOpenName = currentCompanyName || _libNotes.company;
        wizCloseFolder();
        await new Promise(res => setTimeout(res, 100));
        if (typeof wizLoadReports === 'function') {
            wizLoadReports();
            // After the library reload, try to re-open the same folder if it
            // still exists. Defer slightly to let the fetch complete.
            if (stayOpenName) {
                setTimeout(() => {
                    const idx = (window._libCompanies || []).findIndex(c =>
                        (c.company_name || '').trim().toLowerCase() === (stayOpenName || '').trim().toLowerCase()
                    );
                    if (idx >= 0) wizOpenFolder(idx);
                }, 600);
            }
        }
    } catch (e) {
        showToast('Delete failed: ' + e.message);
    }
}

function libDeleteDealReport(reportId) {
    _libDeleteWithConfirm({
        url: `/api/deal-pipeline/report/${reportId}`,
        label: 'deal report',
    });
}

function libDeleteDdr(ddrId) {
    _libDeleteWithConfirm({
        url: `/api/ddr/reports/${ddrId}`,
        label: 'DDR report',
    });
}

function libDeleteMemo(memoId) {
    _libDeleteWithConfirm({
        url: `/api/memo/history/${memoId}`,
        label: 'IC memo',
    });
}

async function libNotesReset() {
    if (!_libNotes.company) return;
    if (!confirm(`Reset analyst notes for ${_libNotes.company}?\n\nThe current notes will be cleared back to the default template. Past versions stay in History.`)) return;
    try {
        const r = await fetch(`/api/deal-pipeline/notes?company=${encodeURIComponent(_libNotes.company)}`, {
            method: 'DELETE',
            headers: _rvmHeaders(),
        });
        if (!r.ok) {
            const err = await r.json().catch(() => ({ detail: r.statusText }));
            throw new Error(err.detail || ('HTTP ' + r.status));
        }
        showToast('Notes reset to template.');
        await libNotesLoad(_libNotes.company);
    } catch (e) {
        showToast('Reset failed: ' + e.message);
    }
}

// ─── Source Data viewer (per deal report) ───────────────────────────────────
// Surfaces the raw inputs + extraction stored alongside a deal report so any
// team member can audit "what did the model actually consume?" without
// loading the whole report.

function _libSrcEscape(s) {
    return String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function _libSrcFmtVal(v) {
    if (v == null) return '<span class="lib-src-null">—</span>';
    if (typeof v === 'number') {
        // Render numbers with reasonable precision; preserve integers as-is.
        if (Number.isInteger(v)) return String(v);
        return v.toFixed(Math.abs(v) < 1 ? 4 : 2);
    }
    if (typeof v === 'boolean') return v ? 'true' : 'false';
    if (Array.isArray(v)) {
        if (!v.length) return '<span class="lib-src-null">—</span>';
        // Array of strings/numbers: comma-separated.
        if (v.every(x => x === null || typeof x !== 'object')) {
            return _libSrcEscape(v.map(x => x == null ? '—' : String(x)).join(', '));
        }
        // Array of objects: render each as a chip with a sensible label.
        // Common schemas: {name, role/title}, {name, ...}, {label, ...}.
        const items = v.map(o => {
            if (o == null) return '<li class="lib-src-listitem">—</li>';
            if (typeof o !== 'object') return `<li class="lib-src-listitem">${_libSrcEscape(o)}</li>`;
            const primary = o.name || o.label || o.title || o.text;
            const secondary = o.role || o.title || o.position;
            if (primary && secondary && primary !== secondary) {
                return `<li class="lib-src-listitem"><strong>${_libSrcEscape(primary)}</strong> · ${_libSrcEscape(secondary)}</li>`;
            }
            if (primary) return `<li class="lib-src-listitem">${_libSrcEscape(primary)}</li>`;
            // Fallback for un-named objects: tiny JSON blob.
            return `<li class="lib-src-listitem"><code class="lib-src-inline-json">${_libSrcEscape(JSON.stringify(o))}</code></li>`;
        }).join('');
        return `<ul class="lib-src-list">${items}</ul>`;
    }
    if (typeof v === 'object') return `<pre class="lib-src-pre">${_libSrcEscape(JSON.stringify(v, null, 2))}</pre>`;
    return _libSrcEscape(v);
}

function _libSrcKvTable(obj, emptyMsg) {
    if (!obj || !Object.keys(obj).length) {
        return `<div class="lib-src-empty">${_libSrcEscape(emptyMsg)}</div>`;
    }
    const rows = Object.entries(obj)
        .filter(([_, v]) => v !== null && v !== undefined && v !== '' &&
                            !(Array.isArray(v) && v.length === 0))
        .map(([k, v]) => `
            <tr>
                <th class="lib-src-key">${_libSrcEscape(k)}</th>
                <td class="lib-src-val">${_libSrcFmtVal(v)}</td>
            </tr>`).join('');
    if (!rows) return `<div class="lib-src-empty">${_libSrcEscape(emptyMsg)}</div>`;
    return `<table class="lib-src-table">${rows}</table>`;
}

function _libSrcFinancialsTable(financials, units, fiscalYears, scaleInfo) {
    const metrics = financials ? Object.keys(financials) : [];
    if (!metrics.length) {
        return '<div class="lib-src-empty">No financial model extracted for this deal.</div>';
    }
    // Collect the union of years across all metrics, then sort.
    const yearSet = new Set();
    metrics.forEach(m => {
        const vals = financials[m] || {};
        Object.keys(vals).forEach(y => yearSet.add(y));
    });
    let years = Array.from(yearSet).sort((a, b) => Number(a) - Number(b));
    if (fiscalYears && fiscalYears.length && years.length === 0) years = fiscalYears.map(String);
    const fmtCell = v => {
        if (v == null) return '<span class="lib-src-null">—</span>';
        if (typeof v === 'number') return v.toLocaleString(undefined, { maximumFractionDigits: 2 });
        return _libSrcEscape(v);
    };
    const head = `<tr><th class="lib-src-key">Metric</th><th class="lib-src-key">Unit</th>${years.map(y => `<th class="lib-src-yearcol">${_libSrcEscape(y)}</th>`).join('')}</tr>`;
    const body = metrics.map(m => {
        const vals = financials[m] || {};
        const u = (units && units[m]) || '';
        const cells = years.map(y => `<td class="lib-src-numcell">${fmtCell(vals[y])}</td>`).join('');
        return `<tr><th class="lib-src-key">${_libSrcEscape(m)}</th><td class="lib-src-unit">${_libSrcEscape(u)}</td>${cells}</tr>`;
    }).join('');
    const scale = scaleInfo ? `<div class="lib-src-scaleinfo">Scale: ${_libSrcEscape(scaleInfo)}</div>` : '';
    return scale + `<div class="lib-src-tablewrap"><table class="lib-src-table lib-src-fin-table">${head}${body}</table></div>`;
}

async function libShowSourceData(reportId) {
    const modal  = document.getElementById('lib-source-data-modal');
    const title  = document.getElementById('lib-srcdata-title');
    const meta   = document.getElementById('lib-srcdata-meta');
    const body   = document.getElementById('lib-srcdata-body');
    if (!modal || !title || !body) return;
    title.textContent = 'Loading...';
    meta.textContent = '';
    body.innerHTML = '<div class="lib-src-empty">Loading source data...</div>';
    modal.style.display = 'flex';
    document.body.style.overflow = 'hidden';
    document.addEventListener('keydown', _libSrcEsc);

    try {
        const r = await fetch(`/api/deal-pipeline/report/${reportId}/source-data`, { headers: _rvmHeaders() });
        if (!r.ok) {
            const err = await r.json().catch(() => ({ detail: r.statusText }));
            throw new Error(err.detail || 'HTTP ' + r.status);
        }
        const d = await r.json();
        title.textContent = d.company_name || 'Deal Report';
        meta.innerHTML = `Generated by <strong>${_libSrcEscape(d.owner_username || 'unknown')}</strong> · ${_libSrcEscape(_libNotesFmtDate(d.created_at))}`;

        const fm = d.financial_model || {};
        const sections = [];
        sections.push(`
            <div class="lib-src-section">
                <div class="lib-src-section-title">Deal Terms</div>
                ${_libSrcKvTable(d.deal_terms, 'No deal terms recorded.')}
            </div>`);
        sections.push(`
            <div class="lib-src-section">
                <div class="lib-src-section-title">Simulation Parameters</div>
                ${_libSrcKvTable(d.simulation_params, 'No simulation parameters recorded.')}
            </div>`);
        sections.push(`
            <div class="lib-src-section">
                <div class="lib-src-section-title">Financial Model${fm.file_name ? ` <span class="lib-src-fileinfo">· ${_libSrcEscape(fm.file_name)}</span>` : ''}</div>
                ${_libSrcFinancialsTable(fm.financials, fm.units, fm.fiscal_years, fm.scale_info)}
            </div>`);
        sections.push(`
            <div class="lib-src-section">
                <div class="lib-src-section-title">Pitch Deck Extraction</div>
                ${_libSrcKvTable(d.deck_extraction, 'No pitch deck extraction recorded.')}
            </div>`);
        body.innerHTML = sections.join('');
    } catch (e) {
        title.textContent = 'Source Data';
        meta.textContent = '';
        body.innerHTML = `<div class="lib-src-empty">Failed to load: ${_libSrcEscape(e.message)}</div>`;
    }
}

function libCloseSourceData() {
    const modal = document.getElementById('lib-source-data-modal');
    if (modal) modal.style.display = 'none';
    document.body.style.overflow = 'hidden'; // folder modal still open underneath
    document.removeEventListener('keydown', _libSrcEsc);
}

function _libSrcEsc(e) { if (e.key === 'Escape') libCloseSourceData(); }

// ─── Team-shared analyst notes (per company) ────────────────────────────────
// Loaded by wizOpenFolder(); edited inline in the folder modal. Light
// optimistic-concurrency: every save sends the version we loaded; if the
// server has a newer version we show the colleague's text and let the user
// merge before retrying.
let _libNotes = { company: null, version: 0, exists: false, editing: false, lastEditedBy: '', lastEditedAt: '' };

function _libNotesEscape(s) {
    return String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
function _libNotesFmtDate(iso) {
    if (!iso) return '';
    try {
        const d = new Date(iso.replace(' ', 'T') + (iso.includes('Z') || iso.includes('+') ? '' : 'Z'));
        if (isNaN(d.getTime())) return iso;
        return d.toLocaleString();
    } catch (_) { return iso; }
}
function _libNotesRenderMeta() {
    const el = document.getElementById('lib-notes-meta');
    if (!el) return;
    if (!_libNotes.exists) {
        el.innerHTML = '<span class="lib-notes-meta-empty">No notes yet — start the working doc</span>';
    } else {
        const who = _libNotesEscape(_libNotes.lastEditedBy || 'unknown');
        const when = _libNotesEscape(_libNotesFmtDate(_libNotes.lastEditedAt));
        el.innerHTML = `Last edited by <strong>${who}</strong> · ${when} · v${_libNotes.version}`;
    }
}
// Render a small, safe subset of markdown: ##/###/####, **bold**, *bold*,
// _italic_/*italic*, - bullets, blank-line paragraph breaks. ALL content is
// HTML-escaped FIRST, before any markdown tag substitution, so nothing the
// user (or LLM, or a teammate) types can inject HTML/JS. Inline code with
// `backticks` is also rendered as <code>.
function _libNotesMarkdownToHtml(src) {
    if (!src) return '';
    const escape = (s) => String(s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

    // Apply inline transforms (bold/italic/code) AFTER escaping. Order
    // matters: code first so its contents aren't re-interpreted; then bold
    // (longer markers) before italic (shorter markers) to avoid swallowing.
    const inline = (line) => {
        let s = escape(line);
        // Inline code: `...`
        s = s.replace(/`([^`]+?)`/g, '<code>$1</code>');
        // Bold: **...** or __...__
        s = s.replace(/\*\*([^*]+?)\*\*/g, '<strong>$1</strong>');
        s = s.replace(/__([^_]+?)__/g, '<strong>$1</strong>');
        // Italic: *...* or _..._  (single-char markers, after bold so they
        // don't eat ** pairs)
        s = s.replace(/(^|[^*])\*([^*\n]+?)\*(?!\*)/g, '$1<em>$2</em>');
        s = s.replace(/(^|[^_])_([^_\n]+?)_(?!_)/g, '$1<em>$2</em>');
        // Checkboxes: - [ ] / - [x] inside a bullet line
        s = s.replace(/\[ \]/g, '<span class="lib-notes-checkbox">☐</span>');
        s = s.replace(/\[(?:x|X)\]/g, '<span class="lib-notes-checkbox checked">☑</span>');
        return s;
    };

    const lines = src.split('\n');
    const out = [];
    let inList = false;
    let para = [];
    const flushPara = () => {
        if (!para.length) return;
        out.push('<p>' + para.map(inline).join('<br>') + '</p>');
        para = [];
    };
    const closeList = () => {
        if (inList) { out.push('</ul>'); inList = false; }
    };

    for (const raw of lines) {
        const line = raw.replace(/\s+$/, '');
        if (!line.trim()) {
            flushPara(); closeList(); continue;
        }
        // Headings
        const h = /^(#{2,6})\s+(.+)$/.exec(line);
        if (h) {
            flushPara(); closeList();
            const level = Math.min(h[1].length, 6);  // ## → h2, etc. up to h6
            out.push(`<h${level} class="lib-notes-h${level}">${inline(h[2])}</h${level}>`);
            continue;
        }
        // Bullet (with optional checkbox)
        const b = /^\s*[-*]\s+(.+)$/.exec(line);
        if (b) {
            flushPara();
            if (!inList) { out.push('<ul class="lib-notes-ul">'); inList = true; }
            // Detect checkbox-style task lines so we can drop the bullet dot
            // and just show the checkbox (otherwise it renders as ●  ☐ which
            // looks redundant).
            const cb = /^\[( |x|X)\]\s*(.*)$/.exec(b[1]);
            if (cb) {
                const checked = cb[1].toLowerCase() === 'x';
                const text = inline(cb[2]);
                out.push(
                    `<li class="lib-notes-task${checked ? ' done' : ''}">` +
                    `<span class="lib-notes-checkbox${checked ? ' checked' : ''}">${checked ? '☑' : '☐'}</span>` +
                    `<span class="lib-notes-task-text">${text}</span></li>`
                );
            } else {
                out.push(`<li>${inline(b[1])}</li>`);
            }
            continue;
        }
        // Plain paragraph line
        closeList();
        para.push(line);
    }
    flushPara(); closeList();
    return out.join('\n');
}

function _libNotesRenderView(content) {
    const view = document.getElementById('lib-notes-view');
    if (!view) return;
    // Click anywhere in the view to jump straight into edit mode — fewer
    // clicks for the common case of "I want to add a note."
    view.style.cursor = 'text';
    view.title = 'Click to edit';
    view.onclick = () => libNotesStartEdit();
    if (!content || !content.trim()) {
        view.innerHTML = '<div class="lib-notes-empty">No notes yet. Click anywhere here or hit <strong>Edit</strong> to start the team working doc.</div>';
    } else {
        view.innerHTML = `<div class="lib-notes-rendered">${_libNotesMarkdownToHtml(content)}</div>`;
    }
}

function _libNotesAutosize(ta) {
    if (!ta) return;
    // Reset and grow to content; cap at viewport-ish so it doesn't run wild.
    ta.style.height = 'auto';
    const target = Math.min(ta.scrollHeight + 4, Math.max(window.innerHeight - 220, 360));
    ta.style.height = target + 'px';
}

async function libNotesLoad(company) {
    _libNotes.company = company;
    const view = document.getElementById('lib-notes-view');
    const meta = document.getElementById('lib-notes-meta');
    const conflict = document.getElementById('lib-notes-conflict');
    if (conflict) conflict.style.display = 'none';
    if (meta) meta.textContent = 'Loading...';
    if (view) view.innerHTML = '';
    try {
        const r = await fetch('/api/deal-pipeline/notes?company=' + encodeURIComponent(company), { headers: _rvmHeaders() });
        if (!r.ok) throw new Error('HTTP ' + r.status);
        const data = await r.json();
        _libNotes.version = data.version || 0;
        _libNotes.exists = !!data.exists;
        _libNotes.lastEditedBy = data.last_edited_by_username || '';
        _libNotes.lastEditedAt = data.last_edited_at || '';
        _libNotes.editing = false;
        _libNotesRenderMeta();
        _libNotesRenderView(data.content || '');
        // Stash the loaded content for "cancel" reset.
        _libNotes._loadedContent = data.content || '';
    } catch (e) {
        if (meta) meta.textContent = 'Failed to load notes: ' + e.message;
    }
}

function libNotesStartEdit() {
    if (!_libNotes.company) return;
    const ta = document.getElementById('lib-notes-edit');
    const view = document.getElementById('lib-notes-view');
    const editBtn = document.getElementById('lib-notes-edit-btn');
    const histBtn = document.getElementById('lib-notes-history-btn');
    const resetBtn = document.getElementById('lib-notes-reset-btn');
    const saveBtn = document.getElementById('lib-notes-save-btn');
    const cancelBtn = document.getElementById('lib-notes-cancel-btn');
    const histPanel = document.getElementById('lib-notes-history-panel');
    if (!ta || !view) return;
    ta.value = _libNotes._loadedContent || '';
    ta.style.display = 'block';
    view.style.display = 'none';
    if (histPanel) histPanel.style.display = 'none';
    if (editBtn) editBtn.style.display = 'none';
    if (histBtn) histBtn.style.display = 'none';
    if (resetBtn) resetBtn.style.display = 'none';
    if (saveBtn) saveBtn.style.display = '';
    if (cancelBtn) cancelBtn.style.display = '';
    _libNotes.editing = true;
    // Bind grow-as-you-type + Cmd/Ctrl+Enter / Cmd/Ctrl+S to save. Idempotent
    // — re-binding overwrites the previous handler so no listener leaks.
    ta.oninput = () => _libNotesAutosize(ta);
    ta.onkeydown = (e) => {
        const meta = e.metaKey || e.ctrlKey;
        if (meta && (e.key === 'Enter' || e.key === 's' || e.key === 'S')) {
            e.preventDefault();
            libNotesSave();
        } else if (e.key === 'Escape') {
            e.preventDefault();
            libNotesCancelEdit();
        }
    };
    _libNotesAutosize(ta);
    setTimeout(() => { ta.focus(); _libNotesAutosize(ta); }, 50);
}

function libNotesCancelEdit() {
    const ta = document.getElementById('lib-notes-edit');
    const view = document.getElementById('lib-notes-view');
    const editBtn = document.getElementById('lib-notes-edit-btn');
    const histBtn = document.getElementById('lib-notes-history-btn');
    const resetBtn = document.getElementById('lib-notes-reset-btn');
    const saveBtn = document.getElementById('lib-notes-save-btn');
    const cancelBtn = document.getElementById('lib-notes-cancel-btn');
    const conflict = document.getElementById('lib-notes-conflict');
    if (ta) ta.style.display = 'none';
    if (view) view.style.display = 'block';
    if (editBtn) editBtn.style.display = '';
    if (histBtn) histBtn.style.display = '';
    if (resetBtn) resetBtn.style.display = '';
    if (saveBtn) saveBtn.style.display = 'none';
    if (cancelBtn) cancelBtn.style.display = 'none';
    if (conflict) conflict.style.display = 'none';
    _libNotes.editing = false;
}

async function libNotesSave() {
    if (!_libNotes.company) return;
    const ta = document.getElementById('lib-notes-edit');
    const saveBtn = document.getElementById('lib-notes-save-btn');
    const status = document.getElementById('lib-notes-status');
    const conflict = document.getElementById('lib-notes-conflict');
    if (!ta) return;
    const content = ta.value;
    if (saveBtn) { saveBtn.disabled = true; saveBtn.textContent = 'Saving...'; }
    if (status) { status.style.display = 'block'; status.textContent = 'Saving...'; status.className = 'lib-notes-status info'; }
    if (conflict) conflict.style.display = 'none';
    try {
        const r = await fetch('/api/deal-pipeline/notes', {
            method: 'PUT',
            headers: { ..._rvmHeaders(), 'Content-Type': 'application/json' },
            body: JSON.stringify({
                company_name: _libNotes.company,
                content: content,
                expected_version: _libNotes.version,
            }),
        });
        if (r.status === 409) {
            // Stale write — show the colleague's version side-by-side and bail.
            const data = await r.json();
            const cur = data.current || {};
            _libNotes.version = cur.version || _libNotes.version;
            _libNotes.exists = !!cur.exists;
            _libNotes.lastEditedBy = cur.last_edited_by_username || '';
            _libNotes.lastEditedAt = cur.last_edited_at || '';
            _libNotes._loadedContent = cur.content || '';
            _libNotesRenderMeta();
            if (conflict) {
                conflict.style.display = 'block';
                const who = _libNotesEscape(cur.last_edited_by_username || 'someone');
                const when = _libNotesEscape(_libNotesFmtDate(cur.last_edited_at));
                conflict.innerHTML =
                    `<strong>${who}</strong> saved a newer version (${when}). ` +
                    `Their text is shown below — copy anything you want to keep, then click <em>Cancel</em> to reload, or <em>Save</em> again to overwrite their version.<br><br>` +
                    `<details><summary>Show their version</summary><pre class="lib-notes-pre" style="margin-top:8px;background:#FCFAF5;border:1px solid #E5DFCF;padding:10px;border-radius:6px;">${_libNotesEscape(cur.content || '')}</pre></details>`;
            }
            // Bump expected_version so a deliberate re-save will go through.
            _libNotes.version = cur.version || _libNotes.version;
            if (status) status.style.display = 'none';
            return;
        }
        if (!r.ok) {
            const err = await r.json().catch(() => ({ detail: r.statusText }));
            throw new Error(err.detail || 'Save failed');
        }
        const data = await r.json();
        _libNotes.version = data.version || 0;
        _libNotes.exists = !!data.exists;
        _libNotes.lastEditedBy = data.last_edited_by_username || '';
        _libNotes.lastEditedAt = data.last_edited_at || '';
        _libNotes._loadedContent = data.content || '';
        _libNotesRenderMeta();
        _libNotesRenderView(data.content || '');
        libNotesCancelEdit();
        if (status) { status.style.display = 'block'; status.textContent = 'Saved.'; status.className = 'lib-notes-status success';
            setTimeout(() => { if (status) status.style.display = 'none'; }, 2000); }
    } catch (e) {
        if (status) { status.style.display = 'block'; status.textContent = 'Save failed: ' + e.message; status.className = 'lib-notes-status error'; }
    } finally {
        if (saveBtn) { saveBtn.disabled = false; saveBtn.textContent = 'Save'; }
    }
}

async function libNotesShowHistory() {
    if (!_libNotes.company) return;
    const panel = document.getElementById('lib-notes-history-panel');
    if (!panel) return;
    if (panel.style.display === 'block') { panel.style.display = 'none'; return; }
    panel.style.display = 'block';
    panel.innerHTML = '<div class="lib-notes-history-empty">Loading history...</div>';
    try {
        const r = await fetch('/api/deal-pipeline/notes/history?company=' + encodeURIComponent(_libNotes.company), { headers: _rvmHeaders() });
        if (!r.ok) throw new Error('HTTP ' + r.status);
        const data = await r.json();
        const rows = data.history || [];
        if (!rows.length) {
            panel.innerHTML = '<div class="lib-notes-history-empty">No saved versions yet.</div>';
            return;
        }
        panel.innerHTML = rows.map(h => `
            <details class="lib-notes-history-item">
                <summary>
                    <strong>v${h.version}</strong> · ${_libNotesEscape(h.edited_by_username || 'unknown')} · ${_libNotesEscape(_libNotesFmtDate(h.edited_at))}
                </summary>
                <pre class="lib-notes-pre" style="margin-top:8px;">${_libNotesEscape(h.content || '')}</pre>
            </details>
        `).join('');
    } catch (e) {
        panel.innerHTML = `<div class="lib-notes-history-empty">Failed to load history: ${_libNotesEscape(e.message)}</div>`;
    }
}

function _wizFolderEsc(e) {
    if (e.key === 'Escape') wizCloseFolder();
}

function wizOpenFolderDeal(reportId) {
    wizCloseFolder();
    wizLoadReport(reportId);
}

function wizOpenFolderDdr(ddrId, filename) {
    // Reuse the existing DDR-by-id download endpoint
    fetch(`/api/ddr/reports/${ddrId}/download`, { headers: _rvmHeaders() })
        .then(r => r.ok ? r.blob() : Promise.reject('Download failed'))
        .then(blob => {
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = filename || 'DDR_Report.pdf';
            a.click();
            URL.revokeObjectURL(url);
        })
        .catch(err => showToast('DDR download failed: ' + err));
}

function wizOpenFolderMemo(memoId) {
    wizCloseFolder();
    switchTab('memo');
    setTimeout(() => {
        if (typeof memoLoadHistoryItem === 'function') {
            memoLoadHistoryItem(memoId);
        }
    }, 200);
}

// ── Soft warning during extraction ────────────────────────────────────
// Called after a successful extraction to nudge the analyst if a teammate
// has already analyzed this company.
function wizCheckLibraryForCompany(companyName) {
    if (!companyName || !_libCompanies?.length) return;
    const norm = companyName.trim().toLowerCase();
    const match = _libCompanies.find(g =>
        (g.company_name || '').trim().toLowerCase() === norm);
    if (!match) return;

    const idx = _libCompanies.indexOf(match);
    const authors = _libCollectAuthors(match);
    const authorList = authors.length ? authors.join(', ') : 'someone';
    const banner = document.createElement('div');
    banner.className = 'lib-already-banner';
    banner.innerHTML = `
        <div>
            <strong>${_libEscape(match.company_name)}</strong> has already been analyzed by ${_libEscape(authorList)}
            (${_libFmtDate(match.latest_at)}).
        </div>
        <span class="lib-already-link" onclick="wizOpenFolder(${idx})">View existing folder &rsaquo;</span>`;
    const review = document.getElementById('wiz-review-fields');
    if (review && !review.parentNode.querySelector('.lib-already-banner')) {
        review.parentNode.insertBefore(banner, review);
    }
}

// Wire the search box once on first render
document.addEventListener('input', (e) => {
    if (e.target && e.target.id === 'lib-search') {
        _libQuery = e.target.value;
        _wizRenderLibrary();
    }
});

async function wizLoadReport(rid) {
    try {
        const r = await fetch(`/api/deal-pipeline/report/${rid}`, {headers: _rvmHeaders()});
        const d = await r.json();
        _wizReport = d.report;
        _wizReportId = d.id;
        window._currentReportId = d.id;
        _wizSavedInputs = d.inputs || null;
        // Restore config form so "Back to Configure" works
        if (_wizSavedInputs) wizRestoreConfig(_wizSavedInputs);
        wizRenderReport(_wizReport);
        wizGoStep(4);
        setTimeout(() => wizRunQAReview(d.id, null), 800);
    } catch(e) { showToast('Failed to load report'); }
}

/* ═══════════════════════════════════════════════════════════
   QA REVIEW — auto-runs after report generation/load
   ═══════════════════════════════════════════════════════════ */

let _qaFindings = [];

async function wizRunQAReview(reportId, memoMarkdown) {
    if (!reportId) return;

    // Show spinner state
    const banner  = document.getElementById('qa-banner');
    const summary = document.getElementById('qa-banner-summary');
    const spinner = document.getElementById('qa-spinner');
    const panel   = document.getElementById('qa-panel');
    if (!banner) return;

    banner.style.display = 'flex';
    banner.className = 'qa-banner qa-scanning';
    summary.textContent = '';
    spinner.style.display = 'inline';
    if (panel) panel.style.display = 'none';

    try {
        // Try to get memo text from the open memo editor if available
        let memo = memoMarkdown || '';
        if (!memo) {
            const memoEl = document.querySelector('.memo-section-content');
            if (memoEl) memo = memoEl.innerText || '';
        }

        const resp = await fetch(`/api/deal-pipeline/report/${reportId}/qa-review`, {
            method: 'POST',
            headers: _rvmHeaders(),
            body: JSON.stringify({ memo_markdown: memo, run_llm: !!memo }),
        });
        const data = await resp.json();
        _qaFindings = data.findings || [];
        const s = data.summary || {};

        spinner.style.display = 'none';

        if (s.total === 0) {
            banner.className = 'qa-banner qa-clean';
            summary.innerHTML = '<span class="qa-ok-icon">&#10003;</span> No issues found — all checks passed.';
            document.querySelector('.qa-toggle-btn') && (document.querySelector('.qa-toggle-btn').style.display = 'none');
            return;
        }

        // Build severity summary chips
        let chips = [];
        if (s.errors   > 0) chips.push(`<span class="qa-chip qa-chip-error">${s.errors} error${s.errors>1?'s':''}</span>`);
        if (s.warnings > 0) chips.push(`<span class="qa-chip qa-chip-warn">${s.warnings} warning${s.warnings>1?'s':''}</span>`);
        if (s.infos    > 0) chips.push(`<span class="qa-chip qa-chip-info">${s.infos} note${s.infos>1?'s':''}</span>`);

        banner.className = s.errors > 0 ? 'qa-banner qa-has-errors' : 'qa-banner qa-has-warnings';
        summary.innerHTML = `QA Review: ${chips.join(' ')} across this report`;
        const toggleBtn = document.querySelector('.qa-toggle-btn');
        if (toggleBtn) { toggleBtn.style.display = ''; toggleBtn.textContent = 'View Findings'; }

        _wizRenderQAPanel(_qaFindings);

    } catch(e) {
        spinner.style.display = 'none';
        banner.className = 'qa-banner qa-scan-error';
        summary.textContent = 'QA review unavailable.';
    }
}

function wizToggleQAPanel() {
    const panel = document.getElementById('qa-panel');
    const btn   = document.querySelector('.qa-toggle-btn');
    if (!panel) return;
    const visible = panel.style.display !== 'none';
    panel.style.display = visible ? 'none' : 'block';
    if (btn) btn.textContent = visible ? 'View Findings' : 'Hide Findings';
}

function _wizRenderQAPanel(findings) {
    const panel = document.getElementById('qa-panel');
    if (!panel) return;

    const _icon = sev => sev === 'error' ? '&#10007;' : sev === 'warning' ? '&#9888;' : '&#9432;';
    const _cat  = cat => ({'number_discrepancy':'# Discrepancy','internal_consistency':'Inconsistency','logic':'Logic Check','missing_section':'Missing Data'}[cat] || cat);

    const rows = findings.map(f => `
        <div class="qa-finding qa-finding-${f.severity}">
            <div class="qa-finding-header">
                <span class="qa-finding-icon">${_icon(f.severity)}</span>
                <span class="qa-finding-title">${f.title}</span>
                <span class="qa-finding-cat">${_cat(f.category)}</span>
                <span class="qa-finding-loc">${f.location || ''}</span>
            </div>
            <div class="qa-finding-detail">${f.detail}</div>
        </div>`).join('');

    panel.innerHTML = `
        <div class="qa-panel-header">
            <strong>QA Findings</strong>
            <span style="color:var(--text-tertiary);font-size:0.78rem;margin-left:8px;">${findings.length} item${findings.length!==1?'s':''} — errors first</span>
        </div>
        ${rows}`;
}

/**
 * Populate the Step 3 configuration form from a saved inputs_json object.
 * Called when loading a previous report so the user can tweak & re-run.
 */
function wizRestoreConfig(inp) {
    const _s = (id, val) => { const el = document.getElementById(id); if (el && val != null) el.value = val; };

    // Deal terms
    _s('wiz-company-name', inp.company_name);
    _s('wiz-tech-desc', inp.technology_description);
    _s('wiz-entry-stage', inp.entry_stage);
    _s('wiz-trl', inp.trl);
    _s('wiz-check-size', inp.check_size_millions);
    _s('wiz-pre-money', inp.pre_money_millions);
    _s('wiz-round-size', inp.round_size_m);
    // Restore entry year; fall back to volume.commercial_launch_yr for old saved reports
    const restoredEntryYear = inp.entry_year || inp.volume?.commercial_launch_yr;
    if (restoredEntryYear && restoredEntryYear >= 2010 && restoredEntryYear <= 2040) {
        _s('wiz-entry-year', restoredEntryYear);
    }
    _wizUpdateVolumeLabels();

    // Market & simulation
    _s('wiz-archetype', inp.archetype);
    _s('wiz-tam', inp.tam_millions);
    _s('wiz-sector', inp.sector_profile);
    _s('wiz-pen-low', inp.penetration_low);
    _s('wiz-pen-high', inp.penetration_high);
    _s('wiz-mult-low', inp.exit_multiple_low);
    _s('wiz-mult-high', inp.exit_multiple_high);

    // Carbon model
    if (inp.op_carbon) {
        // Ensure the displaced_resource option exists in the select before setting
        const drSel = document.getElementById('wiz-displaced-resource');
        const drVal = inp.op_carbon.displaced_resource;
        if (drSel && drVal) {
            if (![...drSel.options].some(o => o.value === drVal)) {
                const opt = document.createElement('option');
                opt.value = drVal;
                opt.textContent = drVal;
                drSel.appendChild(opt);
            }
            drSel.value = drVal;
        }
        _s('wiz-baseline-prod', inp.op_carbon.baseline_lifetime_prod);
        _s('wiz-range-imp', inp.op_carbon.range_improvement);
        _s('wiz-specific-prod-units', inp.op_carbon.specific_production_units);
    }
    if (inp.volume) {
        _s('wiz-unit-definition', inp.volume.unit_definition);
        _s('wiz-service-life', inp.volume.unit_service_life_yrs);
        // Volume projections
        const vols = inp.volume.year_volumes || [];
        for (let i = 0; i < 10; i++) {
            _s(`wiz-vol-${i}`, vols[i] != null ? vols[i] : 0);
        }
    }
    // Update carbon chain labels to match restored values
    if (typeof _updateCarbonLabels === 'function') _updateCarbonLabels();

    // Fund parameters
    _s('wiz-fund-size', inp.fund_size_m);
    _s('wiz-n-deals', inp.n_deals);
    _s('wiz-mgmt-fee', inp.mgmt_fee_pct);
    _s('wiz-reserve', inp.reserve_pct);
    _s('wiz-max-conc', inp.max_concentration_pct);
    // Restore fund vintage year (master anchor for all chart X-axes)
    if (inp.fund_vintage_year && inp.fund_vintage_year >= 2010 && inp.fund_vintage_year <= 2040) {
        _s('wiz-fund-vintage-year', inp.fund_vintage_year);
    }

    // Founder projections (review step fields, if they exist)
    if (inp.founder_revenue_projections) {
        for (let i = 0; i < inp.founder_revenue_projections.length; i++) {
            _s(`wiz-rev-rev-${i}`, inp.founder_revenue_projections[i]);
        }
    }
    if (inp.founder_volume_projections) {
        for (let i = 0; i < inp.founder_volume_projections.length; i++) {
            _s(`wiz-rev-vol-${i}`, inp.founder_volume_projections[i]);
        }
    }

    // Recalculate entry ownership display from restored check / round / pre-money
    _wizAutoCalcOwnership();

    // Store extraction & financial model data if present
    if (inp.extraction_data) {
        // Full extraction persisted — restore entirely so Review step shows all fields
        _wizExtraction = inp.extraction_data;
    } else if (inp.extraction_confidence) {
        // Legacy reports: only confidence + source were saved
        _wizExtraction = { confidence: inp.extraction_confidence, _source: inp.extraction_source };
    }
    if (inp.financial_model) _wizFmData = inp.financial_model;
}

async function wizExtractAndNext() {
    const deckFile = document.getElementById('wiz-deck-file').files[0];
    const modelFile = document.getElementById('wiz-model-file').files[0];
    const url = document.getElementById('wiz-url-input').value.trim();

    if (!deckFile && !modelFile && !url) { showToast('Upload a file or enter a URL'); return; }

    const status = document.getElementById('wiz-extract-status');
    status.className = 'wiz-status loading';
    status.textContent = deckFile ? 'Extracting deck (this may take 10-15 seconds)...' : 'Processing...';
    document.getElementById('wiz-extract-btn').disabled = true;

    _wizExtraction = {};
    _wizFmData = null;
    let extractionOk = false;

    try {
        if (deckFile || url) {
            const fd = new FormData();
            if (deckFile) fd.append('file', deckFile);
            if (url && !deckFile) fd.append('url', url);
            const headers = {};
            if (_rvmToken) headers['Authorization'] = 'Bearer ' + _rvmToken;
            const r = await fetch('/api/extract', {method: 'POST', headers, body: fd});
            let d;
            try { d = await r.json(); } catch(_) { d = {detail: 'Server error (HTTP ' + r.status + ')'}; }
            if (r.ok && d && !d.detail && !d.error) {
                _wizExtraction = d;
                extractionOk = true;
            } else {
                let errMsg = (d && d.detail) ? d.detail
                           : (d && d.error)  ? d.error
                           : 'Extraction failed (HTTP ' + r.status + ')';
                if (errMsg.includes('ANTHROPIC_API_KEY')) {
                    errMsg = 'ANTHROPIC_API_KEY is not set. Add it to volo-engine/.env and restart the server.';
                } else if (r.status === 401) {
                    errMsg = 'Not authenticated. Please log in first (Data > Auth tab).';
                }
                status.className = 'wiz-status error';
                status.textContent = errMsg;
                document.getElementById('wiz-extract-btn').disabled = false;
                return;
            }
        }

        if (modelFile) {
            const modelExt = ('.' + modelFile.name.split('.').pop()).toLowerCase();
            const isVision = _IMAGE_EXTS.has(modelExt);
            const modeField = document.getElementById('wiz-model-mode');
            const mode = (modeField && modeField.value === 'deep' && !isVision) ? 'deep' : 'quick';

            let md;
            if (mode === 'deep') {
                // Deep extraction: AI agent with streamed progress.
                status.textContent = '';  // handled by overlay
                md = await _wizDeepExtract(modelFile, status);
                if (!md) { // user-visible error already shown by _wizDeepExtract
                    document.getElementById('wiz-extract-btn').disabled = false;
                    return;
                }
            } else {
                status.textContent = isVision
                    ? '✦ Vision AI: reading financial model screenshot (15-25 seconds)...'
                    : 'Extracting financial model (this may take 20-30 seconds)...';
                const mfd = new FormData();
                mfd.append('file', modelFile);
                mfd.append('mode', 'quick');
                const mHeaders = {};
                if (_rvmToken) mHeaders['Authorization'] = 'Bearer ' + _rvmToken;
                const mr = await fetch('/api/extract-model', {method: 'POST', headers: mHeaders, body: mfd});
                try { md = await mr.json(); } catch(_) { md = {detail: 'Server error (HTTP ' + mr.status + ')'}; }
                if (!(mr.ok && md && md.status === 'ok')) {
                    const fmErr = (md && md.detail) ? md.detail : 'Financial model extraction failed';
                    status.className = 'wiz-status error';
                    status.textContent = fmErr;
                    document.getElementById('wiz-extract-btn').disabled = false;
                    return;
                }
            }
            _wizFmData = md;
            extractionOk = true;
        }

        if (!extractionOk) {
            status.className = 'wiz-status error';
            status.textContent = 'No extraction performed.';
            document.getElementById('wiz-extract-btn').disabled = false;
            return;
        }

        if (_wizFmData && _wizFmData.financials) {
            _wizMergeFmIntoExtraction();
        }

        status.className = 'wiz-status';
        status.textContent = '';
        document.getElementById('wiz-extract-btn').disabled = false;
        wizPopulateReview();
        wizGoStep(2);

        // Remember the deck so the analyst can optionally run DDR on it
        // from the Due Diligence Report tab. DDR no longer runs automatically.
        if (deckFile) {
            window._wizLastDeckFile = deckFile;
            if (typeof ddrSyncSubmittedDeck === 'function') ddrSyncSubmittedDeck();
        }

        // Soft warning: nudge if a teammate already analyzed this company.
        const extractedName = (_wizExtraction && _wizExtraction.name) || '';
        if (extractedName && typeof wizCheckLibraryForCompany === 'function') {
            wizCheckLibraryForCompany(extractedName);
        }
    } catch(e) {
        status.className = 'wiz-status error';
        status.textContent = 'Extraction error: ' + e.message;
        document.getElementById('wiz-extract-btn').disabled = false;
    }
}

function wizPopulateReview() {
    const d = _wizExtraction || {};
    const fieldsDiv = document.getElementById('wiz-review-fields');
    const metaDiv = document.getElementById('wiz-review-meta');

    const hasDeckData = !!(d.name || d.technology_description || d.stage || d.tam_claim || d.current_revenue);
    const hasFmData = !!(_wizFmData && _wizFmData.financials && Object.keys(_wizFmData.financials).length);

    let html = '';

    if (hasFmData) {
        html += '<div id="wiz-fm-review-inline"></div>';
    }

    if (hasDeckData || !hasFmData) {
        const groups = [
            {title: 'Company Info', fields: [
                ['name', 'Company Name', 'text'],
                ['technology_description', 'Technology Description', 'text'],
                ['stage', 'Stage', 'text'],
                ['commercial_launch_yr', 'Launch Year', 'number'],
                ['team_size', 'Team Size', 'number'],
                ['funding_raised', 'Total Funding Raised ($)', 'number'],
            ]},
            {title: 'Market Claims', fields: [
                ['tam_claim', 'TAM (raw $)', 'number'],
                ['sam_claim', 'SAM (raw $)', 'number'],
                ['market_geography', 'Target Geography', 'text'],
                ['competitive_landscape', 'Competitors', 'text'],
            ]},
            {title: 'Financial Data', fields: [
                ['current_revenue', 'Current Revenue', 'number'],
                ['revenue_units', 'Revenue Units', 'text'],
                ['growth_rate_claim', 'Growth Rate', 'text'],
            ]},
            {title: 'Technology', fields: [
                ['unit_definition', 'Unit Definition', 'text'],
                ['displaced_technology', 'Displaced Technology', 'text'],
                ['trl_indicators', 'TRL Indicators', 'text'],
            ]},
        ];

        for (const group of groups) {
            html += `<div class="wiz-review-group"><h4 class="wiz-review-group-title">${group.title}</h4>`;
            for (const [key, label, type] of group.fields) {
                const val = d[key] ?? '';
                const conf = d.confidence?.fields?.[key];
                let badge = '';
                if (conf != null) {
                    const cls = conf >= 0.7 ? 'high' : conf >= 0.4 ? 'med' : 'low';
                    badge = `<span class="ext-confidence ${cls}">${(conf*100).toFixed(0)}%</span>`;
                }
                html += `<div class="form-group"><label>${label} ${badge}</label><input type="${type}" id="wiz-rev-${key}" value="${val === null ? '' : val}" data-field="${key}"></div>`;
            }
            html += '</div>';
        }
    }

    const fmYears = d._fm_fiscal_years || [];
    if (d.revenue_projections && Array.isArray(d.revenue_projections) && d.revenue_projections.some(v => v !== 0)) {
        const src = d._fm_revenue_source ? ' (from financial model)' : '';
        html += `<div class="wiz-review-group"><h4 class="wiz-review-group-title">Revenue Projections, $M${src}</h4><div class="form-group"><div class="volume-row">`;
        for (let i = 0; i < d.revenue_projections.length && i < 10; i++) {
            const label = fmYears[i] ? fmYears[i] : `Y${i+1}`;
            html += `<div style="text-align:center;"><small style="font-size:0.65rem; color:var(--text-secondary);">${label}</small><br><input type="number" id="wiz-rev-rev-${i}" value="${d.revenue_projections[i] || 0}" style="width:80px;"></div>`;
        }
        html += '</div></div></div>';
    }

    if (d.unit_volumes_projected && Array.isArray(d.unit_volumes_projected) && d.unit_volumes_projected.some(v => v !== 0)) {
        const src = d._fm_units_source ? ' (from financial model)' : '';
        html += `<div class="wiz-review-group"><h4 class="wiz-review-group-title">Unit Volume Projections${src}</h4><div class="form-group"><div class="volume-row">`;
        for (let i = 0; i < d.unit_volumes_projected.length && i < 10; i++) {
            const label = fmYears[i] ? fmYears[i] : `Y${i+1}`;
            html += `<div style="text-align:center;"><small style="font-size:0.65rem; color:var(--text-secondary);">${label}</small><br><input type="number" id="wiz-rev-vol-${i}" value="${d.unit_volumes_projected[i] || 0}" style="width:80px;"></div>`;
        }
        html += '</div></div></div>';
    }

    if (d.unit_economics && typeof d.unit_economics === 'object') {
        html += '<div class="wiz-review-group"><h4 class="wiz-review-group-title">Unit Economics</h4>';
        for (const [k, v] of Object.entries(d.unit_economics)) {
            html += `<div class="form-group"><label>${k.replace(/_/g, ' ')}</label><input type="text" value="${v}" readonly></div>`;
        }
        html += '</div>';
    }

    if (d.key_risks && Array.isArray(d.key_risks)) {
        html += '<div class="wiz-review-group"><h4 class="wiz-review-group-title">Key Risks</h4><ul style="font-size:0.82rem; padding-left:16px;">';
        d.key_risks.forEach(r => { html += `<li>${r}</li>`; });
        html += '</ul></div>';
    }

    fieldsDiv.innerHTML = html;

    if (hasFmData) {
        const fmTarget = document.getElementById('wiz-fm-review-inline');
        if (fmTarget) _wizRenderFmInto(fmTarget);
    }

    let metaHtml = '';
    if (d.confidence?.overall != null) {
        const oc = d.confidence.overall;
        const cls = oc >= 0.7 ? 'high' : oc >= 0.4 ? 'med' : 'low';
        metaHtml += `<div style="margin-bottom:16px;"><strong>Overall Confidence:</strong> <span class="ext-confidence ${cls}" style="font-size:0.82rem;">${(oc*100).toFixed(0)}%</span></div>`;
    }
    if (d._validation_warnings?.length) {
        metaHtml += '<div style="margin-bottom:16px;"><strong>Warnings:</strong><ul style="margin:4px 0 0 16px; font-size:0.78rem; color:var(--danger);">';
        d._validation_warnings.forEach(w => metaHtml += `<li>${w}</li>`);
        metaHtml += '</ul></div>';
    }
    if (d.notes) metaHtml += `<div style="font-size:0.78rem; color:var(--text-tertiary);"><em>${d.notes}</em></div>`;
    metaDiv.innerHTML = metaHtml || '<p style="color:var(--text-tertiary); font-size:0.78rem;">No additional metadata.</p>';
}

function _wizMergeFmIntoExtraction() {
    if (!_wizFmData || !_wizFmData.financials) return;
    const d = _wizExtraction || {};
    const fin = _wizFmData.financials;
    const allYears = _wizFmData.fiscal_years || [];
    const years = allYears.filter(y => y >= 2020 && y <= 2045);

    // Auto-suggest Investment Year from FM's first fiscal year.
    // Rule: only pull entry_year EARLIER (toward current year), never push it further into
    // the future.  A company's FM may start in 2029 (commercial launch) while VoLo invests
    // in 2026 — we must not overwrite a valid near-term entry_year with a far-future FM start.
    const entryYrEl = document.getElementById('wiz-entry-year');
    if (entryYrEl && years.length) {
        const currentYear = new Date().getFullYear();
        const firstFutureYear = years.find(y => y >= currentYear) || years[years.length - 1];
        const existingEntry = parseInt(entryYrEl.value || 0);
        const entryIsValid = existingEntry >= 2010 && existingEntry <= 2040;
        // Only auto-set if:
        //  (a) no valid entry year is set yet, OR
        //  (b) FM's first year is EARLIER than the currently set entry year
        //      (meaning the FM covers earlier history — pull the anchor back, never forward)
        if (firstFutureYear && (!entryIsValid || firstFutureYear < existingEntry)) {
            entryYrEl.value = firstFutureYear;
            _wizUpdateVolumeLabels();
        }
    }

    if ((!d.revenue_projections || !d.revenue_projections.length) && fin.revenue && years.length) {
        d.revenue_projections = years.map(y => {
            const raw = fin.revenue[String(y)] ?? fin.revenue[y] ?? 0;
            return Math.round((raw / 1_000_000) * 100) / 100;
        });
        d.revenue_units = d.revenue_units || '$M (from model)';
        d._fm_revenue_source = true;
        d._fm_fiscal_years = years;
    }

    if ((!d.unit_volumes_projected || !d.unit_volumes_projected.length) && _wizFmData.units) {
        const unitKey = Object.keys(_wizFmData.units)[0];
        if (unitKey) {
            const series = _wizFmData.units[unitKey];
            d.unit_volumes_projected = years.map(y => {
                const entry = series[String(y)] ?? series[y];
                return typeof entry === 'object' ? (entry?.value ?? 0) : (entry ?? 0);
            });
            d._fm_units_source = true;
        }
    }

    _wizExtraction = d;
}

function _wizRenderFmInto(el) {
    const fm = _wizFmData;
    if (!fm || !fm.financials || !Object.keys(fm.financials).length) {
        el.innerHTML = '';
        return;
    }

    const years = fm.fiscal_years || [];
    const scenarios = fm.scenarios || null;
    const detectedScenarios = fm.detected_scenarios || ['base'];
    const hasMultiScenario = scenarios && detectedScenarios.length > 1;

    function fmtVal(v) {
        if (v == null) return '--';
        const n = Number(v);
        if (isNaN(n)) return String(v);
        const abs = Math.abs(n);
        const sign = n < 0 ? '-' : '';
        if (abs >= 1e9) return sign + '$' + (abs / 1e9).toFixed(1) + 'B';
        if (abs >= 1e6) return sign + '$' + (abs / 1e6).toFixed(1) + 'M';
        if (abs >= 1e3) return sign + '$' + (abs / 1e3).toFixed(1) + 'K';
        if (abs > 0) return sign + '$' + abs.toFixed(0);
        return '$0';
    }

    function renderFinTable(fin, unitData, label) {
        const metrics = Object.keys(fin);
        let h = '';
        if (label) h += `<div style="font-size:0.78rem; font-weight:600; margin:8px 0 4px; text-transform:capitalize;">${label}</div>`;
        h += '<div style="overflow-x:auto;"><table class="prerun-fin-table" style="width:100%; border-collapse:collapse; font-size:0.75rem;">';
        h += '<thead><tr><th style="text-align:left; padding:6px 8px; border-bottom:2px solid var(--border); font-size:0.7rem; text-transform:uppercase; color:var(--text-secondary);">Metric</th>';
        for (const y of years) h += `<th style="text-align:right; padding:6px 8px; border-bottom:2px solid var(--border); font-size:0.7rem;">${y}</th>`;
        h += '</tr></thead><tbody>';
        for (const m of metrics) {
            // Skip metrics where every value is null or zero (e.g. capex when not extracted)
            const hasData = years.some(y => {
                const v = fin[m]?.[String(y)] ?? fin[m]?.[y];
                return v != null && v !== 0;
            });
            if (!hasData) continue;
            h += `<tr data-metric="${m}"><td style="padding:4px 8px; border-bottom:1px solid var(--border); font-weight:500; text-transform:capitalize;">${m.replace(/_/g, ' ')}</td>`;
            for (const y of years) {
                const v = fin[m]?.[String(y)] ?? fin[m]?.[y];
                h += `<td class="prerun-fm-cell" data-metric="${m}" data-year="${y}" style="text-align:right; padding:4px 8px; border-bottom:1px solid var(--border); font-family:var(--font-mono,monospace);">${fmtVal(v)}</td>`;
            }
            h += '</tr>';
        }
        h += '</tbody></table></div>';

        if (unitData && Object.keys(unitData).length) {
            h += '<div style="font-size:0.75rem; font-weight:600; margin:8px 0 4px;">Unit Metrics</div>';
            h += '<div style="overflow-x:auto;"><table style="width:100%; border-collapse:collapse; font-size:0.75rem;">';
            h += '<thead><tr><th style="text-align:left; padding:6px 8px; border-bottom:2px solid var(--border);">Metric</th>';
            for (const y of years) h += `<th style="text-align:right; padding:6px 8px; border-bottom:2px solid var(--border);">${y}</th>`;
            h += '</tr></thead><tbody>';
            for (const [m, series] of Object.entries(unitData)) {
                const hasData = years.some(y => {
                    const e = series?.[String(y)] ?? series?.[y];
                    const vv = typeof e === 'object' ? e?.value : e;
                    return vv != null;
                });
                if (!hasData) continue;
                const firstEntry = Object.values(series).find(e => e != null);
                const unitLabel = (typeof firstEntry === 'object' && firstEntry?.unit_type) ? ` (${firstEntry.unit_type})` : '';
                h += `<tr><td style="padding:4px 8px; border-bottom:1px solid var(--border); font-weight:500; text-transform:capitalize;">${m.replace(/_/g, ' ')}${unitLabel}</td>`;
                for (const y of years) {
                    const entry = series?.[String(y)] ?? series?.[y];
                    const vv = typeof entry === 'object' ? entry?.value : entry;
                    h += `<td style="text-align:right; padding:4px 8px; border-bottom:1px solid var(--border); font-family:var(--font-mono,monospace);">${vv != null ? _fmtNum(vv) : '--'}</td>`;
                }
                h += '</tr>';
            }
            h += '</tbody></table></div>';
        }
        return h;
    }

    if (!years.length) {
        el.innerHTML = '<div style="padding:12px; color:var(--danger);">Financial model extracted but no valid fiscal years found.</div>';
        return;
    }

    let html = '<div style="background:var(--bg-secondary); border:1px solid var(--border); border-radius:8px; padding:16px; margin-bottom:16px;">';
    html += `<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px;">
        <h4 style="margin:0; font-size:0.9rem; font-weight:600;">Financial Model Extracted</h4>
        <button onclick="togglePreRunFinancialEdit()" id="btn-prerun-edit"
            style="font-size:0.72rem;padding:4px 12px;border:1px solid var(--border-color,#e1e4e8);border-radius:4px;background:#fff;cursor:pointer;color:var(--text-secondary,#586069);font-weight:500;">
            &#9998; Edit Values
        </button>
    </div>`;
    html += `<p style="font-size:0.78rem; color:var(--text-secondary); margin:0 0 4px 0;">${fm.file_name} -- ${fm.records_count || 0} records, FY${years[0]}-${years[years.length-1]}</p>`;
    html += `<p style="font-size:0.72rem; color:var(--text-tertiary); margin:0 0 8px 0;">Values in USD</p>`;

    if (hasMultiScenario) {
        const SCENARIO_COLORS = {bear: '#dc3545', base: '#28a745', bull: '#007bff'};
        const SCENARIO_LABELS = {bear: 'Bear / Downside', base: 'Base / Management', bull: 'Bull / Upside'};
        html += '<div style="display:flex; gap:6px; margin-bottom:12px; flex-wrap:wrap;">';
        for (const sc of detectedScenarios) {
            const color = SCENARIO_COLORS[sc] || '#6c757d';
            html += `<span style="display:inline-block; padding:3px 10px; border-radius:12px; font-size:0.72rem; font-weight:600; background:${color}18; color:${color}; border:1px solid ${color}40;">${SCENARIO_LABELS[sc] || sc}</span>`;
        }
        html += '</div>';

        for (const sc of detectedScenarios) {
            const scFin = scenarios[sc]?.financials || {};
            const scUnits = scenarios[sc]?.units || {};
            if (!Object.keys(scFin).length && !Object.keys(scUnits).length) continue;
            const color = SCENARIO_COLORS[sc] || '#6c757d';
            const lbl = SCENARIO_LABELS[sc] || sc;
            html += `<div style="border-left:3px solid ${color}; padding-left:12px; margin-bottom:12px;">`;
            html += renderFinTable(scFin, scUnits, lbl);
            html += '</div>';
        }
    } else {
        html += renderFinTable(fm.financials, fm.units, null);
    }

    // Show extraction diagnostics if available
    if (fm._diagnostics) {
        const diag = fm._diagnostics;
        html += `<details style="margin-top:12px;font-size:0.72rem;"><summary style="cursor:pointer;font-weight:600;color:var(--text-secondary);">Extraction Diagnostics (sheet priority &amp; source mapping)</summary><div style="margin-top:6px;background:#fff;border:1px solid var(--border);border-radius:4px;padding:10px;max-height:400px;overflow:auto;">`;

        // Pipeline-level sheet diagnostics (shows ALL sheets, including skipped ones)
        if (diag.pipeline_sheet_diagnostics && diag.pipeline_sheet_diagnostics.length) {
            html += '<div style="font-weight:600;margin-bottom:4px;">All Sheets (pipeline-level):</div>';
            html += '<table style="width:100%;border-collapse:collapse;font-size:0.7rem;"><thead><tr><th style="text-align:left;padding:3px 6px;border-bottom:1px solid var(--border);">Sheet</th><th style="text-align:right;padding:3px 6px;border-bottom:1px solid var(--border);">Score</th><th style="text-align:left;padding:3px 6px;border-bottom:1px solid var(--border);">Status</th><th style="text-align:left;padding:3px 6px;border-bottom:1px solid var(--border);">Details</th></tr></thead><tbody>';
            const psd = diag.pipeline_sheet_diagnostics.sort((a,b) => b.score - a.score);
            for (const s of psd) {
                const color = s.score >= 100 ? '#28a745' : s.score <= 15 ? '#dc3545' : '#6c757d';
                const statusColor = s.status === 'processed' ? '#28a745' : s.status === 'skipped' ? '#dc3545' : '#6c757d';
                const details = s.status === 'skipped'
                    ? `Skipped: ${s.skipped_reason}`
                    : `Headers: ${s.period_headers}, Metrics: ${s.metric_rows} — ${(s.metrics_found||[]).join(', ')}`;
                html += `<tr><td style="padding:3px 6px;border-bottom:1px solid #f0f0f0;">${s.sheet}</td><td style="text-align:right;padding:3px 6px;border-bottom:1px solid #f0f0f0;color:${color};font-weight:600;">${s.score}</td><td style="padding:3px 6px;border-bottom:1px solid #f0f0f0;color:${statusColor};font-weight:500;">${s.status}</td><td style="padding:3px 6px;border-bottom:1px solid #f0f0f0;font-size:0.65rem;">${details}</td></tr>`;
            }
            html += '</tbody></table>';
            html += '<hr style="margin:8px 0;border:none;border-top:1px solid #eee;">';
        }

        if (diag.sheets_processed && Object.keys(diag.sheets_processed).length) {
            html += '<div style="font-weight:600;margin-bottom:4px;">Route-level dedup results:</div>';
            html += '<table style="width:100%;border-collapse:collapse;font-size:0.7rem;"><thead><tr><th style="text-align:left;padding:3px 6px;border-bottom:1px solid var(--border);">Sheet</th><th style="text-align:right;padding:3px 6px;border-bottom:1px solid var(--border);">Priority</th><th style="text-align:left;padding:3px 6px;border-bottom:1px solid var(--border);">Metrics Found</th></tr></thead><tbody>';
            const sorted = Object.entries(diag.sheets_processed).sort((a,b) => b[1].priority - a[1].priority);
            for (const [sheet, info] of sorted) {
                const metrics = Object.entries(info.metrics).map(([m,yrs]) => `${m}(${yrs.filter(y=>y).length}yr)`).join(', ');
                const color = info.priority >= 100 ? '#28a745' : info.priority <= 15 ? '#dc3545' : '#6c757d';
                html += `<tr><td style="padding:3px 6px;border-bottom:1px solid #f0f0f0;">${sheet}</td><td style="text-align:right;padding:3px 6px;border-bottom:1px solid #f0f0f0;color:${color};font-weight:600;">${info.priority}</td><td style="padding:3px 6px;border-bottom:1px solid #f0f0f0;">${metrics || 'none'}</td></tr>`;
            }
            html += '</tbody></table>';
        }
        if (diag.best_record_sources) {
            html += '<div style="margin-top:8px;font-weight:600;">Winning sources per metric:</div>';
            for (const [metric, records] of Object.entries(diag.best_record_sources)) {
                const sheets = [...new Set(records.map(r => r.sheet))];
                html += `<div style="margin:2px 0;"><span style="font-weight:500;">${metric}</span>: ${sheets.join(', ')}</div>`;
            }
        }
        // Show extraction failures
        if (diag.extraction_failures && diag.extraction_failures.length) {
            const formulaFails = diag.extraction_failures.filter(f => f.has_formulas);
            const otherFails = diag.extraction_failures.filter(f => !f.has_formulas);
            if (formulaFails.length) {
                html += `<div style="margin-top:8px;padding:6px;background:#fff3cd;border:1px solid #ffc107;border-radius:4px;"><strong style="color:#856404;">Formula cells detected:</strong> ${formulaFails.length} metric(s) have Excel formulas with no cached values. Re-save the file in Excel to fix.<ul style="margin:4px 0 0 16px;">`;
                for (const f of formulaFails) {
                    html += `<li>${f.metric} on "${f.sheet}"</li>`;
                }
                html += '</ul></div>';
            }
            if (otherFails.length) {
                html += `<div style="margin-top:6px;font-weight:600;">Extraction failures (${otherFails.length}):</div>`;
                for (const f of otherFails.slice(0, 10)) {
                    html += `<div style="margin:1px 0;color:#dc3545;">${f.metric} on "${f.sheet}": ${f.fail_code}</div>`;
                }
            }
        }
        html += '</div></details>';
    }

    html += '</div>';
    el.innerHTML = html;
}

function _fmtNum(v) {
    if (v == null) return '--';
    const n = Number(v);
    if (isNaN(n)) return String(v);
    if (Math.abs(n) >= 1e9) return (n / 1e9).toFixed(1) + 'B';
    if (Math.abs(n) >= 1e6) return (n / 1e6).toFixed(1) + 'M';
    if (Math.abs(n) >= 1e3) return (n / 1e3).toFixed(1) + 'K';
    return n % 1 === 0 ? n.toLocaleString() : n.toFixed(1);
}

function _fmtUsd(v) {
    if (v == null) return '--';
    const n = Number(v);
    if (isNaN(n)) return String(v);
    const abs = Math.abs(n);
    const sign = n < 0 ? '-' : '';
    if (abs >= 1e9) return sign + '$' + (abs / 1e9).toFixed(1) + 'B';
    if (abs >= 1e6) return sign + '$' + (abs / 1e6).toFixed(1) + 'M';
    if (abs >= 1e3) return sign + '$' + Math.round(abs / 1e3).toLocaleString() + 'K';
    if (abs > 0) return sign + '$' + abs.toFixed(0);
    return '$0';
}

function wizApplyAndConfigure() {
    const get = (id) => document.getElementById(id)?.value ?? '';

    document.getElementById('wiz-company-name').value = get('wiz-rev-name');
    const stage = get('wiz-rev-stage');
    if (stage) document.getElementById('wiz-entry-stage').value = stage;

    const tamRaw = parseFloat(get('wiz-rev-tam_claim'));
    if (tamRaw && tamRaw > 0) document.getElementById('wiz-tam').value = Math.round(tamRaw / 1000000) || tamRaw;

    const techDesc = get('wiz-rev-technology_description');
    const tdEl = document.getElementById('wiz-tech-desc');
    if (tdEl && techDesc) tdEl.value = techDesc;

    wizGoStep(3);
}

function _wizPopulateVolumes() {
    const row = document.getElementById('wiz-volume-row');
    if (!row) return;

    // Priority: saved inputs (from loaded report) > review step extraction > zero
    const savedVols = _wizSavedInputs?.volume?.year_volumes;

    // If inputs already exist, update their values and return
    if (row.children.length >= 10) {
        for (let i = 0; i < 10; i++) {
            const el = document.getElementById(`wiz-vol-${i}`);
            if (!el) continue;
            if (savedVols && savedVols[i] != null) {
                el.value = savedVols[i];
            }
        }
        return;
    }

    // Create inputs fresh using DOM API (avoids innerHTML reparse issues)
    row.innerHTML = '';
    for (let i = 0; i < 10; i++) {
        let val = 0;
        if (savedVols && savedVols[i] != null) {
            val = savedVols[i];
        } else {
            const revEl = document.getElementById(`wiz-rev-vol-${i}`);
            if (revEl) val = revEl.value;
        }
        const inp = document.createElement('input');
        inp.type = 'number';
        inp.id = `wiz-vol-${i}`;
        inp.value = val;
        inp.placeholder = `Y${i + 1}`;
        row.appendChild(inp);
    }
    // After building inputs, apply calendar year labels
    _wizUpdateVolumeLabels();
}

function _wizGetEntryYear() {
    const v = parseInt(document.getElementById('wiz-entry-year')?.value || 0);
    return (v >= 2010 && v <= 2040) ? v : new Date().getFullYear();
}

function _wizGetFundVintageYear() {
    const v = parseInt(document.getElementById('wiz-fund-vintage-year')?.value || 0);
    return (v >= 2010 && v <= 2040) ? v : _wizGetEntryYear();
}

function _wizUpdateVolumeLabels() {
    // Re-label volume input placeholders with actual calendar years
    const startYear = _wizGetEntryYear();
    for (let i = 0; i < 10; i++) {
        const el = document.getElementById(`wiz-vol-${i}`);
        if (el) el.placeholder = String(startYear + i);
    }
    // Update the year-range hint next to the section label
    const lbl = document.getElementById('wiz-volume-unit-label');
    if (lbl && !lbl.dataset.customText) {
        lbl.textContent = `(${startYear}–${startYear + 9})`;
    }
}

function _wizAutoCalcOwnership() {
    const check = parseFloat(document.getElementById('wiz-check-size')?.value || 0);
    const pre = parseFloat(document.getElementById('wiz-pre-money')?.value || 0);
    const roundSize = parseFloat(document.getElementById('wiz-round-size')?.value || 0);
    const effectiveRound = roundSize > 0 ? roundSize : check;
    const post = pre + effectiveRound;
    const own = post > 0 ? (check / post * 100).toFixed(2) : '0';
    const owEl = document.getElementById('wiz-ownership-display');
    if (owEl) owEl.textContent = own + '%';
}

let _wizPriorCount = 1;  // how many prior rounds are active

function _wizInvestTypeChanged() {
    const sel = document.getElementById('wiz-investment-type');
    const panel = document.getElementById('wiz-followon-panel');
    if (panel) panel.style.display = sel?.value === 'followon' ? 'block' : 'none';
    _wizUpdateExposure();
}

function _wizSetPriorCount(n) {
    _wizPriorCount = n;
    const btn1 = document.getElementById('wiz-prior-btn-1');
    const btn2 = document.getElementById('wiz-prior-btn-2');
    if (btn1) btn1.classList.toggle('btn-prior-active', n === 1);
    if (btn2) btn2.classList.toggle('btn-prior-active', n === 2);
    const card2 = document.getElementById('wiz-prior-card-2');
    if (card2) card2.style.display = n >= 2 ? '' : 'none';
    _wizUpdateExposure();
}

function _wizPriorTypeChanged(idx) {
    const type = document.getElementById(`wiz-prior-${idx}-type`)?.value || 'priced';
    const pricedDiv = document.getElementById(`wiz-prior-${idx}-priced`);
    const convDiv   = document.getElementById(`wiz-prior-${idx}-conv`);
    if (pricedDiv) pricedDiv.style.display = type === 'priced' ? '' : 'none';
    if (convDiv)   convDiv.style.display   = type !== 'priced' ? '' : 'none';
    _wizUpdateExposure();
}

function _wizConvOwnership(checkM, capM, discPct, preMoney) {
    /* Returns ownership fraction for a convertible at the current priced round.
       Investor gets the more-favourable (lower) price. */
    const capPre  = (capM  > 0) ? capM  : Infinity;
    const discPre = (discPct > 0) ? preMoney * (1 - discPct / 100) : Infinity;
    let effPre = Math.min(capPre, discPre);
    if (!isFinite(effPre) || effPre <= 0) effPre = preMoney;
    return effPre > 0 ? checkM / effPre : 0;
}

function _wizUpdateExposure() {
    const preMoney = parseFloat(document.getElementById('wiz-pre-money')?.value || 0);
    let totalPrior = 0;

    for (let i = 1; i <= _wizPriorCount; i++) {
        const check   = parseFloat(document.getElementById(`wiz-prior-${i}-check`)?.value || 0);
        const type    = document.getElementById(`wiz-prior-${i}-type`)?.value || 'priced';
        const preview = document.getElementById(`wiz-prior-${i}-preview`);

        if (check > 0) {
            totalPrior += check;
            let ownershipPct = null;

            if (type === 'priced') {
                const pre = parseFloat(document.getElementById(`wiz-prior-${i}-premoney`)?.value || 0);
                const rnd = parseFloat(document.getElementById(`wiz-prior-${i}-roundsize`)?.value || 0);
                if (pre > 0) {
                    const post = pre + (rnd > 0 ? rnd : check);
                    ownershipPct = (check / post * 100).toFixed(1);
                    if (preview) {
                        preview.textContent = `→ ${ownershipPct}% at entry`;
                        preview.style.display = '';
                    }
                }
            } else if (preMoney > 0) {
                const cap  = parseFloat(document.getElementById(`wiz-prior-${i}-cap`)?.value  || 0) || 0;
                const disc = parseFloat(document.getElementById(`wiz-prior-${i}-discount`)?.value || 0) || 0;
                ownershipPct = (_wizConvOwnership(check, cap, disc, preMoney) * 100).toFixed(1);
                const termStr = cap && disc ? `cap $${cap}M / ${disc}% disc`
                              : cap         ? `cap $${cap}M`
                              : disc        ? `${disc}% disc`
                              : 'face value';
                if (preview) {
                    preview.textContent = `→ ${ownershipPct}% at current round (${termStr})`;
                    preview.style.display = '';
                }
            }
        } else if (preview) {
            preview.style.display = 'none';
        }
    }

    const currentCheck = parseFloat(document.getElementById('wiz-check-size')?.value || 0);
    const totalExp     = totalPrior + currentCheck;
    const summaryEl    = document.getElementById('wiz-exposure-summary');
    if (summaryEl) summaryEl.style.display = totalPrior > 0 ? '' : 'none';
    const priorEl   = document.getElementById('wiz-prior-total');
    const currEl    = document.getElementById('wiz-current-total');
    const totalEl   = document.getElementById('wiz-total-exp');
    if (priorEl)   priorEl.textContent   = totalPrior   > 0 ? `$${totalPrior.toFixed(2)}M`   : '—';
    if (currEl)    currEl.textContent    = currentCheck > 0 ? `$${currentCheck.toFixed(2)}M`  : '—';
    if (totalEl)   totalEl.textContent   = totalExp     > 0 ? `$${totalExp.toFixed(2)}M`      : '—';
}

async function _wizExtractDealTerms() {
    const btn = document.getElementById('wiz-extract-terms-btn');
    const status = document.getElementById('wiz-extract-terms-status');
    const sessionId = _memo?.sessionId;
    if (!sessionId) {
        if (status) status.textContent = 'Upload a prior IC memo in the Investment Memo tab first.';
        return;
    }
    if (btn) btn.disabled = true;
    if (status) status.textContent = 'Extracting deal terms...';
    try {
        const r = await fetch(`/api/memo/documents/extract-deal-terms?session_id=${sessionId}`, {
            method: 'POST', headers: _rvmHeaders()
        });
        const d = await r.json();
        if (!r.ok) throw new Error(d.detail || 'Extraction failed');

        // Fill in form fields from extracted terms
        // Populate Prior Investment 1 fields
        if (d.check_size_m) document.getElementById('wiz-prior-1-check').value = d.check_size_m;
        if (d.pre_money_m)  document.getElementById('wiz-prior-1-premoney').value = d.pre_money_m;
        if (d.round_size_m) document.getElementById('wiz-prior-1-roundsize').value = d.round_size_m;
        if (d.entry_stage)  document.getElementById('wiz-prior-1-stage').value = d.entry_stage;
        if (d.fund_year)    document.getElementById('wiz-prior-1-year').value = d.fund_year;
        _wizUpdateExposure();

        const conf = d.confidence?.overall ? ` (${(d.confidence.overall * 100).toFixed(0)}% confidence)` : '';
        if (status) status.textContent = `Extracted from ${d._source_documents?.join(', ') || 'memo'}${conf}`;
        if (status) status.style.color = '#5B7744';
    } catch (e) {
        if (status) { status.textContent = e.message; status.style.color = '#dc2626'; }
    } finally {
        if (btn) btn.disabled = false;
    }
}

const _CARBON_DEFAULTS = {
    // range_improvement = CI improvement factor (how many times lower the new tech's CI is vs conventional).
    // Displacement fraction = 1 − 1/factor.  Use 1000 for near-zero-CI techs (solar, wind, nuclear).
    // Efficiency gains >1× absorbed into baseline_lifetime_prod (e.g. solar 1.15× → baseline×1.15).
    utility_solar: {displaced_resource:'US electricity', baseline_lifetime_prod:1725, range_improvement:1000, unit_definition:'MW installed capacity', specific_production_units:'MWh/MW/year', service_life:25},
    commercial_solar: {displaced_resource:'US electricity', baseline_lifetime_prod:1320, range_improvement:1000, unit_definition:'MW installed capacity', specific_production_units:'MWh/MW/year', service_life:25},
    residential_solar: {displaced_resource:'US electricity', baseline_lifetime_prod:1155, range_improvement:1000, unit_definition:'MW installed capacity', specific_production_units:'MWh/MW/year', service_life:25},
    onshore_wind: {displaced_resource:'US electricity', baseline_lifetime_prod:2500, range_improvement:1000, unit_definition:'MW installed capacity', specific_production_units:'MWh/MW/year', service_life:20},
    offshore_wind: {displaced_resource:'Global electricity', baseline_lifetime_prod:3800, range_improvement:1000, unit_definition:'MW installed capacity', specific_production_units:'MWh/MW/year', service_life:25},
    geothermal: {displaced_resource:'US electricity', baseline_lifetime_prod:7000, range_improvement:1000, unit_definition:'MW installed capacity', specific_production_units:'MWh/MW/year', service_life:30},
    battery_storage_utility: {displaced_resource:'Natural Gas (CCGT)', baseline_lifetime_prod:2000, range_improvement:6.667, unit_definition:'MWh storage capacity', specific_production_units:'MWh displaced/MWh/year', service_life:15},
    nuclear_smr: {displaced_resource:'Global electricity', baseline_lifetime_prod:8000, range_improvement:1000, unit_definition:'MW installed capacity', specific_production_units:'MWh/MW/year', service_life:40},
    ev_electrification: {displaced_resource:'Gasoline', baseline_lifetime_prod:12000, range_improvement:6.667, unit_definition:'vehicles', specific_production_units:'L gasoline displaced/vehicle/year', service_life:12},
    climate_software: {displaced_resource:'US electricity', baseline_lifetime_prod:50, range_improvement:1000, unit_definition:'enterprise customers', specific_production_units:'MWh saved/customer/year', service_life:10},
    industrial_decarb: {displaced_resource:'Natural Gas', baseline_lifetime_prod:500, range_improvement:1000, unit_definition:'industrial installations', specific_production_units:'MMBtu displaced/unit/year', service_life:15},
    ai_ml: {displaced_resource:'US electricity', baseline_lifetime_prod:10, range_improvement:1000, unit_definition:'enterprise deployments', specific_production_units:'MWh optimized/deployment/year', service_life:5},
    base_capital_intensive: {displaced_resource:'Natural Gas', baseline_lifetime_prod:500, range_improvement:1000, unit_definition:'industrial installations', specific_production_units:'MMBtu displaced/unit/year', service_life:15},
    base_software: {displaced_resource:'US electricity', baseline_lifetime_prod:50, range_improvement:1000, unit_definition:'enterprise customers', specific_production_units:'MWh saved/customer/year', service_life:10},
    base_sw_hw_hybrid: {displaced_resource:'Natural Gas (CCGT)', baseline_lifetime_prod:2000, range_improvement:6.667, unit_definition:'MWh storage capacity', specific_production_units:'MWh displaced/MWh/year', service_life:15},
    base_hard_tech: {displaced_resource:'Global electricity', baseline_lifetime_prod:8000, range_improvement:1000, unit_definition:'MW installed capacity', specific_production_units:'MWh/MW/year', service_life:40},
    custom: {displaced_resource:'US electricity', baseline_lifetime_prod:100, range_improvement:1.4, unit_definition:'units', specific_production_units:'', service_life:10},
};

function _wizOnArchetypeChange() {
    const arch = document.getElementById('wiz-archetype')?.value;
    if (!arch) return;

    const ad = ARCHETYPE_DEFAULTS[arch];
    if (ad) {
        const setVal = (id, v) => { const el = document.getElementById(id); if (el && v != null) el.value = v; };
        setVal('wiz-tam', ad.tam);
        setVal('wiz-pen-low', ad.pen_low);
        setVal('wiz-pen-high', ad.pen_high);
        setVal('wiz-mult-low', ad.mult_low);
        setVal('wiz-mult-high', ad.mult_high);
    }

    const cd = _CARBON_DEFAULTS[arch];
    if (cd) {
        const setVal = (id, v) => { const el = document.getElementById(id); if (el && v != null) el.value = v; };
        setVal('wiz-displaced-resource', cd.displaced_resource);
        setVal('wiz-baseline-prod', cd.baseline_lifetime_prod);
        setVal('wiz-range-imp', cd.range_improvement);
        setVal('wiz-specific-prod-units', cd.specific_production_units || '');
        setVal('wiz-unit-definition', cd.unit_definition || '');
        setVal('wiz-service-life', cd.service_life || 10);
    }

    _updateCarbonLabels();
}

function _updateCarbonLabels() {
    const arch = document.getElementById('wiz-archetype')?.value;
    const cd = _CARBON_DEFAULTS[arch] || _CARBON_DEFAULTS.custom;

    const unitDef = document.getElementById('wiz-unit-definition')?.value || cd.unit_definition || 'units';
    const specProd = document.getElementById('wiz-specific-prod-units')?.value || cd.specific_production_units || '';
    const baselineProd = parseFloat(document.getElementById('wiz-baseline-prod')?.value) || cd.baseline_lifetime_prod || 1;
    const rangeImp = parseFloat(document.getElementById('wiz-range-imp')?.value) || cd.range_improvement || 1;
    const displacedRes = document.getElementById('wiz-displaced-resource')?.value || cd.displaced_resource || '';
    const serviceLife = parseInt(document.getElementById('wiz-service-life')?.value) || cd.service_life || 10;

    // Update volume label
    const volLabel = document.getElementById('wiz-volume-unit-label');
    if (volLabel) volLabel.textContent = unitDef ? `(${unitDef})` : '';

    // Update baseline production label
    const baseLabel = document.getElementById('wiz-baseline-unit-label');
    if (baseLabel) baseLabel.textContent = specProd ? `(${specProd})` : '';

    // Update baseline help text
    const baseHelp = document.getElementById('wiz-baseline-help');
    if (baseHelp) {
        baseHelp.textContent = `Total ${displacedRes || 'resource'} displaced per ${unitDef || 'unit'} over ${serviceLife}-year service life`;
    }

    // Update conversion chain display
    const chainEl = document.getElementById('wiz-carbon-chain');
    const chainText = document.getElementById('wiz-carbon-chain-text');
    if (chainEl && chainText && displacedRes) {
        const displaced = (baselineProd * rangeImp).toLocaleString(undefined, {maximumFractionDigits:1});
        chainText.innerHTML = ` Volume (<em>${unitDef}</em>) &times; ${displaced} ${specProd ? '(' + specProd + ')' : ''} &times; CI (<em>tCO\u2082/${displacedRes}</em>) = <strong>tCO\u2082 avoided</strong>`;
        chainEl.style.display = 'block';
    } else if (chainEl) {
        chainEl.style.display = 'none';
    }
}

async function wizRunPipeline() {
    const btn = document.getElementById('wiz-run-btn');
    const status = document.getElementById('wiz-run-status');
    btn.disabled = true;
    status.className = 'wiz-status loading';
    status.textContent = 'Running analysis...';

    const vols = [];
    for (let i = 0; i < 10; i++) vols.push(parseFloat(document.getElementById(`wiz-vol-${i}`)?.value || 0));

    const founderRevs = [];
    for (let i = 0; i < 10; i++) {
        const el = document.getElementById(`wiz-rev-rev-${i}`);
        if (el) founderRevs.push(parseFloat(el.value || 0));
    }

    const founderVols = [];
    for (let i = 0; i < 10; i++) {
        const el = document.getElementById(`wiz-rev-vol-${i}`);
        if (el) founderVols.push(parseFloat(el.value || 0));
    }

    const multLow = document.getElementById('wiz-mult-low')?.value;
    const multHigh = document.getElementById('wiz-mult-high')?.value;

    const payload = {
        company_name: document.getElementById('wiz-company-name').value || 'Unnamed Deal',
        technology_description: document.getElementById('wiz-tech-desc')?.value || null,
        archetype: document.getElementById('wiz-archetype').value,
        tam_millions: parseFloat(document.getElementById('wiz-tam').value),
        trl: parseInt(document.getElementById('wiz-trl').value),
        entry_stage: document.getElementById('wiz-entry-stage').value,
        check_size_millions: parseFloat(document.getElementById('wiz-check-size').value),
        pre_money_millions: parseFloat(document.getElementById('wiz-pre-money').value),
        sector_profile: document.getElementById('wiz-sector').value,
        penetration_low: parseFloat(document.getElementById('wiz-pen-low').value),
        penetration_high: parseFloat(document.getElementById('wiz-pen-high').value),
        exit_multiple_low: multLow ? parseFloat(multLow) : null,
        exit_multiple_high: multHigh ? parseFloat(multHigh) : null,
        n_simulations: 5000,
        entry_year: _wizGetEntryYear(),
        fund_vintage_year: _wizGetFundVintageYear(),
        volume: {
            year_volumes: vols,
            commercial_launch_yr: _wizGetEntryYear(),
            unit_definition: document.getElementById('wiz-unit-definition')?.value || '',
            unit_service_life_yrs: parseInt(document.getElementById('wiz-service-life')?.value || 10),
        },
        op_carbon: {
            displaced_resource: document.getElementById('wiz-displaced-resource')?.value || null,
            baseline_lifetime_prod: parseFloat(document.getElementById('wiz-baseline-prod')?.value || 0),
            range_improvement: parseFloat(document.getElementById('wiz-range-imp')?.value || 1.0),
            specific_production_units: document.getElementById('wiz-specific-prod-units')?.value || '',
        },
        emb_carbon: {},
        portfolio: {},
        risk_divisor: null,
        founder_revenue_projections: founderRevs.length ? founderRevs : [],
        founder_volume_projections: founderVols.length ? founderVols : [],
        financial_model: _wizFmData ? {
            financials: _wizFmData.financials || {},
            units: _wizFmData.units || {},
            fiscal_years: _wizFmData.fiscal_years || [],
            model_summary: _wizFmData.model_summary || {},
            scenarios: _wizFmData.scenarios || null,
            detected_scenarios: _wizFmData.detected_scenarios || ['base'],
            primary_scenario: _wizFmData.primary_scenario || 'base',
        } : null,
        extraction_source: _wizExtraction?._source || null,
        extraction_confidence: _wizExtraction?.confidence || null,
        extraction_data: _wizExtraction ? ((() => {
            // Persist full extraction object so deck/FM data survives report reload
            try { return JSON.parse(JSON.stringify(_wizExtraction)); } catch(_) { return null; }
        })()) : null,
        fund_size_m: parseFloat(document.getElementById('wiz-fund-size')?.value || 135),
        n_deals: parseInt(document.getElementById('wiz-n-deals')?.value || 22),
        mgmt_fee_pct: parseFloat(document.getElementById('wiz-mgmt-fee')?.value || 2),
        reserve_pct: parseFloat(document.getElementById('wiz-reserve')?.value || 45),
        max_concentration_pct: parseFloat(document.getElementById('wiz-max-conc')?.value || 15),
        round_size_m: parseFloat(document.getElementById('wiz-round-size')?.value || 0) || null,
        // Follow-on optimization parameters
        investment_type: document.getElementById('wiz-investment-type')?.value || 'first',
        followon_round_size_m_actual: parseFloat(document.getElementById('wiz-fo-round-size')?.value || 0) || null,
        followon_fund_year: parseInt(document.getElementById('wiz-fo-fund-year')?.value || 0) || null,
        prior_investments: (() => {
            const invType = document.getElementById('wiz-investment-type')?.value;
            if (invType !== 'followon') return null;
            const items = [];
            for (let i = 1; i <= _wizPriorCount; i++) {
                const check = parseFloat(document.getElementById(`wiz-prior-${i}-check`)?.value || 0);
                if (!check || check <= 0) continue;
                const type  = document.getElementById(`wiz-prior-${i}-type`)?.value || 'priced';
                const stage = document.getElementById(`wiz-prior-${i}-stage`)?.value || 'Seed';
                const year  = parseInt(document.getElementById(`wiz-prior-${i}-year`)?.value || 1);
                const inv   = { type, check_m: check, stage, year };
                if (type === 'priced') {
                    const pre = parseFloat(document.getElementById(`wiz-prior-${i}-premoney`)?.value || 0);
                    const rnd = parseFloat(document.getElementById(`wiz-prior-${i}-roundsize`)?.value || 0);
                    inv.pre_money_m  = pre  > 0 ? pre  : null;
                    inv.round_size_m = rnd  > 0 ? rnd  : null;
                } else {
                    const cap  = parseFloat(document.getElementById(`wiz-prior-${i}-cap`)?.value  || 0);
                    const disc = parseFloat(document.getElementById(`wiz-prior-${i}-discount`)?.value || 0);
                    inv.cap_m        = cap  > 0 ? cap  : null;
                    inv.discount_pct = disc > 0 ? disc : null;
                }
                items.push(inv);
            }
            return items.length > 0 ? items : null;
        })(),
        save: true,
    };

    try {
        const r = await fetch('/api/deal-pipeline/run', {method: 'POST', headers: _rvmHeaders(), body: JSON.stringify(payload)});
        const d = await r.json();
        if (!r.ok) throw new Error(d.detail || 'Pipeline failed');
        _wizReport = d;
        _wizReportId = d.report_id;
        window._currentReportId = d.report_id;
        _wizSavedInputs = payload;
        status.className = 'wiz-status';
        status.textContent = '';
        btn.disabled = false;
        wizRenderReport(d);
        wizGoStep(4);
        wizLoadReports();
        showToast('Deal report generated');
        // Auto-run QA review after a short delay so the report renders first
        setTimeout(() => wizRunQAReview(d.report_id, null), 800);
    } catch(e) {
        status.className = 'wiz-status error';
        status.textContent = e.message;
        btn.disabled = false;
    }
}

function wizRenderReport(r) {
    const el = document.getElementById('deal-report');
    Object.values(_wizReportCharts).forEach(c => { try { c.destroy(); } catch(e) {} });
    _wizReportCharts = {};

    const ov = r.deal_overview || {};
    const hero = r.hero_metrics || {};
    const sim = r.simulation || {};
    const carbon = r.carbon_impact || {};
    const fComp = r.founder_comparison || {};
    const sens = r.sensitivity || {};
    const risk = r.risk_summary || {};
    const adoption = r.adoption_analysis || {};
    const valCtx = r.valuation_context || {};
    const audit = r.audit_trail || {};
    const pImpact = r.portfolio_impact || {};
    const prob = sim.probability || {};
    const moic = sim.moic_unconditional || {};
    const moicC = sim.moic_conditional || {};
    const irr = sim.irr_conditional || {};

    const fmt = (v, d=2) => v != null ? Number(v).toLocaleString(undefined, {minimumFractionDigits: d, maximumFractionDigits: d}) : 'N/A';
    const pctFmt = (v) => v != null ? (v * 100).toLocaleString(undefined, {minimumFractionDigits: 1, maximumFractionDigits: 1}) + '%' : 'N/A';
    const archLabel = (s) => (s||'').replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
    const signFmt = (v, d=3) => v != null ? (v >= 0 ? '+' : '') + Number(v).toLocaleString(undefined, {minimumFractionDigits: d, maximumFractionDigits: d}) : 'N/A';

    let html = '';
    let sectionNum = 0;
    const secNum = () => { sectionNum++; return `<div class="rpt-section-num">Section ${sectionNum}</div>`; };
    const trace = (title, body) => `<details class="rpt-trace"><summary class="rpt-trace-toggle">${title}</summary><div class="rpt-trace-body">${body}</div></details>`;

    const revTraj = sim.revenue_trajectories || {};
    const revSource = revTraj.source || 'scurve_derived';
    const fwdLook = revTraj.forward_look_years || 3;
    const fwdConf = revTraj.forward_confidence || 0.55;
    const trlImp = risk.trl_impact || {};
    const dilStats = risk.dilution || sim.dilution || {};

    // ── COVER / DEAL OVERVIEW ───────────────────────────────────────────────
    html += `<div class="rpt-cover">
        <h2 class="rpt-company">${ov.company_name || 'Deal Report'}</h2>
        ${ov.technology_description ? `<p class="rpt-tech-desc">${ov.technology_description}</p>` : ''}
        <div class="rpt-meta">
            <span>${ov.entry_stage || ''}</span>
            <span>${archLabel(ov.archetype)}</span>
            <span>TRL ${ov.trl} ${ov.trl_label ? '(' + ov.trl_label + ')' : ''}</span>
        </div>
        <div class="rpt-deal-terms">
            <div><span class="rpt-dt-label">Check Size ${infoTip('check_size')}</span><span class="rpt-dt-val">$${fmt(ov.check_size_millions,1)}M</span></div>
            <div><span class="rpt-dt-label">Round Size ${infoTip('round_size')}</span><span class="rpt-dt-val">$${fmt(ov.round_size_millions,1)}M</span></div>
            <div><span class="rpt-dt-label">Pre-Money ${infoTip('pre_money')}</span><span class="rpt-dt-val">$${fmt(ov.pre_money_millions,1)}M</span></div>
            <div><span class="rpt-dt-label">Post-Money ${infoTip('post_money')}</span><span class="rpt-dt-val">$${fmt(ov.post_money_millions,1)}M</span></div>
            <div><span class="rpt-dt-label">Entry Ownership ${infoTip('entry_ownership')}</span><span class="rpt-dt-val">${fmt(ov.entry_ownership_pct,1)}%</span></div>
        </div>
        ${trace('Deal terms derivation', `
            <p>Post-Money = Pre-Money + Round Size = $${fmt(ov.pre_money_millions,1)}M + $${fmt(ov.round_size_millions,1)}M = <strong>$${fmt(ov.post_money_millions,1)}M</strong></p>
            <p>Entry Ownership = Check / Post-Money = $${fmt(ov.check_size_millions,1)}M / $${fmt(ov.post_money_millions,1)}M = <strong>${fmt(ov.entry_ownership_pct,1)}%</strong></p>
            <p>Exit multiples: ${fmt(ov.exit_multiple_range?.[0],1)}x - ${fmt(ov.exit_multiple_range?.[1],1)}x EV/EBITDA. ${ov.comps_derived_multiples ? 'Damodaran/NYU comps, 20% acquisition haircut.' : 'User-specified.'}</p>
        `)}
        <div class="rpt-hero-row">
            <div class="rpt-hero-card accent"><div class="rpt-hero-num">${fmt(hero.expected_moic)}x</div><div class="rpt-hero-label">Expected MOIC ${infoTip('expected_moic')}</div></div>
            <div class="rpt-hero-card"><div class="rpt-hero-num">${pctFmt(hero.p_gt_3x)}</div><div class="rpt-hero-label">P(>3x) ${infoTip('p_gt_3x')}</div></div>
            <div class="rpt-hero-card"><div class="rpt-hero-num">${pctFmt(hero.expected_irr)}</div><div class="rpt-hero-label">Expected IRR ${infoTip('expected_irr')}</div></div>
            <div class="rpt-hero-card"><div class="rpt-hero-num">${pctFmt(hero.survival_rate)}</div><div class="rpt-hero-label">Survival Rate ${infoTip('survival_rate')}</div></div>
        </div>
        ${trace('Hero metrics methodology', `
            <p><strong>E[MOIC]</strong> = unconditional mean over ${(sim.n_simulations||5000).toLocaleString()} paths (~${pctFmt(prob.total_loss)} total-loss). Fund-returner = ~${fmt((ov.check_size_millions > 0 ? ((ov.fund_size_m || 100) / ov.check_size_millions) : 33), 0)}x ($${fmt(ov.check_size_millions,1)}M into $${fmt(ov.fund_size_m || 100, 0)}M fund).</p>
            <p><strong>P(>3x)</strong> = fraction of paths with MOIC >= 3.0.</p>
            <p><strong>E[IRR]</strong> = cashflow-based dollar-weighted IRR. Aggregates all ${(sim.n_simulations||5000).toLocaleString()} simulated paths into a single cashflow vector (invest at t=0, receive proceeds at each exit year) and solves for the discount rate where NPV = 0 (Newton's method). This weights outcomes by actual dollars returned rather than averaging per-path IRRs.</p>
            <p><strong>Survival Rate</strong> = P(MOIC > 0). Driven by TRL-adjusted stage graduation rates from dilution model.</p>
        `)}
    </div>`;

    // ── SECTION 1: DEAL METRICS / RETURN DISTRIBUTION ───────────────────────
    html += `<div class="rpt-section">${secNum()}
        <h3 class="rpt-section-title">Deal Metrics &amp; Return Distribution ${infoTip('deal_metrics')}</h3>
        <div class="rpt-prob-bar">
            <div class="rpt-prob-item"><span class="rpt-prob-val">${pctFmt(prob.total_loss)}</span><span class="rpt-prob-lbl">Total Loss ${infoTip('total_loss')}</span></div>
            <div class="rpt-prob-item"><span class="rpt-prob-val">${pctFmt(prob.gt_1x)}</span><span class="rpt-prob-lbl">&gt;1x ${infoTip('gt_1x')}</span></div>
            <div class="rpt-prob-item"><span class="rpt-prob-val">${pctFmt(prob.gt_3x)}</span><span class="rpt-prob-lbl">&gt;3x ${infoTip('gt_3x')}</span></div>
            <div class="rpt-prob-item"><span class="rpt-prob-val">${pctFmt(prob.gt_5x)}</span><span class="rpt-prob-lbl">&gt;5x ${infoTip('gt_5x')}</span></div>
            <div class="rpt-prob-item"><span class="rpt-prob-val">${pctFmt(prob.gt_10x)}</span><span class="rpt-prob-lbl">&gt;10x ${infoTip('gt_10x')}</span></div>
            <div class="rpt-prob-item"><span class="rpt-prob-val">${pctFmt(prob.gt_20x)}</span><span class="rpt-prob-lbl">&gt;20x ${infoTip('gt_20x')}</span></div>
        </div>
        <div class="rpt-two-col">
            <div class="rpt-chart-wrap"><h4 class="rpt-chart-title">MOIC Distribution (Survivors) ${infoTip('moic_distribution_chart')}</h4><canvas id="rpt-moic-chart" height="280"></canvas></div>
            <div class="rpt-chart-wrap"><h4 class="rpt-chart-title">Outcome Breakdown ${infoTip('outcome_breakdown_chart')}</h4><canvas id="rpt-outcome-chart" height="280"></canvas></div>
        </div>
        <table class="rpt-table">
            <thead><tr><th></th><th>Expected</th><th>P25</th><th>P50</th><th>P75</th><th>P90</th><th>P95</th></tr></thead>
            <tbody>
                <tr><td>MOIC (unconditional) ${infoTip('moic_unconditional')}</td><td class="rpt-num">${fmt(moic.expected)}x</td><td class="rpt-num">--</td><td class="rpt-num">${fmt(moic.p50_all)}x</td><td class="rpt-num">${fmt(moic.p75_all)}x</td><td class="rpt-num">${fmt(moic.p90_all)}x</td><td class="rpt-num">${fmt(moic.p95_all)}x</td></tr>
                <tr><td>MOIC (conditional on exit) ${infoTip('moic_conditional')}</td><td class="rpt-num">${fmt(moicC.mean)}x</td><td class="rpt-num">${fmt(moicC.p25)}x</td><td class="rpt-num">${fmt(moicC.p50)}x</td><td class="rpt-num">${fmt(moicC.p75)}x</td><td class="rpt-num">${fmt(moicC.p90)}x</td><td class="rpt-num">${fmt(moicC.p95)}x</td></tr>
                <tr><td>IRR (conditional on exit) ${infoTip('irr_conditional')}</td><td class="rpt-num">--</td><td class="rpt-num">${pctFmt(irr.p25)}</td><td class="rpt-num">${pctFmt(irr.p50)}</td><td class="rpt-num">${pctFmt(irr.p75)}</td><td class="rpt-num">${pctFmt(irr.p90)}</td><td class="rpt-num">${pctFmt(irr.p95)}</td></tr>
            </tbody>
        </table>
        <div class="rpt-ev-decomp">E[MOIC] = P(survive) x E[MOIC|survive] + P(loss) x 0 = ${pctFmt(sim.survival_rate)} x ${fmt(moicC.mean)}x = <strong>${fmt(moic.expected)}x</strong></div>
        ${trace('Return calculation methodology', `
            <p><strong>Revenue model</strong>: ${revSource === 'founder_anchored'
                ? 'FOUNDER-ANCHORED. Founder revenue as base trajectory, scaled per-path by Bass S-curve market factor. TRL ' + ov.trl + ' execution noise (lognormal).'
                : 'S-CURVE DERIVED. No founder revenue detected. Revenue = Bass adoption increments x penetration share U(' + fmt((ov.penetration_share||[])[0]||0.01,3) + ', ' + fmt((ov.penetration_share||[])[1]||0.05,3) + '). TRL ' + ov.trl + ' revenue lag applied.'
            }</p>
            <p><strong>Exit valuation</strong>: EV = EBITDA x exit_multiple. EBITDA = revenue x margin. Revenue basis = max(trailing_rev, projected_rev_${fwdLook}yr x ${(fwdConf*100).toFixed(0)}%). Exit year sampled from ${ov.exit_year_range?.[0] || 5}-${ov.exit_year_range?.[1] || 10} (Sahlman HBS VC Method).</p>
            <p><strong>Exit multiples</strong>: U(${fmt(ov.exit_multiple_range?.[0],1)}x, ${fmt(ov.exit_multiple_range?.[1],1)}x) EV/EBITDA. ${ov.comps_derived_multiples ? 'Damodaran/NYU comps, 20% acquisition haircut on IPO multiples.' : 'User-specified.'} TRL ${ov.trl} discount: ${fmt((1 - (trlImp.exit_multiple_discount || 1)) * 100, 0)}% → effective ${fmt(trlImp.effective_multiple_range?.[0],1)}x - ${fmt(trlImp.effective_multiple_range?.[1],1)}x.</p>
            <p><strong>EBITDA margin</strong>: Ramp from ${((sim.ebitda_margin?.margin_start || 0) * 100).toFixed(0)}% to ${((sim.ebitda_margin?.margin_end || 0.25) * 100).toFixed(0)}% over ${sim.ebitda_margin?.ramp_years || 6} years (TRL ${ov.trl}). Mean exit margin: ${((sim.ebitda_margin?.exit_margin_mean || 0) * 100).toFixed(1)}%.</p>
            <p><strong>Exit timing</strong>: Years ${ov.exit_year_range?.[0] || 5}-${ov.exit_year_range?.[1] || 10}, Gaussian-weighted, 1.5x boost years 4-7 (Cambridge Associates 2023).</p>
            <p><strong>Dilution</strong>: Round-by-round from ${ov.entry_stage}. Per-stage outcomes: graduate (dilute), stage exit (Carta post-money x acq multiple), or fail. Sector profile "${ov.sector_profile || 'DEFAULT'}": round sizes and post-money from Carta lognormal fits. TRL ${ov.trl} modifiers: ${fmt(trlImp.survival_penalty,0)}% survival penalty/stage, ${fmt(trlImp.capital_intensity_mult,2)}x capital intensity, ${fmt(trlImp.extra_bridge_prob,0)}% bridge probability.</p>
            <p><strong>Outcome types</strong>: (1) Full exit — all stages survived, forward-looking revenue valuation. (2) Stage exit — mid-stage M&A/IPO. (3) Partial recovery — acqui-hire/asset sale. (4) Late small exit. (5) Total loss.</p>
        `)}`;
    if (sim.variance_drivers) {
        html += `<h4 class="rpt-chart-title" style="margin-top:28px;">Variance Drivers ${infoTip('variance_drivers')}</h4><table class="rpt-table"><thead><tr><th>Driver</th><th>Contribution</th><th>Explanation</th></tr></thead><tbody>`;
        const vExp = sim.variance_explanations || {};
        for (const [k, v] of Object.entries(sim.variance_drivers)) {
            html += `<tr><td style="text-transform:capitalize">${k.replace(/_/g, ' ')}</td><td class="rpt-num">${fmt(v * 100, 1)}%</td><td>${typeof vExp[k] === 'object' ? (vExp[k]?.explanation || '') : (vExp[k] || '')}</td></tr>`;
        }
        html += '</tbody></table>';
    }
    html += '</div>';

    // ── SECTION 2: S-CURVE & FOUNDER COMPARISON ─────────────────────────────
    const scBp = adoption.scurve?.bass_p_mean;
    const scBq = adoption.scurve?.bass_q_mean;
    const adoptInfo = adoption.adoption_info || {};
    html += `<div class="rpt-section">${secNum()}
        <h3 class="rpt-section-title">Technology Adoption &amp; Founder Comparison ${infoTip('adoption_scurve')}</h3>
        <p class="rpt-narrative">S-curve adoption trajectories calibrated from NREL ATB deployment data using Bass diffusion (p=${scBp?.toFixed(4) || 'N/A'}, q=${scBq?.toFixed(3) || 'N/A'}). Revenue simulation cone shows P10-P90 range of ${(sim.n_simulations||5000).toLocaleString()} Monte Carlo paths.</p>
        ${trace('S-curve and revenue methodology', `
            <p><strong>Bass diffusion model</strong>: F(t) = M x (1 - e^(-(p+q)t)) / (1 + (q/p) x e^(-(p+q)t)), where M = TAM ($${fmt(ov.tam_millions || 0, 0)}M), p = ${scBp?.toFixed(5) || '?'} (innovation coefficient), q = ${scBq?.toFixed(4) || '?'} (imitation coefficient). Parameters drawn from NREL ATB historical deployment patterns for ${archLabel(ov.archetype)}.</p>
            <p><strong>Market maturity</strong>: ${adoptInfo.maturity || 'N/A'}. Inflection year: ${adoptInfo.inflection_year || 'N/A'}.</p>
            <p><strong>Revenue source</strong>: ${revSource === 'founder_anchored'
                ? 'FOUNDER-ANCHORED. Founder revenue projections are the base trajectory. S-curve Bass parameter draws create a fan of market scenarios that scale founder projections up/down. Beyond the founder projection window, extrapolates at per-path S-curve growth rates.'
                : 'S-CURVE DERIVED. No valid founder revenue detected. Revenue = annual adoption increments x penetration share (U(' + fmt((ov.penetration_share||[])[0]||0.01,3) + ', ' + fmt((ov.penetration_share||[])[1]||0.05,3) + ')), cumulated. TRL ' + ov.trl + ' revenue lag: ' + (trlImp.revenue_lag_years || '?') + ' years.'
            }</p>
            <p><strong>Divergence table</strong>: Founder projections vs. P25-P75 simulation band, year by year.</p>
        `)}
        <div class="rpt-two-col">
            <div class="rpt-chart-wrap"><h4 class="rpt-chart-title">Adoption S-Curve (${archLabel(ov.archetype)}) ${infoTip('adoption_scurve')}</h4><canvas id="rpt-scurve-chart" height="300"></canvas></div>
            <div class="rpt-chart-wrap"><h4 class="rpt-chart-title">Revenue Cone vs Founder Projections${fComp.scenario_revenue && Object.keys(fComp.scenario_revenue).length ? ' (w/ Scenarios)' : ''} ${infoTip('revenue_cone')}</h4><canvas id="rpt-revenue-cone-chart" height="300"></canvas></div>
        </div>`;
    if (fComp.has_data && fComp.revenue) {
        html += `<p class="rpt-narrative">${fComp.revenue.narrative || ''}</p>
        <table class="rpt-table"><thead><tr><th>Year</th><th>Founder ($M) ${infoTip('founder_projections')}</th><th>Sim Median ($M) ${infoTip('sim_median')}</th><th>Sim P25 ${infoTip('sim_p25')}</th><th>Sim P75 ${infoTip('sim_p75')}</th><th>Divergence ${infoTip('divergence')}</th><th>In Band ${infoTip('in_band')}</th></tr></thead><tbody>`;
        (fComp.revenue.year_by_year || []).forEach(y => {
            const cls = y.in_band ? '' : (y.divergence_pct > 0 ? ' class="rpt-above"' : ' class="rpt-below"');
            const yearLbl = y.calendar_year || `Y${y.year}`;
            html += `<tr${cls}><td>${yearLbl}</td><td class="rpt-num">${fmt(y.founder,1)}</td><td class="rpt-num">${fmt(y.simulated_median,1)}</td><td class="rpt-num">${fmt(y.simulated_p25,1)}</td><td class="rpt-num">${fmt(y.simulated_p75,1)}</td><td class="rpt-num">${y.divergence_pct > 0 ? '+' : ''}${fmt(y.divergence_pct,1)}%</td><td>${y.in_band ? 'Yes' : 'No'}</td></tr>`;
        });
        html += '</tbody></table>';
    }
    html += '</div>';

    // ── SECTION 3: PORTFOLIO IMPACT ─────────────────────────────────────────
    if (pImpact.has_data) {
        const liftCls = (v) => v > 0 ? 'rpt-lift-positive' : v < 0 ? 'rpt-lift-negative' : '';
        const nCommitted = pImpact.n_committed_deals || 0;
        const portfolioLabel = nCommitted > 0
            ? `Running Fund (${nCommitted} committed deal${nCommitted > 1 ? 's' : ''} + ${20 - nCommitted} simulated)`
            : 'Simulated Portfolio';
        html += `<div class="rpt-section">${secNum()}
            <h3 class="rpt-section-title">${portfolioLabel} Impact ${infoTip('portfolio_impact')}</h3>
            ${nCommitted > 0 ? `<div style="background:#f0f4ec;border:1px solid #5B7744;border-radius:6px;padding:8px 12px;margin-bottom:10px;font-size:12px;color:#3a5228;">
                <strong>Running portfolio mode:</strong> The baseline includes ${nCommitted} committed deal${nCommitted > 1 ? 's' : ''} replacing simulated company slots.
            </div>` : ''}
            <p class="rpt-narrative">${pImpact.narrative || ''}</p>
            ${trace('Portfolio simulation methodology', `
                <p><strong>Approach</strong>: 2,000 portfolio sims via VCSimulator. ${nCommitted > 0 ? `${nCommitted} committed deal(s) injected as fixed cash flows, replacing baseline company slots.` : 'Base portfolio from strategy.json.'} "With deal" adds this deal as an additional position.</p>
                <p><strong>Deal parameters</strong>: cap_multiple = ${fmt(moicC.mean,1)}x (conditional mean), success_prob = ${pctFmt(sim.survival_rate)}, exit_year = triangular(${ov.exit_year_range?.[0] || 5}, ${ov.exit_year_range?.[1] || 10}), check = $${fmt(ov.check_size_millions, 1)}M.</p>
                ${nCommitted > 0 ? '<p><strong>Note</strong>: Committed deals use their full MOIC distribution from the original Monte Carlo simulation for realistic variance.</p>' : '<p><strong>Limitation</strong>: Simulated portfolio, not actual holdings. No cross-asset correlation.</p>'}
            `)}
            <div class="rpt-impact-grid">
                <div class="rpt-impact-card">
                    <div class="rpt-impact-val">${fmt(pImpact.tvpi_base_mean)}x</div>
                    <div class="rpt-impact-sub">Base Fund ${infoTip('base_fund')}</div>
                    <div class="rpt-impact-lbl">TVPI (Mean) ${infoTip('tvpi_mean')}</div>
                </div>
                <div class="rpt-impact-card">
                    <div class="rpt-impact-val">${fmt(pImpact.tvpi_new_mean)}x</div>
                    <div class="rpt-impact-sub">With This Deal ${infoTip('with_deal')}</div>
                    <div class="rpt-impact-lbl">TVPI (Mean)</div>
                </div>
                <div class="rpt-impact-card lift">
                    <div class="rpt-impact-val ${liftCls(pImpact.tvpi_mean_lift)}">${signFmt(pImpact.tvpi_mean_lift)}x</div>
                    <div class="rpt-impact-sub">Marginal Lift ${infoTip('marginal_lift')}</div>
                    <div class="rpt-impact-lbl">TVPI Delta ${infoTip('tvpi_delta')}</div>
                </div>
            </div>
            <table class="rpt-table">
                <thead><tr><th>Metric</th><th>Base Portfolio</th><th>With Deal</th><th>Delta</th></tr></thead>
                <tbody>
                    <tr><td>TVPI (Mean)</td><td class="rpt-num">${fmt(pImpact.tvpi_base_mean)}x</td><td class="rpt-num">${fmt(pImpact.tvpi_new_mean)}x</td><td class="rpt-num ${liftCls(pImpact.tvpi_mean_lift)}">${signFmt(pImpact.tvpi_mean_lift)}x</td></tr>
                    <tr><td>TVPI (P50)</td><td class="rpt-num">${fmt(pImpact.tvpi_base_p50)}x</td><td class="rpt-num">${fmt(pImpact.tvpi_new_p50)}x</td><td class="rpt-num ${liftCls(pImpact.tvpi_new_p50 - pImpact.tvpi_base_p50)}">${signFmt(pImpact.tvpi_new_p50 - pImpact.tvpi_base_p50)}x</td></tr>
                    <tr><td>TVPI (P75)</td><td class="rpt-num">${fmt(pImpact.tvpi_base_p75)}x</td><td class="rpt-num">${fmt(pImpact.tvpi_new_p75)}x</td><td class="rpt-num ${liftCls(pImpact.tvpi_p75_lift)}">${signFmt(pImpact.tvpi_p75_lift)}x</td></tr>
                    <tr><td>IRR (Mean)</td><td class="rpt-num">${pctFmt(pImpact.irr_base_mean)}</td><td class="rpt-num">${pctFmt(pImpact.irr_new_mean)}</td><td class="rpt-num ${liftCls(pImpact.irr_mean_lift)}">${(pImpact.irr_mean_lift*100).toFixed(1)}pp</td></tr>
                    <tr><td>IRR (P50)</td><td class="rpt-num">${pctFmt(pImpact.irr_base_p50)}</td><td class="rpt-num">${pctFmt(pImpact.irr_new_p50)}</td><td class="rpt-num ${liftCls(pImpact.irr_new_p50 - pImpact.irr_base_p50)}">${((pImpact.irr_new_p50 - pImpact.irr_base_p50)*100).toFixed(1)}pp</td></tr>
                </tbody>
            </table>
        </div>`;
    }

    // ── SECTION 4: CARBON IMPACT ────────────────────────────────────────────
    const co = carbon.outputs || {};
    const ci = carbon.intermediates || {};
    const carbonRD = carbon.risk_divisor_used || '';
    const carbonRDSrc = carbon.risk_divisor_source || '';
    html += `<div class="rpt-section">${secNum()}
        <h3 class="rpt-section-title">Carbon Impact Assessment ${infoTip('carbon_impact')}</h3>
        <div class="rpt-hero-row compact">
            <div class="rpt-hero-card accent"><div class="rpt-hero-num">${fmt(co.company_tonnes, 0)}</div><div class="rpt-hero-label">Total Potential (10yr) ${infoTip('total_lifecycle_tco2')}</div></div>
            <div class="rpt-hero-card"><div class="rpt-hero-num">${fmt(co.volo_prorata, 0)}</div><div class="rpt-hero-label">Fund Pro-Rata Share ${infoTip('volo_prorata')}</div></div>
            <div class="rpt-hero-card"><div class="rpt-hero-num">${fmt(co.volo_risk_adj, 0)}</div><div class="rpt-hero-label">P-50 Risk-Adjusted ${infoTip('risk_adjusted_carbon')}</div></div>
            <div class="rpt-hero-card accent"><div class="rpt-hero-num">${fmt(co.risk_adj_tpd, 4)}</div><div class="rpt-hero-label">TCPI (t CO₂/$) ${infoTip('carbon_tpd')}</div></div>
        </div>
        ${trace('Carbon calculation chain', `
            <p><strong>Total lifecycle tCO2</strong> = Σ annual (operating + embodied) over projection period.</p>
            <p><strong>VoLo Pro-Rata</strong> = lifecycle tCO2 x ${fmt(ov.entry_ownership_pct,1)}% ownership.</p>
            <p><strong>P-50 Risk-Adjusted</strong> = fund pro-rata share / risk_divisor (${carbonRD}, ${carbonRDSrc}).</p>
            <p><strong>TCPI</strong> = P-50 risk-adjusted tonnes / $${fmt(ov.check_size_millions,1)}M check.</p>
            ${ci.total_operating != null ? `<p><strong>Intermediates</strong>: operating = ${fmt(ci.total_operating,0)} tCO2, embodied = ${fmt(ci.total_embodied,0)} tCO2.</p>` : ''}
            ${carbon.error ? `<p style="color:#dc2626;"><strong>Warning</strong>: ${carbon.error}</p>` : ''}
        `)}`;
    if (ci.annual_lifecycle?.length) {
        html += `<div class="rpt-chart-wrap" style="margin-top:8px;"><h4 class="rpt-chart-title">Annual Carbon Impact (tCO2) ${infoTip('annual_carbon_chart')}</h4><canvas id="rpt-carbon-chart" height="260"></canvas></div>`;
    }
    html += '</div>';

    // ── SECTION 5: SENSITIVITY ──────────────────────────────────────────────
    if (sens.tornado?.length) {
        const baseMoic = sens.base_moic || hero.expected_moic || 0;
        const baseP3x = sens.base_p3x || prob.gt_3x || 0;
        html += `<div class="rpt-section">${secNum()}
            <h3 class="rpt-section-title">Sensitivity Analysis ${infoTip('sensitivity')}</h3>
            <p class="rpt-narrative">${sens.narrative || ''}</p>
            ${trace('Sensitivity methodology', `
                <p><strong>Approach</strong>: One-at-a-time perturbation (+/- 20-30%), 1,000-path fast MC (same seed), measuring ΔE[MOIC] and ΔP(>3x).</p>
                <p><strong>Inputs tested</strong>: TAM (±20%), Check Size (±20%), Pre-Money (±20%), Penetration Low/High (±30%), Exit Multiple Low/High (±25%).</p>
                <p><strong>Spread</strong> = |MOIC_up − MOIC_down|.</p>
                <p><strong>Note</strong>: 1,000 sims (vs ${(sim.n_simulations||5000).toLocaleString()} main run). Sensitivity ranking is stable; point estimates have higher variance.</p>
            `)}
            <table class="rpt-table">
                <thead><tr><th>Input Variable</th><th>Base Value</th><th>-20% Scenario</th><th>MOIC Impact</th><th>+20% Scenario</th><th>MOIC Impact</th><th>Spread</th></tr></thead>
                <tbody>`;
        sens.tornado.forEach(t => {
            const spread = Math.abs((t.moic_up || 0) - (t.moic_down || 0));
            html += `<tr>
                <td style="font-weight:600;">${t.input}</td>
                <td class="rpt-num">${fmt(baseMoic, 2)}x</td>
                <td class="rpt-num">${fmt(baseMoic + (t.moic_down || 0), 2)}x</td>
                <td class="rpt-num" style="color:${(t.moic_down || 0) < 0 ? '#dc2626' : '#16a34a'};">${signFmt(t.moic_down, 2)}x</td>
                <td class="rpt-num">${fmt(baseMoic + (t.moic_up || 0), 2)}x</td>
                <td class="rpt-num" style="color:${(t.moic_up || 0) > 0 ? '#16a34a' : '#dc2626'};">${signFmt(t.moic_up, 2)}x</td>
                <td class="rpt-num" style="font-weight:600;">${fmt(spread, 2)}x</td>
            </tr>`;
        });
        html += `</tbody></table>
            <p class="rpt-narrative" style="margin-top:12px;">Base case: ${fmt(baseMoic, 2)}x expected MOIC, ${pctFmt(baseP3x)} probability of >3x return. Largest sensitivity is to ${sens.tornado[0]?.input || 'N/A'} with a ${fmt(Math.abs((sens.tornado[0]?.moic_up || 0) - (sens.tornado[0]?.moic_down || 0)), 2)}x spread.</p>
        </div>`;
    }

    // ── SECTION 6: VALUATION CONTEXT ────────────────────────────────────────
    const vcMatches = valCtx.matches || valCtx.industries || [];
    if (vcMatches.length) {
        html += `<div class="rpt-section">${secNum()}
            <h3 class="rpt-section-title">Valuation Context ${infoTip('valuation_context')}</h3>
            <p class="rpt-narrative">Simulation exit multiples: ${fmt(ov.exit_multiple_range?.[0],1)}x - ${fmt(ov.exit_multiple_range?.[1],1)}x EV/EBITDA (applied to projected exit-year EBITDA). Comps from Damodaran/NYU dataset. ${valCtx.multiples_source || ''}</p>
            <table class="rpt-table"><thead><tr><th>Industry</th><th>IPO EV/EBITDA</th><th>Acq EV/EBITDA (80% haircut)</th></tr></thead><tbody>`;
        vcMatches.forEach(m => {
            html += `<tr><td>${m.label || m.industry || ''}</td><td class="rpt-num">${fmt(m.ipo_ev_ebitda,1)}x</td><td class="rpt-num">${fmt(m.acq_ev_ebitda,1)}x</td></tr>`;
        });
        if (valCtx.ipo_ev_ebitda_mean) {
            html += `<tr style="font-weight:700;border-top:2px solid var(--border)"><td>Weighted Average</td><td class="rpt-num">${fmt(valCtx.ipo_ev_ebitda_mean,1)}x</td><td class="rpt-num">${fmt(valCtx.acq_ev_ebitda_mean,1)}x</td></tr>`;
        }
        html += '</tbody></table></div>';
    }

    // ── ENTERPRISE VALUE AT EXIT ─────────────────────────────────────────────
    const evData = sim.ev_at_exit || {};
    const ebMargin = sim.ebitda_margin || {};
    if (evData.mean_m) {
        html += `<div class="rpt-section">${secNum()}
            <h3 class="rpt-section-title">Enterprise Value at Exit ${infoTip('ev_at_exit')}</h3>
            <p class="rpt-narrative">Implied EV at exit across ${(sim.n_simulations || 5000).toLocaleString()} successful-exit paths. EV = Exit-Year EBITDA × EV/EBITDA Multiple. EBITDA derived from projected revenue × EBITDA margin (margin ramps from ${((ebMargin.margin_start || 0) * 100).toFixed(0)}% to ${((ebMargin.margin_end || 0.25) * 100).toFixed(0)}% over ${ebMargin.ramp_years || 6} years at TRL ${ov.trl}).</p>
            <div class="rpt-two-col">
                <div>
                    <h4 style="font-size:0.85rem;font-weight:600;margin-bottom:8px;">EV Distribution (Successful Exits) ${infoTip('ev_distribution')}</h4>
                    <table class="rpt-table">
                        <thead><tr><th>Percentile</th><th class="rpt-num">EV ($M)</th></tr></thead>
                        <tbody>
                            <tr><td>P25</td><td class="rpt-num">$${fmt(evData.p25_m,1)}M</td></tr>
                            <tr style="font-weight:600;background:var(--accent-light)"><td>P50 (Median)</td><td class="rpt-num">$${fmt(evData.p50_m,1)}M</td></tr>
                            <tr><td>Mean</td><td class="rpt-num">$${fmt(evData.mean_m,1)}M</td></tr>
                            <tr><td>P75</td><td class="rpt-num">$${fmt(evData.p75_m,1)}M</td></tr>
                            <tr><td>P90</td><td class="rpt-num">$${fmt(evData.p90_m,1)}M</td></tr>
                        </tbody>
                    </table>
                </div>
                <div>
                    <h4 style="font-size:0.85rem;font-weight:600;margin-bottom:8px;">EV Build-Up (Mean Path) ${infoTip('ev_buildup')}</h4>
                    <table class="rpt-table">
                        <thead><tr><th>Component</th><th class="rpt-num">Value</th></tr></thead>
                        <tbody>
                            <tr><td>Exit Revenue</td><td class="rpt-num">$${fmt(evData.exit_revenue_mean_m,1)}M</td></tr>
                            <tr><td>× EBITDA Margin</td><td class="rpt-num">${fmt(evData.exit_margin_mean_pct,1)}%</td></tr>
                            <tr style="border-top:1px solid var(--border)"><td>= Exit EBITDA</td><td class="rpt-num">$${fmt(evData.exit_ebitda_mean_m,1)}M</td></tr>
                            <tr><td>× EV/EBITDA Multiple</td><td class="rpt-num">${fmt(evData.exit_multiple_mean,1)}x</td></tr>
                            <tr style="font-weight:600;background:var(--accent-light);border-top:2px solid var(--border)"><td>= Enterprise Value</td><td class="rpt-num">$${fmt(evData.mean_m,1)}M</td></tr>
                        </tbody>
                    </table>
                </div>
            </div>
            ${trace('EV calculation methodology', `
                <p><strong>Revenue</strong>: Exit-year revenue from Monte Carlo simulation (founder-anchored or S-curve derived).</p>
                <p><strong>EBITDA margin</strong>: TRL-dependent ramp model. Starts at ${((ebMargin.margin_start || 0) * 100).toFixed(0)}% (TRL ${ov.trl} entry), linearly ramps to ${((ebMargin.margin_end || 0.25) * 100).toFixed(0)}% terminal margin over ${ebMargin.ramp_years || 6} years. Mean exit margin: ${fmt(evData.exit_margin_mean_pct,1)}%.</p>
                <p><strong>Exit multiple</strong>: EV/EBITDA drawn uniformly from ${fmt(ov.exit_multiple_range?.[0],1)}x–${fmt(ov.exit_multiple_range?.[1],1)}x, TRL-adjusted. Mean realized: ${fmt(evData.exit_multiple_mean,1)}x.</p>
                <p><strong>Enterprise Value</strong>: EV = exit_EBITDA × exit_multiple. Conditional on positive exit (excludes total losses).</p>
            `)}
        </div>`;
    }

    // ── SECTION 7: RISK ASSESSMENT ──────────────────────────────────────────
    const trlImpact = risk.trl_impact || {};
    html += `<div class="rpt-section">${secNum()}
        <h3 class="rpt-section-title">Risk Assessment ${infoTip('risk_assessment')}</h3>
        <table class="rpt-table">
            <thead><tr><th>Factor</th><th>Value</th><th>Impact on Model</th></tr></thead><tbody>
            <tr><td>Revenue Lag (S-curve fallback only)</td><td class="rpt-num">${trlImpact.revenue_lag_years ?? 'N/A'} years</td><td>Zero-revenue lag before S-curve revenue starts. ${revSource === 'founder_anchored' ? '<em>Not active — founder model anchors revenue.</em>' : ''}</td></tr>
            <tr><td>Survival Penalty</td><td class="rpt-num">${fmt(trlImpact.survival_penalty,1)}%</td><td>Additive penalty to Carta base failure rate per stage.</td></tr>
            <tr><td>Capital Intensity</td><td class="rpt-num">${fmt(trlImpact.capital_intensity_mult,2)}x</td><td>Multiplier on Carta round-size draws.</td></tr>
            <tr><td>Extra Bridge Probability</td><td class="rpt-num">${fmt(trlImpact.extra_bridge_prob,1)}%</td><td>Bridge round probability between priced rounds.</td></tr>
            <tr><td>EV/EBITDA Multiple Retention</td><td class="rpt-num">${fmt((trlImpact.exit_multiple_discount||1)*100,0)}%</td><td>${fmt(ov.exit_multiple_range?.[0],1)}x-${fmt(ov.exit_multiple_range?.[1],1)}x EV/EBITDA → ${fmt(trlImpact.effective_multiple_range?.[0],1)}x-${fmt(trlImpact.effective_multiple_range?.[1],1)}x EV/EBITDA (${fmt((1-(trlImpact.exit_multiple_discount||1))*100,0)}% TRL haircut).</td></tr>
            <tr><td>Forward-Look Window</td><td class="rpt-num">${fwdLook} years</td><td>Exit-year EV incorporates projected revenue ${fwdLook} years forward.</td></tr>
            <tr><td>Forward Confidence</td><td class="rpt-num">${(fwdConf*100).toFixed(0)}%</td><td>Discount applied to forward revenue projections at TRL ${ov.trl}.</td></tr>
        </tbody></table>
        ${trace('TRL modifier source', `
            <p>Fixed lookup table: TRL_MODIFIERS in dilution.py. TRL 1 = harshest penalties; TRL 9 = near-zero.</p>
            <p>Forward-look and confidence discount per Sahlman HBS VC Method.</p>
            <p><strong>Sector financing data</strong>: Carta sector-level lognormal fits ("${ov.sector_profile || 'DEFAULT'}"). Stage exit multiples, partial recovery rates, fail probabilities from Carta exit data ([ASSUMPTION]-tagged where calibration data is sparse).</p>
        `)}
    </div>`;

    // ── POSITION SIZING OPTIMIZATION ─────────────────────────────────────
    const ps = r.position_sizing || {};
    if (ps.has_data && ps.grid_search?.grid?.length) {
        const gsGrid = ps.grid_search.grid;
        const gsOptimal = ps.grid_search.optimal || {};
        const bestCheck = gsOptimal;
        const comp = ps.comparison || {};
        const kellyRef = ps.kelly_reference || {};
        const constraints = ps.fund_constraints || {};
        const stageWeights = ps.stage_weights_used || {};
        const fundSimOk = !!ps.grid_search.fund_sim_ok;

        let narrativeExtra = '';
        if (stageWeights.w_p10 !== undefined) narrativeExtra += ` Stage-calibrated weights: ${(stageWeights.w_p10*100).toFixed(0)}% Fund P10 / ${(stageWeights.w_p50*100).toFixed(0)}% Fund P50 / ${(stageWeights.w_p90*100).toFixed(0)}% Fund P90.`;
        if (ps.round_size_m) narrativeExtra += ` Round size cap: $${fmt(ps.round_size_m,1)}M.`;

        const pctChgFmt = (v) => v != null ? (v >= 0 ? '+' : '') + (v * 100).toFixed(2) + '%' : '-';

        html += `<div class="rpt-section">${secNum()}
            <h3 class="rpt-section-title">Check Size Optimization ${infoTip('check_optimization')}</h3>
            <p class="rpt-narrative">Optimal check size determined by sweeping $250K increments across the fund-constrained range ($${fmt(constraints.min_check_m,2)}M - $${fmt(constraints.max_check_m,2)}M).${fundSimOk ? ' At each level, VCSimulator runs the full fund with and without this deal to measure the % change in fund TVPI at P10, P50, and P90.' : ' Fund-level simulation unavailable; scoring based on deal-level dollar returns.'}${narrativeExtra}</p>
            ${trace('Optimizer methodology', `
                <p><strong>Objective</strong>: $250K-increment sweep. At each check size, run VCSimulator (2,000 portfolio paths) with and without deal. Measure % change in fund TVPI at P10, P50, P90.</p>
                <p><strong>Composite score</strong> = ${stageWeights.w_p10 !== undefined ? (stageWeights.w_p10*100).toFixed(0) : '30'}% × norm(Δ%P10) + ${stageWeights.w_p50 !== undefined ? (stageWeights.w_p50*100).toFixed(0) : '35'}% × norm(Δ%P50) + ${stageWeights.w_p90 !== undefined ? (stageWeights.w_p90*100).toFixed(0) : '35'}% × norm(Δ%P90) (min-max normalized). Weights stage-calibrated: earlier stages overweight fund downside protection (P10).</p>
                <p><strong>Fund constraints</strong>: Min = $${fmt(constraints.min_check_m,2)}M. Max = $${fmt(constraints.max_check_m,2)}M (${fmt(constraints.max_concentration_pct,0)}% concentration on $${fmt(constraints.fund_size_m || 0,0)}M fund). ${ps.round_size_m ? 'Round size cap: $' + fmt(ps.round_size_m,1) + 'M.' : ''}</p>
                <p><strong>Kelly reference</strong>: Full = $${fmt(kellyRef.optimal_check_m,2)}M. Half = $${fmt(kellyRef.half_kelly_check_m,2)}M. Shown as benchmark only.</p>
            `)}
            <div class="rpt-hero-row compact">
                <div class="rpt-hero-card accent"><div class="rpt-hero-num">$${fmt(bestCheck.check_m,2)}M</div><div class="rpt-hero-label">Fund-Optimized Check ${infoTip('fund_optimized_check')}</div></div>
                <div class="rpt-hero-card"><div class="rpt-hero-num">${fmt(bestCheck.ownership_pct,1)}%</div><div class="rpt-hero-label">Implied Ownership ${infoTip('implied_ownership')}</div></div>
                <div class="rpt-hero-card"><div class="rpt-hero-num">${pctChgFmt(bestCheck.fund_p50_pct_chg)}</div><div class="rpt-hero-label">Fund P50 Impact ${infoTip('fund_p50_impact')}</div></div>
                <div class="rpt-hero-card"><div class="rpt-hero-num">${fmt(bestCheck.fund_pct,1)}%</div><div class="rpt-hero-label">% of Fund ${infoTip('pct_of_fund')}</div></div>
            </div>
            <div class="rpt-two-col">
                <div class="rpt-chart-wrap"><h4 class="rpt-chart-title">Fund TVPI Impact by Check Size ${infoTip('fund_tvpi_impact_chart')}</h4><canvas id="rpt-sizing-chart" height="300"></canvas></div>
                <div class="rpt-chart-wrap"><h4 class="rpt-chart-title">Composite Score Curve ${infoTip('composite_score_chart')}</h4><canvas id="rpt-sizing-score-chart" height="300"></canvas></div>
            </div>
            <table class="rpt-table">
                <thead><tr><th>Check ($M)</th><th class="rpt-num">Own% ${infoTip('check_own_pct')}</th><th class="rpt-num">Fund Δ%P10 ${infoTip('fund_delta_p10')}</th><th class="rpt-num">Fund Δ%P50 ${infoTip('fund_delta_p50')}</th><th class="rpt-num">Fund Δ%P90 ${infoTip('fund_delta_p90')}</th><th class="rpt-num">E[MOIC] ${infoTip('opt_emoic')}</th><th class="rpt-num">P(Loss) ${infoTip('opt_ploss')}</th><th class="rpt-num">P(>3x) ${infoTip('opt_pgt3x')}</th><th class="rpt-num">Score ${infoTip('opt_score')}</th></tr></thead>
                <tbody>`;
        const step = Math.max(1, Math.floor(gsGrid.length / 12));
        gsGrid.forEach((g, i) => {
            const isOptimal = g.check_m === bestCheck.check_m;
            const isCurrent = Math.abs(g.check_m - (comp.current_check_m || 0)) < 0.13;
            const cls = isOptimal ? ' style="background:var(--accent-light);font-weight:600;"' : (isCurrent ? ' style="background:#fff3cd;"' : '');
            if (i % step === 0 || isOptimal || isCurrent) {
                const tag = isOptimal ? ' *' : (isCurrent ? ' (current)' : '');
                html += `<tr${cls}><td>$${fmt(g.check_m,2)}M${tag}</td><td class="rpt-num">${fmt(g.ownership_pct,1)}%</td><td class="rpt-num">${pctChgFmt(g.fund_p10_pct_chg)}</td><td class="rpt-num">${pctChgFmt(g.fund_p50_pct_chg)}</td><td class="rpt-num">${pctChgFmt(g.fund_p90_pct_chg)}</td><td class="rpt-num">${fmt(g.ev_moic,2)}x</td><td class="rpt-num">${pctFmt(g.p_loss)}</td><td class="rpt-num">${pctFmt(g.p_gt3x)}</td><td class="rpt-num">${fmt(g.composite_score,3)}</td></tr>`;
            }
        });
        html += `</tbody></table>
            <table class="rpt-table" style="margin-top:12px;">
                <thead><tr><th>Sizing Method</th><th class="rpt-num">Check Size</th><th>Notes</th></tr></thead>
                <tbody>
                    <tr style="font-weight:600;background:var(--accent-light)"><td>Fund-Performance Optimizer</td><td class="rpt-num">$${fmt(bestCheck.check_m,2)}M</td><td>Maximizes composite Δ% in fund TVPI P10/P50/P90 at $250K granularity</td></tr>
                    <tr><td>Full Kelly Criterion</td><td class="rpt-num">$${fmt(kellyRef.optimal_check_m,2)}M</td><td>Theoretical log-wealth maximizer (high volatility)</td></tr>
                    <tr><td>Half Kelly</td><td class="rpt-num">$${fmt(kellyRef.half_kelly_check_m,2)}M</td><td>~75% of Kelly growth, substantially lower drawdown</td></tr>
                    <tr><td>Fund Average</td><td class="rpt-num">$${fmt(constraints.avg_check_m,2)}M</td><td>Investable capital / ${constraints.n_deals || 25} deals</td></tr>
                    <tr><td>Fund Maximum</td><td class="rpt-num">$${fmt(constraints.max_check_m,2)}M</td><td>${fmt(constraints.max_concentration_pct,0)}% concentration limit</td></tr>
                    <tr><td>Current</td><td class="rpt-num">$${fmt(comp.current_check_m,2)}M</td><td>As specified in deal terms</td></tr>
                </tbody>
            </table>
        </div>`;
    }

    // ── FINANCIAL MODEL TRACEABILITY ────────────────────────────────────────
    const fm = r.financial_model || {};
    if (fm.has_data && fm.financials && Object.keys(fm.financials).length) {
        const fmYears = fm.fiscal_years || [];
        const fmFin = fm.financials || {};
        const fmUnits = fm.units || {};
        const _reportId = window._currentReportId || _wizReportId || 0;
        html += `<div class="rpt-section">${secNum()}
            <h3 class="rpt-section-title" style="display:flex;align-items:center;justify-content:space-between;">
                <span>Financial Model Traceability ${infoTip('financial_traceability')}</span>
                <button onclick="toggleFinancialEdit(${_reportId})" id="btn-edit-financials"
                    style="font-size:0.75rem;padding:5px 14px;border:1px solid var(--border-color,#e1e4e8);border-radius:4px;background:var(--bg-secondary,#f6f8fa);cursor:pointer;color:var(--text-secondary,#586069);font-weight:500;white-space:nowrap;">
                    &#9998; Edit Financials
                </button>
            </h3>
            <p class="rpt-narrative">Financial projections extracted from <strong>${fm.file_name || audit.financial_model_file || 'uploaded model'}</strong>. ${fm.records_count ? fm.records_count + ' data points across ' : ''}FY${fmYears[0] || '?'}-FY${fmYears[fmYears.length-1] || '?'}. Values in USD.${fm.model_summary?.manually_edited ? ' <span style="color:#e36209;font-weight:600;">&#9998; Manually corrected.</span>' : ''}</p>
            ${trace('Extraction methodology', `
                <p><strong>Source</strong>: ${fm.file_name || 'Uploaded Excel file'}. Scale: ${fm.scale_info || 'USD'}.</p>
                <p><strong>Extraction</strong>: Regex label matching (curated synonym dictionary) across all sheets. Period headers detected from row/column structure.</p>
                <p><strong>Scenarios</strong>: ${fm.has_multi_scenario ? 'Detected: ' + (fm.detected_scenarios || []).join(', ') + '. Primary: ' + (fm.primary_scenario || 'base') + '.' : 'Single scenario.'}</p>
                <p><strong>Simulation link</strong>: ${revSource === 'founder_anchored'
                    ? 'Revenue (converted to $M) anchors the Monte Carlo. ' + (sim.n_simulations||5000).toLocaleString() + ' paths generated around these values with S-curve uncertainty envelope.'
                    : 'Extracted revenue was zero or absent — simulation fell back to S-curve-derived revenue.'
                }</p>
                <p>Values below are raw extractions from the uploaded file. No interpolation or estimation. Empty/unreadable cells show "--".</p>
            `)}`;
        if (fmYears.length && Object.keys(fmFin).length) {
            html += '<table class="rpt-table" id="fm-financials-table"><thead><tr><th>Metric</th>';
            fmYears.forEach(y => html += `<th class="rpt-num">${y}</th>`);
            html += '</tr></thead><tbody>';
            for (const [metric, series] of Object.entries(fmFin)) {
                // Skip metrics where every value is null or zero (e.g. capex when not extracted)
                const hasVals = fmYears.some(y => {
                    const v = series[String(y)] ?? series[y];
                    return v != null && v !== 0;
                });
                if (!hasVals) continue;
                html += `<tr data-metric="${metric}"><td style="text-transform:capitalize;font-weight:500;">${metric.replace(/_/g,' ')}</td>`;
                fmYears.forEach(y => {
                    const v = series[String(y)] ?? series[y];
                    html += `<td class="rpt-num fm-cell" data-metric="${metric}" data-year="${y}">${v != null ? _fmtUsd(v) : '--'}</td>`;
                });
                html += '</tr>';
            }
            html += '</tbody></table>';
            html += `<div id="fm-edit-actions" style="display:none;margin-top:10px;text-align:right;">
                <button onclick="cancelFinancialEdit()" style="padding:6px 16px;margin-right:8px;border:1px solid var(--border-color,#e1e4e8);border-radius:4px;background:var(--bg-secondary,#f6f8fa);cursor:pointer;font-size:0.8rem;">Cancel</button>
                <button onclick="saveFinancialEdit(${_reportId})" style="padding:6px 16px;border:none;border-radius:4px;background:#28a745;color:#fff;cursor:pointer;font-size:0.8rem;font-weight:600;">Save Changes</button>
            </div>`;
        }
        if (Object.keys(fmUnits).length) {
            html += '<h4 style="margin-top:16px; font-size:0.9rem; font-weight:600;">Unit Metrics</h4>';
            html += '<table class="rpt-table"><thead><tr><th>Metric</th>';
            fmYears.forEach(y => html += `<th class="rpt-num">${y}</th>`);
            html += '</tr></thead><tbody>';
            for (const [metric, series] of Object.entries(fmUnits)) {
                const hasVals = fmYears.some(y => {
                    const e = series[String(y)] ?? series[y];
                    return (typeof e === 'object' ? e?.value : e) != null;
                });
                if (!hasVals) continue;
                const firstE = Object.values(series).find(e => e != null);
                const unitLbl = (typeof firstE === 'object' && firstE?.unit_type) ? ` (${firstE.unit_type})` : '';
                html += `<tr><td style="text-transform:capitalize;font-weight:500;">${metric.replace(/_/g,' ')}${unitLbl}</td>`;
                fmYears.forEach(y => {
                    const e = series[String(y)] ?? series[y];
                    const v = typeof e === 'object' ? e?.value : e;
                    html += `<td class="rpt-num">${v != null ? Number(v).toLocaleString(undefined, {maximumFractionDigits: 1}) : '--'}</td>`;
                });
                html += '</tr>';
            }
            html += '</tbody></table>';
        }

        if (fm.has_multi_scenario && fm.scenarios) {
            const SCENARIO_LABELS = {bear: 'Bear / Downside', base: 'Base / Management', bull: 'Bull / Upside'};
            const SCENARIO_COLORS = {bear: '#dc3545', base: '#28a745', bull: '#007bff'};
            html += '<h4 style="margin-top:20px; font-size:0.9rem; font-weight:600;">Scenario Comparison</h4>';
            html += `<p class="rpt-narrative">The financial model contains ${(fm.detected_scenarios || []).length} scenarios: ${(fm.detected_scenarios || []).map(s => SCENARIO_LABELS[s] || s).join(', ')}. Primary scenario used: <strong>${SCENARIO_LABELS[fm.primary_scenario] || fm.primary_scenario}</strong>.</p>`;

            const allMetrics = new Set();
            for (const sc of (fm.detected_scenarios || [])) {
                const scFin = fm.scenarios[sc]?.financials || {};
                Object.keys(scFin).forEach(m => allMetrics.add(m));
            }

            if (allMetrics.size && fmYears.length) {
                html += '<table class="rpt-table"><thead><tr><th>Metric</th><th>Scenario</th>';
                fmYears.forEach(y => html += `<th class="rpt-num">${y}</th>`);
                html += '</tr></thead><tbody>';
                for (const metric of allMetrics) {
                    let isFirst = true;
                    for (const sc of (fm.detected_scenarios || [])) {
                        const scFin = fm.scenarios[sc]?.financials || {};
                        const series = scFin[metric];
                        if (!series) continue;
                        const color = SCENARIO_COLORS[sc] || '#6c757d';
                        html += `<tr>`;
                        if (isFirst) {
                            const rowspan = (fm.detected_scenarios || []).filter(s => fm.scenarios[s]?.financials?.[metric]).length;
                            html += `<td style="text-transform:capitalize;font-weight:500;" rowspan="${rowspan}">${metric.replace(/_/g,' ')}</td>`;
                            isFirst = false;
                        }
                        html += `<td style="color:${color};font-weight:600;font-size:0.78rem;">${SCENARIO_LABELS[sc] || sc}</td>`;
                        fmYears.forEach(y => {
                            const v = series[String(y)] ?? series[y];
                            html += `<td class="rpt-num">${v != null ? _fmtUsd(v) : '--'}</td>`;
                        });
                        html += '</tr>';
                    }
                }
                html += '</tbody></table>';
            }
        }

        html += '</div>';
    }

    // ── AUDIT TRAIL ─────────────────────────────────────────────────────────
    html += `<div class="rpt-section rpt-audit">${secNum()}
        <h3 class="rpt-section-title">Audit Trail &amp; Model Configuration ${infoTip('audit_trail')}</h3>
        <div class="rpt-audit-grid">
            <div><span class="rpt-audit-k">Simulations</span><span class="rpt-audit-v">${(audit.n_simulations || 5000).toLocaleString()} paths</span></div>
            <div><span class="rpt-audit-k">Random Seed</span><span class="rpt-audit-v">${audit.random_seed || 'Auto'}</span></div>
            <div><span class="rpt-audit-k">Revenue Model</span><span class="rpt-audit-v">${revSource === 'founder_anchored' ? 'Founder-anchored (financial model drives trajectory)' : 'S-curve derived (no financial model provided)'}</span></div>
            <div><span class="rpt-audit-k">Exit EV/EBITDA Multiples</span><span class="rpt-audit-v">${fmt(audit.exit_multiple_range?.[0],1)}x - ${fmt(audit.exit_multiple_range?.[1],1)}x EV/EBITDA (effective after TRL discount: ${fmt(trlImp.effective_multiple_range?.[0],1)}x - ${fmt(trlImp.effective_multiple_range?.[1],1)}x)</span></div>
            <div><span class="rpt-audit-k">Forward Valuation</span><span class="rpt-audit-v">${fwdLook}-year look-ahead at ${(fwdConf*100).toFixed(0)}% confidence (TRL ${ov.trl})</span></div>
            <div><span class="rpt-audit-k">Exit Year Weighting</span><span class="rpt-audit-v">Gaussian centered on yr ${((ov.exit_year_range?.[0]||5) + (ov.exit_year_range?.[1]||10)) / 2}, 1.5x boost for years 4-7</span></div>
            <div><span class="rpt-audit-k">Dilution Data</span><span class="rpt-audit-v">Carta sector: ${ov.sector_profile || 'DEFAULT (ALL)'}</span></div>
            <div><span class="rpt-audit-k">Carbon Risk Divisor</span><span class="rpt-audit-v">${audit.risk_divisor} (${carbonRDSrc})</span></div>
            <div><span class="rpt-audit-k">Computation Time</span><span class="rpt-audit-v">${fmt(audit.computation_time_ms, 0)}ms</span></div>
            ${audit.extraction_source ? `<div><span class="rpt-audit-k">Deck Extraction</span><span class="rpt-audit-v">${audit.extraction_source}</span></div>` : ''}
            ${audit.financial_model_file ? `<div><span class="rpt-audit-k">Financial Model File</span><span class="rpt-audit-v">${audit.financial_model_file} (${audit.financial_model_scale || 'USD'})</span></div>` : ''}
        </div>
        ${trace('Full model specification', `
            <p><strong>Layer 1 — Adoption</strong>: Bass diffusion S-curves (NREL ATB). ${(sim.n_simulations||5000).toLocaleString()} draws of (p, q) from Normal(${scBp?.toFixed(5) || '?'}, std) and Normal(${scBq?.toFixed(4) || '?'}, std), clipped to [0.0005, 0.05] and [0.02, 0.8]. TAM = $${fmt(ov.tam_millions || 0,0)}M.</p>
            <p><strong>Layer 2 — Revenue</strong>: ${revSource === 'founder_anchored' ? 'Founder projections (from Excel extraction) as base. Market-driven scaling factor = each path\'s adoption / median adoption at reference year. TRL-calibrated execution noise (lognormal, sigma varies by TRL). Beyond founder window: extrapolate at S-curve growth rates.' : 'Annual market additions x penetration share (uniform ' + fmt((ov.penetration_share||[])[0]||0.01,3) + ' to ' + fmt((ov.penetration_share||[])[1]||0.05,3) + '), cumulated. TRL ' + ov.trl + ' lag = ' + (trlImp.revenue_lag_years||'?') + ' years.'}</p>
            <p><strong>Layer 3 — Dilution</strong>: Stage-by-stage from ${ov.entry_stage}. At each stage, three outcomes: graduate (raise next round), mid-stage exit (acquisition/IPO), or failure. Round sizes and post-money from Carta lognormal fits (sector: ${ov.sector_profile}). TRL modifiers: ${fmt(trlImp.survival_penalty,0)}% survival penalty, ${fmt(trlImp.capital_intensity_mult,2)}x capital intensity, ${fmt(trlImp.extra_bridge_prob,0)}% bridge prob.</p>
            <p><strong>Layer 4 — Exit valuation</strong>: EV = EBITDA x exit_multiple x final_ownership. EBITDA = revenue x margin (margin ramps ${((sim.ebitda_margin?.margin_start || 0)*100).toFixed(0)}% → ${((sim.ebitda_margin?.margin_end || 0.25)*100).toFixed(0)}% over ${sim.ebitda_margin?.ramp_years || 6} years for TRL ${ov.trl}). Revenue basis: max(trailing_rev, projected_rev_${fwdLook}yr x ${(fwdConf*100).toFixed(0)}%). EV/EBITDA multiples: ${fmt(trlImp.effective_multiple_range?.[0],1)}x - ${fmt(trlImp.effective_multiple_range?.[1],1)}x (TRL-adjusted). Stage exits valued from Carta post-money x acq_multiple, capped at forward-looking EV. Partials and late exits use EBITDA ceiling.</p>
            <p><strong>Layer 5 — Returns</strong>: MOIC = gross_proceeds / check. Total losses → MOIC 0.</p>
            <p><strong>Determinism</strong>: Seed ${audit.random_seed || 'auto'} — deterministic and reproducible.</p>
        `)}
    </div>`;

    // ── COMMIT TO FUND ACTION ─────────────────────────────────────────────
    if (_wizReportId) {
        html += `<div class="rpt-section rpt-fund-actions" style="text-align:center;padding:24px 0;">
            <div style="display:flex;justify-content:center;align-items:center;gap:16px;margin-bottom:12px;flex-wrap:wrap;">
                <label style="display:flex;align-items:center;gap:4px;cursor:pointer;font-size:13px;">
                    <input type="radio" name="commit-type" value="first_check" checked onchange="document.getElementById('fo-year-group').style.display='none';document.getElementById('fo-parent-group').style.display='none';">
                    <strong>First Check</strong>
                    <span style="color:#586069;font-size:11px;">(displaces baseline slot)</span>
                </label>
                <label style="display:flex;align-items:center;gap:4px;cursor:pointer;font-size:13px;">
                    <input type="radio" name="commit-type" value="follow_on" onchange="document.getElementById('fo-year-group').style.display='flex';document.getElementById('fo-parent-group').style.display='flex';">
                    <strong>Follow-On</strong>
                    <span style="color:#586069;font-size:11px;">(from reserve pool)</span>
                </label>
            </div>
            <div id="fo-year-group" style="display:none;justify-content:center;align-items:center;gap:8px;margin-bottom:8px;">
                <label style="font-size:12px;color:#586069;">Deploy Year:</label>
                <select id="fo-year-select" style="padding:4px 8px;border:1px solid #d1d5db;border-radius:4px;font-size:12px;">
                    <option value="2">Year 2</option>
                    <option value="3">Year 3</option>
                    <option value="4">Year 4</option>
                </select>
            </div>
            <div id="fo-parent-group" style="display:none;justify-content:center;align-items:center;gap:8px;margin-bottom:8px;">
                <label style="font-size:12px;color:#586069;">Parent Deal:</label>
                <select id="fo-parent-select" style="padding:4px 8px;border:1px solid #d1d5db;border-radius:4px;font-size:12px;">
                    <option value="">— none —</option>
                </select>
            </div>
            <button id="btn-commit-fund" class="btn btn-primary" style="font-size:15px;padding:10px 28px;" onclick="wizCommitToFund()">
                Commit to Fund
            </button>
            <p style="color:#586069;font-size:12px;margin-top:8px;">
                Add this deal to your running fund portfolio. Future analyses will run against the updated fund.
            </p>
        </div>`;
    }

    // ── FUND COMMITMENTS PANEL ──────────────────────────────────────────
    html += `<div id="fund-commitments-panel" class="rpt-section" style="display:none;"></div>`;

    el.innerHTML = html;
    setTimeout(() => _wizRenderCharts(r), 80);
    // Load and display fund commitments
    _wizLoadFundCommitments();
}

function _wizRenderCharts(r) {
    const sim = r.simulation || {};
    const carbon = r.carbon_impact || {};
    const adoption = r.adoption_analysis || {};
    const fComp = r.founder_comparison || {};
    const sens = r.sensitivity || {};
    const ci = carbon.intermediates || {};
    const chartFont = {family: "'Inter', system-ui, sans-serif", size: 11};
    const _c = (r,g,b,a) => `rgba(${r},${g},${b},${a})`;
    const BLU = _c(34,87,122,1);
    const chartOpts = (extra={}) => ({responsive:true, maintainAspectRatio:false, ...extra});

    // 1. MOIC histogram
    const hist = sim.moic_histogram;
    if (hist) {
        const ctx = document.getElementById('rpt-moic-chart');
        if (ctx) {
            const moicU = sim.moic_unconditional || {};
            const pLines = [];
            if (moicU.p50_all != null) pLines.push({val: moicU.p50_all, label: 'P50', color: '#6B7280'});
            if (moicU.p75_all != null) pLines.push({val: moicU.p75_all, label: 'P75', color: VOLO.green});
            if (moicU.p90_all != null) pLines.push({val: moicU.p90_all, label: 'P90', color: VOLO.sage});

            _wizReportCharts.moic = new Chart(ctx, {
                type: 'bar',
                data: {
                    labels: hist.bins?.map(b => b.toFixed(1) + 'x') || [],
                    datasets: [{
                        label: 'Frequency', data: hist.counts || [],
                        backgroundColor: 'rgba(34,87,122,0.7)', borderRadius: 2, barPercentage: 0.95
                    }]
                },
                options: {
                    responsive: true, maintainAspectRatio: false,
                    plugins: {
                        legend: {display: false},
                        annotation: pLines.length ? {
                            annotations: Object.fromEntries(pLines.map((p, i) => ['p'+i, {
                                type: 'line', xMin: p.val, xMax: p.val, borderColor: p.color,
                                borderWidth: 2, borderDash: [4,3],
                                label: {display: true, content: p.label, position: 'start', font: {size: 9}}
                            }]))
                        } : {}
                    },
                    scales: {y: {display: false}, x: {ticks: {font: chartFont, maxRotation: 45}}}
                }
            });
        }
    }

    // Outcome breakdown - horizontal stacked bar
    const ob = sim.outcome_breakdown;
    if (ob) {
        const ctx2 = document.getElementById('rpt-outcome-chart');
        if (ctx2) {
            const items = [];
            const colors = {full_exit: VOLO.green, stage_exit: VOLO.sage, partial_recovery: VOLO.blueSteel, late_small_exit: '#F0AD4E', total_loss: '#DC3545'};
            ['full_exit', 'stage_exit', 'partial_recovery', 'late_small_exit', 'total_loss'].forEach(k => {
                if (ob[k]) {
                    const rawPct = ob[k].pct;
                    const pctVal = rawPct > 1 ? rawPct / 100 : rawPct;
                    items.push({key: k, label: ob[k].label, count: ob[k].count, pct: pctVal});
                }
            });
            _wizReportCharts.outcome = new Chart(ctx2, {
                type: 'bar',
                data: {
                    labels: items.map(it => it.label),
                    datasets: [{
                        data: items.map(it => +(it.pct*100).toFixed(1)),
                        backgroundColor: items.map(it => colors[it.key] || '#999'),
                        borderRadius: 3,
                        barPercentage: 0.7,
                    }]
                },
                options: {
                    indexAxis: 'y', responsive: true, maintainAspectRatio: false,
                    scales: {
                        x: {beginAtZero: true, max: 100, ticks: {font: chartFont, callback: v => v + '%'}, grid: {color: 'rgba(0,0,0,0.04)'}},
                        y: {ticks: {font: {family: "'Inter', system-ui, sans-serif", size: 10}}, grid: {display: false}}
                    },
                    plugins: {
                        legend: {display: false},
                        tooltip: {callbacks: {label: ctx => ctx.parsed.x.toFixed(1) + '% of simulations'}}
                    }
                }
            });
        }
    }

    // S-Curve adoption chart
    const sc = adoption.scurve || adoption.adoption_curve || {};
    if (sc.years?.length) {
        const ctxSc = document.getElementById('rpt-scurve-chart');
        if (ctxSc) {
            const labels = sc.years.map(y => `Y${y}`);
            const datasets = [];
            if (sc.p90) datasets.push({label:'P90',data:sc.p90,borderColor:'transparent',backgroundColor:_c(34,87,122,0.06),fill:true,pointRadius:0,order:5});
            if (sc.p75) datasets.push({label:'P75',data:sc.p75,borderColor:_c(34,87,122,0.2),backgroundColor:_c(34,87,122,0.12),fill:true,pointRadius:0,borderWidth:1,order:4});
            if (sc.median||sc.p50) datasets.push({label:'Median',data:sc.median||sc.p50,borderColor:BLU,fill:false,pointRadius:0,borderWidth:2.5,tension:0.3,order:2});
            if (sc.p25) datasets.push({label:'P25',data:sc.p25,borderColor:_c(34,87,122,0.2),backgroundColor:_c(34,87,122,0.12),fill:'-1',pointRadius:0,borderWidth:1,order:3});
            if (sc.p10) datasets.push({label:'P10',data:sc.p10,borderColor:'transparent',backgroundColor:_c(34,87,122,0.06),fill:'-1',pointRadius:0,order:5});
            _wizReportCharts.scurve = new Chart(ctxSc, {
                type:'line', data:{labels,datasets},
                options: chartOpts({
                    plugins:{legend:{position:'bottom',labels:{font:chartFont,usePointStyle:true,filter:it=>['Median','P25','P75'].includes(it.text)}}},
                    scales:{
                        x:{title:{display:true,text:'Years from Investment',font:chartFont},ticks:{font:chartFont}},
                        y:{title:{display:true,text:'Cumulative Adoption ($M)',font:chartFont},beginAtZero:true,ticks:{font:chartFont}}
                    }
                })
            });
        }
    }

    // Revenue cone with founder overlay
    const revTraj = sim.revenue_trajectories || {};
    {
        const ctxRev = document.getElementById('rpt-revenue-cone-chart');
        if (ctxRev) {
            const med = revTraj.median || revTraj.p50 || [];
            const n = med.length;
            // Anchor X-axis to fund_vintage_year (Year 1 of fund); pad MC data with nulls for deal_offset_years
            const entryYr = r.deal_overview?.entry_year || new Date().getFullYear();
            const fundVintageYr = r.deal_overview?.fund_vintage_year || entryYr;
            const dealOffset = r.deal_overview?.deal_offset_years || 0;
            const totalN = dealOffset + n;
            const labels = Array.from({length: totalN}, (_, i) => String(fundVintageYr + i));
            // Helper: prepend dealOffset nulls to an MC array
            const padMC = arr => arr ? [...new Array(dealOffset).fill(null), ...arr] : null;
            const datasets = [];
            if (revTraj.p90) datasets.push({label:'P90',data:padMC(revTraj.p90),borderColor:'transparent',backgroundColor:_c(34,87,122,0.06),fill:true,pointRadius:0,order:6});
            if (revTraj.p75) datasets.push({label:'P75',data:padMC(revTraj.p75),borderColor:_c(34,87,122,0.25),backgroundColor:_c(34,87,122,0.12),fill:true,pointRadius:0,borderWidth:1,order:5});
            datasets.push({label:'Sim Median',data:padMC(med),borderColor:BLU,fill:false,pointRadius:0,borderWidth:2.5,tension:0.3,order:2});
            if (revTraj.p25) datasets.push({label:'P25',data:padMC(revTraj.p25),borderColor:_c(34,87,122,0.25),backgroundColor:_c(34,87,122,0.12),fill:'-1',pointRadius:0,borderWidth:1,order:4});
            if (revTraj.p10) datasets.push({label:'P10',data:padMC(revTraj.p10),borderColor:'transparent',backgroundColor:_c(34,87,122,0.06),fill:'-1',pointRadius:0,order:6});

            if (fComp.has_data && fComp.revenue?.year_by_year?.length) {
                // Authoritative calendar years from r.financial_model.fiscal_years (same source as FM summary table)
                const _rptFm = r.financial_model || {};
                const _rptFmFiscalYrs = (_rptFm.has_data && _rptFm.fiscal_years?.length)
                    ? _rptFm.fiscal_years.filter(y => !entryYr || y >= entryYr)
                    : null;
                const founderData = new Array(totalN).fill(null);
                fComp.revenue.year_by_year.forEach((y, i) => {
                    const calYr = (_rptFmFiscalYrs && i < _rptFmFiscalYrs.length)
                        ? _rptFmFiscalYrs[i]
                        : (y.calendar_year || (entryYr + i));
                    const t = calYr - fundVintageYr;
                    if (t >= 0 && t < totalN) founderData[t] = y.founder;
                });
                datasets.push({label:'Founder Projection (Base)',data:founderData,borderColor:'#dc2626',backgroundColor:'#dc2626',fill:false,pointRadius:5,pointStyle:'circle',borderWidth:3,tension:0.2,order:1});
            }

            const scenarioRev = fComp.scenario_revenue || {};
            const scStartYr = fComp.scenario_start_year || entryYr;
            if (scenarioRev.bear) {
                const bearData = new Array(totalN).fill(null);
                scenarioRev.bear.forEach((v, i) => {
                    const t = (scStartYr - fundVintageYr) + i;
                    if (t >= 0 && t < totalN && v) bearData[t] = v;
                });
                if (bearData.some(v => v != null)) {
                    datasets.push({label:'Bear Case',data:bearData,borderColor:'#dc3545',backgroundColor:'transparent',fill:false,pointRadius:3,pointStyle:'triangle',borderWidth:2,borderDash:[6,3],tension:0.2,order:1});
                }
            }
            if (scenarioRev.bull) {
                const bullData = new Array(totalN).fill(null);
                scenarioRev.bull.forEach((v, i) => {
                    const t = (scStartYr - fundVintageYr) + i;
                    if (t >= 0 && t < totalN && v) bullData[t] = v;
                });
                if (bullData.some(v => v != null)) {
                    datasets.push({label:'Bull Case',data:bullData,borderColor:'#007bff',backgroundColor:'transparent',fill:false,pointRadius:3,pointStyle:'triangle',borderWidth:2,borderDash:[6,3],tension:0.2,order:1});
                }
            }

            _wizReportCharts.revenueCone = new Chart(ctxRev, {
                type:'line', data:{labels,datasets},
                options: chartOpts({
                    plugins:{legend:{position:'bottom',labels:{font:chartFont,usePointStyle:true,filter:it=>!['P10','P90'].includes(it.text)}}},
                    scales:{
                        x:{title:{display:true,text:'Calendar Year',font:chartFont},ticks:{font:chartFont,maxRotation:45,autoSkip:true,maxTicksLimit:10}},
                        y:{title:{display:true,text:'Revenue ($M)',font:chartFont},beginAtZero:true,ticks:{font:chartFont}}
                    }
                })
            });
        }
    }

    // Position sizing charts
    const ps = r.position_sizing || {};
    if (ps.has_data && ps.grid_search?.grid?.length) {
        const gsGrid = ps.grid_search.grid;
        const gsOptimal = ps.grid_search.optimal || {};

        const ctxSz = document.getElementById('rpt-sizing-chart');
        if (ctxSz) {
            const labels = gsGrid.map(g => '$' + g.check_m.toFixed(1) + 'M');
            const hasFundData = gsGrid[0].fund_p10_pct_chg != null;
            const datasets = hasFundData ? [
                {label: 'Fund Δ%P90', data: gsGrid.map(g => (g.fund_p90_pct_chg || 0) * 100), borderColor: VOLO.green, backgroundColor: _c(91,119,68,0.08), fill: true, pointRadius: 0, borderWidth: 2, tension: 0.3, order: 3},
                {label: 'Fund Δ%P50', data: gsGrid.map(g => (g.fund_p50_pct_chg || 0) * 100), borderColor: BLU, fill: false, pointRadius: 0, borderWidth: 2.5, tension: 0.3, order: 2},
                {label: 'Fund Δ%P10', data: gsGrid.map(g => (g.fund_p10_pct_chg || 0) * 100), borderColor: _c(220,53,69,0.7), backgroundColor: _c(220,53,69,0.06), fill: true, pointRadius: 0, borderWidth: 2, tension: 0.3, order: 1},
            ] : [
                {label: 'P90 MOIC', data: gsGrid.map(g => g.p90_moic), borderColor: VOLO.green, fill: false, pointRadius: 0, borderWidth: 2, tension: 0.3},
                {label: 'P50 MOIC', data: gsGrid.map(g => g.p50_moic), borderColor: BLU, fill: false, pointRadius: 0, borderWidth: 2.5, tension: 0.3},
                {label: 'P10 MOIC', data: gsGrid.map(g => g.p10_moic), borderColor: _c(220,53,69,0.7), fill: false, pointRadius: 0, borderWidth: 2, tension: 0.3},
            ];
            _wizReportCharts.sizing = new Chart(ctxSz, {
                type: 'line',
                data: { labels, datasets },
                options: chartOpts({
                    plugins: {
                        legend: {position: 'bottom', labels: {font: chartFont, usePointStyle: true}},
                        annotation: {
                            annotations: {
                                optLine: {type: 'line', xMin: gsGrid.findIndex(g => g.check_m === gsOptimal.check_m), xMax: gsGrid.findIndex(g => g.check_m === gsOptimal.check_m), borderColor: VOLO.green, borderWidth: 2, borderDash: [4,2], label: {display: true, content: 'Optimal', position: 'start', font: {size: 9}}}
                            }
                        }
                    },
                    scales: {
                        x: {ticks: {font: chartFont, maxRotation: 45, autoSkip: true, maxTicksLimit: 12}},
                        y: {title: {display: true, text: hasFundData ? '% Change in Fund TVPI' : 'MOIC', font: chartFont}, ticks: {font: chartFont, callback: hasFundData ? (v) => v.toFixed(1) + '%' : undefined}}
                    }
                })
            });
        }

        const ctxScore = document.getElementById('rpt-sizing-score-chart');
        if (ctxScore) {
            const labels = gsGrid.map(g => '$' + g.check_m.toFixed(1) + 'M');
            const scores = gsGrid.map(g => g.composite_score);
            const bestIdx = scores.indexOf(Math.max(...scores));
            const barColors = scores.map((_, i) => i === bestIdx ? VOLO.green : _c(34,87,122,0.5));
            _wizReportCharts.sizingScore = new Chart(ctxScore, {
                type: 'bar',
                data: {
                    labels,
                    datasets: [{
                        label: 'Composite Score',
                        data: scores,
                        backgroundColor: barColors,
                        borderRadius: 2,
                        barPercentage: 0.85,
                    }]
                },
                options: chartOpts({
                    plugins: {legend: {display: false}},
                    scales: {
                        x: {ticks: {font: chartFont, maxRotation: 45, autoSkip: true, maxTicksLimit: 12}},
                        y: {title: {display: true, text: 'Score (higher = better)', font: chartFont}, beginAtZero: true, max: 1, ticks: {font: chartFont}}
                    }
                })
            });
        }
    }

    // Carbon stacked bar
    if (ci.annual_operating?.length) {
        const ctx5 = document.getElementById('rpt-carbon-chart');
        if (ctx5) {
            // Anchor carbon chart to fund_vintage_year with null-padding (same as revenue cone and memo chart)
            const _rptCarbonEntryYr = r.deal_overview?.entry_year || new Date().getFullYear();
            const _rptCarbonFundYr  = r.deal_overview?.fund_vintage_year || _rptCarbonEntryYr;
            const _rptCarbonLaunchYr = carbon.carbon_inputs?.commercial_launch_yr || _rptCarbonEntryYr;
            const _rptCarbonOff = Math.max(0, _rptCarbonLaunchYr - _rptCarbonFundYr);
            const _rptOpArr = ci.annual_operating || [];
            const _rptEmbArr = ci.annual_embodied || [];
            const _rptTotalLen = _rptCarbonOff + _rptOpArr.length;
            const labels = Array.from({length: _rptTotalLen}, (_, i) => String(_rptCarbonFundYr + i));
            const _rptPadC = arr => arr ? [...new Array(_rptCarbonOff).fill(null), ...arr] : [];
            _wizReportCharts.carbon = new Chart(ctx5, {
                type: 'bar',
                data: {
                    labels,
                    datasets: [
                        {label: 'Operating', data: _rptPadC(_rptOpArr), backgroundColor: _c(34,87,122,0.75), borderRadius: 3},
                        {label: 'Embodied', data: _rptPadC(_rptEmbArr), backgroundColor: _c(163,190,140,0.75), borderRadius: 3},
                    ]
                },
                options: {
                    responsive: true, maintainAspectRatio: false,
                    scales: {x: {stacked: true, ticks: {font: chartFont, maxRotation: 45, autoSkip: true, maxTicksLimit: 12}}, y: {stacked: true, title: {display: true, text: 'tCO2', font: chartFont}}},
                    plugins: {legend: {position: 'top', labels: {font: chartFont}}}
                }
            });
        }
    }
}

async function wizExportPDF() {
    const reportEl = document.getElementById('deal-report');
    if (!reportEl || !reportEl.innerHTML.trim()) {
        showToast('Generate a report first');
        return;
    }

    // 1. Clone so we never mutate the live DOM.
    const clone = reportEl.cloneNode(true);

    // 2. Replace every live <canvas> with a PNG snapshot. This is the fix for
    //    blank-PDF: Chart.js canvases do not reliably rasterize under the
    //    browser's print pipeline, so we flatten them to images up-front.
    const liveCanvases  = Array.from(reportEl.querySelectorAll('canvas'));
    const cloneCanvases = Array.from(clone.querySelectorAll('canvas'));
    liveCanvases.forEach((canvas, i) => {
        try {
            const dataUrl = canvas.toDataURL('image/png');
            const img = document.createElement('img');
            img.src = dataUrl;
            img.style.width    = Math.min(canvas.offsetWidth,  680) + 'px';
            img.style.height   = Math.min(canvas.offsetHeight, 320) + 'px';
            img.style.maxWidth = '100%';
            img.style.display  = 'block';
            img.style.margin   = '6pt auto';
            if (cloneCanvases[i]) cloneCanvases[i].replaceWith(img);
        } catch (e) { if (cloneCanvases[i]) cloneCanvases[i].remove(); }
    });

    // 3. Strip interactive chrome: debug trace toggles, QA banner/panel,
    //    action buttons, info-tip popovers.
    clone.querySelectorAll(
        '.rpt-trace,.qa-banner,.qa-panel,.report-actions,' +
        'button,.info-tip,.info-tip-body,.info-tip-icon'
    ).forEach(el => el.remove());

    // 3b. Inject hard page-break divs. CSS break-before on class selectors
    //     is unreliable in Chrome's print engine; zero-height divs with
    //     inline page-break-before are the reliable path.
    const cover = clone.querySelector('.rpt-cover');
    if (cover) {
        cover.style.pageBreakAfter = 'always';
        cover.style.breakAfter     = 'page';
    }
    const sections = Array.from(clone.querySelectorAll('.rpt-section'));
    sections.forEach((sec, idx) => {
        // If there's a cover, its break-after opens the page for section 1.
        // If there's no cover, section 1 rides the top of page 1.
        if (idx === 0) return;
        const pb = document.createElement('div');
        pb.setAttribute('aria-hidden', 'true');
        pb.style.cssText = 'page-break-before:always;break-before:page;' +
                           'height:0;margin:0;padding:0;border:none;display:block;';
        sec.parentNode.insertBefore(pb, sec);
    });

    const companyName = clone.querySelector('.rpt-company')?.textContent?.trim()
        || 'Deal Report';
    const safeTitle = companyName.replace(/[<>]/g, '');

    // 4. Build self-contained print HTML. Opening via Blob URL lets the
    //    browser fully lay out the document (resolving @page, decoding
    //    images) before the embedded window.print() fires.
    const printHtml = `<!DOCTYPE html><html><head>
<meta charset="utf-8">
<title>${safeTitle} — VoLo Earth Ventures</title>
<style>
*,*::before,*::after{box-sizing:border-box}
-webkit-print-color-adjust:exact;print-color-adjust:exact;

@page{size:letter portrait;margin:.6in .7in .75in .7in}

body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;
     font-size:10pt;color:#1a2332;background:#fff;margin:0;padding:0;width:100%;overflow:visible}

h1{font-size:20pt;margin:0 0 6pt}
h2{font-size:13pt;font-weight:700;margin:0 0 8pt;color:#1a2332;break-after:avoid;page-break-after:avoid}
h3,h4{font-weight:600;margin:8pt 0 4pt;break-after:avoid;page-break-after:avoid}
p,li{font-size:10pt;line-height:1.55;margin:3pt 0;orphans:3;widows:3}
ul,ol{margin:4pt 0 4pt 18pt}

/* Cover */
.rpt-cover{padding:14pt 0 18pt;border-bottom:2px solid #e1e4e8;margin-bottom:14pt;
           break-after:page;page-break-after:always}
.rpt-company{font-size:22pt;font-weight:700;color:#1a2332;margin:0 0 4pt;letter-spacing:-.02em}
.rpt-tech-desc{font-size:10.5pt;color:#4a5568;margin:4pt 0 14pt;line-height:1.5}
.rpt-meta{display:flex;flex-wrap:wrap;gap:6pt;margin-bottom:16pt}
.rpt-meta span{font-size:8pt;font-weight:500;padding:3pt 10pt;border-radius:40pt;
               background:#f4f5f7;border:1px solid #e1e4e8;color:#4a5568}
.rpt-deal-terms{display:grid;grid-template-columns:repeat(4,1fr);gap:8pt;margin-bottom:16pt;
                break-inside:avoid;page-break-inside:avoid}
.rpt-deal-terms>div{background:#f8f9fb;border:1px solid #e1e4e8;border-radius:4pt;padding:8pt 10pt}
.rpt-dt-label{display:block;font-size:7pt;text-transform:uppercase;letter-spacing:.06em;
              color:#6b7280;margin-bottom:2pt}
.rpt-dt-val{font-size:11pt;font-weight:600;color:#1a2332;font-variant-numeric:tabular-nums}

/* Hero rows (stat cards) */
.rpt-hero-row{display:grid;grid-template-columns:repeat(4,1fr);gap:8pt;margin:8pt 0 12pt;
              break-inside:avoid;page-break-inside:avoid}
.rpt-hero-card{background:#fff;border:1px solid #e1e4e8;border-radius:4pt;padding:10pt 8pt;
               text-align:center;break-inside:avoid;page-break-inside:avoid}
.rpt-hero-card.accent{border-color:#5B7744;border-width:2px;background:#f0f4ef;
                      -webkit-print-color-adjust:exact;print-color-adjust:exact}
.rpt-hero-num{font-size:14pt;font-weight:700;color:#1a2332;line-height:1.1;margin-bottom:3pt;
              font-variant-numeric:tabular-nums}
.rpt-hero-label{font-size:7pt;text-transform:uppercase;letter-spacing:.05em;color:#6b7280}

/* Sections */
.rpt-section{padding:14pt 0;break-inside:auto;page-break-inside:auto}
.rpt-section-num{font-size:7pt;font-weight:600;text-transform:uppercase;letter-spacing:.08em;
                 color:#8b949e;margin-bottom:4pt}
.rpt-section-title{font-size:13pt;font-weight:700;color:#1a2332;margin-bottom:10pt;
                   padding-bottom:6pt;border-bottom:1px solid #e1e4e8;
                   break-after:avoid;page-break-after:avoid}

/* Probability / outcome rows */
.rpt-prob-bar{display:flex;flex-wrap:wrap;gap:6pt;margin-bottom:12pt;
              break-inside:avoid;page-break-inside:avoid}

/* Tables */
.rpt-table-wrap{margin:8pt 0;overflow:visible;break-inside:auto;page-break-inside:auto}
table{width:100%;border-collapse:collapse;font-size:9pt;margin:6pt 0}
thead{display:table-header-group}
tr{break-inside:avoid;page-break-inside:avoid}
th,td{border:1px solid #e1e4e8;padding:4pt 6pt;text-align:left;vertical-align:top}
th{background:#f4f5f7;font-weight:600;-webkit-print-color-adjust:exact;print-color-adjust:exact}

/* Charts (rasterized to <img> above) */
img{max-width:100%;height:auto;display:block;margin:6pt auto;
    break-inside:avoid;page-break-inside:avoid}

footer{margin-top:20pt;padding-top:5pt;border-top:1px solid #e1e4e8;font-size:7.5pt;
       color:#8b949e;text-align:center;letter-spacing:.04em}
</style></head><body>
${clone.innerHTML}
<footer>VoLo Earth Ventures — Quantitative Underwriting Engine — Confidential</footer>
<script>
window.addEventListener('load', function () {
    setTimeout(function () { window.print(); }, 600);
});
<\/script>
</body></html>`;

    // 5. Open as Blob URL so the new window treats it as a real navigation,
    //    completes layout, then fires the embedded print().
    const blob = new Blob([printHtml], { type: 'text/html' });
    const url  = URL.createObjectURL(blob);
    window.open(url, '_blank');
    setTimeout(() => URL.revokeObjectURL(url), 120000);
}


// ── Fund Commitment Functions ──────────────────────────────────────────────

async function wizCommitToFund() {
    if (!_wizReportId) {
        showToast('No saved report to commit');
        return;
    }
    const btn = document.getElementById('btn-commit-fund');
    if (btn) { btn.disabled = true; btn.textContent = 'Committing...'; }

    // Read commitment type from radio buttons
    const ctypeRadio = document.querySelector('input[name="commit-type"]:checked');
    const commitmentType = ctypeRadio ? ctypeRadio.value : 'first_check';
    const foYearSel = document.getElementById('fo-year-select');
    const foParentSel = document.getElementById('fo-parent-select');
    const followOnYear = (commitmentType === 'follow_on' && foYearSel) ? parseInt(foYearSel.value) : 2;
    const parentId = (commitmentType === 'follow_on' && foParentSel && foParentSel.value) ? parseInt(foParentSel.value) : null;

    const payload = {
        report_id: _wizReportId,
        commitment_type: commitmentType,
        follow_on_year: followOnYear,
    };
    if (parentId) payload.parent_id = parentId;

    try {
        const r = await fetch('/api/deal-pipeline/fund/commit', {
            method: 'POST',
            headers: {..._rvmHeaders(), 'Content-Type': 'application/json'},
            body: JSON.stringify(payload),
        });
        const d = await r.json();
        if (!r.ok) {
            showToast(d.detail || 'Failed to commit deal');
            if (btn) { btn.disabled = false; btn.textContent = 'Commit to Fund'; }
            return;
        }
        const typeLabel = d.commitment_type === 'follow_on' ? 'follow-on' : 'first check';
        showToast(`${d.company_name} committed as ${typeLabel} (slot ${d.slot_index + 1})`);
        if (btn) {
            btn.textContent = 'Committed';
            btn.classList.remove('btn-primary');
            btn.classList.add('btn-success');
            btn.style.background = '#5B7744';
        }
        _wizLoadFundCommitments();
    } catch(e) {
        showToast('Network error committing deal');
        if (btn) { btn.disabled = false; btn.textContent = 'Commit to Fund'; }
    }
}

async function wizRemoveCommitment(cid, name) {
    if (!confirm(`Remove ${name} from the fund?`)) return;
    try {
        const r = await fetch(`/api/deal-pipeline/fund/commitment/${cid}`, {
            method: 'DELETE',
            headers: _rvmHeaders(),
        });
        if (r.ok) {
            showToast(`${name} removed from fund`);
            _wizLoadFundCommitments();
        } else {
            showToast('Failed to remove commitment');
        }
    } catch(e) {
        showToast('Network error');
    }
}

async function _wizLoadFundCommitments() {
    const panel = document.getElementById('fund-commitments-panel');
    if (!panel) return;

    try {
        const r = await fetch('/api/deal-pipeline/fund/commitments', {headers: _rvmHeaders()});
        const d = await r.json();
        const deals = d.commitments || [];

        if (deals.length === 0) {
            panel.style.display = 'none';
            return;
        }

        panel.style.display = 'block';
        const firstChecks = deals.filter(d2 => (d2.commitment_type || 'first_check') === 'first_check');
        const followOns = deals.filter(d2 => d2.commitment_type === 'follow_on');
        const fcTotal = firstChecks.reduce((s, d2) => s + (d2.check_size_m || 0), 0);
        const foTotal = followOns.reduce((s, d2) => s + (d2.check_size_m || 0), 0);

        let html = `<h3 class="rpt-section-title" style="display:flex;align-items:center;gap:8px;">
            <span style="background:#5B7744;color:#fff;border-radius:50%;width:24px;height:24px;display:inline-flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;">${deals.length}</span>
            Fund Portfolio — Committed Deals
        </h3>
        <p style="color:#586069;font-size:12px;margin-bottom:12px;">
            Total invested: <strong>$${d.total_invested_m.toFixed(1)}M</strong> across ${deals.length} deal${deals.length > 1 ? 's' : ''}.
            ${firstChecks.length > 0 ? `<span style="background:#e0e7ff;color:#3730a3;padding:1px 6px;border-radius:10px;font-size:10px;font-weight:600;margin-left:4px;">${firstChecks.length} first check${firstChecks.length > 1 ? 's' : ''} — $${fcTotal.toFixed(1)}M</span>` : ''}
            ${followOns.length > 0 ? `<span style="background:#fef3c7;color:#92400e;padding:1px 6px;border-radius:10px;font-size:10px;font-weight:600;margin-left:4px;">${followOns.length} follow-on${followOns.length > 1 ? 's' : ''} — $${foTotal.toFixed(1)}M</span>` : ''}
        </p>
        <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:10px;">`;

        for (const deal of deals) {
            const ctype = deal.commitment_type || 'first_check';
            const isFO = ctype === 'follow_on';
            const badgeBg = isFO ? '#fef3c7' : '#e0e7ff';
            const badgeColor = isFO ? '#92400e' : '#3730a3';
            const badgeLabel = isFO ? `Follow-On Yr ${deal.follow_on_year || '?'}` : 'First Check';
            html += `<div style="background:#f9fafb;border:1px solid #e1e4e8;border-radius:8px;padding:12px;position:relative;">
                <div style="font-weight:600;font-size:13px;margin-bottom:4px;">${deal.company_name}</div>
                <div style="font-size:11px;color:#586069;">
                    $${deal.check_size_m.toFixed(1)}M &middot; ${deal.entry_stage || 'N/A'}
                </div>
                <div style="margin-top:4px;">
                    <span style="background:${badgeBg};color:${badgeColor};padding:1px 6px;border-radius:10px;font-size:10px;font-weight:600;">${badgeLabel}</span>
                </div>
                <div style="font-size:10px;color:#8b949e;margin-top:4px;">Slot ${deal.slot_index + 1} &middot; ${deal.committed_at?.split('T')[0] || ''}</div>
                <button onclick="wizRemoveCommitment(${deal.id}, '${deal.company_name.replace(/'/g, "\\'")}')"
                    style="position:absolute;top:8px;right:8px;background:none;border:none;cursor:pointer;color:#d73a49;font-size:14px;padding:2px 6px;" title="Remove from fund">&times;</button>
            </div>`;
        }
        html += '</div>';

        // Populate the parent deal dropdown for follow-on commits
        const parentSel = document.getElementById('fo-parent-select');
        if (parentSel) {
            parentSel.innerHTML = '<option value="">— none —</option>';
            for (const fc of firstChecks) {
                parentSel.innerHTML += `<option value="${fc.id}">${fc.company_name} ($${fc.check_size_m.toFixed(1)}M)</option>`;
            }
        }

        // If this report is already committed as both types, disable the commit button
        if (_wizReportId) {
            const committedAsFC = deals.some(d2 => d2.report_id === _wizReportId && (d2.commitment_type || 'first_check') === 'first_check');
            const committedAsFO = deals.some(d2 => d2.report_id === _wizReportId && d2.commitment_type === 'follow_on');
            const btn = document.getElementById('btn-commit-fund');
            if (btn && committedAsFC && committedAsFO) {
                btn.disabled = true;
                btn.textContent = 'Committed (both)';
                btn.classList.remove('btn-primary');
                btn.classList.add('btn-success');
                btn.style.background = '#5B7744';
            } else if (btn && committedAsFC) {
                // Committed as first check only — can still add follow-on
                const fcRadio = document.querySelector('input[name="commit-type"][value="first_check"]');
                if (fcRadio) fcRadio.disabled = true;
                const foRadio = document.querySelector('input[name="commit-type"][value="follow_on"]');
                if (foRadio) { foRadio.checked = true; foRadio.dispatchEvent(new Event('change')); }
            } else if (btn && committedAsFO) {
                const foRadio = document.querySelector('input[name="commit-type"][value="follow_on"]');
                if (foRadio) foRadio.disabled = true;
            }
        }

        panel.innerHTML = html;
    } catch(e) {
        panel.style.display = 'none';
    }
}

async function wizExportCSV() {
    try {
        const r = await fetch('/api/deal-pipeline/export-csv', {headers: _rvmHeaders()});
        if (!r.ok) { showToast('CSV export failed'); return; }
        const blob = await r.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'volo_deal_pipeline_export.csv';
        a.click();
        URL.revokeObjectURL(url);
        showToast('CSV exported');
    } catch(e) { showToast('CSV export error'); }
}

async function wizUploadPortfolio() {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.csv,.json';
    input.onchange = async () => {
        if (!input.files.length) return;
        const fd = new FormData();
        fd.append('file', input.files[0]);
        try {
            const r = await fetch('/api/deal-pipeline/upload-portfolio', {
                method: 'POST',
                headers: {'Authorization': `Bearer ${localStorage.getItem('rvm_token')}`},
                body: fd,
            });
            const data = await r.json();
            if (r.ok) {
                showToast(`Uploaded ${data.holdings_count} holdings ($${data.summary?.total_invested_m?.toFixed(1)}M invested)`);
            } else {
                showToast(data.detail || 'Upload failed');
            }
        } catch(e) { showToast('Portfolio upload error'); }
    };
    input.click();
}

function showToast(msg) {
    const t = document.createElement('div');
    t.className = 'toast';
    t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 3200);
}

// ── Extraction mode toggle (Quick / Deep) ─────────────────────────────
document.querySelectorAll('.wiz-mode-pill').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('.wiz-mode-pill').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        const mode = btn.dataset.mode;
        const hidden = document.getElementById('wiz-model-mode');
        if (hidden) hidden.value = mode;
    });
});

// ── Deep (AI) extraction via background job + polling ────────────────
// Replaces the older NDJSON streaming approach, which failed on hosts
// whose proxy buffers chunked responses (Replit in particular). The
// user-visible UX is unchanged: same overlay, same progress log lines,
// same result shape handed to the caller. Under the hood we now:
//   1. POST the file to /api/extract-model-deep, get a job_id back.
//   2. Poll /api/extract-model-deep/status/{job_id} every 2s.
//   3. Render any NEW progress messages since the last poll.
//   4. Stop once status is 'done' (unwrap result) or 'error' (show).
async function _wizDeepExtract(modelFile, statusEl) {
    const overlay = document.getElementById('wiz-deep-progress');
    const statusLine = document.getElementById('wiz-deep-status');
    const logLine = document.getElementById('wiz-deep-log');
    const elapsedLine = document.getElementById('wiz-deep-elapsed');

    overlay.style.display = 'block';
    logLine.innerHTML = '';
    statusLine.style.color = '';
    statusLine.textContent = 'Preparing workbook…';
    const startedAt = Date.now();
    const elapsedTimer = setInterval(() => {
        const s = Math.floor((Date.now() - startedAt) / 1000);
        const m = Math.floor(s / 60);
        const r = s % 60;
        elapsedLine.textContent = m > 0 ? `${m}m ${r}s` : `${s}s`;
    }, 500);

    function _appendLog(msg) {
        const ts = new Date();
        const hh = String(ts.getHours()).padStart(2, '0');
        const mm = String(ts.getMinutes()).padStart(2, '0');
        const ss = String(ts.getSeconds()).padStart(2, '0');
        const line = document.createElement('div');
        line.textContent = `${hh}:${mm}:${ss}  ${msg}`;
        logLine.appendChild(line);
        logLine.scrollTop = logLine.scrollHeight;
    }

    function _hideOverlay(keepError = false) {
        clearInterval(elapsedTimer);
        if (!keepError) overlay.style.display = 'none';
    }

    const headers = {};
    if (_rvmToken) headers['Authorization'] = 'Bearer ' + _rvmToken;

    // ── 1. Start the background job ─────────────────────────────────────
    let jobId;
    try {
        const fd = new FormData();
        fd.append('file', modelFile);
        const r = await fetch('/api/extract-model-deep', {method: 'POST', headers, body: fd});
        if (!r.ok) {
            const text = await r.text();
            _appendLog(`HTTP ${r.status}: ${text.slice(0, 300)}`);
            statusLine.style.color = '#b54700';
            statusLine.textContent = `Request failed (HTTP ${r.status}). See log.`;
            _hideOverlay(true);
            return null;
        }
        const startData = await r.json();
        jobId = startData.job_id;
        if (!jobId) {
            statusLine.style.color = '#b54700';
            statusLine.textContent = 'Server did not return a job_id.';
            _hideOverlay(true);
            return null;
        }
    } catch (e) {
        statusLine.style.color = '#b54700';
        statusLine.textContent = 'Network error: ' + e.message;
        _appendLog('Network error: ' + e.message);
        _hideOverlay(true);
        return null;
    }

    // ── 2. Poll until done, rendering new progress entries each tick ────
    const pollIntervalMs = 2000;
    const hardTimeoutMs = 15 * 60 * 1000;  // 15-min ceiling
    let shownProgressCount = 0;

    while ((Date.now() - startedAt) < hardTimeoutMs) {
        let data;
        try {
            const r = await fetch(`/api/extract-model-deep/status/${jobId}`, {headers});
            if (!r.ok) {
                const text = await r.text();
                _appendLog(`Status poll failed HTTP ${r.status}: ${text.slice(0, 200)}`);
                // Transient — keep trying a few more times.
                await new Promise(res => setTimeout(res, pollIntervalMs));
                continue;
            }
            data = await r.json();
        } catch (e) {
            _appendLog('Network error during poll: ' + e.message);
            await new Promise(res => setTimeout(res, pollIntervalMs));
            continue;
        }

        const progress = Array.isArray(data.progress) ? data.progress : [];
        while (shownProgressCount < progress.length) {
            const evt = progress[shownProgressCount];
            const msg = (evt && evt.message) ? evt.message : '';
            if (msg) {
                statusLine.textContent = msg;
                _appendLog(msg);
            }
            shownProgressCount++;
        }

        if (data.status === 'done') {
            _appendLog('Extraction complete.');
            statusLine.textContent = 'Done.';
            _hideOverlay(false);
            return data.result;
        }
        if (data.status === 'error') {
            const errMsg = data.error || 'Unknown error';
            _appendLog('ERROR: ' + errMsg);
            statusLine.style.color = '#b54700';
            statusLine.textContent = errMsg;
            _hideOverlay(true);
            return null;
        }

        await new Promise(res => setTimeout(res, pollIntervalMs));
    }

    statusLine.style.color = '#b54700';
    statusLine.textContent = 'Extraction timed out (15 min). Check server logs.';
    _hideOverlay(true);
    return null;
}

// File input display
const _IMAGE_EXTS = new Set(['.png','.jpg','.jpeg','.webp']);
['wiz-deck-file', 'wiz-model-file'].forEach(id => {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener('change', () => {
        const nameEl = document.getElementById(id.replace('-file', '-name'));
        const zone   = document.getElementById(id.replace('-file', '-zone'));
        if (!el.files.length) return;
        const file = el.files[0];
        const ext  = ('.' + file.name.split('.').pop()).toLowerCase();
        if (nameEl) nameEl.textContent = file.name;
        if (zone)   zone.classList.add('has-file');

        // Image-specific: show thumbnail + Vision AI badge (model dropzone only)
        if (id === 'wiz-model-file' && _IMAGE_EXTS.has(ext) && zone) {
            zone.querySelectorAll('.wiz-img-preview, .wiz-vision-badge').forEach(e => e.remove());
            const reader = new FileReader();
            reader.onload = ev => {
                const img = document.createElement('img');
                img.src = ev.target.result;
                img.className = 'wiz-img-preview';
                img.style.cssText = 'max-height:90px;max-width:100%;margin-top:6px;border-radius:4px;object-fit:contain;display:block';
                zone.appendChild(img);
            };
            reader.readAsDataURL(file);
            const badge = document.createElement('span');
            badge.className = 'wiz-vision-badge';
            badge.textContent = '✦ Vision AI extraction';
            badge.style.cssText = 'display:inline-block;margin-top:6px;padding:2px 9px;background:#f0f4ef;color:#3B5249;border:1px solid #5B7744;border-radius:12px;font-size:11px;font-weight:600';
            zone.appendChild(badge);
        }
    });
});


// ================================================================
//  AI AGENT CHAT
// ================================================================

let _chatHistory = [];
let _chatOpen = false;

const _PARAM_FIELD_MAP = {
    check_size_millions: 'wiz-check-size',
    pre_money_millions: 'wiz-pre-money',
    tam_millions: 'wiz-tam',
    trl: 'wiz-trl',
    entry_stage: 'wiz-entry-stage',
    archetype: 'wiz-archetype',
    exit_multiple_low: 'wiz-mult-low',
    exit_multiple_high: 'wiz-mult-high',
    penetration_low: 'wiz-pen-low',
    penetration_high: 'wiz-pen-high',
    sector_profile: 'wiz-sector',
    n_simulations: null, // not directly mapped
};

function toggleChat() {
    _chatOpen = !_chatOpen;
    const panel = document.getElementById('chat-panel');
    const toggle = document.getElementById('chat-toggle');
    panel.classList.toggle('open', _chatOpen);
    toggle.classList.toggle('active', _chatOpen);
    toggle.innerHTML = _chatOpen ? '&times;' : '&#x1F4AC;';
    if (_chatOpen) document.getElementById('chat-input').focus();
}

function clearChat() {
    _chatHistory = [];
    const msgs = document.getElementById('chat-messages');
    msgs.innerHTML = '<div class="chat-msg system">I\'m your Deal Agent. I can modify deal parameters, run what-if scenarios, explain metrics, and suggest optimal terms. Ask me anything about the current deal.</div>';
}

function _renderMarkdown(text) {
    if (!text) return '';
    return text
        .replace(/```(\w*)\n([\s\S]*?)```/g, (_, lang, code) =>
            `<pre><code class="lang-${lang}">${code.replace(/</g, '&lt;').replace(/>/g, '&gt;')}</code></pre>`)
        .replace(/`([^`]+)`/g, '<code>$1</code>')
        .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
        .replace(/\*([^*]+)\*/g, '<em>$1</em>')
        .replace(/\n/g, '<br>');
}

function _buildCurrentDealParams() {
    const get = (id) => document.getElementById(id)?.value || '';
    return {
        company_name: get('wiz-company-name'),
        archetype: get('wiz-archetype'),
        tam_millions: parseFloat(get('wiz-tam')) || 0,
        trl: parseInt(get('wiz-trl')) || 5,
        entry_stage: get('wiz-entry-stage'),
        check_size_millions: parseFloat(get('wiz-check-size')) || 0,
        pre_money_millions: parseFloat(get('wiz-pre-money')) || 0,
        sector_profile: get('wiz-sector'),
        penetration_low: parseFloat(get('wiz-pen-low')) || 0,
        penetration_high: parseFloat(get('wiz-pen-high')) || 0,
        exit_multiple_low: parseFloat(get('wiz-mult-low')) || 0,
        exit_multiple_high: parseFloat(get('wiz-mult-high')) || 0,
    };
}

function _applyParamChange(param, value) {
    const fieldId = _PARAM_FIELD_MAP[param];
    if (!fieldId) return false;
    const el = document.getElementById(fieldId);
    if (!el) return false;
    el.value = value;
    el.dispatchEvent(new Event('change', {bubbles: true}));
    // Flash highlight
    el.style.transition = 'background 0.3s';
    el.style.background = '#c8e6c9';
    setTimeout(() => { el.style.background = ''; }, 1500);
    // Trigger archetype change handler if needed
    if (param === 'archetype') _wizOnArchetypeChange();
    if (param === 'check_size_millions' || param === 'pre_money_millions') _wizCalcOwnership();
    return true;
}

function _renderToolAction(action, msgs) {
    const div = document.createElement('div');
    div.className = 'chat-tool-action';

    if (action.tool === 'modify_deal_parameter') {
        const inp = action.input;
        div.innerHTML = `
            <div class="tool-name">Parameter Change</div>
            <div class="tool-body">
                <strong>${inp.parameter}</strong> → <code>${inp.value}</code><br>
                <em>${inp.reasoning}</em>
            </div>
            <button class="tool-apply-btn" onclick="this.disabled=true; _applyParamChange('${inp.parameter}', ${typeof inp.value === 'string' ? "'" + inp.value + "'" : inp.value}); this.outerHTML='<span class=\\'tool-applied\\'>Applied</span>';">Apply Change</button>
        `;
    } else if (action.tool === 'run_scenario_comparison') {
        const inp = action.input;
        const changes = Object.entries(inp.parameter_changes || {}).map(([k,v]) => `${k}: ${v}`).join(', ');
        div.innerHTML = `
            <div class="tool-name">Scenario: ${inp.scenario_name}</div>
            <div class="tool-body">Changes: ${changes}</div>
            <button class="tool-apply-btn" onclick="this.disabled=true; _applyScenario(${JSON.stringify(inp.parameter_changes).replace(/"/g, '&quot;')}); this.outerHTML='<span class=\\'tool-applied\\'>Applied all changes</span>';">Apply Scenario</button>
        `;
    } else if (action.tool === 'explain_metric') {
        div.innerHTML = `<div class="tool-name">Explaining: ${action.input.metric_name}</div>`;
    } else if (action.tool === 'suggest_optimal_terms') {
        div.innerHTML = `<div class="tool-name">Optimizing for: ${action.input.optimize_for}</div>`;
    }
    msgs.appendChild(div);
}

function _applyScenario(changes) {
    for (const [param, value] of Object.entries(changes)) {
        _applyParamChange(param, value);
    }
}

async function sendChat() {
    const input = document.getElementById('chat-input');
    const msg = input.value.trim();
    if (!msg) return;

    const msgs = document.getElementById('chat-messages');
    const sendBtn = document.getElementById('chat-send-btn');

    const userDiv = document.createElement('div');
    userDiv.className = 'chat-msg user';
    userDiv.textContent = msg;
    msgs.appendChild(userDiv);

    input.value = '';
    input.style.height = 'auto';
    sendBtn.disabled = true;

    const typingDiv = document.createElement('div');
    typingDiv.className = 'chat-typing';
    typingDiv.textContent = 'Analyzing...';
    msgs.appendChild(typingDiv);
    msgs.scrollTop = msgs.scrollHeight;

    const payload = {
        message: msg,
        report_context: _wizReport || null,
        deal_params: _buildCurrentDealParams(),
        conversation_history: _chatHistory.slice(-20),
    };

    try {
        const res = await fetch('/api/chat', {
            method: 'POST',
            headers: _rvmHeaders(),
            body: JSON.stringify(payload),
        });
        const data = await res.json();
        typingDiv.remove();

        if (!res.ok) {
            const errDiv = document.createElement('div');
            errDiv.className = 'chat-msg system';
            errDiv.textContent = data.error || data.detail || 'Chat failed';
            msgs.appendChild(errDiv);
        } else {
            // Render tool actions first
            if (data.tool_actions && data.tool_actions.length) {
                data.tool_actions.forEach(action => _renderToolAction(action, msgs));
            }
            // Render text reply
            if (data.reply) {
                const asstDiv = document.createElement('div');
                asstDiv.className = 'chat-msg assistant';
                asstDiv.innerHTML = _renderMarkdown(data.reply);
                msgs.appendChild(asstDiv);
            }
            _chatHistory.push({role: 'user', content: msg});
            _chatHistory.push({role: 'assistant', content: data.reply || ''});
        }
    } catch (e) {
        typingDiv.remove();
        const errDiv = document.createElement('div');
        errDiv.className = 'chat-msg system';
        errDiv.textContent = 'Network error: ' + e.message;
        msgs.appendChild(errDiv);
    }

    sendBtn.disabled = false;
    msgs.scrollTop = msgs.scrollHeight;
    input.focus();
}


// ================================================================
//  DEVELOPER TAB (Admin only)
// ================================================================

let _devCurrentFile = null;
let _devOriginalContent = '';
let _devTreeData = [];

function _showDevTab() {
    const link = document.getElementById('nav-dev-link');
    if (link && _rvmUser) {
        link.style.display = '';
    }
}

async function devLoadTree() {
    try {
        const res = await fetch('/api/dev/tree', {headers: _rvmHeaders()});
        if (!res.ok) { showToast('Failed to load file tree'); return; }
        const data = await res.json();
        _devTreeData = data.files || [];
        _renderDevTree(_devTreeData);
    } catch(e) { showToast('Dev tree error: ' + e.message); }
}

function _renderDevTree(files) {
    const container = document.getElementById('dev-file-tree');
    let html = '';
    let lastDir = '';
    files.forEach(f => {
        const dir = f.path.includes('/') ? f.path.substring(0, f.path.lastIndexOf('/')) : '';
        if (dir !== lastDir) {
            lastDir = dir;
            html += `<div class="dev-file-folder">${dir || 'root'}/</div>`;
        }
        const sizeStr = f.size > 1024 ? (f.size / 1024).toFixed(1) + 'K' : f.size + 'B';
        const active = _devCurrentFile === f.path ? ' active' : '';
        html += `<div class="dev-file-item${active}" onclick="devOpenFile('${f.path}')" title="${f.path}">
            <span>${f.name}</span>
            <span class="file-size">${sizeStr}</span>
        </div>`;
    });
    container.innerHTML = html || '<div style="padding:16px;color:var(--text-tertiary);">No editable files found.</div>';
}

async function devOpenFile(path) {
    try {
        const res = await fetch(`/api/dev/file?path=${encodeURIComponent(path)}`, {headers: _rvmHeaders()});
        if (!res.ok) { showToast('Failed to open file'); return; }
        const data = await res.json();

        _devCurrentFile = path;
        _devOriginalContent = data.content;

        document.getElementById('dev-current-file').textContent = 'app/' + path;
        const editor = document.getElementById('dev-editor');
        editor.value = data.content;
        editor.disabled = false;
        document.getElementById('dev-save-btn').disabled = false;
        document.getElementById('dev-save-status').textContent = `${(data.size / 1024).toFixed(1)} KB`;

        _devUpdateLineNumbers();
        _renderDevTree(_devTreeData); // refresh active highlight
    } catch(e) { showToast('Open file error: ' + e.message); }
}

function _devUpdateLineNumbers() {
    const editor = document.getElementById('dev-editor');
    const lines = document.getElementById('dev-line-numbers');
    const count = (editor.value.match(/\n/g) || []).length + 1;
    let nums = '';
    for (let i = 1; i <= count; i++) nums += i + '\n';
    lines.textContent = nums;
}

async function devSaveFile() {
    if (!_devCurrentFile) return;
    const editor = document.getElementById('dev-editor');
    const content = editor.value;
    const status = document.getElementById('dev-save-status');

    status.textContent = 'Saving...';
    try {
        const res = await fetch('/api/dev/file', {
            method: 'POST',
            headers: _rvmHeaders(),
            body: JSON.stringify({path: _devCurrentFile, content: content}),
        });
        const data = await res.json();
        if (res.ok && data.ok) {
            _devOriginalContent = content;
            status.textContent = 'Saved';
            status.style.color = 'var(--volo-green-dark)';
            setTimeout(() => { status.style.color = ''; status.textContent = `${(content.length / 1024).toFixed(1)} KB`; }, 2000);
            if (_devCurrentFile.endsWith('.py')) {
                showToast('Saved ' + _devCurrentFile + ' — server will auto-reload');
            } else {
                showToast('Saved ' + _devCurrentFile);
            }
        } else {
            status.textContent = data.error || 'Save failed';
            status.style.color = 'var(--danger)';
        }
    } catch(e) {
        status.textContent = 'Save error';
        status.style.color = 'var(--danger)';
    }
}

async function devSearch() {
    const q = document.getElementById('dev-search-input').value.trim();
    if (!q) return;

    const resultsEl = document.getElementById('dev-search-results');
    resultsEl.style.display = 'block';
    resultsEl.innerHTML = '<div style="padding:8px 12px;color:var(--text-tertiary);">Searching...</div>';

    try {
        const res = await fetch(`/api/dev/search?q=${encodeURIComponent(q)}`, {headers: _rvmHeaders()});
        const data = await res.json();
        if (!data.results || !data.results.length) {
            resultsEl.innerHTML = '<div style="padding:8px 12px;color:var(--text-tertiary);">No results found.</div>';
            return;
        }
        let html = `<div style="padding:4px 12px;font-size:0.72rem;color:var(--text-tertiary);border-bottom:1px solid var(--border);">${data.results.length} results${data.truncated ? ' (truncated)' : ''}</div>`;
        data.results.forEach(r => {
            html += `<div class="dev-search-result" onclick="devOpenFile('${r.file}')">
                <span class="sr-file">${r.file}</span>
                <span class="sr-line">:${r.line}</span>
                <span class="sr-text">${r.text.replace(/</g, '&lt;')}</span>
            </div>`;
        });
        resultsEl.innerHTML = html;
    } catch(e) {
        resultsEl.innerHTML = `<div style="padding:8px 12px;color:var(--danger);">Search error: ${e.message}</div>`;
    }
}


// ================================================================
//  REFERENCES TAB
// ================================================================

let _refData = [];

// Category mapping: which reference IDs go in which tab
const _REF_CATEGORIES = {
    core: ["carta_rounds", "nrel_atb", "damodaran_comps", "lazard_lcoe", "doe_electrification"],
    adoption: ["bass_diffusion"],
    valuation: ["damodaran_comps", "ebitda_margins", "private_discount", "carta_benchmarks"],
    carbon: ["carbon_intensity"],
    research: ["cambridge_exits", "market_sizing"],
};

function _refFormatDate(isoStr) {
    if (!isoStr) return '--';
    const d = new Date(isoStr);
    if (isNaN(d)) return isoStr;
    return d.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
}

function _refDaysAgo(isoStr) {
    if (!isoStr) return Infinity;
    const d = new Date(isoStr);
    if (isNaN(d)) return Infinity;
    return Math.floor((Date.now() - d.getTime()) / (1000 * 60 * 60 * 24));
}

function _refStatusBadge(ref) {
    if (ref.file_exists === null) {
        // hardcoded source
        const days = _refDaysAgo(ref.last_modified);
        if (days > 365) return '<span class="ref-status-badge stale">Stale (>' + Math.floor(days / 365) + 'yr)</span>';
        return '<span class="ref-status-badge hardcoded">Hardcoded</span>';
    }
    if (ref.file_exists === false) return '<span class="ref-status-badge missing">Missing</span>';
    const days = _refDaysAgo(ref.last_modified);
    if (days > 365) return '<span class="ref-status-badge stale">Stale (' + Math.floor(days / 365) + 'yr old)</span>';
    if (days > 180) return '<span class="ref-status-badge stale">Aging (' + Math.floor(days / 30) + 'mo)</span>';
    return '<span class="ref-status-badge current">Current</span>';
}

function _refFileSizeStr(bytes) {
    if (!bytes) return '';
    if (bytes > 1048576) return (bytes / 1048576).toFixed(1) + ' MB';
    if (bytes > 1024) return (bytes / 1024).toFixed(0) + ' KB';
    return bytes + ' B';
}

async function refLoadReferences() {
    const btn = document.getElementById('ref-load-btn');
    btn.textContent = 'Loading...';
    btn.disabled = true;

    try {
        const res = await fetch('/api/references', { headers: _rvmHeaders() });
        if (!res.ok) { showToast('Failed to load references'); btn.textContent = 'Load References'; btn.disabled = false; return; }
        const data = await res.json();
        _refData = data.references || [];

        // Populate each category table
        _refPopulateCore();
        _refPopulateAdoption();
        _refPopulateValuation();
        _refPopulateCarbon();
        _refPopulateResearch();

        // Show content, hide placeholder
        document.getElementById('ref-placeholder').style.display = 'none';
        document.getElementById('ref-content').style.display = '';
        document.getElementById('ref-last-checked').textContent = new Date().toLocaleString();
        document.getElementById('ref-refresh-btn').disabled = false;

        btn.textContent = 'Reload';
        btn.disabled = false;
    } catch (e) {
        showToast('References error: ' + e.message);
        btn.textContent = 'Load References';
        btn.disabled = false;
    }
}

function _refById(id) {
    return _refData.find(r => r.id === id);
}

function _refPopulateCore() {
    const tbody = document.getElementById('ref-table-core');
    const ids = _REF_CATEGORIES.core;
    let html = '';
    ids.forEach(id => {
        const r = _refById(id);
        if (!r) return;
        const dateClass = _refDaysAgo(r.last_modified) > 365 ? ' ref-date-stale' : '';
        const fileInfo = r.file
            ? `<span class="ref-file${r.file_exists === false ? ' ref-file-missing' : ''}">${r.file}</span>${r.file_size ? '<br><span style="font-size:0.7rem;color:var(--text-tertiary);">' + _refFileSizeStr(r.file_size) + '</span>' : ''}`
            : '<span class="ref-file">' + (r.hardcoded_in || 'N/A') + '</span>';
        html += `<tr>
            <td>
                <div class="ref-name">${r.name}</div>
                <div class="ref-provider">${r.provider}</div>
                <div class="ref-desc">${r.description}</div>
            </td>
            <td>${fileInfo}</td>
            <td class="ref-date${dateClass}">${_refFormatDate(r.last_modified)}</td>
            <td>${_refStatusBadge(r)}</td>
            <td><button class="ref-action-btn" onclick="refShowRefreshSteps('${r.id}')">Refresh</button></td>
        </tr>`;
    });
    tbody.innerHTML = html || '<tr><td colspan="5" style="text-align:center;color:var(--text-tertiary);">No data sources found.</td></tr>';
}

function _refPopulateAdoption() {
    const tbody = document.getElementById('ref-table-adoption');
    const r = _refById('bass_diffusion');
    if (!r) { tbody.innerHTML = '<tr><td colspan="5">No adoption data found.</td></tr>'; return; }

    const archetypes = [
        { name: 'Utility Solar', inflection: 2018, maturity: 'growth' },
        { name: 'Commercial Solar', inflection: 2020, maturity: 'growth' },
        { name: 'Residential Solar', inflection: 2022, maturity: 'early growth' },
        { name: 'Onshore Wind', inflection: 2016, maturity: 'mature' },
        { name: 'Offshore Wind', inflection: 2028, maturity: 'pre-inflection' },
        { name: 'Geothermal', inflection: 2032, maturity: 'early' },
        { name: 'Battery Storage', inflection: 2025, maturity: 'inflection' },
        { name: 'Nuclear SMR', inflection: 2038, maturity: 'nascent' },
        { name: 'EV Electrification', inflection: 2026, maturity: 'inflection' },
        { name: 'Climate Software', inflection: 2027, maturity: 'inflection' },
        { name: 'Industrial Decarb', inflection: 2030, maturity: 'early' },
        { name: 'AI/ML', inflection: 2024, maturity: 'growth' },
    ];

    let html = '';
    archetypes.forEach(a => {
        html += `<tr>
            <td class="ref-name">${a.name}</td>
            <td class="ref-file">${r.hardcoded_in || 'engine/adoption.py'}</td>
            <td class="ref-date">${_refFormatDate(r.last_modified)}</td>
            <td>${a.inflection} inflection (${a.maturity})</td>
            <td>${_refStatusBadge(r)}</td>
        </tr>`;
    });
    tbody.innerHTML = html;
}

function _refPopulateValuation() {
    const tbody = document.getElementById('ref-table-valuation');
    const ids = ['damodaran_comps', 'ebitda_margins', 'private_discount', 'carta_benchmarks'];
    let html = '';
    ids.forEach(id => {
        const r = _refById(id);
        if (!r) return;
        const dateClass = _refDaysAgo(r.last_modified) > 365 ? ' ref-date-stale' : '';
        const loc = r.file ? r.file : (r.hardcoded_in || 'N/A');
        const industries = id === 'damodaran_comps' ? '97 US industries' : (id === 'ebitda_margins' ? '9 TRL levels' : '');
        html += `<tr>
            <td>
                <div class="ref-name">${r.name}</div>
                <div class="ref-provider">${r.provider}</div>
            </td>
            <td class="ref-file">${loc}</td>
            <td class="ref-date${dateClass}">${_refFormatDate(r.last_modified)}</td>
            <td>${industries}</td>
            <td>${_refStatusBadge(r)}</td>
        </tr>`;
    });
    tbody.innerHTML = html || '<tr><td colspan="5">No valuation data found.</td></tr>';
}

function _refPopulateCarbon() {
    const tbody = document.getElementById('ref-table-carbon');
    const r = _refById('carbon_intensity');
    if (!r) { tbody.innerHTML = '<tr><td colspan="5">No carbon data found.</td></tr>'; return; }
    const dateClass = _refDaysAgo(r.last_modified) > 365 ? ' ref-date-stale' : '';

    const factors = [
        { name: 'Carbon Intensity by Resource', scope: 'US & global electricity, natural gas, gasoline' },
        { name: 'TRL-to-Risk Divisor', scope: 'TRL 1-4: 6x, TRL 5-6: 3x, TRL 7-9: 1x' },
        { name: 'Archetype Defaults', scope: 'Baseline production, range improvement, service life' },
    ];

    let html = '';
    factors.forEach(f => {
        html += `<tr>
            <td class="ref-name">${f.name}<div class="ref-provider">${r.provider}</div></td>
            <td class="ref-file">${r.hardcoded_in || 'engine/rvm_carbon.py'}</td>
            <td class="ref-date${dateClass}">${_refFormatDate(r.last_modified)}</td>
            <td>${f.scope}</td>
            <td>${_refStatusBadge(r)}</td>
        </tr>`;
    });
    tbody.innerHTML = html;
}

function _refPopulateResearch() {
    const tbody = document.getElementById('ref-table-research');
    const research = [
        {
            pub: 'Cambridge Associates — Venture Exit Data',
            year: '2023',
            citation: 'Exit year probability weighting (years 4-7 peak)',
            used: 'monte_carlo.py — exit timing',
            url: 'https://www.cambridgeassociates.com/',
        },
        {
            pub: 'Koeplin, Sarin & Shapiro — The Private Company Discount',
            year: '2000',
            citation: '15-30% acquisition discount to IPO multiples',
            used: 'valuation_comps.py — acquisition haircut',
            url: null,
        },
        {
            pub: 'Officer — The price of corporate liquidity',
            year: '2007',
            citation: 'Confirms 15-30% private company discount',
            used: 'valuation_comps.py — acquisition haircut',
            url: null,
        },
        {
            pub: 'Sahlman (HBS) — A Method for Valuing High-Risk Investments',
            year: '1990',
            citation: 'VC Method: EV = EBITDA x exit_multiple',
            used: 'monte_carlo.py — exit valuation',
            url: null,
        },
        {
            pub: 'BloombergNEF — New Energy Outlook 2024',
            year: '2024',
            citation: 'TAM estimates: Solar $120B, Battery $80B, EV $500B',
            used: 'market_sizing.py — TAM defaults',
            url: 'https://about.bnef.com/new-energy-outlook/',
        },
        {
            pub: 'IEA — World Energy Outlook / Net Zero 2025',
            year: '2025',
            citation: 'Wind TAM $100B, Industrial Decarb $200B',
            used: 'market_sizing.py — TAM defaults',
            url: 'https://www.iea.org/reports/world-energy-outlook-2024',
        },
        {
            pub: 'Bass (1969) — A New Product Growth Model for Consumer Durables',
            year: '1969',
            citation: 'Foundation for technology adoption S-curves (p, q parameters)',
            used: 'adoption.py — Bass diffusion model',
            url: null,
        },
        {
            pub: 'Burger, Systrom &amp; Kearney — Climate Impact Assessment for Early-Stage Ventures (PRIME Coalition / NYSERDA)',
            year: '2017',
            citation: 'PRIME ERP 5-step framework: displaced emissions (§2), additionality (§3), venture emissions (§3), S-curve deployment (§4), ERP summation (§5). Multi-year service life cohort accounting. 30-year analysis horizon recommendation.',
            used: 'rvm_carbon.py — full carbon methodology',
            url: 'https://www.primecoalition.org/library/climate-impact-assessment-for-early-stage-ventures',
        },
    ];

    let html = '';
    research.forEach(r => {
        const link = r.url ? `<a href="${r.url}" target="_blank" rel="noopener" style="color:var(--volo-green-dark);font-size:0.75rem;">Open</a>` : '<span style="color:var(--text-tertiary);font-size:0.72rem;">N/A</span>';
        html += `<tr>
            <td class="ref-name">${r.pub}</td>
            <td>${r.year}</td>
            <td style="font-size:0.78rem;">${r.citation}</td>
            <td class="ref-file">${r.used}</td>
            <td>${link}</td>
        </tr>`;
    });
    tbody.innerHTML = html;
}

function refSwitchCategory(cat) {
    // Update tabs
    document.querySelectorAll('.ref-tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.ref-category').forEach(c => { c.classList.remove('active'); c.style.display = 'none'; });

    const tab = document.querySelector(`.ref-tab[onclick*="${cat}"]`);
    if (tab) tab.classList.add('active');
    const panel = document.getElementById('ref-category-' + cat);
    if (panel) { panel.classList.add('active'); panel.style.display = 'block'; }
}

function refRefreshData() {
    // Build the refresh modal with refreshable sources
    const container = document.getElementById('ref-refresh-options');
    const refreshable = _refData.filter(r => r.file);

    let html = '';
    refreshable.forEach(r => {
        html += `<div class="ref-refresh-item" id="ref-refresh-item-${r.id}">
            <input type="checkbox" id="ref-chk-${r.id}" value="${r.id}">
            <div>
                <label for="ref-chk-${r.id}">${r.name}</label>
                <div style="font-size:0.72rem;color:var(--text-tertiary);">${r.provider} — last updated ${_refFormatDate(r.last_modified)}</div>
                <div class="steps" id="ref-steps-${r.id}" style="display:none;"></div>
            </div>
        </div>`;
    });

    // Also show hardcoded sources
    const hardcoded = _refData.filter(r => !r.file);
    if (hardcoded.length) {
        html += '<div style="margin-top:12px;padding-top:12px;border-top:1px solid var(--border);"><strong style="font-size:0.82rem;">Hardcoded Sources</strong> <span style="font-size:0.72rem;color:var(--text-tertiary);">(edit in Developer Console)</span></div>';
        hardcoded.forEach(r => {
            html += `<div class="ref-refresh-item" style="opacity:0.7;">
                <div>
                    <label>${r.name}</label>
                    <div style="font-size:0.72rem;color:var(--text-tertiary);">In ${r.hardcoded_in || 'engine source'} — ${_refFormatDate(r.last_modified)}</div>
                </div>
            </div>`;
        });
    }

    container.innerHTML = html;
    document.getElementById('ref-refresh-modal').style.display = 'flex';
}

function refCloseModal() {
    document.getElementById('ref-refresh-modal').style.display = 'none';
}

async function refShowRefreshSteps(refId) {
    try {
        const res = await fetch(`/api/references/refresh/${refId}`, {
            method: 'POST',
            headers: _rvmHeaders(),
        });
        const data = await res.json();
        if (data.refresh && data.refresh.steps) {
            let msg = '<strong>' + data.name + '</strong><br><br>';
            if (data.url) msg += 'Source: <a href="' + data.url + '" target="_blank">' + data.url + '</a><br><br>';
            msg += '<strong>Steps to refresh:</strong><ol>';
            data.refresh.steps.forEach(s => { msg += '<li>' + s + '</li>'; });
            msg += '</ol>';

            // Show in a simple modal
            const container = document.getElementById('ref-refresh-options');
            container.innerHTML = `<div style="line-height:1.6;font-size:0.85rem;">${msg}</div>`;
            document.getElementById('ref-refresh-modal').style.display = 'flex';
            document.getElementById('ref-confirm-refresh-btn').style.display = 'none';
        }
    } catch (e) {
        showToast('Error: ' + e.message);
    }
}

async function refExecuteRefresh() {
    const checked = [];
    _refData.filter(r => r.file).forEach(r => {
        const chk = document.getElementById('ref-chk-' + r.id);
        if (chk && chk.checked) checked.push(r.id);
    });

    if (!checked.length) { showToast('Select at least one data source to refresh.'); return; }

    // Show refresh steps for each selected source
    const container = document.getElementById('ref-refresh-options');
    container.innerHTML = '<div style="padding:8px;color:var(--text-tertiary);">Loading refresh instructions...</div>';

    let allSteps = '';
    for (const refId of checked) {
        try {
            const res = await fetch(`/api/references/refresh/${refId}`, {
                method: 'POST',
                headers: _rvmHeaders(),
            });
            const data = await res.json();
            if (data.refresh) {
                allSteps += `<div style="margin-bottom:16px;padding-bottom:12px;border-bottom:1px solid var(--border);">
                    <strong>${data.name}</strong>`;
                if (data.url) allSteps += ` — <a href="${data.url}" target="_blank" style="color:var(--volo-green-dark);">Source</a>`;
                allSteps += '<ol>';
                data.refresh.steps.forEach(s => { allSteps += `<li>${s}</li>`; });
                allSteps += '</ol></div>';
            }
        } catch (e) { /* skip */ }
    }

    container.innerHTML = `<div style="line-height:1.6;font-size:0.85rem;">${allSteps}</div>`;
    document.getElementById('ref-confirm-refresh-btn').style.display = 'none';
}


// ================================================================
//  DEV CODING AGENT
// ================================================================

let _devAgentHistory = [];

function devAgentClear() {
    _devAgentHistory = [];
    const msgs = document.getElementById('dev-agent-messages');
    msgs.innerHTML = '<div class="dev-agent-msg system">I\'m your coding assistant. I can read, search, edit, and write files in this project. I\'ll show you every change I make.<br><br>Try: <em>"Add error handling to the /api/simulate endpoint"</em> or <em>"Explain how the Monte Carlo engine works"</em></div>';
}

function _devAgentAppendMsg(role, html) {
    const msgs = document.getElementById('dev-agent-messages');
    const div = document.createElement('div');
    div.className = 'dev-agent-msg ' + role;
    div.innerHTML = html;
    msgs.appendChild(div);
    msgs.scrollTop = msgs.scrollHeight;
    return div;
}

function _devAgentShowTyping() {
    const msgs = document.getElementById('dev-agent-messages');
    const div = document.createElement('div');
    div.className = 'dev-agent-typing';
    div.id = 'dev-agent-typing';
    div.innerHTML = '<span class="dot"></span><span class="dot"></span><span class="dot"></span> Thinking & coding...';
    msgs.appendChild(div);
    msgs.scrollTop = msgs.scrollHeight;
}

function _devAgentHideTyping() {
    const el = document.getElementById('dev-agent-typing');
    if (el) el.remove();
}

function _devAgentRenderActions(actions) {
    if (!actions || !actions.length) return '';

    const iconMap = {
        read_file: {cls: 'read', icon: 'R'},
        write_file: {cls: 'write', icon: 'W'},
        edit_file: {cls: 'edit', icon: 'E'},
        search_code: {cls: 'search', icon: 'S'},
        list_files: {cls: 'list', icon: 'L'},
    };

    let html = '';
    actions.forEach(a => {
        const ic = iconMap[a.tool] || {cls: 'list', icon: '?'};
        const statusCls = a.success ? 'success' : 'error';
        const statusIcon = a.success ? '\u2713' : '\u2717';

        let detail = '';
        if (a.tool === 'read_file') detail = a.input.path || '';
        else if (a.tool === 'write_file') detail = (a.input.path || '') + ' — ' + (a.input.description || '');
        else if (a.tool === 'edit_file') detail = (a.input.path || '') + ' — ' + (a.input.description || '');
        else if (a.tool === 'search_code') detail = '"' + (a.input.query || '') + '"';
        else detail = a.tool;

        // Button to open file in editor (for read/write/edit)
        let openBtn = '';
        const filePath = a.input.path || '';
        if (filePath && (a.tool === 'read_file' || a.tool === 'write_file' || a.tool === 'edit_file')) {
            openBtn = `<button class="action-open-btn" onclick="devOpenFile('${filePath}')">Open in Editor</button>`;
        }

        html += `<div class="dev-agent-action-card">
            <div class="action-header">
                <span class="action-icon ${ic.cls}">${ic.icon}</span>
                <span>${a.tool.replace(/_/g, ' ')}</span>
                <span class="action-icon ${statusCls}" style="margin-left:auto;">${statusIcon}</span>
            </div>
            <div style="font-size:0.72rem;color:var(--text-secondary);">${_escHtml(detail)}</div>
            ${a.result ? `<div class="action-result">${_escHtml(a.result)}</div>` : ''}
            ${openBtn}
        </div>`;
    });
    return html;
}

function _escHtml(str) {
    if (!str) return '';
    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function _devAgentFormatReply(text) {
    if (!text) return '';
    // Simple markdown: code blocks, inline code, bold, line breaks
    let html = _escHtml(text);
    // Code blocks: ```...```
    html = html.replace(/```(\w*)\n([\s\S]*?)```/g, (m, lang, code) => {
        return '<pre>' + code + '</pre>';
    });
    // Inline code: `...`
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
    // Bold: **...**
    html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    // Line breaks
    html = html.replace(/\n/g, '<br>');
    return html;
}

async function devAgentSend() {
    const input = document.getElementById('dev-agent-input');
    const sendBtn = document.getElementById('dev-agent-send-btn');
    const message = input.value.trim();
    if (!message) return;

    // Show user message
    _devAgentAppendMsg('user', _escHtml(message));
    _devAgentHistory.push({role: 'user', content: message});

    input.value = '';
    input.style.height = 'auto';
    sendBtn.disabled = true;

    _devAgentShowTyping();

    try {
        const payload = {
            message: message,
            conversation_history: _devAgentHistory.slice(-20),
        };

        // Attach current file context if editor has a file open
        if (_devCurrentFile) {
            const editor = document.getElementById('dev-editor');
            payload.current_file = _devCurrentFile;
            // Send first 10K chars to avoid huge payloads
            payload.current_file_content = editor.value.substring(0, 10000);
        }

        const res = await fetch('/api/dev/chat', {
            method: 'POST',
            headers: _rvmHeaders(),
            body: JSON.stringify(payload),
        });

        _devAgentHideTyping();

        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            _devAgentAppendMsg('system', 'Error: ' + (err.error || res.statusText));
            sendBtn.disabled = false;
            return;
        }

        const data = await res.json();

        // Render tool actions if any
        if (data.actions && data.actions.length) {
            const actionsHtml = _devAgentRenderActions(data.actions);
            _devAgentAppendMsg('assistant', actionsHtml);

            // If any file was written/edited, refresh the file tree and reload current file
            const fileChanged = data.actions.some(a =>
                (a.tool === 'write_file' || a.tool === 'edit_file') && a.success
            );
            if (fileChanged) {
                devLoadTree();
                // Reload current file if it was modified
                const modifiedFiles = data.actions
                    .filter(a => (a.tool === 'write_file' || a.tool === 'edit_file') && a.success)
                    .map(a => a.input.path);
                if (_devCurrentFile && modifiedFiles.includes(_devCurrentFile)) {
                    devOpenFile(_devCurrentFile);
                }
            }
        }

        // Render text reply
        if (data.reply) {
            _devAgentAppendMsg('assistant', _devAgentFormatReply(data.reply));
            _devAgentHistory.push({role: 'assistant', content: data.reply});
        }

        // Show token usage as subtle footer
        if (data.usage) {
            const usageEl = document.createElement('div');
            usageEl.style.cssText = 'font-size:0.65rem;color:var(--text-tertiary);text-align:right;padding:0 12px;';
            usageEl.textContent = `${data.usage.input_tokens + data.usage.output_tokens} tokens`;
            document.getElementById('dev-agent-messages').appendChild(usageEl);
        }

    } catch (e) {
        _devAgentHideTyping();
        _devAgentAppendMsg('system', 'Network error: ' + e.message);
    }

    sendBtn.disabled = false;
    input.focus();
}


// ================================================================
//  INIT LISTENERS
// ================================================================

document.addEventListener('DOMContentLoaded', () => {
    // Chat textarea auto-resize
    const chatInput = document.getElementById('chat-input');
    if (chatInput) {
        chatInput.addEventListener('input', () => {
            chatInput.style.height = 'auto';
            chatInput.style.height = Math.min(chatInput.scrollHeight, 100) + 'px';
        });
    }

    // Dev agent textarea auto-resize
    const devAgentInput = document.getElementById('dev-agent-input');
    if (devAgentInput) {
        devAgentInput.addEventListener('input', () => {
            devAgentInput.style.height = 'auto';
            devAgentInput.style.height = Math.min(devAgentInput.scrollHeight, 80) + 'px';
        });
    }

    // Carbon field live-update listeners
    ['wiz-baseline-prod', 'wiz-range-imp', 'wiz-displaced-resource',
     'wiz-specific-prod-units', 'wiz-unit-definition', 'wiz-service-life'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.addEventListener('input', _updateCarbonLabels);
        if (el) el.addEventListener('change', _updateCarbonLabels);
    });

    // Dev editor line numbers sync
    const devEditor = document.getElementById('dev-editor');
    if (devEditor) {
        devEditor.addEventListener('input', _devUpdateLineNumbers);
        devEditor.addEventListener('scroll', () => {
            document.getElementById('dev-line-numbers').scrollTop = devEditor.scrollTop;
        });
        // Ctrl/Cmd+S to save
        devEditor.addEventListener('keydown', (e) => {
            if ((e.ctrlKey || e.metaKey) && e.key === 's') {
                e.preventDefault();
                devSaveFile();
            }
            // Tab key inserts spaces
            if (e.key === 'Tab') {
                e.preventDefault();
                const start = devEditor.selectionStart;
                const end = devEditor.selectionEnd;
                devEditor.value = devEditor.value.substring(0, start) + '    ' + devEditor.value.substring(end);
                devEditor.selectionStart = devEditor.selectionEnd = start + 4;
                _devUpdateLineNumbers();
            }
        });
    }
});

/* =============================================
   MODELS BENCHMARK TAB
   ============================================= */
// ========================================
// VoLo Benchmark System - Complete App
// ========================================

// ========================================
// Models Configuration
// ========================================

const BENCH_MODELS = [
  {
    key: 'haiku',
    label: 'Claude Haiku 4.5',
    type: 'anthropic',
    model: 'claude-haiku-4-5-20251001',
    color: '#38bdf8'
  },
  {
    key: 'sonnet',
    label: 'Claude Sonnet 4',
    type: 'anthropic',
    model: 'claude-sonnet-4-20250514',
    color: '#a78bfa'
  },
  {
    key: 'opus',
    label: 'Claude Opus 4',
    type: 'anthropic',
    model: 'claude-opus-4-20250514',
    color: '#f472b6'
  },
  {
    key: 'refiant',
    label: 'Refiant qwen-rfnt',
    type: 'refiant',
    model: 'qwen-rfnt',
    color: '#4ade80'
  },
];

// In-memory API key storage
let _benchKeys = {
  anthropic: null,
  refiant: null,
};

// ========================================
// Task Prompts - EXACT from original benchmark
// ========================================

const EXTRACT_PROMPT = `You are a venture capital analyst at VoLo Earth Ventures. Extract structured deal data from this financial model summary for XGS Energy.

COMPANY: XGS Energy — next-generation geothermal power using closed-loop, supercritical CO2 systems.
ROUND: Series B, $285M post-money, $250M pre-money, $35M round size
TAM: Global geothermal market ~$25B growing to $50B+ by 2035

FINANCIAL PROJECTIONS (from XGS Enterprise Model, Feb 2026):
Year | MW Operational | Revenue      | EBITDA       | EBITDA Margin
2027 |           30   |    $13.4M    |   -$9.2M     |   -68.7%
2028 |          380   |   $142.3M    |  $112.9M     |    79.3%
2029 |        1,530   |   $469.2M    |  $387.4M     |    82.6%
2030 |        3,180   |   $613.1M    |  $501.5M     |    81.8%
2031 |        5,900   | $1,014.6M    |  $828.0M     |    81.6%
2032 |       10,450   | $1,609.7M    |$1,326.3M     |    82.4%
2033 |       15,450   | $1,792.7M    |$1,435.3M     |    80.1%
2034 |       22,450   | $2,094.7M    |$1,676.9M     |    80.1%
2035 |       30,950   | $2,327.7M    |$1,863.3M     |    80.0%

CARBON IMPACT: Displaces natural gas (CCGT). Each MW produces ~7,000 MWh/yr with 20-year service life.

Extract as JSON (return ONLY valid JSON, no markdown fences):
{
  "company_name": "string",
  "technology_description": "string — 2-3 sentences",
  "tam_millions": number,
  "trl_estimate": integer (1-9),
  "trl_rationale": "string",
  "revenue_cagr_2028_2032": number (decimal),
  "peak_ebitda_margin": number (decimal),
  "mw_2032": number,
  "terminal_revenue_2035_m": number,
  "ev_estimate_at_exit": "string — rough EV range using 12-20x EBITDA",
  "key_risks": ["string","string","string"],
  "carbon_tonnes_displaced_annual_2032": "string — rough estimate",
  "investment_recommendation": "string — 1-2 sentences"
}`;

const DEAL_SYSTEM = `You are the VoLo Earth Ventures AI Deal Agent. You have tools:
1. modify_deal_parameter — change a deal parameter (parameter, value, reasoning)
2. run_scenario_comparison — run "what if" scenarios (scenario_name, parameter_changes)
3. explain_metric — explain a metric (metric_name)
4. suggest_optimal_terms — suggest terms (optimize_for)

When the user asks to change something, USE the tool. When they ask "what if", USE run_scenario_comparison. Be quantitative and concise.`;

const DEAL_USER_MSG = `[DEAL REPORT CONTEXT]
Company: XGS Energy
Archetype: geothermal | TRL: 7 | Stage: Series B
TAM: $25,000M | Check: $3.0M | Pre-money: $250.0M
MOIC (Unconditional): P10=0.00x | P50=1.42x | P90=8.73x | Mean=3.21x
Expected IRR: 24.7%
Survival Rate: 48.2%
P(Loss): 41.3% | P(>3x): 32.1% | P(>10x): 12.4%
EV at Exit: Mean=$2,145.3M | P50=$1,389.7M | P90=$5,823.1M

What if we increased the check size to $5M and moved the TRL down to 5? Run both scenarios and tell me the impact.`;

const CODE_SYSTEM = `You are a senior full-stack developer in the VoLo Earth Ventures Underwriting Engine.
TECH STACK: Python 3.11 + FastAPI, NumPy/SciPy Monte Carlo, Bass diffusion, EBITDA-based EV model.
You have tools: read_file, write_file, edit_file, search_code, list_files.
First read files to understand code, then make targeted edits. Be precise.`;

const CODE_USER_MSG = `I need you to add a new query parameter "discount_pct" to the /api/valuation-comps endpoint that lets callers override the default 20% acquisition discount. The parameter should:
1. Be an optional float between 0 and 50
2. Default to the existing 20% if not provided
3. Be passed through to get_comps_for_archetype or applied after

Before making changes, search the codebase to understand how the acquisition discount is currently used, then describe your plan.`;

const CHART_SYSTEM = `You are a data visualization developer at VoLo Earth Ventures.
You have tools: read_file, write_file, edit_file, search_code, list_files.
When asked to produce a chart, write a COMPLETE self-contained HTML file that uses Chart.js (loaded from CDN) to render the visualization. The file should be fully functional when opened in a browser.`;

const CHART_USER_MSG = `Create a fund performance visualization that overlays our simulated VoLo Fund I performance against Carta VC benchmark data.

CARTA BENCHMARK DATA (FY2024 VC Fund Performance):
{"ages":[1,2,3,4,5,6,7,8,9,10],"tvpi":{"p10":[0.7,0.75,0.8,0.85,0.9,0.95,1.0,1.02,1.03,1.05],"p50":[0.85,0.95,1.05,1.15,1.3,1.45,1.55,1.6,1.62,1.65],"p75":[0.95,1.05,1.15,1.4,1.6,1.8,2.0,2.1,2.15,2.2],"p90":[1.1,1.25,1.5,2.3,2.8,3.2,3.5,3.7,3.8,3.9]}}

SIMULATED VOLO FUND I PERFORMANCE (Monte Carlo P10/P50/P75/P90):
{"ages":[1,2,3,4,5,6,7,8,9,10],"tvpi":{"p10":[0.65,0.70,0.72,0.78,0.85,0.88,0.92,0.97,1.00,1.02],"p50":[0.90,1.02,1.18,1.38,1.55,1.72,1.90,2.05,2.15,2.28],"p75":[1.05,1.22,1.45,1.85,2.15,2.55,2.90,3.10,3.25,3.45],"p90":[1.25,1.55,2.00,2.90,3.50,4.10,4.60,5.00,5.30,5.60]}}

Requirements:
1. Use Chart.js (from CDN: https://cdn.jsdelivr.net/npm/chart.js)
2. X-axis: Fund Age (years 1-10)
3. Y-axis: TVPI (Total Value to Paid-In)
4. Show Carta benchmarks as dashed lines (p10, p50, p75, p90)
5. Show VoLo simulated performance as solid lines (p10, p50, p75, p90)
6. Include a legend distinguishing Carta vs VoLo lines
7. Professional dark theme matching VoLo branding (dark background, clean typography)
8. Title: "VoLo Fund I vs Carta VC Benchmark — TVPI by Fund Age"

Write the complete HTML file to charts/fund_vs_carta.html`;

const RESEARCH_SYSTEM = `You are a research analyst at VoLo Earth Ventures. You have access to web research tools.
When asked to research a company or deal, ALWAYS use the web_search tool first to find relevant information, then use fetch_url to read promising articles. Synthesize findings into actionable investment insights.`;

const RESEARCH_USER_MSG = `[DEAL CONTEXT]
Company: XGS Energy — next-generation geothermal power using closed-loop, supercritical CO2 systems
Stage: Series B ($35M round, $285M post-money)
Technology: Enhanced Geothermal Systems (EGS) with supercritical CO2 as working fluid
Sector: Clean energy / Geothermal
Location: Houston, TX

Research recent news and developments about XGS Energy and the broader geothermal/EGS industry. I need insights on:
1. Any recent funding rounds, partnerships, or milestones for XGS Energy
2. Competitive landscape — other companies in enhanced geothermal (Fervo Energy, Eavor, Sage Geosystems, etc.)
3. DOE/government policy developments affecting geothermal energy
4. Technology readiness and recent pilot/demonstration project results
5. Market trends and utility/corporate offtake agreements in geothermal

Synthesize your findings into a deal-relevant intelligence brief with clear implications for our Series B investment thesis.`;

// ========================================
// Tool Definitions - Deal Agent
// ========================================

const DEAL_TOOLS = [
  {
    name: "modify_deal_parameter",
    description: "Modify a deal parameter and see updated projections",
    input_schema: {
      type: "object",
      properties: {
        parameter: {
          type: "string",
          description: "Parameter to modify (e.g., 'check_size', 'trl', 'pre_money_valuation')"
        },
        value: {
          type: "number",
          description: "New value for the parameter"
        },
        reasoning: {
          type: "string",
          description: "Why this change makes sense"
        }
      },
      required: ["parameter", "value", "reasoning"]
    }
  },
  {
    name: "run_scenario_comparison",
    description: "Run what-if scenarios to compare outcomes",
    input_schema: {
      type: "object",
      properties: {
        scenario_name: {
          type: "string",
          description: "Name of the scenario (e.g., 'Conservative', 'Aggressive')"
        },
        parameter_changes: {
          type: "object",
          description: "Map of parameter names to new values"
        }
      },
      required: ["scenario_name", "parameter_changes"]
    }
  },
  {
    name: "explain_metric",
    description: "Get detailed explanation of a key metric",
    input_schema: {
      type: "object",
      properties: {
        metric_name: {
          type: "string",
          description: "Name of metric (e.g., 'MOIC', 'IRR', 'Survival_Rate')"
        }
      },
      required: ["metric_name"]
    }
  },
  {
    name: "suggest_optimal_terms",
    description: "Get suggestions for optimal deal terms",
    input_schema: {
      type: "object",
      properties: {
        optimize_for: {
          type: "string",
          description: "What to optimize for (e.g., 'downside_protection', 'max_upside', 'risk_balanced')"
        }
      },
      required: ["optimize_for"]
    }
  }
];

// ========================================
// Tool Definitions - Coding Agent
// ========================================

const CODE_TOOLS = [
  {
    name: "read_file",
    description: "Read a file from the codebase",
    input_schema: {
      type: "object",
      properties: {
        file_path: {
          type: "string",
          description: "Path to the file to read"
        }
      },
      required: ["file_path"]
    }
  },
  {
    name: "search_code",
    description: "Search for code patterns in the codebase",
    input_schema: {
      type: "object",
      properties: {
        pattern: {
          type: "string",
          description: "Search pattern or keyword"
        },
        file_type: {
          type: "string",
          description: "File type filter (e.g., 'py', 'js')"
        }
      },
      required: ["pattern"]
    }
  },
  {
    name: "edit_file",
    description: "Edit a file in the codebase",
    input_schema: {
      type: "object",
      properties: {
        file_path: {
          type: "string",
          description: "Path to the file to edit"
        },
        changes: {
          type: "string",
          description: "Description of changes to make"
        }
      },
      required: ["file_path", "changes"]
    }
  },
  {
    name: "list_files",
    description: "List files in a directory",
    input_schema: {
      type: "object",
      properties: {
        directory: {
          type: "string",
          description: "Directory path to list"
        }
      },
      required: ["directory"]
    }
  }
];

// ========================================
// Tool Definitions - Chart Generation
// ========================================

const CHART_TOOLS = [
  {
    name: "read_file",
    description: "Read a file from the codebase",
    input_schema: {
      type: "object",
      properties: {
        file_path: {
          type: "string",
          description: "Path to the file to read"
        }
      },
      required: ["file_path"]
    }
  },
  {
    name: "write_file",
    description: "Write a file to the codebase",
    input_schema: {
      type: "object",
      properties: {
        file_path: {
          type: "string",
          description: "Path where to write the file"
        },
        content: {
          type: "string",
          description: "File content"
        }
      },
      required: ["file_path", "content"]
    }
  },
  {
    name: "search_code",
    description: "Search for code patterns in the codebase",
    input_schema: {
      type: "object",
      properties: {
        pattern: {
          type: "string",
          description: "Search pattern or keyword"
        }
      },
      required: ["pattern"]
    }
  },
  {
    name: "edit_file",
    description: "Edit a file in the codebase",
    input_schema: {
      type: "object",
      properties: {
        file_path: {
          type: "string",
          description: "Path to the file to edit"
        },
        changes: {
          type: "string",
          description: "Description of changes to make"
        }
      },
      required: ["file_path", "changes"]
    }
  }
];

// ========================================
// Tool Definitions - Web Research
// ========================================

const RESEARCH_TOOLS = [
  {
    name: "web_search",
    description: "Search the web for information",
    input_schema: {
      type: "object",
      properties: {
        query: {
          type: "string",
          description: "Search query"
        }
      },
      required: ["query"]
    }
  },
  {
    name: "fetch_url",
    description: "Fetch and read content from a URL",
    input_schema: {
      type: "object",
      properties: {
        url: {
          type: "string",
          description: "URL to fetch"
        }
      },
      required: ["url"]
    }
  },
  {
    name: "write_file",
    description: "Write research findings to a file",
    input_schema: {
      type: "object",
      properties: {
        file_path: {
          type: "string",
          description: "Path where to write the findings"
        },
        content: {
          type: "string",
          description: "Research findings"
        }
      },
      required: ["file_path", "content"]
    }
  }
];

// ========================================
// Model Preferences (per-task model selection)
// ========================================

async function loadModelPreferences() {
  try {
    const r = await fetch('/api/model-preferences', { headers: _rvmHeaders() });
    if (!r.ok) return;
    const data = await r.json();
    const prefs = data.preferences || {};
    for (const [task, model] of Object.entries(prefs)) {
      const sel = document.getElementById('model-pref-' + task);
      if (sel) sel.value = model;
    }
    const statusEl = document.getElementById('model-pref-status');
    if (statusEl) statusEl.textContent = 'Loaded';
    setTimeout(() => { if (statusEl) statusEl.textContent = ''; }, 2000);
  } catch (e) {
    console.warn('Could not load model preferences:', e);
  }
}

async function saveModelPref(taskKey, modelKey) {
  const statusEl = document.getElementById('model-pref-status');
  if (statusEl) statusEl.textContent = 'Saving...';
  try {
    const r = await fetch('/api/model-preferences', {
      method: 'POST',
      headers: _rvmHeaders(),
      body: JSON.stringify({ task_key: taskKey, model_key: modelKey }),
    });
    if (r.ok) {
      if (statusEl) statusEl.textContent = 'Saved ✓';
      setTimeout(() => { if (statusEl) statusEl.textContent = ''; }, 2000);
    } else {
      const d = await r.json();
      if (statusEl) statusEl.textContent = 'Error: ' + (d.error || r.statusText);
    }
  } catch (e) {
    if (statusEl) statusEl.textContent = 'Save failed';
    console.error('Save model pref failed:', e);
  }
}

// ========================================
// Manual Financial Editing
// ========================================

let _fmEditMode = false;
let _fmOriginalValues = {};  // backup of original cell innerHTML
let _prerunEditMode = false;
let _prerunOriginalValues = {};

// ── Pre-run editing (Step 3, before generating report) ──────────────────────

function togglePreRunFinancialEdit() {
    if (_prerunEditMode) {
        cancelPreRunFinancialEdit();
        return;
    }
    _prerunEditMode = true;
    _prerunOriginalValues = {};
    const btn = document.getElementById('btn-prerun-edit');
    if (btn) { btn.textContent = 'Cancel Edit'; btn.style.color = '#dc3545'; }

    // Add save button if not present
    let actions = document.getElementById('prerun-edit-actions');
    if (!actions) {
        actions = document.createElement('div');
        actions.id = 'prerun-edit-actions';
        actions.style.cssText = 'margin-top:10px;text-align:right;';
        actions.innerHTML = `<button onclick="savePreRunFinancialEdit()" style="padding:6px 16px;border:none;border-radius:4px;background:#28a745;color:#fff;cursor:pointer;font-size:0.8rem;font-weight:600;">Apply Changes</button>`;
        const table = document.querySelector('.prerun-fin-table');
        if (table) table.parentElement.parentElement.appendChild(actions);
    }
    actions.style.display = 'block';

    document.querySelectorAll('.prerun-fm-cell').forEach(td => {
        const key = `${td.dataset.metric}_${td.dataset.year}`;
        _prerunOriginalValues[key] = td.innerHTML;
        const raw = td.textContent.trim();
        let numVal = '';
        if (raw !== '--') {
            // Parse formatted values like "$1.2M", "-$335.9K", "$0"
            let str = raw.replace(/[$,]/g, '');
            if (str.endsWith('B')) numVal = String(parseFloat(str) * 1e9);
            else if (str.endsWith('M')) numVal = String(parseFloat(str) * 1e6);
            else if (str.endsWith('K')) numVal = String(parseFloat(str) * 1e3);
            else numVal = str;
        }
        td.innerHTML = `<input type="text" value="${numVal}"
            data-metric="${td.dataset.metric}" data-year="${td.dataset.year}"
            style="width:100%;padding:2px 4px;font-size:0.72rem;text-align:right;border:1px solid #4a9eff;border-radius:3px;background:#f0f7ff;font-family:inherit;box-sizing:border-box;"
            onfocus="this.select()" />`;
    });
}

function cancelPreRunFinancialEdit() {
    _prerunEditMode = false;
    const btn = document.getElementById('btn-prerun-edit');
    if (btn) { btn.textContent = '\u270E Edit Values'; btn.style.color = ''; }
    const actions = document.getElementById('prerun-edit-actions');
    if (actions) actions.style.display = 'none';

    document.querySelectorAll('.prerun-fm-cell').forEach(td => {
        const key = `${td.dataset.metric}_${td.dataset.year}`;
        if (_prerunOriginalValues[key] !== undefined) td.innerHTML = _prerunOriginalValues[key];
    });
    _prerunOriginalValues = {};
}

function savePreRunFinancialEdit() {
    if (!_wizFmData) { showToast('No financial model loaded'); return; }

    // Collect edited values and push them into _wizFmData.financials
    document.querySelectorAll('.prerun-fin-table input[data-metric]').forEach(inp => {
        const metric = inp.dataset.metric;
        const year = inp.dataset.year;
        const val = inp.value.trim();
        if (!_wizFmData.financials[metric]) _wizFmData.financials[metric] = {};
        _wizFmData.financials[metric][year] = (val === '' || val === '--') ? null : parseFloat(val.replace(/,/g, ''));
    });

    _prerunEditMode = false;
    showToast('Financial values updated — run the deal to apply');

    // Re-render the preview with updated values
    const el = document.getElementById('wiz-fm-review-inline');
    if (el) _wizRenderFmInto(el);

    // Also re-merge into extraction so revenue projections update
    _wizMergeFmIntoExtraction();
}

// ── Post-run editing (Step 4, saved report) ─────────────────────────────────

function toggleFinancialEdit(reportId) {
    if (_fmEditMode) {
        cancelFinancialEdit();
        return;
    }
    _fmEditMode = true;
    _fmOriginalValues = {};
    const btn = document.getElementById('btn-edit-financials');
    if (btn) { btn.textContent = 'Cancel Edit'; btn.style.color = '#dc3545'; }

    const actions = document.getElementById('fm-edit-actions');
    if (actions) actions.style.display = 'block';

    // Convert each .fm-cell to an editable input
    document.querySelectorAll('#fm-financials-table .fm-cell').forEach(td => {
        const metric = td.dataset.metric;
        const year = td.dataset.year;
        const key = `${metric}_${year}`;
        _fmOriginalValues[key] = td.innerHTML;

        const raw = td.textContent.trim();
        // Parse the displayed value back to a number
        let numVal = '';
        if (raw !== '--') {
            numVal = raw.replace(/[$,]/g, '').replace(/[()]/g, m => m === '(' ? '-' : '');
        }
        td.innerHTML = `<input type="text" value="${numVal}"
            data-metric="${metric}" data-year="${year}"
            style="width:100%;padding:3px 6px;font-size:0.8rem;text-align:right;border:1px solid #4a9eff;border-radius:3px;background:#f0f7ff;font-family:inherit;box-sizing:border-box;"
            onfocus="this.select()" />`;
    });
}

function cancelFinancialEdit() {
    _fmEditMode = false;
    const btn = document.getElementById('btn-edit-financials');
    if (btn) { btn.textContent = 'Edit Financials'; btn.style.color = ''; }

    const actions = document.getElementById('fm-edit-actions');
    if (actions) actions.style.display = 'none';

    // Restore original values
    document.querySelectorAll('#fm-financials-table .fm-cell').forEach(td => {
        const key = `${td.dataset.metric}_${td.dataset.year}`;
        if (_fmOriginalValues[key] !== undefined) {
            td.innerHTML = _fmOriginalValues[key];
        }
    });
    _fmOriginalValues = {};
}

async function saveFinancialEdit(reportId) {
    if (!reportId) { showToast('No report loaded'); return; }

    // Collect all values from inputs
    const financials = {};
    document.querySelectorAll('#fm-financials-table input[data-metric]').forEach(inp => {
        const metric = inp.dataset.metric;
        const year = inp.dataset.year;
        if (!financials[metric]) financials[metric] = {};
        const val = inp.value.trim();
        financials[metric][year] = val === '' || val === '--' ? null : parseFloat(val.replace(/,/g, ''));
    });

    const btn = document.querySelector('#fm-edit-actions button:last-child');
    if (btn) { btn.textContent = 'Saving...'; btn.disabled = true; }

    try {
        const r = await fetch(`/api/deal-pipeline/report/${reportId}/financials`, {
            method: 'PUT',
            headers: _rvmHeaders(),
            body: JSON.stringify({ financials }),
        });
        if (r.ok) {
            showToast('Financials saved successfully');
            _fmEditMode = false;
            // Reload the report to reflect changes
            await wizLoadReport(reportId);
        } else {
            const d = await r.json();
            showToast('Error: ' + (d.detail || d.error || r.statusText));
        }
    } catch (e) {
        showToast('Save failed: ' + e.message);
        console.error('Financial edit save failed:', e);
    } finally {
        if (btn) { btn.textContent = 'Save Changes'; btn.disabled = false; }
    }
}

// ========================================
// API Key Management
// ========================================

async function benchLoadKeys() {
  try {
    const response = await fetch('/api/keys');
    if (!response.ok) {
      throw new Error(`Failed to load keys: ${response.statusText}`);
    }
    const data = await response.json();
    _benchKeys.anthropic = data.anthropic || null;
    _benchKeys.refiant = data.refiant || null;

    updateKeyStatusDisplay();
    return true;
  } catch (error) {
    console.error('Error loading keys:', error);
    updateKeyStatusDisplay(true);
    return false;
  }
}

function updateKeyStatusDisplay(error = false) {
  const anthropicStatus = document.getElementById('bench-anthropic-status');
  const refiantStatus = document.getElementById('bench-refiant-status');

  if (error) {
    anthropicStatus?.classList.add('error');
    refiantStatus?.classList.add('error');
  } else {
    if (_benchKeys.anthropic) {
      anthropicStatus?.classList.add('loaded');
      anthropicStatus?.classList.remove('error');
    } else {
      anthropicStatus?.classList.remove('loaded');
      anthropicStatus?.classList.add('error');
    }

    if (_benchKeys.refiant) {
      refiantStatus?.classList.add('loaded');
      refiantStatus?.classList.remove('error');
    } else {
      refiantStatus?.classList.remove('loaded');
      refiantStatus?.classList.add('error');
    }
  }
}

async function benchTestKey(provider) {
  const key = provider === 'anthropic' ? _benchKeys.anthropic : _benchKeys.refiant;
  if (!key) return false;

  try {
    if (provider === 'anthropic') {
      const result = await benchCallAnthropicSimple(key, 'claude-opus-4-20250514', 'Say "OK" in one word.');
      return !!result.text;
    } else {
      // Refiant test
      return true;
    }
  } catch (error) {
    console.error(`Error testing ${provider} key:`, error);
    return false;
  }
}

// ========================================
// API Call Functions - Anthropic
// ========================================

async function benchCallAnthropicSimple(key, model, prompt) {
  const r = await fetch('https://api.anthropic.com/v1/messages', {
    method: 'POST',
    headers: {
      'x-api-key': key,
      'anthropic-version': '2023-06-01',
      'content-type': 'application/json',
      'anthropic-dangerous-direct-browser-access': 'true'
    },
    body: JSON.stringify({
      model,
      max_tokens: 1500,
      messages: [{ role: 'user', content: prompt }]
    }),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${(await r.text()).substring(0, 200)}`);
  const d = await r.json();
  return {
    model: d.model,
    tokens_in: d.usage?.input_tokens || 0,
    tokens_out: d.usage?.output_tokens || 0,
    text: d.content?.[0]?.text || '',
    toolCalls: []
  };
}

async function benchCallAnthropicTools(key, model, system, userMsg, tools) {
  const r = await fetch('https://api.anthropic.com/v1/messages', {
    method: 'POST',
    headers: {
      'x-api-key': key,
      'anthropic-version': '2023-06-01',
      'content-type': 'application/json',
      'anthropic-dangerous-direct-browser-access': 'true'
    },
    body: JSON.stringify({
      model,
      max_tokens: 4096,
      system,
      tools,
      messages: [{ role: 'user', content: userMsg }]
    }),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${(await r.text()).substring(0, 200)}`);
  const d = await r.json();

  const toolCalls = [];
  const textParts = [];

  for (const block of d.content || []) {
    if (block.type === 'text') {
      textParts.push(block.text);
    } else if (block.type === 'tool_use') {
      toolCalls.push({
        name: block.name,
        id: block.id,
        input: block.input
      });
    }
  }

  return {
    model: d.model,
    tokens_in: d.usage?.input_tokens || 0,
    tokens_out: d.usage?.output_tokens || 0,
    text: textParts.join('\n'),
    toolCalls
  };
}

async function benchCallRefiantSimple(key, model, prompt) {
  const r = await fetch('https://api.refiant.ai/v1/messages', {
    method: 'POST',
    headers: {
      'authorization': `Bearer ${key}`,
      'content-type': 'application/json'
    },
    body: JSON.stringify({
      model,
      max_tokens: 1500,
      messages: [{ role: 'user', content: prompt }]
    }),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${(await r.text()).substring(0, 200)}`);
  const d = await r.json();
  return {
    model: d.model,
    tokens_in: d.usage?.input_tokens || 0,
    tokens_out: d.usage?.output_tokens || 0,
    text: d.choices?.[0]?.message?.content || '',
    toolCalls: []
  };
}

async function benchCallRefiantTools(key, model, system, userMsg, tools) {
  const r = await fetch('https://api.refiant.ai/v1/messages', {
    method: 'POST',
    headers: {
      'authorization': `Bearer ${key}`,
      'content-type': 'application/json'
    },
    body: JSON.stringify({
      model,
      max_tokens: 4096,
      system,
      tools: tools.map(t => ({ type: 'function', function: t })),
      messages: [{ role: 'user', content: userMsg }]
    }),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${(await r.text()).substring(0, 200)}`);
  const d = await r.json();

  const toolCalls = [];
  const textParts = [];
  const choice = d.choices?.[0];

  if (choice?.message?.content) {
    if (typeof choice.message.content === 'string') {
      textParts.push(choice.message.content);
    } else {
      for (const block of choice.message.content) {
        if (block.type === 'text') {
          textParts.push(block.text);
        } else if (block.type === 'tool_use') {
          toolCalls.push({
            name: block.name,
            id: block.id,
            input: block.input
          });
        }
      }
    }
  }

  return {
    model: d.model,
    tokens_in: d.usage?.prompt_tokens || 0,
    tokens_out: d.usage?.completion_tokens || 0,
    text: textParts.join('\n'),
    toolCalls
  };
}

// ========================================
// Scoring Functions
// ========================================

function parseJSON(text) {
  try {
    const jsonMatch = text.match(/\{[\s\S]*\}/);
    if (jsonMatch) {
      return JSON.parse(jsonMatch[0]);
    }
  } catch (e) {
    // Continue to try other parsing methods
  }
  return null;
}

function scoreExtraction(parsed) {
  let total = 0;
  const max = 10;
  const details = [];

  if (!parsed) {
    return { total: 0, max, details: [{ check: 'Valid JSON', pass: false }] };
  }

  // company_name
  if (parsed.company_name === 'XGS Energy') {
    total += 1;
    details.push({ check: 'Company name correct', pass: true });
  } else {
    details.push({ check: 'Company name correct', pass: false });
  }

  // technology_description present and reasonable length
  if (parsed.technology_description && parsed.technology_description.length > 20) {
    total += 1;
    details.push({ check: 'Technology description present', pass: true });
  } else {
    details.push({ check: 'Technology description present', pass: false });
  }

  // tam_millions reasonable (20B-50B = 20000-50000M)
  if (parsed.tam_millions && parsed.tam_millions >= 20000 && parsed.tam_millions <= 50000) {
    total += 1;
    details.push({ check: 'TAM in reasonable range', pass: true });
  } else {
    details.push({ check: 'TAM in reasonable range', pass: false });
  }

  // trl_estimate between 6-8 (reasonable for Series B)
  if (parsed.trl_estimate && parsed.trl_estimate >= 6 && parsed.trl_estimate <= 8) {
    total += 1;
    details.push({ check: 'TRL estimate reasonable', pass: true });
  } else {
    details.push({ check: 'TRL estimate reasonable', pass: false });
  }

  // revenue_cagr_2028_2032 should be ~50%
  const expectedCAGR = Math.pow(1609.7 / 142.3, 1 / 4) - 1;
  if (parsed.revenue_cagr_2028_2032 && Math.abs(parsed.revenue_cagr_2028_2032 - expectedCAGR) < 0.1) {
    total += 2;
    details.push({ check: 'Revenue CAGR calculated correctly', pass: true });
  } else {
    details.push({ check: 'Revenue CAGR calculated correctly', pass: false });
  }

  // peak_ebitda_margin should be ~82%
  if (parsed.peak_ebitda_margin && parsed.peak_ebitda_margin >= 0.79 && parsed.peak_ebitda_margin <= 0.83) {
    total += 1;
    details.push({ check: 'Peak EBITDA margin accurate', pass: true });
  } else {
    details.push({ check: 'Peak EBITDA margin accurate', pass: false });
  }

  // mw_2032 should be 10,450
  if (parsed.mw_2032 === 10450) {
    total += 1;
    details.push({ check: 'MW 2032 value correct', pass: true });
  } else {
    details.push({ check: 'MW 2032 value correct', pass: false });
  }

  // terminal_revenue_2035_m should be 2327.7
  if (parsed.terminal_revenue_2035_m && Math.abs(parsed.terminal_revenue_2035_m - 2327.7) < 10) {
    total += 1;
    details.push({ check: 'Terminal revenue accurate', pass: true });
  } else {
    details.push({ check: 'Terminal revenue accurate', pass: false });
  }

  // key_risks array present with 3 items
  if (Array.isArray(parsed.key_risks) && parsed.key_risks.length === 3) {
    total += 1;
    details.push({ check: 'Risk array complete', pass: true });
  } else {
    details.push({ check: 'Risk array complete', pass: false });
  }

  // investment_recommendation present
  if (parsed.investment_recommendation && parsed.investment_recommendation.length > 10) {
    total += 1;
    details.push({ check: 'Investment recommendation present', pass: true });
  } else {
    details.push({ check: 'Investment recommendation present', pass: false });
  }

  return { total, max, details };
}

function scoreDealAgent(text, toolCalls) {
  let total = 0;
  const max = 10;
  const details = [];

  // Tool usage (4 points)
  const toolNames = toolCalls.map(t => t.name);
  if (toolNames.includes('run_scenario_comparison')) {
    total += 2;
    details.push({ check: 'Used scenario comparison tool', pass: true });
  } else {
    details.push({ check: 'Used scenario comparison tool', pass: false });
  }

  if (toolNames.includes('modify_deal_parameter')) {
    total += 1;
    details.push({ check: 'Used parameter modification tool', pass: true });
  } else {
    details.push({ check: 'Used parameter modification tool', pass: false });
  }

  if (toolNames.includes('explain_metric') || toolNames.includes('suggest_optimal_terms')) {
    total += 1;
    details.push({ check: 'Used explanation/suggestion tools', pass: true });
  } else {
    details.push({ check: 'Used explanation/suggestion tools', pass: false });
  }

  // Analysis quality (6 points)
  if (text.toLowerCase().includes('check') && text.toLowerCase().includes('5m')) {
    total += 2;
    details.push({ check: 'Addressed check size increase', pass: true });
  } else {
    details.push({ check: 'Addressed check size increase', pass: false });
  }

  if (text.toLowerCase().includes('trl') && text.toLowerCase().includes('5')) {
    total += 2;
    details.push({ check: 'Addressed TRL change', pass: true });
  } else {
    details.push({ check: 'Addressed TRL change', pass: false });
  }

  if (text.toLowerCase().includes('impact') || text.toLowerCase().includes('scenario')) {
    total += 2;
    details.push({ check: 'Quantified impact of changes', pass: true });
  } else {
    details.push({ check: 'Quantified impact of changes', pass: false });
  }

  return { total, max, details };
}

function scoreCodingAgent(text, toolCalls) {
  let total = 0;
  const max = 10;
  const details = [];

  const toolNames = toolCalls.map(t => t.name);

  // Code exploration (3 points)
  if (toolNames.includes('search_code')) {
    total += 1;
    details.push({ check: 'Searched for discount usage', pass: true });
  } else {
    details.push({ check: 'Searched for discount usage', pass: false });
  }

  if (toolNames.includes('read_file')) {
    total += 1;
    details.push({ check: 'Read relevant files', pass: true });
  } else {
    details.push({ check: 'Read relevant files', pass: false });
  }

  if (text.toLowerCase().includes('plan') || text.toLowerCase().includes('strategy')) {
    total += 1;
    details.push({ check: 'Described implementation plan', pass: true });
  } else {
    details.push({ check: 'Described implementation plan', pass: false });
  }

  // Edit quality (7 points)
  if (toolNames.includes('edit_file')) {
    total += 2;
    details.push({ check: 'Made code edits', pass: true });
  } else {
    details.push({ check: 'Made code edits', pass: false });
  }

  if (text.includes('discount_pct') || text.includes('discount')) {
    total += 2;
    details.push({ check: 'Referenced discount_pct parameter', pass: true });
  } else {
    details.push({ check: 'Referenced discount_pct parameter', pass: false });
  }

  if (text.includes('0') && text.includes('50') && text.includes('20')) {
    total += 2;
    details.push({ check: 'Mentioned parameter constraints', pass: true });
  } else {
    details.push({ check: 'Mentioned parameter constraints', pass: false });
  }

  if (text.toLowerCase().includes('default')) {
    total += 1;
    details.push({ check: 'Addressed default value handling', pass: true });
  } else {
    details.push({ check: 'Addressed default value handling', pass: false });
  }

  return { total, max, details };
}

function scoreChartGeneration(text, toolCalls) {
  let total = 0;
  const max = 10;
  const details = [];

  const toolNames = toolCalls.map(t => t.name);

  // File operations (3 points)
  if (toolNames.includes('write_file')) {
    total += 2;
    details.push({ check: 'Generated HTML file', pass: true });
  } else {
    details.push({ check: 'Generated HTML file', pass: false });
  }

  if (text.includes('charts/fund_vs_carta.html') || text.includes('fund_vs_carta')) {
    total += 1;
    details.push({ check: 'Correct file path', pass: true });
  } else {
    details.push({ check: 'Correct file path', pass: false });
  }

  // Chart implementation (7 points)
  if (text.includes('Chart.js') || text.includes('chart.js')) {
    total += 2;
    details.push({ check: 'Used Chart.js library', pass: true });
  } else {
    details.push({ check: 'Used Chart.js library', pass: false });
  }

  if (text.includes('Carta') && text.includes('VoLo')) {
    total += 2;
    details.push({ check: 'Included both benchmarks', pass: true });
  } else {
    details.push({ check: 'Included both benchmarks', pass: false });
  }

  if (text.includes('TVPI') || text.includes('tvpi')) {
    total += 1;
    details.push({ check: 'Used correct metric (TVPI)', pass: true });
  } else {
    details.push({ check: 'Used correct metric (TVPI)', pass: false });
  }

  if (text.includes('Fund Age') || text.includes('fund age')) {
    total += 1;
    details.push({ check: 'X-axis labeled correctly', pass: true });
  } else {
    details.push({ check: 'X-axis labeled correctly', pass: false });
  }

  if (text.includes('legend') || text.includes('Legend') || text.includes('dashed') || text.includes('solid')) {
    total += 1;
    details.push({ check: 'Included visual distinction (legend/line styles)', pass: true });
  } else {
    details.push({ check: 'Included visual distinction (legend/line styles)', pass: false });
  }

  return { total, max, details };
}

function scoreWebResearch(text, toolCalls) {
  let total = 0;
  const max = 10;
  const details = [];

  const toolNames = toolCalls.map(t => t.name);

  // Research methodology (3 points)
  if (toolNames.includes('web_search')) {
    total += 1;
    details.push({ check: 'Performed web search', pass: true });
  } else {
    details.push({ check: 'Performed web search', pass: false });
  }

  if (toolNames.includes('fetch_url')) {
    total += 1;
    details.push({ check: 'Fetched URLs for details', pass: true });
  } else {
    details.push({ check: 'Fetched URLs for details', pass: false });
  }

  if (toolNames.includes('write_file')) {
    total += 1;
    details.push({ check: 'Documented findings', pass: true });
  } else {
    details.push({ check: 'Documented findings', pass: false });
  }

  // Content quality (7 points)
  if (text.includes('XGS Energy') || text.toLowerCase().includes('xgs')) {
    total += 2;
    details.push({ check: 'Researched XGS Energy', pass: true });
  } else {
    details.push({ check: 'Researched XGS Energy', pass: false });
  }

  if (text.toLowerCase().includes('geothermal') || text.toLowerCase().includes('egs')) {
    total += 1;
    details.push({ check: 'Covered geothermal industry', pass: true });
  } else {
    details.push({ check: 'Covered geothermal industry', pass: false });
  }

  if (text.toLowerCase().includes('fervo') || text.toLowerCase().includes('eavor') || text.toLowerCase().includes('sage')) {
    total += 1;
    details.push({ check: 'Identified competitors', pass: true });
  } else {
    details.push({ check: 'Identified competitors', pass: false });
  }

  if (text.toLowerCase().includes('doe') || text.toLowerCase().includes('policy') || text.toLowerCase().includes('government')) {
    total += 1;
    details.push({ check: 'Covered policy/regulatory landscape', pass: true });
  } else {
    details.push({ check: 'Covered policy/regulatory landscape', pass: false });
  }

  if (text.toLowerCase().includes('offtake') || text.toLowerCase().includes('utility') || text.toLowerCase().includes('contract')) {
    total += 1;
    details.push({ check: 'Covered market/offtake agreements', pass: true });
  } else {
    details.push({ check: 'Covered market/offtake agreements', pass: false });
  }

  return { total, max, details };
}

// ========================================
// Task Runner
// ========================================

async function benchRunTask(modelKey, taskKey) {
  const model = BENCH_MODELS.find(m => m.key === modelKey);
  if (!model) return;

  const statusEl = document.getElementById(`bench-task-${modelKey}-${taskKey}-status`);
  const scoreEl = document.getElementById(`bench-task-${modelKey}-${taskKey}-score`);
  const metricsEl = document.getElementById(`bench-task-${modelKey}-${taskKey}-metrics`);
  const responseEl = document.getElementById(`bench-task-${modelKey}-${taskKey}-response`);

  if (statusEl) statusEl.textContent = 'Running';
  if (statusEl) statusEl.className = 'bench-task-status running';

  try {
    let result;
    const startTime = performance.now();

    if (taskKey === 'extract') {
      result = await benchRunExtractTask(model);
    } else if (taskKey === 'deal') {
      result = await benchRunDealTask(model);
    } else if (taskKey === 'code') {
      result = await benchRunCodeTask(model);
    } else if (taskKey === 'chart') {
      result = await benchRunChartTask(model);
    } else if (taskKey === 'research') {
      result = await benchRunResearchTask(model);
    }

    const endTime = performance.now();
    const responseTime = ((endTime - startTime) / 1000).toFixed(2);

    // Score the result
    let scoring;
    if (taskKey === 'extract') {
      const parsed = parseJSON(result.text);
      scoring = scoreExtraction(parsed);
    } else if (taskKey === 'deal') {
      scoring = scoreDealAgent(result.text, result.toolCalls || []);
    } else if (taskKey === 'code') {
      scoring = scoreCodingAgent(result.text, result.toolCalls || []);
    } else if (taskKey === 'chart') {
      scoring = scoreChartGeneration(result.text, result.toolCalls || []);
    } else if (taskKey === 'research') {
      scoring = scoreWebResearch(result.text, result.toolCalls || []);
    }

    // Update UI
    if (statusEl) statusEl.textContent = 'Complete';
    if (statusEl) statusEl.className = 'bench-task-status complete';

    benchRenderScore(`bench-task-${modelKey}-${taskKey}-score`, scoring);
    if (scoreEl) scoreEl.style.display = 'block';

    const metricsText = `Tokens: ${result.tokens_in}→${result.tokens_out} | Time: ${responseTime}s`;
    if (metricsEl) metricsEl.textContent = metricsText;

    if (responseEl) responseEl.textContent = result.text.substring(0, 500);

  } catch (error) {
    console.error('Task error:', error);
    if (statusEl) statusEl.textContent = 'Error';
    if (statusEl) statusEl.className = 'bench-task-status error';
    if (responseEl) responseEl.textContent = `Error: ${error.message}`;
  }
}

async function benchRunExtractTask(model) {
  if (model.type === 'anthropic') {
    return await benchCallAnthropicSimple(_benchKeys.anthropic, model.model, EXTRACT_PROMPT);
  } else {
    return await benchCallRefiantSimple(_benchKeys.refiant, model.model, EXTRACT_PROMPT);
  }
}

async function benchRunDealTask(model) {
  if (model.type === 'anthropic') {
    return await benchCallAnthropicTools(_benchKeys.anthropic, model.model, DEAL_SYSTEM, DEAL_USER_MSG, DEAL_TOOLS);
  } else {
    return await benchCallRefiantTools(_benchKeys.refiant, model.model, DEAL_SYSTEM, DEAL_USER_MSG, DEAL_TOOLS);
  }
}

async function benchRunCodeTask(model) {
  if (model.type === 'anthropic') {
    return await benchCallAnthropicTools(_benchKeys.anthropic, model.model, CODE_SYSTEM, CODE_USER_MSG, CODE_TOOLS);
  } else {
    return await benchCallRefiantTools(_benchKeys.refiant, model.model, CODE_SYSTEM, CODE_USER_MSG, CODE_TOOLS);
  }
}

async function benchRunChartTask(model) {
  if (model.type === 'anthropic') {
    return await benchCallAnthropicTools(_benchKeys.anthropic, model.model, CHART_SYSTEM, CHART_USER_MSG, CHART_TOOLS, 8192);
  } else {
    return await benchCallRefiantTools(_benchKeys.refiant, model.model, CHART_SYSTEM, CHART_USER_MSG, CHART_TOOLS);
  }
}

async function benchRunResearchTask(model) {
  if (model.type === 'anthropic') {
    return await benchCallAnthropicTools(_benchKeys.anthropic, model.model, RESEARCH_SYSTEM, RESEARCH_USER_MSG, RESEARCH_TOOLS);
  } else {
    return await benchCallRefiantTools(_benchKeys.refiant, model.model, RESEARCH_SYSTEM, RESEARCH_USER_MSG, RESEARCH_TOOLS);
  }
}

async function benchRunAllForModel(modelKey) {
  const tasks = ['extract', 'deal', 'code', 'chart', 'research'];
  for (const taskKey of tasks) {
    await benchRunTask(modelKey, taskKey);
  }
}

// ========================================
// Results Display
// ========================================

function benchRenderScore(elementId, scoring) {
  const el = document.getElementById(elementId);
  if (!el) return;

  const percentage = Math.round((scoring.total / scoring.max) * 100);
  const statusClass = percentage >= 80 ? 'pass' : percentage >= 50 ? 'partial' : 'fail';

  let html = `
    <div class="bench-score-header">
      <div class="bench-score-value">${scoring.total}/${scoring.max}</div>
      <div class="bench-score-percentage bench-score-${statusClass}">${percentage}%</div>
    </div>
    <table class="bench-details-table">
      <thead>
        <tr>
          <th>Check</th>
          <th>Status</th>
        </tr>
      </thead>
      <tbody>
  `;

  for (const detail of scoring.details) {
    const status = detail.pass ? '✓ Pass' : '✗ Fail';
    html += `<tr><td>${detail.check}</td><td>${status}</td></tr>`;
  }

  html += `</tbody></table>`;
  el.innerHTML = html;
}

function benchRenderComparison(results) {
  const resultsEl = document.getElementById('bench-comparison-results');
  const tableEl = document.getElementById('bench-comparison-table');
  const rankingsEl = document.getElementById('bench-comparison-rankings');

  if (!resultsEl || !tableEl || !rankingsEl) return;

  // Build table
  let tableHtml = '<table class="bench-comparison-table"><thead><tr><th>Task</th>';
  const selectedModels = BENCH_MODELS.filter(m => document.getElementById(`bench-compare-${m.key}`)?.checked);
  for (const model of selectedModels) {
    tableHtml += `<th>${model.label}</th>`;
  }
  tableHtml += '</tr></thead><tbody>';

  const tasks = ['extract', 'deal', 'code', 'chart', 'research'];
  for (const task of tasks) {
    tableHtml += `<tr><td class="bench-task-name">${task}</td>`;
    for (const model of selectedModels) {
      const key = `${model.key}-${task}`;
      const score = results[key] || { total: 0, max: 10 };
      const percentage = Math.round((score.total / score.max) * 100);
      const statusClass = percentage >= 80 ? 'pass' : percentage >= 50 ? 'partial' : 'fail';
      tableHtml += `<td class="bench-score-cell ${statusClass}">${score.total}/${score.max}</td>`;
    }
    tableHtml += '</tr>';
  }

  tableHtml += '</tbody></table>';
  tableEl.innerHTML = tableHtml;

  // Build rankings
  const modelScores = {};
  for (const model of selectedModels) {
    let total = 0;
    let max = 0;
    for (const task of tasks) {
      const key = `${model.key}-${task}`;
      const score = results[key] || { total: 0, max: 10 };
      total += score.total;
      max += score.max;
    }
    modelScores[model.key] = { label: model.label, total, max };
  }

  const sorted = Object.entries(modelScores).sort((a, b) => b[1].total - a[1].total);

  let rankingsHtml = '<h4 class="bench-rankings-title">Overall Rankings</h4>';
  for (let i = 0; i < sorted.length; i++) {
    const [key, data] = sorted[i];
    const percentage = Math.round((data.total / data.max) * 100);
    rankingsHtml += `
      <div class="bench-ranking-item">
        <div class="bench-ranking-position">${i + 1}</div>
        <div class="bench-ranking-model">${data.label}</div>
        <div class="bench-ranking-score">${data.total}/${data.max} (${percentage}%)</div>
      </div>
    `;
  }

  rankingsEl.innerHTML = rankingsHtml;
  resultsEl.style.display = 'block';
}

async function benchRunComparison() {
  const selectedModels = BENCH_MODELS.filter(m => document.getElementById(`bench-compare-${m.key}`)?.checked);
  const selectedTasks = ['extract', 'deal', 'code', 'chart', 'research'].filter(t => document.getElementById(`bench-task-${t}`)?.checked);

  const results = {};

  for (const model of selectedModels) {
    for (const task of selectedTasks) {
      await benchRunTask(model.key, task);
      // Get the score from the DOM element
      const scoreEl = document.getElementById(`bench-task-${model.key}-${task}-score`);
      if (scoreEl && scoreEl.textContent) {
        const scoreMatch = scoreEl.textContent.match(/(\d+)\/(\d+)/);
        if (scoreMatch) {
          results[`${model.key}-${task}`] = {
            total: parseInt(scoreMatch[1]),
            max: parseInt(scoreMatch[2])
          };
        }
      }
    }
  }

  benchRenderComparison(results);
}

// Initialize
document.addEventListener('DOMContentLoaded', () => {
  updateKeyStatusDisplay();
});


/* ═══════════════════════════════════════════════════════════════════════════════
   INVESTMENT MEMO TAB
   ═══════════════════════════════════════════════════════════════════════════════ */

const _memo = {
    sessionId: localStorage.getItem('rvm_memo_session') || '',
    documents: [],
    links: [],
    templates: [],
    currentMemoId: null,
    currentMemoMd: '',
    generating: false,
    citations: {},
    libraryId: null,
    libraries: [],
    sections: {},          // section_key → stored markdown text (kept in sync with DB)
    lockedSections: {},    // section_key → markdown text to preserve on regeneration
};

// ── Section toggle ──────────────────────────────────────────────────────────
function memoToggleSection(id) {
    const el = document.getElementById(id);
    if (!el) return;
    const open = el.style.display !== 'none';
    el.style.display = open ? 'none' : 'block';
    const chev = document.getElementById(id + '-chev');
    if (chev) chev.innerHTML = open ? '&#9654;' : '&#9660;';
}

// ── Investment Type Toggle (First vs Follow-on) ─────────────────────────────
function memoInvTypeChanged() {
    const val = document.querySelector('input[name="memo-inv-type"]:checked')?.value || 'first';
    const panel = document.getElementById('memo-followon-panel');
    if (panel) panel.style.display = val === 'followon' ? 'block' : 'none';
}

// Follow-on file upload handlers
function _memoFollowonInit() {
    // Original IC Memo upload
    const memoDZ = document.getElementById('memo-followon-memo-dropzone');
    const memoIn = document.getElementById('memo-followon-memo-input');
    if (memoDZ && memoIn && !memoDZ._initDone) {
        memoDZ._initDone = true;
        memoDZ.addEventListener('click', (e) => { if (e.target.tagName !== 'LABEL') memoIn.click(); });
        memoDZ.addEventListener('dragover', (e) => { e.preventDefault(); memoDZ.classList.add('dragover'); });
        memoDZ.addEventListener('dragleave', () => memoDZ.classList.remove('dragover'));
        memoDZ.addEventListener('drop', (e) => { e.preventDefault(); memoDZ.classList.remove('dragover'); _memoFollowonUpload(e.dataTransfer.files, 'prior_ic_memo'); });
        memoIn.addEventListener('change', () => { _memoFollowonUpload(memoIn.files, 'prior_ic_memo'); memoIn.value = ''; });
    }
    // Board Reports upload
    const boardDZ = document.getElementById('memo-followon-board-dropzone');
    const boardIn = document.getElementById('memo-followon-board-input');
    if (boardDZ && boardIn && !boardDZ._initDone) {
        boardDZ._initDone = true;
        boardDZ.addEventListener('click', (e) => { if (e.target.tagName !== 'LABEL') boardIn.click(); });
        boardDZ.addEventListener('dragover', (e) => { e.preventDefault(); boardDZ.classList.add('dragover'); });
        boardDZ.addEventListener('dragleave', () => boardDZ.classList.remove('dragover'));
        boardDZ.addEventListener('drop', (e) => { e.preventDefault(); boardDZ.classList.remove('dragover'); _memoFollowonUpload(e.dataTransfer.files, 'board_report'); });
        boardIn.addEventListener('change', () => { _memoFollowonUpload(boardIn.files, 'board_report'); boardIn.value = ''; });
    }
}

async function _memoFollowonUpload(files, category) {
    if (!files || !files.length) return;
    if (!_memo.sessionId) _memo.sessionId = crypto.randomUUID().replace(/-/g,'').substring(0,12);
    const fd = new FormData();
    for (const f of files) fd.append('files', f);
    fd.append('session_id', _memo.sessionId);
    fd.append('category', category);
    try {
        const r = await fetch('/api/memo/documents/upload', { method:'POST', headers: { 'Authorization': `Bearer ${_rvmToken}` }, body: fd });
        if (!r.ok) { const d = await r.json(); alert(d.detail || 'Upload failed'); return; }
        const data = await r.json();
        _memo.sessionId = data.session_id;
        localStorage.setItem('rvm_memo_session', _memo.sessionId);
        _memoFollowonRefreshLists();
    } catch (e) { alert('Upload error: ' + e.message); }
}

async function _memoFollowonRefreshLists() {
    if (!_memo.sessionId) return;
    try {
        const r = await fetch(`/api/memo/documents/${_memo.sessionId}`, { headers: _rvmHeaders() });
        if (!r.ok) return;
        const docs = await r.json();
        const memoList = document.getElementById('memo-followon-memo-list');
        const boardList = document.getElementById('memo-followon-board-list');
        if (memoList) memoList.innerHTML = docs.filter(d => d.category === 'prior_ic_memo').map(d => _memoDocItemHtml(d)).join('');
        if (boardList) boardList.innerHTML = docs.filter(d => d.category === 'board_report').map(d => _memoDocItemHtml(d)).join('');
        // Also refresh the main doc list
        memoRefreshDocList();
    } catch(e) {}
}

function _memoDocItemHtml(d) {
    const icons = { pdf:'&#128196;', docx:'&#128196;', xlsx:'&#128202;', pptx:'&#128202;', csv:'&#128202;', txt:'&#128196;' };
    const ext = (d.file_name || d.file || '').split('.').pop().toLowerCase();
    const icon = icons[ext] || '&#128196;';
    const size = d.file_size ? (d.file_size > 1048576 ? (d.file_size/1048576).toFixed(1)+'MB' : (d.file_size/1024).toFixed(0)+'KB') : '';
    return `<div class="memo-doc-item"><span class="memo-doc-icon">${icon}</span><div class="memo-doc-info"><div class="memo-doc-name">${d.file_name || d.file}</div><div class="memo-doc-meta">${size}</div></div><button class="memo-doc-remove" onclick="memoRemoveDoc(${d.id})">&times;</button></div>`;
}

// ── Output tab switching ────────────────────────────────────────────────────
function memoSwitchOutputTab(tab) {
    document.querySelectorAll('.memo-output-tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.memo-output-content').forEach(c => c.classList.remove('active'));
    const btn = document.querySelector(`.memo-output-tab[data-memo-tab="${tab}"]`);
    if (btn) btn.classList.add('active');
    const content = document.getElementById(`memo-output-${tab}`);
    if (content) content.classList.add('active');
    if (tab === 'history') memoLoadHistory();
}

// ── Memo page mode switching (setup / focus / history) ──────────────────────
// The IC Memo tab has three modes driven by the data-memo-mode attribute on
// #tab-memo. CSS handles all the layout swapping; this function just sets the
// attribute, syncs the modeswitch tab active states, mirrors the company
// title into the focus header, and triggers history load on demand.
function memoSetMode(mode) {
    const tab = document.getElementById('tab-memo');
    if (!tab) return;
    const allowed = new Set(['setup', 'focus', 'history']);
    if (!allowed.has(mode)) mode = 'setup';
    tab.dataset.memoMode = mode;

    document.querySelectorAll('.memo-modeswitch-btn').forEach(b => {
        b.classList.toggle('active', b.dataset.modeTarget === mode);
    });

    if (mode === 'focus') {
        // Mirror the company name into the focus bar header.
        const titleEl = document.getElementById('memo-focus-title');
        if (titleEl) {
            const meta = document.getElementById('memo-output-meta');
            const fromMeta = (meta?.textContent || '').split('·')[0].trim();
            titleEl.textContent = (window._memo?.currentMemoCompany) || fromMeta || 'Investment Memo';
        }
        // Always hide the empty state in focus mode — if the user got here,
        // there's a memo loaded (or about to load).
        const empty = document.getElementById('memo-output-empty');
        if (empty) empty.style.display = 'none';
        // Clone the action buttons (Export, Lock, etc.) into the focus header
        // so they sit next to the Back button instead of below the title.
        const src = document.getElementById('memo-output-actions');
        const dst = document.getElementById('memo-focus-actions');
        if (src && dst) {
            dst.innerHTML = '';
            // Show the source actions inline-ish for the focus bar — clone
            // each button so handlers stay wired up via the `onclick` attribute.
            Array.from(src.children).forEach(child => {
                const clone = child.cloneNode(true);
                // Keep span wrappers but flatten styling
                if (clone.tagName === 'SPAN') clone.style.borderLeft = '1px solid var(--vf-hairline)';
                dst.appendChild(clone);
            });
        }
        // Scroll to top of the memo so the focus bar is visible.
        window.scrollTo({ top: 0, behavior: 'auto' });
    }

    if (mode === 'history') {
        if (typeof memoLoadHistory === 'function') memoLoadHistory();
    }
}

// ── Load reports into dropdown ──────────────────────────────────────────────
async function memoLoadReports() {
    try {
        const r = await fetch('/api/memo/reports', { headers: _rvmHeaders() });
        if (!r.ok) return;
        const reports = await r.json();
        const sel = document.getElementById('memo-report-select');
        if (!sel) return;
        sel.innerHTML = '<option value="">— No report selected —</option>';
        reports.forEach(rpt => {
            const opt = document.createElement('option');
            opt.value = rpt.id;
            opt.textContent = `#${rpt.id} — ${rpt.company_name} (${rpt.archetype}, ${rpt.entry_stage}) — ${rpt.created_at?.substring(0,10) || ''}`;
            sel.appendChild(opt);
        });
    } catch (e) {
        console.error('Failed to load reports for memo:', e);
    }
}

function memoOnReportSelect() {
    const sel = document.getElementById('memo-report-select');
    const preview = document.getElementById('memo-report-preview');
    if (!sel || !preview) return;
    if (sel.value) {
        preview.style.display = 'block';
        preview.innerHTML = `<span style="color:var(--accent);">&#10003;</span> Report #${sel.value} selected. Simulation results, carbon impact, and financial data will be included.`;
    } else {
        preview.style.display = 'none';
    }
}

// ── Load templates ──────────────────────────────────────────────────────────
async function memoLoadTemplates() {
    try {
        const r = await fetch('/api/memo/templates', { headers: _rvmHeaders() });
        if (!r.ok) return;
        _memo.templates = await r.json();
        const sel = document.getElementById('memo-template-select');
        if (!sel) return;
        sel.innerHTML = '<option value="">— Use default structure —</option><option value="__upload__">Upload new template...</option>';
        _memo.templates.forEach(t => {
            const opt = document.createElement('option');
            opt.value = t.id;
            opt.textContent = `${t.name}${t.description ? ' — ' + t.description : ''}`;
            sel.appendChild(opt);
        });
    } catch (e) {
        console.error('Failed to load memo templates:', e);
    }
}

function memoOnTemplateSelect() {
    const sel = document.getElementById('memo-template-select');
    const uploadRow = document.getElementById('memo-template-upload-row');
    const textarea = document.getElementById('memo-template-text');
    if (!sel) return;

    if (sel.value === '__upload__') {
        if (uploadRow) uploadRow.style.display = 'flex';
        return;
    }
    if (uploadRow) uploadRow.style.display = 'none';

    if (sel.value && sel.value !== '__upload__') {
        // Load template content
        const tpl = _memo.templates.find(t => String(t.id) === sel.value);
        if (tpl) {
            fetch(`/api/memo/templates/${tpl.id}`, { headers: _rvmHeaders() })
                .then(r => r.json())
                .then(data => { if (textarea) textarea.value = data.content || ''; })
                .catch(e => console.error(e));
        }
    } else {
        if (textarea) textarea.value = '';
    }
}

async function memoUploadTemplate() {
    const fileInput = document.getElementById('memo-template-file');
    const textarea = document.getElementById('memo-template-text');
    if (!fileInput?.files?.length || !textarea) return;

    const file = fileInput.files[0];
    const ext = file.name.split('.').pop().toLowerCase();

    if (ext === 'txt' || ext === 'md') {
        const text = await file.text();
        textarea.value = text;
    } else if (ext === 'docx') {
        // For .docx, upload to server for text extraction
        const fd = new FormData();
        fd.append('files', file);
        fd.append('session_id', 'template_upload');
        fd.append('category', 'other');
        try {
            const r = await fetch('/api/memo/documents/upload', {
                method: 'POST', headers: { 'Authorization': `Bearer ${_rvmToken}` }, body: fd
            });
            if (r.ok) {
                const data = await r.json();
                if (data.documents?.[0]?.id) {
                    // Fetch extracted text
                    textarea.value = '(Extracting template text from DOCX... You may also paste the template directly.)';
                }
            }
        } catch (e) {
            console.error(e);
        }
    }

    // Reset the file select
    const sel = document.getElementById('memo-template-select');
    if (sel) sel.value = '';
    const uploadRow = document.getElementById('memo-template-upload-row');
    if (uploadRow) uploadRow.style.display = 'none';
}

async function memoSaveTemplate() {
    const textarea = document.getElementById('memo-template-text');
    if (!textarea || !textarea.value.trim()) {
        alert('No template text to save. Paste or type a template first.');
        return;
    }
    const name = prompt('Template name:');
    if (!name) return;
    const desc = prompt('Brief description (optional):', '');

    try {
        const r = await fetch('/api/memo/templates', {
            method: 'POST',
            headers: { ..._rvmHeaders(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, description: desc || '', content: textarea.value }),
        });
        if (r.ok) {
            await memoLoadTemplates();
            alert('Template saved!');
        } else {
            alert('Failed to save template.');
        }
    } catch (e) {
        console.error(e);
        alert('Error saving template.');
    }
}

// ── Document upload ─────────────────────────────────────────────────────────
function memoInitUpload() {
    const dropzone = document.getElementById('memo-dropzone');
    const fileInput = document.getElementById('memo-file-input');
    if (!dropzone || !fileInput || dropzone._initDone) return;
    dropzone._initDone = true;

    dropzone.addEventListener('click', (e) => { if (e.target.tagName !== 'LABEL' && e.target.tagName !== 'INPUT') fileInput.click(); });
    dropzone.addEventListener('dragover', (e) => { e.preventDefault(); dropzone.classList.add('dragover'); });
    dropzone.addEventListener('dragleave', () => dropzone.classList.remove('dragover'));
    dropzone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropzone.classList.remove('dragover');
        if (e.dataTransfer.files.length) memoUploadFiles(e.dataTransfer.files);
    });

    fileInput.addEventListener('change', () => {
        if (fileInput.files.length) memoUploadFiles(fileInput.files);
        fileInput.value = '';
    });
}

async function memoUploadFiles(files) {
    if (!_memo.sessionId) _memo.sessionId = crypto.randomUUID().substring(0, 12);

    const category = document.getElementById('memo-doc-category')?.value || 'other';
    const catRow = document.getElementById('memo-category-row');
    if (catRow) catRow.style.display = 'flex';

    const fd = new FormData();
    for (const f of files) fd.append('files', f);
    fd.append('session_id', _memo.sessionId);
    fd.append('category', category);

    const statusEl = document.getElementById('memo-generate-status');
    if (statusEl) { statusEl.style.display = 'block'; statusEl.textContent = `Uploading ${files.length} file(s)...`; statusEl.className = 'memo-generate-status info'; }

    try {
        const r = await fetch('/api/memo/documents/upload', {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${_rvmToken}` },
            body: fd,
        });
        if (!r.ok) throw new Error(`Upload failed: ${r.status}`);
        const data = await r.json();
        _memo.sessionId = data.session_id;
        localStorage.setItem('rvm_memo_session', _memo.sessionId);
        await memoRefreshDocList();
        if (statusEl) { statusEl.textContent = `${data.documents.length} file(s) uploaded successfully.`; statusEl.className = 'memo-generate-status success'; }
    } catch (e) {
        console.error(e);
        if (statusEl) { statusEl.textContent = `Upload error: ${e.message}`; statusEl.className = 'memo-generate-status error'; }
    }
}

async function memoRefreshDocList() {
    if (!_memo.sessionId) return;
    try {
        const r = await fetch(`/api/memo/documents/${_memo.sessionId}`, { headers: _rvmHeaders() });
        if (!r.ok) { console.error('memoRefreshDocList failed:', r.status, r.statusText); return; }
        _memo.documents = await r.json();
        memoRenderDocList();
    } catch (e) {
        console.error('memoRefreshDocList error:', e);
    }
}

function memoRenderDocList() {
    const list = document.getElementById('memo-doc-list');
    if (!list) return;
    if (!_memo.documents.length) { list.innerHTML = ''; return; }

    const fmtSize = (b) => b > 1024*1024 ? (b/1024/1024).toFixed(1)+'MB' : (b/1024).toFixed(0)+'KB';
    const IMAGE_EXTS = new Set(['.png','.jpg','.jpeg','.gif','.webp','.svg']);
    const iconMap = {
        '.pdf':'📄', '.docx':'📄', '.doc':'📄',
        '.xlsx':'📊', '.xls':'📊', '.csv':'📊',
        '.pptx':'📖', '.ppt':'📖',
        '.txt':'📝', '.md':'📝', '.html':'🌐',
        '.png':'🖼', '.jpg':'🖼', '.jpeg':'🖼', '.gif':'🖼', '.webp':'🖼',
    };

    list.innerHTML = _memo.documents.map(d => {
        const ext = (d.file_type || '').toLowerCase();
        const isImage = IMAGE_EXTS.has(ext);
        const icon = iconMap[ext] || '📄';
        const cat = (d.doc_category || 'other').replace(/_/g, ' ');
        const token = localStorage.getItem('rvm_token') || '';
        const thumb = isImage
            ? `<img src="/api/memo/documents/${d.id}/view?token=${encodeURIComponent(token)}"
                   class="memo-doc-thumb" alt="${_escHtml(d.file_name)}"
                   onerror="this.style.display='none'">`
            : `<span class="memo-doc-icon">${icon}</span>`;
        return `<div class="memo-doc-item${isImage ? ' memo-doc-item-image' : ''}">
            ${thumb}
            <div class="memo-doc-info">
                <span class="memo-doc-name">${_escHtml(d.file_name)}</span>
                <span class="memo-doc-meta">${fmtSize(d.file_size)} · ${cat}</span>
            </div>
            <button class="memo-doc-remove" onclick="memoRemoveDoc(${d.id})" title="Remove document">&times;</button>
        </div>`;
    }).join('');
}

async function memoReprocessImages() {
    const btn = document.getElementById('memo-reprocess-btn');
    const status = document.getElementById('memo-reprocess-status');
    const sessionId = _memo?.sessionId;
    if (!sessionId) { if (status) status.textContent = 'Upload documents first.'; return; }
    if (btn) btn.disabled = true;
    if (status) { status.textContent = 'Extracting...'; status.style.color = '#666'; }
    try {
        const r = await fetch(`/api/memo/documents/reprocess-images?session_id=${sessionId}`, {
            method: 'POST', headers: _rvmHeaders()
        });
        const d = await r.json();
        if (!r.ok) throw new Error(d.detail || 'Failed');
        if (status) {
            status.textContent = d.images_added > 0
                ? `${d.images_added} image(s) extracted from ${d.pdfs_processed} PDF(s)`
                : `No new images found in ${d.pdfs_processed} PDF(s)`;
            status.style.color = d.images_added > 0 ? '#5B7744' : '#666';
        }
        await memoRefreshDocList();
    } catch (e) {
        if (status) { status.textContent = e.message; status.style.color = '#dc2626'; }
    } finally {
        if (btn) btn.disabled = false;
    }
}

async function memoRemoveDoc(docId) {
    try {
        await fetch(`/api/memo/documents/${docId}`, { method: 'DELETE', headers: _rvmHeaders() });
        memoRefreshDocList();
    } catch (e) {
        console.error(e);
    }
}

// ── Links ───────────────────────────────────────────────────────────────────
function memoAddLink() {
    const input = document.getElementById('memo-link-input');
    if (!input || !input.value.trim()) return;
    let url = input.value.trim();
    if (!url.startsWith('http')) url = 'https://' + url;
    _memo.links.push(url);
    input.value = '';
    memoRenderLinks();
}

function memoRenderLinks() {
    const list = document.getElementById('memo-link-list');
    if (!list) return;
    list.innerHTML = _memo.links.map((url, i) => {
        const display = url.length > 60 ? url.substring(0, 57) + '...' : url;
        return `<div class="memo-link-item">
            <span class="memo-link-url" title="${url}">&#128279; ${display}</span>
            <button class="memo-doc-remove" onclick="_memo.links.splice(${i},1);memoRenderLinks();" title="Remove">&times;</button>
        </div>`;
    }).join('');
}

// ── Generate Memo ───────────────────────────────────────────────────────────
async function memoGenerate() {
    if (_memo.generating) return;

    const reportId = document.getElementById('memo-report-select')?.value || null;
    const templateId = document.getElementById('memo-template-select')?.value || null;
    const templateText = document.getElementById('memo-template-text')?.value || '';
    const instructions = document.getElementById('memo-extra-instructions')?.value || '';
    const model = document.getElementById('memo-model-select')?.value || 'qwen-rfnt';

    if (!reportId && !_memo.documents.length && !templateText.trim()) {
        alert('Please select a deal report, upload documents, or provide a template before generating.');
        return;
    }

    _memo.generating = true;
    const btn = document.getElementById('memo-generate-btn');
    const statusEl = document.getElementById('memo-generate-status');
    if (btn) { btn.disabled = true; btn.textContent = 'Generating...'; }

    // Switch to preview tab and show initial progress
    memoSwitchOutputTab('preview');

    const invType = document.querySelector('input[name="memo-inv-type"]:checked')?.value || 'first';

    // Paint the progress bar from the real backend status.
    const renderProgress = (pct, msg) => {
        if (!statusEl) return;
        const safePct = Math.max(0, Math.min(100, pct || 0));
        statusEl.style.display = 'block';
        statusEl.className = 'memo-generate-status info';
        statusEl.innerHTML = `
            <div class="memo-progress-bar"><div class="memo-progress-fill" style="width:${safePct}%"></div></div>
            <div class="memo-progress-text">${msg || 'Working...'} <span style="float:right">${safePct}%</span></div>`;
    };
    renderProgress(0, 'Queued...');

    // Engine version toggle. v2 = manifest pass + cached section writing
    // (sees the whole data room when writing each section). v1 = legacy
    // per-doc extraction → per-section bucket stitching. Defaults to v1
    // until v2 is proven on real deals.
    const engineVersion = document.querySelector('input[name="memo-engine-version"]:checked')?.value || 'v1';

    const body = {
        report_id: reportId ? parseInt(reportId) : null,
        template_id: (templateId && templateId !== '__upload__') ? parseInt(templateId) : null,
        template_text: templateText || null,
        session_id: _memo.sessionId || null,
        library_id: _memo.libraryId || null,
        additional_instructions: instructions,
        company_name: '',
        links: _memo.links,
        model_override: model,
        investment_type: invType,
        engine_version: engineVersion,
        // Pass locked sections verbatim — LLM will not regenerate them
        locked_sections: Object.keys(_memo.lockedSections).length > 0
            ? { ..._memo.lockedSections }
            : {},
    };

    // ── Start the background memo job ───────────────────────────────────────
    // The HTTP response returns immediately with a job_id; the heavy LLM
    // pipeline runs in a background thread so proxy/CDN HTTP timeouts can't
    // kill the request mid-way.
    let jobId;
    try {
        const r = await fetch('/api/memo/generate', {
            method: 'POST',
            headers: { ..._rvmHeaders(), 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!r.ok) {
            const err = await r.json().catch(() => ({ detail: r.statusText }));
            throw new Error(err.detail || err.error || 'Failed to start memo job');
        }
        const startData = await r.json();
        jobId = startData.job_id;
        if (!jobId) throw new Error('Server did not return a job_id');
    } catch (e) {
        console.error('Memo job start failed:', e);
        if (statusEl) { statusEl.innerHTML = `Error: ${e.message}`; statusEl.className = 'memo-generate-status error'; }
        _memo.generating = false;
        if (btn) { btn.disabled = false; btn.textContent = 'Generate Investment Memo'; }
        return;
    }

    // ── Poll for progress until complete or error ───────────────────────────
    // Up to 20 minutes at 3s intervals.
    const POLL_INTERVAL_MS = 3000;
    const MAX_POLLS = Math.ceil((20 * 60 * 1000) / POLL_INTERVAL_MS);
    let data = null;
    try {
        for (let i = 0; i < MAX_POLLS; i++) {
            await new Promise(res => setTimeout(res, POLL_INTERVAL_MS));
            if (!_memo.generating) break;  // user navigated away / aborted
            const sr = await fetch(`/api/memo/generate/status/${jobId}`, { headers: _rvmHeaders() });
            // Special-case 404: the server lost the job (deploy/restart, or
            // 1h TTL expired). Tell the user clearly to retry instead of
            // confusing them with "not found or expired".
            if (sr.status === 404) {
                throw new Error('Memo generation was interrupted (server restarted). Click Generate to retry — your inputs are still selected.');
            }
            if (!sr.ok) {
                const err = await sr.json().catch(() => ({ detail: sr.statusText }));
                throw new Error(err.detail || 'Status poll failed');
            }
            const snap = await sr.json();
            renderProgress(snap.progress_pct, snap.progress_msg);
            if (snap.status === 'complete') { data = snap.result; break; }
            if (snap.status === 'error')    { throw new Error(snap.error || 'Generation failed'); }
        }
        if (!data) throw new Error('Memo generation timed out after 20 minutes.');

        _memo.currentMemoId = data.id;
        _memo.currentMemoMd = data.memo_markdown;
        _memo.currentMemoCompany = data.company_name || _memo.currentMemoCompany || '';

        // Render the memo
        const rendered = document.getElementById('memo-rendered');
        const empty = document.getElementById('memo-output-empty');
        const actions = document.getElementById('memo-output-actions');
        const meta = document.getElementById('memo-output-meta');

        if (rendered) rendered.innerHTML = data.memo_html;
        if (empty) empty.style.display = 'none';
        if (actions) actions.style.display = 'flex';

        // Store memo state for section-level editing
        _memo.currentMemoId = data.id;
        _memo.sections = data.sections || {};
        _memo.reportData = data.report_data || null;

        // Store citations metadata and bind click handlers
        _memo.citations = data.citations || {};
        _memoCitationsBind(rendered);

        // Wrap sections FIRST so the section-content divs exist before charts are injected
        if (rendered && data.sections) {
            _memoWrapSections(rendered, data.sections);
        }

        // Inject data room images into memo sections
        if (data.image_docs && data.image_docs.length > 0 && rendered) {
            _memoInjectImages(rendered, data.image_docs);
        }

        // Inject pipeline charts LAST — DOM is stable, canvases won't be reparented
        if (data.report_data && rendered) {
            _memoInjectCharts(rendered, data.report_data);
            _memoInjectDealCard(rendered, data.report_data);
        }

        // Inject cover page, legal disclaimer, and TOC as the very first elements
        _memoInjectFrontMatter(rendered, data);

        // Show pipeline stats
        const pl = data.pipeline || {};
        const tokenCount = (data.input_tokens + data.output_tokens).toLocaleString();
        if (meta) meta.textContent = `${data.model_used} · ${tokenCount} tokens · ${pl.total_llm_calls || '?'} LLM calls · ${data.generation_time_s}s`;

        if (statusEl) {
            statusEl.innerHTML = `
                <div class="memo-progress-bar"><div class="memo-progress-fill complete" style="width:100%"></div></div>
                <div class="memo-progress-text">Memo generated — ${pl.documents_processed || 0} docs processed, ${pl.sections_written || 0} sections written, ${pl.total_llm_calls || 0} LLM calls in ${data.generation_time_s}s</div>`;
            statusEl.className = 'memo-generate-status success';
        }

        // Switch to focus mode so the memo takes the full screen and the
        // setup form gets out of the way. User can return via "Back to Setup".
        if (typeof memoSetMode === 'function') memoSetMode('focus');

    } catch (e) {
        console.error('Memo generation error:', e);
        if (statusEl) { statusEl.innerHTML = `Error: ${e.message}`; statusEl.className = 'memo-generate-status error'; }
    } finally {
        _memo.generating = false;
        if (btn) { btn.disabled = false; btn.textContent = 'Generate Investment Memo'; }
    }
}

// ═══════════════════════════════════════════════════════════════════════════════
//  FRONT MATTER: Cover Page + Legal Disclaimer + Table of Contents
//  Injected at the top of every rendered memo. Idempotent — safe to call twice.
// ═══════════════════════════════════════════════════════════════════════════════

function _memoInjectFrontMatter(rendered, data) {
    if (!rendered) return;

    // Remove any existing front matter so this is idempotent
    rendered.querySelectorAll('.memo-front-matter').forEach(el => el.remove());

    const ov        = data?.report_data?.deal_overview || {};
    const companyName = data?.company_name || ov.company_name || ov.archetype || 'Portfolio Company';
    const entryStage  = ov.entry_stage   || '';
    const checkM      = ov.check_size_millions ? `$${Number(ov.check_size_millions).toFixed(1)}M` : '';
    const archetype   = ov.archetype     || '';
    const today       = new Date().toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' });
    const genDate     = data?.created_at ? new Date(data.created_at).toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' }) : today;

    // ── 1. COVER PAGE ────────────────────────────────────────────────────────
    const coverDiv = document.createElement('div');
    coverDiv.className = 'memo-front-matter memo-cover-page';
    coverDiv.innerHTML = `
        <div class="memo-cover-inner">
            <div class="memo-cover-logo">
                <div class="memo-cover-logo-text">VoLo Earth Ventures</div>
            </div>
            <div class="memo-cover-divider"></div>
            <div class="memo-cover-type">INVESTMENT MEMORANDUM</div>
            <div class="memo-cover-company">${_escHtml(companyName)}</div>
            ${entryStage ? `<div class="memo-cover-detail">${_escHtml(entryStage)}${archetype ? ' · ' + _escHtml(archetype) : ''}${checkM ? ' · ' + checkM : ''}</div>` : ''}
            <div class="memo-cover-date">${genDate}</div>
            <div class="memo-cover-confidential">
                STRICTLY CONFIDENTIAL — FOR INTERNAL USE ONLY<br>
                Not for Distribution Without Prior Written Consent
            </div>
            <div class="memo-cover-footer">
                VoLo Earth Ventures<br>
                This document is subject to the Legal Disclaimer set out on the following page.
            </div>
        </div>
    `;

    // ── 2. LEGAL DISCLAIMER ──────────────────────────────────────────────────
    const disclaimerDiv = document.createElement('div');
    disclaimerDiv.className = 'memo-front-matter memo-disclaimer-page';
    disclaimerDiv.innerHTML = `
        <div class="memo-disclaimer-inner">
            <h2 class="memo-disclaimer-title">Legal Disclaimer &amp; Risk Disclosure</h2>

            <p class="memo-disclaimer-block"><strong>CONFIDENTIALITY.</strong>
            This Investment Memorandum (the "Memo") is furnished by VoLo Earth Ventures ("VoLo") on a strictly
            confidential basis solely to the intended recipient for informational and internal evaluation purposes.
            It may not be reproduced, distributed, or disclosed to any third party without the prior written
            consent of VoLo. Receipt of this Memo constitutes acceptance of these terms.</p>

            <p class="memo-disclaimer-block"><strong>NOT AN OFFER OF SECURITIES.</strong>
            Nothing in this Memo constitutes or shall be construed as an offer to sell, or a solicitation of an
            offer to buy, any security or investment product in any jurisdiction. Any such offer or solicitation
            will be made only pursuant to separate definitive agreements and applicable securities laws.</p>

            <p class="memo-disclaimer-block"><strong class="memo-disclaimer-nonreliance">NON-RELIANCE STATEMENT.
            THIS MEMO IS PROVIDED FOR INFORMATIONAL PURPOSES ONLY AND MUST NOT BE RELIED UPON AS THE BASIS
            FOR ANY INVESTMENT DECISION. NO REPRESENTATION OR WARRANTY, EXPRESS OR IMPLIED, IS MADE AS TO
            THE ACCURACY, COMPLETENESS, OR FITNESS FOR PURPOSE OF ANY INFORMATION CONTAINED HEREIN.
            RECIPIENTS MUST CONDUCT THEIR OWN INDEPENDENT DUE DILIGENCE AND SEEK INDEPENDENT FINANCIAL,
            LEGAL, AND TAX ADVICE BEFORE MAKING ANY INVESTMENT DECISION. VOLO EXPRESSLY DISCLAIMS ANY AND
            ALL LIABILITY ARISING FROM RELIANCE ON THIS MEMO OR ITS CONTENTS.</strong></p>

            <p class="memo-disclaimer-block"><strong>FORWARD-LOOKING STATEMENTS.</strong>
            Certain statements in this Memo, including financial projections, market size estimates, adoption
            forecasts, and carbon impact calculations, constitute forward-looking statements. These statements
            involve known and unknown risks, uncertainties, and other factors that may cause actual results,
            performance, or achievements to differ materially from those expressed or implied. Past performance
            is not indicative of future results. All projections are inherently speculative and subject to
            change without notice.</p>

            <p class="memo-disclaimer-block"><strong>STATISTICAL AND QUANTITATIVE MODEL LIMITATIONS.</strong>
            Financial return projections, fund-level impact analyses, and portfolio simulations contained in
            this Memo are generated by VoLo's proprietary Return Value Model (RVM), which employs Monte Carlo
            simulation, Bass diffusion modeling, and statistical regression techniques. These models are
            calibrated on historical market data and carry inherent assumptions that may not hold in future
            conditions. Model outputs represent probability-weighted estimates across simulated scenarios —
            they are not guarantees or predictions of actual outcomes. Simulation results are sensitive to
            input assumptions including exit multiples, graduation rates, and revenue trajectories; small
            changes in inputs can produce materially different outputs. No model can fully capture the
            complexity of venture capital markets.</p>

            <p class="memo-disclaimer-block"><strong>ARTIFICIAL INTELLIGENCE AND INFERENCE.</strong>
            Portions of this Memo, including section narratives, company analyses, and synthesis sections,
            were generated or assisted by large language model (LLM) AI systems. AI-generated content is
            based on information provided at the time of generation and is subject to errors, omissions,
            and the inherent limitations of language model inference. AI outputs have not been independently
            verified in their entirety and should not be treated as expert opinion or professional advice.
            Recipients should verify all material facts independently.</p>

            <p class="memo-disclaimer-block"><strong>THIRD-PARTY DATA SOURCES.</strong>
            This Memo incorporates data from third-party sources including, without limitation: Carta Inc.
            (venture graduation rates and fund benchmarking), Damodaran Online at NYU Stern (sector valuation
            multiples), the PRIME Coalition / NYSERDA ERP framework (carbon impact methodology), EPA and IEA
            emissions databases (carbon intensity factors), and various public market databases. VoLo does not
            independently verify third-party data and makes no representation as to its accuracy or
            completeness. Third-party data is subject to revision, methodology changes, and may contain
            errors. Use of third-party data does not constitute endorsement of or by those sources.</p>

            <p class="memo-disclaimer-block"><strong>CARBON IMPACT ESTIMATES.</strong>
            Carbon impact calculations are based on the PRIME Coalition Expected Impact Returns (ERP)
            methodology and VoLo's multi-cohort extension thereof. These estimates rely on assumptions
            regarding technology adoption, grid carbon intensity trajectories, product displacement factors,
            and company survival probabilities. Actual carbon outcomes will depend on commercial execution,
            regulatory environment, and grid evolution — factors that cannot be predicted with certainty.
            Risk-adjusted carbon metrics incorporate survival probability estimates derived from Monte Carlo
            simulation and are not part of the PRIME ERP deterministic framework.</p>

            <p class="memo-disclaimer-block"><strong>RISK FACTORS.</strong>
            Investment in early-stage venture companies involves a high degree of risk, including but not
            limited to: risk of total loss of capital; technology development risk; market adoption risk;
            regulatory and policy risk; team execution risk; competition risk; financing risk (inability to
            raise future rounds); and liquidity risk (illiquidity of private equity interests over extended
            time horizons). Investors should be prepared to sustain a total loss of their investment.
            This Memo does not purport to identify all risks associated with an investment in the subject
            company.</p>

            <p class="memo-disclaimer-block"><strong>NO FIDUCIARY DUTY.</strong>
            VoLo does not owe any fiduciary duty to recipients of this Memo who are not investors in VoLo's
            funds. This Memo does not create any advisory, fiduciary, or agency relationship between VoLo
            and the recipient.</p>

            <p class="memo-disclaimer-date">© ${new Date().getFullYear()} VoLo Earth Ventures. All rights reserved. &nbsp;|&nbsp; Generated: ${genDate}</p>
        </div>
    `;

    // ── 3. TABLE OF CONTENTS ─────────────────────────────────────────────────
    // Build TOC by scanning H2 elements already in the rendered container,
    // assigning anchor IDs and building hyperlinked entries.
    const tocDiv = document.createElement('div');
    tocDiv.className = 'memo-front-matter memo-toc-page';

    // Assign id anchors to every H2 in the memo body (skip our own front matter)
    const allH2s = Array.from(rendered.querySelectorAll('h2')).filter(h => !h.closest('.memo-front-matter'));
    allH2s.forEach((h2, i) => {
        const slug = 'ms-' + (h2.textContent || '').trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') + '-' + i;
        h2.id = slug;
    });

    // Special: the H1 title at top
    const h1 = rendered.querySelector('h1:not(.memo-front-matter *)');
    if (h1 && !h1.id) h1.id = 'memo-top';

    let tocRows = '';
    let secNum = 0;
    allH2s.forEach(h2 => {
        const text = h2.textContent.trim();
        // Skip Appendix from numbered list
        const isAppendix = text.toLowerCase().startsWith('appendix');
        if (!isAppendix) secNum++;
        const numLabel = isAppendix ? '' : `${secNum}.`;
        const anchor = h2.id;
        tocRows += `
            <tr class="memo-toc-row${isAppendix ? ' memo-toc-appendix' : ''}">
                <td class="memo-toc-num">${numLabel}</td>
                <td class="memo-toc-title"><a href="#${anchor}" class="memo-toc-link">${_escHtml(text)}</a></td>
                <td class="memo-toc-dots"></td>
            </tr>`;
    });

    tocDiv.innerHTML = `
        <div class="memo-toc-inner">
            <div class="memo-toc-header">
                <div class="memo-toc-header-company">${_escHtml(companyName)}</div>
                <div class="memo-toc-header-label">Table of Contents</div>
            </div>
            <table class="memo-toc-table">
                <tbody>${tocRows || '<tr><td colspan="3">Sections will appear here after memo is generated.</td></tr>'}</tbody>
            </table>
            <p class="memo-toc-note">Click any section title to jump to that section.</p>
        </div>
    `;

    // ── Prepend all three pages before the memo body ─────────────────────────
    rendered.insertBefore(tocDiv, rendered.firstChild);
    rendered.insertBefore(disclaimerDiv, rendered.firstChild);
    rendered.insertBefore(coverDiv, rendered.firstChild);
}

// ── Citation popover system ─────────────────────────────────────────────────
function _memoCitationsBind(container) {
    if (!container) return;
    // Remove any existing popover
    const existing = document.getElementById('memo-cite-popover');
    if (existing) existing.remove();

    // Create rich source viewer popover
    const popover = document.createElement('div');
    popover.id = 'memo-cite-popover';
    popover.className = 'memo-cite-popover hidden';
    popover.innerHTML = `
        <div class="memo-cite-popover-header">
            <div class="memo-cite-popover-header-left">
                <span class="memo-cite-popover-badge"></span>
                <span class="memo-cite-popover-title"></span>
            </div>
            <div class="memo-cite-popover-header-right">
                <a class="memo-cite-view-btn" href="#" target="_blank" style="display:none;">Open Document</a>
                <button class="memo-cite-popover-close" onclick="this.closest('.memo-cite-popover').classList.add('hidden')">&times;</button>
            </div>
        </div>
        <div class="memo-cite-popover-meta">
            <span class="memo-cite-popover-category"></span>
            <span class="memo-cite-popover-stats"></span>
        </div>
        <div class="memo-cite-page-nav" style="display:none;">
            <button class="memo-cite-page-btn" onclick="_memoCitePagePrev()">&#9664; Prev</button>
            <span class="memo-cite-page-label">Page 1</span>
            <button class="memo-cite-page-btn" onclick="_memoCitePageNext()">Next &#9654;</button>
        </div>
        <div class="memo-cite-popover-content"></div>`;
    document.body.appendChild(popover);

    // Track current page state
    window._memoCiteState = { pages: [], currentPage: 0 };

    // Bind click handlers to all citation spans
    container.querySelectorAll('.memo-cite').forEach(el => {
        el.addEventListener('click', (e) => {
            e.stopPropagation();
            const citeKey = el.dataset.cite;
            const cite = _memo.citations[citeKey];
            if (!cite) return;

            const badge = popover.querySelector('.memo-cite-popover-badge');
            const title = popover.querySelector('.memo-cite-popover-title');
            const category = popover.querySelector('.memo-cite-popover-category');
            const stats = popover.querySelector('.memo-cite-popover-stats');
            const content = popover.querySelector('.memo-cite-popover-content');
            const pageNav = popover.querySelector('.memo-cite-page-nav');
            const viewBtn = popover.querySelector('.memo-cite-view-btn');

            badge.textContent = `[${cite.number}]`;
            title.textContent = cite.file_name;
            category.textContent = cite.category;

            // Stats
            const charCount = cite.total_chars ? `${Math.round(cite.total_chars / 1000)}K chars` : '';
            const pageCount = cite.total_pages ? `${cite.total_pages} page${cite.total_pages > 1 ? 's' : ''}` : '';
            stats.textContent = [pageCount, charCount].filter(Boolean).join(' · ');

            // View document button
            if (cite.doc_id && cite.file_type === '.pdf') {
                viewBtn.href = `/api/memo/documents/${cite.doc_id}/view?token=${_rvmToken}`;
                viewBtn.style.display = 'inline-block';
            } else if (cite.doc_id) {
                viewBtn.href = `/api/memo/documents/${cite.doc_id}/view?token=${_rvmToken}`;
                viewBtn.style.display = 'inline-block';
            } else {
                viewBtn.style.display = 'none';
            }

            // Pages
            const pages = cite.pages || [];
            window._memoCiteState = { pages, currentPage: 0 };

            if (pages.length > 1) {
                pageNav.style.display = 'flex';
                _memoCiteRenderPage(0);
            } else if (pages.length === 1) {
                pageNav.style.display = 'none';
                const p = pages[0];
                content.innerHTML = `<pre class="memo-cite-text">${_escHtml(p.text)}${p.truncated ? '\n\n[... content truncated ...]' : ''}</pre>`;
            } else {
                pageNav.style.display = 'none';
                content.innerHTML = '<p class="memo-cite-empty">No extracted text available for this document.</p>';
            }

            // Position — show as side panel on right
            const rect = el.getBoundingClientRect();
            const scrollTop = window.scrollY || document.documentElement.scrollTop;
            popover.style.top = Math.max(80, rect.top + scrollTop - 40) + 'px';
            popover.style.left = Math.max(12, Math.min(rect.left + 30, window.innerWidth - 560)) + 'px';
            popover.classList.remove('hidden');
        });
    });

    // Close popover when clicking outside
    document.addEventListener('click', (e) => {
        if (!popover.contains(e.target) && !e.target.classList.contains('memo-cite')) {
            popover.classList.add('hidden');
        }
    });
}

function _memoCiteRenderPage(idx) {
    const state = window._memoCiteState;
    if (!state || !state.pages.length) return;
    idx = Math.max(0, Math.min(idx, state.pages.length - 1));
    state.currentPage = idx;

    const p = state.pages[idx];
    const popover = document.getElementById('memo-cite-popover');
    if (!popover) return;

    const content = popover.querySelector('.memo-cite-popover-content');
    const label = popover.querySelector('.memo-cite-page-label');

    const pageLabel = p.page ? `Page ${p.page}` : `Section ${idx + 1}`;
    label.textContent = `${pageLabel} of ${state.pages.length}`;
    content.innerHTML = `<pre class="memo-cite-text">${_escHtml(p.text)}${p.truncated ? '\n\n[... content truncated ...]' : ''}</pre>`;
}

function _memoCitePagePrev() {
    const state = window._memoCiteState;
    if (state && state.currentPage > 0) _memoCiteRenderPage(state.currentPage - 1);
}

function _memoCitePageNext() {
    const state = window._memoCiteState;
    if (state && state.currentPage < state.pages.length - 1) _memoCiteRenderPage(state.currentPage + 1);
}

function _escHtml(s) {
    return (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// ── Export & Copy ───────────────────────────────────────────────────────────
function memoExportDocx() {
    if (!_memo.currentMemoId) return;
    window.open(`/api/memo/history/${_memo.currentMemoId}/docx?token=${_rvmToken}`, '_blank');
}

async function memoPrintPDF() {
    const rendered = document.getElementById('memo-rendered');
    if (!rendered || !rendered.innerHTML.trim()) { showToast('No memo loaded'); return; }

    // 1. Clone memo so we never mutate the live DOM
    const clone = rendered.cloneNode(true);

    // 2. Replace every live <canvas> with a PNG snapshot in the clone
    const liveCanvases  = Array.from(rendered.querySelectorAll('canvas'));
    const cloneCanvases = Array.from(clone.querySelectorAll('canvas'));
    liveCanvases.forEach((canvas, i) => {
        try {
            const dataUrl = canvas.toDataURL('image/png');
            const img = document.createElement('img');
            img.src = dataUrl;
            img.style.width    = Math.min(canvas.offsetWidth,  680) + 'px';
            img.style.height   = Math.min(canvas.offsetHeight, 280) + 'px';
            img.style.maxWidth = '100%';
            img.style.display  = 'block';
            if (cloneCanvases[i]) cloneCanvases[i].replaceWith(img);
        } catch(e) { if (cloneCanvases[i]) cloneCanvases[i].remove(); }
    });

    // 3. Strip interactive chrome (toolbars, edit panels, remove buttons, citations)
    clone.querySelectorAll(
        '.memo-section-toolbar,.memo-sect-btn,.memo-edit-panel,' +
        '.memo-revise-panel,.memo-history-panel,.memo-image-remove,' +
        'sup[data-cite],.memo-citation-chip'
    ).forEach(el => el.remove());

    // 3b. Inject hard page-break divs so Chrome actually honours section boundaries.
    // CSS break-before on class selectors is unreliable in Chrome's print engine;
    // physical zero-height divs with inline page-break-before are the reliable path.
    //
    // Front matter pages (cover, disclaimer, TOC) each get break-after.
    clone.querySelectorAll('.memo-cover-page,.memo-disclaimer-page,.memo-toc-page')
        .forEach(fm => {
            fm.style.pageBreakAfter = 'always';
            fm.style.breakAfter     = 'page';
        });
    // Every content section gets a break-before EXCEPT the very first one
    // (the TOC's break-after already opens a fresh page for it).
    const sectionWrappers = Array.from(clone.querySelectorAll('.memo-section-wrapper'));
    sectionWrappers.forEach((wrapper, idx) => {
        if (idx === 0) return;  // first section rides the TOC's break-after
        const pb = document.createElement('div');
        pb.setAttribute('aria-hidden', 'true');
        pb.style.cssText = 'page-break-before:always;break-before:page;' +
                           'height:0;margin:0;padding:0;border:none;display:block;';
        wrapper.parentNode.insertBefore(pb, wrapper);
    });

    const companyName = document.getElementById('memo-output-meta')
        ?.textContent?.split('·')[0]?.trim() || 'Investment Memo';

    // 4. Build the full print HTML, then open via Blob URL so the browser fully
    //    renders (CSS layout, image decode, page-break calculation) before the
    //    print dialog opens.  This avoids the document.write + win.print() race
    //    that was causing Chrome to honour only 3 pages.
    const printHtml = `<!DOCTYPE html><html><head>
<meta charset="utf-8">
<title>${companyName} — VoLo Earth Ventures</title>
<style>
/* ── Reset & base ── */
*,*::before,*::after{box-sizing:border-box}
-webkit-print-color-adjust:exact;
print-color-adjust:exact;

/* ── Page setup: US Letter portrait ── */
@page{size:letter portrait;margin:.75in .75in .85in .75in}
/* Cover page: zero margin for full bleed */
@page:first{margin:0}

/* ── Body: let the @page dimensions drive layout; no fixed width ── */
body{font-family:Georgia,serif;font-size:10.5pt;color:#1a1a1a;background:#fff;
     margin:0;padding:0;width:100%;overflow:visible}

/* ── Typography ── */
h1{font-size:20pt;margin:0 0 4pt}
h2{font-size:13pt;font-weight:700;margin:0 0 6pt;border-bottom:1.5px solid #5B7744;
   padding-bottom:3pt;color:#1a2332;break-after:avoid;page-break-after:avoid}
h3{font-size:11pt;font-weight:600;margin:10pt 0 4pt;break-after:avoid;page-break-after:avoid}
h4{font-size:10.5pt;font-weight:600;margin:8pt 0 3pt;break-after:avoid;page-break-after:avoid}
h2+p,h3+p,h2+ul,h3+ul{break-before:avoid;page-break-before:avoid}
p,li{font-size:10.5pt;line-height:1.55;margin-top:4pt;orphans:3;widows:3}
ul,ol{margin:4pt 0 4pt 18pt}
blockquote{border-left:3px solid #5B7744;margin:8pt 0 8pt 10pt;padding-left:10pt;color:#555;font-style:italic}
code{font-family:monospace;font-size:9pt;background:#f5f5f5;padding:1pt 3pt;border-radius:2pt}

/* ── FRONT MATTER ── */
/* Cover: fills first page edge-to-edge (no margin on @page:first) */
.memo-cover-page{
  background:#1e2d24;color:#fff;
  width:100%;
  display:flex;flex-direction:column;align-items:center;justify-content:center;
  text-align:center;padding:56pt 48pt;
  break-after:page;page-break-after:always;
  -webkit-print-color-adjust:exact;print-color-adjust:exact}
.memo-cover-inner{max-width:420pt;width:100%}
.memo-cover-logo{display:flex;align-items:center;justify-content:center;gap:10pt;margin-bottom:28pt}
.memo-cover-logo-mark{width:36pt;height:36pt;background:#5B7744;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:14pt;font-weight:700;color:#fff;flex-shrink:0;-webkit-print-color-adjust:exact;print-color-adjust:exact}
.memo-cover-logo-text{font-size:12pt;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:#8BA872}
.memo-cover-divider{width:60pt;height:1pt;background:#5B7744;margin:0 auto 28pt;-webkit-print-color-adjust:exact;print-color-adjust:exact}
.memo-cover-type{font-size:8.5pt;letter-spacing:.2em;text-transform:uppercase;color:#8BA872;margin-bottom:12pt;font-weight:600}
.memo-cover-company{font-size:26pt;font-weight:700;color:#fff;line-height:1.15;margin-bottom:10pt}
.memo-cover-detail{font-size:10pt;color:#aaa;margin-bottom:22pt}
.memo-cover-date{font-size:9.5pt;color:#8BA872;margin-bottom:32pt}
.memo-cover-confidential{font-size:7.5pt;letter-spacing:.12em;text-transform:uppercase;color:#dc2626;border:1px solid #dc2626;padding:5pt 14pt;border-radius:3pt;margin-bottom:24pt;font-weight:600}
.memo-cover-footer{font-size:8pt;color:#666;line-height:1.5}

/* Disclaimer page */
.memo-disclaimer-page{
  background:#fff;width:100%;
  padding:48pt 64pt 48pt 64pt;
  break-before:page;page-break-before:always;
  break-after:page;page-break-after:always}
.memo-disclaimer-inner{max-width:100%}
.memo-disclaimer-title{font-size:15pt;font-weight:700;color:#1a1a1a;margin-bottom:16pt;padding-bottom:6pt;border-bottom:2px solid #5B7744}
.memo-disclaimer-block{font-size:9pt;line-height:1.65;margin-bottom:9pt;text-align:justify}
.memo-disclaimer-nonreliance{display:block;font-size:9pt;font-weight:700;line-height:1.65}
.memo-disclaimer-date{font-size:8pt;color:#888;margin-top:18pt;border-top:1px solid #ddd;padding-top:6pt}

/* TOC page */
.memo-toc-page{
  background:#fff;width:100%;
  padding:48pt 64pt 48pt 64pt;
  break-before:page;page-break-before:always;
  break-after:page;page-break-after:always}
.memo-toc-inner{max-width:100%;width:100%}
.memo-toc-header{margin-bottom:24pt;border-bottom:2px solid #5B7744;padding-bottom:8pt}
.memo-toc-header-company{font-size:10pt;color:#888;font-weight:600;letter-spacing:.06em;text-transform:uppercase;margin-bottom:4pt}
.memo-toc-header-label{font-size:20pt;font-weight:700;color:#1a1a1a}
.memo-toc-table{width:100%;border-collapse:collapse}
.memo-toc-row td{padding:5pt 0;border:none;vertical-align:baseline}
.memo-toc-num{font-size:9pt;font-weight:700;color:#5B7744;width:20pt;white-space:nowrap}
.memo-toc-title{font-size:10pt;color:#1a1a1a;font-weight:500;padding-right:6pt}
.memo-toc-link{color:#1a1a1a!important;text-decoration:none;border-bottom:none}
.memo-toc-dots{font-size:9pt;color:#ccc;white-space:nowrap;min-width:40pt}
.memo-toc-appendix td{color:#888;font-size:9pt}
.memo-toc-note{font-size:8pt;color:#888;margin-top:16pt;font-style:italic}

/* ── SECTION WRAPPERS ── */
/* Each major section starts on a new page */
.memo-section-wrapper{
  break-before:page;page-break-before:always;
  break-inside:auto;page-break-inside:auto;
  padding-top:0;margin-top:0}
/* Body wrapper for the page area (after cover, has margins) */
.memo-content-body{background:#fff;padding:0 .75in;width:8.5in}
/* First section after TOC — TOC's break-after already opens the page */
.memo-section-wrapper:first-of-type{break-before:avoid;page-break-before:avoid}

/* ── SECTION CONTENT ── */
.memo-section-content{display:block!important;visibility:visible!important}

/* ── SMALL ELEMENTS — keep together ── */
.memo-rvm-hero{display:flex;gap:8pt;flex-wrap:wrap;margin:10pt 0;break-inside:avoid;page-break-inside:avoid}
.memo-rvm-hero-card{border:1px solid #ccc;padding:6pt 10pt;border-radius:4pt;flex:1;min-width:80pt;text-align:center;break-inside:avoid;page-break-inside:avoid}
.memo-rvm-hero-card.accent{background:#f0f4ef;border-color:#5B7744;-webkit-print-color-adjust:exact;print-color-adjust:exact}
.memo-rvm-hero-num{font-size:15pt;font-weight:700;color:#3B5249}
.memo-rvm-hero-lbl{font-size:8pt;font-weight:600;text-transform:uppercase;color:#888;margin-top:2pt}
/* Deal card */
.memo-deal-card{border:1px solid #e5e7eb;border-radius:6pt;padding:12pt;margin:10pt 0;break-inside:avoid;page-break-inside:avoid;background:#fff}
.memo-dc-tags{display:flex;flex-wrap:wrap;gap:6pt;margin-bottom:10pt}
.memo-dc-tag{background:#fff;border:1px solid #e5e7eb;border-radius:20pt;padding:3pt 10pt;font-size:9pt;color:#555;font-weight:500}
.memo-dc-terms{display:flex;flex-wrap:wrap;gap:8pt;margin-bottom:10pt}
.memo-dc-term{background:#fff;border:1px solid #e5e7eb;border-radius:5pt;padding:7pt 10pt;min-width:80pt;flex:1}
.memo-dc-label{display:block;font-size:7.5pt;font-weight:600;letter-spacing:.06em;color:#888;text-transform:uppercase;margin-bottom:3pt}
.memo-dc-val{font-size:13pt;font-weight:700;color:#1a1a1a}
.memo-dc-hero{display:flex;gap:8pt;flex-wrap:wrap;break-inside:avoid;page-break-inside:avoid}
.memo-dc-hero-card{flex:1;min-width:80pt;background:#fff;border:1px solid #e5e7eb;border-radius:5pt;padding:8pt 10pt;text-align:center;break-inside:avoid;page-break-inside:avoid}
.memo-dc-hero-card.accent{background:#f0f4ef;border-color:#5B7744;-webkit-print-color-adjust:exact;print-color-adjust:exact}
.memo-dc-hero-num{font-size:16pt;font-weight:700;color:#1a1a1a;line-height:1.1}
.memo-dc-hero-card.accent .memo-dc-hero-num{color:#3B5249}
.memo-dc-hero-lbl{font-size:7.5pt;font-weight:600;letter-spacing:.06em;color:#888;text-transform:uppercase;margin-top:3pt}
/* Charts */
.memo-chart-inline{break-inside:avoid;page-break-inside:avoid;margin:10pt 0}
.memo-chart-context{background:#fafaf9;border:1px solid #eee;padding:8pt;border-radius:4pt;margin:8pt 0;break-inside:avoid;page-break-inside:avoid}
.memo-chart-label{font-size:9pt;font-weight:700;text-transform:uppercase;color:#5B7744;letter-spacing:.05em;margin-bottom:3pt}
.memo-chart-caption,.memo-chart-note{font-size:8.5pt;color:#666;margin:3pt 0}
.memo-chart-row{display:flex;gap:12pt;break-inside:avoid;page-break-inside:avoid}
.memo-chart-wrap{flex:1;overflow:hidden}
.memo-chart-wrap img{width:100%;max-height:190pt;object-fit:contain}
canvas{max-height:190pt!important;width:100%!important;object-fit:contain}
/* Images */
.memo-image-figure{break-inside:avoid;page-break-inside:avoid;max-width:60%;margin:10pt auto;text-align:center}
.memo-image-embed{max-width:100%;height:auto}
.memo-image-caption{font-size:8pt;color:#888;margin-top:3pt}
/* Tables — allow long tables to break between rows, header repeats */
table{width:100%;border-collapse:collapse;font-size:9pt;margin:8pt 0;break-inside:auto;page-break-inside:auto}
thead{display:table-header-group}
tr{break-inside:avoid;page-break-inside:avoid}
th,td{border:1px solid #ddd;padding:3.5pt 5pt;text-align:left}
th{background:#f5f5f5;font-weight:600;-webkit-print-color-adjust:exact;print-color-adjust:exact}
/* Variance / rpt table */
.rpt-table td,.rpt-table th{border:1px solid #ddd;padding:3pt 5pt;font-size:9pt}
/* Footer */
footer{margin-top:28pt;padding-top:5pt;border-top:1px solid #ddd;font-size:8pt;color:#aaa;text-align:center;letter-spacing:.04em}
</style></head><body>
${clone.innerHTML}
<footer>VoLo Earth Ventures — Confidential &amp; Proprietary</footer>
<script>
// Fire print once the document is fully loaded & laid out.
// Using load (not DOMContentLoaded) so images are decoded first.
window.addEventListener('load', function () {
    setTimeout(function () { window.print(); }, 600);
});
<\/script>
</body></html>`;

    // 5. Open via Blob URL — the browser treats it as a full navigation,
    //    runs layout, resolves @page rules, then fires the embedded print.
    const blob = new Blob([printHtml], { type: 'text/html' });
    const url  = URL.createObjectURL(blob);
    window.open(url, '_blank');
    // Revoke after 2 min to free memory (the window will have already loaded)
    setTimeout(() => URL.revokeObjectURL(url), 120000);
}

function memoCopyMarkdown() {
    if (!_memo.currentMemoMd) return;
    navigator.clipboard.writeText(_memo.currentMemoMd).then(() => {
        const btn = document.querySelector('[onclick="memoCopyMarkdown()"]');
        if (btn) { const orig = btn.textContent; btn.textContent = 'Copied!'; setTimeout(() => btn.textContent = orig, 1500); }
    });
}

// ── History ─────────────────────────────────────────────────────────────────
async function memoLoadHistory() {
    const list = document.getElementById('memo-history-list');
    if (!list) return;
    try {
        const r = await fetch('/api/memo/history', { headers: _rvmHeaders() });
        if (!r.ok) return;
        const memos = await r.json();
        if (!memos.length) {
            list.innerHTML = '<p style="color:var(--text-tertiary);padding:20px;text-align:center;">No memos generated yet.</p>';
            return;
        }
        list.innerHTML = memos.map(m => `
            <div class="memo-history-item" onclick="memoLoadHistoryItem(${m.id})">
                <div class="memo-history-name">${m.company_name || 'Untitled Memo'}</div>
                <div class="memo-history-meta">
                    ${m.model_used} · ${(m.input_token_count + m.output_token_count).toLocaleString()} tokens · ${m.generation_time_s}s
                </div>
                <div class="memo-history-date">${m.created_at?.substring(0,16).replace('T',' ') || ''}</div>
                <button class="memo-history-delete" onclick="event.stopPropagation();memoDeleteHistory(${m.id})" title="Delete">&times;</button>
            </div>
        `).join('');
    } catch (e) {
        console.error(e);
        list.innerHTML = '<p style="color:var(--text-tertiary);padding:20px;">Failed to load history.</p>';
    }
}

async function memoLoadHistoryItem(id) {
    // Ensure memo tab is visible
    switchTab('memo');

    const rendered = document.getElementById('memo-rendered');
    const empty = document.getElementById('memo-output-empty');
    const actions = document.getElementById('memo-output-actions');
    const meta = document.getElementById('memo-output-meta');

    // Show loading state
    if (rendered) rendered.innerHTML = '<p style="padding:24px;color:var(--text-tertiary)">Loading memo…</p>';
    if (empty) empty.style.display = 'none';
    if (actions) actions.style.display = 'none';
    memoSwitchOutputTab('preview');

    try {
        const r = await fetch(`/api/memo/history/${id}`, { headers: _rvmHeaders() });
        if (!r.ok) {
            let detail = `Error ${r.status}`;
            try { const d = await r.json(); detail = d.detail || detail; } catch {}
            if (rendered) rendered.innerHTML = `<p style="padding:24px;color:#dc2626">Failed to load memo: ${detail}</p>`;
            return;
        }
        const data = await r.json();

        _memo.currentMemoId = data.id;
        _memo.currentMemoMd = data.memo_markdown;
        _memo.currentMemoCompany = data.company_name || _memo.currentMemoCompany || '';
        _memo.sections = data.sections || {};
        _memo.reportData = data.report_data || null;

        if (rendered) rendered.innerHTML = data.memo_html || '<p style="padding:24px;color:var(--text-tertiary)">No content.</p>';
        if (actions) actions.style.display = 'flex';
        if (meta) meta.textContent = `${data.model_used} · ${((data.input_token_count || 0) + (data.output_token_count || 0)).toLocaleString()} tokens · ${data.created_at?.substring(0,16).replace('T',' ')}`;

        // Bind citation handlers
        _memoCitationsBind(rendered);
        // Wrap sections FIRST so section-content divs exist before charts are injected
        if (rendered) {
            _memoWrapSections(rendered, data.sections);
        }
        // Inject data room images
        if (data.image_docs && data.image_docs.length > 0 && rendered) {
            _memoInjectImages(rendered, data.image_docs);
        }
        // Inject pipeline charts LAST — DOM is stable, canvases won't be reparented
        if (data.report_data && rendered) {
            _memoInjectCharts(rendered, data.report_data);
            _memoInjectDealCard(rendered, data.report_data);
        }

        // Cover page, legal disclaimer, and TOC
        _memoInjectFrontMatter(rendered, data);

        // Click on a history item -> open the memo full-screen.
        if (typeof memoSetMode === 'function') memoSetMode('focus');
    } catch (e) {
        console.error('memoLoadHistoryItem error:', e);
        if (rendered) rendered.innerHTML = `<p style="padding:24px;color:#dc2626">Unexpected error: ${e.message}</p>`;
    }
}

async function memoDeleteHistory(id) {
    if (!confirm('Delete this memo?')) return;
    try {
        await fetch(`/api/memo/history/${id}`, { method: 'DELETE', headers: _rvmHeaders() });
        if (_memo.currentMemoId === id) {
            _memo.currentMemoId = null;
            _memo.currentMemoMd = '';
            const rendered = document.getElementById('memo-rendered');
            const empty = document.getElementById('memo-output-empty');
            const actions = document.getElementById('memo-output-actions');
            if (rendered) rendered.innerHTML = '';
            if (empty) empty.style.display = 'flex';
            if (actions) actions.style.display = 'none';
        }
        memoLoadHistory();
    } catch (e) {
        console.error(e);
    }
}

// ── Google Drive Integration ────────────────────────────────────────────────

// Per-user OAuth: each user signs into their own Google account. The IC Memo
// tab shows a "Connect Google Drive" panel until they do; once connected, it
// shows their Google email + a Disconnect button, and the library selector
// becomes available.

async function driveCheckStatus() {
    const statusEl = document.getElementById('memo-drive-status');
    const connectPanel = document.getElementById('memo-drive-connect-panel');
    const connectedPanel = document.getElementById('memo-drive-connect');
    const disconnectBtn = document.getElementById('memo-drive-disconnect');
    if (!statusEl || !connectPanel || !connectedPanel) return;
    try {
        const r = await fetch('/api/drive/connection-status', { headers: _rvmHeaders() });
        if (!r.ok) {
            statusEl.textContent = '';
            return;
        }
        const data = await r.json();
        if (data.connected) {
            statusEl.textContent = 'Connected as ' + (data.google_email || 'Google account');
            statusEl.classList.add('is-connected');
            statusEl.classList.remove('is-disconnected');
            connectPanel.style.display = 'none';
            connectedPanel.style.display = '';
            if (disconnectBtn) disconnectBtn.style.display = '';
            driveLoadLibraries();
        } else {
            statusEl.textContent = 'Not connected';
            statusEl.classList.add('is-disconnected');
            statusEl.classList.remove('is-connected');
            connectPanel.style.display = '';
            connectedPanel.style.display = 'none';
            if (disconnectBtn) disconnectBtn.style.display = 'none';
        }
    } catch (e) {
        console.error('drive status check failed:', e);
        statusEl.textContent = '';
    }
}

function driveConnect() {
    // Top-level navigation — JS can't send the Authorization header on a
    // navigation, so we pass the JWT as a ?token= query param. The
    // /oauth/authorize endpoint accepts either form (Bearer header or query).
    const tok = localStorage.getItem('rvm_token') || '';
    if (!tok) {
        alert('Please log in first.');
        return;
    }
    window.location.href = '/api/drive/oauth/authorize?token=' + encodeURIComponent(tok);
}

async function driveDisconnect() {
    if (!confirm('Disconnect Google Drive? You can reconnect any time.')) return;
    try {
        const r = await fetch('/api/drive/disconnect', {
            method: 'POST',
            headers: _rvmHeaders(),
        });
        if (!r.ok) {
            alert('Failed to disconnect. Please try again.');
            return;
        }
        await driveCheckStatus();
    } catch (e) {
        console.error('drive disconnect failed:', e);
    }
}

// Detect the OAuth callback's redirect query param on page load.
// Auto-switches to the IC Memo tab and refreshes the connection panel so the
// user sees the result right where they started the flow.
function _driveHandleOAuthRedirect() {
    const params = new URLSearchParams(window.location.search);
    const result = params.get('drive_oauth');
    if (!result) return;

    // Auto-jump to the IC Memo tab where the Connect button lives
    try { if (typeof switchTab === 'function') switchTab('memo'); } catch (e) { /* noop */ }

    if (result === 'success') {
        if (typeof showToast === 'function') {
            showToast('Google Drive connected.', 'success');
        }
        // Make sure the panel reflects the new state
        setTimeout(() => { try { driveCheckStatus(); } catch (e) {} }, 100);
    } else if (result === 'error') {
        const reason = (params.get('reason') || 'unknown_error').replace(/_/g, ' ');
        const detail = params.get('detail');
        const redirectUriUsed = params.get('redirect_uri_used');
        let msg = 'Could not connect Google Drive: ' + reason;
        if (detail) msg += '\n\nDetails: ' + detail;
        if (redirectUriUsed) {
            msg += '\n\nRedirect URI the app sent to Google:\n' + redirectUriUsed;
            msg += '\n\nMake sure this exact URL is in your Google Cloud OAuth Client → Authorized redirect URIs list.';
        }
        alert(msg);
    }
    // Strip the query param so refreshes don't re-trigger
    const cleanUrl = window.location.pathname + window.location.hash;
    window.history.replaceState({}, document.title, cleanUrl);
}

// Defer until everything (including the switchTab wrapper) is set up
window.addEventListener('load', () => {
    _driveHandleOAuthRedirect();
});

async function driveLoadLibraries() {
    const sel = document.getElementById('memo-library-select');
    if (!sel) return;
    try {
        const r = await fetch('/api/drive/libraries', { headers: _rvmHeaders() });
        if (!r.ok) return;
        const libs = await r.json();
        _memo.libraries = libs;
        sel.innerHTML = '<option value="">Select a deal library...</option>' +
            libs.map(l => `<option value="${l.id}">${l.company_name} (${l.doc_count} docs${l.sync_status === 'synced' ? '' : ' — ' + l.sync_status})</option>`).join('');
        // Restore selection
        if (_memo.libraryId) sel.value = _memo.libraryId;
    } catch (e) {
        console.error('Failed to load libraries:', e);
    }
}

function driveShowNewLibrary() {
    const el = document.getElementById('memo-drive-new');
    if (el) el.style.display = el.style.display === 'none' ? 'block' : 'none';
}

async function driveCreateLibrary() {
    const company = document.getElementById('memo-drive-company')?.value?.trim();
    const url = document.getElementById('memo-drive-url')?.value?.trim();
    if (!company || !url) return alert('Please enter both company name and Drive folder URL.');

    try {
        const r = await fetch('/api/drive/libraries', {
            method: 'POST',
            headers: { ..._rvmHeaders(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ company_name: company, drive_folder_url: url }),
        });
        if (!r.ok) {
            const err = await r.json().catch(() => ({}));
            return alert(err.detail || 'Failed to link folder.');
        }
        const data = await r.json();
        document.getElementById('memo-drive-new').style.display = 'none';
        document.getElementById('memo-drive-company').value = '';
        document.getElementById('memo-drive-url').value = '';
        await driveLoadLibraries();
        // Select the new library
        const sel = document.getElementById('memo-library-select');
        if (sel) { sel.value = data.id; driveLibraryChanged(); }
    } catch (e) {
        console.error(e);
        alert('Failed to create library: ' + e.message);
    }
}

async function driveLibraryChanged() {
    const sel = document.getElementById('memo-library-select');
    const info = document.getElementById('memo-drive-library-info');
    const meta = document.getElementById('memo-drive-meta');
    const status = document.getElementById('memo-drive-status');

    const libId = sel?.value ? parseInt(sel.value) : null;
    _memo.libraryId = libId;

    if (!libId) {
        if (info) info.style.display = 'none';
        if (status) status.textContent = '';
        return;
    }

    try {
        const r = await fetch(`/api/drive/libraries/${libId}`, { headers: _rvmHeaders() });
        if (!r.ok) return;
        const lib = await r.json();

        if (info) info.style.display = 'block';
        if (status) {
            const statusText = lib.sync_status === 'synced' ? 'Synced' : lib.sync_status === 'never' ? 'Not synced' : lib.sync_status;
            status.textContent = statusText;
            status.className = 'memo-drive-status ' + (lib.sync_status === 'synced' ? 'synced' : 'pending');
        }
        if (meta) {
            const lastSync = lib.last_synced_at ? new Date(lib.last_synced_at).toLocaleString() : 'Never';
            meta.innerHTML = `<strong>${lib.company_name}</strong> &middot; ${lib.doc_count} documents &middot; Last sync: ${lastSync}`;
        }

        // Show documents
        const docList = document.getElementById('memo-drive-doc-list');
        if (docList && lib.documents?.length) {
            docList.innerHTML = lib.documents.map(d => {
                const cat = d.doc_category.replace(/_/g, ' ');
                const path = d.subfolder_path ? `<span class="memo-drive-doc-path">${d.subfolder_path}</span>` : '';
                const size = d.file_size ? `${(d.file_size / 1024).toFixed(0)}KB` : '';
                const extracted = d.extracted_chars ? `${(d.extracted_chars / 1000).toFixed(1)}K chars` : 'no text';
                return `<div class="memo-drive-doc-item">
                    <div class="memo-drive-doc-name">${path}${d.file_name}</div>
                    <div class="memo-drive-doc-meta"><span class="memo-badge">${cat}</span> ${size} &middot; ${extracted}</div>
                </div>`;
            }).join('');
        } else if (docList) {
            docList.innerHTML = '<p class="memo-helper-text">No documents synced yet. Click "Sync from Drive" to pull files.</p>';
        }
    } catch (e) {
        console.error(e);
    }
}

async function driveSyncLibrary() {
    if (!_memo.libraryId) return;
    const btn = document.getElementById('memo-drive-sync-btn');
    const status = document.getElementById('memo-drive-status');
    if (btn) { btn.disabled = true; btn.textContent = 'Syncing...'; }
    if (status) { status.textContent = 'Syncing...'; status.className = 'memo-drive-status syncing'; }

    try {
        const r = await fetch(`/api/drive/libraries/${_memo.libraryId}/sync`, {
            method: 'POST',
            headers: _rvmHeaders(),
        });
        if (!r.ok) {
            const err = await r.json().catch(() => ({}));
            alert(err.detail || 'Sync failed.');
            return;
        }
        const data = await r.json();
        if (status) { status.textContent = `Synced (${data.stats.new} new, ${data.stats.updated} updated, ${data.total_docs} total)`; status.className = 'memo-drive-status synced'; }
        // Refresh the library view
        await driveLibraryChanged();
        await driveLoadLibraries();
    } catch (e) {
        console.error(e);
        alert('Sync error: ' + e.message);
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = 'Sync from Drive'; }
    }
}

// ═══════════════════════════════════════════════════════════════════════════════
// ═══════════════════════════════════════════════════════════════════════════════
//  SECTION-LEVEL REVIEW & REVISION
// ═══════════════════════════════════════════════════════════════════════════════

const _MEMO_SECTION_KEYS = {
    'investment overview': 'investment_overview',
    'high level opportunities': 'high_level_opportunities',
    'high level risks': 'high_level_risks',
    'company overview': 'company_overview',
    'market': 'market',
    'business model': 'business_model',
    'financials': 'financials',
    'team': 'team',
    'traction': 'traction',
    'competitive position': 'competitive_position',
    'carbon impact': 'carbon_impact',
    'technology': 'technology_ip_moat',
    'financing overview': 'financing_overview',
    'cohort analysis': 'cohort_analysis',
    'fund return model': 'fund_return_model',
    'portfolio tracking scorecard': 'portfolio_tracking_scorecard',
    'investment recommendation': 'recommendation',
};

function _memoMatchSectionKey(h2Text) {
    const lower = h2Text.toLowerCase().trim();
    for (const [match, key] of Object.entries(_MEMO_SECTION_KEYS)) {
        if (lower.includes(match)) return key;
    }
    return null;
}

function _memoWrapSections(container, sections) {
    const h2s = Array.from(container.querySelectorAll('h2'));
    for (const h2 of h2s) {
        const sectionKey = _memoMatchSectionKey(h2.textContent);
        // Wrap any recognised section H2 — even if sections[sectionKey] is empty
        // (happens when memo was loaded from history or sections_json was not fully saved)
        if (!sectionKey) continue;

        // Collect all elements belonging to this section (until next H2)
        const sectionEls = [];
        let sibling = h2.nextElementSibling;
        while (sibling && sibling.tagName !== 'H2') {
            sectionEls.push(sibling);
            sibling = sibling.nextElementSibling;
        }

        // Create wrapper
        const wrapper = document.createElement('div');
        wrapper.className = 'memo-section-wrapper';
        wrapper.dataset.sectionKey = sectionKey;

        // Create toolbar
        const toolbar = document.createElement('div');
        toolbar.className = 'memo-section-toolbar';
        toolbar.innerHTML = `
            <div class="memo-section-toolbar-left">
                <span class="memo-section-key-badge">${sectionKey.replace(/_/g, ' ')}</span>
            </div>
            <div class="memo-section-toolbar-right">
                <button class="memo-sect-btn memo-sect-btn-lock" id="memo-lock-btn-${sectionKey}" onclick="_memoToggleLock('${sectionKey}')" title="Lock this section so it is preserved on regeneration">🔓 Lock</button>
                <button class="memo-sect-btn memo-sect-btn-edit" onclick="_memoStartDirectEdit('${sectionKey}')" title="Edit text directly">Edit</button>
                <button class="memo-sect-btn memo-sect-btn-revise" onclick="_memoStartRevise('${sectionKey}')" title="Give LLM revision instructions">Revise</button>
                <button class="memo-sect-btn memo-sect-btn-history" onclick="_memoShowHistory('${sectionKey}')" title="View revision history">History</button>
            </div>
        `;

        // Create content wrapper
        const content = document.createElement('div');
        content.className = 'memo-section-content';
        content.id = `memo-sect-content-${sectionKey}`;

        // Create revise panel (hidden)
        const revisePanel = document.createElement('div');
        revisePanel.className = 'memo-revise-panel';
        revisePanel.id = `memo-revise-panel-${sectionKey}`;
        revisePanel.style.display = 'none';
        revisePanel.innerHTML = `
            <textarea class="memo-revise-input" id="memo-revise-input-${sectionKey}" placeholder="e.g. 'Make the tone more positive', 'Add more detail about the competitive moat', 'Incorporate the attached board report data'..." rows="3"></textarea>
            <div class="memo-revise-actions">
                <button class="memo-sect-btn memo-sect-btn-revise" onclick="_memoSubmitRevise('${sectionKey}')">Revise Section</button>
                <button class="memo-sect-btn" onclick="_memoCancelRevise('${sectionKey}')">Cancel</button>
                <span class="memo-revise-status" id="memo-revise-status-${sectionKey}"></span>
            </div>
        `;

        // Create edit panel (hidden)
        const editPanel = document.createElement('div');
        editPanel.className = 'memo-edit-panel';
        editPanel.id = `memo-edit-panel-${sectionKey}`;
        editPanel.style.display = 'none';
        editPanel.innerHTML = `
            <textarea class="memo-edit-textarea" id="memo-edit-textarea-${sectionKey}" rows="15"></textarea>
            <div class="memo-edit-actions">
                <button class="memo-sect-btn memo-sect-btn-edit" onclick="_memoSaveDirectEdit('${sectionKey}')">Save Changes</button>
                <button class="memo-sect-btn" onclick="_memoCancelDirectEdit('${sectionKey}')">Cancel</button>
                <span class="memo-edit-status" id="memo-edit-status-${sectionKey}"></span>
            </div>
        `;

        // Create history panel (hidden)
        const historyPanel = document.createElement('div');
        historyPanel.className = 'memo-history-panel';
        historyPanel.id = `memo-history-panel-${sectionKey}`;
        historyPanel.style.display = 'none';

        // Insert wrapper before h2, then move h2 + content into it
        h2.parentNode.insertBefore(wrapper, h2);
        wrapper.appendChild(toolbar);
        wrapper.appendChild(h2);
        wrapper.appendChild(content);
        for (const el of sectionEls) {
            content.appendChild(el);
        }
        wrapper.appendChild(revisePanel);
        wrapper.appendChild(editPanel);
        wrapper.appendChild(historyPanel);
    }
}

function _memoStartRevise(sectionKey) {
    // Hide any other open panels
    document.querySelectorAll('.memo-revise-panel, .memo-edit-panel, .memo-history-panel').forEach(p => p.style.display = 'none');
    const panel = document.getElementById(`memo-revise-panel-${sectionKey}`);
    if (panel) {
        panel.style.display = 'block';
        const input = document.getElementById(`memo-revise-input-${sectionKey}`);
        if (input) input.focus();
    }
}

function _memoCancelRevise(sectionKey) {
    const panel = document.getElementById(`memo-revise-panel-${sectionKey}`);
    if (panel) panel.style.display = 'none';
}

async function _memoSubmitRevise(sectionKey) {
    const input = document.getElementById(`memo-revise-input-${sectionKey}`);
    const status = document.getElementById(`memo-revise-status-${sectionKey}`);
    const instructions = input?.value?.trim();
    if (!instructions) { if (status) status.textContent = 'Enter revision instructions'; return; }

    const memoId = _memo.currentMemoId;
    if (!memoId) { if (status) status.textContent = 'No memo loaded'; return; }

    if (status) { status.textContent = 'Revising section...'; status.style.color = '#607D8B'; }

    try {
        const r = await fetch('/api/memo/revise-section', {
            method: 'POST',
            headers: _rvmHeaders(),
            body: JSON.stringify({
                memo_id: memoId,
                section_key: sectionKey,
                instructions: instructions,
            }),
        });
        const d = await r.json();
        if (!r.ok) throw new Error(d.detail || 'Revision failed');

        // Update stored section text
        _memo.sections[sectionKey] = d.new_text;

        // Update the rendered content
        const contentEl = document.getElementById(`memo-sect-content-${sectionKey}`);
        if (contentEl) {
            // Parse the section_html and extract inner content (skip the H2)
            const temp = document.createElement('div');
            temp.innerHTML = d.section_html;
            const newH2 = temp.querySelector('h2');
            if (newH2) newH2.remove();
            contentEl.innerHTML = temp.innerHTML;
        }

        // Re-inject charts if we have report data
        if (_memo.reportData) {
            const rendered = document.getElementById('memo-rendered');
            if (rendered) { _memoInjectCharts(rendered, _memo.reportData); _memoInjectDealCard(rendered, _memo.reportData); }
        }

        if (status) { status.textContent = `Revised (${d.model_used}, ${(d.tokens_in + d.tokens_out).toLocaleString()} tokens)`; status.style.color = '#5B7744'; }
        if (input) input.value = '';

        // Hide panel after short delay
        setTimeout(() => {
            const panel = document.getElementById(`memo-revise-panel-${sectionKey}`);
            if (panel) panel.style.display = 'none';
        }, 2000);

    } catch (e) {
        if (status) { status.textContent = e.message; status.style.color = '#dc2626'; }
    }
}

function _memoStartDirectEdit(sectionKey) {
    document.querySelectorAll('.memo-revise-panel, .memo-edit-panel, .memo-history-panel').forEach(p => p.style.display = 'none');
    const panel = document.getElementById(`memo-edit-panel-${sectionKey}`);
    const textarea = document.getElementById(`memo-edit-textarea-${sectionKey}`);
    if (panel && textarea) {
        // Prefer stored markdown; fall back to rendered text from DOM
        const stored = _memo.sections[sectionKey];
        if (stored) {
            textarea.value = stored;
        } else {
            const contentEl = document.getElementById(`memo-sect-content-${sectionKey}`);
            textarea.value = contentEl ? contentEl.innerText : '';
        }
        panel.style.display = 'block';
        textarea.focus();
    }
}

function _memoCancelDirectEdit(sectionKey) {
    const panel = document.getElementById(`memo-edit-panel-${sectionKey}`);
    if (panel) panel.style.display = 'none';
}

// ── Section Lock / Unlock ────────────────────────────────────────────────────
// Locked sections are preserved verbatim on memo regeneration.
// The lock state is held in _memo.lockedSections (in-memory) and reflected
// in the toolbar button UI. When the user regenerates, locked sections are
// passed as req.locked_sections and the LLM is never called for them.

function _memoToggleLock(sectionKey) {
    const btn = document.getElementById(`memo-lock-btn-${sectionKey}`);
    const isLocked = sectionKey in _memo.lockedSections;

    if (isLocked) {
        // Unlock: remove from locked set
        delete _memo.lockedSections[sectionKey];
        if (btn) {
            btn.textContent = '🔓 Lock';
            btn.title = 'Lock this section so it is preserved on regeneration';
            btn.style.background = '';
            btn.style.color = '';
            btn.style.fontWeight = '';
        }
        // Remove visual indicator from wrapper
        const wrapper = btn?.closest('.memo-section-wrapper');
        if (wrapper) wrapper.style.outline = '';
        showToast(`Section unlocked — will be regenerated next time`);
    } else {
        // Lock: capture the current stored text
        const text = _memo.sections[sectionKey] || '';
        if (!text) {
            showToast('Save your edits first before locking');
            return;
        }
        _memo.lockedSections[sectionKey] = text;
        if (btn) {
            btn.textContent = '🔒 Locked';
            btn.title = 'Click to unlock — this section will be preserved on regeneration';
            btn.style.background = '#5B7744';
            btn.style.color = '#fff';
            btn.style.fontWeight = '600';
        }
        // Visual indicator on the section wrapper
        const wrapper = btn?.closest('.memo-section-wrapper');
        if (wrapper) wrapper.style.outline = '2px solid #5B7744';
        showToast(`Section locked — your edits will be preserved on regeneration`);
    }
    // Update the lock counter badge
    _memoUpdateLockBadge();
}

function _memoUpdateLockBadge() {
    const n = Object.keys(_memo.lockedSections).length;
    const badge = document.getElementById('memo-lock-badge');
    if (!badge) return;
    badge.textContent = n > 0 ? `${n} locked` : '';
    badge.style.display = n > 0 ? 'inline-block' : 'none';
}

function _memoLockAll() {
    // Lock every section that has saved text
    for (const [key, text] of Object.entries(_memo.sections)) {
        if (!text) continue;
        _memo.lockedSections[key] = text;
        const btn = document.getElementById(`memo-lock-btn-${key}`);
        if (btn) {
            btn.textContent = '🔒 Locked';
            btn.title = 'Click to unlock';
            btn.style.background = '#5B7744';
            btn.style.color = '#fff';
            btn.style.fontWeight = '600';
        }
        const wrapper = btn?.closest('.memo-section-wrapper');
        if (wrapper) wrapper.style.outline = '2px solid #5B7744';
    }
    _memoUpdateLockBadge();
    showToast(`All sections locked — your edits will be preserved on regeneration`);
}

function _memoUnlockAll() {
    _memo.lockedSections = {};
    document.querySelectorAll('[id^="memo-lock-btn-"]').forEach(btn => {
        btn.textContent = '🔓 Lock';
        btn.title = 'Lock this section so it is preserved on regeneration';
        btn.style.background = '';
        btn.style.color = '';
        btn.style.fontWeight = '';
        const wrapper = btn?.closest('.memo-section-wrapper');
        if (wrapper) wrapper.style.outline = '';
    });
    _memoUpdateLockBadge();
    showToast('All sections unlocked');
}

async function _memoSaveDirectEdit(sectionKey) {
    const textarea = document.getElementById(`memo-edit-textarea-${sectionKey}`);
    const status = document.getElementById(`memo-edit-status-${sectionKey}`);
    const newText = textarea?.value;
    if (newText == null) return;

    const memoId = _memo.currentMemoId;
    if (!memoId) { if (status) status.textContent = 'No memo loaded'; return; }

    if (status) { status.textContent = 'Saving...'; status.style.color = '#607D8B'; }

    try {
        const r = await fetch('/api/memo/edit-section', {
            method: 'POST',
            headers: _rvmHeaders(),
            body: JSON.stringify({
                memo_id: memoId,
                section_key: sectionKey,
                new_text: newText,
            }),
        });
        const d = await r.json();
        if (!r.ok) throw new Error(d.detail || 'Save failed');

        _memo.sections[sectionKey] = d.new_text;

        const contentEl = document.getElementById(`memo-sect-content-${sectionKey}`);
        if (contentEl) {
            const temp = document.createElement('div');
            temp.innerHTML = d.section_html;
            const newH2 = temp.querySelector('h2');
            if (newH2) newH2.remove();
            contentEl.innerHTML = temp.innerHTML;
        }

        if (status) { status.textContent = 'Saved'; status.style.color = '#5B7744'; }
        setTimeout(() => {
            const panel = document.getElementById(`memo-edit-panel-${sectionKey}`);
            if (panel) panel.style.display = 'none';
        }, 1500);

    } catch (e) {
        if (status) { status.textContent = e.message; status.style.color = '#dc2626'; }
    }
}

async function _memoShowHistory(sectionKey) {
    document.querySelectorAll('.memo-revise-panel, .memo-edit-panel, .memo-history-panel').forEach(p => p.style.display = 'none');
    const panel = document.getElementById(`memo-history-panel-${sectionKey}`);
    if (!panel) return;

    const memoId = _memo.currentMemoId;
    if (!memoId) { panel.innerHTML = '<p>No memo loaded</p>'; panel.style.display = 'block'; return; }

    panel.innerHTML = '<p style="color:var(--text-secondary);font-size:0.85rem;">Loading history...</p>';
    panel.style.display = 'block';

    try {
        const r = await fetch(`/api/memo/revisions/${memoId}/${sectionKey}`, { headers: _rvmHeaders() });
        const revisions = await r.json();
        if (!r.ok) throw new Error('Failed to load history');

        if (!revisions.length) {
            panel.innerHTML = '<p style="color:var(--text-secondary);font-size:0.85rem;padding:8px 0;">No revisions yet for this section.</p>';
            return;
        }

        let html = '<div class="memo-history-list">';
        html += `<div class="memo-history-header"><span>${revisions.length} revision${revisions.length > 1 ? 's' : ''}</span><button class="memo-sect-btn" onclick="this.closest(\'.memo-history-panel\').style.display=\'none\'">Close</button></div>`;
        for (const rev of revisions) {
            const typeLabel = rev.revision_type === 'llm' ? '🤖 LLM Revision' : '✏️ Manual Edit';
            const time = new Date(rev.created_at + 'Z').toLocaleString();
            html += `<div class="memo-history-item">
                <div class="memo-history-item-header">
                    <span class="memo-history-type">${typeLabel}</span>
                    <span class="memo-history-by">${rev.revised_by}</span>
                    <span class="memo-history-time">${time}</span>
                </div>
                ${rev.instructions ? `<div class="memo-history-instructions">"${rev.instructions}"</div>` : ''}
                ${rev.model_used ? `<div class="memo-history-model">${rev.model_used} · ${(rev.tokens_in + rev.tokens_out).toLocaleString()} tokens</div>` : ''}
            </div>`;
        }
        html += '</div>';
        panel.innerHTML = html;
    } catch (e) {
        panel.innerHTML = `<p style="color:#dc2626;font-size:0.85rem;">${e.message}</p>`;
    }
}


//  MEMO CHART INJECTION — Embed deal pipeline charts into rendered memo sections
// ═══════════════════════════════════════════════════════════════════════════════

const _memoChartInstances = [];
// Charts queued by inline memo section renderers (funnel, outcome, etc.).
// Producer pushes here; consumer drains and renders when the memo DOM is
// stable. Must be declared before either side runs — otherwise the
// `_pendingCharts = _pendingCharts || []` shorthand throws ReferenceError
// because reading an undeclared variable is fatal in strict mode.
let _pendingCharts = [];


function _memoInjectDealCard(container, report) {
    if (!report) return;
    const ov = report.deal_overview || {};
    const hero = report.hero_metrics || {};

    if (!ov.company_name) return;

    const fmt1 = (v) => v != null ? Number(v).toFixed(1) : 'N/A';
    const pct1 = (v) => v != null ? (v * 100).toFixed(1) + '%' : 'N/A';

    // Build the card HTML — mirrors the deal report cover block
    const archLabel = (s) => (s||'').replace(/_/g,' ').replace(/\w/g, c => c.toUpperCase());
    const tags = [ov.entry_stage, archLabel(ov.archetype), ov.trl_label ? `TRL ${ov.trl} (${ov.trl_label})` : null]
        .filter(Boolean).map(t => `<span class="memo-dc-tag">${t}</span>`).join('');

    const html = `
    <div class="memo-deal-card">
        <div class="memo-dc-tags">${tags}</div>
        <div class="memo-dc-terms">
            <div class="memo-dc-term"><span class="memo-dc-label">CHECK SIZE</span><span class="memo-dc-val">$${fmt1(ov.check_size_millions)}M</span></div>
            <div class="memo-dc-term"><span class="memo-dc-label">ROUND SIZE</span><span class="memo-dc-val">$${fmt1(ov.round_size_millions)}M</span></div>
            <div class="memo-dc-term"><span class="memo-dc-label">PRE-MONEY</span><span class="memo-dc-val">$${fmt1(ov.pre_money_millions)}M</span></div>
            <div class="memo-dc-term"><span class="memo-dc-label">POST-MONEY</span><span class="memo-dc-val">$${fmt1(ov.post_money_millions)}M</span></div>
            <div class="memo-dc-term"><span class="memo-dc-label">ENTRY OWNERSHIP</span><span class="memo-dc-val">${fmt1(ov.entry_ownership_pct)}%</span></div>
        </div>
        <div class="memo-dc-hero">
            <div class="memo-dc-hero-card accent"><div class="memo-dc-hero-num">${fmt1(hero.expected_moic)}x</div><div class="memo-dc-hero-lbl">EXPECTED MOIC</div></div>
            <div class="memo-dc-hero-card"><div class="memo-dc-hero-num">${pct1(hero.p_gt_3x)}</div><div class="memo-dc-hero-lbl">P(&gt;3X)</div></div>
            <div class="memo-dc-hero-card"><div class="memo-dc-hero-num">${pct1(hero.expected_irr)}</div><div class="memo-dc-hero-lbl">EXPECTED IRR</div></div>
            <div class="memo-dc-hero-card"><div class="memo-dc-hero-num">${pct1(hero.survival_rate)}</div><div class="memo-dc-hero-lbl">SURVIVAL RATE</div></div>
        </div>
    </div>`;

    // Find the Investment Overview H2
    let targetH2 = null;
    for (const h2 of container.querySelectorAll('h2')) {
        if (h2.textContent.toLowerCase().includes('investment overview')) {
            targetH2 = h2;
            break;
        }
    }
    if (!targetH2) return;

    // Insert immediately after the H2, before any paragraph text
    const div = document.createElement('div');
    div.innerHTML = html;
    targetH2.after(div.firstElementChild);
}

function _memoInjectCharts(container, report) {
    // Destroy any previous chart instances
    _memoChartInstances.forEach(c => { try { c.destroy(); } catch(e) {} });
    _memoChartInstances.length = 0;

    // Remove all previously injected blocks so re-runs don't stack duplicates
    container.querySelectorAll('.memo-chart-inline').forEach(el => el.remove());

    const r = report;
    const sim = r.simulation || {};
    const carbon = r.carbon_impact || {};
    const adoption = r.adoption_analysis || {};
    const fComp = r.founder_comparison || {};
    const valCtx = r.valuation_context || {};
    const pImpact = r.portfolio_impact || {};
    const ps = r.position_sizing || {};
    const ov = r.deal_overview || {};
    const hero = r.hero_metrics || {};
    const prob = sim.probability || {};
    const moic = sim.moic_unconditional || {};
    const moicC = sim.moic_conditional || {};

    const fmt = (v, d=2) => v != null ? Number(v).toLocaleString(undefined, {minimumFractionDigits: d, maximumFractionDigits: d}) : 'N/A';
    const pctFmt = (v) => v != null ? (v * 100).toLocaleString(undefined, {minimumFractionDigits: 1, maximumFractionDigits: 1}) + '%' : 'N/A';

    // Find memo sections by H2 title text
    function findSection(titleMatch) {
        const h2s = container.querySelectorAll('h2');
        for (const h2 of h2s) {
            if (h2.textContent.toLowerCase().includes(titleMatch.toLowerCase())) return h2;
        }
        return null;
    }

    // Get the container element for a section's content.
    // After _memoWrapSections runs, content lives inside .memo-section-content.
    // Before wrapping (flat DOM), we walk nextElementSibling until the next H2.
    function getSectionContainer(h2) {
        if (!h2) return null;
        // Wrapped DOM: h2 is inside .memo-section-wrapper, content is in sibling .memo-section-content
        const wrapper = h2.closest('.memo-section-wrapper');
        if (wrapper) return wrapper.querySelector('.memo-section-content') || wrapper;
        return null; // flat DOM — handled by getSectionElements
    }

    function getSectionElements(h2) {
        if (!h2) return [];
        const contentDiv = getSectionContainer(h2);
        if (contentDiv) return Array.from(contentDiv.children);
        // Flat DOM fallback (before wrapping)
        const elements = [];
        let el = h2.nextElementSibling;
        while (el && el.tagName !== 'H2') {
            elements.push(el);
            el = el.nextElementSibling;
        }
        return elements;
    }

    // Insert content INLINE within a section — finds the best paragraph to insert after
    // based on keyword matching. Falls back to appending at section end.
    function insertInline(h2, html, keywords) {
        if (!h2) return null;
        const div = document.createElement('div');
        div.className = 'memo-chart-inline';
        div.innerHTML = html;

        // Determine the parent container to append into (wrapped or flat)
        const contentContainer = getSectionContainer(h2);
        const sectionEls = getSectionElements(h2);

        if (keywords && keywords.length > 0) {
            // Score each paragraph by keyword match density
            let bestEl = null;
            let bestScore = 0;
            for (const el of sectionEls) {
                if (el.tagName === 'P' || el.tagName === 'UL' || el.tagName === 'OL') {
                    const text = el.textContent.toLowerCase();
                    let score = 0;
                    for (const kw of keywords) {
                        if (text.includes(kw.toLowerCase())) score++;
                    }
                    if (score > bestScore) {
                        bestScore = score;
                        bestEl = el;
                    }
                }
            }
            if (bestEl && bestScore > 0) {
                // Insert AFTER the matching paragraph
                bestEl.after(div);
                return div;
            }
        }

        // Fallback: insert after the first substantial paragraph (>50 chars)
        for (const el of sectionEls) {
            if (el.tagName === 'P' && el.textContent.length > 50) {
                el.after(div);
                return div;
            }
        }

        // Final fallback: append into content container (wrapped) or before next H2 (flat)
        if (contentContainer) {
            contentContainer.appendChild(div);
        } else {
            let insertBefore = h2.nextElementSibling;
            while (insertBefore && insertBefore.tagName !== 'H2') {
                insertBefore = insertBefore.nextElementSibling;
            }
            if (insertBefore) container.insertBefore(div, insertBefore);
            else container.appendChild(div);
        }
        return div;
    }

    // Append a chart block at the end of a section
    function appendToSection(h2, html) {
        if (!h2) return null;
        const div = document.createElement('div');
        div.className = 'memo-chart-inline';
        div.innerHTML = html;
        const contentContainer = getSectionContainer(h2);
        if (contentContainer) {
            contentContainer.appendChild(div);
        } else {
            // Flat DOM fallback
            let insertBefore = h2.nextElementSibling;
            while (insertBefore && insertBefore.tagName !== 'H2') {
                insertBefore = insertBefore.nextElementSibling;
            }
            if (insertBefore) container.insertBefore(div, insertBefore);
            else container.appendChild(div);
        }
        return div;
    }

    // Shared chart styling
    const chartFont = "'Inter', 'system-ui', sans-serif";
    const chartColors = {
        green: '#5B7744', sage: '#8BA872', blueSteel: '#607D8B',
        accent: '#3B5249', danger: '#dc2626', muted: '#94a3b8',
        light: 'rgba(91,119,68,0.12)', band: 'rgba(91,119,68,0.08)',
    };

    // ── 1. FINANCING OVERVIEW: Break into inline sub-blocks ──
    const finH2 = findSection('financing overview');
    if (finH2) {
        // Hero metrics — insert near the top of the section (after first paragraph about the deal)
        insertInline(finH2, `<div class="memo-chart-context">
            <p class="memo-chart-label">RVM Deal Metrics Summary</p>
            <p>The VoLo Return Validation Model (RVM) simulated ${(sim.n_simulations||5000).toLocaleString()} Monte Carlo paths to derive the following key deal metrics:</p>
            <div class="memo-rvm-hero">
                <div class="memo-rvm-hero-card accent"><div class="memo-rvm-hero-num">${fmt(hero.expected_moic)}x</div><div class="memo-rvm-hero-lbl">Expected MOIC</div></div>
                <div class="memo-rvm-hero-card"><div class="memo-rvm-hero-num">${pctFmt(hero.p_gt_3x)}</div><div class="memo-rvm-hero-lbl">P(>3x)</div></div>
                <div class="memo-rvm-hero-card"><div class="memo-rvm-hero-num">${pctFmt(hero.expected_irr)}</div><div class="memo-rvm-hero-lbl">Expected IRR</div></div>
                <div class="memo-rvm-hero-card"><div class="memo-rvm-hero-num">${pctFmt(hero.survival_rate)}</div><div class="memo-rvm-hero-lbl">Survival Rate</div></div>
            </div>
            <p class="memo-chart-note">Probability distribution: P(loss)=${pctFmt(prob.total_loss)}, P(>1x)=${pctFmt(prob.gt_1x)}, P(>3x)=${pctFmt(prob.gt_3x)}, P(>5x)=${pctFmt(prob.gt_5x)}, P(>10x)=${pctFmt(prob.gt_10x)}</p>
        </div>`, ['moic', 'irr', 'return', 'monte carlo', 'simulation', 'rvm', 'deal structure']);

        // MOIC + Outcome charts — inline after return/MOIC discussion
        if (sim.moic_histogram || sim.outcome_breakdown) {
            insertInline(finH2, `<div class="memo-chart-context">
                <p class="memo-chart-label">Return Distribution Analysis</p>
                <p class="memo-chart-caption">MOIC distribution conditional on survival to exit. Outcome breakdown shows probability-weighted split across exit scenarios.</p>
                <div class="memo-chart-row">
                    ${sim.moic_histogram ? `<div class="memo-chart-wrap"><canvas id="memo-moic-chart" height="260"></canvas></div>` : ''}
                    ${sim.outcome_breakdown ? `<div class="memo-chart-wrap"><canvas id="memo-outcome-chart" height="260"></canvas></div>` : ''}
                </div>
            </div>`, ['moic', 'distribution', 'return', 'outcome', 'exit', 'probability', 'loss']);
        }

        // Variance drivers table — inline after risk/sensitivity discussion
        if (sim.variance_drivers) {
            const vExp = sim.variance_explanations || {};
            let vRows = '';
            for (const [k, v] of Object.entries(sim.variance_drivers)) {
                const exp = typeof vExp[k] === 'object' ? (vExp[k]?.explanation || '') : (vExp[k] || '');
                vRows += `<tr><td style="text-transform:capitalize">${k.replace(/_/g, ' ')}</td><td style="text-align:right;font-weight:600;">${fmt(v * 100, 1)}%</td><td>${exp}</td></tr>`;
            }
            insertInline(finH2, `<div class="memo-chart-context">
                <p class="memo-chart-label">Variance Drivers</p>
                <p class="memo-chart-caption">Decomposition of outcome uncertainty ranked by contribution to total return variance:</p>
                <table class="memo-rvm-table"><thead><tr><th>Driver</th><th>Contribution</th><th>Explanation</th></tr></thead><tbody>${vRows}</tbody></table>
            </div>`, ['variance', 'sensitivity', 'driver', 'risk', 'uncertainty']);
        }

        // Portfolio impact — inline after portfolio/fund discussion
        if (pImpact.has_data) {
            const nCommitted = pImpact.n_committed_deals || 0;
            insertInline(finH2, `<div class="memo-chart-context">
                <p class="memo-chart-label">Simulated Portfolio Impact</p>
                <p>Adding this deal to ${nCommitted > 0 ? 'the running fund (' + nCommitted + ' committed deals)' : 'a simulated VoLo portfolio'} produces the following marginal impact on fund-level returns:</p>
                <table class="memo-rvm-table"><thead><tr><th>Metric</th><th>Base Portfolio</th><th>With Deal</th><th>Delta</th></tr></thead><tbody>
                    <tr><td>TVPI (Mean)</td><td style="text-align:right;">${fmt(pImpact.tvpi_base_mean)}x</td><td style="text-align:right;">${fmt(pImpact.tvpi_new_mean)}x</td><td style="text-align:right;font-weight:600;">${(pImpact.tvpi_mean_lift >= 0 ? '+' : '') + fmt(pImpact.tvpi_mean_lift)}x</td></tr>
                    <tr><td>TVPI (P50)</td><td style="text-align:right;">${fmt(pImpact.tvpi_base_p50)}x</td><td style="text-align:right;">${fmt(pImpact.tvpi_new_p50)}x</td><td style="text-align:right;font-weight:600;">${((pImpact.tvpi_new_p50 - pImpact.tvpi_base_p50) >= 0 ? '+' : '') + fmt(pImpact.tvpi_new_p50 - pImpact.tvpi_base_p50)}x</td></tr>
                    <tr><td>IRR (Mean)</td><td style="text-align:right;">${pctFmt(pImpact.irr_base_mean)}</td><td style="text-align:right;">${pctFmt(pImpact.irr_new_mean)}</td><td style="text-align:right;font-weight:600;">${((pImpact.irr_mean_lift*100) >= 0 ? '+' : '') + (pImpact.irr_mean_lift*100).toFixed(1)}pp</td></tr>
                </tbody></table>
            </div>`, ['portfolio', 'fund', 'tvpi', 'marginal', 'impact']);
        }

        // Follow-on charts rendered in Fund Return Model section only (memo-frm-fo-* canvases)
    }

    // ── 2. MARKET: S-curve adoption chart — inline after adoption/TAM paragraphs ──
    const marketH2 = findSection('market');
    if (marketH2 && adoption.scurve) {
        const scBp = adoption.scurve?.bass_p_mean;
        const scBq = adoption.scurve?.bass_q_mean;
        const archLabel = (s) => (s||'').replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
        const mktHtml = `<div class="memo-chart-context">
            <p class="memo-chart-label">Technology Adoption S-Curve</p>
            <p class="memo-chart-caption">Market adoption trajectory calibrated from NREL ATB data using Bass diffusion parameters (p=${scBp?.toFixed(4) || 'N/A'}, q=${scBq?.toFixed(3) || 'N/A'}) for the ${archLabel(ov.archetype)} archetype. Uncertainty cone: P10–P90 across ${(sim.n_simulations||5000).toLocaleString()} paths.</p>
            <div class="memo-chart-wrap"><canvas id="memo-scurve-chart" height="300"></canvas></div>
        </div>`;
        insertInline(marketH2, mktHtml, ['adoption', 'tam', 'market size', 'serviceable', 'bass', 's-curve', 'diffusion', 'penetration']);
    }

    // ── 3. BUSINESS MODEL: Revenue cone — inline after revenue/projection paragraphs ──
    const bizH2 = findSection('business model');
    if (bizH2 && sim.revenue_trajectories) {
        const revSource = sim.revenue_trajectories?.source || 'scurve_derived';
        const bizHtml = `<div class="memo-chart-context">
            <p class="memo-chart-label">Revenue Cone vs Founder Projections</p>
            <p class="memo-chart-caption">The ${revSource === 'founder_anchored' ? 'founder-anchored' : 'S-curve-derived'} simulation shows P10–P90 revenue paths. ${fComp.has_data ? 'Founder projections overlaid — divergences flag where management assumptions depart from the model.' : ''}</p>
            <div class="memo-chart-wrap"><canvas id="memo-revenue-cone-chart" height="300"></canvas></div>
        </div>`;
        insertInline(bizH2, bizHtml, ['revenue', 'projection', 'forecast', 'growth', 'founder', 'diverge', 'trajectory', 'simulation']);
    }

    // ── 4. FINANCIALS: S-Curve adoption + Revenue Cone + Founder Comparison table ──
    const finSectionH2 = findSection('financials');
    if (finSectionH2) {
        const scBp = adoption.scurve?.bass_p_mean;
        const scBq = adoption.scurve?.bass_q_mean;
        const archLabel2 = (s) => (s||'').replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());

        // Founder comparison table
        if (fComp.has_data && fComp.revenue?.year_by_year?.length) {
            let tableRows = '';
            fComp.revenue.year_by_year.forEach(y => {
                const rowCls = y.in_band ? '' : (y.divergence_pct > 0 ? ' class="memo-rvm-above"' : ' class="memo-rvm-below"');
                const divPct = y.divergence_pct != null ? (y.divergence_pct > 0 ? '+' : '') + Number(y.divergence_pct).toFixed(1) + '%' : 'N/A';
                const yearLbl = y.calendar_year || `Y${y.year}`;
                tableRows += `<tr${rowCls}>
                    <td>${yearLbl}</td>
                    <td style="text-align:right;">${y.founder != null ? Number(y.founder).toFixed(1) : '—'}</td>
                    <td style="text-align:right;">${y.simulated_median != null ? Number(y.simulated_median).toFixed(1) : '—'}</td>
                    <td style="text-align:right;">${y.simulated_p25 != null ? Number(y.simulated_p25).toFixed(1) : '—'}</td>
                    <td style="text-align:right;">${y.simulated_p75 != null ? Number(y.simulated_p75).toFixed(1) : '—'}</td>
                    <td style="text-align:right;font-weight:600;">${divPct}</td>
                    <td style="text-align:center;">${y.in_band ? '✓' : '✗'}</td>
                </tr>`;
            });
            const narrative = fComp.revenue?.narrative || '';
            insertInline(finSectionH2, `<div class="memo-chart-context">
                <p class="memo-chart-label">Founder vs Simulation Comparison</p>
                ${narrative ? `<p class="memo-chart-caption">${narrative}</p>` : ''}
                <table class="memo-rvm-table">
                    <thead><tr>
                        <th>Year</th>
                        <th style="text-align:right;">Founder ($M)</th>
                        <th style="text-align:right;">Sim Median ($M)</th>
                        <th style="text-align:right;">Sim P25</th>
                        <th style="text-align:right;">Sim P75</th>
                        <th style="text-align:right;">Divergence</th>
                        <th style="text-align:center;">In Band</th>
                    </tr></thead>
                    <tbody>${tableRows}</tbody>
                </table>
                <p class="memo-chart-note">In Band = founder projection falls within Sim P25–P75 range. ✓ = plausible, ✗ = outside model expectations.</p>
            </div>`, ['divergence', 'founder', 'comparison', 'projection', 'band', 'median', 'year']);
        }

        // ── Section 6: Valuation Context (comps table) ──────────────────────
        const valCtx2 = r.valuation_context || {};
        const vcMatches2 = valCtx2.matches || valCtx2.industries || [];
        if (vcMatches2.length) {
            let vcRows = '';
            vcMatches2.forEach(m => {
                vcRows += `<tr>
                    <td>${m.label || m.industry || ''}</td>
                    <td style="text-align:right;">${fmt(m.ipo_ev_ebitda,1)}x</td>
                    <td style="text-align:right;">${fmt(m.acq_ev_ebitda,1)}x</td>
                </tr>`;
            });
            if (valCtx2.ipo_ev_ebitda_mean) {
                vcRows += `<tr style="font-weight:700;border-top:2px solid var(--border,#ccc);">
                    <td>Weighted Average</td>
                    <td style="text-align:right;">${fmt(valCtx2.ipo_ev_ebitda_mean,1)}x</td>
                    <td style="text-align:right;">${fmt(valCtx2.acq_ev_ebitda_mean,1)}x</td>
                </tr>`;
            }
            appendToSection(finSectionH2, `<div class="memo-chart-context">
                <p class="memo-chart-label">Section 6 — Valuation Context: Public Comparables</p>
                <p class="memo-chart-caption">Simulation exit multiples: ${fmt(ov.exit_multiple_range?.[0],1)}x – ${fmt(ov.exit_multiple_range?.[1],1)}x EV/EBITDA applied to projected exit-year EBITDA. Comps sourced from Damodaran/NYU dataset with 20% acquisition haircut. ${valCtx2.multiples_source || ''}</p>
                <table class="memo-rvm-table">
                    <thead><tr>
                        <th>Industry</th>
                        <th style="text-align:right;">IPO EV/EBITDA</th>
                        <th style="text-align:right;">Acq EV/EBITDA (80% haircut)</th>
                    </tr></thead>
                    <tbody>${vcRows}</tbody>
                </table>
            </div>`);
        }

        // ── Section 6 cont: Enterprise Value at Exit ─────────────────────────
        const evData2 = sim.ev_at_exit || {};
        const ebMargin2 = sim.ebitda_margin || {};
        if (evData2.mean_m) {
            appendToSection(finSectionH2, `<div class="memo-chart-context">
                <p class="memo-chart-label">Enterprise Value at Exit</p>
                <p class="memo-chart-caption">Implied EV at exit across ${(sim.n_simulations||5000).toLocaleString()} successful-exit paths (conditional on positive exit). EV = exit-year EBITDA × EV/EBITDA multiple. EBITDA margin ramps from ${((ebMargin2.margin_start||0)*100).toFixed(0)}% to ${((ebMargin2.margin_end||0.25)*100).toFixed(0)}% over ${ebMargin2.ramp_years||6} years at TRL ${ov.trl}.</p>
                <div class="memo-chart-row" style="align-items:flex-start; gap:20px;">
                    <div style="flex:1;">
                        <p style="font-size:0.82rem;font-weight:600;margin:0 0 6px;color:var(--accent,#3B5249);">EV Distribution (Successful Exits)</p>
                        <table class="memo-rvm-table">
                            <thead><tr><th>Percentile</th><th style="text-align:right;">EV ($M)</th></tr></thead>
                            <tbody>
                                <tr><td>P25</td><td style="text-align:right;">$${fmt(evData2.p25_m,1)}M</td></tr>
                                <tr style="font-weight:700;background:rgba(91,119,68,0.08)"><td>P50 (Median)</td><td style="text-align:right;">$${fmt(evData2.p50_m,1)}M</td></tr>
                                <tr><td>Mean</td><td style="text-align:right;">$${fmt(evData2.mean_m,1)}M</td></tr>
                                <tr><td>P75</td><td style="text-align:right;">$${fmt(evData2.p75_m,1)}M</td></tr>
                                <tr><td>P90</td><td style="text-align:right;">$${fmt(evData2.p90_m,1)}M</td></tr>
                            </tbody>
                        </table>
                    </div>
                    <div style="flex:1;">
                        <p style="font-size:0.82rem;font-weight:600;margin:0 0 6px;color:var(--accent,#3B5249);">EV Build-Up (Mean Path)</p>
                        <table class="memo-rvm-table">
                            <thead><tr><th>Component</th><th style="text-align:right;">Value</th></tr></thead>
                            <tbody>
                                <tr><td>Exit Revenue</td><td style="text-align:right;">$${fmt(evData2.exit_revenue_mean_m,1)}M</td></tr>
                                <tr><td>× EBITDA Margin</td><td style="text-align:right;">${fmt(evData2.exit_margin_mean_pct,1)}%</td></tr>
                                <tr style="border-top:1px solid #ddd;"><td>= Exit EBITDA</td><td style="text-align:right;">$${fmt(evData2.exit_ebitda_mean_m,1)}M</td></tr>
                                <tr><td>× EV/EBITDA Multiple</td><td style="text-align:right;">${fmt(evData2.exit_multiple_mean,1)}x</td></tr>
                                <tr style="font-weight:700;background:rgba(91,119,68,0.08);border-top:2px solid #ccc;"><td>= Enterprise Value</td><td style="text-align:right;">$${fmt(evData2.mean_m,1)}M</td></tr>
                            </tbody>
                        </table>
                    </div>
                    <div class="memo-chart-wrap" style="flex:1.2; min-width:220px;"><canvas id="memo-fin-ev-chart" height="260"></canvas></div>
                </div>
                <p class="memo-chart-note">Exit multiples drawn uniformly from ${fmt(ov.exit_multiple_range?.[0],1)}x–${fmt(ov.exit_multiple_range?.[1],1)}x (TRL-adjusted). Mean realized: ${fmt(evData2.exit_multiple_mean,1)}x. EBITDA margin ramp: TRL ${ov.trl} entry → ${((ebMargin2.margin_end||0.25)*100).toFixed(0)}% terminal.</p>
            </div>`);
        }

        // ── FM Financials Summary Table ──────────────────────────────────────
        const fm2 = r.financial_model || {};
        if (fm2.has_data && fm2.financials && fm2.fiscal_years?.length) {
            const fmYrs2 = fm2.fiscal_years || [];
            const fmFin2 = fm2.financials || {};
            const entryYr2 = r.deal_overview?.entry_year || 0;
            // Filter to years >= entry_year (show forward-looking only); fall back to all years
            const dispYrs = fmYrs2.filter(y => !entryYr2 || parseInt(y) >= entryYr2);
            const colYrs = dispYrs.length ? dispYrs : fmYrs2;
            if (colYrs.length) {
                const exitMult = ov.exit_multiple_range || [];
                const multMid = exitMult.length === 2 ? (exitMult[0] + exitMult[1]) / 2 : null;
                // Normalise key lookup — fm stores as lowercase
                const revSeries = fmFin2.revenue || fmFin2.Revenue || {};
                const ebSeries  = fmFin2.ebitda  || fmFin2.EBITDA  || {};
                const fmHdrCols = colYrs.map(y => `<th style="text-align:right;">${y}</th>`).join('');
                // Detect whether FM values are raw dollars or already in $M.
                // Properly-extracted XLSX data is stored in raw dollars (multiplied by scale factor).
                // Vision-extracted or manually-edited data is stored in $M as-shown in the table.
                // Heuristic: if any value exceeds 1e5, it's raw dollars → divide by 1e6.
                // Otherwise treat as already in $M → no division.
                const _allFmVals = Object.values(revSeries).concat(Object.values(ebSeries)).filter(v => v != null);
                const _fmMaxAbs = _allFmVals.length ? Math.max(..._allFmVals.map(v => Math.abs(v))) : 0;
                const _fmScaleDiv = _fmMaxAbs >= 1e5 ? 1e6 : 1;
                const _fmCell = (series, y) => {
                    const v = series[String(y)] ?? series[y];
                    if (v == null) return `<td style="text-align:right;">—</td>`;
                    const vM = v / _fmScaleDiv;
                    const neg = vM < 0;
                    return `<td style="text-align:right;${neg ? 'color:#dc2626;' : ''}">${neg ? '(' : ''}$${Math.abs(vM).toFixed(1)}M${neg ? ')' : ''}</td>`;
                };
                const fmRow = (label, series) =>
                    `<tr><td style="font-weight:600;">${label}</td>${colYrs.map(y => _fmCell(series, y)).join('')}</tr>`;
                const marginRow = () => {
                    const cells = colYrs.map(y => {
                        const rev = revSeries[String(y)] ?? revSeries[y];
                        const eb  = ebSeries[String(y)]  ?? ebSeries[y];
                        if (rev == null || eb == null || rev === 0) return `<td style="text-align:right;">—</td>`;
                        const pct = (eb / rev * 100).toFixed(1);
                        return `<td style="text-align:right;">${pct}%</td>`;
                    }).join('');
                    return `<tr><td style="font-weight:600;">EBITDA Margin</td>${cells}</tr>`;
                };
                const valRow = () => {
                    const cells = colYrs.map(y => {
                        const eb = ebSeries[String(y)] ?? ebSeries[y];
                        if (eb == null || multMid == null || eb <= 0) return `<td style="text-align:right;">—</td>`;
                        const evM = (eb / _fmScaleDiv) * multMid;
                        return `<td style="text-align:right;font-weight:700;">$${evM.toFixed(0)}M</td>`;
                    }).join('');
                    return `<tr style="background:rgba(91,119,68,0.08);border-top:2px solid #ccc;"><td style="font-weight:600;">Implied EV (${multMid?.toFixed(1)}x)</td>${cells}</tr>`;
                };
                const hasRev = colYrs.some(y => (revSeries[String(y)] ?? revSeries[y]) != null);
                const hasEb  = colYrs.some(y => (ebSeries[String(y)]  ?? ebSeries[y])  != null);
                if (hasRev || hasEb) {
                    const multNote = multMid ? ` Implied EV = EBITDA × ${multMid.toFixed(1)}x (midpoint of ${exitMult[0]}x–${exitMult[1]}x range).` : '';
                    appendToSection(finSectionH2, `<div class="memo-chart-context">
                        <p class="memo-chart-label">Financial Model Summary</p>
                        <p class="memo-chart-caption">Revenue, EBITDA, and implied valuation from uploaded financial model (${fm2.file_name || 'FM'}).${multNote} Values in USD millions.</p>
                        <table class="memo-rvm-table">
                            <thead><tr><th>Metric</th>${fmHdrCols}</tr></thead>
                            <tbody>
                                ${hasRev ? fmRow('Revenue ($M)', revSeries) : ''}
                                ${hasEb  ? fmRow('EBITDA ($M)',  ebSeries)  : ''}
                                ${(hasRev && hasEb) ? marginRow() : ''}
                                ${(hasEb && multMid) ? valRow() : ''}
                            </tbody>
                        </table>
                        <p class="memo-chart-note">Raw extraction — no interpolation or estimation. ${fm2.model_summary?.manually_edited ? '&#9998; Values include manual corrections.' : ''} Empty cells = not present in source file.</p>
                    </div>`);
                }
            }
        }

        // ── Simulation Risk Parameters Documentation Block ────────────────────
        // Shows TRL modifiers, Carta inputs, realized survival_rate, and outcome
        // breakdown so analysts can replicate the model without reading source code.
        const simMeta = r.simulation || {};
        const trlMods = simMeta.trl_modifiers || {};
        const cartaIn = simMeta.carta_inputs || {};
        const outBk = simMeta.outcome_breakdown || {};
        const _sp = trlMods.survival_penalty;
        const _cm = trlMods.capital_multiplier;
        const _bp = trlMods.extra_bridge_prob;
        const _em = trlMods.exit_multiple_discount;
        const _sr = simMeta.survival_rate;
        const _mer = simMeta.meaningful_exit_rate;
        const _ns = simMeta.n_simulations || 5000;
        const _pctFmt1 = v => v != null ? (v * 100).toFixed(1) + '%' : '—';
        // Build outcome rows from breakdown object
        // Breakdown values may be plain fractions OR objects {count, pct, label}
        let outRows = '';
        const outLabels = {
            full_exit: 'Full Exit (acq/IPO)',
            stage_exit: 'Stage Exit (mid-round)',
            partial_recovery: 'Partial Recovery',
            late_small_exit: 'Late Small Exit',
            bridge_survives: 'Bridge Survives',
            total_loss: 'Total Loss / Zero',
        };
        Object.entries(outLabels).forEach(([k, lbl]) => {
            const v = outBk[k];
            if (v == null) return;
            // Handle both flat fraction (0.19) and nested object {count, pct, label}
            let pctNum, countNum;
            if (typeof v === 'object' && v.pct != null) {
                pctNum = v.pct;       // already in percent (e.g. 19.0)
                countNum = v.count;
            } else {
                pctNum = v * 100;
                countNum = Math.round(v * _ns);
            }
            const pctStr = pctNum.toFixed(1) + '%';
            const countStr = countNum != null ? `${countNum.toLocaleString()} / ${_ns.toLocaleString()}` : '—';
            const isLoss = k === 'total_loss';
            const isWin = k === 'full_exit' || k === 'stage_exit';
            const rowStyle = isLoss ? 'color:#dc2626;' : isWin ? 'color:#2d6a4f;font-weight:600;' : '';
            outRows += `<tr><td>${lbl}</td><td style="text-align:right;${rowStyle}">${pctStr}</td><td style="text-align:right;">${countStr}</td></tr>`;
        });
        appendToSection(finSectionH2, `<div class="memo-chart-context" style="background:rgba(91,119,68,0.04);border-left:3px solid #5B7744;">
            <p class="memo-chart-label">Monte Carlo Simulation — Risk Summary</p>
            <div class="memo-rvm-hero" style="margin-bottom:8px;">
                <div class="memo-rvm-hero-card accent">
                    <div class="memo-rvm-hero-num">${_pctFmt1(_sr)}</div>
                    <div class="memo-rvm-hero-lbl">Survival Rate<br><span style="font-size:0.7rem;font-weight:400;">(paths with MOIC&gt;0)</span></div>
                </div>
                <div class="memo-rvm-hero-card">
                    <div class="memo-rvm-hero-num">${_pctFmt1(_mer)}</div>
                    <div class="memo-rvm-hero-lbl">Meaningful Exit<br><span style="font-size:0.7rem;font-weight:400;">(full or stage exit)</span></div>
                </div>
                <div class="memo-rvm-hero-card" style="border-color:#5B7744;">
                    <div class="memo-rvm-hero-num">TRL ${trlMods.trl || ov.trl}</div>
                    <div class="memo-rvm-hero-lbl">Tech Readiness<br><span style="font-size:0.7rem;font-weight:400;">survival penalty: ${_sp != null ? (_sp*100).toFixed(0)+'%' : '—'}</span></div>
                </div>
            </div>
            <p style="font-size:0.83rem;margin:4px 0 0;color:#555;">
                Simulation uses Carta ${cartaIn.sector_profile || ov.sector_profile} graduation rates × TRL ${trlMods.trl || ov.trl} modifiers across ${_ns.toLocaleString()} paths.
                See <strong>Cohort Analysis</strong> section for full TRL modifier table, stage-by-stage funnel, decay formulas, and outcome distribution.
            </p>
        </div>`);
    }

    // ── 5. COHORT ANALYSIS: Survival, Dilution & Risk Calibration ─────────────
    // Standalone section: all Carta-derived factors, TRL modifier table,
    // survival funnel, outcome distribution. NOT part of PRIME methodology.
    const cohortH2 = findSection('cohort analysis');
    if (cohortH2) {
        const simMeta2    = r.simulation || {};
        const trlMods2    = simMeta2.trl_modifiers || {};
        const trlTable    = simMeta2.trl_modifier_table || {};
        const csData      = simMeta2.carta_stage_data || {};
        const outBk2      = simMeta2.outcome_breakdown || {};
        const cartaIn2    = simMeta2.carta_inputs || {};
        const _sr2        = simMeta2.survival_rate;
        const _mer2       = simMeta2.meaningful_exit_rate;
        const _ns2        = simMeta2.n_simulations || 5000;
        const _thisTrl    = trlMods2.trl || ov.trl;
        const _sp2        = trlMods2.survival_penalty;
        const _pctFmt2    = v => v != null ? (v * 100).toFixed(1) + '%' : '—';

        // ─ Hero cards ─────────────────────────────────────────────────────────
        const totalLossRaw = outBk2.total_loss;
        const totalLossPct = totalLossRaw == null ? null
            : typeof totalLossRaw === 'object' ? totalLossRaw.pct / 100
            : totalLossRaw;
        appendToSection(cohortH2, `<div class="memo-chart-context">
            <p class="memo-chart-label">Cohort Risk Summary — Monte Carlo Output (${_ns2.toLocaleString()} paths)</p>
            <p style="font-size:0.85rem;margin:0 0 10px;line-height:1.6;">
                VoLo's probabilistic risk model combines <strong>Carta benchmark graduation rates</strong>
                (sector: <em>${cartaIn2.sector_profile || ov.sector_profile}</em>, entry at
                <em>${cartaIn2.entry_stage || ov.entry_stage}</em>) with
                <strong>TRL ${_thisTrl} modifiers</strong> across ${_ns2.toLocaleString()} Monte Carlo paths.
                This produces the survival rate and outcome distribution below.
                <span style="color:#dc2626;font-weight:600;">These metrics are VoLo's own risk layer —
                they are NOT part of the PRIME ERP carbon methodology.</span>
                They are applied solely to the P-50 Risk-Adjusted tCO₂ and TCPI metrics in the
                Carbon Impact section.
            </p>
            <div class="memo-rvm-hero" style="margin-bottom:14px;">
                <div class="memo-rvm-hero-card accent">
                    <div class="memo-rvm-hero-num">${_pctFmt2(_sr2)}</div>
                    <div class="memo-rvm-hero-lbl">Survival Rate<br><span style="font-size:0.7rem;font-weight:400;">paths with any positive return</span></div>
                </div>
                <div class="memo-rvm-hero-card">
                    <div class="memo-rvm-hero-num">${_pctFmt2(_mer2)}</div>
                    <div class="memo-rvm-hero-lbl">Meaningful Exit Rate<br><span style="font-size:0.7rem;font-weight:400;">full or stage exit</span></div>
                </div>
                <div class="memo-rvm-hero-card" style="border-color:#dc2626;">
                    <div class="memo-rvm-hero-num" style="color:#dc2626;">${_pctFmt2(totalLossPct)}</div>
                    <div class="memo-rvm-hero-lbl">Total Loss Rate<br><span style="font-size:0.7rem;font-weight:400;">zero-return paths</span></div>
                </div>
            </div>
        </div>`);

        // ─ TRL Modifier Table (all 9 levels) ─────────────────────────────────
        const trlLevels = [1,2,3,4,5,6,7,8,9];
        let trlTableRows = '';
        for (const lvl of trlLevels) {
            const m = trlTable[lvl] || trlTable[String(lvl)] || {};
            const isThis = String(lvl) === String(_thisTrl);
            const rowBg = isThis ? 'background:rgba(91,119,68,0.12);font-weight:700;' : '';
            const marker = isThis ? ' ◀ this deal' : '';
            trlTableRows += `<tr style="${rowBg}">
                <td style="text-align:center;">TRL ${lvl}${marker}</td>
                <td style="text-align:center;color:#dc2626;">${m.survival_penalty != null ? (m.survival_penalty*100).toFixed(0)+'%' : '—'}</td>
                <td style="text-align:center;">${m.capital_multiplier != null ? m.capital_multiplier.toFixed(2)+'×' : '—'}</td>
                <td style="text-align:center;color:#b45309;">${m.extra_bridge_prob != null ? (m.extra_bridge_prob*100).toFixed(0)+'%' : '—'}</td>
                <td style="text-align:center;">${m.exit_multiple_discount != null ? m.exit_multiple_discount.toFixed(2)+'×' : '—'}</td>
            </tr>`;
        }
        appendToSection(cohortH2, `<div class="memo-chart-context">
            <p class="memo-chart-label">TRL Modifier Lookup Table — All 9 Technology Readiness Levels</p>
            <p style="font-size:0.85rem;margin:0 0 8px;line-height:1.6;">
                Applied at every stage transition during the Monte Carlo simulation. The decay function
                reduces the modifier's effect for later-stage companies:
                <code style="background:#f0f4ec;padding:2px 5px;border-radius:3px;">decay(s) = max(0, 1 − survival_penalty × 0.3)</code>
                where <em>s</em> = stages elapsed from entry. A TRL 1 company (fully pre-commercial) faces
                the maximum penalty at every stage; a TRL 9 company faces no modifier. TRL advances toward
                commercialization improve all four parameters.
            </p>
            <table class="memo-rvm-table" style="font-size:0.82rem;">
                <thead>
                    <tr>
                        <th style="text-align:center;">TRL Level</th>
                        <th style="text-align:center;">Survival Penalty<br><span style="font-weight:400;font-size:0.75rem;">annual failure risk added</span></th>
                        <th style="text-align:center;">Capital Multiplier<br><span style="font-weight:400;font-size:0.75rem;">round size inflation</span></th>
                        <th style="text-align:center;">Extra Bridge Prob<br><span style="font-weight:400;font-size:0.75rem;">P(bridge | graduate)</span></th>
                        <th style="text-align:center;">Exit Multiple Discount<br><span style="font-weight:400;font-size:0.75rem;">applied to exit EV multiple</span></th>
                    </tr>
                </thead>
                <tbody>${trlTableRows}</tbody>
            </table>
            <p style="font-size:0.79rem;color:#555;margin:6px 0 0;">
                <strong>Decay formula:</strong> grad_rate = carta_base × (1 − survival_penalty × decay(s));
                bridge_prob = extra_bridge_prob × decay(s); capital multiplier blends from max at s=0 to 1.0× as s grows.
                The exit_multiple_discount is applied globally (not per-stage).
            </p>
        </div>`, ['TRL', 'modifier', 'survival', 'penalty', 'capital', 'bridge', 'exit', 'decay', 'technology readiness']);

        // ─ Carta Stage Data table + Survival Funnel chart ─────────────────────
        const stageOrder = ['Pre-Seed','Seed','Series A','Series B','Series C','Series D','Series E+'];
        const entryStage = cartaIn2.entry_stage || ov.entry_stage || 'Seed';
        const entryIdx = stageOrder.indexOf(entryStage);
        const activeStages = stageOrder.slice(entryIdx >= 0 ? entryIdx : 0);

        let cartaStageRows = '';
        let funnelLabels = [];
        let funnelBase = [];
        let funnelEffective = [];
        let cumulBase = 1.0, cumulEff = 1.0;
        funnelLabels.push('Entry\\n(' + entryStage + ')');
        funnelBase.push(100);
        funnelEffective.push(100);

        for (let i = 0; i < activeStages.length - 1; i++) {
            const stg = activeStages[i];
            const nextStg = activeStages[i+1];
            const sd = csData[stg];
            if (!sd) continue;
            const bg = sd.graduation_rate;
            const eg = sd.effective_graduation_rate;
            const df = sd.trl_decay_factor;
            const rs = sd.round_size_p50;
            const pm = sd.pre_money_p50;
            const mo = sd.months_to_grad_median;
            const ss = sd.sample_size;
            const bgStr = bg != null ? (bg*100).toFixed(1)+'%' : '—';
            const egStr = eg != null ? (eg*100).toFixed(1)+'%' : '—';
            const dfStr = df != null ? df.toFixed(3) : '—';
            const rsStr = rs != null ? '$'+rs.toFixed(1)+'M' : '—';
            const pmStr = pm != null ? '$'+pm.toFixed(1)+'M' : '—';
            const moStr = mo != null ? mo.toFixed(0)+' mo' : '—';
            const ssStr = ss != null ? ss.toFixed(0) : '—';
            cumulBase *= (bg || 1);
            cumulEff  *= (eg || 1);
            funnelLabels.push(nextStg);
            funnelBase.push(Math.round(cumulBase * 1000) / 10);
            funnelEffective.push(Math.round(cumulEff * 1000) / 10);
            cartaStageRows += `<tr>
                <td>${stg} → ${nextStg}</td>
                <td style="text-align:center;">${bgStr}</td>
                <td style="text-align:center;color:#dc2626;">${dfStr}</td>
                <td style="text-align:center;color:#5B7744;font-weight:600;">${egStr}</td>
                <td style="text-align:center;">${rsStr}</td>
                <td style="text-align:center;">${pmStr}</td>
                <td style="text-align:center;">${moStr}</td>
                <td style="text-align:center;color:#888;">${ssStr}</td>
            </tr>`;
        }

        const funnelChartId = 'cohort-funnel-' + Date.now();
        const outcomeChartId = 'cohort-outcome-' + Date.now();

        appendToSection(cohortH2, `<div class="memo-chart-context">
            <p class="memo-chart-label">Carta Benchmark Graduation Rates with TRL ${_thisTrl} Modifier Applied</p>
            <p style="font-size:0.85rem;margin:0 0 8px;line-height:1.6;">
                Sector: <strong>${cartaIn2.sector_profile || '—'}</strong>.
                Carta base rates represent median market-wide venture outcomes (Feb 2026 data).
                The TRL ${_thisTrl} decay factor (<code style="background:#f0f4ec;padding:2px 4px;border-radius:3px;">decay = max(0, 1 − ${_sp2 != null ? _sp2.toFixed(2) : '?'} × 0.3) = ${_sp2 != null ? Math.max(0,1-_sp2*0.3).toFixed(3) : '?'}</code>)
                is applied at entry stage and diminishes for later stages.
                Effective = Carta × decay. Cumulative survival compounds across stages.
            </p>
            ${cartaStageRows ? `<table class="memo-rvm-table" style="font-size:0.82rem;">
                <thead>
                    <tr>
                        <th>Stage Transition</th>
                        <th style="text-align:center;">Carta Base<br>Grad Rate</th>
                        <th style="text-align:center;">TRL Decay<br>Factor</th>
                        <th style="text-align:center;">Effective<br>Grad Rate</th>
                        <th style="text-align:center;">Round Size<br>(P50)</th>
                        <th style="text-align:center;">Pre-Money<br>(P50)</th>
                        <th style="text-align:center;">Months to<br>Grad (med)</th>
                        <th style="text-align:center;">Carta n</th>
                    </tr>
                </thead>
                <tbody>${cartaStageRows}</tbody>
            </table>` : '<p style="color:#888;font-size:0.85rem;">Carta stage data not available for this sector/entry stage combination.</p>'}
            <p style="font-size:0.79rem;color:#555;margin:6px 0 0;">
                <strong>Final survival rate:</strong> ${_pctFmt2(_sr2)} (Monte Carlo compound, includes path variance) &nbsp;|&nbsp;
                <strong>Meaningful exit rate:</strong> ${_pctFmt2(_mer2)}.
                Carta data: Carta Insights Fund Forecasting Profiles, Feb 2026.
            </p>
        </div>`);

        // ─ Charts: Survival Funnel + Outcome Distribution side-by-side ────────
        if (funnelLabels.length > 1 || Object.keys(outBk2).length > 0) {
            // Build outcome distribution data
            const outLabels2 = {
                'full_exit': 'Full Exit',
                'stage_exit': 'Stage Exit',
                'bridge_survives': 'Bridge Survives',
                'late_small_exit': 'Late Small Exit',
                'partial_recovery': 'Partial Recovery',
                'total_loss': 'Total Loss',
            };
            const outColors2 = {
                'full_exit': '#2d6a4f',
                'stage_exit': '#52b788',
                'bridge_survives': '#f59e0b',
                'late_small_exit': '#f97316',
                'partial_recovery': '#fb923c',
                'total_loss': '#dc2626',
            };
            const outVals2 = [], outLbls2 = [], outClrs2 = [];
            Object.entries(outLabels2).forEach(([k,lbl]) => {
                const v = outBk2[k];
                if (v == null) return;
                const pct = typeof v === 'object' ? v.pct : v * 100;
                outVals2.push(pct);
                outLbls2.push(lbl);
                outClrs2.push(outColors2[k] || '#888');
            });

            appendToSection(cohortH2, `<div class="memo-chart-context">
                <p class="memo-chart-label">Survival Funnel &amp; Outcome Distribution</p>
                <div class="memo-chart-row" style="gap:24px;align-items:flex-start;">
                    <div style="flex:1;min-width:0;">
                        <p style="font-size:0.82rem;font-weight:600;margin:0 0 6px;color:var(--accent,#3B5249);">Stage Survival Funnel — % of Cohort Reaching Each Stage</p>
                        <div style="position:relative;height:260px;">
                            <canvas id="${funnelChartId}"></canvas>
                        </div>
                        <p style="font-size:0.78rem;color:#555;margin:4px 0 0;">
                            Green = Carta baseline; Red = after TRL ${_thisTrl} modifier. Both start at 100% at entry.
                        </p>
                    </div>
                    <div style="flex:1;min-width:0;">
                        <p style="font-size:0.82rem;font-weight:600;margin:0 0 6px;color:var(--accent,#3B5249);">Outcome Distribution — ${_ns2.toLocaleString()} Simulation Paths</p>
                        <div style="position:relative;height:260px;">
                            <canvas id="${outcomeChartId}"></canvas>
                        </div>
                        <p style="font-size:0.78rem;color:#555;margin:4px 0 0;">
                            Top = best outcomes; bottom = worst. Survival rate = all non-zero-return paths.
                        </p>
                    </div>
                </div>
            </div>`);

            // Queue charts for rendering
            _pendingCharts = _pendingCharts || [];
            _pendingCharts.push({
                id: funnelChartId,
                type: 'line',
                data: {
                    labels: funnelLabels,
                    datasets: [
                        {
                            label: 'Carta Baseline',
                            data: funnelBase,
                            borderColor: '#52b788',
                            backgroundColor: 'rgba(82,183,136,0.1)',
                            fill: true,
                            tension: 0.3,
                            pointRadius: 4,
                        },
                        {
                            label: 'TRL ' + _thisTrl + ' Adjusted',
                            data: funnelEffective,
                            borderColor: '#dc2626',
                            backgroundColor: 'rgba(220,38,38,0.08)',
                            fill: true,
                            tension: 0.3,
                            pointRadius: 4,
                        }
                    ]
                },
                options: {
                    responsive: true, maintainAspectRatio: false,
                    plugins: { legend: { position: 'bottom', labels: { font: { size: 11 } } }, tooltip: { callbacks: { label: ctx => ctx.dataset.label + ': ' + ctx.parsed.y.toFixed(1) + '%' } } },
                    scales: { y: { min: 0, max: 100, ticks: { callback: v => v + '%', font: { size: 10 } }, title: { display: true, text: '% of cohort surviving', font: { size: 10 } } }, x: { ticks: { font: { size: 10 } } } }
                }
            });
            _pendingCharts.push({
                id: outcomeChartId,
                type: 'bar',
                data: {
                    labels: outLbls2,
                    datasets: [{
                        label: '% of paths',
                        data: outVals2,
                        backgroundColor: outClrs2,
                        borderRadius: 3,
                    }]
                },
                options: {
                    indexAxis: 'y',
                    responsive: true, maintainAspectRatio: false,
                    plugins: { legend: { display: false }, tooltip: { callbacks: { label: ctx => ctx.parsed.x.toFixed(1) + '%' } } },
                    scales: { x: { min: 0, ticks: { callback: v => v + '%', font: { size: 10 } } }, y: { ticks: { font: { size: 11 } } } }
                }
            });
        }
    }

    // ── 6. FUND RETURN MODEL: Section 3 (Portfolio Impact) + Section 8 (Check Size) ──
    const frmH2 = findSection('fund return model');
    if (frmH2) {
        const pImpact2 = r.portfolio_impact || {};
        const ps2 = r.position_sizing || {};
        const foPs2 = r.followon_position_sizing || {};

        // ─ Block A: Section 3 — Portfolio Impact ─────────────────────────────
        if (pImpact2.has_data) {
            const liftCls2 = (v) => v > 0 ? 'color:#2d6a4f;font-weight:700;' : v < 0 ? 'color:#dc2626;font-weight:700;' : '';
            const signFmt2 = (v) => v != null ? (v >= 0 ? '+' : '') + Number(v).toFixed(2) : 'N/A';
            const nComm = pImpact2.n_committed_deals || 0;
            const portLabel = nComm > 0
                ? `Running Fund (${nComm} committed deal${nComm > 1 ? 's' : ''} + ${20 - nComm} simulated)`
                : 'Simulated VoLo Portfolio';

            insertInline(frmH2, `<div class="memo-chart-context">
                <p class="memo-chart-label">Section 3 — Portfolio Impact: ${portLabel}</p>
                ${nComm > 0 ? `<p class="memo-chart-caption" style="background:#f0f4ec;border:1px solid #5B7744;border-radius:4px;padding:6px 10px;">Running portfolio mode: baseline includes ${nComm} committed deal${nComm > 1 ? 's' : ''} replacing simulated company slots.</p>` : ''}
                <p class="memo-chart-caption">${pImpact2.narrative || 'VCSimulator 2,000 portfolio paths — base portfolio vs portfolio including this deal.'}</p>
                <div class="memo-rvm-hero">
                    <div class="memo-rvm-hero-card">
                        <div class="memo-rvm-hero-num">${fmt(pImpact2.tvpi_base_mean)}x</div>
                        <div class="memo-rvm-hero-lbl">Base Fund TVPI</div>
                    </div>
                    <div class="memo-rvm-hero-card accent">
                        <div class="memo-rvm-hero-num">${fmt(pImpact2.tvpi_new_mean)}x</div>
                        <div class="memo-rvm-hero-lbl">With Deal TVPI</div>
                    </div>
                    <div class="memo-rvm-hero-card">
                        <div class="memo-rvm-hero-num" style="${liftCls2(pImpact2.tvpi_mean_lift)}">${signFmt2(pImpact2.tvpi_mean_lift)}x</div>
                        <div class="memo-rvm-hero-lbl">TVPI Lift (Marginal)</div>
                    </div>
                </div>
                <table class="memo-rvm-table">
                    <thead><tr><th>Metric</th><th style="text-align:right;">Base Portfolio</th><th style="text-align:right;">With This Deal</th><th style="text-align:right;">Delta</th></tr></thead>
                    <tbody>
                        <tr><td>TVPI (Mean)</td><td style="text-align:right;">${fmt(pImpact2.tvpi_base_mean)}x</td><td style="text-align:right;">${fmt(pImpact2.tvpi_new_mean)}x</td><td style="text-align:right;${liftCls2(pImpact2.tvpi_mean_lift)}">${signFmt2(pImpact2.tvpi_mean_lift)}x</td></tr>
                        <tr><td>TVPI (P50)</td><td style="text-align:right;">${fmt(pImpact2.tvpi_base_p50)}x</td><td style="text-align:right;">${fmt(pImpact2.tvpi_new_p50)}x</td><td style="text-align:right;${liftCls2(pImpact2.tvpi_new_p50 - pImpact2.tvpi_base_p50)}">${signFmt2(pImpact2.tvpi_new_p50 - pImpact2.tvpi_base_p50)}x</td></tr>
                        <tr><td>TVPI (P75)</td><td style="text-align:right;">${fmt(pImpact2.tvpi_base_p75)}x</td><td style="text-align:right;">${fmt(pImpact2.tvpi_new_p75)}x</td><td style="text-align:right;${liftCls2(pImpact2.tvpi_p75_lift)}">${signFmt2(pImpact2.tvpi_p75_lift)}x</td></tr>
                        <tr><td>IRR (Mean)</td><td style="text-align:right;">${pctFmt(pImpact2.irr_base_mean)}</td><td style="text-align:right;">${pctFmt(pImpact2.irr_new_mean)}</td><td style="text-align:right;${liftCls2(pImpact2.irr_mean_lift)}">${pImpact2.irr_mean_lift != null ? ((pImpact2.irr_mean_lift*100).toFixed(1) + 'pp') : 'N/A'}</td></tr>
                        <tr><td>IRR (P50)</td><td style="text-align:right;">${pctFmt(pImpact2.irr_base_p50)}</td><td style="text-align:right;">${pctFmt(pImpact2.irr_new_p50)}</td><td style="text-align:right;${liftCls2(pImpact2.irr_new_p50 - pImpact2.irr_base_p50)}">${pImpact2.irr_new_p50 != null ? (((pImpact2.irr_new_p50 - pImpact2.irr_base_p50)*100).toFixed(1) + 'pp') : 'N/A'}</td></tr>
                    </tbody>
                </table>
                <p class="memo-chart-note">Deal parameters used in simulation: Conditional MOIC=${fmt(r.simulation?.moic_conditional?.mean)}x, Survival=${pctFmt(r.simulation?.survival_rate)}, Exit ${ov.exit_year_range?.[0] || 5}–${ov.exit_year_range?.[1] || 10} years, Check=$${fmt(ov.check_size_millions,1)}M.</p>
            </div>`, ['portfolio', 'fund', 'tvpi', 'irr', 'impact', 'return', 'base', 'marginal']);
        }

        // ─ Block B: Section 8 — Check Size Optimization ──────────────────────
        if (ps2.has_data && ps2.grid_search?.grid?.length) {
            const gsOptimal2 = ps2.grid_search.optimal || {};
            const constraints2 = ps2.fund_constraints || {};
            const kelly2 = ps2.kelly_reference || {};
            const pctChgFmt2 = (v) => v != null ? (v >= 0 ? '+' : '') + (v * 100).toFixed(2) + '%' : '—';
            const grid2 = ps2.grid_search.grid;
            const step2 = Math.max(1, Math.floor(grid2.length / 10));
            let gridRows = '';
            grid2.forEach((g, i) => {
                const isOpt = g.check_m === gsOptimal2.check_m;
                const isCur = Math.abs(g.check_m - (ov.check_size_millions || 0)) < 0.13;
                if (i % step2 === 0 || isOpt || isCur) {
                    const rowStyle = isOpt ? 'background:rgba(91,119,68,0.12);font-weight:700;' : isCur ? 'background:#fff3cd;' : '';
                    const tag = isOpt ? ' ★' : isCur ? ' (entered)' : '';
                    gridRows += `<tr style="${rowStyle}">
                        <td>$${Number(g.check_m).toFixed(2)}M${tag}</td>
                        <td style="text-align:right;">${Number(g.ownership_pct).toFixed(1)}%</td>
                        <td style="text-align:right;">${pctChgFmt2(g.fund_p10_pct_chg)}</td>
                        <td style="text-align:right;">${pctChgFmt2(g.fund_p50_pct_chg)}</td>
                        <td style="text-align:right;">${pctChgFmt2(g.fund_p90_pct_chg)}</td>
                        <td style="text-align:right;">${g.composite_score != null ? Number(g.composite_score).toFixed(3) : '—'}</td>
                    </tr>`;
                }
            });

            appendToSection(frmH2, `<div class="memo-chart-context">
                <p class="memo-chart-label">Section 8 — Check Size Optimization</p>
                <p class="memo-chart-caption">$250K-increment sweep from $${fmt(constraints2.min_check_m,2)}M to $${fmt(constraints2.max_check_m,2)}M (${fmt(constraints2.max_concentration_pct,0)}% concentration limit on $${fmt(constraints2.fund_size_m,0)}M fund). VCSimulator measures % change in fund TVPI P10/P50/P90 at each level. Composite score = weighted sum of normalized Δ%.</p>
                <div class="memo-rvm-hero">
                    <div class="memo-rvm-hero-card accent">
                        <div class="memo-rvm-hero-num">$${fmt(gsOptimal2.check_m,2)}M</div>
                        <div class="memo-rvm-hero-lbl">Fund-Optimized Check ★</div>
                    </div>
                    <div class="memo-rvm-hero-card">
                        <div class="memo-rvm-hero-num">${fmt(gsOptimal2.ownership_pct,1)}%</div>
                        <div class="memo-rvm-hero-lbl">Implied Ownership</div>
                    </div>
                    <div class="memo-rvm-hero-card">
                        <div class="memo-rvm-hero-num">${pctChgFmt2(gsOptimal2.fund_p50_pct_chg)}</div>
                        <div class="memo-rvm-hero-lbl">Fund P50 Impact</div>
                    </div>
                    <div class="memo-rvm-hero-card">
                        <div class="memo-rvm-hero-num">${fmt(gsOptimal2.fund_pct,1)}%</div>
                        <div class="memo-rvm-hero-lbl">% of Fund</div>
                    </div>
                </div>
                <div class="memo-chart-row">
                    <div class="memo-chart-wrap"><canvas id="memo-frm-sizing-chart" height="280"></canvas></div>
                    <div class="memo-chart-wrap"><canvas id="memo-frm-sizing-score-chart" height="280"></canvas></div>
                </div>
                <table class="memo-rvm-table" style="margin-top:12px;">
                    <thead><tr>
                        <th>Check Size</th>
                        <th style="text-align:right;">Ownership</th>
                        <th style="text-align:right;">Fund Δ%P10</th>
                        <th style="text-align:right;">Fund Δ%P50</th>
                        <th style="text-align:right;">Fund Δ%P90</th>
                        <th style="text-align:right;">Score</th>
                    </tr></thead>
                    <tbody>${gridRows}</tbody>
                </table>
                <table class="memo-rvm-table" style="margin-top:10px;">
                    <thead><tr><th>Sizing Method</th><th style="text-align:right;">Check Size</th><th>Notes</th></tr></thead>
                    <tbody>
                        <tr style="background:rgba(91,119,68,0.10);font-weight:700;"><td>Fund-Performance Optimizer ★</td><td style="text-align:right;">$${fmt(gsOptimal2.check_m,2)}M</td><td>Max composite Δ% fund TVPI P10/P50/P90</td></tr>
                        <tr><td>Full Kelly Criterion</td><td style="text-align:right;">$${fmt(kelly2.optimal_check_m,2)}M</td><td>Theoretical log-wealth maximizer (high volatility)</td></tr>
                        <tr><td>Half Kelly</td><td style="text-align:right;">$${fmt(kelly2.half_kelly_check_m,2)}M</td><td>~75% of Kelly growth, lower drawdown</td></tr>
                        <tr><td>Fund Average</td><td style="text-align:right;">$${fmt(constraints2.avg_check_m,2)}M</td><td>Investable capital ÷ ${constraints2.n_deals || 25} deals</td></tr>
                        <tr><td>Entered (this deal)</td><td style="text-align:right;">$${fmt(ov.check_size_millions,2)}M</td><td>As specified in deal terms</td></tr>
                    </tbody>
                </table>
            </div>`);
        }

        // ─ Block C: Follow-on Check Size Optimization (if applicable) ─────────
        if (foPs2.has_data && foPs2.blended_curve?.length) {
            const foInv2 = foPs2.followon_investment || {};
            const comb2 = foPs2.combined || {};
            const blStats2 = comb2.blended_stats || {};
            // Support new multi-prior format and old single first_investment
            const priorArr2 = foPs2.prior_investments || (foPs2.first_investment ? [Object.assign({}, foPs2.first_investment, {stage: foPs2.first_investment.entry_stage})] : []);
            const totalPriorCheck2 = priorArr2.reduce((s,p) => s + (p.check_m||0), 0);
            const combinedOwn2 = comb2.total_ownership_pct_approx ?? comb2.combined_ownership_pct ?? 0;
            const priorLabel2 = priorArr2.length > 1 ? `${priorArr2.length} prior rounds ($${fmt(totalPriorCheck2,2)}M)` : (priorArr2[0] ? `$${fmt(priorArr2[0].check_m,2)}M at ${priorArr2[0].stage||priorArr2[0].entry_stage||'entry'}` : 'Prior');
            const priorCheckCell2 = priorArr2.length > 1 ? `$${fmt(totalPriorCheck2,2)}M` : `$${fmt(priorArr2[0]?.check_m,2)}M`;
            const priorOwnCell2  = priorArr2.length > 1 ? priorArr2.map(p=>`${fmt(p.ownership_pct,1)}%`).join(' + ') : `${fmt(priorArr2[0]?.ownership_pct,1)}%`;
            const priorRowsHtml2 = priorArr2.length > 1 ? priorArr2.map(p =>
                `<tr><td style="padding-left:12px;color:var(--color-muted);font-size:11px;">Round ${p.round_num||''}: ${p.type==='convertible'?'Conv/SAFE':'Priced'} ${p.stage||''}</td><td style="text-align:right;color:var(--color-muted);font-size:11px;">$${fmt(p.check_m,2)}M</td><td></td><td></td></tr>`
            ).join('') : '';
            // Only show sweep charts when there are multiple data points (sweep actually ran)
            const foSweepMultipoint2 = foPs2.blended_curve.length > 1;
            const foGridMultipoint2  = (foPs2.standalone_grid_search?.grid?.length > 1) &&
                foPs2.standalone_grid_search.grid.some(g => g.fund_p50_pct_chg != null);
            appendToSection(frmH2, `<div class="memo-chart-context">
                <p class="memo-chart-label">Follow-on Check Size Optimization</p>
                <p class="memo-chart-caption">
                    Prior investment (${priorLabel2}) is treated as sunk cost — its return is fixed regardless of follow-on decision.
                    The optimizer finds the follow-on check size that maximises <strong>blended MOIC</strong>:
                    the combined return on <em>all</em> capital VoLo has deployed to this company (prior rounds + follow-on),
                    measured as total exit proceeds divided by total invested ($${fmt(comb2.total_invested_m,2)}M combined).
                    This differs from the standalone follow-on MOIC, which measures only the new check in isolation.
                    ${foSweepMultipoint2 ? 'Blended MOIC shown at P10 (downside), P50 (median), and P90 (upside) across 5,000 simulation paths.' : `Sweep collapsed to a single point ($${fmt(foPs2.recommended_followon_check_m,2)}M) — concentration limit equals minimum check size.`}
                </p>
                <table class="memo-rvm-table">
                    <thead><tr><th></th><th style="text-align:right;">Prior Round(s)</th><th style="text-align:right;">Follow-on</th><th style="text-align:right;">Combined</th></tr></thead>
                    <tbody>
                        <tr><td>Check Size</td><td style="text-align:right;">${priorCheckCell2}</td><td style="text-align:right;font-weight:700;">$${fmt(foPs2.recommended_followon_check_m,2)}M</td><td style="text-align:right;">$${fmt(comb2.total_invested_m,2)}M</td></tr>
                        ${priorRowsHtml2}
                        <tr><td>Ownership</td><td style="text-align:right;">${priorOwnCell2}</td><td style="text-align:right;">${fmt(foInv2.ownership_pct,1)}%</td><td style="text-align:right;font-weight:700;">${fmt(combinedOwn2,1)}%</td></tr>
                    </tbody>
                </table>
                ${blStats2.blended_moic_p50 != null ? `
                <table class="memo-rvm-table" style="margin-top:8px;">
                    <thead><tr>
                        <th>Blended MOIC Percentile</th>
                        <th style="text-align:right;">P10 (Downside)</th>
                        <th style="text-align:right;">P50 (Median)</th>
                        <th style="text-align:right;">P90 (Upside)</th>
                    </tr></thead>
                    <tbody>
                        <tr>
                            <td style="font-size:11px;color:var(--text-secondary);">Return on $${fmt(comb2.total_invested_m,2)}M total deployed (prior + follow-on)</td>
                            <td style="text-align:right;color:#dc2626;">${fmt(blStats2.blended_moic_p10,2)}x</td>
                            <td style="text-align:right;font-weight:700;">${fmt(blStats2.blended_moic_p50,2)}x</td>
                            <td style="text-align:right;color:#2d6a4f;">${fmt(blStats2.blended_moic_p90,2)}x</td>
                        </tr>
                        ${blStats2.followon_standalone_moic_p50 != null ? `<tr>
                            <td style="font-size:11px;color:var(--text-secondary);">Standalone follow-on MOIC (P50) — new $${fmt(foPs2.recommended_followon_check_m,2)}M only</td>
                            <td style="text-align:right;">—</td>
                            <td style="text-align:right;">${fmt(blStats2.followon_standalone_moic_p50,2)}x</td>
                            <td style="text-align:right;">—</td>
                        </tr>` : ''}
                    </tbody>
                </table>` : ''}
                ${foSweepMultipoint2 ? `<div class="memo-chart-row">
                    <div class="memo-chart-wrap"><canvas id="memo-frm-fo-blended-chart" height="260"></canvas></div>
                    ${foGridMultipoint2 ? `<div class="memo-chart-wrap"><canvas id="memo-frm-fo-grid-chart" height="260"></canvas></div>` : ''}
                </div>` : ''}
            </div>`);
        }
    }

    // ── 6. CARBON IMPACT: Full Section 4 from deal pipeline ──
    const carbonH2 = findSection('carbon impact');
    if (carbonH2) {
        const co = carbon.outputs || {};
        const ci = carbon.intermediates || {};
        const cInp = carbon.carbon_inputs || {};
        const riskDiv = carbon.risk_divisor_used || 1;
        const riskSrc = carbon.risk_divisor_source || '';
        const carbonSurvRate = carbon.survival_rate_used;
        const carbonSurvSrc = carbon.survival_rate_source || '';
        const entryOwn = ov.entry_ownership_pct || 0;
        const checkM = ov.check_size_millions || 0;

        // ─ Block A: Carbon Hero Cards + Methodology Overview ─────────────────
        const _horizon = cInp.n_ll_years || 10;
        insertInline(carbonH2, `<div class="memo-chart-context memo-carbon-hero-block">
            <p class="memo-chart-label">RVM Carbon Impact Summary — Section 4 &nbsp;·&nbsp; <em>PRIME ERP Methodology</em></p>
            <p style="font-size:0.85rem;line-height:1.6;margin:0 0 8px;">
                VoLo's carbon impact model follows the <strong>PRIME Coalition Emissions Reduction Potential (ERP) methodology</strong>
                (Burger, Systrom &amp; Kearney, PRIME Coalition / NYSERDA, December 2017).
                PRIME defines ERP as the cumulative avoided CO₂ attributable to a venture's deployed technology, measured over a multi-year analysis horizon.
                The five-step PRIME framework maps directly onto VoLo's calculation chain:
            </p>
            <table style="font-size:0.8rem;width:100%;border-collapse:collapse;margin:0 0 10px;">
                <thead><tr style="background:rgba(91,119,68,0.12);">
                    <th style="padding:5px 8px;text-align:left;border-bottom:1px solid #ccc;">PRIME Step</th>
                    <th style="padding:5px 8px;text-align:left;border-bottom:1px solid #ccc;">What PRIME Requires</th>
                    <th style="padding:5px 8px;text-align:left;border-bottom:1px solid #ccc;">VoLo Implementation</th>
                    <th style="padding:5px 8px;text-align:left;border-bottom:1px solid #ccc;">Alignment</th>
                </tr></thead>
                <tbody>
                    <tr style="border-bottom:1px solid #eee;">
                        <td style="padding:5px 8px;"><strong>§2 — Displaced Product Emissions</strong></td>
                        <td style="padding:5px 8px;">LCA of conventional product per unit of production; CI time series (declining grid)</td>
                        <td style="padding:5px 8px;">JA = baseline_lifetime_prod; JC = CI improvement factor; JD = (1−1/JC)×JA; CI series from NREL ATB / EPA eGRID / IEA, declining over time</td>
                        <td style="padding:5px 8px;color:#16a34a;">✓ Full alignment</td>
                    </tr>
                    <tr style="border-bottom:1px solid #eee;">
                        <td style="padding:5px 8px;"><strong>§3 — Additionality</strong></td>
                        <td style="padding:5px 8px;">Isolate emissions reductions attributable to the venture vs. market baseline (4 additionality types)</td>
                        <td style="padding:5px 8px;">The CI improvement factor (JC) captures relative advantage over the conventional incumbent. Explicit market-baseline additionality decomposition (PRIME's 4 types) is not separately modeled — this is a conservative simplification.</td>
                        <td style="padding:5px 8px;color:#d97706;">⚠ Partial — JC captures relative improvement; PRIME's 4-type additionality matrix not implemented</td>
                    </tr>
                    <tr style="border-bottom:1px solid #eee;">
                        <td style="padding:5px 8px;"><strong>§3 — Venture's Own Emissions</strong></td>
                        <td style="padding:5px 8px;">Subtract manufacturing / operational CO₂ from venture's own product (upstream LCA)</td>
                        <td style="padding:5px 8px;">Embodied carbon model: KE × CI_embodied_yr × vol_yr, using an upstream embodied CI improvement factor. Attributed at deployment year.</td>
                        <td style="padding:5px 8px;color:#16a34a;">✓ Full alignment</td>
                    </tr>
                    <tr style="border-bottom:1px solid #eee;">
                        <td style="padding:5px 8px;"><strong>§4 — Deployment Forecast</strong></td>
                        <td style="padding:5px 8px;">S-curve penetration × TAM × third-party market forecast: units_y = M/(1+e^(−k(y−x))) × TAM_y</td>
                        <td style="padding:5px 8px;">Founder-supplied year_volumes[y] — mathematically equivalent when founders derive volumes from TAM × SAM × SOM × S-curve. Validated against Bass diffusion S-curve; TAM/SAM stored explicitly. (See equivalence note below.)</td>
                        <td style="padding:5px 8px;color:#16a34a;">✓ Equivalent approach — see founder projection equivalence note</td>
                    </tr>
                    <tr style="border-bottom:1px solid #eee;">
                        <td style="padding:5px 8px;"><strong>§5 — ERP Summation</strong></td>
                        <td style="padding:5px 8px;">ERP = Σ_y (displaced_CI_y − venture_CI_y) × deployment_y; multi-year service life per cohort; 30-year horizon recommended. <em>PRIME does not apply a survival/probability discount — ERP is a deterministic potential estimate.</em></td>
                        <td style="padding:5px 8px;">Multi-cohort accumulation: for each calendar year t, sum JD_annual × vol[d] × CI[t] across all in-service cohorts d. Horizon = ${_horizon} years (configurable; PRIME recommends 30). Result = Total Lifecycle tCO₂ and VoLo Pro-Rata tCO₂.</td>
                        <td style="padding:5px 8px;color:#16a34a;">✓ Full alignment — true multi-cohort, user-configurable horizon</td>
                    </tr>
                    <tr style="border-bottom:1px solid #eee;background:rgba(220,38,38,0.04);">
                        <td style="padding:5px 8px;"><strong>VoLo Risk Layer</strong> <span style="font-weight:400;font-size:0.75rem;color:#dc2626;">(not part of PRIME)</span></td>
                        <td style="padding:5px 8px;color:#666;">PRIME has no equivalent — ERP is a deterministic forecast</td>
                        <td style="padding:5px 8px;">P-50 Risk-Adjusted tCO₂ = Fund Pro-Rata Share × survival_rate (from Monte Carlo simulation using likelihood-of-success curves). TCPI (Tonnes CO₂ to Paid-In Capital) = P-50 Risk-Adjusted tCO₂ ÷ check size. The survival rate is drawn from the same financial model (Carta graduation rates × TRL modifiers × 5,000 paths) — ensuring carbon and financial risk are governed by the same probability.</td>
                        <td style="padding:5px 8px;color:#dc2626;">✗ VoLo extension only — applies only to P-50 Risk-Adjusted tCO₂ and TCPI</td>
                    </tr>
                </tbody>
            </table>
            <div class="memo-rvm-hero">
                <div class="memo-rvm-hero-card accent">
                    <div class="memo-rvm-hero-num">${fmt(co.company_tonnes, 0)}</div>
                    <div class="memo-rvm-hero-lbl">Total Potential (10yr)</div>
                    <div style="font-size:0.68rem;color:rgba(255,255,255,0.75);margin-top:3px;">PRIME ERP (§5) · GIIN IRIS+</div>
                </div>
                <div class="memo-rvm-hero-card">
                    <div class="memo-rvm-hero-num">${fmt(co.volo_prorata, 0)}</div>
                    <div class="memo-rvm-hero-lbl">Fund Pro-Rata Share</div>
                    <div style="font-size:0.68rem;color:var(--text-tertiary,#888);margin-top:3px;">GHG Protocol · ownership %</div>
                </div>
                <div class="memo-rvm-hero-card">
                    <div class="memo-rvm-hero-num">${fmt(co.volo_risk_adj, 0)}</div>
                    <div class="memo-rvm-hero-lbl">P-50 Risk-Adjusted</div>
                    <div style="font-size:0.68rem;color:var(--text-tertiary,#888);margin-top:3px;">Likelihood-of-success · ${carbonSurvRate != null ? (carbonSurvRate*100).toFixed(1)+'%' : ''} survival</div>
                </div>
                <div class="memo-rvm-hero-card accent">
                    <div class="memo-rvm-hero-num">${co.risk_adj_tpd != null ? Number(co.risk_adj_tpd).toFixed(4) : 'N/A'}</div>
                    <div class="memo-rvm-hero-lbl">TCPI (t CO₂/$)</div>
                    <div style="font-size:0.68rem;color:rgba(255,255,255,0.75);margin-top:3px;">Tonnes CO₂ to Paid-In Capital</div>
                </div>
            </div>
        </div>`, ['theory of change', 'carbon', 'emissions', 'displace', 'avoided', 'tco2', 'lifecycle', 'impact']);

        // ─ Block A.5: PRIME Deployment Equivalence + Multi-Cohort Method ────────
        const _tam10 = cInp.tam_10y != null ? Number(cInp.tam_10y).toLocaleString(undefined,{maximumFractionDigits:0}) : null;
        const _sam10 = cInp.sam_10y != null ? Number(cInp.sam_10y).toLocaleString(undefined,{maximumFractionDigits:0}) : null;
        const _samPct = cInp.sam_pct_of_tam != null ? (Number(cInp.sam_pct_of_tam)*100).toFixed(1) : null;
        const _scM = cInp.s_curve_M != null ? (Number(cInp.s_curve_M)*100).toFixed(1) : null;
        const _scK = cInp.s_curve_K != null ? Number(cInp.s_curve_K).toFixed(3) : null;
        const _scX = cInp.s_curve_X != null ? Number(cInp.s_curve_X).toFixed(1) : null;
        const _svcL = cInp.unit_service_life_yrs || 'N/A';
        insertInline(carbonH2, `<div class="memo-chart-context">
            <p class="memo-chart-label">PRIME §4 — Deployment Forecast: Founder Volume Equivalence</p>
            <p style="font-size:0.85rem;line-height:1.6;margin:0 0 8px;">
                PRIME requires estimating annual deployments using an S-curve applied to a TAM × third-party market forecast:
                <code style="background:#f3f4f6;padding:1px 5px;border-radius:3px;">units_y = TAM_y × [M / (1 + e^(−k(y−x)))]</code>.
                VoLo uses <strong>founder-supplied year_volumes[y]</strong> directly — which is <em>mathematically equivalent</em> when founders build their volume model from TAM → SAM → SOM → S-curve:
            </p>
            <div style="background:#f0f4ec;border-left:3px solid #5B7744;padding:8px 12px;margin:0 0 10px;border-radius:0 4px 4px 0;font-size:0.83rem;font-family:monospace;line-height:1.7;">
                PRIME:  units_y = TAM_y × M / (1 + e^(−k(y−x)))<br>
                VoLo:   units_y = year_volumes[y]  (founder-supplied)<br>
                <br>
                Equivalence when:  year_volumes[y] = SAM × SOM_pct × S_curve_share(y)<br>
                &nbsp;&nbsp;where S_curve_share(y) = Bass_cumulative(p, q, y) or Logistic(M, k, x, y)<br>
                &nbsp;&nbsp;and   SAM = TAM × sam_pct_of_tam
            </div>
            <p style="font-size:0.84rem;line-height:1.6;margin:0 0 8px;">
                Both formulations produce the same deployment vector when founders ground their projections in market sizing. VoLo stores TAM and SAM explicitly and validates founder volumes against the simulation's Bass diffusion S-curve (parameters p, q calibrated from NREL ATB data) — founder projections within the P25–P75 range are treated as internally consistent.
                ${_tam10 ? `For this deal: TAM (10yr) = ${_tam10} units; SAM = ${_sam10 || '—'} units${_samPct ? ` (${_samPct}% of TAM)` : ''}.` : ''}
                ${_scM ? `S-curve max penetration M = ${_scM}%, speed k = ${_scK}, inflection yr x = ${_scX}.` : ''}
            </p>
            <p class="memo-chart-label" style="margin-top:10px;">PRIME §2 &amp; §5 — Multi-Cohort Service Life Accounting</p>
            <p style="font-size:0.85rem;line-height:1.6;margin:0 0 8px;">
                PRIME explicitly requires that "for displaced products that have multi-year lifetimes, it is important to credit the venture for the emissions avoided over the lifetime of that product" (PRIME 2017, p.17–18).
                VoLo implements <strong>true multi-cohort accounting</strong>: each deployment cohort (units deployed in year <em>d</em>) operates from year <em>d</em> through year <em>d + service_life − 1</em>, and each operating year uses the CI of <em>that calendar year</em> — capturing the benefit of grid decarbonization for units that are still in service when the grid is cleaner.
            </p>
            <div style="background:#f0f4ec;border-left:3px solid #5B7744;padding:8px 12px;margin:0 0 10px;border-radius:0 4px 4px 0;font-size:0.83rem;font-family:monospace;line-height:1.7;">
                JD_annual = JD / service_life  (per-unit annual displacement)<br>
                impact(t) = Σ_{d: d≤t, t−d &lt; service_life}  JD_annual × vol[d] × CI[t]<br>
                <br>
                Where:  d = deployment year (cohort index, 0 = first commercial year)<br>
                &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; t = calendar year (0-indexed from commercial launch)<br>
                &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; CI[t] = carbon intensity of displaced resource in year t<br>
                &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; service_life = ${_svcL} yrs  &nbsp; horizon = ${_horizon} yrs
            </div>
            <p style="font-size:0.84rem;line-height:1.6;margin:0;">
                This differs from a naïve single-cohort calculation (JD × vol[y] × CI[y]) which assigns all lifetime impact to the deployment year and uses only the deployment-year CI. Multi-cohort correctly applies later-year (lower) CI values to units still in service, producing a more conservative and methodologically correct result. Analysis horizon is configurable (default ${_horizon} years; PRIME recommends 30 years).
            </p>
        </div>`, ['PRIME', 'TAM', 'SAM', 'SOM', 'S-curve', 'multi-cohort', 'service life', 'deployment', 'equivalence', 'founder', 'methodology']);

        // ─ Block B: Displacement Chain Step-by-Step Calculation ──────────────
        const unitDef = cInp.unit_definition || 'unit';
        const dispRes = cInp.displaced_resource || 'N/A';
        const baselifeProd = cInp.baseline_lifetime_prod != null ? Number(cInp.baseline_lifetime_prod).toLocaleString() : 'N/A';
        // range_improvement is now a CI improvement factor (how many times lower the tech CI is vs conventional)
        // Display as the derived displacement fraction: (1 - 1/factor) × 100%
        const _rangeFactor = cInp.range_improvement != null ? Number(cInp.range_improvement) : null;
        const rangeImp = _rangeFactor != null && _rangeFactor > 0
            ? (_rangeFactor >= 100
                ? `~100% (factor=${_rangeFactor.toFixed(0)}×, near-zero-CI)`
                : `${((1 - 1 / _rangeFactor) * 100).toFixed(1)}% displaced (factor=${_rangeFactor.toFixed(3)}×)`)
            : 'N/A';
        const specProdUnits = cInp.specific_production_units || '';
        const jd = ci.jd != null ? Number(ci.jd).toLocaleString(undefined, {maximumFractionDigits: 2}) : 'N/A';
        const ciY1 = (ci.operating_ci_series || [])[0];
        const svcLife = cInp.unit_service_life_yrs || 'N/A';
        const launchYr = cInp.commercial_launch_yr || 'N/A';
        const embDisp = cInp.emb_displaced_resource;
        const totalOp = ci.total_operating != null ? Number(ci.total_operating).toLocaleString(undefined, {maximumFractionDigits: 0}) : 'N/A';
        const totalEmb = ci.total_embodied != null ? Number(ci.total_embodied).toLocaleString(undefined, {maximumFractionDigits: 0}) : '0';
        const totalLc = ci.total_lifecycle != null ? Number(ci.total_lifecycle).toLocaleString(undefined, {maximumFractionDigits: 0}) : 'N/A';

        // CI series declining-check
        const ciSeries = ci.operating_ci_series || [];
        const ciDeclines = ciSeries.length > 1 && ciSeries[ciSeries.length-1] < ciSeries[0];
        const ciNote = ciDeclines
            ? `Declines from ${ciSeries[0]?.toFixed(4)} → ${ciSeries[ciSeries.length-1]?.toFixed(4)} tCO₂/unit as grid decarbonizes.`
            : `Flat at ${ciSeries[0]?.toFixed(4)} tCO₂/unit (resource CI does not change over model horizon).`;

        // Volume forecast
        const yearVols = cInp.year_volumes || [];
        let volRows = '';
        if (yearVols.length) {
            const _volCalLaunch = typeof launchYr === 'number' ? launchYr : (parseInt(launchYr) || null);
            yearVols.slice(0,10).forEach((v, i) => {
                const annOpVal = (ci.annual_operating || [])[i];
                const annEmbVal = (ci.annual_embodied || [])[i];
                const annLcVal = (ci.annual_lifecycle || [])[i];
                const ciYrVal = ciSeries[i];
                // Use calendar year label (same system as revenue table) — falls back to Y1/Y2 if no launch year
                const volYrLbl = _volCalLaunch ? String(_volCalLaunch + i) : `Y${i+1}`;
                volRows += `<tr>
                    <td>${volYrLbl}</td>
                    <td style="text-align:right;">${v != null ? Number(v).toLocaleString(undefined,{maximumFractionDigits:1}) : '—'}</td>
                    <td style="text-align:right;">${ciYrVal != null ? ciYrVal.toFixed(4) : '—'}</td>
                    <td style="text-align:right;">${annOpVal != null ? Number(annOpVal).toLocaleString(undefined,{maximumFractionDigits:0}) : '—'}</td>
                    <td style="text-align:right;">${annEmbVal != null && annEmbVal > 0 ? Number(annEmbVal).toLocaleString(undefined,{maximumFractionDigits:0}) : '—'}</td>
                    <td style="text-align:right;font-weight:600;">${annLcVal != null ? Number(annLcVal).toLocaleString(undefined,{maximumFractionDigits:0}) : '—'}</td>
                </tr>`;
            });
        }

        // Build a worked-example sentence to make the numbers concrete
        const _jdNum = ci.jd != null ? Number(ci.jd) : null;
        const _ciY1Num = (ci.operating_ci_series || [])[0];
        const _exampleVol = (cInp.year_volumes || [])[0];
        const _exampleOp = (ci.annual_operating || [])[0];
        const _workedExample = (_jdNum != null && _ciY1Num != null && _exampleVol != null && _exampleOp != null)
            ? `Worked example (Year 1): ${Number(_exampleVol).toLocaleString(undefined,{maximumFractionDigits:1})} ${unitDef}s deployed × ${_jdNum.toLocaleString(undefined,{maximumFractionDigits:2})} ${specProdUnits} displaced/unit × ${_ciY1Num.toFixed(4)} tCO₂/${specProdUnits||'unit'} CI = <strong>${Number(_exampleOp).toLocaleString(undefined,{maximumFractionDigits:0})} tCO₂</strong> avoided in Year 1.`
            : '';

        insertInline(carbonH2, `<div class="memo-chart-context">
            <p class="memo-chart-label">Displacement Chain Calculation</p>
            <p style="font-size:0.85rem;line-height:1.6;margin:0 0 8px;">
                The displacement chain converts a unit of deployed product into an annual avoided-CO₂ figure using four inputs that any analyst can verify independently.
                The chain runs: <em>deployed units → resource produced per unit over its life (JA) → fraction of conventional CI displaced (derived from JC) → displaced resource volume per unit (JD) → multiply by fleet size and by the CI of the displaced resource in each year → annual operating avoided CO₂ (JO)</em>.
                Embodied carbon (the CO₂ cost of manufacturing the units themselves) is then subtracted.
            </p>
            ${_workedExample ? `<p style="font-size:0.84rem;background:#f0f4ec;border-left:3px solid #5B7744;padding:6px 10px;margin:0 0 10px;border-radius:0 4px 4px 0;">${_workedExample}</p>` : ''}
            <table class="memo-rvm-table memo-carbon-chain-table">
                <thead><tr><th>Input / Step</th><th>Formula</th><th>Value for this Deal</th><th>How to Reproduce</th></tr></thead>
                <tbody>
                    <tr>
                        <td><strong>Unit (product)</strong></td>
                        <td>—</td>
                        <td>${unitDef}</td>
                        <td>One commercially deployable unit of the company's product. The volume forecast counts how many of these are built each year. Define this based on what the company sells (e.g. one solar panel system, one refinery module, one EV charger).</td>
                    </tr>
                    <tr>
                        <td><strong>Service Life</strong></td>
                        <td>—</td>
                        <td>${svcLife} yrs</td>
                        <td>Expected operational lifespan of one unit. Used to construct the carbon intensity time series for the years each cohort of units is in operation — earlier vintages get earlier-year CI values, later vintages get later-year values. Source: manufacturer specs or industry standard.</td>
                    </tr>
                    <tr>
                        <td><strong>JA — Baseline Lifetime Production</strong></td>
                        <td>—</td>
                        <td>${baselifeProd} ${specProdUnits}</td>
                        <td>Total quantity of the <em>displaced resource</em> that one unit produces (or processes) over its full service life. This bridges "unit of product" to "unit of resource" — because carbon intensity is a property of the resource (e.g. electricity, nickel, heat), not the product itself. Example: 1 MW solar × 25 yr × 0.25 CF × 8,760 hr = 54,750 MWh. Source: engineering specs × service life.</td>
                    </tr>
                    <tr>
                        <td><strong>JC — CI Improvement Factor</strong></td>
                        <td>—</td>
                        <td>${rangeImp}</td>
                        <td>How many times lower the new technology's carbon intensity is compared to the conventional alternative it displaces. The displacement fraction is derived as <em>(1 − 1/JC)</em>. Examples: JC = 1.4 → new tech has CI = conventional/1.4, so 1 − 1/1.4 = 28.6% of conventional CI is avoided. JC = 1000 → near-zero-CI technology (solar, wind, nuclear) displacing ~100% of conventional CI. JC = 1.111 → 10% improvement. Source: published lifecycle assessments or engineering analysis of the specific technology vs. its incumbent.</td>
                    </tr>
                    <tr style="background:rgba(91,119,68,0.05);">
                        <td><strong>JD — Displaced Volume / Unit</strong></td>
                        <td>(1 − 1/JC) × JA</td>
                        <td>${jd} ${specProdUnits}</td>
                        <td><strong>Lifetime total</strong> — the net quantity of displaced resource avoided because this unit exists, accumulated across its entire ${svcLife}-year service life. JD encodes both how much resource the unit handles (JA) and how much cleaner it is vs. conventional (JC). <em>This is a lifetime figure, not an annual rate</em> — it must be divided by service life before multiplying into annual impact rows.</td>
                    </tr>
                    <tr>
                        <td><strong>JD_annual — Annual Displacement Rate</strong></td>
                        <td>JD ÷ service_life</td>
                        <td>${_jdNum != null ? (_jdNum / (cInp.unit_service_life_yrs || 1)).toLocaleString(undefined, {maximumFractionDigits: 2}) : 'N/A'} ${specProdUnits}/yr</td>
                        <td>The per-unit <em>annual</em> displacement rate. Service life is in the <strong>denominator</strong> because JD is a lifetime total — dividing converts it to the annual rate that gets multiplied by CI and fleet size in each calendar year. Over a cohort's full service life the division cancels: Σ<sub>t</sub> JD_annual × CI[t] = JD × avg(CI). This is the value actually used in the year-by-year table below.</td>
                    </tr>
                    <tr>
                        <td><strong>Displaced Resource</strong></td>
                        <td>—</td>
                        <td>${dispRes}</td>
                        <td>The carbon-intensive resource or energy form being replaced (e.g. grid electricity, natural gas, coal-refined nickel, diesel). This determines which carbon intensity time series to look up. The CI of this resource — not the product itself — is what drives the tCO₂ calculation.</td>
                    </tr>
                    <tr>
                        <td><strong>JE — Carbon Intensity, Year 1</strong></td>
                        <td>CI database lookup</td>
                        <td>${ciY1 != null ? ciY1.toFixed(4) : 'N/A'} tCO₂/${specProdUnits||'unit'}</td>
                        <td>${ciDeclines
                            ? `Declines from ${ciSeries[0]?.toFixed(4)} → ${ciSeries[ciSeries.length-1]?.toFixed(4)} tCO₂/${specProdUnits||'unit'} over the model horizon as the underlying grid or supply chain decarbonizes. The declining series is why service life matters — a unit deployed in Year 1 gets Year 1 through Year N CI values; a unit deployed in Year 5 gets Year 5 through Year N+4 values. Source: NREL ATB, EPA eGRID, IEA, or commodity-specific LCA databases.`
                            : `Flat at ${ciSeries[0]?.toFixed(4)} tCO₂/${specProdUnits||'unit'} across the model horizon (this resource's carbon intensity is not projected to change materially). Source: EPA, IEA, or commodity LCA.`
                        }</td>
                    </tr>
                    <tr style="background:rgba(91,119,68,0.05);">
                        <td><strong>JO — Annual Operating Impact</strong></td>
                        <td>Σ<sub>d active</sub>: (JD ÷ service_life) × vol[d] × CI[t]</td>
                        <td colspan="2">Calculated year-by-year — see table below. For each calendar year <em>t</em>, sum across all cohorts <em>d</em> still in service: <code>JD_annual × vol[d] × CI[t]</code>. Because each cohort contributes to <strong>${svcLife} rows</strong> (one per service year), the lifetime total recovers correctly: summing a single cohort across its full life gives <code>JD_annual × service_life × vol × avg_CI = JD × vol × avg_CI</code>. The division and the row-count cancel exactly — dividing by service life is not a penalty, it is the correct annualisation of a lifetime total.</td>
                    </tr>
                    <tr>
                        <td><strong>Embodied Carbon</strong></td>
                        <td>${embDisp ? '(1 − 1/JC_emb) × baseline_prod_emb × CI_emb_yr × Vol_yr' : '—'}</td>
                        <td>${embDisp ? `${embDisp} (displaced resource for embodied)` : 'Not modeled for this deal'}</td>
                        <td>${totalEmb !== '0'
                            ? `Manufacturing-phase CO₂: the emissions generated in producing (not operating) the units themselves. Calculated the same way as operating impact but using the embodied CI improvement factor and the upstream resource's CI. Total embodied across the forecast: ${totalEmb} tCO₂. This reduces the net carbon benefit.`
                            : `Embodied carbon (manufacturing-phase emissions) is not modeled for this archetype, either because the upstream manufacturing process is negligible relative to operating impact, or because data is not available. For completeness, a future version should include an LCA-sourced embodied CO₂ per unit.`
                        }</td>
                    </tr>
                    <tr style="background:rgba(91,119,68,0.10);font-weight:700;">
                        <td><strong>LL — Total Lifecycle tCO₂</strong> <span style="font-weight:400;font-size:0.78rem;">(PRIME §5 ERP)</span></td>
                        <td>Σ_{t=0}^{horizon−1} (Operating_t + Embodied_t)</td>
                        <td>${totalLc} tCO₂</td>
                        <td>Sum of all calendar-year lifecycle impacts across the ${_horizon}-year analysis horizon (operating: ${totalOp} tCO₂ + embodied: ${totalEmb} tCO₂). Multi-cohort: each year's figure aggregates all in-service cohorts. This is the company-level gross avoided CO₂ before ownership attribution or risk adjustment. Horizon is configurable (${_horizon} yrs here; PRIME recommends 30). To replicate: build the year-by-year table below using the multi-cohort formula, then sum all rows.</td>
                    </tr>
                </tbody>
            </table>
        </div>`, ['displacement', 'chain', 'calculation', 'baseline', 'range', 'ci', 'jd', 'formula', 'carbon', 'avoided']);

        // ─ Block C: Year-by-Year Impact Table + Annual Bar Chart ─────────────
        if (volRows) {
            insertInline(carbonH2, `<div class="memo-chart-context">
                <p class="memo-chart-label">Year-by-Year Carbon Impact (Commercial Launch ${launchYr})</p>
                <p style="font-size:0.84rem;line-height:1.6;margin:0 0 8px;">
                    Each row represents one calendar year of the ${_horizon}-year analysis horizon (PRIME §5 — ERP summation).
                    <strong>Units Deployed</strong> = new ${unitDef}s put into service that year (annual deployment cohort, not a cumulative total). Each cohort remains in service for ${_svcL} years.
                    <strong>CI (tCO₂/${specProdUnits||'unit'})</strong> = the carbon intensity of the displaced resource in that calendar year — this is the grid/supply-chain CI at time t, shared across all in-service cohorts operating in that year.
                    <strong>Operating tCO₂ (multi-cohort)</strong> = Σ_{d active in year t} (JD/service_life) × vol[d] × CI[t] — the sum across all cohorts still operating in this calendar year. Earlier-year deployments continue to contribute in later years, and benefit from lower CI as the grid decarbonizes (PRIME §2).
                    <strong>Embodied tCO₂</strong> = manufacturing-phase emissions attributed at the deployment year: KE × CI_embodied_yr × vol_yr (PRIME §3).
                    <strong>Lifecycle tCO₂</strong> = Operating − Embodied (net avoided CO₂ for that calendar year, summing all active cohorts).
                </p>
                <div class="memo-chart-row">
                    <div class="memo-carbon-table-wrap">
                        <table class="memo-rvm-table">
                            <thead><tr>
                                <th>Year</th>
                                <th style="text-align:right;">Units Deployed</th>
                                <th style="text-align:right;">CI (tCO₂/${specProdUnits||'unit'})</th>
                                <th style="text-align:right;">Operating tCO₂</th>
                                <th style="text-align:right;">Embodied tCO₂</th>
                                <th style="text-align:right;">Net Lifecycle tCO₂</th>
                            </tr></thead>
                            <tbody>${volRows}</tbody>
                        </table>
                    </div>
                    <div class="memo-chart-wrap"><canvas id="memo-carbon-chart" height="300"></canvas></div>
                </div>
            </div>`, ['annual', 'year', 'impact', 'deployment', 'volume', 'operating', 'embodied', 'lifecycle']);
        }

        // ─ Block D: Attribution Waterfall ─────────────────────────────────────
        const riskDivLabel = riskDiv >= 6 ? 'TRL 1–4 (pre-commercial, 6× haircut)'
                           : riskDiv >= 3 ? 'TRL 5–6 (validated, 3× haircut)'
                           : 'TRL 7–9 (proven, no haircut)';
        const compTonnes = co.company_tonnes || 0;
        const voloProrata = co.volo_prorata || 0;
        const voloRiskAdj = co.volo_risk_adj || 0;
        const tpdRaw = co.tonnes_per_dollar != null ? Number(co.tonnes_per_dollar).toFixed(5) : 'N/A';
        const tpdRisk = co.risk_adj_tpd != null ? Number(co.risk_adj_tpd).toFixed(5) : 'N/A';

        insertInline(carbonH2, `<div class="memo-chart-context">
            <p class="memo-chart-label">VoLo Attribution Waterfall — From Company tCO₂ to t/$</p>
            <p style="font-size:0.85rem;line-height:1.6;margin:0 0 8px;">
                The company-level lifecycle tCO₂ is the PRIME ERP result — the deterministic potential avoided CO₂ if deployment proceeds as forecast.
                PRIME does not apply a probability or survival discount; it reports potential impact.
                VoLo adds two investor-level adjustments on top of PRIME: an <strong>ownership attribution</strong> (steps ①→②) and a <strong>risk adjustment</strong> (steps ②→④) that PRIME does not include.
            </p>
            <p style="font-size:0.85rem;line-height:1.6;margin:0 0 10px;">
                <strong>① → ② Ownership attribution (PRIME-aligned):</strong> VoLo only "owns" a fraction of the company's carbon impact equal to its equity stake.
                At entry, VoLo holds ${fmt(entryOwn,1)}% of the company, so VoLo's pro-rata tCO₂ = total lifecycle tCO₂ × ${fmt(entryOwn,1)}%.
                This is consistent with GHG Protocol equity-method attribution — and is the last step within the PRIME framework.
            </p>
            <p style="font-size:0.85rem;line-height:1.6;margin:0 0 10px;">
                <strong>② → ④ VoLo Risk Layer (not part of PRIME):</strong> The survival rate from the Monte Carlo simulation
                (${carbonSurvRate != null ? (carbonSurvRate*100).toFixed(1)+'%' : 'N/A'}) is applied as a probability-weighted discount to yield Risk-Adjusted tCO₂.
                <em>Only ④ P-50 Risk-Adjusted tCO₂ and ⑥ TCPI reflect this layer — ① and ② are PRIME-only.</em>
                See <strong>Cohort Analysis</strong> section for full derivation of survival rate (Carta graduation rates × TRL modifiers).
            </p>
            <p style="font-size:0.85rem;line-height:1.6;margin:0 0 10px;">
                <strong>TCPI (Tonnes CO₂ to Paid-In Capital):</strong> Dividing P-50 risk-adjusted tCO₂ by VoLo's check size ($${fmt(checkM,2)}M) gives the carbon capital efficiency — how many tonnes of CO₂ are avoided per dollar invested.
                This is VoLo's primary carbon KPI for comparing investment opportunities across sectors and scales, consistent with the metric published in VoLo's annual Impact Report.
                Typical VoLo portfolio range: 0.005–0.10 t CO₂/$. Higher is better, but extremely high values should be interrogated for model assumptions.
            </p>
            ${co.company_tonnes != null ? `<div class="memo-chart-wrap" style="height:260px;margin-bottom:14px;"><canvas id="memo-carbon-waterfall-chart"></canvas></div>` : ''}
            <table class="memo-rvm-table" style="width:100%;table-layout:fixed;">
                <colgroup>
                    <col style="width:16%;">
                    <col style="width:17%;">
                    <col style="width:10%;text-align:right;">
                    <col style="width:57%;">
                </colgroup>
                <thead><tr><th>Step</th><th>Formula</th><th style="text-align:right;">Value</th><th>Why This Step</th></tr></thead>
                <tbody>
                    <tr>
                        <td><strong>① Company Lifecycle tCO₂</strong> <span style="font-weight:400;font-size:0.75rem;color:#16a34a;">PRIME ERP</span></td>
                        <td>Σ annual lifecycle (above)</td>
                        <td style="text-align:right;font-weight:700;">${fmt(compTonnes, 0)}</td>
                        <td>PRIME ERP result — gross avoided CO₂ at 100% company scale across the ${_horizon}-year analysis horizon. Deterministic potential; no survival discount applied here. Starting point before any investor-level adjustments.</td>
                    </tr>
                    <tr>
                        <td><strong>② VoLo Pro-Rata tCO₂</strong> <span style="font-weight:400;font-size:0.75rem;color:#16a34a;">PRIME ERP</span></td>
                        <td>① × ${fmt(entryOwn,1)}% entry ownership</td>
                        <td style="text-align:right;font-weight:700;">${fmt(voloProrata, 0)}</td>
                        <td>VoLo's proportional share of company-level PRIME ERP impact at the entry ownership stake. Uses entry (not diluted) ownership because we measure impact at the time of investment commitment. This is the last step within the PRIME framework — no survival discount applied.</td>
                    </tr>
                    <tr style="background:rgba(220,38,38,0.03);">
                        <td><strong>③ Simulation Survival Rate</strong> <span style="font-weight:400;font-size:0.75rem;color:#dc2626;">VoLo only</span></td>
                        <td>× ${carbonSurvRate != null ? (carbonSurvRate*100).toFixed(1)+'%' : 'N/A'} survival rate</td>
                        <td style="text-align:right;">${carbonSurvRate != null ? (carbonSurvRate*100).toFixed(1)+'%' : (riskSrc || 'TRL-based')}</td>
                        <td>Probability-weighted survival rate from the Monte Carlo simulation (${carbonSurvSrc || 'simulation'}). This is the same survival_rate used in the financial model — Carta graduation rates × TRL modifiers across 5,000 paths. Replaces the old coarse TRL bucket divisor so carbon and financial risk are consistent.</td>
                    </tr>
                    <tr style="background:rgba(91,119,68,0.08);">
                        <td><strong>④ Risk-Adjusted tCO₂</strong> <span style="font-weight:400;font-size:0.75rem;color:#dc2626;">VoLo only</span></td>
                        <td>② × ${carbonSurvRate != null ? (carbonSurvRate*100).toFixed(1)+'%' : 'N/A'}</td>
                        <td style="text-align:right;font-weight:700;">${fmt(voloRiskAdj, 0)}</td>
                        <td>VoLo Risk Layer: expected (probability-weighted) avoided CO₂. Not a PRIME output — this is VoLo's own extension to put early-stage and late-stage deals on an equal footing. To replicate: multiply pro-rata tCO₂ (② above) by the simulation survival_rate (③ above).</td>
                    </tr>
                    <tr>
                        <td><strong>⑤ TCPI (Unadjusted)</strong></td>
                        <td>② ÷ $${fmt(checkM,2)}M check</td>
                        <td style="text-align:right;">${tpdRaw}</td>
                        <td>Raw carbon capital efficiency before survival risk adjustment. Represents the theoretical maximum if the company fully succeeds. Useful as an upper bound and for comparing sector-level potential independent of technology maturity.</td>
                    </tr>
                    <tr style="background:rgba(91,119,68,0.08);font-weight:600;">
                        <td><strong>⑥ TCPI (Risk-Adj)</strong> <span style="font-weight:400;font-size:0.75rem;color:#dc2626;">VoLo only</span></td>
                        <td>④ ÷ $${fmt(checkM,2)}M check</td>
                        <td style="text-align:right;font-weight:700;">${tpdRisk}</td>
                        <td>TCPI — Tonnes CO₂ to Paid-In Capital. VoLo's primary carbon KPI, published in VoLo's annual Impact Report. Applies likelihood-of-success survival discount before dividing by check size. To replicate: use ④ (P-50 risk-adj tCO₂) ÷ check size in dollars. Use this — not raw company tCO₂ — for cross-deal comparison. Typical VoLo range: 0.005–0.10 t CO₂/$.</td>
                    </tr>
                </tbody>
            </table>
            <p class="memo-chart-note">To replicate this waterfall: (1) compute total potential tCO₂ (10yr) from the year-by-year table above, (2) multiply by entry ownership % per GHG Protocol equity-method attribution, (3) multiply by the simulation survival_rate (${carbonSurvRate != null ? (carbonSurvRate*100).toFixed(1)+'%' : 'see simulation section'} — likelihood-of-success curve from the Monte Carlo financial model), (4) divide by check size in dollars to get TCPI. All inputs are shown explicitly in this memo.</p>
        </div>`, ['attribution', 'pro-rata', 'ownership', 'risk', 'divisor', 'waterfall', 'tco2', 'portfolio', 'efficiency', 'tpd']);

        // Grid Carbon Intensity Trajectory chart removed from memo
    }

    // ── Now render the actual Chart.js charts ──
    setTimeout(() => { _memoRenderCharts(report); }, 100);
}


// ── Inject data room images into memo sections by category ──────────────────
function _memoInjectImages(container, imageDocs) {
    if (!container || !imageDocs || imageDocs.length === 0) return;

    // Section key → H2 title keyword mapping
    const SECTION_TITLES = {
        'company_overview': 'company overview',
        'market': 'market',
        'business_model': 'business model',
        'financials': 'financials',
        'team': 'team',
        'traction': 'traction',
        'competitive_position': 'competitive position',
        'carbon_impact': 'carbon impact',
        'technology_ip_moat': 'technology',
        'financing_overview': 'financing overview',
        'cohort_analysis': 'cohort analysis',
        'fund_return_model': 'fund return model',
    };

    function findSectionH2(sectionKey) {
        const keyword = SECTION_TITLES[sectionKey];
        if (!keyword) return null;
        const h2s = container.querySelectorAll('h2');
        for (const h2 of h2s) {
            if (h2.textContent.toLowerCase().includes(keyword)) return h2;
        }
        return null;
    }

    // Track which sections already have an image to avoid overloading
    const sectionsWithImages = new Set();

    for (const img of imageDocs) {
        // Find the best target section (first matching section that doesn't already have an image)
        let targetH2 = null;
        let targetKey = null;
        for (const sectionKey of (img.section_targets || [])) {
            if (!sectionsWithImages.has(sectionKey)) {
                const h2 = findSectionH2(sectionKey);
                if (h2) {
                    targetH2 = h2;
                    targetKey = sectionKey;
                    break;
                }
            }
        }
        if (!targetH2) continue;

        sectionsWithImages.add(targetKey);

        // Build a readable caption from the filename
        const cleanName = img.file_name.replace(/\.[^.]+$/, '').replace(/[_-]/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
        const catLabel = (img.category || 'other').replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());

        const imgHtml = `<figure class="memo-image-figure">
            <button class="memo-image-remove" title="Remove image from memo" onclick="_memoRemoveImage(this, ${img.doc_id})">✕</button>
            <img src="/api/memo/documents/${img.doc_id}/view?token=${encodeURIComponent(localStorage.getItem('rvm_token') || '')}"
                 alt="${_escHtml(cleanName)}"
                 class="memo-image-embed"
                 loading="lazy"
                 onerror="this.closest('.memo-image-figure').style.display='none'">
            <figcaption class="memo-image-caption">
                <span class="memo-image-source">${_escHtml(catLabel)}</span>
                ${_escHtml(cleanName)}
            </figcaption>
        </figure>`;

        // Insert after the first substantial paragraph in the section
        const sectionEls = [];
        let el = targetH2.nextElementSibling;
        while (el && el.tagName !== 'H2') {
            sectionEls.push(el);
            el = el.nextElementSibling;
        }

        let inserted = false;
        for (const sEl of sectionEls) {
            if (sEl.tagName === 'P' && sEl.textContent.length > 50) {
                const div = document.createElement('div');
                div.innerHTML = imgHtml;
                sEl.after(div.firstElementChild);
                inserted = true;
                break;
            }
        }
        if (!inserted && sectionEls.length > 0) {
            const div = document.createElement('div');
            div.innerHTML = imgHtml;
            sectionEls[sectionEls.length - 1].after(div.firstElementChild);
        }
    }
}


async function _memoRemoveImage(btn, docId) {
    const figure = btn.closest('.memo-image-figure');
    if (!figure) return;
    // Remove from DOM immediately for instant feedback
    figure.remove();
    // Persist deletion so it stays gone on export and reload
    if (docId) {
        try {
            await fetch(`/api/memo/documents/${docId}`, { method: 'DELETE', headers: _rvmHeaders() });
        } catch (e) {
            console.warn('Could not delete image doc:', e);
        }
    }
}

function _memoRenderCharts(r) {
    const sim = r.simulation || {};
    const carbon = r.carbon_impact || {};
    const adoption = r.adoption_analysis || {};
    const fComp = r.founder_comparison || {};
    const ps = r.position_sizing || {};
    const ov = r.deal_overview || {};
    const risk = r.risk_summary || {};

    const chartFont = "'Inter', 'system-ui', sans-serif";
    const C = {
        green: '#5B7744', sage: '#8BA872', blueSteel: '#607D8B',
        accent: '#3B5249', danger: '#dc2626', muted: '#94a3b8',
    };

    // Helper to create chart and track instance
    function mkChart(id, config) {
        const canvas = document.getElementById(id);
        if (!canvas) return null;
        const ctx = canvas.getContext('2d');
        const chart = new Chart(ctx, config);
        _memoChartInstances.push(chart);
        return chart;
    }

    // ── MOIC Distribution ──
    if (sim.moic_histogram) {
        const hist = sim.moic_histogram;
        const bins = hist.bins || [];
        const counts = hist.counts || [];
        const labels = bins.map((b, i) => i < bins.length - 1 ? `${b.toFixed(1)}x` : '');
        mkChart('memo-moic-chart', {
            type: 'bar',
            data: {
                labels: labels.slice(0, -1),
                datasets: [{
                    data: counts, backgroundColor: C.green + '88',
                    borderColor: C.green, borderWidth: 1,
                }]
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                plugins: { legend: { display: false }, title: { display: true, text: 'MOIC Distribution (Survivors)', font: { family: chartFont, size: 13 } } },
                scales: { x: { title: { display: true, text: 'MOIC' } }, y: { title: { display: true, text: 'Frequency' } } },
            }
        });
    }

    // ── Outcome Breakdown ──
    if (sim.outcome_breakdown) {
        const ob = sim.outcome_breakdown;
        const oLabels = ['Full Exit', 'Stage Exit', 'Partial Recovery', 'Late Small Exit', 'Total Loss'];
        // outcome_breakdown values can be plain numbers OR objects {count, pct, label}
        const _obVal = (v) => v == null ? 0 : (typeof v === 'object' ? (v.pct != null ? v.pct / 100 : (v.count || 0)) : v);
        const oData = [_obVal(ob.full_exit), _obVal(ob.stage_exit), _obVal(ob.partial_recovery), _obVal(ob.late_small_exit), _obVal(ob.total_loss)];
        const oColors = [C.green, C.sage, C.blueSteel, C.muted, C.danger];
        mkChart('memo-outcome-chart', {
            type: 'bar',
            data: {
                labels: oLabels,
                datasets: [{ data: oData, backgroundColor: oColors }]
            },
            options: {
                responsive: true, maintainAspectRatio: false, indexAxis: 'y',
                plugins: { legend: { display: false }, title: { display: true, text: 'Outcome Breakdown', font: { family: chartFont, size: 13 } } },
                scales: { x: { title: { display: true, text: 'Probability' }, ticks: { callback: v => (v*100).toFixed(0)+'%' } } },
            }
        });
    }

    // ── S-Curve (Market section + Financials section) ──
    if (adoption.scurve) {
        const sc = adoption.scurve;
        const years = sc.years || [];
        const buildSCurveDatasets = () => {
            const ds = [];
            // data key may be 'p50' or 'median'
            const scMedian = sc.p50 || sc.median;
            if (scMedian) ds.push({ label: 'Median (P50)', data: scMedian, borderColor: C.green, borderWidth: 2.5, fill: false, pointRadius: 0 });
            if (sc.p25 && sc.p75) {
                ds.push({ label: 'P25', data: sc.p25, borderColor: C.sage+'66', borderWidth: 1, borderDash: [4,4], fill: false, pointRadius: 0 });
                ds.push({ label: 'P75', data: sc.p75, borderColor: C.sage+'66', borderWidth: 1, borderDash: [4,4], fill: '-1', backgroundColor: C.green+'15', pointRadius: 0 });
            }
            if (sc.p10 && sc.p90) {
                ds.push({ label: 'P10', data: sc.p10, borderColor: C.muted+'44', borderWidth: 1, fill: false, pointRadius: 0 });
                ds.push({ label: 'P90', data: sc.p90, borderColor: C.muted+'44', borderWidth: 1, fill: '-1', backgroundColor: C.green+'08', pointRadius: 0 });
            }
            return ds;
        };
        const sCurveOptions = {
            responsive: true, maintainAspectRatio: false,
            plugins: { title: { display: true, text: 'Adoption S-Curve', font: { family: chartFont, size: 13 } }, legend: { position: 'bottom', labels: { font: { size: 10 } } } },
            scales: { x: { title: { display: true, text: 'Year' } }, y: { title: { display: true, text: 'Cumulative Adoption' } } },
        };
        mkChart('memo-scurve-chart', { type: 'line', data: { labels: years, datasets: buildSCurveDatasets() }, options: sCurveOptions });
    }

    // ── Revenue Cone (Business Model + Financials sections) ──
    const revTraj = sim.revenue_trajectories || {};
    const _memoEntryYr = r.deal_overview?.entry_year || new Date().getFullYear();
    const _memoFundVintageYr = r.deal_overview?.fund_vintage_year || _memoEntryYr;
    const _memoDealOffset = r.deal_overview?.deal_offset_years || 0;
    const _rtMedian = revTraj.median || revTraj.p50 || [];
    // X-axis anchored to fund_vintage_year; MC simulation data offset by deal_offset_years
    const _rtN = _rtMedian.length;
    if (_rtN) {
        const _rtTotalN = _memoDealOffset + _rtN;
        const ry = Array.from({length: _rtTotalN}, (_, i) => _memoFundVintageYr + i);
        // Helper: prepend fund-offset nulls to MC arrays
        const _padRT = arr => arr ? [...new Array(_memoDealOffset).fill(null), ...arr] : null;
        const buildRevDatasets = () => {
            const ds = [];
            const rtMedian = revTraj.p50 || revTraj.median;
            if (rtMedian) ds.push({ label: 'Sim Median', data: _padRT(rtMedian), borderColor: C.green, borderWidth: 2.5, fill: false, pointRadius: 0 });
            if (revTraj.p25 && revTraj.p75) {
                ds.push({ label: 'P25', data: _padRT(revTraj.p25), borderColor: C.sage+'55', borderWidth: 1, fill: false, pointRadius: 0 });
                ds.push({ label: 'P75', data: _padRT(revTraj.p75), borderColor: C.sage+'55', borderWidth: 1, fill: '-1', backgroundColor: C.green+'12', pointRadius: 0 });
            }
            if (revTraj.p10 && revTraj.p90) {
                ds.push({ label: 'P10', data: _padRT(revTraj.p10), borderColor: C.muted+'33', borderWidth: 1, fill: false, pointRadius: 0 });
                ds.push({ label: 'P90', data: _padRT(revTraj.p90), borderColor: C.muted+'33', borderWidth: 1, fill: '-1', backgroundColor: C.green+'06', pointRadius: 0 });
            }
            // Founder projection overlay — calendar_year aligned to fund_vintage_year axis
            // Authoritative source: r.financial_model.fiscal_years (same as FM summary table).
            // Falls back to stored y.calendar_year, then entry_year+i if neither is available.
            if (fComp.has_data && fComp.revenue?.year_by_year?.length) {
                const fm = r.financial_model || {};
                const _fmFiscalYrs = (fm.has_data && fm.fiscal_years?.length)
                    ? fm.fiscal_years.filter(y => !_memoEntryYr || y >= _memoEntryYr)
                    : null;
                const founderData = new Array(_rtTotalN).fill(null);
                fComp.revenue.year_by_year.forEach((y, i) => {
                    const calYr = (_fmFiscalYrs && i < _fmFiscalYrs.length)
                        ? _fmFiscalYrs[i]
                        : (y.calendar_year || (_memoEntryYr + i));
                    const t = calYr - _memoFundVintageYr;
                    if (t >= 0 && t < _rtTotalN) founderData[t] = y.founder;
                });
                if (founderData.some(v => v != null)) {
                    ds.push({ label: 'Founder', data: founderData, borderColor: '#e36209', borderWidth: 2, borderDash: [6,3], fill: false, pointRadius: 3 });
                }
            }
            return ds;
        };
        const revConeOptions = {
            responsive: true, maintainAspectRatio: false,
            plugins: { title: { display: true, text: 'Revenue Cone vs Founder Projections ($M)', font: { family: chartFont, size: 13 } }, legend: { position: 'bottom', labels: { font: { size: 10 } } } },
            scales: { x: { title: { display: true, text: 'Calendar Year' } }, y: { title: { display: true, text: 'Revenue ($M)' } } },
        };
        mkChart('memo-revenue-cone-chart', { type: 'line', data: { labels: ry, datasets: buildRevDatasets() }, options: revConeOptions });
    }

    // ── Carbon Impact (3 charts: stacked bar + waterfall + CI decay) ──
    const ci = carbon.intermediates || {};
    const cInp2 = carbon.carbon_inputs || {};
    const co2 = carbon.outputs || {};
    const riskDiv2 = carbon.risk_divisor_used || 1;

    // Chart 1: Annual stacked bar (operating + embodied) — X-axis anchored to fund_vintage_year
    if (ci.annual_lifecycle?.length) {
        const _carbonLaunchYr = cInp2.commercial_launch_yr || _memoEntryYr;
        const _carbonOffset = Math.max(0, _carbonLaunchYr - _memoFundVintageYr);
        const _carbonTotalLen = _carbonOffset + ci.annual_lifecycle.length;
        const carbonYears = Array.from({length: _carbonTotalLen}, (_, i) => String(_memoFundVintageYr + i));
        const _padCarbon = arr => arr ? [...new Array(_carbonOffset).fill(null), ...arr] : [];
        mkChart('memo-carbon-chart', {
            type: 'bar',
            data: {
                labels: carbonYears,
                datasets: [
                    { label: 'Operating (displaced energy)', data: _padCarbon(ci.annual_operating), backgroundColor: C.accent + 'cc', borderRadius: 2 },
                    { label: 'Embodied (manufacturing)', data: _padCarbon(ci.annual_embodied), backgroundColor: C.sage + '88', borderRadius: 2 },
                ]
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                scales: { x: { stacked: true }, y: { stacked: true, title: { display: true, text: 'tCO₂' } } },
                plugins: {
                    title: { display: true, text: 'Annual Carbon Impact by Type (tCO₂)', font: { family: chartFont, size: 13 } },
                    legend: { position: 'bottom', labels: { font: { size: 10 } } },
                    tooltip: {
                        callbacks: {
                            footer: (items) => {
                                const total = items.reduce((s, i) => s + i.parsed.y, 0);
                                return `Total: ${total.toLocaleString(undefined, {maximumFractionDigits: 0})} tCO₂`;
                            }
                        }
                    }
                },
            }
        });
    }

    // Chart 2: Attribution waterfall — dual Y-axis (tCO₂ left, t/$ right)
    if (co2.company_tonnes != null) {
        const wfLabels = ['① Total Potential\n(10yr)', '② Fund\nPro-Rata Share', '④ P-50\nRisk-Adjusted', '⑥ TCPI\n(t CO₂/$)'];
        mkChart('memo-carbon-waterfall-chart', {
            type: 'bar',
            data: {
                labels: wfLabels,
                datasets: [
                    {
                        label: 'Avoided CO₂ (left axis)',
                        data: [co2.company_tonnes || 0, co2.volo_prorata || 0, co2.volo_risk_adj || 0, null],
                        backgroundColor: [C.blueSteel + 'cc', C.green + 'cc', C.accent + 'cc', 'transparent'],
                        borderColor:     [C.blueSteel,         C.green,         C.accent,         'transparent'],
                        borderWidth: 1, borderRadius: 3,
                        yAxisID: 'y',
                    },
                    {
                        label: 'TCPI t CO₂/$ (right axis)',
                        data: [null, null, null, co2.risk_adj_tpd || 0],
                        backgroundColor: ['transparent', 'transparent', 'transparent', '#e36209cc'],
                        borderColor:     ['transparent', 'transparent', 'transparent', '#e36209'],
                        borderWidth: 1, borderRadius: 3,
                        yAxisID: 'y2',
                    }
                ]
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                plugins: {
                    legend: { display: true, position: 'bottom', labels: { font: { size: 10 }, boxWidth: 12 } },
                    title: { display: true, text: 'Carbon Attribution Step-Down', font: { family: chartFont, size: 13 } },
                    tooltip: {
                        callbacks: {
                            label: (item) => {
                                if (item.datasetIndex === 1) return `t/$ = ${(item.parsed.y || 0).toFixed(5)}`;
                                return `${(item.parsed.y || 0).toLocaleString(undefined, {maximumFractionDigits: 0})} tCO₂`;
                            }
                        }
                    }
                },
                scales: {
                    x: { ticks: { font: { size: 10 }, maxRotation: 0, minRotation: 0 } },
                    y: {
                        type: 'linear', position: 'left',
                        title: { display: true, text: 'Avoided CO₂ (tonnes)', font: { size: 10 } },
                        beginAtZero: true,
                        ticks: { callback: v => v >= 1e6 ? (v/1e6).toFixed(1)+'M' : v >= 1e3 ? (v/1e3).toFixed(0)+'K' : v }
                    },
                    y2: {
                        type: 'linear', position: 'right',
                        title: { display: true, text: 't/$ (risk-adjusted)', font: { size: 10 } },
                        beginAtZero: true,
                        grid: { drawOnChartArea: false },
                        ticks: { callback: v => v.toFixed(4) }
                    }
                }
            }
        });
    }

    // Chart 3: Carbon Intensity decay series — removed from memo

    // ── EV at Exit distribution bar chart (Financials section) ──
    const evData3 = sim.ev_at_exit || {};
    if (evData3.mean_m) {
        mkChart('memo-fin-ev-chart', {
            type: 'bar',
            data: {
                labels: ['P25', 'P50', 'Mean', 'P75', 'P90'],
                datasets: [{
                    label: 'EV at Exit ($M)',
                    data: [evData3.p25_m, evData3.p50_m, evData3.mean_m, evData3.p75_m, evData3.p90_m],
                    backgroundColor: [C.muted+'88', C.green+'aa', C.blueSteel+'cc', C.green+'cc', C.accent+'cc'],
                    borderColor: [C.muted, C.green, C.blueSteel, C.green, C.accent],
                    borderWidth: 1,
                    borderRadius: 3,
                }]
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    title: { display: true, text: 'EV at Exit Distribution ($M)', font: { family: chartFont, size: 13 } },
                    tooltip: { callbacks: { label: (i) => `$${i.parsed.y?.toLocaleString(undefined, {maximumFractionDigits:1})}M` } }
                },
                scales: { y: { title: { display: true, text: 'Enterprise Value ($M)' }, beginAtZero: true } }
            }
        });
    }

    // Check size charts rendered in Fund Return Model section only (memo-frm-sizing-chart)

    // Follow-on charts rendered in Fund Return Model section only (memo-frm-fo-* canvases)

    // ── Fund Return Model: Check Size + Follow-on charts (separate canvases) ──
    const ps3 = r.position_sizing || {};
    if (ps3.has_data && ps3.grid_search?.grid?.length) {
        const grid3 = ps3.grid_search.grid;
        const optimal3 = ps3.grid_search.optimal || {};
        const labels3 = grid3.map(g => '$' + g.check_m.toFixed(2) + 'M');
        mkChart('memo-frm-sizing-chart', {
            type: 'line',
            data: {
                labels: labels3,
                datasets: [
                    { label: 'Fund P50 Δ%', data: grid3.map(g => (g.fund_p50_pct_chg||0)*100), borderColor: C.green, borderWidth: 2, fill: false, pointRadius: 1 },
                    { label: 'Fund P10 Δ%', data: grid3.map(g => (g.fund_p10_pct_chg||0)*100), borderColor: C.danger+'88', borderWidth: 1.5, fill: false, pointRadius: 0 },
                    { label: 'Fund P90 Δ%', data: grid3.map(g => (g.fund_p90_pct_chg||0)*100), borderColor: C.blueSteel+'88', borderWidth: 1.5, fill: false, pointRadius: 0 },
                ]
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                plugins: { title: { display: true, text: 'Fund TVPI Impact by Check Size', font: { family: chartFont, size: 13 } }, legend: { position: 'bottom', labels: { font: { size: 10 } } } },
                scales: { x: { title: { display: true, text: 'Check Size' } }, y: { title: { display: true, text: 'Δ% Fund TVPI' }, ticks: { callback: v => v.toFixed(2)+'%' } } },
            }
        });
        mkChart('memo-frm-sizing-score-chart', {
            type: 'bar',
            data: {
                labels: labels3,
                datasets: [{
                    data: grid3.map(g => g.composite_score || 0),
                    backgroundColor: grid3.map(g => g.check_m === optimal3.check_m ? C.green : C.blueSteel+'66'),
                    borderColor: grid3.map(g => g.check_m === optimal3.check_m ? C.green : C.blueSteel+'44'),
                    borderWidth: 1,
                    borderRadius: 2,
                }]
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                plugins: { legend: { display: false }, title: { display: true, text: 'Composite Optimization Score', font: { family: chartFont, size: 13 } } },
                scales: { x: { title: { display: true, text: 'Check Size' } }, y: { title: { display: true, text: 'Score' } } },
            }
        });
    }

    const foPs3 = r.followon_position_sizing || {};
    if (foPs3.has_data && foPs3.blended_curve?.length > 1) {
        const bc3 = foPs3.blended_curve;
        const bcLabels3 = bc3.map(b => '$' + b.followon_check_m.toFixed(2) + 'M');
        mkChart('memo-frm-fo-blended-chart', {
            type: 'line',
            data: {
                labels: bcLabels3,
                datasets: [
                    { label: 'P90 (Upside)',  data: bc3.map(b => b.blended_moic_p90), borderColor: C.blueSteel+'88', borderWidth: 1.5, fill: false, pointRadius: 0 },
                    { label: 'P50 (Median)',  data: bc3.map(b => b.blended_moic_p50), borderColor: C.green, borderWidth: 2.5, fill: false, pointRadius: 1 },
                    { label: 'P10 (Downside)',data: bc3.map(b => b.blended_moic_p10), borderColor: C.danger+'88', borderWidth: 1.5, fill: false, pointRadius: 0 },
                ]
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                plugins: {
                    title: { display: true, text: 'Blended MOIC vs Follow-on Check Size', font: { family: chartFont, size: 13 } },
                    subtitle: { display: true, text: 'Blended = exit proceeds ÷ (prior rounds + follow-on)', font: { family: chartFont, size: 10 }, color: '#888', padding: { bottom: 6 } },
                    legend: { position: 'bottom', labels: { font: { size: 10 } } }
                },
                scales: { x: { title: { display: true, text: 'Follow-on Check Size' } }, y: { title: { display: true, text: 'Blended MOIC (all-in)' }, ticks: { callback: v => v.toFixed(1)+'x' } } },
            }
        });
        const foGrid3 = foPs3.standalone_grid_search?.grid || [];
        const foGridHasFundData3 = foGrid3.some(g => g.fund_p50_pct_chg != null);
        if (foGrid3.length > 1 && foGridHasFundData3) {
            const foLabels3 = foGrid3.map(g => '$' + g.check_m.toFixed(2) + 'M');
            mkChart('memo-frm-fo-grid-chart', {
                type: 'line',
                data: {
                    labels: foLabels3,
                    datasets: [
                        { label: 'P50 (Median)',  data: foGrid3.map(g => (g.fund_p50_pct_chg||0)*100), borderColor: C.green,           borderWidth: 2,   fill: false, pointRadius: 1 },
                        { label: 'P10 (Downside)',data: foGrid3.map(g => (g.fund_p10_pct_chg||0)*100), borderColor: C.danger+'88',     borderWidth: 1.5, fill: false, pointRadius: 0 },
                        { label: 'P90 (Upside)',  data: foGrid3.map(g => (g.fund_p90_pct_chg||0)*100), borderColor: C.blueSteel+'88',  borderWidth: 1.5, fill: false, pointRadius: 0 },
                    ]
                },
                options: {
                    responsive: true, maintainAspectRatio: false,
                    plugins: {
                        title: { display: true, text: 'Follow-on Impact on Fund TVPI', font: { family: chartFont, size: 13 } },
                        subtitle: { display: true, text: '% change in fund-level TVPI at each follow-on check size', font: { family: chartFont, size: 10 }, color: '#888', padding: { bottom: 6 } },
                        legend: { position: 'bottom', labels: { font: { size: 10 } } }
                    },
                    scales: { x: { title: { display: true, text: 'Follow-on Check Size' } }, y: { title: { display: true, text: 'Δ% Fund TVPI' }, ticks: { callback: v => v.toFixed(2)+'%' } } },
                }
            });
        }
    }

    // ── Render any pending charts queued by inline section blocks ──────────
    if (typeof _pendingCharts !== 'undefined' && _pendingCharts && _pendingCharts.length > 0) {
        for (const pc of _pendingCharts) {
            mkChart(pc.id, { type: pc.type, data: pc.data, options: pc.options });
        }
        _pendingCharts = [];
    }
}


// ── Init on tab switch ──────────────────────────────────────────────────────
const _origSwitchTab = switchTab;
switchTab = function(tab) {
    _origSwitchTab(tab);
    if (tab === 'memo') {
        memoLoadReports();
        memoLoadTemplates();
        memoInitUpload();
        _memoFollowonInit();
        driveCheckStatus();    // also calls driveLoadLibraries() if connected
        if (_memo.sessionId) memoRefreshDocList();
    }
};

// ══════════════════════════════════════════════════════════════════════
// DD FINANCIAL ANALYSIS TAB
// ══════════════════════════════════════════════════════════════════════

const _dd = {
    currentScenario: 'conservative',
    scenarios: {},       // {name: assumptions}
    results: null,       // last compute result
    reportId: null,
    reportData: null,    // selected deal report data
    charts: {},          // Chart.js instances
};

// ── Scenario tab switching ────────────────────────────────────────
function ddSwitchScenario(name) {
    _dd.currentScenario = name;
    document.querySelectorAll('.dd-scenario-tab').forEach(t => t.classList.remove('active'));
    const tab = document.querySelector(`.dd-scenario-tab[data-scenario="${name}"]`);
    if (tab) tab.classList.add('active');

    const editorPanel = document.getElementById('dd-editor-panel');
    const resultsPanel = document.getElementById('dd-results-panel');
    const compPanel = document.getElementById('dd-comparison-panel');
    const dilutionPanel = document.getElementById('dd-dilution-panel');

    if (name === 'comparison') {
        editorPanel.style.display = 'none';
        resultsPanel.style.display = 'none';
        if (dilutionPanel) dilutionPanel.style.display = 'none';
        compPanel.style.display = _dd.results ? '' : 'none';
        if (_dd.results) ddRenderComparison();
    } else {
        editorPanel.style.display = '';
        compPanel.style.display = 'none';
        // Load this scenario's assumptions into the editor
        ddLoadScenarioToEditor(name);
        // Show results for this scenario if available
        if (_dd.results && _dd.results.scenarios && _dd.results.scenarios[name]) {
            resultsPanel.style.display = '';
            if (dilutionPanel) dilutionPanel.style.display = '';
            ddRenderScenarioResults(name);
        } else {
            resultsPanel.style.display = 'none';
            if (dilutionPanel) dilutionPanel.style.display = 'none';
        }
    }
}

// ── Load deal reports into the dropdown ────────────────────────────
function ddLoadReports() {
    const sel = document.getElementById('dd-report-select');
    if (!sel) return;
    _apiFetch('/api/deal-pipeline/reports')
        .then(r => r.json())
        .then(data => {
            sel.innerHTML = '<option value="">Select a Deal Report...</option>';
            (data.reports || []).forEach(rpt => {
                const opt = document.createElement('option');
                opt.value = rpt.id;
                opt.textContent = `${rpt.company_name} — ${rpt.entry_stage} (${rpt.created_at?.split('T')[0] || ''})`;
                sel.appendChild(opt);
            });
        }).catch(() => {});
}

function ddReportChanged() {
    const sel = document.getElementById('dd-report-select');
    const id = sel ? parseInt(sel.value) : null;
    _dd.reportId = id || null;
    _dd.reportData = null;

    const btns = ['dd-defaults-btn', 'dd-run-btn', 'dd-save-btn'];
    btns.forEach(b => { const el = document.getElementById(b); if (el) el.disabled = !id; });

    if (id) {
        _apiFetch(`/api/deal-pipeline/report/${id}`)
            .then(r => r.json())
            .then(data => {
                _dd.reportData = data;
                // Also try to load saved scenarios
                ddLoadSavedScenarios(id);
            }).catch(() => {});
    }
}

// ── Load default assumptions from backend ─────────────────────────
function ddLoadDefaults() {
    const stage = _dd.reportData?.inputs?.entry_stage || 'Seed';
    _apiFetch('/api/dd/defaults', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ entry_stage: stage }),
    })
    .then(r => r.json())
    .then(data => {
        _dd.scenarios = data;
        ddLoadScenarioToEditor(_dd.currentScenario);
    }).catch(e => console.error('DD defaults error', e));
}

// ── Load saved scenarios ──────────────────────────────────────────
function ddLoadSavedScenarios(reportId) {
    _apiFetch(`/api/dd/scenarios/${reportId}`)
        .then(r => r.json())
        .then(data => {
            if (data.count > 0) {
                _dd.scenarios = data.scenarios;
                ddLoadScenarioToEditor(_dd.currentScenario);
            } else {
                ddLoadDefaults();
            }
        }).catch(() => ddLoadDefaults());
}

// ── Editor ↔ Scenario Data Binding ────────────────────────────────
function ddLoadScenarioToEditor(name) {
    const a = _dd.scenarios[name];
    if (!a) return;
    _ddSet('dd-time-to-launch', a.time_to_launch_years || 0);
    _ddSet('dd-revenue-y1', a.revenue_y1_m);
    _ddSet('dd-revenue-cagr', a.revenue_cagr_pct);
    _ddSet('dd-proj-years', a.projection_years);
    _ddSet('dd-custom-rev', '');
    _ddSet('dd-gm-start', a.gross_margin_start_pct);
    _ddSet('dd-gm-end', a.gross_margin_end_pct);
    _ddSet('dd-rd-start', a.opex_rd_pct_rev);
    _ddSet('dd-rd-end', a.opex_rd_end_pct_rev);
    _ddSet('dd-sm-start', a.opex_sm_pct_rev);
    _ddSet('dd-sm-end', a.opex_sm_end_pct_rev);
    _ddSet('dd-ga-start', a.opex_ga_pct_rev);
    _ddSet('dd-ga-end', a.opex_ga_end_pct_rev);
    _ddSet('dd-da-pct', a.da_pct_rev);
    _ddSet('dd-exit-type', a.exit_multiple_type || 'ev_revenue');
    _ddSet('dd-exit-mult', a.exit_multiple);
    _ddSet('dd-discount', a.discount_rate_pct);
    _ddSet('dd-terminal-g', a.terminal_growth_pct);
    _ddSet('dd-tax', a.tax_rate_pct);
    _ddSet('dd-capex', a.capex_pct_rev);
    _ddSet('dd-nwc', a.nwc_pct_rev);
    _ddSet('dd-dilution-rounds', a.future_rounds || 2);
    _ddSet('dd-dilution-pct', a.dilution_per_round_pct || 20);
    // Fundraising plan
    _ddRenderFundraisingPlan(a.fundraising_plan || []);
}

function ddReadEditorToScenario() {
    return {
        time_to_launch_years: _ddNum('dd-time-to-launch', 0),
        projection_years: _ddNum('dd-proj-years', 10),
        revenue_y1_m: _ddNum('dd-revenue-y1', 1),
        revenue_cagr_pct: _ddNum('dd-revenue-cagr', 100),
        gross_margin_start_pct: _ddNum('dd-gm-start', 40),
        gross_margin_end_pct: _ddNum('dd-gm-end', 65),
        opex_rd_pct_rev: _ddNum('dd-rd-start', 50),
        opex_sm_pct_rev: _ddNum('dd-sm-start', 30),
        opex_ga_pct_rev: _ddNum('dd-ga-start', 15),
        opex_rd_end_pct_rev: _ddNum('dd-rd-end', 15),
        opex_sm_end_pct_rev: _ddNum('dd-sm-end', 12),
        opex_ga_end_pct_rev: _ddNum('dd-ga-end', 7),
        da_pct_rev: _ddNum('dd-da-pct', 3),
        capex_pct_rev: _ddNum('dd-capex', 5),
        nwc_pct_rev: _ddNum('dd-nwc', 10),
        tax_rate_pct: _ddNum('dd-tax', 0),
        terminal_growth_pct: _ddNum('dd-terminal-g', 3),
        discount_rate_pct: _ddNum('dd-discount', 25),
        exit_multiple_type: _ddVal('dd-exit-type') || 'ev_revenue',
        exit_multiple: _ddNum('dd-exit-mult', 10),
        scenario: _dd.currentScenario,
        fundraising_plan: _ddReadFundraisingPlan(),
    };
}

function _ddSet(id, val) { const el = document.getElementById(id); if (el) el.value = val ?? ''; }
function _ddVal(id) { const el = document.getElementById(id); return el ? el.value : ''; }
function _ddNum(id, def) { const v = parseFloat(_ddVal(id)); return isNaN(v) ? def : v; }

// ── Run Analysis ──────────────────────────────────────────────────
function ddRunAnalysis() {
    // Save current editor state to current scenario
    _dd.scenarios[_dd.currentScenario] = ddReadEditorToScenario();

    // Make sure all three scenarios exist (use defaults for missing)
    ['conservative', 'base', 'best_case'].forEach(s => {
        if (!_dd.scenarios[s]) {
            const stage = _dd.reportData?.inputs?.entry_stage || 'Seed';
            // We'll let the backend fill missing
        }
    });

    // Parse custom revenues
    const customRevenues = {};
    const crInput = _ddVal('dd-custom-rev').trim();
    if (crInput) {
        const revs = crInput.split(',').map(v => { const n = parseFloat(v.trim()); return isNaN(n) ? null : n; });
        customRevenues[_dd.currentScenario] = revs;
    }

    const inputs = _dd.reportData?.inputs || {};
    const payload = {
        report_id: _dd.reportId,
        entry_stage: inputs.entry_stage || 'Seed',
        check_size_m: inputs.check_size_millions || 2.0,
        pre_money_m: inputs.pre_money_millions || 15.0,
        round_size_m: inputs.round_size_m || null,
        exit_year: null,
        dilution_per_round_pct: _ddNum('dd-dilution-pct', 20),
        future_rounds: _ddNum('dd-dilution-rounds', 2),
        scenarios: _dd.scenarios,
        custom_revenues: Object.keys(customRevenues).length ? customRevenues : null,
        fundraising_plans: _ddBuildFundraisingPlans(),
    };

    const runBtn = document.getElementById('dd-run-btn');
    if (runBtn) { runBtn.disabled = true; runBtn.textContent = 'Computing...'; }

    _apiFetch('/api/dd/compute', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    })
    .then(r => r.json())
    .then(data => {
        if (data.status === 'ok') {
            _dd.results = data.result;
            ddSwitchScenario(_dd.currentScenario);
        } else {
            alert('Analysis failed: ' + (data.detail || 'Unknown error'));
        }
    })
    .catch(e => alert('Analysis error: ' + e.message))
    .finally(() => {
        if (runBtn) { runBtn.disabled = false; runBtn.textContent = 'Run Analysis'; }
    });
}

// ── Save Scenarios ────────────────────────────────────────────────
function ddSaveScenarios() {
    if (!_dd.reportId) return;
    // Capture current editor state
    _dd.scenarios[_dd.currentScenario] = ddReadEditorToScenario();

    const payload = {
        report_id: _dd.reportId,
        scenarios: _dd.scenarios,
        deal_params: {
            check_size_m: _dd.reportData?.inputs?.check_size_millions || 2.0,
            pre_money_m: _dd.reportData?.inputs?.pre_money_millions || 15.0,
        },
        notes: '',
    };

    _apiFetch('/api/dd/scenarios', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    })
    .then(r => r.json())
    .then(data => {
        if (data.status === 'ok') {
            const btn = document.getElementById('dd-save-btn');
            if (btn) { btn.textContent = 'Saved!'; setTimeout(() => btn.textContent = 'Save', 2000); }
        }
    }).catch(e => alert('Save failed: ' + e.message));
}

// ── Render Single Scenario Results ────────────────────────────────
function ddRenderScenarioResults(name) {
    const s = _dd.results.scenarios[name];
    if (!s) return;
    const pnl = s.pnl;
    const ret = s.returns;

    // Hero metrics
    const heroEl = document.getElementById('dd-hero');
    const moicClass = ret.moic >= 3 ? 'dd-positive' : ret.moic < 1 ? 'dd-negative' : '';
    const prelaunch = pnl.pre_launch_years || 0;
    const bridgeDelay = ret.bridge_delay_years || 0;
    const totalHold = ret.hold_years_total || (ret.exit_year + prelaunch);
    const holdParts = [];
    if (prelaunch > 0) holdParts.push(`${prelaunch}yr pre-launch`);
    if (bridgeDelay > 0) holdParts.push(`${bridgeDelay}yr bridge delay`);
    const holdSub = holdParts.length > 0 ? ` (${holdParts.join(' + ')})` : '';
    heroEl.innerHTML = `
        <div class="dd-hero-card dd-hero-highlight">
            <div class="dd-hero-label">Implied MOIC</div>
            <div class="dd-hero-value ${moicClass}">${ret.moic.toFixed(1)}x</div>
            <div class="dd-hero-sub">${totalHold}yr hold${holdSub}</div>
        </div>
        <div class="dd-hero-card">
            <div class="dd-hero-label">Implied IRR</div>
            <div class="dd-hero-value">${ret.irr_pct.toFixed(1)}%</div>
            ${bridgeDelay > 0 ? `<div class="dd-hero-sub dd-warn">bridge adds +${bridgeDelay}yr</div>` : ''}
        </div>
        <div class="dd-hero-card">
            <div class="dd-hero-label">Exit EV</div>
            <div class="dd-hero-value">$${_ddFmt(ret.exit_ev_m)}M</div>
            <div class="dd-hero-sub">${s.assumptions.exit_multiple_type === 'ev_ebitda' ? 'EV/EBITDA' : 'EV/Rev'} × ${s.assumptions.exit_multiple}</div>
        </div>
        <div class="dd-hero-card">
            <div class="dd-hero-label">DCF Value</div>
            <div class="dd-hero-value">$${_ddFmt(pnl.enterprise_value)}M</div>
        </div>
        <div class="dd-hero-card">
            <div class="dd-hero-label">Exit Revenue</div>
            <div class="dd-hero-value">$${_ddFmt(pnl.revenue[pnl.revenue.length - 1])}M</div>
        </div>
        <div class="dd-hero-card">
            <div class="dd-hero-label">Exit EBITDA Margin</div>
            <div class="dd-hero-value">${pnl.ebitda_margin_pct[pnl.ebitda_margin_pct.length - 1].toFixed(1)}%</div>
        </div>
        <div class="dd-hero-card">
            <div class="dd-hero-label">Entry Ownership</div>
            <div class="dd-hero-value">${ret.entry_ownership_pct.toFixed(1)}%</div>
            <div class="dd-hero-sub">→ ${ret.exit_ownership_pct.toFixed(1)}% at exit</div>
        </div>
    `;

    // Render dilution waterfall if schedule exists
    _ddRenderDilutionWaterfall(ret);

    // P&L table
    ddRenderPnlTable(pnl, 'dd-pnl-table');

    // Charts
    ddRenderScenarioCharts(pnl, name);
}

function ddRenderPnlTable(pnl, tableId) {
    const tbl = document.getElementById(tableId);
    if (!tbl) return;
    const yrs = pnl.years;
    const prelaunch = pnl.pre_launch_years || 0;
    const hdr = '<tr><th>Line Item</th>' + yrs.map(y => {
        if (y <= prelaunch) return `<th class="dd-prelaunch-hdr">Pre-L ${y}</th>`;
        return `<th>Yr ${y - prelaunch}</th>`;
    }).join('') + '</tr>';
    const rows = [
        { label: 'Revenue', data: pnl.revenue, cls: 'dd-row-header' },
        { label: 'COGS', data: pnl.cogs, neg: true },
        { label: 'Gross Profit', data: pnl.gross_profit, cls: 'dd-row-subtotal' },
        { label: 'Gross Margin %', data: pnl.gross_margin_pct, pct: true },
        { label: 'R&D', data: pnl.opex_rd, neg: true },
        { label: 'Sales & Marketing', data: pnl.opex_sm, neg: true },
        { label: 'G&A', data: pnl.opex_ga, neg: true },
        { label: 'EBITDA', data: pnl.ebitda, cls: 'dd-row-subtotal' },
        { label: 'EBITDA Margin %', data: pnl.ebitda_margin_pct, pct: true },
        { label: 'D&A', data: pnl.da, neg: true },
        { label: 'EBIT', data: pnl.ebit },
        { label: 'Taxes', data: pnl.taxes, neg: true },
        { label: 'Net Income', data: pnl.net_income, cls: 'dd-row-subtotal' },
        { label: 'CapEx', data: pnl.capex, neg: true },
        { label: 'Δ Working Capital', data: pnl.delta_nwc, neg: true },
        { label: 'Free Cash Flow', data: pnl.fcf, cls: 'dd-row-total' },
    ];

    let html = '<thead>' + hdr + '</thead><tbody>';
    for (const row of rows) {
        const cls = row.cls ? ` class="${row.cls}"` : '';
        html += `<tr${cls}><td>${row.label}</td>`;
        for (const val of row.data) {
            const isNeg = val < 0;
            const cellCls = row.pct ? 'dd-pct' : (isNeg ? 'dd-neg' : '');
            const display = row.pct ? `${val.toFixed(1)}%` : _ddFmt(val);
            html += `<td class="${cellCls}">${display}</td>`;
        }
        html += '</tr>';
    }
    html += '</tbody>';
    tbl.innerHTML = html;
}

function _ddFmt(v) {
    if (v == null || isNaN(v)) return '-';
    const abs = Math.abs(v);
    if (abs >= 1000) return (v < 0 ? '(' : '') + abs.toFixed(0).replace(/\B(?=(\d{3})+(?!\d))/g, ',') + (v < 0 ? ')' : '');
    if (abs >= 10) return v.toFixed(1);
    if (abs >= 1) return v.toFixed(2);
    return v.toFixed(3);
}

// ── Scenario Charts ───────────────────────────────────────────────
function ddRenderScenarioCharts(pnl, name) {
    const prelaunch = pnl.pre_launch_years || 0;
    const labels = pnl.years.map(y => y <= prelaunch ? `Pre-L ${y}` : `Yr ${y - prelaunch}`);
    const base = { tension: 0.3, pointRadius: 3, borderWidth: 2 };

    // Destroy old charts
    Object.values(_dd.charts).forEach(c => { try { c.destroy(); } catch(e) {} });
    _dd.charts = {};

    // Revenue & EBITDA
    _dd.charts.revEbitda = new Chart(document.getElementById('dd-chart-rev-ebitda'), {
        type: 'line', data: {
            labels,
            datasets: [
                { ...base, label: 'Revenue', data: pnl.revenue, borderColor: VOLO.green, backgroundColor: VOLO.greenLight },
                { ...base, label: 'EBITDA', data: pnl.ebitda, borderColor: VOLO.blueSteel, backgroundColor: VOLO.blueSteelLight },
            ]
        },
        options: _ddChartOpts('$M'),
    });

    // Margins
    _dd.charts.margins = new Chart(document.getElementById('dd-chart-margins'), {
        type: 'line', data: {
            labels,
            datasets: [
                { ...base, label: 'Gross Margin %', data: pnl.gross_margin_pct, borderColor: VOLO.green },
                { ...base, label: 'EBITDA Margin %', data: pnl.ebitda_margin_pct, borderColor: VOLO.blueSteel },
            ]
        },
        options: _ddChartOpts('%'),
    });

    // FCF
    _dd.charts.fcf = new Chart(document.getElementById('dd-chart-fcf'), {
        type: 'bar', data: {
            labels,
            datasets: [{
                label: 'Free Cash Flow',
                data: pnl.fcf,
                backgroundColor: pnl.fcf.map(v => v >= 0 ? 'rgba(91,119,68,0.6)' : 'rgba(220,53,69,0.5)'),
                borderColor: pnl.fcf.map(v => v >= 0 ? 'rgba(91,119,68,1)' : 'rgba(220,53,69,0.8)'),
                borderWidth: 1,
            }]
        },
        options: _ddChartOpts('$M'),
    });

    // OpEx breakdown
    const totalRev = pnl.revenue;
    _dd.charts.opex = new Chart(document.getElementById('dd-chart-opex'), {
        type: 'bar', data: {
            labels,
            datasets: [
                { label: 'R&D %', data: pnl.opex_rd.map((v, i) => totalRev[i] > 0 ? (v / totalRev[i] * 100) : 0), backgroundColor: 'rgba(91,119,68,0.7)' },
                { label: 'S&M %', data: pnl.opex_sm.map((v, i) => totalRev[i] > 0 ? (v / totalRev[i] * 100) : 0), backgroundColor: 'rgba(157,181,196,0.7)' },
                { label: 'G&A %', data: pnl.opex_ga.map((v, i) => totalRev[i] > 0 ? (v / totalRev[i] * 100) : 0), backgroundColor: 'rgba(44,62,80,0.4)' },
            ]
        },
        options: { ..._ddChartOpts('%'), scales: { x: { stacked: true }, y: { stacked: true, title: { display: true, text: '% of Revenue' } } } },
    });
}

function _ddChartOpts(yLabel) {
    return {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { position: 'top', labels: { font: { size: 11 } } } },
        scales: { y: { title: { display: true, text: yLabel } } },
    };
}

// ── Comparison View ───────────────────────────────────────────────
function ddRenderComparison() {
    if (!_dd.results) return;
    const comp = _dd.results.comparison;
    const scenarios = comp.scenarios;

    // Hero comparison
    const heroEl = document.getElementById('dd-comp-hero');
    let heroHtml = '';
    for (const s of scenarios) {
        const label = s === 'conservative' ? 'Conservative' : s === 'base' ? 'Base Case' : 'Best Case';
        const moic = comp.moic[s] || 0;
        const bridgeD = comp.bridge_delay[s] || 0;
        const holdYrs = comp.hold_years[s] || 0;
        const cls = moic >= 3 ? 'dd-positive' : moic < 1 ? 'dd-negative' : '';
        heroHtml += `
            <div class="dd-hero-card">
                <div class="dd-hero-label">${label} MOIC</div>
                <div class="dd-hero-value ${cls}">${moic.toFixed(1)}x</div>
                <div class="dd-hero-sub">IRR: ${(comp.irr[s] || 0).toFixed(1)}% · ${holdYrs}yr hold${bridgeD > 0 ? ` (${bridgeD}yr bridge)` : ''}</div>
            </div>`;
    }
    heroEl.innerHTML = heroHtml;

    // Comparison charts
    Object.values(_dd.charts).forEach(c => { try { c.destroy(); } catch(e) {} });
    _dd.charts = {};

    const colors = { conservative: '#dc2626', base: '#5b7744', best_case: '#1e40af' };
    const labels_map = { conservative: 'Conservative', base: 'Base Case', best_case: 'Best Case' };

    // Get year labels from first available scenario
    const firstKey = scenarios[0];
    const yrs = _dd.results.scenarios[firstKey]?.pnl?.years || [];
    const yearLabels = yrs.map(y => `Yr ${y}`);

    // Revenue comparison
    const revDatasets = scenarios.map(s => ({
        label: labels_map[s],
        data: _dd.results.scenarios[s]?.pnl?.revenue || [],
        borderColor: colors[s],
        backgroundColor: colors[s] + '22',
        tension: 0.3, pointRadius: 3, borderWidth: 2, fill: true,
    }));
    _dd.charts.compRev = new Chart(document.getElementById('dd-chart-comp-rev'), {
        type: 'line', data: { labels: yearLabels, datasets: revDatasets },
        options: _ddChartOpts('Revenue ($M)'),
    });

    // EBITDA comparison
    const ebitdaDatasets = scenarios.map(s => ({
        label: labels_map[s],
        data: _dd.results.scenarios[s]?.pnl?.ebitda || [],
        borderColor: colors[s],
        backgroundColor: colors[s] + '22',
        tension: 0.3, pointRadius: 3, borderWidth: 2, fill: true,
    }));
    _dd.charts.compEbitda = new Chart(document.getElementById('dd-chart-comp-ebitda'), {
        type: 'line', data: { labels: yearLabels, datasets: ebitdaDatasets },
        options: _ddChartOpts('EBITDA ($M)'),
    });

    // MOIC bar chart
    _dd.charts.compMoic = new Chart(document.getElementById('dd-chart-comp-moic'), {
        type: 'bar', data: {
            labels: scenarios.map(s => labels_map[s]),
            datasets: [{ label: 'MOIC', data: scenarios.map(s => comp.moic[s] || 0),
                backgroundColor: scenarios.map(s => colors[s] + 'aa'), borderColor: scenarios.map(s => colors[s]), borderWidth: 1 }]
        },
        options: { ..._ddChartOpts('MOIC (x)'), plugins: { legend: { display: false } } },
    });

    // IRR bar chart
    _dd.charts.compIrr = new Chart(document.getElementById('dd-chart-comp-irr'), {
        type: 'bar', data: {
            labels: scenarios.map(s => labels_map[s]),
            datasets: [{ label: 'IRR %', data: scenarios.map(s => comp.irr[s] || 0),
                backgroundColor: scenarios.map(s => colors[s] + 'aa'), borderColor: scenarios.map(s => colors[s]), borderWidth: 1 }]
        },
        options: { ..._ddChartOpts('IRR (%)'), plugins: { legend: { display: false } } },
    });

    // Comparison summary table
    const tbl = document.getElementById('dd-comp-table');
    if (tbl) {
        let html = '<thead><tr><th>Metric</th>';
        for (const s of scenarios) html += `<th>${labels_map[s]}</th>`;
        html += '</tr></thead><tbody>';

        const metrics = [
            ['Exit Revenue ($M)', s => _ddFmt(comp.exit_year_revenue[s])],
            ['Exit EBITDA ($M)', s => _ddFmt(comp.exit_year_ebitda[s])],
            ['Exit EBITDA Margin', s => (comp.exit_year_ebitda_margin[s] || 0).toFixed(1) + '%'],
            ['Exit EV ($M)', s => _ddFmt(comp.exit_ev[s])],
            ['DCF EV ($M)', s => _ddFmt(comp.dcf_ev[s])],
            ['MOIC', s => (comp.moic[s] || 0).toFixed(1) + 'x'],
            ['IRR', s => (comp.irr[s] || 0).toFixed(1) + '%'],
            ['Exit Ownership', s => (comp.exit_ownership[s] || 0).toFixed(1) + '%'],
            ['Hold Period (yr)', s => (comp.hold_years[s] || 0).toFixed(1)],
            ['Bridge Delay (yr)', s => (comp.bridge_delay[s] || 0).toFixed(1)],
        ];

        for (const [label, fn] of metrics) {
            html += `<tr><td>${label}</td>`;
            for (const s of scenarios) html += `<td>${fn(s)}</td>`;
            html += '</tr>';
        }
        html += '</tbody>';
        tbl.innerHTML = html;
    }
}

// ── Info-tip injection for DD labels ──────────────────────────────
// ── Fundraising Plan UI ───────────────────────────────────────────
function _ddRenderFundraisingPlan(plan) {
    const container = document.getElementById('dd-fundraising-rounds');
    if (!container) return;
    container.innerHTML = '';
    if (!plan || plan.length === 0) {
        plan = [{ label: 'Series A', year: 2, dilution_pct: 20, needs_bridge: false, bridge_dilution_pct: 8, bridge_delay_years: 0.5 }];
    }
    plan.forEach((rnd, idx) => {
        container.appendChild(_ddCreateRoundRow(rnd, idx));
    });
    _ddReindexRounds();
}

function _ddCreateRoundRow(rnd, idx) {
    const div = document.createElement('div');
    div.className = 'dd-fundraising-round';
    div.dataset.roundIdx = idx;
    const bridgeChecked = rnd.needs_bridge ? 'checked' : '';
    const bridgeVis = rnd.needs_bridge ? '' : 'style="display:none"';
    div.innerHTML = `
        <div class="dd-round-header">
            <span class="dd-round-num">#${idx + 1}</span>
            <button class="dd-round-remove" title="Remove round" onclick="_ddRemoveRound(${idx})">×</button>
        </div>
        <div class="dd-field-row">
            <div class="dd-field dd-field-sm">
                <label data-tip="dd_round_label">Round</label>
                <input type="text" class="dd-fp-label" value="${rnd.label || 'Round'}" placeholder="e.g. Series A">
            </div>
            <div class="dd-field dd-field-sm">
                <label data-tip="dd_round_year">Year</label>
                <input type="number" class="dd-fp-year" value="${rnd.year || 2}" step="1" min="1" max="15">
            </div>
            <div class="dd-field dd-field-sm">
                <label data-tip="dd_round_dilution">Dilution %</label>
                <input type="number" class="dd-fp-dilution" value="${rnd.dilution_pct || 20}" step="1" min="0" max="50">
            </div>
        </div>
        <div class="dd-field-row dd-bridge-row">
            <div class="dd-field dd-field-sm">
                <label data-tip="dd_needs_bridge">
                    <input type="checkbox" class="dd-fp-bridge-toggle" ${bridgeChecked} onchange="_ddToggleBridge(this)"> Needs Bridge
                </label>
            </div>
            <div class="dd-bridge-fields" ${bridgeVis}>
                <div class="dd-field dd-field-sm">
                    <label data-tip="dd_bridge_dilution">Bridge Dilution %</label>
                    <input type="number" class="dd-fp-bridge-dilution" value="${rnd.bridge_dilution_pct || 8}" step="1" min="0" max="30">
                </div>
                <div class="dd-field dd-field-sm">
                    <label data-tip="dd_bridge_delay">Bridge Delay (yr)</label>
                    <input type="number" class="dd-fp-bridge-delay" value="${rnd.bridge_delay_years || 0.5}" step="0.1" min="0" max="3">
                </div>
            </div>
        </div>
    `;
    return div;
}

function _ddToggleBridge(checkbox) {
    const row = checkbox.closest('.dd-fundraising-round');
    const fields = row.querySelector('.dd-bridge-fields');
    if (fields) fields.style.display = checkbox.checked ? '' : 'none';
}

function _ddRemoveRound(idx) {
    const container = document.getElementById('dd-fundraising-rounds');
    if (!container) return;
    const rows = container.querySelectorAll('.dd-fundraising-round');
    if (rows.length <= 1) return; // keep at least one
    if (rows[idx]) rows[idx].remove();
    _ddReindexRounds();
}

function _ddReindexRounds() {
    const container = document.getElementById('dd-fundraising-rounds');
    if (!container) return;
    container.querySelectorAll('.dd-fundraising-round').forEach((row, i) => {
        row.dataset.roundIdx = i;
        const num = row.querySelector('.dd-round-num');
        if (num) num.textContent = '#' + (i + 1);
        const removeBtn = row.querySelector('.dd-round-remove');
        if (removeBtn) removeBtn.setAttribute('onclick', `_ddRemoveRound(${i})`);
    });
    // Re-inject info-tips for new round rows
    _ddInjectInfoTips();
}

function _ddAddFundraisingRound() {
    const container = document.getElementById('dd-fundraising-rounds');
    if (!container) return;
    const count = container.querySelectorAll('.dd-fundraising-round').length;
    const labels = ['Series A', 'Series B', 'Series C', 'Series D', 'Series E'];
    const rnd = {
        label: labels[Math.min(count, labels.length - 1)],
        year: (count + 1) * 2,
        dilution_pct: 18,
        needs_bridge: false,
        bridge_dilution_pct: 8,
        bridge_delay_years: 0.5,
    };
    container.appendChild(_ddCreateRoundRow(rnd, count));
    _ddReindexRounds();
}

function _ddReadFundraisingPlan() {
    const container = document.getElementById('dd-fundraising-rounds');
    if (!container) return [];
    const rounds = [];
    container.querySelectorAll('.dd-fundraising-round').forEach(row => {
        const label = (row.querySelector('.dd-fp-label') || {}).value || 'Round';
        const year = parseInt((row.querySelector('.dd-fp-year') || {}).value) || 2;
        const dilution = parseFloat((row.querySelector('.dd-fp-dilution') || {}).value) || 20;
        const needsBridge = (row.querySelector('.dd-fp-bridge-toggle') || {}).checked || false;
        const bridgeDilution = parseFloat((row.querySelector('.dd-fp-bridge-dilution') || {}).value) || 8;
        const bridgeDelay = parseFloat((row.querySelector('.dd-fp-bridge-delay') || {}).value) || 0.5;
        rounds.push({
            label: label,
            year: year,
            dilution_pct: dilution,
            needs_bridge: needsBridge,
            bridge_dilution_pct: bridgeDilution,
            bridge_delay_years: bridgeDelay,
        });
    });
    return rounds;
}

function _ddBuildFundraisingPlans() {
    // Build fundraising plans dict from each scenario's assumptions
    const plans = {};
    let hasPlan = false;
    for (const [name, assumptions] of Object.entries(_dd.scenarios)) {
        if (assumptions.fundraising_plan && assumptions.fundraising_plan.length > 0) {
            plans[name] = assumptions.fundraising_plan;
            hasPlan = true;
        }
    }
    return hasPlan ? plans : null;
}

// ── Dilution Waterfall Renderer ──────────────────────────────────
function _ddRenderDilutionWaterfall(ret) {
    const panel = document.getElementById('dd-dilution-panel');
    const container = document.getElementById('dd-dilution-waterfall');
    if (!panel || !container) return;
    const schedule = ret.dilution_schedule;
    if (!schedule || schedule.length === 0) {
        panel.style.display = 'none';
        return;
    }
    panel.style.display = '';
    let html = '<div class="dd-waterfall-chart">';
    // Entry ownership bar
    html += `<div class="dd-wf-step">
        <div class="dd-wf-label">Entry</div>
        <div class="dd-wf-bar-container">
            <div class="dd-wf-bar dd-wf-entry" style="width:${Math.min(ret.entry_ownership_pct, 100)}%">
                ${ret.entry_ownership_pct.toFixed(1)}%
            </div>
        </div>
    </div>`;
    // Each dilution event
    for (const evt of schedule) {
        const isbridge = evt.type === 'bridge';
        const barClass = isbridge ? 'dd-wf-bridge' : 'dd-wf-priced';
        const label = evt.label + (isbridge ? '' : (evt.year ? ` (Yr ${evt.year})` : ''));
        const dilNote = `-${evt.dilution_pct}%${isbridge ? ` +${evt.delay_years}yr` : ''}`;
        html += `<div class="dd-wf-step">
            <div class="dd-wf-label">${label}<span class="dd-wf-dil">${dilNote}</span></div>
            <div class="dd-wf-bar-container">
                <div class="dd-wf-bar ${barClass}" style="width:${Math.min(evt.ownership_after_pct * (100 / ret.entry_ownership_pct), 100)}%">
                    ${evt.ownership_after_pct.toFixed(1)}%
                </div>
            </div>
        </div>`;
    }
    // Final ownership
    html += `<div class="dd-wf-step dd-wf-final">
        <div class="dd-wf-label">Exit Ownership</div>
        <div class="dd-wf-bar-container">
            <div class="dd-wf-bar dd-wf-exit" style="width:${Math.min(ret.exit_ownership_pct * (100 / ret.entry_ownership_pct), 100)}%">
                ${ret.exit_ownership_pct.toFixed(1)}%
            </div>
        </div>
    </div>`;
    html += '</div>';
    if (ret.bridge_delay_years > 0) {
        html += `<p class="dd-wf-note">Bridge financing adds <strong>${ret.bridge_delay_years} year(s)</strong> to the hold period, extending total hold to <strong>${ret.hold_years_total} years</strong>.</p>`;
    }
    container.innerHTML = html;
}

function _ddInjectInfoTips() {
    document.querySelectorAll('#tab-ddanalysis label[data-tip]').forEach(label => {
        const key = label.getAttribute('data-tip');
        if (key && !label.querySelector('.info-tip')) {
            label.insertAdjacentHTML('beforeend', ' ' + infoTip(key));
        }
    });
}

// ── Tab init hook ─────────────────────────────────────────────────
// Hook into the nav click to load reports when DD tab is activated
document.addEventListener('DOMContentLoaded', () => {
    // Inject info-tips into DD labels once DOM is ready
    _ddInjectInfoTips();

    const ddLink = document.querySelector('.nav-link[data-tab="ddanalysis"]');
    if (ddLink) {
        ddLink.addEventListener('click', () => {
            setTimeout(() => { ddLoadReports(); }, 50);
        });
    }

    // Wire up Add Round button
    const addRoundBtn = document.getElementById('dd-add-round');
    if (addRoundBtn) {
        addRoundBtn.addEventListener('click', (e) => {
            e.preventDefault();
            _ddAddFundraisingRound();
        });
    }
});

// ════════════════════════════════════════════════════════════════════════════
// FUND II DEPLOYMENT TAB
// ════════════════════════════════════════════════════════════════════════════

const _fdCharts = {};
let _fdCompanies = [];
let _fdQuarters = [];
let _fdFund = {};

// ── Color palettes ───────────────────────────────────────────────────────────
const FD_VERTICAL_COLORS = {
    'Energy':         '#5b7744',
    'Buildings':      '#3b82f6',
    'AI':             '#8b5cf6',
    'Transportation': '#f59e0b',
    'Agriculture':    '#10b981',
    'Industrial':     '#ef4444',
    'Water':          '#06b6d4',
    'Other':          '#9ca3af',
    'Unassigned':     '#e5e7eb',
};
const FD_TYPE_COLORS = {
    'Software':    '#3b82f6',
    'Hardware':    '#f59e0b',
    'HE-SaaS':     '#5b7744',
    'Unassigned':  '#e5e7eb',
};

// ── Init ─────────────────────────────────────────────────────────────────────
async function fdInit() {
    try {
        const [configRes, companiesRes, summaryRes] = await Promise.all([
            apiCall('/api/fund/config'),
            apiCall('/api/fund/companies'),
            apiCall('/api/fund/summary'),
        ]);
        _fdFund = configRes.fund;
        _fdQuarters = configRes.quarters;
        _fdCompanies = companiesRes.companies;
        _fdRenderKPIs(summaryRes);
        _fdRenderCompanyTable();
        _fdRenderCharts();
    } catch(e) {
        console.error('Fund II init failed:', e);
    }
}

function fdRefresh() { fdInit(); }

// ── KPI Strip ────────────────────────────────────────────────────────────────
function _fdRenderKPIs(s) {
    const set = (id, val, cls) => {
        const el = document.getElementById(id);
        if (!el) return;
        el.textContent = val;
        if (cls) el.className = 'fd-kpi-value ' + cls;
    };
    set('fd-kpi-aum', `$${_fdFund.aum_m}M`);
    set('fd-kpi-target', _fdFund.target_company_count);
    set('fd-kpi-deployed', `$${s.actual_nic_m.toFixed(1)}M`);
    const pct = s.pct_nic_deployed;
    const pctCls = pct >= 80 ? 'fd-positive' : pct >= 50 ? '' : 'fd-warn';
    set('fd-kpi-pct', `${pct}%`, pctCls);
    const gap = s.deal_gap;
    set('fd-kpi-deal-gap', `${s.actual_deals} / ${Math.round(s.plan_deals)} (${gap > 0 ? '-' : '+'}${Math.abs(gap).toFixed(1)})`, gap > 0.5 ? 'fd-negative' : 'fd-positive');
    const nicGap = s.nic_gap_m;
    set('fd-kpi-nic-gap', `$${s.actual_nic_m.toFixed(1)}M / $${s.plan_nic_m.toFixed(1)}M`, nicGap > 5 ? 'fd-negative' : 'fd-positive');
    set('fd-kpi-rc', `$${s.actual_rc_deployed_m.toFixed(1)}M`);
    set('fd-kpi-quarter', `Q${s.current_quarter} (${s.current_quarter_label})`);
}

// ── Charts ───────────────────────────────────────────────────────────────────
function _fdDestroyChart(key) {
    if (_fdCharts[key]) { _fdCharts[key].destroy(); delete _fdCharts[key]; }
}

function _fdRenderCharts() {
    _fdRenderCadenceChart();
    _fdRenderCumulativeChart();
    _fdRenderDealCountChart();
    _fdRenderSectorCharts();
}

function _fdRenderCadenceChart() {
    _fdDestroyChart('cadence');
    const ctx = document.getElementById('fd-chart-cadence');
    if (!ctx) return;
    const labels = _fdQuarters.map(q => q.label);
    const planned = _fdQuarters.map(q => q.planned_nic_m);
    const actual = _fdQuarters.map(q => q.actual_nic_m);
    const updated = _fdQuarters.map(q => q.updated_nic_m);
    _fdCharts.cadence = new Chart(ctx, {
        type: 'bar',
        data: {
            labels,
            datasets: [
                { label: 'Planned', data: planned, backgroundColor: 'rgba(59,130,246,0.6)', borderColor: '#3b82f6', borderWidth: 1, borderRadius: 3 },
                { label: 'Actual', data: actual, backgroundColor: 'rgba(91,119,68,0.85)', borderColor: '#5b7744', borderWidth: 1, borderRadius: 3 },
                { label: 'Updated Plan', data: updated, backgroundColor: 'rgba(217,119,6,0.5)', borderColor: '#d97706', borderWidth: 1.5, borderRadius: 3, borderDash: [4,4] },
            ]
        },
        options: {
            responsive: true,
            plugins: { legend: { display: false }, tooltip: { callbacks: { label: ctx => `${ctx.dataset.label}: $${(ctx.raw||0).toFixed(2)}M` } } },
            scales: {
                x: { grid: { display: false }, ticks: { font: { size: 10 }, maxRotation: 45 } },
                y: { grid: { color: 'rgba(0,0,0,0.05)' }, ticks: { callback: v => `$${v}M`, font: { size: 10 } }, beginAtZero: true },
            }
        }
    });
}

function _fdRenderCumulativeChart() {
    _fdDestroyChart('cumulative');
    const ctx = document.getElementById('fd-chart-cumulative');
    if (!ctx) return;
    const labels = _fdQuarters.map(q => q.label);
    let cumPlan = 0, cumActual = 0, cumUpdated = 0;
    const planData = [], actualData = [], updatedData = [];
    for (const q of _fdQuarters) {
        cumPlan += q.planned_nic_m || 0;
        planData.push(parseFloat(cumPlan.toFixed(2)));
        if (q.actual_nic_m !== null) { cumActual += q.actual_nic_m || 0; actualData.push(parseFloat(cumActual.toFixed(2))); }
        else actualData.push(null);
        if (q.updated_nic_m !== null) { cumUpdated += q.updated_nic_m || 0; updatedData.push(parseFloat(cumUpdated.toFixed(2))); }
        else updatedData.push(null);
    }
    _fdCharts.cumulative = new Chart(ctx, {
        type: 'line',
        data: {
            labels,
            datasets: [
                { label: 'Planned', data: planData, borderColor: '#3b82f6', backgroundColor: 'rgba(59,130,246,0.08)', fill: true, tension: 0.3, pointRadius: 2 },
                { label: 'Actual', data: actualData, borderColor: '#5b7744', backgroundColor: 'rgba(91,119,68,0.08)', fill: false, tension: 0.3, pointRadius: 3, spanGaps: false },
                { label: 'Updated Plan', data: updatedData, borderColor: '#d97706', borderDash: [5,5], fill: false, tension: 0.3, pointRadius: 2, spanGaps: false },
            ]
        },
        options: {
            responsive: true,
            plugins: { legend: { labels: { font: { size: 10 } } }, tooltip: { callbacks: { label: ctx => `${ctx.dataset.label}: $${(ctx.raw||0).toFixed(1)}M` } } },
            scales: {
                x: { grid: { display: false }, ticks: { font: { size: 9 }, maxRotation: 45 } },
                y: { grid: { color: 'rgba(0,0,0,0.05)' }, ticks: { callback: v => `$${v}M`, font: { size: 10 } }, beginAtZero: true },
            }
        }
    });
}

function _fdRenderDealCountChart() {
    _fdDestroyChart('deals');
    const ctx = document.getElementById('fd-chart-deals');
    if (!ctx) return;
    const labels = _fdQuarters.map(q => q.label);
    let cumPlan = 0, cumActual = 0;
    const planData = [], actualData = [];
    for (const q of _fdQuarters) {
        cumPlan += q.planned_ni || 0;
        planData.push(parseFloat(cumPlan.toFixed(1)));
        if (q.actual_ni !== null) { cumActual += q.actual_ni || 0; actualData.push(parseFloat(cumActual.toFixed(1))); }
        else actualData.push(null);
    }
    _fdCharts.deals = new Chart(ctx, {
        type: 'line',
        data: {
            labels,
            datasets: [
                { label: 'Planned Cumulative Deals', data: planData, borderColor: '#3b82f6', borderDash: [5,5], fill: false, tension: 0.2, pointRadius: 2 },
                { label: 'Actual Cumulative Deals', data: actualData, borderColor: '#5b7744', backgroundColor: 'rgba(91,119,68,0.1)', fill: true, tension: 0.2, pointRadius: 3, spanGaps: false },
            ]
        },
        options: {
            responsive: true,
            plugins: { legend: { labels: { font: { size: 10 } } } },
            scales: {
                x: { grid: { display: false }, ticks: { font: { size: 9 }, maxRotation: 45 } },
                y: { grid: { color: 'rgba(0,0,0,0.05)' }, ticks: { stepSize: 2, font: { size: 10 } }, beginAtZero: true },
            }
        }
    });
}

function _fdRenderSectorCharts() {
    // Tally capital by vertical and type — only assigned NIC entries (exclude reserves and "?")
    const vertMap = {}, typeMap = {};
    for (const c of _fdCompanies) {
        if (c.is_reserve) continue;                         // Exclude reserve follow-ons
        const nic = parseFloat(c.nic_m) || 0;
        if (nic <= 0) continue;                             // Must have capital deployed
        const vert = c.vertical && c.vertical !== '?' ? c.vertical : 'Unassigned';
        const type = c.type && c.type !== '?' ? c.type : 'Unassigned';
        vertMap[vert] = (vertMap[vert] || 0) + nic;
        typeMap[type] = (typeMap[type] || 0) + nic;
    }

    // Vertical donut
    _fdDestroyChart('vertical');
    const ctxV = document.getElementById('fd-chart-vertical');
    if (ctxV) {
        const vLabels = Object.keys(vertMap);
        const vData = vLabels.map(k => parseFloat(vertMap[k].toFixed(2)));
        const vColors = vLabels.map(k => FD_VERTICAL_COLORS[k] || '#9ca3af');
        _fdCharts.vertical = new Chart(ctxV, {
            type: 'doughnut',
            data: { labels: vLabels, datasets: [{ data: vData, backgroundColor: vColors, borderWidth: 2, borderColor: '#fff' }] },
            options: { responsive: true, cutout: '60%', plugins: { legend: { display: false }, tooltip: { callbacks: { label: ctx => `${ctx.label}: $${ctx.raw.toFixed(1)}M (${Math.round(ctx.parsed / vData.reduce((a,b)=>a+b,0) * 100)}%)` } } } }
        });
        const legend = document.getElementById('fd-vertical-legend');
        if (legend) legend.innerHTML = vLabels.map((l, i) => `<div class="fd-pie-legend-item"><div class="fd-pie-legend-color" style="background:${vColors[i]}"></div>${l}: $${vData[i].toFixed(1)}M</div>`).join('');
    }

    // Type donut
    _fdDestroyChart('type');
    const ctxT = document.getElementById('fd-chart-type');
    if (ctxT) {
        const tLabels = Object.keys(typeMap);
        const tData = tLabels.map(k => parseFloat(typeMap[k].toFixed(2)));
        const tColors = tLabels.map(k => FD_TYPE_COLORS[k] || '#9ca3af');
        _fdCharts.type = new Chart(ctxT, {
            type: 'doughnut',
            data: { labels: tLabels, datasets: [{ data: tData, backgroundColor: tColors, borderWidth: 2, borderColor: '#fff' }] },
            options: { responsive: true, cutout: '60%', plugins: { legend: { display: false }, tooltip: { callbacks: { label: ctx => `${ctx.label}: $${ctx.raw.toFixed(1)}M (${Math.round(ctx.parsed / tData.reduce((a,b)=>a+b,0) * 100)}%)` } } } }
        });
        const legend = document.getElementById('fd-type-legend');
        if (legend) legend.innerHTML = tLabels.map((l, i) => `<div class="fd-pie-legend-item"><div class="fd-pie-legend-color" style="background:${tColors[i]}"></div>${l}: $${tData[i].toFixed(1)}M</div>`).join('');
    }

    // Software / Hardware / HE-SaaS grouped bar
    _fdDestroyChart('shs');
    const ctxS = document.getElementById('fd-chart-shs');
    if (ctxS) {
        const shsTypes = ['Software', 'Hardware', 'HE-SaaS'];
        const shsData = shsTypes.map(t => parseFloat((typeMap[t] || 0).toFixed(2)));
        const shsColors = shsTypes.map(t => FD_TYPE_COLORS[t]);
        _fdCharts.shs = new Chart(ctxS, {
            type: 'bar',
            data: {
                labels: shsTypes,
                datasets: [{ label: 'Capital Deployed ($M)', data: shsData, backgroundColor: shsColors, borderRadius: 6, borderWidth: 0 }]
            },
            options: {
                responsive: true,
                plugins: { legend: { display: false }, tooltip: { callbacks: { label: ctx => `$${ctx.raw.toFixed(1)}M` } } },
                scales: {
                    x: { grid: { display: false }, ticks: { font: { size: 11, weight: '600' } } },
                    y: { grid: { color: 'rgba(0,0,0,0.05)' }, ticks: { callback: v => `$${v}M`, font: { size: 10 } }, beginAtZero: true },
                }
            }
        });
    }
}

// ── Company Table ─────────────────────────────────────────────────────────────
const FD_VERTICALS = ['Energy', 'Buildings', 'AI', 'Transportation', 'Agriculture', 'Industrial', 'Water', 'Other'];
const FD_TYPES = ['Software', 'Hardware', 'HE-SaaS'];
const FD_UNASSIGNED = '?';

function _fdRenderCompanyTable() {
    const tbody = document.getElementById('fd-company-tbody');
    if (!tbody) return;
    tbody.innerHTML = '';

    // Split into new investments (nic) and reserve follow-ons
    const newInvest = _fdCompanies.filter(c => !c.is_reserve);
    const reserves  = _fdCompanies.filter(c => c.is_reserve);

    const renderGroup = (list, startIdx, isReserve) => {
        if (isReserve && list.length) {
            // Section separator row
            const sep = document.createElement('tr');
            sep.innerHTML = `<td colspan="7" style="background:#f8f9fa;font-size:0.7rem;font-weight:700;color:#6b7280;text-transform:uppercase;letter-spacing:.05em;padding:6px 12px;border-top:2px solid #e1e4e8;">Reserve / Follow-on Capital</td>`;
            tbody.appendChild(sep);
        }
        list.forEach((c, localIdx) => {
            const i = startIdx + localIdx;
            const tr = document.createElement('tr');
            tr.dataset.idx = i;
            if (isReserve) tr.style.background = 'rgba(248,249,250,0.7)';

            // vertical options — include "?" as first option if unassigned
            const vertOpts = [
                (c.vertical === FD_UNASSIGNED ? `<option value="?" selected>— unassigned —</option>` : `<option value="?">— unassigned —</option>`),
                ...FD_VERTICALS.map(v => `<option value="${v}" ${c.vertical===v?'selected':''}>${v}</option>`)
            ].join('');
            const typeOpts = [
                (c.type === FD_UNASSIGNED ? `<option value="?" selected>— unassigned —</option>` : `<option value="?">— unassigned —</option>`),
                ...FD_TYPES.map(t => `<option value="${t}" ${c.type===t?'selected':''}>${t}</option>`)
            ].join('');

            // NIC or RC column
            const capitalLabel = isReserve ? 'RC ($M)' : 'NIC ($M)';
            const capitalVal = isReserve ? (c.rc_m || 0) : (c.nic_m || 0);
            const capitalField = isReserve ? 'rc_m' : 'nic_m';

            // Highlight unassigned cells
            const vertStyle = c.vertical === FD_UNASSIGNED ? 'border-color:#f59e0b;background:#fffbeb;' : '';
            const typeStyle = c.type === FD_UNASSIGNED ? 'border-color:#f59e0b;background:#fffbeb;' : '';

            tr.innerHTML = `
              <td style="color:#6b7280;font-size:0.75rem;">${c.label || ('Q' + c.quarter)}</td>
              <td><input type="text" value="${c.name}" style="min-width:140px${c.name === 'TBD' ? ';border-color:#f59e0b;background:#fffbeb;' : ''}" onchange="_fdTableChange(${i},'name',this.value)"></td>
              <td><input type="number" step="0.01" min="0" value="${capitalVal.toFixed(2)}" style="width:70px" onchange="_fdTableChange(${i},'${capitalField}',parseFloat(this.value)||0); _fdRenderSectorCharts()"></td>
              <td><select style="${vertStyle}" onchange="_fdTableChange(${i},'vertical',this.value); _fdRenderSectorCharts()">${vertOpts}</select></td>
              <td><select style="${typeStyle}" onchange="_fdTableChange(${i},'type',this.value); _fdRenderSectorCharts()">${typeOpts}</select></td>
              <td>${isReserve ? '<span style="font-size:0.65rem;color:#9ca3af;font-weight:700;letter-spacing:.04em">RC</span>' : ''}</td>
              <td><button class="fd-table-remove" onclick="_fdRemoveCompany(${i})" title="Remove">×</button></td>`;
            tbody.appendChild(tr);
        });
    };

    renderGroup(newInvest, 0, false);
    renderGroup(reserves, newInvest.length, true);

    // Show unassigned count badge
    const unassignedCount = _fdCompanies.filter(c => c.vertical === FD_UNASSIGNED || c.type === FD_UNASSIGNED || c.name === 'TBD').length;
    const status = document.getElementById('fd-save-status');
    if (status && unassignedCount > 0) {
        status.textContent = `⚠ ${unassignedCount} row(s) need assignment — highlighted in amber`;
        status.className = 'fd-save-status error';
    } else if (status && !status.textContent.startsWith('✓')) {
        status.textContent = '';
    }
}

function _fdTableChange(idx, field, val) {
    if (_fdCompanies[idx]) _fdCompanies[idx][field] = val;
}

function _fdRemoveCompany(idx) {
    _fdCompanies.splice(idx, 1);
    _fdRenderCompanyTable();
    _fdRenderSectorCharts();
}

function fdAddCompany() {
    // Insert before reserve section
    const insertAt = _fdCompanies.findIndex(c => c.is_reserve);
    const newEntry = { quarter: 12, label: 'Q12', name: 'New Company', nic_m: 2.0, vertical: '?', type: '?', is_reserve: false };
    if (insertAt >= 0) _fdCompanies.splice(insertAt, 0, newEntry);
    else _fdCompanies.push(newEntry);
    _fdRenderCompanyTable();
}

async function fdSaveCompanies() {
    const status = document.getElementById('fd-save-status');
    if (status) { status.textContent = 'Saving…'; status.className = 'fd-save-status'; }
    try {
        // Sync all input values from DOM before saving
        const rows = document.querySelectorAll('#fd-company-tbody tr[data-idx]');
        rows.forEach(tr => {
            const i = parseInt(tr.dataset.idx);
            const inputs = tr.querySelectorAll('input, select');
            if (!_fdCompanies[i] || inputs.length < 4) return;
            _fdCompanies[i].name    = inputs[0].value;
            const capVal = parseFloat(inputs[1].value) || 0;
            if (_fdCompanies[i].is_reserve) _fdCompanies[i].rc_m = capVal;
            else _fdCompanies[i].nic_m = capVal;
            _fdCompanies[i].vertical = inputs[2].value;
            _fdCompanies[i].type     = inputs[3].value;
        });
        await apiCall('/api/fund/companies', { method: 'POST', body: JSON.stringify({ companies: _fdCompanies }) });
        if (status) { status.textContent = '✓ Saved'; status.className = 'fd-save-status'; }
        _fdRenderSectorCharts();
        setTimeout(() => { if (status) status.textContent = ''; }, 3000);
    } catch(e) {
        if (status) { status.textContent = 'Save failed: ' + e.message; status.className = 'fd-save-status error'; }
    }
}

// Hook into tab switching
const _fdOrigSwitchTab = switchTab;
switchTab = function(tab) {
    _fdOrigSwitchTab(tab);
    if (tab === 'funddeployment') {
        setTimeout(() => fdInit(), 50);
    }
    if (tab === 'ddr') { ddrInitDropzone(); ddrLoadHistory(); }
};

// ══════════════════════════════════════════════════════════════════════════════
// DDR (Due Diligence Report) — Background analysis with polling
// ══════════════════════════════════════════════════════════════════════════════

let _ddrCurrentJobId = null;
let _ddrPollTimer = null;
let _ddrPdfFilename = null;
let _ddrSelectedDeck = null;  // File selected via the DDR dropzone (overrides the deck from Deal Pipeline)

function _ddrStartJob(deckFile) {
    if (!deckFile) return;
    const ext = deckFile.name.split('.').pop().toLowerCase();
    if (ext !== 'pdf') {
        showToast('DDR requires a PDF pitch deck.');
        return;
    }

    const fd = new FormData();
    fd.append('file', deckFile);
    const headers = {};
    if (_rvmToken) headers['Authorization'] = 'Bearer ' + _rvmToken;

    const runBtn = document.getElementById('ddr-run-btn');
    const origLabel = 'Run on Already Submitted Deck';
    if (runBtn) { runBtn.disabled = true; runBtn.textContent = 'Starting...'; }

    fetch('/api/ddr/start', { method: 'POST', headers, body: fd })
        .then(async r => {
            const d = await r.json();
            if (r.status === 429) {
                showToast(d.detail || 'Please wait before starting another analysis.');
                if (runBtn) { runBtn.disabled = false; runBtn.textContent = origLabel; }
                return;
            }
            if (d.job_id) {
                _ddrCurrentJobId = d.job_id;
                ddrShowActive(deckFile.name);
                ddrStartPolling(d.job_id);
                showToast('Due diligence report started');
                if (runBtn) { runBtn.disabled = false; runBtn.textContent = origLabel; }
            } else {
                showToast(d.detail || 'DDR start failed');
                console.warn('[DDR] Start failed:', d.detail || d);
                if (runBtn) { runBtn.disabled = false; runBtn.textContent = origLabel; }
            }
        })
        .catch(err => {
            showToast('DDR error: ' + err.message);
            console.warn('[DDR] Start error:', err);
            if (runBtn) { runBtn.disabled = false; runBtn.textContent = origLabel; }
        });
}

// Called when the analyst clicks "Run DDR on Deck Already Submitted".
// Prefers a deck dropped into the DDR dropzone; otherwise falls back to the
// deck uploaded in the Deal Pipeline tab (window._wizLastDeckFile).
function ddrRunSubmittedDeck() {
    const deck = _ddrSelectedDeck || window._wizLastDeckFile;
    if (!deck) {
        showToast('No deck submitted yet. Drop a PDF above or upload one in the Deal Pipeline tab.');
        return;
    }
    _ddrStartJob(deck);
}

// Update the dropzone display + button enabled state based on what deck
// is currently available (DDR-dropzone file or Deal Pipeline fallback).
function ddrSyncSubmittedDeck() {
    const selEl = document.getElementById('ddr-selected-file');
    const runBtn = document.getElementById('ddr-run-btn');

    const deck = _ddrSelectedDeck || window._wizLastDeckFile;
    const source = _ddrSelectedDeck ? 'dropzone' : (window._wizLastDeckFile ? 'pipeline' : null);

    if (selEl) {
        if (deck) {
            const size = (deck.size / (1024 * 1024)).toFixed(1);
            const label = source === 'pipeline' ? 'from Deal Pipeline' : 'ready';
            selEl.innerHTML = `<strong>${deck.name}</strong> <span style="color:#6b7280;">(${size} MB · ${label})</span>`;
            selEl.style.display = 'block';
        } else {
            selEl.style.display = 'none';
            selEl.innerHTML = '';
        }
    }
    if (runBtn) runBtn.disabled = !deck;
}

function ddrInitDropzone() {
    const dropzone = document.getElementById('ddr-dropzone');
    const fileInput = document.getElementById('ddr-file-input');
    if (!dropzone || !fileInput || dropzone._initDone) {
        ddrSyncSubmittedDeck();
        return;
    }
    dropzone._initDone = true;

    const handleFile = (file) => {
        if (!file) return;
        const ext = (file.name.split('.').pop() || '').toLowerCase();
        if (ext !== 'pdf') { showToast('DDR requires a PDF pitch deck.'); return; }
        _ddrSelectedDeck = file;
        ddrSyncSubmittedDeck();
    };

    dropzone.addEventListener('click', (e) => {
        if (e.target.tagName !== 'LABEL' && e.target.tagName !== 'INPUT') fileInput.click();
    });
    dropzone.addEventListener('dragover', (e) => { e.preventDefault(); dropzone.classList.add('dragover'); });
    dropzone.addEventListener('dragleave', () => dropzone.classList.remove('dragover'));
    dropzone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropzone.classList.remove('dragover');
        if (e.dataTransfer.files.length) handleFile(e.dataTransfer.files[0]);
    });
    fileInput.addEventListener('change', () => {
        if (fileInput.files.length) handleFile(fileInput.files[0]);
        fileInput.value = '';
    });

    ddrSyncSubmittedDeck();
}

function ddrShowActive(filename) {
    const emptyEl = document.getElementById('ddr-empty');
    const progressCard = document.getElementById('ddr-progress-card');
    const downloadArea = document.getElementById('ddr-download-area');
    if (emptyEl) emptyEl.style.display = 'none';
    if (progressCard) { progressCard.style.display = 'block'; progressCard.className = 'ddr-progress-card'; }
    if (downloadArea) downloadArea.style.display = 'none';

    const filenameEl = document.getElementById('ddr-file-name');
    if (filenameEl) filenameEl.textContent = filename || '';

    const titleEl = document.getElementById('ddr-progress-title');
    if (titleEl) titleEl.textContent = 'Generating Due Diligence Report...';

    const bar = document.getElementById('ddr-progress-bar');
    if (bar) bar.style.width = '0%';
    const msg = document.getElementById('ddr-progress-msg');
    if (msg) { msg.textContent = 'Queued...'; msg.style.color = ''; }
    const pct = document.getElementById('ddr-progress-pct');
    if (pct) pct.textContent = '0%';

    const badge = document.getElementById('ddr-company-badge');
    if (badge) { badge.style.display = 'none'; badge.textContent = ''; }
}

function ddrStartPolling(jobId) {
    if (_ddrPollTimer) clearInterval(_ddrPollTimer);
    _ddrPollTimer = setInterval(() => ddrPollStatus(jobId), 3000);
    ddrPollStatus(jobId);
}

async function ddrPollStatus(jobId) {
    try {
        const headers = _rvmHeaders();
        const r = await fetch(`/api/ddr/status/${jobId}`, { headers });
        if (!r.ok) return;
        const d = await r.json();

        const bar = document.getElementById('ddr-progress-bar');
        const msg = document.getElementById('ddr-progress-msg');
        const pct = document.getElementById('ddr-progress-pct');
        if (bar) bar.style.width = (d.progress_pct || 0) + '%';
        if (msg) msg.textContent = d.progress_msg || '';
        if (pct) pct.textContent = (d.progress_pct || 0) + '%';

        if (d.company_name) {
            const badge = document.getElementById('ddr-company-badge');
            if (badge) { badge.textContent = d.company_name; badge.style.display = 'inline-block'; }
        }

        if (d.status === 'complete') {
            clearInterval(_ddrPollTimer);
            _ddrPollTimer = null;
            ddrShowComplete(d);
        } else if (d.status === 'error') {
            clearInterval(_ddrPollTimer);
            _ddrPollTimer = null;
            ddrShowError(d.error);
        }
    } catch (err) {
        console.warn('[DDR] Poll error:', err);
    }
}

function ddrShowComplete(data) {
    const titleEl = document.getElementById('ddr-progress-title');
    if (titleEl) titleEl.textContent = 'Due Diligence Report Complete';

    const progressCard = document.getElementById('ddr-progress-card');
    if (progressCard) progressCard.classList.add('ddr-complete');

    const downloadArea = document.getElementById('ddr-download-area');
    if (downloadArea) downloadArea.style.display = 'flex';

    const meta = document.getElementById('ddr-download-meta');
    if (meta) meta.textContent = `${data.company_name || 'Unknown'} — Generated ${new Date(data.finished_at).toLocaleString()}`;

    _ddrPdfFilename = data.pdf_filename || null;

    // Refresh shared report list so the new report appears
    ddrLoadHistory();
}

function ddrShowError(errorMsg) {
    const titleEl = document.getElementById('ddr-progress-title');
    if (titleEl) titleEl.textContent = 'Due Diligence Report Failed';
    const msg = document.getElementById('ddr-progress-msg');
    if (msg) { msg.textContent = errorMsg || 'Unknown error'; msg.style.color = '#dc3545'; }
    const progressCard = document.getElementById('ddr-progress-card');
    if (progressCard) progressCard.classList.add('ddr-error');
}

async function ddrDownload() {
    if (!_ddrCurrentJobId) return;
    try {
        const r = await fetch(`/api/ddr/download/${_ddrCurrentJobId}`, {headers: _rvmHeaders()});
        if (!r.ok) {
            const err = await r.text();
            showToast('Download failed: ' + err);
            return;
        }
        const blob = await r.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = _ddrPdfFilename || `DDR_Report_${_ddrCurrentJobId}.pdf`;
        a.click();
        URL.revokeObjectURL(url);
    } catch (e) {
        showToast('Download failed: ' + e.message);
    }
}

async function ddrLoadHistory() {
    // Load in-progress jobs (in-memory)
    try {
        const headers = _rvmHeaders();
        const r = await fetch('/api/ddr/jobs', { headers });
        if (r.ok) {
            const d = await r.json();
            const jobs = (d.jobs || []).filter(j => j.status !== 'complete');
            const activeEl = document.getElementById('ddr-active-jobs');
            if (activeEl && jobs.length > 0) {
                activeEl.innerHTML = '<h4 style="margin:0 0 6px 0;font-size:0.85rem;color:#2d5f3f;">In Progress</h4>' +
                    jobs.map(j => {
                        const sc = j.status==='error'?'ddr-job-error':'ddr-job-running';
                        const sl = j.status==='error'?'Failed':`${j.progress_pct}%`;
                        return `<div class="ddr-history-item ${sc}" onclick="ddrResumeJob('${j.job_id}')"><span class="ddr-hist-name">${j.company_name||j.filename||'Processing...'}</span><span class="ddr-hist-status">${sl}</span></div>`;
                    }).join('');
            }
        }
    } catch (err) { console.warn('[DDR] Jobs error:', err); }

    // Load saved reports from database (shared across all users)
    try {
        const headers = _rvmHeaders();
        const r = await fetch('/api/ddr/reports', { headers });
        if (!r.ok) return;
        const d = await r.json();
        const reports = d.reports || [];
        const listEl = document.getElementById('ddr-history-list');
        if (!listEl) return;
        if (reports.length === 0) { listEl.innerHTML = '<p class="text-muted">No saved reports yet.</p>'; return; }
        listEl.innerHTML = reports.map(rpt => {
            const sizeMB = (rpt.file_size_bytes / (1024*1024)).toFixed(1);
            const date = rpt.generated_at ? new Date(rpt.generated_at).toLocaleDateString() : '';
            return `<div class="ddr-history-item ddr-job-complete" style="cursor:pointer;" onclick="ddrDownloadReport(${rpt.id}, '${rpt.filename.replace(/'/g, "\\'")}')">
                <span class="ddr-hist-name">${rpt.company_name}</span>
                <span class="ddr-hist-status" style="font-size:0.75rem;color:#666;">${rpt.generated_by} · ${sizeMB}MB</span>
                <span class="ddr-hist-date">${date}</span>
            </div>`;
        }).join('');
    } catch (err) { console.warn('[DDR] Reports error:', err); }
}

async function ddrDownloadReport(reportId, filename) {
    try {
        const r = await fetch(`/api/ddr/reports/${reportId}/download`, {headers: _rvmHeaders()});
        if (!r.ok) { showToast('Download failed'); return; }
        const blob = await r.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename || 'DDR_Report.pdf';
        a.click();
        URL.revokeObjectURL(url);
    } catch (e) { showToast('Download failed: ' + e.message); }
}

function ddrResumeJob(jobId) {
    _ddrCurrentJobId = jobId;
    ddrShowActive('');
    ddrStartPolling(jobId);
}
