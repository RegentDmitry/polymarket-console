#!/usr/bin/env python3
"""
Модуль для скачивания исторических данных earthquake рынков.

Скачивает:
1. Метаданные событий из Gamma API
2. Историю сделок из CLOB API
3. Историю землетрясений из USGS

Использование:
    python history_downloader.py              # Скачать всё
    python history_downloader.py --trades     # Только сделки
    python history_downloader.py --usgs       # Только USGS данные
"""

import argparse
import json
import os
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv

load_dotenv()

# Директории
BASE_DIR = Path(__file__).parent
HISTORY_DIR = BASE_DIR / "history"
CLOSED_DIR = HISTORY_DIR / "closed"
OPEN_DIR = HISTORY_DIR / "open"
TRADES_DIR = HISTORY_DIR / "trades"
USGS_DIR = HISTORY_DIR / "usgs"

# API URLs
GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = os.getenv("CLOB_API_URL", "https://clob.polymarket.com")
USGS_API = "https://earthquake.usgs.gov/fdsnws/event/1"

# CLOB credentials
CLOB_API_KEY = os.getenv("CLOB_API_KEY", "")
CLOB_SECRET = os.getenv("CLOB_SECRET", "")
CLOB_PASS_PHRASE = os.getenv("CLOB_PASS_PHRASE", "")

# Dune Analytics
DUNE_API_KEY = os.getenv("DUNE_API_KEY", "")


# ============================================================================
# DATA CLASSES
# ============================================================================

@dataclass
class Trade:
    """Одна сделка."""
    timestamp: str
    price: float
    size: float
    side: str  # "BUY" or "SELL"

@dataclass
class PricePoint:
    """Цена на момент времени."""
    timestamp: str
    price: float
    volume_24h: float = 0


# ============================================================================
# GAMMA API - Метаданные событий
# ============================================================================

# Все earthquake события
EARTHQUAKE_SLUGS = {
    "closed": [
        "megaquake-in-february",
        "6pt0-earthquake-in-mediterranean-by-next-friday",
        "megaquake-in-january",
        "megaquake-in-december",
        "megaquake-in-november",
        "megaquake-in-october",
        "megaquake-in-september",
        "megaquake-in-august",
        "will-an-earthquake-measuring-80-or-above-occur-anywhere-on-earth-before-june-1-2022",
        "will-there-be-an-earthquake-of-magnitude-4pt5-or-higher-in-the-conterminous-us-by-december-31st",
        "will-there-be-an-earthquake-of-magnitude-4pt5-or-higher-in-conterminous-us-by-november-29",
    ],
    "open": [
        "megaquake-by-january-31",
        "megaquake-by-march-31",
        "megaquake-by-june-30",
        "how-many-7pt0-or-above-earthquakes-by-june-30",
        "how-many-7pt0-or-above-earthquakes-in-2026",
        "9pt0-or-above-earthquake-before-2027",
        "10pt0-or-above-earthquake-before-2027",
    ],
}


def download_event_metadata(slug: str, output_dir: Path) -> Optional[dict]:
    """Скачать метаданные события из Gamma API."""
    try:
        r = httpx.get(f"{GAMMA_API}/events?slug={slug}", timeout=30)
        data = r.json()

        if not data:
            print(f"  ❌ {slug}: не найдено")
            return None

        event = data[0]

        # Сохраняем
        filepath = output_dir / f"{slug}.json"
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(event, f, indent=2, ensure_ascii=False)

        title = event.get('title', '')
        volume = event.get('volume', 0)
        print(f"  ✅ {title} (${volume:,.0f})")

        return event

    except Exception as e:
        print(f"  ❌ {slug}: {e}")
        return None


