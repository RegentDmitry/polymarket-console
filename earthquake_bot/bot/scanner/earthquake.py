"""
Earthquake scanner - uses the same logic as main_tested.py
"""

import sys
from pathlib import Path
from typing import List, Optional
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from .base import BaseScanner
from ..models.market import Market
from ..models.signal import Signal, SignalType
from ..models.position import Position
from ..config import BotConfig

# Import from main_tested.py
try:
    from main_tested import (
        run_analysis, TestedOpportunity, TestedModel,
        MIN_EDGE, MIN_ANNUAL_RETURN,
    )
    from polymarket_client import PolymarketClient
    from usgs_client import USGSClient
    IMPORTS_OK = True
except ImportError as e:
    print(f"Warning: Failed to import main_tested: {e}")
    IMPORTS_OK = False
    TestedModel = None
    PolymarketClient = None


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

    def scan_for_entries(self) -> List[Signal]:
        """Scan markets for entry opportunities using main_tested logic."""
        signals = []

        if not IMPORTS_OK or not self.api_client:
            return signals

        try:
            # Run the same analysis as main_tested.py
            self._opportunities = run_analysis(self.api_client, self.usgs_client)

            # Convert opportunities to signals
            for opp in self._opportunities:
                # Create market for cache
                market = Market(
                    id=opp.condition_id or opp.event,
                    slug=opp.event,
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
                meets_roi = opp.expected_return >= self.config.min_roi

                if meets_edge and meets_roi:
                    signal = Signal(
                        type=SignalType.BUY,
                        market_id=opp.condition_id or opp.event,
                        market_slug=opp.event,
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
                    )
                else:
                    signal = Signal(
                        type=SignalType.SKIP,
                        market_id=opp.condition_id or opp.event,
                        market_slug=opp.event,
                        market_name=f"{opp.outcome} ({opp.side})",
                        outcome=opp.side,
                        current_price=opp.market_price,
                        fair_price=opp.fair_price,
                        edge=opp.edge,
                        roi=opp.expected_return,
                        days_remaining=opp.remaining_days,
                        model_used=getattr(opp, 'model_used', 'unknown'),
                        annual_return=opp.annual_return,
                    )

                signals.append(signal)

        except Exception as e:
            print(f"Error during scan: {e}")
            import traceback
            traceback.print_exc()

        return signals

    def scan_for_exits(self, positions: List[Position],
                       current_prices: dict[str, float]) -> List[Signal]:
        """Scan open positions for exit opportunities."""
        # For now, basic exit logic
        # TODO: implement proper exit signals based on model
        return []

    def get_current_prices(self) -> dict[str, float]:
        """Get current prices for all tracked markets."""
        prices = {}
        for opp in self._opportunities:
            prices[opp.event] = opp.market_price
        return prices
