"""
Touch probability calculation using Student-t Monte Carlo simulation.

Batch-optimized: generates paths once per (currency, drift) combo,
then checks all strikes against pre-computed running max/min.
This gives ~40x speedup vs per-market MC.
"""

import math
from dataclasses import dataclass
from typing import Dict, List, Tuple

import numpy as np


# Calibrated from 1500 days Deribit perpetual data (2022-02 to 2026-02)
STUDENT_DF_BTC = 2.61
STUDENT_DF_ETH = 2.88
MC_PATHS = 150_000


@dataclass
class TouchResult:
    """Result of touch probability calculation for one market."""
    slug: str
    currency: str
    strike: float
    direction: str       # "above" or "below"
    touch_prob: float    # P(price touches strike)
    fair_price: float    # = touch_prob for "above", 1-touch_prob for "below"
    days: float
    drift_used: float


def batch_touch_probabilities(
    spot: float,
    iv: float,
    days: int,
    strikes_above: List[float],
    strikes_below: List[float],
    drift: float = 0.0,
    df: float = STUDENT_DF_BTC,
    n_paths: int = MC_PATHS,
) -> Tuple[Dict[float, float], Dict[float, float]]:
    """Compute touch probabilities for multiple strikes in one batch.

    Generates paths once, computes running_max and running_min,
    then vectorized comparison against each strike.

    Args:
        spot: Current spot price
        iv: Annualized implied volatility
        days: Number of days to simulate
        strikes_above: List of strikes above spot (touch-above)
        strikes_below: List of strikes below spot (touch-below)
        drift: Annualized drift (from futures curve)
        df: Student-t degrees of freedom
        n_paths: Number of MC paths

    Returns:
        Tuple of (above_probs, below_probs) dicts mapping strike → touch_prob
    """
    if days <= 0 or iv <= 0:
        above = {k: (1.0 if k <= spot else 0.0) for k in strikes_above}
        below = {k: (1.0 if k >= spot else 0.0) for k in strikes_below}
        return above, below

    T = days / 365
    n_days = max(days, 1)
    dt = T / n_days

    # Scale Student-t so variance matches iv^2 * dt
    # Var(t_df) = df/(df-2) for df>2
    if df > 2:
        t_var = df / (df - 2)
    else:
        # df <= 2: infinite variance, use empirical scaling
        t_var = 10.0  # approximate

    scale = iv * math.sqrt(dt / t_var)
    drift_per_step = (drift - 0.5 * iv**2) * dt

    # Generate all paths at once using numpy (much faster than scipy)
    rng = np.random.default_rng()
    innovations = rng.standard_t(df, size=(n_paths, n_days)) * scale
    log_returns = drift_per_step + innovations

    # Cumulative log-returns → price paths
    cum_log = np.cumsum(log_returns, axis=1)
    prices = spot * np.exp(cum_log)  # shape: (n_paths, n_days)

    # Pre-compute running max and min along each path
    running_max = np.maximum.accumulate(prices, axis=1)
    running_min = np.minimum.accumulate(prices, axis=1)

    # Max and min over entire path for each simulation
    path_max = running_max[:, -1]  # shape: (n_paths,)
    path_min = running_min[:, -1]  # shape: (n_paths,)

    # Touch probabilities for above strikes
    above_probs = {}
    for strike in strikes_above:
        if strike <= spot:
            above_probs[strike] = 1.0
        else:
            touched = path_max >= strike
            above_probs[strike] = float(np.mean(touched))

    # Touch probabilities for below strikes
    below_probs = {}
    for strike in strikes_below:
        if strike >= spot:
            below_probs[strike] = 1.0
        else:
            touched = path_min <= strike
            below_probs[strike] = float(np.mean(touched))

    return above_probs, below_probs


def single_touch_prob(
    spot: float,
    strike: float,
    iv: float,
    T: float,
    drift: float = 0.0,
    df: float = STUDENT_DF_BTC,
    n_paths: int = MC_PATHS,
) -> float:
    """Single market touch probability (non-batched, for compatibility)."""
    if T <= 0 or iv <= 0:
        return 1.0 if strike <= spot else 0.0

    is_up = strike > spot
    days = max(int(T * 365), 1)

    if is_up:
        above, _ = batch_touch_probabilities(
            spot, iv, days, [strike], [], drift=drift, df=df, n_paths=n_paths
        )
        return above[strike]
    else:
        _, below = batch_touch_probabilities(
            spot, iv, days, [], [strike], drift=drift, df=df, n_paths=n_paths
        )
        return below[strike]


def get_df(currency: str) -> float:
    """Get Student-t df for a currency."""
    return STUDENT_DF_BTC if currency.upper() == "BTC" else STUDENT_DF_ETH
