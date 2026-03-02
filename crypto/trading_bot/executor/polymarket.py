"""
Polymarket order executor for crypto bot — handles buying and selling.

Adapted from earthquakes/trading_bot/executor/polymarket.py.
Key difference: loads crypto/.env for separate wallet.
"""

import sys
from pathlib import Path
from typing import Optional, Tuple
from dataclasses import dataclass
from datetime import datetime

# Add parent directory to path for polymarket_client import
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "earthquakes"))

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
    Executes trades on Polymarket for the crypto bot.

    Uses crypto/.env for wallet credentials (separate from earthquake bot).
    """

    def __init__(self):
        """Initialize executor with PolymarketClient using crypto/.env."""
        self.client: Optional[PolymarketClient] = None
        self.initialized = False

        if POLYMARKET_AVAILABLE:
            try:
                # Load crypto-specific .env
                env_path = Path(__file__).parent.parent.parent / ".env"
                self.client = PolymarketClient(env_path=env_path)
                self.initialized = True
            except Exception as e:
                print(f"Failed to initialize Polymarket client: {e}")
                self.client = None
                self.initialized = False

    def get_balance(self) -> float:
        """Get current USDC balance. Tries API first, falls back to on-chain."""
        if not self.client:
            return 0.0

        try:
            balance_info = self.client.get_balance()
            balance_raw = float(balance_info.get("balance", 0))
            return balance_raw / 1e6  # USDC has 6 decimals
        except Exception:
            # Fallback: on-chain USDC.e balance
            return self.get_usdc_balance_onchain()

    def get_address(self) -> str:
        """Get wallet address."""
        if not self.client:
            return ""
        return self.client.get_address()

    def buy(self, signal: Signal, market: Market) -> Tuple[OrderResult, Optional[Position]]:
        """
        Execute a BUY order.

        Buys maximum available liquidity at the signal price within balance limits.
        """
        if not self.client:
            return OrderResult(success=False, error="Client not initialized"), None

        token_id = signal.token_id or market.yes_token_id
        if not token_id:
            return OrderResult(success=False, error="No token ID"), None

        try:
            # Use suggested_size from signal, fall back to full balance
            if signal.suggested_size and signal.suggested_size > 0:
                balance = signal.suggested_size * 0.98  # 2% safety margin
            else:
                raw_balance = self.get_balance()
                balance = raw_balance * 0.98

            if balance <= 0:
                return OrderResult(success=False, error="No balance available"), None

            # Get orderbook
            orderbook = self.client.get_orderbook(token_id)
            if hasattr(orderbook, 'asks'):
                asks = orderbook.asks or []
            else:
                asks = orderbook.get("asks", [])

            if not asks:
                return OrderResult(success=False, error="No asks in orderbook"), None

            # Calculate available liquidity up to fair price
            target_price = signal.fair_price if signal.fair_price else signal.current_price
            available_size = 0.0
            total_cost = 0.0

            for ask in asks:
                ask_price = float(ask.get("price", 0) if isinstance(ask, dict) else getattr(ask, 'price', 0))
                ask_size = float(ask.get("size", 0) if isinstance(ask, dict) else getattr(ask, 'size', 0))

                if ask_price <= target_price:
                    cost_for_this = ask_price * ask_size
                    if total_cost + cost_for_this <= balance:
                        available_size += ask_size
                        total_cost += cost_for_this
                    else:
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

            MIN_ORDER_SIZE = 1.0
            if total_cost < MIN_ORDER_SIZE:
                return OrderResult(
                    success=False,
                    error=f"Order too small (${total_cost:.2f}), min $1"
                ), None

            order_price = target_price
            avg_price = total_cost / available_size if available_size > 0 else target_price

            # Record balance before buy to measure actual cost (incl. fees)
            balance_before_buy = self.get_balance()

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
                # Measure actual cost
                actual_entry_size = total_cost
                try:
                    balance_after_buy = self.get_balance()
                    actual_cost = balance_before_buy - balance_after_buy
                    if actual_cost > 0 and actual_cost < total_cost * 2:
                        actual_entry_size = actual_cost
                except Exception:
                    pass

                actual_avg_price = actual_entry_size / available_size if available_size > 0 else avg_price

                position = Position(
                    market_id=signal.market_id,
                    market_slug=signal.market_slug,
                    market_name=signal.market_name,
                    outcome=signal.outcome,
                    resolution_date=market.end_date,
                    entry_price=actual_avg_price,
                    entry_time=datetime.utcnow().isoformat() + "Z",
                    entry_size=actual_entry_size,
                    tokens=available_size,
                    strategy="crypto",
                    fair_price_at_entry=signal.fair_price,
                    edge_at_entry=signal.edge,
                    entry_order_id=order_id,
                )

                return OrderResult(
                    success=True,
                    order_id=order_id,
                    filled_price=actual_avg_price,
                    filled_size=actual_entry_size,
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
        """Execute a SELL order."""
        if not self.client:
            return OrderResult(success=False, error="Client not initialized")

        if position.outcome.upper() == "NO":
            token_id = market.no_token_id
            token_type = "NO"
        else:
            token_id = market.yes_token_id
            token_type = "YES"
        if not token_id:
            return OrderResult(success=False, error=f"No {token_type} token ID")

        try:
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

    def _get_eoa_address(self) -> str:
        """Get EOA address from private key."""
        if not self.client or not self.client.pk:
            return ""
        try:
            from eth_account import Account
            return Account.from_key(self.client.pk).address
        except ImportError:
            # Fallback: derive from private key without web3
            try:
                import hashlib
                import binascii
                pk_bytes = bytes.fromhex(self.client.pk.replace("0x", ""))
                from coincurve import PublicKey
                pub = PublicKey.from_valid_secret(pk_bytes).format(compressed=False)[1:]
                addr = "0x" + hashlib.new("keccak256", pub).hexdigest()[-40:]
                return addr
            except ImportError:
                return ""

    def _rpc_call(self, method: str, params: list) -> str:
        """Make a JSON-RPC call to Polygon (tries multiple RPCs)."""
        import json, os, ssl, urllib.request
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        primary = os.getenv("POLYGON_RPC", "https://polygon-bor-rpc.publicnode.com")
        rpcs = [primary, "https://polygon.llamarpc.com", "https://1rpc.io/matic"]

        payload = json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": method, "params": params
        }).encode()

        for rpc in rpcs:
            try:
                req = urllib.request.Request(rpc, data=payload,
                                             headers={"Content-Type": "application/json"})
                resp = urllib.request.urlopen(req, timeout=10, context=ctx)
                result = json.loads(resp.read())
                return result.get("result", "0x0")
            except Exception:
                continue
        return "0x0"

    def get_matic_balance(self) -> float:
        """Get MATIC (POL) balance for gas fees via JSON-RPC."""
        addr = self._get_eoa_address()
        if not addr:
            return 0.0
        try:
            hex_balance = self._rpc_call("eth_getBalance", [addr, "latest"])
            return int(hex_balance, 16) / 1e18
        except Exception:
            return 0.0

    def get_usdc_balance_onchain(self) -> float:
        """Get USDC.e balance via on-chain JSON-RPC (no API key needed)."""
        addr = self._get_eoa_address()
        if not addr:
            return 0.0
        try:
            # USDC.e on Polygon: 0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174
            usdc = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
            # balanceOf(address) selector = 0x70a08231
            padded_addr = "0x70a08231" + addr.lower().replace("0x", "").zfill(64)
            hex_balance = self._rpc_call("eth_call", [{"to": usdc, "data": padded_addr}, "latest"])
            return int(hex_balance, 16) / 1e6  # USDC has 6 decimals
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

        except Exception as e:
            logger.log_warning(f"CTF approval error: {e}")

    def place_sell_limit(self, token_id: str, price: float, size: float) -> Optional[str]:
        """Place a GTC sell limit order. Returns order_id or None."""
        if not self.client:
            return None
        logger = get_logger()
        try:
            import math
            price = round(price, 2)
            if price <= 0:
                return None
            if price >= 1:
                price = 0.99
            size = math.floor(size * 100) / 100
            if size < 5:
                return None
            self.ensure_sell_approval()
            try:
                from polymarket_console.clob_types import BalanceAllowanceParams, AssetType
                params = BalanceAllowanceParams(
                    asset_type=AssetType.CONDITIONAL,
                    token_id=token_id,
                )
                self.client.client.update_balance_allowance(params)
                bal = self.client.client.get_balance_allowance(params)
                logger.log_info(f"SELL token={token_id[:12]}... balance={bal}")
            except Exception as e:
                logger.log_warning(f"SELL allowance update failed: {e}")
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
        """Get set of currently open order IDs."""
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
        """Check if a market has resolved."""
        if not self.client:
            return False, None
        try:
            market = self.client.get_clob_market(condition_id)
            if not market:
                return False, None
            closed = market.get("closed", False)
            outcome = market.get("outcome")
            return bool(closed), outcome
        except Exception:
            return False, None

    def get_market_info(self, market_id: str) -> Optional[dict]:
        """Get full market info from CLOB API."""
        if not self.client:
            return None
        try:
            return self.client.get_clob_market(market_id)
        except Exception:
            return None

    def redeem_neg_risk(self, condition_id: str, outcome: str, token_id: str) -> bool:
        """Redeem resolved NegRisk position via NegRisk adapter."""
        if not self.client or not self.client.pk:
            return False
        logger = get_logger()
        try:
            import os
            from web3 import Web3

            rpc = os.getenv("POLYGON_RPC", "https://polygon-bor-rpc.publicnode.com")
            w3 = Web3(Web3.HTTPProvider(rpc))
            account = w3.eth.account.from_key(self.client.pk)
            eoa = account.address

            CTF = Web3.to_checksum_address("0x4D97DCd97eC945f40cF65F87097ACe5EA0476045")
            NEG_RISK = Web3.to_checksum_address("0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296")

            bal_abi = [{"inputs": [{"name": "account", "type": "address"},
                                   {"name": "id", "type": "uint256"}],
                        "name": "balanceOf",
                        "outputs": [{"name": "", "type": "uint256"}],
                        "stateMutability": "view", "type": "function"}]
            ctf = w3.eth.contract(address=CTF, abi=bal_abi)
            balance = ctf.functions.balanceOf(eoa, int(token_id)).call()

            if balance <= 0:
                return False

            logger.log_info(
                f"REDEEM: condition={condition_id[:16]}... "
                f"{outcome} balance={balance / 1e6:.2f}"
            )

            cid_bytes = bytes.fromhex(condition_id.replace("0x", ""))
            if outcome.upper() == "YES":
                amounts = [balance, 0]
            else:
                amounts = [0, balance]

            redeem_abi = [{
                "inputs": [
                    {"name": "_conditionId", "type": "bytes32"},
                    {"name": "_amounts", "type": "uint256[]"}
                ],
                "name": "redeemPositions",
                "outputs": [],
                "stateMutability": "nonpayable",
                "type": "function"
            }]

            neg_risk = w3.eth.contract(address=NEG_RISK, abi=redeem_abi)
            gas_price = max(w3.eth.gas_price * 2, w3.to_wei(50, "gwei"))

            tx = neg_risk.functions.redeemPositions(
                cid_bytes, amounts
            ).build_transaction({
                "from": eoa,
                "nonce": w3.eth.get_transaction_count(eoa),
                "gas": 500000,
                "gasPrice": gas_price,
                "chainId": 137,
            })

            signed = w3.eth.account.sign_transaction(tx, self.client.pk)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

            if receipt.status == 1:
                new_bal = ctf.functions.balanceOf(eoa, int(token_id)).call()
                redeemed = (balance - new_bal) / 1e6
                logger.log_info(
                    f"REDEEM SUCCESS: {redeemed:.2f} tokens redeemed, "
                    f"tx=0x{tx_hash.hex()[:12]}..."
                )
                return new_bal < balance
            else:
                logger.log_warning(f"REDEEM FAILED: tx reverted 0x{tx_hash.hex()[:12]}...")
                return False

        except Exception as e:
            logger.log_warning(f"REDEEM error: {e}")
            return False

    def get_api_positions(self) -> list[dict]:
        """Get all positions from Polymarket API."""
        if not self.client:
            return []
        try:
            return self.client.get_positions()
        except Exception as e:
            print(f"Error getting positions from API: {e}")
            return []
