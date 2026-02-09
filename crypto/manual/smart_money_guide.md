# Smart Money Analyzer — Логика и параметры

## Что это

Скрипт `crypto/smart_money.py` анализирует крупнейших холдеров любого рынка на Polymarket и оценивает, на какой стороне (YES/NO) стоят исторически прибыльные трейдеры.

## Использование

```bash
# Анализ всего события
python crypto/smart_money.py what-price-will-bitcoin-hit-before-2027

# Фильтр по конкретному рынку
python crypto/smart_money.py what-price-will-bitcoin-hit-before-2027 --market "120,000"

# URL тоже работает
python crypto/smart_money.py https://polymarket.com/event/bitcoin-best-month-in-2026

# Параметры
--market, -m    Фильтр по подстроке в названии рынка
--top, -t       Количество холдеров на сторону (по умолчанию 30)
--no-cache      Игнорировать кеш (перезагрузить все данные)
```

## Алгоритм

### 1. Сбор данных

```
Slug/URL → Gamma API (события + рынки)
        → Data API /holders (топ холдеры YES и NO)
        → Data API /positions + /closed-positions (статистика каждого трейдера)
```

### 2. Расчёт веса трейдера (conviction-weighted)

Для каждого холдера рассчитывается **вес**:

```
weight = total_profit × conviction × shrinkage × profile_bonus
```

Где:

| Компонент | Формула | Что измеряет |
|-----------|---------|-------------|
| **total_profit** | realized_pnl + unrealized_pnl ($) | Скилл: сколько человек заработал за всё время |
| **conviction** | position_value / portfolio_value | Уверенность: какую долю портфеля вложил в эту ставку |
| **shrinkage** | n_trades / (n_trades + 30) | Доверие: фильтрует лакеров с малым числом сделок |
| **profile_bonus** | 1.0 + crypto_share × 0.5 | Профильность: +50% бонус для crypto-трейдеров на crypto-рынках |

### 3. Агрегация

**Smart Money Flow** — от -1 (все умные на NO) до +1 (все на YES):

```
flow = Σ(weight × side) / Σ(|weight|)
```

где side = +1 для YES, -1 для NO.

**Smart Implied** — подразумеваемая вероятность YES:

```
implied = yes_weight / (yes_weight + no_weight)
```

### 4. Интерпретация

| Flow | Сигнал |
|------|--------|
| > +0.3 | STRONG YES |
| +0.1 ... +0.3 | YES |
| -0.1 ... +0.1 | NEUTRAL |
| -0.3 ... -0.1 | NO |
| < -0.3 | STRONG NO |

**Edge = Smart Implied - PM Price**

Если edge > 0 → smart money считает YES недооценённым.
Если edge < 0 → smart money считает YES переоценённым (ставить NO).

## Параметры модели

| Параметр | Значение | Описание |
|----------|----------|----------|
| HOLDER_LIMIT | 30 | Топ холдеров на каждую сторону (YES/NO) |
| SHRINKAGE_K | 30 | Байесовский порог: при 30 сделках shrinkage = 0.5 |
| CACHE_TTL | 3600 сек | Время жизни кеша трейдеров (1 час) |
| profile_bonus | ×1.5 max | Бонус для профильных трейдеров (>30% crypto) |

## Категоризация трейдеров

Скрипт определяет профиль трейдера по названиям его рынков:

| Ключевые слова | Категория |
|---------------|-----------|
| bitcoin, btc, ethereum, crypto | crypto |
| trump, biden, election | politics |
| earthquake, weather, climate | science |
| nfl, nba, super bowl | sports |
| fed, inflation, gdp | economics |

**crypto_share** = доля crypto-рынков в портфеле трейдера. На crypto-рынках трейдеры с высоким crypto_share получают бонус к весу.

## Ограничения

1. **Размер выборки** — для маленьких рынков мало холдеров, сигнал шумный
2. **Past performance** — прибыльность в прошлом не гарантирует будущего
3. **Один доминирующий трейдер** может исказить результат
4. **API лимиты** — при анализе всего события (~30 рынков × 60 трейдеров) запросы идут ~10 минут
5. **Нет данных о времени входа** — не знаем когда трейдер купил (мог войти по другой цене)

## Пример результата

```
СВОДНАЯ ТАБЛИЦА — Smart Money Flow
Рынок                    PM Price   Smart $    Edge    Flow       Signal
↓ 55,000                     74%      24% -50.3%  -0.19           NO
↑ 150,000                    10%      53% +42.0%  +0.04            —
↑ 120,000                    26%      54% +27.6%  +0.06            —
↑ 100,000                    50%      18% -31.7%  -0.58    STRONG NO
```

## Кеширование

Статистика трейдеров кешируется в `/tmp/pm_trader_cache.json` на 1 час. При повторном запуске используются кешированные данные (ускоряет в ~10 раз). Для свежих данных: `--no-cache`.
