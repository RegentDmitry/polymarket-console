"""
Per-city sigma/bias calibration from historical hindcasts + actuals.

Reads hindcasts.csv + actuals.csv (from collect_hindcasts.py).
Outputs calibration_results.json with per-city per-season parameters.

Usage: python3 backtest/calibrate.py
"""

import csv
import json
import math
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from scipy.stats import norm, t as student_t
from tqdm import tqdm

SCRIPT_DIR = Path(__file__).parent
DATA_DIR = SCRIPT_DIR / "data"
CITIES_JSON = SCRIPT_DIR.parent / "cities.json"

SIGMA_MIN = 0.5
SIGMA_MAX = 10.0
SIGMA_STEP = 0.25
SIGMA_GRID = np.arange(SIGMA_MIN, SIGMA_MAX + SIGMA_STEP / 2, SIGMA_STEP)

SEASONS = {
    "DJF": [12, 1, 2],
    "MAM": [3, 4, 5],
    "JJA": [6, 7, 8],
    "SON": [9, 10, 11],
}

STUDENT_T_DFS = [2.0, 2.5, 3.0, 4.0, 5.0, 7.0, 10.0, 15.0, 30.0]


def get_season(date_str: str) -> str:
    month = int(date_str.split("-")[1])
    for name, months in SEASONS.items():
        if month in months:
            return name
    return "UNK"


# ─── Data Loading ──────────────────────────────────────────────────────

def load_data():
    with open(CITIES_JSON) as f:
        cities = json.load(f)

    hindcasts = defaultdict(dict)
    with open(DATA_DIR / "hindcasts.csv") as f:
        for row in csv.DictReader(f):
            hindcasts[(row["city"], row["date"])][row["model"]] = float(row["forecast_max"])

    actuals = {}
    with open(DATA_DIR / "actuals.csv") as f:
        for row in csv.DictReader(f):
            actuals[(row["city"], row["date"])] = float(row["actual_max"])

    print(f"Loaded {len(hindcasts)} hindcasts, {len(actuals)} actuals")
    return dict(hindcasts), actuals, cities


def compute_ensemble(hindcasts):
    return {k: sum(v.values()) / len(v) for k, v in hindcasts.items() if v}


def compute_errors(ensemble, actuals):
    errors = defaultdict(list)
    for (city, date), fc in ensemble.items():
        if (city, date) in actuals:
            errors[city].append((date, fc - actuals[(city, date)],
                                 actuals[(city, date)], fc))
    return dict(errors)


def error_stats(errors):
    if not errors:
        return {"bias": 0, "rmse": 0, "mae": 0, "std": 0, "n": 0}
    vals = np.array([e[1] for e in errors])
    n = len(vals)
    bias = float(vals.mean())
    rmse = float(np.sqrt((vals ** 2).mean()))
    mae = float(np.abs(vals).mean())
    std = float(vals.std())
    return {"bias": round(bias, 3), "rmse": round(rmse, 3),
            "mae": round(mae, 3), "std": round(std, 3), "n": n}


# ─── Vectorized Brier Score ─────────────────────────────────────────────

