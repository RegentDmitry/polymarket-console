#!/bin/bash

# Earthquake Bots Installation Script
# Usage: ./install.sh

set -e  # Exit on error

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  Earthquake Bots Installation${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Check if on Windows filesystem (WSL)
CURRENT_DIR=$(pwd)
if [[ "$CURRENT_DIR" == /mnt/* ]]; then
    echo -e "${YELLOW}⚠️  Warning: You are on Windows filesystem (/mnt/c/)${NC}"
    echo -e "${YELLOW}   Performance will be slow and venv may not work correctly.${NC}"
    echo ""
    echo -e "${BLUE}Recommended: Move project to Linux filesystem${NC}"
    echo -e "  cp -r \"$CURRENT_DIR\" ~/earthquakes"
    echo -e "  cd ~/earthquakes"
    echo -e "  ./install.sh"
    echo ""
    read -p "Continue anyway? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo -e "${RED}Installation cancelled.${NC}"
        exit 1
    fi
fi

# Check Python version
echo -e "${BLUE}[1/6] Checking Python...${NC}"
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}✗ Python 3 not found!${NC}"
    echo "  Install Python 3.10+ first"
    exit 1
fi

PYTHON_VERSION=$(python3 --version | cut -d' ' -f2)
echo -e "${GREEN}✓ Python $PYTHON_VERSION found${NC}"
echo ""

# Remove old venv if exists
if [ -d ".venv" ]; then
    echo -e "${BLUE}[2/6] Removing old virtual environment...${NC}"
    rm -rf .venv
    echo -e "${GREEN}✓ Old venv removed${NC}"
else
    echo -e "${BLUE}[2/6] No old virtual environment found${NC}"
fi
echo ""

# Create new venv
echo -e "${BLUE}[3/6] Creating virtual environment...${NC}"
python3 -m venv .venv
echo -e "${GREEN}✓ Virtual environment created${NC}"
echo ""

# Activate venv
echo -e "${BLUE}[4/6] Activating virtual environment...${NC}"
source .venv/bin/activate
echo -e "${GREEN}✓ Virtual environment activated${NC}"
echo ""

# Install dependencies
echo -e "${BLUE}[5/6] Installing dependencies...${NC}"
echo "  This may take a few minutes..."
python -m pip install --upgrade pip > /dev/null 2>&1
pip install -r requirements.txt

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ Dependencies installed successfully${NC}"
else
    echo -e "${RED}✗ Failed to install dependencies${NC}"
    exit 1
fi
echo ""

# Check Claude API key
echo -e "${BLUE}[6/6] Checking Claude API configuration...${NC}"
if [ -f ".env" ]; then
    if grep -q "ANTHROPIC_API_KEY=sk-ant-" .env; then
        echo -e "${GREEN}✓ Claude API key found in .env${NC}"
        echo -e "  Update bot will work correctly"
    else
        echo -e "${YELLOW}⚠️  ANTHROPIC_API_KEY not set in .env${NC}"
        echo -e "  Update bot requires Claude API key to function"
        echo ""
        echo -e "${BLUE}Get API key:${NC}"
        echo -e "  1. Visit https://console.anthropic.com/"
        echo -e "  2. Create API key in Settings → API Keys"
        echo -e "  3. Add to .env: ANTHROPIC_API_KEY=sk-ant-..."
    fi
else
    echo -e "${YELLOW}⚠️  .env file not found${NC}"
    echo -e "  Create .env file and add ANTHROPIC_API_KEY"
fi
echo ""

# Create .env if doesn't exist
if [ ! -f ".env" ]; then
    echo -e "${BLUE}Creating .env file from template...${NC}"
    if [ -f ".env.example" ]; then
        cp .env.example .env
        echo -e "${GREEN}✓ .env created${NC}"
        echo -e "${YELLOW}⚠️  Don't forget to edit .env with your API credentials!${NC}"
    else
        echo -e "${YELLOW}⚠️  .env.example not found, skipping${NC}"
    fi
    echo ""
fi

# Success message
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Installation Complete! ✓${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "${BLUE}Next steps:${NC}"
echo ""
echo -e "1. Configure API credentials:"
echo -e "   ${YELLOW}nano .env${NC}"
echo ""
echo -e "2. Activate virtual environment (in new terminal):"
echo -e "   ${YELLOW}source .venv/bin/activate${NC}"
echo ""
echo -e "3. Run trading bot (dry-run mode):"
echo -e "   ${YELLOW}python -m trading_bot${NC}"
echo ""
echo -e "4. Run update bot:"
echo -e "   ${YELLOW}python -m update_bot${NC}"
echo ""
echo -e "${BLUE}For live trading:${NC}"
echo -e "   ${YELLOW}python -m trading_bot --live${NC}"
echo -e "   ${YELLOW}python -m trading_bot --live --auto${NC}"
echo ""
echo -e "${BLUE}Documentation:${NC}"
echo -e "   README.md"
echo -e "   trading_bot/README.md"
echo -e "   update_bot/README.md"
echo ""
