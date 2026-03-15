# Weather Temperature Bot — Trading Strategy v2.0

## Overview

The bot trades Polymarket daily high temperature markets across 18 cities.
It uses a **single best NWP model per city** selected by lowest RMSE over
14 months of previous_day1 data (Jan 2025 — Mar 2026, 438 days per city).

Trades are only generated after the city's designated model publishes its
**last forecast of the day** (12Z+ init run), ensuring maximum accuracy.

## Data Sources

| Component | Source | Notes |
|-----------|--------|-------|
| Forecasts | Open-Meteo API (4 NWP models) | GFS, ECMWF, ICON, JMA |
| Calibration | Open-Meteo Previous Runs API | `previous_day1` = last forecast of prior day |
| Actuals (calibration) | Open-Meteo Historical Weather API | Used for offline calibration |
| Actuals (live) | Weather.com API (WU backend) | 100% match with PM resolution |
| Resolution | Weather Underground (per-market URL) | Station mapping in `cities.json` |
| Model freshness | Open-Meteo S3 `meta.json` | Polled every 5 min |

## Model Selection Per City

| Best Model | Cities | Last Run Avail |
|------------|--------|----------------|
| **ECMWF** (15) | ankara, atlanta, buenos-aires, chicago, dallas, london, lucknow, miami, nyc, paris, sao-paulo, seattle, seoul, tokyo, wellington | **~19:00 UTC** |
| **ICON** (2) | munich, tel-aviv | **~22:00 UTC** |
| **GFS** (1) | toronto | **~21:30 UTC** |

## Timezone and Daily Boundaries

**Polymarket and Weather Underground use LOCAL TIME for daily boundaries.**
Weather.com API returns observations for the station's local calendar day.

| City | TZ | "Mar 15" local in UTC |
|------|-----|----------------------|
| Wellington | UTC+13 | Mar 14 11:00 → Mar 15 11:00 UTC |
| Seoul | UTC+9 | Mar 14 15:00 → Mar 15 15:00 UTC |
| London | UTC+0 | Mar 15 00:00 → Mar 16 00:00 UTC |
| Seattle | UTC-7 | Mar 15 07:00 → Mar 16 07:00 UTC |

Open-Meteo forecasts are also requested with `timezone={city_tz}`, so daily max
is computed over the correct local day. Everything is consistent.

## Trading Window and Lead Time

For each city, the bot waits until its best model has `init_time >= 12:00 UTC today`
(checked via S3 meta.json). Signals are generated for the **next local day's** markets.

When ECMWF 12Z becomes available (~19:00 UTC), the effective lead time varies by timezone:

| City Group | 19:00 UTC = local | Target day | Lead time | vs Calibration |
|------------|-------------------|------------|-----------|----------------|
| **APAC** (Wellington, Seoul, Tokyo, Lucknow) | early morning next day | same day | **3-12h** | Conservative (sigma too high) |
| **Europe** (London, Paris, Munich, Ankara) | evening | next day | **14-18h** | Slightly better |
| **Americas** (US + Toronto, Buenos Aires, Sao Paulo) | afternoon | next day | **19-26h** | Matches calibration |

The calibration is based on `previous_day1` ≈ 18-24h lead time. For APAC cities,
we trade with a shorter lead time (more accurate forecast) but use the longer-lead
sigma — this makes us **conservative** (never overconfident). Safe but may miss
some good bets in APAC. Future improvement: per-timezone sigma calibration.

| City Group | Model | Signal Start | Signal End |
|------------|-------|-------------|------------|
| 15 ECMWF cities | ECMWF 12Z | ~19:00 UTC | ~06:00 UTC+1 |
| Munich, Tel-Aviv | ICON 18Z | ~22:00 UTC | ~06:00 UTC+1 |
| Toronto | GFS 18Z | ~21:30 UTC | ~06:00 UTC+1 |

## Calibration Parameters (per city)

From `trading_bot/data/calibration_single_model.json`:

