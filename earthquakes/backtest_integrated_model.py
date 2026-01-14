#!/usr/bin/env python3
"""
Бэктест стратегии с IntegratedModel (Bayesian Poisson).
Сравнение с упрощённой моделью.
"""

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Optional

# Импортируем модели
from main_integrated import IntegratedModel, M8_MEAN, M8_HISTORICAL_COUNTS
from main_tested import TestedModel, SimpleModel, get_model_for_interval

from backtest_edge_strategy import (
    StrategyConfig,
    Position,
    TradeLog,
    BacktestResult,
    generate_markdown_report,
    generate_price_chart,
    calculate_expected_roi,
    calculate_apy,
)


# Параметры рынков (все megaquake = M8.0+)
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


class TestedBacktestEngine:
    """
    Движок бэктеста с базовой моделью для ВХОДА и TestedModel для ВЫХОДА.

    Вход: базовая модель (9.3%/мес) — более агрессивный (больше возможностей).
    Выход: TestedModel (7.89%/мес) — более консервативный (держим дольше).
    """

    def __init__(self, config: 'IntegratedStrategyConfig'):
        self.config = config
        self.positions: List[Position] = []
        self.result = BacktestResult(config=config)
        self.model = TestedModel(magnitude=8.0)
        self.base_monthly_prob = 0.093  # Базовая модель для входа

    def calculate_entry_fair_price(self, remaining_days: float) -> tuple[float, str]:
        """
        Рассчитать fair price для ВХОДА через базовую формулу.
        Более высокий fair_price = больше возможностей для входа.
        """
        prob = 1 - (1 - self.base_monthly_prob) ** (remaining_days / 30)
        return prob, "basic_9.3%"

    def calculate_exit_fair_price(self, remaining_days: float) -> tuple[float, str]:
        """
        Рассчитать fair price для ВЫХОДА через TestedModel.
        Более низкий fair_price = держим дольше, выходим реже.
        """
        prob, model_used = self.model.predict_range(
            min_count=1,
            max_count=None,
            period_days=remaining_days,
            current_count=0,
            interval_name="1+",
        )
        return prob, model_used

    def run(
        self,
        trades: List[Dict],
        market_end: datetime,
        final_outcome: str = "NO",
        verbose: bool = True,
    ) -> BacktestResult:
        """Запустить бэктест."""

        if verbose:
            # Проверяем какая модель будет использоваться
            test_entry, entry_model = self.calculate_entry_fair_price(30.0)
            test_exit, exit_model = self.calculate_exit_fair_price(30.0)
            print(f"\n{'='*70}")
            print("ЗАПУСК БЭКТЕСТА (базовая для входа, TestedModel для выхода)")
            print(f"{'='*70}")
            print(f"Сделок: {len(trades)}")
            print(f"Период: {trades[0]['time'][:10]} - {trades[-1]['time'][:10]}")
            print(f"Итог рынка: {final_outcome}")
            print(f"ВХОД: {entry_model} = {test_entry:.2%}")
            print(f"ВЫХОД: TestedModel ({exit_model}) = {test_exit:.2%}")
            print()

        for trade in trades:
            self._process_trade(trade, market_end, verbose)

        self._resolve_market(final_outcome, market_end, verbose)

        return self.result

    def _parse_time(self, time_str: str) -> Optional[datetime]:
        try:
            ts = time_str.replace(" UTC", "").replace(" ", "T")
            if "+" not in ts and "Z" not in ts:
                ts += "+00:00"
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except:
            return None

    def _process_trade(self, trade: Dict, market_end: datetime, verbose: bool):
        trade_time = self._parse_time(trade["time"])
        if not trade_time:
            return

        price = trade["price"]
        outcome = trade["outcome"]

        days_remaining = (market_end - trade_time).total_seconds() / 86400

        # Fair price для ВХОДА (базовая модель)
        entry_fair_price, entry_model = self.calculate_entry_fair_price(days_remaining)

        # Fair price для ВЫХОДА (TestedModel)
        exit_fair_price, exit_model = self.calculate_exit_fair_price(days_remaining)

        # Проверка входа (YES) — используем базовую модель (9.3%)
        if outcome == "YES" and len(self.positions) < self.config.max_positions:
            edge = entry_fair_price - price
            roi = calculate_expected_roi(price, entry_fair_price)
            apy = calculate_apy(roi, days_remaining)

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
                    fair_price=entry_fair_price,
                    edge=edge,
                    roi=roi,
                    details=f"days_left={days_remaining:.0f}, apy={apy:.0%}, model={entry_model}",
                )
                self.result.add_entry(log)

                if verbose:
                    print(f"ENTRY: {trade['time'][:16]} | YES @ {price:.4f} | "
                          f"fair={entry_fair_price:.4f} ({entry_model}) | edge={edge:.1%}")

        # Проверка выхода — используем TestedModel (более консервативный выход)
        target_price = exit_fair_price * (1 - self.config.sell_discount)

        closed_indices = []
        for i, pos in enumerate(self.positions):
            if outcome == "YES":
                if price >= target_price and price >= pos.entry_price:
                    pnl = pos.pnl_at_price(price)

                    log = TradeLog(
                        time=trade["time"],
                        action="EXIT",
                        price=price,
                        fair_price=exit_fair_price,
                        edge=exit_fair_price - price,
                        roi=(price - pos.entry_price) / pos.entry_price,
                        pnl=pnl,
                        details=f"entry={pos.entry_price:.4f}, target={target_price:.4f}",
                    )
                    self.result.add_exit(log)
                    closed_indices.append(i)

                    if verbose:
                        print(f"EXIT:  {trade['time'][:16]} | YES @ {price:.4f} | "
                              f"entry={pos.entry_price:.4f} | P&L=${pnl:.2f}")

        for i in reversed(closed_indices):
            self.positions.pop(i)

    def _resolve_market(self, final_outcome: str, end_time: datetime, verbose: bool):
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


