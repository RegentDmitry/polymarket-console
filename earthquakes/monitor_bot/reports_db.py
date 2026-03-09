"""
Reports database for source calibration logging.

Logs every incoming SourceReport to PostgreSQL for later analysis:
- Which sources detect earthquakes first
- Magnitude differences between sources and USGS
- USGS confirmation rates per source
"""

import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from .config import config

logger = logging.getLogger(__name__)

try:
    import asyncpg
    HAS_ASYNCPG = True
except ImportError:
    HAS_ASYNCPG = False

try:
    import psycopg2
    import psycopg2.extras
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False


class ReportsDB:
    """Async database client for source reports logging."""

    def __init__(self):
        self._pool = None
        self._sync_conn = None
        self._connected = False

    async def connect(self) -> bool:
        """Connect to reports database. Returns True if successful."""
        if not config.LOG_REPORTS:
            logger.info("Reports logging disabled (LOG_REPORTS=False)")
            return False

        try:
            if HAS_ASYNCPG:
                self._pool = await asyncpg.create_pool(
                    host=config.REPORTS_DB_HOST,
                    port=config.REPORTS_DB_PORT,
                    database=config.REPORTS_DB_NAME,
                    user=config.REPORTS_DB_USER,
                    password=config.REPORTS_DB_PASSWORD,
                    min_size=1,
                    max_size=5,
                )
                self._connected = True
                logger.info(f"Reports DB connected (asyncpg): {config.REPORTS_DB_HOST}/{config.REPORTS_DB_NAME}")
                return True
            elif HAS_PSYCOPG2:
                self._sync_conn = psycopg2.connect(
                    host=config.REPORTS_DB_HOST,
                    port=config.REPORTS_DB_PORT,
                    database=config.REPORTS_DB_NAME,
                    user=config.REPORTS_DB_USER,
                    password=config.REPORTS_DB_PASSWORD,
                )
                self._connected = True
                logger.info(f"Reports DB connected (psycopg2): {config.REPORTS_DB_HOST}/{config.REPORTS_DB_NAME}")
                return True
            else:
                logger.warning("No PostgreSQL driver (asyncpg or psycopg2) — reports logging disabled")
                return False
        except Exception as e:
            logger.error(f"Reports DB connection failed: {e}")
            return False

    async def close(self):
        if self._pool:
            await self._pool.close()
        if self._sync_conn:
            self._sync_conn.close()
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def _execute(self, query: str, *args):
        """Execute query (fire-and-forget style)."""
        if not self._connected:
            return
        try:
            if self._pool:
                async with self._pool.acquire() as conn:
                    await conn.execute(query, *args)
            elif self._sync_conn:
                with self._sync_conn.cursor() as cur:
                    cur.execute(query, args)
                    self._sync_conn.commit()
        except Exception as e:
            logger.error(f"Reports DB execute error: {e}")

    async def _fetchrow(self, query: str, *args):
        if not self._connected:
            return None
        try:
            if self._pool:
                async with self._pool.acquire() as conn:
                    return await conn.fetchrow(query, *args)
            elif self._sync_conn:
                with self._sync_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(query, args)
                    return cur.fetchone()
        except Exception as e:
            logger.error(f"Reports DB fetchrow error: {e}")
            return None

    # =========================================================================
    # Source Reports
    # =========================================================================

    async def log_report(
        self,
        report,  # SourceReport
        matched_event_id: Optional[UUID],
        is_new_event: bool,
    ) -> None:
        """Log incoming source report."""
        if not self._connected:
            return

        query = """
            INSERT INTO source_reports (
                received_at, source, source_event_id,
                magnitude, magnitude_type,
                latitude, longitude, depth_km, location_name,
                event_time, reported_at,
                matched_event_id, is_new_event
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13
            )
            ON CONFLICT (source, source_event_id) DO NOTHING
        """

        try:
            await self._execute(
                query,
                report.received_at,
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
                matched_event_id,
                is_new_event,
            )
        except Exception as e:
            logger.error(f"Failed to log report {report.source}/{report.source_event_id}: {e}")

    # =========================================================================
    # Events
    # =========================================================================

    async def log_event(
        self,
        event,  # EarthquakeEvent
        first_source: str,
    ) -> None:
        """Log or update deduplicated event."""
        if not self._connected:
            return

        query = """
            INSERT INTO events (
                event_id, event_time, best_magnitude, best_magnitude_type,
                latitude, longitude, depth_km, location_name,
                first_detected_at, first_source, source_count,
                usgs_id, usgs_published_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13
            )
            ON CONFLICT (event_id) DO UPDATE SET
                best_magnitude = EXCLUDED.best_magnitude,
                best_magnitude_type = EXCLUDED.best_magnitude_type,
                source_count = EXCLUDED.source_count,
                usgs_id = COALESCE(EXCLUDED.usgs_id, events.usgs_id),
                usgs_published_at = COALESCE(EXCLUDED.usgs_published_at, events.usgs_published_at),
                updated_at = NOW()
        """

        try:
            await self._execute(
                query,
                event.event_id,
                event.event_time,
                event.best_magnitude,
                event.best_magnitude_type,
                event.latitude,
                event.longitude,
                event.depth_km,
                event.location_name,
                event.first_detected_at,
                first_source,
                event.source_count,
                event.usgs_id,
                event.usgs_published_at,
            )
        except Exception as e:
            logger.error(f"Failed to log event {event.event_id}: {e}")

    # =========================================================================
    # USGS Confirmation
    # =========================================================================

    async def update_usgs_confirmation(
        self,
        event_id: UUID,
        usgs_report,  # SourceReport from USGS
    ) -> None:
        """
        When USGS confirms an event, backfill USGS data into all source_reports
        for that event. This enables magnitude/location comparison per source.
        """
        if not self._connected:
            return

        try:
            # Update all source_reports linked to this event
            query = """
                UPDATE source_reports SET
                    usgs_confirmed = TRUE,
                    usgs_magnitude = $2,
                    usgs_event_time = $3,
                    usgs_latitude = $4,
                    usgs_longitude = $5,
                    usgs_depth_km = $6,
                    usgs_confirmed_at = NOW()
                WHERE matched_event_id = $1
                  AND source != 'usgs'
            """
            await self._execute(
                query,
                event_id,
                usgs_report.magnitude,
                usgs_report.event_time,
                usgs_report.latitude,
                usgs_report.longitude,
                usgs_report.depth_km,
            )

            # Update events table with USGS details
            query_event = """
                UPDATE events SET
                    usgs_id = $2,
                    usgs_magnitude = $3,
                    usgs_published_at = $4,
                    usgs_event_time = $5,
                    usgs_latitude = $6,
                    usgs_longitude = $7,
                    usgs_depth_km = $8,
                    updated_at = NOW()
                WHERE event_id = $1
            """
            await self._execute(
                query_event,
                event_id,
                usgs_report.source_event_id,
                usgs_report.magnitude,
                usgs_report.reported_at or usgs_report.received_at,
                usgs_report.event_time,
                usgs_report.latitude,
                usgs_report.longitude,
                usgs_report.depth_km,
            )

            logger.info(
                f"USGS confirmation backfilled for event {event_id}: "
                f"M{usgs_report.magnitude} ({usgs_report.source_event_id})"
            )
        except Exception as e:
            logger.error(f"Failed to update USGS confirmation for {event_id}: {e}")
