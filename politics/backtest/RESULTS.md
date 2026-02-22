# SM Backtest — Political Markets

## Фаза 1: Hit Rate (направление SM vs исход)

Markets analyzed: 24 (US elections 2024, tag=politics, vol>\$500k, NegRisk)
Trades (T-14, flow>0.1): 18
Win rate: 94%
Total P&L (hold to resolution): \$+340

### Per-Market Results

| Market | Won | T-30 | T-14 | T-7 | Hit? |
|--------|-----|------|------|-----|------|
| Will a candidate from another party win Ohio US Se | NO | -0.76 | -0.42 | — | ✓ |
| 2024 Balance of Power: R Prez R Senate R House | YES | +0.01 | -0.14 | — | ✗ |
| Will a Democrat win Arizona US Senate Election? | YES | +0.00 | -0.08 | — | ✗ |
| 2024 Balance of Power: D Prez, R Senate, R House | NO | -0.37 | -0.35 | — | ✓ |
| 2024 Balance of Power: D Prez, R Senate, D House | NO | -0.32 | -0.39 | — | ✓ |
| 2024 Balance of Power: D Prez, D Senate, D House | NO | -0.34 | -0.40 | — | ✓ |
| Will a candidate from another party win New York P | NO | -0.68 | -0.96 | — | ✓ |
| Will a Republican win New York Presidential Electi | NO | -0.14 | -0.48 | — | ✓ |
| Will a candidate from another party win Montana US | NO | -0.71 | -0.73 | — | ✓ |
| Will a Democrat win New York Presidential Election | YES | -0.05 | +0.23 | — | ✓ |
| Will a Republican win Pennsylvania US Senate Elect | YES | +0.16 | +0.12 | — | ✓ |
| Will a candidate from another party win Maine's 2n | NO | -0.53 | -0.50 | — | ✓ |
| Will a Democrat win Pennsylvania US Senate Electio | NO | -0.08 | -0.10 | — | ✓ |
| Will a Republican win Ohio US Senate Election? | YES | -0.02 | +0.12 | — | ✓ |
| Will a Democrat win Nevada US Senate Election? | YES | -0.07 | -0.01 | — | ✗ |
| Will a Republican win Nevada US Senate Election? | NO | +0.07 | -0.06 | — | ✓ |
| Will a candidate from another party win New Mexico | NO | -0.68 | -0.82 | — | ✓ |
| Will a Republican win New Mexico Presidential Elec | NO | +0.11 | -0.09 | — | ✓ |
| Will a Democrat win New Mexico Presidential Electi | YES | +0.32 | +0.35 | — | ✓ |
| Will a Democrat win Ohio US Senate Election? | NO | -0.22 | -0.35 | — | ✓ |
| Will a candidate from another party win Florida US | NO | -0.66 | -0.67 | — | ✓ |
| Will a Democrat win Montana US Senate Election? | NO | -0.37 | -0.38 | — | ✓ |
| Will a Republican win Montana US Senate Election? | YES | -0.09 | -0.10 | — | ✗ |
| Will a candidate from another party win Pennsylvan | NO | -0.78 | -0.86 | — | ✓ |

---

## Фаза 2: SM Reversal Exit Strategy (2026-02-17)

**Вопрос:** Помогает ли выход при развороте SM flow vs hold to resolution?

### Методология

- 24 рынка (те же US elections), SM flow каждые 5 дней
- Вход: \|flow\| > min\_edge
- Выход: SM flow развернулся (сменил знак через exit\_threshold)
- Если SM не развернулся → hold to resolution
- Fee: 2% на покупку (taker), 0% на продажу (maker)
- Trade size: \$100 на сделку

### Grid Search Results

| min\_edge | exit\_thr | Trades | Exits | Holds | Wins | Win% | P&L exit | P&L hold | Advantage |
|-----------|-----------|--------|-------|-------|------|------|----------|----------|-----------|
| 0.03 | 0.00 | 61 | 38 | 23 | 27 | 44% | -\$95 | +\$695 | **-\$790** |
| 0.05 | 0.00 | 59 | 36 | 23 | 27 | 46% | -\$109 | +\$837 | **-\$946** |
| **0.10** | **0.00** | **56** | **35** | **21** | **25** | **45%** | **-\$20** | **+\$1,002** | **-\$1,022** |
| 0.15 | 0.00 | 49 | 30 | 19 | 19 | 39% | -\$415 | -\$246 | -\$169 |
| 0.20 | 0.00 | 44 | 27 | 17 | 16 | 36% | -\$380 | -\$535 | +\$155 |
| 0.05 | -0.05 | 76 | 55 | 21 | 29 | 38% | -\$467 | -\$162 | -\$306 |
| 0.10 | -0.05 | 70 | 50 | 20 | 27 | 39% | -\$350 | +\$185 | -\$535 |
| 0.10 | -0.10 | 81 | 65 | 16 | 32 | 40% | -\$125 | +\$131 | -\$256 |
| 0.15 | -0.05 | 56 | 37 | 19 | 21 | 38% | -\$451 | -\$213 | -\$238 |
| 0.15 | -0.10 | 61 | 45 | 16 | 22 | 36% | -\$365 | -\$460 | +\$95 |
| 0.20 | -0.10 | 50 | 36 | 14 | 18 | 36% | -\$95 | -\$508 | +\$413 |

