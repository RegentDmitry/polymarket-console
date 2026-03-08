"""
Portfolio outcome distribution via Monte Carlo temperature simulation.

Samples temperatures from Normal(forecast, sigma) for each (city, date),
checks which bucket positions win, computes P&L distribution.

Intra-city date correlation ~0.7 (persistent forecast error).
Inter-city correlation = 0 (independent).
"""

import re
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np


@dataclass
class WeatherPositionSpec:
    """Position spec for MC simulation."""
    city: str
    date: str
    bucket_lower: Optional[float]  # None = -∞ (cumulative ≤X)
    bucket_upper: Optional[float]  # None = +∞ (cumulative ≥X)
    outcome: str                    # "YES"
    entry_size: float
    tokens: float
    forecast: float
    sigma: float


@dataclass
class PortfolioOutcome:
    """Result of portfolio MC simulation."""
    percentiles: Dict[int, float]   # {5: -120, 25: -40, 50: 10, 75: 80, 95: 200}
    mean_pnl: float
    median_pnl: float
    win_prob: float                 # P(total P&L > 0)
    expected_value: float           # mean final portfolio value
    n_paths: int
    compute_time_ms: float


# Intra-city date correlation (forecast error persistence)
INTRA_CITY_CORR = 0.70


def simulate_weather_portfolio(
    specs: List[WeatherPositionSpec],
    balance: float = 0.0,
    n_paths: int = 100_000,
) -> PortfolioOutcome:
    """Simulate portfolio P&L distribution at resolution.

    For each MC path:
    1. Sample one temperature per (city, date) from Normal(forecast, sigma)
    2. Dates within same city are correlated (rho=0.7)
    3. Check which buckets the temperature falls into
    4. Compute total P&L = sum(payouts) - sum(entry_sizes)

    Returns percentile distribution.
    """
    t0 = time.monotonic()

    if not specs:
        return PortfolioOutcome(
            percentiles={p: 0.0 for p in [5, 10, 25, 50, 75, 90, 95]},
            mean_pnl=0.0, median_pnl=0.0, win_prob=0.5,
            expected_value=balance, n_paths=0, compute_time_ms=0.0,
        )

    rng = np.random.default_rng()
    total_cost = sum(s.entry_size for s in specs)

    # Group positions by (city, date) to share temperature samples
    # Each unique (city, date) gets one temperature draw
    cd_keys = []  # list of (city, date) tuples, ordered
    cd_map = {}   # (city, date) -> index
    for s in specs:
        key = (s.city, s.date)
        if key not in cd_map:
            cd_map[key] = len(cd_keys)
            cd_keys.append(key)

    n_cd = len(cd_keys)

    # Build forecast/sigma arrays for each (city, date)
    forecasts = np.zeros(n_cd)
    sigmas = np.zeros(n_cd)
    for s in specs:
        idx = cd_map[(s.city, s.date)]
        forecasts[idx] = s.forecast
        sigmas[idx] = max(s.sigma, 0.5)  # floor sigma

    # Generate correlated temperature samples
    # Group city-dates by city for intra-city correlation
    city_groups: Dict[str, List[int]] = {}
    for i, (city, date) in enumerate(cd_keys):
        city_groups.setdefault(city, []).append(i)

    # Sample temperatures: shape (n_paths, n_cd)
    temps = np.zeros((n_paths, n_cd))

    for city, indices in city_groups.items():
        n_dates = len(indices)
        if n_dates == 1:
            # Single date — just sample normally
            idx = indices[0]
            temps[:, idx] = rng.standard_normal(n_paths)
        else:
            # Multiple dates — correlated via Cholesky
            rho = INTRA_CITY_CORR
            # Correlation matrix: all pairs have same correlation
            corr = np.full((n_dates, n_dates), rho)
            np.fill_diagonal(corr, 1.0)
            L = np.linalg.cholesky(corr)

            z = rng.standard_normal((n_paths, n_dates))
            corr_z = z @ L.T  # (n_paths, n_dates)

            for j, idx in enumerate(indices):
                temps[:, idx] = corr_z[:, j]

    # Scale to actual temperatures
    temps = forecasts + temps * sigmas  # broadcast

    # Compute P&L for each path
    total_pnl = np.zeros(n_paths)

    for s in specs:
        idx = cd_map[(s.city, s.date)]
        temp = temps[:, idx]

        # Check bucket membership
        if s.bucket_lower is not None and s.bucket_upper is not None:
            in_bucket = (temp >= s.bucket_lower) & (temp < s.bucket_upper)
        elif s.bucket_lower is None and s.bucket_upper is not None:
            in_bucket = temp < s.bucket_upper
        elif s.bucket_lower is not None and s.bucket_upper is None:
            in_bucket = temp >= s.bucket_lower
        else:
            in_bucket = np.ones(n_paths, dtype=bool)

        # Payout
        if s.outcome == "YES":
            payout = np.where(in_bucket, s.tokens, 0.0)
        else:
            payout = np.where(~in_bucket, s.tokens, 0.0)

        total_pnl += (payout - s.entry_size)

    # Percentiles
    pct_keys = [5, 10, 25, 50, 75, 90, 95]
    pct_values = np.percentile(total_pnl, pct_keys)
    percentiles = dict(zip(pct_keys, [float(v) for v in pct_values]))

    elapsed_ms = (time.monotonic() - t0) * 1000

    return PortfolioOutcome(
        percentiles=percentiles,
        mean_pnl=float(np.mean(total_pnl)),
        median_pnl=float(np.median(total_pnl)),
        win_prob=float(np.mean(total_pnl > 0)),
        expected_value=balance + total_cost + float(np.mean(total_pnl)),
        n_paths=n_paths,
        compute_time_ms=elapsed_ms,
    )