def download_all_metadata():
    """Скачать метаданные всех событий."""
    print("\n" + "=" * 60)
    print("СКАЧИВАНИЕ МЕТАДАННЫХ СОБЫТИЙ (Gamma API)")
    print("=" * 60)

    CLOSED_DIR.mkdir(parents=True, exist_ok=True)
    OPEN_DIR.mkdir(parents=True, exist_ok=True)

    all_events = []

    print(f"\n📁 Закрытые события ({len(EARTHQUAKE_SLUGS['closed'])}):\n")
    for slug in EARTHQUAKE_SLUGS['closed']:
        event = download_event_metadata(slug, CLOSED_DIR)
        if event:
            all_events.append(event)
        time.sleep(0.2)  # Rate limiting

    print(f"\n📁 Открытые события ({len(EARTHQUAKE_SLUGS['open'])}):\n")
    for slug in EARTHQUAKE_SLUGS['open']:
        event = download_event_metadata(slug, OPEN_DIR)
        if event:
            all_events.append(event)
        time.sleep(0.2)

    # Сводка
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_events": len(all_events),
        "total_volume": sum(e.get('volume', 0) for e in all_events),
    }

    with open(HISTORY_DIR / "summary.json", 'w') as f:
        json.dump(summary, f, indent=2)

    print(f"\n✅ Скачано {len(all_events)} событий")
    return all_events


# ============================================================================
# CLOB API - История сделок
# ============================================================================

def get_clob_headers() -> dict:
    """Получить заголовки для CLOB API."""
    if not CLOB_API_KEY:
        return {}
    return {
        "Authorization": f"Bearer {CLOB_API_KEY}",
    }


def download_trades_for_market(condition_id: str, slug: str) -> list[dict]:
    """Скачать историю сделок для одного рынка."""
    trades = []

    try:
        # Пробуем публичный endpoint
        r = httpx.get(
            f"{CLOB_API}/trades",
            params={"market": condition_id, "limit": 500},
            headers=get_clob_headers(),
            timeout=30,
        )

        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list):
                trades = data
            elif isinstance(data, dict) and 'trades' in data:
                trades = data['trades']
        elif r.status_code == 401:
            print(f"    ⚠️  Требуется авторизация для {slug}")
        else:
            print(f"    ⚠️  HTTP {r.status_code} для {slug}")

    except Exception as e:
        print(f"    ❌ Ошибка: {e}")

    return trades




# ============================================================================
# USGS API - История землетрясений
# ============================================================================

def download_usgs_history(
    start_date: datetime,
    end_date: datetime,
    min_magnitude: float = 4.5,
) -> list[dict]:
    """Скачать историю землетрясений с USGS."""
    earthquakes = []

    try:
        r = httpx.get(
            f"{USGS_API}/query",
            params={
                "format": "geojson",
                "starttime": start_date.strftime("%Y-%m-%d"),
                "endtime": end_date.strftime("%Y-%m-%d"),
                "minmagnitude": min_magnitude,
                "orderby": "time",
            },
            timeout=60,
        )

        if r.status_code == 200:
            data = r.json()
            for feature in data.get('features', []):
                props = feature.get('properties', {})
                earthquakes.append({
                    "id": feature.get('id'),
                    "time": props.get('time'),
                    "magnitude": props.get('mag'),
                    "place": props.get('place'),
                    "url": props.get('url'),
                })
    except Exception as e:
        print(f"  ❌ USGS ошибка: {e}")

    return earthquakes


def download_all_usgs():
    """Скачать историю землетрясений для всех релевантных периодов."""
    print("\n" + "=" * 60)
    print("СКАЧИВАНИЕ ИСТОРИИ ЗЕМЛЕТРЯСЕНИЙ (USGS)")
    print("=" * 60)

    USGS_DIR.mkdir(parents=True, exist_ok=True)

    # Периоды для скачивания (по магнитудам)
    periods = [
        # M4.5+ для US рынков 2021
        {
            "name": "m4.5_us_2021",
            "start": datetime(2021, 10, 1),
            "end": datetime(2022, 1, 1),
            "magnitude": 4.5,
        },
        # M6.0+ для Mediterranean
        {
            "name": "m6.0_2025",
            "start": datetime(2025, 1, 1),
            "end": datetime(2025, 3, 1),
            "magnitude": 6.0,
        },
        # M7.0+ глобально (для текущих рынков)
        {
            "name": "m7.0_global_2024_2026",
            "start": datetime(2024, 1, 1),
            "end": datetime(2026, 12, 31),
            "magnitude": 7.0,
        },
        # M8.0+ глобально (megaquake)
        {
            "name": "m8.0_global_2020_2026",
            "start": datetime(2020, 1, 1),
            "end": datetime(2026, 12, 31),
            "magnitude": 8.0,
        },
        # M9.0+ глобально
        {
            "name": "m9.0_global_2000_2026",
            "start": datetime(2000, 1, 1),
            "end": datetime(2026, 12, 31),
            "magnitude": 9.0,
        },
    ]

    total_quakes = 0

    for period in periods:
        print(f"\n📊 {period['name']} (M{period['magnitude']}+)...")

        quakes = download_usgs_history(
            period['start'],
            period['end'],
            period['magnitude'],
        )

        if quakes:
            filepath = USGS_DIR / f"{period['name']}.json"
            with open(filepath, 'w') as f:
                json.dump({
                    "period": period['name'],
                    "start_date": period['start'].isoformat(),
                    "end_date": period['end'].isoformat(),
                    "min_magnitude": period['magnitude'],
                    "count": len(quakes),
                    "earthquakes": quakes,
                }, f, indent=2)

            print(f"  ✅ {len(quakes)} землетрясений")
            total_quakes += len(quakes)
        else:
            print(f"  ⚠️  0 землетрясений")

        time.sleep(0.5)

    print(f"\n✅ Всего скачано {total_quakes} записей о землетрясениях")
    return total_quakes


