"""
Backtest trading strategies on historical hindcasts + actuals.

Model: the market prices buckets using the "true" (actual-calibrated) sigma
with some noise. Our bot uses its own sigma/bias model, and trades when it
sees edge vs the market. We then check if the trade won.

Usage: python3 backtest/strategy_backtest.py
"""

import csv
import json
import math
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).parent.parent))
from trading_bot.pricing import bucket_fair_price

SCRIPT_DIR = Path(__file__).parent
DATA_DIR = SCRIPT_DIR / "data"
CITIES_JSON = SCRIPT_DIR.parent / "cities.json"


SEASONS = {
    "DJF": [12, 1, 2],
    "MAM": [3, 4, 5],
    "JJA": [6, 7, 8],
    "SON": [9, 10, 11],
}


def get_season(date_str: str) -> str:
    month = int(date_str.split("-")[1])
    for name, months in SEASONS.items():
        if month in months:
            return name
    return "UNK"


@dataclass
class StrategyConfig:
    name: str
    sigma_mode: str = "global"       # "global" | "per_city" | "seasonal"
    global_sigma_f: float = 2.5
    global_sigma_c: float = 1.39
    use_bias: bool = False
    kelly_divisor: float = 2
    max_per_date_pct: float = 1.0
    max_per_bucket: float = 50.0
    max_edge_cap: float = 1.0
    min_edge: float = 0.05

    def get_sigma(self, city, date, unit, calibration):
        if self.sigma_mode == "global":
            return self.global_sigma_f if unit == "F" else self.global_sigma_c
        cal = calibration.get(city, {})
        if self.sigma_mode == "seasonal":
            season = get_season(date)
            s = cal.get("seasonal", {}).get(season, {})
            if s:
                return s["sigma"]
        return cal.get("sigma", self.global_sigma_f if unit == "F" else self.global_sigma_c)

    def get_bias(self, city, date, calibration):
        if not self.use_bias:
            return 0.0
        cal = calibration.get(city, {})
        if self.sigma_mode == "seasonal":
            season = get_season(date)
            s = cal.get("seasonal", {}).get(season, {})
            if s:
                return s.get("bias", 0.0)
        return cal.get("bias", 0.0)


@dataclass
class Trade:
    city: str
    date: str
    bucket_label: str
    forecast: float
    sigma: float
    fair_price: float
    market_price: float
    edge: float
    size: float
    won: bool
    pnl: float


# ─── Data Loading ──────────────────────────────────────────────────────

def load_all():
    with open(CITIES_JSON) as f:
        cities = json.load(f)

    models_per_key = defaultdict(dict)
    with open(DATA_DIR / "hindcasts.csv") as f:
        for row in csv.DictReader(f):
            models_per_key[(row["city"], row["date"])][row["model"]] = float(row["forecast_max"])
    ensemble = {k: sum(v.values()) / len(v) for k, v in models_per_key.items() if v}

    actuals = {}
    with open(DATA_DIR / "actuals.csv") as f:
        for row in csv.DictReader(f):
            actuals[(row["city"], row["date"])] = float(row["actual_max"])

    calibration = {}
    cal_path = DATA_DIR / "calibration_results.json"
    if cal_path.exists():
        with open(cal_path) as f:
            calibration = json.load(f)

    return cities, ensemble, actuals, calibration


# ─── Market Model ──────────────────────────────────────────────────────

def compute_market_price(raw_forecast: float, unit: str, bucket_lo, bucket_hi) -> float:
    """Simulate market price: market uses raw ensemble forecast with old sigma.

    The market represents "average participants" who use the raw NWP forecast
    without bias correction and with a generic sigma (~old floor * 1.1).
    Our edge comes from: (1) bias correction, (2) per-city sigma calibration.
    """
    # Market sigma: slightly above old floor (market is somewhat smart)
    market_sigma = 3.0 if unit == "F" else 1.7
    return bucket_fair_price(raw_forecast, market_sigma, bucket_lo, bucket_hi)


# ─── Strategy Simulation ────────────────────────────────────────────────

