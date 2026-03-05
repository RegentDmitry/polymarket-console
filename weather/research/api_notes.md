# API Notes — Weather Markets

## Polymarket (Gamma API)

### Discovery

Рынки на температуру именуются по паттерну:
```
highest-temperature-in-{city}-on-{month}-{day}
```

Города: london, chicago, nyc, miami, dallas, atlanta, seattle, toronto,
paris, seoul, lucknow, buenos-aires, sao-paulo, ankara, munich, wellington.

Новые рынки появляются ежедневно (на 2-3 дня вперёд).

### Структура

Каждый event содержит 7-9 sub-markets (бакетов):
- Нижний крайний: "≤X°F" (или "X°C or below")
- Средние: "between X-Y°F" (или "X°C")
- Верхний крайний: "≥X°F" (или "X°C or higher")

Бакеты в °F обычно 2°F шириной, в °C — 1°C.

### Резолюция

Источник: Weather Underground (wunderground.com).
Конкретная метеостанция для каждого города.
Highest temperature за весь день (00:00-23:59 local time).

Пример URL резолюции:
```
https://www.wunderground.com/history/daily/us/il/chicago/KORD
https://www.wunderground.com/history/daily/gb/london/EGLC
```

### Ценообразование

`outcomePrices` — JSON array, первый элемент = YES price.
Ликвидность — `liquidity` поле на sub-market уровне.

### Месячные температурные рынки

Также существуют:
- "February 2026 Temperature Increase (°C)" — глобальная аномалия
- "March 2026 Temperature Increase (°C)"
- Резолюция: Copernicus/ERA5 данные

Эти рынки крупнее (\$14-20k liq) и дольше живут (до 10-го числа следующего месяца).

---

## Open-Meteo API (прогнозы)

### Базовый endpoint

```
GET https://api.open-meteo.com/v1/forecast
  ?latitude={lat}&longitude={lon}
  &hourly=temperature_2m
  &temperature_unit=fahrenheit  (или celsius)
  &timezone={tz}
  &forecast_days=3
```

Возвращает hourly прогнозы. Daily max = `max(hourly temps for that day)`.

### Multi-model ensemble

```
GET https://api.open-meteo.com/v1/forecast
  ?latitude={lat}&longitude={lon}
  &hourly=temperature_2m
  &models=gfs_seamless,ecmwf_ifs025,icon_seamless,jma_seamless
  &temperature_unit=fahrenheit
  &forecast_days=3
```

Разброс между моделями → оценка uncertainty (σ прогноза).

### Rate limits

- Бесплатный tier: 10,000 запросов/день, 5,000/час
- Достаточно для нашего использования (15 городов × 3 дня = 45 запросов)

### Исторические данные (для калибровки)

```
GET https://archive-api.open-meteo.com/v1/archive
  ?latitude={lat}&longitude={lon}
  &daily=temperature_2m_max
  &start_date=2026-01-01&end_date=2026-03-05
```

---

## Weather Underground API (actual resolution data)

Для бэктеста нужны фактические данные со станций WU.
WU не имеет бесплатного API — парсить HTML или использовать
альтернативные источники (NOAA ISD, Open-Meteo archive).

Для калибровки можно:
1. Open-Meteo archive (ERA5 reanalysis) — не совпадёт точно с WU станцией
2. NOAA ISD — raw station data, нужно найти правильный station ID
3. Scrape WU history page (не рекомендуется)

Лучший подход: сравнивать прогноз Open-Meteo с фактом Open-Meteo archive,
а bias vs WU учитывать отдельно (если будет).
