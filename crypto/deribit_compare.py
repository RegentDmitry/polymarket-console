#!/usr/bin/env python3
"""
Сравнение цен Polymarket с implied probability из Deribit опционов.

Deribit даёт implied volatility (IV) из реальных опционных цен.
Из IV можно посчитать:
  1. Terminal probability — P(BTC > K в конце периода) — из Black-Scholes N(d2)
  2. Touch probability — P(BTC коснётся K хотя бы раз) — из формулы first passage time

Polymarket контракты "Will BTC hit $X?" — это TOUCH (достаточно коснуться один раз).
Поэтому: touch_prob >= terminal_prob ВСЕГДА.
Если Polymarket цена < terminal_prob от Deribit — это гарантированный mispricing.

Использование:
    python crypto/deribit_compare.py
    python crypto/deribit_compare.py --drift 0.0   # нулевой drift (консервативно)
    python crypto/deribit_compare.py --drift 0.27   # наш базовый drift +27%
"""

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from urllib.request import urlopen
from urllib.error import URLError


def norm_cdf(x: float) -> float:
    """Standard normal CDF (без scipy)."""
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def terminal_prob_above(S: float, K: float, T: float, sigma: float, mu: float = 0.0) -> float:
    """P(S_T > K) — вероятность что BTC будет ВЫШЕ K в момент T.

    Black-Scholes N(d2) с заданным drift.
    mu = 0 для risk-neutral, mu > 0 для бычьего сценария.
    """
    if T <= 0 or sigma <= 0:
        return 1.0 if S > K else 0.0
    d2 = (math.log(S / K) + (mu - 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    return norm_cdf(d2)


def terminal_prob_below(S: float, K: float, T: float, sigma: float, mu: float = 0.0) -> float:
    """P(S_T < K) — вероятность что BTC будет НИЖЕ K в момент T."""
    return 1.0 - terminal_prob_above(S, K, T, sigma, mu)


def touch_prob_above(S: float, H: float, T: float, sigma: float, mu: float = 0.0) -> float:
    """P(max S_t >= H, t in [0,T]) — вероятность коснуться уровня H сверху.

    First passage time formula для GBM.
    """
    if S >= H:
        return 1.0
    if T <= 0 or sigma <= 0:
        return 0.0

    ln_ratio = math.log(H / S)
    drift = mu - 0.5 * sigma**2
    vol_sqrt_t = sigma * math.sqrt(T)

    d1 = (-ln_ratio + drift * T) / vol_sqrt_t
    d2 = (-ln_ratio - drift * T) / vol_sqrt_t

    # Reflection principle для GBM
    if abs(drift) < 1e-10:
        # Нулевой drift — упрощённая формула
        return 2 * norm_cdf(-ln_ratio / vol_sqrt_t)

    exponent = 2 * drift * ln_ratio / (sigma**2)
    # Клампим экспоненту чтобы избежать overflow
    exponent = min(exponent, 100)

    prob = norm_cdf(d1) + math.exp(exponent) * norm_cdf(d2)
    return min(prob, 1.0)


def touch_prob_below(S: float, H: float, T: float, sigma: float, mu: float = 0.0) -> float:
    """P(min S_t <= H, t in [0,T]) — вероятность коснуться уровня H снизу."""
    if S <= H:
        return 1.0
    if T <= 0 or sigma <= 0:
        return 0.0

    ln_ratio = math.log(H / S)
    drift = mu - 0.5 * sigma**2
    vol_sqrt_t = sigma * math.sqrt(T)

    d1 = (ln_ratio + drift * T) / vol_sqrt_t
    d2 = (ln_ratio - drift * T) / vol_sqrt_t

    if abs(drift) < 1e-10:
        return 2 * norm_cdf(ln_ratio / vol_sqrt_t)

    exponent = 2 * drift * ln_ratio / (sigma**2)
    exponent = min(exponent, 100)

    prob = norm_cdf(d1) + math.exp(exponent) * norm_cdf(d2)
    return min(prob, 1.0)


def fetch_deribit_data() -> dict:
    """Получить данные с Deribit API."""
    # Все BTC опционные инструменты
    url = "https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency=BTC&kind=option"
    try:
        with urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read())
    except URLError as e:
        print(f"Ошибка загрузки Deribit: {e}")
        sys.exit(1)

    if "result" not in data:
        print("Неожиданный формат ответа Deribit")
        sys.exit(1)

    return data["result"]


def parse_instrument(name: str) -> dict | None:
    """Парсит имя инструмента: BTC-25DEC26-100000-C → {expiry, strike, type}."""
    parts = name.split("-")
    if len(parts) != 4:
        return None

    _, expiry_str, strike_str, opt_type = parts

    # Парс даты
    try:
        expiry = datetime.strptime(expiry_str, "%d%b%y").replace(tzinfo=timezone.utc)
    except ValueError:
        return None

    try:
        strike = float(strike_str)
    except ValueError:
        return None

    return {
        "expiry": expiry,
        "expiry_str": expiry_str,
        "strike": strike,
        "type": opt_type,  # C or P
    }


def main():
    parser = argparse.ArgumentParser(description="Сравнение Polymarket vs Deribit implied probabilities")
    parser.add_argument("--drift", type=float, default=0.0,
                        help="Годовой drift (mu). 0.0 = risk-neutral, 0.27 = базовый бычий")
    parser.add_argument("--btc", type=float, default=None,
                        help="Текущая цена BTC (по умолчанию — из Deribit underlying)")
    args = parser.parse_args()

    print("Загрузка данных Deribit...")
    options = fetch_deribit_data()

    # Группируем по экспирации, ищем Dec 2026
    dec26_options = {}
    underlying_price = None

    for opt in options:
        name = opt.get("instrument_name", "")
        parsed = parse_instrument(name)
        if not parsed:
            continue

        # Dec 2026 (25DEC26)
        if "DEC26" not in name:
            continue

        if underlying_price is None and opt.get("underlying_price"):
            underlying_price = opt["underlying_price"]

        key = (parsed["strike"], parsed["type"])
        dec26_options[key] = {
            "name": name,
            "mark_price": opt.get("mark_price", 0),  # В BTC
            "mark_iv": opt.get("mark_iv", 0),  # В процентах
            "bid": opt.get("bid_price") or 0,
            "ask": opt.get("ask_price") or 0,
            **parsed,
        }

    if not dec26_options:
        print("Не найдены опционы Dec 2026!")
        sys.exit(1)

    btc_price = args.btc or underlying_price or 69000
    mu = args.drift

    # Время до экспирации Dec 25, 2026
    expiry = datetime(2026, 12, 25, 8, 0, tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    T = (expiry - now).total_seconds() / (365.25 * 24 * 3600)

    print(f"\nBTC: ${btc_price:,.0f}")
    print(f"Drift (mu): {mu:+.0%}")
    print(f"Время до экспирации: {T:.2f} лет ({(expiry - now).days} дней)")

    # Наши позиции на Polymarket
    positions = [
        # (название, strike, направление, PM цена YES, PM цена нашей стороны, наша сторона)
        ("BTC > $100k", 100000, "above", 0.43, 0.43, "YES"),
        ("BTC > $120k", 120000, "above", 0.24, 0.24, "YES"),
        ("BTC > $150k", 150000, "above", 0.10, 0.10, "YES"),
        ("BTC dip $55k", 55000, "below", 0.72, 0.28, "NO"),
    ]

    # Февральские (отдельная экспирация)
    feb_expiry = datetime(2026, 3, 1, 0, 0, tzinfo=timezone.utc)
    T_feb = (feb_expiry - now).total_seconds() / (365.25 * 24 * 3600)

    feb_positions = [
        ("BTC dip $60k Feb", 60000, "below", 0.33, 0.67, "NO"),
        ("BTC dip $55k Feb", 55000, "below", 0.16, 0.84, "NO"),
    ]

    # Извлекаем IV из Deribit для нужных страйков
    def get_iv_for_strike(strike: float, opt_type: str) -> float | None:
        """Найти IV для ближайшего страйка."""
        key = (strike, opt_type)
        if key in dec26_options:
            iv = dec26_options[key]["mark_iv"]
            return iv / 100 if iv else None

        # Ищем ближайший страйк
        best_key = None
        best_dist = float("inf")
        for (s, t), data in dec26_options.items():
            if t != opt_type:
                continue
            dist = abs(s - strike)
            if dist < best_dist:
                best_dist = dist
                best_key = (s, t)

        if best_key and best_dist / strike < 0.15:  # В пределах 15%
            iv = dec26_options[best_key]["mark_iv"]
            return iv / 100 if iv else None
        return None

    # Собираем все доступные IV для оценки средней волатильности
    all_ivs = [d["mark_iv"] / 100 for d in dec26_options.values() if d["mark_iv"] and d["mark_iv"] > 0]
    avg_iv = sum(all_ivs) / len(all_ivs) if all_ivs else 0.50

    # Вывод доступных страйков
    strikes_available = sorted(set(s for s, t in dec26_options.keys()))
    print(f"\nДоступные страйки Dec 2026: {', '.join(f'${s/1000:.0f}k' for s in strikes_available)}")
    print(f"Средняя IV: {avg_iv:.1%}")

    print("\n" + "=" * 100)
    print(f"  СРАВНЕНИЕ: Polymarket vs Deribit (Dec 25, 2026)")
    print(f"  BTC = ${btc_price:,.0f} | IV ~ {avg_iv:.0%} | drift = {mu:+.0%} | T = {T:.2f}y")
    print("=" * 100)

    header = f"{'Позиция':<20} {'IV':>6} {'Terminal':>10} {'Touch':>10} {'PM цена':>10} {'Edge(T)':>10} {'Edge(touch)':>12} {'Наша':>6}"
    print(header)
    print("-" * 100)

    for name, strike, direction, pm_yes, pm_ours, our_side in positions:
        # Определяем тип опциона для IV
        if direction == "above":
            iv = get_iv_for_strike(strike, "C")
        else:
            iv = get_iv_for_strike(strike, "P")

        sigma = iv if iv else avg_iv

        # Рассчитываем вероятности
        if direction == "above":
            term_prob = terminal_prob_above(btc_price, strike, T, sigma, mu)
            touch = touch_prob_above(btc_price, strike, T, sigma, mu)
            pm_display = pm_yes
        else:
            term_prob = terminal_prob_below(btc_price, strike, T, sigma, mu)
            touch = touch_prob_below(btc_price, strike, T, sigma, mu)
            pm_display = pm_yes  # YES цена (вероятность события)

        # Edge: Deribit implied - Polymarket price
        # Polymarket торгует touch, поэтому edge = touch_prob - pm_yes
        edge_terminal = term_prob - pm_display
        edge_touch = touch - pm_display

        iv_str = f"{sigma:.0%}" if iv else f"~{sigma:.0%}"

        # Цветовой маркер
        if edge_touch > 0.05:
            marker = ">>>"  # PM недооценивает
        elif edge_touch < -0.05:
            marker = "<<<"  # PM переоценивает
        else:
            marker = "  ="

        print(f"{name:<20} {iv_str:>6} {term_prob:>9.1%} {touch:>9.1%} {pm_display:>9.1%} "
              f"{edge_terminal:>+9.1%} {edge_touch:>+11.1%} {our_side:>6} {marker}")

    # Февральские
    if feb_positions:
        print(f"\n{'--- Февральские (до 1 марта 2026) ---':^100}")
        print(f"{'T = '}{T_feb:.3f}y ({(feb_expiry - now).days} дней)")
        print("-" * 100)

        for name, strike, direction, pm_yes, pm_ours, our_side in feb_positions:
            # Для февральских используем среднюю IV (нет отдельной экспирации)
            sigma = avg_iv

            if direction == "below":
                term_prob = terminal_prob_below(btc_price, strike, T_feb, sigma, mu)
                touch = touch_prob_below(btc_price, strike, T_feb, sigma, mu)
                pm_display = pm_yes
            else:
                term_prob = terminal_prob_above(btc_price, strike, T_feb, sigma, mu)
                touch = touch_prob_above(btc_price, strike, T_feb, sigma, mu)
                pm_display = pm_yes

            edge_terminal = term_prob - pm_display
            edge_touch = touch - pm_display

            if edge_touch > 0.05:
                marker = ">>>"
            elif edge_touch < -0.05:
                marker = "<<<"
            else:
                marker = "  ="

            print(f"{name:<20} {'~' + f'{sigma:.0%}':>5} {term_prob:>9.1%} {touch:>9.1%} {pm_display:>9.1%} "
                  f"{edge_terminal:>+9.1%} {edge_touch:>+11.1%} {our_side:>6} {marker}")

    # Сводка
    print("\n" + "=" * 100)
    print("ИНТЕРПРЕТАЦИЯ:")
    print("  Terminal = P(BTC закончит год выше/ниже K) — из Black-Scholes + Deribit IV")
    print("  Touch    = P(BTC коснётся K хотя бы раз за период) — из first passage time + Deribit IV")
    print("  PM цена  = implied probability на Polymarket (YES цена)")
    print("  Edge(T)  = Terminal - PM (если >0 → Deribit считает вероятнее чем PM)")
    print("  Edge(touch) = Touch - PM (если >0 → PM недооценивает, наш edge)")
    print()
    print("  >>> = PM недооценивает (edge для покупки YES)")
    print("  <<< = PM переоценивает (edge для покупки NO)")
    print("  =   = PM примерно справедлив (edge < 5%)")
    print()
    print("ВАЖНО: Touch >= Terminal ВСЕГДА. Если Terminal > PM → гарантированный mispricing.")
    print(f"       При drift=0 (risk-neutral) результат консервативный.")
    print(f"       При drift>0 (бычий) — все вероятности для upside выше.")


if __name__ == "__main__":
    main()
