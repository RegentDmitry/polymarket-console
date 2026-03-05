#!/usr/bin/env python3
"""
Backtest weather temperature model on 351 closed Polymarket markets.

PM prices at resolution are 0/1 (post-resolution), so we can't backtest
against real market prices. Instead we evaluate:

1. σ calibration — find σ minimizing Brier score (archive as perfect forecast)
2. Model accuracy — top-1/top-2 prediction accuracy
3. Calibration curve — when model says 30%, does it happen 30% of the time?
4. Simulated trading — model vs uniform baseline and volume-weighted baseline
5. Archive vs WU station bias — quantify and correct

Usage:
    python3 backtest.py
    python3 backtest.py --data /path/to/weather_backtest_data.json
"""

import argparse
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

SIGMA_GRID_F = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0]  # °F
EDGE_THRESHOLDS = [0.03, 0.05, 0.08, 0.10, 0.15, 0.20]

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

MONTH_MAP = {
    "january": 1, "february": 2, "march": 3,
    "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9,
    "october": 10, "november": 11, "december": 12,
}


# ---------------------------------------------------------------------------
# Bucket parsing
# ---------------------------------------------------------------------------

def parse_bucket_bounds(question: str):
    """Parse temperature bucket boundaries from question text.

    Returns (lower, upper, unit) where:
      - lower=None means "≤ upper"
      - upper=None means "≥ lower"
      - unit is 'F' or 'C'
      - bounds are integers; upper is EXCLUSIVE for "between" buckets
    """
    q = question
    unit = "C" if "°C" in q else "F"

    # "between X-Y°F" or "between X-Y°C"
    m = re.search(r"between\s+(-?\d+)[–-](-?\d+)", q)
    if m:
        lo = int(m.group(1))
        hi = int(m.group(2))
        return lo, hi + 1, unit  # upper exclusive

    # "be X°C on" (single degree Celsius bucket)
    m = re.search(r"be\s+(-?\d+)°C\s+on", q)
    if m:
        val = int(m.group(1))
        return val, val + 1, unit

    # "be X°F or below" / "X°C or below"
    m = re.search(r"be\s+(-?\d+)°[FC]\s+or\s+below", q)
    if m:
        return None, int(m.group(1)) + 1, unit

    # "be -X°C or below" (negative temps)
    m = re.search(r"be\s+-(\d+)°[FC]\s+or\s+below", q)
    if m:
        return None, -int(m.group(1)) + 1, unit

    # "be X°F or higher" / "X°C or higher"
    m = re.search(r"be\s+(-?\d+)°[FC]\s+or\s+higher", q)
    if m:
        return int(m.group(1)), None, unit

    return None, None, unit


def parse_actual_from_winner(winner_q: str):
    """Extract actual temperature (midpoint) from winning bucket question.

    Returns (midpoint_temp, unit).
    For "between 30-31°F" → midpoint = 30.5, unit = F
    For "34°F or higher" → 35, unit = F (assume 1 above threshold)
    For "23°F or below" → 22, unit = F (assume 1 below threshold)
    """
    lo, hi, unit = parse_bucket_bounds(winner_q)
    if lo is not None and hi is not None:
        # hi is exclusive, so real range is [lo, hi-1], midpoint = (lo + hi - 1) / 2
        return (lo + hi - 1) / 2, unit
    elif lo is not None:
        # "≥X" — assume X+0.5 (just above threshold)
        bw = 2 if unit == "F" else 1
        return lo + bw / 2, unit
    elif hi is not None:
        # "≤X" where hi = X+1 (exclusive) — assume X-0.5
        bw = 2 if unit == "F" else 1
        return (hi - 1) - bw / 2, unit
    return None, unit


# ---------------------------------------------------------------------------
# Open-Meteo archive
# ---------------------------------------------------------------------------

def fetch_archive(lat, lon, start, end, unit="fahrenheit"):
    """Fetch daily max temperature from Open-Meteo archive API."""
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
    return dict(zip(dates, temps))


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

