"""
Weather actuals collector — fetches observed daily high temperatures.

Runs inside the trading bot scan loop with 6h throttle. Fetches yesterday
and day-before-yesterday for all cities.

IMPORTANT: Polymarket resolves temperature markets using Weather Underground
(wunderground.com). WU's backend is Weather.com API. We fetch actuals
directly from Weather.com API to match PM resolution exactly.

Fallback: IEM METAR hourly observations (report_type=3) in local timezone
if Weather.com API is unavailable.

Resolution source per city documented in cities.json (wu_url, wu_station).
"""

import csv
import io
import json
import logging
import ssl
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from .forecast_db import ForecastDB

logger = logging.getLogger(__name__)

_ssl_ctx = ssl.create_default_context()

# Weather.com API key (public, same as WU website uses)
_WU_API_KEY = "e1f10a1e78da46f5b10a1e78da96f525"

# Weather.com location ID format per city (ICAO:9:country)
# Maps wu_station from cities.json to Weather.com location IDs
_WU_LOCATION_MAP = {
    "KORD": "KORD:9:US",
    "KLGA": "KLGA:9:US",
    "KMIA": "KMIA:9:US",
    "KDAL": "KDAL:9:US",
    "KATL": "KATL:9:US",
    "KSEA": "KSEA:9:US",
    "CYYZ": "CYYZ:9:CA",
    "EGLC": "EGLC:9:GB",
    "LFPG": "LFPG:9:FR",
    "RKSI": "RKSI:9:KR",
    "VILK": "VILK:9:IN",
    "SAEZ": "SAEZ:9:AR",
    "SBGR": "SBGR:9:BR",
    "LTAC": "LTAC:9:TR",
    "EDDM": "EDDM:9:DE",
    "NZWN": "NZWN:9:NZ",
    "RJTT": "RJTT:9:JP",
    "LLBG": "LLBG:9:IL",
}


def _fetch_wu_daily_max(wu_station: str, date_str: str,
                        unit: str) -> Optional[float]:
    """Fetch daily max temp from Weather.com API (WU backend).

    Returns temperature in the city's native unit (°F or °C).
    This is the authoritative source matching Polymarket resolution.
    """
    location = _WU_LOCATION_MAP.get(wu_station)
    if not location:
        return None

    d = datetime.strptime(date_str, "%Y-%m-%d")
    date_fmt = d.strftime("%Y%m%d")
    units = "e" if unit == "fahrenheit" else "m"  # e=imperial, m=metric

    url = (
        f"https://api.weather.com/v1/location/{location}"
        f"/observations/historical.json"
        f"?apiKey={_WU_API_KEY}&units={units}"
        f"&startDate={date_fmt}&endDate={date_fmt}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    resp = urllib.request.urlopen(req, timeout=15, context=_ssl_ctx)
    data = json.loads(resp.read())

    temps = []
    for obs in data.get("observations", []):
        t = obs.get("temp")
        if t is not None:
            temps.append(t)

    return max(temps) if temps else None


def _fetch_iem_metar_max(station: str, date_str: str,
                         tz: str) -> Optional[float]:
    """Fallback: fetch METAR hourly max temp (°F) from IEM ASOS API.

    Uses report_type=3 (routine METAR) in local timezone.
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
    """Collects actual observed temperatures matching Polymarket resolution."""

    THROTTLE_SECONDS = 6 * 3600  # 6 hours between runs

    def __init__(self, cities: Dict[str, dict], db: ForecastDB):
        self.cities = cities
        self.db = db
        self._last_run: Optional[float] = None

    def collect_if_needed(self) -> int:
        """Collect actuals if enough time has passed since last run."""
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
            wu_station = cfg.get("wu_station")
            iem_station = cfg.get("iem_station")
            unit = cfg.get("unit", "fahrenheit")
            tz = cfg.get("timezone", "UTC")

            for date_str in dates:
                try:
                    actual_high = self._fetch_one(
                        wu_station, iem_station, date_str, unit, tz)
                    if actual_high is None:
                        continue

                    self.db.log_actual(
                        city=city_slug,
                        target_date=date_str,
                        actual_high=round(actual_high),
                        source="WU",
                        station=wu_station or iem_station,
                    )
                    count += 1
                except Exception as e:
                    logger.warning("Failed to fetch actual for %s %s: %s",
                                   city_slug, date_str, e)

        return count

    def _fetch_one(self, wu_station: Optional[str], iem_station: Optional[str],
                   date_str: str, unit: str, tz: str) -> Optional[float]:
        """Fetch daily high for one city/date. WU primary, IEM fallback."""
        # Primary: Weather.com API (matches Polymarket resolution)
        if wu_station:
            try:
                val = _fetch_wu_daily_max(wu_station, date_str, unit)
                if val is not None:
                    return val
            except Exception as e:
                logger.debug("WU API failed for %s %s: %s",
                             wu_station, date_str, e)

        # Fallback: IEM METAR
        if iem_station:
            temp_f = _fetch_iem_metar_max(iem_station, date_str, tz)
            if temp_f is not None:
                if unit == "celsius":
                    return (temp_f - 32) / 1.8
                return temp_f

        return None
