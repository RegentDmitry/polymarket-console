#!/usr/bin/env python3
"""
Data loader for Deribit IV vs Polymarket backtest.

Discovers closed crypto markets from Polymarket, loads daily price history,
and fetches Deribit DVOL + spot prices. All data is cached locally.
"""

import json
import re
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.request import Request, urlopen
from urllib.error import URLError

from tqdm import tqdm

CACHE_DIR = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)

# All known event slugs with closed BTC/ETH crypto markets
EVENT_SLUGS = [
    # BTC annual
    "what-price-will-bitcoin-hit-in-2025",
    # BTC monthly
    "what-price-will-bitcoin-hit-in-november-2025",
    "what-price-will-bitcoin-hit-in-january-2026",
    # BTC 2027 (has closed sub-markets)
    "what-price-will-bitcoin-hit-before-2027",
    # ETH annual
    "what-price-will-ethereum-hit-in-2025",
    # ETH monthly
    "what-price-will-ethereum-hit-in-november-2025",
    "what-price-will-ethereum-hit-in-january-2026",
]


@dataclass
class Market:
    name: str               # e.g. "BTC reach $120k 2025"
    question: str           # Full question text
    event_slug: str         # Parent event slug
    token_yes: str          # YES token ID (for price history)
    token_no: str           # NO token ID
    strike: float           # Strike price (e.g. 120000)
    direction: str          # "above" or "below"
    currency: str           # "BTC" or "ETH"
    expiry_date: str        # ISO date string
    resolved: Optional[str] # "YES", "NO", or None (still active)
    closed: bool


def _fetch_json(url: str, timeout: int = 15) -> dict | list:
    """Fetch JSON from URL with User-Agent header."""
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _parse_strike(question: str) -> Optional[float]:
    """Extract strike price from question text. E.g. '$120,000' -> 120000."""
    m = re.search(r'\$(\d[\d,]+)', question)
    if m:
        return float(m.group(1).replace(",", ""))
    return None


def _parse_direction(question: str) -> str:
    """Determine direction from question. 'reach' -> above, 'dip' -> below."""
    q = question.lower()
    if "dip" in q or "fall" in q or "drop" in q:
        return "below"
    return "above"


def _parse_currency(question: str, event_slug: str) -> str:
    """Determine currency from question or event slug."""
    text = (question + " " + event_slug).lower()
    if "ethereum" in text or "eth" in text:
        return "ETH"
    return "BTC"


def _parse_expiry(event_slug: str) -> str:
    """Estimate expiry date from event slug."""
    # Annual events
    if "in-2025" in event_slug or "before-2026" in event_slug:
        return "2025-12-31"
    if "before-2027" in event_slug:
        return "2026-12-31"
    # Monthly events
    month_map = {
        "january": "01", "february": "02", "march": "03", "april": "04",
        "may": "05", "june": "06", "july": "07", "august": "08",
        "september": "09", "october": "10", "november": "11", "december": "12",
    }
    for month_name, month_num in month_map.items():
        if month_name in event_slug:
            # Find year
            year_match = re.search(r'20\d{2}', event_slug)
            year = year_match.group() if year_match else "2025"
            # Last day of month
            if month_num == "02":
                day = "28"
            elif month_num in ("04", "06", "09", "11"):
                day = "30"
            else:
                day = "31"
            return f"{year}-{month_num}-{day}"
    return "2025-12-31"


def _parse_resolution(outcome_prices: list) -> Optional[str]:
    """Parse resolution from outcomePrices. ['1','0'] = YES won, ['0','1'] = NO won."""
    if not outcome_prices or len(outcome_prices) < 2:
        return None
    try:
        yes_price = float(outcome_prices[0])
        no_price = float(outcome_prices[1])
    except (ValueError, TypeError):
        return None
    if yes_price >= 0.99:
        return "YES"
    if no_price >= 0.99:
        return "NO"
    return None  # Still active or ambiguous


