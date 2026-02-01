"""
Earthquake scanner - uses the same logic as main_tested.py
"""

import sys
from pathlib import Path
from typing import List, Optional, Callable
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from .base import BaseScanner
from ..models.market import Market
from ..models.signal import Signal, SignalType
from ..models.position import Position
from ..config import BotConfig
from ..logger import get_logger

# Import from main_tested.py
try:
    from main_tested import (
        run_analysis, TestedOpportunity, TestedModel,
        MIN_EDGE, MIN_ANNUAL_RETURN,
    )
    from main import get_spread_info
    from polymarket_client import PolymarketClient
    from usgs_client import USGSClient
    IMPORTS_OK = True
except ImportError as e:
    print(f"Warning: Failed to import main_tested: {e}")
    IMPORTS_OK = False
    TestedModel = None
    PolymarketClient = None
    get_spread_info = None


class EarthquakeScanner(BaseScanner):
    """
    Scanner for earthquake markets using the same logic as main_tested.py
    """

    def __init__(self, config: BotConfig):
        super().__init__(config)

        self.api_client = None
        self.usgs_client = None

        if IMPORTS_OK:
            try:
                self.api_client = PolymarketClient()
                self.usgs_client = USGSClient()
                print(f"Polymarket client initialized: {self.api_client.get_address()[:10]}...")
            except Exception as e:
                print(f"Warning: Failed to initialize clients: {e}")

        # Cache for opportunities from last scan
        self._opportunities: List[TestedOpportunity] = []
        self._markets_cache: List[Market] = []
        # Cache for fair prices and token_ids (for exit checks)
        self._fair_prices: dict[str, float] = {}  # market_slug -> fair_price
        self._token_ids: dict[str, str] = {}  # market_slug -> token_id
        self._bid_prices: dict[str, float] = {}  # market_slug -> bid_price

    @property
    def name(self) -> str:
        return "TestedModel" if IMPORTS_OK else "Unavailable"

    def get_balance(self) -> float:
        """Get current USDC balance."""
        if not self.api_client:
            return 0.0

        try:
            balance_info = self.api_client.get_balance()
            balance_raw = float(balance_info.get("balance", 0))
            return balance_raw / 1e6
        except Exception as e:
            print(f"Error getting balance: {e}")
            return 0.0

    def get_markets(self) -> List[Market]:
        """Return cached markets."""
        return self._markets_cache

    def calculate_fair_price(self, market: Market) -> float:
        """Not used - scan_for_entries handles everything."""
        return 0.0

    def should_buy(self, market: Market, fair_price: float) -> bool:
        """Not used - scan_for_entries handles everything."""
        return False

    def should_sell(self, position: Position, current_price: float) -> tuple[bool, str]:
        """Basic exit logic."""
        # TODO: implement proper exit signals
        return False, ""

    def scan_for_entries(self,
                         progress_callback: Optional[Callable[[str], None]] = None) -> List[Signal]:
        """Scan markets for entry opportunities using main_tested logic."""
        signals = []
        logger = get_logger()

        if not IMPORTS_OK or not self.api_client:
            logger.log_warning("Scanner not available - imports failed or no API client")
            return signals

        try:
            logger.log_scan_start()
            # Clear caches before each scan
            self._markets_cache = []
            self._fair_prices = {}
            self._token_ids = {}
            self._condition_id_to_slug = {}  # condition_id -> unique_slug mapping

            # Run the same analysis as main_tested.py
            self._opportunities = run_analysis(
                self.api_client, self.usgs_client,
                progress_callback=progress_callback,
                min_edge=self.config.min_edge,
                min_apy=self.config.min_apy
            )

            # Convert opportunities to signals
            if progress_callback:
                progress_callback(f"Processing {len(self._opportunities)} results...")

            for opp in self._opportunities:
                # Use unique slug: event + outcome + side to avoid price collisions
                unique_slug = f"{opp.event}-{opp.outcome}-{opp.side}"

                # Cache fair price and token_id for exit checks
                self._fair_prices[unique_slug] = opp.fair_price
                if opp.token_id:
                    self._token_ids[unique_slug] = opp.token_id
                # Map condition_id+side to slug for synced positions lookup
                if opp.condition_id:
                    self._condition_id_to_slug[f"{opp.condition_id}-{opp.side}"] = unique_slug

                # Also cache the opposite side (1 - fair_price) for positions on the other side
                opposite_side = "NO" if opp.side == "YES" else "YES"
                opposite_slug = f"{opp.event}-{opp.outcome}-{opposite_side}"
                if opposite_slug not in self._fair_prices:
                    self._fair_prices[opposite_slug] = 1 - opp.fair_price
                if opp.condition_id:
                    opp_key = f"{opp.condition_id}-{opposite_side}"
                    if opp_key not in self._condition_id_to_slug:
                        self._condition_id_to_slug[opp_key] = opposite_slug

                # Create market for cache
                market = Market(
                    id=opp.condition_id or unique_slug,
                    slug=unique_slug,
                    name=f"{opp.event} - {opp.outcome}",
                    yes_token_id=opp.token_id if opp.side == "YES" else "",
                    no_token_id=opp.token_id if opp.side == "NO" else "",
                    yes_price=opp.market_price if opp.side == "YES" else 1 - opp.market_price,
                    no_price=1 - opp.market_price if opp.side == "YES" else opp.market_price,
                    end_date=None,
                    is_active=True,
                    category="earthquake",
                )
                self._markets_cache.append(market)

                # Check if meets our criteria
                meets_edge = opp.edge >= self.config.min_edge
                meets_apy = opp.annual_return >= self.config.min_apy

                # Get usable liquidity (filtered by edge/apy) and kelly from opportunity
                liquidity = getattr(opp, 'usable_liquidity', 0.0) or 0.0
                kelly = getattr(opp, 'kelly', 0.0) or 0.0

                # Skip if liquidity below Polymarket minimum order ($1)
                meets_liquidity = liquidity >= 1.0

                if meets_edge and meets_apy and meets_liquidity:
                    signal = Signal(
                        type=SignalType.BUY,
                        market_id=opp.condition_id or unique_slug,
                        market_slug=unique_slug,
                        market_name=f"{opp.outcome} ({opp.side})",
                        outcome=opp.side,
                        current_price=opp.market_price,
                        fair_price=opp.fair_price,
                        edge=opp.edge,
                        roi=opp.expected_return,
                        days_remaining=opp.remaining_days,
                        token_id=opp.token_id,
                        model_used=getattr(opp, 'model_used', 'unknown'),
                        annual_return=opp.annual_return,
                        liquidity=liquidity,
                        kelly=kelly,
                    )
                else:
                    signal = Signal(
                        type=SignalType.SKIP,
                        market_id=opp.condition_id or unique_slug,
                        market_slug=unique_slug,
                        market_name=f"{opp.outcome} ({opp.side})",
                        outcome=opp.side,
                        current_price=opp.market_price,
                        fair_price=opp.fair_price,
                        edge=opp.edge,
                        roi=opp.expected_return,
                        days_remaining=opp.remaining_days,
                        model_used=getattr(opp, 'model_used', 'unknown'),
                        annual_return=opp.annual_return,
                        liquidity=liquidity,
                        kelly=kelly,
                    )

                signals.append(signal)
                logger.log_signal(signal)

            # Log scan summary
            buy_count = len([s for s in signals if s.type == SignalType.BUY])
            skip_count = len([s for s in signals if s.type == SignalType.SKIP])
            logger.log_info(f"Scan found {buy_count} BUY, {skip_count} SKIP signals")

        except Exception as e:
            logger.log_error(f"Error during scan: {e}")
            import traceback
            traceback.print_exc()

        return signals

    def scan_for_exits(self, positions: List[Position],
                       current_prices: dict[str, float]) -> List[Signal]:
        """Scan open positions for exit opportunities.

        Logic: SELL when bid_price >= current_fair_price
        (market is willing to pay more than our model thinks it's worth)
        """
        signals = []
        logger = get_logger()

        if not IMPORTS_OK or not self.api_client or not get_spread_info:
            return signals

        if positions:
            logger.log_info(f"Checking exits for {len(positions)} positions...")

        for position in positions:
            # Get current fair price from cache - try slug first, then condition_id
            lookup_slug = position.market_slug
            fair_price = self._fair_prices.get(lookup_slug)

            if fair_price is None and position.market_id:
                # Try matching by condition_id (for synced positions with different slugs)
                # Normalize outcome case (synced: Yes/No, scanner: YES/NO)
                key = f"{position.market_id}-{position.outcome.upper()}"
                mapped_slug = self._condition_id_to_slug.get(key)
                if mapped_slug:
                    lookup_slug = mapped_slug
                    fair_price = self._fair_prices.get(lookup_slug)

            if fair_price is None:
                # Market not in current scan results - skip
                logger.log_info(
                    f"EXIT SKIP (no fair price): {position.market_slug[:40]} "
                    f"mid={position.market_id[:20] if position.market_id else 'none'}"
                )
                continue

            # Get token_id for this position
            token_id = self._token_ids.get(lookup_slug)
            if not token_id:
                logger.log_info(
                    f"EXIT SKIP (no token_id): {lookup_slug[:40]}"
                )
                continue

            # Get bid price from orderbook
            spread_info = get_spread_info(self.api_client, token_id)
            if not spread_info:
                continue

            bid_price = spread_info.get("best_bid", 0)
            bid_liquidity = spread_info.get("bid_liquidity", 0)

            # Cache bid price for UI display
            self._bid_prices[position.market_slug] = bid_price

            if bid_price <= 0:
                # No bids - can't sell
                continue

            logger.log_info(
                f"EXIT CHECK: {position.market_slug[:40]} - "
                f"bid {bid_price:.1%} vs fair {fair_price:.1%}"
            )

            # Check sell condition: bid >= fair
            if bid_price >= fair_price:
                # Calculate position value at bid price
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
                    liquidity=bid_liquidity,
                    token_id=token_id,
                )
                signals.append(signal)

                # Log sell signal with reasoning
                pnl = position.unrealized_pnl(bid_price)
                logger.log_info(
                    f"EXIT SIGNAL: {position.market_slug} - "
                    f"bid {bid_price:.1%} >= fair {fair_price:.1%}, P&L: ${pnl:+.2f}"
                )

        return signals

    def get_current_prices(self) -> dict[str, float]:
        """Get current prices for all tracked markets."""
        prices = {}
        for opp in self._opportunities:
            unique_slug = f"{opp.event}-{opp.outcome}-{opp.side}"
            prices[unique_slug] = opp.market_price
        return prices
