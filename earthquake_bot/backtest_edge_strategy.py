#!/usr/bin/env python3
"""
Бэктест Edge-Based Trading Strategy для Polymarket earthquake markets.

Стратегия:
1. ВХОД: Покупать YES когда edge >= MIN_EDGE и expected_roi >= MIN_ROI
2. ВЫХОД: Продавать когда рыночная цена >= fair_price - SELL_DISCOUNT
3. ЗАЩИТА: Никогда не продавать ниже цены покупки
4. РЕЗОЛЮЦИЯ: Оставшиеся позиции закрываются по итогу рынка

Использование:
    python backtest_edge_strategy.py
    python backtest_edge_strategy.py --min-edge 0.03 --min-roi 0.15
"""

import json
import argparse
import base64
import io
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from pathlib import Path

# Опциональный импорт matplotlib
try:
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False


# =============================================================================
# КОНФИГУРАЦИЯ
# =============================================================================

@dataclass
class StrategyConfig:
    """Параметры стратегии."""
    min_edge: float = 0.05           # Минимальный edge для входа (5%)
    min_roi: float = 0.20            # Минимальный expected ROI (20%)
    min_apy: float = 0.0             # Минимальный APY (0 = не проверять)
    position_size: float = 1.0       # Размер позиции ($)
    sell_discount: float = 0.05      # Продавать на X% ниже fair price
    max_positions: int = 100         # Максимум открытых позиций

    # Модель вероятности
    base_monthly_prob: float = 0.10  # Базовая P(M8.0+) в месяц


@dataclass
class Position:
    """Открытая позиция."""
    entry_time: str
    entry_price: float
    size_usd: float
    tokens: float
    outcome: str  # "YES"

    def pnl_at_price(self, price: float) -> float:
        """P&L при заданной цене."""
        return self.tokens * (price - self.entry_price)

    def pnl_at_resolution(self, won: bool) -> float:
        """P&L при резолюции."""
        if won:
            return self.tokens * (1.0 - self.entry_price)
        else:
            return -self.size_usd


@dataclass
class TradeLog:
    """Запись о сделке."""
    time: str
    action: str  # "ENTRY", "EXIT", "RESOLUTION"
    price: float
    fair_price: float
    edge: float
    roi: float
    pnl: float = 0.0
    details: str = ""


@dataclass
class BacktestResult:
    """Результат бэктеста."""
    config: StrategyConfig
    trades: List[TradeLog] = field(default_factory=list)

    # Метрики
    total_entries: int = 0
    total_exits: int = 0
    positions_at_resolution: int = 0

    total_invested: float = 0.0
    total_pnl: float = 0.0

    winning_trades: int = 0
    losing_trades: int = 0

    def add_entry(self, log: TradeLog):
        self.trades.append(log)
        self.total_entries += 1
        self.total_invested += self.config.position_size

    def add_exit(self, log: TradeLog):
        self.trades.append(log)
        self.total_exits += 1
        self.total_pnl += log.pnl
        if log.pnl >= 0:
            self.winning_trades += 1
        else:
            self.losing_trades += 1

    def add_resolution(self, log: TradeLog):
        self.trades.append(log)
        self.positions_at_resolution += 1
        self.total_pnl += log.pnl
        if log.pnl >= 0:
            self.winning_trades += 1
        else:
            self.losing_trades += 1

    @property
    def win_rate(self) -> float:
        total = self.winning_trades + self.losing_trades
        return self.winning_trades / total if total > 0 else 0

    @property
    def roi(self) -> float:
        return self.total_pnl / self.total_invested if self.total_invested > 0 else 0

    def summary(self) -> str:
        apy_str = f"{self.config.min_apy:.0%}" if self.config.min_apy > 0 else "off"
        return f"""
{'='*60}
РЕЗУЛЬТАТЫ БЭКТЕСТА
{'='*60}

Параметры стратегии:
  Min edge:        {self.config.min_edge:.1%}
  Min ROI:         {self.config.min_roi:.1%}
  Min APY:         {apy_str}
  Position size:   ${self.config.position_size:.2f}
  Sell discount:   {self.config.sell_discount:.1%}
  Base prob:       {self.config.base_monthly_prob:.1%}/месяц

Результаты:
  Входов:          {self.total_entries}
  Выходов:         {self.total_exits}
  На резолюции:    {self.positions_at_resolution}

  Выигрышных:      {self.winning_trades}
  Проигрышных:     {self.losing_trades}
  Win rate:        {self.win_rate:.1%}

  Инвестировано:   ${self.total_invested:.2f}
  P&L:             ${self.total_pnl:.2f}
  ROI:             {self.roi:.1%}
{'='*60}
"""


