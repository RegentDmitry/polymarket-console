#!/usr/bin/env python3
"""
Earthquake Trading Bot с ИНТЕГРИРОВАННОЙ МОДЕЛЬЮ для Polymarket.

Улучшения по сравнению с базовой моделью (main.py):
1. Байесовский Пуассон (Gamma-Poisson) вместо фиксированного λ
2. ETAS-коррекция для учёта кластеризации афтершоков
3. Исторический fit на данных 1900-2024

Использование:
    python main_integrated.py              # Режим анализа (без торговли)
    python main_integrated.py --debug      # Режим отладки
    python main_integrated.py --auto       # Автоматическая торговля
    python main_integrated.py --compare    # Сравнение с базовой моделью

"""

import argparse
import math
import sys
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from usgs_client import USGSClient
from polymarket_client import PolymarketClient
from markets import EARTHQUAKE_ANNUAL_RATES

# Для Negative Binomial и Gamma distributions
try:
    from scipy import stats
    from scipy.special import gammaln
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False
    print("WARNING: scipy not installed. Using fallback implementations.")



# ============================================================================
# КОНФИГУРАЦИЯ
# ============================================================================

# Минимальный edge для торговли (0.5% = 0.005)
MIN_EDGE = 0.005

# Минимальная годовая доходность (3% = 0.03)
MIN_ANNUAL_RETURN = 0.03

# Минимальная ставка в долларах
MIN_BET_USD = 5.0

# Максимальная ставка в % от ликвидности (чтобы избежать slippage)
MAX_LIQUIDITY_PCT = 0.10

# Kelly fraction (консервативный подход)
KELLY_FRACTION = 0.25

# Максимум на одну ставку в % от банкролла
MAX_BET_PCT = 0.05


# ============================================================================
# ИСТОРИЧЕСКИЕ ДАННЫЕ M7.0+ (для Bayesian prior)
# Источник: USGS, 1900-2024
# ============================================================================

# Количество M7.0+ землетрясений по годам (1900-2024)
# Источник: https://earthquake.usgs.gov/earthquakes/browse/stats.php
M7_HISTORICAL_COUNTS = {
    # 1900-1909
    1900: 13, 1901: 14, 1902: 12, 1903: 10, 1904: 16,
    1905: 14, 1906: 21, 1907: 16, 1908: 11, 1909: 14,
    # 1910-1919
    1910: 18, 1911: 15, 1912: 12, 1913: 13, 1914: 16,
    1915: 17, 1916: 14, 1917: 19, 1918: 16, 1919: 13,
    # 1920-1929
    1920: 13, 1921: 14, 1922: 14, 1923: 16, 1924: 18,
    1925: 17, 1926: 18, 1927: 15, 1928: 14, 1929: 13,
    # 1930-1939
    1930: 13, 1931: 18, 1932: 13, 1933: 14, 1934: 18,
    1935: 19, 1936: 15, 1937: 13, 1938: 13, 1939: 16,
    # 1940-1949
    1940: 14, 1941: 17, 1942: 16, 1943: 22, 1944: 13,
    1945: 15, 1946: 17, 1947: 13, 1948: 11, 1949: 15,
    # 1950-1959
    1950: 18, 1951: 16, 1952: 15, 1953: 16, 1954: 13,
    1955: 13, 1956: 15, 1957: 14, 1958: 13, 1959: 15,
    # 1960-1969
    1960: 14, 1961: 14, 1962: 13, 1963: 15, 1964: 13,
    1965: 16, 1966: 13, 1967: 12, 1968: 17, 1969: 14,
    # 1970-1979
    1970: 15, 1971: 14, 1972: 12, 1973: 13, 1974: 14,
    1975: 14, 1976: 15, 1977: 11, 1978: 14, 1979: 13,
    # 1980-1989
    1980: 14, 1981: 11, 1982: 12, 1983: 14, 1984: 11,
    1985: 13, 1986: 14, 1987: 11, 1988: 12, 1989: 14,
    # 1990-1999
    1990: 12, 1991: 11, 1992: 13, 1993: 12, 1994: 13,
    1995: 18, 1996: 14, 1997: 16, 1998: 12, 1999: 18,
    # 2000-2009
    2000: 14, 2001: 15, 2002: 13, 2003: 14, 2004: 16,
    2005: 14, 2006: 15, 2007: 17, 2008: 12, 2009: 16,
    # 2010-2019
    2010: 23, 2011: 19, 2012: 12, 2013: 17, 2014: 11,
    2015: 18, 2016: 16, 2017: 7, 2018: 16, 2019: 13,
    # 2020-2024
    2020: 9, 2021: 16, 2022: 11, 2023: 18, 2024: 14,
}

# Статистика M7.0+
M7_MEAN = sum(M7_HISTORICAL_COUNTS.values()) / len(M7_HISTORICAL_COUNTS)  # ~14.5
M7_VAR = sum((x - M7_MEAN) ** 2 for x in M7_HISTORICAL_COUNTS.values()) / len(M7_HISTORICAL_COUNTS)  # ~8.5
M7_STD = math.sqrt(M7_VAR)  # ~2.9
M7_MIN = min(M7_HISTORICAL_COUNTS.values())  # 7
M7_MAX = max(M7_HISTORICAL_COUNTS.values())  # 23

# Gamma prior параметры (fit на исторические данные)
# Gamma: mean = α/β, var = α/β²
# => β = mean/var, α = mean * β
GAMMA_BETA_M7 = M7_MEAN / M7_VAR  # ~1.7
GAMMA_ALPHA_M7 = M7_MEAN * GAMMA_BETA_M7  # ~24.6


# ============================================================================
# ИСТОРИЧЕСКИЕ ДАННЫЕ M8.0+ (для Bayesian prior)
# Источник: USGS, 2000-2024
# ============================================================================

M8_HISTORICAL_COUNTS = {
    2000: 1, 2001: 1, 2002: 0, 2003: 1, 2004: 2,  # 2004: Sumatra M9.1
    2005: 2, 2006: 2, 2007: 4, 2008: 0, 2009: 1,
    2010: 2, 2011: 1, 2012: 2, 2013: 2, 2014: 1,  # 2011: Tohoku M9.1
    2015: 1, 2016: 0, 2017: 1, 2018: 1, 2019: 0,
    2020: 0, 2021: 2, 2022: 0, 2023: 0, 2024: 1,
}

M8_MEAN = sum(M8_HISTORICAL_COUNTS.values()) / len(M8_HISTORICAL_COUNTS)  # ~1.08
M8_VAR = sum((x - M8_MEAN) ** 2 for x in M8_HISTORICAL_COUNTS.values()) / len(M8_HISTORICAL_COUNTS)
M8_STD = math.sqrt(M8_VAR) if M8_VAR > 0 else 0.5


