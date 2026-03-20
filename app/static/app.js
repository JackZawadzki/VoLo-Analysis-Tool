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
    range_improvement: "Multiplier on baseline production reflecting the new technology's efficiency advantage. 1.0 = same output as incumbent, 1.15 = 15% more efficient.",
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
    total_lifecycle_tco2: "Total avoided CO₂ emissions over the full deployment horizon, summing annual operating and embodied emissions across all projected units.",
    volo_prorata: "VoLo's attributable share of total avoided emissions, calculated as total lifecycle tCO₂ × VoLo's entry ownership percentage.",
    risk_adjusted_carbon: "Pro-rata emissions adjusted for survival probability using a risk divisor. Reflects the expected avoided emissions accounting for the chance the company fails.",
    carbon_tpd: "Risk-adjusted tonnes of CO₂ avoided per dollar invested. Key metric for comparing carbon efficiency across deals — higher = more carbon impact per investment dollar.",
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

function showAuthTab(tab) {
    const loginForm = document.getElementById('auth-login-form');
    const regForm = document.getElementById('auth-register-form');
    // Remove any CSS classes that might override inline display
    loginForm.classList.remove('auth-hidden');
    regForm.classList.remove('auth-hidden');
    loginForm.style.display = tab === 'login' ? 'block' : 'none';
    regForm.style.display = tab === 'register' ? 'block' : 'none';
    document.getElementById('auth-tab-login').className = 'auth-tab' + (tab === 'login' ? ' active' : '');
    document.getElementById('auth-tab-register').className = 'auth-tab' + (tab === 'register' ? ' active' : '');
    document.getElementById('auth-error').style.display = 'none';
}

function _authError(msg) {
    const el = document.getElementById('auth-error');
    el.textContent = msg;
    el.style.display = '';
}

async function doLogin() {
    const u = document.getElementById('login-username').value.trim();
    const p = document.getElementById('login-password').value;
    if (!u || !p) return _authError('Please enter username and password');
    try {
        const r = await fetch('/api/auth/login', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({username:u, password:p})});
        const d = await r.json();
        if (!r.ok) return _authError(d.detail || d.error || 'Login failed');
        _rvmToken = d.token;
        _rvmUser = d.user;
        localStorage.setItem('rvm_token', d.token);
        _onAuthSuccess();
    } catch(e) { _authError('Network error'); }
}

