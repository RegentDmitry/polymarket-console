#!/usr/bin/env python3
"""Быстрая проверка статуса бота землетрясений."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from trading_bot.executor.polymarket import PolymarketExecutor

def main():
    print("🤖 Проверка статуса бота землетрясений\n")

    executor = PolymarketExecutor()

    if not executor.initialized:
        print("❌ Бот не инициализирован (проверь .env)")
        return 1

    # Баланс
    balance = executor.get_balance()
    address = executor.get_address()

    print(f"Address: {address}")
    print(f"Свободный USDC.e: ${balance:.2f}")
    print()

    # Позиции
    from trading_bot.storage.history import PositionStorage
    storage = PositionStorage(Path("trading_bot/data"))
    positions = storage.load_all_active()

    print(f"Активных позиций: {len(positions)}")
    if positions:
        total_entry = sum(p.entry_size for p in positions)
        print(f"Вложено в позиции: ${total_entry:.2f}")
        print()

        # Топ-3 позиции
        sorted_positions = sorted(positions, key=lambda p: p.entry_size, reverse=True)
        print("Топ-3 позиции:")
        for p in sorted_positions[:3]:
            print(f"  {p.market_name[:40]:40s} ${p.entry_size:6.2f}")

    print()

    # Проверка логов
    log_dir = Path("trading_bot/data/logs")
    if log_dir.exists():
        logs = sorted(log_dir.glob("*.log"), key=lambda f: f.stat().st_mtime, reverse=True)
        if logs:
            latest_log = logs[0]
            age_hours = (Path().stat().st_mtime - latest_log.stat().st_mtime) / 3600
            print(f"Последний лог: {latest_log.name}")
            print(f"Возраст: {age_hours:.1f} часов назад")

            if age_hours > 24:
                print("⚠️ БОТ НЕ РАБОТАЕТ > 24 ЧАСОВ!")
            elif age_hours > 1:
                print("⚠️ Бот давно не писал в лог")
            else:
                print("✅ Бот активен")

    print()
    print("💡 Для запуска бота используй:")
    print("   .venv/bin/python -m trading_bot --live --auto")

    return 0

if __name__ == '__main__':
    sys.exit(main())
