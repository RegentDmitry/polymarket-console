# Бэктест: Polymarket крипто vs Deribit IV

## Идея

Взять закрытые крипто-рынки Polymarket (например, "Will BTC hit $100k by Dec 2024?"), восстановить историческую IV с Deribit на момент входа, посчитать touch probability, и проверить — давала ли стратегия "покупай когда Deribit touch > PM price" реальный +EV.

## Доступные исторические данные Deribit (публичный API, без регистрации)

### 1. DVOL Index (implied volatility index)
- **API:** `GET /public/get_volatility_index_data?currency=BTC&resolution=86400&start_timestamp=...&end_timestamp=...`
- **Глубина:** ~2.5 года (с мая 2023)
- **Разрешение:** 1D (дневные OHLC)
- **Формат:** `[timestamp, open, high, low, close]` — значения в % (annualized IV)
- **Валюты:** BTC, ETH
- **Описание:** Взвешенный индекс implied vol по всем опционам. Хорошая прокси для ATM IV. Для OTM страйков нужна поправка на skew (~5-15%).

### 2. Option Price Charts (OHLCV для конкретных инструментов)
- **API:** `GET /public/get_tradingview_chart_data?instrument_name=BTC-27DEC24-100000-C&resolution=1D&start_timestamp=...&end_timestamp=...`
- **Глубина:** Вся история инструмента, **включая истёкшие**
- **Разрешение:** 1D, 1H, и меньше
- **Формат:** OHLCV (цены в BTC)
- **Важно:** Работает для ЛЮБЫХ инструментов, даже после экспирации

**Проверенные горизонты:**

| Инструмент | Данные от | Данные до | Дней |
|------------|-----------|-----------|------|
| BTC-28JUN24-70000-C | 2023-06-22 | 2026-02-14 | 969 |
| BTC-27SEP24-80000-C | 2023-09-28 | 2026-02-14 | 871 |
| BTC-27DEC24-100000-C | 2023-12-28 | 2026-02-14 | 780 |
| BTC-28MAR25-90000-C | 2024-03-28 | 2026-02-14 | 689 |
| BTC-27JUN25-100000-C | 2024-06-27 | 2026-02-14 | 598 |
| ETH-27DEC24-4000-C | 2023-12-28 | 2026-02-14 | 780 |
| ETH-28MAR25-3000-C | 2024-04-15 | 2026-02-14 | 671 |

### 3. Delivery/Settlement Prices
- **API:** `GET /public/get_delivery_prices?index_name=btc_usd&count=100`
- **Глубина:** ~100 дней
- **Описание:** Ежедневная цена BTC/ETH для расчёта settlement

### 4. Что НЕ доступно
- Historical mark IV для конкретных страйков (нет прямого API)
- Trade history для истёкших инструментов (очищается)
- Realized volatility (только ~2 недели)
- Order book snapshots (нужен Tardis.dev или Kaiko)

## Вариант B: Восстановление IV из цен опционов

**Горизонт: ~2-2.5 года** (с середины 2023 для ближайших экспираций).

### Алгоритм:
1. Берём дневную цену опциона (close из chart data) — в BTC
2. Берём дневную цену BTC (из delivery prices или внешний источник)
3. Цена опциона в USD = close_btc × btc_price
4. Знаем: S (BTC price), K (strike), T (time to expiry), r (≈0), option_price
5. Решаем Black-Scholes обратно через Newton-Raphson → получаем IV

```python
def implied_vol_from_price(S, K, T, price, opt_type='C', r=0.0):
    """Восстановить IV из цены опциона методом Newton-Raphson."""
    sigma = 0.5  # initial guess
    for _ in range(100):
        d1 = (math.log(S/K) + (r + 0.5*sigma**2)*T) / (sigma*math.sqrt(T))
        d2 = d1 - sigma*math.sqrt(T)

        if opt_type == 'C':
            bs_price = S * norm_cdf(d1) - K * math.exp(-r*T) * norm_cdf(d2)
        else:
            bs_price = K * math.exp(-r*T) * norm_cdf(-d2) - S * norm_cdf(-d1)

        # Vega
        vega = S * math.sqrt(T) * norm_pdf(d1)
        if vega < 1e-10:
            break

        sigma -= (bs_price - price) / vega
        sigma = max(sigma, 0.01)

        if abs(bs_price - price) < 1e-8:
            break

    return sigma
```

### Ограничения варианта B:
- Цена опциона close — это последняя сделка дня, может быть неликвидной
- Для deep OTM опционов восстановление IV менее точное (маленькая vega)
- Нет данных по BTC price за тот же момент — нужен внешний источник (CoinGecko, Binance API)

## Внешние источники исторических BTC/ETH цен

- **CoinGecko API:** `GET /api/v3/coins/bitcoin/market_chart/range?vs_currency=usd&from=...&to=...` (бесплатно, до 365 дней)
- **Binance API:** `GET /api/v3/klines?symbol=BTCUSDT&interval=1d&startTime=...&endTime=...` (бесплатно)
- **Yahoo Finance:** через yfinance (`BTC-USD`)

## План бэктеста

### Шаг 1: Собрать данные
- Закрытые PM крипто-рынки (через Gamma API, `closed=true`)
- Дневные BTC/ETH цены за весь период
- DVOL или восстановленные IV за весь период

### Шаг 2: Для каждого закрытого рынка
- Определить: strike, direction (above/below), expiry
- На каждый день периода рассчитать: T (оставшееся время), IV (из DVOL или опциона), touch prob
- Определить "сигнал входа": touch prob > PM price + min_edge
- Записать: вошли бы? по какой цене? какой итог?

### Шаг 3: Результаты
- Win rate, средний P&L, Sharpe ratio
- Разбивка по типам: upside vs downside, BTC vs ETH
- Сравнение стратегий: DVOL-based vs instrument-IV-based

## Платные источники (если нужна высокая точность)

| Сервис | Данные | Цена |
|--------|--------|------|
| [Tardis.dev](https://tardis.dev) | Full order books, trades, mark prices | от $50/мес |
| [Laevitas](https://laevitas.ch) | IV surfaces, Greeks, term structure | от $30/мес |
| [Kaiko](https://kaiko.com) | Institutional-grade market data | Enterprise |
| [The Block](https://theblock.co) | Historical crypto options data | от $20/мес |
