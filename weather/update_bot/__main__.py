"""
Entry point for weather market update bot.

Usage:
    cd weather && python -m update_bot          # TUI mode (periodic)
    cd weather && python -m update_bot --once   # single scan, no TUI
"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from .config import parse_args
from .scanner import WeatherMarketScanner


def run_once(config):
    """Single scan: discover markets and save to JSON."""
    scanner = WeatherMarketScanner()

    print("Scanning Gamma API for weather temperature markets...")
    entries = scanner.search_markets(
        progress_callback=lambda msg: print(f"  {msg}")
    )

    if not entries:
        print("\nNo weather markets found.")
        return

    # Group by city+date for display
    events = {}
    for e in entries:
        key = f"{e.city} {e.date}"
        events.setdefault(key, []).append(e)

    print(f"\nFound {len(entries)} buckets across {len(events)} events:\n")

    for key in sorted(events.keys()):
        buckets = events[key]
        city = buckets[0].city.replace("-", " ").title()
        date = buckets[0].date
        print(f"  {city:<16} {date}  ({len(buckets)} buckets)")

    # Save
    scanner.save_to_json(entries, config.markets_json)
    print(f"\nSaved to {config.markets_json}")


def main():
    config = parse_args()

    if config.once:
        run_once(config)
    else:
        from .ui.app import run_update_bot
        run_update_bot(config)


if __name__ == "__main__":
    main()
