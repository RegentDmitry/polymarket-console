"""
GFZ (GeoForschungsZentrum Potsdam) earthquake data collector.

Coverage: Global
Latency: ~6-9 minutes
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import AsyncIterator, Optional

import httpx

from .base import BaseCollector
from ..models import SourceReport
from ..config import config

logger = logging.getLogger(__name__)


class GFZCollector(BaseCollector):
    """
    Collector for GFZ (German Research Centre for Geosciences) earthquake data.

    Uses FDSN webservice API.
    URL: https://geofon.gfz-potsdam.de/fdsnws/event/1/query
    """

    SOURCE_NAME = "gfz"
    POLL_INTERVAL = config.GFZ_POLL_INTERVAL

    def __init__(self):
        super().__init__()
        self._url = config.GFZ_URL

    async def fetch_earthquakes(self) -> AsyncIterator[SourceReport]:
        """Fetch earthquakes from GFZ FDSN API."""
        # Query last 24 hours
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(hours=24)

        params = {
            "format": "json",
            "minmagnitude": config.MIN_MAGNITUDE_TRACK,
            "starttime": start_time.strftime("%Y-%m-%dT%H:%M:%S"),
            "endtime": end_time.strftime("%Y-%m-%dT%H:%M:%S"),
            "orderby": "time",
            "limit": 100,
        }

        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(self._url, params=params, timeout=30)
                response.raise_for_status()
                data = response.json()
            except httpx.HTTPError as e:
                logger.error(f"[GFZ] HTTP error: {e}")
                return
            except Exception as e:
                logger.error(f"[GFZ] Error fetching data: {e}")
                return

        features = data.get("features", [])
        for feature in features:
            try:
                report = self._parse_feature(feature)
                if report and self._filter_by_magnitude(report.magnitude):
                    yield report
            except Exception as e:
                logger.warning(f"[GFZ] Error parsing feature: {e}")
                continue

    def _parse_feature(self, feature: dict) -> Optional[SourceReport]:
        """Parse GFZ GeoJSON feature."""
        try:
            properties = feature.get("properties", {})
            geometry = feature.get("geometry", {})
            coords = geometry.get("coordinates", [])

            if len(coords) < 2:
                return None

            event_id = feature.get("id") or properties.get("publicID")
            time_str = properties.get("time")

            if not time_str:
                return None

            event_time = datetime.fromisoformat(time_str.replace("Z", "+00:00"))

            return SourceReport(
                source=self.SOURCE_NAME,
                source_event_id=str(event_id),
                magnitude=float(properties.get("mag", 0)),
                magnitude_type=properties.get("magType"),
                latitude=float(coords[1]),
                longitude=float(coords[0]),
                depth_km=float(coords[2]) if len(coords) > 2 else None,
                location_name=properties.get("place") or properties.get("region"),
                event_time=event_time,
                received_at=datetime.now(timezone.utc),
                raw_data=feature,
            )
        except Exception as e:
            logger.warning(f"[GFZ] Parse error: {e}")
            return None
