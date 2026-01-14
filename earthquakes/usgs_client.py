"""
Клиент для получения данных о землетрясениях с USGS API.
https://earthquake.usgs.gov/fdsnws/event/1/
"""

import httpx
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Optional


@dataclass
class Earthquake:
    """Данные о землетрясении."""
    id: str
    magnitude: float
    place: str
    time: datetime
    url: str

    def __repr__(self) -> str:
        return f"M{self.magnitude} - {self.place} ({self.time.strftime('%Y-%m-%d %H:%M')} UTC)"


class USGSClient:
    """Клиент для USGS Earthquake API."""

    BASE_URL = "https://earthquake.usgs.gov/fdsnws/event/1"

    def __init__(self, timeout: float = 30.0):
        self.client = httpx.Client(timeout=timeout)

    def get_earthquakes(
        self,
        start_time: datetime,
        end_time: Optional[datetime] = None,
        min_magnitude: float = 7.0,
    ) -> list[Earthquake]:
        """
        Получить список землетрясений за период.

        Args:
            start_time: Начало периода (UTC)
            end_time: Конец периода (UTC), по умолчанию - сейчас
            min_magnitude: Минимальная магнитуда (по умолчанию 7.0)

        Returns:
            Список землетрясений
        """
        if end_time is None:
            end_time = datetime.now(timezone.utc)

        params = {
            "format": "geojson",
            "starttime": start_time.strftime("%Y-%m-%d"),
            "endtime": end_time.strftime("%Y-%m-%d"),
            "minmagnitude": min_magnitude,
            "orderby": "time",
        }

        response = self.client.get(f"{self.BASE_URL}/query", params=params)
        response.raise_for_status()

        data = response.json()
        earthquakes = []

        for feature in data.get("features", []):
            props = feature["properties"]
            eq = Earthquake(
                id=feature["id"],
                magnitude=props["mag"],
                place=props["place"] or "Unknown location",
                time=datetime.fromtimestamp(props["time"] / 1000, tz=timezone.utc),
                url=props["url"],
            )
            earthquakes.append(eq)

        return earthquakes

    def count_earthquakes(
        self,
        start_time: datetime,
        end_time: Optional[datetime] = None,
        min_magnitude: float = 7.0,
    ) -> int:
        """Получить количество землетрясений за период."""
        if end_time is None:
            end_time = datetime.now(timezone.utc)

        params = {
            "format": "geojson",
            "starttime": start_time.strftime("%Y-%m-%d"),
            "endtime": end_time.strftime("%Y-%m-%d"),
            "minmagnitude": min_magnitude,
        }

        response = self.client.get(f"{self.BASE_URL}/count", params=params)
        response.raise_for_status()

        return int(response.text.strip())

    def get_historical_rate(
        self,
        years: int = 10,
        min_magnitude: float = 7.0,
    ) -> float:
        """
        Рассчитать среднегодовую частоту землетрясений на основе исторических данных.

        Args:
            years: Количество лет для анализа
            min_magnitude: Минимальная магнитуда

        Returns:
            Среднее количество землетрясений в год
        """
        now = datetime.now(timezone.utc)
        start = datetime(now.year - years, 1, 1, tzinfo=timezone.utc)
        end = datetime(now.year - 1, 12, 31, tzinfo=timezone.utc)  # Полные годы

        total = self.count_earthquakes(start, end, min_magnitude)
        actual_years = years - 1  # Исключаем текущий неполный год

        return total / actual_years if actual_years > 0 else total

    def close(self):
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


if __name__ == "__main__":
    # Тест
    with USGSClient() as client:
        # Получаем землетрясения с начала рынка
        market_start = datetime(2025, 12, 4, tzinfo=timezone.utc)
        earthquakes = client.get_earthquakes(market_start, min_magnitude=7.0)

        print(f"Землетрясений M7.0+ с {market_start.date()}: {len(earthquakes)}")
        for eq in earthquakes:
            print(f"  {eq}")

        # Историческая частота
        rate = client.get_historical_rate(years=10, min_magnitude=7.0)
        print(f"\nСредняя частота M7.0+ за последние 10 лет: {rate:.1f} в год")
