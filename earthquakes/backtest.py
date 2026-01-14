#!/usr/bin/env python3
"""
Бэктест моделей прогнозирования землетрясений.

Сравнивает три модели на исторических данных USGS (1973+):
1. Простая модель (Poisson)
2. Интегрированная модель (Bayesian + ETAS)
3. Консенсусная модель (комбинация)

Периоды прогнозирования:
- 2 недели (14 дней)
- 1 месяц (30 дней)
- 1 квартал (91 день)
- 6 месяцев (182 дня)
- 1 год (365 дней)

Магнитуды:
- M7.0+
- M8.0+
- M9.0+

Два режима тестирования:
1. Интервалы (по умолчанию) - тестирует P(min <= X <= max) как на Polymarket
2. Пороги (--thresholds) - тестирует P(X >= N) для разных N

Использование:
    python backtest.py                    # Бэктест с интервалами (по умолчанию)
    python backtest.py --thresholds       # Бэктест с порогами (>=N)
    python backtest.py --start 1990       # С 1990 года
    python backtest.py --magnitude 8.0    # Только M8.0+
    python backtest.py --period 365       # Только годовой прогноз

Примеры:
    # Полный бэктест с интервалами Polymarket (по умолчанию)
    python backtest.py

    # Только M7.0+ за год с интервалами
    python backtest.py --magnitude 7.0 --period 365

    # Бэктест с порогами (старый режим)
    python backtest.py --thresholds
"""

import argparse
import json
import math
import sys
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import statistics
from concurrent.futures import ProcessPoolExecutor, as_completed
import multiprocessing

# Для прогресс-бара
try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False
    def tqdm(iterable, **kwargs):
        """Заглушка без прогресс-бара."""
        desc = kwargs.get("desc", "")
        if desc:
            print(f"  {desc}...", end=" ", flush=True)
        return iterable

# Для Negative Binomial
try:
    from scipy import stats
    from scipy.special import gammaln
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False
    def gammaln(x):
        """Логарифм гамма-функции (приближение Стирлинга)."""
        if x <= 0:
            return float('inf')
        return (x - 0.5) * math.log(x) - x + 0.5 * math.log(2 * math.pi)

# Для HTTP запросов
try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False
    import urllib.request
    import urllib.parse


# ============================================================================
# КОНФИГУРАЦИЯ
# ============================================================================

# Периоды прогнозирования (дни)
FORECAST_PERIODS = {
    "2_weeks": 14,
    "1_month": 30,
    "1_quarter": 91,
    "6_months": 182,
    "1_year": 365,
}

# Магнитуды для тестирования
MAGNITUDES = [7.0, 8.0, 9.0]

# Годовые частоты (справочные)
ANNUAL_RATES = {
    7.0: 15.0,
    8.0: 1.0,
    9.0: 0.04,  # ~5 событий за 124 года
}


# ============================================================================
# ИСТОРИЧЕСКИЕ ДАННЫЕ (для Bayesian prior)
# ============================================================================

M7_HISTORICAL_COUNTS = {
    1973: 13, 1974: 14, 1975: 14, 1976: 15, 1977: 11, 1978: 14, 1979: 13,
    1980: 14, 1981: 11, 1982: 12, 1983: 14, 1984: 11, 1985: 13, 1986: 14,
    1987: 11, 1988: 12, 1989: 14, 1990: 12, 1991: 11, 1992: 13, 1993: 12,
    1994: 13, 1995: 18, 1996: 14, 1997: 16, 1998: 12, 1999: 18, 2000: 14,
    2001: 15, 2002: 13, 2003: 14, 2004: 16, 2005: 14, 2006: 15, 2007: 17,
    2008: 12, 2009: 16, 2010: 23, 2011: 19, 2012: 12, 2013: 17, 2014: 11,
    2015: 18, 2016: 16, 2017: 7, 2018: 16, 2019: 13, 2020: 9, 2021: 16,
    2022: 11, 2023: 18, 2024: 14,
}

M8_HISTORICAL_COUNTS = {
    2000: 1, 2001: 1, 2002: 0, 2003: 1, 2004: 2, 2005: 2, 2006: 2, 2007: 4,
    2008: 0, 2009: 1, 2010: 2, 2011: 1, 2012: 2, 2013: 2, 2014: 1, 2015: 1,
    2016: 0, 2017: 1, 2018: 1, 2019: 0, 2020: 0, 2021: 2, 2022: 0, 2023: 0,
    2024: 1,
}

# M9.0+ события (очень редкие - только годы с событиями)
# 1952: Kamchatka M9.0
# 1960: Chile M9.5
# 1964: Alaska M9.2
# 2004: Sumatra M9.1
# 2011: Tohoku M9.1
M9_HISTORICAL_EVENTS = [
    (1952, 11, 4),   # Kamchatka
    (1960, 5, 22),   # Chile
    (1964, 3, 27),   # Alaska
    (2004, 12, 26),  # Sumatra
    (2011, 3, 11),   # Tohoku
]
M9_ANNUAL_RATE = 5 / 124  # ~0.04


# ============================================================================
# ЗАГРУЗКА ДАННЫХ USGS
# ============================================================================

@dataclass
class Earthquake:
    """Землетрясение."""
    time: datetime
    magnitude: float
    place: str
    latitude: float
    longitude: float
    depth: float
    id: str


def fetch_usgs_data(
    start_date: datetime,
    end_date: datetime,
    min_magnitude: float,
) -> list[Earthquake]:
    """
    Загрузить данные из USGS API.

    USGS API: https://earthquake.usgs.gov/fdsnws/event/1/
    """
    earthquakes = []

    # USGS API ограничивает запросы, делаем по годам
    current = start_date
    while current < end_date:
        year_end = min(
            datetime(current.year + 1, 1, 1, tzinfo=timezone.utc),
            end_date
        )

        params = {
            "format": "geojson",
            "starttime": current.strftime("%Y-%m-%d"),
            "endtime": year_end.strftime("%Y-%m-%d"),
            "minmagnitude": str(min_magnitude),
            "orderby": "time",
        }

        url = "https://earthquake.usgs.gov/fdsnws/event/1/query?" + "&".join(
            f"{k}={v}" for k, v in params.items()
        )

        try:
            if HAS_HTTPX:
                response = httpx.get(url, timeout=60)
                data = response.json()
            else:
                with urllib.request.urlopen(url, timeout=60) as resp:
                    data = json.loads(resp.read().decode())

            for feature in data.get("features", []):
                props = feature.get("properties", {})
                coords = feature.get("geometry", {}).get("coordinates", [0, 0, 0])

                time_ms = props.get("time", 0)
                eq_time = datetime.fromtimestamp(time_ms / 1000, tz=timezone.utc)

                earthquakes.append(Earthquake(
                    time=eq_time,
                    magnitude=float(props.get("mag", 0)),
                    place=props.get("place", ""),
                    latitude=coords[1] if len(coords) > 1 else 0,
                    longitude=coords[0] if len(coords) > 0 else 0,
                    depth=coords[2] if len(coords) > 2 else 0,
                    id=props.get("code", ""),
                ))

            print(f"  {current.year}: загружено {len(data.get('features', []))} событий M{min_magnitude}+")

        except Exception as e:
            print(f"  {current.year}: ошибка загрузки - {e}")

        current = year_end

    # Сортируем по времени
    earthquakes.sort(key=lambda x: x.time)

    return earthquakes


def load_or_fetch_data(
    magnitude: float,
    start_year: int = 1973,
    end_year: int = 2024,
    cache_dir: Path = None,
) -> list[Earthquake]:
    """
    Загрузить данные из кэша или USGS API.
    """
    if cache_dir is None:
        cache_dir = Path(__file__).parent / "history" / "usgs"

    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"m{magnitude:.1f}_global_{start_year}_{end_year}.json"

    if cache_file.exists():
        print(f"Загружаю из кэша: {cache_file}")
        with open(cache_file, "r") as f:
            data = json.load(f)

        earthquakes = []
        for item in data:
            eq_time = datetime.fromisoformat(item["time"].replace("Z", "+00:00"))
            earthquakes.append(Earthquake(
                time=eq_time,
                magnitude=item["magnitude"],
                place=item.get("place", ""),
                latitude=item.get("latitude", 0),
                longitude=item.get("longitude", 0),
                depth=item.get("depth", 0),
                id=item.get("id", ""),
            ))

        print(f"Загружено {len(earthquakes)} событий M{magnitude}+ ({start_year}-{end_year})")
        return earthquakes

    print(f"Загружаю данные M{magnitude}+ с USGS ({start_year}-{end_year})...")

    start_date = datetime(start_year, 1, 1, tzinfo=timezone.utc)
    end_date = datetime(end_year + 1, 1, 1, tzinfo=timezone.utc)

    earthquakes = fetch_usgs_data(start_date, end_date, magnitude)

    # Сохраняем в кэш
    cache_data = [
        {
            "time": eq.time.isoformat(),
            "magnitude": eq.magnitude,
            "place": eq.place,
            "latitude": eq.latitude,
            "longitude": eq.longitude,
            "depth": eq.depth,
            "id": eq.id,
        }
        for eq in earthquakes
    ]

    with open(cache_file, "w") as f:
        json.dump(cache_data, f, indent=2)

    print(f"Сохранено в кэш: {cache_file}")

    return earthquakes


