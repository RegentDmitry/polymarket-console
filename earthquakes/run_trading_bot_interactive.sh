#!/bin/bash

# Interactive Trading Bot Launcher for WSL
# This script ensures proper TTY allocation

set -e

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}Starting Trading Bot (Interactive Mode)...${NC}"
echo ""

# Check if venv exists
if [ ! -d ".venv" ]; then
    echo -e "${RED}✗ Virtual environment not found!${NC}"
    echo "  Run ./install.sh first"
    exit 1
fi

# Check if .env exists
if [ ! -f ".env" ]; then
    echo -e "${RED}⚠️  Warning: .env file not found${NC}"
    echo "  Create .env from .env.example and configure your API keys"
    echo ""
fi

# Run trading bot with explicit TTY allocation
echo -e "${GREEN}✓ Launching trading_bot with interactive terminal...${NC}"
echo ""

# Use exec to replace shell with python process
# This ensures proper signal handling and TTY allocation
exec .venv/bin/python -m trading_bot "$@"
