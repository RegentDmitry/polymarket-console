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

        # JMA publishes multiple versions of the same event with different magnitudes
        # Group by event time + location (within 2 minutes, 50km) and keep highest magnitude
        reports = []
        for quake in data:
            try:
                report = self._parse_quake(quake)
                if report and self._filter_by_magnitude(report.magnitude):
                    reports.append(report)
            except Exception as e:
                logger.warning(f"[JMA] Error parsing quake: {e}")
                continue

        # Deduplicate JMA reports - keep highest magnitude for same event
        deduplicated = self._deduplicate_jma_reports(reports)
        for report in deduplicated:
            yield report

    def _deduplicate_jma_reports(self, reports: list[SourceReport]) -> list[SourceReport]:
        """
        Deduplicate JMA reports by grouping similar events.

        JMA publishes multiple versions of the same earthquake with different
        magnitude estimates. Group by time (2 min) + location (50 km) and keep
        the report with highest magnitude.
        """
        if not reports:
            return []

        from ..services.event_matcher import haversine_distance

        # Group similar reports
        groups: list[list[SourceReport]] = []

        for report in reports:
            matched_group = None
            for group in groups:
                # Compare with first report in group
                ref = group[0]
                time_diff = abs((report.event_time - ref.event_time).total_seconds())
                distance = haversine_distance(
                    report.latitude, report.longitude,
                    ref.latitude, ref.longitude
                )
                # Tight matching for JMA: 2 minutes, 50 km
                if time_diff < 120 and distance < 50:
                    matched_group = group
                    break

            if matched_group:
                matched_group.append(report)
            else:
                groups.append([report])

        # From each group, pick the LATEST report (most refined estimate)
        # JMA publishes updates with more accurate magnitudes over time
        result = []
        for group in groups:
            # Sort by reported_at (publication time), take latest
            # Fall back to received_at if reported_at is None
            def get_report_time(r):
                if r.reported_at:
                    return r.reported_at
                return r.received_at

            latest = max(group, key=get_report_time)
            if len(group) > 1:
                mags = [f"M{r.magnitude}" for r in sorted(group, key=get_report_time)]
                logger.info(
                    f"[JMA] Deduplicated {len(group)} versions for {latest.location_name}: "
                    f"{' → '.join(mags)} (using latest: M{latest.magnitude})"
                )
            result.append(latest)

        return result

    def _parse_quake(self, quake: dict) -> Optional[SourceReport]:
        """Parse JMA quake data to SourceReport."""
        try:
            # JMA magnitude field - skip entries without magnitude (intensity reports)
            magnitude = quake.get("mag")
            if magnitude is None or magnitude == "" or magnitude == "-":
                return None

            try:
                magnitude = float(magnitude)
            except (ValueError, TypeError):
                return None

            # Parse event time
            # JMA uses format like "2025-01-04T18:21:55+09:00"
            time_str = quake.get("at") or quake.get("ot")
            if not time_str:
                return None

            event_time = datetime.fromisoformat(time_str)
            if event_time.tzinfo is None:
                event_time = event_time.replace(tzinfo=timezone.utc)

            # Coordinates - try direct fields first, then parse from 'cod'
            lat = quake.get("lat")
            lon = quake.get("lon")
            depth = quake.get("dep")

            # If lat/lon not directly available, parse from 'cod' field
            # Format: "+34.5+135.2-10/" or similar
            if lat is None or lon is None:
                cod = quake.get("cod", "")
                if cod:
                    parsed = self._parse_cod(cod)
                    if parsed:
                        lat, lon, depth = parsed

            if lat is None or lon is None:
                return None

            # Event ID
            event_id = quake.get("eid") or quake.get("id")
            if not event_id:
                # Generate ID from time and location
                event_id = f"jma_{event_time.timestamp()}_{lat}_{lon}"

            # Location name (English if available)
            location = quake.get("en_anm") or quake.get("anm") or "Japan region"

            # Report publication time (rdt) - used for deduplication
            # Later reports are more accurate (refined magnitude estimates)
            reported_at = None
            rdt_str = quake.get("rdt")
            if rdt_str:
                try:
                    reported_at = datetime.fromisoformat(rdt_str)
                except (ValueError, TypeError):
                    pass

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
                reported_at=reported_at,
                received_at=datetime.now(timezone.utc),
                raw_data=quake,
            )
        except Exception as e:
            logger.warning(f"[JMA] Parse error: {e}, data: {quake}")
            return None

    def _parse_cod(self, cod: str) -> Optional[tuple]:
        """
        Parse JMA 'cod' field for coordinates.

        Format examples:
        - "+34.5+135.2-10/" -> lat=34.5, lon=135.2, depth=10
        - "+38.9+142.1+30/" -> lat=38.9, lon=142.1, depth=30 (+ means above sea level, rare)
        """
        try:
            if not cod:
                return None

            # Remove trailing slash and spaces
            cod = cod.strip().rstrip("/").split()[0] if cod.strip() else ""
            if not cod:
                return None

            # Find positions of + and - signs
            # First character is always sign for latitude
            lat_sign = 1 if cod[0] == "+" else -1
            cod = cod[1:]  # Remove first sign

            # Find the second sign (start of longitude)
            lon_start = -1
            for i, c in enumerate(cod):
                if c in ["+", "-"]:
                    lon_start = i
                    break

            if lon_start == -1:
                return None

            lat = lat_sign * float(cod[:lon_start])

            # Parse longitude
            lon_sign = 1 if cod[lon_start] == "+" else -1
            cod = cod[lon_start + 1:]

            # Find the third sign (start of depth)
            depth_start = -1
            for i, c in enumerate(cod):
                if c in ["+", "-"]:
                    depth_start = i
                    break

            if depth_start == -1:
                lon = lon_sign * float(cod)
                depth = None
            else:
                lon = lon_sign * float(cod[:depth_start])
                depth_sign = 1 if cod[depth_start] == "+" else -1
                depth = depth_sign * float(cod[depth_start + 1:])

            return (lat, lon, depth)
        except Exception:
            return None
