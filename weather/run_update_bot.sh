#!/bin/bash

# =============================================================================
# WEATHER UPDATE BOT — Market Discovery
# =============================================================================

INTERVAL="6h"               # Rescan Gamma API every 6 hours

# =============================================================================
# STARTUP (don't modify below)
# =============================================================================

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Find Python: prefer .venv, then pyenv, then system
if [ -x ".venv/bin/python" ]; then
    PYTHON=".venv/bin/python"
else
    if [ -d "$HOME/.pyenv" ]; then
        export PYENV_ROOT="$HOME/.pyenv"
        export PATH="$PYENV_ROOT/bin:$PATH"
        eval "$(pyenv init - bash)" 2>/dev/null || true
    fi
    PYTHON="python3"
fi

echo -e "${BLUE}Starting Weather Update Bot...${NC}"
echo "  Interval: $INTERVAL"
echo "  Python:   $PYTHON"
echo ""

# Auto-restart loop
MAX_RESTARTS=50
RESTART_DELAY=5
restart_count=0

while true; do
    echo -e "${GREEN}Launching...${NC}"
    echo ""

    $PYTHON -m update_bot --interval "$INTERVAL"

    exit_code=$?

    if [ $exit_code -eq 0 ] || [ $exit_code -eq 130 ]; then
        echo -e "${BLUE}Bot stopped normally.${NC}"
        exit 0
    fi

    restart_count=$((restart_count + 1))
    if [ $restart_count -ge $MAX_RESTARTS ]; then
        echo -e "${YELLOW}Max restarts ($MAX_RESTARTS) reached. Stopping.${NC}"
        exit 1
    fi

    echo ""
    echo -e "${YELLOW}Crashed (exit $exit_code). Restart $restart_count/$MAX_RESTARTS in ${RESTART_DELAY}s...${NC}"
    sleep $RESTART_DELAY
done
