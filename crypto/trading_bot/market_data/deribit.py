"""
Deribit market data — spot, IV, and futures curve.

Fetches from Deribit public API:
- Spot price via perpetual contract
- IV from ATM options (nearest expiry)
- Futures curve for drift interpolation

Thread-safe: designed to be called from a background thread.
"""

import json
import math
import ssl
import urllib.request
from datetime import datetime, timezone
from threading import Lock
from typing import List, Optional, Tuple


_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE


def deribit_get(path: str) -> dict:
    """Call Deribit public API."""
    req = urllib.request.Request(f"https://www.deribit.com/api/v2/public/{path}")
    resp = urllib.request.urlopen(req, timeout=10, context=_ctx)
    return json.loads(resp.read())["result"]


# (days_to_expiry, annualized_drift, futures_price, instrument_name)
FuturesEntry = Tuple[int, float, float, str]


class DeribitData:
    """Fetches and caches Deribit data (spot, IV, futures curve)."""

    def __init__(self):
        self._lock = Lock()
        self._btc_spot: float = 0.0
        self._eth_spot: float = 0.0
        self._btc_iv: float = 0.0
        self._eth_iv: float = 0.0
        self._btc_curve: List[FuturesEntry] = []
        self._eth_curve: List[FuturesEntry] = []
        self._last_update: Optional[datetime] = None

    # --- Thread-safe properties ---

    @property
    def btc_spot(self) -> float:
        with self._lock:
            return self._btc_spot

    @property
    def eth_spot(self) -> float:
        with self._lock:
            return self._eth_spot

    @property
    def btc_iv(self) -> float:
        with self._lock:
            return self._btc_iv

    @property
    def eth_iv(self) -> float:
        with self._lock:
            return self._eth_iv

    @property
    def btc_curve(self) -> List[FuturesEntry]:
        with self._lock:
            return list(self._btc_curve)

    @property
    def eth_curve(self) -> List[FuturesEntry]:
        with self._lock:
            return list(self._eth_curve)

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

    def get_spot(self, currency: str) -> float:
        currency = currency.upper()
        if currency == "BTC":
            return self.btc_spot
        elif currency == "ETH":
            return self.eth_spot
        return 0.0

    def get_iv(self, currency: str) -> float:
        currency = currency.upper()
        if currency == "BTC":
            return self.btc_iv
        elif currency == "ETH":
            return self.eth_iv
        return 0.0

    def get_curve(self, currency: str) -> List[FuturesEntry]:
        currency = currency.upper()
        if currency == "BTC":
            return self.btc_curve
        elif currency == "ETH":
            return self.eth_curve
        return []

    def drift_for_days(self, currency: str, target_days: int) -> float:
        """Interpolate drift from futures curve for a given number of days."""
        curve = self.get_curve(currency)
        if not curve:
            return 0.04  # fallback 4% annualized
        best = min(curve, key=lambda x: abs(x[0] - target_days))
        return best[1]

    # --- Data fetching ---

    def _fetch_spot(self, currency: str) -> float:
        """Fetch spot price from perpetual."""
        try:
            ticker = deribit_get(f"ticker?instrument_name={currency}-PERPETUAL")
            return ticker["last_price"]
        except Exception:
            return 0.0

    def _fetch_iv(self, currency: str) -> float:
        """Fetch ATM IV from nearest-expiry options.

        Strategy: find the nearest expiry, get the option closest to ATM,
        and use its mark_iv.
        """
        try:
            spot = self._fetch_spot(currency) if currency == "BTC" else self._eth_spot
            if spot <= 0:
                spot = self._btc_spot if currency == "BTC" else self._eth_spot
            if spot <= 0:
                return 0.0

            # Get all option instruments
            instruments = deribit_get(
                f"get_instruments?currency={currency}&kind=option&expired=false"
            )

            # Find nearest expiry
            now_ts = datetime.now(timezone.utc).timestamp() * 1000
            min_exp = float('inf')
            nearest_expiry = None
            for inst in instruments:
                exp = inst["expiration_timestamp"]
                if exp > now_ts and exp < min_exp:
                    min_exp = exp
                    nearest_expiry = exp

            if nearest_expiry is None:
                return 0.0

            # Get ATM call at nearest expiry
            best_iv = 0.0
            best_dist = float('inf')
            for inst in instruments:
                if inst["expiration_timestamp"] != nearest_expiry:
                    continue
                if inst["option_type"] != "call":
                    continue
                strike = inst["strike"]
                dist = abs(strike - spot)
                if dist < best_dist:
                    best_dist = dist
                    # Fetch ticker for this option to get mark_iv
                    try:
                        ticker = deribit_get(
                            f"ticker?instrument_name={inst['instrument_name']}"
                        )
                        iv = ticker.get("mark_iv", 0)
                        if iv > 0:
                            best_iv = iv / 100  # Convert from percentage
                            best_dist = dist
                    except Exception:
                        pass

            return best_iv
        except Exception:
            return 0.0

    def _fetch_futures_curve(self, currency: str) -> Tuple[float, List[FuturesEntry]]:
        """Fetch full futures curve: list of (days, drift, price, name)."""
        try:
            futures = deribit_get(
                f"get_book_summary_by_currency?currency={currency}&kind=future"
            )
            spot = None
            curve = []
            for f in futures:
                name = f["instrument_name"]
                if name == f"{currency}-PERPETUAL":
                    spot = f.get("last") or f.get("mark_price")

            if not spot:
                return 0.0, []

            now = datetime.now(timezone.utc)
            for f in futures:
                name = f["instrument_name"]
                price = f.get("last") or f.get("mark_price") or 0
                if not price or "PERPETUAL" in name:
                    continue
                parts = name.split("-")
                if len(parts) >= 2:
                    try:
                        exp = datetime.strptime(parts[1], "%d%b%y").replace(
                            tzinfo=timezone.utc
                        )
                        days = (exp - now).days
                        if days <= 0:
                            continue
                        T = days / 365
                        drift = math.log(price / spot) / T
                        curve.append((days, drift, price, name))
                    except ValueError:
                        pass
            curve.sort(key=lambda x: x[0])
            return spot, curve
        except Exception:
            return 0.0, []

    def update(self) -> bool:
        """Fetch all data from Deribit. Returns True on success."""
        try:
            # Fetch futures curves (includes spot)
            btc_spot, btc_curve = self._fetch_futures_curve("BTC")
            eth_spot, eth_curve = self._fetch_futures_curve("ETH")

            # Fallback spot from perpetual if futures didn't give us one
            if btc_spot <= 0:
                btc_spot = self._fetch_spot("BTC")
            if eth_spot <= 0:
                eth_spot = self._fetch_spot("ETH")

            # Fetch IV from ATM options
            # Store spots first so _fetch_iv can use them
            with self._lock:
                self._btc_spot = btc_spot
                self._eth_spot = eth_spot

            btc_iv = self._fetch_iv("BTC")
            eth_iv = self._fetch_iv("ETH")

            with self._lock:
                self._btc_curve = btc_curve
                self._eth_curve = eth_curve
                if btc_iv > 0:
                    self._btc_iv = btc_iv
                if eth_iv > 0:
                    self._eth_iv = eth_iv
                self._last_update = datetime.now(timezone.utc)

            return True
        except Exception:
            return False

    def get_snapshot(self) -> dict:
        """Get a thread-safe snapshot of all data."""
        with self._lock:
            return {
                "btc_spot": self._btc_spot,
                "eth_spot": self._eth_spot,
                "btc_iv": self._btc_iv,
                "eth_iv": self._eth_iv,
                "btc_curve": list(self._btc_curve),
                "eth_curve": list(self._eth_curve),
                "last_update": self._last_update,
            }
