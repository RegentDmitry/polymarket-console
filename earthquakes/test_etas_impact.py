#!/usr/bin/env python3
"""
Скрипт для проверки реального влияния ETAS на прогнозы.

Сравнивает вероятности с ETAS и без для разных магнитуд и периодов.
"""

import sys
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass

# Добавляем путь к модулям
sys.path.insert(0, '.')

from main_integrated import IntegratedModel
from usgs_client import USGSClient


@dataclass
class ETASComparison:
    """Результат сравнения моделей с/без ETAS."""
    magnitude: float
    period_days: int
    recent_events_count: int
    prob_with_etas: float
    prob_without_etas: float

    @property
    def absolute_diff(self) -> float:
        return self.prob_with_etas - self.prob_without_etas

    @property
    def relative_diff_pct(self) -> float:
        if self.prob_without_etas == 0:
            return 0.0
        return (self.absolute_diff / self.prob_without_etas) * 100


def get_recent_events(usgs: USGSClient, magnitude: float, days: int = 30) -> list[dict]:
    """Получить недавние события для ETAS."""
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days)

    # Для ETAS берём события на 0.5 меньше целевой магнитуды
    min_mag = magnitude - 0.5
    events = usgs.get_earthquakes(start, now, min_magnitude=min_mag)

    # USGSClient возвращает Earthquake dataclass объекты
    return [
        {
            'time': e.time,  # datetime объект
            'magnitude': e.magnitude,
        }
        for e in events
    ]


def compare_etas_impact(
    magnitude: float,
    period_days: int,
    recent_events: list[dict],
) -> ETASComparison:
    """Сравнить прогнозы с ETAS и без."""
    now = datetime.now(timezone.utc)
    end_date = now + timedelta(days=period_days)

    # Модель с ETAS
    model_with = IntegratedModel(
        magnitude=magnitude,
        use_etas=True,
        use_bayesian=True,
    )

    # Модель без ETAS
    model_without = IntegratedModel(
        magnitude=magnitude,
        use_etas=False,
        use_bayesian=True,
    )

    # Считаем P(хотя бы 1 событие)
    prob_with = model_with.probability_at_least_one(
        remaining_days=period_days,
        current_count=0,
        recent_events=recent_events,
        now=now,
        end_date=end_date,
    )

    prob_without = model_without.probability_at_least_one(
        remaining_days=period_days,
        current_count=0,
        recent_events=[],  # Без событий
        now=now,
        end_date=end_date,
    )

    return ETASComparison(
        magnitude=magnitude,
        period_days=period_days,
        recent_events_count=len(recent_events),
        prob_with_etas=prob_with,
        prob_without_etas=prob_without,
    )


