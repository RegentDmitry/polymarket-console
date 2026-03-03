# Skilled Trader Scouting System — План реализации

## Context

Текущая Smart Money система (`crypto/smart_money.py`) выбирает трейдеров по **размеру позиции** (top-30 holders), а затем взвешивает по profit/ROI. Проблемы:
- Маркетмейкеры попадают в выборку (большие позиции, но нет direction skill)
- Нет персистентности — каждый запуск заново загружает данные
- Нет возможности отслеживать трейдеров между рынками
- Нельзя строить copy-trade сигналы

**Цель:** Найти skilled трейдеров по track record, исключить маркетмейкеров, построить persistent базу, генерировать copy-trade сигналы усреднением ставок лучших трейдеров.

---

## Архитектура

### Хранилище: SQLite (`data/traders.db`)

Таблицы:
- **traders** — `address`, `alias`, `first_seen`, `last_seen`, `is_mm` (bool), `skill_score`, `total_markets`, `win_rate`, `avg_roi`, `realized_pnl`
- **positions** — `address`, `market_slug`, `token_id`, `outcome` (YES/NO), `size`, `avg_price`, `realized_pnl`, `percent_pnl`, `is_closed`, `timestamp`
- **markets** — `slug`, `condition_id`, `title`, `resolved`, `resolution_outcome`, `end_date`
- **signals** — `market_slug`, `signal_type` (BUY/SELL), `confidence`, `avg_entry`, `n_traders`, `timestamp`

### Модули

```
crypto/scouting/
├── __init__.py
├── db.py              # SQLite schema + CRUD
├── scanner.py         # Сканирование рынков, загрузка холдеров
├── scorer.py          # Skill scoring по track record
├── filters.py         # MM detection, минимальные пороги
├── monitor.py         # Мониторинг новых ставок skilled трейдеров
├── copytrade.py       # Генерация copy-trade сигналов
└── cli.py             # CLI интерфейс
```

---

## Фаза 1: Сканер + БД (основа)

### 1.1 `db.py` — SQLite storage
- Создание таблиц (миграции через версионирование schema)
- CRUD операции для traders, positions, markets
- Upsert логика (обновление при повторном сканировании)

### 1.2 `scanner.py` — Загрузка данных
- Переиспользовать `fetch_holders()` из `crypto/smart_money.py:141` для получения top holders
- Переиспользовать `fetch_trader_stats()` из `crypto/smart_money.py:165` для загрузки позиций трейдера
- API endpoints:
  - `GET /holders?market={condition_id}&limit=100` — holders по рынку
  - `GET /positions?user={address}` — открытые позиции
  - `GET /closed-positions?user={address}&limit=100&offset=0` — закрытые (пагинация!)
  - `GET https://gamma-api.polymarket.com/events?slug={slug}` — метаданные рынка
- Сканировать resolved рынки для получения track record (ключевое отличие от текущей системы)
- Кеш: `/tmp/pm_trader_cache.json` (уже есть, TTL 1 час)

### 1.3 Начальное наполнение
- Просканировать все resolved политические рынки за последние 6 месяцев
- Для каждого: загрузить top-100 holders (обе стороны), сохранить позиции
- ~50-100 рынков × 200 holders = 10-20k записей positions

**Файлы для модификации/создания:**
- Создать: `crypto/scouting/db.py`, `crypto/scouting/scanner.py`
- Переиспользовать: `crypto/smart_money.py` (функции `fetch_holders`, `fetch_trader_stats`, `fetch_market_info`)

---

## Фаза 2: Скоринг + Фильтры

### 2.1 `scorer.py` — Skill scoring

Метрика skill на основе **закрытых позиций** трейдера:

```
skill_score = win_rate × log(1 + total_markets) × avg_roi_factor × consistency
```

Где:
- `win_rate` = доля прибыльных позиций (realized_pnl > 0)
- `total_markets` = количество уникальных рынков (разнообразие)
- `avg_roi_factor` = средний percentPnl по закрытым позициям
- `consistency` = std(returns) penalty (низкая дисперсия = стабильный skill)

Минимальные пороги:
- `total_markets >= 10` (достаточно данных)
- `total_realized_pnl > $100` (не мелкие ставки)
- `win_rate > 55%` (лучше coin flip)

### 2.2 `filters.py` — Исключение маркетмейкеров

Признаки MM:
- **Двусторонние позиции:** одновременно YES и NO на одном рынке (или частые флипы)
- **Много мелких сделок:** trades > 50 на рынке с маленьким средним размером
- **Низкий net exposure:** abs(yes_size - no_size) / (yes_size + no_size) < 0.3
- **Высокий crypto%:** > 50% позиций в крипто price markets (арбитражёры)
- **Паттерн лимитных ордеров:** покупка и продажа на одном рынке с узким спредом

Классификация: `is_mm = True` если >= 2 из 5 признаков совпадают.

**Файлы:** Создать `crypto/scouting/scorer.py`, `crypto/scouting/filters.py`

