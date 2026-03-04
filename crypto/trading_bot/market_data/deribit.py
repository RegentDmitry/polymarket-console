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

# (days_to_expiry, iv_decimal)
IVEntry = Tuple[int, float]


class DeribitData:
    """Fetches and caches Deribit data (spot, IV, futures curve)."""

    def __init__(self):
        self._lock = Lock()
        self._btc_spot: float = 0.0
        self._eth_spot: float = 0.0
        self._btc_iv: float = 0.0
        self._eth_iv: float = 0.0
        self._btc_iv_curve: List[IVEntry] = []
        self._eth_iv_curve: List[IVEntry] = []
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

    def iv_headline_days(self, currency: str) -> int:
        """Days to expiry of the headline IV (nearest 7d+ expiry)."""
        currency = currency.upper()
        with self._lock:
            curve = self._btc_iv_curve if currency == "BTC" else self._eth_iv_curve
            return curve[0][0] if curve else 0

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

    def iv_for_days(self, currency: str, target_days: int) -> float:
        """Get IV from the expiry closest to target_days.

        Uses per-maturity IV curve fetched from ATM options at each expiry.
        Falls back to the headline IV (7d+ nearest) if no curve available.
        """
        currency = currency.upper()
        with self._lock:
            iv_curve = list(self._btc_iv_curve if currency == "BTC" else self._eth_iv_curve)

        if not iv_curve:
            return self.get_iv(currency)

        # Find closest expiry to target_days (but at least 7 days)
        target = max(target_days, 7)
        best = min(iv_curve, key=lambda x: abs(x[0] - target))
        return best[1]

    # --- Data fetching ---

    def _fetch_spot(self, currency: str) -> float:
        """Fetch spot price from perpetual."""
        try:
            ticker = deribit_get(f"ticker?instrument_name={currency}-PERPETUAL")
            return ticker["last_price"]
        except Exception:
            return 0.0

    def _fetch_iv(self, currency: str) -> Tuple[float, List[IVEntry]]:
        """Fetch ATM IV curve from Deribit options.

        Returns (headline_iv, iv_curve) where:
        - headline_iv: IV from nearest 7d+ expiry (for display)
        - iv_curve: [(days, iv), ...] for all expiries 7d+ (for per-maturity matching)

        Short-dated (<7d) options are excluded — their IV swings 20+ pts/day.
        """
        try:
            spot = self._fetch_spot(currency) if currency == "BTC" else self._eth_spot
            if spot <= 0:
                spot = self._btc_spot if currency == "BTC" else self._eth_spot
            if spot <= 0:
                return 0.0, []

            instruments = deribit_get(
                f"get_instruments?currency={currency}&kind=option&expired=false"
            )

            now_ts = datetime.now(timezone.utc).timestamp() * 1000
            min_exp_ts = now_ts + 7 * 86400 * 1000  # at least 7 days out

            # Group instruments by expiry (only 7d+)
            expiry_groups: dict = {}
            for inst in instruments:
                exp = inst["expiration_timestamp"]
                if exp >= min_exp_ts:
                    expiry_groups.setdefault(exp, []).append(inst)

            # Fallback: if no 7d+ expiry, use absolute nearest
            if not expiry_groups:
                for inst in instruments:
                    exp = inst["expiration_timestamp"]
                    if exp > now_ts:
                        expiry_groups.setdefault(exp, []).append(inst)

            if not expiry_groups:
                return 0.0, []

            # For each expiry, find ATM call IV
            iv_curve: List[IVEntry] = []
            for exp_ts in sorted(expiry_groups.keys()):
                days = max(1, int((exp_ts - now_ts) / 86400000))
                best_iv = 0.0
                best_dist = float('inf')
                for inst in expiry_groups[exp_ts]:
                    if inst["option_type"] != "call":
                        continue
                    dist = abs(inst["strike"] - spot)
                    if dist < best_dist:
                        try:
                            ticker = deribit_get(
                                f"ticker?instrument_name={inst['instrument_name']}"
                            )
                            iv = ticker.get("mark_iv", 0)
                            if iv > 0:
                                best_iv = iv / 100
                                best_dist = dist
                        except Exception:
                            pass
                if best_iv > 0:
                    iv_curve.append((days, best_iv))

            headline_iv = iv_curve[0][1] if iv_curve else 0.0
            return headline_iv, iv_curve

        except Exception:
            return 0.0, []

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

            btc_iv, btc_iv_curve = self._fetch_iv("BTC")
            eth_iv, eth_iv_curve = self._fetch_iv("ETH")

            with self._lock:
                self._btc_curve = btc_curve
                self._eth_curve = eth_curve
                if btc_iv > 0:
                    self._btc_iv = btc_iv
                if eth_iv > 0:
                    self._eth_iv = eth_iv
                self._btc_iv_curve = btc_iv_curve
                self._eth_iv_curve = eth_iv_curve
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
                "btc_iv_days": self._btc_iv_curve[0][0] if self._btc_iv_curve else 0,
                "eth_iv_days": self._eth_iv_curve[0][0] if self._eth_iv_curve else 0,
                "btc_curve": list(self._btc_curve),
                "eth_curve": list(self._eth_curve),
                "last_update": self._last_update,
            }
