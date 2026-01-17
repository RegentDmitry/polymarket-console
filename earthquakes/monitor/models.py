"""
Data models for earthquake monitoring.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID
import json


@dataclass
class SourceReport:
    """Raw report from a single source."""
    source: str  # 'jma', 'emsc', 'gfz', 'geonet', 'usgs'
    source_event_id: str
    magnitude: float
    latitude: float
    longitude: float
    event_time: datetime
    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    magnitude_type: Optional[str] = None
    depth_km: Optional[float] = None
    location_name: Optional[str] = None
    reported_at: Optional[datetime] = None
    raw_data: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "source_event_id": self.source_event_id,
            "magnitude": self.magnitude,
            "magnitude_type": self.magnitude_type,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "depth_km": self.depth_km,
            "location_name": self.location_name,
            "event_time": self.event_time.isoformat(),
            "reported_at": self.reported_at.isoformat() if self.reported_at else None,
            "received_at": self.received_at.isoformat(),
            "raw_data": self.raw_data,
        }


@dataclass
class EarthquakeEvent:
    """Deduplicated earthquake event from multiple sources."""
    event_id: UUID
    best_magnitude: float
    latitude: float
    longitude: float
    event_time: datetime
    first_detected_at: datetime
    best_magnitude_type: Optional[str] = None
    depth_km: Optional[float] = None
    location_name: Optional[str] = None
    usgs_published_at: Optional[datetime] = None
    usgs_id: Optional[str] = None
    jma_id: Optional[str] = None
    emsc_id: Optional[str] = None
    gfz_id: Optional[str] = None
    geonet_id: Optional[str] = None
    source_count: int = 1
    is_significant: bool = False

    @property
    def detection_advantage_minutes(self) -> Optional[float]:
        """
        Minutes between our detection and USGS publication.

        Positive = we detected before USGS published (information advantage)
        Negative = USGS published before we detected (no advantage, historical data)

        Returns None for negative values (historical data loaded after USGS publication).
        """
        if self.usgs_published_at and self.first_detected_at:
            delta = (self.usgs_published_at - self.first_detected_at).total_seconds()
            minutes = delta / 60

            # Only return positive edge (real-time detection advantage)
            # Negative means historical data loaded after USGS already published
            if minutes > 0:
                return minutes

        return None

    @property
    def is_in_usgs(self) -> bool:
        """Whether this event has been published by USGS."""
        return self.usgs_id is not None

    @property
    def hours_since_detection(self) -> float:
        """Hours since first detection."""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        delta = now - self.first_detected_at
        return delta.total_seconds() / 3600

    @property
    def usgs_status(self) -> str:
        """
        Get USGS confirmation status.

        Returns:
            "confirmed" - USGS has published
            "pending" - Waiting for USGS (< 6 hours)
            "delayed" - USGS delayed (6-24 hours)
            "unlikely" - USGS unlikely to publish (> 24 hours)
        """
        if self.is_in_usgs:
            return "confirmed"

        from monitor_bot.config import config
        hours = self.hours_since_detection

        if hours < config.USGS_WARNING_HOURS:
            return "pending"
        elif hours < config.USGS_TIMEOUT_HOURS:
            return "delayed"
        else:
            return "unlikely"

    def to_dict(self) -> dict:
        return {
            "event_id": str(self.event_id),
            "best_magnitude": self.best_magnitude,
            "best_magnitude_type": self.best_magnitude_type,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "depth_km": self.depth_km,
            "location_name": self.location_name,
            "event_time": self.event_time.isoformat(),
            "first_detected_at": self.first_detected_at.isoformat(),
            "usgs_published_at": self.usgs_published_at.isoformat() if self.usgs_published_at else None,
            "usgs_id": self.usgs_id,
            "source_count": self.source_count,
            "is_significant": self.is_significant,
            "detection_advantage_minutes": self.detection_advantage_minutes,
        }
