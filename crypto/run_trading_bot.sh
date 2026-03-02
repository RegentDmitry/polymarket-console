#!/bin/bash

# =============================================================================
# CRYPTO TRADING BOT — CONFIGURATION
# =============================================================================

# --- Signal Filters ---
MIN_EDGE="0.05"              # 5% minimum edge
MIN_APY="0.30"               # 30% minimum APY

# --- Scan Settings ---
SCAN_INTERVAL="1m"           # Scan every 1 minute

# --- Trading Mode ---
# (no flags)         : dry-run with confirmation (default)
# --live             : real trades with confirmation
# --live --auto      : fully automatic trading
MODE="--live"

# --- Pricing ---
# (default)          : fast analytical pricing (~25ms)
# --mc-pricing       : MC Student-t simulation (~30s)
PRICING=""

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

# Load pyenv if available (for Linux servers)
if [ -d "$HOME/.pyenv" ]; then
    export PYENV_ROOT="$HOME/.pyenv"
    export PATH="$PYENV_ROOT/bin:$PATH"
    eval "$(pyenv init - bash)" 2>/dev/null || true
fi

echo -e "${BLUE}Starting Crypto Trading Bot...${NC}"
echo ""
echo "Configuration:"
echo "  Min Edge:      $MIN_EDGE"
echo "  Min APY:       $MIN_APY"
echo "  Scan Interval: $SCAN_INTERVAL"
echo "  Mode:          ${MODE:-(dry-run)}"
echo "  Pricing:       ${PRICING:-fast (default)}"
echo ""

# Check if .env exists
if [ ! -f ".env" ]; then
    echo -e "${YELLOW}Warning: .env file not found${NC}"
    if [ -f ".env.example" ]; then
        cp .env.example .env
        echo "  Created .env from .env.example — please configure your API keys"
    else
        echo "  Create .env and configure your API keys"
    fi
    echo ""
fi

# Check if venv exists, create if not
VENV_DIR="$SCRIPT_DIR/../.venv"
if [ ! -d "$VENV_DIR" ]; then
    echo -e "${YELLOW}Virtual environment not found. Creating...${NC}"
    python3 -m venv "$VENV_DIR"
    echo -e "${GREEN}Virtual environment created${NC}"
fi

PYTHON="$VENV_DIR/bin/python3"
PIP="$VENV_DIR/bin/pip"

# Check and install dependencies
if ! "$PYTHON" -c "import textual" 2>/dev/null; then
    echo -e "${YELLOW}Installing dependencies...${NC}"
    "$PIP" install -q textual rich numpy scipy
    echo -e "${GREEN}Dependencies installed${NC}"
fi

# Auto-restart loop
MAX_RESTARTS=50
RESTART_DELAY=5
restart_count=0

while true; do
    echo -e "${GREEN}Launching trading_bot...${NC}"
    echo ""

    "$PYTHON" -m trading_bot \
        --min-edge "$MIN_EDGE" \
        --min-apy "$MIN_APY" \
        --interval "$SCAN_INTERVAL" \
        $MODE \
        $PRICING

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
    echo -e "${YELLOW}Bot crashed (exit code: $exit_code). Restart $restart_count/$MAX_RESTARTS in ${RESTART_DELAY}s...${NC}"
    sleep $RESTART_DELAY
done
