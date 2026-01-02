"""
Анализатор earthquake рынков.
Сравнивает справедливые цены (модель Пуассона) с рыночными ценами.
"""

import math
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Optional

from usgs_client import USGSClient
from polymarket_client import PolymarketClient, MarketData
from markets import EARTHQUAKE_ANNUAL_RATES


@dataclass
class Opportunity:
    """Торговая возможность."""
    event_slug: str           # Slug события
    market_question: str      # Вопрос рынка
    outcome: str              # "Yes" или "No"
    position: str             # "BUY YES" или "BUY NO"
    token_id: str             # ID токена для торговли
    fair_price: float         # Справедливая цена по модели (0-1)
    market_price: float       # Текущая рыночная цена (0-1)
    edge: float               # fair_price - market_price
    edge_pct: float           # edge в процентах
    ev_per_100: float         # Ожидаемая прибыль на $100
    kelly_fraction: float     # Доля банкролла по Kelly
    magnitude: float          # Магнитуда рынка
    current_count: int        # Текущее количество землетрясений
    remaining_days: float     # Дней до конца рынка

    def __repr__(self):
        return (
            f"{self.position} on '{self.outcome}' | "
            f"Fair: {self.fair_price*100:.1f}% vs Market: {self.market_price*100:.1f}% | "
            f"Edge: {self.edge_pct:+.1f}% | EV/100$: ${self.ev_per_100:.2f}"
        )