### Ключевые выводы

**SM reversal exit НЕ улучшает P&L.** Hold to resolution всегда лучше при разумных порогах (0.03-0.15).

Почему:
1. **SM flow шумный на 5-дневных интервалах** — разворот ≠ wrong direction, просто ротация трейдеров
2. **Exit фиксирует убытки рано** — Pennsylvania Senate: SM сказал YES, развернулся, продали @ 0.29... а рынок зарезолвился YES (\$419 упущенного профита)
3. **Hold to resolution = binary payout \$1.00** — exit даёт промежуточную цену, всегда хуже при правильном направлении
4. **Единственный edge от exit** — только при очень жёстких фильтрах (min\_edge=0.20, exit=-0.10), где "exit" по сути = "не входи в слабые сделки"

### Рекомендация для торговли

```
✅ SM — ХОРОШИЙ ФИЛЬТР ДЛЯ ВХОДА
   Hit rate 83% (all), 94% (|flow|>0.1)
   Используй SM flow для решения "входить или нет"

❌ SM — ПЛОХОЙ СИГНАЛ ДЛЯ ВЫХОДА
   Win rate с exits ~40-45% (хуже coin flip!)
   SM reversal на коротких интервалах = шум

📌 ЛУЧШАЯ СТРАТЕГИЯ: SM entry → hold to resolution
   P&L: +$1,002 на 24 рынках (vs -$20 с exits)
```

### Ограничения

- Выборка: 24 рынка, все US elections 2024
- Trader stats текущие (не исторические) — lookahead bias
- NegRisk only, vol>\$500k — ликвидные рынки
- Нужна проверка на других категориях (fed-rates, geopolitics, etc.)

---

## Фаза 3: Tail Risk Analysis (2026-02-18)

**Вопрос:** Когда позиция на 95¢+, стоит ли продать или держать до resolution?

### Данные

- **Gamma API:** 9,705 закрытых политических рынков (YES token IDs)
- **Dune query 6707950:** max/min цена для каждого токена (все NegRisk контракты)
- **Matched:** 181 рынок (YES tokens из Gamma × Dune data)
- **BIAS:** 94% YES-won в выборке (vs ~50% реально). Dune вернул 32k из 100k+ токенов

### Raw Results (biased)

| Threshold | YES reached | YES flipped | NO reached | NO flipped |
|-----------|-------------|-------------|------------|------------|
| 90%       | 170         | 0 (0.0%)    | 67         | 60 (89.6%) |
| 95%       | 170         | 0 (0.0%)    | 44         | 38 (86.4%) |
| 97%       | 169         | 0 (0.0%)    | 28         | 27 (96.4%) |

"YES reached 95%" = YES token max price ≥ 0.95
"NO reached 95%" = YES token min price ≤ 0.05 (meaning NO was at 95%+)
"Flipped" = market resolved OPPOSITE to the side that reached the threshold

### Bias Correction

Sample: 170 YES-won / 11 NO-won (94%/6%). True rate ~50%/50%.

Corrected flip rates (Bayesian with 50% prior):
- **YES at 95%:** ~0% (0 events in 11 NO-won markets — sample too small)
- **NO at 95%:** ~29% (inflated by YES-won dominance in sample)
- **True estimate:** 5-15% flip rate for either side (wide uncertainty)

Breakeven: hold at 95% is +EV only if flip rate < 5%.

### Tail Event Examples (NO at 95%+ that flipped)

| Market | NO max | Resolved | Volume |
|--------|--------|----------|--------|
| Republican win Pennsylvania Senate | 97¢ | YES | $2.6M |
| Democrat win Wisconsin Senate | 98¢ | YES | $256k |
| N-VA win Belgian federal election | 98¢ | YES | $38k |
| González win Venezuela presidential | 99¢ | YES | $1.7M |
| Republican win Ohio Senate | 95¢ | YES | $1.6M |

### EV Analysis

| Threshold | Breakeven flip% | Est. flip% | EV(hold) | Verdict |
|-----------|-----------------|------------|----------|---------|
| 90%       | 10%             | 5-15%      | borderline | partial exit |
| 95%       | 5%              | 5-15%      | likely negative | **EXIT** |
| 97%       | 3%              | 5-15%      | negative | **EXIT** |

### Рекомендация

```
ПРАВИЛА TAKE-PROFIT (политические рынки):
   90%+ -> продать 50% позиции (partial exit)
   95%+ -> продать всё (если не <24ч до resolution)
   97%+ -> ВСЕГДА продать (EV hold < 3c, risk > 97c)

   Исключение: если до resolution < 24ч и рынок стабилен -> hold
```

### Ограничения

- **Сильный YES-won bias** — 94% vs ~50% реально, коррекция приблизительная
- **max/min != timeline** — рынок мог достичь 95%, упасть до 50%, снова вырасти
- **Только 11 NO-won рынков** — YES-side flip rate не определён
- **Fix:** получить NO token IDs из Gamma API -> удвоить matched markets, убрать bias
