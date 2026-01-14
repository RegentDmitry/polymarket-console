"""
Event matching and deduplication service.

Matches earthquake reports from different sources to the same event.
"""

import logging
import math
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

from ..models import SourceReport, EarthquakeEvent
from ..config import config

logger = logging.getLogger(__name__)


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate the great circle distance between two points on Earth (in km).

    Uses the Haversine formula.
    """
    R = 6371  # Earth's radius in kilometers

    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return R * c


class EventMatcher:
    """
    Matches incoming source reports to existing earthquake events.

    Matching criteria:
    1. Time difference < 5 minutes
    2. Distance < 100 km
    3. Magnitude difference < 1.5 (sanity check)
    """

    def __init__(
        self,
        time_window_sec: int = None,
        distance_km: float = None,
        magnitude_tolerance: float = 1.5,
    ):
        self.time_window_sec = time_window_sec or config.MATCH_TIME_WINDOW_SEC
        self.distance_km = distance_km or config.MATCH_DISTANCE_KM
        self.magnitude_tolerance = magnitude_tolerance

    def find_matching_event(
        self,
        report: SourceReport,
        existing_events: list[EarthquakeEvent],
    ) -> Optional[UUID]:
        """
        Find an existing event that matches the incoming report.

        Returns:
            event_id if match found, None otherwise
        """
        for event in existing_events:
            if self._is_match(report, event):
                return event.event_id
        return None

    def _is_match(self, report: SourceReport, event: EarthquakeEvent) -> bool:
        """Check if report matches existing event."""
        # Time check
        time_diff = abs((report.event_time - event.event_time).total_seconds())
        if time_diff > self.time_window_sec:
            return False

        # Distance check
        distance = haversine_distance(
            report.latitude, report.longitude,
            event.latitude, event.longitude
        )
        if distance > self.distance_km:
            return False

        # Magnitude sanity check (optional)
        if abs(report.magnitude - event.best_magnitude) > self.magnitude_tolerance:
            logger.warning(
                f"Magnitude mismatch: {report.source} M{report.magnitude} vs existing M{event.best_magnitude}"
            )
            # Still match, but log warning
            pass

        return True

    def create_event_from_report(self, report: SourceReport) -> EarthquakeEvent:
        """Create a new earthquake event from a source report."""
        event_id = uuid4()
        now = datetime.now(timezone.utc)

        event = EarthquakeEvent(
            event_id=event_id,
            best_magnitude=report.magnitude,
            best_magnitude_type=report.magnitude_type,
            latitude=report.latitude,
            longitude=report.longitude,
            depth_km=report.depth_km,
            location_name=report.location_name,
            event_time=report.event_time,
            first_detected_at=now,
            source_count=1,
            is_significant=report.magnitude >= config.MIN_MAGNITUDE_SIGNIFICANT,
        )

        # Set source-specific ID
        self._set_source_id(event, report)

        return event

    def update_event_from_report(
        self,
        event: EarthquakeEvent,
        report: SourceReport,
    ) -> EarthquakeEvent:
        """Update existing event with new source report."""
        # Set source-specific ID
        self._set_source_id(event, report)

        # Increment source count
        event.source_count += 1

        # Update best magnitude using priority rules
        new_magnitude, new_type = self._select_best_magnitude(
            event, report
        )
        event.best_magnitude = new_magnitude
        event.best_magnitude_type = new_type

        # Update USGS publication time if this is USGS
        if report.source == "usgs":
            event.usgs_published_at = report.reported_at or report.received_at

        # Update location if better
        if report.location_name and not event.location_name:
            event.location_name = report.location_name

        # Update depth if available and not set
        if report.depth_km and not event.depth_km:
            event.depth_km = report.depth_km

        return event

    def _set_source_id(self, event: EarthquakeEvent, report: SourceReport) -> None:
        """Set the source-specific event ID."""
        source_id = report.source_event_id

        if report.source == "usgs":
            event.usgs_id = source_id
        elif report.source == "jma":
            event.jma_id = source_id
        elif report.source == "emsc":
            event.emsc_id = source_id
        elif report.source == "gfz":
            event.gfz_id = source_id
        elif report.source == "geonet":
            event.geonet_id = source_id

    def _select_best_magnitude(
        self,
        event: EarthquakeEvent,
        new_report: SourceReport,
    ) -> tuple[float, Optional[str]]:
        """
        Select best magnitude estimate.

        Priority:
        1. USGS Mw (authoritative)
        2. Average of Mw estimates
        3. Keep higher magnitude (conservative for trading)
        """
        # If new report is USGS with Mw, use it
        if new_report.source == "usgs" and new_report.magnitude_type in ("Mw", "mww", "mwb", "mwc"):
            return new_report.magnitude, new_report.magnitude_type

        # If existing is USGS Mw, keep it
        if event.usgs_id and event.best_magnitude_type in ("Mw", "mww", "mwb", "mwc"):
            return event.best_magnitude, event.best_magnitude_type

        # If new report is Mw and current isn't, prefer Mw
        if new_report.magnitude_type in ("Mw", "mww", "mwb", "mwc"):
            if event.best_magnitude_type not in ("Mw", "mww", "mwb", "mwc"):
                return new_report.magnitude, new_report.magnitude_type

        # Default: keep higher magnitude (conservative)
        if new_report.magnitude > event.best_magnitude:
            return new_report.magnitude, new_report.magnitude_type

        return event.best_magnitude, event.best_magnitude_type
