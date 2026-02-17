#!/usr/bin/env python3
"""
Backtest: Deribit IV vs Polymarket crypto markets.

Simulates the strategy: buy when Deribit touch_prob shows edge vs PM price,
sell when edge disappears or market resolves.

Usage:
    python3 crypto/backtest/backtest.py
    python3 crypto/backtest/backtest.py --drift 0.27 --min-edge 0.05
    python3 crypto/backtest/backtest.py --grid-search
    python3 crypto/backtest/backtest.py --no-cache --currency BTC
"""

import argparse
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from itertools import product
from pathlib import Path
from typing import Optional

from tqdm import tqdm

# Add parent dirs to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from crypto.deribit_compare import touch_prob_above, touch_prob_below
from crypto.backtest.data_loader import load_all_data, Market


@dataclass
class Trade:
    market_name: str
    side: str           # "YES" or "NO"
    entry_date: str
    entry_price: float  # Price we paid per token
    tokens: float       # Number of tokens
    cost: float         # Total cost ($)
    exit_date: Optional[str] = None
    exit_price: Optional[float] = None
    exit_value: Optional[float] = None
    pnl: Optional[float] = None
    exit_reason: str = ""  # "resolution", "edge_exit", "end_of_data"


@dataclass
class BacktestParams:
    min_edge: float = 0.05
    exit_edge: float = 0.0
    drift: float = 0.0
    trade_size: float = 100.0
    fee: float = 0.0  # Taker fee on buys (e.g. 0.02 for 2%)


@dataclass
class BacktestResult:
    params: BacktestParams
    trades: list[Trade] = field(default_factory=list)
    markets_analyzed: int = 0
    markets_with_data: int = 0
    period_start: str = ""
    period_end: str = ""

    @property
    def total_pnl(self) -> float:
        return sum(t.pnl for t in self.trades if t.pnl is not None)

    @property
    def n_trades(self) -> int:
        return len(self.trades)

    @property
    def n_winners(self) -> int:
        return sum(1 for t in self.trades if t.pnl is not None and t.pnl > 0)

    @property
    def n_losers(self) -> int:
        return sum(1 for t in self.trades if t.pnl is not None and t.pnl <= 0)

    @property
    def win_rate(self) -> float:
        closed = [t for t in self.trades if t.pnl is not None]
        return self.n_winners / len(closed) if closed else 0.0

    @property
    def avg_pnl(self) -> float:
        closed = [t for t in self.trades if t.pnl is not None]
        return self.total_pnl / len(closed) if closed else 0.0


def _compute_edge(
    market: Market,
    spot: float,
    iv: float,
    pm_yes_price: float,
    days_to_expiry: float,
    drift: float,
) -> tuple[float, str]:
    """Compute edge and recommended side for a market.

    Returns: (edge, side) where edge > 0 means we have an edge.
    - For "above" (reach) markets: buy YES if touch_prob > pm_yes
    - For "below" (dip) markets: buy NO if pm_yes > touch_prob (PM overestimates dip)
    """
    T = days_to_expiry / 365.25
    if T <= 0 or iv <= 0:
        return 0.0, ""

    if market.direction == "above":
        touch = touch_prob_above(spot, market.strike, T, iv, drift)
        edge = touch - pm_yes_price
        return edge, "YES"
    else:  # below / dip
        touch = touch_prob_below(spot, market.strike, T, iv, drift)
        # PM YES price = probability of dip happening
        # If touch < pm_yes → PM overestimates dip → buy NO
        edge = pm_yes_price - touch  # Positive when PM overestimates
        return edge, "NO"


def run_backtest(
    markets: list[Market],
    pm_prices: dict[str, dict[str, float]],
    dvol: dict[str, float],  # {date: iv_decimal} for the right currency
    spot: dict[str, float],  # {date: price_usd}
    params: BacktestParams,
    show_progress: bool = True,
) -> BacktestResult:
    """Run backtest simulation across all markets."""
    result = BacktestResult(params=params, markets_analyzed=len(markets))

    # Get all dates with spot data
    all_dates = sorted(set(spot.keys()) & set(dvol.keys()))
    if all_dates:
        result.period_start = all_dates[0]
        result.period_end = all_dates[-1]

    iterator = tqdm(markets, desc="Simulating", disable=not show_progress)

    for market in iterator:
        prices = pm_prices.get(market.token_yes, {})
        if len(prices) < 3:
            continue

        result.markets_with_data += 1
        expiry_dt = datetime.strptime(market.expiry_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)

        # Get dates for this market (sorted)
        market_dates = sorted(prices.keys())
        if not market_dates:
            continue

        # Track open position for this market
        open_trade: Optional[Trade] = None

        for date_str in market_dates:
            pm_yes = prices[date_str]
            if pm_yes <= 0.01 or pm_yes >= 0.99:
                continue

            # Need spot and DVOL for this date
            if date_str not in spot or date_str not in dvol:
                continue

            spot_price = spot[date_str]
            iv = dvol[date_str]
            current_dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            days_to_expiry = (expiry_dt - current_dt).days

            if days_to_expiry <= 0:
                continue

            edge, side = _compute_edge(market, spot_price, iv, pm_yes, days_to_expiry, params.drift)

            # --- EXIT logic ---
            if open_trade is not None:
                should_exit = False
                exit_reason = ""

                if edge < params.exit_edge:
                    should_exit = True
                    exit_reason = "edge_exit"

                if should_exit:
                    # Sell at current PM price for our side
                    if open_trade.side == "YES":
                        exit_price = pm_yes
                    else:
                        exit_price = 1.0 - pm_yes  # NO price

                    open_trade.exit_date = date_str
                    open_trade.exit_price = exit_price
                    open_trade.exit_value = open_trade.tokens * exit_price
                    open_trade.pnl = open_trade.exit_value - open_trade.cost
                    open_trade.exit_reason = exit_reason
                    result.trades.append(open_trade)
                    open_trade = None

            # --- ENTRY logic ---
            if open_trade is None and edge >= params.min_edge:
                # Buy
                if side == "YES":
                    buy_price = pm_yes
                else:
                    buy_price = 1.0 - pm_yes  # NO price

                if buy_price <= 0.01 or buy_price >= 0.99:
                    continue

                cost = params.trade_size * (1 + params.fee)
                tokens = params.trade_size / buy_price

                open_trade = Trade(
                    market_name=market.name,
                    side=side,
                    entry_date=date_str,
                    entry_price=buy_price,
                    tokens=tokens,
                    cost=cost,
                )

        # --- End of market data: close open position ---
        if open_trade is not None:
            if market.resolved:
                # Market resolved — check if we won
                won = (open_trade.side == market.resolved)
                exit_price = 1.0 if won else 0.0
                open_trade.exit_date = market_dates[-1]
                open_trade.exit_price = exit_price
                open_trade.exit_value = open_trade.tokens * exit_price
                open_trade.pnl = open_trade.exit_value - open_trade.cost
                open_trade.exit_reason = "resolution"
            else:
                # Still active — mark at last known price
                last_date = market_dates[-1]
                last_pm_yes = prices[last_date]
                if open_trade.side == "YES":
                    exit_price = last_pm_yes
                else:
                    exit_price = 1.0 - last_pm_yes
                open_trade.exit_date = last_date
                open_trade.exit_price = exit_price
                open_trade.exit_value = open_trade.tokens * exit_price
                open_trade.pnl = open_trade.exit_value - open_trade.cost
                open_trade.exit_reason = "end_of_data"

            result.trades.append(open_trade)

    return result