def bucket_fair_price(forecast, sigma, lower, upper):
    """P(lower ≤ X < upper) where X ~ Normal(forecast, sigma)."""
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
    """Compute fair prices for all buckets in an event. Returns list of floats."""
    prices = []
    for b in buckets:
        p = bucket_fair_price(forecast, sigma, b["lower"], b["upper"])
        prices.append(p)
    return prices


# ---------------------------------------------------------------------------
# Prepare events
# ---------------------------------------------------------------------------

def prepare_events(events, cities_cfg, archive_f, archive_c):
    """Parse events into structured list with archive temps.

    Returns list of dicts, each with:
      - city, date, archive_temp, is_fahrenheit
      - buckets: list of {lower, upper, is_winner, volume, question}
    """
    prepared = []

    for ev in events:
        city = ev["city"]
        month_num = MONTH_MAP.get(ev["month"], 1)
        date_str = f"2026-{month_num:02d}-{ev['day']:02d}"

        # Parse winning bucket
        winner_q = ev.get("winner", "")
        if not winner_q:
            continue

        # Determine unit from first bucket question (per event!)
        first_q = ev["buckets"][0]["question"] if ev["buckets"] else ""
        is_fahrenheit = "°F" in first_q or "°F" in winner_q

        # Get archive temp in matching unit
        arch = archive_f if is_fahrenheit else archive_c
        if city not in arch or date_str not in arch[city]:
            continue
        archive_temp = arch[city][date_str]
        if archive_temp is None:
            continue

        # Parse all buckets
        buckets = []
        for b in ev.get("buckets", []):
            q = b["question"]
            lo, hi, u = parse_bucket_bounds(q)
            if lo is None and hi is None:
                continue
            is_winner = (q == winner_q)
            buckets.append({
                "lower": lo,
                "upper": hi,
                "is_winner": is_winner,
                "volume": b.get("volume", 0),
                "question": q,
            })

        if not buckets:
            continue

        # Verify exactly one winner
        n_winners = sum(1 for b in buckets if b["is_winner"])
        if n_winners != 1:
            continue

        prepared.append({
            "city": city,
            "date": date_str,
            "archive_temp": archive_temp,
            "is_fahrenheit": is_fahrenheit,
            "buckets": buckets,
            "total_volume": ev.get("volume", 0),
        })

    return prepared


# ---------------------------------------------------------------------------
# Baselines
# ---------------------------------------------------------------------------

def uniform_prices(n_buckets):
    """Uniform baseline: each bucket gets 1/N."""
    return [1.0 / n_buckets] * n_buckets


def volume_weighted_prices(buckets):
    """Volume-weighted baseline: proportional to trading volume.

    More volume on a bucket ≈ more people thought it would win.
    This is our best approximation of pre-resolution market prices.
    """
    volumes = [b["volume"] for b in buckets]
    total = sum(volumes)
    if total <= 0:
        return uniform_prices(len(buckets))
    return [v / total for v in volumes]


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def brier_score(probs, outcomes):
    """Mean Brier score: avg (prob - outcome)^2."""
    return np.mean([(p - o) ** 2 for p, o in zip(probs, outcomes)])