# ============================================================================
# МОДЕЛИ
# ============================================================================

def negative_binomial_pmf(k: int, r: float, p: float) -> float:
    """PMF Negative Binomial."""
    if k < 0 or r <= 0 or p <= 0 or p > 1:
        return 0.0
    log_coeff = gammaln(k + r) - gammaln(k + 1) - gammaln(r)
    log_prob = r * math.log(p) + k * math.log(1 - p)
    return math.exp(log_coeff + log_prob)


def negative_binomial_cdf(k: int, r: float, p: float) -> float:
    """CDF Negative Binomial."""
    if HAS_SCIPY:
        return stats.nbinom.cdf(k, r, p)
    return sum(negative_binomial_pmf(i, r, p) for i in range(k + 1))


def poisson_pmf(k: int, lam: float) -> float:
    """PMF Poisson."""
    if k < 0 or lam <= 0:
        return 0.0
    return (lam ** k) * math.exp(-lam) / math.factorial(k)


def poisson_cdf(k: int, lam: float) -> float:
    """CDF Poisson."""
    return sum(poisson_pmf(i, lam) for i in range(k + 1))


class SimpleModel:
    """Простая модель Пуассона."""

    def __init__(self, magnitude: float):
        self.magnitude = magnitude
        self.annual_rate = ANNUAL_RATES.get(magnitude, 15.0)

    def predict_at_least(
        self,
        min_count: int,
        period_days: float,
        current_count: int = 0,
        **kwargs,
    ) -> float:
        """P(X >= min_count) за период."""
        if current_count >= min_count:
            return 1.0

        lam = self.annual_rate * (period_days / 365.0)
        additional_needed = min_count - current_count

        # P(X >= n) = 1 - P(X < n) = 1 - CDF(n-1)
        return 1.0 - poisson_cdf(additional_needed - 1, lam)

    def predict_range(
        self,
        min_count: int,
        max_count: Optional[int],
        period_days: float,
        current_count: int = 0,
        **kwargs,
    ) -> float:
        """P(min_count <= X <= max_count) за период."""
        lam = self.annual_rate * (period_days / 365.0)

        min_additional = max(0, min_count - current_count)

        if max_count is None:
            return 1.0 - poisson_cdf(min_additional - 1, lam) if min_additional > 0 else 1.0

        max_additional = max_count - current_count
        if max_additional < 0:
            return 0.0

        prob = poisson_cdf(max_additional, lam)
        if min_additional > 0:
            prob -= poisson_cdf(min_additional - 1, lam)

        return max(0.0, prob)


class IntegratedModel:
    """Интегрированная модель (Bayesian + ETAS)."""

    def __init__(
        self,
        magnitude: float,
        use_bayesian: bool = True,
        use_etas: bool = False,  # Отключено: для M7.0+ эффект минимален
    ):
        self.magnitude = magnitude
        self.use_etas = use_etas

        # Выбираем исторические данные
        if magnitude >= 9.0:
            # M9.0+ — слишком редкие для Bayesian
            self.historical_counts = None
            self.annual_rate = M9_ANNUAL_RATE
            self.use_bayesian = False  # Отключаем для редких событий
            self.beta_prior = 1.0
            self.alpha_prior = self.annual_rate
        elif magnitude >= 8.0:
            self.historical_counts = M8_HISTORICAL_COUNTS
            counts = list(M8_HISTORICAL_COUNTS.values())
            self.annual_rate = statistics.mean(counts) if counts else ANNUAL_RATES.get(magnitude, 1.0)
            self.use_bayesian = use_bayesian and HAS_SCIPY
        else:
            self.historical_counts = M7_HISTORICAL_COUNTS
            counts = list(M7_HISTORICAL_COUNTS.values())
            self.annual_rate = statistics.mean(counts) if counts else ANNUAL_RATES.get(magnitude, 15.0)
            self.use_bayesian = use_bayesian and HAS_SCIPY

        # Вычисляем prior параметры для Bayesian
        if self.historical_counts is not None:
            counts = list(self.historical_counts.values())
            if len(counts) >= 2:
                var = statistics.variance(counts)
                if var > 0:
                    self.beta_prior = self.annual_rate / var
                    self.alpha_prior = self.annual_rate * self.beta_prior
                else:
                    self.beta_prior = 1.0
                    self.alpha_prior = self.annual_rate
            else:
                self.beta_prior = 1.0
                self.alpha_prior = self.annual_rate

    def _get_bayesian_params(
        self,
        forecast_date: datetime,
        lookback_years: int = 5,
    ) -> tuple[float, float, float]:
        """Получить posterior параметры на основе данных до forecast_date."""
        if not self.use_bayesian:
            return self.alpha_prior, self.beta_prior, self.annual_rate

        # Считаем события за последние lookback_years лет
        observed_count = 0
        observed_years = 0

        for year in range(forecast_date.year - lookback_years, forecast_date.year):
            if year in self.historical_counts:
                observed_count += self.historical_counts[year]
                observed_years += 1

        if observed_years == 0:
            return self.alpha_prior, self.beta_prior, self.annual_rate

        alpha_post = self.alpha_prior + observed_count
        beta_post = self.beta_prior + observed_years
        lambda_mean = alpha_post / beta_post

        return alpha_post, beta_post, lambda_mean

    def _etas_boost(
        self,
        recent_events: list[Earthquake],
        forecast_date: datetime,
    ) -> float:
        """ETAS-коррекция на основе недавних событий."""
        if not self.use_etas or not recent_events:
            return 0.0

        boost = 0.0
        c = 0.01
        p = 1.1
        alpha = 0.8
        Mc = self.magnitude - 0.5

        for eq in recent_events:
            days_ago = (forecast_date - eq.time).total_seconds() / 86400

            if days_ago < 0 or days_ago > 30:
                continue

            if eq.magnitude < Mc:
                continue

            productivity = 10 ** (alpha * (eq.magnitude - Mc))
            omori_factor = 1 / ((days_ago + c) ** p)
            boost += productivity * omori_factor * 0.01

        return boost

    def predict_at_least(
        self,
        min_count: int,
        period_days: float,
        current_count: int = 0,
        forecast_date: datetime = None,
        recent_events: list[Earthquake] = None,
        **kwargs,
    ) -> float:
        """P(X >= min_count) за период."""
        if current_count >= min_count:
            return 1.0

        if forecast_date is None:
            forecast_date = datetime.now(timezone.utc)
        if recent_events is None:
            recent_events = []

        end_date = forecast_date + timedelta(days=period_days)

        # Bayesian posterior
        alpha_post, beta_post, lambda_mean = self._get_bayesian_params(forecast_date)

        # ETAS
        etas_lambda = self._etas_boost(recent_events, forecast_date)

        # Корректируем параметры
        remaining_years = period_days / 365.0
        effective_beta = beta_post / (1 + etas_lambda / max(lambda_mean, 0.1))

        r = alpha_post
        p = effective_beta / (effective_beta + remaining_years)

        if p <= 0 or p >= 1:
            # Fallback на Poisson
            lam = lambda_mean * remaining_years
            additional_needed = min_count - current_count
            return 1.0 - poisson_cdf(additional_needed - 1, lam)

        additional_needed = min_count - current_count
        if additional_needed <= 0:
            return 1.0

        return 1.0 - negative_binomial_cdf(additional_needed - 1, r, p)

    def predict_range(
        self,
        min_count: int,
        max_count: Optional[int],
        period_days: float,
        current_count: int = 0,
        forecast_date: datetime = None,
        recent_events: list[Earthquake] = None,
        **kwargs,
    ) -> float:
        """P(min_count <= X <= max_count) за период."""
        if forecast_date is None:
            forecast_date = datetime.now(timezone.utc)
        if recent_events is None:
            recent_events = []

        end_date = forecast_date + timedelta(days=period_days)

        # Bayesian posterior
        alpha_post, beta_post, lambda_mean = self._get_bayesian_params(forecast_date)

        # ETAS
        etas_lambda = self._etas_boost(recent_events, forecast_date)

        # Корректируем параметры
        remaining_years = period_days / 365.0
        effective_beta = beta_post / (1 + etas_lambda / max(lambda_mean, 0.1))

        r = alpha_post
        p = effective_beta / (effective_beta + remaining_years)

        min_additional = max(0, min_count - current_count)

        if p <= 0 or p >= 1:
            # Fallback на Poisson
            lam = lambda_mean * remaining_years
            if max_count is None:
                return 1.0 - poisson_cdf(min_additional - 1, lam) if min_additional > 0 else 1.0
            max_additional = max_count - current_count
            if max_additional < 0:
                return 0.0
            prob = poisson_cdf(max_additional, lam)
            if min_additional > 0:
                prob -= poisson_cdf(min_additional - 1, lam)
            return max(0.0, prob)

        if max_count is None:
            if min_additional <= 0:
                return 1.0
            return 1.0 - negative_binomial_cdf(min_additional - 1, r, p)

        max_additional = max_count - current_count
        if max_additional < 0:
            return 0.0
        if max_additional < min_additional:
            return 0.0

        prob = negative_binomial_cdf(max_additional, r, p)
        if min_additional > 0:
            prob -= negative_binomial_cdf(min_additional - 1, r, p)

        return max(0.0, min(1.0, prob))


