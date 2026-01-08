# Dune Analytics SQL Queries для Earthquake Markets

## Быстрый старт

1. Зарегистрируйся на https://dune.com
2. Создай новый Query: https://dune.com/queries
3. Вставь SQL запрос ниже
4. Run Query -> Save
5. Скопируй query_id из URL (например, `https://dune.com/queries/4567890` -> ID = 4567890)
6. Скачай данные: `python history_downloader.py --dune --query-id 4567890`

---

## Query 1: История сделок по earthquake рынкам (все)

Этот запрос получает ВСЕ сделки по earthquake рынкам.

```sql
-- Earthquake Markets Trade History
-- Polymarket CTFExchange OrderFilled events

SELECT
    block_time,
    block_number,
    tx_hash,
    CASE
        WHEN side = 0 THEN 'BUY'
        ELSE 'SELL'
    END as side,
    asset_id,
    maker_amount_filled / 1e6 as tokens,
    taker_amount_filled / 1e6 as usd_amount,
    (taker_amount_filled::decimal / NULLIF(maker_amount_filled::decimal, 0)) as price,
    fee / 1e6 as fee_usd
FROM polymarket_polygon.CTFExchange_evt_OrderFilled
WHERE asset_id IN (
    -- Megaquake in January (YES, NO)
    97289889511893592337518322719377385985134803683926334938435561961592943943205,
    52072459119681745493632157161519172226320630028666933804844212209325595398479,

    -- Megaquake in February (YES, NO)
    21310451439776287053010066498110375284213718787498584018040631296093001839979,
    30135879158750340662413351521911568773049498193619763556978611893342697891088,

    -- Megaquake in August (YES, NO)
    89202771594098148011632570769386085676843662152418846752849638750735394948741,
    108099995948619525135041609222503974151408423311815461618616538103844925007820,

    -- Megaquake in September (YES, NO)
    69608048651498441890188408897248431513223596328488551124904117504091936287754,
    41174022053505888066934737116785619420406605014743657629633266782489978619614,

    -- Megaquake in October (YES, NO)
    45785990958294505175098024879546870166291754035498547855584221375674565096977,
    66428946772893929832222536390073942523912168879591205219959469432627410188971,

    -- Megaquake in November (YES, NO)
    106040056798282171938979285431599999401251331277611762454878906741498668390688,
    68419667632878463254029635704227992287455755295960689773289068991671100638665,

    -- Megaquake in December (YES, NO)
    27168390291904476926105303473697691655016773639792052494702467454482025920679,
    86116459903212313476298893534920369042619926664813860560330933839461544680665
)
ORDER BY block_time ASC
```

---

## Query 2: История сделок для конкретного рынка

Для бэктеста одного рынка (например, megaquake-in-january):

```sql
-- Megaquake in January - Trade History
SELECT
    block_time,
    block_number,
    tx_hash,
    CASE
        WHEN side = 0 THEN 'BUY'
        ELSE 'SELL'
    END as trade_side,
    CASE
        WHEN asset_id = 97289889511893592337518322719377385985134803683926334938435561961592943943205 THEN 'YES'
        ELSE 'NO'
    END as outcome,
    maker_amount_filled / 1e6 as tokens,
    taker_amount_filled / 1e6 as usd_amount,
    (taker_amount_filled::decimal / NULLIF(maker_amount_filled::decimal, 0)) as price
FROM polymarket_polygon.CTFExchange_evt_OrderFilled
WHERE asset_id IN (
    97289889511893592337518322719377385985134803683926334938435561961592943943205,  -- YES
    52072459119681745493632157161519172226320630028666933804844212209325595398479   -- NO
)
ORDER BY block_time ASC
```

---

## Query 3: Агрегированная история цен (OHLC)

Для построения графика цен:

```sql
-- Megaquake in January - Hourly OHLC
WITH trades AS (
    SELECT
        date_trunc('hour', block_time) as hour,
        (taker_amount_filled::decimal / NULLIF(maker_amount_filled::decimal, 0)) as price,
        taker_amount_filled / 1e6 as volume
    FROM polymarket_polygon.CTFExchange_evt_OrderFilled
    WHERE asset_id = 97289889511893592337518322719377385985134803683926334938435561961592943943205  -- YES token
)
SELECT
    hour,
    MIN(price) as low,
    MAX(price) as high,
    (array_agg(price ORDER BY hour))[1] as open,
    (array_agg(price ORDER BY hour DESC))[1] as close,
    SUM(volume) as volume,
    COUNT(*) as trades_count
FROM trades
GROUP BY hour
ORDER BY hour ASC
```

---

## Query 4: Топ трейдеры

```sql
-- Earthquake Markets - Top Traders
SELECT
    maker as trader,
    COUNT(*) as trades_count,
    SUM(taker_amount_filled) / 1e6 as total_volume_usd,
    AVG(taker_amount_filled) / 1e6 as avg_trade_size
FROM polymarket_polygon.CTFExchange_evt_OrderFilled
WHERE asset_id IN (
    97289889511893592337518322719377385985134803683926334938435561961592943943205,
    52072459119681745493632157161519172226320630028666933804844212209325595398479
)
GROUP BY maker
ORDER BY total_volume_usd DESC
LIMIT 50
```

---

## Как скачать данные через API

### Способ 1: Через history_downloader.py

```bash
# Сначала создай .env с DUNE_API_KEY
cp .env.example .env
# Отредактируй .env, добавь ключ

# Скачай данные
python history_downloader.py --dune --query-id ТВОЙ_QUERY_ID
```

### Способ 2: Экспорт CSV с сайта

1. Запусти запрос на dune.com
2. Нажми "Export" -> "CSV"
3. Сохрани в `history/trades/`

### Способ 3: Прямой API запрос

```python
import httpx

DUNE_API_KEY = "твой_ключ"
QUERY_ID = 123456  # ID твоего запроса

# Получить результаты последнего выполнения
r = httpx.get(
    f"https://api.dune.com/api/v1/query/{QUERY_ID}/results",
    headers={"x-dune-api-key": DUNE_API_KEY},
    params={"limit": 50000}
)

data = r.json()
trades = data["result"]["rows"]
print(f"Получено {len(trades)} сделок")
```

---

## Token IDs для всех earthquake рынков

| Рынок | YES Token ID | NO Token ID |
|-------|-------------|-------------|
| megaquake-in-january | 97289889...943205 | 52072459...398479 |
| megaquake-in-february | 21310451...839979 | 30135879...891088 |
| megaquake-in-august | 89202771...948741 | 108099995...007820 |
| megaquake-in-september | 69608048...287754 | 41174022...619614 |
| megaquake-in-october | 45785990...096977 | 66428946...188971 |
| megaquake-in-november | 106040056...390688 | 68419667...638665 |
| megaquake-in-december | 27168390...920679 | 86116459...680665 |

---

## Полезные ссылки

- [Dune Analytics](https://dune.com)
- [Polymarket Dune Tables](https://dune.com/polymarket)
- [API Documentation](https://dune.com/docs/api/)
- [Free Tier Limits](https://dune.com/pricing): 2,500 datapoints/месяц