@dataclass
class IntegratedStrategyConfig:
    """Параметры стратегии для IntegratedModel."""
    min_edge: float = 0.04
    min_roi: float = 0.15
    min_apy: float = 0.0
    position_size: float = 1.0
    sell_discount: float = 0.02
    max_positions: int = 100

    # Для совместимости с generate_markdown_report
    base_monthly_prob: float = M8_MEAN / 12  # ~9%/месяц


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

        raw_price = t.get("price", 0)
        outcome = t.get("outcome", "")

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

    for market in trades_by_market:
        trades_by_market[market].sort(key=lambda x: x["time"])

    return trades_by_market


def parse_time(time_str: str) -> Optional[datetime]:
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
    config: IntegratedStrategyConfig,
    start_from: datetime = None,
) -> dict:
    """Запустить бэктест с фильтрацией по дате."""
    if start_from:
        filtered = []
        for t in trades:
            dt = parse_time(t["time"])
            if dt and dt >= start_from:
                filtered.append(t)
        trades = filtered

    if not trades:
        return {"entries": 0, "pnl": 0, "roi": 0, "invested": 0}

    engine = TestedBacktestEngine(config)
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
    config: IntegratedStrategyConfig,
    output_dir: Path,
):
    """Запустить бэктест и сохранить MD отчёт."""
    market_end = market_info["end"]
    final_outcome = market_info["outcome"]

    engine = TestedBacktestEngine(config)
    result = engine.run(trades, market_end, final_outcome, verbose=False)

    # График
    chart_title = f"{market_info['name']} — История цен YES (IntegratedModel)"
    chart_base64 = generate_price_chart(trades, chart_title)

    # Переводим конфиг в StrategyConfig для generate_markdown_report
    basic_config = StrategyConfig(
        min_edge=config.min_edge,
        min_roi=config.min_roi,
        min_apy=config.min_apy,
        position_size=config.position_size,
        sell_discount=config.sell_discount,
        base_monthly_prob=config.base_monthly_prob,
    )

    md_content = generate_markdown_report(
        config=basic_config,
        result=result,
        trades_data=trades,
        final_outcome=final_outcome,
        data_path=f"dune_trades_6488549.json (market={market_key})",
        market_name=f"megaquake-in-{market_key}",
        chart_base64=chart_base64,
    )

    # Добавляем информацию о модели
    model_note = f"""
> **Модель:** IntegratedModel (Bayesian Poisson)
> **λ (M8.0+):** {M8_MEAN:.2f} событий/год
> **Исторические данные:** 2000-2024
"""
    md_content = md_content.replace("## Описание", f"## Описание\n\n{model_note}")

    md_path = output_dir / f"backtest_{market_key}_integrated.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    return result, md_path


