# План: SM бэктест — политические рынки Polymarket

## Статус

**Фаза 1 завершена** (2026-02-17):
- Инфраструктура: `dune_loader.py` + `sm_backtest.py` готовы
- Загружено 28 рынков (tag=politics, min_volume=$500k, NegRisk only)
- Проанализировано 24 рынка (все — US elections 2024)
- **Hit rate: 83% (20/24), Win rate с фильтром: 94% (17/18), P&L: +$340**

**Фаза 2: расширение по тегам** (ожидает Dune credits)

## Цель Фазы 2

Определить: **на каких типах политических рынков SM работает, а на каких нет?**

Гипотезы:
| Категория | Гипотеза | Почему |
|-----------|----------|--------|
| **US elections** | SM хорош (✅ подтверждено: 83%) | Polling + insiders, ликвидность |
| **Fed rates** | SM очень точен | Макро-трейдеры хорошо информированы |
| **Geopolitics** | SM средний | Black swan events, но инсайдеры есть |
| **Middle East** | SM слабый | Непредсказуемость, мало инсайдов |
| **Trump policy** | SM средний | Зависит от одного человека |
| **Congress/legislation** | SM хорош | Лоббисты торгуют |
| **Trade war/tariffs** | SM слабый | Policy shifts непредсказуемы |

## Доступные данные (проверено 2026-02-17)

### Закрытые NegRisk рынки по тегам

| Тег | Events (стр.1) | NegRisk рынков | Ещё страниц? | Приоритет |
|-----|---------------|----------------|--------------|-----------|
| elections | 100+ (3+ стр.) | 747+ | да, тысячи | P1 — выборки по странам |
| congress | 125 | 233 | нет | P1 |
| fed-rates | 96 | 211 | нет | P1 — ключевой тест |
| politics | 100+ (3+ стр.) | 237+ | да | уже частично загружено |
| us-politics | 95 | 146 | нет | P2 |
| us-elections | 31 | 118 | нет | перекрывается с elections |
| china | 100 | 78 | возможно | P2 |
| trade-war | 100 | 52 | возможно | P2 |
| trump | 100 | 48 | возможно | P2 |
| immigration | 22 | 34 | нет | P3 |
| middle-east | 100 | 25 | возможно | P2 |
| tariffs | 31 | 14 | нет | P3 |

**Итого NegRisk: ~1,500-2,000 уникальных рынков** (с дедупликацией между тегами).

### Dune credits

- Бесплатный лимит: 2,500 credits/month
- Потрачено: ~1,770 (осталось ~730)
- Стоимость 1 рынка: ~3.5 credits (один Dune запрос)
- **На остатке можно загрузить: ~200 рынков**
- Для полного анализа (500+ рынков): нужно ~1,750 credits → **докупить или подождать обновления лимита**

## План загрузки Фазы 2

### Приоритет 1 (~300 credits, ~85 рынков)

```bash
# Fed rates — ключевой тест гипотезы "SM хорош для макро"
python politics/backtest/sm_backtest.py --query-id 6707297 \
    --discover --tag fed-rates --min-volume 100000 --limit 50

# Congress — законодательные рынки
python politics/backtest/sm_backtest.py --query-id 6707297 \
    --discover --tag congress --min-volume 200000 --limit 40
```

### Приоритет 2 (~500 credits, ~140 рынков)

```bash
# Geopolitics / Middle East / China
python politics/backtest/sm_backtest.py --query-id 6707297 \
    --discover --tag geopolitics --min-volume 100000 --limit 50

python politics/backtest/sm_backtest.py --query-id 6707297 \
    --discover --tag middle-east --min-volume 50000 --limit 40

python politics/backtest/sm_backtest.py --query-id 6707297 \
    --discover --tag china --min-volume 100000 --limit 30

python politics/backtest/sm_backtest.py --query-id 6707297 \
    --discover --tag trump --min-volume 200000 --limit 30
```

### Приоритет 3 (~300 credits, ~85 рынков)

