"""Portfolio sizing and pricing utilities."""

from typing import Optional

from scipy.stats import norm, t as student_t


def bucket_fair_price(forecast: float, sigma: float,
                      lower: Optional[float], upper: Optional[float],
                      df: Optional[float] = None) -> float:
    """P(lower <= X < upper) where X ~ distribution(forecast, sigma).

    If df is provided, uses Student-t with that many degrees of freedom
    (fatter tails, more conservative on extreme buckets).
    Otherwise uses Normal distribution.
    """
    if sigma <= 0:
        if lower is None and upper is None:
            return 1.0
        if lower is None:
            return 1.0 if forecast < upper else 0.0
        if upper is None:
            return 1.0 if forecast >= lower else 0.0
        return 1.0 if lower <= forecast < upper else 0.0

    if df is not None:
        dist = student_t(max(df, 2.0), forecast, sigma)
    else:
        dist = norm(forecast, sigma)

    if lower is None and upper is None:
        return 1.0
    if lower is None:
        return float(dist.cdf(upper))
    if upper is None:
        return 1 - float(dist.cdf(lower))
    return float(dist.cdf(upper) - dist.cdf(lower))
