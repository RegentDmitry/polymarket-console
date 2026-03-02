"""
Entry point for the crypto trading bot.

Usage:
    cd crypto && python -m trading_bot                    # dry-run mode
    cd crypto && python -m trading_bot --live             # live with confirmation
    cd crypto && python -m trading_bot --live --auto      # live auto-trade
    cd crypto && python -m trading_bot --interval 5m      # scan every 5 minutes
"""

import subprocess
import sys
import logging
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))


def _ensure_deps():
    """Install missing dependencies automatically."""
    required = {
        "textual": "textual",
        "rich": "rich",
        "numpy": "numpy",
        "scipy": "scipy",
    }
    missing = []
    for module, package in required.items():
        try:
            __import__(module)
        except ImportError:
            missing.append(package)

    if missing:
        print(f"Installing missing dependencies: {', '.join(missing)}...")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", *missing],
            stdout=subprocess.DEVNULL,
        )
        print("Done.\n")


_ensure_deps()

# Config is lightweight - import and parse first
from .config import parse_args, format_interval
config = parse_args()

# Show settings table immediately
mode = "DRY RUN" if config.dry_run else ("AUTO" if config.auto_mode else "CONFIRM")
pricing = "Fast (analytical)" if config.fast_pricing else f"MC ({config.mc_paths:,} paths)"
print(f"""
┌─────────────────────────────────────────────────────────────┐
│                    CRYPTO TRADING BOT                       │
│                    BTC/ETH • Polymarket                     │
├─────────────────────────────────────────────────────────────┤
│  Mode:      {mode:<47} │
│  Pricing:   {pricing:<47} │
│  Interval:  {format_interval(config.scan_interval):<47} │
│  Min Edge:  {f"{config.min_edge:.0%}":<47} │
│  Min APY:   {f"{config.min_apy:.0%}":<47} │
└─────────────────────────────────────────────────────────────┘
""")

# Now load heavy modules
print("Loading storage...", end=" ", flush=True)
from .storage.positions import PositionStorage
from .storage.history import HistoryStorage
print("OK")

print("Loading scanner (pricing + market data)...", end=" ", flush=True)
from .scanner.crypto import CryptoScanner
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

    # Check markets JSON exists
    if not config.markets_json.exists():
        print(f"WARNING: {config.markets_json} not found.")
        print("Run `python -m update_bot` first to discover crypto markets.")
        print("Proceeding with empty market list...\n")

    # Initialize storage
    print("Initializing storage...", end=" ", flush=True)
    position_storage = PositionStorage(config.active_dir, config.history_dir)
    history_storage = HistoryStorage(config.history_dir)
    positions = position_storage.load_all_active()
    print(f"OK ({len(positions)} positions)")

    # Initialize scanner
    print("Initializing scanner...", end=" ", flush=True)
    scanner = CryptoScanner(config)
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
        import traceback
        traceback.print_exc()
        raise


if __name__ == "__main__":
    # Set up crash log
    crash_log = Path(__file__).parent.parent / "crash.log"
    crash_logger = logging.getLogger("crash")
    crash_logger.setLevel(logging.ERROR)
    fh = logging.FileHandler(crash_log, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    crash_logger.addHandler(fh)

    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        crash_logger.error("CRASH: %s", e, exc_info=True)
        print(f"\nCRASH logged to {crash_log}: {e}")
        sys.exit(1)
