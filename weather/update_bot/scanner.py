"""
Weather market discovery via Gamma API.

Finds all open "highest temperature in X on Y" events,
parses buckets with token IDs, and saves to weather_markets.json.

Strategy: generates expected slugs for 16 cities × N days ahead,
fetches each by exact slug (0.1s/request) instead of paginating
through all events (5+ minutes).
"""

import json
import re
import ssl
import urllib.request
from dataclasses import dataclass, asdict
from datetime import date, timedelta
from pathlib import Path
from typing import List, Optional

GAMMA_API = "https://gamma-api.polymarket.com"
SLUG_PATTERN = re.compile(r"highest-temperature-in-(.+)-on-(\w+)-(\d+)(?:-(\d{4}))?$")

MONTH_MAP = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}
MONTH_NAMES = {v: k for k, v in MONTH_MAP.items()}

# All 16 cities as they appear in Polymarket slugs
CITIES = [
    "chicago", "new-york", "miami", "dallas", "atlanta", "seattle",
    "toronto", "london", "paris", "seoul", "lucknow", "buenos-aires",
    "sao-paulo", "ankara", "munich", "wellington",
]

_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE


def _fetch_json(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    resp = urllib.request.urlopen(req, timeout=timeout, context=_ctx)
    return json.loads(resp.read())


def _parse_bucket_bounds(question: str):
    """Parse temperature bucket boundaries from question text."""
    q = question
    unit = "C" if "\u00b0C" in q else "F"

    m = re.search(r"between\s+(-?\d+)[\u2013-](-?\d+)", q)
    if m:
        return int(m.group(1)), int(m.group(2)) + 1, unit

    m = re.search(r"be\s+(-?\d+)\u00b0C\s+on", q)
    if m:
        val = int(m.group(1))
        return val, val + 1, unit

    m = re.search(r"be\s+(-?\d+)\u00b0[FC]\s+or\s+below", q)
    if m:
        return None, int(m.group(1)) + 1, unit

    m = re.search(r"be\s+(-?\d+)\u00b0[FC]\s+or\s+higher", q)
    if m:
        return int(m.group(1)), None, unit

    return None, None, unit


def _bucket_label(lower, upper, unit):
    u = f"\u00b0{unit}"
    if lower is None:
        return f"\u2264{upper - 1}{u}"
    if upper is None:
        return f"\u2265{lower}{u}"
    if upper - lower == 1:
        return f"{lower}{u}"
    return f"{lower}-{upper - 1}{u}"


@dataclass
class WeatherMarketEntry:
    """One bucket-level market entry for weather_markets.json."""
    event_slug: str
    market_slug: str
    question: str
    city: str
    date: str          # "2026-03-07"
    unit: str          # "F" or "C"
    bucket_lower: Optional[float]
    bucket_upper: Optional[float]
    bucket_label: str
    condition_id: str
    yes_token_id: str
    no_token_id: str
    end_date: str


class WeatherMarketScanner:
    """Discovers weather temperature markets from Gamma API."""

    def search_markets(self, progress_callback=None, days_ahead=5) -> List[WeatherMarketEntry]:
        """Discover temperature markets by generating expected slugs.

        Instead of paginating through all Polymarket events (5+ minutes),
        generates expected slug patterns for 16 cities × N days and fetches
        each directly (~0.1s per request, ~10s total).
        """
        entries: List[WeatherMarketEntry] = []
        events_found = 0
        today = date.today()

        # Generate all expected slugs: 16 cities × days_ahead days
        slugs_to_check = []
        for day_offset in range(days_ahead):
            d = today + timedelta(days=day_offset)
            month_name = MONTH_NAMES[d.month]
            for city in CITIES:
                slug = f"highest-temperature-in-{city}-on-{month_name}-{d.day}-{d.year}"
                slugs_to_check.append((slug, city, d.isoformat()))

        total = len(slugs_to_check)
        if progress_callback:
            progress_callback(f"Checking {total} slugs ({len(CITIES)} cities × {days_ahead} days)...")

        for i, (slug, city, date_str) in enumerate(slugs_to_check):
            url = f"{GAMMA_API}/events?slug={slug}"
            try:
                data = _fetch_json(url)
            except Exception:
                continue

            if not data:
                continue

            ev = data[0]
            end_date = ev.get("endDate", "")
            events_found += 1

            for mkt in ev.get("markets", []):
                if mkt.get("closed") or not mkt.get("active"):
                    continue

                q = mkt.get("question", "")
                lo, hi, unit = _parse_bucket_bounds(q)
                if lo is None and hi is None:
                    continue

                # Parse token IDs
                tokens = mkt.get("tokens", [])
                yes_token = ""
                no_token = ""
                for t in tokens:
                    if t.get("outcome") == "Yes":
                        yes_token = t.get("token_id", "")
                    elif t.get("outcome") == "No":
                        no_token = t.get("token_id", "")

                # Fallback: clobTokenIds
                if not yes_token:
                    clob_raw = mkt.get("clobTokenIds", "")
                    if clob_raw:
                        try:
                            clob_ids = json.loads(clob_raw) if isinstance(clob_raw, str) else clob_raw
                            if len(clob_ids) >= 2:
                                yes_token = clob_ids[0]
                                no_token = clob_ids[1]
                            elif len(clob_ids) == 1:
                                yes_token = clob_ids[0]
                        except (json.JSONDecodeError, TypeError):
                            pass

                if not yes_token:
                    continue

                label = _bucket_label(lo, hi, unit)
                market_slug = mkt.get("market_slug", mkt.get("slug", ""))

                entries.append(WeatherMarketEntry(
                    event_slug=slug,
                    market_slug=market_slug,
                    question=q,
                    city=city,
                    date=date_str,
                    unit=unit,
                    bucket_lower=lo,
                    bucket_upper=hi,
                    bucket_label=label,
                    condition_id=mkt.get("conditionId", ""),
                    yes_token_id=yes_token,
                    no_token_id=no_token,
                    end_date=end_date,
                ))

            if progress_callback and (i + 1) % 16 == 0:
                progress_callback(f"Day {(i + 1) // 16}/{days_ahead}: {events_found} events, {len(entries)} buckets")

        if progress_callback:
            progress_callback(f"Done: {events_found} events, {len(entries)} buckets")

        return entries

    @staticmethod
    def save_to_json(entries: List[WeatherMarketEntry], path: Path) -> None:
        """Save market entries to JSON file."""
        data = [asdict(e) for e in entries]
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

    @staticmethod
    def load_from_json(path: Path) -> List[WeatherMarketEntry]:
        """Load market entries from JSON file."""
        if not path.exists():
            return []
        with open(path) as f:
            data = json.load(f)
        return [WeatherMarketEntry(**e) for e in data]