class ConsensusModel:
    """Консенсусная модель (комбинация простой и интегрированной)."""

    def __init__(self, magnitude: float):
        self.magnitude = magnitude
        self.simple = SimpleModel(magnitude)
        self.integrated = IntegratedModel(magnitude)

    def predict_at_least(
        self,
        min_count: int,
        period_days: float,
        current_count: int = 0,
        **kwargs,
    ) -> tuple[float, str]:
        """
        P(X >= min_count) с консенсусом.

        Returns:
            (probability, consensus_type)
            consensus_type: "agree", "weak", "disagree"
        """
        simple_prob = self.simple.predict_at_least(min_count, period_days, current_count)
        integrated_prob = self.integrated.predict_at_least(min_count, period_days, current_count, **kwargs)

        # Определяем консенсус
        diff = abs(integrated_prob - simple_prob)

        # Для M7.0+ доверяем интегрированной
        if self.magnitude < 8.0:
            return integrated_prob, "primary"

        # Для M8.0+ проверяем согласие
        if diff < 0.05:
            return (integrated_prob + simple_prob) / 2, "agree"
        elif diff < 0.15:
            return integrated_prob, "weak"
        else:
            return integrated_prob, "disagree"

    def predict_range(
        self,
        min_count: int,
        max_count: Optional[int],
        period_days: float,
        current_count: int = 0,
        **kwargs,
    ) -> tuple[float, str]:
        """P(min_count <= X <= max_count) с консенсусом."""
        simple_prob = self.simple.predict_range(min_count, max_count, period_days, current_count)
        integrated_prob = self.integrated.predict_range(min_count, max_count, period_days, current_count, **kwargs)

        diff = abs(integrated_prob - simple_prob)

        if self.magnitude < 8.0:
            return integrated_prob, "primary"

        if diff < 0.05:
            return (integrated_prob + simple_prob) / 2, "agree"
        elif diff < 0.15:
            return integrated_prob, "weak"
        else:
            return integrated_prob, "disagree"


# ============================================================================
# МЕТРИКИ
# ============================================================================

@dataclass
class ForecastResult:
    """Результат одного прогноза (порог >=N)."""
    forecast_date: datetime
    end_date: datetime
    period_days: int
    magnitude: float
    threshold: int  # Минимальное количество для прогноза

    simple_prob: float
    integrated_prob: float
    consensus_prob: float
    consensus_type: str

    actual_count: int
    outcome: bool  # True если actual >= threshold

    @property
    def simple_error(self) -> float:
        """Ошибка простой модели: |prob - outcome|"""
        return abs(self.simple_prob - (1.0 if self.outcome else 0.0))

    @property
    def integrated_error(self) -> float:
        """Ошибка интегрированной модели."""
        return abs(self.integrated_prob - (1.0 if self.outcome else 0.0))

    @property
    def consensus_error(self) -> float:
        """Ошибка консенсусной модели."""
        return abs(self.consensus_prob - (1.0 if self.outcome else 0.0))


@dataclass
class ForecastResultInterval:
    """Результат одного прогноза (интервал [min, max])."""
    forecast_date: datetime
    end_date: datetime
    period_days: int
    magnitude: float

    interval_name: str  # Название интервала (напр. "14-16", "8+")
    interval_min: int   # Минимум интервала
    interval_max: Optional[int]  # Максимум (None = без верхней границы)

    simple_prob: float
    integrated_prob: float
    consensus_prob: float
    consensus_type: str

    actual_count: int
    outcome: bool  # True если actual попадает в интервал

    @property
    def simple_error(self) -> float:
        return abs(self.simple_prob - (1.0 if self.outcome else 0.0))

    @property
    def integrated_error(self) -> float:
        return abs(self.integrated_prob - (1.0 if self.outcome else 0.0))

    @property
    def consensus_error(self) -> float:
        return abs(self.consensus_prob - (1.0 if self.outcome else 0.0))


@dataclass
class MetricsSummary:
    """Сводка метрик для модели."""
    model_name: str
    n_forecasts: int

    # Brier Score (ниже = лучше, 0 = идеально)
    brier_score: float

    # Mean Absolute Error вероятности
    mae: float

    # Калибровка (reliability)
    calibration_bins: list[tuple[float, float, int]]  # (predicted, actual, count)

    # Accuracy (при пороге 0.5)
    accuracy: float

    # Log loss (ниже = лучше)
    log_loss: float


def calculate_metrics(
    results: list,  # ForecastResult или ForecastResultInterval
    model: str,  # "simple", "integrated", "consensus"
) -> MetricsSummary:
    """Рассчитать метрики для модели."""
    if not results:
        return MetricsSummary(
            model_name=model,
            n_forecasts=0,
            brier_score=0.0,
            mae=0.0,
            calibration_bins=[],
            accuracy=0.0,
            log_loss=0.0,
        )

    # Получаем вероятности по модели
    if model == "simple":
        probs = [r.simple_prob for r in results]
    elif model == "integrated":
        probs = [r.integrated_prob for r in results]
    else:
        probs = [r.consensus_prob for r in results]

    outcomes = [1.0 if r.outcome else 0.0 for r in results]

    n = len(results)

    # Brier Score = mean((prob - outcome)^2)
    brier = sum((p - o) ** 2 for p, o in zip(probs, outcomes)) / n

    # MAE = mean(|prob - outcome|)
    mae = sum(abs(p - o) for p, o in zip(probs, outcomes)) / n

    # Accuracy (порог 0.5)
    correct = sum(1 for p, o in zip(probs, outcomes) if (p >= 0.5) == (o >= 0.5))
    accuracy = correct / n

    # Log Loss = -mean(outcome * log(prob) + (1-outcome) * log(1-prob))
    eps = 1e-10
    log_loss_sum = 0.0
    for p, o in zip(probs, outcomes):
        p_clipped = max(eps, min(1 - eps, p))
        log_loss_sum += -(o * math.log(p_clipped) + (1 - o) * math.log(1 - p_clipped))
    log_loss = log_loss_sum / n

    # Калибровка (группируем по бинам вероятности)
    bins = [(0.0, 0.1), (0.1, 0.2), (0.2, 0.3), (0.3, 0.4), (0.4, 0.5),
            (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.0)]

    calibration = []
    for low, high in bins:
        bin_probs = []
        bin_outcomes = []
        for p, o in zip(probs, outcomes):
            if low <= p < high or (high == 1.0 and p == 1.0):
                bin_probs.append(p)
                bin_outcomes.append(o)

        if bin_probs:
            avg_pred = sum(bin_probs) / len(bin_probs)
            avg_actual = sum(bin_outcomes) / len(bin_outcomes)
            calibration.append((avg_pred, avg_actual, len(bin_probs)))

    return MetricsSummary(
        model_name=model,
        n_forecasts=n,
        brier_score=brier,
        mae=mae,
        calibration_bins=calibration,
        accuracy=accuracy,
        log_loss=log_loss,
    )


# ============================================================================
# БЭКТЕСТ
# ============================================================================

