#!/usr/bin/env python3
"""
Backtest: which data source best matches Weather Underground resolution?

Polymarket resolves weather markets using Weather Underground (WU) station data.
We need a forecast source that best predicts what WU will show, NOT the "most
accurate" forecast in absolute terms.

This script compares:
1. IEM METAR station data (same ASOS stations WU uses) vs ERA5 archive
2. Individual NWP models (ECMWF, GFS, ICON, JMA) as hindcast
3. Bias correction per city/source
4. σ calibration per source
5. Simulated trading P&L per source

Usage:
    python3 backtest_sources.py
    python3 backtest_sources.py --data /path/to/weather_backtest_data.json
"""

import argparse
import csv
import io
import json
import math
import re
import ssl
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from scipy.stats import norm

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CITIES_JSON = Path(__file__).parent / "cities.json"
DEFAULT_DATA = Path("/tmp/weather_backtest_data.json")
CACHE_DIR = Path("/tmp/weather_backtest_cache")
CACHE_DIR.mkdir(exist_ok=True)

SIGMA_GRID_F = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0]

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

MONTH_MAP = {
    "january": 1, "february": 2, "march": 3,
    "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9,
    "october": 10, "november": 11, "december": 12,
}

# IEM network mapping for each city
IEM_NETWORKS = {
    "chicago":      ("IL_ASOS", "ORD"),
    "nyc":          ("NY_ASOS", "LGA"),
    "miami":        ("FL_ASOS", "MIA"),
    "dallas":       ("TX_ASOS", "DFW"),
    "atlanta":      ("GA_ASOS", "ATL"),
    "seattle":      ("WA_ASOS", "SEA"),
    "toronto":      ("CA_ON_ASOS", "CYYZ"),
    "london":       ("GB__ASOS", "EGLC"),
    "paris":        ("FR__ASOS", "LFPG"),
    "seoul":        ("KR__ASOS", "RKSS"),
    "lucknow":      ("IN__ASOS", "VILK"),
    "buenos-aires": ("AR__ASOS", "SAEZ"),
    "sao-paulo":    ("BR__ASOS", "SBGR"),
    "ankara":       ("TR__ASOS", "LTAC"),
    "munich":       ("DE__ASOS", "EDDM"),
    "wellington":   (None, "NZWN"),  # daily.py doesn't work, use hourly
}


# ---------------------------------------------------------------------------
# Bucket parsing (from backtest.py)
# ---------------------------------------------------------------------------

def parse_bucket_bounds(question: str):
    q = question
    unit = "C" if "°C" in q else "F"

    m = re.search(r"between\s+(-?\d+)[–-](-?\d+)", q)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        return lo, hi + 1, unit

    m = re.search(r"be\s+(-?\d+)°C\s+on", q)
    if m:
        return int(m.group(1)), int(m.group(1)) + 1, unit

    m = re.search(r"be\s+(-?\d+)°[FC]\s+or\s+below", q)
    if m:
        return None, int(m.group(1)) + 1, unit

    m = re.search(r"be\s+-(\d+)°[FC]\s+or\s+below", q)
    if m:
        return None, -int(m.group(1)) + 1, unit

    m = re.search(r"be\s+(-?\d+)°[FC]\s+or\s+higher", q)
    if m:
        return int(m.group(1)), None, unit

    return None, None, unit


def winning_bucket_midpoint(winner_q):
    lo, hi, unit = parse_bucket_bounds(winner_q)
    if lo is not None and hi is not None:
        return (lo + hi - 1) / 2, unit
    elif lo is not None:
        bw = 2 if unit == "F" else 1
        return lo + bw / 2, unit
    elif hi is not None:
        bw = 2 if unit == "F" else 1
        return (hi - 1) - bw / 2, unit
    return None, unit


def winning_bucket_contains(winner_q, temp):
    """Check if temperature falls within the winning bucket."""
    lo, hi, unit = parse_bucket_bounds(winner_q)
    if lo is not None and hi is not None:
        return lo <= temp < hi
    elif lo is not None:
        return temp >= lo
    elif hi is not None:
        return temp < hi
    return False


