"""
EMSC (European-Mediterranean Seismological Centre) earthquake data collector.

Uses WebSocket for real-time updates.
Coverage: Europe M5+, Global M7+
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import AsyncIterator, Optional

import httpx

from .base import BaseCollector
from ..models import SourceReport
from ..config import config

logger = logging.getLogger(__name__)

# Try to import websockets, fall back to polling if not available
try:
    import websockets
    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False
    logger.warning("[EMSC] websockets not installed, using HTTP polling instead")


class EMSCCollector(BaseCollector):
    """
    Collector for EMSC earthquake data.

    Can use either WebSocket (real-time) or HTTP polling.
    WebSocket: wss://www.seismicportal.eu/standing_order/websocket
    HTTP: https://www.seismicportal.eu/fdsnws/event/1/query
    """

    SOURCE_NAME = "emsc"
    POLL_INTERVAL = config.EMSC_RECONNECT_INTERVAL

    def __init__(self, use_websocket: bool = True):
        super().__init__()
        self._ws_url = config.EMSC_WS_URL
        self._http_url = "https://www.seismicportal.eu/fdsnws/event/1/query"
        self._use_websocket = use_websocket and HAS_WEBSOCKETS

    async def fetch_earthquakes(self) -> AsyncIterator[SourceReport]:
        """Fetch earthquakes from EMSC (HTTP polling mode)."""
        params = {
            "format": "json",
            "minmagnitude": config.MIN_MAGNITUDE_TRACK,
            "orderby": "time",
            "limit": 50,
        }

        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(self._http_url, params=params, timeout=30)
                response.raise_for_status()
                data = response.json()
            except httpx.HTTPError as e:
                logger.error(f"[EMSC] HTTP error: {e}")
                return
            except Exception as e:
                logger.error(f"[EMSC] Error fetching data: {e}")
                return

        features = data.get("features", [])
        for feature in features:
            try:
                report = self._parse_feature(feature)
                if report and self._filter_by_magnitude(report.magnitude):
                    yield report
            except Exception as e:
                logger.warning(f"[EMSC] Error parsing feature: {e}")
                continue

    async def run_websocket(self, callback) -> None:
        """Run EMSC collector using WebSocket for real-time updates."""
        if not HAS_WEBSOCKETS:
            logger.error("[EMSC] websockets not installed")
            return

        self._running = True
        logger.info(f"[{self.SOURCE_NAME}] Starting WebSocket collector")

        while self._running:
            try:
                async with websockets.connect(self._ws_url) as ws:
                    logger.info(f"[EMSC] WebSocket connected")

                    async for message in ws:
                        if not self._running:
                            break

                        try:
                            data = json.loads(message)
                            await self._handle_ws_message(data, callback)
                        except json.JSONDecodeError:
                            logger.warning(f"[EMSC] Invalid JSON: {message[:100]}")
                        except Exception as e:
                            logger.error(f"[EMSC] Error handling message: {e}")

            except Exception as e:
                logger.error(f"[EMSC] WebSocket error: {e}")

            if self._running:
                logger.info(f"[EMSC] Reconnecting in {self.POLL_INTERVAL}s...")
                await asyncio.sleep(self.POLL_INTERVAL)

    async def _handle_ws_message(self, data: dict, callback) -> None:
        """Handle incoming WebSocket message."""
        action = data.get("action")

        if action in ("create", "update"):
            event_data = data.get("data", {})
            properties = event_data.get("properties", {})
            geometry = event_data.get("geometry", {})

            magnitude = properties.get("mag")
            if magnitude is None or magnitude < config.MIN_MAGNITUDE_TRACK:
                return

            report = self._parse_ws_event(event_data)
            if report:
                if report.source_event_id not in self._seen_ids:
                    self._seen_ids.add(report.source_event_id)
                    logger.info(
                        f"[EMSC] New M{report.magnitude} at {report.location_name}"
                    )
                    await callback(report)

    def _parse_ws_event(self, event_data: dict) -> Optional[SourceReport]:
        """Parse WebSocket event data."""
        try:
            properties = event_data.get("properties", {})
            geometry = event_data.get("geometry", {})
            coords = geometry.get("coordinates", [])

            if len(coords) < 2:
                return None

            event_id = event_data.get("id") or properties.get("unid")
            time_str = properties.get("time")

            if not time_str:
                return None

            # Parse ISO time
            event_time = datetime.fromisoformat(time_str.replace("Z", "+00:00"))

            return SourceReport(
                source=self.SOURCE_NAME,
                source_event_id=str(event_id),
                magnitude=float(properties.get("mag", 0)),
                magnitude_type=properties.get("magtype"),
                latitude=float(coords[1]),
                longitude=float(coords[0]),
                depth_km=float(coords[2]) if len(coords) > 2 else None,
                location_name=properties.get("flynn_region") or properties.get("place"),
                event_time=event_time,
                received_at=datetime.now(timezone.utc),
                raw_data=event_data,
            )
        except Exception as e:
            logger.warning(f"[EMSC] Parse WS error: {e}")
            return None

    def _parse_feature(self, feature: dict) -> Optional[SourceReport]:
        """Parse EMSC GeoJSON feature."""
        try:
            properties = feature.get("properties", {})
            geometry = feature.get("geometry", {})
            coords = geometry.get("coordinates", [])

            if len(coords) < 2:
                return None

            event_id = feature.get("id") or properties.get("unid")
            time_str = properties.get("time")

            if not time_str:
                return None

            event_time = datetime.fromisoformat(time_str.replace("Z", "+00:00"))

            return SourceReport(
                source=self.SOURCE_NAME,
                source_event_id=str(event_id),
                magnitude=float(properties.get("mag", 0)),
                magnitude_type=properties.get("magtype"),
                latitude=float(coords[1]),
                longitude=float(coords[0]),
                depth_km=float(coords[2]) if len(coords) > 2 else None,
                location_name=properties.get("flynn_region") or properties.get("place"),
                event_time=event_time,
                received_at=datetime.now(timezone.utc),
                raw_data=feature,
            )
        except Exception as e:
            logger.warning(f"[EMSC] Parse error: {e}")
            return None