def get_thresholds_for_period(magnitude: float, period_days: float) -> list[int]:
    """
    Получить список порогов для тестирования.

    Пороги выбираются так, чтобы вероятность была в диапазоне ~10-90%.
    """
    # Ожидаемое количество событий за период
    annual_rate = ANNUAL_RATES.get(magnitude, 15.0)
    expected = annual_rate * (period_days / 365.0)

    # Пороги вокруг ожидаемого значения
    if magnitude >= 9.0:
        # M9.0+ очень редкие
        return [1]
    elif magnitude >= 8.0:
        # M8.0+ примерно 1/год
        if period_days <= 30:
            return [1]
        elif period_days <= 91:
            return [1, 2]
        else:
            return [1, 2, 3]
    else:
        # M7.0+ примерно 15/год
        thresholds = []
        # Добавляем пороги от expected-3σ до expected+3σ
        std = math.sqrt(expected)  # Для Пуассона std ≈ sqrt(λ)

        low = max(1, int(expected - 2 * std))
        high = int(expected + 2 * std) + 1

        # Не более 10 порогов, шаг зависит от диапазона
        step = max(1, (high - low) // 8)

        for t in range(low, high + 1, step):
            thresholds.append(t)

        # Убедимся что есть хотя бы 3 порога
        if len(thresholds) < 3:
            thresholds = list(range(max(1, int(expected) - 1), int(expected) + 3))

        return thresholds[:10]  # Максимум 10


def get_market_ranges_for_period(magnitude: float, period_days: float) -> list[tuple[str, int, Optional[int]]]:
    """
    Получить интервалы как на реальном рынке Polymarket.

    Returns:
        Список кортежей (название, min, max) где max=None означает "и больше"
    """
    if magnitude >= 9.0:
        # M9.0+ - бинарный (будет/не будет)
        return [("1+", 1, None)]

    elif magnitude >= 8.0:
        # M8.0+ за разные периоды
        if period_days <= 45:  # ~месяц
            return [("1+", 1, None)]
        elif period_days <= 100:  # ~квартал
            return [("1", 1, 1), ("2+", 2, None)]
        elif period_days <= 200:  # ~полгода
            return [("1", 1, 1), ("2", 2, 2), ("3+", 3, None)]
        else:  # год
            return [("0", 0, 0), ("1", 1, 1), ("2", 2, 2), ("3+", 3, None)]

    else:
        # M7.0+ - интервалы как на Polymarket
        if period_days <= 45:  # ~месяц
            return [
                ("0", 0, 0), ("1", 1, 1), ("2", 2, 2), ("3+", 3, None)
            ]
        elif period_days <= 100:  # ~квартал
            return [
                ("0-1", 0, 1), ("2-3", 2, 3), ("4-5", 4, 5), ("6+", 6, None)
            ]
        elif period_days <= 200:  # ~полгода (как how-many-7pt0-by-june-30)
            return [
                ("2", 2, 2), ("3", 3, 3), ("4", 4, 4), ("5", 5, 5),
                ("6", 6, 6), ("7", 7, 7), ("8+", 8, None)
            ]
        else:  # год (как how-many-7pt0-in-2026)
            return [
                ("<5", 0, 4), ("5-7", 5, 7), ("8-10", 8, 10), ("11-13", 11, 13),
                ("14-16", 14, 16), ("17-19", 17, 19), ("20+", 20, None)
            ]


def _process_single_forecast(args: tuple) -> list[ForecastResult]:
    """
    Обработать один прогноз для ВСЕХ порогов (для параллельного выполнения).

    Возвращает список результатов — по одному на каждый threshold.
    """
    (forecast_date, period_days, magnitude,
     eq_times_mags, historical_m7, historical_m8) = args

    # Пересоздаём модели в процессе (они не сериализуются)
    simple = SimpleModel(magnitude)
    integrated = IntegratedModel(magnitude)
    consensus = ConsensusModel(magnitude)

    forecast_end = forecast_date + timedelta(days=period_days)

    # Получаем недавние события (последние 30 дней)
    recent_start = forecast_date - timedelta(days=30)
    recent_events = [
        Earthquake(
            time=datetime.fromisoformat(t),
            magnitude=m,
            place="", latitude=0, longitude=0, depth=0, id=""
        )
        for t, m in eq_times_mags
        if recent_start <= datetime.fromisoformat(t) < forecast_date
    ]

    # Считаем события в периоде
    actual = sum(
        1 for t, m in eq_times_mags
        if forecast_date <= datetime.fromisoformat(t) < forecast_end
    )

    # Получаем пороги для этого периода
    thresholds = get_thresholds_for_period(magnitude, period_days)

    results = []
    for threshold in thresholds:
        # Прогнозы для каждого порога
        simple_prob = simple.predict_at_least(threshold, period_days)
        integrated_prob = integrated.predict_at_least(
            threshold, period_days,
            forecast_date=forecast_date,
            recent_events=recent_events,
        )
        consensus_prob, consensus_type = consensus.predict_at_least(
            threshold, period_days,
            forecast_date=forecast_date,
            recent_events=recent_events,
        )

        results.append(ForecastResult(
            forecast_date=forecast_date,
            end_date=forecast_end,
            period_days=period_days,
            magnitude=magnitude,
            threshold=threshold,
            simple_prob=simple_prob,
            integrated_prob=integrated_prob,
            consensus_prob=consensus_prob,
            consensus_type=consensus_type,
            actual_count=actual,
            outcome=actual >= threshold,
        ))

    return results


def _process_single_forecast_intervals(args: tuple) -> list[ForecastResultInterval]:
    """
    Обработать один прогноз для ВСЕХ интервалов (для параллельного выполнения).

    Возвращает список результатов — по одному на каждый интервал.
    """
    (forecast_date, period_days, magnitude,
     eq_times_mags, historical_m7, historical_m8) = args

    # Пересоздаём модели в процессе (они не сериализуются)
    simple = SimpleModel(magnitude)
    integrated = IntegratedModel(magnitude)
    consensus = ConsensusModel(magnitude)

    forecast_end = forecast_date + timedelta(days=period_days)

    # Получаем недавние события (последние 30 дней)
    recent_start = forecast_date - timedelta(days=30)
    recent_events = [
        Earthquake(
            time=datetime.fromisoformat(t),
            magnitude=m,
            place="", latitude=0, longitude=0, depth=0, id=""
        )
        for t, m in eq_times_mags
        if recent_start <= datetime.fromisoformat(t) < forecast_date
    ]

    # Считаем события в периоде
    actual = sum(
        1 for t, m in eq_times_mags
        if forecast_date <= datetime.fromisoformat(t) < forecast_end
    )

    # Получаем интервалы для этого периода
    intervals = get_market_ranges_for_period(magnitude, period_days)

    results = []
    for interval_name, min_count, max_count in intervals:
        # Проверяем, попадает ли actual в интервал
        if max_count is None:
            outcome = actual >= min_count
        else:
            outcome = min_count <= actual <= max_count

        # Прогнозы для каждого интервала
        simple_prob = simple.predict_range(min_count, max_count, period_days)
        integrated_prob = integrated.predict_range(
            min_count, max_count, period_days,
            forecast_date=forecast_date,
            recent_events=recent_events,
        )
        consensus_prob, consensus_type = consensus.predict_range(
            min_count, max_count, period_days,
            forecast_date=forecast_date,
            recent_events=recent_events,
        )

        results.append(ForecastResultInterval(
            forecast_date=forecast_date,
            end_date=forecast_end,
            period_days=period_days,
            magnitude=magnitude,
            interval_name=interval_name,
            interval_min=min_count,
            interval_max=max_count,
            simple_prob=simple_prob,
            integrated_prob=integrated_prob,
            consensus_prob=consensus_prob,
            consensus_type=consensus_type,
            actual_count=actual,
            outcome=outcome,
        ))

    return results


def run_backtest_intervals(
    earthquakes: list[Earthquake],
    magnitude: float,
    period_days: int,
    start_year: int = 1980,
    end_year: int = 2023,
    step_days: int = 30,
    parallel: bool = True,
    n_workers: int = None,
    show_progress: bool = True,
) -> list[ForecastResultInterval]:
    """
    Запустить бэктест для интервалов (как на Polymarket).

    В отличие от run_backtest, тестирует P(min <= X <= max), а не P(X >= N).
    """
    # Фильтруем землетрясения и преобразуем для сериализации
    eq_filtered = [eq for eq in earthquakes if eq.magnitude >= magnitude]
    eq_times_mags = [(eq.time.isoformat(), eq.magnitude) for eq in eq_filtered]

    # Генерируем даты прогнозов
    forecast_dates = []
    current = datetime(start_year, 1, 1, tzinfo=timezone.utc)
    end_limit = datetime(end_year, 12, 31, tzinfo=timezone.utc) - timedelta(days=period_days)

    while current < end_limit:
        forecast_dates.append(current)
        current += timedelta(days=step_days)

    # Подготавливаем аргументы
    tasks = [
        (fd, period_days, magnitude,
         eq_times_mags, M7_HISTORICAL_COUNTS, M8_HISTORICAL_COUNTS)
        for fd in forecast_dates
    ]

    all_results = []

    if parallel and len(tasks) > 10:
        if n_workers is None:
            n_workers = min(multiprocessing.cpu_count(), 8)

        desc = f"M{magnitude}+ {period_days}d intervals"

        if HAS_TQDM and show_progress:
            with ProcessPoolExecutor(max_workers=n_workers) as executor:
                futures = [executor.submit(_process_single_forecast_intervals, task) for task in tasks]
                for future in tqdm(as_completed(futures), total=len(futures), desc=desc, leave=False):
                    all_results.extend(future.result())
        else:
            with ProcessPoolExecutor(max_workers=n_workers) as executor:
                for result_list in executor.map(_process_single_forecast_intervals, tasks):
                    all_results.extend(result_list)
    else:
        desc = f"M{magnitude}+ {period_days}d intervals"
        iterator = tqdm(tasks, desc=desc, leave=False) if (HAS_TQDM and show_progress) else tasks
        for task in iterator:
            all_results.extend(_process_single_forecast_intervals(task))

    # Сортируем по дате прогноза и интервалу
    all_results.sort(key=lambda r: (r.forecast_date, r.interval_min))

    return all_results


def run_backtest(
    earthquakes: list[Earthquake],
    magnitude: float,
    period_days: int,
    start_year: int = 1980,
    end_year: int = 2023,
    step_days: int = 30,
    parallel: bool = True,
    n_workers: int = None,
    show_progress: bool = True,
) -> list[ForecastResult]:
    """
    Запустить бэктест для одного периода и магнитуды.

    Делает прогнозы каждые step_days дней с start_year по end_year.
    Для каждой даты тестирует несколько порогов (thresholds).

    Args:
        parallel: Использовать параллельную обработку
        n_workers: Количество процессов (None = auto)
        show_progress: Показывать прогресс-бар
    """
    # Фильтруем землетрясения и преобразуем для сериализации
    eq_filtered = [eq for eq in earthquakes if eq.magnitude >= magnitude]
    eq_times_mags = [(eq.time.isoformat(), eq.magnitude) for eq in eq_filtered]

    # Генерируем даты прогнозов
    forecast_dates = []
    current = datetime(start_year, 1, 1, tzinfo=timezone.utc)
    end_limit = datetime(end_year, 12, 31, tzinfo=timezone.utc) - timedelta(days=period_days)

    while current < end_limit:
        forecast_dates.append(current)
        current += timedelta(days=step_days)

    # Подготавливаем аргументы для обработки (без threshold — он теперь внутри)
    tasks = [
        (fd, period_days, magnitude,
         eq_times_mags, M7_HISTORICAL_COUNTS, M8_HISTORICAL_COUNTS)
        for fd in forecast_dates
    ]

    all_results = []

    if parallel and len(tasks) > 10:
        # Параллельная обработка
        if n_workers is None:
            n_workers = min(multiprocessing.cpu_count(), 8)

        desc = f"M{magnitude}+ {period_days}d"

        if HAS_TQDM and show_progress:
            with ProcessPoolExecutor(max_workers=n_workers) as executor:
                futures = [executor.submit(_process_single_forecast, task) for task in tasks]
                for future in tqdm(as_completed(futures), total=len(futures), desc=desc, leave=False):
                    all_results.extend(future.result())  # extend т.к. возвращает list
        else:
            with ProcessPoolExecutor(max_workers=n_workers) as executor:
                for result_list in executor.map(_process_single_forecast, tasks):
                    all_results.extend(result_list)
    else:
        # Последовательная обработка
        desc = f"M{magnitude}+ {period_days}d"
        iterator = tqdm(tasks, desc=desc, leave=False) if (HAS_TQDM and show_progress) else tasks
        for task in iterator:
            all_results.extend(_process_single_forecast(task))

    # Сортируем по дате прогноза и порогу
    all_results.sort(key=lambda r: (r.forecast_date, r.threshold))

    return all_results


# ============================================================================
# ОТЧЁТ
# ============================================================================

def generate_report(
    all_results: dict[str, dict[str, list[ForecastResult]]],
    output_path: Path,
):
    """
    Сгенерировать детальный MD отчёт.

    all_results: {magnitude: {period: [results]}}
    """
    lines = []
    lines.append("# Бэктест моделей прогнозирования землетрясений")
    lines.append("")
    lines.append(f"**Дата генерации:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Оглавление
    lines.append("## Содержание")
    lines.append("")
    lines.append("1. [Сводка по моделям](#сводка-по-моделям)")
    lines.append("2. [Детали по магнитудам и периодам](#детали-по-магнитудам-и-периодам)")
    lines.append("3. [Калибровка](#калибровка)")
    lines.append("4. [Выводы](#выводы)")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Сводка по моделям
    lines.append("## Сводка по моделям")
    lines.append("")

    # Собираем все результаты для общей статистики
    all_forecasts = []
    for mag_results in all_results.values():
        for period_results in mag_results.values():
            all_forecasts.extend(period_results)

    if all_forecasts:
        simple_metrics = calculate_metrics(all_forecasts, "simple")
        integrated_metrics = calculate_metrics(all_forecasts, "integrated")
        consensus_metrics = calculate_metrics(all_forecasts, "consensus")

        lines.append("### Общая статистика (все периоды и магнитуды)")
        lines.append("")
        lines.append(f"Всего прогнозов: **{len(all_forecasts)}**")
        lines.append("")
        lines.append("| Метрика | Простая | Интегрированная | Консенсусная | Лучшая |")
        lines.append("|---------|---------|-----------------|--------------|--------|")

        # Brier Score
        brier_scores = [simple_metrics.brier_score, integrated_metrics.brier_score, consensus_metrics.brier_score]
        best_brier = min(brier_scores)
        best_brier_model = ["Простая", "Интегр.", "Консенс."][brier_scores.index(best_brier)]
        lines.append(f"| Brier Score ↓ | {simple_metrics.brier_score:.4f} | {integrated_metrics.brier_score:.4f} | {consensus_metrics.brier_score:.4f} | {best_brier_model} |")

        # MAE
        maes = [simple_metrics.mae, integrated_metrics.mae, consensus_metrics.mae]
        best_mae = min(maes)
        best_mae_model = ["Простая", "Интегр.", "Консенс."][maes.index(best_mae)]
        lines.append(f"| MAE ↓ | {simple_metrics.mae:.4f} | {integrated_metrics.mae:.4f} | {consensus_metrics.mae:.4f} | {best_mae_model} |")

        # Log Loss
        log_losses = [simple_metrics.log_loss, integrated_metrics.log_loss, consensus_metrics.log_loss]
        best_ll = min(log_losses)
        best_ll_model = ["Простая", "Интегр.", "Консенс."][log_losses.index(best_ll)]
        lines.append(f"| Log Loss ↓ | {simple_metrics.log_loss:.4f} | {integrated_metrics.log_loss:.4f} | {consensus_metrics.log_loss:.4f} | {best_ll_model} |")

        # Accuracy
        accs = [simple_metrics.accuracy, integrated_metrics.accuracy, consensus_metrics.accuracy]
        best_acc = max(accs)
        best_acc_model = ["Простая", "Интегр.", "Консенс."][accs.index(best_acc)]
        lines.append(f"| Accuracy ↑ | {simple_metrics.accuracy:.1%} | {integrated_metrics.accuracy:.1%} | {consensus_metrics.accuracy:.1%} | {best_acc_model} |")

        lines.append("")
        lines.append("*↓ = меньше лучше, ↑ = больше лучше*")
        lines.append("")

    lines.append("---")
    lines.append("")

    # Детали по магнитудам и периодам
    lines.append("## Детали по магнитудам и периодам")
    lines.append("")

    for magnitude, period_results in sorted(all_results.items()):
        lines.append(f"### M{magnitude}+")
        lines.append("")

        for period_name, results in sorted(period_results.items(), key=lambda x: FORECAST_PERIODS.get(x[0], 0)):
            if not results:
                continue

            period_days = FORECAST_PERIODS.get(period_name, 0)

            simple_m = calculate_metrics(results, "simple")
            integrated_m = calculate_metrics(results, "integrated")
            consensus_m = calculate_metrics(results, "consensus")

            # Базовая статистика
            unique_dates = len(set(r.forecast_date for r in results))
            thresholds_used = sorted(set(r.threshold for r in results))
            actual_counts = [r.actual_count for r in results if r.threshold == thresholds_used[0]]
            outcomes = [r.outcome for r in results]
            actual_occurrence_rate = sum(outcomes) / len(outcomes) if outcomes else 0

            lines.append(f"#### {period_name.replace('_', ' ').title()} ({period_days} дней)")
            lines.append("")
            lines.append(f"- Дат прогноза: {unique_dates}")
            lines.append(f"- Порогов (>=N): {thresholds_used}")
            lines.append(f"- Всего прогнозов: {len(results)}")
            lines.append(f"- Среднее событий (реальное): {statistics.mean(actual_counts):.1f}")
            lines.append(f"- Частота outcome=True: {actual_occurrence_rate:.1%}")
            lines.append("")

            lines.append("| Модель | Brier ↓ | MAE ↓ | Log Loss ↓ | Accuracy ↑ |")
            lines.append("|--------|---------|-------|------------|------------|")
            lines.append(f"| Простая | {simple_m.brier_score:.4f} | {simple_m.mae:.4f} | {simple_m.log_loss:.4f} | {simple_m.accuracy:.1%} |")
            lines.append(f"| Интегрированная | {integrated_m.brier_score:.4f} | {integrated_m.mae:.4f} | {integrated_m.log_loss:.4f} | {integrated_m.accuracy:.1%} |")
            lines.append(f"| Консенсусная | {consensus_m.brier_score:.4f} | {consensus_m.mae:.4f} | {consensus_m.log_loss:.4f} | {consensus_m.accuracy:.1%} |")
            lines.append("")

            # Статистика по порогам - средние вероятности
            lines.append("**Средние вероятности по порогам (>=N):**")
            lines.append("")
            lines.append("| Порог | Реальность | Простая | Интегр | Консенс | Ближе к реальности |")
            lines.append("|-------|------------|---------|--------|---------|-------------------|")

            for thresh in thresholds_used:
                thresh_results = [r for r in results if r.threshold == thresh]
                if not thresh_results:
                    continue

                outcome_rate = sum(1 for r in thresh_results if r.outcome) / len(thresh_results)
                avg_simple = statistics.mean(r.simple_prob for r in thresh_results)
                avg_integr = statistics.mean(r.integrated_prob for r in thresh_results)
                avg_consens = statistics.mean(r.consensus_prob for r in thresh_results)

                # Кто ближе к реальности?
                diffs = {
                    "Простая": abs(avg_simple - outcome_rate),
                    "Интегр": abs(avg_integr - outcome_rate),
                    "Консенс": abs(avg_consens - outcome_rate),
                }
                best = min(diffs, key=diffs.get)

                lines.append(f"| >={thresh} | {outcome_rate:.1%} | {avg_simple:.1%} | {avg_integr:.1%} | {avg_consens:.1%} | {best} |")

            lines.append("")

            # Brier Score по порогам
            lines.append("**Brier Score по порогам (↓ меньше = лучше):**")
            lines.append("")
            lines.append("| Порог | Простая | Интегр | Консенс | Лучшая | Δ лучшей vs простой |")
            lines.append("|-------|---------|--------|---------|--------|---------------------|")

            for thresh in thresholds_used:
                thresh_results = [r for r in results if r.threshold == thresh]
                if not thresh_results:
                    continue

                # Brier Score = mean((prob - outcome)²)
                def brier(probs, outcomes):
                    return sum((p - o) ** 2 for p, o in zip(probs, outcomes)) / len(probs)

                outcomes_binary = [1.0 if r.outcome else 0.0 for r in thresh_results]

                brier_simple = brier([r.simple_prob for r in thresh_results], outcomes_binary)
                brier_integr = brier([r.integrated_prob for r in thresh_results], outcomes_binary)
                brier_consens = brier([r.consensus_prob for r in thresh_results], outcomes_binary)

                # Лучший по Brier
                briers = {
                    "Простая": brier_simple,
                    "Интегр": brier_integr,
                    "Консенс": brier_consens,
                }
                best = min(briers, key=briers.get)
                best_brier = briers[best]

                # Улучшение относительно простой модели
                if brier_simple > 0:
                    improvement = (brier_simple - best_brier) / brier_simple * 100
                    delta_str = f"+{improvement:.1f}%" if improvement > 0 else f"{improvement:.1f}%"
                else:
                    delta_str = "—"

                lines.append(f"| >={thresh} | {brier_simple:.4f} | {brier_integr:.4f} | {brier_consens:.4f} | {best} | {delta_str} |")

            lines.append("")

            # Пример прогнозов
            lines.append("<details>")
            lines.append("<summary>Примеры прогнозов (последние 20)</summary>")
            lines.append("")
            lines.append("| Дата | Порог | Простая | Интегр | Консенс | Факт | ✓/✗ |")
            lines.append("|------|-------|---------|--------|---------|------|-----|")
            for r in results[-20:]:
                outcome_str = "✓" if r.outcome else "✗"
                lines.append(f"| {r.forecast_date.strftime('%Y-%m-%d')} | >={r.threshold} | {r.simple_prob:.1%} | {r.integrated_prob:.1%} | {r.consensus_prob:.1%} | {r.actual_count} | {outcome_str} |")
            lines.append("")
            lines.append("</details>")
            lines.append("")

    lines.append("---")
    lines.append("")

    # Калибровка
    lines.append("## Калибровка")
    lines.append("")
    lines.append("Калибровка показывает, насколько точно вероятности модели соответствуют реальным частотам.")
    lines.append("Идеальная калибровка: predicted ≈ actual.")
    lines.append("")

    if all_forecasts:
        for model_name, model_key in [("Простая", "simple"), ("Интегрированная", "integrated"), ("Консенсусная", "consensus")]:
            metrics = calculate_metrics(all_forecasts, model_key)

            lines.append(f"### {model_name} модель")
            lines.append("")
            lines.append("| Predicted Range | Avg Predicted | Avg Actual | Count | Diff |")
            lines.append("|-----------------|---------------|------------|-------|------|")

            for pred, actual, count in metrics.calibration_bins:
                if count > 0:
                    diff = actual - pred
                    diff_str = f"{diff:+.1%}"
                    lines.append(f"| {pred:.0%} | {pred:.1%} | {actual:.1%} | {count} | {diff_str} |")

            lines.append("")

    lines.append("---")
    lines.append("")

    # Выводы
    lines.append("## Выводы")
    lines.append("")

    if all_forecasts:
        simple_m = calculate_metrics(all_forecasts, "simple")
        integrated_m = calculate_metrics(all_forecasts, "integrated")
        consensus_m = calculate_metrics(all_forecasts, "consensus")

        # Определяем лучшую модель по Brier Score
        best_model = "Простая"
        best_brier = simple_m.brier_score
        if integrated_m.brier_score < best_brier:
            best_model = "Интегрированная"
            best_brier = integrated_m.brier_score
        if consensus_m.brier_score < best_brier:
            best_model = "Консенсусная"
            best_brier = consensus_m.brier_score

        improvement_simple = (simple_m.brier_score - best_brier) / simple_m.brier_score * 100 if simple_m.brier_score > 0 else 0

        lines.append(f"1. **Лучшая модель по Brier Score:** {best_model}")
        lines.append(f"2. **Улучшение относительно простой модели:** {improvement_simple:.1f}%")
        lines.append("")

        # Рекомендации по периодам
        lines.append("### Рекомендации по периодам")
        lines.append("")

        for period_name, period_days in FORECAST_PERIODS.items():
            period_results = []
            for mag_results in all_results.values():
                if period_name in mag_results:
                    period_results.extend(mag_results[period_name])

            if period_results:
                sm = calculate_metrics(period_results, "simple")
                im = calculate_metrics(period_results, "integrated")
                cm = calculate_metrics(period_results, "consensus")

                briers = {"Простая": sm.brier_score, "Интегрированная": im.brier_score, "Консенсусная": cm.brier_score}
                best = min(briers, key=briers.get)

                lines.append(f"- **{period_name.replace('_', ' ').title()}:** рекомендуется {best} модель")

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("*Сгенерировано автоматически скриптом backtest.py*")

    # Сохраняем
    content = "\n".join(lines)
    output_path.write_text(content, encoding="utf-8")

    return output_path


def generate_report_intervals(
    all_results: dict[str, dict[str, list[ForecastResultInterval]]],
    output_path: Path,
):
    """
    Сгенерировать детальный MD отчёт для интервалов (как на Polymarket).

    all_results: {magnitude: {period: [results]}}
    """
    lines = []
    lines.append("# Бэктест моделей: ИНТЕРВАЛЫ (как на Polymarket)")
    lines.append("")
    lines.append(f"**Дата генерации:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Сводка
    lines.append("## Сводка")
    lines.append("")
    lines.append("Этот отчёт тестирует модели на **интервалах** (например, '14-16', '8+'),")
    lines.append("а не на порогах (>=N). Это соответствует реальным рынкам Polymarket.")
    lines.append("")

    # Собираем все результаты
    all_forecasts = []
    for mag_results in all_results.values():
        for period_results in mag_results.values():
            all_forecasts.extend(period_results)

    if all_forecasts:
        simple_metrics = calculate_metrics(all_forecasts, "simple")
        integrated_metrics = calculate_metrics(all_forecasts, "integrated")
        consensus_metrics = calculate_metrics(all_forecasts, "consensus")

        lines.append("### Общая статистика")
        lines.append("")
        lines.append(f"Всего прогнозов: **{len(all_forecasts)}**")
        lines.append("")
        lines.append("| Метрика | Простая | Интегрированная | Консенсусная | Лучшая |")
        lines.append("|---------|---------|-----------------|--------------|--------|")

        # Brier Score
        brier_scores = [simple_metrics.brier_score, integrated_metrics.brier_score, consensus_metrics.brier_score]
        best_brier = min(brier_scores)
        best_brier_model = ["Простая", "Интегр.", "Консенс."][brier_scores.index(best_brier)]
        lines.append(f"| Brier Score ↓ | {simple_metrics.brier_score:.4f} | {integrated_metrics.brier_score:.4f} | {consensus_metrics.brier_score:.4f} | {best_brier_model} |")

        # MAE
        maes = [simple_metrics.mae, integrated_metrics.mae, consensus_metrics.mae]
        best_mae = min(maes)
        best_mae_model = ["Простая", "Интегр.", "Консенс."][maes.index(best_mae)]
        lines.append(f"| MAE ↓ | {simple_metrics.mae:.4f} | {integrated_metrics.mae:.4f} | {consensus_metrics.mae:.4f} | {best_mae_model} |")

        # Accuracy
        accs = [simple_metrics.accuracy, integrated_metrics.accuracy, consensus_metrics.accuracy]
        best_acc = max(accs)
        best_acc_model = ["Простая", "Интегр.", "Консенс."][accs.index(best_acc)]
        lines.append(f"| Accuracy ↑ | {simple_metrics.accuracy:.1%} | {integrated_metrics.accuracy:.1%} | {consensus_metrics.accuracy:.1%} | {best_acc_model} |")

        lines.append("")

    lines.append("---")
    lines.append("")

    # Детали по магнитудам и периодам
    lines.append("## Детали по интервалам")
    lines.append("")

    # Правила выбора модели по интервалам
    model_rules = {}  # (magnitude, period, interval) -> best_model

    for magnitude, period_results in sorted(all_results.items()):
        lines.append(f"### M{magnitude}+")
        lines.append("")

        for period_name, results in sorted(period_results.items(), key=lambda x: FORECAST_PERIODS.get(x[0], 0)):
            if not results:
                continue

            period_days = FORECAST_PERIODS.get(period_name, 0)

            # Статистика
            unique_dates = len(set(r.forecast_date for r in results))
            intervals_used = sorted(set(r.interval_name for r in results), key=lambda x: results[0].interval_min if x == results[0].interval_name else 0)

            # Восстанавливаем порядок интервалов
            interval_order = {r.interval_name: r.interval_min for r in results}
            intervals_used = sorted(set(r.interval_name for r in results), key=lambda x: interval_order.get(x, 0))

            lines.append(f"#### {period_name.replace('_', ' ').title()} ({period_days} дней)")
            lines.append("")
            lines.append(f"- Дат прогноза: {unique_dates}")
            lines.append(f"- Интервалы: {intervals_used}")
            lines.append("")

            # Таблица по интервалам
            lines.append("**Brier Score по интервалам (↓ меньше = лучше):**")
            lines.append("")
            lines.append("| Интервал | Частота | Простая | Интегр | Консенс | Лучшая | Δ vs простой |")
            lines.append("|----------|---------|---------|--------|---------|--------|--------------|")

            for interval_name in intervals_used:
                interval_results = [r for r in results if r.interval_name == interval_name]
                if not interval_results:
                    continue

                # Частота реального исхода
                outcome_rate = sum(1 for r in interval_results if r.outcome) / len(interval_results)

                # Brier Score для каждой модели
                def brier(probs, outcomes):
                    return sum((p - o) ** 2 for p, o in zip(probs, outcomes)) / len(probs)

                outcomes_binary = [1.0 if r.outcome else 0.0 for r in interval_results]

                brier_simple = brier([r.simple_prob for r in interval_results], outcomes_binary)
                brier_integr = brier([r.integrated_prob for r in interval_results], outcomes_binary)
                brier_consens = brier([r.consensus_prob for r in interval_results], outcomes_binary)

                # Лучший по Brier
                briers = {
                    "Простая": brier_simple,
                    "Интегр": brier_integr,
                    "Консенс": brier_consens,
                }
                best = min(briers, key=briers.get)
                best_brier = briers[best]

                # Сохраняем правило
                model_rules[(magnitude, period_name, interval_name)] = best

                # Улучшение относительно простой модели
                if brier_simple > 0:
                    improvement = (brier_simple - best_brier) / brier_simple * 100
                    delta_str = f"+{improvement:.1f}%" if improvement > 0 else f"{improvement:.1f}%"
                else:
                    delta_str = "—"

                lines.append(f"| {interval_name} | {outcome_rate:.1%} | {brier_simple:.4f} | {brier_integr:.4f} | {brier_consens:.4f} | **{best}** | {delta_str} |")

            lines.append("")

            # Средние вероятности
            lines.append("**Средние вероятности (модель vs реальность):**")
            lines.append("")
            lines.append("| Интервал | Реальность | Простая | Интегр | Консенс | Ближе |")
            lines.append("|----------|------------|---------|--------|---------|-------|")

            for interval_name in intervals_used:
                interval_results = [r for r in results if r.interval_name == interval_name]
                if not interval_results:
                    continue

                outcome_rate = sum(1 for r in interval_results if r.outcome) / len(interval_results)
                avg_simple = statistics.mean(r.simple_prob for r in interval_results)
                avg_integr = statistics.mean(r.integrated_prob for r in interval_results)
                avg_consens = statistics.mean(r.consensus_prob for r in interval_results)

                diffs = {
                    "Простая": abs(avg_simple - outcome_rate),
                    "Интегр": abs(avg_integr - outcome_rate),
                    "Консенс": abs(avg_consens - outcome_rate),
                }
                best = min(diffs, key=diffs.get)

                lines.append(f"| {interval_name} | {outcome_rate:.1%} | {avg_simple:.1%} | {avg_integr:.1%} | {avg_consens:.1%} | {best} |")

            lines.append("")

    lines.append("---")
    lines.append("")

    # Правила выбора модели
    lines.append("## Правила выбора модели")
    lines.append("")
    lines.append("На основе бэктеста, рекомендуемая модель для каждого интервала:")
    lines.append("")

    # Группируем по магнитуде и периоду
    current_mag = None
    current_period = None

    for (mag, period, interval), best_model in sorted(model_rules.items()):
        if mag != current_mag:
            lines.append(f"### M{mag}+")
            lines.append("")
            current_mag = mag
            current_period = None

        if period != current_period:
            period_days = FORECAST_PERIODS.get(period, 0)
            lines.append(f"**{period.replace('_', ' ').title()} ({period_days}d):**")
            lines.append("")
            current_period = period

        lines.append(f"- `{interval}` → **{best_model}**")

    lines.append("")
    lines.append("---")
    lines.append("")

    # Итоговые рекомендации
    lines.append("## Итоговые рекомендации")
    lines.append("")

    # Считаем, какая модель чаще лучшая
    model_wins = {"Простая": 0, "Интегр": 0, "Консенс": 0}
    for best in model_rules.values():
        model_wins[best] += 1

    total = sum(model_wins.values())
    if total > 0:
        lines.append("**Частота выигрыша по интервалам:**")
        lines.append("")
        for model, wins in sorted(model_wins.items(), key=lambda x: x[1], reverse=True):
            pct = wins / total * 100
            lines.append(f"- {model}: {wins}/{total} ({pct:.0f}%)")

        lines.append("")

        # Общая рекомендация
        best_overall = max(model_wins, key=model_wins.get)
        lines.append(f"**Общая рекомендация:** {best_overall} модель")
        lines.append("")

        # Специфичные рекомендации
        lines.append("**Специфичные рекомендации:**")
        lines.append("")

        # Анализируем паттерны
        # Крайние интервалы (низкие и высокие)
        extreme_low = []  # <5, 0-1 и т.д.
        extreme_high = []  # 20+, 8+ и т.д.
        central = []  # Центральные интервалы

        for (mag, period, interval), best in model_rules.items():
            if interval.startswith("<") or interval in ["0", "0-1", "1", "2"]:
                extreme_low.append(best)
            elif interval.endswith("+") or interval in ["20+", "17-19", "8+"]:
                extreme_high.append(best)
            else:
                central.append(best)

        if extreme_low:
            low_best = max(set(extreme_low), key=extreme_low.count)
            lines.append(f"- **Низкие интервалы** (<5, 0-1, и т.д.): {low_best}")

        if central:
            central_best = max(set(central), key=central.count)
            lines.append(f"- **Центральные интервалы** (11-13, 14-16, и т.д.): {central_best}")

        if extreme_high:
            high_best = max(set(extreme_high), key=extreme_high.count)
            lines.append(f"- **Высокие интервалы** (17-19, 20+, и т.д.): {high_best}")

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("*Сгенерировано автоматически скриптом backtest.py*")

    # Сохраняем
    content = "\n".join(lines)
    output_path.write_text(content, encoding="utf-8")

    return output_path


# ============================================================================
# MAIN
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="Бэктест моделей прогнозирования землетрясений")
    parser.add_argument("--start", type=int, default=1980, help="Начальный год (default: 1980)")
    parser.add_argument("--end", type=int, default=2023, help="Конечный год (default: 2023)")
    parser.add_argument("--magnitude", type=float, help="Только эта магнитуда (7.0, 8.0 или 9.0)")
    parser.add_argument("--period", type=int, help="Только этот период (дни)")
    parser.add_argument("--step", type=int, default=30, help="Шаг между прогнозами в днях (default: 30)")
    parser.add_argument("--workers", type=int, default=None, help="Количество worker процессов")
    parser.add_argument("--no-parallel", action="store_true", help="Отключить параллелизм")
    parser.add_argument("--output", type=str, help="Путь к выходному файлу")
    parser.add_argument("--thresholds", action="store_true",
                        help="Тестировать пороги >=N вместо интервалов Polymarket")
    args = parser.parse_args()

    # По умолчанию используем интервалы (как на Polymarket)
    use_intervals = not args.thresholds

    print("=" * 70)
    print("БЭКТЕСТ МОДЕЛЕЙ ПРОГНОЗИРОВАНИЯ ЗЕМЛЕТРЯСЕНИЙ")
    print("=" * 70)
    print(f"Период данных: {args.start}-{args.end}")
    print(f"Шаг прогноза: {args.step} дней")
    if use_intervals:
        print(f"Режим: ИНТЕРВАЛЫ (как на Polymarket)")
    else:
        print(f"Режим: пороги (>=N событий)")
    n_workers = args.workers or min(multiprocessing.cpu_count(), 8)
    print(f"Параллелизм: {'ВЫКЛ' if args.no_parallel else f'{n_workers} workers'}")
    print(f"TQDM: {'ВКЛ' if HAS_TQDM else 'ВЫКЛ (pip install tqdm)'}")
    print(f"Scipy: {'ВКЛ' if HAS_SCIPY else 'ВЫКЛ (pip install scipy)'}")
    print()

    # Определяем магнитуды для тестирования
    magnitudes = [args.magnitude] if args.magnitude else MAGNITUDES

    # Определяем периоды для тестирования
    if args.period:
        periods = {f"custom_{args.period}d": args.period}
    else:
        periods = FORECAST_PERIODS

    # Загружаем данные
    print("Загрузка данных USGS...")
    all_earthquakes = {}
    for mag in magnitudes:
        print(f"\nM{mag}+:")
        all_earthquakes[mag] = load_or_fetch_data(
            magnitude=mag,
            start_year=1973,  # Начинаем с 1973 (полная сейсмосеть)
            end_year=args.end,
        )

    print()

    # Подсчитываем общее количество задач для прогресса
    total_tasks = len(magnitudes) * len(periods)
    print(f"Всего комбинаций магнитуда × период: {total_tasks}")
    print()

    # Запускаем бэктест
    all_results = {}

    # Создаём список всех комбинаций для tqdm
    combinations = [
        (mag, period_name, period_days)
        for mag in magnitudes
        for period_name, period_days in sorted(periods.items(), key=lambda x: x[1])
    ]

    if HAS_TQDM:
        pbar = tqdm(combinations, desc="Бэктест", unit="combo")
    else:
        pbar = combinations
        print("Бэктест:")

    for mag, period_name, period_days in pbar:
        if mag not in all_results:
            all_results[mag] = {}

        earthquakes = all_earthquakes[mag]

        if HAS_TQDM:
            pbar.set_description(f"M{mag}+ {period_name}")

        if use_intervals:
            results = run_backtest_intervals(
                earthquakes=earthquakes,
                magnitude=mag,
                period_days=period_days,
                start_year=args.start,
                end_year=args.end,
                step_days=args.step,
                parallel=not args.no_parallel,
                n_workers=args.workers,
                show_progress=False,
            )
        else:
            results = run_backtest(
                earthquakes=earthquakes,
                magnitude=mag,
                period_days=period_days,
                start_year=args.start,
                end_year=args.end,
                step_days=args.step,
                parallel=not args.no_parallel,
                n_workers=args.workers,
                show_progress=False,  # Не показываем вложенный прогресс
            )

        all_results[mag][period_name] = results

        if not HAS_TQDM:
            print(f"  M{mag}+ {period_name}: {len(results)} прогнозов")

    print()

    # Генерируем отчёт
    if args.output:
        output_path = Path(args.output)
    else:
        output_dir = Path(__file__).parent / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        suffix = "_intervals" if use_intervals else ""
        output_path = output_dir / f"backtest_{args.start}_{args.end}{suffix}.md"

    print("Генерация отчёта...")
    if use_intervals:
        report_path = generate_report_intervals(all_results, output_path)
    else:
        report_path = generate_report(all_results, output_path)
    print(f"\nОтчёт сохранён: {report_path}")

    # Краткая сводка в консоль
    print("\n" + "=" * 70)
    print("КРАТКАЯ СВОДКА")
    print("=" * 70)

    all_forecasts = []
    for mag_results in all_results.values():
        for period_results in mag_results.values():
            all_forecasts.extend(period_results)

    if all_forecasts:
        # Общая статистика
        unique_dates = len(set((r.forecast_date, r.period_days) for r in all_forecasts))

        if use_intervals:
            intervals_count = len(set(r.interval_name for r in all_forecasts))
            print(f"\nВсего прогнозов: {len(all_forecasts)} ({unique_dates} дат × {intervals_count} интервалов)")
        else:
            thresholds_count = len(set(r.threshold for r in all_forecasts))
            print(f"\nВсего прогнозов: {len(all_forecasts)} ({unique_dates} дат × {thresholds_count} порогов)")

        print("\n{:<20} {:>10} {:>10} {:>10} {:>10}".format(
            "Модель", "Brier↓", "MAE↓", "LogLoss↓", "Accuracy↑"
        ))
        print("-" * 60)

        metrics_list = []
        for model_name, model_key in [("Простая", "simple"), ("Интегрированная", "integrated"), ("Консенсусная", "consensus")]:
            metrics = calculate_metrics(all_forecasts, model_key)
            metrics_list.append((model_name, metrics))
            print("{:<20} {:>10.4f} {:>10.4f} {:>10.4f} {:>9.1%}".format(
                model_name, metrics.brier_score, metrics.mae, metrics.log_loss, metrics.accuracy
            ))

        # Определяем лучшую модель
        best_brier = min(m.brier_score for _, m in metrics_list)
        best_model = [name for name, m in metrics_list if m.brier_score == best_brier][0]
        print(f"\nЛучшая модель (по Brier Score): {best_model}")

        # Улучшение относительно простой
        simple_brier = metrics_list[0][1].brier_score
        if simple_brier > 0:
            for name, m in metrics_list[1:]:
                improvement = (simple_brier - m.brier_score) / simple_brier * 100
                if improvement > 0:
                    print(f"  {name}: лучше на {improvement:.1f}%")
                else:
                    print(f"  {name}: хуже на {-improvement:.1f}%")

        # Для интервалов показываем топ рекомендации
        if use_intervals:
            print("\nРекомендации по интервалам (см. отчёт для деталей):")
            # Собираем модель-победитель по каждому интервалу
            model_wins = {"Простая": 0, "Интегр": 0, "Консенс": 0}
            for mag_results in all_results.values():
                for period_results in mag_results.values():
                    # Группируем по интервалу
                    intervals = set(r.interval_name for r in period_results)
                    for interval in intervals:
                        interval_results = [r for r in period_results if r.interval_name == interval]
                        if not interval_results:
                            continue

                        def brier(probs, outcomes):
                            return sum((p - o) ** 2 for p, o in zip(probs, outcomes)) / len(probs)

                        outcomes_binary = [1.0 if r.outcome else 0.0 for r in interval_results]
                        briers = {
                            "Простая": brier([r.simple_prob for r in interval_results], outcomes_binary),
                            "Интегр": brier([r.integrated_prob for r in interval_results], outcomes_binary),
                            "Консенс": brier([r.consensus_prob for r in interval_results], outcomes_binary),
                        }
                        best = min(briers, key=briers.get)
                        model_wins[best] += 1

            total = sum(model_wins.values())
            if total > 0:
                for model, wins in sorted(model_wins.items(), key=lambda x: x[1], reverse=True):
                    pct = wins / total * 100
                    print(f"  {model}: выиграла {wins}/{total} интервалов ({pct:.0f}%)")

    print("\n" + "=" * 70)
    print("Готово!")


if __name__ == "__main__":
    main()
