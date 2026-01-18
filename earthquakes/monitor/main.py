#!/usr/bin/env python3
"""
Real-time earthquake monitoring system.

Collects earthquake data from multiple sources in parallel
and stores in PostgreSQL database.

Usage:
    python -m monitor.main                    # Run all collectors
    python -m monitor.main --sources jma,usgs # Run specific collectors
    python -m monitor.main --status           # Show current status
"""

import argparse
import asyncio
import logging
import signal
import sys
from datetime import datetime, timezone
from typing import Optional

from .config import config
from .database import Database
from .models import SourceReport, EarthquakeEvent
from .services.event_matcher import EventMatcher
from .collectors import (
    JMACollector,
    EMSCCollector,
    GFZCollector,
    USGSCollector,
    IRISCollector,
    INGVCollector,
)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


class EarthquakeMonitor:
    """Main earthquake monitoring service."""

    COLLECTORS = {
        "jma": JMACollector,
        "emsc": EMSCCollector,
        "gfz": GFZCollector,
        "usgs": USGSCollector,
        "iris": IRISCollector,
        "ingv": INGVCollector,
    }

    def __init__(self, sources: Optional[list[str]] = None):
        self.db = Database()
        self.matcher = EventMatcher()
        self._running = False
        self._collectors = []
        self._tasks = []

        # Select which collectors to run
        if sources:
            self._source_names = [s.lower() for s in sources]
        else:
            self._source_names = list(self.COLLECTORS.keys())

    async def start(self):
        """Start the monitoring service."""
        logger.info("=" * 60)
        logger.info("EARTHQUAKE MONITORING SERVICE")
        logger.info("=" * 60)

        # Connect to database
        await self.db.connect()
        logger.info(f"Database: {config.DB_HOST}:{config.DB_PORT}/{config.DB_NAME}")

        # Initialize collectors
        for name in self._source_names:
            if name in self.COLLECTORS:
                collector = self.COLLECTORS[name]()
                self._collectors.append(collector)
                logger.info(f"Initialized collector: {name.upper()}")

        # Show current status
        await self._print_status()

        # Start collectors
        self._running = True
        logger.info("-" * 60)
        logger.info("Starting collectors...")

        for collector in self._collectors:
            task = asyncio.create_task(
                collector.run(self._handle_report)
            )
            self._tasks.append(task)

        logger.info(f"Running {len(self._collectors)} collectors in parallel")
        logger.info("Press Ctrl+C to stop")
        logger.info("-" * 60)

        # Wait for all tasks
        try:
            await asyncio.gather(*self._tasks)
        except asyncio.CancelledError:
            pass

    async def stop(self):
        """Stop the monitoring service."""
        logger.info("Stopping monitoring service...")
        self._running = False

        for collector in self._collectors:
            collector.stop()

        for task in self._tasks:
            task.cancel()

        await self.db.close()
        logger.info("Stopped")

    async def _handle_report(self, report: SourceReport):
        """Handle incoming earthquake report."""
        try:
            # Check if we already have this source event
            existing = await self.db.get_event_by_source_id(
                report.source, report.source_event_id
            )

            if existing:
                # Already processed this exact report
                return

            # Get recent events for matching
            recent_events = await self.db.get_recent_events(
                hours=24,
                min_magnitude=config.MIN_MAGNITUDE_TRACK - 0.5,
            )

            # Try to match to existing event
            matched_id = self.matcher.find_matching_event(report, recent_events)

            if matched_id:
                # Update existing event
                event = next(e for e in recent_events if e.event_id == matched_id)
                event = self.matcher.update_event_from_report(event, report)
                await self.db.update_event(event)
                await self.db.insert_report(report, event.event_id)

                logger.info(
                    f"[{report.source.upper()}] Matched M{report.magnitude} to existing event "
                    f"(now {event.source_count} sources)"
                )

                # If USGS just confirmed, log the advantage
                if report.source == "usgs" and event.detection_advantage_minutes:
                    logger.info(
                        f"  -> USGS confirmed! Detection advantage: "
                        f"{event.detection_advantage_minutes:.1f} minutes"
                    )
            else:
                # Create new event
                event = self.matcher.create_event_from_report(report)
                await self.db.insert_event(event)
                await self.db.insert_report(report, event.event_id)

                # Log with emphasis for significant events
                if event.is_significant:
                    logger.warning(
                        f"[{report.source.upper()}] NEW SIGNIFICANT EVENT: "
                        f"M{report.magnitude} at {report.location_name}"
                    )
                else:
                    logger.info(
                        f"[{report.source.upper()}] New event: "
                        f"M{report.magnitude} at {report.location_name}"
                    )

        except Exception as e:
            logger.error(f"Error handling report: {e}")

    async def _print_status(self):
        """Print current status and pending events."""
        print("\n" + "=" * 60)
        print("CURRENT STATUS")
        print("=" * 60)

        # Get pending events (not yet in USGS)
        pending = await self.db.get_pending_usgs_events(min_magnitude=6.5)

        if pending:
            print(f"\n*** EXTENDED HISTORY EVENTS (not yet in USGS): {len(pending)} ***")
            print("-" * 60)
            for row in pending:
                print(
                    f"  M{float(row['best_magnitude']):.1f} | "
                    f"{row['location_name'] or 'Unknown'} | "
                    f"Sources: {row['source_count']} | "
                    f"Detected {float(row['minutes_since_detection']):.0f} min ago"
                )
            print()
        else:
            print("\nNo pending extended history events.")

        # Get recent confirmed events
        try:
            query = """
                SELECT
                    best_magnitude, location_name, source_count,
                    first_detected_at, usgs_published_at,
                    EXTRACT(EPOCH FROM (usgs_published_at - first_detected_at))/60 as advantage
                FROM earthquake_events
                WHERE best_magnitude >= 6.5
                  AND event_time > NOW() - INTERVAL '7 days'
                ORDER BY event_time DESC
                LIMIT 5
            """
            recent = await self.db.fetch(query)

            if recent:
                print("\nRecent M6.5+ events (last 7 days):")
                print("-" * 60)
                for row in recent:
                    advantage = row.get("advantage")
                    adv_str = f"+{advantage:.0f}min edge" if advantage else "pending"
                    print(
                        f"  M{float(row['best_magnitude']):.1f} | "
                        f"{row['location_name'] or 'Unknown'} | "
                        f"Sources: {row['source_count']} | "
                        f"{adv_str}"
                    )
        except Exception as e:
            logger.debug(f"Could not fetch recent events: {e}")

        print("=" * 60 + "\n")


async def show_status():
    """Show current status without running collectors."""
    db = Database()
    await db.connect()

    monitor = EarthquakeMonitor()
    monitor.db = db
    await monitor._print_status()

    await db.close()


async def main():
    parser = argparse.ArgumentParser(description="Earthquake Monitoring Service")
    parser.add_argument(
        "--sources",
        type=str,
        help="Comma-separated list of sources (jma,emsc,gfz,geonet,usgs)",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show current status and exit",
    )
    args = parser.parse_args()

    if args.status:
        await show_status()
        return

    sources = args.sources.split(",") if args.sources else None
    monitor = EarthquakeMonitor(sources=sources)

    # Handle shutdown signals
    loop = asyncio.get_event_loop()

    def signal_handler():
        asyncio.create_task(monitor.stop())

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            pass

    try:
        await monitor.start()
    except KeyboardInterrupt:
        await monitor.stop()


if __name__ == "__main__":
    asyncio.run(main())
