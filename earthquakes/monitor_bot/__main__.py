#!/usr/bin/env python3
"""
Earthquake Monitor Bot entry point.

Usage:
    python -m monitor_bot
"""

import sys
import logging
from pathlib import Path
from datetime import datetime

# Ensure earthquakes directory is in path
sys.path.insert(0, str(Path(__file__).parent.parent))

from monitor_bot.ui.app import run_monitor_bot


def setup_logging():
    """Setup file logging with daily rotation."""
    # Create logs directory
    log_dir = Path(__file__).parent / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    # Log file with date
    log_file = log_dir / f"monitor_{datetime.now().strftime('%Y-%m-%d')}.log"

    # Configure root logger
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            # Don't add StreamHandler - TUI handles console output
        ],
    )

    logging.info("=" * 80)
    logging.info(f"Monitor Bot started - logging to {log_file}")
    logging.info("=" * 80)


if __name__ == "__main__":
    setup_logging()
    run_monitor_bot()
