# История данных Polymarket

Polymarket работает на блокчейне Polygon — все сделки публичны и доступны для анализа.

## Источники данных

### 1. Dune Analytics (рекомендуется)

Лучший инструмент для аналитики Polymarket.

- **Официальные дашборды**: https://dune.com/polymarket
- **Примеры запросов**:
  - Топ трейдеры по объёму
  - История ставок по рынкам
  - PnL трейдеров
  - Объёмы по дням/неделям

**Пример SQL запроса для Dune:**
```sql
SELECT
    block_time,
    trader,
    side,
    size,
    price,
    market_slug
FROM polymarket.trades
WHERE market_slug LIKE '%earthquake%'
ORDER BY block_time DESC
LIMIT 100
```

### 2. Polymarket CLOB API

Прямой доступ к истории сделок.

**Эндпоинты:**

```
# История сделок по рынку
GET https://clob.polymarket.com/trades?market={condition_id}&limit=100

# История сделок пользователя
GET https://clob.polymarket.com/trades?maker={wallet_address}

# Ордербук
GET https://clob.polymarket.com/book?token_id={token_id}
```

**Пример запроса:**
```python
import httpx

condition_id = "0x16df76a155e148ef925c2a808204d5f2e2ac68d9585363616b82a6c6df765e84"
response = httpx.get(
    "https://clob.polymarket.com/trades",
    params={"market": condition_id, "limit": 50}
)
trades = response.json()

for trade in trades:
    print(f"{trade['timestamp']} | {trade['side']} | ${trade['size']} @ {trade['price']}")
```

### 3. Gamma API

Информация о событиях и рынках (не история сделок).

```
# Событие по slug
GET https://gamma-api.polymarket.com/events?slug={event_slug}

# Поиск событий
GET https://gamma-api.polymarket.com/events?tag=earthquake

# Все активные рынки
GET https://gamma-api.polymarket.com/markets?active=true
```

**Полезные поля:**
- `volumeNum` — общий объём торгов
- `liquidityNum` — текущая ликвидность
- `outcomePrices` — текущие цены

### 4. Polygonscan

Сырые блокчейн-данные.

- **Основной контракт**: https://polygonscan.com/address/0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E
- **CTF Exchange**: https://polygonscan.com/address/0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174

**Что можно найти:**
- Все транзакции
- События (events) контрактов
- Балансы токенов

### 5. The Graph (Subgraph)

GraphQL API для структурированных запросов.

```graphql
{
  trades(
    first: 100
    where: { market: "0x..." }
    orderBy: timestamp
    orderDirection: desc
  ) {
    id
    trader
    side
    size
    price
    timestamp
  }
}
```

## Примеры анализа

### Топ трейдеры по рынку

```python
from collections import defaultdict
import httpx

def get_top_traders(condition_id: str, limit: int = 100):
    response = httpx.get(
        "https://clob.polymarket.com/trades",
        params={"market": condition_id, "limit": limit}
    )
    trades = response.json()

    volumes = defaultdict(float)
    for trade in trades:
        volumes[trade['maker']] += float(trade['size'])

    return sorted(volumes.items(), key=lambda x: x[1], reverse=True)
```

### История цен

```python
def get_price_history(condition_id: str):
    response = httpx.get(
        "https://clob.polymarket.com/trades",
        params={"market": condition_id, "limit": 500}
    )
    trades = response.json()

    prices = []
    for trade in trades:
        prices.append({
            'time': trade['timestamp'],
            'price': float(trade['price']),
            'side': trade['side'],
            'size': float(trade['size'])
        })

    return prices
```

## Earthquake рынки — condition IDs

| Рынок | Condition ID |
|-------|--------------|
| 7.0+ by June 30 | Получить через Gamma API |
| 7.0+ in 2026 | Получить через Gamma API |
| 10.0+ before 2027 | `0x16df76a155e148ef925c2a808204d5f2e2ac68d9585363616b82a6c6df765e84` |
| 9.0+ before 2027 | Получить через Gamma API |

## Полезные ссылки

- [Polymarket Docs](https://docs.polymarket.com/)
- [CLOB API Reference](https://docs.polymarket.com/#clob-api)
- [Dune Polymarket](https://dune.com/polymarket)
- [Polygonscan](https://polygonscan.com/)
