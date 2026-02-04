#!/bin/bash

# =============================================================================
# EARTHQUAKE TRADING BOT — CONFIGURATION
# =============================================================================

# --- Signal Filters ---
MIN_EDGE="0.01"              # 1% minimum edge
MIN_APY="0.20"               # 20% minimum APY

# --- Reserve Balance (for early detection) ---
RESERVE_BALANCE="1000"       # $1000 kept for info advantage situations
RESERVE_MIN_CERTAINTY="0.90" # 90% fair = "верняк" (certainty threshold)
RESERVE_MIN_ROI="0.50"       # 50% ROI = very profitable

# --- Scan Settings ---
SCAN_INTERVAL="1m"           # Scan every 1 minute

# --- Trading Mode ---
# --dry-run        : simulation only (no real trades)
# --live           : real trades with confirmation
# --live --auto    : fully automatic trading
MODE="--live --auto"

# =============================================================================
# STARTUP (don't modify below)
# =============================================================================

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Change to script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Load pyenv if available (for Linux servers)
if [ -d "$HOME/.pyenv" ]; then
    export PYENV_ROOT="$HOME/.pyenv"
    export PATH="$PYENV_ROOT/bin:$PATH"
    eval "$(pyenv init - bash)" 2>/dev/null || true
fi

echo -e "${BLUE}Starting Trading Bot...${NC}"
echo ""
echo "Configuration:"
echo "  Min Edge:      $MIN_EDGE"
echo "  Min APY:       $MIN_APY"
echo "  Reserve:       \$$RESERVE_BALANCE"
echo "  Reserve ROI:   $RESERVE_MIN_ROI"
echo "  Scan Interval: $SCAN_INTERVAL"
echo "  Mode:          $MODE"
echo ""

# Check if .env exists
if [ ! -f ".env" ]; then
    echo -e "${YELLOW}Warning: .env file not found${NC}"
    if [ -f ".env.example" ]; then
        cp .env.example .env
        echo "  Created .env from .env.example - please configure your API keys"
    else
        echo "  Create .env and configure your API keys"
    fi
    echo ""
fi

# Check if venv exists, create if not
if [ ! -d ".venv" ]; then
    echo -e "${YELLOW}Virtual environment not found. Creating...${NC}"
    python3 -m venv .venv
    echo -e "${GREEN}Virtual environment created${NC}"
fi

# Activate venv
source .venv/bin/activate

# Check and install dependencies
if ! python -c "import textual" 2>/dev/null; then
    echo -e "${YELLOW}Installing dependencies...${NC}"
    pip install -q -r requirements.txt
    # Install polymarket_console from parent directory
    pip install -q -e ..
    echo -e "${GREEN}Dependencies installed${NC}"
fi

# Auto-restart loop
MAX_RESTARTS=50
RESTART_DELAY=5
restart_count=0

while true; do
    echo -e "${GREEN}Launching trading_bot...${NC}"
    echo ""

    python -m trading_bot \
        --min-edge "$MIN_EDGE" \
        --min-apy "$MIN_APY" \
        --reserve-balance "$RESERVE_BALANCE" \
        --reserve-min-certainty "$RESERVE_MIN_CERTAINTY" \
        --reserve-min-roi "$RESERVE_MIN_ROI" \
        --interval "$SCAN_INTERVAL" \
        $MODE

    exit_code=$?

    # Exit cleanly on Ctrl+C (130) or normal exit (0)
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
    echo -e "${YELLOW}Bot crashed (exit code: $exit_code). Restart $restart_count/$MAX_RESTARTS in ${RESTART_DELAY}s...${NC}"
    sleep $RESTART_DELAY
done
