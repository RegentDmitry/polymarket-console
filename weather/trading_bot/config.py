"""
Weather bot configuration and CLI argument parsing.
"""

import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class WeatherBotConfig:
    """Main bot configuration for weather trading."""

    # Trading mode
    auto_mode: bool = False
    dry_run: bool = True
    scan_once: bool = False  # Run one scan and exit
    observe_only: bool = False  # Scan + resolve, but no buy/sell

    # Scan settings
    scan_interval: int = 300        # 5 minutes (PM price refresh)
    # Forecast refresh: automatic via S3 meta.json (no fixed interval)

    # Strategy parameters
    min_edge: float = 0.08          # 8% — calibrated on 14 months previous_day1 data
    max_edge_cap: float = float("inf")  # No cap — calibrated sigma prevents false edges
    min_market_price: float = 0.08  # Don't buy buckets cheaper than 8%
    min_hours_to_expiry: float = 12  # Don't buy if <12h to resolution
    skip_cities: list = field(default_factory=list)  # No blanket skip — per-city sigma handles it
    kelly_divisor: float = 4.0      # Quarter-Kelly
    last_forecast_only: bool = True  # Only trade after all models have fresh data (last run of day)

    # Portfolio risk limits (Kelly proportional + hard caps)
    max_per_bucket: float = float("inf")  # No per-bucket limit (was $50)
    max_per_event: float = 200.0    # Max $ per event (city+date) — correlated!
    max_per_city: float = 500.0     # Max $ per city (all dates)
    max_position_pct: float = 0.30  # Max 30% of portfolio per position
    min_position_size: float = 2.0  # Skip if < $2 (allows more diversification)
    target_alloc: float = 1.0       # Fraction of portfolio to invest

    # Paths
    data_dir: Path = field(default_factory=lambda: Path("trading_bot/data"))
    active_dir: Path = field(default_factory=lambda: Path("trading_bot/data/active"))
    history_dir: Path = field(default_factory=lambda: Path("trading_bot/data/history"))
    markets_json: Path = field(default_factory=lambda: Path("weather_markets.json"))
    cities_json: Path = field(default_factory=lambda: Path("cities.json"))

    # API (loaded from environment)
    api_key: Optional[str] = None
    private_key: Optional[str] = None

    # PostgreSQL forecast logging
    db_url: Optional[str] = None

    def __post_init__(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.active_dir.mkdir(parents=True, exist_ok=True)
        self.history_dir.mkdir(parents=True, exist_ok=True)


def parse_interval(interval_str: str) -> int:
    """Parse interval string to seconds. E.g. "5m" -> 300."""
    s = interval_str.lower().strip()
    if s.endswith('s'):
        return int(s[:-1])
    elif s.endswith('m'):
        return int(s[:-1]) * 60
    elif s.endswith('h'):
        return int(s[:-1]) * 3600
    return int(s)


def parse_args() -> WeatherBotConfig:
    """Parse command line arguments and return config."""
    parser = argparse.ArgumentParser(
        description="Polymarket Weather Trading Bot (Temperature Markets)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m trading_bot                     # dry-run, 5m interval
  python -m trading_bot --live              # live trading with confirmation
  python -m trading_bot --live --auto       # live, no confirmation
  python -m trading_bot --scan-once         # single scan and exit
        """
    )

    parser.add_argument("--interval", "-i", type=str, default="5m",
                        help="Price scan interval (e.g., 1m, 5m). Default: 5m")
    parser.add_argument("--auto", "-a", action="store_true",
                        help="AUTO mode (trade without confirmation)")
    parser.add_argument("--live", "-l", action="store_true",
                        help="Enable live trading (default: dry-run)")
    parser.add_argument("--observe", action="store_true",
                        help="Observe mode: scan + resolve positions, no buy/sell")
    parser.add_argument("--scan-once", action="store_true",
                        help="Run one scan and exit (no TUI)")
    parser.add_argument("--min-edge", type=float, default=0.08,
                        help="Minimum edge to enter. Default: 0.08 (8%%)")
    parser.add_argument("--max-edge", type=float, default=float("inf"),
                        help="Max edge cap. Default: unlimited")
    parser.add_argument("--min-price", type=float, default=0.08,
                        help="Min market price to buy. Default: 0.08 (8%%)")
    parser.add_argument("--kelly-div", type=float, default=4.0,
                        help="Kelly divisor (4=quarter-Kelly). Default: 4")
    parser.add_argument("--min-hours", type=float, default=12,
                        help="Min hours to expiry. Default: 12")
    parser.add_argument("--max-bucket", type=float, default=float("inf"),
                        help="Max $ per bucket. Default: unlimited")
    parser.add_argument("--max-event", type=float, default=200.0,
                        help="Max $ per event (city+date). Default: 200")
    parser.add_argument("--max-city", type=float, default=500.0,
                        help="Max $ per city. Default: 500")
    parser.add_argument("--alloc", type=float, default=1.0,
                        help="Target allocation (0.0-1.0). Default: 1.0")
    parser.add_argument("--data-dir", type=Path, default=Path("trading_bot/data"),
                        help="Data directory. Default: trading_bot/data")
    parser.add_argument("--markets-json", type=Path, default=Path("weather_markets.json"),
                        help="Markets JSON. Default: weather_markets.json")
    parser.add_argument("--skip-cities", type=str, default="",
                        help="Comma-separated cities to skip. Default: none")
    parser.add_argument("--db-url", type=str, default=None,
                        help="PostgreSQL URL for forecast logging. E.g. postgresql://user:pass@host/db")

    args = parser.parse_args()

    skip = [c.strip() for c in args.skip_cities.split(",") if c.strip()]

    return WeatherBotConfig(
        auto_mode=args.auto,
        dry_run=not args.live and not args.observe,
        observe_only=args.observe,
        scan_once=args.scan_once,
        scan_interval=parse_interval(args.interval),
        min_edge=args.min_edge,
        max_edge_cap=args.max_edge,
        min_market_price=args.min_price,
        kelly_divisor=args.kelly_div,
        min_hours_to_expiry=args.min_hours,
        max_per_bucket=args.max_bucket,
        max_per_event=args.max_event,
        max_per_city=args.max_city,
        skip_cities=skip,
        target_alloc=args.alloc,
        data_dir=args.data_dir,
        active_dir=args.data_dir / "active",
        history_dir=args.data_dir / "history",
        markets_json=args.markets_json,
        db_url=args.db_url,
    )


def format_interval(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h"