# ============================================================================
# ИСТОРИЧЕСКИЕ ДАННЫЕ M9.0+ (для справки)
# ============================================================================

# M9.0+ события за 1900-2024 (всего 5):
# 1952: Kamchatka M9.0
# 1960: Chile M9.5 (крупнейшее в истории)
# 1964: Alaska M9.2
# 2004: Sumatra M9.1
# 2011: Tohoku M9.1
M9_EVENTS_TOTAL = 5
M9_YEARS_OBSERVED = 124
M9_ANNUAL_RATE = M9_EVENTS_TOTAL / M9_YEARS_OBSERVED  # ~0.04/год


# ============================================================================
# ИНТЕГРИРОВАННАЯ МОДЕЛЬ
# ============================================================================

def gamma_pdf(x: float, alpha: float, beta: float) -> float:
    """PDF Gamma распределения (fallback без scipy)."""
    if x <= 0:
        return 0.0
    log_pdf = alpha * math.log(beta) - gammaln(alpha) + (alpha - 1) * math.log(x) - beta * x
    return math.exp(log_pdf)


def negative_binomial_pmf(k: int, r: float, p: float) -> float:
    """
    PMF Negative Binomial (fallback без scipy).

    P(X = k) = C(k + r - 1, k) * p^r * (1-p)^k
    """
    if k < 0 or r <= 0 or p <= 0 or p > 1:
        return 0.0

    # Используем log для численной стабильности
    log_coeff = gammaln(k + r) - gammaln(k + 1) - gammaln(r)
    log_prob = r * math.log(p) + k * math.log(1 - p)

    return math.exp(log_coeff + log_prob)


def negative_binomial_cdf(k: int, r: float, p: float) -> float:
    """CDF Negative Binomial (сумма PMF до k включительно)."""
    if HAS_SCIPY:
        return stats.nbinom.cdf(k, r, p)
    return sum(negative_binomial_pmf(i, r, p) for i in range(k + 1))


