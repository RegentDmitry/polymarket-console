#!/usr/bin/env python3
"""
Консолидация позиций — объединяет несколько файлов для одного market+outcome в один.

Использование:
    # Dry-run (только показать что будет):
    python earthquakes/scripts/consolidate_positions.py

    # Выполнить:
    python earthquakes/scripts/consolidate_positions.py --apply

    # На сервере:
    cd /opt/polymarket/earthquakes && .venv/bin/python scripts/consolidate_positions.py --apply

Что делает:
1. Загружает все active/*.json
2. Группирует по (market_slug, outcome)
3. Для групп с >1 позицией: создаёт одну позицию со средневзвешенной ценой
4. Старые файлы удаляет (или --keep перемещает в history/)
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trading_bot.models.position import Position, PositionStatus
from trading_bot.storage.positions import PositionStorage


def main():
    parser = argparse.ArgumentParser(description="Консолидация позиций earthquake бота")
    parser.add_argument("--apply", action="store_true", help="Применить (без этого — dry-run)")
    parser.add_argument("--keep", action="store_true", help="Сохранить старые файлы в history/")
    parser.add_argument("--data-dir", type=str, default=None,
                        help="Путь к trading_bot/data/ (по умолчанию — автоопределение)")
    args = parser.parse_args()

    # Find data directory
    if args.data_dir:
        data_dir = Path(args.data_dir)
    else:
        # Try relative paths
        candidates = [
            Path(__file__).resolve().parent.parent / "trading_bot" / "data",
            Path("trading_bot/data"),
            Path("earthquakes/trading_bot/data"),
        ]
        data_dir = None
        for c in candidates:
            if c.exists():
                data_dir = c
                break
        if not data_dir:
            print("ERROR: Не найдена директория trading_bot/data/")
            print("Запусти из earthquakes/ или укажи --data-dir")
            sys.exit(1)

    active_dir = data_dir / "active"
    history_dir = data_dir / "history"

    storage = PositionStorage(active_dir, history_dir)
    positions = storage.load_all_active()

    print(f"Загружено {len(positions)} активных позиций из {active_dir}")

    # Group by (market_slug, outcome)
    groups: dict[tuple[str, str], list[Position]] = defaultdict(list)
    for pos in positions:
        groups[(pos.market_slug, pos.outcome)].append(pos)

    # Find groups that need consolidation
    to_consolidate = {k: v for k, v in groups.items() if len(v) > 1}

    if not to_consolidate:
        print("Все позиции уже уникальны. Нечего консолидировать.")
        return

    print(f"\nНайдено {len(to_consolidate)} групп для консолидации:\n")

    total_before = 0
    total_after = 0

    for (slug, outcome), group in sorted(to_consolidate.items()):
        total_tokens = sum(p.tokens for p in group)
        total_size = sum(p.entry_size for p in group)
        avg_price = total_size / total_tokens if total_tokens > 0 else 0

        # Keep the oldest position as the "survivor"
        group.sort(key=lambda p: p.entry_time)
        survivor = group[0]
        to_remove = group[1:]

        # Remove zero-token positions
        zero_positions = [p for p in group if p.tokens <= 0]

        total_before += len(group)
        total_after += 1

        print(f"  {slug} ({outcome})")
        print(f"    {len(group)} позиций → 1")
        print(f"    Токены: {' + '.join(f'{p.tokens:.1f}' for p in group)} = {total_tokens:.1f}")
        print(f"    Вход: {' + '.join(f'${p.entry_size:.2f}' for p in group)} = ${total_size:.2f}")
        print(f"    Средняя цена: {avg_price:.4f} ({avg_price:.2%})")
        if zero_positions:
            print(f"    ⚠ Пустые позиции (0 токенов): {len(zero_positions)}")
        print(f"    Выживший: {survivor.id} (самый старый)")
        print()

        if args.apply:
            # Update survivor with totals
            survivor.tokens = total_tokens
            survivor.entry_size = total_size
            survivor.entry_price = avg_price
            # Keep latest order_id
            latest = max(group, key=lambda p: p.entry_time)
            survivor.entry_order_id = latest.entry_order_id
            storage.save(survivor)

            # Remove duplicates
            for pos in to_remove:
                if args.keep:
                    pos.status = PositionStatus.CLOSED
                    pos.exit_price = pos.entry_price
                    pos.exit_size = 0
                    storage.move_to_history(pos)
                else:
                    storage.delete(pos.id)

            print(f"    ✓ Консолидировано в {survivor.id}")

    print(f"\nИтого: {total_before} позиций → {total_after}")

    if not args.apply:
        print("\n⚠ DRY-RUN: ничего не изменено. Добавь --apply для выполнения.")
    else:
        print("\n✓ Консолидация завершена.")

        # Clean sell_orders.json — remove entries for deleted position IDs
        sell_orders_path = data_dir / "sell_orders.json"
        if sell_orders_path.exists():
            try:
                with open(sell_orders_path) as f:
                    sell_orders = json.load(f)
                active_ids = {p.id for p in storage.load_all_active()}
                cleaned = {k: v for k, v in sell_orders.items() if k in active_ids}
                removed = len(sell_orders) - len(cleaned)
                if removed > 0:
                    with open(sell_orders_path, "w") as f:
                        json.dump(cleaned, f, indent=2)
                    print(f"  Очищено {removed} записей из sell_orders.json")
            except Exception as e:
                print(f"  ⚠ Ошибка очистки sell_orders.json: {e}")


if __name__ == "__main__":
    main()
