"""
Open-Meteo ensemble forecast provider with smart caching.

Fetches 4 NWP model forecasts (GFS, ECMWF, ICON, JMA), computes daily max
per model, returns mean forecast and sigma.

Cache invalidation: polls Open-Meteo S3 meta.json every 5 minutes to detect
new model runs. Refreshes forecasts only when a new run becomes available.
Fallback: 6-hour TTL if S3 is unreachable.
"""

import json
import logging
import ssl
import time
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from ..calibration import CityCalibration

logger = logging.getLogger(__name__)

OPEN_METEO_API = "https://api.open-meteo.com/v1/forecast"
ENSEMBLE_MODELS = "gfs_seamless,ecmwf_ifs025,icon_seamless,jma_seamless"

# Legacy fallback (used only if calibration not loaded)
SIGMA_FLOOR_F = 2.5   # °F
SIGMA_FLOOR_C = SIGMA_FLOOR_F / 1.8  # ≈1.39°C

# Smart caching: poll S3 meta.json every 5 min, fallback to 6h TTL
META_CHECK_INTERVAL = 300   # 5 minutes between S3 meta.json checks
CACHE_TTL_FALLBACK = 6 * 3600  # 6 hours — fallback if S3 unreachable

# S3 meta.json URLs for each model
# Note: API uses "gfs_seamless" / "jma_seamless" but S3 uses different names
S3_META_URL = "https://openmeteo.s3.amazonaws.com/data/{model}/static/meta.json"
S3_MODEL_MAP = {
    "gfs_seamless":    "ncep_gfs025",
    "ecmwf_ifs025":    "ecmwf_ifs025",
    "icon_seamless":   "dwd_icon",
    "jma_seamless":    "jma_gsm",
}

_ssl_ctx = ssl.create_default_context()


@dataclass
class ModelRunInfo:
    """Tracks the latest known run for a single NWP model."""
    model: str
    init_time: int = 0       # last_run_initialisation_time (unix)
    avail_time: int = 0      # last_run_availability_time (unix)
    checked_at: float = 0.0  # when we last polled meta.json


@dataclass
class ForecastResult:
    """Forecast for a city+date."""
    forecast: float           # mean(daily_max across 4 models)
    sigma: float              # max(std(models), sigma_floor)
    sigma_ensemble: float     # raw std across models
    models: Dict[str, float]  # {model_name: daily_max}
    df: Optional[float] = None   # Student-t degrees of freedom (None = Normal)
    cached: bool = False
    cache_age_min: float = 0.0


class ModelUpdateTracker:
    """Polls Open-Meteo S3 meta.json to detect new model runs.

    Instead of a fixed 6h TTL, checks S3 every 5 minutes. When a model's
    last_run_initialisation_time changes, the forecast cache is stale.
    """

    def __init__(self):
        self._models: Dict[str, ModelRunInfo] = {}
        self._last_check: float = 0.0
        self._s3_available: bool = True  # assume available until proven otherwise
        self._consecutive_failures: int = 0

        for api_name, s3_name in S3_MODEL_MAP.items():
            self._models[api_name] = ModelRunInfo(model=s3_name)

    def has_new_data(self, since: float) -> bool:
        """Check if any model has a new run available since `since` (unix ts).

        Polls S3 meta.json at most every META_CHECK_INTERVAL seconds.
        Returns True if any model's init_time changed after `since`.
        Falls back to time-based TTL if S3 is unreachable.
        """
        now = time.time()

        # Rate-limit S3 checks
        if (now - self._last_check) < META_CHECK_INTERVAL:
            # Between checks: use cached model info
            return self._any_model_newer_than(since)

        # Time to check S3
        self._last_check = now
        updated = self._poll_s3()

        if not updated and not self._s3_available:
            # S3 unreachable — fallback to TTL
            return (now - since) >= CACHE_TTL_FALLBACK

        return self._any_model_newer_than(since)

    def _any_model_newer_than(self, since: float) -> bool:
        """True if any model's availability time is after `since`."""
        for info in self._models.values():
            if info.avail_time > 0 and info.avail_time > since:
                return True
        return False

    def _poll_s3(self) -> bool:
        """Fetch meta.json for all models. Returns True if at least one succeeded."""
        any_success = False
        any_new = False

        for api_name, info in self._models.items():
            s3_name = info.model
            url = S3_META_URL.format(model=s3_name)

            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                resp = urllib.request.urlopen(req, timeout=10, context=_ssl_ctx)
                meta = json.loads(resp.read())

                new_init = meta.get("last_run_initialisation_time", 0)
                new_avail = meta.get("last_run_availability_time", 0)

                if new_init != info.init_time:
                    old_dt = _ts_to_str(info.init_time) if info.init_time else "none"
                    new_dt = _ts_to_str(new_init)
                    logger.info("Model %s: new run %s (was %s)", api_name, new_dt, old_dt)
                    any_new = True

                info.init_time = new_init
                info.avail_time = new_avail
                info.checked_at = time.time()
                any_success = True

            except Exception as e:
                logger.debug("S3 meta.json failed for %s: %s", api_name, e)

        if any_success:
            self._s3_available = True
            self._consecutive_failures = 0
        else:
            self._consecutive_failures += 1
            if self._consecutive_failures >= 3:
                self._s3_available = False
                logger.warning("S3 meta.json unreachable (3 consecutive failures), using TTL fallback")

        return any_success

    def get_status(self) -> Dict[str, str]:
        """Return human-readable status for each model."""
        result = {}
        for api_name, info in self._models.items():
            if info.init_time:
                init_str = _ts_to_str(info.init_time)
                age_min = (time.time() - info.avail_time) / 60 if info.avail_time else 0
                result[api_name] = f"{init_str} ({age_min:.0f}m ago)"
            else:
                result[api_name] = "unknown"
        return result

    def model_has_late_run(self, model_name: str) -> bool:
        """True if a specific model has init_time >= 12:00 UTC today.

        Used by single-model strategy: each city waits only for its best model.
        Model availability after 12Z init:
          ECMWF 12Z -> ~19:00 UTC (earliest)
          JMA   12Z -> ~18:00 UTC
          GFS   18Z -> ~21:30 UTC
          ICON  18Z -> ~22:00 UTC (latest)
        """
        info = self._models.get(model_name)
        if not info or info.init_time == 0:
            return False
        today_midnight = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0)
        today_noon_ts = int((today_midnight.replace(hour=12)).timestamp())
        return info.init_time >= today_noon_ts

    def all_models_have_late_run(self) -> bool:
        """True if ALL 4 models have init_time >= 12:00 UTC today.

        This means the forecast includes the afternoon/evening model runs,
        which is the "last forecast of the day" that our calibration is based on.

        Model schedule (last daily run init -> available):
          GFS:   18Z -> ~21:30 UTC
          ECMWF: 12Z -> ~19:00 UTC
          ICON:  18Z -> ~22:00 UTC
          JMA:   12Z -> ~18:00 UTC

        So all models are available with 12Z+ init by ~22:00 UTC.
        """
        today_midnight = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0)
        today_noon_ts = int((today_midnight.replace(hour=12)).timestamp())

        for info in self._models.values():
            if info.init_time < today_noon_ts:
                return False
        return True

    def get_oldest_model_age_hours(self) -> float:
        """Hours since the oldest model's init_time."""
        now = time.time()
        oldest = min(
            (info.init_time for info in self._models.values() if info.init_time > 0),
            default=0
        )
        return (now - oldest) / 3600 if oldest > 0 else 999.0

    @property
    def is_s3_available(self) -> bool:
        return self._s3_available


