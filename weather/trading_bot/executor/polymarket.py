"""
Polymarket order executor for weather bot.

Adapted from crypto bot executor. Simplified:
- No sell limit orders (hold to resolution)
- No market_sell (only edge_exit via CLOB sell)
- Loads weather/.env for separate wallet
"""

import json
import ssl
import sys
import time
import urllib.request
from pathlib import Path
from typing import Optional, Tuple
from dataclasses import dataclass
from datetime import datetime, timezone

# Add earthquakes to path for polymarket_client import
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "earthquakes"))

from ..models.position import Position
from ..models.signal import Signal
from ..models.market import Market
from ..logger import get_logger

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
            self.timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class PolymarketExecutor:
    """Executes trades on Polymarket for the weather bot."""

    def __init__(self):
        self.client: Optional[PolymarketClient] = None
        self.initialized = False

        if POLYMARKET_AVAILABLE:
            try:
                env_path = Path(__file__).parent.parent.parent / ".env"
                self.client = PolymarketClient(env_path=env_path)
                self.initialized = True
            except Exception as e:
                get_logger().log_warning(f"Failed to initialize Polymarket client: {e}")

    def get_balance(self) -> float:
        """Get current USDC balance."""
        if not self.client:
            return 0.0

        try:
            balance_info = self.client.get_balance()
            balance_raw = float(balance_info.get("balance", 0))
            return balance_raw / 1e6  # USDC has 6 decimals
        except Exception:
            return self.get_usdc_balance_onchain()

    def get_address(self) -> str:
        if not self.client:
            return ""
        return self.client.get_address()

    def get_usdc_balance_onchain(self) -> float:
        """Fallback: get USDC.e balance via JSON-RPC."""
        if not self.client or not self.client.pk:
            return 0.0
        try:
            from eth_account import Account
            eoa = Account.from_key(self.client.pk).address

            USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
            data = f"0x70a08231000000000000000000000000{eoa[2:]}"
            rpcs = [
                "https://polygon-bor-rpc.publicnode.com",
                "https://polygon.llamarpc.com",
            ]

            import json
            import urllib.request
            import ssl
            ctx = ssl.create_default_context()

            for rpc in rpcs:
                try:
                    payload = json.dumps({
                        "jsonrpc": "2.0", "method": "eth_call",
                        "params": [{"to": USDC_E, "data": data}, "latest"],
                        "id": 1
                    })
                    req = urllib.request.Request(rpc, payload.encode(),
                                                headers={"Content-Type": "application/json"})
                    resp = urllib.request.urlopen(req, timeout=10, context=ctx)
                    result = json.loads(resp.read())
                    hex_balance = result.get("result", "0x0")
                    return int(hex_balance, 16) / 1e6
                except Exception:
                    continue
        except Exception:
            pass
        return 0.0

    def ensure_buy_approval(self):
        """Ensure USDC approval for exchange contracts."""
        if not self.client:
            return
        try:
            from polymarket_console.clob_types import BalanceAllowanceParams, AssetType
            params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            self.client.client.update_balance_allowance(params)
        except Exception:
            pass

    def buy(self, signal: Signal, market: Market) -> Tuple[OrderResult, Optional[Position]]:
        """Execute a BUY order."""
        if not self.client:
            return OrderResult(success=False, error="Client not initialized"), None

        token_id = signal.token_id or market.yes_token_id
        if not token_id:
            return OrderResult(success=False, error="No token ID"), None

        try:
            if signal.suggested_size and signal.suggested_size > 0:
                balance = signal.suggested_size * 0.98  # 2% safety margin
            else:
                balance = self.get_balance() * 0.98

            if balance <= 0:
                return OrderResult(success=False, error="No balance"), None

            # Get orderbook
            orderbook = self.client.get_orderbook(token_id)
            if hasattr(orderbook, 'asks'):
                asks = orderbook.asks or []
            else:
                asks = orderbook.get("asks", [])

            if not asks:
                return OrderResult(success=False, error="No asks"), None

            # Walk the orderbook up to signal price
            target_price = signal.current_price
            available_size = 0.0
            total_cost = 0.0

            for ask in asks:
                ask_price = float(ask.get("price", 0) if isinstance(ask, dict) else getattr(ask, 'price', 0))
                ask_size = float(ask.get("size", 0) if isinstance(ask, dict) else getattr(ask, 'size', 0))

                if ask_price <= target_price:
                    cost = ask_price * ask_size
                    if total_cost + cost <= balance:
                        available_size += ask_size
                        total_cost += cost
                    else:
                        remaining = balance - total_cost
                        available_size += remaining / ask_price
                        total_cost += remaining
                        break

            if available_size <= 0 or total_cost < 1.0:
                return OrderResult(
                    success=False,
                    error=f"No liquidity at {target_price:.2%} (min $1)"
                ), None

            self.ensure_buy_approval()

            # Snapshot balance right before order
            balance_before = self.get_balance()

            # Place order with retry
            last_error = None
            for attempt in range(3):
                try:
                    result = self.client.create_limit_order(
                        token_id=token_id,
                        side="BUY",
                        price=target_price,
                        size=available_size,
                    )
                    last_error = None
                    break
                except Exception as e:
                    err_str = str(e)
                    if "not enough balance" in err_str and attempt < 2:
                        self.ensure_buy_approval()
                        time.sleep(2)
                        available_size /= 2
                        total_cost /= 2
                        if total_cost < 1.0:
                            return OrderResult(success=False, error="Balance insufficient"), None
                        last_error = err_str
                    else:
                        raise

            if last_error:
                return OrderResult(success=False, error=last_error), None

            order_id = result.get("orderID") or result.get("order_id")

            if order_id:
                # Use total_cost as primary (calculated from orderbook).
                # Verify with balance delta if available, but cap it to
                # avoid over-counting when API returns stale balance.
                actual_entry_size = total_cost
                for _wait in range(3):
                    try:
                        time.sleep(1)
                        balance_after = self.get_balance()
                        actual_cost = balance_before - balance_after
                        if actual_cost > 0.5:
                            # Use balance delta only if it's reasonable
                            # (within 2x of expected cost — avoid stale reads)
                            if actual_cost <= total_cost * 2:
                                actual_entry_size = actual_cost
                            break
                    except Exception:
                        pass

                actual_avg_price = actual_entry_size / available_size if available_size > 0 else target_price

                position = Position(
                    market_id=signal.market_id,
                    market_slug=signal.market_slug,
                    market_name=signal.market_name,
                    outcome=signal.outcome,
                    entry_price=actual_avg_price,
                    entry_time=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    entry_size=actual_entry_size,
                    tokens=available_size,
                    strategy="weather",
                    fair_price_at_entry=signal.fair_price,
                    edge_at_entry=signal.edge,
                    entry_order_id=order_id,
                    token_id=token_id,
                    city=signal.city,
                    date=signal.date,
                    bucket_label=signal.bucket_label,
                )

                return OrderResult(
                    success=True,
                    order_id=order_id,
                    filled_price=actual_avg_price,
                    filled_size=actual_entry_size,
                    tokens=available_size,
                ), position
            else:
                return OrderResult(success=False, error=f"No order ID: {result}"), None

        except Exception as e:
            return OrderResult(success=False, error=str(e)), None

    def sell(self, signal: Signal, position: Position, market: Market) -> OrderResult:
        """Execute a SELL order (edge_exit only — rare for weather)."""
        if not self.client:
            return OrderResult(success=False, error="Client not initialized")

        token_id = position.token_id or market.yes_token_id
        if not token_id:
            return OrderResult(success=False, error="No token ID")

        logger = get_logger()

        try:
            balance_before = self.get_balance()

            result = self.client.create_limit_order(
                token_id=token_id,
                side="SELL",
                price=signal.current_price,
                size=position.tokens,
            )

            order_id = result.get("orderID") or result.get("order_id")
            if not order_id:
                return OrderResult(success=False, error=f"No order ID: {result}")

            # Wait for fill confirmation (balance should increase)
            actual_proceeds = 0.0
            for _ in range(5):
                time.sleep(1)
                try:
                    balance_after = self.get_balance()
                    actual_proceeds = balance_after - balance_before
                    if actual_proceeds > 0.5:
                        break
                except Exception:
                    pass

            if actual_proceeds < 0.5:
                # Not filled — cancel order
                self.cancel_order(order_id)
                logger.log_warning(f"Sell order {order_id} not filled, cancelled")
                return OrderResult(success=False, error="Sell order not filled, cancelled")

            return OrderResult(
                success=True,
                order_id=order_id,
                filled_price=actual_proceeds / position.tokens if position.tokens > 0 else signal.current_price,
                filled_size=actual_proceeds,
                tokens=position.tokens,
            )

        except Exception as e:
            return OrderResult(success=False, error=str(e))

    def check_market_resolved(self, condition_id: str) -> Tuple[bool, Optional[str]]:
        """Check if a market has resolved. Returns (is_resolved, winning_outcome)."""
        info = self.get_market_info(condition_id)
        if info and info.get("closed"):
            return True, info.get("outcome")
        return False, None

    def get_market_info(self, condition_id: str) -> Optional[dict]:
        """Get full market info from CLOB API via direct HTTP."""
        try:
            url = f"https://clob.polymarket.com/markets/{condition_id}"
            ctx = ssl.create_default_context()
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            resp = urllib.request.urlopen(req, timeout=10, context=ctx)
            return json.loads(resp.read())
        except Exception:
            return None

    def get_open_orders(self) -> list:
        """Get all open orders."""
        if not self.client:
            return []
        try:
            return self.client.get_open_orders() or []
        except Exception:
            return []

    def cancel_order(self, order_id: str) -> bool:
        """Cancel a specific order."""
        if not self.client:
            return False
        try:
            self.client.cancel_order(order_id)
            return True
        except Exception:
            return False