```bash
# Trade war / tariffs / immigration
python politics/backtest/sm_backtest.py --query-id 6707297 \
    --discover --tag trade-war --min-volume 100000 --limit 40

python politics/backtest/sm_backtest.py --query-id 6707297 \
    --discover --tag tariffs --min-volume 50000 --limit 20

python politics/backtest/sm_backtest.py --query-id 6707297 \
    --discover --tag immigration --min-volume 100000 --limit 25
```

**Итого: ~1,100 credits на ~310 рынков** (плюс 28 уже загруженных).

## Технические детали

### Инфраструктура (готова)

```
politics/backtest/
├── PLAN.md              — этот файл
├── RESULTS.md           — результаты (24 рынка, elections)
├── dune_loader.py       — Dune API + кеш + discover + reconstruct positions
├── sm_backtest.py       — SM алгоритм + backtest engine + P&L sim
└── cache/
    ├── dune_trades_*.json      — 28 файлов с on-chain трейдами
    ├── markets_politics.json   — 66 обнаруженных рынков
    └── trader_stats_cache.json — кеш P&L трейдеров
```

### Dune запрос

- Query ID: **6707297** (параметр `{{token_id}}`)
- Таблица: `polymarket_polygon.NegRiskCTFExchange_evt_OrderFilled`
- API key: в `.env` файле (`DUNE_API_KEY`)
- Лимит: 32,000 строк на запрос (достаточно для большинства рынков)

### SM алгоритм

```
weight = log_profit × roi_mult × health × conviction × shrinkage
smart_flow = Σ(signed_weight) / Σ(|signed_weight|)   # [-1, +1]
```

- YES holders: positive weight (из on-chain позиций)
- NO holders: negative balance в YES token = net sellers (proxy)
- Trader stats: текущий P&L через data-api (приближение — не исторический)
- Cap: max 15% от сигнала на одного трейдера

### Приближения и bias

| Фактор | Влияние | Критичность |
|--------|---------|-------------|
| P&L трейдеров = текущий | Lookahead bias (трейдер мог стать профитным позже) | Среднее |
| 32k row limit на Dune | Крупные рынки теряют последние дни данных | Низкое |
| NegRisk only | CTF рынки не анализируем | Низкое (большинство политики = NegRisk) |
| NO side = proxy | Не настоящие NO holders, а net YES sellers | Среднее |
| Выборка = elections 2024 | Результаты могут не переноситься на другие типы | Высокое (именно это проверяем в Фазе 2) |

## Метрики для сравнения тегов

Для каждого тега:
1. **Hit Rate @ T-30**: SM предсказал правильно за 30 дней
2. **Hit Rate @ T-14**: SM предсказал правильно за 14 дней
3. **Strong Signal Hit Rate**: |flow| > 0.3
4. **P&L** (buy $100 per signal, |flow| > 0.1 filter)
5. **Win Rate**: % профитных сделок
6. **Avg P&L/trade**: средний profit

### Ожидаемый вывод Фазы 2

```
═══ SM HIT RATE BY CATEGORY ═══

Category          Markets  T-30   T-14   Strong  P&L      Win%
elections            24    75%    83%    90%    +$340     94%
fed-rates            35    ??%    ??%    ??%    $???      ??%
congress             28    ??%    ??%    ??%    $???      ??%
geopolitics          30    ??%    ??%    ??%    $???      ??%
middle-east          15    ??%    ??%    ??%    $???      ??%
trump                20    ??%    ??%    ??%    $???      ??%
trade-war            25    ??%    ??%    ??%    $???      ??%

═══ TRADING STRATEGY IMPLICATIONS ═══

✅ USE SM for: [categories with hit rate > 70%]
⚠️  CAUTION with SM for: [categories with 50-70%]
❌ IGNORE SM for: [categories with < 50%]
```

## Что дальше после Фазы 2

1. **Проспективный сбор** — cron job для ежедневных SM snapshot по открытым рынкам
2. **Grid search** по параметрам SM (conviction threshold, holder count, shrinkage_k)
3. **Комбинация SM + price momentum** — SM как фильтр, momentum как тайминг
4. **Автоматизация** — интеграция в торгового бота (если SM надёжен для категории)
