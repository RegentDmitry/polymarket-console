"""
Polymarket order executor for weather bot.

Adapted from crypto bot executor. Simplified:
- No sell limit orders (hold to resolution)
- No market_sell (only edge_exit via CLOB sell)
- Loads weather/.env for separate wallet
"""

import json
import math
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

    def ensure_sell_approval(self):
        """Ensure CTF token approval for exchange contracts (one-time on-chain tx)."""
        if not self.client or getattr(self, '_sell_approved', False):
            return
        logger = get_logger()
        try:
            import os
            from web3 import Web3

            pk = self.client.pk
            if not pk:
                return

            rpc = os.getenv("POLYGON_RPC", "https://polygon-bor-rpc.publicnode.com")
            w3 = Web3(Web3.HTTPProvider(rpc))

            account = w3.eth.account.from_key(pk)
            ctf_address = w3.to_checksum_address("0x4D97DCd97eC945f40cF65F87097ACe5EA0476045")

            abi = [
                {"inputs": [{"name": "operator", "type": "address"}, {"name": "approved", "type": "bool"}],
                 "name": "setApprovalForAll", "outputs": [], "stateMutability": "nonpayable", "type": "function"},
                {"inputs": [{"name": "owner", "type": "address"}, {"name": "operator", "type": "address"}],
                 "name": "isApprovedForAll", "outputs": [{"name": "", "type": "bool"}],
                 "stateMutability": "view", "type": "function"},
            ]
            ctf = w3.eth.contract(address=ctf_address, abi=abi)

            exchanges = [
                "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E",
                "0xC5d563A36AE78145C45a50134d48A1215220f80a",
                "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296",
            ]

            all_approved = True
            for ex in exchanges:
                ex_addr = w3.to_checksum_address(ex)
                approved = ctf.functions.isApprovedForAll(account.address, ex_addr).call()
                if not approved:
                    matic = float(w3.from_wei(w3.eth.get_balance(account.address), "ether"))
                    if matic < 0.001:
                        logger.log_warning(f"Cannot approve CTF: no MATIC for gas ({matic:.6f})")
                        all_approved = False
                        continue
                    logger.log_info(f"Approving CTF for exchange {ex[:10]}...")
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
                    nonce += 1

            self._sell_approved = all_approved
            if not all_approved:
                logger.log_warning("CTF approval incomplete - will retry next time")
        except Exception as e:
            logger.log_warning(f"CTF sell approval error: {e}")

    def sell(self, signal: Signal, position: Position, market: Market) -> OrderResult:
        """Execute a SELL order (edge_exit only — rare for weather).

        Walks the bid side of the orderbook to sell into existing buyers.
        If best bid is too far from signal price, places a maker order and waits.
        """
        if not self.client:
            return OrderResult(success=False, error="Client not initialized")

        token_id = position.token_id or market.yes_token_id
        if not token_id:
            return OrderResult(success=False, error="No token ID")

        logger = get_logger()

        try:
            # Step 1: Ensure on-chain CTF approval
            self.ensure_sell_approval()

            # Step 2: Sync token balance allowance and get real on-chain token balance
            real_balance_raw = 0
            try:
                from polymarket_console.clob_types import BalanceAllowanceParams, AssetType
                params = BalanceAllowanceParams(
                    asset_type=AssetType.CONDITIONAL,
                    token_id=token_id,
                )
                self.client.client.update_balance_allowance(params)
                bal_info = self.client.client.get_balance_allowance(params)
                real_balance_raw = int(bal_info.get("balance", 0))
            except Exception as e:
                logger.log_warning(f"SELL allowance sync: {e}")

            # Use real on-chain balance to avoid "not enough balance" errors
            if real_balance_raw > 0:
                size = math.floor(real_balance_raw / 1e4) / 100  # floor to 2 decimals
            else:
                size = math.floor(position.tokens * 100) / 100

            if size < 1:
                return OrderResult(success=False, error=f"Size too small: {size:.2f} tokens")

            # Step 3: Check orderbook bids to find executable price
            sell_price = round(signal.current_price, 2)
            if sell_price <= 0:
                return OrderResult(success=False, error=f"Invalid sell price: {signal.current_price}")
            if sell_price >= 1:
                sell_price = 0.99

            try:
                ob = self.client.get_orderbook(token_id)
                bids = ob.bids if hasattr(ob, 'bids') else ob.get("bids", [])
                if bids:
                    best_bid = float(bids[0].price if hasattr(bids[0], 'price') else bids[0].get("price", 0))
                    # Sell at best bid to get filled immediately (taker)
                    # Only if best bid is at least half of entry price (don't dump for nothing)
                    min_acceptable = position.entry_price * 0.5
                    if best_bid >= min_acceptable:
                        sell_price = best_bid
                        logger.log_info(f"SELL using best bid: {best_bid}")
                    else:
                        logger.log_info(f"SELL best bid {best_bid} too low (min {min_acceptable:.2f}), using {sell_price}")
            except Exception as e:
                logger.log_warning(f"SELL orderbook check: {e}")

            balance_before = self.get_balance()

            # Step 4: Place sell order with retry
            logger.log_info(f"SELL order: price={sell_price} size={size} (raw={real_balance_raw})")
            order_id = None
            for attempt in range(2):
                try:
                    result = self.client.create_limit_order(
                        token_id=token_id,
                        side="SELL",
                        price=sell_price,
                        size=size,
                    )
                    order_id = result.get("orderID") or result.get("order_id")
                    break
                except Exception as e:
                    err_detail = str(e)
                    if hasattr(e, 'error_msg'):
                        err_detail = f"status={getattr(e, 'status_code', '?')} msg={e.error_msg}"
                    logger.log_warning(f"SELL attempt {attempt+1} failed: {err_detail}")
                    if attempt == 0:
                        time.sleep(2)
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

            if not order_id:
                return OrderResult(success=False, error=f"No order ID: {result}")

            # Step 5: Wait for fill confirmation (balance should increase)
            actual_proceeds = 0.0
            for _ in range(8):
                time.sleep(1)
                try:
                    balance_after = self.get_balance()
                    actual_proceeds = balance_after - balance_before
                    if actual_proceeds > 0.5:
                        break
                except Exception:
                    pass

            if actual_proceeds < 0.5:
                self.cancel_order(order_id)
                logger.log_warning(f"Sell order {order_id} not filled after 8s, cancelled")
                return OrderResult(success=False, error="Sell order not filled, cancelled")

            logger.log_info(f"SELL filled: proceeds=${actual_proceeds:.2f}")
            return OrderResult(
                success=True,
                order_id=order_id,
                filled_price=actual_proceeds / position.tokens if position.tokens > 0 else sell_price,
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
