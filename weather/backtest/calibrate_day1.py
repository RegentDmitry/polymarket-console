"""Calibrate previous_day1 forecasts against actuals.
Compute per-city: RMSE, bias, optimal weights, and Student-t df.
Then simulate effect on our actual Polymarket positions.
"""
import csv
import glob
import json
import math
import re
from collections import defaultdict
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "trading_bot" / "data"
CITIES_JSON = Path(__file__).parent.parent / "cities.json"
MODELS = ["gfs_seamless", "ecmwf_ifs025", "icon_seamless", "jma_seamless"]


def load_data():
    forecasts = defaultdict(lambda: defaultdict(dict))
    with open(DATA_DIR / "previous_runs_day1.csv") as f:
        for row in csv.DictReader(f):
            forecasts[row["city"]][row["date"]][row["model"]] = float(row["prev_day1_max"])

    actuals = {}
    with open(DATA_DIR / "historical_actuals.csv") as f:
        for row in csv.DictReader(f):
            actuals.setdefault(row["city"], {})[row["date"]] = float(row["actual_max"])

    with open(CITIES_JSON) as f:
        cities = json.load(f)

    return forecasts, actuals, cities


def calibrate(forecasts, actuals, cities):
    print("=" * 90)
    print("PER-CITY CALIBRATION (previous_day1 vs Open-Meteo actuals, ~14 months)")
    print("=" * 90)
    print()
    print("%-15s %4s  %6s %6s %6s %6s  %6s %6s  %6s %6s %6s %6s" % (
        "CITY", "N", "RMSE_G", "RMSE_E", "RMSE_I", "RMSE_J",
        "RM_WGT", "BIAS", "W_GFS", "W_ECM", "W_ICO", "W_JMA"))
    print("-" * 90)

    calibration = {}

    for city_slug in sorted(cities.keys()):
        city_fc = forecasts.get(city_slug, {})
        city_act = actuals.get(city_slug, {})
        unit = cities[city_slug].get("unit", "fahrenheit")

        errors = {m: [] for m in MODELS}
        dates_used = []

        for date in sorted(city_fc.keys()):
            if date not in city_act:
                continue
            fc = city_fc[date]
            act = city_act[date]
            if not all(m in fc for m in MODELS):
                continue
            dates_used.append(date)
            for m in MODELS:
                errors[m].append(fc[m] - act)

        n = len(dates_used)
        if n < 30:
            continue

        # Per-model RMSE and bias
        rmse = {}
        bias = {}
        for m in MODELS:
            errs = errors[m]
            rmse[m] = math.sqrt(sum(e ** 2 for e in errs) / n)
            bias[m] = sum(errs) / n

        # Optimal weights (inverse-variance)
        inv_var = {m: 1.0 / (rmse[m] ** 2) for m in MODELS if rmse[m] > 0}
        total_iv = sum(inv_var.values())
        weights = {m: inv_var.get(m, 0) / total_iv for m in MODELS}

        # Weighted ensemble errors
        weighted_errors = []
        for i, date in enumerate(dates_used):
            fc = city_fc[date]
            act = city_act[date]
            w_fc = sum(weights[m] * fc[m] for m in MODELS)
            weighted_errors.append(w_fc - act)

        w_rmse = math.sqrt(sum(e ** 2 for e in weighted_errors) / n)
        w_bias = sum(weighted_errors) / n

        # Student-t df (kurtosis method)
        df = None
        if w_rmse > 0:
            centered = [(e - w_bias) for e in weighted_errors]
            var = sum(e ** 2 for e in centered) / n
            if var > 0:
                kurt = sum(e ** 4 for e in centered) / (n * var ** 2) - 3
                if kurt > 0.1:
                    df = max(4.0, 6.0 / kurt + 4)

        u = "F" if unit == "fahrenheit" else "C"
        print("%-15s %4d  %5.2f%s %5.2f%s %5.2f%s %5.2f%s  %5.2f%s %+5.2f  %.2f   %.2f   %.2f   %.2f" % (
            city_slug, n,
            rmse["gfs_seamless"], u, rmse["ecmwf_ifs025"], u,
            rmse["icon_seamless"], u, rmse["jma_seamless"], u,
            w_rmse, u, w_bias,
            weights["gfs_seamless"], weights["ecmwf_ifs025"],
            weights["icon_seamless"], weights["jma_seamless"]))

        calibration[city_slug] = {
            "n": n,
            "sigma": round(w_rmse, 3),
            "bias": round(w_bias, 3),
            "student_t_df": round(df, 1) if df else None,
            "ensemble_weights": {m: round(weights[m], 4) for m in MODELS},
            "per_model_rmse": {m: round(rmse[m], 3) for m in MODELS},
            "per_model_bias": {m: round(bias[m], 3) for m in MODELS},
        }

    return calibration