class IntegratedModel:
    """
    Интегрированная модель прогнозирования землетрясений.

    Компоненты:
    1. Bayesian Poisson (Gamma-Poisson conjugate)
    2. ETAS-коррекция (кластеризация) — отключена по умолчанию

    Примечание по ETAS:
        ETAS полезен для M4.0-5.0 на коротких горизонтах (дни-недели).
        Для M7.0+ на месячных горизонтах эффект < 0.1%, можно игнорировать.
        Включать use_etas=True только для низких магнитуд.
    """

    def __init__(
        self,
        magnitude: float = 7.0,
        use_etas: bool = False,  # Отключено: для M7.0+ эффект минимален
        use_bayesian: bool = True,
    ):
        self.magnitude = magnitude
        self.use_etas = use_etas
        self.use_bayesian = use_bayesian and HAS_SCIPY

        # Выбираем исторические данные в зависимости от магнитуды
        if magnitude >= 9.0:
            # M9.0+ — слишком редкие, Bayesian не имеет смысла
            self.annual_rate = M9_ANNUAL_RATE  # ~0.04/год
            self.historical_counts = None  # Нет годовых данных
            self.use_bayesian = False  # Отключаем Bayesian для редких событий
            self.alpha_prior = 1.0
            self.beta_prior = 1.0 / self.annual_rate

        elif magnitude >= 8.0:
            # M8.0+ — используем отдельные данные
            self.annual_rate = M8_MEAN  # ~1.08/год
            self.historical_counts = M8_HISTORICAL_COUNTS
            if M8_VAR > 0:
                self.beta_prior = M8_MEAN / M8_VAR
                self.alpha_prior = M8_MEAN * self.beta_prior
            else:
                # Fallback: предполагаем Poisson (var = mean)
                self.beta_prior = 1.0
                self.alpha_prior = M8_MEAN

        elif magnitude >= 7.0:
            # M7.0+ — полные исторические данные
            self.annual_rate = M7_MEAN
            self.historical_counts = M7_HISTORICAL_COUNTS
            self.alpha_prior = GAMMA_ALPHA_M7
            self.beta_prior = GAMMA_BETA_M7

        else:
            # M6.x и ниже — табличные значения
            self.annual_rate = EARTHQUAKE_ANNUAL_RATES.get(magnitude, 15.0)
            self.historical_counts = None
            # Предполагаем похожую относительную вариативность как у M7.0+
            var_ratio = M7_VAR / (M7_MEAN ** 2)  # ~0.04
            var = self.annual_rate ** 2 * var_ratio
            self.beta_prior = self.annual_rate / var if var > 0 else 1.0
            self.alpha_prior = self.annual_rate * self.beta_prior

    def get_bayesian_lambda(
        self,
        observed_count: int = 0,
        observed_years: float = 0.0,
    ) -> tuple[float, float, float]:
        """
        Получить posterior параметры λ после наблюдений.

        Returns:
            (alpha_post, beta_post, lambda_mean)
        """
        if not self.use_bayesian:
            return self.alpha_prior, self.beta_prior, self.annual_rate

        # Bayesian update: Gamma(α, β) + Poisson(n за t лет) = Gamma(α + n, β + t)
        alpha_post = self.alpha_prior + observed_count
        beta_post = self.beta_prior + observed_years

        # Posterior mean
        lambda_mean = alpha_post / beta_post

        return alpha_post, beta_post, lambda_mean

    def etas_boost(
        self,
        recent_events: list[dict],
        now: datetime,
        decay_days: float = 30.0,
    ) -> float:
        """
        ETAS-коррекция на основе недавних событий.

        Закон Омори: частота афтершоков ~ 1/(t + c)^p
        Продуктивность ~ 10^(α * (M - Mc))

        Args:
            recent_events: список событий с 'time' и 'magnitude'
            now: текущее время
            decay_days: горизонт влияния

        Returns:
            Дополнительный λ от кластеризации
        """
        if not self.use_etas or not recent_events:
            return 0.0

        boost = 0.0

        # Параметры ETAS (типичные значения)
        c = 0.01  # Параметр Омори (дни)
        p = 1.1   # Экспонента Омори
        alpha = 0.8  # Параметр продуктивности
        Mc = self.magnitude - 0.5  # Cutoff magnitude

        for event in recent_events:
            event_time = event.get('time')
            if isinstance(event_time, str):
                event_time = datetime.fromisoformat(event_time.replace('Z', '+00:00'))

            days_ago = (now - event_time).total_seconds() / 86400

            if days_ago < 0 or days_ago > decay_days:
                continue

            mag = float(event.get('magnitude', 0))

            if mag < Mc:
                continue

            # Продуктивность: сколько афтершоков ожидаем от этого события
            # 10^(α * (M - Mc)) = ожидаемое число M≥Mc афтершоков
            productivity = 10 ** (alpha * (mag - Mc))

            # Затухание по Омори
            omori_factor = 1 / ((days_ago + c) ** p)

            # Вклад этого события
            boost += productivity * omori_factor * 0.01  # Калибровочный коэффициент

        return boost

    def probability_count(
        self,
        min_count: int,
        max_count: Optional[int],
        remaining_days: float,
        current_count: int = 0,
        recent_events: list[dict] = None,
        now: datetime = None,
        end_date: datetime = None,
    ) -> float:
        """
        Вероятность P(min_count <= X <= max_count) за оставшийся период.

        Использует Negative Binomial (Gamma-Poisson predictive).
        """
        if now is None:
            now = datetime.now(timezone.utc)
        if end_date is None:
            end_date = now + timedelta(days=remaining_days)
        if recent_events is None:
            recent_events = []

        # 1. Bayesian posterior для λ
        # Используем данные последних 5 лет как "наблюдения"
        if self.use_bayesian and self.historical_counts is not None:
            recent_years = 5
            recent_observed = sum(
                self.historical_counts.get(year, int(self.annual_rate))
                for year in range(2020, 2025)
            )
            alpha_post, beta_post, lambda_mean = self.get_bayesian_lambda(
                observed_count=recent_observed,
                observed_years=recent_years,
            )
        else:
            # Для редких событий (M9.0+) или без Bayesian — используем prior
            alpha_post = self.alpha_prior
            beta_post = self.beta_prior
            lambda_mean = self.annual_rate

        # 2. ETAS boost
        etas_lambda = self.etas_boost(recent_events, now)

        # 3. Финальный λ для периода
        remaining_years = remaining_days / 365.0

        # Базовый λ для периода из posterior
        # Predictive: X ~ NegBinom(α_post, β_post/(β_post + t))
        # где t = remaining_years

        # Корректируем на ETAS
        effective_alpha = alpha_post
        effective_beta = beta_post / (1 + etas_lambda / lambda_mean)

        # Negative Binomial параметры
        r = effective_alpha
        p = effective_beta / (effective_beta + remaining_years)

        if p <= 0 or p >= 1:
            # Fallback на простой Пуассон
            lam = lambda_mean * remaining_years + etas_lambda * remaining_years
            return self._poisson_range(min_count - current_count, max_count - current_count if max_count else None, lam)

        # Считаем дополнительное количество (сверх current_count)
        min_additional = max(0, min_count - current_count)

        if max_count is None:
            # P(X >= min_additional) = 1 - P(X < min_additional) = 1 - CDF(min_additional - 1)
            if min_additional == 0:
                return 1.0
            prob = 1.0 - negative_binomial_cdf(min_additional - 1, r, p)
        else:
            max_additional = max_count - current_count
            if max_additional < 0:
                return 0.0
            if max_additional < min_additional:
                return 0.0
            # P(min <= X <= max) = CDF(max) - CDF(min - 1)
            prob = negative_binomial_cdf(max_additional, r, p)
            if min_additional > 0:
                prob -= negative_binomial_cdf(min_additional - 1, r, p)

        return max(0.0, min(1.0, prob))

    def probability_at_least_one(
        self,
        remaining_days: float,
        current_count: int = 0,
        recent_events: list[dict] = None,
        now: datetime = None,
        end_date: datetime = None,
    ) -> float:
        """P(X >= 1) — вероятность хотя бы одного события."""
        if current_count > 0:
            return 1.0

        return self.probability_count(
            min_count=1,
            max_count=None,
            remaining_days=remaining_days,
            current_count=0,
            recent_events=recent_events,
            now=now,
            end_date=end_date,
        )

    def _poisson_range(self, min_k: int, max_k: Optional[int], lam: float) -> float:
        """Fallback: P(min_k <= X <= max_k) для Пуассона."""
        if lam <= 0:
            return 1.0 if min_k <= 0 else 0.0

        def poisson_pmf(k, l):
            if k < 0:
                return 0.0
            return (l ** k) * math.exp(-l) / math.factorial(k)

        if max_k is None:
            return 1 - sum(poisson_pmf(i, lam) for i in range(min_k))
        return sum(poisson_pmf(i, lam) for i in range(min_k, max_k + 1))

    def get_model_info(self) -> dict:
        """Информация о параметрах модели."""
        return {
            "magnitude": self.magnitude,
            "use_bayesian": self.use_bayesian,
            "use_etas": self.use_etas,
            "alpha_prior": self.alpha_prior,
            "beta_prior": self.beta_prior,
            "annual_rate_mean": self.annual_rate,
            "annual_rate_std": math.sqrt(self.alpha_prior) / self.beta_prior,
            "historical_mean": M7_MEAN,
            "historical_std": M7_STD,
            "historical_min": M7_MIN,
            "historical_max": M7_MAX,
        }


# ============================================================================
# ПРОСТАЯ МОДЕЛЬ (для сравнения)
# ============================================================================

class SimpleModel:
    """Простая модель Пуассона (как в main.py)."""

    def __init__(self, magnitude: float = 7.0):
        self.magnitude = magnitude
        self.annual_rate = EARTHQUAKE_ANNUAL_RATES.get(magnitude, 15.0)

    def probability_count(
        self,
        min_count: int,
        max_count: Optional[int],
        remaining_days: float,
        current_count: int = 0,
        **kwargs,
    ) -> float:
        """P(min_count <= X <= max_count) для простого Пуассона."""
        lam = self.annual_rate * (remaining_days / 365.0)

        min_additional = max(0, min_count - current_count)

        def poisson_pmf(k, l):
            if k < 0 or l <= 0:
                return 0.0
            return (l ** k) * math.exp(-l) / math.factorial(k)

        if max_count is None:
            return 1 - sum(poisson_pmf(i, lam) for i in range(min_additional))

        max_additional = max_count - current_count
        if max_additional < 0:
            return 0.0

        return sum(poisson_pmf(i, lam) for i in range(min_additional, max_additional + 1))

    def probability_at_least_one(
        self,
        remaining_days: float,
        current_count: int = 0,
        **kwargs,
    ) -> float:
        """P(X >= 1)."""
        if current_count > 0:
            return 1.0

        lam = self.annual_rate * (remaining_days / 365.0)
        return 1 - math.exp(-lam)

    def get_model_info(self) -> dict:
        return {
            "type": "simple_poisson",
            "magnitude": self.magnitude,
            "annual_rate": self.annual_rate,
        }


# ============================================================================
# KELLY CRITERION
# ============================================================================

