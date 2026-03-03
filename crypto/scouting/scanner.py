"""Scanner: discover markets and load holder data into PostgreSQL."""

import json
import time
import sys

import httpx

from . import db

GAMMA_API = "https://gamma-api.polymarket.com"
DATA_API = "https://data-api.polymarket.com"
REQUEST_TIMEOUT = 30
HOLDER_LIMIT = 100  # max per side
RATE_LIMIT_SLEEP = 0.15  # seconds between API calls


def fetch_event(slug: str) -> dict:
    """Get event with all its markets from Gamma API."""
    resp = httpx.get(f"{GAMMA_API}/events", params={"slug": slug}, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    if not data:
        raise ValueError(f"Event '{slug}' not found")
    return data[0]


def fetch_resolved_events(limit: int = 100, offset: int = 0) -> list[dict]:
    """Get resolved events from Gamma API."""
    resp = httpx.get(
        f"{GAMMA_API}/events",
        params={"closed": True, "limit": limit, "offset": offset},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def fetch_holders(condition_id: str, limit: int = HOLDER_LIMIT) -> list[dict]:
    """Get top holders (both sides) for a market."""
    resp = httpx.get(
        f"{DATA_API}/holders",
        params={"market": condition_id, "limit": limit},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()

    holders = []
    for token_group in data:
        for h in token_group.get("holders", []):
            side = "YES" if h.get("outcomeIndex", 0) == 0 else "NO"
            holders.append({
                "address": h.get("proxyWallet", ""),
                "alias": h.get("pseudonym") or h.get("name", ""),
                "amount": float(h.get("amount", 0)),
                "side": side,
            })
    return holders


def fetch_trader_positions(address: str) -> tuple[list[dict], list[dict]]:
    """Fetch open and closed positions for a trader. Returns (open, closed)."""
    open_pos = []
    closed_pos = []

    # Open positions
    try:
        resp = httpx.get(
            f"{DATA_API}/positions",
            params={"user": address, "limit": 200, "sortBy": "CURRENT", "sortDir": "desc"},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        open_pos = resp.json()
    except Exception:
        pass

    time.sleep(RATE_LIMIT_SLEEP)

    # Closed positions (paginate)
    offset = 0
    while True:
        try:
            resp = httpx.get(
                f"{DATA_API}/closed-positions",
                params={"user": address, "limit": 200, "offset": offset},
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            closed_pos.extend(batch)
            if len(batch) < 200:
                break
            offset += 200
            time.sleep(RATE_LIMIT_SLEEP)
        except Exception:
            break

    return open_pos, closed_pos


def _classify_category(title: str) -> str:
    """Classify market category from title. Politics is broad — includes geopolitics,
    government, international relations, sanctions, wars, etc."""
    t = title.lower()
    if any(k in t for k in ("bitcoin", "btc", "ethereum", "eth", "crypto", "solana", "dogecoin")):
        return "crypto"
    if any(k in t for k in ("nfl", "nba", "mlb", "nhl", "super bowl", "world cup",
                             "premier league", "champions league", "ufc", "boxing")):
        return "sports"
    # Politics: very broad — elections, government, geopolitics, wars, sanctions, leaders
    politics_kw = (
        "trump", "biden", "election", "president", "congress", "senate", "governor",
        "government", "shutdown", "impeach", "supreme court", "scotus",
        "war", "invasion", "military", "strike", "nato", "sanctions",
        "china", "russia", "ukraine", "iran", "israel", "gaza", "palestine",
        "north korea", "taiwan", "syria",
        "greenland", "tariff", "trade war", "embargo",
        "khamenei", "putin", "zelensky", "netanyahu", "bolsonaro", "modi",
        "uk ", "france", "germany", "brazil", "mexico", "canada",
        "prime minister", "parliament", "referendum", "coup", "resign",
        "democrat", "republican", "gop", "tiktok ban", "debt ceiling",
        "cabinet", "secretary", "attorney general", "fbi", "cia", "doj",
        "indictment", "conviction", "pardon", "executive order",
        "ceasefire", "peace deal", "treaty", "nuclear",
        "assassination", "regime", "dictator", "leader",
        "border", "immigration", "deport", "asylum",
        "vance", "harris", "desantis", "haley", "newsom", "pence",
    )
    if any(k in t for k in politics_kw):
        return "politics"
    if any(k in t for k in ("fed ", "inflation", "gdp", "interest rate", "cpi", "recession",
                             "unemployment", "stock market", "s&p", "nasdaq")):
        return "economics"
    if any(k in t for k in ("earthquake", "weather", "hurricane", "climate", "tornado")):
        return "science"
    return "other"


def _parse_outcome(market_data: dict) -> str | None:
    """Determine resolution outcome from Gamma market data."""
    resolved = market_data.get("resolved", False)
    if not resolved:
        return None
    # resolutionSource can vary; check winner
    winner = market_data.get("winner")
    if winner is not None:
        return "YES" if str(winner) == "0" else "NO"
    return None


def scan_event(slug: str, load_holders: bool = True, verbose: bool = True) -> int:
    """Scan one event: save markets + optionally load holders.
    Returns number of markets processed."""
    if verbose:
        print(f"  Loading event: {slug}")

    event = fetch_event(slug)
    markets_data = event.get("markets", [])
    count = 0

    for m in markets_data:
        condition_id = m.get("conditionId", "")
        if not condition_id:
            continue

        title = m.get("groupItemTitle") or m.get("question", "?")
        outcome = _parse_outcome(m)
        end_date = m.get("endDate")
        volume = float(m.get("volume", 0) or 0)
        liquidity = float(m.get("liquidity", 0) or 0)
        category = _classify_category(title)

        db.upsert_market(
            condition_id=condition_id, slug=slug, title=title,
            outcome=outcome, end_date=end_date,
            volume=volume, liquidity=liquidity, category=category,
        )

        if load_holders:
            if verbose:
                print(f"    [{count+1}] {title[:50]} (vol=${volume:,.0f})", end="", flush=True)
            try:
                holders = fetch_holders(condition_id)
                n_new = _save_holders(holders, condition_id)
                if verbose:
                    print(f" — {len(holders)} holders, {n_new} new traders")
            except Exception as e:
                if verbose:
                    print(f" — ERROR: {e}")
            time.sleep(RATE_LIMIT_SLEEP)

        count += 1

    return count


def _save_holders(holders: list[dict], condition_id: str) -> int:
    """Save holders to DB. Returns count of new traders."""
    new_traders = 0
    for h in holders:
        address = h["address"]
        if not address:
            continue

        existing = db.get_trader(address)
        if not existing:
            new_traders += 1

        db.upsert_trader(address=address, alias=h.get("alias"))
        db.upsert_position(
            address=address,
            condition_id=condition_id,
            outcome_side=h["side"],
            size=h["amount"],
        )
    return new_traders


def load_trader_history(address: str, verbose: bool = False) -> dict:
    """Load full position history for a trader and save to DB.
    Returns summary stats."""
    open_pos, closed_pos = fetch_trader_positions(address)

    total_invested = 0
    total_returned = 0
    realized_pnl = 0
    win_count = 0
    loss_count = 0
    market_ids = set()

    for p in open_pos:
        cid = p.get("conditionId", "")
        if not cid:
            continue
        market_ids.add(cid)

        side = "YES" if p.get("outcomeIndex") == "0" or p.get("outcomeIndex") == 0 else "NO"
        size = float(p.get("size", 0) or 0)
        avg_price = float(p.get("avgPrice", 0) or 0)
        initial = float(p.get("initialValue", 0) or 0)
        current = float(p.get("currentValue", 0) or 0)
        pnl = float(p.get("cashPnl", 0) or 0)

        total_invested += initial

        db.upsert_position(
            address=address, condition_id=cid, outcome_side=side,
            size=size, avg_price=avg_price, initial_value=initial,
            current_value=current, realized_pnl=0, percent_pnl=0,
            is_closed=False,
        )

    for p in closed_pos:
        cid = p.get("conditionId", "")
        if not cid:
            continue
        market_ids.add(cid)

        side = "YES" if p.get("outcomeIndex") == "0" or p.get("outcomeIndex") == 0 else "NO"
        size = float(p.get("size", 0) or 0)
        avg_price = float(p.get("avgPrice", 0) or 0)
        initial = float(p.get("initialValue", 0) or 0)
        rpnl = float(p.get("realizedPnl", 0) or 0)
        ppnl = float(p.get("percentPnl", 0) or 0)

        total_invested += initial
        total_returned += initial + rpnl
        realized_pnl += rpnl

        if rpnl > 0:
            win_count += 1
        elif rpnl < 0:
            loss_count += 1

        db.upsert_position(
            address=address, condition_id=cid, outcome_side=side,
            size=size, avg_price=avg_price, initial_value=initial,
            current_value=0, realized_pnl=rpnl, percent_pnl=ppnl,
            is_closed=True,
        )

    total_markets = len(market_ids)
    avg_roi = (realized_pnl / total_invested * 100) if total_invested > 0 else 0

    db.update_trader_stats(
        address=address,
        total_markets=total_markets,
        win_count=win_count,
        loss_count=loss_count,
        total_invested=total_invested,
        total_returned=total_returned,
        realized_pnl=realized_pnl,
        avg_roi=avg_roi,
    )

    if verbose:
        alias = db.get_trader(address)
        name = alias["alias"] if alias and alias["alias"] else address[:16]
        print(f"    {name}: {total_markets} mkts, W{win_count}/L{loss_count}, "
              f"PnL=${realized_pnl:+,.0f}, ROI={avg_roi:+.1f}%")

    return {
        "total_markets": total_markets,
        "win_count": win_count,
        "loss_count": loss_count,
        "realized_pnl": realized_pnl,
        "avg_roi": avg_roi,
    }


def scan_resolved_events(max_events: int = 50, category: str = None,
                         min_volume: float = 10000, verbose: bool = True) -> int:
    """Scan resolved events from Gamma, load holders for each.
    Args:
        category: filter by category (e.g. 'politics'). None = all.
        min_volume: skip events with volume below this.
    Returns total markets scanned."""
    if verbose:
        cat_str = f", category={category}" if category else ""
        print(f"Fetching resolved events (limit={max_events}{cat_str})...")

    total_markets = 0
    offset = 0
    events_processed = 0
    events_skipped = 0

    while events_processed < max_events:
        batch_size = min(50, max_events - events_processed + events_skipped + 50)
        events = fetch_resolved_events(limit=batch_size, offset=offset)
        if not events:
            break

        for event in events:
            slug = event.get("slug", "")
            title = event.get("title", "?")
            if not slug:
                continue

            # Category filter
            if category:
                event_cat = _classify_category(title)
                if event_cat != category:
                    events_skipped += 1
                    continue

            # Volume filter
            event_vol = sum(float(m.get("volume", 0) or 0) for m in event.get("markets", []))
            if event_vol < min_volume:
                events_skipped += 1
                continue

            if verbose:
                print(f"\n[{events_processed+1}/{max_events}] {title[:60]} (vol=${event_vol:,.0f})")

            try:
                n = scan_event(slug, load_holders=True, verbose=verbose)
                total_markets += n
            except Exception as e:
                if verbose:
                    print(f"  ERROR: {e}")

            events_processed += 1
            if events_processed >= max_events:
                break

        offset += batch_size

    if verbose:
        stats = db.get_db_stats()
        print(f"\nDone. Events: {events_processed}, Markets: {total_markets}")
        print(f"DB: {stats['traders']} traders, {stats['markets']} markets, "
              f"{stats['positions']} positions")

    return total_markets


def enrich_traders(limit: int = None, verbose: bool = True) -> int:
    """Load full position history for all traders in DB.
    Returns number of traders enriched."""
    with db.get_cursor(commit=False) as cur:
        q = "SELECT address, alias FROM traders ORDER BY last_seen DESC"
        if limit:
            q += f" LIMIT {int(limit)}"
        cur.execute(q)
        traders = cur.fetchall()

    if verbose:
        print(f"Enriching {len(traders)} traders with full history...")

    count = 0
    for i, t in enumerate(traders):
        if verbose and (i + 1) % 10 == 0:
            print(f"  Progress: {i+1}/{len(traders)}")

        try:
            load_trader_history(t["address"], verbose=verbose)
            count += 1
        except Exception as e:
            if verbose:
                print(f"  ERROR {t['address'][:16]}: {e}")

        time.sleep(RATE_LIMIT_SLEEP)

    if verbose:
        print(f"Enriched {count}/{len(traders)} traders")

    return count
