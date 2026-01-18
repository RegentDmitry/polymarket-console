#!/usr/bin/env python3
"""
Test script to debug USGS event matching issue.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from monitor.database import Database
from monitor.services.event_matcher import EventMatcher
from monitor.collectors.usgs import USGSCollector
from monitor.collectors.emsc import EMSCCollector
from monitor.config import config
from monitor_bot.config import config as bot_config

async def test_matching():
    """Test event matching between USGS and EMSC."""

    db = Database()
    matcher = EventMatcher()

    # Try to connect to DB
    print(f"USE_DATABASE: {config.USE_DATABASE}")
    print(f"DB connected: {db._pool is not None or db._sync_conn is not None}")

    # Collect EMSC events
    print("\n=== Collecting EMSC events ===")
    emsc = EMSCCollector()
    emsc_events = []
    async for report in emsc.fetch_earthquakes():
        event = matcher.create_event_from_report(report)
        emsc_events.append(event)
        print(f"EMSC: M{report.magnitude} at {report.location_name} ({report.latitude}, {report.longitude}) @ {report.event_time}")
        if len(emsc_events) >= 10:
            break

    print(f"\nCollected {len(emsc_events)} EMSC events")

    # Collect USGS events
    print("\n=== Collecting USGS events ===")
    usgs = USGSCollector()
    usgs_reports = []
    async for report in usgs.fetch_earthquakes():
        usgs_reports.append(report)
        print(f"USGS: M{report.magnitude} at {report.location_name} ({report.latitude}, {report.longitude}) @ {report.event_time}")
        if len(usgs_reports) >= 10:
            break

    print(f"\nCollected {len(usgs_reports)} USGS reports")

    # Try matching
    print("\n=== Testing matching ===")
    for usgs_report in usgs_reports:
        print(f"\nUSGS Event: M{usgs_report.magnitude} at {usgs_report.location_name}")
        print(f"  Time: {usgs_report.event_time}")
        print(f"  Coords: {usgs_report.latitude}, {usgs_report.longitude}")

        matched_id = matcher.find_matching_event(usgs_report, emsc_events)

        if matched_id:
            matched_event = next(e for e in emsc_events if e.event_id == matched_id)
            print(f"  ✅ MATCHED to EMSC event:")
            print(f"     M{matched_event.best_magnitude} at {matched_event.location_name}")
            print(f"     Time: {matched_event.event_time}")
            print(f"     Coords: {matched_event.latitude}, {matched_event.longitude}")

            # Calculate time and distance difference
            time_diff = abs((usgs_report.event_time - matched_event.event_time).total_seconds())
            from monitor.services.event_matcher import haversine_distance
            distance = haversine_distance(
                usgs_report.latitude, usgs_report.longitude,
                matched_event.latitude, matched_event.longitude
            )
            print(f"     Time diff: {time_diff:.1f} sec, Distance: {distance:.1f} km")
        else:
            print(f"  ❌ NO MATCH found")

            # Find closest EMSC event by time
            closest = None
            min_time_diff = float('inf')
            for emsc_event in emsc_events:
                time_diff = abs((usgs_report.event_time - emsc_event.event_time).total_seconds())
                if time_diff < min_time_diff:
                    min_time_diff = time_diff
                    closest = emsc_event

            if closest:
                from monitor.services.event_matcher import haversine_distance
                distance = haversine_distance(
                    usgs_report.latitude, usgs_report.longitude,
                    closest.latitude, closest.longitude
                )
                print(f"     Closest EMSC event:")
                print(f"     M{closest.best_magnitude} at {closest.location_name}")
                print(f"     Time diff: {min_time_diff:.1f} sec ({min_time_diff/60:.1f} min)")
                print(f"     Distance: {distance:.1f} km")
                print(f"     Coords: {closest.latitude}, {closest.longitude}")

                # Check why it didn't match
                if min_time_diff > config.MATCH_TIME_WINDOW_SEC:
                    print(f"     ⚠️ Time diff {min_time_diff:.1f}s > threshold {config.MATCH_TIME_WINDOW_SEC}s")
                if distance > config.MATCH_DISTANCE_KM:
                    print(f"     ⚠️ Distance {distance:.1f}km > threshold {config.MATCH_DISTANCE_KM}km")

if __name__ == "__main__":
    asyncio.run(test_matching())
