#!/bin/bash

# =============================================================================
# WEATHER TRADING BOT — CONFIGURATION
# =============================================================================

# --- Signal Filters ---
MIN_EDGE="0.10"              # 10% minimum edge
MIN_HOURS="12"               # Don't buy if <12h to expiry

# --- Limits ---
MAX_PER_BUCKET="50"          # $50 max per bucket
MAX_PER_EVENT="200"          # $200 max per event (city+date)
MAX_PER_CITY="500"           # $500 max per city

# --- Scan Settings ---
SCAN_INTERVAL="5m"           # Price scan every 5 min
# Forecast refresh: automatic via S3 meta.json (detects new model runs)

# --- Trading Mode ---
# (no flags)       : dry-run (default)
# --live           : real trades with confirmation
# --live --auto    : fully automatic trading
MODE="--live --auto"

# =============================================================================
# STARTUP (don't modify below)
# =============================================================================

GREEN='\033[0;32m'
RED='\033[0;31m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Find Python: prefer .venv, then ../.venv, then pyenv, then system
if [ -x ".venv/bin/python" ]; then
    PYTHON=".venv/bin/python"
elif [ -x "../.venv/bin/python" ]; then
    PYTHON="../.venv/bin/python"
else
    if [ -d "$HOME/.pyenv" ]; then
        export PYENV_ROOT="$HOME/.pyenv"
        export PATH="$PYENV_ROOT/bin:$PATH"
        eval "$(pyenv init - bash)" 2>/dev/null || true
    fi
    PYTHON="python3"
fi

echo -e "${BLUE}Starting Weather Trading Bot...${NC}"
echo ""
echo "Configuration:"
echo "  Min Edge:      $MIN_EDGE"
echo "  Min Hours:     ${MIN_HOURS}h"
echo "  Limits:        \$$MAX_PER_BUCKET/bucket \$$MAX_PER_EVENT/event \$$MAX_PER_CITY/city"
echo "  Scan:          $SCAN_INTERVAL"
echo "  Forecast:      smart (S3 meta.json)"
echo "  Mode:          ${MODE:-dry-run}"
echo "  Python:        $PYTHON"
echo ""

if [ ! -f ".env" ]; then
    echo -e "${YELLOW}Warning: .env file not found${NC}"
    echo ""
fi

# Auto-restart loop
MAX_RESTARTS=50
RESTART_DELAY=5
restart_count=0

while true; do
    echo -e "${GREEN}Launching...${NC}"
    echo ""

    $PYTHON -m trading_bot \
        --min-edge "$MIN_EDGE" \
        --min-hours "$MIN_HOURS" \
        --max-bucket "$MAX_PER_BUCKET" \
        --max-event "$MAX_PER_EVENT" \
        --max-city "$MAX_PER_CITY" \
        --interval "$SCAN_INTERVAL" \
        $MODE

    exit_code=$?

    if [ $exit_code -eq 0 ] || [ $exit_code -eq 130 ]; then
        echo -e "${BLUE}Bot stopped normally.${NC}"
        exit 0
    fi

    restart_count=$((restart_count + 1))
    if [ $restart_count -ge $MAX_RESTARTS ]; then
        echo -e "${RED}Max restarts ($MAX_RESTARTS) reached. Stopping.${NC}"
        exit 1
    fi

    echo ""
    echo -e "${YELLOW}Crashed (exit $exit_code). Restart $restart_count/$MAX_RESTARTS in ${RESTART_DELAY}s...${NC}"
    sleep $RESTART_DELAY
done