def run_backtest_allin(
    markets: list[Market],
    pm_prices: dict[str, dict[str, float]],
    dvol: dict[str, float],
    spot: dict[str, float],
    params: BacktestParams,
    bankroll: float = 1000.0,
) -> BacktestResult:
    """All-in backtest: invest entire bankroll in the best signal, exit fully, repeat.

    Scans all markets each day, picks the highest-edge opportunity,
    goes all-in, then waits for exit before entering next trade.
    """
    result = BacktestResult(params=params, markets_analyzed=len(markets))

    all_dates = sorted(set(spot.keys()) & set(dvol.keys()))
    if all_dates:
        result.period_start = all_dates[0]
        result.period_end = all_dates[-1]

    # Pre-compute market data for fast lookup
    market_data = []
    for m in markets:
        prices = pm_prices.get(m.token_yes, {})
        if len(prices) < 3:
            continue
        result.markets_with_data += 1
        expiry_dt = datetime.strptime(m.expiry_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        market_data.append((m, prices, expiry_dt))

    capital = bankroll
    open_trade: Trade | None = None
    open_market: Market | None = None
    open_expiry: datetime | None = None

    for date_str in tqdm(all_dates, desc="All-in sim"):
        spot_price = spot[date_str]
        iv = dvol[date_str]
        current_dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)

        # --- EXIT logic (if we have a position) ---
        if open_trade is not None:
            pm_prices_market = pm_prices.get(open_market.token_yes, {})
            pm_yes = pm_prices_market.get(date_str)

            if pm_yes is None:
                # No price data for today — check if market resolved
                market_dates = sorted(pm_prices_market.keys())
                if market_dates and date_str > market_dates[-1] and open_market.resolved:
                    won = (open_trade.side == open_market.resolved)
                    exit_price = 1.0 if won else 0.0
                    open_trade.exit_date = date_str
                    open_trade.exit_price = exit_price
                    open_trade.exit_value = open_trade.tokens * exit_price
                    open_trade.pnl = open_trade.exit_value - open_trade.cost
                    open_trade.exit_reason = "resolution"
                    result.trades.append(open_trade)
                    capital += open_trade.exit_value
                    open_trade = None
                    open_market = None
                continue

            if pm_yes <= 0.01 or pm_yes >= 0.99:
                # Market at extreme — likely resolved
                if open_market.resolved:
                    won = (open_trade.side == open_market.resolved)
                    exit_price = 1.0 if won else 0.0
                else:
                    exit_price = pm_yes if open_trade.side == "YES" else 1.0 - pm_yes
                open_trade.exit_date = date_str
                open_trade.exit_price = exit_price
                open_trade.exit_value = open_trade.tokens * exit_price
                open_trade.pnl = open_trade.exit_value - open_trade.cost
                open_trade.exit_reason = "resolution"
                result.trades.append(open_trade)
                capital += open_trade.exit_value
                open_trade = None
                open_market = None
                continue

            days_to_expiry = (open_expiry - current_dt).days
            if days_to_expiry <= 0:
                continue

            edge, _ = _compute_edge(open_market, spot_price, iv, pm_yes, days_to_expiry, params.drift)

            if edge < params.exit_edge:
                if open_trade.side == "YES":
                    exit_price = pm_yes
                else:
                    exit_price = 1.0 - pm_yes
                open_trade.exit_date = date_str
                open_trade.exit_price = exit_price
                open_trade.exit_value = open_trade.tokens * exit_price
                open_trade.pnl = open_trade.exit_value - open_trade.cost
                open_trade.exit_reason = "edge_exit"
                result.trades.append(open_trade)
                capital += open_trade.exit_value
                open_trade = None
                open_market = None

        # --- ENTRY logic (find best signal across all markets) ---
        if open_trade is None and capital > 1.0:
            best_edge = params.min_edge
            best_candidate = None

            for m, prices, expiry_dt in market_data:
                pm_yes = prices.get(date_str)
                if pm_yes is None or pm_yes <= 0.01 or pm_yes >= 0.99:
                    continue

                days_to_expiry = (expiry_dt - current_dt).days
                if days_to_expiry <= 0:
                    continue

                edge, side = _compute_edge(m, spot_price, iv, pm_yes, days_to_expiry, params.drift)

                if edge > best_edge:
                    buy_price = pm_yes if side == "YES" else 1.0 - pm_yes
                    if 0.01 < buy_price < 0.99:
                        best_edge = edge
                        best_candidate = (m, side, buy_price, expiry_dt)

            if best_candidate:
                m, side, buy_price, expiry_dt = best_candidate
                cost = capital * (1 + params.fee)  # fee reduces effective capital
                invest = capital  # we invest all capital
                tokens = invest / buy_price

                open_trade = Trade(
                    market_name=m.name,
                    side=side,
                    entry_date=date_str,
                    entry_price=buy_price,
                    tokens=tokens,
                    cost=cost,
                )
                open_market = m
                open_expiry = expiry_dt
                capital = 0.0  # all-in

    # Close open position at end
    if open_trade is not None:
        if open_market.resolved:
            won = (open_trade.side == open_market.resolved)
            exit_price = 1.0 if won else 0.0
            open_trade.exit_reason = "resolution"
        else:
            last_prices = pm_prices.get(open_market.token_yes, {})
            last_dates = sorted(last_prices.keys())
            if last_dates:
                last_pm = last_prices[last_dates[-1]]
                exit_price = last_pm if open_trade.side == "YES" else 1.0 - last_pm
            else:
                exit_price = open_trade.entry_price
            open_trade.exit_reason = "end_of_data"
        open_trade.exit_date = all_dates[-1] if all_dates else ""
        open_trade.exit_price = exit_price
        open_trade.exit_value = open_trade.tokens * exit_price
        open_trade.pnl = open_trade.exit_value - open_trade.cost
        result.trades.append(open_trade)
        capital += open_trade.exit_value

    return result


