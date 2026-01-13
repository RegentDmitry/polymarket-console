"""
Bot configuration and CLI argument parsing.
"""

import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class BotConfig:
    """Main bot configuration."""

    # Trading mode
    auto_mode: bool = False  # False = CONFIRM, True = AUTO
    dry_run: bool = True     # True = don't execute trades, just show signals (default)

    # Scan settings
    scan_interval: int = 300  # seconds (5 minutes default)

    # Position limits
    max_positions: int = 20

    # Strategy parameters
    min_edge: float = 0.01  # 1% (noise protection)
    min_apy: float = 0.30   # 30% annualized

    # Paths
    data_dir: Path = field(default_factory=lambda: Path("bot/data"))
    active_dir: Path = field(default_factory=lambda: Path("bot/data/active"))
    history_dir: Path = field(default_factory=lambda: Path("bot/data/history"))

    # API (loaded from environment)
    api_key: Optional[str] = None
    private_key: Optional[str] = None

    def __post_init__(self):
        """Ensure directories exist."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.active_dir.mkdir(parents=True, exist_ok=True)
        self.history_dir.mkdir(parents=True, exist_ok=True)


def parse_interval(interval_str: str) -> int:
    """
    Parse interval string to seconds.
    Examples: "5m" -> 300, "1h" -> 3600, "30s" -> 30
    """
    interval_str = interval_str.lower().strip()

    if interval_str.endswith('s'):
        return int(interval_str[:-1])
    elif interval_str.endswith('m'):
        return int(interval_str[:-1]) * 60
    elif interval_str.endswith('h'):
        return int(interval_str[:-1]) * 3600
    else:
        # Assume seconds if no suffix
        return int(interval_str)


def parse_args() -> BotConfig:
    """Parse command line arguments and return config."""

    parser = argparse.ArgumentParser(
        description="Polymarket Trading Bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m bot                          # dry-run mode (default)
  python -m bot --live                   # live trading with confirmation
  python -m bot --live --auto            # live trading without confirmation
  python -m bot --interval 1h
        """
    )

    parser.add_argument(
        "--interval", "-i",
        type=str,
        default="5m",
        help="Scan interval (e.g., 30s, 5m, 1h). Default: 5m"
    )

    parser.add_argument(
        "--auto", "-a",
        action="store_true",
        help="Enable AUTO mode (execute trades without confirmation)"
    )

    parser.add_argument(
        "--live", "-l",
        action="store_true",
        help="Enable live trading (default is dry-run mode)"
    )

    parser.add_argument(
        "--max-positions", "-m",
        type=int,
        default=20,
        help="Maximum number of open positions. Default: 20"
    )

    parser.add_argument(
        "--min-edge",
        type=float,
        default=0.01,
        help="Minimum edge to enter. Default: 0.01 (1%%)"
    )

    parser.add_argument(
        "--min-apy",
        type=float,
        default=0.30,
        help="Minimum APY (annualized return) to enter. Default: 0.30 (30%%)"
    )

    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("bot/data"),
        help="Data directory. Default: bot/data"
    )

    args = parser.parse_args()

    return BotConfig(
        auto_mode=args.auto,
        dry_run=not args.live,  # dry-run by default, --live disables it
        scan_interval=parse_interval(args.interval),
        max_positions=args.max_positions,
        min_edge=args.min_edge,
        min_apy=args.min_apy,
        data_dir=args.data_dir,
        active_dir=args.data_dir / "active",
        history_dir=args.data_dir / "history",
    )


def format_interval(seconds: int) -> str:
    """Format seconds as human readable interval."""
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        return f"{seconds // 60}m"
    else:
        return f"{seconds // 3600}h"