def main():
    # Конфиг
    config = IntegratedStrategyConfig(
        min_edge=0.04,
        min_roi=0.15,
        sell_discount=0.02,
    )

    # Загрузка данных
    data_path = Path("history/trades/dune_trades_6488549.json")
    if not data_path.exists():
        print(f"ERROR: Файл не найден: {data_path}")
        return

    trades_by_market = load_all_trades(data_path)

    output_dir = Path("output/all_months_integrated")
    output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 100)
    print("БЭКТЕСТ С INTEGRATEDMODEL (Bayesian Poisson)")
    print("=" * 100)
    print(f"\nМодель: M8.0+, λ={M8_MEAN:.2f}/год, Bayesian=True")
    print(f"Параметры: min_edge={config.min_edge:.0%}, min_roi={config.min_roi:.0%}")
    print(f"Выходная директория: {output_dir}")
    print()

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
        print()

    # Сводка
    print("=" * 100)
    print("СВОДКА: НАЧАЛО vs СЕРЕДИНА МЕСЯЦА (IntegratedModel)")
    print("=" * 100)
    print()
    print(f"{'Рынок':<12} {'Сделок':<8} | {'С начала':^25} | {'С середины':^25}")
    print(f"{'':<12} {'':<8} | {'Входов':<8} {'P&L':<9} {'ROI':<8} | {'Входов':<8} {'P&L':<9} {'ROI':<8}")
    print("-" * 100)

    total_start_pnl = 0
    total_mid_pnl = 0
    total_start_invested = 0
    total_mid_invested = 0

    for r in all_results:
        start = r["start"]
        mid = r["mid"]

        print(f"{r['market'].capitalize():<12} {r['trades']:<8} | "
              f"{start['entries']:<8} ${start['pnl']:<8.2f} {start['roi']:.1%}{'':>3} | "
              f"{mid['entries']:<8} ${mid['pnl']:<8.2f} {mid['roi']:.1%}")

        total_start_pnl += start["pnl"]
        total_mid_pnl += mid["pnl"]
        total_start_invested += start["invested"]
        total_mid_invested += mid["invested"]

    print("-" * 100)
    total_start_roi = total_start_pnl / total_start_invested if total_start_invested > 0 else 0
    total_mid_roi = total_mid_pnl / total_mid_invested if total_mid_invested > 0 else 0
    print(f"{'ИТОГО':<12} {'':<8} | "
          f"{'':<8} ${total_start_pnl:<8.2f} {total_start_roi:.1%}{'':>3} | "
          f"{'':<8} ${total_mid_pnl:<8.2f} {total_mid_roi:.1%}")

    # Выводы
    print("\n" + "=" * 100)
    print("ВЫВОДЫ (IntegratedModel)")
    print("=" * 100)
    if total_mid_pnl > 0:
        loss = (1 - total_mid_pnl / total_start_pnl) * 100 if total_start_pnl > 0 else 100
        print(f"\n📈 С НАЧАЛА месяца: ${total_start_pnl:.2f} прибыли (ROI {total_start_roi:.1%})")
        print(f"📉 С СЕРЕДИНЫ месяца: ${total_mid_pnl:.2f} прибыли (ROI {total_mid_roi:.1%})")
        print(f"\n⚠️  Потеря при старте с середины: {loss:.0f}%")
    else:
        print(f"\n📈 С НАЧАЛА месяца: ${total_start_pnl:.2f} прибыли (ROI {total_start_roi:.1%})")
        print(f"❌ С СЕРЕДИНЫ месяца: $0 — НЕТ ВОЗМОЖНОСТЕЙ")

    # Сохраняем сводку
    summary_md = f"""# Сводка бэктестов с IntegratedModel

**Дата:** {datetime.now().strftime('%Y-%m-%d %H:%M')}

## Модель

- **Тип:** IntegratedModel (Bayesian Poisson)
- **Магнитуда:** M8.0+
- **λ:** {M8_MEAN:.2f} событий/год
- **Исторические данные:** 2000-2024

## Параметры стратегии

| Параметр | Значение |
|----------|----------|
| Min Edge | {config.min_edge:.0%} |
| Min ROI | {config.min_roi:.0%} |
| Sell Discount | {config.sell_discount:.0%} |

## Сравнение: НАЧАЛО vs СЕРЕДИНА месяца

| Рынок | Сделок | С начала (входов/P&L/ROI) | С середины (входов/P&L/ROI) |
|-------|--------|---------------------------|------------------------------|
"""

    for r in all_results:
        start = r["start"]
        mid = r["mid"]
        summary_md += f"| {r['name']} | {r['trades']} | {start['entries']} / ${start['pnl']:.2f} / {start['roi']:.1%} | {mid['entries']} / ${mid['pnl']:.2f} / {mid['roi']:.1%} |\n"

    summary_md += f"| **ИТОГО** | | **${total_start_pnl:.2f}** ({total_start_roi:.1%}) | **${total_mid_pnl:.2f}** ({total_mid_roi:.1%}) |\n"

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

## Файлы отчётов

"""
    for r in all_results:
        summary_md += f"- [backtest_{r['market']}_integrated.md](backtest_{r['market']}_integrated.md)\n"

    summary_md += """
---

*Сгенерировано backtest_integrated_model.py*
"""

    summary_path = output_dir / "README.md"
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(summary_md)

    print(f"\n✅ Сводка сохранена: {summary_path}")
    print(f"✅ Всего отчётов: {len(all_results)}")


if __name__ == "__main__":
    main()
