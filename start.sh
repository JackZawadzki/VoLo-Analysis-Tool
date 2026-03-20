#!/bin/bash
# ──────────────────────────────────────────────────────────────
# VoLo Engine — One-Click Launcher (Local + Replit)
# Run:  chmod +x start.sh && ./start.sh
# ──────────────────────────────────────────────────────────────
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo "  ┌─────────────────────────────────────────────┐"
echo "  │  VoLo Earth — Quantitative Underwriting Engine  │"
echo "  └─────────────────────────────────────────────┘"
echo ""

# Detect if running on Replit
IS_REPLIT=false
if [ -n "$REPL_ID" ] || [ -n "$REPLIT_DB_URL" ]; then
    IS_REPLIT=true
    echo "✓  Detected Replit environment"
fi

# 1. Check Python
if command -v python3 &>/dev/null; then
    PY=python3
elif command -v python &>/dev/null; then
    PY=python
else
    echo "❌  Python 3.10+ is required. Install from https://python.org"
    exit 1
fi

PY_VER=$($PY -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "✓  Python $PY_VER found"

# 2. Create virtual environment if needed (skip on Replit — it manages its own)
if [ "$IS_REPLIT" = false ]; then
    if [ ! -d ".venv" ]; then
        echo "→  Creating virtual environment..."
        $PY -m venv .venv
    fi
    source .venv/bin/activate
    echo "✓  Virtual environment activated"
fi

# 3. Install dependencies
echo "→  Installing dependencies (first run may take ~60s)..."
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
echo "✓  All dependencies installed"

# 4. Check .env / Replit Secrets
if [ "$IS_REPLIT" = true ]; then
    # On Replit, check if ANTHROPIC_API_KEY is set via Secrets
    if [ -z "$ANTHROPIC_API_KEY" ]; then
        echo ""
        echo "⚠️   ANTHROPIC_API_KEY not found in Replit Secrets."
        echo "→  Go to the Secrets tab (lock icon) and add:"
        echo "   Key:   ANTHROPIC_API_KEY"
        echo "   Value: sk-ant-..."
        echo "   (AI chat and extraction features require this.)"
        echo ""
    else
        echo "✓  ANTHROPIC_API_KEY found in Secrets"
    fi
else
    if [ ! -f ".env" ]; then
        echo ""
        echo "⚠️   No .env file found. Creating from template..."
        cp .env.example .env
        echo "→  Edit .env to add your ANTHROPIC_API_KEY for AI features."
        echo ""
    fi
fi

# 5. Check data sources
REQUIRED_FILES=("Carta Insights_Fund Forecasting Profiles.xlsx" "Annual Tech Baseline 2024_v3_Workbook.xlsx" "VEBITDA - PubComps.xls")
MISSING=0
for f in "${REQUIRED_FILES[@]}"; do
    if [ ! -f "data/sources/$f" ]; then
        echo "⚠️   Missing data file: data/sources/$f"
        MISSING=1
    fi
done

if [ $MISSING -eq 1 ]; then
    echo ""
    echo "→  Some data files are missing. The app will still start, but"
    echo "   certain features (Carta benchmarks, NREL data, Damodaran comps)"
    echo "   won't be available. See data/sources/FILES.md for details."
    echo ""
fi

# 6. Ensure data directories exist
mkdir -p data/sources configs

# 7. Launch server
PORT=${PORT:-8000}
echo ""
echo "🚀  Starting VoLo Engine on http://0.0.0.0:$PORT"
echo "    Press Ctrl+C to stop."
echo ""

# Open browser after a short delay (macOS only, not on Replit)
if [ "$IS_REPLIT" = false ] && [[ "$OSTYPE" == "darwin"* ]]; then
    (sleep 2 && open "http://localhost:$PORT") &
fi

uvicorn app.main:app --host 0.0.0.0 --port $PORT --reload
