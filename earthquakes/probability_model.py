"""
Модель Пуассона для расчёта вероятностей землетрясений.
"""

import math
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Optional


@dataclass
class MarketOutcome:
    """Исход рынка с вероятностями и ценами."""
    outcome: str  # "2", "3", "4", ..., "8+"
    min_count: int  # Минимальное количество для выигрыша
    max_count: Optional[int]  # Максимальное (None для "8+")
    fair_probability: float  # Справедливая вероятность по модели
    market_yes_price: Optional[float]  # Текущая цена YES на рынке
    market_no_price: Optional[float]  # Текущая цена NO на рынке

    @property
    def yes_edge(self) -> Optional[float]:
        """Преимущество при покупке YES (положительное = выгодно)."""
        if self.market_yes_price is None:
            return None
        return self.fair_probability - self.market_yes_price

    @property
    def no_edge(self) -> Optional[float]:
        """Преимущество при покупке NO (положительное = выгодно)."""
        if self.market_no_price is None:
            return None
        return (1 - self.fair_probability) - self.market_no_price


class PoissonModel:
    """
    Модель Пуассона для прогнозирования землетрясений.

    Распределение Пуассона хорошо описывает редкие независимые события,
    такие как землетрясения.

    P(k) = (λ^k * e^(-λ)) / k!

    где λ - среднее количество событий за период
    """

    # Историческое среднее M7.0+ землетрясений в год (USGS данные 2000-2021)
    DEFAULT_ANNUAL_RATE = 12.5

    def __init__(self, annual_rate: float = DEFAULT_ANNUAL_RATE):
        """
        Args:
            annual_rate: Среднее количество M7.0+ землетрясений в год
        """
        self.annual_rate = annual_rate

    def poisson_probability(self, k: int, lambda_: float) -> float:
        """
        Вероятность ровно k событий при среднем lambda_.

        P(k) = (λ^k * e^(-λ)) / k!
        """
        if k < 0:
            return 0.0
        return (lambda_ ** k) * math.exp(-lambda_) / math.factorial(k)

    def poisson_cumulative(self, k: int, lambda_: float) -> float:
        """
        Вероятность k или меньше событий.

        P(X <= k) = Σ P(i) для i от 0 до k
        """
        return sum(self.poisson_probability(i, lambda_) for i in range(k + 1))

    def poisson_at_least(self, k: int, lambda_: float) -> float:
        """
        Вероятность k или более событий.

        P(X >= k) = 1 - P(X <= k-1)
        """
        if k <= 0:
            return 1.0
        return 1 - self.poisson_cumulative(k - 1, lambda_)

    def calculate_lambda(
        self,
        start_date: datetime,
        end_date: datetime,
    ) -> float:
        """
        Рассчитать λ (ожидаемое количество событий) для периода.

        λ = annual_rate * (days / 365)
        """
        days = (end_date - start_date).days
        return self.annual_rate * (days / 365.0)

    def calculate_remaining_lambda(
        self,
        market_end: datetime,
        current_date: Optional[datetime] = None,
    ) -> float:
        """
        Рассчитать λ для оставшегося периода рынка.
        """
        if current_date is None:
            current_date = datetime.now(timezone.utc)
        return self.calculate_lambda(current_date, market_end)

    def calculate_outcome_probabilities(
        self,
        current_count: int,
        remaining_lambda: float,
        outcomes: list[tuple[str, int, Optional[int]]],
    ) -> list[tuple[str, float]]:
        """
        Рассчитать вероятности для каждого исхода рынка.

        Args:
            current_count: Текущее количество землетрясений
            remaining_lambda: λ для оставшегося периода
            outcomes: Список (название, min, max) - max=None означает "и более"

        Returns:
            Список (название, вероятность)
        """
        results = []

        for name, min_total, max_total in outcomes:
            # Сколько ещё нужно событий для попадания в диапазон
            min_additional = max(0, min_total - current_count)

            if max_total is None:
                # "8+" - вероятность min_additional или более
                prob = self.poisson_at_least(min_additional, remaining_lambda)
            else:
                max_additional = max_total - current_count
                if max_additional < 0:
                    # Уже превысили максимум - исход невозможен
                    prob = 0.0
                elif min_additional > max_additional:
                    prob = 0.0
                else:
                    # Вероятность попасть в диапазон [min_additional, max_additional]
                    prob = sum(
                        self.poisson_probability(i, remaining_lambda)
                        for i in range(min_additional, max_additional + 1)
                    )

            results.append((name, prob))

        return results


# Стандартные исходы для рынка землетрясений Polymarket
EARTHQUAKE_MARKET_OUTCOMES = [
    ("0", 0, 0),
    ("1", 1, 1),
    ("2", 2, 2),
    ("3", 3, 3),
    ("4", 4, 4),
    ("5", 5, 5),
    ("6", 6, 6),
    ("7", 7, 7),
    ("8+", 8, None),
]


if __name__ == "__main__":
    from datetime import datetime, timezone

    model = PoissonModel(annual_rate=12.5)

    # Параметры рынка
    market_start = datetime(2025, 12, 4, tzinfo=timezone.utc)
    market_end = datetime(2026, 6, 30, tzinfo=timezone.utc)
    current_date = datetime(2026, 1, 2, tzinfo=timezone.utc)

    # Уже произошло 2 землетрясения
    current_count = 2

    # Рассчитываем λ для оставшегося периода
    remaining_lambda = model.calculate_remaining_lambda(market_end, current_date)
    print(f"Оставшийся период: {(market_end - current_date).days} дней")
    print(f"λ (ожидаемое количество): {remaining_lambda:.2f}")
    print(f"Текущее количество: {current_count}")
    print()

    # Вероятности исходов
    print("Справедливые вероятности исходов:")
    print("-" * 40)

    probabilities = model.calculate_outcome_probabilities(
        current_count, remaining_lambda, EARTHQUAKE_MARKET_OUTCOMES
    )

    # Текущие рыночные цены (YES)
    market_prices = {
        "0": None,  # resolved
        "1": None,  # resolved
        "2": 0.007,
        "3": 0.021,
        "4": 0.014,
        "5": 0.046,
        "6": 0.08,
        "7": 0.12,
        "8+": 0.76,
    }

    total_prob = 0
    for name, prob in probabilities:
        market_price = market_prices.get(name)
        total_prob += prob

        if market_price is not None:
            edge = prob - market_price
            edge_pct = edge * 100
            signal = "BUY YES" if edge > 0.05 else ("BUY NO" if edge < -0.05 else "---")
            print(f"{name:>3}: {prob*100:6.2f}%  | Рынок: {market_price*100:5.1f}%  | Edge: {edge_pct:+6.2f}%  | {signal}")
        else:
            print(f"{name:>3}: {prob*100:6.2f}%  | (resolved)")

    print("-" * 40)
    print(f"Сумма вероятностей: {total_prob*100:.2f}%")