| Parameter | Description |
|-----------|-------------|
| `best_model` | Single NWP model with lowest RMSE for this city |
| `sigma` | RMSE of best model over 438 days (°F or °C) |
| `bias` | Mean error (forecast - actual), subtracted from forecast |
| `student_t_df` | Degrees of freedom for fat-tail pricing (if applicable) |

### Calibration Values

| City | Model | Sigma | Bias | Student-t df |
|------|-------|-------|------|-------------|
| ankara | ECMWF | 0.83°C | -0.26 | 6.8 |
| atlanta | ECMWF | 2.34°F | -0.63 | 4.5 |
| buenos-aires | ECMWF | 1.14°C | -0.23 | 6.4 |
| chicago | ECMWF | 2.69°F | -1.51 | 6.2 |
| dallas | ECMWF | 2.20°F | -0.01 | 7.4 |
| london | ECMWF | 0.73°C | -0.20 | 6.8 |
| lucknow | ECMWF | 1.14°C | -0.51 | 9.7 |
| miami | ECMWF | 1.60°F | -0.79 | 11.9 |
| munich | ICON | 1.18°C | +0.14 | 6.0 |
| nyc | ECMWF | 2.35°F | -1.11 | 6.1 |
| paris | ECMWF | 1.01°C | -0.12 | 6.4 |
| sao-paulo | ECMWF | 1.23°C | +0.17 | 7.4 |
| seattle | ECMWF | 1.58°F | -0.21 | 14.6 |
| seoul | ECMWF | 1.06°C | +0.08 | 7.2 |
| tel-aviv | ICON | 0.87°C | +0.06 | 6.3 |
| tokyo | ECMWF | 1.17°C | +0.10 | 10.7 |
| toronto | GFS | 1.47°C | +0.20 | 6.7 |
| wellington | ECMWF | 0.66°C | -0.04 | 6.9 |

## Signal Generation

For each active market bucket:

1. Fetch latest forecast from best model for this city
2. Apply bias correction: `forecast = model_value - bias`
3. Compute fair price: `P(actual in bucket)` using Student-t(df, forecast, sigma)
4. Compute edge: `edge = fair_price - market_ask_price`
5. Generate BUY signal if edge ≥ 8%

## Trading Filters

| Filter | Value | Rationale |
|--------|-------|-----------|
| `min_edge` | 8% | Calibrated threshold — ROI +27.9% in backtest |
| `max_edge` | unlimited | Calibrated sigma prevents false large edges |
| `min_price` | 8% | Avoid extreme tail buckets where model is unreliable |
| `min_hours` | 12h | Don't trade too close to resolution |
| `last_forecast_only` | true | Wait for city's best model late run (12Z+ init) |
| `skip_cities` | none | Per-city calibration handles accuracy differences |

## Position Sizing

- Quarter-Kelly: `size = 0.25 * edge / (1 - fair_price) * portfolio`
- Max per event (city+date): $200
- Max per city: $500
- Min position: $2

## Backtest Results (Mar 9-14, 2026)

| Strategy | Bets | Wins | Win% | P&L | ROI |
|----------|------|------|------|-----|-----|
| Single best model | 294 | 83 | 28% | +$819 | **+27.9%** |
| Weighted ensemble (4 models) | 263 | 83 | 32% | +$594 | +22.6% |

## On-Chain Settlement

Winning positions are automatically redeemed via NegRisk adapter:
- Contract: `0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296`
- CTF: `0x4D97DCd97eC945f40cF65F87097ACe5EA0476045`
- Both wins (→ USDC) and losses (→ portfolio cleanup) are redeemed

## Status

**OBSERVE mode** — bot scans and shows signals but does not execute trades.
Awaiting manual review period before enabling live trading.

## Future Improvements

1. **Multi-lead-time calibration**: sigma(city, lead_hours) for trading at any time
2. **WU-based actuals calibration**: recalibrate on Weather.com data instead of Open-Meteo
3. **Seasonal recalibration**: sigma varies by season (winter vs summer)
4. **Liquidity-aware sizing**: adjust position size based on orderbook depth
