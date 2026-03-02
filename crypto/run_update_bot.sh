#!/bin/bash

# Update Bot Launcher — discovers BTC/ETH markets on Polymarket
# Usage: ./run_update_bot.sh

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

echo -e "${BLUE}Starting Crypto Update Bot...${NC}"
echo ""

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
    "$PIP" install -q textual rich
    echo -e "${GREEN}Dependencies installed${NC}"
fi

echo -e "${GREEN}Launching update_bot...${NC}"
echo ""
"$PYTHON" -m update_bot "$@"
