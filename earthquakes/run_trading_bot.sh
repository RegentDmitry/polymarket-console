#!/bin/bash

# Trading Bot Launcher
# Usage: ./run_trading_bot.sh [--live] [--auto] [--interval 5m]

set -e

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
BLUE='\033[0;34m'
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

# Run trading bot with all passed arguments
echo -e "${GREEN}✓ Launching trading_bot...${NC}"
echo ""

# Use exec to replace shell with python process
# This ensures proper TTY allocation and signal handling
exec python -m trading_bot "$@"
