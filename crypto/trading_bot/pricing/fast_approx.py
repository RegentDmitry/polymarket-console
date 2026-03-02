"""
Fast analytical approximation for touch probabilities.

Uses GBM first-passage-time formula as base, with a correction factor
calibrated against Student-t MC results.

STATUS: Placeholder — needs separate calibration and validation.
Currently just wraps the GBM formulas without correction.
"""

import math

from scipy.stats import norm


def touch_above_gbm(S: float, K: float, sigma: float, T: float, mu: float = 0) -> float:
    """GBM first passage time probability for touching K from below.

    P(max_{0..T} S_t >= K | S_0 = S) under GBM with drift mu.
    """
    if K <= S:
        return 1.0
    if T <= 0 or sigma <= 0:
        return 0.0
    ln = math.log(K / S)
    st = sigma * math.sqrt(T)
    drift = mu - 0.5 * sigma**2
    if abs(drift) < 1e-10:
        return 2 * (1 - norm.cdf(ln / st))
    d1 = (-ln + drift * T) / st
    d2 = (-ln - drift * T) / st
    exp = min(2 * drift * ln / sigma**2, 100)
    return min(norm.cdf(d1) + math.exp(exp) * norm.cdf(d2), 1.0)


def touch_below_gbm(S: float, K: float, sigma: float, T: float, mu: float = 0) -> float:
    """GBM first passage time probability for touching K from above.

    P(min_{0..T} S_t <= K | S_0 = S) under GBM with drift mu.
    """
    if K >= S:
        return 1.0
    if T <= 0 or sigma <= 0:
        return 0.0
    drift = mu - 0.5 * sigma**2
    ln_ks = math.log(K / S)
    st = sigma * math.sqrt(T)
    if abs(drift) < 1e-10:
        return 2 * (1 - norm.cdf(-ln_ks / st))
    d1 = (ln_ks + drift * T) / st
    d2 = (ln_ks - drift * T) / st
    exp = min(2 * drift * ln_ks / sigma**2, 100)
    return min(norm.cdf(d1) + math.exp(exp) * norm.cdf(d2), 1.0)


def fast_touch_prob(
    spot: float,
    strike: float,
    iv: float,
    T: float,
    drift: float = 0.0,
    df: float = 2.61,
) -> float:
    """Fast touch probability using GBM + correction.

    TODO: Implement correction factor calibration.
    Currently returns raw GBM probability (NO correction applied).
    This will UNDERESTIMATE probabilities vs Student-t MC because
    GBM has thinner tails.
    """
    is_up = strike > spot
    if is_up:
        return touch_above_gbm(spot, strike, iv, T, mu=drift)
    else:
        return touch_below_gbm(spot, strike, iv, T, mu=drift)