def kelly_criterion(prob: float, odds: float) -> float:
    """Kelly fraction = (p * b - q) / b."""
    if prob <= 0 or prob >= 1 or odds <= 0:
        return 0.0
    q = 1 - prob
    return max(0, (prob * odds - q) / odds)


# ============================================================================
# MARKET CONFIGS
# ============================================================================

MARKET_CONFIGS = {
    "how-many-7pt0-or-above-earthquakes-by-june-30": {
        "magnitude": 7.0,
        "start": datetime(2025, 12, 4, 17, 0, 0, tzinfo=timezone.utc),
        "end": datetime(2026, 6, 30, 23, 59, 59, tzinfo=timezone.utc),
        "type": "count",
        "outcomes": [
            ("2", 2, 2), ("3", 3, 3), ("4", 4, 4), ("5", 5, 5),
            ("6", 6, 6), ("7", 7, 7), ("8+", 8, None),
        ],
    },
    "how-many-7pt0-or-above-earthquakes-in-2026": {
        "magnitude": 7.0,
        "start": datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
        "end": datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc),
        "type": "count",
        "outcomes": [
            ("<5", 0, 4), ("5-7", 5, 7), ("8-10", 8, 10), ("11-13", 11, 13),
            ("14-16", 14, 16), ("17-19", 17, 19), ("20+", 20, None),
        ],
    },
    "10pt0-or-above-earthquake-before-2027": {
        "magnitude": 10.0,
        "start": datetime(2025, 12, 8, 12, 0, 0, tzinfo=timezone.utc),
        "end": datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc),
        "type": "binary",
    },
    "9pt0-or-above-earthquake-before-2027": {
        "magnitude": 9.0,
        "start": datetime(2025, 12, 8, 12, 0, 0, tzinfo=timezone.utc),
        "end": datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc),
        "type": "binary",
    },
    "megaquake-by-january-31": {
        "magnitude": 8.0,
        "start": datetime(2025, 12, 28, 0, 0, 0, tzinfo=timezone.utc),
        "end": datetime(2026, 1, 31, 23, 59, 59, tzinfo=timezone.utc),
        "type": "binary",
    },
    "megaquake-by-march-31": {
        "magnitude": 8.0,
        "start": datetime(2025, 12, 28, 0, 0, 0, tzinfo=timezone.utc),
        "end": datetime(2026, 3, 31, 23, 59, 59, tzinfo=timezone.utc),
        "type": "binary",
    },
    "megaquake-by-june-30": {
        "magnitude": 8.0,
        "start": datetime(2025, 12, 28, 0, 0, 0, tzinfo=timezone.utc),
        "end": datetime(2026, 6, 30, 23, 59, 59, tzinfo=timezone.utc),
        "type": "binary",
    },
}


# ============================================================================
# OPPORTUNITY DATACLASS
# ============================================================================

@dataclass
class Opportunity:
    """Торговая возможность."""
    event: str
    outcome: str
    side: str  # "YES" or "NO"
    token_id: str
    fair_price: float
    market_price: float
    edge: float
    kelly: float
    current_count: int
    lambda_used: float
    remaining_days: float = 0
    condition_id: str = ""
    liquidity_usd: Optional[float] = None

    # Дополнительные поля для интегрированной модели
    simple_fair_price: float = 0.0  # Цена по простой модели (для сравнения)
    model_components: dict = field(default_factory=dict)  # Вклад компонентов

    @property
    def expected_return(self) -> float:
        """Ожидаемая доходность сделки."""
        if self.market_price <= 0:
            return 0.0
        return self.fair_price / self.market_price - 1

    @property
    def annual_return(self) -> float:
        """Годовая доходность (APY)."""
        if self.remaining_days <= 0:
            return 0.0
        return self.expected_return * (365 / self.remaining_days)

    @property
    def model_diff(self) -> float:
        """Разница между интегрированной и простой моделью."""
        return self.fair_price - self.simple_fair_price


# ============================================================================
# ORDERBOOK HELPERS
# ============================================================================

def get_orderbook_data(poly: 'PolymarketClient', condition_id: str, outcome: str) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """Получить данные ордербука для покупки из CLOB API."""
    import httpx

    try:
        clob_market = poly.get_clob_market(condition_id)
        if not clob_market or not clob_market.get("enable_order_book"):
            return None, None, None

        for token in clob_market.get("tokens", []):
            if token.get("outcome") == outcome:
                token_id = token.get("token_id")

                response = httpx.get(
                    f"{poly.host}/book",
                    params={"token_id": token_id},
                    timeout=30,
                )
                if response.status_code != 200:
                    return None, None, token_id

                ob = response.json()
                asks = ob.get("asks", [])

                if not asks:
                    return None, None, token_id

                best_ask = min(float(ask.get("price", 1.0)) for ask in asks)

                liquidity = 0.0
                for ask in asks:
                    price = float(ask.get("price", 0))
                    size = float(ask.get("size", 0))
                    liquidity += price * size

                return best_ask, liquidity, token_id

        return None, None, None
    except Exception:
        return None, None, None


def get_orderbook_tiers(poly: 'PolymarketClient', token_id: str, fair_price: float, remaining_days: float) -> list[dict]:
    """Получить уровни из ордербука с расчётом APY для каждого."""
    import httpx

    try:
        response = httpx.get(
            f"{poly.host}/book",
            params={"token_id": token_id},
            timeout=30,
        )
        if response.status_code != 200:
            return []

        ob = response.json()
        asks = ob.get("asks", [])

        if not asks:
            return []

        asks = sorted(asks, key=lambda x: float(x.get("price", 1.0)))

        tiers = []
        cumulative = 0.0

        for ask in asks:
            price = float(ask.get("price", 0))
            size = float(ask.get("size", 0))

            if price <= 0 or price >= 1 or size <= 0:
                continue

            size_usd = price * size
            cumulative += size_usd

            roi = fair_price / price - 1 if price > 0 else 0
            apy = roi * (365 / remaining_days) if remaining_days > 0 else 0

            tiers.append({
                "price": price,
                "size_usd": size_usd,
                "cumulative_usd": cumulative,
                "roi": roi,
                "apy": apy,
            })

        return tiers
    except Exception:
        return []


