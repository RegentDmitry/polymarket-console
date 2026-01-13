"""
Entry point for the trading bot.

Usage:
    python -m bot --interval 5m --strategy tested
    python -m bot --interval 5m --strategy tested --auto
"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

print("Loading config...", end=" ", flush=True)
from .config import parse_args, format_interval
print("OK")

print("Loading storage...", end=" ", flush=True)
from .storage.positions import PositionStorage
from .storage.history import HistoryStorage
print("OK")

print("Loading scanner (polymarket + models)...", end=" ", flush=True)
from .scanner.earthquake import EarthquakeScanner
print("OK")

print("Loading executor...", end=" ", flush=True)
from .executor.polymarket import PolymarketExecutor
print("OK")

print("Loading UI (Textual)...", end=" ", flush=True)
from .ui.app import TradingBotApp
print("OK")
print()


def main():
    """Main entry point."""
    # Parse CLI arguments
    config = parse_args()

    mode = "DRY RUN" if config.dry_run else ("AUTO" if config.auto_mode else "CONFIRM")
    min_edge_str = f"{config.min_edge:.0%}"
    min_roi_str = f"{config.min_roi:.0%}"
    print(f"""
┌─────────────────────────────────────────────────────────────┐
│                    EARTHQUAKE TRADING BOT                   │
├─────────────────────────────────────────────────────────────┤
│  Mode:      {mode:<47} │
│  Interval:  {format_interval(config.scan_interval):<47} │
│  Min Edge:  {min_edge_str:<47} │
│  Min ROI:   {min_roi_str:<47} │
└─────────────────────────────────────────────────────────────┘
    """)

    # Initialize storage
    print("Initializing storage...", end=" ", flush=True)
    position_storage = PositionStorage(config.active_dir, config.history_dir)
    history_storage = HistoryStorage(config.history_dir)
    positions = position_storage.load_all_active()
    print(f"OK ({len(positions)} positions)")

    # Initialize scanner
    print("Initializing scanner...", end=" ", flush=True)
    scanner = EarthquakeScanner(config)
    print("OK")

    # Initialize executor
    print("Initializing executor...", end=" ", flush=True)
    executor = PolymarketExecutor()
    if executor.initialized:
        print(f"OK ({executor.get_address()[:10]}...)")
        print("Fetching balance...", end=" ", flush=True)
        balance = executor.get_balance()
        print(f"${balance:,.2f}")
    else:
        print("SKIP (no API credentials)")

    print("\nStarting TUI... Press Q to quit.\n")

    # Run the TUI app
    app = TradingBotApp(
        config=config,
        position_storage=position_storage,
        history_storage=history_storage,
        scanner=scanner,
        executor=executor,
    )

    try:
        app.run()
    except KeyboardInterrupt:
        print("\nShutting down...")
    except Exception as e:
        print(f"\nError: {e}")
        raise


if __name__ == "__main__":
    main()
