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
from ..logger import get_logger

# Import Polymarket client
try:
    from polymarket_client import PolymarketClient
    POLYMARKET_AVAILABLE = True
except ImportError:
    PolymarketClient = None
    POLYMARKET_AVAILABLE = False


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

    Buys maximum available liquidity at target price within balance limits.
    """

    def __init__(self):
        """Initialize executor with PolymarketClient."""
        self.client: Optional[PolymarketClient] = None
        self.initialized = False

        if POLYMARKET_AVAILABLE:
            try:
                self.client = PolymarketClient()
                self.initialized = True
            except Exception as e:
                print(f"Failed to initialize Polymarket client: {e}")
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

        Buys maximum available liquidity at the signal price within balance limits.

        Args:
            signal: BUY signal with market info
            market: Market object with token IDs

        Returns:
            Tuple of (OrderResult, Position if successful)
        """
        if not self.client:
            return OrderResult(success=False, error="Client not initialized"), None

        token_id = signal.token_id or market.yes_token_id
        if not token_id:
            return OrderResult(success=False, error="No token ID"), None

        try:
            # 1. Get current balance
            balance = self.get_balance()
            if balance <= 0:
                return OrderResult(success=False, error="No balance available"), None

            # 2. Get orderbook
            orderbook = self.client.get_orderbook(token_id)
            # OrderBookSummary может быть объектом с атрибутами или dict
            if hasattr(orderbook, 'asks'):
                asks = orderbook.asks or []
            else:
                asks = orderbook.get("asks", [])

            if not asks:
                return OrderResult(success=False, error="No asks in orderbook"), None

            # 3. Calculate available liquidity at target price or better
            # asks are sorted by price ascending (best price first)
            target_price = signal.current_price
            available_size = 0.0  # in tokens
            total_cost = 0.0      # in USD

            for ask in asks:
                ask_price = float(ask.get("price", 0) if isinstance(ask, dict) else getattr(ask, 'price', 0))
                ask_size = float(ask.get("size", 0) if isinstance(ask, dict) else getattr(ask, 'size', 0))

                # Only take asks at our target price or better (lower)
                if ask_price <= target_price:
                    cost_for_this = ask_price * ask_size
                    if total_cost + cost_for_this <= balance:
                        available_size += ask_size
                        total_cost += cost_for_this
                    else:
                        # Partial fill with remaining balance
                        remaining = balance - total_cost
                        partial_size = remaining / ask_price
                        available_size += partial_size
                        total_cost += remaining
                        break

            if available_size <= 0 or total_cost <= 0:
                return OrderResult(
                    success=False,
                    error=f"No liquidity at price {target_price:.2%} or better"
                ), None

            # 4. Place order for the calculated size
            result = self.client.create_limit_order(
                token_id=token_id,
                side="BUY",
                price=target_price,
                size=available_size,
            )

            order_id = result.get("orderID") or result.get("order_id")

            if order_id:
                # Create position
                position = Position(
                    market_id=signal.market_id,
                    market_slug=signal.market_slug,
                    market_name=signal.market_name,
                    outcome=signal.outcome,
                    resolution_date=market.end_date,
                    entry_price=target_price,
                    entry_time=datetime.utcnow().isoformat() + "Z",
                    entry_size=total_cost,
                    tokens=available_size,
                    strategy="earthquake",
                    fair_price_at_entry=signal.fair_price,
                    edge_at_entry=signal.edge,
                    entry_order_id=order_id,
                )

                return OrderResult(
                    success=True,
                    order_id=order_id,
                    filled_price=target_price,
                    filled_size=total_cost,
                    tokens=available_size,
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
            # Sell all tokens at current price
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

    def get_api_positions(self) -> list[dict]:
        """
        Get all positions from Polymarket API.

        Returns:
            List of position dicts from the API
        """
        if not self.client:
            return []

        try:
            return self.client.get_positions()
        except Exception as e:
            print(f"Error getting positions from API: {e}")
            return []

    def sync_positions(self, storage) -> list[Position]:
        """
        Sync positions with Polymarket API.

        Fetches positions from API and creates local Position objects
        for any that don't exist locally.

        Args:
            storage: PositionStorage instance

        Returns:
            List of newly created Position objects
        """
        logger = get_logger()

        if not self.client:
            logger.log_warning("Cannot sync positions - client not initialized")
            return []

        logger.log_info("Syncing positions with Polymarket API...")

        api_positions = self.get_api_positions()
        if not api_positions:
            logger.log_info("No positions found on Polymarket API")
            return []

        logger.log_info(f"Found {len(api_positions)} positions on Polymarket")

        new_positions = []
        existing_slugs = {p.market_slug for p in storage.load_all_active()}

        for api_pos in api_positions:
            # Extract position data
            token_id = api_pos.get("asset", "")
            size = float(api_pos.get("size", 0))
            avg_cost = float(api_pos.get("avgCost", 0))
            market_info = api_pos.get("market", {})

            if size <= 0:
                continue

            # Build market slug from API data
            condition_id = market_info.get("conditionId", "")
            outcome = api_pos.get("outcome", "Yes")
            slug = api_pos.get("slug", condition_id)

            # Skip if we already have this position locally
            if slug in existing_slugs:
                continue

            # Calculate entry price from avg cost and size
            entry_price = avg_cost if avg_cost > 0 else 0.5
            entry_size = size * entry_price

            position = Position(
                market_id=condition_id,
                market_slug=slug,
                market_name=market_info.get("question", slug)[:50],
                outcome=outcome,
                resolution_date=market_info.get("endDateIso"),
                entry_price=entry_price,
                entry_time=datetime.utcnow().isoformat() + "Z",
                entry_size=entry_size,
                tokens=size,
                strategy="synced",
                fair_price_at_entry=entry_price,  # Unknown, use entry as estimate
            )

            # Save to storage
            storage.save(position)
            new_positions.append(position)

            logger.log_info(
                f"Synced position: {slug} - {size:.2f} tokens @ {entry_price:.2%}"
            )

        if new_positions:
            logger.log_info(f"Synced {len(new_positions)} new positions from API")
        else:
            logger.log_info("All API positions already exist locally")

        return new_positions
