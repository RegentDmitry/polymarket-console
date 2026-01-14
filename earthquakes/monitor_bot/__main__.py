#!/usr/bin/env python3
"""
Earthquake Monitor Bot entry point.

Usage:
    python -m monitor_bot
"""

import sys
from pathlib import Path

# Ensure earthquakes directory is in path
sys.path.insert(0, str(Path(__file__).parent.parent))

from monitor_bot.ui.app import run_monitor_bot

if __name__ == "__main__":
    run_monitor_bot()
