#!/usr/bin/env python3
"""Full scan of BTC + ETH markets on Polymarket vs Deribit touch probabilities.

Three drift scenarios:
  d=0     — risk-neutral (conservative)
  d=fut   — implied from Deribit Dec 2026 futures curve
  d=27%   — our subjective model (5 scenarios weighted)

Plus Student-t Monte Carlo touch probability (fat tails, df=2.95).
"""

import argparse
import json
import math
import re
import ssl
import sys
import urllib.request
from datetime import datetime, timezone

import numpy as np
from scipy.stats import norm, t as student_t

from trading_bot.pricing.fast_approx import fast_touch_prob

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE


def deribit_get(path):
    req = urllib.request.Request(f"https://www.deribit.com/api/v2/public/{path}")
    resp = urllib.request.urlopen(req, timeout=10, context=ctx)
    return json.loads(resp.read())["result"]


def _fetch_iv_curve(currency: str, spot: float):
    """Fetch ATM IV curve from Deribit options (all expiries 7d+).

    Returns (headline_iv, iv_curve) where:
    - headline_iv: IV from nearest 7d+ expiry (for display)
    - iv_curve: [(days, iv), ...] for per-maturity matching

    Short-dated (<7d) options excluded — IV swings 20+ pts/day.
    """
    try:
        instruments = deribit_get(
            f"get_instruments?currency={currency}&kind=option&expired=false"
        )
        now_ts = datetime.now(timezone.utc).timestamp() * 1000
        min_exp_ts = now_ts + 7 * 86400 * 1000  # at least 7 days out

        # Group instruments by expiry (only 7d+)
        expiry_groups = {}
        for inst in instruments:
            exp = inst["expiration_timestamp"]
            if exp >= min_exp_ts:
                expiry_groups.setdefault(exp, []).append(inst)

        # Fallback: if no 7d+ expiry, use absolute nearest
        if not expiry_groups:
            for inst in instruments:
                exp = inst["expiration_timestamp"]
                if exp > now_ts:
                    expiry_groups.setdefault(exp, []).append(inst)

        if not expiry_groups:
            return 0.50, []

        # For each expiry, find ATM call IV
        iv_curve = []
        for exp_ts in sorted(expiry_groups.keys()):
            days = max(1, int((exp_ts - now_ts) / 86400000))
            best_iv = 0.0
            best_dist = float('inf')
            for inst in expiry_groups[exp_ts]:
                if inst["option_type"] != "call":
                    continue
                dist = abs(inst["strike"] - spot)
                if dist < best_dist:
                    try:
                        ticker = deribit_get(
                            f"ticker?instrument_name={inst['instrument_name']}"
                        )
                        iv = ticker.get("mark_iv", 0)
                        if iv > 0:
                            best_iv = iv / 100
                            best_dist = dist
                    except Exception:
                        pass
            if best_iv > 0:
                iv_curve.append((days, best_iv))

        headline_iv = iv_curve[0][1] if iv_curve else 0.50
        return headline_iv, iv_curve
    except Exception:
        return 0.50, []


def iv_for_days(iv_curve, target_days, headline_iv=0.50):
    """Get IV from the expiry closest to target_days."""
    if not iv_curve:
        return headline_iv
    target = max(target_days, 7)
    best = min(iv_curve, key=lambda x: abs(x[0] - target))
    return best[1]


def fetch_futures_curve(currency="BTC"):
    """Get full futures curve: list of (days_to_expiry, implied_annual_drift, price, name)."""
    try:
        futures = deribit_get(f"get_book_summary_by_currency?currency={currency}&kind=future")
        spot = None
        curve = []
        for f in futures:
            name = f["instrument_name"]
            if name == f"{currency}-PERPETUAL":
                spot = f.get("last") or f.get("mark_price")

        if not spot:
            return None, []

        now = datetime.now(timezone.utc)
        for f in futures:
            name = f["instrument_name"]
            price = f.get("last") or f.get("mark_price") or 0
            if not price or "PERPETUAL" in name:
                continue
            parts = name.split("-")
            if len(parts) >= 2:
                try:
                    exp = datetime.strptime(parts[1], "%d%b%y").replace(tzinfo=timezone.utc)
                    days = (exp - now).days
                    if days <= 0:
                        continue
                    T = days / 365
                    drift = math.log(price / spot) / T
                    curve.append((days, drift, price, name))
                except ValueError:
                    pass
        curve.sort(key=lambda x: x[0])
        return spot, curve
    except Exception:
        return None, []


