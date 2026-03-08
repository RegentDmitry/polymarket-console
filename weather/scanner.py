#!/usr/bin/env python3
"""
Live weather temperature scanner for Polymarket.

Discovers all open temperature markets, fetches ensemble forecasts from
Open-Meteo (GFS, ECMWF, ICON, JMA), computes fair bucket probabilities
via Normal distribution, and reports edge vs PM prices.

Usage:
    python3 weather/scanner.py                  # all cities
    python3 weather/scanner.py --city chicago   # one city
    python3 weather/scanner.py --min-edge 10    # filter by edge %
    python3 weather/scanner.py --json           # JSON output
"""

import argparse
import json
import re
import ssl
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy.stats import norm

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CITIES_JSON = Path(__file__).parent / "cities.json"
GAMMA_API = "https://gamma-api.polymarket.com"
OPEN_METEO_API = "https://api.open-meteo.com/v1/forecast"
ENSEMBLE_MODELS = "gfs_seamless,ecmwf_ifs025,icon_seamless,jma_seamless"

# From IEM-calibrated backtest: sqrt(σ_iem² + σ_forecast²) ≈ sqrt(1.0² + 2.3²)
SIGMA_FLOOR_F = 2.5  # °F
SIGMA_FLOOR_C = SIGMA_FLOOR_F / 1.8  # °C