def get_spread_info(poly: 'PolymarketClient', token_id: str) -> dict:
    """
    Получить информацию о спреде для оценки возможности активной торговли.

    Returns:
        dict с полями:
        - best_bid: лучшая цена продажи
        - best_ask: лучшая цена покупки
        - spread: абсолютный спред
        - spread_pct: спред в процентах от ask
        - bid_liquidity: ликвидность на bid
        - ask_liquidity: ликвидность на ask
        - active_trading_ok: рекомендация по активной торговле
    """
    import httpx

    try:
        response = httpx.get(
            f"{poly.host}/book",
            params={"token_id": token_id},
            timeout=30,
        )
        if response.status_code != 200:
            return {}

        ob = response.json()
        asks = ob.get("asks", [])
        bids = ob.get("bids", [])

        if not asks or not bids:
            return {
                "best_bid": 0,
                "best_ask": float(asks[0]["price"]) if asks else 0,
                "spread": 0,
                "spread_pct": 0,
                "bid_liquidity": 0,
                "ask_liquidity": sum(float(a["price"]) * float(a["size"]) for a in asks) if asks else 0,
                "active_trading_ok": False,
                "reason": "Нет bids — только hold to expiry",
            }

        best_ask = min(float(a["price"]) for a in asks)
        best_bid = max(float(b["price"]) for b in bids)
        spread = best_ask - best_bid
        spread_pct = (spread / best_ask * 100) if best_ask > 0 else 0

        ask_liquidity = sum(float(a["price"]) * float(a["size"]) for a in asks if float(a["price"]) == best_ask)
        bid_liquidity = sum(float(b["price"]) * float(b["size"]) for b in bids if float(b["price"]) == best_bid)

        if spread_pct < 5:
            active_trading_ok = True
            reason = "Узкий спред — активная торговля OK"
        elif spread_pct < 10:
            active_trading_ok = True
            reason = "Средний спред — активная торговля возможна"
        else:
            active_trading_ok = False
            reason = f"Широкий спред ({spread_pct:.0f}%) — только hold to expiry"

        return {
            "best_bid": best_bid,
            "best_ask": best_ask,
            "spread": spread,
            "spread_pct": spread_pct,
            "bid_liquidity": bid_liquidity,
            "ask_liquidity": ask_liquidity,
            "active_trading_ok": active_trading_ok,
            "reason": reason,
        }
    except Exception:
        return {}


# ============================================================================
# ANALYSIS
# ============================================================================

def analyze_market(
    event_slug: str,
    config: dict,
    usgs: USGSClient,
    market_prices: dict[str, float],
    integrated_model: IntegratedModel,
    simple_model: SimpleModel,
    recent_events: list[dict] = None,
) -> list[Opportunity]:
    """Анализировать один рынок с интегрированной моделью."""
    opportunities = []
    now = datetime.now(timezone.utc)

    magnitude = config["magnitude"]
    start = config["start"]
    end = config["end"]

    # Получаем текущее количество
    earthquakes = usgs.get_earthquakes(start, now, magnitude)
    current_count = len(earthquakes)

    remaining_days = max(0, (end - now).total_seconds() / 86400)

    if recent_events is None:
        # Получаем недавние M6+ события для ETAS
        recent_events = []
        try:
            recent_quakes = usgs.get_earthquakes(
                now - timedelta(days=30),
                now,
                min(magnitude - 0.5, 6.0),
            )
            recent_events = [
                {"time": q.time, "magnitude": q.magnitude}
                for q in recent_quakes
            ]
        except Exception:
            pass

    if config["type"] == "count":
        for outcome_name, min_k, max_k in config["outcomes"]:
            if outcome_name not in market_prices:
                continue

            # Интегрированная модель
            fair_yes = integrated_model.probability_count(
                min_count=min_k,
                max_count=max_k,
                remaining_days=remaining_days,
                current_count=current_count,
                recent_events=recent_events,
                now=now,
                end_date=end,
            )

            # Простая модель (для сравнения)
            simple_fair_yes = simple_model.probability_count(
                min_count=min_k,
                max_count=max_k,
                remaining_days=remaining_days,
                current_count=current_count,
            )

            mkt_yes = market_prices[outcome_name]
            mkt_no = 1 - mkt_yes

            # Edge для YES
            edge_yes = fair_yes - mkt_yes
            if edge_yes > MIN_EDGE:
                odds = (1 - mkt_yes) / mkt_yes if mkt_yes > 0 else 0
                opportunities.append(Opportunity(
                    event=event_slug,
                    outcome=outcome_name,
                    side="YES",
                    token_id="",
                    fair_price=fair_yes,
                    market_price=mkt_yes,
                    edge=edge_yes,
                    kelly=kelly_criterion(fair_yes, odds),
                    current_count=current_count,
                    lambda_used=integrated_model.annual_rate * (remaining_days / 365),
                    remaining_days=remaining_days,
                    simple_fair_price=simple_fair_yes,
                ))

            # Edge для NO
            fair_no = 1 - fair_yes
            simple_fair_no = 1 - simple_fair_yes
            edge_no = fair_no - mkt_no
            if edge_no > MIN_EDGE:
                odds = (1 - mkt_no) / mkt_no if mkt_no > 0 else 0
                opportunities.append(Opportunity(
                    event=event_slug,
                    outcome=outcome_name,
                    side="NO",
                    token_id="",
                    fair_price=fair_no,
                    market_price=mkt_no,
                    edge=edge_no,
                    kelly=kelly_criterion(fair_no, odds),
                    current_count=current_count,
                    lambda_used=integrated_model.annual_rate * (remaining_days / 365),
                    remaining_days=remaining_days,
                    simple_fair_price=simple_fair_no,
                ))

    elif config["type"] == "binary":
        # Вероятность хотя бы одного события
        fair_yes = integrated_model.probability_at_least_one(
            remaining_days=remaining_days,
            current_count=current_count,
            recent_events=recent_events,
            now=now,
            end_date=end,
        )

        simple_fair_yes = simple_model.probability_at_least_one(
            remaining_days=remaining_days,
            current_count=current_count,
        )

        for outcome_name in ["Yes", "YES"]:
            if outcome_name in market_prices:
                mkt_yes = market_prices[outcome_name]
                mkt_no = 1 - mkt_yes

                edge_yes = fair_yes - mkt_yes
                if edge_yes > MIN_EDGE:
                    odds = (1 - mkt_yes) / mkt_yes if mkt_yes > 0 else 0
                    opportunities.append(Opportunity(
                        event=event_slug,
                        outcome="Yes",
                        side="YES",
                        token_id="",
                        fair_price=fair_yes,
                        market_price=mkt_yes,
                        edge=edge_yes,
                        kelly=kelly_criterion(fair_yes, odds),
                        current_count=current_count,
                        lambda_used=integrated_model.annual_rate * (remaining_days / 365),
                        remaining_days=remaining_days,
                        simple_fair_price=simple_fair_yes,
                    ))

                fair_no = 1 - fair_yes
                simple_fair_no = 1 - simple_fair_yes
                edge_no = fair_no - mkt_no
                if edge_no > MIN_EDGE:
                    odds = (1 - mkt_no) / mkt_no if mkt_no > 0 else 0
                    opportunities.append(Opportunity(
                        event=event_slug,
                        outcome="No",
                        side="NO",
                        token_id="",
                        fair_price=fair_no,
                        market_price=mkt_no,
                        edge=edge_no,
                        kelly=kelly_criterion(fair_no, odds),
                        current_count=current_count,
                        remaining_days=remaining_days,
                        lambda_used=integrated_model.annual_rate * (remaining_days / 365),
                        simple_fair_price=simple_fair_no,
                    ))

    return opportunities


