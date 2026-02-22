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

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE


def deribit_get(path):
    req = urllib.request.Request(f"https://www.deribit.com/api/v2/public/{path}")
    resp = urllib.request.urlopen(req, timeout=10, context=ctx)
    return json.loads(resp.read())["result"]


def fetch_futures_drift():
    """Get implied annual drift from Deribit Dec 2026 futures vs perpetual."""
    try:
        futures = deribit_get("get_book_summary_by_currency?currency=BTC&kind=future")
        spot = None
        dec_price = None
        for f in futures:
            name = f["instrument_name"]
            if name == "BTC-PERPETUAL":
                spot = f.get("last") or f.get("mark_price")
            if "DEC26" in name:
                dec_price = f.get("last") or f.get("mark_price")

        if not spot or not dec_price:
            return None, None, None

        now = datetime.now(timezone.utc)
        exp = datetime(2026, 12, 25, 8, 0, tzinfo=timezone.utc)
        T = (exp - now).total_seconds() / (365.25 * 24 * 3600)
        drift = math.log(dec_price / spot) / T
        return drift, dec_price, spot
    except Exception:
        return None, None, None


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
MC_PATHS = 50_000
STUDENT_DF = 2.95  # from btc_model.ipynb calibration


def mc_touch_prob(spot, strike, iv, T, mu=0, n_paths=MC_PATHS, df=STUDENT_DF):
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


def mc_edge(spot, strike, iv, T, pm, is_up, mu=0):
    """Edge using MC Student-t touch probability."""
    tp = mc_touch_prob(spot, strike, iv, T, mu=mu)
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

    drift_fut, fut_price, fut_spot = fetch_futures_drift()
    if drift_fut is None:
        drift_fut = 0.04  # fallback ~4%
        fut_price = S_BTC * 1.033
        fut_spot = S_BTC
        print("WARNING: Deribit futures unavailable, using fallback drift=4%")

    IV_BTC = 0.522
    IV_ETH = 0.70
    T_annual = 306 / 365

    print(f"BTC Deribit: ${S_BTC:,.0f} | IV: {IV_BTC*100:.1f}%")
    print(f"ETH Deribit: ${S_ETH:,.0f} | IV: {IV_ETH*100:.1f}%")
    print(f"Futures Dec26: ${fut_price:,.0f} (spot ${fut_spot:,.0f}, +{(fut_price/fut_spot-1)*100:.1f}%)")
    print(f"Implied drift: {drift_fut*100:+.1f}%/yr")
    print(f"Our drift:     +27.0%/yr")
    print()

    if args.futures_only:
        # Show full futures curve
        try:
            futures = deribit_get("get_book_summary_by_currency?currency=BTC&kind=future")
            now = datetime.now(timezone.utc)
            print(f"{'Фьючерс':<20} {'Цена':<12} {'Премия':<10} {'Drift':<14}")
            print("=" * 58)
            for f in sorted(futures, key=lambda x: x["instrument_name"]):
                name = f["instrument_name"]
                price = f.get("last") or f.get("mark_price") or 0
                if not price:
                    continue
                prem = (price / S_BTC - 1) * 100
                # Parse expiry from name
                parts = name.split("-")
                if len(parts) >= 2 and parts[1] != "PERPETUAL":
                    try:
                        exp = datetime.strptime(parts[1], "%d%b%y").replace(tzinfo=timezone.utc)
                        days = (exp - now).days
                        T = days / 365
                        annual = math.log(price / S_BTC) / T * 100 if T > 0 else 0
                        print(f"{name:<20} ${price:>8,.0f}   {prem:>+5.2f}%     {annual:>+5.1f}%/yr ({days}d)")
                    except ValueError:
                        print(f"{name:<20} ${price:>8,.0f}   {prem:>+5.2f}%")
                else:
                    print(f"{name:<20} ${price:>8,.0f}   (spot)")
        except Exception as e:
            print(f"Error: {e}")
        return

    # Full scan
    slugs = [
        ("what-price-will-bitcoin-hit-before-2027", "BTC", S_BTC, IV_BTC, T_annual),
        ("what-price-will-bitcoin-hit-in-march-2026", "BTC", S_BTC, IV_BTC, 37 / 365),
        ("what-price-will-ethereum-hit-before-2027", "ETH", S_ETH, IV_ETH, T_annual),
        ("what-price-will-ethereum-hit-in-march-2026", "ETH", S_ETH, IV_ETH, 37 / 365),
        ("what-price-will-bitcoin-hit-in-february-2026", "BTC", S_BTC, IV_BTC, 6 / 365),
        ("what-price-will-ethereum-hit-in-february-2026", "ETH", S_ETH, IV_ETH, 6 / 365),
    ]

    results = []
    for slug, currency, spot, iv, T in slugs:
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

                dollar_match = re.search(r"\$([0-9,]+)", q)
                if dollar_match:
                    strike = int(dollar_match.group(1).replace(",", ""))
                else:
                    continue
                if strike < 100 or 2020 < strike < 2030:
                    continue

                is_up = strike > spot
                pm = yes_p if is_up else (1 - yes_p)

                edge_d0 = calc_edge(spot, strike, iv, T, pm, is_up, 0.0)
                edge_fut = calc_edge(spot, strike, iv, T, pm, is_up, drift_fut)
                edge_d27 = calc_edge(spot, strike, iv, T, pm, is_up, 0.27)
                edge_mc = mc_edge(spot, strike, iv, T, pm, is_up, mu=0.0)

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
                    "edge_d0": edge_d0,
                    "edge_fut": edge_fut,
                    "edge_d27": edge_d27,
                    "edge_mc": edge_mc,
                })
        except Exception as e:
            print(f"  [{slug}]: {e}")

    results.sort(key=lambda x: x["edge_d0"], reverse=True)

    print(f"Найдено {len(results)} активных рынков")
    print(f"MC Student-t: df={STUDENT_DF}, {MC_PATHS:,} paths, drift=0")
    print()
    hdr = (
        f"{'Рынок':<25} {'Пер':<6} {'St':<4} {'PM':<6}"
        f" {'Edge d=0':>8} {'Edge fut':>9} {'Edge d=27':>9} {'MC t-dist':>9}   {'Вердикт':<22}"
    )
    print(hdr)
    print("=" * 108)
    for r in results:
        pm_str = f"{r['pm']*100:.0f}c"
        e0, ef, e27, emc = r["edge_d0"], r["edge_fut"], r["edge_d27"], r["edge_mc"]
        if e0 > 0.03 and ef > 0.03:
            verdict = "*** EDGE d=0 + fut ***"
        elif e0 > 0.0 and ef > 0.0:
            verdict = "edge d=0 + fut"
        elif ef > 0.03 and e27 > 0.03:
            verdict = "edge fut + d=27"
        elif ef > 0.0:
            verdict = "edge при fut"
        elif e27 > 0.05:
            verdict = "ТОЛЬКО d=27%"
        elif e27 > 0.0:
            verdict = "нужен drift"
        else:
            verdict = "—"
        print(
            f"{r['market']:<25} {r['period']:<6} {r['side']:<4} {pm_str:<6}"
            f" {e0*100:>+6.1f}%  {ef*100:>+6.1f}%   {e27*100:>+6.1f}%  {emc*100:>+6.1f}%   {verdict}"
        )


if __name__ == "__main__":
    main()