def print_allin_results(result: BacktestResult, bankroll: float = 1000.0):
    """Print all-in backtest results with bankroll evolution."""
    p = result.params
    print()
    print("=" * 80)
    print(f"  ALL-IN BACKTEST: Deribit IV vs Polymarket")
    print("=" * 80)
    print(f"  Period: {result.period_start} → {result.period_end}")
    print(f"  Markets: {result.markets_analyzed} | with data: {result.markets_with_data}")
    print(f"  Parameters: min_edge={p.min_edge:.0%}, exit_edge={p.exit_edge:.0%}, "
          f"drift={p.drift:+.0%}, fee={p.fee:.0%}")
    print(f"  Starting bankroll: ${bankroll:,.2f}")
    print()

    closed = [t for t in result.trades if t.pnl is not None]
    if not closed:
        print("  No trades generated.")
        return

    # Simulate bankroll evolution
    capital = bankroll
    max_capital = capital
    max_drawdown = 0.0
    bankroll_history = [(closed[0].entry_date, capital)]

    for t in sorted(closed, key=lambda x: x.entry_date):
        capital = t.exit_value  # all-in: exit value IS the new capital
        if capital > max_capital:
            max_capital = capital
        dd = (max_capital - capital) / max_capital if max_capital > 0 else 0
        if dd > max_drawdown:
            max_drawdown = dd
        bankroll_history.append((t.exit_date, capital))

    final_capital = capital
    total_return = (final_capital / bankroll - 1) * 100

    print(f"  SUMMARY")
    print(f"  {'─' * 50}")
    print(f"  Total trades:      {len(closed)}")
    print(f"  Winners:           {result.n_winners} ({result.win_rate:.0%})")
    print(f"  Losers:            {result.n_losers}")
    print(f"  Final bankroll:    ${final_capital:,.2f}")
    print(f"  Total return:      {total_return:+.1f}%")
    print(f"  Max drawdown:      {max_drawdown:.1%}")
    print()

    print(f"  TRADES (chronological)")
    print(f"  {'─' * 95}")
    print(f"  {'#':>3} {'Market':<30} {'Side':>4} {'Entry':>6} {'Exit':>6} "
          f"{'Bankroll':>10} {'Return':>8} {'Reason':<12} {'Dates'}")
    print(f"  {'─' * 95}")

    capital = bankroll
    for i, t in enumerate(sorted(closed, key=lambda x: x.entry_date), 1):
        capital_after = t.exit_value
        ret = (capital_after / (t.cost) - 1) * 100 if t.cost > 0 else 0
        print(f"  {i:>3} {t.market_name:<30} {t.side:>4} {t.entry_price:>5.2f} {t.exit_price:>5.2f} "
              f"${capital_after:>9,.2f} {ret:>+7.1f}% {t.exit_reason:<12} {t.entry_date}→{t.exit_date}")
        capital = capital_after


@dataclass
class PortfolioPosition:
    """A live position in the portfolio."""
    market: Market
    trade: Trade
    expiry_dt: datetime


def _kelly_fraction(edge: float, win_price: float) -> float:
    """Simplified Kelly criterion for binary outcome.

    For a binary bet at price p with edge e:
      fair_prob = p + e
      odds = (1 - p) / p  (payout if win, relative to stake)
      kelly = (fair_prob * odds - (1 - fair_prob)) / odds
            = fair_prob - (1 - fair_prob) / odds
            = fair_prob - (1 - fair_prob) * p / (1 - p)

    We use half-Kelly for safety.
    """
    if win_price <= 0.01 or win_price >= 0.99 or edge <= 0:
        return 0.0
    fair_prob = win_price + edge
    fair_prob = min(fair_prob, 0.99)
    odds = (1.0 - win_price) / win_price
    if odds <= 0:
        return 0.0
    kelly = (fair_prob * odds - (1.0 - fair_prob)) / odds
    kelly = max(0.0, min(kelly, 0.5))  # cap at 50%
    return kelly * 0.5  # half-Kelly