def parse_bucket(bucket_label):
    """Parse bucket label into (lower, upper) bounds."""
    m = re.match(r"(\d+)-(\d+)", bucket_label)
    if m:
        return int(m.group(1)), int(m.group(2)) + 1

    m = re.match(r"[\u2265](\d+)", bucket_label)
    if m:
        return int(m.group(1)), None

    m = re.match(r"[\u2264](\d+)", bucket_label)
    if m:
        return None, int(m.group(1)) + 1

    m = re.match(r"(-?\d+)", bucket_label)
    if m:
        return int(m.group(1)), int(m.group(1)) + 1

    return None, None


def bucket_prob_normal(forecast, sigma, lower, upper):
    """Normal CDF probability for bucket."""
    from math import erf, sqrt
    def norm_cdf(x):
        return 0.5 * (1.0 + erf(x / sqrt(2.0)))

    if sigma <= 0:
        sigma = 0.01
    p_lo = norm_cdf((lower - forecast) / sigma) if lower is not None else 0.0
    p_hi = norm_cdf((upper - forecast) / sigma) if upper is not None else 1.0
    return max(0.0, p_hi - p_lo)


def simulate_positions(calibration, forecasts, cities):
    print()
    print("=" * 100)
    print("EFFECT ON OUR 41 POLYMARKET POSITIONS")
    print("=" * 100)
    print()

    positions = []
    hist_dir = DATA_DIR / "history"
    for f_path in sorted(glob.glob(str(hist_dir / "*.json"))):
        with open(f_path) as fh:
            positions.append(json.load(fh))

    print("%-15s %-11s %-12s %6s %6s %5s %6s %6s %5s %7s  %s" % (
        "CITY", "DATE", "BUCKET", "ENTRY%", "FC_D1", "SIGMA", "FAIR%", "EDGE%", "WON", "PNL", "ACTION"))
    print("-" * 105)

    total_pnl_old = 0
    total_pnl_new = 0
    would_skip = 0
    would_trade = 0
    new_wins = 0
    new_losses = 0

    for pos in sorted(positions, key=lambda p: (p.get("date", ""), p.get("city", ""))):
        city = pos.get("city", "")
        date = pos.get("date", "")
        bucket = pos.get("bucket_label", "")
        entry_price = pos.get("entry_price", 0)
        entry_size = pos.get("entry_size", 0)
        tokens = pos.get("tokens", 0)
        status = pos.get("status", "")
        won = status == "win"
        pnl = (tokens - entry_size) if won else -entry_size
        total_pnl_old += pnl

        cal = calibration.get(city)
        if not cal:
            continue

        city_fc = forecasts.get(city, {}).get(date, {})
        if not all(m in city_fc for m in MODELS):
            continue

        weights = cal["ensemble_weights"]
        fc = sum(weights[m] * city_fc[m] for m in MODELS)
        fc_corrected = fc - cal["bias"]
        sigma = cal["sigma"]

        lower, upper = parse_bucket(bucket)
        if lower is None and upper is None:
            continue

        fair = bucket_prob_normal(fc_corrected, sigma, lower, upper)
        edge = fair - entry_price

        would = edge >= 0.05 and edge <= 0.25 and entry_price >= 0.08
        if would:
            would_trade += 1
            if won:
                new_wins += 1
            else:
                new_losses += 1
            total_pnl_new += pnl
        else:
            would_skip += 1

        marker = "TRADE" if would else "SKIP"
        print("%-15s %-11s %-12s %5.1f%% %5.1f  %5.2f  %5.1f%% %+5.1f%% %5s %+7.2f  %s" % (
            city, date, bucket,
            entry_price * 100, fc_corrected, sigma,
            fair * 100, edge * 100,
            "WIN" if won else "LOSS", pnl,
            marker))

    print("-" * 105)
    print()
    print("ORIGINAL:  %d positions, P&L = $%.2f" % (len(positions), total_pnl_old))
    print("NEW CAL:   %d would trade, %d would skip" % (would_trade, would_skip))
    print("NEW P&L:   $%.2f  (wins=%d, losses=%d)" % (total_pnl_new, new_wins, new_losses))
    print("SAVED:     $%.2f by skipping bad trades" % (total_pnl_old - total_pnl_new))


if __name__ == "__main__":
    forecasts, actuals, cities = load_data()
    calibration = calibrate(forecasts, actuals, cities)

    cal_path = DATA_DIR / "calibration_day1.json"
    with open(cal_path, "w") as f:
        json.dump(calibration, f, indent=2)
    print("\nSaved calibration to %s" % cal_path)

    simulate_positions(calibration, forecasts, cities)
