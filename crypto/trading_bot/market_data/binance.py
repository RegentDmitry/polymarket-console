"""
Binance market data — BTC/ETH spot prices via REST API.

Thread-safe: designed to be called from a background thread.
"""

import json
import ssl
import urllib.request
from datetime import datetime, timezone
from threading import Lock
from typing import Optional


# SSL context for Binance API
_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE

BINANCE_API = "https://api.binance.com/api/v3"


class BinanceData:
    """Fetches and caches BTC/ETH spot prices from Binance."""

    def __init__(self):
        self._lock = Lock()
        self._btc_price: float = 0.0
        self._eth_price: float = 0.0
        self._last_update: Optional[datetime] = None

    @property
    def btc_price(self) -> float:
        with self._lock:
            return self._btc_price

    @property
    def eth_price(self) -> float:
        with self._lock:
            return self._eth_price

    @property
    def last_update(self) -> Optional[datetime]:
        with self._lock:
            return self._last_update

    @property
    def age_seconds(self) -> float:
        with self._lock:
            if self._last_update is None:
                return float('inf')
            return (datetime.now(timezone.utc) - self._last_update).total_seconds()

    def update(self) -> bool:
        """Fetch latest prices from Binance. Returns True on success."""
        try:
            url = f"{BINANCE_API}/ticker/price?symbols=[\"BTCUSDT\",\"ETHUSDT\"]"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            resp = urllib.request.urlopen(req, timeout=10, context=_ctx)
            data = json.loads(resp.read())

            btc = 0.0
            eth = 0.0
            for item in data:
                if item["symbol"] == "BTCUSDT":
                    btc = float(item["price"])
                elif item["symbol"] == "ETHUSDT":
                    eth = float(item["price"])

            if btc > 0 and eth > 0:
                with self._lock:
                    self._btc_price = btc
                    self._eth_price = eth
                    self._last_update = datetime.now(timezone.utc)
                return True

        except Exception:
            pass
        return False

    def get_spot(self, currency: str) -> float:
        """Get spot price for a currency (BTC or ETH)."""
        currency = currency.upper()
        if currency == "BTC":
            return self.btc_price
        elif currency == "ETH":
            return self.eth_price
        return 0.0

    def get_snapshot(self) -> dict:
        """Get a thread-safe snapshot of all data."""
        with self._lock:
            return {
                "btc_price": self._btc_price,
                "eth_price": self._eth_price,
                "last_update": self._last_update,
            }