MONTH_MAP = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}
MONTH_NAMES = {v: k for k, v in MONTH_MAP.items()}
MONTH_SHORT = {
    1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun",
    7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec",
}

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def fetch_json(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    resp = urllib.request.urlopen(req, timeout=timeout, context=ctx)
    return json.loads(resp.read())


# ---------------------------------------------------------------------------
# Bucket parsing (from backtest.py)
# ---------------------------------------------------------------------------

def parse_bucket_bounds(question: str):
    """Parse temperature bucket boundaries from question text.

    Returns (lower, upper, unit) where lower/upper are ints,
    upper is EXCLUSIVE for "between" and "or below" buckets.
    """
    q = question
    unit = "C" if "°C" in q else "F"

    m = re.search(r"between\s+(-?\d+)[–-](-?\d+)", q)
    if m:
        return int(m.group(1)), int(m.group(2)) + 1, unit

    m = re.search(r"be\s+(-?\d+)°C\s+on", q)
    if m:
        val = int(m.group(1))
        return val, val + 1, unit

    m = re.search(r"be\s+(-?\d+)°[FC]\s+or\s+below", q)
    if m:
        return None, int(m.group(1)) + 1, unit

    m = re.search(r"be\s+(-?\d+)°[FC]\s+or\s+higher", q)
    if m:
        return int(m.group(1)), None, unit

    return None, None, unit


def bucket_fair_price(forecast, sigma, lower, upper):
    """P(lower <= X < upper) where X ~ Normal(forecast, sigma)."""
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


def bucket_label(lower, upper, unit):
    """Short label for a bucket."""
    u = f"°{unit}"
    if lower is None:
        return f"≤{upper - 1}{u}"
    if upper is None:
        return f"≥{lower}{u}"
    if upper - lower == 1:
        return f"{lower}{u}"
    return f"{lower}-{upper - 1}{u}"


# ---------------------------------------------------------------------------
# Discovery: find all open temperature events on PM
# ---------------------------------------------------------------------------

def discover_temperature_events():
    """Fetch all open temperature events from Gamma API.

    Returns list of dicts: {slug, city, month, day, date_str, markets}
    """
    events = []
    offset = 0
    slug_pattern = re.compile(
        r"highest-temperature-in-(.+)-on-(\w+)-(\d+)"
    )

    while True:
        url = f"{GAMMA_API}/events?closed=false&limit=100&offset={offset}"
        try:
            data = fetch_json(url)
        except Exception as e:
            print(f"  Gamma API error at offset {offset}: {e}", file=sys.stderr)
            break

        if not data:
            break

        for ev in data:
            slug = ev.get("slug", "")
            m = slug_pattern.match(slug)
            if not m:
                continue

            city = m.group(1)
            month_name = m.group(2)
            day = int(m.group(3))
            month_num = MONTH_MAP.get(month_name, 0)
            if month_num == 0:
                continue

            date_str = f"2026-{month_num:02d}-{day:02d}"

            # Parse markets (buckets)
            buckets = []
            for mkt in ev.get("markets", []):
                if mkt.get("closed") or not mkt.get("active"):
                    continue
                q = mkt.get("question", "")
                try:
                    prices = json.loads(mkt.get("outcomePrices", "[]"))
                    yes_p = float(prices[0])
                except Exception:
                    continue

                lo, hi, unit = parse_bucket_bounds(q)
                if lo is None and hi is None:
                    continue

                buckets.append({
                    "question": q,
                    "lower": lo,
                    "upper": hi,
                    "unit": unit,
                    "pm_yes": yes_p,
                    "condition_id": mkt.get("conditionId", ""),
                })

            if not buckets:
                continue

            # Sort buckets by lower bound
            buckets.sort(key=lambda b: (b["lower"] if b["lower"] is not None
                                        else -9999))

            events.append({
                "slug": slug,
                "city": city,
                "month": month_num,
                "day": day,
                "date_str": date_str,
                "unit": buckets[0]["unit"],  # unit from first bucket
                "buckets": buckets,
            })

        offset += 100
        if len(data) < 100:
            break

    return events


# ---------------------------------------------------------------------------
# Forecast: Open-Meteo ensemble
# ---------------------------------------------------------------------------

def fetch_ensemble_forecast(lat, lon, unit_str, tz, forecast_days=5):
    """Fetch ensemble forecast from Open-Meteo.

    Returns dict: {date_str: {"forecast": mean_max, "sigma": std_of_maxes,
                               "models": {name: max_temp}}}
    """
    temp_unit = "fahrenheit" if unit_str == "F" else "celsius"
    url = (
        f"{OPEN_METEO_API}"
        f"?latitude={lat}&longitude={lon}"
        f"&hourly=temperature_2m"
        f"&models={ENSEMBLE_MODELS}"
        f"&temperature_unit={temp_unit}"
        f"&timezone={tz}"
        f"&forecast_days={forecast_days}"
    )

    data = fetch_json(url, timeout=30)
    hourly = data.get("hourly", {})
    times = hourly.get("time", [])

    # Parse model names from keys like "temperature_2m_gfs_seamless"
    model_keys = [k for k in hourly if k.startswith("temperature_2m_")
                  and k != "temperature_2m"]
    model_names = [k.replace("temperature_2m_", "") for k in model_keys]

    if not model_keys:
        return {}

    # Group by date, compute daily max per model
    daily = defaultdict(lambda: defaultdict(list))
    for i, t in enumerate(times):
        date = t[:10]  # "2026-03-05"
        for mk, mn in zip(model_keys, model_names):
            vals = hourly.get(mk, [])
            if i < len(vals) and vals[i] is not None:
                daily[date][mn].append(vals[i])

    result = {}
    for date, models in daily.items():
        maxes = {}
        for mn, temps in models.items():
            if temps:
                maxes[mn] = max(temps)

        if not maxes:
            continue

        vals = list(maxes.values())
        result[date] = {
            "forecast": np.mean(vals),
            "sigma": np.std(vals) if len(vals) > 1 else 0.0,
            "models": maxes,
        }

    return result


# ---------------------------------------------------------------------------
# Edge computation
# ---------------------------------------------------------------------------

def compute_edge(event, forecast_data, cities_cfg):
    """Compute edge for all buckets in an event.

    Returns updated event dict with forecast, sigma, and per-bucket fair/edge.
    """
    city = event["city"]
    date = event["date_str"]
    unit = event["unit"]

    cfg = cities_cfg.get(city)
    if not cfg:
        return None

    # Get forecast for this date
    if date not in forecast_data:
        return None

    fc = forecast_data[date]
    forecast = fc["forecast"]
    sigma_ensemble = fc["sigma"]

    # Total sigma: max of ensemble spread and calibrated floor
    sigma_floor = SIGMA_FLOOR_F if unit == "F" else SIGMA_FLOOR_C
    sigma = max(sigma_ensemble, sigma_floor)

    # Compute fair prices
    results = []
    for b in event["buckets"]:
        fair = bucket_fair_price(forecast, sigma, b["lower"], b["upper"])
        edge = fair - b["pm_yes"]
        label = bucket_label(b["lower"], b["upper"], b["unit"])

        results.append({
            **b,
            "label": label,
            "fair": fair,
            "edge": edge,
            "abs_edge": abs(edge),
        })

    return {
        **event,
        "forecast": forecast,
        "sigma": sigma,
        "sigma_ensemble": sigma_ensemble,
        "models": fc["models"],
        "buckets": results,
    }


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def print_table(events, min_edge_pct, json_output=False):
    """Print results as table or JSON."""
    # Collect all bucket-level results with edge
    rows = []
    for ev in events:
        if not ev:
            continue

        # Skip events where all buckets are 0% or 100% (already resolved)
        prices = [b["pm_yes"] for b in ev["buckets"]]
        if all(p < 0.01 or p > 0.99 for p in prices):
            continue

        for b in ev["buckets"]:
            # Skip resolved buckets
            if b["pm_yes"] < 0.01 or b["pm_yes"] > 0.99:
                continue
            if abs(b["edge"]) * 100 < min_edge_pct:
                continue
            side = "YES" if b["edge"] > 0 else "NO"
            rows.append({
                "city": ev["city"],
                "date": ev["date_str"],
                "forecast": ev["forecast"],
                "sigma": ev["sigma"],
                "sigma_ens": ev["sigma_ensemble"],
                "unit": ev["unit"],
                "bucket": b["label"],
                "side": side,
                "pm": b["pm_yes"] if side == "YES" else 1 - b["pm_yes"],
                "fair": b["fair"] if side == "YES" else 1 - b["fair"],
                "edge": abs(b["edge"]),
                "slug": ev["slug"],
                "condition_id": b.get("condition_id", ""),
            })

    rows.sort(key=lambda r: -r["edge"])

    if json_output:
        print(json.dumps(rows, indent=2))
        return

    # Header
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n{'='*85}")
    print(f"  WEATHER SCANNER — {today}")
    print(f"  Forecast: Open-Meteo ensemble ({ENSEMBLE_MODELS.replace(',', ', ')})")
    print(f"  σ model: max(ensemble_spread, {SIGMA_FLOOR_F}°F / {SIGMA_FLOOR_C:.1f}°C floor)")
    print(f"{'='*85}\n")

    if not rows:
        print("  No opportunities found above threshold.\n")
        return

    # Table
    hdr = (f"{'City':<14} {'Date':>6} {'Fcast':>7} {'σ':>5} "
           f"{'Bucket':<12} {'Side':<4} {'PM':>5} {'Fair':>5} "
           f"{'Edge':>6}  {'Verdict'}")
    print(hdr)
    print("─" * 85)

    prev_city_date = None
    for r in rows:
        u = "°" + r["unit"]
        city_date = (r["city"], r["date"])

        # Separator between different events
        if prev_city_date and prev_city_date != city_date:
            pass  # compact output

        fcast_str = f"{r['forecast']:.0f}{u}"
        sigma_str = f"{r['sigma']:.1f}{u}"
        pm_str = f"{r['pm']*100:.0f}%"
        fair_str = f"{r['fair']*100:.0f}%"
        edge_str = f"+{r['edge']*100:.0f}%"
        date_short = f"{MONTH_SHORT[int(r['date'][5:7])]} {int(r['date'][8:10])}"

        edge_pct = r["edge"] * 100
        if edge_pct >= 15:
            verdict = "*** BUY ***"
        elif edge_pct >= 10:
            verdict = "** BUY **"
        elif edge_pct >= 5:
            verdict = "buy"
        else:
            verdict = "small"

        city_name = r["city"].replace("-", " ").title()
        if len(city_name) > 13:
            city_name = city_name[:13]

        print(f"{city_name:<14} {date_short:>6} {fcast_str:>7} {sigma_str:>5} "
              f"{r['bucket']:<12} {r['side']:<4} {pm_str:>5} {fair_str:>5} "
              f"{edge_str:>6}  {verdict}")

        prev_city_date = city_date

    # Summary
    print(f"\n{'─'*85}")
    cities = len(set(r["city"] for r in rows))
    dates = len(set(r["date"] for r in rows))
    med_edge = np.median([r["edge"] for r in rows]) * 100
    big = sum(1 for r in rows if r["edge"] >= 0.10)
    print(f"  {len(rows)} opportunities across {cities} cities, {dates} dates")
    print(f"  Median edge: {med_edge:.0f}%, strong (≥10%): {big}")

    # Per-event summary
    print(f"\n  Per-event forecast summary:")
    seen = set()
    for r in rows:
        key = (r["city"], r["date"])
        if key in seen:
            continue
        seen.add(key)
        u = "°" + r["unit"]
        city_name = r["city"].replace("-", " ").title()
        date_short = f"{MONTH_SHORT[int(r['date'][5:7])]} {int(r['date'][8:10])}"
        models = []
        # Find this event in events list
        for ev in events:
            if ev and ev["city"] == r["city"] and ev["date_str"] == r["date"]:
                for mn, val in sorted(ev["models"].items()):
                    models.append(f"{mn}={val:.0f}")
                break
        model_str = ", ".join(models) if models else ""
        print(f"    {city_name} {date_short}: {r['forecast']:.1f}{u} "
              f"(σ_ens={r['sigma_ens']:.1f}{u}) [{model_str}]")

    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Weather temperature scanner")
    parser.add_argument("--city", help="Filter by city slug (e.g. chicago)")
    parser.add_argument("--min-edge", type=float, default=5,
                        help="Minimum edge %% to show (default: 5)")
    parser.add_argument("--json", action="store_true",
                        help="Output JSON instead of table")
    args = parser.parse_args()

    # Load cities config
    with open(CITIES_JSON) as f:
        cities_cfg = json.load(f)

    # Step 1: Discover open temperature events
    if not args.json:
        print("Discovering open temperature markets...", end="", flush=True)
    events = discover_temperature_events()
    if not args.json:
        print(f" found {len(events)} events")

    if args.city:
        events = [e for e in events if e["city"] == args.city]
        if not events:
            print(f"No events found for city '{args.city}'")
            return

    # Step 2: Fetch ensemble forecasts per city
    # Group events by (city, unit) to minimize API calls
    city_groups = defaultdict(list)
    for ev in events:
        city_groups[(ev["city"], ev["unit"])].append(ev)

    forecasts = {}  # (city, unit) -> {date: forecast_data}

    for (city, unit), evts in city_groups.items():
        cfg = cities_cfg.get(city)
        if not cfg:
            if not args.json:
                print(f"  {city}: not in cities.json, skipping",
                      file=sys.stderr)
            continue

        if not args.json:
            print(f"  Fetching forecast for {city} ({unit})...", end="",
                  flush=True)

        try:
            fc = fetch_ensemble_forecast(
                cfg["lat"], cfg["lon"], unit, cfg["timezone"]
            )
            forecasts[(city, unit)] = fc
            if not args.json:
                print(f" {len(fc)} days")
        except Exception as e:
            if not args.json:
                print(f" ERROR: {e}")
        time.sleep(0.2)

    # Step 3: Compute edge for each event
    results = []
    for ev in events:
        fc = forecasts.get((ev["city"], ev["unit"]))
        if not fc:
            continue
        result = compute_edge(ev, fc, cities_cfg)
        if result:
            results.append(result)

    # Step 4: Output
    print_table(results, args.min_edge, json_output=args.json)


if __name__ == "__main__":
    main()
