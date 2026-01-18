"""
Helper module to read Monitor Bot's JSON cache from Trading Bot.

Usage:
    from monitor_cache import get_monitor_events, get_edge_time

    # Get all events
    events = get_monitor_events()

    # Get edge time for specific USGS event
    edge = get_edge_time("us7000p123")
"""

import json
from pathlib import Path
from typing import Optional, List, Dict
from datetime import datetime


CACHE_FILE = Path(__file__).parent / "monitor_bot" / "data" / "events_cache.json"


def get_monitor_events() -> List[Dict]:
    """
    Read all events from monitor bot's JSON cache.

    Returns:
        List of event dictionaries, or empty list if cache doesn't exist.
    """
    if not CACHE_FILE.exists():
        return []

    try:
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data.get("events", [])
    except Exception as e:
        print(f"Warning: Could not read monitor cache: {e}")
        return []


def get_edge_time(usgs_id: str) -> Optional[float]:
    """
    Get edge time (in minutes) for a specific USGS event.

    Args:
        usgs_id: USGS event ID (e.g., "us7000p123")

    Returns:
        Edge time in minutes if found and positive, None otherwise.

    Example:
        edge = get_edge_time("us7000p123")
        if edge:
            print(f"We detected {edge:.1f} minutes before USGS!")
        else:
            print("No information advantage")
    """
    events = get_monitor_events()

    for event in events:
        if event.get("usgs_id") == usgs_id:
            return event.get("detection_advantage_minutes")

    return None


def get_event_sources(usgs_id: str) -> List[str]:
    """
    Get list of sources that detected this event.

    Args:
        usgs_id: USGS event ID

    Returns:
        List of source names (e.g., ["JMA", "EMSC", "USGS"])
    """
    events = get_monitor_events()

    for event in events:
        if event.get("usgs_id") == usgs_id:
            sources = []
            if event.get("jma_id"):
                sources.append("JMA")
            if event.get("emsc_id"):
                sources.append("EMSC")
            if event.get("gfz_id"):
                sources.append("GFZ")
            if event.get("geonet_id"):
                sources.append("GEONET")
            if event.get("usgs_id"):
                sources.append("USGS")
            return sources

    return []


def get_cache_info() -> Dict:
    """
    Get metadata about the cache file.

    Returns:
        Dictionary with cache metadata (last_updated, event_count, etc.)
    """
    if not CACHE_FILE.exists():
        return {
            "exists": False,
            "last_updated": None,
            "event_count": 0,
        }

    try:
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)

        return {
            "exists": True,
            "last_updated": data.get("last_updated"),
            "event_count": data.get("event_count", len(data.get("events", []))),
            "file_size_kb": CACHE_FILE.stat().st_size / 1024,
        }
    except Exception as e:
        return {
            "exists": True,
            "error": str(e),
        }


if __name__ == "__main__":
    # Test
    info = get_cache_info()
    print(f"Cache info: {info}")

    events = get_monitor_events()
    print(f"\nTotal events: {len(events)}")

    if events:
        # Show first event
        event = events[0]
        print(f"\nFirst event:")
        print(f"  Magnitude: M{event['best_magnitude']}")
        print(f"  Location: {event['location_name']}")
        print(f"  USGS ID: {event.get('usgs_id', 'N/A')}")
        print(f"  Edge Time: {event.get('detection_advantage_minutes', 'N/A')} min")

        # Test edge time lookup
        if event.get('usgs_id'):
            edge = get_edge_time(event['usgs_id'])
            sources = get_event_sources(event['usgs_id'])
            print(f"\nLookup test:")
            print(f"  Edge: {edge} min")
            print(f"  Sources: {sources}")
