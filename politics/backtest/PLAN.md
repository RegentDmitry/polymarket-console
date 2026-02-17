# План: SM бэктест — политические рынки Polymarket

## Цель

Проверить: предсказывает ли Smart Money flow исход политических рынков?
Конкретно: если SM говорил "YES" за 2-4 недели до резолюции, как часто рынок резолвился YES?

## Подход 1: On-chain реконструкция (ОСНОВНОЙ)

### Источник данных

**data-api.polymarket.com/trades** — бесплатный, без ключа:
```
GET /trades?asset=<token_id>&limit=1000&offset=N
```
- Все сделки всех пользователей по конкретному токену
- Поля: proxyWallet, timestamp, size, usdcSize, price, side (BUY/SELL), outcome
- Max offset: 3000 (до ~4000 трейдов на токен)
- Rate limit: мягкий (5+ rps без проблем), cache 300s

### Алгоритм

Для каждого закрытого политического рынка:

1. **Загрузить все трейды** через пагинацию (trades?asset=token_id)
2. **Реконструировать позиции** на каждый день:
   - Для каждого кошелька: cumsum(BUY) - cumsum(SELL) = position at time T
3. **Для каждого "snapshot дня"** (например, T-30, T-14, T-7 до резолюции):
   - Взять топ-30 холдеров YES и NO
   - Загрузить их P&L через /positions + /closed-positions
   - Прогнать SM алгоритм (conviction × profit × shrinkage)
   - Записать smart_flow и smart_implied
4. **Сравнить** SM signal с фактическим outcome

### Важный нюанс: P&L трейдеров

P&L трейдера через API = **текущий** совокупный P&L (включая уже зарезолвленные рынки).
Для честного бэктеста нужно учитывать что P&L трейдера на момент T мог быть другим.

**Варианты решения:**
- a) Использовать текущий P&L как прокси (assumption: профитные трейдеры остаются профитными)
- b) Через Dune: реконструировать P&L на дату T (сложно, дорого)
- c) Использовать только ROI и conviction (без absolute profit) — менее зависимо от точного P&L

**Рекомендация:** вариант (a) + sensitivity analysis. Если SM работает с текущим P&L,
он тем более работал с историческим (трейдеры имели ту же репутацию).

### Ограничения

| Проблема | Влияние | Решение |
|----------|---------|---------|
| Max 4000 трейдов на токен | Крупные рынки (>$100M) не покрыть | Фильтровать volume < $50M |
| P&L = текущий, не исторический | Slight lookahead bias | Sensitivity analysis |
| Трейдер мог продать и уйти | Не видим его на snapshot | Реконструкция из трейдов решает это |
| Rate limits /positions | 60 запросов на snapshot × N snapshots | Кеш + throttle |

## Подход 2: Проспективный сбор (ПАРАЛЛЕЛЬНО)

### Cron job

```bash
# Каждый день в 10:00 UTC (17:00 Bali)
0 10 * * * /opt/polymarket/sm_snapshot.sh
```

Скрипт:
1. Получить все открытые политические рынки (Gamma API, tag=politics, closed=false)
2. Для каждого рынка запустить smart_money.py
3. Сохранить результат в CSV/JSON: date, slug, market, smart_flow, smart_implied, pm_price

Через 3-6 месяцев: реальный проспективный бэктест без bias.

## Выбор рынков для бэктеста

### Теги (из Gamma API)

| Тег | Закрытых событий | Примеры |
|-----|-----------------|---------|
| politics | ~4,846 | Всё политическое |
| us-politics | ~500 | Выборы, конгресс, президент |
| geopolitics | ~2,000 | Войны, санкции, дипломатия |
| elections | ~500 | Все выборы мира |
| fed-rates | ~100 | Решения ФРС |
| middle-east | ~200 | Иран, Израиль, Газа |
| trump | ~300 | Трамп-специфичные |
| ukraine | ~150 | Украина/Россия |

### Фильтры для бэктеста

- Объём > $100k (ликвидные рынки, SM значим)
- Binary markets (YES/NO) — проще анализировать
- Не crypto-tagged (отдельный бэктест)
- Закрыт > 30 дней назад (есть время для snapshot за T-30)

**Оценка: ~500-1000 рынков** подходят для анализа.

## Категории для hit rate

| Категория | Гипотеза |
|-----------|----------|
| **Fed rates** | SM должен быть очень точен (информированные трейдеры) |
| **US elections** | SM хорош (polling + insiders) |
| **Geopolitics/wars** | SM менее точен (black swan events) |
| **Middle East** | SM слабый (непредсказуемость) |
| **Персональные** (health, death) | SM = шум |
| **Deadlines** (by date X) | SM может быть хорош (информация о переговорах) |

## Выходные метрики

Для каждой категории:
- **SM Hit Rate @ T-30**: % случаев когда SM предсказал правильный исход за 30 дней
- **SM Hit Rate @ T-14**: то же за 14 дней
- **SM Hit Rate @ T-7**: то же за 7 дней
- **SM ROI**: если бы мы покупали по SM сигналу, какой P&L?
- **SM vs Naive**: SM лучше чем просто "покупать текущую цену > 50%"?
- **SM Confidence**: корреляция |smart_flow| с hit rate (сильный сигнал = точнее?)

## Архитектура

### Файлы

```
politics/backtest/
├── PLAN.md              — этот файл
├── data_loader.py       — загрузка закрытых рынков + трейдов
├── position_reconstructor.py — реконструкция позиций на дату T
├── sm_backtest.py       — основной бэктест + отчёт
├── daily_collector.py   — cron-скрипт для проспективного сбора
├── cache/               — кеш трейдов и рынков
└── RESULTS.md           — результаты
```

### CLI

```bash
# Полный бэктест по всем закрытым политическим рынкам
python3 politics/backtest/sm_backtest.py

# Только определённая категория
python3 politics/backtest/sm_backtest.py --tag fed-rates
python3 politics/backtest/sm_backtest.py --tag us-elections

# С фильтром по объёму
python3 politics/backtest/sm_backtest.py --min-volume 1000000

# Snapshot на конкретную дату (для отладки)
python3 politics/backtest/sm_backtest.py --slug "presidential-election-winner-2024" --snapshot-days 30,14,7
```

## Порядок реализации

1. [ ] Сохранить план
2. [ ] Проверить на одном рынке: загрузить трейды, реконструировать позиции, прогнать SM
3. [ ] data_loader.py — discover + load trades + cache
4. [ ] position_reconstructor.py — snapshot позиций на дату T
5. [ ] sm_backtest.py — основной цикл + отчёт по категориям
6. [ ] Тестовый запуск на ~50 рынках
7. [ ] Полный прогон на ~500+ рынках
8. [ ] daily_collector.py — cron для проспективного сбора
9. [ ] Анализ результатов, сохранить в RESULTS.md

## Зависимости

- `tqdm` — прогресс-бары
- `crypto/smart_money.py` — SM алгоритм (импорт логики)
- Gamma API — метаданные рынков
- Data API — трейды + позиции трейдеров