# =============================================================================
# МОДЕЛЬ FAIR PRICE
# =============================================================================

def calculate_fair_price_yes(days_remaining: float, base_monthly_prob: float) -> float:
    """
    Рассчитать справедливую цену YES токена.

    P(at least one M8.0+ in T days) = 1 - (1 - monthly_prob)^(T/30)
    """
    if days_remaining <= 0:
        return 0.0
    months = days_remaining / 30.0
    prob_no_event = (1 - base_monthly_prob) ** months
    return 1 - prob_no_event


def calculate_edge(market_price: float, fair_price: float) -> float:
    """Edge = fair_price - market_price."""
    return fair_price - market_price


def calculate_expected_roi(entry_price: float, fair_price: float) -> float:
    """
    Ожидаемый ROI для YES.

    E[profit] = (1 - entry) * P(yes) - entry * P(no)
    ROI = E[profit] / entry
    """
    if entry_price <= 0 or entry_price >= 1:
        return 0
    expected_profit = (1 - entry_price) * fair_price - entry_price * (1 - fair_price)
    return expected_profit / entry_price


def calculate_apy(expected_roi: float, days_remaining: float) -> float:
    """
    Годовая доходность (APY).

    APY = ROI * (365 / days_remaining)
    """
    if days_remaining <= 0:
        return 0
    return expected_roi * (365 / days_remaining)


# =============================================================================
# ЗАГРУЗКА ДАННЫХ
# =============================================================================

def load_trades(filepath: Path) -> List[Dict]:
    """Загрузить и нормализовать сделки."""
    with open(filepath) as f:
        data = json.load(f)

    trades = data.get("trades", [])
    normalized = []

    for t in trades:
        raw_price = t.get("price", 0)
        outcome = t.get("outcome", "")

        # Нормализуем цену для YES (инвертируем если > 1)
        if outcome == "YES" and raw_price > 1:
            price = 1.0 / raw_price
        elif outcome == "NO" and raw_price > 1:
            # NO цена ~1.05 означает реальную цену ~0.95
            price = 1.0 / raw_price
        else:
            price = raw_price

        # Пропускаем невалидные
        if not (0 < price < 1) or outcome not in ["YES", "NO"]:
            continue

        normalized.append({
            "time": t.get("block_time", ""),
            "price": price,
            "outcome": outcome,
            "tokens": t.get("maker_tokens", 0),
            "usd": t.get("taker_amount", 0),
            "tx": t.get("tx_hash", ""),
        })

    return sorted(normalized, key=lambda x: x["time"])


# =============================================================================
# БЭКТЕСТ
# =============================================================================

