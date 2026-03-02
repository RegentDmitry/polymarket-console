# Crypto Trading Bot — План реализации

## Контекст

Нужно создать торгового бота для BTC/ETH рынков на Polymarket, аналогичного боту землетрясений. Бот использует Student-t Monte Carlo модель для оценки fair price (touch probability), непрерывно обновляет рыночные данные и управляет позициями.

Ключевые отличия от бота землетрясений:
- **Ground truth** = Student-t MC модель вместо сейсмической модели
- **Живые данные** = Binance (spot), Deribit (IV, futures curve) вместо USGS
- **Несколько потоков данных** с разными таймерами (как monitor_bot)
- **Нет резерва** (reserve_balance не нужен)
- **Нужна отладка fast pricing** — аналитика vs MC, отдельный этап

## Архитектура

### Структура файлов

```
crypto/trading_bot/
  __init__.py
  __main__.py                    # Точка входа
  config.py                      # CryptoBotConfig + CLI args

  models/                        # КОПИЯ из earthquakes (без изменений)
    signal.py, position.py, market.py

  storage/                       # КОПИЯ из earthquakes (без изменений)
    positions.py, sell_orders.py, history.py

  executor/
    polymarket.py                # Адаптация: загружает crypto/.env

  scanner/
    base.py                      # КОПИЯ из earthquakes
    crypto.py                    # НОВЫЙ — CryptoScanner

  market_data/                   # НОВЫЙ модуль — живые данные
    binance.py                   # BTC/ETH spot price (REST)
    deribit.py                   # IV + futures curve
    polymarket.py                # Загрузка рынков через Gamma API

  pricing/                       # НОВЫЙ модуль — fair price
    touch_prob.py                # MC Student-t (из full_scan.py)
    fast_approx.py               # Аналитика + correction (отладить отдельно)

  ui/
    app.py                       # Адаптация TUI

  data/
    active/, history/, logs/
    sell_orders.json

  logger.py                      # КОПИЯ из earthquakes

update_bot/                      # НОВЫЙ — обновление списка крипто-рынков
  __init__.py
  __main__.py                    # Точка входа
  config.py                      # Конфигурация + CLI args
  scanner.py                     # Поиск BTC/ETH рынков через Gamma API
  updater.py                     # Парсинг strike/direction, сохранение JSON
  ui/
    app.py                       # TUI (как у earthquake update_bot)

crypto_markets.json              # Выходной файл (аналог earthquake_markets.json)
```

### Что копировать vs создавать

**Копировать без изменений (7 файлов):**
- `models/signal.py`, `models/position.py`, `models/market.py`
- `storage/positions.py`, `storage/sell_orders.py`, `storage/history.py`
- `scanner/base.py`, `logger.py`

**Адаптировать (4 файла):**
- `executor/polymarket.py` — путь к `crypto/.env`, strategy="crypto"
- `config.py` — убрать reserve, добавить MC параметры (df, paths)
- `__main__.py` — CryptoScanner вместо EarthquakeScanner
- `ui/app.py` — новые панели данных, убрать ExtraEvents

**Создать новые (6 файлов trading_bot + 4 файла update_bot):**
- `scanner/crypto.py` — CryptoScanner
- `market_data/binance.py`, `market_data/deribit.py`, `market_data/polymarket.py`
- `pricing/touch_prob.py`, `pricing/fast_approx.py`
- `update_bot/scanner.py`, `update_bot/updater.py`, `update_bot/config.py`, `update_bot/ui/app.py`

### Потоки данных (отдельные таймеры, как monitor_bot)

```
Поток 1: Binance spot        — каждые 5-10 сек (быстрый REST)
Поток 2: Deribit IV + futures — каждые 5 мин (API, кэш)
Поток 3: Polymarket рынки    — каждые 5 мин (Gamma API)
Поток 4: Scan + Trade        — каждые 1 мин (основной цикл)
```

Потоки 1-3 обновляют shared state (с locks). Поток 4 читает это state для расчётов.