def run_strategy(config: StrategyConfig, cities: dict, ensemble: dict,
                 actuals: dict, calibration: dict,
                 portfolio: float = 300.0) -> dict:
    """Run strategy. Market model: actual-centered pricing with wider sigma."""

    trades = []
    equity = portfolio
    peak = portfolio
    max_dd = 0.0

    dates = sorted(set(d for (c, d) in ensemble if (c, d) in actuals))

    for date in dates:
        day_signals = []

        for slug, cfg in cities.items():
            if (slug, date) not in ensemble or (slug, date) not in actuals:
                continue

            unit = "F" if cfg.get("unit", "fahrenheit") == "fahrenheit" else "C"
            width = 2 if unit == "F" else 1
            raw_fc = ensemble[(slug, date)]
            actual = actuals[(slug, date)]

            # Our model's forecast (with optional bias correction)
            bias = config.get_bias(slug, date, calibration)
            our_fc = raw_fc - bias

            # Our model's sigma
            our_sigma = config.get_sigma(slug, date, unit, calibration)

            # Generate buckets around forecast range
            center = round(our_fc / width) * width
            for i in range(-8, 9):
                lo = center + i * width
                hi = lo + width
                label = f"{lo}-{hi-1}" if unit == "F" else f"{lo}-{hi-1}"

                our_fair = bucket_fair_price(our_fc, our_sigma, lo, hi)
                # Market uses raw forecast (no bias corr) with generic sigma
                mkt_price = compute_market_price(raw_fc, unit, lo, hi)

                edge = our_fair - mkt_price
                if edge < config.min_edge:
                    continue
                if edge > config.max_edge_cap:
                    continue

                # Did actual fall in this bucket?
                won = lo <= actual < hi

                day_signals.append({
                    "city": slug, "date": date,
                    "label": label, "lo": lo, "hi": hi,
                    "forecast": our_fc, "sigma": our_sigma,
                    "fair": our_fair, "mkt": mkt_price,
                    "edge": edge, "won": won, "unit": unit,
                })

        # Sort by edge desc
        day_signals.sort(key=lambda s: -s["edge"])

        # Allocate with limits
        date_budget = config.max_per_date_pct * equity
        date_spent = 0.0

        for sig in day_signals:
            if equity <= 1.0:
                break

            # Kelly
            p = min(sig["mkt"] + sig["edge"], 0.99)
            odds = (1.0 - sig["mkt"]) / sig["mkt"] if sig["mkt"] > 0.01 else 0
            kelly = (p * odds - (1 - p)) / odds if odds > 0 else 0
            kelly /= config.kelly_divisor
            kelly = max(0, min(kelly, 0.25))

            size = kelly * equity
            size = min(size, config.max_per_bucket)
            size = min(size, date_budget - date_spent)
            size = min(size, equity * 0.3)
            if size < 2.0:
                continue

            tokens = size / sig["mkt"] if sig["mkt"] > 0.01 else 0
            pnl = (tokens - size) if sig["won"] else -size

            trades.append(Trade(
                city=sig["city"], date=sig["date"],
                bucket_label=sig["label"],
                forecast=sig["forecast"], sigma=sig["sigma"],
                fair_price=round(sig["fair"], 3),
                market_price=round(sig["mkt"], 3),
                edge=round(sig["edge"], 3),
                size=round(size, 2), won=sig["won"],
                pnl=round(pnl, 2),
            ))

            equity += pnl
            date_spent += size
            peak = max(peak, equity)
            dd = (peak - equity) / peak if peak > 0 else 0
            max_dd = max(max_dd, dd)

    n_wins = sum(1 for t in trades if t.won)
    total_pnl = sum(t.pnl for t in trades)
    win_pct = n_wins / len(trades) * 100 if trades else 0

    return {
        "strategy": config.name,
        "n_trades": len(trades),
        "n_wins": n_wins,
        "win_pct": round(win_pct, 1),
        "total_pnl": round(total_pnl, 2),
        "max_drawdown": round(max_dd, 4),
        "final_equity": round(equity, 2),
        "peak_equity": round(peak, 2),
        "trades": trades,
    }