# ============================================================================
# DUNE ANALYTICS - История сделок
# ============================================================================

DUNE_API = "https://api.dune.com/api/v1"

# Готовые запросы для earthquake markets
# Можно создать свой запрос на dune.com и использовать его ID
DUNE_QUERIES = {
    # Пример: история сделок по всем polymarket рынкам
    "polymarket_trades": 3145285,  # Замени на актуальный query_id
}


def run_dune_query(query_id: int, params: dict = None) -> list[dict]:
    """Выполнить запрос Dune Analytics."""
    if not DUNE_API_KEY:
        print("  ⚠️  DUNE_API_KEY не установлен в .env")
        print("  → Получи бесплатный ключ: https://dune.com/settings/api")
        return []

    headers = {"X-Dune-API-Key": DUNE_API_KEY}

    try:
        # Запускаем выполнение запроса
        print(f"  Запускаем Dune query {query_id}...")
        r = httpx.post(
            f"{DUNE_API}/query/{query_id}/execute",
            headers=headers,
            json={"query_parameters": params or {}},
            timeout=30,
        )

        if r.status_code != 200:
            print(f"  ❌ Ошибка запуска: {r.status_code} - {r.text[:200]}")
            return []

        execution_id = r.json().get("execution_id")
        if not execution_id:
            print("  ❌ Нет execution_id")
            return []

        # Ждём результат
        print(f"  Ожидаем результат (execution_id: {execution_id})...")
        for _ in range(60):  # Максимум 5 минут
            time.sleep(5)

            r = httpx.get(
                f"{DUNE_API}/execution/{execution_id}/status",
                headers=headers,
                timeout=30,
            )
            status = r.json().get("state")
            print(f"    Status: {status}")

            if status == "QUERY_STATE_COMPLETED":
                break
            elif status in ["QUERY_STATE_FAILED", "QUERY_STATE_CANCELLED"]:
                print(f"  ❌ Query failed: {status}")
                return []

        # Получаем результаты
        r = httpx.get(
            f"{DUNE_API}/execution/{execution_id}/results",
            headers=headers,
            timeout=60,
        )

        if r.status_code == 200:
            data = r.json()
            rows = data.get("result", {}).get("rows", [])
            print(f"  ✅ Получено {len(rows)} записей")
            return rows
        else:
            print(f"  ❌ Ошибка получения результатов: {r.status_code}")
            return []

    except Exception as e:
        print(f"  ❌ Dune error: {e}")
        return []


def create_earthquake_trades_query() -> str:
    """SQL запрос для получения сделок по earthquake рынкам."""
    return """
    SELECT
        block_time,
        tx_hash,
        trader,
        side,
        size,
        price,
        outcome,
        market_slug
    FROM polymarket.trades
    WHERE (
        LOWER(market_slug) LIKE '%earthquake%'
        OR LOWER(market_slug) LIKE '%megaquake%'
        OR LOWER(market_slug) LIKE '%9pt0%'
        OR LOWER(market_slug) LIKE '%10pt0%'
        OR LOWER(market_slug) LIKE '%7pt0%'
    )
    ORDER BY block_time DESC
    """


