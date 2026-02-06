#!/usr/bin/env python3
"""
One-time script: cancel all sell orders on Polymarket, clear local position data,
so the bot re-syncs from API on next start.
"""

import os
import sys
import json
import glob
from pathlib import Path
from dotenv import load_dotenv

# Load environment
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(env_path)

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from polymarket_client import PolymarketClient

DATA_DIR = Path(__file__).parent.parent / "trading_bot" / "data"
ACTIVE_DIR = DATA_DIR / "active"
SELL_ORDERS_FILE = DATA_DIR / "sell_orders.json"


def main():
    print("=== RESYNC POSITIONS ===\n")

    # Step 1: Cancel all orders on Polymarket
    print("Step 1: Cancelling all orders on Polymarket...")
    try:
        client = PolymarketClient()
        result = client.cancel_all_orders()
        print(f"  Cancel result: {result}")
    except Exception as e:
        print(f"  Warning: cancel failed: {e}")
        print("  Continuing anyway (orders may already be cancelled)...")

    # Step 2: Show current active positions
    active_files = list(ACTIVE_DIR.glob("*.json"))
    print(f"\nStep 2: Found {len(active_files)} active position files:")
    for f in active_files:
        try:
            data = json.loads(f.read_text())
            print(f"  {f.name}: {data.get('market_slug', '?')[:40]} "
                  f"tokens={data.get('tokens', 0):.2f} "
                  f"entry_size=${data.get('entry_size', 0):.2f}")
        except Exception:
            print(f"  {f.name}: (unreadable)")

    # Step 3: Delete active position files
    print(f"\nStep 3: Deleting {len(active_files)} active position files...")
    for f in active_files:
        f.unlink()
        print(f"  Deleted {f.name}")

    # Step 4: Clear sell orders
    print(f"\nStep 4: Clearing sell_orders.json...")
    if SELL_ORDERS_FILE.exists():
        old = json.loads(SELL_ORDERS_FILE.read_text())
        print(f"  Had {len(old)} sell orders")
        SELL_ORDERS_FILE.write_text("{}")
        print("  Cleared.")
    else:
        print("  File not found, nothing to clear.")

    print("\n=== DONE ===")
    print("Restart the bot — it will sync positions from Polymarket API.")


if __name__ == "__main__":
    main()
