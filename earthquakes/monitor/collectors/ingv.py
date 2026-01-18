"""
INGV (Istituto Nazionale di Geofisica e Vulcanologia) earthquake data collector.

Coverage: Mediterranean / Europe
Latency: ~3-5 minutes
Reliability: 96.9% USGS match rate
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import AsyncIterator, Optional

import httpx

from .base import BaseCollector
from ..models import SourceReport
from ..config import config

logger = logging.getLogger(__name__)


class INGVCollector(BaseCollector):
    """
    Collector for INGV (Italy) earthquake data.

    Good coverage for Mediterranean and European events.

    Uses FDSN webservice API with text format.
    URL: https://webservices.ingv.it/fdsnws/event/1/query
    """

    SOURCE_NAME = "ingv"
    POLL_INTERVAL = 60  # Poll every 60 seconds

    def __init__(self):
        super().__init__()
        self._url = "https://webservices.ingv.it/fdsnws/event/1/query"

    async def fetch_earthquakes(self) -> AsyncIterator[SourceReport]:
        """Fetch earthquakes from INGV FDSN API."""
        # Query last 24 hours
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(hours=24)

        params = {
            "format": "text",
            "starttime": start_time.strftime("%Y-%m-%dT%H:%M:%S"),
            "endtime": end_time.strftime("%Y-%m-%dT%H:%M:%S"),
            "minmag": config.MIN_MAGNITUDE_TRACK,
            "orderby": "time",
            "limit": 200,
        }

        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(self._url, params=params, timeout=30)
                if response.status_code == 204:  # No content
                    return
                response.raise_for_status()
                text = response.text
            except httpx.HTTPError as e:
                logger.error(f"[INGV] HTTP error: {e}")
                return
            except Exception as e:
                logger.error(f"[INGV] Error fetching data: {e}")
                return

        # Parse text format (pipe-delimited)
        lines = text.strip().split("\n")

        for line in lines[1:]:  # Skip header
            if not line.strip():
                continue

            try:
                report = self._parse_line(line)
                if report and self._filter_by_magnitude(report.magnitude):
                    yield report
            except Exception as e:
                logger.warning(f"[INGV] Error parsing line: {e}")
                continue

    def _parse_line(self, line: str) -> Optional[SourceReport]:
        """Parse INGV text format line."""
        try:
            parts = line.split("|")
            if len(parts) < 11:
                return None

            event_id = parts[0].strip()
            time_str = parts[1].strip()
            lat = parts[2].strip()
            lon = parts[3].strip()
            depth = parts[4].strip()
            mag_type = parts[9].strip() if len(parts) > 9 else None
            magnitude = parts[10].strip() if len(parts) > 10 else None
            location = parts[12].strip() if len(parts) > 12 else "Mediterranean"

            if not time_str or not magnitude:
                return None

            # Parse time
            event_time = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
            if event_time.tzinfo is None:
                event_time = event_time.replace(tzinfo=timezone.utc)

            return SourceReport(
                source=self.SOURCE_NAME,
                source_event_id=str(event_id),
                magnitude=float(magnitude),
                magnitude_type=mag_type or None,
                latitude=float(lat),
                longitude=float(lon),
                depth_km=float(depth) if depth else None,
                location_name=location,
                event_time=event_time,
                received_at=datetime.now(timezone.utc),
                raw_data={"line": line},
            )
        except Exception as e:
            logger.warning(f"[INGV] Parse error: {e}")
            return None