def run_backtest_portfolio(
    markets: list[Market],
    pm_prices: dict[str, dict[str, float]],
    dvol: dict[str, float] | dict[str, dict[str, float]] = None,
    spot: dict[str, float] | dict[str, dict[str, float]] = None,
    params: BacktestParams = None,
    bankroll: float = 1000.0,
    max_positions: int = 5,
    dvol_by_currency: dict[str, dict[str, float]] = None,
    spot_by_currency: dict[str, dict[str, float]] = None,
) -> tuple[BacktestResult, list[tuple[str, float]]]:
    """Portfolio backtest: up to N concurrent positions, Kelly-weighted allocation.

    When capital frees up (position exits), reinvest into best available signal.
    Returns (result, equity_curve) where equity_curve = [(date, total_value), ...].

    Can be called two ways:
    - Single currency: dvol={date: iv}, spot={date: price}
    - Multi currency: dvol_by_currency={"BTC": {...}, "ETH": {...}}, same for spot
    """
    result = BacktestResult(params=params, markets_analyzed=len(markets))

    # Build per-currency lookups
    if dvol_by_currency and spot_by_currency:
        _dvol = dvol_by_currency
        _spot = spot_by_currency
    else:
        # Single currency mode — detect from markets
        currency = markets[0].currency if markets else "BTC"
        _dvol = {currency: dvol}
        _spot = {currency: spot}

    # Union of all dates across currencies
    all_date_sets = []
    for cur in _dvol:
        if cur in _spot:
            all_date_sets.append(set(_dvol[cur].keys()) & set(_spot[cur].keys()))
    all_dates = sorted(set().union(*all_date_sets)) if all_date_sets else []
    if all_dates:
        result.period_start = all_dates[0]
        result.period_end = all_dates[-1]

    # Pre-compute market data
    market_data = []
    for m in markets:
        prices = pm_prices.get(m.token_yes, {})
        if len(prices) < 3:
            continue
        result.markets_with_data += 1
        expiry_dt = datetime.strptime(m.expiry_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        market_data.append((m, prices, expiry_dt))

    cash = bankroll
    positions: list[PortfolioPosition] = []
    equity_curve: list[tuple[str, float]] = []

    for date_str in tqdm(all_dates, desc="Portfolio sim"):
        current_dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)

        # --- EXIT logic: check all open positions ---
        still_open = []
        for pos in positions:
            cur = pos.market.currency
            if date_str not in _spot.get(cur, {}) or date_str not in _dvol.get(cur, {}):
                still_open.append(pos)
                continue

            spot_price = _spot[cur][date_str]
            iv = _dvol[cur][date_str]
            pm_prices_market = pm_prices.get(pos.market.token_yes, {})
            pm_yes = pm_prices_market.get(date_str)

            should_close = False
            exit_reason = ""
            exit_price = 0.0

            if pm_yes is None:
                # No price today — check if past market end
                market_dates = sorted(pm_prices_market.keys())
                if market_dates and date_str > market_dates[-1] and pos.market.resolved:
                    should_close = True
                    exit_reason = "resolution"
                    won = (pos.trade.side == pos.market.resolved)
                    exit_price = 1.0 if won else 0.0
                else:
                    still_open.append(pos)
                    continue
            elif pm_yes <= 0.01 or pm_yes >= 0.99:
                should_close = True
                exit_reason = "resolution"
                if pos.market.resolved:
                    won = (pos.trade.side == pos.market.resolved)
                    exit_price = 1.0 if won else 0.0
                else:
                    exit_price = pm_yes if pos.trade.side == "YES" else 1.0 - pm_yes
            else:
                days_to_expiry = (pos.expiry_dt - current_dt).days
                if days_to_expiry <= 0:
                    still_open.append(pos)
                    continue

                edge, _ = _compute_edge(pos.market, spot_price, iv, pm_yes, days_to_expiry, params.drift)
                if edge < params.exit_edge:
                    should_close = True
                    exit_reason = "edge_exit"
                    exit_price = pm_yes if pos.trade.side == "YES" else 1.0 - pm_yes

            if should_close:
                pos.trade.exit_date = date_str
                pos.trade.exit_price = exit_price
                pos.trade.exit_value = pos.trade.tokens * exit_price
                pos.trade.pnl = pos.trade.exit_value - pos.trade.cost
                pos.trade.exit_reason = exit_reason
                result.trades.append(pos.trade)
                cash += pos.trade.exit_value
            else:
                still_open.append(pos)

        positions = still_open

        # --- ENTRY logic: fill empty slots with best signals ---
        if len(positions) < max_positions and cash > 5.0:
            # Collect all signals for today
            occupied_tokens = {p.market.token_yes for p in positions}
            candidates = []

            for m, prices, expiry_dt in market_data:
                if m.token_yes in occupied_tokens:
                    continue
                cur = m.currency
                if date_str not in _spot.get(cur, {}) or date_str not in _dvol.get(cur, {}):
                    continue
                pm_yes = prices.get(date_str)
                if pm_yes is None or pm_yes <= 0.01 or pm_yes >= 0.99:
                    continue
                days_to_expiry = (expiry_dt - current_dt).days
                if days_to_expiry <= 0:
                    continue

                spot_price = _spot[cur][date_str]
                iv = _dvol[cur][date_str]
                edge, side = _compute_edge(m, spot_price, iv, pm_yes, days_to_expiry, params.drift)
                if edge >= params.min_edge:
                    buy_price = pm_yes if side == "YES" else 1.0 - pm_yes
                    if 0.01 < buy_price < 0.99:
                        kelly = _kelly_fraction(edge, buy_price)
                        candidates.append((m, side, buy_price, expiry_dt, edge, kelly))

            # Sort by edge descending, take top N to fill slots
            candidates.sort(key=lambda x: x[4], reverse=True)
            slots_available = max_positions - len(positions)

            # Calculate Kelly weights for selected candidates
            selected = candidates[:slots_available]
            if selected:
                total_kelly = sum(c[5] for c in selected)
                if total_kelly <= 0:
                    total_kelly = 1.0

                for m, side, buy_price, expiry_dt, edge, kelly in selected:
                    if cash <= 5.0:
                        break

                    # Allocate proportional to Kelly, but minimum $10
                    alloc_frac = kelly / total_kelly
                    invest = cash * alloc_frac
                    invest = max(invest, min(10.0, cash))
                    invest = min(invest, cash)

                    cost = invest * (1 + params.fee)
                    tokens = invest / buy_price

                    trade = Trade(
                        market_name=m.name,
                        side=side,
                        entry_date=date_str,
                        entry_price=buy_price,
                        tokens=tokens,
                        cost=cost,
                    )
                    positions.append(PortfolioPosition(market=m, trade=trade, expiry_dt=expiry_dt))
                    cash -= invest

        # --- Equity snapshot ---
        positions_value = 0.0
        for pos in positions:
            pm_prices_market = pm_prices.get(pos.market.token_yes, {})
            pm_yes = pm_prices_market.get(date_str)
            if pm_yes is not None:
                if pos.trade.side == "YES":
                    positions_value += pos.trade.tokens * pm_yes
                else:
                    positions_value += pos.trade.tokens * (1.0 - pm_yes)
            else:
                positions_value += pos.trade.cost  # fallback: at cost

        equity_curve.append((date_str, cash + positions_value))

    # Close remaining positions at end
    for pos in positions:
        if pos.market.resolved:
            won = (pos.trade.side == pos.market.resolved)
            exit_price = 1.0 if won else 0.0
            pos.trade.exit_reason = "resolution"
        else:
            last_prices = pm_prices.get(pos.market.token_yes, {})
            last_dates = sorted(last_prices.keys())
            if last_dates:
                last_pm = last_prices[last_dates[-1]]
                exit_price = last_pm if pos.trade.side == "YES" else 1.0 - last_pm
            else:
                exit_price = pos.trade.entry_price
            pos.trade.exit_reason = "end_of_data"
        pos.trade.exit_date = all_dates[-1] if all_dates else ""
        pos.trade.exit_price = exit_price
        pos.trade.exit_value = pos.trade.tokens * exit_price
        pos.trade.pnl = pos.trade.exit_value - pos.trade.cost
        result.trades.append(pos.trade)
        cash += pos.trade.exit_value

    return result, equity_curve


def print_portfolio_results(result: BacktestResult, equity_curve: list[tuple[str, float]],
                            bankroll: float = 1000.0):
    """Print portfolio backtest results."""
    p = result.params
    print()
    print("=" * 80)
    print(f"  PORTFOLIO BACKTEST: Deribit IV vs Polymarket")
    print("=" * 80)
    print(f"  Period: {result.period_start} → {result.period_end}")
    print(f"  Markets: {result.markets_analyzed} | with data: {result.markets_with_data}")
    print(f"  Parameters: min_edge={p.min_edge:.0%}, exit_edge={p.exit_edge:.0%}, "
          f"drift={p.drift:+.0%}, fee={p.fee:.0%}")
    print(f"  Starting bankroll: ${bankroll:,.2f} | Max positions: 5 | Sizing: half-Kelly")
    print()

    closed = sorted([t for t in result.trades if t.pnl is not None], key=lambda x: x.entry_date)
    if not closed:
        print("  No trades generated.")
        return

    final_equity = equity_curve[-1][1] if equity_curve else bankroll
    total_return = (final_equity / bankroll - 1) * 100

    # Max drawdown from equity curve
    max_equity = bankroll
    max_dd = 0.0
    for _, eq in equity_curve:
        if eq > max_equity:
            max_equity = eq
        dd = (max_equity - eq) / max_equity if max_equity > 0 else 0
        if dd > max_dd:
            max_dd = dd

    winners = sum(1 for t in closed if t.pnl and t.pnl > 0)

    print(f"  SUMMARY")
    print(f"  {'─' * 50}")
    print(f"  Total trades:      {len(closed)}")
    print(f"  Winners:           {winners} ({winners / len(closed):.0%})")
    print(f"  Losers:            {len(closed) - winners}")
    print(f"  Final equity:      ${final_equity:,.2f}")
    print(f"  Total return:      {total_return:+.1f}%")
    print(f"  Max drawdown:      {max_dd:.1%}")
    print(f"  Total P&L:         ${sum(t.pnl for t in closed):+,.2f}")
    print()

    # Trades table
    print(f"  TRADES (chronological)")
    print(f"  {'─' * 100}")
    print(f"  {'#':>3} {'Market':<28} {'Side':>4} {'Entry':>6} {'Exit':>6} "
          f"{'Size':>8} {'P&L':>9} {'Ret':>7} {'Reason':<12} {'Dates'}")
    print(f"  {'─' * 100}")

    for i, t in enumerate(closed, 1):
        ret = (t.pnl / t.cost * 100) if t.cost > 0 else 0
        print(f"  {i:>3} {t.market_name:<28} {t.side:>4} {t.entry_price:>5.2f} {t.exit_price:>5.2f} "
              f"${t.cost:>7.1f} ${t.pnl:>+8.2f} {ret:>+6.1f}% {t.exit_reason:<12} "
              f"{t.entry_date}→{t.exit_date}")

    # Equity milestones
    print(f"\n  EQUITY MILESTONES")
    print(f"  {'─' * 40}")
    milestones = [equity_curve[0]]
    prev_month = equity_curve[0][0][:7]
    for date_str, eq in equity_curve:
        if date_str[:7] != prev_month:
            milestones.append((date_str, eq))
            prev_month = date_str[:7]
    milestones.append(equity_curve[-1])

    for date_str, eq in milestones:
        ret = (eq / bankroll - 1) * 100
        bar_len = int(max(0, min(50, eq / bankroll * 10)))
        bar = "█" * bar_len
        print(f"  {date_str}  ${eq:>10,.2f}  {ret:>+7.1f}%  {bar}")