# ─── Strategies ──────────────────────────────────────────────────────────

def get_strategies():
    return [
        StrategyConfig(
            name="Old (σ=2.5 global)",
            sigma_mode="global", global_sigma_f=2.5, global_sigma_c=1.39,
            use_bias=False, kelly_divisor=2,
            max_per_date_pct=1.0, max_edge_cap=1.0, min_edge=0.05,
        ),
        StrategyConfig(
            name="Fix1: per-city σ",
            sigma_mode="per_city",
            use_bias=False, kelly_divisor=2,
            max_per_date_pct=1.0, max_edge_cap=1.0, min_edge=0.05,
        ),
        StrategyConfig(
            name="Fix2: +bias corr",
            sigma_mode="per_city",
            use_bias=True, kelly_divisor=2,
            max_per_date_pct=1.0, max_edge_cap=1.0, min_edge=0.05,
        ),
        StrategyConfig(
            name="Fix3: +risk mgmt",
            sigma_mode="per_city",
            use_bias=True, kelly_divisor=4,
            max_per_date_pct=0.30, max_edge_cap=0.25, min_edge=0.08,
        ),
        StrategyConfig(
            name="Fix4: conservative",
            sigma_mode="seasonal",
            use_bias=True, kelly_divisor=4,
            max_per_date_pct=0.25, max_edge_cap=0.20, min_edge=0.10,
        ),
    ]


# ─── Main ────────────────────────────────────────────────────────────────

def main():
    cities, ensemble, actuals, calibration = load_all()

    if not calibration:
        print("WARNING: No calibration_results.json. Run calibrate.py first.\n")

    strategies = get_strategies()
    all_results = []

    print("=" * 95)
    hdr = f"{'Strategy':25s} {'Trades':>7s} {'Wins':>5s} {'Win%':>6s} " \
          f"{'PnL':>9s} {'MaxDD':>7s} {'Final$':>8s}"
    print(hdr)
    print("=" * 95)

    for config in tqdm(strategies, desc="Strategies", leave=False):
        r = run_strategy(config, cities, ensemble, actuals, calibration)
        all_results.append(r)
        print(f"{r['strategy']:25s} {r['n_trades']:7d} {r['n_wins']:5d} "
              f"{r['win_pct']:5.1f}% ${r['total_pnl']:+8.2f} "
              f"{r['max_drawdown']:6.1%} ${r['final_equity']:7.2f}")

    # Counterfactual March 9
    print("\n" + "=" * 95)
    print("COUNTERFACTUAL: March 9, 2026")
    print("=" * 95)

    for r in all_results:
        mar9 = [t for t in r["trades"] if t.date == "2026-03-09"]
        if not mar9:
            print(f"\n{r['strategy']}: 0 trades on Mar 9")
            continue
        mar9_pnl = sum(t.pnl for t in mar9)
        mar9_size = sum(t.size for t in mar9)
        mar9_wins = sum(1 for t in mar9 if t.won)
        print(f"\n{r['strategy']}: {len(mar9)} trades, {mar9_wins} wins, "
              f"invested ${mar9_size:.0f}, PnL ${mar9_pnl:+.2f}")
        for t in sorted(mar9, key=lambda x: -abs(x.pnl))[:5]:
            w = "WIN" if t.won else "LOSS"
            print(f"    {w}: {t.city:12s} {t.bucket_label:8s} "
                  f"σ={t.sigma:.1f} edge={t.edge:.0%} ${t.size:.0f} → ${t.pnl:+.0f}")

    # Save
    out = []
    for r in all_results:
        out.append({
            "strategy": r["strategy"],
            "n_trades": r["n_trades"],
            "n_wins": r["n_wins"],
            "win_pct": r["win_pct"],
            "total_pnl": r["total_pnl"],
            "max_drawdown": r["max_drawdown"],
            "final_equity": r["final_equity"],
        })
    with open(DATA_DIR / "backtest_results.json", "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nResults saved to {DATA_DIR / 'backtest_results.json'}")


if __name__ == "__main__":
    main()
