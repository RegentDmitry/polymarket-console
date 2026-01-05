# Стратегия выхода из позиций (Exit Strategy)

## Обзор

Документ описывает стратегию "Exit when edge disappears" — держать позицию пока есть edge, продавать когда edge исчез и есть прибыль.

---

## Две базовые стратегии

### 1. Hold to Expiry (пассивная)

```
Покупка → Ждёшь резолюции → $1 (выигрыш) или $0 (проигрыш)
```

| Плюсы | Минусы |
|-------|--------|
| Не платишь спред на выход | Капитал заморожен до резолюции |
| Простая логика | Бинарный риск (всё или ничего) |
| Максимальный ROI при выигрыше | Нельзя зафиксировать прибыль раньше |

### 2. Exit When Edge Disappears (активная)

```
Покупка → Мониторинг → Продажа когда edge исчез → Реинвестиция
```

| Плюсы | Минусы |
|-------|--------|
| Фиксация прибыли раньше | Платишь спред на выход |
| Освобождение капитала | Сложнее логика |
| Ограничение риска | Можешь продать слишком рано |

---

## Когда продавать

### Условия для продажи

Продавать когда **ВСЕ** условия выполнены:

1. **Edge исчез**: `model_fair_price - current_bid < 2%`
2. **Есть прибыль**: `current_bid > buy_price * 1.15` (минимум +15%)
3. **Или**: появилась лучшая возможность для капитала

### Почему минимум 15% прибыли?

```
Типичный спред на earthquake рынках: 10-20%
Половина спреда на выход: 5-10%
Буфер безопасности: 5%
─────────────────────────────
Итого минимум для продажи: 15%
```

Если продаёшь с прибылью < 15%, спред может съесть весь profit.

---

## Расчёт Target Price

### Формула

```
target_bid = buy_price × (1 + min_profit + spread_buffer)
```

Где:
- `buy_price` — цена покупки
- `min_profit` — минимальная желаемая прибыль (рекомендуется 15%)
- `spread_buffer` — буфер на спред (рекомендуется 5%)

### Пример

```
Покупка: YES @ 12¢
Min profit: 15%
Spread buffer: 5%

Target bid = 0.12 × (1 + 0.15 + 0.05) = 0.12 × 1.20 = 14.4¢

Продавать когда:
  - bid >= 14.4¢
  - И edge < 2%
```

---

## Функция принятия решения

```python
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Tuple


@dataclass
class Position:
    """Открытая позиция."""
    token_id: str
    event_slug: str
    outcome: str
    side: str  # "YES" или "NO"
    entry_price: float  # Цена покупки (0-1)
    entry_date: datetime
    size_usd: float  # Сумма в USD
    size_contracts: float  # Количество контрактов


@dataclass
class ExitSignal:
    """Сигнал на выход."""
    should_exit: bool
    reason: str
    current_profit_pct: float
    current_edge: float
    recommended_action: str  # "SELL", "HOLD", "HOLD_TO_EXPIRY"


def calculate_exit_signal(
    position: Position,
    current_bid: float,
    current_ask: float,
    model_fair_price: float,
    days_to_expiry: float,
    min_profit: float = 0.15,
    min_edge_to_hold: float = 0.02,
    min_days_for_active: int = 30,
) -> ExitSignal:
    """
    Определить нужно ли выходить из позиции.

    Args:
        position: Открытая позиция
        current_bid: Текущая цена bid (цена продажи)
        current_ask: Текущая цена ask (цена покупки)
        model_fair_price: Справедливая цена по модели
        days_to_expiry: Дней до резолюции
        min_profit: Минимальная прибыль для продажи (default 15%)
        min_edge_to_hold: Минимальный edge чтобы держать (default 2%)
        min_days_for_active: Минимум дней для активной торговли (default 30)

    Returns:
        ExitSignal с рекомендацией
    """
    # Расчёт текущей прибыли
    current_profit = (current_bid - position.entry_price) / position.entry_price

    # Расчёт текущего edge (относительно bid, т.к. продаём по bid)
    current_edge = model_fair_price - current_bid

    # Спред
    spread = current_ask - current_bid
    spread_pct = spread / current_ask if current_ask > 0 else 0

    # Слишком мало времени — hold to expiry
    if days_to_expiry < min_days_for_active:
        return ExitSignal(
            should_exit=False,
            reason=f"Мало времени ({days_to_expiry:.0f} дней) — hold to expiry выгоднее",
            current_profit_pct=current_profit,
            current_edge=current_edge,
            recommended_action="HOLD_TO_EXPIRY",
        )

    # Широкий спред — активная торговля невыгодна
    if spread_pct > 0.10:  # Спред > 10%
        return ExitSignal(
            should_exit=False,
            reason=f"Широкий спред ({spread_pct:.1%}) — hold to expiry",
            current_profit_pct=current_profit,
            current_edge=current_edge,
            recommended_action="HOLD_TO_EXPIRY",
        )

    # Edge всё ещё есть — держим
    if current_edge >= min_edge_to_hold:
        return ExitSignal(
            should_exit=False,
            reason=f"Edge есть ({current_edge:.1%}) — продолжаем держать",
            current_profit_pct=current_profit,
            current_edge=current_edge,
            recommended_action="HOLD",
        )

    # Edge исчез, но прибыли недостаточно
    if current_profit < min_profit:
        return ExitSignal(
            should_exit=False,
            reason=f"Edge исчез, но прибыль ({current_profit:.1%}) < минимума ({min_profit:.0%})",
            current_profit_pct=current_profit,
            current_edge=current_edge,
            recommended_action="HOLD",
        )

    # Edge исчез И прибыль достаточная — продаём
    return ExitSignal(
        should_exit=True,
        reason=f"Edge исчез ({current_edge:.1%}), прибыль {current_profit:.1%} — продавать",
        current_profit_pct=current_profit,
        current_edge=current_edge,
        recommended_action="SELL",
    )


def calculate_target_price(
    entry_price: float,
    min_profit: float = 0.15,
    spread_buffer: float = 0.05,
) -> float:
    """
    Рассчитать целевую цену bid для продажи.

    Args:
        entry_price: Цена покупки
        min_profit: Минимальная прибыль (default 15%)
        spread_buffer: Буфер на спред (default 5%)

    Returns:
        Целевая цена bid
    """
    return entry_price * (1 + min_profit + spread_buffer)


def should_rebalance_to_better_opportunity(
    current_position: Position,
    current_bid: float,
    current_edge: float,
    new_opportunity_edge: float,
    new_opportunity_apy: float,
    min_edge_improvement: float = 0.05,
) -> Tuple[bool, str]:
    """
    Проверить стоит ли продать текущую позицию ради лучшей возможности.

    Args:
        current_position: Текущая позиция
        current_bid: Цена продажи текущей позиции
        current_edge: Edge текущей позиции
        new_opportunity_edge: Edge новой возможности
        new_opportunity_apy: APY новой возможности
        min_edge_improvement: Минимальное улучшение edge (default 5%)

    Returns:
        (should_rebalance, reason)
    """
    edge_improvement = new_opportunity_edge - current_edge

    if edge_improvement < min_edge_improvement:
        return False, f"Улучшение edge ({edge_improvement:.1%}) недостаточно"

    # Учитываем стоимость выхода (примерно половина спреда)
    exit_cost = 0.05  # ~5% на выход

    net_improvement = edge_improvement - exit_cost

    if net_improvement <= 0:
        return False, f"После учёта спреда ({exit_cost:.0%}) нет улучшения"

    return True, f"Ребаланс выгоден: +{net_improvement:.1%} net edge, новый APY {new_opportunity_apy:.0%}"
```