class EarthquakeAnalyzer:
    """Анализатор earthquake рынков."""

    def __init__(self):
        self.usgs = USGSClient()
        self.polymarket = PolymarketClient()

    def poisson_probability(self, k: int, lambda_: float) -> float:
        """P(X = k) для распределения Пуассона."""
        if k < 0 or lambda_ <= 0:
            return 0.0
        return (lambda_ ** k) * math.exp(-lambda_) / math.factorial(k)

    def poisson_at_least(self, k: int, lambda_: float) -> float:
        """P(X >= k) для распределения Пуассона."""
        if k <= 0:
            return 1.0
        # P(X >= k) = 1 - P(X <= k-1)
        cumulative = sum(self.poisson_probability(i, lambda_) for i in range(k))
        return 1 - cumulative

    def poisson_range(self, min_k: int, max_k: Optional[int], lambda_: float) -> float:
        """P(min_k <= X <= max_k) для распределения Пуассона."""
        if max_k is None:
            return self.poisson_at_least(min_k, lambda_)
        return sum(self.poisson_probability(i, lambda_) for i in range(min_k, max_k + 1))

    def get_annual_rate(self, magnitude: float) -> float:
        """Получить среднегодовую частоту для магнитуды."""
        # Интерполируем между известными значениями
        known_mags = sorted(EARTHQUAKE_ANNUAL_RATES.keys())
        for mag in known_mags:
            if magnitude <= mag:
                return EARTHQUAKE_ANNUAL_RATES[mag]
        return EARTHQUAKE_ANNUAL_RATES[known_mags[-1]]

    def calculate_lambda(self, magnitude: float, days: float) -> float:
        """Рассчитать λ для периода."""
        annual_rate = self.get_annual_rate(magnitude)
        return annual_rate * (days / 365.0)

    def kelly_criterion(self, probability: float, odds: float) -> float:
        """
        Рассчитать оптимальную долю банкролла по Kelly.

        Args:
            probability: Вероятность выигрыша (0-1)
            odds: Коэффициент выплаты (например, 1/price - 1)

        Returns:
            Доля банкролла (0-1)
        """
        if probability <= 0 or probability >= 1 or odds <= 0:
            return 0.0

        q = 1 - probability
        kelly = (probability * odds - q) / odds

        return max(0, kelly)

    def analyze_count_market(
        self,
        event_slug: str,
        markets: list[MarketData],
        magnitude: float,
        start_date: datetime,
        end_date: datetime,
    ) -> list[Opportunity]:
        """
        Анализировать count market (сколько землетрясений).

        Returns:
            Список торговых возможностей
        """
        opportunities = []
        now = datetime.now(timezone.utc)

        # Получаем текущее количество землетрясений
        earthquakes = self.usgs.get_earthquakes(start_date, now, magnitude)
        current_count = len(earthquakes)

        # Рассчитываем λ для оставшегося периода
        remaining_days = max(0, (end_date - now).total_seconds() / 86400)
        remaining_lambda = self.calculate_lambda(magnitude, remaining_days)

        for market in markets:
            if not market.active:
                continue

            # Парсим диапазон из вопроса
            range_info = self._parse_count_range(market.question)
            if range_info is None:
                continue

            min_total, max_total = range_info

            # Рассчитываем вероятность попадания в диапазон
            min_additional = max(0, min_total - current_count)

            if max_total is None:
                # "8+" - вероятность min_additional или более
                fair_prob = self.poisson_at_least(min_additional, remaining_lambda)
            else:
                max_additional = max_total - current_count
                if max_additional < 0:
                    fair_prob = 0.0
                elif min_additional > max_additional:
                    fair_prob = 0.0
                else:
                    fair_prob = self.poisson_range(min_additional, max_additional, remaining_lambda)

            # Получаем рыночные цены
            for outcome in market.outcomes:
                if outcome.closed:
                    continue

                if outcome.outcome_name == "Yes":
                    market_price = outcome.yes_price
                    fair_price = fair_prob
                    position = "BUY YES"
                else:  # No
                    market_price = outcome.no_price
                    fair_price = 1 - fair_prob
                    position = "BUY NO"

                edge = fair_price - market_price
                edge_pct = edge * 100

                # EV на $100
                if market_price > 0:
                    payout = 1 / market_price
                    ev_per_100 = 100 * (fair_price * payout - 1)
                else:
                    ev_per_100 = 0

                # Kelly
                if market_price > 0 and market_price < 1:
                    odds = (1 - market_price) / market_price
                    kelly = self.kelly_criterion(fair_price, odds)
                else:
                    kelly = 0

                opportunities.append(Opportunity(
                    event_slug=event_slug,
                    market_question=market.question,
                    outcome=outcome.outcome_name,
                    position=position,
                    token_id=outcome.token_id,
                    fair_price=fair_price,
                    market_price=market_price,
                    edge=edge,
                    edge_pct=edge_pct,
                    ev_per_100=ev_per_100,
                    kelly_fraction=kelly,
                    magnitude=magnitude,
                    current_count=current_count,
                    remaining_days=remaining_days,
                ))

        return opportunities

    def analyze_binary_market(
        self,
        event_slug: str,
        markets: list[MarketData],
        magnitude: float,
        start_date: datetime,
        end_date: datetime,
    ) -> list[Opportunity]:
        """
        Анализировать binary market (будет/не будет хотя бы одно).

        Returns:
            Список торговых возможностей
        """
        opportunities = []
        now = datetime.now(timezone.utc)

        # Получаем текущее количество землетрясений с начала рынка
        earthquakes = self.usgs.get_earthquakes(start_date, now, magnitude)
        current_count = len(earthquakes)

        # Если уже было хотя бы одно - YES выигрывает
        if current_count > 0:
            fair_prob_yes = 1.0
        else:
            remaining_days = max(0, (end_date - now).total_seconds() / 86400)
            remaining_lambda = self.calculate_lambda(magnitude, remaining_days)
            # P(хотя бы 1) = 1 - P(0)
            fair_prob_yes = 1 - self.poisson_probability(0, remaining_lambda)

        for market in markets:
            if not market.active:
                continue

            for outcome in market.outcomes:
                if outcome.closed:
                    continue

                if outcome.outcome_name == "Yes":
                    market_price = outcome.yes_price
                    fair_price = fair_prob_yes
                    position = "BUY YES"
                else:
                    market_price = outcome.no_price
                    fair_price = 1 - fair_prob_yes
                    position = "BUY NO"

                edge = fair_price - market_price
                edge_pct = edge * 100

                if market_price > 0:
                    payout = 1 / market_price
                    ev_per_100 = 100 * (fair_price * payout - 1)
                else:
                    ev_per_100 = 0

                if market_price > 0 and market_price < 1:
                    odds = (1 - market_price) / market_price
                    kelly = self.kelly_criterion(fair_price, odds)
                else:
                    kelly = 0

                remaining_days = max(0, (end_date - now).total_seconds() / 86400)

                opportunities.append(Opportunity(
                    event_slug=event_slug,
                    market_question=market.question,
                    outcome=outcome.outcome_name,
                    position=position,
                    token_id=outcome.token_id,
                    fair_price=fair_price,
                    market_price=market_price,
                    edge=edge,
                    edge_pct=edge_pct,
                    ev_per_100=ev_per_100,
                    kelly_fraction=kelly,
                    magnitude=magnitude,
                    current_count=current_count,
                    remaining_days=remaining_days,
                ))

        return opportunities

    def _parse_count_range(self, question: str) -> Optional[tuple[int, Optional[int]]]:
        """Парсит диапазон из вопроса рынка."""
        q = question.lower()

        # "exactly 0", "exactly 1", etc.
        if "exactly" in q:
            import re
            match = re.search(r"exactly (\d+)", q)
            if match:
                n = int(match.group(1))
                return (n, n)

        # "8 or more", "20 or more"
        if "or more" in q:
            import re
            match = re.search(r"(\d+) or more", q)
            if match:
                n = int(match.group(1))
                return (n, None)

        # "fewer than 5"
        if "fewer than" in q:
            import re
            match = re.search(r"fewer than (\d+)", q)
            if match:
                n = int(match.group(1))
                return (0, n - 1)

        # "between 5 and 7", "between 11 and 13"
        if "between" in q:
            import re
            match = re.search(r"between (\d+) and (\d+)", q)
            if match:
                return (int(match.group(1)), int(match.group(2)))

        return None

    def find_all_opportunities(self, min_edge: float = 0.0) -> list[Opportunity]:
        """
        Найти все торговые возможности на earthquake рынках.

        Args:
            min_edge: Минимальный edge для включения (0.05 = 5%)

        Returns:
            Список возможностей, отсортированный по EV
        """
        all_opportunities = []

        # Получаем все earthquake рынки
        all_prices = self.polymarket.get_all_earthquake_prices()

        for event_slug, markets in all_prices.items():
            # Определяем параметры рынка
            params = self._get_market_params(event_slug)
            if params is None:
                continue

            magnitude, start_date, end_date, market_type = params

            if market_type == "count":
                opps = self.analyze_count_market(
                    event_slug, markets, magnitude, start_date, end_date
                )
            else:  # binary
                opps = self.analyze_binary_market(
                    event_slug, markets, magnitude, start_date, end_date
                )

            all_opportunities.extend(opps)

        # Фильтруем по минимальному edge
        filtered = [o for o in all_opportunities if o.edge >= min_edge]

        # Сортируем по EV (убывание)
        filtered.sort(key=lambda x: x.ev_per_100, reverse=True)

        return filtered

    def _get_market_params(self, slug: str) -> Optional[tuple]:
        """Получить параметры рынка по slug."""
        # Конфигурация известных рынков
        configs = {
            "how-many-7pt0-or-above-earthquakes-by-june-30": (
                7.0,
                datetime(2025, 12, 4, 17, 0, 0, tzinfo=timezone.utc),
                datetime(2026, 6, 30, 23, 59, 59, tzinfo=timezone.utc),
                "count",
            ),
            "how-many-7pt0-or-above-earthquakes-in-2026": (
                7.0,
                datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
                datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc),
                "count",
            ),
            "10pt0-or-above-earthquake-before-2027": (
                10.0,
                datetime(2025, 12, 8, 12, 0, 0, tzinfo=timezone.utc),
                datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc),
                "binary",
            ),
            "9pt0-or-above-earthquake-before-2027": (
                9.0,
                datetime(2025, 12, 8, 12, 0, 0, tzinfo=timezone.utc),
                datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc),
                "binary",
            ),
            "another-7pt0-or-above-earthquake-by-555": (
                7.0,
                datetime(2025, 12, 31, 17, 5, 0, tzinfo=timezone.utc),
                datetime(2026, 3, 31, 23, 59, 59, tzinfo=timezone.utc),
                "binary",
            ),
            "how-many-6pt5-or-above-earthquakes-by-january-4": (
                6.5,
                datetime(2025, 12, 28, 0, 0, 0, tzinfo=timezone.utc),
                datetime(2026, 1, 4, 23, 59, 59, tzinfo=timezone.utc),
                "count",
            ),
        }

        return configs.get(slug)


if __name__ == "__main__":
    print("=" * 70)
    print("EARTHQUAKE MARKET ANALYZER")
    print("=" * 70)

    analyzer = EarthquakeAnalyzer()

    print("\nИщу торговые возможности...")
    opportunities = analyzer.find_all_opportunities(min_edge=-1.0)  # Показать все

    print(f"\nНайдено возможностей: {len(opportunities)}")
    print("\n" + "=" * 70)
    print("ТОП-15 ПО EV:")
    print("=" * 70)

    for i, opp in enumerate(opportunities[:15], 1):
        print(f"\n{i}. {opp.event_slug}")
        print(f"   Позиция: {opp.position} на '{opp.outcome}'")
        print(f"   Модель: {opp.fair_price*100:.1f}%  |  Рынок: {opp.market_price*100:.1f}%")
        print(f"   Edge: {opp.edge_pct:+.1f}%  |  EV/$100: ${opp.ev_per_100:.2f}")
        print(f"   Kelly: {opp.kelly_fraction*100:.1f}%  |  M{opp.magnitude}+ count: {opp.current_count}")
