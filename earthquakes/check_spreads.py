#!/usr/bin/env python3
"""
Проверка bid-ask спредов на earthquake рынках Polymarket.
"""

import sys
sys.path.insert(0, '.')

from polymarket_client import PolymarketClient


def get_orderbook_safe(poly, token_id: str) -> tuple[list, list]:
    """Безопасно получить orderbook, обрабатывая разные форматы ответа."""
    try:
        ob = poly.get_orderbook(token_id)

        # Может быть объект OrderBookSummary или dict
        if hasattr(ob, 'asks'):
            asks = ob.asks or []
            bids = ob.bids or []
        elif isinstance(ob, dict):
            asks = ob.get("asks", [])
            bids = ob.get("bids", [])
        else:
            return [], []

        # Нормализуем формат (может быть list of dicts или list of objects)
        def normalize(items):
            result = []
            for item in items:
                if hasattr(item, 'price'):
                    result.append({"price": item.price, "size": item.size})
                elif isinstance(item, dict):
                    result.append(item)
            return result

        return normalize(asks), normalize(bids)
    except Exception as e:
        return [], []


def check_spreads():
    """Проверить спреды на всех earthquake рынках."""
    poly = PolymarketClient()

    print("=" * 80)
    print("BID-ASK СПРЕДЫ НА EARTHQUAKE РЫНКАХ")
    print("=" * 80)
    print()

    all_prices = poly.get_all_earthquake_prices()

    total_spreads = []

    for event_slug, market_list in all_prices.items():
        print(f"\n{event_slug}")
        print("-" * 60)

        for market in market_list:
            # Пропускаем закрытые рынки
            if not market.active:
                continue

            print(f"  {market.question}:")

            for outcome in market.outcomes:
                token_id = outcome.token_id
                if not token_id:
                    continue

                outcome_name = outcome.outcome_name

                asks, bids = get_orderbook_safe(poly, token_id)

                if asks and bids:
                    best_ask = min(float(a["price"]) for a in asks)
                    best_bid = max(float(b["price"]) for b in bids)
                    spread = best_ask - best_bid
                    spread_pct = (spread / best_ask) * 100 if best_ask > 0 else 0
                    mid_price = (best_ask + best_bid) / 2

                    # Ликвидность на лучших уровнях
                    best_ask_size = sum(float(a["size"]) for a in asks if float(a["price"]) == best_ask)
                    best_bid_size = sum(float(b["size"]) for b in bids if float(b["price"]) == best_bid)

                    print(f"    {outcome_name}:")
                    print(f"      Best Bid: {best_bid:.3f} (${best_bid_size * best_bid:.0f})")
                    print(f"      Best Ask: {best_ask:.3f} (${best_ask_size * best_ask:.0f})")
                    print(f"      Spread:   {spread:.3f} ({spread_pct:.1f}%)")
                    print(f"      Mid:      {mid_price:.3f}")

                    total_spreads.append({
                        "market": f"{event_slug}/{market.question}/{outcome_name}",
                        "spread": spread,
                        "spread_pct": spread_pct,
                        "mid_price": mid_price,
                        "ask_liquidity": best_ask_size * best_ask,
                        "bid_liquidity": best_bid_size * best_bid,
                    })
                elif asks:
                    best_ask = min(float(a["price"]) for a in asks)
                    print(f"    {outcome_name}: Ask only @ {best_ask:.3f} (no bids)")
                elif bids:
                    best_bid = max(float(b["price"]) for b in bids)
                    print(f"    {outcome_name}: Bid only @ {best_bid:.3f} (no asks)")
                else:
                    print(f"    {outcome_name}: Empty orderbook")

    if total_spreads:
        print("\n" + "=" * 80)
        print("СТАТИСТИКА СПРЕДОВ")
        print("=" * 80)

        spreads_pct = [s["spread_pct"] for s in total_spreads]

        print(f"\nВсего рынков с двусторонней ликвидностью: {len(total_spreads)}")
        print(f"Минимальный спред: {min(spreads_pct):.1f}%")
        print(f"Максимальный спред: {max(spreads_pct):.1f}%")
        print(f"Средний спред: {sum(spreads_pct) / len(spreads_pct):.1f}%")
        print(f"Медианный спред: {sorted(spreads_pct)[len(spreads_pct)//2]:.1f}%")

        # Топ-5 самых узких спредов
        print("\nТоп-5 самых узких спредов:")
        for s in sorted(total_spreads, key=lambda x: x["spread_pct"])[:5]:
            print(f"  {s['spread_pct']:.1f}% - {s['market']}")

        # Топ-5 самых широких спредов
        print("\nТоп-5 самых широких спредов:")
        for s in sorted(total_spreads, key=lambda x: x["spread_pct"], reverse=True)[:5]:
            print(f"  {s['spread_pct']:.1f}% - {s['market']}")

        print("\n" + "=" * 80)
        print("ВЫВОДЫ ДЛЯ MIN_EDGE")
        print("=" * 80)

        avg_spread = sum(spreads_pct) / len(spreads_pct)

        print(f"""
При входе и выходе из позиции платишь ~половину спреда каждый раз.
Средний спред: {avg_spread:.1f}%
Стоимость round-trip (вход + выход): ~{avg_spread:.1f}%

Рекомендуемый MIN_EDGE должен покрывать:
  1. Спред (round-trip): {avg_spread:.1f}%
  2. Model uncertainty: ~2-3%
  3. Буфер безопасности: ~1%

  Итого рекомендуемый MIN_EDGE: {avg_spread + 3:.0f}-{avg_spread + 5:.0f}%

Текущий MIN_EDGE = 0.5% — {"СЛИШКОМ МАЛ" if avg_spread > 1 else "OK"}
""")


if __name__ == "__main__":
    check_spreads()
