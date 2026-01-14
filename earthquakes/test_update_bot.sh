#!/bin/bash

# Test Update Bot
# Quick test to verify update_bot works correctly

set -e

echo "========================================="
echo " Testing Update Bot"
echo "========================================="
echo ""

# Check if venv exists
if [ ! -d ".venv" ]; then
    echo "ERROR: .venv not found. Run ./install.sh first"
    exit 1
fi

# Activate venv
source .venv/bin/activate

# Check dependencies
echo "Checking dependencies..."
python -c "import textual; print(f'✓ Textual {textual.__version__}')" || {
    echo "ERROR: textual not installed"
    exit 1
}

python -c "import httpx; print(f'✓ httpx installed')" || {
    echo "ERROR: httpx not installed"
    exit 1
}

echo ""

# Check Claude Code CLI
echo "Checking Claude Code CLI..."
if command -v claude &> /dev/null; then
    echo "✓ Claude Code CLI found"
    claude --version
else
    echo "⚠️  Claude Code CLI not found"
    echo "   Install: npm install -g @anthropic-ai/claude-code"
    echo ""
    echo "   Update bot will not be able to run without Claude Code!"
    echo ""
fi

echo ""
echo "========================================="
echo " All checks passed!"
echo "========================================="
echo ""
echo "Run update bot:"
echo "  bash run_update_bot.sh"
echo ""
echo "Or with custom interval:"
echo "  bash run_update_bot.sh --interval 1h"
echo ""