def _fetch_event_markets(slug: str) -> list[Market]:
    """Fetch all markets for a given event slug from Gamma API."""
    url = f"https://gamma-api.polymarket.com/events?slug={slug}"
    try:
        events = _fetch_json(url)
    except Exception as e:
        print(f"  Warning: failed to fetch {slug}: {e}")
        return []

    markets = []
    for event in events:
        for m in event.get("markets", []):
            question = m.get("question", "")
            closed = m.get("closed", False)

            tokens = json.loads(m.get("clobTokenIds", "[]"))
            if len(tokens) < 2:
                continue

            outcome_prices = json.loads(m.get("outcomePrices", "[]"))
            strike = _parse_strike(question)
            if strike is None:
                continue

            direction = _parse_direction(question)
            currency = _parse_currency(question, slug)
            expiry = _parse_expiry(slug)
            resolved = _parse_resolution(outcome_prices)

            # Build short name
            action = "reach" if direction == "above" else "dip"
            strike_k = f"${strike/1000:.0f}k" if strike >= 1000 else f"${strike:.0f}"
            # Determine event type for name
            if "before-2027" in slug:
                period = "2027"
            elif "in-2025" in slug:
                period = "2025"
            elif "november" in slug:
                period = "Nov"
            elif "january" in slug:
                period = "Jan"
            elif "february" in slug:
                period = "Feb"
            else:
                period = ""
            name = f"{currency} {action} {strike_k} {period}".strip()

            markets.append(Market(
                name=name,
                question=question,
                event_slug=slug,
                token_yes=tokens[0],
                token_no=tokens[1],
                strike=strike,
                direction=direction,
                currency=currency,
                expiry_date=expiry,
                resolved=resolved,
                closed=closed,
            ))
    return markets


def discover_markets(
    event_slugs: list[str] = EVENT_SLUGS,
    closed_only: bool = True,
    use_cache: bool = True,
) -> list[Market]:
    """Discover all crypto markets from Polymarket Gamma API.

    Args:
        event_slugs: List of event slugs to query.
        closed_only: If True, only return closed/resolved markets.
        use_cache: If True, use cached markets list.

    Returns:
        List of Market objects.
    """
    cache_file = CACHE_DIR / "markets.json"
    if use_cache and cache_file.exists():
        data = json.loads(cache_file.read_text())
        markets = [Market(**m) for m in data]
        if closed_only:
            markets = [m for m in markets if m.closed]
        return markets

    all_markets = []
    with ThreadPoolExecutor(max_workers=len(event_slugs)) as executor:
        futures = {executor.submit(_fetch_event_markets, slug): slug for slug in event_slugs}
        for future in tqdm(as_completed(futures), total=len(futures), desc="Discovering markets"):
            all_markets.extend(future.result())

    # Deduplicate by token_yes
    seen = set()
    unique = []
    for m in all_markets:
        if m.token_yes not in seen:
            seen.add(m.token_yes)
            unique.append(m)
    all_markets = unique

    # Cache
    cache_file.write_text(json.dumps([asdict(m) for m in all_markets], indent=2))

    if closed_only:
        all_markets = [m for m in all_markets if m.closed]

    return all_markets


def _load_single_pm_prices(token_id: str) -> dict[str, float]:
    """Load PM price history for a single token. Returns {date_str: price}."""
    cache_file = CACHE_DIR / f"pm_{token_id[:20]}.json"
    if cache_file.exists():
        return json.loads(cache_file.read_text())

    url = f"https://clob.polymarket.com/prices-history?market={token_id}&interval=max&fidelity=1440"
    try:
        data = _fetch_json(url)
        history = data.get("history", [])
    except Exception:
        history = []

    prices = {}
    for point in history:
        t = point.get("t", 0)
        p = point.get("p", 0)
        if t and p:
            date_str = datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d")
            prices[date_str] = float(p)

    cache_file.write_text(json.dumps(prices))
    return prices


def load_all_pm_prices(
    markets: list[Market],
    max_workers: int = 8,
    use_cache: bool = True,
) -> dict[str, dict[str, float]]:
    """Load PM price history for all markets in parallel.

    Returns: {token_yes: {date_str: price}}
    """
    if not use_cache:
        # Clear PM cache files
        for f in CACHE_DIR.glob("pm_*.json"):
            f.unlink()

    results = {}
    tokens = [(m.token_yes, m.name) for m in markets]

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_load_single_pm_prices, token): (token, name)
            for token, name in tokens
        }
        for future in tqdm(as_completed(futures), total=len(futures), desc="Loading PM prices"):
            token, name = futures[future]
            try:
                prices = future.result()
                results[token] = prices
            except Exception as e:
                print(f"  Warning: failed to load {name}: {e}")
                results[token] = {}

    return results


def load_dvol(
    currency: str = "BTC",
    start_date: str = "2024-01-01",
    end_date: str = "2026-12-31",
    use_cache: bool = True,
) -> dict[str, float]:
    """Load Deribit DVOL (implied volatility index) daily data.

    Returns: {date_str: iv_decimal} (e.g. 0.50 for 50% IV)
    """
    cache_file = CACHE_DIR / f"dvol_{currency.lower()}.json"
    if use_cache and cache_file.exists():
        return json.loads(cache_file.read_text())

    start_ms = int(datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)
    end_ms = int(datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)

    url = (
        f"https://www.deribit.com/api/v2/public/get_volatility_index_data"
        f"?currency={currency}&resolution=86400"
        f"&start_timestamp={start_ms}&end_timestamp={end_ms}"
    )

    try:
        data = _fetch_json(url)
        records = data.get("result", {}).get("data", [])
    except Exception as e:
        print(f"Warning: failed to load DVOL for {currency}: {e}")
        return {}

    dvol = {}
    for record in records:
        # record = [timestamp_ms, open, high, low, close]
        ts = record[0]
        close_iv = record[4]  # IV in percentage
        date_str = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        dvol[date_str] = close_iv / 100.0  # Convert to decimal

    cache_file.write_text(json.dumps(dvol))
    print(f"  DVOL {currency}: {len(dvol)} days ({min(dvol.keys()) if dvol else '?'} → {max(dvol.keys()) if dvol else '?'})")
    return dvol


