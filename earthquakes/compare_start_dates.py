#!/usr/bin/env python3
"""
Сравнение прибыльности стратегии при разных датах начала торгов.

Вопрос: насколько менее прибыльна стратегия если начать с середины месяца?
"""

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dataclasses import dataclass
from typing import List, Dict, Optional

# Импортируем из основного модуля
from backtest_edge_strategy import (
    StrategyConfig,
    BacktestEngine,
    load_trades,
)


@dataclass
class StartDateResult:
    """Результат для конкретной даты старта."""
    start_date: datetime
    days_in_market: int
    entries: int
    exits: int
    at_resolution: int
    invested: float
    pnl: float
    roi: float
    win_rate: float


def run_backtest_from_date(
    trades: List[Dict],
    start_date: datetime,
    market_end: datetime,
    final_outcome: str,
    config: StrategyConfig,
) -> StartDateResult:
    """Запустить бэктест начиная с определённой даты."""

    # Фильтруем сделки — только после start_date
    filtered_trades = []
    for t in trades:
        time_str = t.get("time", "")
        try:
            ts = time_str.replace(" UTC", "").replace(" ", "T")
            if "+" not in ts and "Z" not in ts:
                ts += "+00:00"
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if dt >= start_date:
                filtered_trades.append(t)
        except:
            continue

    if not filtered_trades:
        return StartDateResult(
            start_date=start_date,
            days_in_market=0,
            entries=0, exits=0, at_resolution=0,
            invested=0, pnl=0, roi=0, win_rate=0,
        )

    # Запускаем бэктест
    engine = BacktestEngine(config)
    result = engine.run(filtered_trades, market_end, final_outcome, verbose=False)

    days_in_market = (market_end - start_date).days

    return StartDateResult(
        start_date=start_date,
        days_in_market=days_in_market,
        entries=result.total_entries,
        exits=result.total_exits,
        at_resolution=result.positions_at_resolution,
        invested=result.total_invested,
        pnl=result.total_pnl,
        roi=result.roi,
        win_rate=result.win_rate,
    )


def main():
    # Параметры
    data_path = Path("history/trades/megaquake_january_dune.json")
    market_end = datetime(2025, 1, 31, 23, 59, 59, tzinfo=timezone.utc)
    final_outcome = "NO"

    # Конфиг стратегии (как в основном бэктесте)
    config = StrategyConfig(
        min_edge=0.04,
        min_roi=0.15,
        sell_discount=0.02,
        base_monthly_prob=0.093,
    )

    # Загружаем данные
    if not data_path.exists():
        print(f"ERROR: Файл не найден: {data_path}")
        return

    trades = load_trades(data_path)
    if not trades:
        print("ERROR: Нет данных")
        return

    print("=" * 80)
    print("СРАВНЕНИЕ ПРИБЫЛЬНОСТИ ПРИ РАЗНЫХ ДАТАХ СТАРТА")
    print("=" * 80)
    print(f"\nРынок: megaquake-in-january")
    print(f"Окончание: {market_end.date()}")
    print(f"Итог: {final_outcome}")
    print(f"\nПараметры: min_edge={config.min_edge:.0%}, min_roi={config.min_roi:.0%}, sell_discount={config.sell_discount:.0%}")
    print()

    # Тестируем разные даты старта
    start_dates = [
        datetime(2024, 12, 30, 0, 0, 0, tzinfo=timezone.utc),  # Начало данных
        datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc),    # 1 января
        datetime(2025, 1, 5, 0, 0, 0, tzinfo=timezone.utc),    # 5 января
        datetime(2025, 1, 10, 0, 0, 0, tzinfo=timezone.utc),   # 10 января
        datetime(2025, 1, 15, 0, 0, 0, tzinfo=timezone.utc),   # 15 января (середина)
        datetime(2025, 1, 20, 0, 0, 0, tzinfo=timezone.utc),   # 20 января
        datetime(2025, 1, 25, 0, 0, 0, tzinfo=timezone.utc),   # 25 января
    ]

    results: List[StartDateResult] = []

    for start_date in start_dates:
        res = run_backtest_from_date(trades, start_date, market_end, final_outcome, config)
        results.append(res)

    # Выводим таблицу результатов
    print("-" * 80)
    print(f"{'Старт':<12} {'Дней':<6} {'Входов':<8} {'Выходов':<8} {'Резол.':<7} {'Инвест.':<10} {'P&L':<10} {'ROI':<8} {'Win%':<6}")
    print("-" * 80)

    baseline_roi = results[0].roi if results else 0

    for r in results:
        roi_diff = ""
        if r.roi > 0 and baseline_roi > 0 and r != results[0]:
            diff_pct = ((r.roi / baseline_roi) - 1) * 100
            roi_diff = f" ({diff_pct:+.0f}%)" if diff_pct != 0 else ""

        print(f"{r.start_date.strftime('%Y-%m-%d'):<12} "
              f"{r.days_in_market:<6} "
              f"{r.entries:<8} "
              f"{r.exits:<8} "
              f"{r.at_resolution:<7} "
              f"${r.invested:<9.2f} "
              f"${r.pnl:<9.2f} "
              f"{r.roi:.1%}{roi_diff:<8} "
              f"{r.win_rate:.0%}")

    print("-" * 80)

    # Анализ
    print("\n" + "=" * 80)
    print("АНАЛИЗ")
    print("=" * 80)

    if len(results) >= 2:
        early = results[0]

        # Находим последнюю дату с входами
        last_with_entries = None
        for r in results:
            if r.entries > 0:
                last_with_entries = r

        # Находим первую дату без входов
        first_no_entries = None
        for r in results:
            if r.entries == 0:
                first_no_entries = r
                break

        print(f"\nСтарт с начала ({early.start_date.date()}):")
        print(f"  - Входов: {early.entries}, P&L: ${early.pnl:.2f}, ROI: {early.roi:.1%}")

        if last_with_entries and last_with_entries != early:
            print(f"\nПоследняя дата с входами ({last_with_entries.start_date.date()}):")
            print(f"  - Входов: {last_with_entries.entries}, P&L: ${last_with_entries.pnl:.2f}, ROI: {last_with_entries.roi:.1%}")

        if first_no_entries:
            print(f"\nС {first_no_entries.start_date.date()} и позже: НЕТ ВХОДОВ!")
            print(f"  - Рынок уже не даёт edge >= {config.min_edge:.0%}")

        # Окно возможностей
        if last_with_entries:
            window_days = (last_with_entries.start_date - early.start_date).days + 5  # примерно
            print(f"\n>>> ОКНО ВОЗМОЖНОСТЕЙ: первые ~{window_days} дней месяца <<<")

    # Почему так происходит
    print("\n" + "-" * 80)
    print("ПРИЧИНА:")
    print("-" * 80)
    print("""
Стратегия edge-based покупает когда market_price << fair_price.
- В начале месяца fair_price высокий (много времени до конца)
- К середине/концу fair_price падает (мало времени)
- Рынок обычно торгуется близко к fair_price
- Поэтому edge (разница) больше в начале месяца

Чем позже старт — тем меньше возможностей для входа с хорошим edge.
""")


if __name__ == "__main__":
    main()
