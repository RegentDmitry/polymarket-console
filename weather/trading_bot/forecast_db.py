"""
PostgreSQL logger for weather forecasts and actual temperatures.

Stores every forecast snapshot (per refresh) and actual observations (IEM).
Used for σ calibration: compare predicted vs actual to tune sigma_floor.
"""

import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    import psycopg2
    import psycopg2.extras
    PSYCOPG2_AVAILABLE = True
except ImportError:
    psycopg2 = None
    PSYCOPG2_AVAILABLE = False

FORECASTS_TABLE = """
CREATE TABLE IF NOT EXISTS forecasts (
    id SERIAL PRIMARY KEY,
    city TEXT NOT NULL,
    target_date DATE NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL,
    unit TEXT NOT NULL,
    forecast REAL NOT NULL,
    sigma REAL NOT NULL,
    model_gfs REAL,
    model_ecmwf REAL,
    model_icon REAL,
    model_jma REAL,
    UNIQUE(city, target_date, fetched_at)
)
"""

ACTUALS_TABLE = """
CREATE TABLE IF NOT EXISTS actuals (
    id SERIAL PRIMARY KEY,
    city TEXT NOT NULL,
    target_date DATE NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actual_high REAL NOT NULL,
    source TEXT NOT NULL,
    station TEXT,
    UNIQUE(city, target_date, source)
)
"""

# Model name mapping: Open-Meteo API names → DB column names
MODEL_COLUMN_MAP = {
    "gfs_seamless": "model_gfs",
    "ecmwf_ifs025": "model_ecmwf",
    "icon_seamless": "model_icon",
    "jma_seamless": "model_jma",
}


class ForecastDB:
    """PostgreSQL storage for forecast history."""

    def __init__(self, db_url: str):
        if not PSYCOPG2_AVAILABLE:
            raise ImportError("psycopg2 is required: pip install psycopg2-binary")

        self._db_url = db_url
        self.conn = psycopg2.connect(db_url)
        self.conn.autocommit = True
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute(FORECASTS_TABLE)
            cur.execute(ACTUALS_TABLE)

    def _reconnect(self) -> bool:
        """Reconnect if connection was lost."""
        try:
            if self.conn.closed:
                self.conn = psycopg2.connect(self._db_url)
                self.conn.autocommit = True
                return True
            # Test connection
            self.conn.cursor().execute("SELECT 1")
            return True
        except Exception:
            try:
                self.conn = psycopg2.connect(self._db_url)
                self.conn.autocommit = True
                return True
            except Exception as e:
                logger.warning("DB reconnect failed: %s", e)
                return False

    def log_forecast(self, city: str, target_date: str, fetched_at: float,
                     unit: str, forecast: float, sigma: float,
                     models: Dict[str, float]) -> None:
        """Log a single forecast snapshot.

        Args:
            city: City slug (e.g. "new-york")
            target_date: "YYYY-MM-DD"
            fetched_at: Unix timestamp when forecast was fetched
            unit: "F" or "C"
            forecast: Mean forecast across models
            sigma: Std dev across models (raw, before floor)
            models: {model_name: daily_max_value}
        """
        ts = datetime.fromtimestamp(fetched_at, tz=timezone.utc)

        model_gfs = models.get("gfs_seamless")
        model_ecmwf = models.get("ecmwf_ifs025")
        model_icon = models.get("icon_seamless")
        model_jma = models.get("jma_seamless")

        try:
            with self.conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO forecasts
                        (city, target_date, fetched_at, unit, forecast, sigma,
                         model_gfs, model_ecmwf, model_icon, model_jma)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (city, target_date, fetched_at) DO NOTHING
                """, (city, target_date, ts, unit, forecast, sigma,
                      model_gfs, model_ecmwf, model_icon, model_jma))
        except Exception as e:
            logger.warning("Failed to log forecast: %s", e)
            self._reconnect()

    def log_forecasts_batch(self, city: str, fetched_at: float, unit: str,
                            data: Dict[str, dict]) -> int:
        """Log all dates for a city refresh in one batch.

        Args:
            city: City slug
            fetched_at: Unix timestamp
            unit: "F" or "C"
            data: {date_str: {"forecast": float, "sigma": float, "models": {...}}}

        Returns: Number of rows inserted.
        """
        ts = datetime.fromtimestamp(fetched_at, tz=timezone.utc)
        rows = []
        for date_str, day in data.items():
            models = day.get("models", {})
            rows.append((
                city, date_str, ts, unit,
                day["forecast"], day["sigma"],
                models.get("gfs_seamless"),
                models.get("ecmwf_ifs025"),
                models.get("icon_seamless"),
                models.get("jma_seamless"),
            ))

        if not rows:
            return 0

        try:
            with self.conn.cursor() as cur:
                psycopg2.extras.execute_values(
                    cur,
                    """INSERT INTO forecasts
                        (city, target_date, fetched_at, unit, forecast, sigma,
                         model_gfs, model_ecmwf, model_icon, model_jma)
                    VALUES %s
                    ON CONFLICT (city, target_date, fetched_at) DO NOTHING""",
                    rows,
                )
                return cur.rowcount
        except Exception as e:
            logger.warning("Failed to batch-log forecasts: %s", e)
            self._reconnect()
            return 0

    def log_actual(self, city: str, target_date: str, actual_high: float,
                   source: str = "IEM", station: Optional[str] = None) -> None:
        """Log an actual observed temperature."""
        try:
            with self.conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO actuals (city, target_date, actual_high, source, station)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (city, target_date, source)
                    DO UPDATE SET actual_high = EXCLUDED.actual_high,
                                  recorded_at = NOW()
                """, (city, target_date, actual_high, source, station))
        except Exception as e:
            logger.warning("Failed to log actual: %s", e)

    def get_forecast_errors(self, city: Optional[str] = None,
                            days_back: int = 30) -> List[dict]:
        """Get forecast vs actual errors for analysis.

        Returns list of {city, date, forecast, actual, error, sigma, hours_before}.
        """
        query = """
            SELECT DISTINCT ON (f.city, f.target_date)
                f.city, f.target_date, f.forecast, f.sigma,
                a.actual_high,
                (a.actual_high - f.forecast) AS error,
                EXTRACT(EPOCH FROM (f.target_date::timestamp - f.fetched_at)) / 3600
                    AS hours_before
            FROM forecasts f
            JOIN actuals a ON f.city = a.city AND f.target_date = a.target_date
            WHERE f.fetched_at > NOW() - INTERVAL '%s days'
        """
        params = [days_back]

        if city:
            query += " AND f.city = %s"
            params.append(city)

        query += " ORDER BY f.city, f.target_date, f.fetched_at DESC"

        try:
            with self.conn.cursor() as cur:
                cur.execute(query, params)
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
        except Exception as e:
            logger.warning("Failed to get forecast errors: %s", e)
            return []

    def count_forecasts(self) -> int:
        try:
            with self.conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM forecasts")
                return cur.fetchone()[0]
        except Exception:
            return 0

    def close(self) -> None:
        if self.conn and not self.conn.closed:
            self.conn.close()
