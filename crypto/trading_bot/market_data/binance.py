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
        self._btc_high_3m: float = 0.0
        self._btc_low_3m: float = 0.0
        self._eth_high_3m: float = 0.0
        self._eth_low_3m: float = 0.0
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
    def btc_high_3m(self) -> float:
        with self._lock:
            return self._btc_high_3m if self._btc_high_3m > 0 else self._btc_price

    @property
    def btc_low_3m(self) -> float:
        with self._lock:
            return self._btc_low_3m if self._btc_low_3m > 0 else self._btc_price

    @property
    def eth_high_3m(self) -> float:
        with self._lock:
            return self._eth_high_3m if self._eth_high_3m > 0 else self._eth_price

    @property
    def eth_low_3m(self) -> float:
        with self._lock:
            return self._eth_low_3m if self._eth_low_3m > 0 else self._eth_price

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
                self._update_klines()
                return True

        except Exception:
            pass
        return False

    def _update_klines(self) -> bool:
        """Fetch last 3 closed 1m candles for BTC and ETH.

        Requests 4 candles (limit=4), drops the last one (current/unclosed).
        From the 3 closed candles, extracts max(High) and min(Low).
        """
        try:
            for symbol, prefix in [("BTCUSDT", "btc"), ("ETHUSDT", "eth")]:
                url = f"{BINANCE_API}/klines?symbol={symbol}&interval=1m&limit=4"
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                resp = urllib.request.urlopen(req, timeout=10, context=_ctx)
                data = json.loads(resp.read())

                # Drop the last candle (current, unclosed); use first 3 (closed)
                closed = data[:-1] if len(data) > 1 else data
                high = max(float(c[2]) for c in closed)  # index 2 = High
                low = min(float(c[3]) for c in closed)   # index 3 = Low

                with self._lock:
                    setattr(self, f"_{prefix}_high_3m", high)
                    setattr(self, f"_{prefix}_low_3m", low)
            return True
        except Exception:
            return False

    def get_spot(self, currency: str) -> float:
        """Get spot price for a currency (BTC or ETH)."""
        currency = currency.upper()
        if currency == "BTC":
            return self.btc_price
        elif currency == "ETH":
            return self.eth_price
        return 0.0

    def get_high_low(self, currency: str) -> tuple:
        """Get (high_3m, low_3m) for a currency."""
        currency = currency.upper()
        if currency == "BTC":
            return self.btc_high_3m, self.btc_low_3m
        elif currency == "ETH":
            return self.eth_high_3m, self.eth_low_3m
        return 0.0, 0.0

    def get_snapshot(self) -> dict:
        """Get a thread-safe snapshot of all data."""
        with self._lock:
            return {
                "btc_price": self._btc_price,
                "eth_price": self._eth_price,
                "btc_high_3m": self._btc_high_3m if self._btc_high_3m > 0 else self._btc_price,
                "btc_low_3m": self._btc_low_3m if self._btc_low_3m > 0 else self._btc_price,
                "eth_high_3m": self._eth_high_3m if self._eth_high_3m > 0 else self._eth_price,
                "eth_low_3m": self._eth_low_3m if self._eth_low_3m > 0 else self._eth_price,
                "last_update": self._last_update,
            }