def download_dune_trades():
    """Скачать историю сделок через Dune Analytics."""
    print("\n" + "=" * 60)
    print("СКАЧИВАНИЕ ИСТОРИИ СДЕЛОК (Dune Analytics)")
    print("=" * 60)

    if not DUNE_API_KEY:
        print("\n⚠️  Для скачивания истории сделок нужен Dune API key")
        print("\nКак получить (бесплатно):")
        print("  1. Зарегистрируйся на https://dune.com")
        print("  2. Перейди в Settings → API")
        print("  3. Создай API key")
        print("  4. Добавь в .env: DUNE_API_KEY=твой_ключ")
        print("\nАльтернатива - ручной экспорт CSV:")
        print("  → https://dune.com/polymarket")
        print("  → Найди нужный запрос → Export → CSV")
        return []

    TRADES_DIR.mkdir(parents=True, exist_ok=True)

    # Используем готовый запрос или создаём свой
    # Примечание: для своего запроса нужно сначала создать его на dune.com

    print("\n📊 Запрашиваем данные...")

    # Попробуем получить результаты последнего выполнения (быстрее)
    query_id = DUNE_QUERIES.get("polymarket_trades")

    try:
        headers = {"X-Dune-API-Key": DUNE_API_KEY}
        r = httpx.get(
            f"{DUNE_API}/query/{query_id}/results",
            headers=headers,
            timeout=60,
        )

        if r.status_code == 200:
            data = r.json()
            rows = data.get("result", {}).get("rows", [])

            # Фильтруем только earthquake
            earthquake_trades = [
                row for row in rows
                if any(kw in str(row.get('market_slug', '')).lower()
                       for kw in ['earthquake', 'megaquake', '7pt0', '8pt0', '9pt0', '10pt0'])
            ]

            if earthquake_trades:
                filepath = TRADES_DIR / "dune_earthquake_trades.json"
                with open(filepath, 'w') as f:
                    json.dump({
                        "source": "dune_analytics",
                        "query_id": query_id,
                        "downloaded_at": datetime.now(timezone.utc).isoformat(),
                        "count": len(earthquake_trades),
                        "trades": earthquake_trades,
                    }, f, indent=2)

                print(f"  ✅ Сохранено {len(earthquake_trades)} сделок")
                return earthquake_trades
            else:
                print("  ⚠️  Нет earthquake сделок в результатах")
        else:
            print(f"  ⚠️  HTTP {r.status_code}: {r.text[:200]}")

    except Exception as e:
        print(f"  ❌ Ошибка: {e}")

    return []


# ============================================================================
# MAIN
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="Скачать исторические данные")
    parser.add_argument("--metadata", action="store_true", help="Только метаданные")
    parser.add_argument("--trades", action="store_true", help="Только сделки (Dune)")
    parser.add_argument("--usgs", action="store_true", help="Только USGS")
    parser.add_argument("--dune", action="store_true", help="Только Dune Analytics")
    args = parser.parse_args()

    # Если ничего не указано - скачиваем всё
    download_all = not (args.metadata or args.trades or args.usgs or args.dune)

    print("=" * 60)
    print("EARTHQUAKE HISTORY DOWNLOADER")
    print("=" * 60)
    print(f"Время: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Директория: {HISTORY_DIR}")

    if download_all or args.metadata:
        download_all_metadata()

    if download_all or args.trades or args.dune:
        download_dune_trades()

    if download_all or args.usgs:
        download_all_usgs()

    print("\n" + "=" * 60)
    print("ГОТОВО!")
    print("=" * 60)

    # Показываем что скачано
    print("\nСодержимое history/:")
    for item in sorted(HISTORY_DIR.rglob("*.json")):
        rel_path = item.relative_to(HISTORY_DIR)
        size = item.stat().st_size / 1024
        print(f"  {rel_path} ({size:.1f} KB)")

    # Инструкции если нет Dune key
    if not DUNE_API_KEY:
        print("\n" + "-" * 60)
        print("💡 Для полной истории сделок добавь в .env:")
        print("   DUNE_API_KEY=твой_ключ")
        print("   → https://dune.com/settings/api (бесплатно)")
        print("-" * 60)


if __name__ == "__main__":
    main()