class BacktestEngine:
    """Движок бэктеста."""

    def __init__(self, config: StrategyConfig):
        self.config = config
        self.positions: List[Position] = []
        self.result = BacktestResult(config=config)

    def run(
        self,
        trades: List[Dict],
        market_end: datetime,
        final_outcome: str = "NO",
        verbose: bool = True,
    ) -> BacktestResult:
        """Запустить бэктест."""

        if verbose:
            print(f"\n{'='*70}")
            print("ЗАПУСК БЭКТЕСТА")
            print(f"{'='*70}")
            print(f"Сделок: {len(trades)}")
            print(f"Период: {trades[0]['time'][:10]} - {trades[-1]['time'][:10]}")
            print(f"Итог рынка: {final_outcome}")
            print()

        # Обрабатываем каждую сделку
        for trade in trades:
            self._process_trade(trade, market_end, verbose)

        # Резолюция
        self._resolve_market(final_outcome, market_end, verbose)

        return self.result

    def _parse_time(self, time_str: str) -> Optional[datetime]:
        """Парсить время."""
        try:
            ts = time_str.replace(" UTC", "").replace(" ", "T")
            if "+" not in ts and "Z" not in ts:
                ts += "+00:00"
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except:
            return None

    def _process_trade(self, trade: Dict, market_end: datetime, verbose: bool):
        """Обработать одну сделку."""

        trade_time = self._parse_time(trade["time"])
        if not trade_time:
            return

        price = trade["price"]
        outcome = trade["outcome"]

        # Рассчитываем fair price
        days_remaining = (market_end - trade_time).total_seconds() / 86400
        fair_price = calculate_fair_price_yes(days_remaining, self.config.base_monthly_prob)

        # === ПРОВЕРКА ВХОДА (только YES) ===
        if outcome == "YES" and len(self.positions) < self.config.max_positions:
            edge = calculate_edge(price, fair_price)
            roi = calculate_expected_roi(price, fair_price)
            apy = calculate_apy(roi, days_remaining)

            # Проверяем все условия входа
            entry_conditions = (
                edge >= self.config.min_edge and
                roi >= self.config.min_roi and
                (self.config.min_apy <= 0 or apy >= self.config.min_apy)
            )

            if entry_conditions:
                tokens = self.config.position_size / price

                pos = Position(
                    entry_time=trade["time"],
                    entry_price=price,
                    size_usd=self.config.position_size,
                    tokens=tokens,
                    outcome="YES",
                )
                self.positions.append(pos)

                log = TradeLog(
                    time=trade["time"],
                    action="ENTRY",
                    price=price,
                    fair_price=fair_price,
                    edge=edge,
                    roi=roi,
                    details=f"days_left={days_remaining:.0f}, apy={apy:.0%}",
                )
                self.result.add_entry(log)

                if verbose:
                    print(f"ENTRY: {trade['time'][:16]} | YES @ {price:.4f} | "
                          f"fair={fair_price:.4f} | edge={edge:.1%} | apy={apy:.0%}")

        # === ПРОВЕРКА ВЫХОДА ===
        # sell_discount как процент от fair_price (5% = продать на 5% дешевле fair)
        target_price = fair_price * (1 - self.config.sell_discount)

        closed_indices = []
        for i, pos in enumerate(self.positions):
            if outcome == "YES":
                # Условия выхода
                if price >= target_price and price >= pos.entry_price:
                    pnl = pos.pnl_at_price(price)

                    log = TradeLog(
                        time=trade["time"],
                        action="EXIT",
                        price=price,
                        fair_price=fair_price,
                        edge=calculate_edge(price, fair_price),
                        roi=(price - pos.entry_price) / pos.entry_price,
                        pnl=pnl,
                        details=f"entry={pos.entry_price:.4f}",
                    )
                    self.result.add_exit(log)
                    closed_indices.append(i)

                    if verbose:
                        print(f"EXIT:  {trade['time'][:16]} | YES @ {price:.4f} | "
                              f"entry={pos.entry_price:.4f} | P&L=${pnl:.2f}")

        # Удаляем закрытые
        for i in reversed(closed_indices):
            self.positions.pop(i)

    def _resolve_market(self, final_outcome: str, end_time: datetime, verbose: bool):
        """Резолюция рынка."""

        if verbose:
            print(f"\n{'='*70}")
            print(f"РЕЗОЛЮЦИЯ: {final_outcome}")
            print(f"{'='*70}")

        for pos in self.positions:
            won = (pos.outcome == final_outcome)
            pnl = pos.pnl_at_resolution(won)

            log = TradeLog(
                time=end_time.isoformat(),
                action="RESOLUTION",
                price=1.0 if won else 0.0,
                fair_price=0.0,
                edge=0.0,
                roi=pnl / pos.size_usd,
                pnl=pnl,
                details=f"entry={pos.entry_price:.4f}, {'WON' if won else 'LOST'}",
            )
            self.result.add_resolution(log)

            if verbose:
                status = "WON" if won else "LOST"
                print(f"  {status}: entry={pos.entry_price:.4f} | P&L=${pnl:.2f}")

        self.positions = []