def run_analysis(
    poly: PolymarketClient,
    usgs: USGSClient,
    use_etas: bool = False,  # Отключено: для M7.0+ эффект минимален
    use_bayesian: bool = True,
) -> list[Opportunity]:
    """Запустить анализ всех рынков."""
    all_opportunities = []

    # Получаем данные с Polymarket
    all_prices = poly.get_all_earthquake_prices()

    # Получаем недавние события для ETAS
    now = datetime.now(timezone.utc)
    recent_events = []
    try:
        recent_quakes = usgs.get_earthquakes(now - timedelta(days=30), now, 6.0)
        recent_events = [
            {"time": q.time, "magnitude": q.magnitude}
            for q in recent_quakes
        ]
        print(f"Загружено {len(recent_events)} событий M6.0+ за последние 30 дней для ETAS")
    except Exception as e:
        print(f"Не удалось загрузить события для ETAS: {e}")

    for event_slug, markets in all_prices.items():
        if event_slug not in MARKET_CONFIGS:
            continue

        config = MARKET_CONFIGS[event_slug]
        magnitude = config["magnitude"]

        # Создаём модели для этой магнитуды
        integrated_model = IntegratedModel(
            magnitude=magnitude,
            use_etas=use_etas,
            use_bayesian=use_bayesian,
        )
        simple_model = SimpleModel(magnitude=magnitude)

        # Собираем рыночные цены и condition_ids
        market_prices = {}
        token_ids = {}
        condition_ids = {}

        for market in markets:
            if not market.active:
                continue

            yes_outcome = None
            no_outcome = None
            for outcome in market.outcomes:
                if outcome.outcome_name == "Yes":
                    yes_outcome = outcome
                elif outcome.outcome_name == "No":
                    no_outcome = outcome

            if yes_outcome is None or yes_outcome.closed:
                continue

            q = market.question.lower()

            import re
            for name, _, _ in config.get("outcomes", []):
                matched = False

                if name.endswith("+"):
                    num = name[:-1]
                    matched = bool(re.search(rf'\b{num}\s+or\s+more\b', q))
                elif "-" in name and name[0].isdigit():
                    parts = name.split("-")
                    if len(parts) == 2:
                        matched = bool(re.search(rf'between\s+{parts[0]}\s+and\s+{parts[1]}', q))
                elif name.startswith("<"):
                    num = name[1:]
                    matched = bool(re.search(rf'fewer\s+than\s+{num}\b', q))
                else:
                    matched = bool(re.search(rf'exactly\s+{name}\b', q))

                if matched:
                    market_prices[name] = yes_outcome.yes_price
                    token_ids[(name, "YES")] = yes_outcome.token_id
                    condition_ids[name] = market.condition_id
                    if no_outcome:
                        token_ids[(name, "NO")] = no_outcome.token_id
                    break
            else:
                market_prices[yes_outcome.outcome_name] = yes_outcome.yes_price
                token_ids[(yes_outcome.outcome_name, "YES")] = yes_outcome.token_id
                condition_ids[yes_outcome.outcome_name] = market.condition_id
                if no_outcome:
                    token_ids[("No", "NO")] = no_outcome.token_id

        # Анализируем
        opps = analyze_market(
            event_slug,
            config,
            usgs,
            market_prices,
            integrated_model,
            simple_model,
            recent_events,
        )

        # Добавляем token_id и condition_id
        for opp in opps:
            key = (opp.outcome, opp.side)
            opp.token_id = token_ids.get(key, "")

            if opp.outcome in condition_ids:
                opp.condition_id = condition_ids[opp.outcome]
            elif "Yes" in condition_ids:
                opp.condition_id = condition_ids["Yes"]

        all_opportunities.extend(opps)

    # Получаем реальные цены и ликвидность из ордербука
    print("Проверяю ордербуки...")
    updated_opportunities = []
    for opp in all_opportunities:
        if opp.condition_id:
            outcome_to_check = "Yes" if opp.side == "YES" else "No"
            best_ask, liquidity, token_id = get_orderbook_data(poly, opp.condition_id, outcome_to_check)

            opp.liquidity_usd = liquidity
            if token_id:
                opp.token_id = token_id

            if best_ask is not None and best_ask > 0:
                opp.market_price = best_ask
                opp.edge = opp.fair_price - opp.market_price

                if opp.market_price > 0 and opp.market_price < 1:
                    odds = (1 - opp.market_price) / opp.market_price
                    opp.kelly = kelly_criterion(opp.fair_price, odds)
                else:
                    opp.kelly = 0

        if opp.edge > MIN_EDGE:
            updated_opportunities.append(opp)

    all_opportunities = updated_opportunities

    # Фильтруем по минимальной годовой доходности
    all_opportunities = [
        opp for opp in all_opportunities
        if opp.annual_return >= MIN_ANNUAL_RETURN
    ]

    # Сортируем по годовой доходности
    all_opportunities.sort(key=lambda x: x.annual_return, reverse=True)

    return all_opportunities


# ============================================================================
# PORTFOLIO ALLOCATION
# ============================================================================

def allocate_portfolio(opportunities: list[Opportunity], bankroll: float) -> list[tuple[Opportunity, float]]:
    """Распределить банкролл по возможностям."""
    if not opportunities:
        return []

    best_per_event: dict[str, Opportunity] = {}
    for opp in opportunities:
        if opp.event not in best_per_event or opp.annual_return > best_per_event[opp.event].annual_return:
            best_per_event[opp.event] = opp

    selected = list(best_per_event.values())

    if not selected:
        return []

    scores = []
    for opp in selected:
        score = opp.edge * opp.annual_return
        scores.append((opp, score))

    total_score = sum(s for _, s in scores)
    if total_score <= 0:
        return []

    allocations = []
    remaining = bankroll

    for opp, score in sorted(scores, key=lambda x: x[1], reverse=True):
        base_alloc = bankroll * (score / total_score)

        if opp.liquidity_usd:
            liq_limit = opp.liquidity_usd * MAX_LIQUIDITY_PCT
            base_alloc = min(base_alloc, liq_limit)

        if base_alloc < MIN_BET_USD:
            base_alloc = MIN_BET_USD

        alloc = min(base_alloc, remaining)

        if alloc >= MIN_BET_USD:
            allocations.append((opp, alloc))
            remaining -= alloc

    return allocations


