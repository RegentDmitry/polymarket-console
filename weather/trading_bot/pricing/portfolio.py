"""
Portfolio-aware position sizing for weather markets.

Kelly proportional weights with hard concentration caps.
Hierarchy: kelly_weight → max_position_pct → max_per_bucket → max_per_event → max_per_city.
"""

import re
from typing import Dict

from ..config import WeatherBotConfig
from ..models.signal import SignalType

_EVENT_CITY_RE = re.compile(r"in-(.+?)-(?:be|on)-")
_EVENT_DATE_RE = re.compile(r"on-(\w+-\d+)$")


def kelly_fraction(edge: float, market_price: float) -> float:
    """Compute half-Kelly fraction for a binary market.

    Returns fraction of bankroll to wager (0.0–0.25).
    Uses half-Kelly for safety.
    """
    if market_price <= 0.01 or market_price >= 0.99 or edge <= 0:
        return 0.0

    fair_prob = min(market_price + edge, 0.99)
    odds = (1.0 - market_price) / market_price

    # Kelly = (p*b - q) / b where p=fair_prob, q=1-p, b=odds
    kelly = (fair_prob * odds - (1.0 - fair_prob)) / odds
    kelly *= 0.5  # half-Kelly
    return max(0.0, min(kelly, 0.25))


def allocate_sizes(
    signals: list,
    balance: float,
    positions: list,
    config: WeatherBotConfig,
) -> None:
    """Allocate budget across BUY signals using Kelly proportional weights.

    Modifies signals in-place: sets signal.kelly and signal.suggested_size.

    Steps:
    1. Compute kelly fraction for each BUY signal
    2. Distribute budget proportionally to kelly weights
    3. Apply hard caps (position %, bucket, event, city limits)
    4. Redistribute excess to remaining signals
    """
    if balance <= 0:
        return

    total_invested = sum(p.entry_size for p in positions)
    total_portfolio = balance + total_invested
    target_invested = config.target_alloc * total_portfolio
    budget = min(max(0.0, target_invested - total_invested), balance)

    if budget < config.min_position_size:
        return

    max_per_position = config.max_position_pct * total_portfolio

    # Track invested amounts per event and city (existing positions)
    event_invested: Dict[str, float] = {}
    city_invested: Dict[str, float] = {}
    bucket_invested: Dict[str, float] = {}

    for p in positions:
        bucket_invested[p.market_slug] = bucket_invested.get(p.market_slug, 0.0) + p.entry_size
        if p.event_slug:
            event_invested[p.event_slug] = event_invested.get(p.event_slug, 0.0) + p.entry_size
        if p.city:
            city_invested[p.city] = city_invested.get(p.city, 0.0) + p.entry_size

    # Step 1: Compute kelly for all BUY signals
    buy_signals = []
    for s in signals:
        if s.type != SignalType.BUY:
            s.suggested_size = 0.0
            s.kelly = 0.0
            continue
        s.kelly = kelly_fraction(s.edge, s.current_price)
        if s.kelly > 0:
            buy_signals.append(s)
        else:
            s.suggested_size = 0.0

    if not buy_signals:
        return

    # Step 2: Iterative allocation — proportional Kelly weights with
    # cap enforcement and min_size pruning. Each round:
    # - Distribute remaining budget proportionally to Kelly weights
    # - Cap signals that exceed hard limits (lock them in)
    # - Prune signals below min_size (exclude them)
    # - Redistribute freed budget to remaining signals
    remaining = budget
    active = list(buy_signals)
    min_size = config.min_position_size

    for _ in range(10):  # max 10 redistribution rounds
        if remaining < min_size or not active:
            break

        total_kelly = sum(s.kelly for s in active)
        if total_kelly <= 0:
            break

        # Compute proportional allocation for all active signals
        changed = False
        next_active = []

        for s in active:
            weight = s.kelly / total_kelly
            raw_size = weight * remaining

            # Hard caps
            event_slug = _event_slug_from_market(s.market_slug)
            bucket_room = config.max_per_bucket - bucket_invested.get(s.market_slug, 0.0)
            event_room = config.max_per_event - event_invested.get(event_slug, 0.0)
            city_room = config.max_per_city - city_invested.get(s.city, 0.0)
            cap = max(0.0, min(max_per_position, bucket_room, event_room,
                               city_room, s.liquidity))

            size = min(raw_size, cap)

            if size < min_size:
                s.suggested_size = 0.0
                changed = True
            elif raw_size > cap:
                s.suggested_size = size
                remaining -= size
                bucket_invested[s.market_slug] = bucket_invested.get(s.market_slug, 0.0) + size
                if event_slug:
                    event_invested[event_slug] = event_invested.get(event_slug, 0.0) + size
                if s.city:
                    city_invested[s.city] = city_invested.get(s.city, 0.0) + size
                changed = True
            else:
                s.suggested_size = size
                next_active.append(s)

        if not changed:
            # No caps hit and no pruning — finalize all remaining
            for s in next_active:
                event_slug = _event_slug_from_market(s.market_slug)
                bucket_invested[s.market_slug] = bucket_invested.get(s.market_slug, 0.0) + s.suggested_size
                if event_slug:
                    event_invested[event_slug] = event_invested.get(event_slug, 0.0) + s.suggested_size
                if s.city:
                    city_invested[s.city] = city_invested.get(s.city, 0.0) + s.suggested_size
            break

        active = next_active


def get_portfolio_breakdown(positions: list, balance: float) -> dict:
    """Get portfolio breakdown by city/date.

    Returns dict with by_city, by_date, by_event, totals.
    Risk metrics (percentiles, win prob) come from MC simulation separately.
    """
    by_city: Dict[str, float] = {}
    by_date: Dict[str, float] = {}
    by_event: Dict[str, float] = {}
    total_invested = 0.0

    for p in positions:
        by_city[p.city] = by_city.get(p.city, 0.0) + p.entry_size
        by_date[p.date] = by_date.get(p.date, 0.0) + p.entry_size
        if p.event_slug:
            by_event[p.event_slug] = by_event.get(p.event_slug, 0.0) + p.entry_size
        total_invested += p.entry_size

    total_portfolio = balance + total_invested

    return {
        "by_city": by_city,
        "by_date": by_date,
        "by_event": by_event,
        "total_invested": total_invested,
        "balance": balance,
        "total_portfolio": total_portfolio,
        "position_count": len(positions),
    }


def _event_slug_from_market(market_slug: str) -> str:
    """Extract event slug from market slug.

    Market: will-the-highest-temperature-in-chicago-be-44-45-f-on-march-7
    Event:  highest-temperature-in-chicago-on-march-7
    """
    city_match = _EVENT_CITY_RE.search(market_slug)
    date_match = _EVENT_DATE_RE.search(market_slug)
    if city_match and date_match:
        return f"highest-temperature-in-{city_match.group(1)}-on-{date_match.group(1)}"
    return market_slug