# =============================================================================
# MARKDOWN REPORT
# =============================================================================

def generate_markdown_report(
    config: StrategyConfig,
    result: BacktestResult,
    trades_data: List[Dict],
    final_outcome: str,
    data_path: str = "",
    market_name: str = "",
    chart_base64: str = "",
) -> str:
    """Генерирует MD-отчёт о бэктесте."""

    # Период данных
    if trades_data:
        start_date = trades_data[0]["time"][:10]
        end_date = trades_data[-1]["time"][:10]
    else:
        start_date = end_date = "N/A"

    # Статистика по сделкам
    entries = [t for t in result.trades if t.action == "ENTRY"]
    exits = [t for t in result.trades if t.action == "EXIT"]
    resolutions = [t for t in result.trades if t.action == "RESOLUTION"]

    # Средние значения
    avg_entry_price = sum(t.price for t in entries) / len(entries) if entries else 0
    avg_edge = sum(t.edge for t in entries) / len(entries) if entries else 0
    avg_pnl = sum(t.pnl for t in exits) / len(exits) if exits else 0

    # APY (если есть в details)
    apy_values = []
    for t in entries:
        if "apy=" in t.details:
            try:
                apy_str = t.details.split("apy=")[1].split(",")[0].replace("%", "")
                apy_values.append(float(apy_str) / 100)
            except:
                pass
    avg_apy = sum(apy_values) / len(apy_values) if apy_values else 0

    # Формируем APY строку для конфига
    apy_config_str = f"{config.min_apy:.0%}" if config.min_apy > 0 else "off"

    # Определяем название рынка из пути если не задано
    if not market_name and data_path:
        if "january" in data_path.lower():
            market_name = "megaquake-in-january"
        elif "february" in data_path.lower():
            market_name = "megaquake-in-february"
        else:
            market_name = Path(data_path).stem

    md = f"""# Backtest Report: Edge Strategy

**Дата отчёта:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

---

## Описание

### Источник данных
- **Файл:** `{data_path}`
- **Рынок:** {market_name}
- **Источник:** Dune Analytics (Polymarket CTFExchange OrderFilled events)

### Используемая модель
- **Тип:** Упрощённая модель (Simple Poisson)
- **НЕ используется:** IntegratedModel, TestedModel, ConsensusModel из main_*.py
- **Формула fair price:** `P(YES) = 1 - (1 - base_monthly_prob)^(days/30)`
- **Базовая вероятность:** {config.base_monthly_prob:.1%}/месяц (≈ {config.base_monthly_prob * 12:.2f} событий/год)
- **Примечание:** Для точного бэктеста нужно интегрировать IntegratedModel

### Стратегия
1. **ВХОД:** Покупать YES когда `edge >= {config.min_edge:.1%}` и `ROI >= {config.min_roi:.1%}`
2. **ВЫХОД:** Продавать когда `price >= fair_price * (1 - {config.sell_discount:.1%})`
3. **ЗАЩИТА:** Никогда не продавать ниже цены покупки
4. **РЕЗОЛЮЦИЯ:** Оставшиеся позиции закрываются по итогу рынка

---

## Параметры стратегии

| Параметр | Значение |
|----------|----------|
| Min Edge | {config.min_edge:.1%} |
| Min ROI | {config.min_roi:.1%} |
| Min APY | {apy_config_str} |
| Position Size | ${config.position_size:.2f} |
| Sell Discount | {config.sell_discount:.1%} |
| Base Prob | {config.base_monthly_prob:.1%}/месяц |

---

## Данные

| Параметр | Значение |
|----------|----------|
| Период | {start_date} — {end_date} |
| Всего сделок в данных | {len(trades_data)} |
| Итог рынка | **{final_outcome}** |

---

## Результаты

| Метрика | Значение |
|---------|----------|
| Входов | {result.total_entries} |
| Выходов | {result.total_exits} |
| На резолюции | {result.positions_at_resolution} |
| Выигрышных | {result.winning_trades} |
| Проигрышных | {result.losing_trades} |
| **Win Rate** | **{result.win_rate:.1%}** |
| Инвестировано | ${result.total_invested:.2f} |
| **P&L** | **${result.total_pnl:.2f}** |
| **ROI** | **{result.roi:.1%}** |

---

## Статистика сделок

| Метрика | Значение |
|---------|----------|
| Средняя цена входа | {avg_entry_price:.2%} |
| Средний edge | {avg_edge:.1%} |
| Средний APY при входе | {avg_apy:.0%} |
| Средний P&L на выход | ${avg_pnl:.2f} |

---

## Полный лог сделок

| # | Вход (дата) | Выход (дата) | Удержание | Тип | Цена входа | Fair Price | Edge | APY | Цена выхода | P&L | Итого | ROI | Статус |
|---|-------------|--------------|-----------|-----|------------|------------|------|-----|-------------|-----|------|-----|--------|
"""

    # Функция для парсинга времени
    def parse_time(time_str: str) -> Optional[datetime]:
        try:
            ts = time_str.replace(" UTC", "").replace(" ", "T")
            if "+" not in ts and "Z" not in ts:
                ts += "+00:00"
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except:
            return None

    # Сопоставляем входы с выходами
    # Группируем по цене входа для matching
    entry_queue = []  # [(entry_trade, index)]
    trade_pairs = []  # [(entry, exit, trade_num)]

    trade_num = 0
    for t in result.trades:
        if t.action == "ENTRY":
            entry_queue.append((t, trade_num))
            trade_num += 1
        elif t.action in ("EXIT", "RESOLUTION"):
            # Ищем соответствующий вход по цене
            entry_price_str = ""
            if "entry=" in t.details:
                entry_price_str = t.details.split("entry=")[1].split(",")[0]

            matched = False
            for i, (entry, num) in enumerate(entry_queue):
                if f"{entry.price:.4f}" == entry_price_str or not entry_price_str:
                    trade_pairs.append((entry, t, num))
                    entry_queue.pop(i)
                    matched = True
                    break

            if not matched and entry_queue:
                # Берём первый из очереди
                entry, num = entry_queue.pop(0)
                trade_pairs.append((entry, t, num))

    # Сортируем по номеру сделки
    trade_pairs.sort(key=lambda x: x[2])

    cumulative_pnl = 0.0  # Накопительная прибыль

    for entry, exit_trade, num in trade_pairs:
        # Парсим APY из entry details
        apy_str = ""
        if "apy=" in entry.details:
            apy_str = entry.details.split("apy=")[1].split(",")[0]

        # Определяем статус
        if exit_trade.action == "EXIT":
            status = "Закрыта"
        else:
            status = "WON" if "WON" in exit_trade.details else "LOST"

        # Рассчитываем ROI сделки
        trade_roi = (exit_trade.price - entry.price) / entry.price if entry.price > 0 else 0

        # Накопительная прибыль
        cumulative_pnl += exit_trade.pnl

        # Рассчитываем время удержания
        entry_dt = parse_time(entry.time)
        exit_dt = parse_time(exit_trade.time)
        if entry_dt and exit_dt:
            hold_delta = exit_dt - entry_dt
            hold_days = hold_delta.days
            hold_hours = hold_delta.seconds // 3600
            if hold_days > 0:
                hold_str = f"{hold_days}д {hold_hours}ч"
            else:
                hold_str = f"{hold_hours}ч"
        else:
            hold_str = "?"

        md += f"| {num+1} | {entry.time[:16]} | {exit_trade.time[:16]} | {hold_str} | YES | {entry.price:.2%} | {entry.fair_price:.2%} | {entry.edge:.1%} | {apy_str} | {exit_trade.price:.2%} | ${exit_trade.pnl:.2f} | ${cumulative_pnl:.2f} | {trade_roi:.0%} | {status} |\n"

    md += f"""
---

## Выводы

"""
    if result.win_rate >= 0.95:
        md += "- Очень высокий win rate (>95%) — стратегия консервативна\n"
    if result.roi > 0.5:
        md += f"- Отличный ROI ({result.roi:.0%}) — стратегия прибыльна\n"
    if result.positions_at_resolution > 0:
        md += f"- {result.positions_at_resolution} позиций не закрылись до резолюции — возможно нужен более агрессивный выход\n"
    if result.positions_at_resolution == 0 and result.total_exits > 0:
        md += "- Все позиции закрыты до резолюции — хорошее управление рисками\n"

    # Добавляем график если есть (встроенный base64)
    if chart_base64:
        md += f"""
## График цен

![График цен YES](data:image/png;base64,{chart_base64})

"""

    md += f"""---

*Сгенерировано backtest_edge_strategy.py*
"""

    return md


