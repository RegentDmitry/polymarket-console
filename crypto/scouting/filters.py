"""Market maker detection and trader filtering."""

import math
from . import db


def detect_mm(address: str) -> bool:
    """Detect if a trader is likely a market maker.
    Returns True if MM pattern detected (>=2 signals)."""
    positions = db.get_trader_positions(address)
    if not positions:
        return False

    signals = 0

    # 1. Two-sided positions: YES and NO on same market
    markets = {}
    for p in positions:
        cid = p["condition_id"]
        if cid not in markets:
            markets[cid] = set()
        markets[cid].add(p["outcome_side"])

    two_sided = sum(1 for sides in markets.values() if len(sides) > 1)
    if two_sided >= 3 or (len(markets) > 0 and two_sided / len(markets) > 0.2):
        signals += 1

    # 2. Very high number of markets with tiny positions
    tiny_positions = sum(1 for p in positions if p["initial_value"] < 5)
    if tiny_positions > 20:
        signals += 1

    # 3. Low net exposure (buys both sides roughly equally)
    total_yes = sum(p["size"] for p in positions if p["outcome_side"] == "YES")
    total_no = sum(p["size"] for p in positions if p["outcome_side"] == "NO")
    total = total_yes + total_no
    if total > 0:
        net_exposure = abs(total_yes - total_no) / total
        if net_exposure < 0.2:
            signals += 1

    # 4. High number of markets but low avg PnL (spread-capturing)
    trader = db.get_trader(address)
    if trader and trader["total_markets"] > 30:
        avg_pnl_per_market = abs(trader["realized_pnl"]) / trader["total_markets"]
        if avg_pnl_per_market < 2:  # less than $2 avg per market = spread capture
            signals += 1

    return signals >= 2


def run_mm_detection(verbose: bool = True) -> int:
    """Run MM detection on all traders. Returns count of MMs found."""
    with db.get_cursor(commit=False) as cur:
        cur.execute("SELECT address, alias, total_markets FROM traders WHERE total_markets >= 3")
        traders = cur.fetchall()

    if verbose:
        print(f"Running MM detection on {len(traders)} traders...")

    mm_count = 0
    for i, t in enumerate(traders):
        is_mm = detect_mm(t["address"])
        db.update_trader_stats(t["address"], is_mm=is_mm)
        if is_mm:
            mm_count += 1
            if verbose:
                name = t["alias"] or t["address"][:16]
                print(f"  MM: {name} ({t['total_markets']} markets)")

    if verbose:
        print(f"Found {mm_count} market makers out of {len(traders)} traders")

    return mm_count
