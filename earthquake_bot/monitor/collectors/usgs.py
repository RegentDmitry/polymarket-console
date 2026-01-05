"""
USGS (United States Geological Survey) earthquake data collector.

This is the REFERENCE source - used for resolution on Polymarket.
Coverage: Global
Latency: ~13-20 minutes for international events
"""

import logging
from datetime import datetime, timezone
from typing import AsyncIterator, Optional

import httpx

from .base import BaseCollector
from ..models import SourceReport
from ..config import config

logger = logging.getLogger(__name__)


class USGSCollector(BaseCollector):
    """
    Collector for USGS earthquake data.

    This is the authoritative source for Polymarket resolution.
    We track when USGS publishes to measure our information advantage.

    API: https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/
    """

    SOURCE_NAME = "usgs"
    POLL_INTERVAL = config.USGS_POLL_INTERVAL

    def __init__(self):
        super().__init__()
        self._url = config.USGS_URL

    async def fetch_earthquakes(self) -> AsyncIterator[SourceReport]:
        """Fetch earthquakes from USGS GeoJSON feed."""
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(self._url, timeout=30)
                response.raise_for_status()
                data = response.json()
            except httpx.HTTPError as e:
                logger.error(f"[USGS] HTTP error: {e}")
                return
            except Exception as e:
                logger.error(f"[USGS] Error fetching data: {e}")
                return

        features = data.get("features", [])
        for feature in features:
            try:
                report = self._parse_feature(feature)
                if report and self._filter_by_magnitude(report.magnitude):
                    yield report
            except Exception as e:
                logger.warning(f"[USGS] Error parsing feature: {e}")
                continue

    def _parse_feature(self, feature: dict) -> Optional[SourceReport]:
        """Parse USGS GeoJSON feature."""
        try:
            properties = feature.get("properties", {})
            geometry = feature.get("geometry", {})
            coords = geometry.get("coordinates", [])

            if len(coords) < 2:
                return None

            event_id = feature.get("id")
            if not event_id:
                return None

            # USGS uses milliseconds since epoch
            time_ms = properties.get("time")
            if not time_ms:
                return None

            event_time = datetime.fromtimestamp(time_ms / 1000, tz=timezone.utc)

            # Updated time (when USGS published/updated)
            updated_ms = properties.get("updated")
            reported_at = None
            if updated_ms:
                reported_at = datetime.fromtimestamp(updated_ms / 1000, tz=timezone.utc)

            magnitude = properties.get("mag")
            if magnitude is None:
                return None

            return SourceReport(
                source=self.SOURCE_NAME,
                source_event_id=str(event_id),
                magnitude=float(magnitude),
                magnitude_type=properties.get("magType"),
                latitude=float(coords[1]),
                longitude=float(coords[0]),
                depth_km=float(coords[2]) if len(coords) > 2 else None,
                location_name=properties.get("place"),
                event_time=event_time,
                reported_at=reported_at,  # When USGS published
                received_at=datetime.now(timezone.utc),
                raw_data=feature,
            )
        except Exception as e:
            logger.warning(f"[USGS] Parse error: {e}")
            return None

    async def get_event_details(self, event_id: str) -> Optional[dict]:
        """
        Get detailed information about a specific USGS event.

        Useful for getting exact publication timestamps.
        """
        url = f"https://earthquake.usgs.gov/fdsnws/event/1/query"
        params = {
            "eventid": event_id,
            "format": "geojson",
        }

        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(url, params=params, timeout=30)
                response.raise_for_status()
                return response.json()
            except Exception as e:
                logger.error(f"[USGS] Error fetching event {event_id}: {e}")
                return None
