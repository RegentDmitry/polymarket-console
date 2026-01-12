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

from .config import parse_args, format_interval
from .storage.positions import PositionStorage
from .storage.history import HistoryStorage
from .scanner.earthquake import EarthquakeScanner
from .executor.polymarket import PolymarketExecutor
from .ui.app import TradingBotApp


def main():
    """Main entry point."""
    # Parse CLI arguments
    config = parse_args()

    mode = "DRY RUN" if config.dry_run else ("AUTO" if config.auto_mode else "CONFIRM")
    print(f"""
┌─────────────────────────────────────────────────────────────┐
│                    EARTHQUAKE TRADING BOT                   │
├─────────────────────────────────────────────────────────────┤
│  Mode:      {mode:<47} │
│  Interval:  {format_interval(config.scan_interval):<47} │
│  Position:  ${config.position_size:<46.2f} │
│  Min Edge:  {config.min_edge:.0%}{'':<45} │
│  Min ROI:   {config.min_roi:.0%}{'':<45} │
└─────────────────────────────────────────────────────────────┘
    """)

    # Initialize storage
    position_storage = PositionStorage(config.active_dir, config.history_dir)
    history_storage = HistoryStorage(config.history_dir)

    # Load existing positions
    positions = position_storage.load_all_active()
    print(f"Loaded {len(positions)} active position(s)")

    # Initialize scanner
    scanner = EarthquakeScanner(config)

    # Initialize executor
    executor = PolymarketExecutor(use_market_orders=False)  # Use limit orders
    if executor.initialized:
        print(f"Executor: {executor.get_address()[:10]}...")
        print(f"Balance: ${executor.get_balance():,.2f}")
    else:
        print("Executor: Not initialized (no API credentials)")

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
