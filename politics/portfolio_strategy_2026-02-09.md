# Political Portfolio Strategy — $1,500 (2026-02-09)

## Источник данных

Smart Money Analysis по 67 политическим рынкам Polymarket (25 топ-событий по объёму).
Методология: conviction-weighted Smart Money Flow (profit x conviction x shrinkage x politics_bonus).

## Фильтрация шума

Отсеяно:
- **Edge >50%** — скорее шум (Trump GOP nominee 79% vs PM 5%, China invades Taiwan 78% vs 10%)
- **GTA VI markets** — развлечение, не политика
- **Elon tweets** — не инвестируемо серьёзно
- **Ликвидность <$20k** — невозможно нормально зайти/выйти
- **Срок >1 года** — слишком долгий горизонт (JD Vance 2028, Kamala Harris 2028)

## Качественные сигналы

### Tier 1 — Высокая уверенность (NO ставки на "не произойдёт")

| Рынок | PM | Smart $ | Edge | Liq | Почему хорошо | Ссылка |
|-------|-----|---------|------|-----|--------------|--------|
| US strikes Iran by Feb 28 NO | 78% | 96% | +18.7% | $202k | Flow -0.80, до резолюции 19 дней | [Polymarket](https://polymarket.com/event/us-strikes-iran-by) |
| Khamenei out by Mar 31 NO | 82% | 101% | +19.0% | $196k | Flow -0.93, почти единогласно | [Polymarket](https://polymarket.com/event/khamenei-out-as-supreme-leader-of-iran-by-march-31) |
| Trump acquires Greenland NO | 88% | 161% | +73%* | $587k | Flow -0.94, абсурдный edge но NO очевидно | [Polymarket](https://polymarket.com/event/will-trump-acquire-greenland-before-2027) |
| US acquires Greenland 2026 NO | 84% | 95% | +11.6% | $180k | Flow -0.85 | [Polymarket](https://polymarket.com/event/will-the-us-acquire-any-part-of-greenland-in-2026) |
| Fed 25bps cut in March NO | 86% | 97% | +11.6% | $307k | Flow -0.94, ФРС вряд ли режет | [Polymarket](https://polymarket.com/event/fed-decision-in-march-885) |

*Edge по Greenland гигантский потому что implied уходит в минус — SM единогласно на NO*

### Tier 2 — Средняя уверенность

| Рынок | PM | Smart $ | Edge | Liq | Комментарий | Ссылка |
|-------|-----|---------|------|-----|------------|--------|
| US strikes Iran by Mar 31 NO | 60% | 88% | +28.9% | $151k | Лучший edge по NO, 50 дней | [Polymarket](https://polymarket.com/event/us-strikes-iran-by) |
| Russia-Ukraine ceasefire by Mar 31 YES | 8% | 21% | +13.6% | $567k | Flow -0.55 КОНФЛИКТ с edge, но liq огромная | [Polymarket](https://polymarket.com/event/russia-x-ukraine-ceasefire-by-march-31-2026) |
| US/Israel strikes Iran Feb YES | 23% | 46% | +22.9% | $27k | Мало ликвидности | [Polymarket](https://polymarket.com/event/usisrael-strikes-iran-by) |

### Tier 3 — Спекулятивные

| Рынок | PM | Smart $ | Edge | Комментарий | Ссылка |
|-------|-----|---------|------|------------|--------|
| Trump meets Putin by Mar 31 | 14% | 38% | +24.3% | Малая ликвидность | [Polymarket](https://polymarket.com/event/will-trump-meet-with-putin-again-by) |
| Venezuela: Maduro stays NO | 8% | 25% | +17.0% | Далеко, но хорошая ликвидность | [Polymarket](https://polymarket.com/event/venezuela-leader-end-of-2026) |

---

## Распределение портфеля $1,500

Все 6 позиций — ставки на то, что события НЕ произойдут. Акцент на независимые от геополитики позиции (Fed, Greenland).

| # | Ставка | Что покупать | Сумма | % | Цена | Мультипл. | Срок | Ссылка |
|---|--------|-------------|-------|---|------|-----------|------|--------|
| 1 | Fed March: ставку не снизят | **"No change" → Buy YES** | $450 | 30% | 84c | x1.19 | ~40 дней | [Polymarket](https://polymarket.com/event/fed-decision-in-march-885) |
| 2 | US не ударит по Iran до Feb 28 | **"February 28" → Buy NO** | $300 | 20% | 78c | x1.28 | 19 дней | [Polymarket](https://polymarket.com/event/us-strikes-iran-by) |
| 3 | Khamenei останется до Mar 31 | **Buy NO** | $250 | 17% | 82c | x1.22 | 50 дней | [Polymarket](https://polymarket.com/event/khamenei-out-as-supreme-leader-of-iran-by-march-31) |
| 4 | Trump не купит Greenland | **Buy NO** | $200 | 13% | 88c | x1.14 | ~11 мес | [Polymarket](https://polymarket.com/event/will-trump-acquire-greenland-before-2027) |
| 5 | US не ударит по Iran до Mar 31 | **"March 31" → Buy NO** | $150 | 10% | 60c | x1.67 | 50 дней | [Polymarket](https://polymarket.com/event/us-strikes-iran-by) |
| 6 | US не купит часть Greenland в 2026 | **Buy NO** | $150 | 10% | 84c | x1.19 | ~11 мес | [Polymarket](https://polymarket.com/event/will-the-us-acquire-any-part-of-greenland-in-2026) |

### Распределение по рискам

```
Fed (независимый):     $450  (30%)  ████████████
Iran (Feb+Mar):        $450  (30%)  ████████████
Khamenei (ME):         $250  (17%)  ███████
Greenland (x2):        $350  (23%)  █████████
```

Middle East суммарно: $700 (47%) — Iran $450 + Khamenei $250.
После резолюции Iran Feb 28 (19 дней): ME exposure снизится до $400 (27%).

### Реинвестирование после Feb 28

Если Iran Feb 28 NO выиграл → получаем ~$384 (+$84 прибыль).
Варианты реинвеста: свежие short-duration NO с сильным smart money сигналом, или увеличить Fed.

---

## Ожидаемые результаты

| Сценарий | Возврат | Прибыль |
|----------|---------|---------|
| Все 6 NO резолвятся | $1,500 → ~$1,780 | **+$280** |
| Worst case (все стопы) | $1,500 → ~$1,195 | -$305 |
| Iran black swan (Feb+Mar NO проиграли) | $1,500 → ~$1,075 | -$425 |

## Главная идея

**Short-duration NO bets** — ставки на то, что события НЕ произойдут в ближайшие 1-2 месяца.
Это самая безопасная стратегия на Polymarket, и smart money единогласно это подтверждает.

### Преимущества NO-стратегии:
- Время работает на тебя (чем ближе дедлайн, тем дороже NO если событие не случилось)
- Высокая вероятность выигрыша (геополитические события обычно НЕ происходят)
- Smart money flow -0.80...-0.94 на всех Tier 1 ставках
- Быстрый оборот капитала (19-50 дней)
- Реинвестирование: после резолюции Feb 28 ($300 → $384) перекладываем в следующий NO

### Риски:
- Black swan: если США ударят по Ирану — потеря $450 (Iran Feb + Iran Mar)
- Корреляция: Iran strikes + Khamenei связаны через Middle East ($700, 47%)
- Ликвидность на выходе: на NO-рынках может быть сложно продать до резолюции
- Greenland $350 заморожены на 11 месяцев ради ~$50 прибыли (низкая капиталоэффективность)

### Календарь резолюций:
- **Feb 28** — US strikes Iran Feb 28 → если NO, получаем ~$384 → реинвест
- **Mar ~15** — Fed decision March → если NO, получаем ~$523
- **Mar 31** — Khamenei out + Iran Mar 31 → две резолюции
- **Dec 31** — Greenland x2

---

*Стратегия на основе Smart Money Analysis от 2026-02-09.*
*Данные: Polymarket Data API, conviction-weighted flow по ~1000 уникальных трейдеров.*
