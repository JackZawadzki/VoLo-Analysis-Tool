# VoLo Underwriting Engine — Replit Deployment Guide

## Quick Start (5 minutes)

### 1. Import to Replit
- Go to [replit.com](https://replit.com) and click **Create Repl**
- Choose **Import from GitHub** (if you push to GitHub first) or **Upload folder**
- Upload the entire `volo-engine-local` folder contents

### 2. Add Your API Key
- Click the **Secrets** tab (lock icon in the left sidebar)
- Add a new secret:
  - **Key:** `ANTHROPIC_API_KEY`
  - **Value:** `sk-ant-...` (your Anthropic API key)
- This is required for the AI Deal Agent and Coding Agent features

### 3. Upload Data Files
The engine needs reference data files in `data/sources/`. Upload these files:

| File | Required | Source |
|------|----------|--------|
| `Carta Insights_Fund Forecasting Profiles.xlsx` | Yes | Carta |
| `Annual Tech Baseline 2024_v3_Workbook.xlsx` | Yes | NREL ATB |
| `VEBITDA - PubComps.xls` | Yes | Damodaran/NYU |
| `lazards-lcoeplus-june-2025-_vf.pdf` | Optional | Lazard |
| `data-appendix-electrification-pathways-063025.xlsx` | Optional | DOE |

To upload: click the Files tab, navigate to `data/sources/`, and drag files in.

### 4. Click Run
- Click the green **Run** button
- First run installs dependencies (~60 seconds)
- The app opens in the Webview panel

## Configuration

### Environment Variables (Replit Secrets)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | Yes | — | Claude API key for AI features |
| `JWT_SECRET` | No | Auto-generated | Secret for JWT token signing |
| `DEV_AGENT_MODEL` | No | `claude-haiku-4-5-20251001` | Model for coding agent |
| `PORT` | No | `8000` | Server port (Replit sets this) |

### Deployment
To deploy as a permanent web app:
1. Click the **Deploy** button in the top right
2. Choose **Reserved VM** or **Autoscale** (Reserved VM recommended)
3. The deployment config is pre-set in `.replit`

## Architecture

```
volo-engine-local/
├── app/                    # Python application
│   ├── main.py            # FastAPI routes & API endpoints
│   ├── auth.py            # JWT authentication
│   ├── database.py        # SQLite schema & migrations
│   ├── data/loader.py     # Data file loading
│   ├── engine/            # Simulation engines
│   │   ├── monte_carlo.py # Core Monte Carlo (5000 paths)
│   │   ├── adoption.py    # Bass diffusion S-curves
│   │   ├── fund_model.py  # Fund-level TVPI modeling
│   │   ├── rvm_carbon.py  # Carbon impact calculations
│   │   └── ...
│   ├── static/            # Frontend assets
│   │   ├── app.js         # All frontend JS
│   │   └── styles.css     # All styles
│   └── templates/
│       └── index.html     # Single-page app template
├── configs/               # JSON configuration files
├── data/
│   ├── sources/           # Reference data files (see above)
│   └── rvm.db            # SQLite DB (auto-created)
├── .replit                # Replit run configuration
├── replit.nix             # Nix system dependencies
├── requirements.txt       # Python dependencies
└── start.sh              # Startup script
```

## Troubleshooting

**"Module not found" errors**: Click Stop, then Run again. Dependencies may not have installed fully.

**"ANTHROPIC_API_KEY not configured"**: Add the key in Secrets tab. The simulation engine works without it, but AI chat features won't.

**"Missing data file" warnings**: Upload the required Excel files to `data/sources/`. The app starts without them but Carta benchmarks and Damodaran comps won't load.

**Port already in use**: Replit automatically assigns ports. If you see this, click Stop and wait 10 seconds before clicking Run again.

**SQLite locked errors**: This can happen with multiple concurrent requests. The app handles this gracefully — just retry the operation.