def drift_for_days(curve, target_days):
    """Interpolate drift from futures curve for a given number of days."""
    if not curve:
        return 0.04  # fallback
    # Find closest futures by days
    best = min(curve, key=lambda x: abs(x[0] - target_days))
    return best[1]


def touch_above(S, K, sigma, T, mu=0):
    if K <= S:
        return 1.0
    if T <= 0 or sigma <= 0:
        return 0.0
    ln = math.log(K / S)
    st = sigma * math.sqrt(T)
    drift = mu - 0.5 * sigma**2
    if abs(drift) < 1e-10:
        return 2 * (1 - norm.cdf(ln / st))
    d1 = (-ln + drift * T) / st
    d2 = (-ln - drift * T) / st
    exp = min(2 * drift * ln / sigma**2, 100)
    return min(norm.cdf(d1) + math.exp(exp) * norm.cdf(d2), 1.0)


def touch_below(S, K, sigma, T, mu=0):
    if K >= S:
        return 1.0
    if T <= 0 or sigma <= 0:
        return 0.0
    drift = mu - 0.5 * sigma**2
    ln_ks = math.log(K / S)
    st = sigma * math.sqrt(T)
    if abs(drift) < 1e-10:
        return 2 * (1 - norm.cdf(-ln_ks / st))
    d1 = (ln_ks + drift * T) / st
    d2 = (ln_ks - drift * T) / st
    exp = min(2 * drift * ln_ks / sigma**2, 100)
    return min(norm.cdf(d1) + math.exp(exp) * norm.cdf(d2), 1.0)


def calc_edge(spot, strike, iv, T, pm, is_up, mu):
    if is_up:
        tp = touch_above(spot, strike, iv, T, mu=mu)
        return tp - pm
    else:
        tp = touch_below(spot, strike, iv, T, mu=mu)
        return (1 - tp) - pm


# --- Student-t Monte Carlo touch probability ---
MC_PATHS = 150_000
# Calibrated from 1500 days Deribit perpetual data (2022-02 to 2026-02)
STUDENT_DF_BTC = 2.61
STUDENT_DF_ETH = 2.88


def mc_touch_prob(spot, strike, iv, T, mu=0, n_paths=MC_PATHS, df=STUDENT_DF_BTC):
    """Monte Carlo touch probability using Student-t innovations (fat tails).

    Simulates daily log-returns with Student-t distribution scaled to match IV.
    Returns P(path touches strike at any point during T).
    """
    if T <= 0 or iv <= 0:
        return 1.0 if strike <= spot else 0.0

    n_days = max(int(T * 365), 1)
    dt = T / n_days

    # Scale Student-t so that its variance matches iv^2 * dt
    # Var(t_df) = df/(df-2) for df>2
    t_var = df / (df - 2)
    scale = iv * math.sqrt(dt / t_var)
    drift_per_step = (mu - 0.5 * iv**2) * dt

    # Generate all innovations at once: (n_paths, n_days)
    innovations = student_t.rvs(df, scale=scale, size=(n_paths, n_days))
    log_returns = drift_per_step + innovations

    # Cumulative sum → log(S_t/S_0)
    cum_log = np.cumsum(log_returns, axis=1)
    # Price paths
    prices = spot * np.exp(cum_log)

    is_up = strike > spot
    if is_up:
        touched = np.any(prices >= strike, axis=1)
    else:
        touched = np.any(prices <= strike, axis=1)

    return float(np.mean(touched))


def mc_edge(spot, strike, iv, T, pm, is_up, mu=0, df=STUDENT_DF_BTC):
    """Edge using MC Student-t touch probability."""
    tp = mc_touch_prob(spot, strike, iv, T, mu=mu, df=df)
    if is_up:
        return tp - pm
    else:
        return (1 - tp) - pm


