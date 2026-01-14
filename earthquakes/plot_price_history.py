#!/usr/bin/env python3
"""
График истории цен YES токена из данных Dune.
"""

import json
import argparse
from pathlib import Path
from datetime import datetime

try:
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
except ImportError:
    print("ERROR: matplotlib не установлен")
    print("Установите: pip install matplotlib")
    exit(1)


def load_trades(filepath: Path) -> list:
    """Загрузить сделки из JSON."""
    with open(filepath) as f:
        data = json.load(f)

    trades = []
    for t in data.get("trades", []):
        raw_price = t.get("price", 0)
        outcome = t.get("outcome", "")

        # Нормализуем цену для YES
        if outcome == "YES" and raw_price > 1:
            price = 1.0 / raw_price
        elif outcome == "NO" and raw_price > 1:
            price = 1.0 / raw_price
        else:
            price = raw_price

        if not (0 < price < 1):
            continue

        # Парсим время
        time_str = t.get("block_time", "")
        try:
            ts = time_str.replace(" UTC", "").replace(" ", "T")
            if "+" not in ts and "Z" not in ts:
                ts += "+00:00"
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except:
            continue

        trades.append({
            "time": dt,
            "price": price,
            "outcome": outcome,
        })

    return sorted(trades, key=lambda x: x["time"])


def plot_prices(trades: list, output_path: Path, title: str = ""):
    """Построить график цен."""

    # Разделяем YES и NO
    yes_times = [t["time"] for t in trades if t["outcome"] == "YES"]
    yes_prices = [t["price"] for t in trades if t["outcome"] == "YES"]

    no_times = [t["time"] for t in trades if t["outcome"] == "NO"]
    no_prices = [t["price"] for t in trades if t["outcome"] == "NO"]

    # Создаём график
    fig, ax = plt.subplots(figsize=(14, 7))

    # YES цены (основной график)
    if yes_times:
        ax.plot(yes_times, yes_prices, 'g-', linewidth=0.5, alpha=0.7, label='YES trades')
        ax.scatter(yes_times, yes_prices, c='green', s=3, alpha=0.5)

    # NO цены (конвертируем в YES эквивалент)
    if no_times:
        no_as_yes = [1 - p for p in no_prices]
        ax.plot(no_times, no_as_yes, 'r-', linewidth=0.5, alpha=0.7, label='NO trades (as YES)')
        ax.scatter(no_times, no_as_yes, c='red', s=3, alpha=0.5)

    # Настройки
    ax.set_xlabel('Дата', fontsize=12)
    ax.set_ylabel('Цена YES', fontsize=12)
    ax.set_title(title or 'История цен YES токена', fontsize=14)

    # Форматирование дат
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%d %b'))
    ax.xaxis.set_major_locator(mdates.DayLocator(interval=2))
    plt.xticks(rotation=45)

    # Сетка
    ax.grid(True, alpha=0.3)
    ax.set_ylim(0, max(max(yes_prices) if yes_prices else 0.2, 0.15) * 1.2)

    # Легенда
    ax.legend(loc='upper right')

    # Статистика
    if yes_prices:
        stats_text = f"YES: min={min(yes_prices):.1%}, max={max(yes_prices):.1%}, avg={sum(yes_prices)/len(yes_prices):.1%}"
        ax.text(0.02, 0.98, stats_text, transform=ax.transAxes, fontsize=9,
                verticalalignment='top', bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))

    plt.tight_layout()

    # Сохраняем
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    print(f"График сохранён: {output_path}")

    # Показываем если возможно
    try:
        plt.show()
    except:
        pass


def main():
    parser = argparse.ArgumentParser(description="График истории цен")
    parser.add_argument("--data", type=str, default="history/trades/megaquake_january_dune.json",
                        help="Путь к файлу с данными")
    parser.add_argument("--output", type=str, default="output/price_history.png",
                        help="Путь для сохранения графика")
    parser.add_argument("--title", type=str, default="",
                        help="Заголовок графика")

    args = parser.parse_args()

    # Загружаем данные
    data_path = Path(args.data)
    if not data_path.exists():
        print(f"ERROR: Файл не найден: {data_path}")
        return

    trades = load_trades(data_path)
    if not trades:
        print("ERROR: Нет данных для графика")
        return

    print(f"Загружено {len(trades)} сделок")
    print(f"Период: {trades[0]['time'].date()} — {trades[-1]['time'].date()}")

    # Определяем название рынка
    title = args.title
    if not title:
        if "january" in args.data.lower():
            title = "Megaquake in January — История цен YES"
        else:
            title = f"История цен — {data_path.stem}"

    # Строим график
    output_path = Path(args.output)
    output_path.parent.mkdir(exist_ok=True)

    plot_prices(trades, output_path, title)


if __name__ == "__main__":
    main()
