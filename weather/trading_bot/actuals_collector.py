"""
IEM METAR actuals collector — fetches observed daily high temperatures.

Runs inside the trading bot scan loop with 6h throttle. Fetches yesterday
and day-before-yesterday for all 16 cities from IEM ASOS/METAR stations
(same sensors as Weather Underground, which Polymarket uses for resolution).

Stores results via forecast_db.log_actual() into PostgreSQL `actuals` table.
"""

import csv
import io
import logging
import ssl
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from .forecast_db import ForecastDB

logger = logging.getLogger(__name__)

_ssl_ctx = ssl.create_default_context()


def _fetch_iem_daily(network: str, station: str, date_str: str) -> Optional[float]:
    """Fetch daily max temp (°F) from IEM daily.py for a single date.

    Returns max_temp_f or None if not available.
    """
    d = datetime.strptime(date_str, "%Y-%m-%d")
    end = d + timedelta(days=1)

    url = (
        f"https://mesonet.agron.iastate.edu/cgi-bin/request/daily.py"
        f"?network={network}&stations={station}"
        f"&year1={d.year}&month1={d.month}&day1={d.day}"
        f"&year2={end.year}&month2={end.month}&day2={end.day}"
        f"&var=max_temp_f&format=csv"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    resp = urllib.request.urlopen(req, timeout=30, context=_ssl_ctx)
    text = resp.read().decode()

    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        try:
            if row["day"] == date_str:
                return float(row["max_temp_f"])
        except (ValueError, KeyError):
            pass
    return None


def _fetch_iem_hourly_max(station: str, date_str: str, tz: str) -> Optional[float]:
    """Fetch hourly METAR obs and compute daily max (°F).

    Used for stations without daily.py support (e.g. Wellington NZWN).
    """
    d = datetime.strptime(date_str, "%Y-%m-%d")
    end = d + timedelta(days=1)

    url = (
        f"https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
        f"?station={station}&data=tmpf&tz={tz}"
        f"&format=onlycomma&report_type=3"
        f"&year1={d.year}&month1={d.month}&day1={d.day}"
        f"&year2={end.year}&month2={end.month}&day2={end.day}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    resp = urllib.request.urlopen(req, timeout=30, context=_ssl_ctx)
    text = resp.read().decode()

    temps = []
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        try:
            row_date = row["valid"].split(" ")[0]
            if row_date == date_str:
                temp = float(row["tmpf"])
                if -80 < temp < 150:
                    temps.append(temp)
        except (ValueError, KeyError):
            pass

    return max(temps) if temps else None


class ActualsCollector:
    """Collects actual observed temperatures from IEM METAR stations."""

    THROTTLE_SECONDS = 6 * 3600  # 6 hours between runs

    def __init__(self, cities: Dict[str, dict], db: ForecastDB):
        self.cities = cities
        self.db = db
        self._last_run: Optional[float] = None

    def collect_if_needed(self) -> int:
        """Collect actuals if enough time has passed since last run.

        Returns number of new records inserted.
        """
        now = time.time()
        if self._last_run and (now - self._last_run) < self.THROTTLE_SECONDS:
            return 0
        count = self._collect_recent()
        self._last_run = now
        return count

    def _collect_recent(self) -> int:
        """Fetch yesterday + day-before-yesterday for all cities."""
        today = datetime.now(timezone.utc).date()
        dates = [
            (today - timedelta(days=1)).isoformat(),
            (today - timedelta(days=2)).isoformat(),
        ]

        count = 0
        for city_slug, cfg in self.cities.items():
            network = cfg.get("iem_network")
            station = cfg.get("iem_station")
            if not station:
                continue

            unit = cfg.get("unit", "fahrenheit")
            tz = cfg.get("timezone", "UTC")

            for date_str in dates:
                try:
                    temp_f = self._fetch_one(network, station, date_str, tz)
                    if temp_f is None:
                        continue

                    # Convert to city's unit
                    if unit == "celsius":
                        actual_high = (temp_f - 32) / 1.8
                    else:
                        actual_high = temp_f

                    self.db.log_actual(
                        city=city_slug,
                        target_date=date_str,
                        actual_high=round(actual_high, 1),
                        source="IEM",
                        station=station,
                    )
                    count += 1
                except Exception as e:
                    logger.warning("Failed to fetch actual for %s %s: %s",
                                   city_slug, date_str, e)

        return count

    def _fetch_one(self, network: Optional[str], station: str,
                   date_str: str, tz: str) -> Optional[float]:
        """Fetch daily high (°F) for one city/date from IEM."""
        if network is None:
            # Wellington and other stations without daily.py
            return _fetch_iem_hourly_max(station, date_str, tz)
        return _fetch_iem_daily(network, station, date_str)
