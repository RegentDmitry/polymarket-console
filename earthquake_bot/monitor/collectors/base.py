"""
Base collector class for earthquake data sources.
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import AsyncIterator, Optional, Set

from ..models import SourceReport
from ..config import config

logger = logging.getLogger(__name__)


class BaseCollector(ABC):
    """Base class for all earthquake data collectors."""

    SOURCE_NAME: str = "unknown"
    POLL_INTERVAL: int = 60  # seconds

    def __init__(self):
        self._seen_ids: Set[str] = set()
        self._running = False
        self._last_poll: Optional[datetime] = None

    @property
    def name(self) -> str:
        return self.SOURCE_NAME

    @abstractmethod
    async def fetch_earthquakes(self) -> AsyncIterator[SourceReport]:
        """Fetch new earthquakes from the source."""
        yield  # type: ignore

    async def poll_once(self) -> list[SourceReport]:
        """Poll once and return new earthquakes."""
        new_reports = []
        try:
            async for report in self.fetch_earthquakes():
                if report.source_event_id not in self._seen_ids:
                    self._seen_ids.add(report.source_event_id)
                    new_reports.append(report)
                    logger.info(
                        f"[{self.SOURCE_NAME}] New M{report.magnitude} at {report.location_name}"
                    )
        except Exception as e:
            logger.error(f"[{self.SOURCE_NAME}] Error polling: {e}")

        self._last_poll = datetime.now(timezone.utc)
        return new_reports

    async def run(self, callback) -> None:
        """
        Run collector in a loop.

        Args:
            callback: async function to call with new reports
        """
        self._running = True
        logger.info(f"[{self.SOURCE_NAME}] Starting collector (interval: {self.POLL_INTERVAL}s)")

        while self._running:
            try:
                reports = await self.poll_once()
                for report in reports:
                    await callback(report)
            except Exception as e:
                logger.error(f"[{self.SOURCE_NAME}] Error in run loop: {e}")

            await asyncio.sleep(self.POLL_INTERVAL)

    def stop(self):
        """Stop the collector."""
        self._running = False
        logger.info(f"[{self.SOURCE_NAME}] Stopping collector")

    def _filter_by_magnitude(self, magnitude: float) -> bool:
        """Check if magnitude meets minimum threshold."""
        return magnitude >= config.MIN_MAGNITUDE_TRACK
