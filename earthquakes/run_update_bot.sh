#!/bin/bash

# Update Bot Launcher
# Usage: ./run_update_bot.sh [--interval 12h]

set -e

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${BLUE}Starting Update Bot...${NC}"
echo ""

# Check if venv exists
if [ ! -d ".venv" ]; then
    echo -e "${RED}✗ Virtual environment not found!${NC}"
    echo "  Run ./install.sh first"
    exit 1
fi

# Activate venv
source .venv/bin/activate

# Check Claude Code
if ! command -v claude &> /dev/null; then
    echo -e "${YELLOW}⚠️  Warning: Claude Code CLI not found${NC}"
    echo "  Update bot requires Claude Code to function"
    echo "  Install: npm install -g @anthropic-ai/claude-code"
    echo ""
    read -p "Continue anyway? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo -e "${RED}Cancelled.${NC}"
        exit 1
    fi
fi

# Run update bot with all passed arguments
echo -e "${GREEN}✓ Launching update_bot...${NC}"
echo ""
python -m update_bot "$@"
