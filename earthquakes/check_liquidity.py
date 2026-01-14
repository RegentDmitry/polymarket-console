#!/usr/bin/env python3
"""
Проверка ликвидности earthquake рынков на Polymarket.
"""

import httpx
from polymarket_client import PolymarketClient

CLOB_URL = "https://clob.polymarket.com"

# Известные earthquake события
EARTHQUAKE_EVENTS = [
    "how-many-7pt0-or-above-earthquakes-by-june-30",
    "how-many-7pt0-or-above-earthquakes-in-2026",
    "10pt0-or-above-earthquake-before-2027",
    "9pt0-or-above-earthquake-before-2027",
    "another-7pt0-or-above-earthquake-by-555",
    "how-many-6pt5-or-above-earthquakes-by-january-4",
    # Megaquake (M8.0+)
    "megaquake-by-january-31",
    "megaquake-by-march-31",
    "megaquake-by-june-30",
]


def get_orderbook_liquidity(token_id: str, max_price: float = 1.0) -> tuple[float, float]:
    """
    Получить ликвидность в ордербуке.

    Returns:
        (ask_liquidity, bid_liquidity) в USD
    """
    try:
        response = httpx.get(
            f"{CLOB_URL}/book",
            params={"token_id": token_id},
            timeout=30,
        )
        if response.status_code != 200:
            return 0.0, 0.0

        ob = response.json()

        ask_liquidity = 0.0
        for ask in ob.get("asks", []):
            price = float(ask.get("price", 0))
            size = float(ask.get("size", 0))
            if price <= max_price:
                ask_liquidity += price * size

        bid_liquidity = 0.0
        for bid in ob.get("bids", []):
            price = float(bid.get("price", 0))
            size = float(bid.get("size", 0))
            bid_liquidity += price * size

        return ask_liquidity, bid_liquidity
    except Exception as e:
        print(f"  Ошибка: {e}")
        return 0.0, 0.0


def check_all_liquidity():
    """Проверить ликвидность всех earthquake рынков."""
    print("=" * 70)
    print("ЛИКВИДНОСТЬ EARTHQUAKE РЫНКОВ")
    print("=" * 70)

    poly = PolymarketClient()
    total_yes_liquidity = 0.0
    total_no_liquidity = 0.0

    for event_slug in EARTHQUAKE_EVENTS:
        event = poly.get_event_by_slug(event_slug)
        if not event:
            print(f"\n{event_slug}: НЕ НАЙДЕН")
            continue

        print(f"\n{'=' * 70}")
        print(f"EVENT: {event_slug}")
        print("=" * 70)

        markets = event.get("markets", [])
        for market in markets:
            question = market.get("question", "")[:55]
            condition_id = market.get("conditionId")
            closed = market.get("closed", False)

            if closed:
                continue

            print(f"\n  {question}...")

            # Получаем данные из CLOB
            clob_market = poly.get_clob_market(condition_id)
            if not clob_market:
                print("    CLOB: не найден")
                continue

            if not clob_market.get("enable_order_book"):
                print("    Ордербук отключён")
                continue

            for token in clob_market.get("tokens", []):
                outcome = token.get("outcome")
                token_id = token.get("token_id")
                price = token.get("price", 0)

                ask_liq, bid_liq = get_orderbook_liquidity(token_id)

                print(f"    {outcome} (цена: {price:.2f}):")
                print(f"      Купить (asks): ${ask_liq:,.0f}")
                print(f"      Продать (bids): ${bid_liq:,.0f}")

                if outcome == "Yes":
                    total_yes_liquidity += ask_liq
                else:
                    total_no_liquidity += ask_liq

    print(f"\n{'=' * 70}")
    print("ИТОГО")
    print("=" * 70)
    print(f"Ликвидность для покупки YES: ${total_yes_liquidity:,.0f}")
    print(f"Ликвидность для покупки NO:  ${total_no_liquidity:,.0f}")
    print(f"Всего доступно:              ${total_yes_liquidity + total_no_liquidity:,.0f}")


if __name__ == "__main__":
    check_all_liquidity()
