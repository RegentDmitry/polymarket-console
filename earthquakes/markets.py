"""
Конфигурация earthquake рынков на Polymarket.
Легко добавлять новые рынки — просто добавьте в EARTHQUAKE_MARKETS.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from enum import Enum


class MarketType(Enum):
    """Тип рынка."""
    COUNT = "count"      # Сколько землетрясений (диапазоны)
    BINARY = "binary"    # Будет/не будет (YES/NO)


@dataclass
class Outcome:
    """Исход рынка."""
    name: str           # "8+", "14-16", "YES", "NO"
    min_count: int      # Минимальное количество (включительно)
    max_count: Optional[int]  # Максимальное (None = без ограничения)
    token_id: Optional[str] = None  # Заполняется при получении данных с Polymarket

    def matches(self, count: int) -> bool:
        """Проверяет, попадает ли count в этот исход."""
        if self.max_count is None:
            return count >= self.min_count
        return self.min_count <= count <= self.max_count


@dataclass
class EarthquakeMarket:
    """Конфигурация одного рынка."""
    id: str                     # Уникальный ID для внутреннего использования
    name: str                   # Человекочитаемое название
    url: str                    # URL на Polymarket
    condition_id: Optional[str] # Condition ID для API (извлекается из URL или API)
    market_type: MarketType
    magnitude: float            # Минимальная магнитуда (6.5, 7.0, 9.0, 10.0)
    start_date: datetime        # Начало периода
    end_date: datetime          # Конец периода
    outcomes: list[Outcome]     # Возможные исходы
    resolved: bool = False      # Рынок уже завершён?

    @property
    def period_days(self) -> float:
        """Длина периода в днях."""
        return (self.end_date - self.start_date).total_seconds() / 86400

    @property
    def remaining_days(self) -> float:
        """Сколько дней осталось до конца."""
        now = datetime.now(timezone.utc)
        remaining = (self.end_date - now).total_seconds() / 86400
        return max(0, remaining)

    @property
    def elapsed_days(self) -> float:
        """Сколько дней прошло с начала."""
        now = datetime.now(timezone.utc)
        elapsed = (now - self.start_date).total_seconds() / 86400
        return max(0, elapsed)


# ============================================================================
# ИСТОРИЧЕСКИЕ ЧАСТОТЫ ЗЕМЛЕТРЯСЕНИЙ (события в год)
# Источник: USGS 2000-2021
# ============================================================================

EARTHQUAKE_ANNUAL_RATES = {
    6.0: 120.0,    # ~120 M6.0+ в год
    6.5: 40.0,     # ~40 M6.5+ в год (USGS 2023-2025: 28-53)
    7.0: 15.0,     # ~15 M7.0+ в год (диапазон 6-23)
    7.5: 5.0,      # ~5 M7.5+ в год
    8.0: 1.0,      # ~1 M8.0+ в год
    8.5: 0.3,      # ~1 за 3 года
    9.0: 0.1,      # ~1 за 10 лет
    9.5: 0.02,     # ~1 за 50 лет
    10.0: 0.001,   # Никогда не было зарегистрировано
}


def get_annual_rate(magnitude: float) -> float:
    """Получить среднегодовую частоту для магнитуды."""
    # Находим ближайшую известную магнитуду
    known_mags = sorted(EARTHQUAKE_ANNUAL_RATES.keys())
    for mag in known_mags:
        if magnitude <= mag:
            return EARTHQUAKE_ANNUAL_RATES[mag]
    return EARTHQUAKE_ANNUAL_RATES[known_mags[-1]]


# ============================================================================
# КОНФИГУРАЦИЯ РЫНКОВ
# ============================================================================

EARTHQUAKE_MARKETS: list[EarthquakeMarket] = [
    # -------------------------------------------------------------------------
    # 7.0+ earthquakes by June 30, 2026
    # -------------------------------------------------------------------------
    EarthquakeMarket(
        id="7.0_by_june_2026",
        name="7.0+ by June 30, 2026",
        url="https://polymarket.com/event/how-many-7pt0-or-above-earthquakes-by-june-30",
        condition_id=None,  # Будет получен через API
        market_type=MarketType.COUNT,
        magnitude=7.0,
        start_date=datetime(2025, 12, 4, tzinfo=timezone.utc),
        end_date=datetime(2026, 6, 30, 23, 59, 59, tzinfo=timezone.utc),
        outcomes=[
            Outcome("0", 0, 0),
            Outcome("1", 1, 1),
            Outcome("2", 2, 2),
            Outcome("3", 3, 3),
            Outcome("4", 4, 4),
            Outcome("5", 5, 5),
            Outcome("6", 6, 6),
            Outcome("7", 7, 7),
            Outcome("8+", 8, None),
        ],
    ),

    # -------------------------------------------------------------------------
    # 7.0+ earthquakes in 2026 (full year)
    # -------------------------------------------------------------------------
    EarthquakeMarket(
        id="7.0_in_2026",
        name="7.0+ in 2026",
        url="https://polymarket.com/event/how-many-7pt0-or-above-earthquakes-in-2026",
        condition_id=None,
        market_type=MarketType.COUNT,
        magnitude=7.0,
        start_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end_date=datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc),
        outcomes=[
            Outcome("<5", 0, 4),
            Outcome("5-7", 5, 7),
            Outcome("8-10", 8, 10),
            Outcome("11-13", 11, 13),
            Outcome("14-16", 14, 16),
            Outcome("17-19", 17, 19),
            Outcome("20+", 20, None),
        ],
    ),

    # -------------------------------------------------------------------------
    # 10.0+ earthquake before 2027
    # -------------------------------------------------------------------------
    EarthquakeMarket(
        id="10.0_before_2027",
        name="10.0+ before 2027",
        url="https://polymarket.com/event/10pt0-or-above-earthquake-before-2027",
        condition_id=None,
        market_type=MarketType.BINARY,
        magnitude=10.0,
        start_date=datetime(2025, 12, 8, 12, 0, 0, tzinfo=timezone.utc),
        end_date=datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc),
        outcomes=[
            Outcome("YES", 1, None),  # 1 или более
            Outcome("NO", 0, 0),       # ровно 0
        ],
    ),

    # -------------------------------------------------------------------------
    # 9.0+ earthquake before 2027
    # -------------------------------------------------------------------------
    EarthquakeMarket(
        id="9.0_before_2027",
        name="9.0+ before 2027",
        url="https://polymarket.com/event/9pt0-or-above-earthquake-before-2027",
        condition_id=None,
        market_type=MarketType.BINARY,
        magnitude=9.0,
        start_date=datetime(2025, 12, 8, 12, 0, 0, tzinfo=timezone.utc),
        end_date=datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc),
        outcomes=[
            Outcome("YES", 1, None),
            Outcome("NO", 0, 0),
        ],
    ),

    # -------------------------------------------------------------------------
    # Another 7.0+ by Jan 31, 2026
    # -------------------------------------------------------------------------
    EarthquakeMarket(
        id="7.0_by_jan31",
        name="Another 7.0+ by Jan 31",
        url="https://polymarket.com/event/another-7pt0-or-above-earthquake-by-555",
        condition_id=None,
        market_type=MarketType.BINARY,
        magnitude=7.0,
        start_date=datetime(2025, 12, 31, 17, 5, 0, tzinfo=timezone.utc),  # Создан 31 дек
        end_date=datetime(2026, 1, 31, 23, 59, 59, tzinfo=timezone.utc),
        outcomes=[
            Outcome("YES", 1, None),
            Outcome("NO", 0, 0),
        ],
    ),

    # -------------------------------------------------------------------------
    # Another 7.0+ by Mar 31, 2026
    # -------------------------------------------------------------------------
    EarthquakeMarket(
        id="7.0_by_mar31",
        name="Another 7.0+ by Mar 31",
        url="https://polymarket.com/event/another-7pt0-or-above-earthquake-by-555",
        condition_id=None,
        market_type=MarketType.BINARY,
        magnitude=7.0,
        start_date=datetime(2025, 12, 31, 17, 5, 0, tzinfo=timezone.utc),
        end_date=datetime(2026, 3, 31, 23, 59, 59, tzinfo=timezone.utc),
        outcomes=[
            Outcome("YES", 1, None),
            Outcome("NO", 0, 0),
        ],
    ),
]


def get_market_by_id(market_id: str) -> Optional[EarthquakeMarket]:
    """Найти рынок по ID."""
    for market in EARTHQUAKE_MARKETS:
        if market.id == market_id:
            return market
    return None


def get_active_markets() -> list[EarthquakeMarket]:
    """Получить все активные (не resolved) рынки."""
    now = datetime.now(timezone.utc)
    return [m for m in EARTHQUAKE_MARKETS if not m.resolved and m.end_date > now]


if __name__ == "__main__":
    print("Earthquake Markets Configuration")
    print("=" * 60)
    for market in EARTHQUAKE_MARKETS:
        print(f"\n{market.name}")
        print(f"  ID: {market.id}")
        print(f"  Type: {market.market_type.value}")
        print(f"  Magnitude: M{market.magnitude}+")
        print(f"  Period: {market.start_date.date()} to {market.end_date.date()}")
        print(f"  Remaining: {market.remaining_days:.1f} days")
        print(f"  Outcomes: {[o.name for o in market.outcomes]}")
