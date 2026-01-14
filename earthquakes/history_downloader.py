#!/usr/bin/env python3
"""
Модуль для скачивания исторических данных earthquake рынков.

Скачивает:
1. Метаданные событий из Gamma API (--metadata)
2. Историю землетрясений из USGS (--usgs)
3. Историю сделок из блокчейна Polygon (--blockchain)
4. Историю сделок из Dune Analytics (--dune --query-id ID)

Использование:
    python history_downloader.py                    # Скачать метаданные + USGS
    python history_downloader.py --metadata         # Только метаданные
    python history_downloader.py --usgs             # Только USGS данные
    python history_downloader.py --blockchain       # Сделки из блокчейна (100k блоков)
    python history_downloader.py --blockchain --blocks 500000  # Больше блоков
    python history_downloader.py --dune --query-id 123456      # Сделки из Dune

Данные сохраняются в history/:
    - closed/*.json   - закрытые события
    - open/*.json     - открытые события
    - trades/*.json   - история сделок
    - usgs/*.json     - данные о землетрясениях
    - summary.json    - сводка
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

# Polygon RPC (QuikNode поддерживает 2000 блоков за запрос)
POLYGON_RPC = os.getenv("POLYGON_RPC", "https://polygon-mainnet.g.alchemy.com/v2/demo")

# CTFExchange контракт (Polymarket)
CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"

# OrderFilled event signature (из реальных логов контракта)
ORDER_FILLED_TOPIC = "0xd0a08e8c493f9c94f29311604c9de1b4e8c8d4c06bd0c789af57f2d65bfec0f6"

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
# Создай свой запрос на dune.com и добавь его ID сюда
DUNE_QUERIES = {
    # "polymarket_trades": ТВОЙ_QUERY_ID,  # Раскомментируй после создания
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


def download_dune_trades(query_id: int = None):
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
        return []

    # Если query_id не передан, показываем инструкции
    if not query_id:
        query_id = DUNE_QUERIES.get("polymarket_trades")

    if not query_id:
        print("\n⚠️  Нужен query_id для скачивания данных")
        print("\nКак создать запрос на Dune:")
        print("  1. Перейди на https://dune.com/queries")
        print("  2. Нажми 'New Query'")
        print("  3. Вставь SQL:")
        print("""
    SELECT
        block_time,
        tx_hash,
        maker as trader,
        taker,
        side,
        size,
        price,
        fee_rate_bps,
        asset_id as token_id
    FROM polymarket_polygon.CTFExchange_evt_OrderFilled
    ORDER BY block_time DESC
    LIMIT 50000
        """)
        print("  4. Run Query → Save")
        print("  5. Скопируй query_id из URL")
        print("  6. Запусти: python history_downloader.py --dune --query-id ТВОЙ_ID")
        return []

    TRADES_DIR.mkdir(parents=True, exist_ok=True)

    print(f"\n📊 Запрашиваем данные (query_id: {query_id})...")

    try:
        headers = {"x-dune-api-key": DUNE_API_KEY}
        r = httpx.get(
            f"{DUNE_API}/query/{query_id}/results",
            headers=headers,
            params={"limit": 50000},
            timeout=120,
        )

        if r.status_code == 200:
            data = r.json()
            rows = data.get("result", {}).get("rows", [])

            print(f"  Получено {len(rows)} записей")

            if rows:
                # Сохраняем все данные
                filepath = TRADES_DIR / f"dune_trades_{query_id}.json"
                with open(filepath, 'w') as f:
                    json.dump({
                        "source": "dune_analytics",
                        "query_id": query_id,
                        "downloaded_at": datetime.now(timezone.utc).isoformat(),
                        "count": len(rows),
                        "columns": list(rows[0].keys()) if rows else [],
                        "trades": rows,
                    }, f, indent=2)

                print(f"  ✅ Сохранено: {filepath}")

                # Показываем пример данных
                if rows:
                    print(f"\n  Пример записи:")
                    for k, v in list(rows[0].items())[:5]:
                        print(f"    {k}: {v}")

                return rows
            else:
                print("  ⚠️  Нет данных. Возможно запрос ещё не выполнялся.")
                print("  → Запусти запрос на dune.com, затем повтори скачивание")
        elif r.status_code == 404:
            print(f"  ❌ Query {query_id} не найден или приватный")
            print("  → Убедись что query сохранён и публичный")
        else:
            print(f"  ⚠️  HTTP {r.status_code}: {r.text[:300]}")

    except Exception as e:
        print(f"  ❌ Ошибка: {e}")

    return []


# ============================================================================
# POLYGON RPC - История сделок из блокчейна
# ============================================================================

def load_earthquake_token_ids() -> tuple[set, dict]:
    """Загрузить все token_ids earthquake рынков из сохранённых метаданных.

    Returns:
        tuple: (set of token_ids, dict mapping token_id -> market title)
    """
    token_ids = set()
    token_to_market = {}

    for dir_path in [CLOSED_DIR, OPEN_DIR]:
        if not dir_path.exists():
            continue

        for filepath in dir_path.glob("*.json"):
            try:
                with open(filepath) as f:
                    event = json.load(f)

                title = event.get("title", filepath.stem)

                for market in event.get("markets", []):
                    clob_tokens = market.get("clobTokenIds", "[]")
                    if isinstance(clob_tokens, str):
                        clob_tokens = json.loads(clob_tokens)

                    outcomes = market.get("outcomes", "[]")
                    if isinstance(outcomes, str):
                        outcomes = json.loads(outcomes)

                    for i, token_id in enumerate(clob_tokens):
                        token_ids.add(str(token_id))
                        outcome = outcomes[i] if i < len(outcomes) else "?"
                        token_to_market[str(token_id)] = f"{title} [{outcome}]"

            except Exception as e:
                print(f"  ⚠️  Ошибка загрузки {filepath}: {e}")

    return token_ids, token_to_market


def get_current_block() -> int:
    """Получить текущий номер блока."""
    try:
        r = httpx.post(
            POLYGON_RPC,
            json={"jsonrpc": "2.0", "method": "eth_blockNumber", "params": [], "id": 1},
            timeout=30,
        )
        data = r.json()
        if "error" in data:
            print(f"  ❌ RPC error: {data['error']}")
            return 0
        if "result" not in data:
            print(f"  ❌ Неожиданный ответ: {data}")
            return 0
        return int(data["result"], 16)
    except Exception as e:
        print(f"  ❌ Ошибка получения блока: {e}")
        return 0


def get_block_timestamp(block_number: int) -> int:
    """Получить timestamp блока."""
    try:
        r = httpx.post(
            POLYGON_RPC,
            json={
                "jsonrpc": "2.0",
                "method": "eth_getBlockByNumber",
                "params": [hex(block_number), False],
                "id": 1,
            },
            timeout=30,
        )
        result = r.json().get("result")
        if result:
            return int(result["timestamp"], 16)
    except:
        pass
    return 0


def fetch_order_filled_logs(from_block: int, to_block: int) -> list:
    """Получить OrderFilled логи из блокчейна."""
    try:
        r = httpx.post(
            POLYGON_RPC,
            json={
                "jsonrpc": "2.0",
                "method": "eth_getLogs",
                "params": [{
                    "address": CTF_EXCHANGE,
                    "topics": [ORDER_FILLED_TOPIC],
                    "fromBlock": hex(from_block),
                    "toBlock": hex(to_block),
                }],
                "id": 1,
            },
            timeout=60,
        )

        result = r.json()
        if "error" in result:
            error_msg = result["error"].get("message", "Unknown error")
            if "range" in error_msg.lower():
                return None  # Слишком большой диапазон блоков
            print(f"  ⚠️  RPC error: {error_msg}")
            return []

        return result.get("result", [])

    except Exception as e:
        print(f"  ❌ Ошибка запроса логов: {e}")
        return []


def decode_order_filled(log: dict) -> dict:
    """Декодировать OrderFilled событие.

    NOTE: Цена рассчитывается как amount_usd / amount_tokens.
    На Polymarket цена должна быть 0-1, но из-за особенностей контракта
    результат может отличаться. Для точного расчёта нужно изучить
    логику CTFExchange контракта.
    """
    data = log.get("data", "0x")[2:]  # Убираем "0x"

    if len(data) < 320:  # 5 параметров по 64 символа
        return None

    try:
        # OrderFilled event data (indexed: orderHash, maker, taker в topics):
        # data[0:64] = side (uint8, 0=BUY, 1=SELL)
        # data[64:128] = assetId (uint256 - token ID)
        # data[128:192] = makerAmountFilled (uint256 - outcome tokens)
        # data[192:256] = takerAmountFilled (uint256 - USDC в raw units)
        # data[256:320] = fee (uint256)

        side = int(data[0:64], 16)  # 0 = BUY, 1 = SELL
        asset_id = str(int(data[64:128], 16))
        maker_amount = int(data[128:192], 16) / 1e6  # Outcome tokens (6 decimals)
        taker_amount = int(data[192:256], 16) / 1e6  # USDC (6 decimals)

        # Цена = USDC / outcome tokens
        # TODO: Проверить правильность расчёта для Polymarket
        if maker_amount > 0:
            price = taker_amount / maker_amount
        else:
            price = 0

        return {
            "block": int(log["blockNumber"], 16),
            "tx_hash": log["transactionHash"],
            "asset_id": asset_id,
            "side": "BUY" if side == 0 else "SELL",
            "amount_usd": taker_amount,  # Сумма в USDC
            "amount_tokens": maker_amount,  # Количество токенов
            "price": round(price, 6),
        }

    except Exception:
        return None


def download_blockchain_trades(
    start_block: int = None,
    blocks_to_scan: int = 100000,
    chunk_size: int = 2000,
):
    """Скачать историю сделок из блокчейна Polygon."""
    print("\n" + "=" * 60)
    print("СКАЧИВАНИЕ ИСТОРИИ СДЕЛОК (Polygon RPC)")
    print("=" * 60)

    # Загружаем earthquake token IDs
    token_ids, token_to_market = load_earthquake_token_ids()
    if not token_ids:
        print("\n⚠️  Не найдены token_ids. Сначала скачайте метаданные:")
        print("   python history_downloader.py --metadata")
        return []

    print(f"\n📊 Token IDs для earthquake рынков: {len(token_ids)}")

    # Определяем диапазон блоков
    current_block = get_current_block()
    if not current_block:
        print("❌ Не удалось получить текущий блок")
        return []

    if start_block is None:
        start_block = current_block - blocks_to_scan

    print(f"📦 Текущий блок: {current_block}")
    print(f"📦 Сканируем: {start_block} → {current_block} ({current_block - start_block} блоков)")
    print(f"📦 Chunk size: {chunk_size} блоков")

    TRADES_DIR.mkdir(parents=True, exist_ok=True)

    all_trades = []
    total_logs = 0
    failed_chunks = 0

    # Сканируем блоки чанками
    chunks_total = (current_block - start_block) // chunk_size + 1
    chunk_num = 0

    for from_block in range(start_block, current_block, chunk_size):
        to_block = min(from_block + chunk_size - 1, current_block)
        chunk_num += 1

        # Прогресс
        progress = (chunk_num / chunks_total) * 100
        print(f"\r  [{progress:5.1f}%] Блоки {from_block}-{to_block}...", end="", flush=True)

        logs = fetch_order_filled_logs(from_block, to_block)

        if logs is None:
            # Слишком большой диапазон - пробуем меньший chunk
            failed_chunks += 1
            continue

        total_logs += len(logs)

        # Фильтруем earthquake trades
        for log in logs:
            trade = decode_order_filled(log)
            if trade and trade["asset_id"] in token_ids:
                trade["market"] = token_to_market.get(trade["asset_id"], "Unknown")
                all_trades.append(trade)

        time.sleep(0.1)  # Rate limiting

    print(f"\n\n✅ Просканировано {total_logs:,} логов")
    print(f"✅ Найдено {len(all_trades)} earthquake trades")

    if failed_chunks > 0:
        print(f"⚠️  Пропущено чанков (слишком большой range): {failed_chunks}")

    if all_trades:
        # Добавляем timestamps для первого и последнего трейда
        if all_trades:
            first_ts = get_block_timestamp(all_trades[0]["block"])
            last_ts = get_block_timestamp(all_trades[-1]["block"])
        else:
            first_ts = last_ts = 0

        # Сохраняем
        filepath = TRADES_DIR / f"blockchain_trades_{start_block}_{current_block}.json"
        with open(filepath, 'w') as f:
            json.dump({
                "source": "polygon_rpc",
                "downloaded_at": datetime.now(timezone.utc).isoformat(),
                "start_block": start_block,
                "end_block": current_block,
                "total_logs_scanned": total_logs,
                "earthquake_trades_count": len(all_trades),
                "first_trade_timestamp": first_ts,
                "last_trade_timestamp": last_ts,
                "trades": all_trades,
            }, f, indent=2)

        print(f"✅ Сохранено: {filepath}")

        # Показываем примеры
        print(f"\n📈 Примеры сделок:")
        for trade in all_trades[:5]:
            market = trade.get('market', 'Unknown')[:50]
            print(f"  {trade['side']} ${trade['amount_usd']:.2f} - {market}")

    return all_trades


# ============================================================================
# MAIN
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="Скачать исторические данные")
    parser.add_argument("--metadata", action="store_true", help="Только метаданные")
    parser.add_argument("--trades", action="store_true", help="Только сделки (Dune)")
    parser.add_argument("--usgs", action="store_true", help="Только USGS")
    parser.add_argument("--dune", action="store_true", help="Только Dune Analytics")
    parser.add_argument("--query-id", type=int, help="Dune query ID для скачивания")
    parser.add_argument("--blockchain", action="store_true", help="Сделки из Polygon блокчейна")
    parser.add_argument("--blocks", type=int, default=100000, help="Сколько блоков сканировать (default: 100000)")
    parser.add_argument("--chunk-size", type=int, default=2000, help="Размер чанка блоков (default: 2000)")
    args = parser.parse_args()

    # Если ничего не указано - скачиваем всё (кроме Dune и blockchain)
    download_all = not (args.metadata or args.trades or args.usgs or args.dune or args.blockchain)

    print("=" * 60)
    print("EARTHQUAKE HISTORY DOWNLOADER")
    print("=" * 60)
    print(f"Время: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Директория: {HISTORY_DIR}")

    if download_all or args.metadata:
        download_all_metadata()

    if args.trades or args.dune or args.query_id:
        download_dune_trades(args.query_id)

    if args.blockchain:
        download_blockchain_trades(
            blocks_to_scan=args.blocks,
            chunk_size=args.chunk_size,
        )

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
