"""
Crypto scanner — scans BTC/ETH markets on Polymarket for trading opportunities.

Uses batch Monte Carlo with Student-t innovations for touch probability,
live Deribit data for IV and drift, and Binance for spot prices.
"""

import math
from typing import Callable, Dict, List, Optional, Tuple

from .base import BaseScanner
from ..config import BotConfig
from ..models.market import Market
from ..models.signal import Signal, SignalType
from ..models.position import Position
from ..market_data.binance import BinanceData
from ..market_data.deribit import DeribitData
from ..market_data.polymarket import PolymarketData, CryptoMarket
from ..pricing.touch_prob import (
    batch_touch_probabilities, get_df, MC_PATHS
)
from ..pricing.fast_approx import batch_fast_touch_probabilities
from ..logger import get_logger


class CryptoScanner(BaseScanner):
    """
    Scanner for BTC/ETH prediction markets on Polymarket.

    Uses Student-t MC model for fair price estimation.
    """

    def __init__(self, config: BotConfig):
        super().__init__(config)

        # Market data sources (shared, updated by background threads)
        self.binance = BinanceData()
        self.deribit = DeribitData()
        self.polymarket = PolymarketData(config.markets_json)

        # Caches from last scan
        self._markets_cache: List[Market] = []
        self._fair_prices: Dict[str, float] = {}      # slug -> fair_price
        self._token_ids: Dict[str, str] = {}           # slug -> token_id
        self._bid_prices: Dict[str, float] = {}        # slug -> bid_price
        self._crypto_markets: List[CryptoMarket] = []  # raw markets from scan

    @property
    def name(self) -> str:
        return "CryptoMC"

    def get_balance(self) -> float:
        """Get current USDC balance (delegated to executor)."""
        return 0.0  # Executor handles this

    def get_markets(self) -> List[Market]:
        return self._markets_cache

    def calculate_fair_price(self, market: Market) -> float:
        return self._fair_prices.get(market.slug, 0.0)

    def should_buy(self, market: Market, fair_price: float) -> bool:
        return False  # scan_for_entries handles everything

    def should_sell(self, position: Position, current_price: float) -> tuple[bool, str]:
        return False, ""

    def scan_for_entries(
        self,
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> List[Signal]:
        """Scan markets for entry opportunities using batch MC."""
        signals = []
        logger = get_logger()

        try:
            logger.log_scan_start()
            self._markets_cache = []
            self._fair_prices = {}
            self._token_ids = {}

            # Get latest data snapshots
            deribit_snap = self.deribit.get_snapshot()
            binance_snap = self.binance.get_snapshot()

            # Prefer Deribit spot (perpetual), fallback to Binance
            btc_spot = deribit_snap["btc_spot"] or binance_snap.get("btc_price", 0)
            eth_spot = deribit_snap["eth_spot"] or binance_snap.get("eth_price", 0)

            btc_iv = deribit_snap["btc_iv"]
            eth_iv = deribit_snap["eth_iv"]

            if btc_spot <= 0 or eth_spot <= 0:
                logger.log_warning("No spot prices available")
                return signals
            if btc_iv <= 0 or eth_iv <= 0:
                logger.log_warning(f"No IV available (BTC={btc_iv}, ETH={eth_iv})")
                return signals

            # Load markets
            crypto_markets = self.polymarket.markets
            if not crypto_markets:
                logger.log_warning("No crypto markets found")
                return signals

            self._crypto_markets = crypto_markets

            if progress_callback:
                progress_callback(f"Scanning {len(crypto_markets)} markets...")

            logger.log_info(
                f"BTC: ${btc_spot:,.0f} IV={btc_iv:.1%} | "
                f"ETH: ${eth_spot:,.0f} IV={eth_iv:.1%}"
            )

            # Filter out expired and priceless markets
            active_markets = [
                m for m in crypto_markets
                if m.days_remaining > 0 and m.yes_price > 0
            ]

            if progress_callback:
                progress_callback(
                    f"Active: {len(active_markets)}/{len(crypto_markets)} markets"
                )

            # Group markets by (currency, days) for batch MC
            groups: Dict[Tuple[str, int], List[CryptoMarket]] = {}
            for m in active_markets:
                days = max(int(m.days_remaining), 1)
                key = (m.currency, days)
                groups.setdefault(key, []).append(m)

            # Run batch MC for each group
            for (currency, days), markets_in_group in groups.items():
                spot = btc_spot if currency == "BTC" else eth_spot
                iv = btc_iv if currency == "BTC" else eth_iv
                df = get_df(currency)
                drift = self.deribit.drift_for_days(currency, days)

                # Separate above and below strikes
                strikes_above = [m.strike for m in markets_in_group if m.is_up]
                strikes_below = [m.strike for m in markets_in_group if not m.is_up]

                if progress_callback:
                    mode = "Fast" if self.config.fast_pricing else "MC"
                    progress_callback(
                        f"{mode} {currency} {days}d: {len(strikes_above)}↑ {len(strikes_below)}↓"
                    )

                if self.config.fast_pricing:
                    above_probs, below_probs = batch_fast_touch_probabilities(
                        spot=spot,
                        iv=iv,
                        days=days,
                        strikes_above=strikes_above,
                        strikes_below=strikes_below,
                        drift=drift,
                        df=df,
                    )
                else:
                    above_probs, below_probs = batch_touch_probabilities(
                        spot=spot,
                        iv=iv,
                        days=days,
                        strikes_above=strikes_above,
                        strikes_below=strikes_below,
                        drift=drift,
                        df=df,
                        n_paths=self.config.mc_paths,
                    )

                # Generate signals for each market
                for m in markets_in_group:
                    if m.is_up:
                        touch_prob = above_probs.get(m.strike, 0)
                        fair_price = touch_prob  # YES = touches
                    else:
                        touch_prob = below_probs.get(m.strike, 0)
                        fair_price = 1 - touch_prob  # YES = doesn't touch

                    # Cache
                    self._fair_prices[m.slug] = fair_price
                    if m.is_up:
                        self._token_ids[m.slug] = m.yes_token_id
                    else:
                        self._token_ids[m.slug] = m.no_token_id

                    # Determine which side to trade
                    if m.is_up:
                        # Touch-above: YES price = pm_price, fair = touch_prob
                        side = "YES"
                        market_price = m.yes_price
                        edge = fair_price - market_price
                        token_id = m.yes_token_id
                    else:
                        # Touch-below: buy NO (= doesn't touch)
                        side = "NO"
                        market_price = 1 - m.yes_price  # NO price
                        edge = fair_price - market_price
                        token_id = m.no_token_id

                    # Calculate APY
                    T = days / 365 if days > 0 else 1 / 365
                    if market_price > 0 and market_price < 1:
                        roi = (fair_price - market_price) / market_price
                        annual_return = roi / T if T > 0 else 0
                    else:
                        roi = 0
                        annual_return = 0

                    # YES positions need 2x edge (time decay works against YES)
                    effective_min_edge = self.config.min_edge * 2 if side == "YES" else self.config.min_edge
                    meets_edge = edge >= effective_min_edge
                    meets_apy = annual_return >= self.config.min_apy

                    # Create Market for cache
                    market_obj = Market(
                        id=m.condition_id,
                        slug=m.slug,
                        name=m.question,
                        yes_token_id=m.yes_token_id,
                        no_token_id=m.no_token_id,
                        yes_price=m.yes_price,
                        no_price=m.no_price,
                        end_date=m.end_date,
                        is_active=True,
                        category="crypto",
                    )
                    self._markets_cache.append(market_obj)

                    # Build signal
                    if meets_edge and meets_apy:
                        signal_type = SignalType.BUY
                    else:
                        signal_type = SignalType.SKIP

                    signal = Signal(
                        type=signal_type,
                        market_id=m.condition_id,
                        market_slug=m.slug,
                        market_name=f"{m.question[:50]} ({side})",
                        outcome=side,
                        current_price=market_price,
                        fair_price=fair_price,
                        edge=edge,
                        roi=roi,
                        days_remaining=days,
                        token_id=token_id,
                        model_used=f"{'Fast' if self.config.fast_pricing else 'MC'}-t(df={df:.2f})",
                        annual_return=annual_return,
                    )
                    signals.append(signal)
                    logger.log_signal(signal)

            # Summary
            buy_count = len([s for s in signals if s.type == SignalType.BUY])
            skip_count = len([s for s in signals if s.type == SignalType.SKIP])
            logger.log_info(f"Scan found {buy_count} BUY, {skip_count} SKIP signals")

        except Exception as e:
            logger.log_error(f"Error during scan: {e}")
            import traceback
            traceback.print_exc()

        return signals

    def scan_for_exits(
        self,
        positions: List[Position],
        current_prices: Dict[str, float],
    ) -> List[Signal]:
        """Scan open positions for exit opportunities.

        Logic: SELL when bid_price >= fair_price
        """
        signals = []
        logger = get_logger()

        if not positions:
            return signals

        logger.log_info(f"Checking exits for {len(positions)} positions...")

        for position in positions:
            fair_price = self._fair_prices.get(position.market_slug)
            if fair_price is None:
                continue

            token_id = self._token_ids.get(position.market_slug)
            if not token_id:
                continue

            # Get bid price from current_prices (set by scan_for_entries)
            bid_price = current_prices.get(position.market_slug, 0)

            self._bid_prices[position.market_slug] = bid_price

            if bid_price <= 0:
                continue

            logger.log_info(
                f"EXIT CHECK: {position.market_slug[:40]} - "
                f"bid {bid_price:.1%} vs fair {fair_price:.1%}"
            )

            if bid_price >= fair_price:
                current_value = position.current_value(bid_price)
                signal = Signal(
                    type=SignalType.SELL,
                    market_id=position.market_id,
                    market_slug=position.market_slug,
                    market_name=position.market_name,
                    outcome=position.outcome,
                    current_price=bid_price,
                    fair_price=fair_price,
                    target_price=bid_price,
                    position_id=position.id,
                    reason=f"Bid {bid_price:.1%} >= Fair {fair_price:.1%}",
                    suggested_size=current_value,
                    token_id=token_id,
                )
                signals.append(signal)

                pnl = position.unrealized_pnl(bid_price)
                logger.log_info(
                    f"EXIT SIGNAL: {position.market_slug} - "
                    f"bid {bid_price:.1%} >= fair {fair_price:.1%}, P&L: ${pnl:+.2f}"
                )

        return signals

    def scan_for_rotations(
        self,
        positions: List[Position],
        buy_signals: List[Signal],
        balance: float,
    ) -> List[dict]:
        """Identify rotation opportunities: sell position A to fund better position B.

        Returns list of rotation proposals:
        {
            "sell_position": Position,
            "sell_bid": float,
            "sell_proceeds": float,
            "sell_loss": float,
            "buy_signal": Signal,
            "buy_edge": float,
            "net_improvement": float,  # positive = rotation is profitable
        }
        """
        logger = get_logger()

        # Only consider BUY signals we can't afford
        affordable_threshold = balance
        unaffordable_buys = [
            s for s in buy_signals
            if s.type == SignalType.BUY and s.liquidity > affordable_threshold
        ]

        if not unaffordable_buys or not positions:
            return []

        rotations = []

        for buy_signal in unaffordable_buys:
            for position in positions:
                # Don't sell the same market we want to buy
                if position.market_slug == buy_signal.market_slug:
                    continue

                # Get current bid for this position
                bid_price = self._bid_prices.get(position.market_slug, 0)
                if bid_price <= 0:
                    continue

                # Calculate sell proceeds and loss
                sell_proceeds = position.tokens * bid_price
                sell_loss = position.unrealized_pnl(bid_price)

                # Calculate expected gain from new position
                buy_edge = buy_signal.edge
                buy_expected_gain = sell_proceeds * buy_edge

                # Net improvement: expected gain minus loss from selling
                net_improvement = buy_expected_gain + sell_loss  # sell_loss is negative

                if net_improvement > 0:
                    rotations.append({
                        "sell_position": position,
                        "sell_bid": bid_price,
                        "sell_proceeds": sell_proceeds,
                        "sell_loss": sell_loss,
                        "buy_signal": buy_signal,
                        "buy_edge": buy_edge,
                        "net_improvement": net_improvement,
                    })

        # Sort by net improvement (best first)
        rotations.sort(key=lambda r: r["net_improvement"], reverse=True)

        if rotations:
            logger.log_info(f"Found {len(rotations)} rotation opportunities")
            for r in rotations[:3]:
                logger.log_info(
                    f"  ROTATE: sell {r['sell_position'].market_slug[:25]} "
                    f"(loss ${r['sell_loss']:+.2f}) → "
                    f"buy {r['buy_signal'].market_slug[:25]} "
                    f"(edge {r['buy_edge']:.1%}, net +${r['net_improvement']:.2f})"
                )

        return rotations

    def get_current_prices(self) -> Dict[str, float]:
        """Get current prices for all tracked markets."""
        prices = {}
        for m in self._crypto_markets:
            if m.is_up:
                prices[m.slug] = m.yes_price
            else:
                prices[m.slug] = 1 - m.yes_price
        return prices