def save_portfolio_chart(equity_curve: list[tuple[str, float]], result: BacktestResult,
                         output_path: str = "crypto/backtest/portfolio_results.png",
                         bankroll: float = 1000.0):
    """Save portfolio equity curve chart."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
    except ImportError:
        print("  matplotlib not available, skipping chart")
        return

    dates = [datetime.strptime(d, "%Y-%m-%d") for d, _ in equity_curve]
    values = [v for _, v in equity_curve]

    fig, ax = plt.subplots(figsize=(12, 6))
    ax.plot(dates, values, linewidth=2, color="#4CAF50")
    ax.fill_between(dates, bankroll, values, alpha=0.15, color="#4CAF50",
                    where=[v >= bankroll for v in values])
    ax.fill_between(dates, bankroll, values, alpha=0.15, color="#F44336",
                    where=[v < bankroll for v in values])
    ax.axhline(y=bankroll, color="gray", linestyle="--", linewidth=0.8, label=f"Start ${bankroll:,.0f}")

    p = result.params
    final = values[-1] if values else bankroll
    ax.set_title(
        f"Portfolio Backtest: Deribit IV vs Polymarket\n"
        f"edge>{p.min_edge:.0%}, drift={p.drift:+.0%}, half-Kelly, max 5 positions | "
        f"${bankroll:,.0f} → ${final:,.0f} ({(final/bankroll-1)*100:+.0f}%)",
        fontsize=12,
    )
    ax.set_ylabel("Portfolio Value ($)")
    ax.set_xlabel("Date")
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    fig.autofmt_xdate()
    ax.legend()

    ax.annotate(
        f"${final:,.0f}",
        xy=(dates[-1], final),
        fontsize=11, fontweight="bold",
        xytext=(10, 5), textcoords="offset points",
        color="#4CAF50" if final >= bankroll else "#F44336",
    )

    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    print(f"\n  Chart saved to {output_path}")


def run_backtest_daily_rebalance(
    markets: list[Market],
    pm_prices: dict[str, dict[str, float]],
    params: BacktestParams,
    bankroll: float = 1000.0,
    max_positions: int = 5,
    dvol_by_currency: dict[str, dict[str, float]] = None,
    spot_by_currency: dict[str, dict[str, float]] = None,
) -> tuple[BacktestResult, list[tuple[str, float]]]:
    """Daily rebalance backtest: every day at 10am, review entire portfolio.

    Each day:
    1. Score all available markets by edge
    2. Score current positions by remaining edge
    3. Keep top-N across both — rotate if a new opportunity beats a current position
    4. Size by half-Kelly

    This simulates a trader who checks once per day and can't act between reviews.
    """
    result = BacktestResult(params=params, markets_analyzed=len(markets))

    _dvol = dvol_by_currency or {}
    _spot = spot_by_currency or {}

    # Union of all dates
    all_date_sets = []
    for cur in _dvol:
        if cur in _spot:
            all_date_sets.append(set(_dvol[cur].keys()) & set(_spot[cur].keys()))
    all_dates = sorted(set().union(*all_date_sets)) if all_date_sets else []

    if all_dates:
        result.period_start = all_dates[0]
        result.period_end = all_dates[-1]

    # Pre-compute market data
    market_data = []
    for m in markets:
        prices = pm_prices.get(m.token_yes, {})
        if len(prices) < 3:
            continue
        result.markets_with_data += 1
        expiry_dt = datetime.strptime(m.expiry_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        market_data.append((m, prices, expiry_dt))

    cash = bankroll
    # positions: {token_yes: (Market, Trade, expiry_dt)}
    positions: dict[str, tuple[Market, Trade, datetime]] = {}
    equity_curve: list[tuple[str, float]] = []

    for date_str in tqdm(all_dates, desc="Daily rebalance"):
        current_dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)

        # --- Step 1: Score everything ---
        # Score current positions
        current_scores: dict[str, float] = {}  # token -> edge
        for token, (m, trade, expiry_dt) in list(positions.items()):
            cur = m.currency
            if date_str not in _spot.get(cur, {}) or date_str not in _dvol.get(cur, {}):
                current_scores[token] = 0.0  # can't evaluate, keep neutral
                continue

            pm_prices_market = pm_prices.get(token, {})
            pm_yes = pm_prices_market.get(date_str)

            if pm_yes is None:
                # Past market end — check resolution
                market_dates = sorted(pm_prices_market.keys())
                if market_dates and date_str > market_dates[-1] and m.resolved:
                    won = (trade.side == m.resolved)
                    exit_price = 1.0 if won else 0.0
                    trade.exit_date = date_str
                    trade.exit_price = exit_price
                    trade.exit_value = trade.tokens * exit_price
                    trade.pnl = trade.exit_value - trade.cost
                    trade.exit_reason = "resolution"
                    result.trades.append(trade)
                    cash += trade.exit_value
                    del positions[token]
                continue

            if pm_yes <= 0.01 or pm_yes >= 0.99:
                # Resolved
                if m.resolved:
                    won = (trade.side == m.resolved)
                    exit_price = 1.0 if won else 0.0
                else:
                    exit_price = pm_yes if trade.side == "YES" else 1.0 - pm_yes
                trade.exit_date = date_str
                trade.exit_price = exit_price
                trade.exit_value = trade.tokens * exit_price
                trade.pnl = trade.exit_value - trade.cost
                trade.exit_reason = "resolution"
                result.trades.append(trade)
                cash += trade.exit_value
                del positions[token]
                continue

            days_to_expiry = (expiry_dt - current_dt).days
            if days_to_expiry <= 0:
                current_scores[token] = -1.0  # force exit
                continue

            spot_price = _spot[cur][date_str]
            iv = _dvol[cur][date_str]
            edge, _ = _compute_edge(m, spot_price, iv, pm_yes, days_to_expiry, params.drift)
            current_scores[token] = edge

        # Score potential new positions
        new_candidates = []  # (market, side, buy_price, expiry_dt, edge, kelly, token)
        occupied_tokens = set(positions.keys())

        for m, prices, expiry_dt in market_data:
            if m.token_yes in occupied_tokens:
                continue
            cur = m.currency
            if date_str not in _spot.get(cur, {}) or date_str not in _dvol.get(cur, {}):
                continue
            pm_yes = prices.get(date_str)
            if pm_yes is None or pm_yes <= 0.01 or pm_yes >= 0.99:
                continue
            days_to_expiry = (expiry_dt - current_dt).days
            if days_to_expiry <= 0:
                continue

            spot_price = _spot[cur][date_str]
            iv = _dvol[cur][date_str]
            edge, side = _compute_edge(m, spot_price, iv, pm_yes, days_to_expiry, params.drift)

            if edge >= params.min_edge:
                buy_price = pm_yes if side == "YES" else 1.0 - pm_yes
                if 0.01 < buy_price < 0.99:
                    kelly = _kelly_fraction(edge, buy_price)
                    new_candidates.append((m, side, buy_price, expiry_dt, edge, kelly, m.token_yes))

        new_candidates.sort(key=lambda x: x[4], reverse=True)

        # --- Step 2: Decide what to keep / rotate ---
        # Exit positions where edge dropped below exit_edge
        for token in list(positions.keys()):
            if token not in current_scores:
                continue
            if current_scores[token] < params.exit_edge:
                m, trade, expiry_dt = positions[token]
                pm_prices_market = pm_prices.get(token, {})
                pm_yes = pm_prices_market.get(date_str, trade.entry_price)
                exit_price = pm_yes if trade.side == "YES" else 1.0 - pm_yes

                trade.exit_date = date_str
                trade.exit_price = exit_price
                trade.exit_value = trade.tokens * exit_price
                trade.pnl = trade.exit_value - trade.cost
                trade.exit_reason = "edge_exit"
                result.trades.append(trade)
                cash += trade.exit_value
                del positions[token]

        # Rotation: if a new candidate has higher edge than worst current position, swap
        while (new_candidates and len(positions) >= max_positions):
            best_new = new_candidates[0]
            best_new_edge = best_new[4]

            # Find worst current position
            if not positions:
                break
            worst_token = min(current_scores, key=lambda t: current_scores.get(t, 0))
            worst_edge = current_scores.get(worst_token, 0)

            # Only rotate if new is significantly better (>3% edge improvement)
            if best_new_edge > worst_edge + 0.03:
                m, trade, expiry_dt = positions[worst_token]
                pm_prices_market = pm_prices.get(worst_token, {})
                pm_yes = pm_prices_market.get(date_str, trade.entry_price)
                exit_price = pm_yes if trade.side == "YES" else 1.0 - pm_yes

                trade.exit_date = date_str
                trade.exit_price = exit_price
                trade.exit_value = trade.tokens * exit_price
                trade.pnl = trade.exit_value - trade.cost
                trade.exit_reason = "rotation"
                result.trades.append(trade)
                cash += trade.exit_value
                del positions[worst_token]
                del current_scores[worst_token]
            else:
                break  # no more beneficial rotations

        # --- Step 3: Fill empty slots ---
        slots = max_positions - len(positions)
        if slots > 0 and cash > 5.0:
            # Filter out candidates for already-held markets
            available = [c for c in new_candidates if c[6] not in positions][:slots]
            if available:
                total_kelly = sum(c[5] for c in available)
                if total_kelly <= 0:
                    total_kelly = 1.0

                for m, side, buy_price, expiry_dt, edge, kelly, token in available:
                    if cash <= 5.0:
                        break
                    alloc_frac = kelly / total_kelly
                    invest = cash * alloc_frac
                    invest = max(invest, min(10.0, cash))
                    invest = min(invest, cash)

                    cost = invest * (1 + params.fee)
                    tokens = invest / buy_price

                    trade = Trade(
                        market_name=m.name,
                        side=side,
                        entry_date=date_str,
                        entry_price=buy_price,
                        tokens=tokens,
                        cost=cost,
                    )
                    positions[m.token_yes] = (m, trade, expiry_dt)
                    current_scores[m.token_yes] = edge
                    cash -= invest

        # --- Equity snapshot ---
        positions_value = 0.0
        for token, (m, trade, _) in positions.items():
            pm_prices_market = pm_prices.get(token, {})
            pm_yes = pm_prices_market.get(date_str)
            if pm_yes is not None:
                if trade.side == "YES":
                    positions_value += trade.tokens * pm_yes
                else:
                    positions_value += trade.tokens * (1.0 - pm_yes)
            else:
                positions_value += trade.cost
        equity_curve.append((date_str, cash + positions_value))

    # Close remaining
    for token, (m, trade, _) in positions.items():
        if m.resolved:
            won = (trade.side == m.resolved)
            exit_price = 1.0 if won else 0.0
            trade.exit_reason = "resolution"
        else:
            last_prices = pm_prices.get(token, {})
            last_dates = sorted(last_prices.keys())
            if last_dates:
                last_pm = last_prices[last_dates[-1]]
                exit_price = last_pm if trade.side == "YES" else 1.0 - last_pm
            else:
                exit_price = trade.entry_price
            trade.exit_reason = "end_of_data"
        trade.exit_date = all_dates[-1] if all_dates else ""
        trade.exit_price = exit_price
        trade.exit_value = trade.tokens * exit_price
        trade.pnl = trade.exit_value - trade.cost
        result.trades.append(trade)
        cash += trade.exit_value

    return result, equity_curve


def run_grid_search(
    markets: list[Market],
    pm_prices: dict,
    dvol_btc: dict,
    dvol_eth: dict,
    spot_btc: dict,
    spot_eth: dict,
    fee: float = 0.0,
) -> list[tuple[BacktestParams, BacktestResult]]:
    """Run grid search over parameter combinations."""
    min_edges = [0.03, 0.05, 0.08, 0.10]
    exit_edges = [-0.02, 0.0, 0.02]
    drifts = [0.0, 0.15, 0.27]

    combos = list(product(min_edges, exit_edges, drifts))
    results = []

    for min_e, exit_e, drift in tqdm(combos, desc="Grid search"):
        params = BacktestParams(min_edge=min_e, exit_edge=exit_e, drift=drift, fee=fee)

        # Split by currency and run
        btc_markets = [m for m in markets if m.currency == "BTC"]
        eth_markets = [m for m in markets if m.currency == "ETH"]

        r_btc = run_backtest(btc_markets, pm_prices, dvol_btc, spot_btc, params, show_progress=False)
        r_eth = run_backtest(eth_markets, pm_prices, dvol_eth, spot_eth, params, show_progress=False)

        # Merge results
        merged = BacktestResult(params=params)
        merged.trades = r_btc.trades + r_eth.trades
        merged.markets_analyzed = r_btc.markets_analyzed + r_eth.markets_analyzed
        merged.markets_with_data = r_btc.markets_with_data + r_eth.markets_with_data
        merged.period_start = r_btc.period_start or r_eth.period_start
        merged.period_end = r_btc.period_end or r_eth.period_end

        results.append((params, merged))

    return results


def print_results(result: BacktestResult):
    """Print backtest results as formatted table."""
    p = result.params
    print()
    print("=" * 80)
    print(f"  BACKTEST: Deribit IV vs Polymarket")
    print("=" * 80)
    print(f"  Period: {result.period_start} → {result.period_end}")
    print(f"  Markets analyzed: {result.markets_analyzed} | with data: {result.markets_with_data}")
    print(f"  Parameters: min_edge={p.min_edge:.0%}, exit_edge={p.exit_edge:.0%}, "
          f"drift={p.drift:+.0%}, trade=${ p.trade_size:.0f}, fee={p.fee:.0%}")
    print()

    if not result.trades:
        print("  No trades generated.")
        return

    # Summary
    closed = [t for t in result.trades if t.pnl is not None]
    resolved = [t for t in closed if t.exit_reason == "resolution"]
    edge_exits = [t for t in closed if t.exit_reason == "edge_exit"]

    print(f"  SUMMARY")
    print(f"  {'─' * 40}")
    print(f"  Total trades:     {result.n_trades}")
    print(f"  Winners:          {result.n_winners} ({result.win_rate:.0%})")
    print(f"  Losers:           {result.n_losers}")
    print(f"  Total P&L:       ${result.total_pnl:+.2f}")
    print(f"  Avg P&L/trade:   ${result.avg_pnl:+.2f}")
    print(f"  Resolved:         {len(resolved)} trades")
    print(f"  Edge exits:       {len(edge_exits)} trades")

    # By market type
    print(f"\n  BY TYPE")
    print(f"  {'─' * 70}")
    print(f"  {'Type':<25} {'Trades':>7} {'Win%':>6} {'Avg P&L':>9} {'Total P&L':>10}")

    types = {}
    for t in closed:
        # Extract type from market name
        parts = t.market_name.split()
        if len(parts) >= 2:
            key = f"{parts[0]} {parts[1]}"  # e.g. "BTC reach" or "ETH dip"
        else:
            key = t.market_name
        if key not in types:
            types[key] = []
        types[key].append(t)

    for type_name, trades in sorted(types.items()):
        n = len(trades)
        wins = sum(1 for t in trades if t.pnl and t.pnl > 0)
        total = sum(t.pnl for t in trades if t.pnl is not None)
        avg = total / n if n else 0
        wr = wins / n if n else 0
        print(f"  {type_name:<25} {n:>7} {wr:>5.0%} ${avg:>+8.2f} ${total:>+9.2f}")

    # Individual markets
    print(f"\n  INDIVIDUAL TRADES")
    print(f"  {'─' * 90}")
    print(f"  {'Market':<30} {'Side':>4} {'Entry':>6} {'Exit':>6} {'P&L':>8} {'Reason':<12} {'Dates'}")
    print(f"  {'─' * 90}")

    for t in sorted(closed, key=lambda x: x.entry_date):
        pnl_str = f"${t.pnl:+.2f}" if t.pnl is not None else "?"
        print(f"  {t.market_name:<30} {t.side:>4} {t.entry_price:>5.2f} {t.exit_price:>5.2f} "
              f"{pnl_str:>8} {t.exit_reason:<12} {t.entry_date}→{t.exit_date}")


def print_grid_results(grid_results: list[tuple[BacktestParams, BacktestResult]]):
    """Print grid search results as a sorted table."""
    print()
    print("=" * 80)
    print("  GRID SEARCH RESULTS")
    print("=" * 80)
    print(f"  {'min_edge':>9} {'exit_edge':>10} {'drift':>7} {'Trades':>7} "
          f"{'Win%':>6} {'Total P&L':>10} {'Avg P&L':>9}")
    print(f"  {'─' * 70}")

    # Sort by total P&L descending
    sorted_results = sorted(grid_results, key=lambda x: x[1].total_pnl, reverse=True)

    best_pnl = sorted_results[0][1].total_pnl if sorted_results else 0

    for params, result in sorted_results:
        marker = " <<<" if result.total_pnl >= best_pnl * 0.95 and result.total_pnl > 0 else ""
        print(f"  {params.min_edge:>8.0%} {params.exit_edge:>+9.0%} {params.drift:>+6.0%} "
              f"{result.n_trades:>7} {result.win_rate:>5.0%} "
              f"${result.total_pnl:>+9.2f} ${result.avg_pnl:>+8.2f}{marker}")


def save_chart(result: BacktestResult, output_path: str = "crypto/backtest/results.png"):
    """Save cumulative P&L chart."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
    except ImportError:
        print("  matplotlib not available, skipping chart")
        return

    closed = sorted(
        [t for t in result.trades if t.pnl is not None and t.exit_date],
        key=lambda t: t.exit_date,
    )
    if not closed:
        return

    dates = [datetime.strptime(t.exit_date, "%Y-%m-%d") for t in closed]
    cum_pnl = []
    running = 0.0
    for t in closed:
        running += t.pnl
        cum_pnl.append(running)

    fig, ax = plt.subplots(figsize=(12, 6))
    ax.plot(dates, cum_pnl, linewidth=2, color="#2196F3")
    ax.fill_between(dates, 0, cum_pnl, alpha=0.15, color="#2196F3")
    ax.axhline(y=0, color="gray", linestyle="-", linewidth=0.8)

    ax.set_title(
        f"Backtest: Deribit IV vs Polymarket\n"
        f"edge>{result.params.min_edge:.0%}, drift={result.params.drift:+.0%} | "
        f"{result.n_trades} trades, {result.win_rate:.0%} win rate",
        fontsize=12,
    )
    ax.set_ylabel("Cumulative P&L ($)")
    ax.set_xlabel("Date")
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    fig.autofmt_xdate()

    # Annotate final value
    ax.annotate(
        f"${cum_pnl[-1]:+.2f}",
        xy=(dates[-1], cum_pnl[-1]),
        fontsize=11,
        fontweight="bold",
        xytext=(10, 5),
        textcoords="offset points",
        color="#4CAF50" if cum_pnl[-1] >= 0 else "#F44336",
    )

    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    print(f"\n  Chart saved to {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Backtest: Deribit IV vs Polymarket")
    parser.add_argument("--min-edge", type=float, default=0.05, help="Minimum edge to enter (default: 0.05)")
    parser.add_argument("--exit-edge", type=float, default=0.0, help="Edge threshold to exit (default: 0.0)")
    parser.add_argument("--drift", type=float, default=0.0, help="Annual drift (default: 0.0)")
    parser.add_argument("--trade-size", type=float, default=100.0, help="Fixed trade size $ (default: 100)")
    parser.add_argument("--fee", type=float, default=0.0, help="Taker fee on buys (default: 0.0)")
    parser.add_argument("--currency", choices=["BTC", "ETH"], help="Filter by currency")
    parser.add_argument("--no-cache", action="store_true", help="Force fresh data download")
    parser.add_argument("--grid-search", action="store_true", help="Run parameter grid search")
    parser.add_argument("--no-chart", action="store_true", help="Skip chart generation")
    parser.add_argument("--allin", action="store_true", help="All-in mode: full bankroll per trade")
    parser.add_argument("--portfolio", action="store_true", help="Portfolio mode: up to 5 positions, Kelly sizing, reinvestment")
    parser.add_argument("--daily", action="store_true", help="Daily rebalance mode: 10am review, with and without rotation")
    parser.add_argument("--bankroll", type=float, default=1000.0, help="Starting bankroll (default: 1000)")
    args = parser.parse_args()

    # Load data
    markets, pm_prices, dvol_btc, dvol_eth, spot_btc, spot_eth = load_all_data(
        use_cache=not args.no_cache,
        closed_only=True,
        currency_filter=args.currency,
    )

    if args.allin:
        params = BacktestParams(
            min_edge=args.min_edge,
            exit_edge=args.exit_edge,
            drift=args.drift,
            fee=args.fee,
        )

        # All-in needs to pick across BTC and ETH, so we run per-currency
        # and merge chronologically (can only be in one position at a time)
        # For simplicity: run combined with unified dvol/spot lookup
        btc_markets = [m for m in markets if m.currency == "BTC"]
        eth_markets = [m for m in markets if m.currency == "ETH"]

        r_btc = run_backtest_allin(btc_markets, pm_prices, dvol_btc, spot_btc, params, args.bankroll)
        r_eth = run_backtest_allin(eth_markets, pm_prices, dvol_eth, spot_eth, params, args.bankroll)

        print("\n  ═══ BTC ALL-IN ═══")
        print_allin_results(r_btc, args.bankroll)

        print("\n  ═══ ETH ALL-IN ═══")
        print_allin_results(r_eth, args.bankroll)

    elif args.daily:
        params = BacktestParams(
            min_edge=args.min_edge,
            exit_edge=args.exit_edge,
            drift=args.drift,
            fee=args.fee,
        )
        dvol_map = {"BTC": dvol_btc, "ETH": dvol_eth}
        spot_map = {"BTC": spot_btc, "ETH": spot_eth}

        # With rotation
        r_rot, eq_rot = run_backtest_daily_rebalance(
            markets, pm_prices, params, args.bankroll, max_positions=5,
            dvol_by_currency=dvol_map, spot_by_currency=spot_map,
        )
        print("\n  ═══ DAILY REBALANCE (WITH ROTATION) ═══")
        print_portfolio_results(r_rot, eq_rot, args.bankroll)
        if not args.no_chart:
            save_portfolio_chart(eq_rot, r_rot, "crypto/backtest/daily_rotation.png", args.bankroll)

        # Without rotation — use portfolio mode (no rotation logic)
        r_no_rot, eq_no_rot = run_backtest_portfolio(
            markets, pm_prices, params=params, bankroll=args.bankroll, max_positions=5,
            dvol_by_currency=dvol_map, spot_by_currency=spot_map,
        )
        print("\n  ═══ DAILY REBALANCE (NO ROTATION) ═══")
        print_portfolio_results(r_no_rot, eq_no_rot, args.bankroll)
        if not args.no_chart:
            save_portfolio_chart(eq_no_rot, r_no_rot, "crypto/backtest/daily_no_rotation.png", args.bankroll)

    elif args.portfolio:
        params = BacktestParams(
            min_edge=args.min_edge,
            exit_edge=args.exit_edge,
            drift=args.drift,
            fee=args.fee,
        )

        btc_markets = [m for m in markets if m.currency == "BTC"]
        eth_markets = [m for m in markets if m.currency == "ETH"]

        # Combined BTC+ETH portfolio
        dvol_map = {"BTC": dvol_btc, "ETH": dvol_eth}
        spot_map = {"BTC": spot_btc, "ETH": spot_eth}

        r_combined, eq_combined = run_backtest_portfolio(
            markets, pm_prices, params=params, bankroll=args.bankroll,
            dvol_by_currency=dvol_map, spot_by_currency=spot_map,
        )
        print("\n  ═══ COMBINED BTC+ETH PORTFOLIO ═══")
        print_portfolio_results(r_combined, eq_combined, args.bankroll)
        if not args.no_chart:
            save_portfolio_chart(eq_combined, r_combined, "crypto/backtest/portfolio_combined.png", args.bankroll)

        # Also show per-currency
        btc_markets = [m for m in markets if m.currency == "BTC"]
        eth_markets = [m for m in markets if m.currency == "ETH"]

        r_btc, eq_btc = run_backtest_portfolio(btc_markets, pm_prices, dvol_btc, spot_btc, params, args.bankroll)
        r_eth, eq_eth = run_backtest_portfolio(eth_markets, pm_prices, dvol_eth, spot_eth, params, args.bankroll)

        print("\n  ═══ BTC ONLY PORTFOLIO ═══")
        print_portfolio_results(r_btc, eq_btc, args.bankroll)

        print("\n  ═══ ETH ONLY PORTFOLIO ═══")
        print_portfolio_results(r_eth, eq_eth, args.bankroll)

    elif args.grid_search:
        grid_results = run_grid_search(
            markets, pm_prices, dvol_btc, dvol_eth, spot_btc, spot_eth, fee=args.fee,
        )
        print_grid_results(grid_results)
        # Also run and display the best result
        if grid_results:
            best_params, best_result = max(grid_results, key=lambda x: x[1].total_pnl)
            print_results(best_result)
            if not args.no_chart:
                save_chart(best_result)
    else:
        params = BacktestParams(
            min_edge=args.min_edge,
            exit_edge=args.exit_edge,
            drift=args.drift,
            trade_size=args.trade_size,
            fee=args.fee,
        )

        # Split by currency
        btc_markets = [m for m in markets if m.currency == "BTC"]
        eth_markets = [m for m in markets if m.currency == "ETH"]

        r_btc = run_backtest(btc_markets, pm_prices, dvol_btc, spot_btc, params)
        r_eth = run_backtest(eth_markets, pm_prices, dvol_eth, spot_eth, params)

        # Merge
        merged = BacktestResult(params=params)
        merged.trades = r_btc.trades + r_eth.trades
        merged.markets_analyzed = r_btc.markets_analyzed + r_eth.markets_analyzed
        merged.markets_with_data = r_btc.markets_with_data + r_eth.markets_with_data
        merged.period_start = min(filter(None, [r_btc.period_start, r_eth.period_start]), default="")
        merged.period_end = max(filter(None, [r_btc.period_end, r_eth.period_end]), default="")

        print_results(merged)

        if not args.no_chart:
            save_chart(merged)


if __name__ == "__main__":
    main()
