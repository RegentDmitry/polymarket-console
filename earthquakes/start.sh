#!/bin/bash

# Earthquake Bots Launcher
# Interactive menu to start trading_bot or update_bot

set -e

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

clear
echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}     Earthquake Bots Launcher${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Check if venv exists
if [ ! -d ".venv" ]; then
    echo -e "${RED}✗ Virtual environment not found!${NC}"
    echo "  Run ./install.sh first"
    echo ""
    exit 1
fi

# Menu
echo -e "${CYAN}Select bot to launch:${NC}"
echo ""
echo "  1) Trading Bot (dry-run)"
echo "  2) Trading Bot (live, with confirmation)"
echo "  3) Trading Bot (live, auto mode)"
echo "  4) Update Bot"
echo "  5) Exit"
echo ""
read -p "Enter choice [1-5]: " choice

case $choice in
    1)
        echo ""
        echo -e "${GREEN}Launching Trading Bot in DRY RUN mode...${NC}"
        echo ""
        source .venv/bin/activate
        python -m trading_bot
        ;;
    2)
        echo ""
        echo -e "${GREEN}Launching Trading Bot in LIVE mode (with confirmation)...${NC}"
        echo ""
        source .venv/bin/activate
        python -m trading_bot --live
        ;;
    3)
        echo ""
        echo -e "${YELLOW}⚠️  Launching Trading Bot in AUTO mode (no confirmation)${NC}"
        echo ""
        read -p "Are you sure? This will execute trades automatically! (y/N) " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            echo -e "${RED}Cancelled.${NC}"
            exit 1
        fi
        echo ""
        source .venv/bin/activate
        python -m trading_bot --live --auto
        ;;
    4)
        echo ""
        # Check Claude Code
        if ! command -v claude &> /dev/null; then
            echo -e "${YELLOW}⚠️  Warning: Claude Code CLI not found${NC}"
            echo "  Update bot requires Claude Code to function"
            echo ""
        fi
        echo -e "${GREEN}Launching Update Bot...${NC}"
        echo ""
        source .venv/bin/activate
        python -m update_bot
        ;;
    5)
        echo ""
        echo -e "${BLUE}Goodbye!${NC}"
        exit 0
        ;;
    *)
        echo ""
        echo -e "${RED}Invalid choice${NC}"
        exit 1
        ;;
esac