def generate_price_chart(trades_data: List[Dict], title: str = "", candlestick: bool = True) -> str:
    """Генерирует график цен и возвращает как base64 строку.

    Args:
        candlestick: Если True, строит свечной график (OHLC по 6 часов)

    Returns:
        base64 строка изображения или пустая строка при ошибке
    """

    if not HAS_MATPLOTLIB:
        print("  Предупреждение: matplotlib не установлен, график не создан")
        return ""

    # Парсим данные
    yes_times = []
    yes_prices = []

    for t in trades_data:
        if t.get("outcome") != "YES":
            continue

        price = t.get("price", 0)
        if not (0 < price < 1):
            continue

        time_str = t.get("time", "")
        try:
            ts = time_str.replace(" UTC", "").replace(" ", "T")
            if "+" not in ts and "Z" not in ts:
                ts += "+00:00"
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            yes_times.append(dt)
            yes_prices.append(price)
        except:
            continue

    if not yes_times:
        return ""

    fig, ax = plt.subplots(figsize=(14, 6))

    if candlestick:
        # Агрегируем в OHLC свечи по 6 часов
        from collections import defaultdict

        candles = defaultdict(list)
        for dt, price in zip(yes_times, yes_prices):
            # Округляем до 6-часового интервала
            hour_bucket = (dt.hour // 6) * 6
            bucket_time = dt.replace(hour=hour_bucket, minute=0, second=0, microsecond=0)
            candles[bucket_time].append(price)

        # Сортируем по времени
        sorted_times = sorted(candles.keys())

        ohlc_data = []
        for t in sorted_times:
            prices = candles[t]
            ohlc_data.append({
                'time': t,
                'open': prices[0],
                'high': max(prices),
                'low': min(prices),
                'close': prices[-1],
            })

        # Рисуем свечи
        width = 0.15  # ширина свечи в днях

        for i, candle in enumerate(ohlc_data):
            t = mdates.date2num(candle['time'])
            o, h, l, c = candle['open'], candle['high'], candle['low'], candle['close']

            # Цвет: зелёный если close > open, красный если close < open
            if c >= o:
                color = 'green'
                body_bottom = o
                body_height = c - o
            else:
                color = 'red'
                body_bottom = c
                body_height = o - c

            # Тело свечи
            ax.bar(t, body_height, width=width, bottom=body_bottom, color=color, alpha=0.8, edgecolor='black', linewidth=0.5)

            # Тени (фитили)
            ax.plot([t, t], [l, body_bottom], color='black', linewidth=1)
            ax.plot([t, t], [body_bottom + body_height, h], color='black', linewidth=1)

        # Также добавляем линию закрытия для наглядности
        close_times = [c['time'] for c in ohlc_data]
        close_prices = [c['close'] for c in ohlc_data]
        ax.plot(close_times, close_prices, 'b-', linewidth=0.5, alpha=0.3)

    else:
        # Обычный линейный график
        ax.plot(yes_times, yes_prices, 'b-', linewidth=0.8, alpha=0.7)
        ax.scatter(yes_times, yes_prices, c='blue', s=5, alpha=0.5)

    ax.set_xlabel('Дата', fontsize=11)
    ax.set_ylabel('Цена YES', fontsize=11)
    ax.set_title(title or 'История цен YES токена', fontsize=13)

    ax.xaxis.set_major_formatter(mdates.DateFormatter('%d %b'))
    ax.xaxis.set_major_locator(mdates.DayLocator(interval=2))
    plt.xticks(rotation=45)

    ax.grid(True, alpha=0.3)
    ax.set_ylim(0, max(yes_prices) * 1.3)

    # Форматируем Y как проценты
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f'{x:.0%}'))

    # Статистика
    stats = f"min={min(yes_prices):.1%}  max={max(yes_prices):.1%}  avg={sum(yes_prices)/len(yes_prices):.1%}  trades={len(yes_prices)}"
    ax.text(0.02, 0.98, stats, transform=ax.transAxes, fontsize=9,
            verticalalignment='top', bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))

    plt.tight_layout()

    # Сохраняем в буфер как base64
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=150, bbox_inches='tight')
    buf.seek(0)
    img_base64 = base64.b64encode(buf.read()).decode('utf-8')
    buf.close()
    plt.close()

    return img_base64