---

## Фаза 3: Мониторинг + Copy-trade

### 3.1 `monitor.py` — Отслеживание новых ставок

- Периодически (раз в час) проверять открытые позиции skilled трейдеров
- Детектировать новые позиции (diff с предыдущим снимком в БД)
- Триггер: skilled трейдер открыл новую позицию → записать сигнал

### 3.2 `copytrade.py` — Генерация сигналов

Агрегация по рынку:
```
signal_strength = Σ (trader_skill × position_direction × position_size_normalized)
```

- Если >= 3 skilled трейдеров на одной стороне → STRONG signal
- Если 2 → MODERATE
- Если 1 → WEAK
- Если skilled трейдеры разделены → CONFLICT (не торговать)

Вывод: таблица рынков с сигналами, отсортированная по strength.

**Файлы:** Создать `crypto/scouting/monitor.py`, `crypto/scouting/copytrade.py`

---

## Фаза 4: CLI + Интеграция

### 4.1 `cli.py` — Команды

```bash
# Начальное сканирование (долгое, ~30 мин)
python -m crypto.scouting.cli scan --category politics --months 6

# Обновить скоры
python -m crypto.scouting.cli score

# Показать top skilled трейдеров
python -m crypto.scouting.cli top --limit 20

# Показать copy-trade сигналы
python -m crypto.scouting.cli signals

# Мониторинг (continuous)
python -m crypto.scouting.cli monitor --interval 3600

# Проверить конкретного трейдера
python -m crypto.scouting.cli trader 0x1234...
```

### 4.2 Интеграция с SM v2
- Опционально: заменить `fetch_holders()` → `get_skilled_traders()` в `smart_money.py`
- SM flow пересчитывать с весами из skill_score вместо текущей формулы

---

## Порядок реализации

| Шаг | Что | Файлы | Зависимости |
|-----|-----|-------|-------------|
| 1 | SQLite schema + CRUD | `db.py` | — |
| 2 | Scanner (загрузка holders + positions) | `scanner.py` | db.py |
| 3 | Начальный скан resolved рынков | cli.py (scan cmd) | scanner.py |
| 4 | MM фильтры | `filters.py` | db.py |
| 5 | Skill scoring | `scorer.py` | db.py, filters.py |
| 6 | Мониторинг новых ставок | `monitor.py` | db.py, scorer.py |
| 7 | Copy-trade сигналы | `copytrade.py` | monitor.py, scorer.py |
| 8 | CLI обёртка | `cli.py` | все модули |

---

## Верификация

1. **Unit tests:** scorer и filters на синтетических данных
2. **Backtest:** Применить skill scoring к трейдерам из resolved рынков, проверить что skilled трейдеры действительно предсказывают outcomes лучше случайного
3. **Сравнение с SM v2:** На тех же рынках сравнить сигналы scouting vs SM v2
4. **Manual check:** Посмотреть top-20 skilled трейдеров — нет ли очевидных MM?
5. **Live test:** Запустить monitor на неделю, проверить качество сигналов

---

## Ключевые решения

- **PostgreSQL** (172.24.192.1, user=postgres, db=polymarket) — JOIN'ы, индексы, атомарные обновления
- **Resolved рынки** как источник ground truth — только на них можно оценить skill
- **realizedPnl** включает и ранние продажи и resolution → captures timing skill
- **Переиспользование** существующего кода из `smart_money.py` для API запросов
- **Rate limiting**: Polymarket API ~5 req/s — нужен sleep между запросами при массовом скане

---

## Текущий статус

### Реализовано (Фазы 1-2):
- `db.py` — PostgreSQL schema + CRUD (traders, markets, positions, signals)
- `scanner.py` — scan events, load holders, enrich trader histories
- `scorer.py` — skill scoring (profitability × experience × ROI × size)
- `filters.py` — MM detection (two-sided, tiny positions, low exposure, spread-capture)
- `cli.py` — CLI (init, scan, scan-event, enrich, score, mm, top, trader, stats, pipeline)

### Данные в БД:
- 32,725 трейдеров (из 50 крупнейших политических событий)
- 739 рынков, 174,923 позиций
- 90 MM обнаружено, 1,119 трейдеров оценено
- 1,302 активных трейдера (10+ рынков)

### Известные проблемы:
- `closed-positions` API возвращает max ~50 записей → win/loss ratio ненадёжный
- Нет resolution outcome в markets table → нельзя проверить "угадал ли трейдер"
- Первый трейдер с $1B PnL — аномалия (надо фильтровать)

### TODO (Фазы 3-4):
- [ ] monitor.py — отслеживание новых ставок skilled трейдеров
- [ ] copytrade.py — генерация copy-trade сигналов
- [ ] Пагинация closed-positions (offset, до полного списка)
- [ ] Загрузка resolution outcomes для markets (resolved = YES/NO)
- [ ] Фильтр аномальных PnL (>$100M → exclude)
