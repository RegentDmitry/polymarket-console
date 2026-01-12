#!/usr/bin/env python3
"""
Бэктест стратегии по всем закрытым megaquake рынкам.
Генерирует MD отчёт с графиком для каждого месяца.
"""

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dataclasses import dataclass
from typing import List, Dict, Optional

from backtest_edge_strategy import (
    StrategyConfig,
    BacktestEngine,
    BacktestResult,
    generate_markdown_report,
    generate_price_chart,
)


# Параметры рынков
MARKETS = {
    "august": {
        "name": "Megaquake in August 2024",
        "start": datetime(2024, 8, 1, 0, 0, 0, tzinfo=timezone.utc),
        "end": datetime(2024, 8, 31, 23, 59, 59, tzinfo=timezone.utc),
        "outcome": "NO",
    },
    "september": {
        "name": "Megaquake in September 2024",
        "start": datetime(2024, 9, 1, 0, 0, 0, tzinfo=timezone.utc),
        "end": datetime(2024, 9, 30, 23, 59, 59, tzinfo=timezone.utc),
        "outcome": "NO",
    },
    "october": {
        "name": "Megaquake in October 2024",
        "start": datetime(2024, 10, 1, 0, 0, 0, tzinfo=timezone.utc),
        "end": datetime(2024, 10, 31, 23, 59, 59, tzinfo=timezone.utc),
        "outcome": "NO",
    },
    "november": {
        "name": "Megaquake in November 2024",
        "start": datetime(2024, 11, 1, 0, 0, 0, tzinfo=timezone.utc),
        "end": datetime(2024, 11, 30, 23, 59, 59, tzinfo=timezone.utc),
        "outcome": "NO",
    },
    "december": {
        "name": "Megaquake in December 2024",
        "start": datetime(2024, 12, 1, 0, 0, 0, tzinfo=timezone.utc),
        "end": datetime(2024, 12, 31, 23, 59, 59, tzinfo=timezone.utc),
        "outcome": "NO",
    },
    "january": {
        "name": "Megaquake in January 2025",
        "start": datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
        "end": datetime(2025, 1, 31, 23, 59, 59, tzinfo=timezone.utc),
        "outcome": "NO",
    },
    "february": {
        "name": "Megaquake in February 2025",
        "start": datetime(2025, 2, 1, 0, 0, 0, tzinfo=timezone.utc),
        "end": datetime(2025, 2, 28, 23, 59, 59, tzinfo=timezone.utc),
        "outcome": "NO",
    },
}


def load_all_trades(filepath: Path) -> Dict[str, List[Dict]]:
    """Загрузить и разбить сделки по месяцам."""
    with open(filepath) as f:
        data = json.load(f)

    trades_by_market = {}

    for t in data.get("trades", []):
        market = t.get("market")
        if not market:
            continue

        if market not in trades_by_market:
            trades_by_market[market] = []

        # Нормализуем данные
        raw_price = t.get("price", 0)
        outcome = t.get("outcome", "")

        # Цена должна быть 0-1
        if raw_price > 1:
            price = 1.0 / raw_price
        else:
            price = raw_price

        if not (0 < price < 1):
            continue

        trades_by_market[market].append({
            "time": t.get("block_time", ""),
            "price": price,
            "outcome": outcome,
            "tokens": t.get("maker_tokens", 0),
            "usd": t.get("taker_amount", 0),
        })

    # Сортируем по времени
    for market in trades_by_market:
        trades_by_market[market].sort(key=lambda x: x["time"])

    return trades_by_market


def parse_time(time_str: str) -> Optional[datetime]:
    """Парсить время."""
    try:
        ts = time_str.replace(" UTC", "").replace(" ", "T")
        if "+" not in ts and "Z" not in ts:
            ts += "+00:00"
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except:
        return None


def run_backtest_filtered(
    trades: List[Dict],
    market_end: datetime,
    final_outcome: str,
    config: StrategyConfig,
    start_from: datetime = None,
) -> dict:
    """Запустить бэктест с фильтрацией по дате."""

    # Фильтруем по дате старта
    if start_from:
        filtered = []
        for t in trades:
            dt = parse_time(t["time"])
            if dt and dt >= start_from:
                filtered.append(t)
        trades = filtered

    if not trades:
        return {"entries": 0, "pnl": 0, "roi": 0, "invested": 0}

    engine = BacktestEngine(config)
    result = engine.run(trades, market_end, final_outcome, verbose=False)

    return {
        "entries": result.total_entries,
        "pnl": result.total_pnl,
        "roi": result.roi,
        "invested": result.total_invested,
    }