# =============================================================================
# MAIN
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Бэктест Edge Strategy")
    parser.add_argument("--data", type=str, default="history/trades/megaquake_january_dune.json",
                        help="Путь к файлу с данными")
    parser.add_argument("--min-edge", type=float, default=0.05, help="Минимальный edge (default: 0.05)")
    parser.add_argument("--min-roi", type=float, default=0.20, help="Минимальный ROI (default: 0.20)")
    parser.add_argument("--min-apy", type=float, default=0.0, help="Минимальный APY (default: 0 = не проверять)")
    parser.add_argument("--position-size", type=float, default=1.0, help="Размер позиции (default: 1.0)")
    parser.add_argument("--sell-discount", type=float, default=0.05, help="Скидка при продаже (default: 0.05)")
    parser.add_argument("--base-prob", type=float, default=0.10, help="Базовая вероятность/месяц (default: 0.10)")
    parser.add_argument("--output", type=str, help="Сохранить результаты в файл")
    parser.add_argument("-q", "--quiet", action="store_true", help="Тихий режим")

    args = parser.parse_args()

    # Конфиг
    config = StrategyConfig(
        min_edge=args.min_edge,
        min_roi=args.min_roi,
        min_apy=args.min_apy,
        position_size=args.position_size,
        sell_discount=args.sell_discount,
        base_monthly_prob=args.base_prob,
    )

    # Загружаем данные
    data_path = Path(args.data)
    if not data_path.exists():
        print(f"ERROR: Файл не найден: {data_path}")
        return

    trades = load_trades(data_path)
    if not trades:
        print("ERROR: Нет валидных сделок")
        return

    # Параметры рынка (megaquake-in-january)
    market_end = datetime(2025, 1, 31, 23, 59, 59, tzinfo=timezone.utc)
    final_outcome = "NO"

    # Запускаем
    engine = BacktestEngine(config)
    result = engine.run(trades, market_end, final_outcome, verbose=not args.quiet)

    # Выводим результаты
    print(result.summary())

    # Сохраняем MD отчёт
    if args.output:
        md_path = Path(args.output)
        if md_path.suffix != ".md":
            md_path = md_path.with_suffix(".md")
    else:
        md_path = Path("output") / f"backtest_edge_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"

    md_path.parent.mkdir(exist_ok=True)

    # Генерируем график цен (base64)
    if "january" in args.data.lower():
        chart_title = "Megaquake in January — История цен YES"
    elif "february" in args.data.lower():
        chart_title = "Megaquake in February — История цен YES"
    else:
        chart_title = f"История цен YES — {data_path.stem}"

    chart_base64 = generate_price_chart(trades, chart_title)
    if chart_base64:
        print("График сгенерирован (встроен в MD)")

    md_content = generate_markdown_report(
        config, result, trades, final_outcome,
        data_path=str(data_path),
        chart_base64=chart_base64,
    )
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    print(f"Отчёт сохранён: {md_path}")


if __name__ == "__main__":
    main()
