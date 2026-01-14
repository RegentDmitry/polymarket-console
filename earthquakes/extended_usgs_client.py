"""
Extended USGS Client.

Wraps the standard USGS client and adds extended history
from the monitoring database (events detected before USGS publication).
"""

import logging
from datetime import datetime, timezone
from typing import Optional
from dataclasses import dataclass

from usgs_client import USGSClient, Earthquake

logger = logging.getLogger(__name__)

# Try to import psycopg2 for database access
try:
    import psycopg2
    import psycopg2.extras
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False
    logger.warning("psycopg2 not installed, extended history unavailable")


@dataclass
class ExtendedEarthquake:
    """Earthquake with extended info."""
    id: str
    magnitude: float
    place: str
    time: datetime
    url: str
    latitude: float
    longitude: float
    depth_km: Optional[float]
    # Extended fields
    in_usgs: bool
    source_count: int
    first_detected_at: Optional[datetime]
    detection_advantage_minutes: Optional[float]


class ExtendedUSGSClient:
    """
    Extended USGS Client with monitoring database integration.

    Provides earthquake history that includes:
    1. Standard USGS data
    2. Events detected by our monitoring system before USGS publication
    """

    def __init__(
        self,
        db_host: str = "172.24.192.1",
        db_port: int = 5432,
        db_name: str = "earthquake_monitor",
        db_user: str = "postgres",
        db_password: str = "dbpass",
    ):
        self.usgs = USGSClient()
        self._db_config = {
            "host": db_host,
            "port": db_port,
            "database": db_name,
            "user": db_user,
            "password": db_password,
        }
        self._conn = None

    def _get_connection(self):
        """Get database connection."""
        if not HAS_PSYCOPG2:
            return None

        if self._conn is None or self._conn.closed:
            try:
                self._conn = psycopg2.connect(**self._db_config)
            except Exception as e:
                logger.warning(f"Could not connect to monitoring DB: {e}")
                return None
        return self._conn

    def get_extended_earthquakes(
        self,
        start_time: datetime,
        end_time: datetime,
        min_magnitude: float = 7.0,
    ) -> tuple[list[ExtendedEarthquake], int]:
        """
        Get earthquakes with extended history.

        Returns:
            (list of earthquakes, number of extended events not yet in USGS)
        """
        # Get standard USGS data
        usgs_quakes = self.usgs.get_earthquakes(start_time, end_time, min_magnitude)
        usgs_ids = {q.id for q in usgs_quakes}

        # Convert to extended format
        result = []
        for q in usgs_quakes:
            result.append(ExtendedEarthquake(
                id=q.id,
                magnitude=q.magnitude,
                place=q.place,
                time=q.time,
                url=q.url,
                latitude=0,  # USGS client doesn't provide these
                longitude=0,
                depth_km=None,
                in_usgs=True,
                source_count=1,
                first_detected_at=None,
                detection_advantage_minutes=None,
            ))

        # Try to get extended history from monitoring DB
        extended_count = 0
        conn = self._get_connection()
        if conn:
            try:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    # Get events from our DB that are NOT in USGS
                    query = """
                        SELECT
                            event_id::text as id,
                            best_magnitude as magnitude,
                            location_name as place,
                            event_time as time,
                            latitude,
                            longitude,
                            depth_km,
                            source_count,
                            first_detected_at,
                            usgs_id
                        FROM earthquake_events
                        WHERE event_time BETWEEN %s AND %s
                          AND best_magnitude >= %s
                          AND usgs_id IS NULL
                        ORDER BY event_time DESC
                    """
                    cur.execute(query, (start_time, end_time, min_magnitude))
                    rows = cur.fetchall()

                    for row in rows:
                        # This event is NOT in USGS yet - extended history!
                        extended_count += 1
                        result.append(ExtendedEarthquake(
                            id=row["id"],
                            magnitude=float(row["magnitude"]),
                            place=row["place"] or "Unknown",
                            time=row["time"],
                            url="",  # No USGS URL yet
                            latitude=float(row["latitude"]),
                            longitude=float(row["longitude"]),
                            depth_km=float(row["depth_km"]) if row["depth_km"] else None,
                            in_usgs=False,
                            source_count=row["source_count"],
                            first_detected_at=row["first_detected_at"],
                            detection_advantage_minutes=None,  # Not confirmed yet
                        ))

                    # Also update existing results with detection info
                    query2 = """
                        SELECT
                            usgs_id,
                            source_count,
                            first_detected_at,
                            EXTRACT(EPOCH FROM (usgs_published_at - first_detected_at))/60 as advantage
                        FROM earthquake_events
                        WHERE usgs_id = ANY(%s)
                    """
                    cur.execute(query2, (list(usgs_ids),))
                    db_info = {row["usgs_id"]: row for row in cur.fetchall()}

                    for eq in result:
                        if eq.id in db_info:
                            info = db_info[eq.id]
                            eq.source_count = info["source_count"]
                            eq.first_detected_at = info["first_detected_at"]
                            eq.detection_advantage_minutes = (
                                float(info["advantage"]) if info["advantage"] else None
                            )

            except Exception as e:
                logger.warning(f"Error fetching extended history: {e}")

        # Sort by time descending
        result.sort(key=lambda x: x.time, reverse=True)

        return result, extended_count

    def count_extended_earthquakes(
        self,
        start_time: datetime,
        end_time: datetime,
        min_magnitude: float = 7.0,
    ) -> tuple[int, int]:
        """
        Count earthquakes including extended history.

        Returns:
            (total count, extended count not in USGS)
        """
        earthquakes, extended_count = self.get_extended_earthquakes(
            start_time, end_time, min_magnitude
        )
        return len(earthquakes), extended_count

    def get_pending_events(self, min_magnitude: float = 6.5) -> list[dict]:
        """
        Get events detected but not yet in USGS.

        These are potential trading opportunities!
        """
        conn = self._get_connection()
        if not conn:
            return []

        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                query = """
                    SELECT
                        event_id,
                        best_magnitude as magnitude,
                        location_name as place,
                        event_time as time,
                        latitude,
                        longitude,
                        source_count,
                        first_detected_at,
                        EXTRACT(EPOCH FROM (NOW() - first_detected_at))/60 as minutes_since_detection
                    FROM earthquake_events
                    WHERE usgs_id IS NULL
                      AND best_magnitude >= %s
                      AND first_detected_at > NOW() - INTERVAL '2 hours'
                    ORDER BY best_magnitude DESC, first_detected_at DESC
                """
                cur.execute(query, (min_magnitude,))
                return cur.fetchall()
        except Exception as e:
            logger.warning(f"Error fetching pending events: {e}")
            return []

    def print_extended_status(self, min_magnitude: float = 7.0):
        """Print extended history status for display at startup."""
        pending = self.get_pending_events(min_magnitude)

        if pending:
            print("\n" + "=" * 60)
            print("*** EXTENDED HISTORY - EVENTS NOT YET IN USGS ***")
            print("=" * 60)
            for event in pending:
                print(
                    f"  M{float(event['magnitude']):.1f} | "
                    f"{event['place'] or 'Unknown'} | "
                    f"Sources: {event['source_count']} | "
                    f"Detected {float(event['minutes_since_detection']):.0f} min ago"
                )
            print("=" * 60)
            print(f"*** {len(pending)} event(s) detected before USGS! ***")
            print("=" * 60 + "\n")
            return True
        else:
            print("\nNo extended history events (all events already in USGS)")
            return False