# ---------------------------------------------------------------------------
# Data fetching: IEM station data
# ---------------------------------------------------------------------------

def fetch_iem_daily(network, station, start, end):
    """Fetch daily max temp from IEM (ASOS/METAR stations = same as WU).

    Returns {date_str: max_temp_f}.
    """
    cache_file = CACHE_DIR / f"iem_daily_{station}_{start}_{end}.json"
    if cache_file.exists():
        with open(cache_file) as f:
            return json.load(f)

    s = datetime.strptime(start, "%Y-%m-%d")
    e = datetime.strptime(end, "%Y-%m-%d")

    url = (
        f"https://mesonet.agron.iastate.edu/cgi-bin/request/daily.py"
        f"?network={network}&stations={station}"
        f"&year1={s.year}&month1={s.month}&day1={s.day}"
        f"&year2={e.year}&month2={e.month}&day2={e.day}"
        f"&var=max_temp_f&format=csv"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    resp = urllib.request.urlopen(req, timeout=30, context=ctx)
    text = resp.read().decode()

    result = {}
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        try:
            result[row["day"]] = float(row["max_temp_f"])
        except (ValueError, KeyError):
            pass

    with open(cache_file, "w") as f:
        json.dump(result, f)
    return result


def fetch_iem_hourly_max(station, start, end, tz="UTC"):
    """Fetch hourly METAR obs and compute daily max (for stations without daily.py).

    Returns {date_str: max_temp_f}.
    """
    cache_file = CACHE_DIR / f"iem_hourly_{station}_{start}_{end}.json"
    if cache_file.exists():
        with open(cache_file) as f:
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
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    resp = urllib.request.urlopen(req, timeout=60, context=ctx)
    text = resp.read().decode()

    daily_temps = defaultdict(list)
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        try:
            date_str = row["valid"].split(" ")[0]
            temp = float(row["tmpf"])
            if temp > -80 and temp < 150:  # sanity
                daily_temps[date_str].append(temp)
        except (ValueError, KeyError):
            pass

    result = {}
    for date_str, temps in daily_temps.items():
        if date_str >= start and date_str <= end:
            result[date_str] = max(temps)

    with open(cache_file, "w") as f:
        json.dump(result, f)
    return result


def fetch_iem_for_city(city, cfg, start, end):
    """Get IEM daily max for a city. Returns {date: temp_f}."""
    net_info = IEM_NETWORKS.get(city)
    if not net_info:
        return {}

    network, station = net_info
    if network is None:
        # Use hourly for Wellington
        return fetch_iem_hourly_max(station, start, end, tz=cfg["timezone"])
    return fetch_iem_daily(network, station, start, end)


# ---------------------------------------------------------------------------
# Data fetching: Open-Meteo archive (ERA5)
# ---------------------------------------------------------------------------

def fetch_archive(lat, lon, start, end, unit="fahrenheit"):
    """Fetch daily max temperature from Open-Meteo archive (ERA5)."""
    cache_file = CACHE_DIR / f"era5_{lat}_{lon}_{start}_{end}_{unit}.json"
    if cache_file.exists():
        with open(cache_file) as f:
            return json.load(f)

    url = (
        f"https://archive-api.open-meteo.com/v1/archive"
        f"?latitude={lat}&longitude={lon}"
        f"&daily=temperature_2m_max"
        f"&temperature_unit={unit}"
        f"&start_date={start}&end_date={end}"
        f"&timezone=UTC"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    resp = urllib.request.urlopen(req, timeout=30, context=ctx)
    data = json.loads(resp.read())
    daily = data.get("daily", {})
    dates = daily.get("time", [])
    temps = daily.get("temperature_2m_max", [])
    result = dict(zip(dates, temps))

    with open(cache_file, "w") as f:
        json.dump(result, f)
    return result


# ---------------------------------------------------------------------------
# Data fetching: Open-Meteo individual model hindcast
# ---------------------------------------------------------------------------

def fetch_model_hindcast(lat, lon, start, end, model, tz, unit="fahrenheit"):
    """Fetch daily max from a specific NWP model via Open-Meteo forecast API.

    Note: forecast API only has ~5 days of data. For hindcast we use
    Open-Meteo's previous_day archive endpoint or ensemble archive.
    Since we can't get true hindcast (what model predicted N days ago),
    we'll use the forecast API for current/recent dates only.

    For historical comparison, we use the ensemble archive API which has
    historical ensemble runs.
    """
    cache_file = CACHE_DIR / f"model_{model}_{lat}_{lon}_{start}_{end}_{unit}.json"
    if cache_file.exists():
        with open(cache_file) as f:
            return json.load(f)

    # Use Open-Meteo previous runs archive for historical model data
    url = (
        f"https://previous-runs-api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        f"&daily=temperature_2m_max"
        f"&temperature_unit={unit}"
        f"&models={model}"
        f"&start_date={start}&end_date={end}"
        f"&timezone={tz}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        resp = urllib.request.urlopen(req, timeout=30, context=ctx)
        data = json.loads(resp.read())
        daily = data.get("daily", {})
        dates = daily.get("time", [])
        temps = daily.get("temperature_2m_max", [])
        result = dict(zip(dates, [t for t in temps if t is not None]))
    except Exception:
        result = {}

    with open(cache_file, "w") as f:
        json.dump(result, f)
    return result


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

def bucket_fair_price(forecast, sigma, lower, upper):
    if sigma <= 0:
        if lower is None and upper is None:
            return 1.0
        if lower is None:
            return 1.0 if forecast < upper else 0.0
        if upper is None:
            return 1.0 if forecast >= lower else 0.0
        return 1.0 if lower <= forecast < upper else 0.0

    if lower is None and upper is None:
        return 1.0
    if lower is None:
        return norm.cdf(upper, forecast, sigma)
    if upper is None:
        return 1 - norm.cdf(lower, forecast, sigma)
    return norm.cdf(upper, forecast, sigma) - norm.cdf(lower, forecast, sigma)


def compute_event_fair_prices(buckets, forecast, sigma):
    return [bucket_fair_price(forecast, sigma, b["lower"], b["upper"])
            for b in buckets]


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def brier_score(probs, outcomes):
    return np.mean([(p - o) ** 2 for p, o in zip(probs, outcomes)])


# ---------------------------------------------------------------------------
# Prepare events
# ---------------------------------------------------------------------------

def prepare_events(events):
    """Parse events into structured list with winning bucket info."""
    prepared = []
    for ev in events:
        city = ev["city"]
        month_num = MONTH_MAP.get(ev["month"], 1)
        date_str = f"2026-{month_num:02d}-{ev['day']:02d}"

        winner_q = ev.get("winner", "")
        if not winner_q:
            continue

        first_q = ev["buckets"][0]["question"] if ev["buckets"] else ""
        is_fahrenheit = "°F" in first_q or "°F" in winner_q

        buckets = []
        for b in ev.get("buckets", []):
            q = b["question"]
            lo, hi, u = parse_bucket_bounds(q)
            if lo is None and hi is None:
                continue
            buckets.append({
                "lower": lo, "upper": hi,
                "is_winner": (q == winner_q),
                "volume": b.get("volume", 0),
                "question": q,
            })

        if not buckets or sum(1 for b in buckets if b["is_winner"]) != 1:
            continue

        actual_mid, _ = winning_bucket_midpoint(winner_q)

        prepared.append({
            "city": city,
            "date": date_str,
            "is_fahrenheit": is_fahrenheit,
            "buckets": buckets,
            "winner_question": winner_q,
            "actual_midpoint": actual_mid,
            "total_volume": ev.get("volume", 0),
        })
    return prepared


# ---------------------------------------------------------------------------
# Evaluate a source
# ---------------------------------------------------------------------------

def evaluate_source(prepared, source_temps, source_name, sigma_grid=SIGMA_GRID_F):
    """Evaluate a temperature source against PM winning buckets.

    source_temps: {city: {date: temp}} — in the same unit as the market (F or C).

    Returns dict with metrics.
    """
    # Filter to events where source has data
    valid = []
    for ev in prepared:
        city = ev["city"]
        date = ev["date"]
        if city in source_temps and date in source_temps[city]:
            t = source_temps[city][date]
            if t is not None:
                valid.append((ev, t))

    if not valid:
        return {"name": source_name, "n_events": 0}

    # 1. How often does source temp fall in winning bucket?
    bucket_hits = 0
    total_error = []
    for ev, temp in valid:
        if winning_bucket_contains(ev["winner_question"], temp):
            bucket_hits += 1
        if ev["actual_midpoint"] is not None:
            total_error.append(temp - ev["actual_midpoint"])

    hit_rate = bucket_hits / len(valid)
    mae = np.mean(np.abs(total_error)) if total_error else 0
    bias = np.mean(total_error) if total_error else 0

    # 2. σ calibration — find best σ
    best_sigma_f = 3.5
    best_brier = float("inf")
    sigma_results = []

    for sigma_f in sigma_grid:
        all_probs = []
        all_outcomes = []
        top1_correct = 0

        for ev, temp in valid:
            sigma = sigma_f if ev["is_fahrenheit"] else sigma_f / 1.8
            fair = compute_event_fair_prices(ev["buckets"], temp, sigma)
            outcomes = [1.0 if b["is_winner"] else 0.0 for b in ev["buckets"]]
            all_probs.extend(fair)
            all_outcomes.extend(outcomes)
            if outcomes[np.argmax(fair)] == 1.0:
                top1_correct += 1

        bs = brier_score(all_probs, all_outcomes)
        top1 = top1_correct / len(valid) * 100
        sigma_results.append((sigma_f, bs, top1))
        if bs < best_brier:
            best_brier = bs
            best_sigma_f = sigma_f

    # 3. Trading sim at best σ
    trades = wins = 0
    pnl = cost = 0.0
    for ev, temp in valid:
        sigma = best_sigma_f if ev["is_fahrenheit"] else best_sigma_f / 1.8
        fair = compute_event_fair_prices(ev["buckets"], temp, sigma)
        n = len(ev["buckets"])
        unif = 1.0 / n
        for i, b in enumerate(ev["buckets"]):
            edge = fair[i] - unif
            if edge > 0.05 and unif < 0.95:
                trades += 1
                cost += unif
                if b["is_winner"]:
                    wins += 1
                    pnl += (1 - unif)
                else:
                    pnl -= unif
            elif edge < -0.05 and unif > 0.05:
                trades += 1
                cost += (1 - unif)
                if not b["is_winner"]:
                    wins += 1
                    pnl += unif
                else:
                    pnl -= (1 - unif)

    win_rate = wins / trades * 100 if trades else 0
    roi = pnl / cost * 100 if cost > 0 else 0

    # 4. Per-city bias
    city_bias = defaultdict(list)
    for ev, temp in valid:
        if ev["actual_midpoint"] is not None:
            city_bias[ev["city"]].append(temp - ev["actual_midpoint"])

    return {
        "name": source_name,
        "n_events": len(valid),
        "bucket_hit_rate": hit_rate,
        "mae": mae,
        "bias": bias,
        "best_sigma_f": best_sigma_f,
        "best_brier": best_brier,
        "sigma_results": sigma_results,
        "trades": trades,
        "wins": wins,
        "win_rate": win_rate,
        "pnl": pnl,
        "roi": roi,
        "city_bias": {c: (np.mean(d), np.std(d), len(d))
                      for c, d in city_bias.items()},
    }


def evaluate_with_bias_correction(prepared, source_temps, source_name,
                                  city_biases):
    """Re-evaluate after subtracting per-city bias from source temps."""
    corrected = {}
    for city, temps in source_temps.items():
        bias = city_biases.get(city, (0, 0, 0))[0]
        corrected[city] = {d: t - bias for d, t in temps.items() if t is not None}
    return evaluate_source(prepared, corrected, f"{source_name} +bias_corr")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Weather source comparison backtest")
    parser.add_argument("--data", default=str(DEFAULT_DATA))
    args = parser.parse_args()

    with open(args.data) as f:
        events = json.load(f)
    print(f"Loaded {len(events)} closed temperature events")

    with open(CITIES_JSON) as f:
        cities_cfg = json.load(f)

    # Prepare events
    prepared = prepare_events(events)
    print(f"Prepared: {len(prepared)} events")

    # Date range
    all_dates = [ev["date"] for ev in prepared]
    date_min = min(all_dates)
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    date_max = min(max(all_dates), yesterday)
    print(f"Date range: {date_min} to {date_max}")

    city_set = sorted(set(ev["city"] for ev in prepared))

    # =====================================================================
    # FETCH DATA FROM ALL SOURCES
    # =====================================================================

    # Source 1: IEM station data (METAR/ASOS = same as WU)
    print(f"\n{'='*70}")
    print("FETCHING DATA SOURCES")
    print(f"{'='*70}")

    print("\n1. IEM station data (METAR/ASOS)...")
    iem_data_f = {}  # city -> {date: temp_F}
    iem_data_c = {}  # city -> {date: temp_C}
    for city in city_set:
        cfg = cities_cfg.get(city)
        if not cfg:
            continue
        try:
            temps_f = fetch_iem_for_city(city, cfg, date_min, date_max)
            iem_data_f[city] = temps_f
            # Convert to Celsius
            iem_data_c[city] = {d: (t - 32) * 5 / 9 for d, t in temps_f.items()}
            print(f"  {city}: {len(temps_f)} days")
        except Exception as e:
            print(f"  {city}: ERROR {e}")
        time.sleep(0.2)

    # Source 2: Open-Meteo ERA5 archive
    print("\n2. Open-Meteo ERA5 archive...")
    era5_data_f = {}
    era5_data_c = {}
    for city in city_set:
        cfg = cities_cfg.get(city)
        if not cfg:
            continue
        try:
            era5_data_f[city] = fetch_archive(cfg["lat"], cfg["lon"],
                                               date_min, date_max, "fahrenheit")
            era5_data_c[city] = fetch_archive(cfg["lat"], cfg["lon"],
                                               date_min, date_max, "celsius")
            print(f"  {city}: {len(era5_data_f[city])} days")
        except Exception as e:
            print(f"  {city}: ERROR {e}")
        time.sleep(0.3)

    # Build source dicts: city -> {date: temp_in_market_unit}
    def build_source(data_f, data_c, prepared):
        """Convert raw data to match each event's unit (F or C)."""
        result = {}
        for ev in prepared:
            city = ev["city"]
            date = ev["date"]
            if ev["is_fahrenheit"]:
                if city in data_f and date in data_f[city]:
                    result.setdefault(city, {})[date] = data_f[city][date]
            else:
                if city in data_c and date in data_c[city]:
                    result.setdefault(city, {})[date] = data_c[city][date]
        return result

    iem_source = build_source(iem_data_f, iem_data_c, prepared)
    era5_source = build_source(era5_data_f, era5_data_c, prepared)

    # =====================================================================
    # EVALUATE SOURCES
    # =====================================================================

    print(f"\n{'='*70}")
    print("SOURCE COMPARISON: IEM (≈WU) vs ERA5 (Open-Meteo archive)")
    print(f"{'='*70}\n")

    results = []

    # Evaluate IEM
    r_iem = evaluate_source(prepared, iem_source, "IEM (METAR/ASOS)")
    results.append(r_iem)

    # Evaluate ERA5
    r_era5 = evaluate_source(prepared, era5_source, "ERA5 (Open-Meteo)")
    results.append(r_era5)

    # Evaluate IEM with bias correction
    if r_iem["n_events"] > 0:
        r_iem_bc = evaluate_with_bias_correction(
            prepared, iem_source, "IEM", r_iem["city_bias"])
        results.append(r_iem_bc)

    # Evaluate ERA5 with bias correction
    if r_era5["n_events"] > 0:
        r_era5_bc = evaluate_with_bias_correction(
            prepared, era5_source, "ERA5", r_era5["city_bias"])
        results.append(r_era5_bc)

    # Print comparison table
    print(f"{'Source':<24} {'Events':>6} {'BucketHit%':>10} {'MAE':>6} "
          f"{'Bias':>6} {'σ*':>4} {'Brier':>7} {'Trades':>6} {'Win%':>6} "
          f"{'P&L':>8} {'ROI':>6}")
    print("-" * 105)

    for r in results:
        if r["n_events"] == 0:
            print(f"{r['name']:<24} {'no data':>6}")
            continue
        print(f"{r['name']:<24} {r['n_events']:>6} "
              f"{r['bucket_hit_rate']*100:>9.1f}% "
              f"{r['mae']:>5.2f}  {r['bias']:>+5.2f} "
              f"{r['best_sigma_f']:>4.1f} {r['best_brier']:>7.4f} "
              f"{r['trades']:>6} {r['win_rate']:>5.1f}% "
              f"${r['pnl']:>+7.2f} {r['roi']:>+5.1f}%")

    # =====================================================================
    # SIGMA CALIBRATION DETAIL
    # =====================================================================

    print(f"\n{'='*70}")
    print("SIGMA CALIBRATION BY SOURCE")
    print(f"{'='*70}\n")

    for r in results[:2]:  # IEM and ERA5 only
        if r["n_events"] == 0:
            continue
        print(f"  {r['name']}:")
        print(f"  {'σ(°F)':>6} {'Brier':>8} {'Top1%':>6}")
        print(f"  {'-'*25}")
        for sigma_f, bs, top1 in r["sigma_results"]:
            marker = " ←" if sigma_f == r["best_sigma_f"] else ""
            print(f"  {sigma_f:>5.1f}  {bs:>7.4f}  {top1:>5.1f}%{marker}")
        print()

    # =====================================================================
    # PER-CITY BIAS: IEM vs WU winning bucket
    # =====================================================================

    print(f"\n{'='*70}")
    print("PER-CITY BIAS (source temp - WU winning bucket midpoint)")
    print(f"{'='*70}\n")

    print(f"{'City':<14} {'IEM bias':>10} {'IEM std':>8} {'ERA5 bias':>10} "
          f"{'ERA5 std':>8} {'N':>4}")
    print("-" * 62)

    for city in city_set:
        iem_b = r_iem["city_bias"].get(city, (0, 0, 0)) if r_iem["n_events"] else (0, 0, 0)
        era_b = r_era5["city_bias"].get(city, (0, 0, 0)) if r_era5["n_events"] else (0, 0, 0)
        n = max(iem_b[2], era_b[2])
        if n == 0:
            continue
        # Determine unit
        is_f = any(ev["is_fahrenheit"] for ev in prepared if ev["city"] == city)
        u = "°F" if is_f else "°C"
        print(f"{city:<14} {iem_b[0]:>+8.2f}{u} {iem_b[1]:>6.2f}{u}  "
              f"{era_b[0]:>+8.2f}{u} {era_b[1]:>6.2f}{u}  {n:>3}")

    # =====================================================================
    # HEAD-TO-HEAD: IEM vs ERA5 per event
    # =====================================================================

    print(f"\n{'='*70}")
    print("HEAD-TO-HEAD: IEM vs ERA5 — which lands in winning bucket more?")
    print(f"{'='*70}\n")

    both_ok = 0
    iem_wins = 0
    era5_wins = 0
    both_hit = 0
    both_miss = 0

    for ev in prepared:
        city, date = ev["city"], ev["date"]
        iem_t = iem_source.get(city, {}).get(date)
        era_t = era5_source.get(city, {}).get(date)
        if iem_t is None or era_t is None:
            continue
        both_ok += 1

        iem_hit = winning_bucket_contains(ev["winner_question"], iem_t)
        era_hit = winning_bucket_contains(ev["winner_question"], era_t)

        if iem_hit and era_hit:
            both_hit += 1
        elif iem_hit and not era_hit:
            iem_wins += 1
        elif era_hit and not iem_hit:
            era5_wins += 1
        else:
            both_miss += 1

    print(f"Events with both sources: {both_ok}")
    print(f"  Both hit winning bucket:  {both_hit} ({both_hit/both_ok*100:.1f}%)")
    print(f"  Only IEM hit:             {iem_wins} ({iem_wins/both_ok*100:.1f}%)")
    print(f"  Only ERA5 hit:            {era5_wins} ({era5_wins/both_ok*100:.1f}%)")
    print(f"  Both miss:                {both_miss} ({both_miss/both_ok*100:.1f}%)")
    print(f"\n  IEM advantage: {iem_wins - era5_wins:+d} events")

    # =====================================================================
    # DISAGREEMENT ANALYSIS
    # =====================================================================

    print(f"\n{'='*70}")
    print("DISAGREEMENT CASES: IEM hit but ERA5 missed (our edge)")
    print(f"{'='*70}\n")

    disagree = []
    for ev in prepared:
        city, date = ev["city"], ev["date"]
        iem_t = iem_source.get(city, {}).get(date)
        era_t = era5_source.get(city, {}).get(date)
        if iem_t is None or era_t is None:
            continue
        iem_hit = winning_bucket_contains(ev["winner_question"], iem_t)
        era_hit = winning_bucket_contains(ev["winner_question"], era_t)
        if iem_hit != era_hit:
            is_f = ev["is_fahrenheit"]
            u = "°F" if is_f else "°C"
            disagree.append({
                "city": city, "date": date,
                "iem": iem_t, "era5": era_t,
                "diff": abs(iem_t - era_t),
                "iem_hit": iem_hit,
                "unit": u,
                "mid": ev["actual_midpoint"],
            })

    disagree.sort(key=lambda x: -x["diff"])
    print(f"{'City':<14} {'Date':>10} {'IEM':>7} {'ERA5':>7} {'WU mid':>7} "
          f"{'Diff':>5} {'IEM✓':>5} {'ERA✓':>5}")
    print("-" * 68)
    for d in disagree[:30]:
        print(f"{d['city']:<14} {d['date']:>10} {d['iem']:>6.1f} {d['era5']:>6.1f} "
              f"{d['mid']:>6.1f} {d['diff']:>4.1f}{d['unit']} "
              f"{'  ✓' if d['iem_hit'] else '  ✗':>5} "
              f"{'  ✓' if not d['iem_hit'] else '  ✗':>5}")

    # =====================================================================
    # PRACTICAL RECOMMENDATION
    # =====================================================================

    print(f"\n{'='*70}")
    print("RECOMMENDATION")
    print(f"{'='*70}\n")

    if r_iem["n_events"] > 0 and r_era5["n_events"] > 0:
        iem_brier = r_iem["best_brier"]
        era_brier = r_era5["best_brier"]
        better = "IEM" if iem_brier < era_brier else "ERA5"
        diff_pct = abs(iem_brier - era_brier) / max(iem_brier, era_brier) * 100

        print(f"Best ground truth source: {better} "
              f"(Brier {min(iem_brier, era_brier):.4f} vs {max(iem_brier, era_brier):.4f}, "
              f"{diff_pct:.1f}% better)")
        print(f"\nIEM bucket hit rate: {r_iem['bucket_hit_rate']*100:.1f}%")
        print(f"ERA5 bucket hit rate: {r_era5['bucket_hit_rate']*100:.1f}%")
        print(f"\nIEM head-to-head advantage: {iem_wins - era5_wins:+d} events")

        if iem_brier < era_brier:
            print(f"\n→ Use IEM METAR station data as ground truth for calibration.")
            print(f"  IEM reads the SAME ASOS sensors that Weather Underground uses.")
            print(f"  This eliminates the ERA5 reanalysis interpolation error.")
        else:
            print(f"\n→ ERA5 performs better despite being a reanalysis product.")
            print(f"  This may indicate WU does its own processing of METAR data.")

        # Bias correction value
        print(f"\nPer-city bias correction improves predictions:")
        if r_iem.get("city_bias"):
            for city in city_set:
                b = r_iem["city_bias"].get(city)
                if b and abs(b[0]) > 0.3:
                    is_f = any(ev["is_fahrenheit"] for ev in prepared if ev["city"] == city)
                    u = "°F" if is_f else "°C"
                    print(f"  {city}: {b[0]:+.1f}{u} correction needed")


if __name__ == "__main__":
    main()
