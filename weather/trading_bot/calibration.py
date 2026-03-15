"""
Per-city calibration data for weather forecast model.

Loads calibration_results.json (from backtest/calibrate.py) and provides
per-city, per-season sigma and bias values.
"""

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Season months
_SEASONS = {
    "DJF": [12, 1, 2],
    "MAM": [3, 4, 5],
    "JJA": [6, 7, 8],
    "SON": [9, 10, 11],
}

# Fallback values (old global floors)
_FALLBACK_SIGMA_F = 2.5
_FALLBACK_SIGMA_C = 1.39


def _get_season(date_str: str) -> str:
    """Get season name from date string (YYYY-MM-DD)."""
    month = int(date_str.split("-")[1])
    for name, months in _SEASONS.items():
        if month in months:
            return name
    return "DJF"


class CityCalibration:
    """Provides calibrated sigma, bias, and ensemble weights per city/season."""

    def __init__(self, calibration_path: Optional[Path] = None):
        self._data = {}
        if calibration_path and calibration_path.exists():
            try:
                with open(calibration_path) as f:
                    self._data = json.load(f)
                logger.info("Loaded calibration for %d cities", len(self._data))
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to load calibration: %s", e)

    @property
    def loaded(self) -> bool:
        return bool(self._data)

    def get_best_model(self, city: str) -> Optional[str]:
        """Get the single best model for this city.

        Returns Open-Meteo model name (e.g. "ecmwf_ifs025") or None.
        Used in single-model strategy where we wait only for this model's run.
        """
        cal = self._data.get(city)
        if not cal:
            return None
        return cal.get("best_model")

    def get_weights(self, city: str) -> Optional[dict]:
        """Get per-city optimal model weights for weighted ensemble.

        Returns dict like {"ecmwf_ifs025": 0.24, "gfs_seamless": 0.70, ...}
        or None if no weights available (falls back to equal weight).
        """
        cal = self._data.get(city)
        if not cal:
            return None
        return cal.get("ensemble_weights")

    def get_sigma(self, city: str, date: str, unit: str) -> float:
        """Get calibrated sigma for city+date (seasonal if available).

        Falls back to annual sigma, then global default.
        """
        cal = self._data.get(city)
        if not cal:
            return _FALLBACK_SIGMA_F if unit == "F" else _FALLBACK_SIGMA_C

        # Try seasonal first
        season = _get_season(date)
        seasonal = cal.get("seasonal", {}).get(season)
        if seasonal and "sigma" in seasonal:
            return seasonal["sigma"]

        # Annual
        if "sigma" in cal:
            return cal["sigma"]

        return _FALLBACK_SIGMA_F if unit == "F" else _FALLBACK_SIGMA_C

    def get_df(self, city: str, date: str) -> Optional[float]:
        """Get Student-t degrees of freedom for fat-tail pricing.

        Uses seasonal df if available, falls back to annual.
        Returns None if not available (use Normal distribution).
        """
        cal = self._data.get(city)
        if not cal:
            return None

        # Try seasonal first
        season = _get_season(date)
        seasonal = cal.get("seasonal", {}).get(season)
        if seasonal and "student_t_df" in seasonal:
            return seasonal["student_t_df"]

        return cal.get("student_t_df")

    def get_bias(self, city: str, date: str) -> float:
        """Get forecast bias for city+date (seasonal if available).

        Bias = forecast - actual (positive = forecast too high).
        Returns 0.0 if no calibration data.
        """
        cal = self._data.get(city)
        if not cal:
            return 0.0

        # Try seasonal
        season = _get_season(date)
        seasonal = cal.get("seasonal", {}).get(season)
        if seasonal and "bias" in seasonal:
            return seasonal["bias"]

        return cal.get("bias", 0.0)