_BUCKET_RE = re.compile(
    r"(?:(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)|"  # range: 64-65
    r"[≤<]\s*(\d+(?:\.\d+)?)|"                         # cumulative low: ≤51
    r"[≥>]\s*(\d+(?:\.\d+)?)|"                          # cumulative high: ≥82
    r"^(\d+(?:\.\d+)?))"                                # exact: 15
)


def _parse_bucket_label(label: str):
    """Parse bucket label to (lower, upper).

    Examples:
        "64-65°F" -> (64.0, 66.0)  # upper exclusive = upper_bound + 1
        "15°C"    -> (15.0, 16.0)  # single degree bucket
        "≤51°F"   -> (None, 52.0)  # cumulative low
        "≥82°F"   -> (82.0, None)  # cumulative high
    """
    m = _BUCKET_RE.search(label)
    if not m:
        return None, None

    if m.group(1) and m.group(2):
        # Range: 64-65 -> [64, 66)
        return float(m.group(1)), float(m.group(2)) + 1.0
    elif m.group(3):
        # ≤51 -> [None, 52)
        return None, float(m.group(3)) + 1.0
    elif m.group(4):
        # ≥82 -> [82, None)
        return float(m.group(4)), None
    elif m.group(5):
        # Exact: 15 -> [15, 16)
        return float(m.group(5)), float(m.group(5)) + 1.0

    return None, None


def positions_to_specs(
    positions: list,
    scanner,
) -> List[WeatherPositionSpec]:
    """Convert Position objects to WeatherPositionSpec using scanner data.

    Uses market data for bucket bounds and cached forecasts for forecast/sigma.
    """
    # Build market lookup: market_slug -> WeatherMarket
    market_map = {m.market_slug: m for m in scanner.polymarket.markets}

    # Get cached forecasts
    forecasts = scanner.get_cached_forecasts()

    specs = []
    for pos in positions:
        market = market_map.get(pos.market_slug)

        # Get bucket bounds from market data or parse from label
        if market:
            lower = market.bucket_lower
            upper = market.bucket_upper
        else:
            lower, upper = _parse_bucket_label(pos.bucket_label)

        # Get forecast data
        city_fc = forecasts.get(pos.city, {})
        dates = city_fc.get("dates", {})
        day_data = dates.get(pos.date, {})
        forecast = day_data.get("forecast", 0)
        sigma = day_data.get("sigma", 0)

        if not day_data or sigma == 0:
            continue  # skip if no forecast data

        specs.append(WeatherPositionSpec(
            city=pos.city,
            date=pos.date,
            bucket_lower=lower,
            bucket_upper=upper,
            outcome=pos.outcome,
            entry_size=pos.entry_size,
            tokens=pos.tokens,
            forecast=forecast,
            sigma=sigma,
        ))

    return specs
