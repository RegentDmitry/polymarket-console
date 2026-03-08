"""
Update bot configuration and CLI argument parsing.
"""

import argparse
from dataclasses import dataclass
from pathlib import Path


@dataclass
class UpdateBotConfig:
    """Update bot configuration."""
    update_interval: int = 6  # hours
    markets_json: Path = Path("weather_markets.json")
    once: bool = False


def format_interval(hours: int) -> str:
    if hours < 24:
        return f"{hours}h"
    return f"{hours // 24}d"


def parse_args() -> UpdateBotConfig:
    parser = argparse.ArgumentParser(
        description="Weather Market Discovery Bot (Polymarket)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m update_bot                   # TUI mode, update every 6h
  python -m update_bot --once            # Single scan, print results
  python -m update_bot --interval 12h    # Update every 12 hours
        """
    )

    parser.add_argument(
        "--once", action="store_true",
        help="Run a single scan and exit (no TUI)"
    )
    parser.add_argument(
        "--interval", "-i", type=str, default="6h",
        help="Update interval (e.g., 6h, 12h). Default: 6h"
    )
    parser.add_argument(
        "--markets-json", type=Path,
        default=Path("weather_markets.json"),
        help="Path to weather markets JSON. Default: weather_markets.json"
    )

    args = parser.parse_args()

    interval_str = args.interval.lower().strip()
    hours = int(interval_str.rstrip("h"))

    return UpdateBotConfig(
        update_interval=hours,
        markets_json=args.markets_json,
        once=args.once,
    )
