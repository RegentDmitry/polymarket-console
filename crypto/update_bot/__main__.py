"""
Entry point for crypto update bot.

Usage:
    cd crypto && python -m update_bot
    cd crypto && python -m update_bot --interval 12h
"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from .config import parse_args
from .ui.app import run_update_bot


def main():
    """Main entry point."""
    config = parse_args()
    run_update_bot(config)


if __name__ == "__main__":
    main()