async function doRegister() {
    console.log('doRegister called');
    const u = document.getElementById('reg-username').value.trim();
    const e = document.getElementById('reg-email').value.trim();
    const p = document.getElementById('reg-password').value;
    console.log('doRegister values:', {u, e, pLen: p.length});
    if (!u || !e || p.length < 8) return _authError('Fill all fields (password 8+ chars)');
    try {
        const r = await fetch('/api/auth/register', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({username:u, email:e, password:p})});
        const d = await r.json();
        if (!r.ok) return _authError(d.detail || d.error || 'Registration failed');
        _rvmToken = d.token;
        _rvmUser = d.user;
        localStorage.setItem('rvm_token', d.token);
        _onAuthSuccess();
    } catch(err) { _authError('Network error'); }
}

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
    if (n === 1) {
        // Clear saved inputs when starting a new deal
        _wizSavedInputs = null;
        _wizReportId = null;
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

function wizLoadResources() {
    if (!_rvmToken) return;
    fetch('/api/resources', {headers: _rvmHeaders()})
        .then(r => r.json())
        .then(data => {
            const sel = document.getElementById('wiz-displaced-resource');
            if (sel) sel.innerHTML = '<option value="">— None —</option>' + data.map(r => `<option value="${r.name}">${r.name}</option>`).join('');
        }).catch(() => {});
}

function wizLoadReports() {
    if (!_rvmToken) return;
    fetch('/api/deal-pipeline/reports', {headers: _rvmHeaders()})
        .then(r => r.json())
        .then(reports => {
            const list = document.getElementById('wiz-reports-list');
            if (!reports.length) { list.innerHTML = '<p style="color:var(--text-tertiary); font-size:0.78rem;">No reports yet.</p>'; return; }
            list.innerHTML = reports.slice(0, 12).map(r => `
                <div class="report-card-mini" onclick="wizLoadReport(${r.id})">
                    <div class="rc-name">${r.company_name}</div>
                    <div class="rc-meta">${r.archetype} &middot; ${r.entry_stage} &middot; ${r.created_at?.split('T')[0] || ''}</div>
                </div>
            `).join('');
        }).catch(() => {});
}

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
    } catch(e) { showToast('Failed to load report'); }
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
    if (inp.extraction_confidence) _wizExtraction = { confidence: inp.extraction_confidence, _source: inp.extraction_source };
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
            if (r.ok && d && !d.detail) {
                _wizExtraction = d;
                extractionOk = true;
            } else {
                let errMsg = (d && d.detail) ? d.detail : 'Extraction failed (HTTP ' + r.status + ')';
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
            status.textContent = 'Extracting financial model (this may take 20-30 seconds)...';
            const mfd = new FormData();
            mfd.append('file', modelFile);
            const mHeaders = {};
            if (_rvmToken) mHeaders['Authorization'] = 'Bearer ' + _rvmToken;
            const mr = await fetch('/api/extract-model', {method: 'POST', headers: mHeaders, body: mfd});
            let md;
            try { md = await mr.json(); } catch(_) { md = {detail: 'Server error (HTTP ' + mr.status + ')'}; }
            if (mr.ok && md && md.status === 'ok') {
                _wizFmData = md;
                extractionOk = true;
            } else {
                const fmErr = (md && md.detail) ? md.detail : 'Financial model extraction failed';
                status.className = 'wiz-status error';
                status.textContent = fmErr;
                document.getElementById('wiz-extract-btn').disabled = false;
                return;
            }
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
            const hasData = years.some(y => (fin[m]?.[String(y)] ?? fin[m]?.[y]) != null);
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

const _CARBON_DEFAULTS = {
    utility_solar: {displaced_resource:'US electricity', baseline_lifetime_prod:1500, range_improvement:1.15, unit_definition:'MW installed capacity', specific_production_units:'MWh/MW/year', service_life:25},
    commercial_solar: {displaced_resource:'US electricity', baseline_lifetime_prod:1200, range_improvement:1.10, unit_definition:'MW installed capacity', specific_production_units:'MWh/MW/year', service_life:25},
    residential_solar: {displaced_resource:'US electricity', baseline_lifetime_prod:1100, range_improvement:1.05, unit_definition:'MW installed capacity', specific_production_units:'MWh/MW/year', service_life:25},
    onshore_wind: {displaced_resource:'US electricity', baseline_lifetime_prod:2500, range_improvement:1.0, unit_definition:'MW installed capacity', specific_production_units:'MWh/MW/year', service_life:20},
    offshore_wind: {displaced_resource:'Global electricity', baseline_lifetime_prod:3800, range_improvement:1.0, unit_definition:'MW installed capacity', specific_production_units:'MWh/MW/year', service_life:25},
    geothermal: {displaced_resource:'US electricity', baseline_lifetime_prod:7000, range_improvement:1.0, unit_definition:'MW installed capacity', specific_production_units:'MWh/MW/year', service_life:30},
    battery_storage_utility: {displaced_resource:'Natural Gas (CCGT)', baseline_lifetime_prod:2000, range_improvement:0.85, unit_definition:'MWh storage capacity', specific_production_units:'MWh displaced/MWh/year', service_life:15},
    nuclear_smr: {displaced_resource:'Global electricity', baseline_lifetime_prod:8000, range_improvement:1.0, unit_definition:'MW installed capacity', specific_production_units:'MWh/MW/year', service_life:40},
    ev_electrification: {displaced_resource:'Gasoline', baseline_lifetime_prod:12000, range_improvement:0.85, unit_definition:'vehicles', specific_production_units:'L gasoline displaced/vehicle/year', service_life:12},
    climate_software: {displaced_resource:'US electricity', baseline_lifetime_prod:50, range_improvement:1.0, unit_definition:'enterprise customers', specific_production_units:'MWh saved/customer/year', service_life:10},
    industrial_decarb: {displaced_resource:'Natural Gas', baseline_lifetime_prod:500, range_improvement:1.0, unit_definition:'industrial installations', specific_production_units:'MMBtu displaced/unit/year', service_life:15},
    ai_ml: {displaced_resource:'US electricity', baseline_lifetime_prod:10, range_improvement:1.0, unit_definition:'enterprise deployments', specific_production_units:'MWh optimized/deployment/year', service_life:5},
    base_capital_intensive: {displaced_resource:'Natural Gas', baseline_lifetime_prod:500, range_improvement:1.0, unit_definition:'industrial installations', specific_production_units:'MMBtu displaced/unit/year', service_life:15},
    base_software: {displaced_resource:'US electricity', baseline_lifetime_prod:50, range_improvement:1.0, unit_definition:'enterprise customers', specific_production_units:'MWh saved/customer/year', service_life:10},
    base_sw_hw_hybrid: {displaced_resource:'Natural Gas (CCGT)', baseline_lifetime_prod:2000, range_improvement:0.85, unit_definition:'MWh storage capacity', specific_production_units:'MWh displaced/MWh/year', service_life:15},
    base_hard_tech: {displaced_resource:'Global electricity', baseline_lifetime_prod:8000, range_improvement:1.0, unit_definition:'MW installed capacity', specific_production_units:'MWh/MW/year', service_life:40},
    custom: {displaced_resource:'US electricity', baseline_lifetime_prod:100, range_improvement:1.0, unit_definition:'units', specific_production_units:'', service_life:10},
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
        volume: {
            year_volumes: vols,
            commercial_launch_yr: 2024,
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
        fund_size_m: parseFloat(document.getElementById('wiz-fund-size')?.value || 135),
        n_deals: parseInt(document.getElementById('wiz-n-deals')?.value || 22),
        mgmt_fee_pct: parseFloat(document.getElementById('wiz-mgmt-fee')?.value || 2),
        reserve_pct: parseFloat(document.getElementById('wiz-reserve')?.value || 45),
        max_concentration_pct: parseFloat(document.getElementById('wiz-max-conc')?.value || 15),
        round_size_m: parseFloat(document.getElementById('wiz-round-size')?.value || 0) || null,
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
            html += `<tr${cls}><td>Y${y.year}</td><td class="rpt-num">${fmt(y.founder,1)}</td><td class="rpt-num">${fmt(y.simulated_median,1)}</td><td class="rpt-num">${fmt(y.simulated_p25,1)}</td><td class="rpt-num">${fmt(y.simulated_p75,1)}</td><td class="rpt-num">${y.divergence_pct > 0 ? '+' : ''}${fmt(y.divergence_pct,1)}%</td><td>${y.in_band ? 'Yes' : 'No'}</td></tr>`;
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
            <div class="rpt-hero-card accent"><div class="rpt-hero-num">${fmt(co.company_tonnes, 0)}</div><div class="rpt-hero-label">Total Lifecycle tCO2 ${infoTip('total_lifecycle_tco2')}</div></div>
            <div class="rpt-hero-card"><div class="rpt-hero-num">${fmt(co.volo_prorata, 0)}</div><div class="rpt-hero-label">VoLo Pro-Rata ${infoTip('volo_prorata')}</div></div>
            <div class="rpt-hero-card"><div class="rpt-hero-num">${fmt(co.volo_risk_adj, 0)}</div><div class="rpt-hero-label">Risk-Adjusted ${infoTip('risk_adjusted_carbon')}</div></div>
            <div class="rpt-hero-card accent"><div class="rpt-hero-num">${fmt(co.risk_adj_tpd, 4)}</div><div class="rpt-hero-label">t/$ (Risk-Adj) ${infoTip('carbon_tpd')}</div></div>
        </div>
        ${trace('Carbon calculation chain', `
            <p><strong>Total lifecycle tCO2</strong> = Σ annual (operating + embodied) over projection period.</p>
            <p><strong>VoLo Pro-Rata</strong> = lifecycle tCO2 x ${fmt(ov.entry_ownership_pct,1)}% ownership.</p>
            <p><strong>Risk-Adjusted</strong> = pro-rata / risk_divisor (${carbonRD}, ${carbonRDSrc}).</p>
            <p><strong>t/$</strong> = risk-adjusted tonnes / $${fmt(ov.check_size_millions,1)}M check.</p>
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
                const hasVals = fmYears.some(y => (series[String(y)] ?? series[y]) != null);
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
            const labels = Array.from({length:n}, (_,i) => `Y${i}`);
            const datasets = [];
            if (revTraj.p90) datasets.push({label:'P90',data:revTraj.p90,borderColor:'transparent',backgroundColor:_c(34,87,122,0.06),fill:true,pointRadius:0,order:6});
            if (revTraj.p75) datasets.push({label:'P75',data:revTraj.p75,borderColor:_c(34,87,122,0.25),backgroundColor:_c(34,87,122,0.12),fill:true,pointRadius:0,borderWidth:1,order:5});
            datasets.push({label:'Sim Median',data:med,borderColor:BLU,fill:false,pointRadius:0,borderWidth:2.5,tension:0.3,order:2});
            if (revTraj.p25) datasets.push({label:'P25',data:revTraj.p25,borderColor:_c(34,87,122,0.25),backgroundColor:_c(34,87,122,0.12),fill:'-1',pointRadius:0,borderWidth:1,order:4});
            if (revTraj.p10) datasets.push({label:'P10',data:revTraj.p10,borderColor:'transparent',backgroundColor:_c(34,87,122,0.06),fill:'-1',pointRadius:0,order:6});

            if (fComp.has_data && fComp.revenue?.year_by_year?.length) {
                const founderData = new Array(n).fill(null);
                fComp.revenue.year_by_year.forEach(y => { if (y.year < n) founderData[y.year] = y.founder; });
                datasets.push({label:'Founder Projection (Base)',data:founderData,borderColor:'#dc2626',backgroundColor:'#dc2626',fill:false,pointRadius:5,pointStyle:'circle',borderWidth:3,tension:0.2,order:1});
            }

            const scenarioRev = fComp.scenario_revenue || {};
            if (scenarioRev.bear) {
                const bearData = new Array(n).fill(null);
                scenarioRev.bear.forEach((v, i) => { if (i < n && v) bearData[i] = v; });
                if (bearData.some(v => v != null)) {
                    datasets.push({label:'Bear Case',data:bearData,borderColor:'#dc3545',backgroundColor:'transparent',fill:false,pointRadius:3,pointStyle:'triangle',borderWidth:2,borderDash:[6,3],tension:0.2,order:1});
                }
            }
            if (scenarioRev.bull) {
                const bullData = new Array(n).fill(null);
                scenarioRev.bull.forEach((v, i) => { if (i < n && v) bullData[i] = v; });
                if (bullData.some(v => v != null)) {
                    datasets.push({label:'Bull Case',data:bullData,borderColor:'#007bff',backgroundColor:'transparent',fill:false,pointRadius:3,pointStyle:'triangle',borderWidth:2,borderDash:[6,3],tension:0.2,order:1});
                }
            }

            _wizReportCharts.revenueCone = new Chart(ctxRev, {
                type:'line', data:{labels,datasets},
                options: chartOpts({
                    plugins:{legend:{position:'bottom',labels:{font:chartFont,usePointStyle:true,filter:it=>!['P10','P90'].includes(it.text)}}},
                    scales:{
                        x:{title:{display:true,text:'Years from Investment',font:chartFont},ticks:{font:chartFont}},
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
            const labels = ci.annual_operating.map((_, i) => `Y${i+1}`);
            _wizReportCharts.carbon = new Chart(ctx5, {
                type: 'bar',
                data: {
                    labels,
                    datasets: [
                        {label: 'Operating', data: ci.annual_operating, backgroundColor: _c(34,87,122,0.75), borderRadius: 3},
                        {label: 'Embodied', data: ci.annual_embodied || [], backgroundColor: _c(163,190,140,0.75), borderRadius: 3},
                    ]
                },
                options: {
                    responsive: true, maintainAspectRatio: false,
                    scales: {x: {stacked: true, ticks: {font: chartFont}}, y: {stacked: true, title: {display: true, text: 'tCO2', font: chartFont}}},
                    plugins: {legend: {position: 'top', labels: {font: chartFont}}}
                }
            });
        }
    }
}

function wizExportPDF() {
    const reportEl = document.getElementById('deal-report');
    if (!reportEl || !reportEl.innerHTML.trim()) {
        showToast('Generate a report first');
        return;
    }
    document.body.classList.add('printing-report');
    setTimeout(() => {
        window.print();
        document.body.classList.remove('printing-report');
    }, 300);
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
                headers: {'Authorization': `Bearer ${localStorage.getItem('volo_token')}`},
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

// File input display
['wiz-deck-file', 'wiz-model-file'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('change', () => {
        const nameEl = document.getElementById(id.replace('-file', '-name'));
        const zone = document.getElementById(id.replace('-file', '-zone'));
        if (el.files.length) {
            if (nameEl) nameEl.textContent = el.files[0].name;
            if (zone) zone.classList.add('has-file');
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
    sessionId: '',
    documents: [],
    links: [],
    templates: [],
    currentMemoId: null,
    currentMemoMd: '',
    generating: false,
    citations: {},
    libraryId: null,
    libraries: [],
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
    if (!dropzone || !fileInput) return;

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
        memoRefreshDocList();
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
        if (!r.ok) return;
        _memo.documents = await r.json();
        memoRenderDocList();
    } catch (e) {
        console.error(e);
    }
}

function memoRenderDocList() {
    const list = document.getElementById('memo-doc-list');
    if (!list) return;
    if (!_memo.documents.length) { list.innerHTML = ''; return; }

    const fmtSize = (b) => b > 1024*1024 ? (b/1024/1024).toFixed(1)+'MB' : (b/1024).toFixed(0)+'KB';
    const iconMap = {
        '.pdf':'&#128196;', '.docx':'&#128196;', '.doc':'&#128196;',
        '.xlsx':'&#128202;', '.xls':'&#128202;', '.csv':'&#128202;',
        '.pptx':'&#128218;', '.ppt':'&#128218;',
        '.txt':'&#128221;', '.md':'&#128221;', '.html':'&#127760;',
        '.png':'&#128247;', '.jpg':'&#128247;', '.jpeg':'&#128247;',
    };

    list.innerHTML = _memo.documents.map(d => {
        const icon = iconMap[d.file_type] || '&#128196;';
        const cat = (d.doc_category || 'other').replace(/_/g, ' ');
        return `<div class="memo-doc-item">
            <span class="memo-doc-icon">${icon}</span>
            <div class="memo-doc-info">
                <span class="memo-doc-name">${d.file_name}</span>
                <span class="memo-doc-meta">${fmtSize(d.file_size)} · ${cat}</span>
            </div>
            <button class="memo-doc-remove" onclick="memoRemoveDoc(${d.id})" title="Remove">&times;</button>
        </div>`;
    }).join('');
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

    // Switch to preview tab and show progress
    memoSwitchOutputTab('preview');

    // Build progress tracker — estimate passes based on doc count
    const nDocs = _memo.documents.length;
    const nDataSections = 9;
    const nSynthesisSections = 4;  // Investment Overview, Opportunities, Risks, Recommendation
    const totalSteps = nDocs + nDataSections + nSynthesisSections;
    let currentStep = 0;

    const progressStages = [];
    if (nDocs > 0) {
        for (let i = 0; i < nDocs; i++) {
            progressStages.push(`Pass 1 — Extracting facts from "${_memo.documents[i]?.file_name || 'document ' + (i+1)}" (${i+1}/${nDocs})`);
        }
    }
    const sectionNames = ['Company Overview','Market','Business Model','Team','Traction',
                          'Competitive Position','Carbon Impact','Technology, IP & Moat',
                          'Financing Overview & Exit Planning'];
    sectionNames.forEach((s, i) => progressStages.push(`Pass 2 — Writing: ${s} (${i+1}/${nDataSections})`));
    progressStages.push('Pass 3 — Synthesizing: Investment Overview');
    progressStages.push('Pass 3 — Synthesizing: High Level Opportunities');
    progressStages.push('Pass 3 — Synthesizing: High Level Risks');
    progressStages.push('Pass 3 — Synthesizing: Investment Recommendation');

    // Animate progress
    const updateProgress = () => {
        if (!_memo.generating) return;
        if (currentStep < progressStages.length) {
            const pct = Math.round((currentStep / totalSteps) * 100);
            if (statusEl) {
                statusEl.style.display = 'block';
                statusEl.className = 'memo-generate-status info';
                statusEl.innerHTML = `
                    <div class="memo-progress-bar"><div class="memo-progress-fill" style="width:${pct}%"></div></div>
                    <div class="memo-progress-text">${progressStages[currentStep]} <span style="float:right">${pct}%</span></div>`;
            }
            currentStep++;
            // Estimate ~8-15s per LLM call
            const delay = currentStep <= nDocs ? 10000 : 12000;
            setTimeout(updateProgress, delay);
        } else {
            if (statusEl) {
                statusEl.innerHTML = `
                    <div class="memo-progress-bar"><div class="memo-progress-fill" style="width:95%"></div></div>
                    <div class="memo-progress-text">Assembling final memo... <span style="float:right">95%</span></div>`;
            }
        }
    };
    updateProgress();

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
    };

    try {
        const r = await fetch('/api/memo/generate', {
            method: 'POST',
            headers: { ..._rvmHeaders(), 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });

        if (!r.ok) {
            const err = await r.json().catch(() => ({ detail: r.statusText }));
            throw new Error(err.detail || err.error || 'Generation failed');
        }

        const data = await r.json();
        _memo.currentMemoId = data.id;
        _memo.currentMemoMd = data.memo_markdown;

        // Render the memo
        const rendered = document.getElementById('memo-rendered');
        const empty = document.getElementById('memo-output-empty');
        const actions = document.getElementById('memo-output-actions');
        const meta = document.getElementById('memo-output-meta');

        if (rendered) rendered.innerHTML = data.memo_html;
        if (empty) empty.style.display = 'none';
        if (actions) actions.style.display = 'flex';

        // Store citations metadata and bind click handlers
        _memo.citations = data.citations || {};
        _memoCitationsBind(rendered);

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

    } catch (e) {
        console.error('Memo generation error:', e);
        if (statusEl) { statusEl.innerHTML = `Error: ${e.message}`; statusEl.className = 'memo-generate-status error'; }
    } finally {
        _memo.generating = false;
        if (btn) { btn.disabled = false; btn.textContent = 'Generate Investment Memo'; }
    }
}

// ── Citation popover system ─────────────────────────────────────────────────
function _memoCitationsBind(container) {
    if (!container) return;
    // Remove any existing popover
    const existing = document.getElementById('memo-cite-popover');
    if (existing) existing.remove();

    // Create popover element
    const popover = document.createElement('div');
    popover.id = 'memo-cite-popover';
    popover.className = 'memo-cite-popover hidden';
    popover.innerHTML = `
        <div class="memo-cite-popover-header">
            <span class="memo-cite-popover-badge"></span>
            <span class="memo-cite-popover-title"></span>
            <button class="memo-cite-popover-close" onclick="this.parentElement.parentElement.classList.add('hidden')">&times;</button>
        </div>
        <div class="memo-cite-popover-category"></div>
        <div class="memo-cite-popover-excerpt"></div>`;
    document.body.appendChild(popover);

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
            const excerpt = popover.querySelector('.memo-cite-popover-excerpt');

            badge.textContent = `[${cite.number}]`;
            title.textContent = cite.file_name;
            category.textContent = cite.category;
            excerpt.textContent = cite.excerpt || 'No preview available.';

            // Position near the clicked citation
            const rect = el.getBoundingClientRect();
            const scrollTop = window.scrollY || document.documentElement.scrollTop;
            popover.style.top = (rect.bottom + scrollTop + 8) + 'px';
            popover.style.left = Math.max(12, Math.min(rect.left, window.innerWidth - 420)) + 'px';
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

// ── Export & Copy ───────────────────────────────────────────────────────────
function memoExportDocx() {
    if (!_memo.currentMemoId) return;
    window.open(`/api/memo/history/${_memo.currentMemoId}/docx?token=${_rvmToken}`, '_blank');
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
    try {
        const r = await fetch(`/api/memo/history/${id}`, { headers: _rvmHeaders() });
        if (!r.ok) return;
        const data = await r.json();

        _memo.currentMemoId = data.id;
        _memo.currentMemoMd = data.memo_markdown;

        const rendered = document.getElementById('memo-rendered');
        const empty = document.getElementById('memo-output-empty');
        const actions = document.getElementById('memo-output-actions');
        const meta = document.getElementById('memo-output-meta');

        if (rendered) rendered.innerHTML = data.memo_html;
        if (empty) empty.style.display = 'none';
        if (actions) actions.style.display = 'flex';
        if (meta) meta.textContent = `${data.model_used} · ${(data.input_token_count + data.output_token_count).toLocaleString()} tokens · ${data.created_at?.substring(0,16).replace('T',' ')}`;

        memoSwitchOutputTab('preview');
    } catch (e) {
        console.error(e);
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

// ── Init on tab switch ──────────────────────────────────────────────────────
const _origSwitchTab = switchTab;
switchTab = function(tab) {
    _origSwitchTab(tab);
    if (tab === 'memo') {
        memoLoadReports();
        memoLoadTemplates();
        memoInitUpload();
        driveLoadLibraries();
        if (_memo.sessionId) memoRefreshDocList();
    }
};
