# Критика модели ценообразования крипто-рынков Polymarket

## Резюме

Модель содержит несколько серьёзных проблем, от фундаментальных (путаница risk-neutral/physical мер, discretization bias) до практических (захардкоженная IV). При этом базовая интуиция — что контракты Polymarket являются one-touch бинарными опционами и должны оцениваться через touch probability — **абсолютно верна**.

---

## 1. Touch probability — правильный ли подход?

### Вердикт: ВЕРНО

Контракты типа "Will BTC hit $100,000 by end of 2026?" резолвятся по правилу:
> "This market will immediately resolve to 'Yes' if any Binance 1 minute candle for Bitcoin (BTC/USDT) has a final 'High' price equal to or greater than the price specified."

Это **one-touch binary option** (hit option). Достаточно, чтобы цена хотя бы раз коснулась барьера. Использование touch probability (first passage time) — единственно правильный подход.

Формулы `touch_above` / `touch_below` — классические формулы first passage time для GBM, математически корректны.

**Нюанс**: Polymarket мониторит по 1-минутным свечам Binance (1440 наблюдений/день). Поправка Broadie-Glasserman-Kou для дискретного мониторинга составляет ~0.03% — можно игнорировать.

---

## 2. IV с Deribit — правильно ли?

### Вердикт: ЧАСТИЧНО ВЕРНО, есть системные ошибки

**Проблема 1: IV ≠ "правильная" vol для touch probability**
IV из Deribit — risk-neutral implied vol, откалиброванная из европейских опционов (чувствительны к terminal distribution). Touch probability чувствительна ко **всему пути**. В GBM мире совпадают, в реальном — нет.

**Проблема 2: Volatility Risk Premium (VRP)**
IV > Realized Vol примерно 70% времени, с premium ~15 пунктов. Использование IV **завышает** touch probability. Для покупателя YES это **консервативная** ошибка (меньше ложных сигналов).

**Проблема 3: Smile/skew не учитывается**
ATM IV не учитывает volatility smile. Для deep OTM страйков (BTC $150k при spot $84k) реальная IV может значительно отличаться.

**Проблема 4: Term structure**
Одна IV для контрактов с разными экспирациями (37 дней vs 306 дней). Краткосрочная vol обычно выше долгосрочной.

**Решение**: использовать DVOL API для текущей ATM IV соответствующего тенора, и в идеале strike-specific IV.

---

## 3. Drift из фьючерсов — правильно ли?

### Вердикт: СПОРНО

**Ключевая проблема: Physical vs Risk-Neutral мера**

- **Risk-neutral (Q)**: drift = risk-free rate. Для хеджированных позиций.
- **Physical (P)**: drift = реальное ожидание. Для спекулятивных позиций.

Polymarket — это **prediction market**, цены отражают **физические вероятности**. Покупка YES — чистая ставка, нет хеджирующего портфеля. Нужна **physical measure (P)**.

Фьючерсная кривая Deribit отражает risk-neutral ожидания + cost of carry + funding rate, а НЕ физическое ожидание будущей цены.

**Практически**:
- `drift = 0` — нижняя граница (если edge есть при d=0, это сильный сигнал)
- `drift из фьючерсов` — приближение к Q-мере, не P-мере
- Подход "два сценария" (d=0 и d=fut) разумен, но drift из фьючерсов не является ни risk-neutral rate, ни physical expectation

---

## 4. Student-t vs другие распределения

### Вердикт: РАЗУМНЫЙ first-order approximation

Student-t хорошо описывает fat tails крипто-returns. Однако:

- **Jump-Diffusion (Kou)** — лучше для barrier options: создаёт jumps (BTC -15% за час), а не только толстые хвосты дневных returns
- **Stochastic Volatility (Heston)** — снижает ошибки на ~8.55%
- **Stable distributions** — теоретически более обоснованы при df < 3

Student-t на дневных шагах не ловит **intraday extremes**, что важно для touch probability.

---

## 5. Калибровка df = 2.61 (BTC), 2.88 (ETH)

