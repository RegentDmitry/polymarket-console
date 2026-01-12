"""
Polymarket order executor - handles buying and selling on Polymarket.
"""

import sys
from pathlib import Path
from typing import Optional, Tuple
from dataclasses import dataclass
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from ..models.position import Position
from ..models.signal import Signal, SignalType
from ..models.market import Market

# Import Polymarket client
try:
    from polymarket_client import PolymarketClient
except ImportError:
    PolymarketClient = None


@dataclass
class OrderResult:
    """Result of an order execution."""
    success: bool
    order_id: Optional[str] = None
    filled_price: Optional[float] = None
    filled_size: Optional[float] = None
    tokens: Optional[float] = None
    error: Optional[str] = None
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.utcnow().isoformat() + "Z"


class PolymarketExecutor:
    """
    Executes trades on Polymarket.

    Supports both limit and market orders.
    """

    def __init__(self, use_market_orders: bool = False):
        """
        Initialize executor.

        Args:
            use_market_orders: If True, use market orders (immediate fill).
                              If False, use limit orders at current best price.
        """
        self.use_market_orders = use_market_orders

        if PolymarketClient:
            try:
                self.client = PolymarketClient()
                self.initialized = True
            except Exception as e:
                print(f"Failed to initialize Polymarket client: {e}")
                self.client = None
                self.initialized = False
        else:
            self.client = None
            self.initialized = False

    def get_balance(self) -> float:
        """Get current USDC balance."""
        if not self.client:
            return 0.0

        try:
            balance_info = self.client.get_balance()
            balance_raw = float(balance_info.get("balance", 0))
            return balance_raw / 1e6  # USDC has 6 decimals
        except Exception as e:
            print(f"Error getting balance: {e}")
            return 0.0

    def get_address(self) -> str:
        """Get wallet address."""
        if not self.client:
            return ""
        return self.client.get_address()

    def buy(self, signal: Signal, market: Market) -> Tuple[OrderResult, Optional[Position]]:
        """
        Execute a BUY order.

        Args:
            signal: BUY signal with market info and suggested size
            market: Market object with token IDs

        Returns:
            Tuple of (OrderResult, Position if successful)
        """
        if not self.client:
            return OrderResult(success=False, error="Client not initialized"), None

        token_id = market.yes_token_id
        if not token_id:
            return OrderResult(success=False, error="No YES token ID"), None

        try:
            if self.use_market_orders:
                # Market order - immediate fill
                result = self.client.create_market_order(
                    token_id=token_id,
                    side="BUY",
                    amount=signal.suggested_size,
                )
            else:
                # Limit order at current price
                # Calculate tokens: size / price
                tokens = signal.suggested_size / signal.current_price
                result = self.client.create_limit_order(
                    token_id=token_id,
                    side="BUY",
                    price=signal.current_price,
                    size=tokens,
                )

            order_id = result.get("orderID") or result.get("order_id")

            if order_id:
                # Create position
                tokens = signal.suggested_size / signal.current_price
                position = Position(
                    market_id=signal.market_id,
                    market_slug=signal.market_slug,
                    market_name=signal.market_name,
                    outcome="YES",
                    resolution_date=market.end_date,
                    entry_price=signal.current_price,
                    entry_time=datetime.utcnow().isoformat() + "Z",
                    entry_size=signal.suggested_size,
                    tokens=tokens,
                    strategy="tested",
                    fair_price_at_entry=signal.fair_price,
                    edge_at_entry=signal.edge,
                    entry_order_id=order_id,
                )

                return OrderResult(
                    success=True,
                    order_id=order_id,
                    filled_price=signal.current_price,
                    filled_size=signal.suggested_size,
                    tokens=tokens,
                ), position
            else:
                return OrderResult(
                    success=False,
                    error=f"No order ID in response: {result}"
                ), None

        except Exception as e:
            return OrderResult(success=False, error=str(e)), None

    def sell(self, signal: Signal, position: Position, market: Market) -> OrderResult:
        """
        Execute a SELL order.

        Args:
            signal: SELL signal
            position: Position to close
            market: Market object with token IDs

        Returns:
            OrderResult
        """
        if not self.client:
            return OrderResult(success=False, error="Client not initialized")

        token_id = market.yes_token_id
        if not token_id:
            return OrderResult(success=False, error="No YES token ID")

        try:
            if self.use_market_orders:
                # Market order - sell all tokens
                result = self.client.create_market_order(
                    token_id=token_id,
                    side="SELL",
                    amount=position.tokens,
                )
            else:
                # Limit order
                result = self.client.create_limit_order(
                    token_id=token_id,
                    side="SELL",
                    price=signal.current_price,
                    size=position.tokens,
                )

            order_id = result.get("orderID") or result.get("order_id")

            if order_id:
                return OrderResult(
                    success=True,
                    order_id=order_id,
                    filled_price=signal.current_price,
                    filled_size=position.tokens * signal.current_price,
                    tokens=position.tokens,
                )
            else:
                return OrderResult(
                    success=False,
                    error=f"No order ID in response: {result}"
                )

        except Exception as e:
            return OrderResult(success=False, error=str(e))

    def get_open_orders(self) -> list:
        """Get all open orders."""
        if not self.client:
            return []

        try:
            return self.client.get_open_orders()
        except Exception as e:
            print(f"Error getting open orders: {e}")
            return []

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order."""
        if not self.client:
            return False

        try:
            self.client.cancel_order(order_id)
            return True
        except Exception as e:
            print(f"Error canceling order {order_id}: {e}")
            return False

    def cancel_all_orders(self) -> bool:
        """Cancel all open orders."""
        if not self.client:
            return False

        try:
            self.client.cancel_all_orders()
            return True
        except Exception as e:
            print(f"Error canceling all orders: {e}")
            return False
