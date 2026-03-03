"""
Portfolio-aware position sizing using Kelly criterion.

Provides:
- kelly_fraction(): half-Kelly for binary outcomes
- allocate_sizes(): distribute balance across signals with concentration limits
- get_portfolio_breakdown(): currency/direction breakdown for risk panel
"""

from typing import Dict, List, TYPE_CHECKING

if TYPE_CHECKING:
    from ..models.signal import Signal
    from ..models.position import Position
    from ..config import BotConfig


def kelly_fraction(edge: float, market_price: float) -> float:
    """Half-Kelly fraction for a binary outcome bet.

    For a binary bet at price p with edge e:
      fair_prob = p + e
      odds = (1 - p) / p
      kelly = (fair_prob * odds - (1 - fair_prob)) / odds

    Returns fraction of bankroll to wager (half-Kelly for safety).
    """
    if market_price <= 0.01 or market_price >= 0.99 or edge <= 0:
        return 0.0
    fair_prob = market_price + edge
    fair_prob = min(fair_prob, 0.99)
    odds = (1.0 - market_price) / market_price
    if odds <= 0:
        return 0.0
    kelly = (fair_prob * odds - (1.0 - fair_prob)) / odds
    kelly = max(0.0, min(kelly, 0.5))  # cap at 50%
    return kelly  # full Kelly


def allocate_sizes(
    signals: list,
    balance: float,
    positions: list,
    config,
) -> None:
    """Allocate budget across BUY signals using Kelly proportions.

    Kelly determines relative weights between positions (not absolute sizes).
    The alloc budget is distributed proportionally to Kelly weights.

    Modifies signals in-place: sets signal.kelly and signal.suggested_size.

    Limits:
    - max_position_pct: max fraction per single position (default 30%)
    - max_direction_pct: max fraction in one direction (up/down)
    - min_position_size: skip if allocation < this (default $5)
    """
    from ..models.signal import SignalType
    from ..logger import get_logger
    logger = get_logger()

    if balance <= 0:
        return

    max_pos_pct = getattr(config, "max_position_pct", 0.30)
    min_size = getattr(config, "min_position_size", 5.0)
    target_alloc = getattr(config, "target_alloc", 1.0)

    total_invested = sum(p.entry_size for p in positions)
    total_portfolio = balance + total_invested

    # Allocation budget: how much more we can invest to reach target
    target_invested = target_alloc * total_portfolio
    alloc_budget = min(max(0.0, target_invested - total_invested), balance)

    logger.log_info(
        f"ALLOC: portfolio=${total_portfolio:.0f} invested=${total_invested:.0f} "
        f"budget=${alloc_budget:.0f}"
    )

    if alloc_budget < min_size:
        logger.log_info(f"ALLOC: budget ${alloc_budget:.1f} < min ${min_size:.0f}, skipping")
        return

    # Compute Kelly for each BUY signal
    buy_signals = [s for s in signals if s.type == SignalType.BUY and s.liquidity > 0]
    if not buy_signals:
        return

    for s in buy_signals:
        s.kelly = kelly_fraction(s.edge, s.current_price)

    # Existing position sizes by market slug
    pos_by_slug: Dict[str, float] = {}
    for p in positions:
        pos_by_slug[p.market_slug] = pos_by_slug.get(p.market_slug, 0.0) + p.entry_size

    # Kelly as proportional weights: distribute alloc_budget by Kelly ratio
    eligible = [(s, s.kelly) for s in buy_signals if s.kelly > 0]
    if not eligible:
        return

    total_kelly = sum(k for _, k in eligible)

    # Allocate sizes with concentration limits
    remaining = alloc_budget
    for s in sorted(buy_signals, key=lambda x: x.kelly, reverse=True):
        if s.kelly <= 0 or remaining <= 0:
            s.suggested_size = 0.0
            continue

        # Target size from total portfolio (Kelly as proportion)
        raw_size = (s.kelly / total_kelly) * target_invested

        # Subtract already invested in this market
        already_in = pos_by_slug.get(s.market_slug, 0.0)
        raw_size = raw_size - already_in
        if raw_size < min_size:
            s.suggested_size = 0.0
            continue

        # Cap by position limit (30% of portfolio)
        max_per_position = max_pos_pct * total_portfolio
        raw_size = min(raw_size, max_per_position - already_in)

        # Cap by available liquidity
        raw_size = min(raw_size, s.liquidity)

        # Cap by remaining budget
        raw_size = min(raw_size, remaining)

        # Skip tiny positions
        if raw_size < min_size:
            s.suggested_size = 0.0
            continue

        s.suggested_size = raw_size
        remaining -= raw_size
        pos_by_slug[s.market_slug] = already_in + raw_size


def get_portfolio_breakdown(
    positions: list,
    balance: float,
) -> dict:
    """Get portfolio breakdown for risk panel display.

    Returns:
        {
            "currency": {"BTC": amount, "ETH": amount},
            "direction": {"up": amount, "down": amount},
            "total_invested": float,
            "balance": float,
            "total_portfolio": float,
            "position_count": int,
        }
    """
    currency: Dict[str, float] = {"BTC": 0.0, "ETH": 0.0}
    direction: Dict[str, float] = {"up": 0.0, "down": 0.0}
    total_invested = 0.0

    for p in positions:
        slug = p.market_slug.lower()
        # Detect currency
        if "btc" in slug or "bitcoin" in slug:
            currency["BTC"] += p.entry_size
        elif "eth" in slug or "ethereum" in slug:
            currency["ETH"] += p.entry_size

        # Detect direction (accounting for outcome side)
        d = _position_direction(p)
        direction[d] += p.entry_size
        total_invested += p.entry_size

    return {
        "currency": currency,
        "direction": direction,
        "total_invested": total_invested,
        "balance": balance,
        "total_portfolio": balance + total_invested,
        "position_count": len(positions),
    }


def _slug_direction(slug: str) -> str:
    """Detect direction from market slug: 'up' or 'down'."""
    slug_lower = slug.lower()
    # "hit", "reach", "above" → up; "dip", "below", "drop", "fall" → down
    down_keywords = ["dip", "below", "drop", "fall", "under"]
    for kw in down_keywords:
        if kw in slug_lower:
            return "down"
    return "up"


def _position_direction(position) -> str:
    """Detect effective direction from position (slug + outcome)."""
    slug_dir = _slug_direction(position.market_slug)
    outcome = getattr(position, "outcome", "YES")
    if outcome == "NO":
        return "down" if slug_dir == "up" else "up"
    return slug_dir


def _signal_direction(signal) -> str:
    """Detect direction from signal."""
    # NO side = betting it doesn't touch = opposite direction
    # YES side = betting it touches = direction from slug
    slug_dir = _slug_direction(signal.market_slug)
    if signal.outcome == "NO":
        return "down" if slug_dir == "up" else "up"
    return slug_dir