def hybrid_edge(spot, strike, iv, T, pm, is_up, mu=0, df=STUDENT_DF_BTC):
    """Edge using hybrid model: Student-t for dip, GBM for reach.

    Backtest-optimal: Student-t wins on dip, GBM wins on reach.
    Uses fast analytical approx (not MC).
    """
    tp = fast_touch_prob(spot, strike, iv, T, drift=mu, df=df, hybrid=True)
    if is_up:
        return tp - pm
    else:
        return (1 - tp) - pm


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--futures-only", action="store_true", help="Only show futures curve")
    args = parser.parse_args()

    # Fetch Deribit data
    try:
        S_BTC = deribit_get("ticker?instrument_name=BTC-PERPETUAL")["last_price"]
    except Exception:
        S_BTC = 68000
    try:
        S_ETH = deribit_get("ticker?instrument_name=ETH-PERPETUAL")["last_price"]
    except Exception:
        S_ETH = 1970

    # Fetch futures curves for both assets
    spot_btc_fut, btc_curve = fetch_futures_curve("BTC")
    spot_eth_fut, eth_curve = fetch_futures_curve("ETH")

    # Fetch ATM IV curves from Deribit (per-maturity)
    IV_BTC, btc_iv_curve = _fetch_iv_curve("BTC", S_BTC)
    IV_ETH, eth_iv_curve = _fetch_iv_curve("ETH", S_ETH)
    T_annual = 306 / 365

    print(f"BTC Deribit: ${S_BTC:,.0f} | IV: {IV_BTC*100:.1f}% (headline) | df={STUDENT_DF_BTC}")
    print(f"ETH Deribit: ${S_ETH:,.0f} | IV: {IV_ETH*100:.1f}% (headline) | df={STUDENT_DF_ETH}")
    if btc_iv_curve:
        iv_str = ", ".join(f"{d}d:{iv*100:.1f}%" for d, iv in btc_iv_curve)
        print(f"  BTC IV curve: {iv_str}")
    if eth_iv_curve:
        iv_str = ", ".join(f"{d}d:{iv*100:.1f}%" for d, iv in eth_iv_curve)
        print(f"  ETH IV curve: {iv_str}")
    print()

    # Print both futures curves
    for label, curve, spot in [("BTC", btc_curve, S_BTC), ("ETH", eth_curve, S_ETH)]:
        print(f"  {label} futures curve:")
        for days, drift, price, name in curve:
            prem = (price / spot - 1) * 100
            print(f"    {name:<20} ${price:>8,.0f}  {prem:>+5.2f}%  drift {drift*100:>+5.1f}%/yr  ({days}d)")
    print()

    if args.futures_only:
        return

    # Full scan — IV matched per market maturity
    slugs = [
        ("what-price-will-bitcoin-hit-before-2027", "BTC", S_BTC, T_annual, STUDENT_DF_BTC, btc_curve, btc_iv_curve, IV_BTC),
        ("what-price-will-bitcoin-hit-in-march-2026", "BTC", S_BTC, 37 / 365, STUDENT_DF_BTC, btc_curve, btc_iv_curve, IV_BTC),
        ("what-price-will-ethereum-hit-before-2027", "ETH", S_ETH, T_annual, STUDENT_DF_ETH, eth_curve, eth_iv_curve, IV_ETH),
        ("what-price-will-ethereum-hit-in-march-2026", "ETH", S_ETH, 37 / 365, STUDENT_DF_ETH, eth_curve, eth_iv_curve, IV_ETH),
        ("what-price-will-bitcoin-hit-in-february-2026", "BTC", S_BTC, 6 / 365, STUDENT_DF_BTC, btc_curve, btc_iv_curve, IV_BTC),
        ("what-price-will-ethereum-hit-in-february-2026", "ETH", S_ETH, 6 / 365, STUDENT_DF_ETH, eth_curve, eth_iv_curve, IV_ETH),
    ]

    results = []
    for slug, currency, spot, T, df, curve, cur_iv_curve, headline_iv in slugs:
        try:
            url = f"https://gamma-api.polymarket.com/events?slug={slug}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            resp = urllib.request.urlopen(req, timeout=10)
            data = json.loads(resp.read())
            if not data:
                continue
            for m in data[0].get("markets", []):
                if m.get("closed") or not m.get("active"):
                    continue
                q = m.get("question", "")
                try:
                    prices = json.loads(m.get("outcomePrices", "[]"))
                    yes_p = float(prices[0])
                except Exception:
                    continue

                # Skip double-barrier "what hits first" markets
                q_lower = q.lower()
                if "first" in q_lower and q_lower.count("$") >= 2:
                    continue

                dollar_match = re.search(r"\$([0-9,]+)", q)
                if dollar_match:
                    strike = int(dollar_match.group(1).replace(",", ""))
                else:
                    continue
                if strike < 100 or 2020 < strike < 2030:
                    continue

                is_up = strike > spot
                pm = yes_p if is_up else (1 - yes_p)

                days_to_exp = int(T * 365)
                # Per-maturity IV and drift
                iv = iv_for_days(cur_iv_curve, days_to_exp, headline_iv)
                drift_matched = drift_for_days(curve, days_to_exp)
                edge_mc0 = mc_edge(spot, strike, iv, T, pm, is_up, mu=0.0, df=df)
                edge_mcf = mc_edge(spot, strike, iv, T, pm, is_up, mu=drift_matched, df=df)
                edge_hyb = hybrid_edge(spot, strike, iv, T, pm, is_up, mu=drift_matched, df=df)

                period = (
                    "Feb" if "february" in slug
                    else ("Mar" if "march" in slug else "Annual")
                )
                direction = "↑" if is_up else "↓"
                results.append({
                    "market": f"{currency} {direction} ${strike:,}",
                    "period": period,
                    "side": "YES" if is_up else "NO",
                    "pm": pm,
                    "edge_mc0": edge_mc0,
                    "edge_mcf": edge_mcf,
                    "edge_hyb": edge_hyb,
                    "drift_used": drift_matched,
                    "iv_used": iv,
                })
        except Exception as e:
            print(f"  [{slug}]: {e}")

    results.sort(key=lambda x: x["edge_hyb"], reverse=True)

    print(f"Найдено {len(results)} активных рынков")
    print(f"MC Student-t: BTC df={STUDENT_DF_BTC}, ETH df={STUDENT_DF_ETH}, {MC_PATHS:,} paths")
    print(f"Hybrid = Student-t(dip) + GBM(reach) + per-maturity IV & drift")
    print()
    hdr = (
        f"{'Рынок':<25} {'Пер':<6} {'St':<4} {'PM':<6}"
        f" {'MC d=0':>8} {'MC fut':>8} {'Hybrid':>8} {'IV':>5} {'d_fut':>6}   {'Вердикт':<22}"
    )
    print(hdr)
    print("=" * 115)
    for r in results:
        pm_str = f"{r['pm']*100:.0f}c"
        mc0, mcf, hyb = r["edge_mc0"], r["edge_mcf"], r["edge_hyb"]
        d_str = f"{r['drift_used']*100:+.1f}%"
        iv_str = f"{r['iv_used']*100:.0f}%"
        if hyb > 0.05:
            verdict = "*** HYBRID EDGE ***"
        elif hyb > 0.03:
            verdict = "hybrid edge"
        elif mcf > 0.03:
            verdict = "MC fut edge"
        elif mcf > 0.0:
            verdict = "нужен drift"
        else:
            verdict = "—"
        print(
            f"{r['market']:<25} {r['period']:<6} {r['side']:<4} {pm_str:<6}"
            f" {mc0*100:>+6.1f}%  {mcf*100:>+6.1f}%  {hyb*100:>+6.1f}%  {iv_str:>5} {d_str:>6}   {verdict}"
        )

    # Save raw data
    import os
    raw_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "raw")
    os.makedirs(raw_dir, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    raw_path = os.path.join(raw_dir, f"{today}.json")
    raw_data = {
        "date": today,
        "btc_spot": S_BTC,
        "eth_spot": S_ETH,
        "iv_btc": IV_BTC,
        "iv_eth": IV_ETH,
        "btc_iv_curve": btc_iv_curve,
        "eth_iv_curve": eth_iv_curve,
        "df_btc": STUDENT_DF_BTC,
        "df_eth": STUDENT_DF_ETH,
        "mc_paths": MC_PATHS,
        "btc_futures": [(d, dr, p, n) for d, dr, p, n in btc_curve],
        "eth_futures": [(d, dr, p, n) for d, dr, p, n in eth_curve],
        "results": results,
    }
    with open(raw_path, "w") as f:
        json.dump(raw_data, f, indent=2)
    print(f"\nRaw data saved: {raw_path}")


if __name__ == "__main__":
    main()