### Вердикт: РАЗУМНО, но с оговорками

**Важное уточнение**: При df = 2.61 > 2 дисперсия **конечна** (= df/(df-2) = 4.28), но третий и четвёртый моменты не существуют (df < 3 и df < 4). Это значит:
- Sample variance нестабильна
- CLT работает, но сходимость медленнее
- Confidence interval для df оценки **широк**

**Для MC**: 150k paths достаточно для бинарного estimator (touched/not touched), стандартная ошибка ~0.1%.

**Рекомендация**: sensitivity analysis по df = {2.0, 2.5, 3.0, 4.0}.

---

## 6. Discretization bias в MC

### Вердикт: BIAS ЕСТЬ, занижает touch probability

Мониторинг только на конце каждого дневного шага пропускает intra-day пересечения барьера. Для GBM с sigma=52% и дневными шагами пропущенные пересечения ~2-5%.

Для Student-t bias **больше** (нет аналитической поправки). Brownian Bridge correction работает только для нормальных инноваций.

**Решение**: увеличить число шагов до 4h или 1h (x6 или x24), или Brownian Bridge sampling.

**Направление bias**: занижение touch prob → **консервативно** для YES, **антиконсервативно** для NO.

---

## 7. Что может пойти не так?

1. **Regime change**: IV=30% летом vs IV=100% зимой — модель с постоянной IV не учитывает
2. **Leverage effect**: в crash vol растёт + цена падает одновременно
3. **Asymmetric tails**: один df для обоих направлений (хотя откалиброваны: df_neg=2.42, df_pos=2.84)
4. **Market maker edge**: профессионалы на PM могут использовать более сложные модели
5. **Execution**: 2% taker fee + slippage + impact может съесть edge 3-5%
6. **Resolution risk**: только Binance BTC/USDT — flash crash на одной бирже

---

## 8. Итоговые рекомендации (по приоритету)

### Высокий приоритет:
1. **Динамическая IV**: DVOL API для ATM IV соответствующего тенора (уже реализовано в trading_bot)
2. **Sensitivity analysis по df**: запуск с df = {2.0, 2.5, 3.0, 4.0}
3. **Увеличить шаги MC** до 4h для уменьшения discretization bias

### Средний приоритет:
4. **Asymmetric tails**: разные df для up/down moves
5. **Strike-specific IV**: из ближайшего страйка Deribit
6. **VRP adjustment**: уменьшать IV на 10-15 пунктов для physical measure

### Низкий приоритет:
7. Jump-diffusion модель (Kou)
8. Stochastic volatility (Heston)

---

## Фундаментальный вопрос: в какой мере ценить?

Для Polymarket нужна **physical probability** (спекулятивная ставка без хеджирования):
- IV из Deribit — risk-neutral vol, не physical vol
- Risk-neutral touch prob (drift=0, vol=IV) — это цена one-touch option на Deribit
- Для physical probability: (a) realized vol вместо IV, или (b) IV минус VRP, и (c) субъективный drift

Если risk-neutral touch prob > цена Polymarket — это потенциальный арбитраж (но не безрисковый).

---

## Источники

- [Gosset Formula — Student-t European Options](https://arxiv.org/abs/0906.4092)
- [Jump Risk in Bitcoin Options](https://www.mdpi.com/2227-7390/9/20/2567)
- [Bitcoin Volatility Regimes — Deribit Insights](https://insights.deribit.com/industry/bitcoin-options-finding-edge-in-four-years-of-volatility-regimes/)
- [Barrier Options — Oxford Mathematics](https://people.maths.ox.ac.uk/howison/barriers.pdf)
- [Continuity Correction for Discrete Barriers (Broadie, Glasserman, Kou)](http://www.columbia.edu/~sk75/mfBGK.pdf)
- [Crypto Vol-of-Vol Modeling](https://onlinelibrary.wiley.com/doi/10.1002/fut.70029)
- [Equilibrium Pricing of Bitcoin Options](https://onlinelibrary.wiley.com/doi/10.1002/fut.70058)
