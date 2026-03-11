"""
Entry point for the weather trading bot.

Usage:
    cd weather && python -m trading_bot                    # dry-run mode
    cd weather && python -m trading_bot --live             # live with confirmation
    cd weather && python -m trading_bot --live --auto      # live auto-trade
    cd weather && python -m trading_bot --scan-once        # single scan, no TUI
"""

import sys
import logging
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from .config import parse_args, format_interval
from . import __version__
config = parse_args()

# Show settings table
mode = "OBSERVE (no trading)" if config.observe_only else ("DRY RUN" if config.dry_run else ("AUTO" if config.auto_mode else "CONFIRM"))
alloc_str = f"{config.target_alloc:.0%}" + (" (all)" if config.target_alloc >= 1.0 else "")
print(f"""
+-------------------------------------------------------------+
|            WEATHER TRADING BOT v{__version__:<24} |
|              Temperature Markets - Polymarket                |
+-------------------------------------------------------------+
|  Mode:      {mode:<47} |
|  Interval:  {format_interval(config.scan_interval):<47} |
|  Forecast:  {"smart (S3 meta.json)":<47} |
|  Min Edge:  {f"{config.min_edge:.0%}":<47} |
|  Min Hours: {f"{config.min_hours_to_expiry:.0f}h to expiry":<47} |
|  Limits:    {f"${config.max_per_bucket:.0f}/bucket ${config.max_per_event:.0f}/event ${config.max_per_city:.0f}/city":<47} |
|  Alloc:     {alloc_str:<47} |
+-------------------------------------------------------------+
""")

# Load heavy modules
print("Loading storage...", end=" ", flush=True)
from .storage.positions import PositionStorage
from .storage.history import HistoryStorage
print("OK")

print("Loading scanner (forecast + market data)...", end=" ", flush=True)
from .scanner.weather import WeatherScanner
print("OK")

print("Loading executor...", end=" ", flush=True)
from .executor.polymarket import PolymarketExecutor
print("OK")

if not config.scan_once:
    print("Loading UI (Textual)...", end=" ", flush=True)
    from .ui.app import TradingBotApp
    print("OK")
print()


def main():
    """Main entry point."""

    if not config.markets_json.exists():
        print(f"WARNING: {config.markets_json} not found.")
        print("Run `python -m update_bot` first to discover weather markets.")
        print("Proceeding with empty market list...\n")

    # Initialize storage
    print("Initializing storage...", end=" ", flush=True)
    position_storage = PositionStorage(config.active_dir, config.history_dir)
    history_storage = HistoryStorage(config.history_dir)
    positions = position_storage.load_all_active()
    print(f"OK ({len(positions)} positions)")

    # Initialize forecast DB (optional)
    forecast_db = None
    if config.db_url:
        print("Connecting to forecast DB...", end=" ", flush=True)
        try:
            from .forecast_db import ForecastDB
            forecast_db = ForecastDB(config.db_url)
            print(f"OK ({forecast_db.count_forecasts()} records)")
        except Exception as e:
            print(f"FAILED ({e})")

    # Initialize actuals collector (optional, requires forecast_db)
    actuals_collector = None
    if forecast_db:
        try:
            import json
            from .actuals_collector import ActualsCollector
            cities = json.loads(config.cities_json.read_text())
            actuals_collector = ActualsCollector(cities, forecast_db)
            print("Actuals collector...", end=" ", flush=True)
            print("OK")
        except Exception as e:
            print(f"Actuals collector FAILED: {e}")

    # Initialize scanner
    print("Initializing scanner...", end=" ", flush=True)
    scanner = WeatherScanner(config)
    if forecast_db:
        scanner.forecast.db = forecast_db
    print("OK")

    # Initialize executor
    print("Initializing executor...", end=" ", flush=True)
    executor = PolymarketExecutor()
    if executor.initialized:
        addr = executor.get_address() or "unknown"
        print(f"OK ({addr[:10]}...)")
        print("Fetching balance...", end=" ", flush=True)
        balance = executor.get_balance()
        print(f"${balance:,.2f}")
    else:
        print("SKIP (no API credentials)")

    # Scan-once mode: run one scan and exit
    if config.scan_once:
        print("\n--- Single scan mode ---\n")
        from .pricing.portfolio import allocate_sizes

        held_slugs = {p.market_slug for p in positions}
        signals = scanner.scan_for_entries(
            progress_callback=lambda msg: print(f"  {msg}"),
            held_slugs=held_slugs,
        )

        balance = executor.get_balance() if executor.initialized else 0.0
        allocate_sizes(signals, balance, positions, config)

        sized = sum(1 for s in signals if s.suggested_size > 0)
        print(f"\n  Found {len(signals)} opportunities ({sized} sized):\n")
        for s in signals[:30]:
            city = s.city.replace("-", " ").title()
            size_str = f" ${s.suggested_size:.0f}" if s.suggested_size > 0 else ""
            kelly_str = f" K={s.kelly:.0%}" if s.kelly > 0 else ""
            print(f"  {city:<14} {s.date[5:]:<6} {s.bucket_label:<10} "
                  f"PM={s.current_price:.0%} Fair={s.fair_price:.0%} "
                  f"Edge=+{s.edge:.0%}{kelly_str}{size_str}")
        return

    # TUI mode
    print("\nStarting TUI... Press Q to quit.\n")

    app = TradingBotApp(
        config=config,
        position_storage=position_storage,
        history_storage=history_storage,
        scanner=scanner,
        executor=executor,
        actuals_collector=actuals_collector,
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
