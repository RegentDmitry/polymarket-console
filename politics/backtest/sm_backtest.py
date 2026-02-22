#!/usr/bin/env python3
"""
Smart Money Backtest for Polymarket political markets.

Uses Dune Analytics to reconstruct historical positions,
then runs SM algorithm to check if SM predicted outcomes correctly.

Usage:
    # Test on one market
    python politics/backtest/sm_backtest.py --query-id 6707297 \
        --slug fed-decision-in-january --market "No change"

    # Discover and backtest all closed political markets
    python politics/backtest/sm_backtest.py --query-id 6707297 \
        --discover --tag fed-rates --min-volume 500000

    # With specific snapshot days before resolution
    python politics/backtest/sm_backtest.py --query-id 6707297 \
        --discover --tag politics --snapshot-days 30,14,7
"""

import argparse
import hashlib
import json
import math
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import httpx
from tqdm import tqdm

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from politics.backtest.dune_loader import DuneLoader, discover_closed_political_markets, CACHE_DIR

# === Config ===
DATA_API = "https://data-api.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"
REQUEST_TIMEOUT = 30
SHRINKAGE_K = 30
MAX_WEIGHT_SHARE = 0.15
HOLDER_LIMIT = 30
TRADER_CACHE_FILE = CACHE_DIR / "trader_stats_cache.json"


# === Trader Stats (from data-api) ===

@dataclass
class TraderStats:
    wallet: str
    total_profit: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    total_volume: float = 0.0
    n_total_trades: int = 0
    portfolio_value: float = 0.0


def load_trader_cache() -> dict:
    if TRADER_CACHE_FILE.exists():
        try:
            return json.loads(TRADER_CACHE_FILE.read_text())
        except Exception:
            pass
    return {}


def save_trader_cache(cache: dict):
    TRADER_CACHE_FILE.write_text(json.dumps(cache))


