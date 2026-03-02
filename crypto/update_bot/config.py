"""
Update bot configuration and CLI argument parsing.
"""

import argparse
from dataclasses import dataclass
from pathlib import Path


@dataclass
class UpdateBotConfig:
    """Update bot configuration."""

    # Update interval in hours
    update_interval: int = 6  # hours

    # Paths
    markets_json: Path = Path("crypto_markets.json")

    # API settings
    api_timeout: int = 30  # seconds


def parse_interval(interval_str: str) -> int:
    """Parse interval string to hours. Examples: "6h" -> 6, "12h" -> 12."""
    interval_str = interval_str.lower().strip()

    if interval_str.endswith('h'):
        return int(interval_str[:-1])
    else:
        return int(interval_str)


def parse_args() -> UpdateBotConfig:
    """Parse command line arguments and return config."""

    parser = argparse.ArgumentParser(
        description="Polymarket Crypto Markets Update Bot (BTC/ETH)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m update_bot                   # Update every 6 hours (default)
  python -m update_bot --interval 12h    # Update every 12 hours
        """
    )

    parser.add_argument(
        "--interval", "-i",
        type=str,
        default="6h",
        help="Update interval (e.g., 6h, 12h, 24h). Default: 6h"
    )

    parser.add_argument(
        "--markets-json",
        type=Path,
        default=Path("crypto_markets.json"),
        help="Path to crypto markets JSON file. Default: crypto_markets.json"
    )

    args = parser.parse_args()

    return UpdateBotConfig(
        update_interval=parse_interval(args.interval),
        markets_json=args.markets_json,
    )


def format_interval(hours: int) -> str:
    """Format hours as human readable interval."""
    if hours < 24:
        return f"{hours}h"
    else:
        days = hours // 24
        return f"{days}d"
