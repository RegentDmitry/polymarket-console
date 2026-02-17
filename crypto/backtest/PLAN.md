# План: Бэктест Deribit IV vs Polymarket — ВСЕ закрытые крипто-рынки

## Контекст

Текущая стратегия: покупаем на Polymarket, когда Deribit touch_prob показывает edge vs PM price. Нужно проверить на исторических данных — давала ли стратегия реальный +EV?

## Доступные данные (проверено через API, всё бесплатно)

### Polymarket закрытые рынки (prices-history API)

| Ивент | Slug | Рынков | Макс глубина | Период |
|-------|------|--------|-------------|--------|
| **BTC 2025 annual** | what-price-will-bitcoin-hit-in-2025 | 30 | **368 дн** | Dec 31 '24 → Jan 1 '26 |
| **ETH 2025 annual** | what-price-will-ethereum-hit-in-2025 | 18 | **368 дн** | Dec 31 '24 → Jan 1 '26 |
| BTC Nov 2025 | what-price-will-bitcoin-hit-in-november-2025 | 21 | 30 дн | Nov 2 → Dec 1 '25 |
| ETH Nov 2025 | what-price-will-ethereum-hit-in-november-2025 | ~20 | ~30 дн | |
| BTC Jan 2026 | what-price-will-bitcoin-hit-in-january-2026 | 20 | 31 дн | Jan 2 → Feb 1 '26 |
| ETH Jan 2026 | what-price-will-ethereum-hit-in-january-2026 | 19 | ~31 дн | |
| BTC 2027 annual (closed subs) | what-price-will-bitcoin-hit-before-2027 | 6 closed | 74 дн | Nov 25 '25 → Feb '26 |

**Итого: ~130+ закрытых рынков** с daily price data.

### Deribit
- **DVOL index**: **779 дней** daily (Jan 1 '24 → Feb 17 '26) — BTC и ETH
- **BTC/ETH-PERPETUAL**: daily spot prices за весь период

### IV Source
DVOL для всех рынков (покрывает весь период, consistent).

## Стратегия бэктеста

**Bankroll:** $1,000. **Trade size:** $100 фикс.

**Вход:**
- "reach" markets: `touch_prob_above > pm_yes + min_edge` → BUY YES
- "dip" markets: `pm_yes > touch_prob_below + min_edge` → BUY NO

**Выход:**
1. Resolution → $1/token если наша сторона, $0 если нет
2. Edge < exit_edge → продаём по PM цене

**Grid search:** min_edge × exit_edge × drift

## Порядок реализации

1. [x] Сохранить план
2. [x] Установить tqdm
3. [x] Создать `data_loader.py` — discover + load + cache + ThreadPoolExecutor + tqdm
4. [x] Создать `backtest.py` — симуляция + grid search + matplotlib
5. [x] Тестовый запуск (131 рынков, 144 сделки, +$5840)
6. [x] Верификация P&L (ETH dip $2400 Jan = корректная потеря, fee bug fixed)
7. [x] Grid search (36 комбинаций, ВСЕ прибыльные, +$3.1k → +$11.4k)
8. [x] Результаты (представлены пользователю)
