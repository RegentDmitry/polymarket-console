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

# Change to script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Load pyenv if available (for Linux servers)
if [ -d "$HOME/.pyenv" ]; then
    export PYENV_ROOT="$HOME/.pyenv"
    export PATH="$PYENV_ROOT/bin:$PATH"
    eval "$(pyenv init - bash)" 2>/dev/null || true
fi

echo -e "${BLUE}Starting Update Bot...${NC}"
echo ""

# Check if venv exists, create if not
if [ ! -d ".venv" ]; then
    echo -e "${YELLOW}Virtual environment not found. Creating...${NC}"
    python3 -m venv .venv
    echo -e "${GREEN}✓ Virtual environment created${NC}"
fi

# Activate venv
source .venv/bin/activate

# Check and install dependencies
if ! python -c "import anthropic" 2>/dev/null; then
    echo -e "${YELLOW}Installing dependencies...${NC}"
    pip install -q -r requirements.txt
    pip install -q anthropic
    echo -e "${GREEN}✓ Dependencies installed${NC}"
fi

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
