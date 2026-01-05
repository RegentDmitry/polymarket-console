"""
Extended history service.

Provides USGS-compatible earthquake history with early detections.
"""

import logging
from datetime import datetime, timezone
from typing import Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ExtendedEvent:
    """Event from extended history."""
    id: str
    time: datetime
    magnitude: float
    mag_type: Optional[str]
    latitude: float
    longitude: float
    depth_km: Optional[float]
    place: Optional[str]
    in_usgs: bool
    source_count: int
    first_detected_at: datetime
    usgs_published_at: Optional[datetime]
    detection_advantage_minutes: Optional[float]


class ExtendedHistoryService:
    """
    Provides extended earthquake history.

    Combines USGS data with early detections from other sources.
    """

    def __init__(self, db):
        self.db = db

    async def get_extended_history(
        self,
        start_date: datetime,
        end_date: datetime,
        min_magnitude: float = 7.0,
    ) -> list[ExtendedEvent]:
        """
        Get extended earthquake history.

        Returns earthquakes including those not yet in USGS.
        """
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

        rows = await self.db.fetch(query, start_date, end_date, min_magnitude)

        return [
            ExtendedEvent(
                id=row["id"],
                time=row["time"],
                magnitude=float(row["magnitude"]),
                mag_type=row["mag_type"],
                latitude=float(row["latitude"]),
                longitude=float(row["longitude"]),
                depth_km=float(row["depth_km"]) if row["depth_km"] else None,
                place=row["place"],
                in_usgs=row["in_usgs"],
                source_count=row["source_count"],
                first_detected_at=row["first_detected_at"],
                usgs_published_at=row["usgs_published_at"],
                detection_advantage_minutes=float(row["detection_advantage_minutes"])
                if row["detection_advantage_minutes"]
                else None,
            )
            for row in rows
        ]

    async def get_events_not_in_usgs(
        self,
        min_magnitude: float = 7.0,
    ) -> list[ExtendedEvent]:
        """Get events detected but not yet published by USGS."""
        query = """
            SELECT
                event_id::text as id,
                event_time as time,
                best_magnitude as magnitude,
                best_magnitude_type as mag_type,
                latitude,
                longitude,
                depth_km,
                location_name as place,
                FALSE as in_usgs,
                source_count,
                first_detected_at,
                NULL as usgs_published_at,
                NULL as detection_advantage_minutes
            FROM earthquake_events
            WHERE usgs_id IS NULL
              AND best_magnitude >= $1
              AND first_detected_at > NOW() - INTERVAL '1 hour'
            ORDER BY first_detected_at DESC
        """

        rows = await self.db.fetch(query, min_magnitude)

        return [
            ExtendedEvent(
                id=row["id"],
                time=row["time"],
                magnitude=float(row["magnitude"]),
                mag_type=row["mag_type"],
                latitude=float(row["latitude"]),
                longitude=float(row["longitude"]),
                depth_km=float(row["depth_km"]) if row["depth_km"] else None,
                place=row["place"],
                in_usgs=False,
                source_count=row["source_count"],
                first_detected_at=row["first_detected_at"],
                usgs_published_at=None,
                detection_advantage_minutes=None,
            )
            for row in rows
        ]

    async def get_detection_statistics(self) -> dict:
        """Get statistics about detection advantage."""
        query = """
            SELECT
                COUNT(*) as total_events,
                COUNT(*) FILTER (WHERE usgs_id IS NOT NULL) as usgs_confirmed,
                COUNT(*) FILTER (WHERE usgs_id IS NULL) as pending_usgs,
                AVG(
                    EXTRACT(EPOCH FROM (usgs_published_at - first_detected_at))/60
                ) FILTER (WHERE usgs_published_at IS NOT NULL) as avg_advantage_minutes,
                MAX(
                    EXTRACT(EPOCH FROM (usgs_published_at - first_detected_at))/60
                ) FILTER (WHERE usgs_published_at IS NOT NULL) as max_advantage_minutes
            FROM earthquake_events
            WHERE best_magnitude >= 6.5
        """

        row = await self.db.fetchrow(query)

        return {
            "total_events": row["total_events"],
            "usgs_confirmed": row["usgs_confirmed"],
            "pending_usgs": row["pending_usgs"],
            "avg_advantage_minutes": float(row["avg_advantage_minutes"]) if row["avg_advantage_minutes"] else None,
            "max_advantage_minutes": float(row["max_advantage_minutes"]) if row["max_advantage_minutes"] else None,
        }
