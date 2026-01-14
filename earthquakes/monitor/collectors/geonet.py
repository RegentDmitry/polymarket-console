"""
GeoNet (New Zealand) earthquake data collector.

Coverage: New Zealand and Pacific region
Latency: ~5 minutes
"""

import logging
from datetime import datetime, timezone
from typing import AsyncIterator, Optional

import httpx

from .base import BaseCollector
from ..models import SourceReport
from ..config import config

logger = logging.getLogger(__name__)


class GeoNetCollector(BaseCollector):
    """
    Collector for GeoNet (New Zealand) earthquake data.

    API: https://api.geonet.org.nz/quake
    """

    SOURCE_NAME = "geonet"
    POLL_INTERVAL = config.GEONET_POLL_INTERVAL

    def __init__(self):
        super().__init__()
        self._url = config.GEONET_URL

    async def fetch_earthquakes(self) -> AsyncIterator[SourceReport]:
        """Fetch earthquakes from GeoNet API."""
        params = {
            "MMI": -1,  # All earthquakes regardless of intensity
        }

        headers = {
            "Accept": "application/json",
        }

        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    self._url, params=params, headers=headers, timeout=30
                )
                response.raise_for_status()
                data = response.json()
            except httpx.HTTPError as e:
                logger.error(f"[GeoNet] HTTP error: {e}")
                return
            except Exception as e:
                logger.error(f"[GeoNet] Error fetching data: {e}")
                return

        features = data.get("features", [])
        for feature in features:
            try:
                report = self._parse_feature(feature)
                if report and self._filter_by_magnitude(report.magnitude):
                    yield report
            except Exception as e:
                logger.warning(f"[GeoNet] Error parsing feature: {e}")
                continue

    def _parse_feature(self, feature: dict) -> Optional[SourceReport]:
        """Parse GeoNet GeoJSON feature."""
        try:
            properties = feature.get("properties", {})
            geometry = feature.get("geometry", {})
            coords = geometry.get("coordinates", [])

            if len(coords) < 2:
                return None

            event_id = properties.get("publicID") or feature.get("id")
            time_str = properties.get("time")

            if not time_str:
                return None

            event_time = datetime.fromisoformat(time_str.replace("Z", "+00:00"))

            magnitude = properties.get("magnitude")
            if magnitude is None:
                return None

            return SourceReport(
                source=self.SOURCE_NAME,
                source_event_id=str(event_id),
                magnitude=float(magnitude),
                magnitude_type=properties.get("magnitudeType", "ML"),
                latitude=float(coords[1]),
                longitude=float(coords[0]),
                depth_km=float(coords[2]) if len(coords) > 2 else None,
                location_name=properties.get("locality"),
                event_time=event_time,
                received_at=datetime.now(timezone.utc),
                raw_data=feature,
            )
        except Exception as e:
            logger.warning(f"[GeoNet] Parse error: {e}")
            return None
