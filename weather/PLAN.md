# Weather Markets — Стратегия и План

## Идея

Polymarket предлагает ежедневные рынки на максимальную температуру в 10-15 городах мира.
Каждый рынок разбит на 7-9 температурных бакетов (например "44-45°F", "46-47°F").
Прогнозы погоды от NWP-моделей (GFS, ECMWF) на 1-3 дня вперёд дают точность ~95%.
PM трейдеры часто не следят за прогнозами — edge 10-50%.

## Ground Truth

**Источник прогноза:** Open-Meteo API (бесплатный, агрегирует GFS/ECMWF/ICON).

```
https://api.open-meteo.com/v1/forecast?latitude=LAT&longitude=LON
  &hourly=temperature_2m&temperature_unit=fahrenheit&forecast_days=3
```

Из hourly данных берём `max(temperature_2m)` за день = прогноз daily high.

**Источник резолюции:** Weather Underground — конкретная метеостанция для каждого города.
Например London = London City Airport (EGLC), Chicago = O'Hare (KORD).

**Ensemble прогнозы** (для оценки uncertainty):
```
https://api.open-meteo.com/v1/forecast?latitude=LAT&longitude=LON
  &hourly=temperature_2m&models=gfs_seamless,ecmwf_ifs025,icon_seamless
```
Разброс между моделями ≈ σ прогноза.

## Модель

### Базовая (v1)
- Прогноз daily max из Open-Meteo
- Распределение: `Normal(forecast_max, σ)`
- σ калибруется по горизонту:
  - 1 день: σ ≈ 2°F / 1°C
  - 2 дня: σ ≈ 3°F / 1.5°C
  - 3 дня: σ ≈ 4°F / 2°C
- Fair price бакета = `P(lower ≤ X < upper)` под Normal

### Улучшенная (v2)
- Ensemble spread из нескольких NWP-моделей → σ адаптивный
- Историческая калибровка: forecast vs actual для каждой станции
- Асимметрия: тёплые выбросы чаще (skew-normal или Student-t)
- Учёт метеостанции резолюции vs прогноза (bias correction)

## Edge (proof of concept, 2026-03-05)

| Город | Дата | Прогноз | Лучший бакет | PM | Модель | Edge |
|-------|------|---------|-------------|-----|--------|------|
| NYC | Mar 7 | 44.2°F | 42-43°F YES | 6% | 24% | +18% |
| NYC | Mar 7 | 44.2°F | ≥48°F NO | 32% | 90% | +58% |
| Chicago | Mar 5 | 46.9°F | 48-49°F YES | 7% | 21% | +14% |
| Chicago | Mar 7 | 64.1°F | ≥60°F YES | 77% | 91% | +15% |
| Miami | Mar 6 | 81°F | 78-79°F YES | 5% | 21% | +17% |
| Dallas | Mar 7 | 71.8°F | ≥64°F YES | 94% | 100% | +6% |

Edge огромный (10-50%), но ликвидность на бакет $300-3,000.

## Города и координаты

Polymarket покрывает ~15 городов. Список обновляется ежедневно.

| Город | Lat | Lon | Станция WU | Единицы |
|-------|-----|-----|-----------|---------|
| Chicago | 41.88 | -87.63 | KORD | °F |
| NYC | 40.71 | -74.01 | KLGA/KJFK | °F |
| Miami | 25.76 | -80.19 | KMIA | °F |
| Dallas | 32.78 | -96.80 | KDFW | °F |
| Atlanta | 33.75 | -84.39 | KATL | °F |
| Seattle | 47.61 | -122.33 | KSEA | °F |
| Toronto | 43.65 | -79.38 | CYYZ | °C |
| London | 51.51 | -0.13 | EGLC | °C |
| Paris | 48.86 | 2.35 | LFPG | °C |
| Seoul | 37.57 | 126.98 | RKSS | °C |
| Mumbai/Lucknow | 26.85 | 80.95 | VILK | °C |
| Buenos Aires | -34.60 | -58.38 | SAEZ | °C |
| Sao Paulo | -23.55 | -46.63 | SBGR | °C |
| Ankara | 39.93 | 32.86 | LTAC | °C |
| Munich | 48.14 | 11.58 | EDDM | °C |
| Wellington | -41.29 | 174.78 | NZWN | °C |

## Экономика

- **Ликвидность на бакет:** $300-3,000 (реально можно купить на $50-200 без проскальзывания)
- **Комиссия PM:** 0% maker / ~0.1% taker = пренебрежимо
- **Edge:** 10-50% (огромный по сравнению с крипто 3-8%)
- **Города/день:** 10-15
- **Бакетов с edge/город:** 2-4
- **Средний profit на сделку:** $5-20 (при ставке $50-200 и edge 10-30%)
- **Теоретический daily P&L:** $50-300
- **Месячный:** $1,500-9,000

Ключевой ограничитель — ликвидность, не edge.

## Риски

1. **Модель прогноза неточна** — σ может быть занижен/завышен
   - Митигация: ensemble spread, историческая калибровка
2. **Метеостанция резолюции ≠ прогноз** — WU использует конкретную станцию
   - Митигация: узнать точную станцию для каждого города, bias correction
3. **PM бакеты могут быть шире/уже** — при широких бакетах edge размазывается
4. **Ликвидность может упасть** — маркет-мейкер может уйти
5. **Прогноз меняется** — купил утром, вечером прогноз изменился
   - Митигация: покупать ближе к закрытию (меньше σ), но и ликвидность ниже
6. **Tail events** — шторм, фрост, heatwave — σ прогноза скачет
   - Митигация: ensemble spread автоматически это учтёт

## Отличия от крипто-бота

| Аспект | Крипто | Погода |
|--------|--------|--------|
| Тип рынка | Touch barrier (binary) | Categorical (7-9 бакетов) |
| Горизонт | 1 месяц — 1 год | 1-3 дня |
| Ground truth | Deribit IV + MC | NWP прогнозы |
| Edge | 3-8% | 10-50% |
| Ликвидность | $1k-50k/рынок | $300-3k/бакет |
| Частота | 1-2 скана/день | Ежедневно, 10-15 городов |
| Sell | Sell limit at fair | Hold to resolution (1 день) |
| Kelly | Важен (большие суммы) | Менее важен (ликвидность лимит) |

## Архитектура бота (будущее)

```
weather/
├── PLAN.md              ← этот файл
├── scanner.py           ← discovery рынков через Gamma API
├── forecast.py          ← прогнозы из Open-Meteo
├── model.py             ← Normal/Student-t distribution → fair prices
├── edge.py              ← сравнение model vs PM → edge по бакетам
├── cities.json          ← город → координаты, станция, единицы
├── calibration/         ← историческая калибровка σ по станциям
│   └── backtest.py      ← forecast vs actual за последние N дней
└── notebooks/
    └── exploration.ipynb ← исследование и визуализация
```

Компоненты переиспользуемые из crypto:
- Polymarket executor (buy/sell)
- Position storage
- TUI framework (textual)
- Notification system (Telegram)

## Следующие шаги

1. **Backtest**: скачать историю прогнозов Open-Meteo vs WU actuals за 30 дней → калибровать σ
2. **Scanner**: автоматически находить все активные temperature рынки на PM
3. **Edge scanner**: прогноз → модель → edge по каждому бакету → отчёт
4. **Manual trading**: несколько дней вручную по рекомендациям сканера
5. **Автоматизация**: бот с автопокупкой
