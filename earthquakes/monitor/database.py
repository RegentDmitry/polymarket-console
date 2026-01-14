"""
Database operations for earthquake monitoring.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from .config import config
from .models import EarthquakeEvent, SourceReport

logger = logging.getLogger(__name__)

# Try to import asyncpg, fall back to sync psycopg2 if not available
try:
    import asyncpg
    HAS_ASYNCPG = True
except ImportError:
    HAS_ASYNCPG = False
    logger.warning("asyncpg not installed, using synchronous database access")

try:
    import psycopg2
    import psycopg2.extras
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False


class Database:
    """Database connection and operations."""

    def __init__(self):
        self._pool = None
        self._sync_conn = None

    async def connect(self):
        """Connect to the database."""
        if HAS_ASYNCPG:
            try:
                self._pool = await asyncpg.create_pool(
                    host=config.DB_HOST,
                    port=config.DB_PORT,
                    database=config.DB_NAME,
                    user=config.DB_USER,
                    password=config.DB_PASSWORD,
                    min_size=2,
                    max_size=10,
                )
                logger.info(f"Connected to PostgreSQL (asyncpg)")
            except Exception as e:
                logger.error(f"Failed to connect with asyncpg: {e}")
                raise
        elif HAS_PSYCOPG2:
            try:
                self._sync_conn = psycopg2.connect(
                    host=config.DB_HOST,
                    port=config.DB_PORT,
                    database=config.DB_NAME,
                    user=config.DB_USER,
                    password=config.DB_PASSWORD,
                )
                logger.info(f"Connected to PostgreSQL (psycopg2)")
            except Exception as e:
                logger.error(f"Failed to connect with psycopg2: {e}")
                raise
        else:
            raise RuntimeError("No PostgreSQL driver available (install asyncpg or psycopg2)")

    async def close(self):
        """Close database connection."""
        if self._pool:
            await self._pool.close()
        if self._sync_conn:
            self._sync_conn.close()

    async def fetch(self, query: str, *args):
        """Execute query and return all rows."""
        if self._pool:
            async with self._pool.acquire() as conn:
                return await conn.fetch(query, *args)
        elif self._sync_conn:
            with self._sync_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(query, args)
                return cur.fetchall()

    async def fetchrow(self, query: str, *args):
        """Execute query and return first row."""
        if self._pool:
            async with self._pool.acquire() as conn:
                return await conn.fetchrow(query, *args)
        elif self._sync_conn:
            with self._sync_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(query, args)
                return cur.fetchone()

    async def execute(self, query: str, *args):
        """Execute query without returning results."""
        if self._pool:
            async with self._pool.acquire() as conn:
                return await conn.execute(query, *args)
        elif self._sync_conn:
            with self._sync_conn.cursor() as cur:
                cur.execute(query, args)
                self._sync_conn.commit()

    # =========================================================================
    # Event Operations
    # =========================================================================

    async def get_recent_events(
        self,
        hours: int = 24,
        min_magnitude: float = 6.5,
    ) -> list[EarthquakeEvent]:
        """Get recent events for matching."""
        query = """
            SELECT
                event_id, best_magnitude, best_magnitude_type,
                latitude, longitude, depth_km, location_name,
                event_time, first_detected_at, usgs_published_at,
                usgs_id, jma_id, emsc_id, gfz_id, geonet_id,
                source_count, is_significant
            FROM earthquake_events
            WHERE event_time > NOW() - INTERVAL '%s hours'
              AND best_magnitude >= %s
            ORDER BY event_time DESC
        """

        if self._pool:
            rows = await self.fetch(
                query.replace("%s", "$1").replace("%s", "$2", 1),
                hours, min_magnitude
            )
        else:
            rows = await self.fetch(query, hours, min_magnitude)

        return [self._row_to_event(row) for row in rows]

    async def get_event_by_source_id(
        self,
        source: str,
        source_event_id: str,
    ) -> Optional[EarthquakeEvent]:
        """Get event by source-specific ID."""
        column_map = {
            "usgs": "usgs_id",
            "jma": "jma_id",
            "emsc": "emsc_id",
            "gfz": "gfz_id",
            "geonet": "geonet_id",
        }

        column = column_map.get(source)
        if not column:
            return None

        query = f"""
            SELECT
                event_id, best_magnitude, best_magnitude_type,
                latitude, longitude, depth_km, location_name,
                event_time, first_detected_at, usgs_published_at,
                usgs_id, jma_id, emsc_id, gfz_id, geonet_id,
                source_count, is_significant
            FROM earthquake_events
            WHERE {column} = $1
        """

        row = await self.fetchrow(query, source_event_id)
        if row:
            return self._row_to_event(row)
        return None

    async def insert_event(self, event: EarthquakeEvent) -> None:
        """Insert new earthquake event."""
        query = """
            INSERT INTO earthquake_events (
                event_id, best_magnitude, best_magnitude_type,
                latitude, longitude, depth_km, location_name,
                event_time, first_detected_at, usgs_published_at,
                usgs_id, jma_id, emsc_id, gfz_id, geonet_id,
                source_count, is_significant
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                $11, $12, $13, $14, $15, $16, $17
            )
        """

        await self.execute(
            query,
            event.event_id,
            event.best_magnitude,
            event.best_magnitude_type,
            event.latitude,
            event.longitude,
            event.depth_km,
            event.location_name,
            event.event_time,
            event.first_detected_at,
            event.usgs_published_at,
            event.usgs_id,
            event.jma_id,
            event.emsc_id,
            event.gfz_id,
            event.geonet_id,
            event.source_count,
            event.is_significant,
        )

        logger.info(f"Inserted event {event.event_id}: M{event.best_magnitude}")

    async def update_event(self, event: EarthquakeEvent) -> None:
        """Update existing earthquake event."""
        query = """
            UPDATE earthquake_events SET
                best_magnitude = $2,
                best_magnitude_type = $3,
                latitude = $4,
                longitude = $5,
                depth_km = $6,
                location_name = $7,
                usgs_published_at = $8,
                usgs_id = $9,
                jma_id = $10,
                emsc_id = $11,
                gfz_id = $12,
                geonet_id = $13,
                source_count = $14,
                is_significant = $15,
                updated_at = NOW()
            WHERE event_id = $1
        """

        await self.execute(
            query,
            event.event_id,
            event.best_magnitude,
            event.best_magnitude_type,
            event.latitude,
            event.longitude,
            event.depth_km,
            event.location_name,
            event.usgs_published_at,
            event.usgs_id,
            event.jma_id,
            event.emsc_id,
            event.gfz_id,
            event.geonet_id,
            event.source_count,
            event.is_significant,
        )

        logger.debug(f"Updated event {event.event_id}")

    # =========================================================================
    # Source Report Operations
    # =========================================================================

    async def insert_report(self, report: SourceReport, event_id: UUID) -> None:
        """Insert source report."""
        query = """
            INSERT INTO source_reports (
                event_id, source, source_event_id,
                magnitude, magnitude_type,
                latitude, longitude, depth_km, location_name,
                event_time, reported_at, received_at,
                raw_data
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13
            )
            ON CONFLICT (source, source_event_id) DO NOTHING
        """

        await self.execute(
            query,
            event_id,
            report.source,
            report.source_event_id,
            report.magnitude,
            report.magnitude_type,
            report.latitude,
            report.longitude,
            report.depth_km,
            report.location_name,
            report.event_time,
            report.reported_at,
            report.received_at,
            json.dumps(report.raw_data) if report.raw_data else None,
        )

    async def get_reports_for_event(self, event_id: UUID) -> list[SourceReport]:
        """Get all source reports for an event."""
        query = """
            SELECT
                source, source_event_id,
                magnitude, magnitude_type,
                latitude, longitude, depth_km, location_name,
                event_time, reported_at, received_at
            FROM source_reports
            WHERE event_id = $1
            ORDER BY received_at
        """

        rows = await self.fetch(query, event_id)

        return [
            SourceReport(
                source=row["source"],
                source_event_id=row["source_event_id"],
                magnitude=float(row["magnitude"]),
                magnitude_type=row["magnitude_type"],
                latitude=float(row["latitude"]) if row["latitude"] else 0,
                longitude=float(row["longitude"]) if row["longitude"] else 0,
                depth_km=float(row["depth_km"]) if row["depth_km"] else None,
                location_name=row["location_name"],
                event_time=row["event_time"],
                reported_at=row["reported_at"],
                received_at=row["received_at"],
            )
            for row in rows
        ]

    # =========================================================================
    # Extended History
    # =========================================================================

    async def get_extended_history(
        self,
        start_date: datetime,
        end_date: datetime,
        min_magnitude: float = 7.0,
    ) -> list[dict]:
        """Get extended history including events not yet in USGS."""
        query = """
            SELECT
                COALESCE(usgs_id, event_id::text) as id,
                event_time as time,
                best_magnitude as magnitude,
                best_magnitude_type as mag_type,
                latitude,
                longitude,
                depth_km,
                location_name as place,
                usgs_id IS NOT NULL as in_usgs,
                source_count,
                first_detected_at,
                usgs_published_at,
                CASE
                    WHEN usgs_published_at IS NOT NULL
                    THEN EXTRACT(EPOCH FROM (usgs_published_at - first_detected_at))/60
                    ELSE NULL
                END as detection_advantage_minutes
            FROM earthquake_events
            WHERE event_time BETWEEN $1 AND $2
              AND best_magnitude >= $3
            ORDER BY event_time DESC
        """

        return await self.fetch(query, start_date, end_date, min_magnitude)

    async def get_pending_usgs_events(
        self,
        min_magnitude: float = 6.5,
    ) -> list[dict]:
        """Get events not yet confirmed by USGS."""
        query = """
            SELECT
                event_id,
                event_time,
                best_magnitude,
                location_name,
                source_count,
                first_detected_at,
                EXTRACT(EPOCH FROM (NOW() - first_detected_at))/60 as minutes_since_detection
            FROM earthquake_events
            WHERE usgs_id IS NULL
              AND best_magnitude >= $1
              AND first_detected_at > NOW() - INTERVAL '2 hours'
            ORDER BY first_detected_at DESC
        """

        return await self.fetch(query, min_magnitude)

    # =========================================================================
    # Helpers
    # =========================================================================

    def _row_to_event(self, row) -> EarthquakeEvent:
        """Convert database row to EarthquakeEvent."""
        return EarthquakeEvent(
            event_id=row["event_id"],
            best_magnitude=float(row["best_magnitude"]),
            best_magnitude_type=row["best_magnitude_type"],
            latitude=float(row["latitude"]),
            longitude=float(row["longitude"]),
            depth_km=float(row["depth_km"]) if row["depth_km"] else None,
            location_name=row["location_name"],
            event_time=row["event_time"],
            first_detected_at=row["first_detected_at"],
            usgs_published_at=row["usgs_published_at"],
            usgs_id=row["usgs_id"],
            jma_id=row["jma_id"],
            emsc_id=row["emsc_id"],
            gfz_id=row["gfz_id"],
            geonet_id=row["geonet_id"],
            source_count=row["source_count"],
            is_significant=row["is_significant"],
        )