# ============================================================================
# OUTPUT
# ============================================================================

def print_opportunities(
    opportunities: list[Opportunity],
    bankroll: float,
    poly: 'PolymarketClient' = None,
    show_comparison: bool = True,
):
    """Вывести возможности с информацией о доступных инвестициях."""
    print("\n" + "=" * 80)
    print("ТОРГОВЫЕ ВОЗМОЖНОСТИ (ИНТЕГРИРОВАННАЯ МОДЕЛЬ)")
    print("=" * 80)

    if not opportunities:
        print(f"\nНет возможностей с edge > {MIN_EDGE:.0%} и APY > {MIN_ANNUAL_RETURN:.0%}")
        return

    # Группируем по событию, выбираем лучший по APY
    best_per_event: dict[str, Opportunity] = {}
    for opp in opportunities:
        if opp.event not in best_per_event or opp.annual_return > best_per_event[opp.event].annual_return:
            best_per_event[opp.event] = opp

    selected = list(best_per_event.values())
    selected.sort(key=lambda x: (x.annual_return, x.expected_return), reverse=True)

    for i, opp in enumerate(selected, 1):
        prob_win = opp.fair_price
        prob_lose = 1 - prob_win

        url = f"https://polymarket.com/event/{opp.event}"
        print(f"\n{i}. {url}")
        print(f"   BUY {opp.side} на '{opp.outcome}'")
        print(f"   Интегр: {opp.fair_price*100:.1f}%  |  Рынок: {opp.market_price*100:.1f}%  |  Edge: {opp.edge*100:+.1f}%")

        if show_comparison:
            diff = opp.model_diff * 100
            simple_edge = opp.simple_fair_price - opp.market_price
            print(f"   Простая: {opp.simple_fair_price*100:.1f}%  |  Simple Edge: {simple_edge*100:+.1f}%  |  Δ модели: {diff:+.1f}%")

        print(f"   Выигрыш: {prob_win*100:.1f}%  |  Проигрыш: {prob_lose*100:.1f}%  |  Дней: {opp.remaining_days:.0f}")
        print(f"   ROI: {opp.expected_return*100:+.1f}%  |  APY: {opp.annual_return*100:+.0f}%")

        # Показываем уровни из ордербука
        if poly and opp.token_id:
            tiers = get_orderbook_tiers(poly, opp.token_id, opp.fair_price, opp.remaining_days)
            if tiers:
                print(f"   Инвестиции по уровням:")

                apy_groups = {}
                for tier in tiers:
                    apy_rounded = int(tier["apy"] * 100)
                    if apy_rounded not in apy_groups:
                        apy_groups[apy_rounded] = 0
                    apy_groups[apy_rounded] = tier["cumulative_usd"]

                shown = 0
                prev_cumulative = 0
                for apy_pct in sorted(apy_groups.keys(), reverse=True):
                    if apy_pct < MIN_ANNUAL_RETURN * 100:
                        break
                    cumulative = apy_groups[apy_pct]
                    if cumulative > prev_cumulative + 10:
                        print(f"     → ${cumulative:,.0f} с APY {apy_pct}%+")
                        prev_cumulative = cumulative
                        shown += 1
                        if shown >= 5:
                            break

                if tiers:
                    total_available = tiers[-1]["cumulative_usd"]
                    min_apy = tiers[-1]["apy"]
                    if total_available > prev_cumulative and min_apy >= MIN_ANNUAL_RETURN:
                        print(f"     → ${total_available:,.0f} всего (мин APY {int(min_apy * 100)}%)")

            # Показываем информацию о спреде и возможности активной торговли
            spread_info = get_spread_info(poly, opp.token_id)
            if spread_info:
                spread_pct = spread_info.get("spread_pct", 0)
                bid = spread_info.get("best_bid", 0)
                ask = spread_info.get("best_ask", 0)
                bid_liq = spread_info.get("bid_liquidity", 0)
                reason = spread_info.get("reason", "")
                active_ok = spread_info.get("active_trading_ok", False)

                symbol = "✓" if active_ok else "✗"

                print(f"   Спред: {spread_pct:.1f}% (bid: {bid:.2f}, ask: {ask:.2f}, bid liquidity: ${bid_liq:.0f})")
                print(f"   Активная торговля: {symbol} {reason}")

    print(f"\n" + "-" * 80)
    print(f"Всего {len(selected)} возможностей")

    return selected


