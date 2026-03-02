"""
Base scanner class - abstract interface for market scanners.
"""

from abc import ABC, abstractmethod
from typing import List, Optional, Callable

from ..models.market import Market
from ..models.signal import Signal, SignalType
from ..models.position import Position
from ..config import BotConfig


class BaseScanner(ABC):
    """
    Abstract base class for market scanners.

    A scanner:
    1. Fetches markets from Polymarket API
    2. Calculates fair prices using a model
    3. Generates BUY signals for underpriced markets
    4. Generates SELL signals for positions that should exit
    """

    def __init__(self, config: BotConfig):
        self.config = config

    @property
    @abstractmethod
    def name(self) -> str:
        """Scanner/strategy name for display."""
        pass

    @abstractmethod
    def get_markets(self) -> List[Market]:
        """
        Fetch markets to scan from API.

        Returns:
            List of Market objects
        """
        pass

    @abstractmethod
    def calculate_fair_price(self, market: Market) -> float:
        """
        Calculate fair price for a market using the model.

        Args:
            market: Market to evaluate

        Returns:
            Fair price (0-1)
        """
        pass

    @abstractmethod
    def should_buy(self, market: Market, fair_price: float) -> bool:
        """
        Check if market meets entry criteria.

        Args:
            market: Market to evaluate
            fair_price: Calculated fair price

        Returns:
            True if should buy
        """
        pass

    @abstractmethod
    def should_sell(self, position: Position, current_price: float) -> tuple[bool, str]:
        """
        Check if position should be closed.

        Args:
            position: Open position
            current_price: Current market price

        Returns:
            Tuple of (should_sell, reason)
        """
        pass

    def scan_for_entries(self,
                         progress_callback: Optional[Callable[[str], None]] = None) -> List[Signal]:
        """
        Scan markets for entry opportunities.

        Args:
            progress_callback: Optional callback for progress updates

        Returns:
            List of BUY or SKIP signals
        """
        signals = []
        markets = self.get_markets()

        for market in markets:
            fair_price = self.calculate_fair_price(market)
            current_price = market.yes_price

            edge = fair_price - current_price
            roi = (fair_price - current_price) / current_price if current_price > 0 else 0

            if self.should_buy(market, fair_price):
                signal = Signal(
                    type=SignalType.BUY,
                    market_id=market.id,
                    market_slug=market.slug,
                    market_name=market.name,
                    outcome="YES",
                    current_price=current_price,
                    fair_price=fair_price,
                    edge=edge,
                    roi=roi,
                    days_remaining=market.days_remaining,
                )
            else:
                signal = Signal(
                    type=SignalType.SKIP,
                    market_id=market.id,
                    market_slug=market.slug,
                    market_name=market.name,
                    current_price=current_price,
                    fair_price=fair_price,
                    edge=edge,
                    roi=roi,
                    days_remaining=market.days_remaining,
                )

            signals.append(signal)

        return signals

    def scan_for_exits(self, positions: List[Position],
                       current_prices: dict[str, float]) -> List[Signal]:
        """
        Scan open positions for exit opportunities.

        Args:
            positions: List of open positions
            current_prices: Dict mapping market_slug to current price

        Returns:
            List of SELL signals
        """
        signals = []

        for position in positions:
            current_price = current_prices.get(position.market_slug, position.entry_price)
            should_sell, reason = self.should_sell(position, current_price)

            if should_sell:
                signal = Signal(
                    type=SignalType.SELL,
                    market_id=position.market_id,
                    market_slug=position.market_slug,
                    market_name=position.market_name,
                    outcome=position.outcome,
                    current_price=current_price,
                    fair_price=position.fair_price_at_entry,
                    target_price=current_price,
                    position_id=position.id,
                    reason=reason,
                    suggested_size=position.current_value(current_price),
                )
                signals.append(signal)

        return signals

    def scan(self, open_positions: List[Position],
             progress_callback: Optional[Callable[[str], None]] = None) -> tuple[List[Signal], List[Signal]]:
        """
        Full scan - entries and exits.

        Args:
            open_positions: Current open positions
            progress_callback: Optional callback for progress updates

        Returns:
            Tuple of (entry_signals, exit_signals)
        """
        entry_signals = self.scan_for_entries(progress_callback)

        # Get current prices for exit check
        current_prices = {}
        for signal in entry_signals:
            current_prices[signal.market_slug] = signal.current_price

        if progress_callback:
            progress_callback("Checking exits...")

        exit_signals = self.scan_for_exits(open_positions, current_prices)

        return entry_signals, exit_signals