### TUI Layout

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  UTC: 12:34  |  Balance: $500  |  Positions: 3  |  Invested: $300          │
│  Scanned: 12:33:45  |  Next: 0:15                                          │
└──────────────────────────────────────────────────────────────────────────────┘
┌─ MARKET SCANNER ──────────────┐┌─ MY POSITIONS ──────────────────────────────┐
│  Next: 0:15  |  Found: 2/40   ││  Market    Entry  Fair  Bid  Sell  P&L     │
│                                ││  BTC>100k  0.43   0.51  0.48 0.51 +$12    │
│  + BTC ↑ $100k (Annual)       ││  ETH<1600  0.82   0.88  0.85 0.88 +$5     │
│    43¢ → 51¢  edge +8%        ││  ...                                       │
│    APY 25%  liq $50           ││                                             │
│                                ││                                             │
│  + ETH ↓ $1,600 (Mar)        ││                                             │
│    82¢ → 88¢  edge +6%        ││                                             │
│    APY 180%  liq $30          │├─ RECENT TRADES ──────────────────────────────┤
│                                ││  12:30 BUY BTC>100k 100 tokens @ 43¢      │
│  - BTC ↑ $120k  edge -2%     ││  12:25 SELL ETH<2000 50 tokens @ 91¢ +$8   │
│  - ETH ↑ $3,000 edge -5%     ││                                             │
├─ LIVE DATA ────────────────────┤├─ FUTURES CURVE ─────────────────────────────┤
│  BTC  $87,234  IV: 52.2%      ││  BTC-28MAR26  $89,100  drift +2.1%         │
│  ETH  $2,143   IV: 70.0%      ││  BTC-27JUN26  $92,500  drift +6.0%         │
│  Updated: 3s ago               ││  BTC-26DEC26  $96,800  drift +11.0%        │
│                                ││  ETH-28MAR26  $2,200   drift +2.7%         │
└────────────────────────────────┘└─────────────────────────────────────────────┘
```

- **My Positions** и **Recent Trades** — как в землетрясениях
- **Live Data** — spot + IV (обновляется отдельным потоком)
- **Futures Curve** — кривая фьючерсов (обновляется отдельным потоком)

### Ускорение MC — стратегия

**Фаза 1 (в составе бота): Batch MC**
- Одна генерация путей на (currency, drift) → переиспользование для всех страйков
- `numpy.random.Generator.standard_t()` вместо `scipy.stats.student_t.rvs()`
- `running_max/running_min` предвычисляются один раз
- **Ожидание: ~40x ускорение** (80 сек → 2 сек для 40 рынков)

**Фаза 2 (отдельная отладка): Аналитика + correction**
- GBM first passage formula (уже есть в `full_scan.py:touch_above/touch_below`)
- Калибровочный множитель `ratio = MC_prob / GBM_prob` на сетке параметров
- Предвычислить таблицу один раз, интерполировать в runtime
- **Ожидание: ~1000x**, но требует отдельной отладки и валидации
- Это будет реализовано как отдельный этап после запуска бота

### Update Bot — обнаружение крипто-рынков

Аналог `earthquakes/update_bot/`, но для BTC/ETH рынков. Earthquake update_bot уже не использует Claude Code — работает напрямую через Gamma API. Крипто update_bot будет работать так же.

**Как работает:**
1. Сканирует Polymarket Gamma API по ключевым словам: `["bitcoin", "ethereum", "BTC", "ETH", "crypto"]`
2. Фильтрует нерелевантные (спорт, политика с упоминанием крипто)
3. Для каждого рынка парсит:
   - **Strike price**: regex `\$([0-9,]+)` из вопроса ("Will BTC hit $100,000?")
   - **Direction**: above/below из контекста ("hit", "reach", "drop below", "fall")
   - **Currency**: BTC или ETH
   - **Expiry**: дата окончания рынка
   - **Token IDs**: для торговли
   - **Condition ID**: для идентификации
4. Сохраняет в `crypto/crypto_markets.json`

**Структура `crypto_markets.json`:**
```json
{
  "will-btc-hit-100k-2026": {
    "currency": "BTC",
    "strike": 100000,
    "direction": "above",
    "start": "2026-01-01T00:00:00Z",
    "end": "2026-12-31T23:59:59Z",
    "type": "binary",
    "condition_id": "0x...",
    "token_ids": {"Yes": "123...", "No": "456..."}
  }
}
```

**Отличия от earthquake update_bot:**
- Ключевые слова: crypto вместо earthquake
- Парсинг: strike + direction вместо magnitude
- Поле `currency` (BTC/ETH) вместо `magnitude`
- Нет resolution_start/end (не привязан к сейсмическим периодам)
- Проще фильтрация (нет count рынков — все binary)

**TUI (как у earthquake update_bot):**
- StatusBar с временем последнего обновления
- Лог обновлений (добавлено/обновлено/удалено)
- Hotkey R для ручного обновления
- Интервал по умолчанию: 6h

**Запуск:**
```bash
cd crypto && python -m update_bot              # интервал 6h
cd crypto && python -m update_bot --interval 12h
```

### Загрузка .env

`PolymarketClient` поддерживает `env_path` параметр. В executor:
```python
self.client = PolymarketClient(env_path=Path(__file__).parent.parent.parent / ".env")
# → crypto/.env
```

## Ключевые файлы для переиспользования

| Исходный файл | Что взять |
|---|---|
| `crypto/full_scan.py:123-172` | `mc_touch_prob()`, `mc_edge()` → `pricing/touch_prob.py` |
| `crypto/full_scan.py:60-120` | `deribit_get()`, `fetch_futures_curve()`, `drift_for_days()` → `market_data/deribit.py` |
| `crypto/full_scan.py:214-271` | Парсинг рынков (regex strike, direction) → `market_data/polymarket.py` |
| `crypto/full_scan.py:82-111` | `touch_above()`, `touch_below()` (GBM) → `pricing/fast_approx.py` |
| `earthquakes/trading_bot/ui/app.py` | TUI framework, scan cycle, sell order management |
| `earthquakes/trading_bot/executor/polymarket.py` | Buy/sell/sell_limit/redeem логика |
| `earthquakes/polymarket_client.py` | Polymarket API wrapper (env_path parameter) |
| `earthquakes/update_bot/scanner.py` | Gamma API сканер (адаптировать ключевые слова) |
| `earthquakes/update_bot/updater.py` | Логика обновления JSON (адаптировать парсинг) |
| `earthquakes/update_bot/ui/app.py` | TUI для update_bot (копия с минимальной адаптацией) |

## План реализации (по порядку)

### Шаг 1: Скелет + модели (копирование)
- Создать `crypto/trading_bot/` структуру
- Скопировать models, storage, scanner/base.py, logger.py
- Создать `config.py` (без reserve, с df/paths параметрами)
- Файлы: 10 файлов копирования + 1 новый config

### Шаг 2: Market Data модуль
- `market_data/binance.py` — BTC/ETH spot через REST (`/api/v3/ticker/price`)
- `market_data/deribit.py` — перенести из `full_scan.py`: spot, IV (живой с ATM опционов), futures curve
- `market_data/polymarket.py` — загрузка крипто-рынков через Gamma API, парсинг strike/direction
- Файлы: 3 новых

### Шаг 3: Pricing модуль
- `pricing/touch_prob.py` — перенести `mc_touch_prob()` из `full_scan.py`, batch-оптимизация
- `pricing/fast_approx.py` — заглушка (будет отлаживаться отдельно позже)
- Файлы: 2 новых

### Шаг 4: CryptoScanner
- `scanner/crypto.py` — наследует BaseScanner
- `scan_for_entries()`: обновить данные → загрузить рынки → batch MC → сигналы
- `scan_for_exits()`: bid ≥ fair → SELL сигнал
- Файл: 1 новый

### Шаг 5: Executor
- `executor/polymarket.py` — копия из earthquakes, путь к crypto/.env, strategy="crypto"
- Файл: 1 адаптация

### Шаг 6: TUI + Entry Point
- `ui/app.py` — адаптация:
  - Убрать ExtraEventsPanel, reserve logic
  - Добавить LiveDataPanel (spot, IV) и FuturesCurvePanel
  - Отдельные потоки для обновления данных (как monitor_bot)
  - My Positions и Recent Trades — как в землетрясениях
- `__main__.py` — точка входа с CryptoScanner
- Файлы: 2 адаптации

### Шаг 7: Update Bot
- `update_bot/__init__.py`, `__main__.py` — точка входа
- `update_bot/config.py` — конфигурация (интервал, путь к JSON)
- `update_bot/scanner.py` — поиск BTC/ETH рынков через Gamma API
  - Ключевые слова: `["bitcoin", "ethereum", "BTC", "ETH", "crypto"]`
  - Blacklist: спортивные, политические контексты
  - Пагинация по 500 событий
- `update_bot/updater.py` — парсинг strike/direction/currency, сохранение в `crypto_markets.json`
  - Strike: regex из вопроса/описания
  - Direction: above/below по ключевым словам
  - Currency: BTC/ETH по контексту
  - Token IDs из Gamma API
- `update_bot/ui/app.py` — TUI (копия из earthquake update_bot с адаптацией)
- Файлы: 6 новых/адаптированных

### Шаг 8: Тестирование dry-run
- Запуск `python -m crypto.trading_bot` (dry-run по умолчанию)
- Проверить: сканирование работает, сигналы генерируются, TUI отображается
- Сравнить fair prices с `full_scan.py`

### Шаг 9 (отдельно): Отладка fast pricing
- Калибровка correction table: MC vs GBM для сетки параметров
- Валидация точности
- Интеграция в бота как опция `--fast-pricing`

## Верификация

1. **Update bot:** `cd crypto && python -m update_bot` — TUI запускается, находит BTC/ETH рынки, создаёт `crypto_markets.json`
2. **Dry-run тест:** `cd crypto && python -m trading_bot` — TUI запускается, данные загружаются, сигналы генерируются
3. **Сравнение с full_scan.py:** fair prices должны совпадать с точностью ±1%
4. **Скорость:** полный скан < 10 сек (с batch MC)
5. **Правильный кошелёк:** executor показывает адрес `0xbf07Fb93...` (не earthquake bot!)
6. **Sell orders:** после виртуальной покупки в dry-run, sell order выставляется по fair price

## Шаг 10: Умное распределение ставок + визуализация риска

### Проблема

Текущая логика: сортировать по edge/ROI, аллоцировать баланс сверху вниз. Это приводит к тому что весь баланс уходит в один актив с максимальным edge. Это опасно:
- Одна лотерейная позиция (low prob, high edge) может забрать весь баланс
- BTC и ETH сильно коррелированы — двойной риск при одновременной ставке на оба
- Нет диверсификации ни по срокам, ни по направлению

### Решение: Portfolio-aware sizing

**Ключевой принцип: НИКОГДА не входить в позиции с негативным edge.** Лимиты на концентрацию — это только верхние ограничения на размер ставки. Мы не диверсифицируем ради диверсификации и не жертвуем EV ради хеджирования. Если осталась одна позиция с позитивным edge — весь оставшийся лимит идёт в неё (с учётом max per position).

**Шаг 1: Классификация рисков каждой позиции**
- `risk_tier`: HIGH (fair < 20% или > 80%), MEDIUM (20-40% или 60-80%), LOW (40-60%)
- Позиции с fair < 15% = "лотерейные" — ограничить до X% портфеля
- Позиции с fair > 85% = "почти наверняка" — можно больше

**Шаг 2: Лимиты на концентрацию (только верхние ограничения)**
- Max на одну позицию: 30% от баланса (конфиг)
- Max на "лотерейные" (HIGH risk): 20% от портфеля суммарно
- Max на один currency (BTC/ETH): 70% (учёт корреляции)
- Max на одно направление (all up или all down): 60%
- **Если после лимитов остался свободный баланс** — он НЕ распределяется в позиции с негативным edge. Лучше держать кэш, чем заходить в минус-EV.

**Шаг 3: Kelly criterion (опционально)**
- Для каждого сигнала: `f* = (edge / (1 - fair_price))` (упрощённый Kelly)
- Ограничить сверху: `min(f* × balance, max_per_position)`
- Half-Kelly для консервативности: `f* / 2`
- Kelly естественно даёт f*=0 при edge=0, т.е. не входит в нулевые/негативные edge

**Шаг 4: Корреляция BTC/ETH**
- Историческая корреляция BTC/ETH ≈ 0.75-0.85 (нужно проверить)
- При высокой корреляции: позиции в одном направлении (both up) считать как одну для целей лимитов
- Penalty: если уже есть BTC-up позиция, ETH-up получает пониженный лимит

### TUI: Панель рискового профиля портфеля

Новая панель `PortfolioRiskPanel` в TUI:

```
┌─ PORTFOLIO RISK ──────────────────────────────────────────┐
│  Diversification: 4/6 positions across 2 currencies      │
│                                                           │
│  By Currency:  BTC ████████░░ 80%   ETH ██░░░░░░░░ 20%  │
│  By Direction: UP  ██████░░░░ 60%   DOWN ████░░░░░░ 40%  │
│  By Risk:      LOW ████░░░░░░ 40%   MED ████░░░░░░ 40%  │
│                HIGH ██░░░░░░░░ 20%                        │
│                                                           │
│  Probability Distribution (portfolio touches $1):         │
│  $0   ▁▂▃▄▅▆▇█▇▆▅▄▃▂▁   $800                           │
│  E[V]=$523  P(loss)=18%  Max loss=$400                   │
│                                                           │
│  Correlation: BTC-ETH ρ=0.82 (HIGH)                      │
│  ⚠ 80% in BTC — consider more ETH exposure               │
└───────────────────────────────────────────────────────────┘
```

**Визуализация в TUI:**
- Текстовые bar charts для распределения по валюте/направлению/риску (braille или block chars)
- Гистограмма portfolio value distribution (из MC paths) — можно использовать braille-символы `⣿⣷⣧⣇⡇⡆⡄⡀` для столбиков
- Ожидаемая стоимость портфеля + P(loss) + max drawdown
- Warnings когда портфель перекошен

### Реализация

1. **`pricing/portfolio.py`** — Portfolio risk calculations
   - `calculate_risk_tiers(positions, fair_prices)` → per-position risk
   - `portfolio_concentration(positions)` → currency, direction breakdown
   - `allocate_sizes(signals, balance, positions)` → smart sizing with limits
   - `portfolio_mc(positions, btc_paths, eth_paths)` → portfolio-level MC for value distribution

2. **`ui/app.py`** — Добавить `PortfolioRiskPanel`
   - Bar charts через Rich/block characters
   - Distribution histogram через braille

3. **`config.py`** — Новые параметры:
   - `max_position_pct: float = 0.30` (max 30% в одну позицию)
   - `max_lottery_pct: float = 0.20` (max 20% в лотерейные)
   - `max_currency_pct: float = 0.70` (max 70% в одну валюту)

### Порядок реализации

Этот шаг идёт **после** запуска бота (Step 8). Сначала нужен работающий бот, потом — умная аллокация.

---

## Заметки

- **update_bot**: Создаётся отдельный `crypto/update_bot/` (earthquake update_bot слишком специфичен для расширения). Работает через Gamma API напрямую (без Claude Code). Выходной файл `crypto_markets.json` читается trading_bot для получения списка рынков и token_ids.
- **Кошелёк**: `0xbf07Fb930990f4aC0ae955932F173B5FB5031b86` (отдельный от earthquake bot)
- **POL для газа**: 5 POL уже переведено на крипто-кошелёк