def save_report_to_markdown(
    opportunities: list[Opportunity],
    poly: 'PolymarketClient',
    model_info: dict,
    output_dir: Path = None,
) -> Path:
    """Сохранить отчёт в markdown файл."""
    if output_dir is None:
        output_dir = Path(__file__).parent / "output"

    output_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    filename = now.strftime("%Y-%m-%d_%H-%M") + "_integrated_UTC.md"
    filepath = output_dir / filename

    lines = []
    lines.append(f"# Earthquake Bot Report (Integrated Model)")
    lines.append(f"")
    lines.append(f"**Время:** {now.strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append(f"")
    lines.append(f"## Параметры модели")
    lines.append(f"")
    lines.append(f"| Параметр | Значение |")
    lines.append(f"|----------|----------|")
    lines.append(f"| Bayesian | {'Да' if model_info.get('use_bayesian') else 'Нет'} |")
    lines.append(f"| ETAS | {'Да' if model_info.get('use_etas') else 'Нет'} |")
    lines.append(f"| α prior (M7.0) | {GAMMA_ALPHA_M7:.1f} |")
    lines.append(f"| β prior (M7.0) | {GAMMA_BETA_M7:.2f} |")
    lines.append(f"| λ mean | {M7_MEAN:.1f}/год |")
    lines.append(f"| λ std | {M7_STD:.1f} |")
    lines.append(f"")
    lines.append(f"---")
    lines.append(f"")
    lines.append(f"## Торговые возможности")
    lines.append(f"")

    if not opportunities:
        lines.append(f"Нет возможностей с edge > {MIN_EDGE:.0%} и APY > {MIN_ANNUAL_RETURN:.0%}")
    else:
        for i, opp in enumerate(opportunities, 1):
            prob_win = opp.fair_price
            prob_lose = 1 - prob_win

            lines.append(f"### {i}. {opp.event}")
            lines.append(f"")
            lines.append(f"**Ссылка:** https://polymarket.com/event/{opp.event}")
            lines.append(f"")
            lines.append(f"| Параметр | Интегр. модель | Простая модель |")
            lines.append(f"|----------|----------------|----------------|")
            lines.append(f"| Позиция | BUY {opp.side} на '{opp.outcome}' | - |")
            lines.append(f"| Fair Price | {opp.fair_price*100:.1f}% | {opp.simple_fair_price*100:.1f}% |")
            lines.append(f"| Рынок | {opp.market_price*100:.1f}% | - |")
            lines.append(f"| Edge | {opp.edge*100:+.1f}% | {(opp.simple_fair_price - opp.market_price)*100:+.1f}% |")
            lines.append(f"| Δ модели | {opp.model_diff*100:+.1f}% | - |")
            lines.append(f"| Дней | {opp.remaining_days:.0f} | - |")
            lines.append(f"| ROI | {opp.expected_return*100:+.1f}% | - |")
            lines.append(f"| APY | {opp.annual_return*100:+.0f}% | - |")
            lines.append(f"")

            # Уровни из ордербука
            if poly and opp.token_id:
                tiers = get_orderbook_tiers(poly, opp.token_id, opp.fair_price, opp.remaining_days)
                if tiers:
                    lines.append(f"**Инвестиции по уровням:**")
                    lines.append(f"")
                    lines.append(f"| Сумма | Мин APY |")
                    lines.append(f"|-------|---------|")

                    apy_groups = {}
                    for tier in tiers:
                        apy_rounded = int(tier["apy"] * 100)
                        if apy_rounded not in apy_groups:
                            apy_groups[apy_rounded] = 0
                        apy_groups[apy_rounded] = tier["cumulative_usd"]

                    shown = 0
                    prev_cumulative = 0
                    for apy_pct in sorted(apy_groups.keys(), reverse=True):
                        if apy_pct < MIN_ANNUAL_RETURN * 100:
                            break
                        cumulative = apy_groups[apy_pct]
                        if cumulative > prev_cumulative + 10:
                            lines.append(f"| ${cumulative:,.0f} | {apy_pct}%+ |")
                            prev_cumulative = cumulative
                            shown += 1
                            if shown >= 5:
                                break

                    lines.append(f"")

    lines.append(f"---")
    lines.append(f"")
    lines.append(f"*Всего {len(opportunities)} возможностей*")

    content = "\n".join(lines)
    filepath.write_text(content, encoding="utf-8")

    return filepath


def execute_trade(
    poly: PolymarketClient,
    opp: Opportunity,
    amount: float,
    debug: bool = True,
) -> bool:
    """Выполнить сделку."""
    print(f"\n{'=' * 50}")
    print(f"СДЕЛКА: BUY {opp.side} ${amount:.2f}")
    print(f"Рынок: {opp.event}")
    print(f"Исход: {opp.outcome}")
    print(f"Edge: {opp.edge*100:+.1f}%")
    print(f"Token ID: {opp.token_id[:30]}...")
    print(f"{'=' * 50}")

    if debug:
        confirm = input("\nПодтвердить? [y/n]: ").strip().lower()
        if confirm != "y":
            print("Отменено.")
            return False

    if not opp.token_id:
        print("Ошибка: token_id не найден")
        return False

    try:
        result = poly.create_market_order(
            token_id=opp.token_id,
            side="BUY",
            amount=amount,
        )
        print(f"Успешно! Order ID: {result.get('orderID', 'N/A')}")
        return True
    except Exception as e:
        print(f"Ошибка: {e}")
        return False


# ============================================================================
# MAIN
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="Earthquake Trading Bot (Integrated Model)")
    parser.add_argument("--debug", action="store_true", help="Режим отладки")
    parser.add_argument("--auto", action="store_true", help="Автоматическая торговля")
    parser.add_argument("--bankroll", type=float, default=230.0, help="Банкролл в USD")
    parser.add_argument("--compare", action="store_true", help="Показать сравнение с простой моделью")
    parser.add_argument("--no-etas", action="store_true", help="Отключить ETAS-коррекцию")
    parser.add_argument("--no-bayesian", action="store_true", help="Отключить Bayesian (использовать точечную оценку)")
    args = parser.parse_args()

    use_etas = not args.no_etas
    use_bayesian = not args.no_bayesian

    print("=" * 80)
    print("EARTHQUAKE TRADING BOT (INTEGRATED MODEL)")
    print("=" * 80)
    print(f"Время: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Режим: {'AUTO' if args.auto else ('DEBUG' if args.debug else 'ANALYSIS')}")
    print(f"Min Edge: {MIN_EDGE*100:.0f}%  |  Min APY: {MIN_ANNUAL_RETURN*100:.0f}%")
    print()
    # Реальный статус компонентов (с учётом доступности библиотек)
    bayesian_active = use_bayesian and HAS_SCIPY

    print("Компоненты модели:")
    if use_bayesian and not HAS_SCIPY:
        print(f"  • Bayesian Poisson: ВЫКЛ (scipy не установлен, pip install scipy)")
    else:
        print(f"  • Bayesian Poisson: {'ВКЛ' if bayesian_active else 'ВЫКЛ'}")

    print(f"  • ETAS-коррекция:   {'ВКЛ' if use_etas else 'ВЫКЛ'}")
    print()
    print(f"Исторические данные M7.0+ (1900-2024):")
    print(f"  • Среднее: {M7_MEAN:.1f}/год  |  Std: {M7_STD:.1f}  |  Min: {M7_MIN}  |  Max: {M7_MAX}")
    print(f"  • Gamma prior: α={GAMMA_ALPHA_M7:.1f}, β={GAMMA_BETA_M7:.2f}")

    # Инициализация
    poly = PolymarketClient()
    usgs = USGSClient()

    # Анализ
    print("\nАнализирую рынки...")
    opportunities = run_analysis(
        poly, usgs,
        use_etas=use_etas,
        use_bayesian=use_bayesian,
    )

    # Вывод возможностей
    selected = print_opportunities(
        opportunities,
        args.bankroll,
        poly,
        show_comparison=args.compare or True,  # Всегда показываем сравнение
    )

    # Сохраняем отчёт в markdown
    if selected:
        model_info = {
            "use_bayesian": use_bayesian,
            "use_etas": use_etas,
        }
        report_path = save_report_to_markdown(selected, poly, model_info)
        print(f"\nОтчёт сохранён: {report_path}")

    # Торговля
    if (args.debug or args.auto) and opportunities:
        allocations = allocate_portfolio(opportunities, args.bankroll)

        if allocations:
            print("\n" + "=" * 80)
            print("ТОРГОВЛЯ")
            print("=" * 80)

            for opp, bet_size in allocations:
                if args.auto:
                    execute_trade(poly, opp, bet_size, debug=False)
                elif args.debug:
                    execute_trade(poly, opp, bet_size, debug=True)

    print("\n" + "=" * 80)
    print("Готово!")


if __name__ == "__main__":
    main()