def load_spot_prices(
    currency: str = "BTC",
    start_date: str = "2024-01-01",
    end_date: str = "2026-12-31",
    use_cache: bool = True,
) -> dict[str, float]:
    """Load daily spot prices from Deribit PERPETUAL.

    Returns: {date_str: price_usd}
    """
    cache_file = CACHE_DIR / f"spot_{currency.lower()}.json"
    if use_cache and cache_file.exists():
        return json.loads(cache_file.read_text())

    start_ms = int(datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)
    end_ms = int(datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)

    url = (
        f"https://www.deribit.com/api/v2/public/get_tradingview_chart_data"
        f"?instrument_name={currency}-PERPETUAL&resolution=1D"
        f"&start_timestamp={start_ms}&end_timestamp={end_ms}"
    )

    try:
        data = _fetch_json(url)
        result = data.get("result", {})
        ticks = result.get("ticks", [])
        closes = result.get("close", [])
    except Exception as e:
        print(f"Warning: failed to load spot prices for {currency}: {e}")
        return {}

    prices = {}
    for ts, close in zip(ticks, closes):
        date_str = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        prices[date_str] = float(close)

    cache_file.write_text(json.dumps(prices))
    print(f"  Spot {currency}: {len(prices)} days ({min(prices.keys()) if prices else '?'} → {max(prices.keys()) if prices else '?'})")
    return prices


def load_all_data(
    use_cache: bool = True,
    closed_only: bool = True,
    currency_filter: Optional[str] = None,
) -> tuple[list[Market], dict, dict, dict, dict]:
    """Load all data needed for backtest.

    Returns: (markets, pm_prices, dvol_btc, dvol_eth, spot_btc, spot_eth)
    """
    print("=" * 60)
    print("  DATA LOADER")
    print("=" * 60)

    # 1. Discover markets
    markets = discover_markets(closed_only=closed_only, use_cache=use_cache)
    if currency_filter:
        markets = [m for m in markets if m.currency == currency_filter.upper()]

    # Filter out markets with no data potential (0-day price history)
    print(f"\nDiscovered {len(markets)} {'closed' if closed_only else 'total'} markets")

    # 2. Load PM prices
    pm_prices = load_all_pm_prices(markets, use_cache=use_cache)

    # Count markets with actual data
    has_data = sum(1 for m in markets if len(pm_prices.get(m.token_yes, {})) >= 3)
    print(f"\nMarkets with price data (>=3 days): {has_data}/{len(markets)}")

    # 3. Load Deribit data
    print("\nLoading Deribit data...")
    dvol_btc = load_dvol("BTC", use_cache=use_cache)
    dvol_eth = load_dvol("ETH", use_cache=use_cache)
    spot_btc = load_spot_prices("BTC", use_cache=use_cache)
    spot_eth = load_spot_prices("ETH", use_cache=use_cache)

    return markets, pm_prices, dvol_btc, dvol_eth, spot_btc, spot_eth


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Load backtest data")
    parser.add_argument("--no-cache", action="store_true", help="Force fresh data download")
    parser.add_argument("--currency", choices=["BTC", "ETH"], help="Filter by currency")
    args = parser.parse_args()

    markets, pm_prices, dvol_btc, dvol_eth, spot_btc, spot_eth = load_all_data(
        use_cache=not args.no_cache,
        currency_filter=args.currency,
    )

    print(f"\n{'='*60}")
    print(f"  SUMMARY")
    print(f"{'='*60}")
    print(f"Markets: {len(markets)}")
    print(f"DVOL BTC: {len(dvol_btc)} days")
    print(f"DVOL ETH: {len(dvol_eth)} days")
    print(f"Spot BTC: {len(spot_btc)} days")
    print(f"Spot ETH: {len(spot_eth)} days")

    print(f"\nMarkets with most data:")
    ranked = sorted(markets, key=lambda m: len(pm_prices.get(m.token_yes, {})), reverse=True)
    for m in ranked[:15]:
        days = len(pm_prices.get(m.token_yes, {}))
        res = m.resolved or "ACTIVE"
        print(f"  {m.name:<35} {days:>4} days  res={res}")
