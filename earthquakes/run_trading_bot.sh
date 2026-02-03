#!/bin/bash

# Trading Bot Launcher
# Usage: ./run_trading_bot.sh [--live] [--auto] [--interval 5m]

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${BLUE}Starting Trading Bot...${NC}"
echo ""

# Check if venv exists
if [ ! -d ".venv" ]; then
    echo -e "${RED}✗ Virtual environment not found!${NC}"
    echo "  Run ./install.sh first"
    exit 1
fi

# Activate venv
source .venv/bin/activate

# Check if .env exists
if [ ! -f ".env" ]; then
    echo -e "${RED}⚠️  Warning: .env file not found${NC}"
    echo "  Create .env from .env.example and configure your API keys"
    echo ""
fi

# Auto-restart loop
MAX_RESTARTS=50
RESTART_DELAY=5
restart_count=0

while true; do
    echo -e "${GREEN}✓ Launching trading_bot...${NC}"
    echo ""

    python -m trading_bot "$@"
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
