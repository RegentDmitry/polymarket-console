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
            # 1. Use suggested_size from signal (respects reserve balance)
            # Fall back to full balance only if suggested_size not set
            if signal.suggested_size and signal.suggested_size > 0:
                balance = signal.suggested_size * 0.98  # 2% safety margin
            else:
                raw_balance = self.get_balance()
                balance = raw_balance * 0.98

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

            # 3. Calculate available liquidity up to fair price
            # Buy at any price below fair_price (we still have positive edge)
            # asks are sorted by price ascending (best price first)
            target_price = signal.fair_price if signal.fair_price else signal.current_price
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

            # Polymarket minimum order size is $1
            MIN_ORDER_SIZE = 1.0
            if total_cost < MIN_ORDER_SIZE:
                return OrderResult(
                    success=False,
                    error=f"Order too small (${total_cost:.2f}), min $1"
                ), None

            # 4. Place order at the worst (highest) price we're willing to pay
            # This ensures it sweeps all levels up to fair price
            order_price = target_price
            avg_price = total_cost / available_size if available_size > 0 else target_price

            # Try placing order, retry with half size on balance error
            last_error = None
            for attempt in range(3):
                try:
                    result = self.client.create_limit_order(
                        token_id=token_id,
                        side="BUY",
                        price=order_price,
                        size=available_size,
                    )
                    last_error = None
                    break
                except Exception as e:
                    err_str = str(e)
                    if "not enough balance" in err_str and attempt < 2:
                        # Reduce size by half and retry
                        available_size = available_size / 2
                        total_cost = total_cost / 2
                        avg_price = total_cost / available_size if available_size > 0 else target_price
                        if total_cost < MIN_ORDER_SIZE:
                            return OrderResult(
                                success=False,
                                error=f"Balance insufficient even at ${total_cost:.2f}"
                            ), None
                        last_error = err_str
                    else:
                        raise

            if last_error:
                return OrderResult(success=False, error=last_error), None

            order_id = result.get("orderID") or result.get("order_id")

            if order_id:
                # Create position
                position = Position(
                    market_id=signal.market_id,
                    market_slug=signal.market_slug,
                    market_name=signal.market_name,
                    outcome=signal.outcome,
                    resolution_date=market.end_date,
                    entry_price=avg_price,
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
                    filled_price=avg_price,
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

    def get_matic_balance(self) -> float:
        """Get MATIC (POL) balance on Polygon for gas fees."""
        if not self.client or not self.client.pk:
            return 0.0
        try:
            import os
            from web3 import Web3
            rpc = os.getenv("POLYGON_RPC", "https://polygon-rpc.com")
            w3 = Web3(Web3.HTTPProvider(rpc))
            account = w3.eth.account.from_key(self.client.pk)
            balance_wei = w3.eth.get_balance(account.address)
            return float(w3.from_wei(balance_wei, "ether"))
        except Exception:
            return 0.0

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

    def ensure_sell_approval(self):
        """Ensure CTF token approval for both exchange contracts (one-time on-chain tx)."""
        if not self.client or getattr(self, '_sell_approved', False):
            return
        logger = get_logger()
        try:
            import os
            from web3 import Web3

            pk = self.client.pk
            if not pk:
                return

            rpc = os.getenv("POLYGON_RPC", "https://polygon-rpc.com")
            w3 = Web3(Web3.HTTPProvider(rpc))

            account = w3.eth.account.from_key(pk)
            ctf_address = w3.to_checksum_address("0x4D97DCd97eC945f40cF65F87097ACe5EA0476045")

            # Minimal ABI for setApprovalForAll and isApprovedForAll
            abi = [
                {"inputs": [{"name": "operator", "type": "address"}, {"name": "approved", "type": "bool"}],
                 "name": "setApprovalForAll", "outputs": [], "stateMutability": "nonpayable", "type": "function"},
                {"inputs": [{"name": "owner", "type": "address"}, {"name": "operator", "type": "address"}],
                 "name": "isApprovedForAll", "outputs": [{"name": "", "type": "bool"}],
                 "stateMutability": "view", "type": "function"},
            ]
            ctf = w3.eth.contract(address=ctf_address, abi=abi)

            exchanges = [
                "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E",  # normal exchange
                "0xC5d563A36AE78145C45a50134d48A1215220f80a",  # neg_risk exchange
                "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296",  # neg_risk adapter
            ]

            all_approved = True
            for ex in exchanges:
                ex_addr = w3.to_checksum_address(ex)
                approved = ctf.functions.isApprovedForAll(account.address, ex_addr).call()
                logger.log_info(f"CTF approval check {ex[:10]}... = {approved}")
                if not approved:
                    matic = float(w3.from_wei(w3.eth.get_balance(account.address), "ether"))
                    if matic < 0.001:
                        logger.log_warning(f"Cannot approve CTF: no MATIC for gas (balance: {matic:.6f})")
                        all_approved = False
                        continue
                    logger.log_info(f"Approving CTF for exchange {ex[:10]}... (MATIC: {matic:.4f})")
                    nonce = w3.eth.get_transaction_count(account.address)
                    tx = ctf.functions.setApprovalForAll(ex_addr, True).build_transaction({
                        "from": account.address,
                        "nonce": nonce,
                        "gas": 100000,
                        "gasPrice": w3.eth.gas_price,
                        "chainId": 137,
                    })
                    signed = w3.eth.account.sign_transaction(tx, pk)
                    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
                    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
                    logger.log_info(f"CTF approval tx: {tx_hash.hex()} status={receipt['status']}")

            self._sell_approved = all_approved
            if not all_approved:
                logger.log_warning("CTF approval incomplete - will retry next time")
                return

        except Exception as e:
            logger.log_warning(f"CTF approval error: {e}")

    def place_sell_limit(self, token_id: str, price: float, size: float) -> Optional[str]:
        """Place a GTC sell limit order. Returns order_id or None."""
        if not self.client:
            return None
        logger = get_logger()
        try:
            import math
            # Round price to 2 decimal places (Polymarket requirement)
            price = round(price, 2)
            if price <= 0:
                return None
            # Cap at 0.99 — Polymarket max valid price
            if price >= 1:
                price = 0.99
            # Round size DOWN to avoid exceeding available balance
            size = math.floor(size * 100) / 100
            # Polymarket minimum order size is 5 tokens
            if size < 5:
                return None
            # Ensure on-chain approval for selling (one-time)
            self.ensure_sell_approval()
            # Update conditional token allowance on API and check balance
            try:
                from polymarket_console.clob_types import BalanceAllowanceParams, AssetType
                params = BalanceAllowanceParams(
                    asset_type=AssetType.CONDITIONAL,
                    token_id=token_id,
                )
                self.client.client.update_balance_allowance(params)
                # Check what API sees for this token
                bal = self.client.client.get_balance_allowance(params)
                logger.log_info(
                    f"SELL token={token_id[:12]}... balance={bal}"
                )
            except Exception as e:
                logger.log_warning(f"SELL allowance update failed: {e}")
            # Try placing the order, retry once after short delay if balance error
            import time
            for attempt in range(2):
                try:
                    result = self.client.create_limit_order(
                        token_id=token_id,
                        side="SELL",
                        price=price,
                        size=round(size, 2),
                    )
                    order_id = result.get("orderID") or result.get("order_id")
                    if order_id:
                        logger.log_info(f"SELL LIMIT placed: {price:.1%} size={size:.2f} order={order_id[:12]}")
                    return order_id
                except Exception as e:
                    if attempt == 0 and "balance" in str(e).lower():
                        logger.log_info(f"SELL retry after allowance refresh (attempt {attempt+1})")
                        time.sleep(2)
                        # Refresh allowance again
                        try:
                            from polymarket_console.clob_types import BalanceAllowanceParams, AssetType
                            params = BalanceAllowanceParams(
                                asset_type=AssetType.CONDITIONAL,
                                token_id=token_id,
                            )
                            self.client.client.update_balance_allowance(params)
                        except Exception:
                            pass
                        continue
                    raise
        except Exception as e:
            logger.log_warning(f"SELL LIMIT failed: {e}")
            return None

    def get_open_order_ids(self) -> set[str]:
        """Get set of currently open order IDs from API."""
        orders = self.get_open_orders()
        result = set()
        for o in orders:
            oid = o.get("id") or o.get("order_id") or o.get("orderID")
            if oid:
                result.add(oid)
        return result

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

    def check_market_resolved(self, condition_id: str) -> tuple[bool, Optional[str]]:
        """Check if a market has resolved.

        Returns:
            (is_resolved, winning_outcome) - e.g. (True, "Yes") or (False, None)
        """
        if not self.client:
            return False, None
        try:
            market = self.client.get_clob_market(condition_id)
            if not market:
                return False, None
            closed = market.get("closed", False)
            outcome = market.get("outcome")  # "Yes" / "No" / None
            return bool(closed), outcome
        except Exception:
            return False, None

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
        active_positions = storage.load_all_active()

        # If we already have earthquake positions, skip sync entirely —
        # the bot tracks its own trades, synced duplicates cause double-counting
        has_earthquake = any(p.strategy == "earthquake" for p in active_positions)
        if has_earthquake:
            logger.log_info("SYNC SKIP: earthquake positions exist, skipping API sync to avoid duplicates")
            return []

        existing_slugs = {p.market_slug for p in active_positions}

        for api_pos in api_positions:
            # Extract position data
            token_id = api_pos.get("asset", "")
            size = float(api_pos.get("size", 0))
            avg_cost = float(api_pos.get("avgCost", 0))
            market_info = api_pos.get("market", {})

            if size <= 0:
                continue

            # Build market slug from API data
            condition_id = api_pos.get("conditionId", "") or market_info.get("conditionId", "") or market_info.get("condition_id", "")
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