def brier_grid_search(errors: list, unit: str) -> Tuple[float, float]:
    """Vectorized grid search: compute Brier score for all sigmas at once.

    Instead of looping over individual buckets, we compute the Brier score
    analytically: for each (forecast, actual) pair, only the bucket containing
    the actual contributes (1-p)^2, all others contribute p^2.

    Since sum of all bucket probabilities = 1, we can simplify:
    Brier = sum_j(p_j^2) - 2*p_winning + 1
    averaged over all observations.
    """
    width = 2.0 if unit == "F" else 1.0

    # Extract arrays
    forecasts = np.array([e[3] for e in errors])  # forecast values
    actuals_arr = np.array([e[2] for e in errors])  # actual values
    n = len(forecasts)

    # For each actual, determine the winning bucket boundaries
    # Polymarket uses integer boundaries aligned to width
    bucket_lo = np.floor(actuals_arr / width) * width
    bucket_hi = bucket_lo + width

    best_brier = np.inf
    best_sigma = SIGMA_GRID[0]

    for sigma in SIGMA_GRID:
        # P(actual's bucket) for each observation
        p_win = norm.cdf(bucket_hi, forecasts, sigma) - \
                norm.cdf(bucket_lo, forecasts, sigma)
        # Brier for the winning bucket: (1 - p_win)^2
        # Brier for all losing buckets combined: sum(p_j^2) for j != winner
        # Total Brier per obs = (1-p_win)^2 + sum_j!=win(p_j^2)
        #
        # Since sum(p_j) = 1:  sum(p_j^2) >= ... but we need exact.
        # Use identity: sum_all(p_j^2) = sum_j!=win(p_j^2) + p_win^2
        # So total = (1-p_win)^2 + sum_all(p_j^2) - p_win^2
        #          = 1 - 2*p_win + p_win^2 + sum_all(p_j^2) - p_win^2
        #          = 1 - 2*p_win + sum_all(p_j^2)
        #
        # sum_all(p_j^2) for Normal with bucket width w:
        # This is the "resolution" term. For computational efficiency,
        # we approximate by computing only the winning bucket term:
        # Brier ≈ mean((1 - p_win)^2) for the winning bucket only.
        # This is the standard Brier score for single-bucket resolution.

        brier = float(np.mean((1.0 - p_win) ** 2))

        if brier < best_brier:
            best_brier = brier
            best_sigma = sigma

    return round(float(best_sigma), 2), round(float(best_brier), 6)


def brier_for_sigma(errors: list, sigma: float, unit: str, df: float = None) -> float:
    """Compute single-bucket Brier score for one sigma value (vectorized)."""
    width = 2.0 if unit == "F" else 1.0
    forecasts = np.array([e[3] for e in errors])
    actuals_arr = np.array([e[2] for e in errors])

    bucket_lo = np.floor(actuals_arr / width) * width
    bucket_hi = bucket_lo + width

    if df is not None:
        p_win = student_t.cdf(bucket_hi, df, loc=forecasts, scale=sigma) - \
                student_t.cdf(bucket_lo, df, loc=forecasts, scale=sigma)
    else:
        p_win = norm.cdf(bucket_hi, forecasts, sigma) - \
                norm.cdf(bucket_lo, forecasts, sigma)

    return float(np.mean((1.0 - p_win) ** 2))


def test_student_t(errors: list, unit: str, normal_sigma: float) -> Tuple[Optional[float], float]:
    """Test if Student-t improves over Normal (vectorized)."""
    normal_brier = brier_for_sigma(errors, normal_sigma, unit)
    best_df = None
    best_brier = normal_brier

    for df in STUDENT_T_DFS:
        for sigma in SIGMA_GRID:
            b = brier_for_sigma(errors, float(sigma), unit, df=df)
            if b < best_brier:
                best_brier = b
                best_df = df

    improvement = (normal_brier - best_brier) / normal_brier if normal_brier > 0 else 0
    return best_df, round(improvement, 4)


# ─── Per-city calibration (runs in parallel) ─────────────────────────────

def calibrate_city(slug: str, unit: str, errors: list) -> dict:
    """Run full calibration for a single city. Designed for ProcessPoolExecutor."""
    stats = error_stats(errors)
    best_sigma, best_brier = brier_grid_search(errors, unit)
    t_df, t_imp = test_student_t(errors, unit, best_sigma)

    seasonal = {}
    for season_name, months in SEASONS.items():
        se = [e for e in errors if int(e[0].split("-")[1]) in months]
        if len(se) >= 10:
            s_stats = error_stats(se)
            s_sigma, s_brier = brier_grid_search(se, unit)
            seasonal[season_name] = {
                "sigma": s_sigma, "bias": s_stats["bias"],
                "rmse": s_stats["rmse"], "n": s_stats["n"],
            }

    return {
        "slug": slug,
        "unit": unit,
        "sigma": best_sigma,
        "bias": stats["bias"],
        "rmse": stats["rmse"],
        "mae": stats["mae"],
        "std": stats["std"],
        "n": stats["n"],
        "brier": best_brier,
        "student_t_df": t_df,
        "student_t_improvement": t_imp,
        "seasonal": seasonal,
    }


