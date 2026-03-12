"""
Adaptive sigma — adjusts forecast sigma based on recent prediction errors.

Queries PostgreSQL for the last 7 days of (forecast, actual) pairs per city,
computes recent RMSE, and compares it to the calibrated sigma.

    ratio = recent_rmse / calibrated_sigma

Actions:
  - ratio <= 1.0: model performing as expected → use calibrated sigma
  - 1.0 < ratio <= 2.0: model underperforming → inflate sigma by ratio
  - ratio > 2.0: model unreliable → skip city entirely

Works WITHOUT a database too: if no DB or insufficient data, returns
neutral adjustments (ratio=1.0, no skip).
"""

import logging
import math
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Minimum days of data needed to compute a meaningful ratio
MIN_DAYS_FOR_RATIO = 3

# Ratio thresholds
RATIO_INFLATE_ABOVE = 1.0   # inflate sigma when ratio > 1.0
RATIO_SKIP_ABOVE = 2.0      # skip city when ratio > 2.0

# How many days to look back
LOOKBACK_DAYS = 7


@dataclass
class CityAdjustment:
    """Adaptive adjustment for a single city."""
    city: str
    ratio: float          # recent_rmse / calibrated_sigma
    recent_rmse: float    # RMSE over last 7 days
    calibrated_sigma: float  # from calibration file
    n_days: int           # number of data points
    skip: bool            # True if ratio > RATIO_SKIP_ABOVE
    sigma_multiplier: float  # multiply calibrated sigma by this


class AdaptiveSigma:
    """Computes per-city sigma adjustments from recent forecast errors."""

    def __init__(self, db=None, cities: Optional[Dict] = None,
                 lookback_days: int = LOOKBACK_DAYS):
        """
        Args:
            db: ForecastDB instance (optional — gracefully degrades without it)
            cities: cities.json dict {city_slug: {unit: ...}} for unit lookup
            lookback_days: How many days of history to consider
        """
        self.db = db
        self.cities = cities or {}
        self.lookback_days = lookback_days
        self._adjustments: Dict[str, CityAdjustment] = {}
        self._last_update: float = 0.0

    def _get_unit(self, city: str) -> str:
        """Get unit for a city ('F' or 'C')."""
        cfg = self.cities.get(city, {})
        return "C" if cfg.get("unit") == "celsius" else "F"

    def update(self, calibration) -> Dict[str, CityAdjustment]:
        """Recompute adjustments from DB data.

        Args:
            calibration: CityCalibration instance (for calibrated sigma lookup)

        Returns:
            Dict of city -> CityAdjustment
        """
        import time
        self._last_update = time.time()

        if not self.db:
            logger.debug("AdaptiveSigma: no DB, returning neutral adjustments")
            self._adjustments = {}
            return self._adjustments

        try:
            errors = self.db.get_forecast_errors(days_back=self.lookback_days)
        except Exception as e:
            logger.warning("AdaptiveSigma: failed to get errors: %s", e)
            self._adjustments = {}
            return self._adjustments

        # Group errors by city
        city_errors: Dict[str, List[float]] = {}
        for row in errors:
            city = row["city"]
            error = row.get("error")
            if error is not None:
                city_errors.setdefault(city, []).append(float(error))

        adjustments = {}
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")

        for city, errs in city_errors.items():
            n = len(errs)
            if n < MIN_DAYS_FOR_RATIO:
                continue

            # Remove calibrated bias before computing RMSE, so we compare
            # bias-corrected residuals against calibrated sigma (both calibrated
            # the same way — on bias-corrected errors)
            bias = calibration.get_bias(city, today)
            residuals = [e + bias for e in errs]  # error = actual - forecast; corrected = error + bias
            rmse = math.sqrt(sum(r ** 2 for r in residuals) / n)

            # Get calibrated sigma in correct unit for this city
            unit = self._get_unit(city)
            cal_sigma = calibration.get_sigma(city, today, unit)

            if cal_sigma <= 0:
                continue

            ratio = rmse / cal_sigma

            skip = ratio > RATIO_SKIP_ABOVE
            if ratio > RATIO_INFLATE_ABOVE:
                multiplier = ratio
            else:
                multiplier = 1.0

            adj = CityAdjustment(
                city=city,
                ratio=round(ratio, 2),
                recent_rmse=round(rmse, 2),
                calibrated_sigma=round(cal_sigma, 2),
                n_days=n,
                skip=skip,
                sigma_multiplier=round(multiplier, 2),
            )
            adjustments[city] = adj

            level = logging.WARNING if skip else (
                logging.INFO if ratio > RATIO_INFLATE_ABOVE else logging.DEBUG
            )
            logger.log(level,
                       "AdaptiveSigma %s: ratio=%.2f (rmse=%.2f / cal_σ=%.2f, n=%d)%s",
                       city, ratio, rmse, cal_sigma, n,
                       " → SKIP" if skip else (
                           f" → inflate σ×{multiplier:.2f}" if multiplier > 1.0 else ""))

        self._adjustments = adjustments
        return adjustments

    def get_adjustment(self, city: str) -> Optional[CityAdjustment]:
        """Get adjustment for a city, or None if no data."""
        return self._adjustments.get(city)

    def should_skip(self, city: str) -> bool:
        """True if recent errors are too high (ratio > 2.0)."""
        adj = self._adjustments.get(city)
        return adj.skip if adj else False

    def get_sigma_multiplier(self, city: str) -> float:
        """Get sigma multiplier for a city (1.0 if no data or ratio <= 1.0)."""
        adj = self._adjustments.get(city)
        return adj.sigma_multiplier if adj else 1.0

    def get_status_lines(self) -> List[str]:
        """Get human-readable status for all cities with adjustments."""
        lines = []
        for city in sorted(self._adjustments):
            adj = self._adjustments[city]
            status = "SKIP" if adj.skip else (
                f"σ×{adj.sigma_multiplier:.1f}" if adj.sigma_multiplier > 1.0 else "OK"
            )
            lines.append(
                f"  {city:20s} ratio={adj.ratio:.2f} "
                f"(rmse={adj.recent_rmse:.1f} / σ={adj.calibrated_sigma:.1f}, "
                f"n={adj.n_days}) → {status}"
            )
        return lines
