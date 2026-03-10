"""
Download historical NWP forecasts + IEM actuals for weather bot calibration.

Sources:
- Open-Meteo Historical Forecast API: archived GFS, ECMWF, ICON, JMA forecasts
- IEM METAR/ASOS: same sensors as Weather Underground (Polymarket resolution source)

Output: backtest/data/hindcasts.csv + backtest/data/actuals.csv
"""

import csv
import io
import json
import os
import ssl
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
CITIES_JSON = SCRIPT_DIR.parent / "cities.json"
DATA_DIR = SCRIPT_DIR / "data"
CACHE_DIR = Path("/tmp/weather_hindcast_cache")

MODELS = ["gfs_seamless", "ecmwf_ifs025", "icon_seamless", "jma_seamless"]
HISTORICAL_API = "https://historical-forecast-api.open-meteo.com/v1/forecast"

# Date range: 2 years back from March 9 2026
START_DATE = "2024-03-01"
END_DATE = "2026-03-09"

# API chunks (Open-Meteo allows up to ~3 months per request)
CHUNK_DAYS = 90

_ctx = ssl.create_default_context()


def load_cities() -> dict:
    with open(CITIES_JSON) as f:
        return json.load(f)


def fetch_url(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    resp = urllib.request.urlopen(req, timeout=timeout, context=_ctx)
    return resp.read()


def date_range_chunks(start: str, end: str, chunk_days: int):
    """Yield (chunk_start, chunk_end) date strings."""
    s = datetime.strptime(start, "%Y-%m-%d")
    e = datetime.strptime(end, "%Y-%m-%d")
    while s <= e:
        ce = min(s + timedelta(days=chunk_days - 1), e)
        yield s.strftime("%Y-%m-%d"), ce.strftime("%Y-%m-%d")
        s = ce + timedelta(days=1)


# ─── Hindcasts (Open-Meteo Historical Forecast API) ─────────────────────

def fetch_hindcasts(cities: dict) -> list:
    """Download archived NWP forecasts for all cities.

    Returns list of dicts: {city, date, model, forecast_max, unit}
    """
    CACHE_DIR.mkdir(exist_ok=True)
    rows = []
    total_cities = len(cities)

    for ci, (slug, cfg) in enumerate(cities.items(), 1):
        lat = cfg["lat"]
        lon = cfg["lon"]
        unit_name = cfg.get("unit", "fahrenheit")
        temp_unit = "fahrenheit" if unit_name == "fahrenheit" else "celsius"
        unit_label = "F" if temp_unit == "fahrenheit" else "C"

        print(f"[{ci}/{total_cities}] {slug} ({lat}, {lon}) ...", flush=True)

        for chunk_start, chunk_end in date_range_chunks(START_DATE, END_DATE, CHUNK_DAYS):
            cache_key = f"hindcast_{slug}_{chunk_start}_{chunk_end}.json"
            cache_path = CACHE_DIR / cache_key

            if cache_path.exists():
                with open(cache_path) as f:
                    data = json.load(f)
            else:
                models_param = ",".join(MODELS)
                url = (
                    f"{HISTORICAL_API}?latitude={lat}&longitude={lon}"
                    f"&start_date={chunk_start}&end_date={chunk_end}"
                    f"&daily=temperature_2m_max&models={models_param}"
                    f"&temperature_unit={temp_unit}"
                )
                try:
                    raw = fetch_url(url)
                    data = json.loads(raw)
                    with open(cache_path, "w") as f:
                        json.dump(data, f)
                except Exception as e:
                    print(f"    ERROR {chunk_start}..{chunk_end}: {e}")
                    continue
                time.sleep(0.5)  # rate limit

            daily = data.get("daily", {})
            dates = daily.get("time", [])

            for model in MODELS:
                key = f"temperature_2m_max_{model}"
                values = daily.get(key, [])
                for i, date in enumerate(dates):
                    if i < len(values) and values[i] is not None:
                        rows.append({
                            "city": slug,
                            "date": date,
                            "model": model,
                            "forecast_max": round(values[i], 1),
                            "unit": unit_label,
                        })

        print(f"    {slug}: {sum(1 for r in rows if r['city'] == slug)} rows")

    return rows


# ─── IEM Actuals ─────────────────────────────────────────────────────────

def fetch_iem_daily(network: str, station: str, start: str, end: str) -> dict:
    """Fetch daily max temp (°F) from IEM for a date range.

    Returns {date_str: max_temp_f}.
    """
    cache_key = f"iem_daily_{station}_{start}_{end}.json"
    cache_path = CACHE_DIR / cache_key
    if cache_path.exists():
        with open(cache_path) as f:
            return json.load(f)

    s = datetime.strptime(start, "%Y-%m-%d")
    e = datetime.strptime(end, "%Y-%m-%d") + timedelta(days=1)

    url = (
        f"https://mesonet.agron.iastate.edu/cgi-bin/request/daily.py"
        f"?network={network}&stations={station}"
        f"&year1={s.year}&month1={s.month}&day1={s.day}"
        f"&year2={e.year}&month2={e.month}&day2={e.day}"
        f"&var=max_temp_f&format=csv"
    )

    raw = fetch_url(url, timeout=60)
    text = raw.decode()
    result = {}
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        try:
            d = row.get("day", "")
            t = row.get("max_temp_f", "")
            if d and t and t.strip() not in ("", "M", "None"):
                result[d] = float(t)
        except (ValueError, KeyError):
            pass

    with open(cache_path, "w") as f:
        json.dump(result, f)
    return result


def fetch_iem_hourly_max(station: str, start: str, end: str, tz: str) -> dict:
    """Fetch hourly METAR and compute daily max (°F).

    For stations without daily.py support (e.g. Wellington NZWN).
    Returns {date_str: max_temp_f}.
    """
    cache_key = f"iem_hourly_{station}_{start}_{end}.json"
    cache_path = CACHE_DIR / cache_key
    if cache_path.exists():
        with open(cache_path) as f:
            return json.load(f)

    s = datetime.strptime(start, "%Y-%m-%d")
    e = datetime.strptime(end, "%Y-%m-%d") + timedelta(days=1)

    url = (
        f"https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
        f"?station={station}&data=tmpf&tz={tz}"
        f"&format=onlycomma&report_type=3"
        f"&year1={s.year}&month1={s.month}&day1={s.day}"
        f"&year2={e.year}&month2={e.month}&day2={e.day}"
    )

    raw = fetch_url(url, timeout=60)
    text = raw.decode()

    daily_temps = {}
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        try:
            date_str = row["valid"].split(" ")[0]
            temp = float(row["tmpf"])
            if -80 < temp < 150:
                if date_str not in daily_temps:
                    daily_temps[date_str] = []
                daily_temps[date_str].append(temp)
        except (ValueError, KeyError):
            pass

    result = {d: max(temps) for d, temps in daily_temps.items() if temps}

    with open(cache_path, "w") as f:
        json.dump(result, f)
    return result


def fetch_actuals(cities: dict) -> list:
    """Download IEM actuals for all cities.

    Returns list of dicts: {city, date, actual_max, unit}
    """
    CACHE_DIR.mkdir(exist_ok=True)
    rows = []
    total = len(cities)

    for ci, (slug, cfg) in enumerate(cities.items(), 1):
        network = cfg.get("iem_network")
        station = cfg.get("iem_station")
        if not station:
            print(f"[{ci}/{total}] {slug}: no IEM station, skip")
            continue

        unit_name = cfg.get("unit", "fahrenheit")
        unit_label = "F" if unit_name == "fahrenheit" else "C"
        tz = cfg.get("timezone", "UTC")

        print(f"[{ci}/{total}] {slug} ({station}) ...", end=" ", flush=True)

        try:
            if network is None:
                # Wellington-style: hourly
                temps = fetch_iem_hourly_max(station, START_DATE, END_DATE, tz)
            else:
                temps = fetch_iem_daily(network, station, START_DATE, END_DATE)
        except Exception as e:
            print(f"ERROR: {e}")
            continue

        for date_str, temp_f in temps.items():
            if unit_name == "celsius":
                actual = round((temp_f - 32) / 1.8, 1)
            else:
                actual = round(temp_f, 1)
            rows.append({
                "city": slug,
                "date": date_str,
                "actual_max": actual,
                "unit": unit_label,
            })

        print(f"{len(temps)} days")
        time.sleep(0.3)

    return rows


# ─── Main ────────────────────────────────────────────────────────────────

def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cities = load_cities()
    print(f"Loaded {len(cities)} cities from {CITIES_JSON}\n")

    # 1. Hindcasts
    print("=" * 60)
    print("PHASE 1: Downloading NWP hindcasts from Open-Meteo")
    print("=" * 60)
    hindcast_rows = fetch_hindcasts(cities)
    hindcast_path = DATA_DIR / "hindcasts.csv"
    with open(hindcast_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["city", "date", "model", "forecast_max", "unit"])
        writer.writeheader()
        writer.writerows(hindcast_rows)
    print(f"\nSaved {len(hindcast_rows)} hindcast rows to {hindcast_path}")

    # 2. Actuals
    print("\n" + "=" * 60)
    print("PHASE 2: Downloading IEM actuals")
    print("=" * 60)
    actual_rows = fetch_actuals(cities)
    actuals_path = DATA_DIR / "actuals.csv"
    with open(actuals_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["city", "date", "actual_max", "unit"])
        writer.writeheader()
        writer.writerows(actual_rows)
    print(f"\nSaved {len(actual_rows)} actual rows to {actuals_path}")

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    h_cities = set(r["city"] for r in hindcast_rows)
    a_cities = set(r["city"] for r in actual_rows)
    print(f"Hindcasts: {len(hindcast_rows)} rows, {len(h_cities)} cities")
    print(f"Actuals:   {len(actual_rows)} rows, {len(a_cities)} cities")
    print(f"Date range: {START_DATE} to {END_DATE}")

    # Per-city counts
    for slug in sorted(cities.keys()):
        hc = sum(1 for r in hindcast_rows if r["city"] == slug)
        ac = sum(1 for r in actual_rows if r["city"] == slug)
        print(f"  {slug:15s}: {hc:5d} hindcasts, {ac:4d} actuals")


if __name__ == "__main__":
    main()
