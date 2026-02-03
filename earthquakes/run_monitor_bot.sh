#!/bin/bash
# Run Earthquake Monitor Bot

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Load pyenv if available (for Linux servers)
if [ -d "$HOME/.pyenv" ]; then
    export PYENV_ROOT="$HOME/.pyenv"
    export PATH="$PYENV_ROOT/bin:$PATH"
    eval "$(pyenv init - bash)" 2>/dev/null || true
fi

echo "=================================================="
echo "Earthquake Monitor Bot"
echo "=================================================="
echo ""

# Check if .env exists
if [ ! -f ".env" ]; then
    echo "⚠ .env file not found!"
    echo ""
    echo "Creating from .env.example..."
    if [ -f ".env.example" ]; then
        cp .env.example .env
        echo "✓ Created .env - please edit with your database credentials"
        echo ""
    else
        echo "❌ .env.example not found!"
        exit 1
    fi
fi

# Load environment
set -a
source .env
set +a

# Check if venv exists
if [ ! -d ".venv" ]; then
    echo "⚠ Virtual environment not found!"
    echo ""
    echo "Creating virtual environment..."
    python3 -m venv .venv
    echo "✓ Virtual environment created"
    echo ""
fi

# Activate venv
echo "Activating virtual environment..."
source .venv/bin/activate

# Install dependencies
echo "Checking dependencies..."
if ! python -c "import textual" 2>/dev/null; then
    echo "Installing monitor_bot dependencies..."
    pip install -q -r monitor_bot/requirements.txt
    echo "✓ Dependencies installed"
else
    echo "✓ Dependencies OK"
fi

echo ""
echo "Starting Monitor Bot..."
echo ""

# Run monitor bot
python -m monitor_bot