---

## Сценарии выхода

### Сценарий 1: Рынок вырос, edge исчез

```
Покупка: YES '8-10' @ 12¢ (модель 16.8%, edge +4.8%)
Через 3 месяца: рынок вырос до 16¢
Текущий bid: 15¢
Модель: 16.8%
Edge: 16.8% - 15% = 1.8% (< 2%)
Profit: (15¢ - 12¢) / 12¢ = +25%

→ SELL: Edge исчез, прибыль 25% > 15%
```

### Сценарий 2: Рынок вырос, но edge остался

```
Покупка: YES '8-10' @ 12¢ (модель 16.8%, edge +4.8%)
Через 2 месяца: рынок вырос до 14¢
Текущий bid: 13¢
Модель пересчитана: 18% (из-за 2 землетрясений)
Edge: 18% - 13% = 5%
Profit: (13¢ - 12¢) / 12¢ = +8.3%

→ HOLD: Edge всё ещё 5%, продолжаем держать
```

### Сценарий 3: Рынок не вырос, edge сохранился

```
Покупка: YES '8-10' @ 12¢ (модель 16.8%, edge +4.8%)
Через 6 месяцев: рынок на том же уровне
Текущий bid: 10¢
Модель: 15% (скорректирована вниз)
Edge: 15% - 10% = 5%
Profit: (10¢ - 12¢) / 12¢ = -16.7%

→ HOLD: Edge есть, ждём резолюции или роста рынка
```

### Сценарий 4: Мало времени до резолюции

```
Покупка: YES '8-10' @ 12¢
До резолюции: 20 дней
Текущий bid: 14¢
Profit: +16.7%

→ HOLD_TO_EXPIRY: Слишком мало времени, hold выгоднее
```

---

## Мониторинг позиций

### Рекомендуемая частота проверки

| Время до резолюции | Частота проверки |
|-------------------|------------------|
| > 180 дней | 1 раз в неделю |
| 30-180 дней | 2-3 раза в неделю |
| 7-30 дней | Ежедневно |
| < 7 дней | Hold to expiry |

### Триггеры для внеплановой проверки

1. **Произошло землетрясение M7.0+** — цены могут резко измениться
2. **Крупное движение рынка (>5%)** — проверить edge
3. **Появилась новая возможность с высоким APY** — рассмотреть ребаланс

---

## Практические рекомендации

### Когда активная торговля выгодна

- Спред < 5%
- Время до резолюции > 60 дней
- Есть другие возможности для капитала

### Когда лучше hold to expiry

- Спред > 10%
- Время до резолюции < 30 дней
- Нет лучших альтернатив
- Высокая уверенность в модели

### Размер позиции для активной торговли

При активной торговле учитывай:
```
Effective position size = nominal_size × (1 - expected_exit_cost)
Expected exit cost = spread / 2 ≈ 5-10%
```

---

## Интеграция с ботом (TODO)

Для реализации активной торговли нужно добавить:

1. **База позиций** — хранение открытых позиций
2. **Мониторинг цен** — периодическая проверка bid/ask
3. **Пересчёт модели** — обновление fair price при новых данных
4. **Сигналы на выход** — уведомления когда условия выполнены
5. **Автоматическая продажа** — опционально

---

*Последнее обновление: 2025-01*