def main():
    print("=" * 70)
    print("ТЕСТ ВЛИЯНИЯ ETAS НА ПРОГНОЗЫ")
    print("=" * 70)
    print()

    # Инициализация
    usgs = USGSClient()
    now = datetime.now(timezone.utc)

    # Тестовые конфигурации
    test_configs = [
        (7.0, 30),   # M7.0+, 1 месяц
        (7.0, 90),   # M7.0+, 3 месяца
        (7.0, 180),  # M7.0+, 6 месяцев
        (7.0, 365),  # M7.0+, 1 год
        (8.0, 30),   # M8.0+, 1 месяц
        (8.0, 180),  # M8.0+, 6 месяцев
        (8.0, 365),  # M8.0+, 1 год
    ]

    # Получаем недавние события для каждой магнитуды
    print("Загрузка недавних событий из USGS...")
    recent_m7 = get_recent_events(usgs, 7.0, days=30)
    recent_m8 = get_recent_events(usgs, 8.0, days=30)

    print(f"  M6.5+ за последние 30 дней: {len(recent_m7)} событий")
    print(f"  M7.5+ за последние 30 дней: {len(recent_m8)} событий")
    print()

    # Детали событий
    if recent_m7:
        print("Последние M6.5+ события:")
        for e in recent_m7[:5]:
            event_time = e['time']
            if isinstance(event_time, datetime):
                time_str = event_time.strftime('%Y-%m-%d')
            else:
                time_str = str(event_time)[:10]
            print(f"  M{e['magnitude']:.1f} - {time_str}")
        if len(recent_m7) > 5:
            print(f"  ... и ещё {len(recent_m7) - 5}")
        print()

    # Тестируем
    print("=" * 70)
    print(f"{'Магн.':<8} {'Период':<12} {'С ETAS':<12} {'Без ETAS':<12} {'Δ абс.':<10} {'Δ отн.':<10}")
    print("-" * 70)

    results = []
    for magnitude, period_days in test_configs:
        recent = recent_m7 if magnitude < 8.0 else recent_m8
        result = compare_etas_impact(magnitude, period_days, recent)
        results.append(result)

        period_str = f"{period_days} дн."
        print(
            f"M{magnitude:.1f}+   "
            f"{period_str:<12} "
            f"{result.prob_with_etas*100:>10.2f}%  "
            f"{result.prob_without_etas*100:>10.2f}%  "
            f"{result.absolute_diff*100:>+8.3f}%  "
            f"{result.relative_diff_pct:>+8.2f}%"
        )

    print("=" * 70)
    print()

    # Анализ
    print("АНАЛИЗ РЕЗУЛЬТАТОВ")
    print("-" * 70)

    max_abs_diff = max(abs(r.absolute_diff) for r in results)
    max_rel_diff = max(abs(r.relative_diff_pct) for r in results)
    avg_abs_diff = sum(abs(r.absolute_diff) for r in results) / len(results)

    print(f"Максимальная абсолютная разница: {max_abs_diff*100:.3f}%")
    print(f"Максимальная относительная разница: {max_rel_diff:.2f}%")
    print(f"Средняя абсолютная разница: {avg_abs_diff*100:.3f}%")
    print()

    # Вывод
    print("ВЫВОД")
    print("-" * 70)

    if max_abs_diff < 0.005:  # < 0.5%
        print("✗ ETAS даёт МИНИМАЛЬНЫЙ эффект (< 0.5% разницы)")
        print("  → Можно БЕЗОПАСНО отключить для упрощения модели")
        print("  → Меньше параметров = меньше риск overfitting")
    elif max_abs_diff < 0.02:  # < 2%
        print("⚠ ETAS даёт НЕБОЛЬШОЙ эффект (0.5-2% разницы)")
        print("  → Влияние есть, но незначительное")
        print("  → Решение зависит от требуемой точности")
    else:
        print("✓ ETAS даёт ЗНАЧИМЫЙ эффект (> 2% разницы)")
        print("  → ETAS стоит оставить в модели")
        print("  → Но проверить корректность параметров!")

    print()

    # Дополнительный тест: искусственное крупное событие
    print("=" * 70)
    print("ТЕСТ: Влияние гипотетического M8.5 события 3 дня назад")
    print("-" * 70)

    fake_event = {
        'time': now - timedelta(days=3),  # datetime объект
        'magnitude': 8.5,
    }

    for magnitude, period_days in [(7.0, 30), (7.0, 90)]:
        result_fake = compare_etas_impact(magnitude, period_days, [fake_event])
        result_none = compare_etas_impact(magnitude, period_days, [])

        print(f"M{magnitude:.1f}+, {period_days} дн.:")
        print(f"  После M8.5: {result_fake.prob_with_etas*100:.2f}% (без ETAS: {result_fake.prob_without_etas*100:.2f}%)")
        print(f"  Без событий: {result_none.prob_with_etas*100:.2f}%")
        print(f"  Δ от M8.5: {(result_fake.prob_with_etas - result_none.prob_without_etas)*100:+.3f}%")
        print()


if __name__ == "__main__":
    main()
