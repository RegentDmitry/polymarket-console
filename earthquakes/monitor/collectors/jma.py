"""
JMA (Japan Meteorological Agency) earthquake data collector.

This is the FASTEST source - publishes within seconds of earthquake.
Coverage: Japan and surrounding region.
"""

import logging
from datetime import datetime, timezone
from typing import AsyncIterator, Optional

import httpx

from .base import BaseCollector
from ..models import SourceReport
from ..config import config

logger = logging.getLogger(__name__)


class JMACollector(BaseCollector):
    """
    Collector for Japan Meteorological Agency earthquake data.

    JMA is the fastest source, publishing earthquake data within
    10-15 seconds of the event for Japan region.

    Data format: JSON array with earthquake objects
    URL: https://www.jma.go.jp/bosai/quake/data/list.json
    """

    SOURCE_NAME = "jma"
    POLL_INTERVAL = config.JMA_POLL_INTERVAL  # 30 seconds

    def __init__(self):
        super().__init__()
        self._url = config.JMA_URL

    async def fetch_earthquakes(self) -> AsyncIterator[SourceReport]:
        """Fetch earthquakes from JMA API."""
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(self._url, timeout=15)
                response.raise_for_status()
                data = response.json()
            except httpx.HTTPError as e:
                logger.error(f"[JMA] HTTP error: {e}")
                return
            except Exception as e:
                logger.error(f"[JMA] Error fetching data: {e}")
                return

        for quake in data:
            try:
                report = self._parse_quake(quake)
                if report and self._filter_by_magnitude(report.magnitude):
                    yield report
            except Exception as e:
                logger.warning(f"[JMA] Error parsing quake: {e}")
                continue

    def _parse_quake(self, quake: dict) -> Optional[SourceReport]:
        """Parse JMA quake data to SourceReport."""
        try:
            # JMA magnitude field
            magnitude = quake.get("mag")
            if magnitude is None:
                return None

            magnitude = float(magnitude)

            # Parse event time
            # JMA uses format like "2025-01-04T18:21:55+09:00"
            time_str = quake.get("at") or quake.get("ot")
            if not time_str:
                return None

            event_time = datetime.fromisoformat(time_str)
            if event_time.tzinfo is None:
                event_time = event_time.replace(tzinfo=timezone.utc)

            # Coordinates
            lat = quake.get("lat")
            lon = quake.get("lon")
            if lat is None or lon is None:
                return None

            # Depth (JMA uses 'dep' in km)
            depth = quake.get("dep")

            # Event ID
            event_id = quake.get("eid") or quake.get("id")
            if not event_id:
                # Generate ID from time and location
                event_id = f"jma_{event_time.timestamp()}_{lat}_{lon}"

            # Location name (English if available)
            location = quake.get("en_anm") or quake.get("anm") or "Japan region"

            return SourceReport(
                source=self.SOURCE_NAME,
                source_event_id=str(event_id),
                magnitude=magnitude,
                magnitude_type="MJMA",  # JMA uses their own scale
                latitude=float(lat),
                longitude=float(lon),
                depth_km=float(depth) if depth else None,
                location_name=location,
                event_time=event_time,
                received_at=datetime.now(timezone.utc),
                raw_data=quake,
            )
        except Exception as e:
            logger.warning(f"[JMA] Parse error: {e}, data: {quake}")
            return None