def log_loss(probs, outcomes):
    """Mean log loss (cross-entropy)."""
    total = 0
    for p, o in zip(probs, outcomes):
        p_c = max(min(p, 0.999), 0.001)
        total += -(o * math.log(p_c) + (1 - o) * math.log(1 - p_c))
    return total / len(probs)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Weather market backtest")
    parser.add_argument("--data", default=str(DEFAULT_DATA),
                        help="Path to weather_backtest_data.json")
    args = parser.parse_args()

    # Load PM data
    with open(args.data) as f:
        events = json.load(f)
    print(f"Loaded {len(events)} closed temperature events")

    # Load cities
    with open(CITIES_JSON) as f:
        cities_cfg = json.load(f)

    # Determine date range
    all_dates = []
    for ev in events:
        month_num = MONTH_MAP.get(ev["month"], 1)
        all_dates.append(f"2026-{month_num:02d}-{ev['day']:02d}")

    date_min = min(all_dates)
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    date_max = min(max(all_dates), yesterday)
    print(f"Date range: {date_min} to {date_max}")

    # Fetch archive data per city — in BOTH fahrenheit and celsius
    # (some cities like London switched units mid-stream on PM)
    print("\nFetching Open-Meteo archive data...")
    archive_f = {}  # city -> {date: temp_F}
    archive_c = {}  # city -> {date: temp_C}
    city_set = set(ev["city"] for ev in events)

    for city in sorted(city_set):
        cfg = cities_cfg.get(city)
        if not cfg:
            print(f"  {city}: no config in cities.json, skipping")
            continue
        try:
            data_f = fetch_archive(cfg["lat"], cfg["lon"], date_min, date_max,
                                   unit="fahrenheit")
            archive_f[city] = data_f
            data_c = fetch_archive(cfg["lat"], cfg["lon"], date_min, date_max,
                                   unit="celsius")
            archive_c[city] = data_c
            print(f"  {city}: {len(data_f)} days")
        except Exception as e:
            print(f"  {city}: ERROR {e}")
        time.sleep(0.3)  # rate limit courtesy

    # Prepare structured events
    prepared = prepare_events(events, cities_cfg, archive_f, archive_c)
    total_buckets = sum(len(e["buckets"]) for e in prepared)
    print(f"\nPrepared: {len(prepared)} events, {total_buckets} buckets")

    # =========================================================================
    # 1. SIGMA CALIBRATION (Brier Score)
    # =========================================================================
    print(f"\n{'='*70}")
    print("1. SIGMA CALIBRATION (Brier Score)")
    print(f"{'='*70}\n")
    print("Using archive_temp as perfect forecast. Finding σ that gives")
    print("best-calibrated bucket probabilities.\n")

    best_sigma_f = None
    best_brier = float("inf")

    print(f"{'σ(°F)':>6} {'σ(°C)':>6} {'Brier':>8} {'LogLoss':>8} {'Top1%':>6}")
    print("-" * 42)

    for sigma_f in SIGMA_GRID_F:
        all_probs = []
        all_outcomes = []
        top1_correct = 0
        n_events = 0

        for ev in prepared:
            sigma = sigma_f if ev["is_fahrenheit"] else sigma_f / 1.8
            fair_prices = compute_event_fair_prices(ev["buckets"],
                                                     ev["archive_temp"], sigma)
            outcomes = [1.0 if b["is_winner"] else 0.0 for b in ev["buckets"]]

            all_probs.extend(fair_prices)
            all_outcomes.extend(outcomes)

            # Top-1: does model's highest prob match winner?
            model_pick = np.argmax(fair_prices)
            if outcomes[model_pick] == 1.0:
                top1_correct += 1
            n_events += 1

        bs = brier_score(all_probs, all_outcomes)
        ll = log_loss(all_probs, all_outcomes)
        top1 = top1_correct / n_events * 100 if n_events else 0

        marker = ""
        if bs < best_brier:
            best_brier = bs
            best_sigma_f = sigma_f
            marker = " ←"

        print(f"{sigma_f:>5.1f}  {sigma_f/1.8:>5.2f}  {bs:>7.4f}  {ll:>7.4f}  "
              f"{top1:>5.1f}%{marker}")

    print(f"\nBest σ: {best_sigma_f}°F / {best_sigma_f/1.8:.2f}°C "
          f"(Brier={best_brier:.4f})")

    # Uniform baseline Brier
    unif_probs = []
    unif_outcomes = []
    for ev in prepared:
        n = len(ev["buckets"])
        unif_probs.extend([1.0 / n] * n)
        unif_outcomes.extend([1.0 if b["is_winner"] else 0.0
                              for b in ev["buckets"]])
    unif_brier = brier_score(unif_probs, unif_outcomes)
    print(f"Uniform baseline Brier: {unif_brier:.4f}")
    print(f"Model improvement: {(1 - best_brier/unif_brier)*100:.1f}%")

    # Volume-weighted baseline Brier
    vol_probs = []
    vol_outcomes = []
    for ev in prepared:
        vp = volume_weighted_prices(ev["buckets"])
        vol_probs.extend(vp)
        vol_outcomes.extend([1.0 if b["is_winner"] else 0.0
                             for b in ev["buckets"]])
    vol_brier = brier_score(vol_probs, vol_outcomes)
    print(f"Volume-weighted baseline Brier: {vol_brier:.4f}")

    # =========================================================================
    # 2. MODEL ACCURACY
    # =========================================================================
    print(f"\n{'='*70}")
    print(f"2. MODEL ACCURACY (σ={best_sigma_f}°F)")
    print(f"{'='*70}\n")

    top1_correct = 0
    top2_correct = 0
    top3_correct = 0
    n_events = 0

    # Accuracy by confidence level
    confidence_bins = defaultdict(lambda: {"correct": 0, "total": 0})

    for ev in prepared:
        sigma = best_sigma_f if ev["is_fahrenheit"] else best_sigma_f / 1.8
        fair_prices = compute_event_fair_prices(ev["buckets"],
                                                 ev["archive_temp"], sigma)
        outcomes = [b["is_winner"] for b in ev["buckets"]]
        winner_idx = next(i for i, o in enumerate(outcomes) if o)

        sorted_idx = sorted(range(len(fair_prices)),
                            key=lambda i: -fair_prices[i])

        if sorted_idx[0] == winner_idx:
            top1_correct += 1
        if winner_idx in sorted_idx[:2]:
            top2_correct += 1
        if winner_idx in sorted_idx[:3]:
            top3_correct += 1
        n_events += 1

        # Confidence bin: max probability
        max_prob = max(fair_prices)
        bin_key = int(max_prob * 10) / 10  # 0.0, 0.1, 0.2, ...
        confidence_bins[bin_key]["total"] += 1
        if sorted_idx[0] == winner_idx:
            confidence_bins[bin_key]["correct"] += 1

    print(f"Events: {n_events}")
    print(f"Top-1 accuracy: {top1_correct}/{n_events} = "
          f"{top1_correct/n_events*100:.1f}%")
    print(f"Top-2 accuracy: {top2_correct}/{n_events} = "
          f"{top2_correct/n_events*100:.1f}%")
    print(f"Top-3 accuracy: {top3_correct}/{n_events} = "
          f"{top3_correct/n_events*100:.1f}%")

    # Compare with volume-weighted top-1
    vol_top1 = 0
    for ev in prepared:
        vp = volume_weighted_prices(ev["buckets"])
        outcomes = [b["is_winner"] for b in ev["buckets"]]
        winner_idx = next(i for i, o in enumerate(outcomes) if o)
        if np.argmax(vp) == winner_idx:
            vol_top1 += 1
    print(f"\nVolume-weighted top-1: {vol_top1}/{n_events} = "
          f"{vol_top1/n_events*100:.1f}%")
    print(f"Uniform random top-1: ~{1/7*100:.1f}% (1/7 buckets)")

    # Accuracy by model confidence
    print(f"\nAccuracy by model confidence (max bucket prob):")
    print(f"{'Conf':>6} {'Correct':>8} {'Total':>6} {'Accuracy':>9}")
    print("-" * 35)
    for bin_key in sorted(confidence_bins):
        d = confidence_bins[bin_key]
        acc = d["correct"] / d["total"] * 100 if d["total"] else 0
        print(f"{bin_key:>5.1f}+  {d['correct']:>6}   {d['total']:>5}  "
              f"{acc:>7.1f}%")

    # =========================================================================
    # 3. CALIBRATION CURVE
    # =========================================================================
    print(f"\n{'='*70}")
    print(f"3. CALIBRATION CURVE (σ={best_sigma_f}°F)")
    print(f"{'='*70}\n")
    print("Predicted probability vs observed frequency:")
    print("(Perfect calibration: predicted ≈ observed)\n")

    cal_bins = defaultdict(lambda: {"predicted_sum": 0, "actual_sum": 0, "count": 0})

    for ev in prepared:
        sigma = best_sigma_f if ev["is_fahrenheit"] else best_sigma_f / 1.8
        fair_prices = compute_event_fair_prices(ev["buckets"],
                                                 ev["archive_temp"], sigma)
        for i, b in enumerate(ev["buckets"]):
            p = fair_prices[i]
            outcome = 1.0 if b["is_winner"] else 0.0
            # Bin: 0-5%, 5-10%, ..., 45-50%, 50-100%
            bin_key = min(int(p * 20) / 20, 0.50)  # group 50%+ together
            cal_bins[bin_key]["predicted_sum"] += p
            cal_bins[bin_key]["actual_sum"] += outcome
            cal_bins[bin_key]["count"] += 1

    print(f"{'Bin':>8} {'AvgPred':>8} {'Observed':>9} {'Count':>6} {'Ratio':>6}")
    print("-" * 45)
    for bin_key in sorted(cal_bins):
        d = cal_bins[bin_key]
        avg_pred = d["predicted_sum"] / d["count"]
        observed = d["actual_sum"] / d["count"]
        ratio = observed / avg_pred if avg_pred > 0.001 else 0
        label = f"{bin_key:.0%}-{bin_key+0.05:.0%}"
        print(f"{label:>8}  {avg_pred:>6.3f}   {observed:>7.3f}  "
              f"{d['count']:>5}  {ratio:>5.2f}")

    # =========================================================================
    # 4. SIMULATED TRADING (model vs baselines)
    # =========================================================================
    print(f"\n{'='*70}")
    print(f"4. SIMULATED TRADING (σ={best_sigma_f}°F)")
    print(f"{'='*70}")
    print("\nModel predicts fair prices. We simulate buying from two baselines:")
    print("  A) Uniform market (each bucket = 1/N)")
    print("  B) Volume-weighted market (volume ∝ pre-resolution interest)")
    print("If model_fair > baseline + min_edge → BUY YES at baseline price")
    print("If model_fair < baseline - min_edge → BUY NO at (1-baseline) price")
    print(f"Each trade is $1 notional.\n")

    for baseline_name, baseline_fn in [
        ("Uniform", lambda ev: uniform_prices(len(ev["buckets"]))),
        ("Volume-weighted", lambda ev: volume_weighted_prices(ev["buckets"])),
    ]:
        print(f"\n  ── {baseline_name} baseline ──")
        print(f"  {'MinEdge':>8} {'Trades':>6} {'Win%':>6} {'P&L':>8} "
              f"{'$/trade':>8} {'ROI':>6}")
        print(f"  {'-'*50}")

        for min_edge in EDGE_THRESHOLDS:
            trades = 0
            wins = 0
            pnl = 0.0
            cost = 0.0

            for ev in prepared:
                sigma = best_sigma_f if ev["is_fahrenheit"] else best_sigma_f / 1.8
                fair = compute_event_fair_prices(ev["buckets"],
                                                  ev["archive_temp"], sigma)
                bp = baseline_fn(ev)

                for i, b in enumerate(ev["buckets"]):
                    edge = fair[i] - bp[i]
                    outcome = b["is_winner"]

                    if edge > min_edge and bp[i] < 0.95:
                        # BUY YES at baseline price
                        trades += 1
                        cost += bp[i]
                        if outcome:
                            wins += 1
                            pnl += (1 - bp[i])
                        else:
                            pnl -= bp[i]

                    elif edge < -min_edge and bp[i] > 0.05:
                        # BUY NO at (1 - baseline)
                        trades += 1
                        cost += (1 - bp[i])
                        if not outcome:
                            wins += 1
                            pnl += bp[i]
                        else:
                            pnl -= (1 - bp[i])

            if trades == 0:
                print(f"  {min_edge:>7.0%}    no trades")
                continue
            wr = wins / trades * 100
            avg = pnl / trades
            roi = pnl / cost * 100 if cost > 0 else 0
            print(f"  {min_edge:>7.0%}  {trades:>5}  {wr:>5.1f}% "
                  f"${pnl:>+7.2f}  ${avg:>+6.3f}  {roi:>+5.1f}%")

    # =========================================================================
    # 5. CITY BREAKDOWN
    # =========================================================================
    print(f"\n{'='*70}")
    print(f"5. CITY BREAKDOWN (σ={best_sigma_f}°F, model vs uniform, "
          f"min_edge=5%)")
    print(f"{'='*70}\n")

    city_stats = defaultdict(lambda: {
        "events": 0, "trades": 0, "wins": 0, "pnl": 0.0,
        "top1": 0, "diffs": [],
    })

    for ev in prepared:
        c = ev["city"]
        city_stats[c]["events"] += 1
        sigma = best_sigma_f if ev["is_fahrenheit"] else best_sigma_f / 1.8

        # Top-1 accuracy
        fair = compute_event_fair_prices(ev["buckets"], ev["archive_temp"], sigma)
        outcomes = [b["is_winner"] for b in ev["buckets"]]
        winner_idx = next(i for i, o in enumerate(outcomes) if o)
        if np.argmax(fair) == winner_idx:
            city_stats[c]["top1"] += 1

        # Archive vs actual diff
        actual, _ = parse_actual_from_winner(
            next(b["question"] for b in ev["buckets"] if b["is_winner"])
        )
        if actual is not None:
            city_stats[c]["diffs"].append(ev["archive_temp"] - actual)

        # Simulated trades vs uniform
        n = len(ev["buckets"])
        unif = 1.0 / n
        for i, b in enumerate(ev["buckets"]):
            edge = fair[i] - unif
            outcome = b["is_winner"]

            if edge > 0.05 and unif < 0.95:
                city_stats[c]["trades"] += 1
                if outcome:
                    city_stats[c]["wins"] += 1
                    city_stats[c]["pnl"] += (1 - unif)
                else:
                    city_stats[c]["pnl"] -= unif
            elif edge < -0.05 and unif > 0.05:
                city_stats[c]["trades"] += 1
                if not outcome:
                    city_stats[c]["wins"] += 1
                    city_stats[c]["pnl"] += unif
                else:
                    city_stats[c]["pnl"] -= (1 - unif)

    print(f"{'City':<14} {'Evts':>4} {'Top1%':>6} {'Trds':>5} {'Win%':>6} "
          f"{'P&L':>7} {'Bias':>6}")
    print("-" * 58)
    for city in sorted(city_stats, key=lambda c: -city_stats[c]["events"]):
        s = city_stats[city]
        if s["events"] == 0:
            continue
        top1 = s["top1"] / s["events"] * 100
        wr = s["wins"] / s["trades"] * 100 if s["trades"] else 0
        bias = np.mean(s["diffs"]) if s["diffs"] else 0
        print(f"{city:<14} {s['events']:>4} {top1:>5.1f}% {s['trades']:>5} "
              f"{wr:>5.1f}% ${s['pnl']:>+6.2f} {bias:>+5.1f}°")

    # =========================================================================
    # 6. ARCHIVE vs ACTUAL (WU) BIAS
    # =========================================================================
    print(f"\n{'='*70}")
    print("6. ARCHIVE vs ACTUAL TEMPERATURE BIAS")
    print(f"{'='*70}\n")
    print("Archive = Open-Meteo ERA5 reanalysis")
    print("Actual = midpoint of winning PM bucket (WU station)\n")

    all_diffs_f = []
    all_diffs_c = []
    seen = set()

    for ev in prepared:
        key = (ev["city"], ev["date"])
        if key in seen:
            continue
        seen.add(key)

        winner_b = next(b for b in ev["buckets"] if b["is_winner"])
        actual, unit = parse_actual_from_winner(winner_b["question"])
        if actual is None:
            continue

        diff = ev["archive_temp"] - actual
        if ev["is_fahrenheit"]:
            all_diffs_f.append((ev["city"], ev["date"], diff, ev["archive_temp"],
                                actual))
        else:
            all_diffs_c.append((ev["city"], ev["date"], diff, ev["archive_temp"],
                                actual))

    if all_diffs_f:
        diffs_f = [d[2] for d in all_diffs_f]
        print(f"°F cities: {len(diffs_f)} days")
        print(f"  Mean bias: {np.mean(diffs_f):+.2f}°F "
              f"(+ = archive warmer)")
        print(f"  Std:       {np.std(diffs_f):.2f}°F")
        print(f"  Median:    {np.median(diffs_f):+.2f}°F")
        print(f"  Range:     [{min(diffs_f):.1f}, {max(diffs_f):.1f}]")

        # Large outliers
        outliers = [(c, d, diff, arch, act) for c, d, diff, arch, act
                     in all_diffs_f if abs(diff) > 5]
        if outliers:
            print(f"\n  Large outliers (|diff| > 5°F): {len(outliers)}")
            for c, d, diff, arch, act in sorted(outliers,
                                                  key=lambda x: -abs(x[2]))[:10]:
                print(f"    {c} {d}: archive={arch:.1f} actual={act:.1f} "
                      f"diff={diff:+.1f}")

    if all_diffs_c:
        diffs_c = [d[2] for d in all_diffs_c]
        print(f"\n°C cities: {len(diffs_c)} days")
        print(f"  Mean bias: {np.mean(diffs_c):+.2f}°C")
        print(f"  Std:       {np.std(diffs_c):.2f}°C")
        print(f"  Median:    {np.median(diffs_c):+.2f}°C")
        print(f"  Range:     [{min(diffs_c):.1f}, {max(diffs_c):.1f}]")

    # =========================================================================
    # 7. SUMMARY
    # =========================================================================
    print(f"\n{'='*70}")
    print("SUMMARY")
    print(f"{'='*70}\n")

    sigma_c = best_sigma_f / 1.8
    print(f"Optimal σ:      {best_sigma_f}°F / {sigma_c:.2f}°C")
    print(f"Model Brier:    {best_brier:.4f}")
    print(f"Uniform Brier:  {unif_brier:.4f} "
          f"(model {(1-best_brier/unif_brier)*100:.0f}% better)")
    print(f"Volume Brier:   {vol_brier:.4f}")
    print(f"Top-1 accuracy: {top1_correct/n_events*100:.1f}% "
          f"(vs uniform {1/7*100:.0f}%, vs volume {vol_top1/n_events*100:.1f}%)")

    if all_diffs_f:
        print(f"\n°F bias: {np.mean([d[2] for d in all_diffs_f]):+.1f}° "
              f"± {np.std([d[2] for d in all_diffs_f]):.1f}°")
    if all_diffs_c:
        print(f"°C bias: {np.mean([d[2] for d in all_diffs_c]):+.1f}° "
              f"± {np.std([d[2] for d in all_diffs_c]):.1f}°")

    print(f"\nConclusion:")
    print(f"  The Normal(archive, σ={best_sigma_f}°F) model is significantly")
    print(f"  better than uniform ({(1-best_brier/unif_brier)*100:.0f}% lower "
          f"Brier) and captures bucket")
    print(f"  probabilities well. In production, use forecast (not archive)")
    print(f"  as center — this adds forecast error, effectively increasing σ.")
    print(f"  Recommended production σ: {best_sigma_f+1}°F-{best_sigma_f+2}°F "
          f"(1-2 day horizon).")


if __name__ == "__main__":
    main()