def _ts_to_str(ts: int) -> str:
    """Unix timestamp to 'HH:MM UTC' string."""
    if not ts:
        return "?"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%MZ")


class ForecastData:
    """Open-Meteo ensemble forecasts with smart caching."""

    def __init__(self, cities_json: Path,
                 calibration: Optional[CityCalibration] = None):
        with open(cities_json) as f:
            self.cities = json.load(f)

        # Cache: {city: {"fetched_at": float, "data": {date: {forecast, sigma, models}}}}
        self._cache: Dict[str, dict] = {}
        self.tracker = ModelUpdateTracker()
        self.db = None  # Optional[ForecastDB] — set externally
        self.calibration = calibration

    def get_forecast(self, city: str, date: str, unit: str) -> Optional[ForecastResult]:
        """Get forecast for a city+date. Uses cache if no new model run detected.

        Applies per-city calibration: bias correction on forecast,
        calibrated sigma instead of global floor.
        """
        cache_entry = self._cache.get(city)
        now = time.time()

        if cache_entry:
            fetched_at = cache_entry["fetched_at"]

            # Smart check: is there new model data since we last fetched?
            if not self.tracker.has_new_data(since=fetched_at):
                # No new model run → cache is still valid
                day_data = cache_entry["data"].get(date)
                if day_data:
                    forecast, sigma, df = self._apply_calibration(
                        day_data["forecast"], day_data["sigma"], city, date, unit,
                        models=day_data.get("models"))
                    return ForecastResult(
                        forecast=forecast,
                        sigma=sigma,
                        sigma_ensemble=day_data["sigma"],
                        models=day_data["models"],
                        df=df,
                        cached=True,
                        cache_age_min=(now - fetched_at) / 60,
                    )

        # Cache miss or new model data available — fetch
        self.refresh_city(city, unit)

        cache_entry = self._cache.get(city)
        if not cache_entry:
            return None

        day_data = cache_entry["data"].get(date)
        if not day_data:
            return None

        forecast, sigma, df = self._apply_calibration(
            day_data["forecast"], day_data["sigma"], city, date, unit,
            models=day_data.get("models"))
        return ForecastResult(
            forecast=forecast,
            sigma=sigma,
            sigma_ensemble=day_data["sigma"],
            models=day_data["models"],
            df=df,
            cached=False,
            cache_age_min=0.0,
        )

    def _apply_calibration(self, raw_forecast: float, ensemble_sigma: float,
                           city: str, date: str, unit: str,
                           models: Optional[Dict[str, float]] = None
                           ) -> Tuple[float, float, Optional[float]]:
        """Apply calibration: use single best model if available, else ensemble.

        Single-model strategy: use only the best model for this city
        (calibrated on 14 months of previous_day1 data).

        Returns (corrected_forecast, calibrated_sigma, student_t_df).
        """
        df = None
        if self.calibration and self.calibration.loaded:
            best_model = self.calibration.get_best_model(city)
            bias = self.calibration.get_bias(city, date)
            sigma = self.calibration.get_sigma(city, date, unit)
            df = self.calibration.get_df(city, date)

            # Use single best model if available in fetched data
            if best_model and models and best_model in models:
                forecast = models[best_model] - bias
            else:
                # Fallback to ensemble mean with bias correction
                forecast = raw_forecast - bias

            # Use calibrated sigma (already RMSE of best model)
            sigma = max(sigma, ensemble_sigma)
        else:
            # Legacy fallback
            sigma_floor = SIGMA_FLOOR_F if unit == "F" else SIGMA_FLOOR_C
            forecast = raw_forecast
            sigma = max(ensemble_sigma, sigma_floor)
        return forecast, sigma, df

    def refresh_city(self, city: str, unit: str = "F") -> bool:
        """Fetch fresh forecast for a city from Open-Meteo."""
        cfg = self.cities.get(city)
        if not cfg:
            return False

        temp_unit = "fahrenheit" if unit == "F" else "celsius"
        url = (
            f"{OPEN_METEO_API}"
            f"?latitude={cfg['lat']}&longitude={cfg['lon']}"
            f"&hourly=temperature_2m"
            f"&models={ENSEMBLE_MODELS}"
            f"&temperature_unit={temp_unit}"
            f"&timezone={cfg['timezone']}"
            f"&forecast_days=5"
        )

        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            resp = urllib.request.urlopen(req, timeout=30, context=_ssl_ctx)
            data = json.loads(resp.read())
        except Exception:
            return False

        hourly = data.get("hourly", {})
        times = hourly.get("time", [])

        model_keys = [k for k in hourly if k.startswith("temperature_2m_")
                      and k != "temperature_2m"]
        model_names = [k.replace("temperature_2m_", "") for k in model_keys]

        if not model_keys:
            return False

        # Group by date, compute daily max per model
        daily: Dict[str, Dict[str, list]] = defaultdict(lambda: defaultdict(list))
        for i, t in enumerate(times):
            date_str = t[:10]
            for mk, mn in zip(model_keys, model_names):
                vals = hourly.get(mk, [])
                if i < len(vals) and vals[i] is not None:
                    daily[date_str][mn].append(vals[i])

        result = {}
        for date_str, models in daily.items():
            maxes = {}
            for mn, temps in models.items():
                if temps:
                    maxes[mn] = max(temps)

            if not maxes:
                continue

            # Weighted ensemble if calibration provides per-city weights
            weights = None
            if self.calibration and self.calibration.loaded:
                weights = self.calibration.get_weights(city)

            if weights:
                # Weighted mean using optimal per-city model weights
                w_sum = 0.0
                f_sum = 0.0
                for mn, val in maxes.items():
                    w = weights.get(mn, 0.0)
                    f_sum += w * val
                    w_sum += w
                if w_sum < 0.5:
                    logger.warning("Low weight coverage for %s: %.2f (models: %s)",
                                   city, w_sum, list(maxes.keys()))
                forecast_val = f_sum / w_sum if w_sum > 0 else float(np.mean(list(maxes.values())))
            else:
                forecast_val = float(np.mean(list(maxes.values())))

            vals = list(maxes.values())
            result[date_str] = {
                "forecast": forecast_val,
                "sigma": float(np.std(vals)) if len(vals) > 1 else 0.0,
                "models": maxes,
            }

        fetched_at = time.time()
        self._cache[city] = {
            "fetched_at": fetched_at,
            "unit": unit,
            "data": result,
        }

        # Log to PostgreSQL if available
        if self.db and result:
            try:
                self.db.log_forecasts_batch(city, fetched_at, unit, result)
            except Exception as e:
                logger.warning("Failed to log forecasts to DB: %s", e)

        return True

    def refresh_all(self, unit_map: Optional[Dict[str, str]] = None) -> int:
        """Refresh forecasts for all cities. Returns count of successful fetches."""
        count = 0
        for city in self.cities:
            unit = "F"
            if unit_map:
                unit = unit_map.get(city, "F")
            elif self.cities[city].get("unit") == "celsius":
                unit = "C"
            if self.refresh_city(city, unit):
                count += 1
            time.sleep(0.2)  # rate limit
        return count

    def cache_age(self, city: str) -> Optional[float]:
        """Return cache age in minutes, or None if not cached."""
        entry = self._cache.get(city)
        if not entry:
            return None
        return (time.time() - entry["fetched_at"]) / 60

    def is_stale(self, city: str) -> bool:
        """Check if cache for a city needs refresh (new model data available)."""
        entry = self._cache.get(city)
        if not entry:
            return True
        return self.tracker.has_new_data(since=entry["fetched_at"])
