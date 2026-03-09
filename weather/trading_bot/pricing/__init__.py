"""Portfolio sizing and pricing utilities."""

from typing import Optional

from scipy.stats import norm


def bucket_fair_price(forecast: float, sigma: float,
                      lower: Optional[float], upper: Optional[float]) -> float:
    """P(lower <= X < upper) where X ~ Normal(forecast, sigma)."""
    if sigma <= 0:
        if lower is None and upper is None:
            return 1.0
        if lower is None:
            return 1.0 if forecast < upper else 0.0
        if upper is None:
            return 1.0 if forecast >= lower else 0.0
        return 1.0 if lower <= forecast < upper else 0.0

    if lower is None and upper is None:
        return 1.0
    if lower is None:
        return norm.cdf(upper, forecast, sigma)
    if upper is None:
        return 1 - norm.cdf(lower, forecast, sigma)
    return norm.cdf(upper, forecast, sigma) - norm.cdf(lower, forecast, sigma)