def fetch_trader_stats(wallet: str, cache: dict) -> TraderStats:
    """Fetch trader P&L stats from Polymarket data-api."""
    cache_key = f"trader_{wallet}"
    if cache_key in cache:
        c = cache[cache_key]
        return TraderStats(
            wallet=wallet,
            total_profit=c.get("total_profit", 0),
            realized_pnl=c.get("realized_pnl", 0),
            unrealized_pnl=c.get("unrealized_pnl", 0),
            total_volume=c.get("total_volume", 0),
            n_total_trades=c.get("n_total_trades", 0),
            portfolio_value=c.get("portfolio_value", 0),
        )

    stats = TraderStats(wallet=wallet)

    # Open positions
    try:
        resp = httpx.get(
            f"{DATA_API}/positions",
            params={"user": wallet, "limit": 200, "sortBy": "CURRENT", "sortDir": "desc"},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        positions = resp.json()
        for p in positions:
            stats.unrealized_pnl += float(p.get("cashPnl", 0) or 0)
            stats.total_volume += float(p.get("totalBought", 0) or 0)
            stats.portfolio_value += float(p.get("currentValue", 0) or 0)
        stats.n_total_trades += len(positions)
    except Exception:
        pass

    # Closed positions
    try:
        resp = httpx.get(
            f"{DATA_API}/closed-positions",
            params={"user": wallet, "limit": 200},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        closed = resp.json()
        for p in closed:
            stats.realized_pnl += float(p.get("realizedPnl", 0) or 0)
            stats.total_volume += float(p.get("totalBought", 0) or 0)
        stats.n_total_trades += len(closed)
    except Exception:
        pass

    stats.total_profit = stats.realized_pnl + stats.unrealized_pnl

    # Cache
    cache[cache_key] = {
        "total_profit": stats.total_profit,
        "realized_pnl": stats.realized_pnl,
        "unrealized_pnl": stats.unrealized_pnl,
        "total_volume": stats.total_volume,
        "n_total_trades": stats.n_total_trades,
        "portfolio_value": stats.portfolio_value,
    }
    return stats


# === SM Algorithm (same as crypto/smart_money.py v2) ===

def compute_smart_flow(
    yes_holders: list[tuple[str, float]],
    trader_cache: dict,
    market_price: float,
    no_holders: list[tuple[str, float]] | None = None,
) -> dict:
    """
    Compute Smart Money Flow from YES and NO holder lists.

    Args:
        yes_holders: list of (wallet, token_balance) for YES side
        trader_cache: shared trader stats cache
        market_price: current YES price for conviction calc
        no_holders: list of (wallet, token_balance) for NO side (net sellers)

    Returns:
        dict with smart_flow, smart_implied, scored_holders
    """
    # Build combined list with side info
    all_holders = [(w, t, "YES") for w, t in yes_holders]
    if no_holders:
        all_holders += [(w, t, "NO") for w, t in no_holders]

    # Parallel fetch of trader stats
    from concurrent.futures import ThreadPoolExecutor, as_completed

    wallets_to_fetch = [w for w, t, s in all_holders if f"trader_{w}" not in trader_cache]
    if wallets_to_fetch:
        def _fetch_one(w):
            time.sleep(0.05)
            return w, fetch_trader_stats(w, trader_cache)

        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = {pool.submit(_fetch_one, w): w for w in wallets_to_fetch}
            for f in as_completed(futures):
                try:
                    f.result()
                except Exception:
                    pass

    scored = []

    for wallet, tokens, side in all_holders:
        stats = fetch_trader_stats(wallet, trader_cache)

        if stats.n_total_trades < 3:
            continue

        # Position value — for NO side use (1-price) as effective price
        effective_price = market_price if side == "YES" else (1.0 - market_price)
        position_value = tokens * effective_price
        portfolio = max(stats.portfolio_value, 1.0)
        conviction = min(position_value / portfolio, 1.0)

        # Shrinkage
        shrinkage = stats.n_total_trades / (stats.n_total_trades + SHRINKAGE_K)

        # Log-scale profit
        profit_sign = 1.0 if stats.total_profit >= 0 else -1.0
        log_profit = profit_sign * math.log1p(abs(stats.total_profit))

        # ROI multiplier
        roi = stats.total_profit / max(stats.total_volume, 1)
        roi_mult = 1.0 + min(max(roi, -0.5), 2.0)

        # Health penalty
        health = 1.0
        if stats.unrealized_pnl < 0 and stats.realized_pnl > 0:
            health = max(0.2, 1.0 + stats.unrealized_pnl / (abs(stats.realized_pnl) + 1))

        weight = log_profit * roi_mult * health * conviction * shrinkage

        # YES holders get positive weight, NO holders get negative
        side_sign = 1.0 if side == "YES" else -1.0

        scored.append({
            "wallet": wallet,
            "side": side,
            "tokens": tokens,
            "profit": stats.total_profit,
            "trades": stats.n_total_trades,
            "weight": abs(weight),
            "signed_weight": weight * side_sign,
        })

    if not scored:
        return {"smart_flow": 0, "smart_implied": 0.5, "n_holders_scored": 0}

    # Cap: no single trader > 15% of signal
    total_abs = sum(abs(h["signed_weight"]) for h in scored) or 1
    cap = MAX_WEIGHT_SHARE * total_abs
    for h in scored:
        if abs(h["signed_weight"]) > cap:
            h["signed_weight"] = math.copysign(cap, h["signed_weight"])
            h["weight"] = cap

    # Recalculate after cap
    total_abs = sum(abs(h["signed_weight"]) for h in scored) or 1
    smart_flow = sum(h["signed_weight"] for h in scored) / total_abs

    yes_weight = sum(h["weight"] for h in scored if h["signed_weight"] > 0)
    no_weight = sum(h["weight"] for h in scored if h["signed_weight"] < 0)
    total_weight = yes_weight + no_weight
    smart_implied = yes_weight / total_weight if total_weight > 0 else 0.5

    return {
        "smart_flow": smart_flow,
        "smart_implied": smart_implied,
        "n_holders_scored": len(scored),
        "top_holders": sorted(scored, key=lambda x: abs(x["weight"]), reverse=True)[:5],
    }


# === Backtest Engine ===

@dataclass
class BacktestResult:
    market_question: str
    event_slug: str
    tags: list[str]
    volume: float
    yes_won: bool | None
    neg_risk: bool
    snapshots: dict  # {days_before: {smart_flow, smart_implied, price, ...}}



def run_backtest(
    markets: list[dict],
    dune: DuneLoader,
    snapshot_days: list[int] = None,
) -> list[BacktestResult]:
    """Run SM backtest on all markets."""
    if snapshot_days is None:
        snapshot_days = [30, 14, 7]

    trader_cache = load_trader_cache()

    # Phase 1: Batch fetch all Dune trades
    token_ids = [m["yes_token"] for m in markets]
    print(f"\n=== Phase 1: Fetching trades from Dune ({len(token_ids)} markets) ===")
    trades_by_token = dune.fetch_trades_batch(token_ids)
    print(f"  Got trades for {len(trades_by_token)}/{len(token_ids)} markets\n")

    # Phase 2: Run SM analysis
    print(f"=== Phase 2: SM analysis ({len(markets)} markets) ===")
    results = []

    for i, market in enumerate(markets):
        q = market["question"][:50]
        token_id = market["yes_token"]
        print(f"\n[{i+1}/{len(markets)}] {q}...")

        trades = trades_by_token.get(token_id)
        if not trades or len(trades) < 50:
            print(f"  ✗ skipped (no/few trades)")
            continue

        result = _analyze_market_trades(market, trades, dune, trader_cache, snapshot_days)
        if result:
            results.append(result)
            print(f"  ✓ {len(result.snapshots)} snapshots")
        else:
            print(f"  ✗ no valid snapshots")

        # Save trader cache periodically
        if (i + 1) % 5 == 0:
            save_trader_cache(trader_cache)

    save_trader_cache(trader_cache)
    return results


def _analyze_market_trades(
    market: dict,
    trades: list[dict],
    dune: DuneLoader,
    trader_cache: dict,
    snapshot_days: list[int],
) -> BacktestResult | None:
    """Analyze a single market given pre-fetched trades."""
    token_id = market["yes_token"]
    end_date = market.get("end_date", "")[:10]
    if not end_date:
        return None

    prices = dune.daily_prices(trades, token_id)
    if not prices:
        return None

    from datetime import datetime, timedelta
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")

    snapshots = {}
    for days_before in snapshot_days:
        snap_date = (end_dt - timedelta(days=days_before)).strftime("%Y-%m-%d")

        # Find closest price
        price = prices.get(snap_date)
        if not price:
            for offset in range(1, 4):
                for d in [
                    (end_dt - timedelta(days=days_before + offset)).strftime("%Y-%m-%d"),
                    (end_dt - timedelta(days=days_before - offset)).strftime("%Y-%m-%d"),
                ]:
                    if d in prices:
                        price = prices[d]
                        snap_date = d
                        break
                if price:
                    break

        if not price:
            continue

        positions = dune.reconstruct_positions(trades, token_id, as_of=snap_date)
        yes_holders, no_holders = dune.get_top_holders(positions, top_n=HOLDER_LIMIT)

        if len(yes_holders) < 3:
            continue

        sm_result = compute_smart_flow(yes_holders, trader_cache, price, no_holders=no_holders)

        snapshots[days_before] = {
            "date": snap_date,
            "price": price,
            "smart_flow": sm_result["smart_flow"],
            "smart_implied": sm_result["smart_implied"],
            "n_holders": sm_result["n_holders_scored"],
        }

    if not snapshots:
        return None

    return BacktestResult(
        market_question=market["question"],
        event_slug=market["event_slug"],
        tags=market.get("tags", []),
        volume=market["volume"],
        yes_won=market["yes_won"],
        neg_risk=market.get("neg_risk", False),
        snapshots=snapshots,
    )


# === Analysis & Output ===

def analyze_results(results: list[BacktestResult], snapshot_days: list[int] = None):
    """Analyze and print backtest results."""
    if snapshot_days is None:
        snapshot_days = [30, 14, 7]

    print("\n" + "=" * 70)
    print("SM BACKTEST RESULTS")
    print("=" * 70)
    print(f"Total markets analyzed: {len(results)}")

    # Overall hit rate by snapshot timing
    for days in snapshot_days:
        correct = 0
        total = 0
        strong_correct = 0
        strong_total = 0

        for r in results:
            if days not in r.snapshots or r.yes_won is None:
                continue

            snap = r.snapshots[days]
            sm_says_yes = snap["smart_flow"] > 0

            total += 1
            if sm_says_yes == r.yes_won:
                correct += 1

            # Strong signal (|flow| > 0.3)
            if abs(snap["smart_flow"]) > 0.3:
                strong_total += 1
                if sm_says_yes == r.yes_won:
                    strong_correct += 1

        hit_rate = correct / total * 100 if total else 0
        strong_rate = strong_correct / strong_total * 100 if strong_total else 0

        print(f"\n--- T-{days} days ---")
        print(f"  All signals:    {correct}/{total} = {hit_rate:.1f}%")
        print(f"  Strong (>0.3):  {strong_correct}/{strong_total} = {strong_rate:.1f}%")

    # By tag
    print("\n" + "-" * 70)
    print("BY CATEGORY")
    print("-" * 70)

    tag_stats: dict[str, dict] = defaultdict(lambda: {"correct": 0, "total": 0})

    for r in results:
        # Use T-14 as primary signal
        if 14 not in r.snapshots or r.yes_won is None:
            continue

        snap = r.snapshots[14]
        sm_says_yes = snap["smart_flow"] > 0
        is_correct = sm_says_yes == r.yes_won

        for tag in r.tags or ["other"]:
            tag_stats[tag]["total"] += 1
            if is_correct:
                tag_stats[tag]["correct"] += 1

    for tag, stats in sorted(tag_stats.items(), key=lambda x: x[1]["total"], reverse=True):
        rate = stats["correct"] / stats["total"] * 100 if stats["total"] else 0
        print(f"  {tag:25s} {stats['correct']:>3}/{stats['total']:<3} = {rate:5.1f}%")

    # Detailed per-market table
    print("\n" + "-" * 70)
    print("DETAILED RESULTS")
    print("-" * 70)
    print(f"{'Market':<45} {'Won':>4} {'T-30':>7} {'T-14':>7} {'T-7':>7} {'Hit?':>5}")
    print("-" * 70)

    for r in sorted(results, key=lambda x: x.volume, reverse=True):
        won = "YES" if r.yes_won else "NO" if r.yes_won is not None else "?"

        cols = [f"{r.market_question[:44]:<45}", f"{won:>4}"]

        for days in snapshot_days:
            if days in r.snapshots:
                flow = r.snapshots[days]["smart_flow"]
                cols.append(f"{flow:>+6.2f}")
            else:
                cols.append(f"{'—':>7}")

        # Hit at T-14
        if 14 in r.snapshots and r.yes_won is not None:
            sm_yes = r.snapshots[14]["smart_flow"] > 0
            hit = "✓" if sm_yes == r.yes_won else "✗"
        else:
            hit = "—"
        cols.append(f"{hit:>5}")

        print(" ".join(cols))

    # P&L simulation
    print("\n" + "-" * 70)
    print("P&L SIMULATION (buy $100 per SM signal at T-14)")
    print("-" * 70)

    total_pnl = 0
    trades = 0
    wins = 0

    for r in results:
        if 14 not in r.snapshots or r.yes_won is None:
            continue

        snap = r.snapshots[14]
        price = snap["price"]
        sm_says_yes = snap["smart_flow"] > 0

        if abs(snap["smart_flow"]) < 0.1:
            continue  # skip weak signals

        trade_size = 100

        if sm_says_yes:
            # Buy YES tokens
            tokens = trade_size / price if price > 0 else 0
            pnl = tokens * (1.0 if r.yes_won else 0.0) - trade_size
        else:
            # Buy NO tokens
            no_price = 1.0 - price
            tokens = trade_size / no_price if no_price > 0 else 0
            pnl = tokens * (0.0 if r.yes_won else 1.0) - trade_size

        total_pnl += pnl
        trades += 1
        if pnl > 0:
            wins += 1

        direction = "YES" if sm_says_yes else "NO"
        result_str = "WIN" if pnl > 0 else "LOSS"
        print(f"  {r.market_question[:40]:<42} {direction:>3} @ {price:.2f} → {result_str:>4} {pnl:>+8.1f}")

    win_rate = wins / trades * 100 if trades else 0
    avg_pnl = total_pnl / trades if trades else 0
    print(f"\n  Total: {trades} trades, {wins} wins ({win_rate:.0f}%), P&L: ${total_pnl:+,.0f} (avg ${avg_pnl:+.1f}/trade)")

    return {
        "n_markets": len(results),
        "total_pnl": total_pnl,
        "trades": trades,
        "win_rate": win_rate,
    }


# === SM Reversal Exit Simulation ===

@dataclass
class ExitTrade:
    market_question: str
    side: str          # "YES" or "NO"
    entry_date: str
    entry_price: float
    entry_flow: float
    exit_date: str
    exit_price: float
    exit_flow: float
    exit_reason: str   # "reversal", "weak_signal", "resolution"
    pnl: float
    pnl_hold: float    # P&L if held to resolution instead
    yes_won: bool | None


def compute_flow_series_all(
    markets: list[dict],
    dune: DuneLoader,
    trader_cache: dict,
    step_days: int = 5,
) -> list[dict]:
    """
    Compute SM flow series for all markets (heavy — does API calls).
    Returns list of {market: dict, flow_series: [(date, flow, price)], end_date, yes_won}.
    Cache-friendly: trader stats cached, Dune trades cached.
    """
    from datetime import datetime, timedelta

    results = []

    for market in tqdm(markets, desc="Computing SM flow series"):
        token_id = market["yes_token"]
        end_date = market.get("end_date", "")[:10]
        yes_won = market.get("yes_won")
        if not end_date or yes_won is None:
            continue

        cache_file = CACHE_DIR / f"dune_trades_{hashlib.md5(token_id.encode()).hexdigest()[:12]}.json"
        if not cache_file.exists():
            continue
        trades = json.loads(cache_file.read_text())
        if not trades or len(trades) < 50:
            continue

        prices = dune.daily_prices(trades, token_id)
        if len(prices) < 10:
            continue

        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        price_dates = sorted(prices.keys())
        first_dt = datetime.strptime(price_dates[0], "%Y-%m-%d")

        snap_dates = []
        current = first_dt
        while current < end_dt:
            snap_dates.append(current.strftime("%Y-%m-%d"))
            current += timedelta(days=step_days)

        if len(snap_dates) < 3:
            continue

        flow_series: list[tuple[str, float, float]] = []
        for snap_date in snap_dates:
            price = prices.get(snap_date)
            if not price:
                for offset in range(1, step_days):
                    for d_str in [
                        (datetime.strptime(snap_date, "%Y-%m-%d") + timedelta(days=offset)).strftime("%Y-%m-%d"),
                        (datetime.strptime(snap_date, "%Y-%m-%d") - timedelta(days=offset)).strftime("%Y-%m-%d"),
                    ]:
                        if d_str in prices:
                            price = prices[d_str]
                            break
                    if price:
                        break
            if not price:
                continue

            positions = dune.reconstruct_positions(trades, token_id, as_of=snap_date)
            yes_holders, no_holders = dune.get_top_holders(positions, top_n=HOLDER_LIMIT)
            if len(yes_holders) < 3:
                continue

            sm = compute_smart_flow(yes_holders, trader_cache, price, no_holders=no_holders)
            flow_series.append((snap_date, sm["smart_flow"], price))

        if len(flow_series) < 3:
            continue

        results.append({
            "market": market,
            "flow_series": flow_series,
            "end_date": end_date,
            "yes_won": yes_won,
        })

    save_trader_cache(trader_cache)
    return results


def simulate_on_series(
    precomputed: list[dict],
    min_edge: float = 0.1,
    exit_threshold: float = 0.0,
    trade_size: float = 100,
    fee: float = 0.02,
) -> list[ExitTrade]:
    """
    Fast simulation on precomputed flow series. No API calls.
    """
    all_trades: list[ExitTrade] = []

    for item in precomputed:
        market = item["market"]
        flow_series = item["flow_series"]
        end_date = item["end_date"]
        yes_won = item["yes_won"]

        in_trade = False
        entry_date = entry_price = entry_flow = entry_side = None

        for date, flow, price in flow_series:
            if not in_trade:
                if abs(flow) >= min_edge:
                    in_trade = True
                    entry_date = date
                    entry_flow = flow
                    entry_side = "YES" if flow > 0 else "NO"
                    entry_price = price if entry_side == "YES" else (1.0 - price)
            else:
                exit_reason = None

                if entry_side == "YES" and flow < -exit_threshold:
                    exit_reason = "reversal"
                elif entry_side == "NO" and flow > exit_threshold:
                    exit_reason = "reversal"

                if exit_reason:
                    exit_price = price if entry_side == "YES" else (1.0 - price)
                    tokens = trade_size / entry_price if entry_price > 0 else 0
                    pnl = tokens * exit_price - trade_size
                    pnl -= trade_size * fee

                    resolution_payout = 1.0 if (entry_side == "YES") == yes_won else 0.0
                    pnl_hold = tokens * resolution_payout - trade_size - trade_size * fee

                    all_trades.append(ExitTrade(
                        market_question=market["question"],
                        side=entry_side,
                        entry_date=entry_date,
                        entry_price=entry_price,
                        entry_flow=entry_flow,
                        exit_date=date,
                        exit_price=exit_price,
                        exit_flow=flow,
                        exit_reason=exit_reason,
                        pnl=pnl,
                        pnl_hold=pnl_hold,
                        yes_won=yes_won,
                    ))
                    in_trade = False

        # Still in trade → resolution
        if in_trade and entry_price and entry_price > 0:
            tokens = trade_size / entry_price
            resolution_payout = 1.0 if (entry_side == "YES") == yes_won else 0.0
            pnl = tokens * resolution_payout - trade_size - trade_size * fee

            all_trades.append(ExitTrade(
                market_question=market["question"],
                side=entry_side,
                entry_date=entry_date,
                entry_price=entry_price,
                entry_flow=entry_flow,
                exit_date=end_date,
                exit_price=resolution_payout,
                exit_flow=flow_series[-1][1] if flow_series else 0,
                exit_reason="resolution",
                pnl=pnl,
                pnl_hold=pnl,
                yes_won=yes_won,
            ))

    return all_trades


def simulate_price_exit(
    precomputed: list[dict],
    min_edge: float = 0.1,
    take_profit: float = 0.95,
    trade_size: float = 100,
    fee: float = 0.02,
) -> list[ExitTrade]:
    """
    Simulate: enter on SM signal, exit when our side price >= take_profit.
    Hypothesis: exit at 95c is better than hold to resolution (tail risk).
    """
    all_trades: list[ExitTrade] = []

    for item in precomputed:
        market = item["market"]
        flow_series = item["flow_series"]
        end_date = item["end_date"]
        yes_won = item["yes_won"]

        in_trade = False
        entry_date = entry_price = entry_flow = entry_side = None

        for date, flow, price in flow_series:
            if not in_trade:
                if abs(flow) >= min_edge:
                    in_trade = True
                    entry_date = date
                    entry_flow = flow
                    entry_side = "YES" if flow > 0 else "NO"
                    entry_price = price if entry_side == "YES" else (1.0 - price)
            else:
                # Our side's current price
                our_price = price if entry_side == "YES" else (1.0 - price)

                if our_price >= take_profit:
                    tokens = trade_size / entry_price if entry_price > 0 else 0
                    pnl = tokens * our_price - trade_size - trade_size * fee

                    resolution_payout = 1.0 if (entry_side == "YES") == yes_won else 0.0
                    pnl_hold = tokens * resolution_payout - trade_size - trade_size * fee

                    all_trades.append(ExitTrade(
                        market_question=market["question"],
                        side=entry_side,
                        entry_date=entry_date,
                        entry_price=entry_price,
                        entry_flow=entry_flow,
                        exit_date=date,
                        exit_price=our_price,
                        exit_flow=flow,
                        exit_reason=f"tp@{take_profit:.0%}",
                        pnl=pnl,
                        pnl_hold=pnl_hold,
                        yes_won=yes_won,
                    ))
                    in_trade = False

        # Still in trade → resolution
        if in_trade and entry_price and entry_price > 0:
            tokens = trade_size / entry_price
            resolution_payout = 1.0 if (entry_side == "YES") == yes_won else 0.0
            pnl = tokens * resolution_payout - trade_size - trade_size * fee

            all_trades.append(ExitTrade(
                market_question=market["question"],
                side=entry_side,
                entry_date=entry_date,
                entry_price=entry_price,
                entry_flow=entry_flow,
                exit_date=end_date,
                exit_price=resolution_payout,
                exit_flow=flow_series[-1][1] if flow_series else 0,
                exit_reason="resolution",
                pnl=pnl,
                pnl_hold=pnl,
                yes_won=yes_won,
            ))

    return all_trades


def run_price_exit_grid(precomputed: list[dict], min_edge: float = 0.10):
    """Grid search over take-profit levels."""
    tps = [0.85, 0.88, 0.90, 0.92, 0.93, 0.94, 0.95, 0.96, 0.97, 0.98, 0.99]

    print("\n" + "=" * 105)
    print(f"TAKE-PROFIT EXIT GRID (min_edge={min_edge}, fee=2%)")
    print("Hypothesis: exit at ~95c beats hold to resolution (tail risk protection)")
    print("=" * 105)
    print(f"\n{'TP level':>10} {'Trades':>8} {'Exits':>6} {'Holds':>6} {'Wins':>6} {'Win%':>6} {'P&L tp':>10} {'P&L hold':>10} {'Advantage':>10} {'Avg/trade':>10}")
    print("-" * 105)

    # Also show pure hold baseline
    hold_trades = simulate_on_series(precomputed, min_edge=min_edge, exit_threshold=99.0)
    if hold_trades:
        hold_pnl = sum(t.pnl for t in hold_trades)
        hold_wins = sum(1 for t in hold_trades if t.pnl > 0)
        hold_avg = hold_pnl / len(hold_trades) if hold_trades else 0
        print(f"  {'HOLD':>8} {len(hold_trades):>8} {0:>6} {len(hold_trades):>6} {hold_wins:>6} {hold_wins/len(hold_trades)*100:>5.0f}% ${hold_pnl:>+9.1f} ${hold_pnl:>+9.1f} ${'0.0':>9} ${hold_avg:>+9.1f}")
        print("-" * 105)

    for tp in tps:
        trades = simulate_price_exit(precomputed, min_edge=min_edge, take_profit=tp)
        if not trades:
            continue

        total_pnl = sum(t.pnl for t in trades)
        total_hold = sum(t.pnl_hold for t in trades)
        wins = sum(1 for t in trades if t.pnl > 0)
        exits = sum(1 for t in trades if t.exit_reason != "resolution")
        holds = sum(1 for t in trades if t.exit_reason == "resolution")
        adv = total_pnl - total_hold
        avg = total_pnl / len(trades) if trades else 0

        marker = " <<<" if total_pnl > hold_pnl else ""
        print(f"  {tp:>8.0%} {len(trades):>8} {exits:>6} {holds:>6} {wins:>6} {wins/len(trades)*100:>5.0f}% ${total_pnl:>+9.1f} ${total_hold:>+9.1f} ${adv:>+9.1f} ${avg:>+9.1f}{marker}")


def print_exit_simulation(trades: list[ExitTrade], min_edge: float, exit_threshold: float, step_days: int):
    """Print exit simulation results."""
    print("\n" + "=" * 80)
    print(f"SM EXIT SIMULATION (min_edge={min_edge}, exit_thr={exit_threshold}, step={step_days}d, fee=2%)")
    print("=" * 80)

    if not trades:
        print("  No trades generated.")
        return {}

    # Split by exit reason
    by_reason: dict[str, list[ExitTrade]] = defaultdict(list)
    for t in trades:
        by_reason[t.exit_reason].append(t)

    total_pnl = sum(t.pnl for t in trades)
    total_pnl_hold = sum(t.pnl_hold for t in trades)
    wins = sum(1 for t in trades if t.pnl > 0)

    print(f"\nTotal trades: {len(trades)}")
    print(f"Winners: {wins} ({wins/len(trades)*100:.0f}%)")
    print(f"P&L (with exits):    ${total_pnl:+,.1f}")
    print(f"P&L (hold to resol): ${total_pnl_hold:+,.1f}")
    print(f"Exit advantage:      ${total_pnl - total_pnl_hold:+,.1f}")
    print(f"Avg P&L/trade:       ${total_pnl/len(trades):+,.1f}")

    print(f"\n{'Exit reason':<15} {'Count':>6} {'Wins':>6} {'Win%':>6} {'P&L':>10} {'vs Hold':>10}")
    print("-" * 60)
    for reason in ["reversal", "weak_signal", "resolution"]:
        rt = by_reason.get(reason, [])
        if not rt:
            continue
        r_wins = sum(1 for t in rt if t.pnl > 0)
        r_pnl = sum(t.pnl for t in rt)
        r_hold = sum(t.pnl_hold for t in rt)
        print(f"  {reason:<13} {len(rt):>6} {r_wins:>6} {r_wins/len(rt)*100:>5.0f}% ${r_pnl:>+9.1f} ${r_pnl - r_hold:>+9.1f}")

    # Per-trade details
    print(f"\n{'Market':<35} {'Side':>4} {'Entry':>7} {'Exit':>7} {'Reason':<10} {'P&L':>8} {'Hold':>8} {'Diff':>8}")
    print("-" * 100)

    for t in sorted(trades, key=lambda x: x.entry_date):
        q = t.market_question[:34]
        diff = t.pnl - t.pnl_hold
        print(f"  {q:<35} {t.side:>4} {t.entry_price:.2f}    {t.exit_price:.2f}    {t.exit_reason:<10} ${t.pnl:>+7.1f} ${t.pnl_hold:>+7.1f} ${diff:>+7.1f}")

    # Grid over parameters
    print(f"\n{'='*80}")
    print("PARAMETER SENSITIVITY")
    print("="*80)

    return {
        "trades": len(trades),
        "total_pnl": total_pnl,
        "total_pnl_hold": total_pnl_hold,
        "win_rate": wins / len(trades) * 100 if trades else 0,
    }


def run_exit_grid(
    markets: list[dict],
    dune: DuneLoader,
    trader_cache: dict,
    step_days: int = 5,
):
    """Run exit simulation with multiple parameter combinations.
    Computes flow series ONCE, then replays different params instantly."""

    print(f"\nComputing SM flow series (step={step_days}d)... This is the slow part.")
    precomputed = compute_flow_series_all(markets, dune, trader_cache, step_days=step_days)
    print(f"Computed flow series for {len(precomputed)} markets.\n")

    params = [
        # (min_edge, exit_threshold)
        (0.03, 0.0),
        (0.05, 0.0),
        (0.10, 0.0),
        (0.15, 0.0),
        (0.20, 0.0),
        (0.05, -0.05),
        (0.10, -0.05),
        (0.10, -0.10),
        (0.15, -0.05),
        (0.15, -0.10),
        (0.20, -0.10),
    ]

    print("=" * 100)
    print(f"EXIT STRATEGY GRID SEARCH (step={step_days}d, fee=2%)")
    print("=" * 100)
    print(f"\n{'min_edge':>10} {'exit_thr':>10} {'Trades':>8} {'Exits':>6} {'Holds':>6} {'Wins':>6} {'Win%':>6} {'P&L exit':>10} {'P&L hold':>10} {'Advantage':>10}")
    print("-" * 95)

    for min_e, exit_t in params:
        trades = simulate_on_series(precomputed, min_edge=min_e, exit_threshold=exit_t)
        if not trades:
            continue

        total_pnl = sum(t.pnl for t in trades)
        total_hold = sum(t.pnl_hold for t in trades)
        wins = sum(1 for t in trades if t.pnl > 0)
        exits = sum(1 for t in trades if t.exit_reason != "resolution")
        holds = sum(1 for t in trades if t.exit_reason == "resolution")
        adv = total_pnl - total_hold

        print(f"  {min_e:>8.2f} {exit_t:>10.2f} {len(trades):>8} {exits:>6} {holds:>6} {wins:>6} {wins/len(trades)*100:>5.0f}% ${total_pnl:>+9.1f} ${total_hold:>+9.1f} ${adv:>+9.1f}")


def save_results(results: list[BacktestResult], summary: dict, filename: str = "RESULTS.md"):
    """Save results to markdown file."""
    path = Path(__file__).parent / filename

    lines = ["# SM Backtest — Political Markets\n"]
    lines.append(f"Markets analyzed: {summary['n_markets']}")
    lines.append(f"Trades (T-14, flow>0.1): {summary['trades']}")
    lines.append(f"Win rate: {summary['win_rate']:.0f}%")
    lines.append(f"Total P&L: \\${summary['total_pnl']:+,.0f}\n")

    lines.append("## Per-Market Results\n")
    lines.append("| Market | Won | T-30 | T-14 | T-7 | Hit? |")
    lines.append("|--------|-----|------|------|-----|------|")

    for r in sorted(results, key=lambda x: x.volume, reverse=True):
        won = "YES" if r.yes_won else "NO" if r.yes_won is not None else "?"

        flows = {}
        for d in [30, 14, 7]:
            if d in r.snapshots:
                flows[d] = f"{r.snapshots[d]['smart_flow']:+.2f}"
            else:
                flows[d] = "—"

        hit = "—"
        if 14 in r.snapshots and r.yes_won is not None:
            sm_yes = r.snapshots[14]["smart_flow"] > 0
            hit = "✓" if sm_yes == r.yes_won else "✗"

        q = r.market_question[:50].replace("|", "\\|")
        lines.append(f"| {q} | {won} | {flows[30]} | {flows[14]} | {flows[7]} | {hit} |")

    path.write_text("\n".join(lines))
    print(f"\nResults saved to {path}")


# === Main ===

def main():
    parser = argparse.ArgumentParser(description="SM Backtest for political markets")
    parser.add_argument("--query-id", type=int, required=True, help="Dune saved query ID")
    parser.add_argument("--slug", help="Single event slug to test")
    parser.add_argument("--market", help="Market filter (substring of question)")
    parser.add_argument("--discover", action="store_true", help="Auto-discover closed markets")
    parser.add_argument("--tag", default="politics", help="Tag for discovery")
    parser.add_argument("--min-volume", type=float, default=500_000)
    parser.add_argument("--limit", type=int, default=50, help="Max markets to discover")
    parser.add_argument("--snapshot-days", default="30,14,7", help="Days before resolution")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--simulate-exits", action="store_true", help="Run SM reversal exit simulation")
    parser.add_argument("--grid-exits", action="store_true", help="Grid search over exit parameters")
    parser.add_argument("--price-exit-grid", action="store_true", help="Grid search: exit at take-profit price")
    parser.add_argument("--min-edge", type=float, default=0.1, help="Min |flow| to enter (exit sim)")
    parser.add_argument("--exit-threshold", type=float, default=0.0, help="Flow threshold to exit")
    parser.add_argument("--step-days", type=int, default=5, help="Days between SM snapshots (exit sim)")
    args = parser.parse_args()

    api_key = os.environ.get("DUNE_API_KEY")
    if not api_key:
        env_path = Path(__file__).parent.parent.parent / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("DUNE_API_KEY="):
                    api_key = line.split("=", 1)[1].strip().strip("'\"")
        if not api_key:
            print("ERROR: Set DUNE_API_KEY env var")
            sys.exit(1)

    dune = DuneLoader(query_id=args.query_id, api_key=api_key)
    snapshot_days = [int(x) for x in args.snapshot_days.split(",")]

    # Get markets
    if args.slug:
        # Single event
        resp = httpx.get(f"{GAMMA_API}/events", params={"slug": args.slug}, timeout=30)
        resp.raise_for_status()
        events = resp.json()
        if not events:
            print(f"Event not found: {args.slug}")
            sys.exit(1)

        markets = []
        event = events[0]
        tags = [t.get("slug", "") for t in event.get("tags", []) if isinstance(t, dict)]

        for m in event.get("markets", []):
            if args.market and args.market.lower() not in m.get("question", "").lower():
                continue

            raw_tokens = m.get("clobTokenIds", [])
            if isinstance(raw_tokens, str):
                raw_tokens = json.loads(raw_tokens)
            if not raw_tokens or len(raw_tokens) < 2:
                continue

            outcome_prices = m.get("outcomePrices")
            try:
                op = json.loads(outcome_prices) if isinstance(outcome_prices, str) else outcome_prices
                yes_won = op[0] == "1" or op[0] == 1
            except Exception:
                yes_won = None

            markets.append({
                "event_title": event.get("title", ""),
                "event_slug": args.slug,
                "question": m.get("question", ""),
                "condition_id": m.get("conditionId", ""),
                "yes_token": raw_tokens[0],
                "no_token": raw_tokens[1],
                "volume": float(m.get("volumeNum", 0)),
                "end_date": m.get("endDate", ""),
                "yes_won": yes_won,
                "tags": tags,
                "neg_risk": m.get("negRisk", False),
            })

        print(f"Found {len(markets)} markets in event '{args.slug}'")

    elif args.discover:
        print(f"Discovering closed markets (tag={args.tag}, min_vol=${args.min_volume:,.0f})...")
        markets = discover_closed_political_markets(
            tag_slug=args.tag,
            min_volume=args.min_volume,
            limit=args.limit,
        )
        # Filter to only NegRisk markets (our Dune query targets NegRisk table)
        neg_risk_markets = [m for m in markets if m.get("neg_risk")]
        other_markets = [m for m in markets if not m.get("neg_risk")]
        print(f"Found {len(markets)} markets: {len(neg_risk_markets)} NegRisk, {len(other_markets)} CTF")

        if other_markets:
            print(f"  Note: {len(other_markets)} CTF markets need separate Dune query (skipped)")

        markets = neg_risk_markets
    else:
        print("Specify --slug or --discover")
        sys.exit(1)

    if not markets:
        print("No markets to backtest")
        sys.exit(0)

    # Exit simulation mode (uses cached data only, 0 Dune credits)
    if args.simulate_exits or args.grid_exits or args.price_exit_grid:
        trader_cache = load_trader_cache()

        if args.grid_exits:
            run_exit_grid(markets, dune, trader_cache, step_days=args.step_days)
        elif args.price_exit_grid:
            print(f"\nComputing SM flow series (step={args.step_days}d)...")
            precomputed = compute_flow_series_all(markets, dune, trader_cache, step_days=args.step_days)
            print(f"Computed for {len(precomputed)} markets.\n")
            run_price_exit_grid(precomputed, min_edge=args.min_edge)
        else:
            print(f"\nComputing SM flow series (step={args.step_days}d)...")
            precomputed = compute_flow_series_all(markets, dune, trader_cache, step_days=args.step_days)
            print(f"Computed for {len(precomputed)} markets.\n")
            exit_trades = simulate_on_series(
                precomputed,
                min_edge=args.min_edge,
                exit_threshold=args.exit_threshold,
            )
            print_exit_simulation(exit_trades, args.min_edge, args.exit_threshold, args.step_days)
        sys.exit(0)

    # Standard backtest
    results = run_backtest(markets, dune, snapshot_days)

    if results:
        summary = analyze_results(results, snapshot_days)
        save_results(results, summary)
    else:
        print("No results to analyze")


if __name__ == "__main__":
    main()