def run_backtest_and_save(
    trades: List[Dict],
    market_key: str,
    market_info: dict,
    config: StrategyConfig,
    output_dir: Path,
):
    """Запустить бэктест и сохранить MD отчёт."""

    market_end = market_info["end"]
    final_outcome = market_info["outcome"]

    # Запускаем бэктест
    engine = BacktestEngine(config)
    result = engine.run(trades, market_end, final_outcome, verbose=False)

    # Генерируем график
    chart_title = f"{market_info['name']} — История цен YES"
    chart_base64 = generate_price_chart(trades, chart_title)

    # Генерируем MD отчёт
    md_content = generate_markdown_report(
        config=config,
        result=result,
        trades_data=trades,
        final_outcome=final_outcome,
        data_path=f"dune_trades_6488549.json (market={market_key})",
        market_name=f"megaquake-in-{market_key}",
        chart_base64=chart_base64,
    )

    # Сохраняем
    md_path = output_dir / f"backtest_{market_key}.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    return result, md_path


def main():
    # Конфиг стратегии
    config = StrategyConfig(
        min_edge=0.04,
        min_roi=0.15,
        sell_discount=0.02,
        base_monthly_prob=0.093,
    )

    # Загружаем данные
    data_path = Path("history/trades/dune_trades_6488549.json")
    if not data_path.exists():
        print(f"ERROR: Файл не найден: {data_path}")
        return

    trades_by_market = load_all_trades(data_path)

    # Создаём директорию для отчётов
    output_dir = Path("output/all_months_basic_strategy")
    output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 80)
    print("БЭКТЕСТ СТРАТЕГИИ ПО ВСЕМ ЗАКРЫТЫМ MEGAQUAKE РЫНКАМ")
    print("=" * 80)
    print(f"\nПараметры: min_edge={config.min_edge:.0%}, min_roi={config.min_roi:.0%}")
    print(f"Выходная директория: {output_dir}")
    print()

    # Результаты для сводки
    all_results = []

    for market_key, market_info in MARKETS.items():
        trades = trades_by_market.get(market_key, [])

        if not trades:
            print(f"⚠️  {market_info['name']}: нет данных — пропускаем")
            continue

        print(f"📊 {market_info['name']}...")
        print(f"   Сделок: {len(trades)}")

        result, md_path = run_backtest_and_save(
            trades, market_key, market_info, config, output_dir
        )

        print(f"   Входов: {result.total_entries}, P&L: ${result.total_pnl:.2f}, ROI: {result.roi:.1%}")
        print(f"   ✅ Сохранено: {md_path}")
        print()

        # Сравнение: начало vs середина
        market_start = market_info["start"]
        market_mid = market_start.replace(day=15)
        market_end = market_info["end"]

        res_start = run_backtest_filtered(trades, market_end, market_info["outcome"], config, market_start)
        res_mid = run_backtest_filtered(trades, market_end, market_info["outcome"], config, market_mid)

        all_results.append({
            "market": market_key,
            "name": market_info["name"],
            "trades": len(trades),
            "entries": result.total_entries,
            "pnl": result.total_pnl,
            "roi": result.roi,
            "start": res_start,
            "mid": res_mid,
        })

    # Сводный отчёт
    print("=" * 100)
    print("СВОДКА: НАЧАЛО vs СЕРЕДИНА МЕСЯЦА")
    print("=" * 100)
    print()
    print(f"{'Рынок':<12} {'Сделок':<8} {'|':^3} {'С начала':^25} {'|':^3} {'С середины':^25}")
    print(f"{'':<12} {'':<8} {'|':^3} {'Входов':<8} {'P&L':<9} {'ROI':<8} {'|':^3} {'Входов':<8} {'P&L':<9} {'ROI':<8}")
    print("-" * 100)

    total_start_pnl = 0
    total_mid_pnl = 0
    total_start_invested = 0
    total_mid_invested = 0

    for r in all_results:
        start = r["start"]
        mid = r["mid"]

        print(f"{r['market'].capitalize():<12} {r['trades']:<8} {'|':^3} "
              f"{start['entries']:<8} ${start['pnl']:<8.2f} {start['roi']:.1%}{'':>3} {'|':^3} "
              f"{mid['entries']:<8} ${mid['pnl']:<8.2f} {mid['roi']:.1%}")

        total_start_pnl += start["pnl"]
        total_mid_pnl += mid["pnl"]
        total_start_invested += start["invested"]
        total_mid_invested += mid["invested"]

    print("-" * 100)
    total_start_roi = total_start_pnl / total_start_invested if total_start_invested > 0 else 0
    total_mid_roi = total_mid_pnl / total_mid_invested if total_mid_invested > 0 else 0
    print(f"{'ИТОГО':<12} {'':<8} {'|':^3} "
          f"{'':<8} ${total_start_pnl:<8.2f} {total_start_roi:.1%}{'':>3} {'|':^3} "
          f"{'':<8} ${total_mid_pnl:<8.2f} {total_mid_roi:.1%}")

    # Вывод
    print("\n" + "=" * 100)
    print("ВЫВОДЫ")
    print("=" * 100)
    if total_mid_pnl > 0:
        loss = (1 - total_mid_pnl / total_start_pnl) * 100 if total_start_pnl > 0 else 100
        print(f"\n📈 С НАЧАЛА месяца: ${total_start_pnl:.2f} прибыли")
        print(f"📉 С СЕРЕДИНЫ месяца: ${total_mid_pnl:.2f} прибыли")
        print(f"\n⚠️  Потеря при старте с середины: {loss:.0f}%")
    else:
        print(f"\n📈 С НАЧАЛА месяца: ${total_start_pnl:.2f} прибыли")
        print(f"❌ С СЕРЕДИНЫ месяца: $0 — НЕТ ВОЗМОЖНОСТЕЙ")

    # Сохраняем сводку
    summary_md = f"""# Сводка бэктестов по всем Megaquake рынкам

**Дата:** {datetime.now().strftime('%Y-%m-%d %H:%M')}

## Параметры стратегии

| Параметр | Значение |
|----------|----------|
| Min Edge | {config.min_edge:.0%} |
| Min ROI | {config.min_roi:.0%} |
| Sell Discount | {config.sell_discount:.0%} |
| Base Prob | {config.base_monthly_prob:.1%}/месяц |

## Сравнение: НАЧАЛО vs СЕРЕДИНА месяца

| Рынок | Сделок | С начала (входов/P&L/ROI) | С середины (входов/P&L/ROI) |
|-------|--------|---------------------------|------------------------------|
"""

    for r in all_results:
        start = r["start"]
        mid = r["mid"]
        summary_md += f"| {r['name']} | {r['trades']} | {start['entries']} / ${start['pnl']:.2f} / {start['roi']:.1%} | {mid['entries']} / ${mid['pnl']:.2f} / {mid['roi']:.1%} |\n"

    summary_md += f"| **ИТОГО** | | **${total_start_pnl:.2f}** ({total_start_roi:.1%}) | **${total_mid_pnl:.2f}** ({total_mid_roi:.1%}) |\n"

    # Выводы
    if total_mid_pnl > 0:
        loss = (1 - total_mid_pnl / total_start_pnl) * 100 if total_start_pnl > 0 else 100
        conclusion = f"Потеря при старте с середины: **{loss:.0f}%**"
    else:
        conclusion = "При старте с середины месяца **НЕТ ВОЗМОЖНОСТЕЙ** для входа"

    summary_md += f"""
## Выводы

- С начала месяца: **${total_start_pnl:.2f}** прибыли (ROI {total_start_roi:.1%})
- С середины месяца: **${total_mid_pnl:.2f}** прибыли (ROI {total_mid_roi:.1%})
- {conclusion}

**Причина:** Edge-based стратегия требует раннего входа, когда fair_price ещё высокий.
К середине месяца рынок уже "прайсит" корректную вероятность и edge исчезает.

## Файлы отчётов

"""
    for r in all_results:
        summary_md += f"- [backtest_{r['market']}.md](backtest_{r['market']}.md)\n"

    summary_md += """
---

*Сгенерировано backtest_all_months.py*
"""

    summary_path = output_dir / "README.md"
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(summary_md)

    print(f"\n✅ Сводка сохранена: {summary_path}")
    print(f"✅ Всего отчётов: {len(all_results)}")


if __name__ == "__main__":
    main()
