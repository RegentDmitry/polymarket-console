#!/usr/bin/env python3
"""Full scan of BTC + ETH markets on Polymarket vs Deribit touch probabilities."""

import json
import math
import re
import ssl
import urllib.request
from scipy.stats import norm

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE


def fetch_deribit_price(instrument):
    try:
        req = urllib.request.Request(
            f"https://www.deribit.com/api/v2/public/ticker?instrument_name={instrument}"
        )
        resp = urllib.request.urlopen(req, timeout=10, context=ctx)
        return json.loads(resp.read())["result"]["last_price"]
    except Exception:
        return None


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
    ln = math.log(S / K)
    st = sigma * math.sqrt(T)
    drift = mu - 0.5 * sigma**2
    if abs(drift) < 1e-10:
        return 2 * (1 - norm.cdf(ln / st))
    d1 = (math.log(K / S) + drift * T) / st
    d2 = (math.log(K / S) - drift * T) / st
    exp = min(2 * drift * math.log(K / S) / sigma**2, 100)
    return min(norm.cdf(d1) + math.exp(exp) * norm.cdf(d2), 1.0)


def main():
    S_BTC = fetch_deribit_price("BTC-PERPETUAL") or 70300
    S_ETH = fetch_deribit_price("ETH-PERPETUAL") or 1970

    IV_BTC = 0.522
    IV_ETH = 0.70  # ETH DVOL typically higher

    T_annual = 306 / 365

    print(f"BTC Deribit: ${S_BTC:,.0f} | IV: {IV_BTC*100:.1f}%")
    print(f"ETH Deribit: ${S_ETH:,.0f} | IV: {IV_ETH*100:.1f}%")
    print()

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

                # Parse strike
                dollar_match = re.search(r"\$([0-9,]+)", q)
                if dollar_match:
                    strike = int(dollar_match.group(1).replace(",", ""))
                else:
                    continue

                if strike < 100:
                    continue
                if 2020 < strike < 2030:
                    continue

                is_up = strike > spot

                # Calculate for both drifts
                for drift_val in [0.0, 0.27]:
                    if is_up:
                        tp = touch_above(spot, strike, iv, T, mu=drift_val)
                    else:
                        tp = touch_below(spot, strike, iv, T, mu=drift_val)
                    if drift_val == 0.0:
                        tp_d0 = tp
                    else:
                        tp_d27 = tp

                if is_up:
                    side = "YES"
                    pm = yes_p
                    edge_d0 = tp_d0 - pm
                    edge_d27 = tp_d27 - pm
                else:
                    side = "NO"
                    pm = 1 - yes_p
                    edge_d0 = (1 - tp_d0) - pm
                    edge_d27 = (1 - tp_d27) - pm

                period = (
                    "Feb"
                    if "february" in slug
                    else ("Mar" if "march" in slug else "Annual")
                )
                direction = "↑" if is_up else "↓"
                results.append(
                    {
                        "market": f"{currency} {direction} ${strike:,}",
                        "period": period,
                        "side": side,
                        "pm": pm,
                        "edge_d0": edge_d0,
                        "edge_d27": edge_d27,
                    }
                )
        except Exception as e:
            print(f"  [{slug}]: {e}")

    # Sort by edge_d0
    results.sort(key=lambda x: x["edge_d0"], reverse=True)

    print(f"Найдено {len(results)} активных рынков")
    print()
    fmt = "{:<25} {:<6} {:<4} {:<6} {:>8} {:>10}   {:<20}"
    print(fmt.format("Рынок", "Пер", "Стор", "PM", "Edge d=0", "Edge d=27%", "Вердикт"))
    print("=" * 85)
    for r in results:
        pm_str = f"{r['pm']*100:.0f}c"
        if r["edge_d0"] > 0.03 and r["edge_d27"] > 0.03:
            verdict = "*** EDGE ОБА ***"
        elif r["edge_d0"] > 0.0 and r["edge_d27"] > 0.0:
            verdict = "слабый edge оба"
        elif r["edge_d0"] < -0.02 and r["edge_d27"] > 0.05:
            verdict = "ТОЛЬКО d=27%"
        elif r["edge_d27"] > 0.0:
            verdict = "нужен drift"
        elif r["edge_d0"] > 0.0:
            verdict = "edge без drift"
        else:
            verdict = "—"
        print(
            fmt.format(
                r["market"],
                r["period"],
                r["side"],
                pm_str,
                f"{r['edge_d0']*100:+.1f}%",
                f"{r['edge_d27']*100:+.1f}%",
                verdict,
            )
        )


if __name__ == "__main__":
    main()