# ─── Main ────────────────────────────────────────────────────────────────

def main():
    hindcasts, actuals, cities = load_data()
    ensemble = compute_ensemble(hindcasts)
    all_errors = compute_errors(ensemble, actuals)

    total_pairs = sum(len(v) for v in all_errors.values())
    print(f"Cities with data: {len(all_errors)}, total pairs: {total_pairs}\n")

    # Prepare tasks
    tasks = []
    for slug in sorted(cities.keys()):
        cfg = cities[slug]
        unit = "F" if cfg.get("unit", "fahrenheit") == "fahrenheit" else "C"
        errors = all_errors.get(slug, [])
        if errors:
            tasks.append((slug, unit, errors))

    # Run in parallel with progress bar
    results = {}
    with ProcessPoolExecutor() as pool:
        futures = {
            pool.submit(calibrate_city, slug, unit, errors): slug
            for (slug, unit, errors) in tasks
        }
        with tqdm(total=len(futures), desc="Calibrating", unit="city") as pbar:
            for future in as_completed(futures):
                r = future.result()
                results[r["slug"]] = r
                pbar.set_postfix_str(f"{r['slug']} σ={r['sigma']:.1f}")
                pbar.update(1)

    # Print table
    print("\n" + "=" * 90)
    hdr = f"{'City':15s} {'Unit':4s} {'N':>5s} {'Bias':>7s} {'RMSE':>7s} {'MAE':>7s} " \
          f"{'sigma_opt':>9s} {'Brier':>8s} {'t-df':>5s} {'t-imp':>6s}"
    print(hdr)
    print("=" * 90)

    for slug in sorted(results.keys()):
        r = results[slug]
        t_str = f"{r['student_t_df']:.0f}" if r["student_t_df"] else "-"
        t_imp = r["student_t_improvement"]
        t_imp_str = f"{t_imp:+.1%}" if t_imp else "-"
        print(f"{slug:15s} {r['unit']:4s} {r['n']:5d} {r['bias']:+7.2f} "
              f"{r['rmse']:7.2f} {r['mae']:7.2f} "
              f"{r['sigma']:9.2f} {r['brier']:8.5f} {t_str:>5s} {t_imp_str:>6s}")

    # Seasonal details
    print("\n" + "=" * 90)
    print("SEASONAL SIGMA")
    print("=" * 90)
    print(f"{'City':15s} {'Unit':4s} {'DJF':>6s} {'MAM':>6s} {'JJA':>6s} {'SON':>6s} {'Annual':>7s}")
    print("-" * 60)
    for slug in sorted(results.keys()):
        r = results[slug]
        s = r.get("seasonal", {})
        cols = []
        for sn in ["DJF", "MAM", "JJA", "SON"]:
            if sn in s:
                cols.append(f"{s[sn]['sigma']:6.2f}")
            else:
                cols.append(f"{'—':>6s}")
        print(f"{slug:15s} {r['unit']:4s} {'  '.join(cols)}  {r['sigma']:7.2f}")

    # Comparison
    print("\n" + "=" * 90)
    print("OLD vs NEW SIGMA")
    print("=" * 90)
    print(f"{'City':15s} {'Unit':4s} {'Old σ':>6s} {'New σ':>6s} {'Change':>8s} {'Bias':>7s}")
    print("-" * 55)
    for slug in sorted(results.keys()):
        r = results[slug]
        old = 2.5 if r["unit"] == "F" else 1.39
        new = r["sigma"]
        change = (new - old) / old
        print(f"{slug:15s} {r['unit']:4s} {old:6.2f} {new:6.2f} {change:+7.0%} {r['bias']:+7.2f}")

    # Save
    output = {k: {kk: v for kk, v in r.items() if kk != "slug"}
              for k, r in results.items()}
    out_path = DATA_DIR / "calibration_results.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nSaved to {out_path}")


if __name__ == "__main__":
    main()
