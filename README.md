# VoLo Earth Ventures -- Quantitative Underwriting Engine

Deterministic, institution-grade venture underwriting engine for early-stage energy
and industrial technology startups. Top-down probabilistic modeling using calibrated
adoption curves, stage-specific capital formation, and Monte Carlo simulation.

## Quick Start (Local)

```bash
# 1. Clone the repo
git clone <repo-url>
cd volo-engine

# 2. Create a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Edit .env — at minimum set ANTHROPIC_API_KEY for deck extraction

# 5. Add data files
# Place required .xlsx/.xls files in data/sources/
# See data/sources/FILES.md for the full list

# 6. Run the server
uvicorn app.main:app --reload --port 8000

# 7. Open http://localhost:8000
```

## Quick Start (Docker)

```bash
# 1. Configure environment
cp .env.example .env
# Edit .env with your ANTHROPIC_API_KEY

# 2. Place data files in data/sources/

# 3. Build and run
docker compose up --build -d

# 4. Open http://localhost:8000

# View logs
docker compose logs -f

# Stop
docker compose down
```

## Project Structure

```
volo-engine/
  app/
    main.py                  # FastAPI application + all API endpoints
    auth.py                  # JWT authentication
    database.py              # SQLite database (users, companies, reports)
    static/
      app.js                 # Frontend logic (Chart.js visualizations)
      styles.css             # Enterprise design system
      logo.png               # VoLo Earth logo
    templates/
      index.html             # Single-page app
      report_pdf.html        # Jinja2 template for PDF export
    routes/
      deal_pipeline.py       # Deal pipeline API (run, reports, PDF, CSV export)
      extraction.py          # Document extraction (deck, model, enrichment)
      companies.py           # Company CRUD
      resources.py           # Carbon intensity resources
      criteria.py            # Screening criteria
      portfolio_rvm.py       # RVM portfolio integration
    engine/
      adoption.py            # Bass diffusion S-curves, 17 technology archetypes
      dilution.py            # Stage-by-stage dilution simulation (Carta data)
      deal_report.py         # Unified deal report orchestrator
      extraction.py          # Claude-powered pitch deck extraction (two-pass)
      financial_pipeline.py  # Excel financial model extraction (cell-level)
      fund_model.py          # Fund-level J-curve, DPI, TVPI, IRR
      market_sizing.py       # TAM / SAM / SOM framework
      monte_carlo.py         # Core deal-level Monte Carlo (weighted exit years)
      position_sizing.py     # Kelly criterion + 250k grid search optimization
      rvm_carbon.py          # Carbon impact model (operating + embodied)
      valuation_comps.py     # Damodaran EV/EBITDA public comps parser
      portfolio/             # Vectorized portfolio simulator
        simulator.py         # VCSimulator class
        config.py            # StrategyConfig, DealConfig dataclasses
        benchmarks.py        # Carta TVPI benchmark loader
        calibration.py       # Grid-search calibration to benchmarks
        convergence.py       # Convergence-driven simulation sizing
        irr.py               # Vectorized IRR (Newton's method)
  configs/
    strategy.json            # Portfolio simulation defaults
    deal.json                # Example deal config
    carta_benchmarks.json    # Carta TVPI percentiles (p10/p50/p75/p90)
  data/
    sources/                 # Source data files (gitignored)
      FILES.md               # Manifest of required files
  Dockerfile
  docker-compose.yml
  requirements.txt
  .env.example
```

## Features

### Deal Pipeline (Unified Workflow)
- Upload pitch deck (PDF/PPTX/DOCX) for AI extraction via Claude
- Upload financial model (Excel) for cell-level extraction with provenance
- 10-year extrapolation of extracted financials using growth rate inference
- Full Monte Carlo simulation (5,000 paths) with weighted exit years 4-7
- Deal report generation: in-app rendering + PDF export

### Monte Carlo Engine
- Bass diffusion adoption curves calibrated from NREL ATB data
- 17 technology archetypes (energy, software, hard tech, AI, custom)
- Stage-by-stage dilution using Carta round-by-round data
- TRL-aware survival penalties, capital intensity, exit multiple discounts
- Variance decomposition (Spearman rank correlation)
- Sensitivity analysis (tornado charts)

### Position Sizing
- Kelly Criterion (binary, N-outcome, MOIC-distribution)
- Half-Kelly / Quarter-Kelly practical variants
- Fund constraint sizing (concentration limits, reserves)
- 250k-increment grid search with 3-parameter optimization
  - Minimize P(MOIC < 2x) — left tail
  - Maximize P(MOIC > 10x) — right tail
  - Maximize P50 MOIC — median outcome

### Valuation
- Damodaran EV/EBITDA industry comps (97 industries)
- 20% acquisition discount (Kengelbach et al., BCG)
- Archetype-to-industry mapping for automatic multiple derivation
- TAM/SAM/SOM framework with (i) info buttons for data sources

### Carbon Impact
- Operating carbon (resource displacement model)
- Embodied carbon (lifecycle analysis)
- Risk-adjusted tonnes per dollar
- Auto-derived risk divisor from TRL

### Portfolio Simulation
- Vectorized multi-company Monte Carlo (VCSimulator)
- Carta TVPI benchmark overlay
- Deal marginal impact analysis (TVPI/IRR lift)
- DPI time series
- CSV export of all deal reports
- Portfolio holdings upload (CSV/JSON)

### Extraction
- Pitch deck: two-pass Claude extraction (structured fields)
- Financial model: cell-level Excel extraction with evidence images
- Web enrichment: deep scraping + Claude analysis
- 10-year forward extrapolation of extracted financials

## Data Sources

- **Carta** -- Fund Forecasting Profiles (round sizes, graduation rates, pre-money)
- **NREL ATB 2024** -- Annual Technology Baseline (LCOE projections, adoption data)
- **Lazard LCOE+ v18** -- Levelized cost of energy benchmarks
- **Damodaran** -- EV/EBITDA multiples for 97 US public company industries

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | For deck extraction | Claude API key |
| `JWT_SECRET` | No (auto-generated) | JWT signing secret |
| `HOST` | No (0.0.0.0) | Server bind address |
| `PORT` | No (8000) | Server port |

## Tech Stack

- **Backend**: Python 3.11+, FastAPI, NumPy, SciPy
- **Frontend**: Vanilla JS, Chart.js, Jinja2 templates
- **Database**: SQLite (auth, companies, reports, holdings)
- **AI**: Anthropic Claude (extraction, enrichment)
- **PDF**: WeasyPrint (server-side rendering)
- **Container**: Docker + docker-compose
