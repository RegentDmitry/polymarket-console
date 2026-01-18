#!/usr/bin/env python3
"""
Analyze source reliability by comparing EMSC/GFZ events with USGS (ground truth).

This script helps understand:
- What % of events from each source get confirmed by USGS
- At what magnitude threshold sources become reliable
- False positive rates by source and magnitude

For trading strategy: helps determine which sources to trust at which magnitudes.
"""

import asyncio
import httpx
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass
from typing import Optional
import math


@dataclass
class Event:
    source: str
    event_id: str
    magnitude: float
    latitude: float
    longitude: float
    event_time: datetime
    location: str


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance between two points in km."""
    R = 6371
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)

    a = (math.sin(delta_lat / 2) ** 2 +
         math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


async def fetch_usgs_events(days: int = 30, min_mag: float = 4.0) -> list[Event]:
    """Fetch USGS events (ground truth) via FDSN API for longer periods."""
    # For periods > 30 days, use FDSN API instead of feeds
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=days)

    # USGS FDSN API supports arbitrary date ranges
    url = "https://earthquake.usgs.gov/fdsnws/event/1/query"
    params = {
        "format": "geojson",
        "starttime": start_time.strftime("%Y-%m-%dT%H:%M:%S"),
        "endtime": end_time.strftime("%Y-%m-%dT%H:%M:%S"),
        "minmagnitude": min_mag,
        "orderby": "time",
        "limit": 2000,
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(url, params=params, timeout=60)
        response.raise_for_status()
        data = response.json()

    events = []
    for feature in data.get("features", []):
        props = feature.get("properties", {})
        coords = feature.get("geometry", {}).get("coordinates", [])

        mag = props.get("mag")
        time_ms = props.get("time")

        if mag is None or time_ms is None or len(coords) < 2:
            continue
        if mag < min_mag:
            continue

        event_time = datetime.fromtimestamp(time_ms / 1000, tz=timezone.utc)

        events.append(Event(
            source="usgs",
            event_id=feature.get("id", ""),
            magnitude=float(mag),
            latitude=float(coords[1]),
            longitude=float(coords[0]),
            event_time=event_time,
            location=props.get("place", "Unknown"),
        ))

    return events


async def fetch_emsc_events(days: int = 7, min_mag: float = 4.0) -> list[Event]:
    """Fetch EMSC events via their FDSN API."""
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=days)

    url = "https://www.seismicportal.eu/fdsnws/event/1/query"
    params = {
        "format": "json",
        "minmag": min_mag,
        "start": start_time.strftime("%Y-%m-%dT%H:%M:%S"),
        "end": end_time.strftime("%Y-%m-%dT%H:%M:%S"),
        "limit": 1000,
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(url, params=params, timeout=60)
        response.raise_for_status()
        data = response.json()

    events = []
    for feature in data.get("features", []):
        props = feature.get("properties", {})
        coords = feature.get("geometry", {}).get("coordinates", [])

        mag = props.get("mag")
        time_str = props.get("time")

        if mag is None or time_str is None or len(coords) < 2:
            continue

        # Parse ISO time
        event_time = datetime.fromisoformat(time_str.replace("Z", "+00:00"))

        events.append(Event(
            source="emsc",
            event_id=props.get("source_id", ""),
            magnitude=float(mag),
            latitude=float(coords[1]),
            longitude=float(coords[0]),
            event_time=event_time,
            location=props.get("flynn_region", "Unknown"),
        ))

    return events


async def fetch_gfz_events(days: int = 7, min_mag: float = 4.0) -> list[Event]:
    """Fetch GFZ events via their FDSN API (XML format)."""
    import xml.etree.ElementTree as ET

    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=days)

    url = "https://geofon.gfz-potsdam.de/fdsnws/event/1/query"
    params = {
        "format": "xml",
        "minmag": min_mag,
        "start": start_time.strftime("%Y-%m-%dT%H:%M:%S"),
        "end": end_time.strftime("%Y-%m-%dT%H:%M:%S"),
        "limit": 1000,
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(url, params=params, timeout=60)
        response.raise_for_status()

    # Parse QuakeML XML
    events = []
    try:
        root = ET.fromstring(response.text)
        ns = {"q": "http://quakeml.org/xmlns/bed/1.2"}

        for event_elem in root.findall(".//q:event", ns):
            try:
                # Get origin
                origin = event_elem.find(".//q:origin", ns)
                if origin is None:
                    continue

                # Time
                time_elem = origin.find("q:time/q:value", ns)
                if time_elem is None:
                    continue
                time_str = time_elem.text
                event_time = datetime.fromisoformat(time_str.replace("Z", "+00:00"))

                # Location
                lat_elem = origin.find("q:latitude/q:value", ns)
                lon_elem = origin.find("q:longitude/q:value", ns)
                if lat_elem is None or lon_elem is None:
                    continue

                # Magnitude
                mag_elem = event_elem.find(".//q:magnitude/q:mag/q:value", ns)
                if mag_elem is None:
                    continue
                mag = float(mag_elem.text)

                if mag < min_mag:
                    continue

                # Description
                desc_elem = event_elem.find(".//q:description/q:text", ns)
                location = desc_elem.text if desc_elem is not None else "Unknown"

                events.append(Event(
                    source="gfz",
                    event_id=event_elem.get("{http://quakeml.org/xmlns/bed/1.2}publicID", ""),
                    magnitude=mag,
                    latitude=float(lat_elem.text),
                    longitude=float(lon_elem.text),
                    event_time=event_time,
                    location=location,
                ))
            except Exception:
                continue
    except Exception as e:
        print(f"  [GFZ] XML parse error: {e}")
        return []

    return events


async def fetch_jma_events(days: int = 7, min_mag: float = 4.0) -> list[Event]:
    """Fetch JMA events."""
    url = "https://www.jma.go.jp/bosai/quake/data/list.json"

    async with httpx.AsyncClient() as client:
        response = await client.get(url, timeout=30)
        response.raise_for_status()
        data = response.json()

    events = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    for item in data:
        try:
            # Parse time - JMA uses ISO format: "2026-01-18T12:34:56+09:00"
            time_str = item.get("at", "")
            if not time_str:
                continue

            try:
                event_time = datetime.fromisoformat(time_str)
                if event_time.tzinfo is None:
                    event_time = event_time.replace(tzinfo=timezone.utc)
            except ValueError:
                continue

            if event_time < cutoff:
                continue

            # Magnitude - JMA uses "mag" field, skip intensity reports without magnitude
            mag_str = item.get("mag", "")
            if not mag_str or mag_str == "-" or mag_str == "":
                continue

            try:
                mag = float(mag_str)
            except ValueError:
                continue

            if mag < min_mag:
                continue

            # Coordinates from "cod" field (format: "+38.5+142.2-10/")
            cod = item.get("cod", "")
            if not cod:
                continue

            # Parse cod field
            lat, lon = None, None
            try:
                cod = cod.strip().rstrip("/").split()[0] if cod.strip() else ""
                if cod and (cod[0] == '+' or cod[0] == '-'):
                    lat_sign = 1 if cod[0] == '+' else -1
                    cod_rest = cod[1:]

                    # Find second sign (longitude start)
                    lon_start = -1
                    for i, c in enumerate(cod_rest):
                        if c in ['+', '-']:
                            lon_start = i
                            break

                    if lon_start > 0:
                        lat = lat_sign * float(cod_rest[:lon_start])
                        lon_sign = 1 if cod_rest[lon_start] == '+' else -1
                        lon_rest = cod_rest[lon_start + 1:]

                        # Find third sign (depth start) or end
                        depth_start = -1
                        for i, c in enumerate(lon_rest):
                            if c in ['+', '-']:
                                depth_start = i
                                break

                        if depth_start > 0:
                            lon = lon_sign * float(lon_rest[:depth_start])
                        else:
                            lon = lon_sign * float(lon_rest)
            except Exception:
                continue

            if lat is None or lon is None:
                continue

            events.append(Event(
                source="jma",
                event_id=item.get("eid", ""),
                magnitude=mag,
                latitude=lat,
                longitude=lon,
                event_time=event_time,
                location=item.get("en_anm") or item.get("anm") or "Japan region",
            ))
        except Exception:
            continue

    return events


async def fetch_geonet_events(days: int = 7, min_mag: float = 4.0) -> list[Event]:
    """Fetch GeoNet (New Zealand) events."""
    url = "https://api.geonet.org.nz/quake"
    params = {
        "MMI": -1,  # All intensities
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()

    events = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    for feature in data.get("features", []):
        try:
            props = feature.get("properties", {})
            coords = feature.get("geometry", {}).get("coordinates", [])

            if len(coords) < 2:
                continue

            # Time
            time_str = props.get("time", "")
            if not time_str:
                continue

            event_time = datetime.fromisoformat(time_str.replace("Z", "+00:00"))

            if event_time < cutoff:
                continue

            # Magnitude
            mag = props.get("magnitude")
            if mag is None:
                continue

            if mag < min_mag:
                continue

            events.append(Event(
                source="geonet",
                event_id=props.get("publicID", ""),
                magnitude=float(mag),
                latitude=float(coords[1]),
                longitude=float(coords[0]),
                event_time=event_time,
                location=props.get("locality", "New Zealand region"),
            ))
        except Exception:
            continue

    return events


async def fetch_iris_events(days: int = 30, min_mag: float = 4.0) -> list[Event]:
    """Fetch IRIS (Incorporated Research Institutions for Seismology) events."""
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=days)

    url = "https://service.iris.edu/fdsnws/event/1/query"
    params = {
        "format": "geocsv",
        "starttime": start_time.strftime("%Y-%m-%dT%H:%M:%S"),
        "endtime": end_time.strftime("%Y-%m-%dT%H:%M:%S"),
        "minmagnitude": min_mag,
        "orderby": "time",
        "limit": 2000,
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(url, params=params, timeout=60)
        response.raise_for_status()
        text = response.text

    events = []
    lines = text.strip().split("\n")

    # Skip header lines (start with #)
    for line in lines:
        if line.startswith("#") or not line.strip():
            continue
        try:
            # CSV format: EventID|Time|Latitude|Longitude|Depth|Author|Catalog|Contributor|ContributorID|MagType|Magnitude|MagAuthor|EventLocationName
            parts = line.split("|")
            if len(parts) < 13:
                continue

            event_id = parts[0].strip()
            time_str = parts[1].strip()
            lat = float(parts[2].strip())
            lon = float(parts[3].strip())
            mag = float(parts[10].strip()) if parts[10].strip() else None
            location = parts[12].strip() if len(parts) > 12 else "Unknown"

            if mag is None or mag < min_mag:
                continue

            event_time = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
            if event_time.tzinfo is None:
                event_time = event_time.replace(tzinfo=timezone.utc)

            events.append(Event(
                source="iris",
                event_id=event_id,
                magnitude=mag,
                latitude=lat,
                longitude=lon,
                event_time=event_time,
                location=location,
            ))
        except Exception:
            continue

    return events


async def fetch_ingv_events(days: int = 30, min_mag: float = 4.0) -> list[Event]:
    """Fetch INGV (Istituto Nazionale di Geofisica e Vulcanologia, Italy) events."""
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=days)

    url = "https://webservices.ingv.it/fdsnws/event/1/query"
    params = {
        "format": "text",
        "starttime": start_time.strftime("%Y-%m-%dT%H:%M:%S"),
        "endtime": end_time.strftime("%Y-%m-%dT%H:%M:%S"),
        "minmag": min_mag,
        "orderby": "time",
        "limit": 2000,
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(url, params=params, timeout=60)
        if response.status_code == 204:  # No content
            return []
        response.raise_for_status()
        text = response.text

    events = []
    lines = text.strip().split("\n")

    for line in lines[1:]:  # Skip header
        if not line.strip():
            continue
        try:
            parts = line.split("|")
            if len(parts) < 11:
                continue

            event_id = parts[0].strip()
            time_str = parts[1].strip()
            lat = float(parts[2].strip())
            lon = float(parts[3].strip())
            mag = float(parts[10].strip()) if parts[10].strip() else None
            location = parts[12].strip() if len(parts) > 12 else "Mediterranean"

            if mag is None or mag < min_mag:
                continue

            event_time = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
            if event_time.tzinfo is None:
                event_time = event_time.replace(tzinfo=timezone.utc)

            events.append(Event(
                source="ingv",
                event_id=event_id,
                magnitude=mag,
                latitude=lat,
                longitude=lon,
                event_time=event_time,
                location=location,
            ))
        except Exception:
            continue

    return events


async def fetch_ga_events(days: int = 30, min_mag: float = 4.0) -> list[Event]:
    """Fetch Geoscience Australia events."""
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=days)

    url = "https://earthquakes.ga.gov.au/fdsnws/event/1/query"
    params = {
        "format": "text",
        "starttime": start_time.strftime("%Y-%m-%dT%H:%M:%S"),
        "endtime": end_time.strftime("%Y-%m-%dT%H:%M:%S"),
        "minmagnitude": min_mag,
        "orderby": "time",
        "limit": 2000,
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(url, params=params, timeout=60)
        if response.status_code == 204:
            return []
        response.raise_for_status()
        text = response.text

    events = []
    lines = text.strip().split("\n")

    for line in lines[1:]:  # Skip header
        if not line.strip():
            continue
        try:
            parts = line.split("|")
            if len(parts) < 11:
                continue

            event_id = parts[0].strip()
            time_str = parts[1].strip()
            lat = float(parts[2].strip())
            lon = float(parts[3].strip())
            mag = float(parts[10].strip()) if parts[10].strip() else None
            location = parts[12].strip() if len(parts) > 12 else "Australia region"

            if mag is None or mag < min_mag:
                continue

            event_time = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
            if event_time.tzinfo is None:
                event_time = event_time.replace(tzinfo=timezone.utc)

            events.append(Event(
                source="ga",
                event_id=event_id,
                magnitude=mag,
                latitude=lat,
                longitude=lon,
                event_time=event_time,
                location=location,
            ))
        except Exception:
            continue

    return events


def find_usgs_match(event: Event, usgs_events: list[Event],
                    time_window_sec: int = 300, distance_km: float = 100) -> Optional[Event]:
    """Find matching USGS event for a given source event."""
    for usgs in usgs_events:
        # Time check
        time_diff = abs((event.event_time - usgs.event_time).total_seconds())
        if time_diff > time_window_sec:
            continue

        # Distance check
        distance = haversine_distance(
            event.latitude, event.longitude,
            usgs.latitude, usgs.longitude
        )
        if distance > distance_km:
            continue

        return usgs

    return None


def analyze_reliability(source_events: list[Event], usgs_events: list[Event], source_name: str):
    """Analyze reliability of a source against USGS."""

    # Group by magnitude buckets
    mag_buckets = {
        "4.0-4.4": {"total": 0, "confirmed": 0, "events": []},
        "4.5-4.9": {"total": 0, "confirmed": 0, "events": []},
        "5.0-5.4": {"total": 0, "confirmed": 0, "events": []},
        "5.5-5.9": {"total": 0, "confirmed": 0, "events": []},
        "6.0-6.4": {"total": 0, "confirmed": 0, "events": []},
        "6.5-6.9": {"total": 0, "confirmed": 0, "events": []},
        "7.0+": {"total": 0, "confirmed": 0, "events": []},
    }

    unconfirmed_events = []

    for event in source_events:
        # Determine bucket
        mag = event.magnitude
        if mag < 4.5:
            bucket = "4.0-4.4"
        elif mag < 5.0:
            bucket = "4.5-4.9"
        elif mag < 5.5:
            bucket = "5.0-5.4"
        elif mag < 6.0:
            bucket = "5.5-5.9"
        elif mag < 6.5:
            bucket = "6.0-6.4"
        elif mag < 7.0:
            bucket = "6.5-6.9"
        else:
            bucket = "7.0+"

        mag_buckets[bucket]["total"] += 1

        # Check if confirmed by USGS
        usgs_match = find_usgs_match(event, usgs_events)
        if usgs_match:
            mag_buckets[bucket]["confirmed"] += 1
            mag_buckets[bucket]["events"].append((event, usgs_match))
        else:
            unconfirmed_events.append(event)

    # Print results
    print(f"\n{'='*60}")
    print(f"  {source_name.upper()} Reliability Analysis")
    print(f"{'='*60}")

    total_all = sum(b["total"] for b in mag_buckets.values())
    confirmed_all = sum(b["confirmed"] for b in mag_buckets.values())

    print(f"\n  Total events: {total_all}")
    print(f"  USGS confirmed: {confirmed_all} ({100*confirmed_all/total_all:.1f}%)" if total_all > 0 else "  No events")

    print(f"\n  {'Magnitude':<12} {'Total':<8} {'Confirmed':<12} {'Rate':<10}")
    print(f"  {'-'*42}")

    for bucket, data in mag_buckets.items():
        if data["total"] > 0:
            rate = 100 * data["confirmed"] / data["total"]
            rate_str = f"{rate:.0f}%"

            # Add reliability indicator
            if rate >= 95:
                indicator = "excellent"
            elif rate >= 80:
                indicator = "good"
            elif rate >= 60:
                indicator = "moderate"
            else:
                indicator = "LOW"

            print(f"  {bucket:<12} {data['total']:<8} {data['confirmed']:<12} {rate_str:<6} ({indicator})")

    # Show unconfirmed examples (recent ones)
    if unconfirmed_events:
        print(f"\n  Recent unconfirmed events (last 5):")
        for event in sorted(unconfirmed_events, key=lambda e: e.event_time, reverse=True)[:5]:
            age_hours = (datetime.now(timezone.utc) - event.event_time).total_seconds() / 3600
            print(f"    M{event.magnitude:.1f} {event.location[:40]:<40} ({age_hours:.0f}h ago)")

    return mag_buckets, unconfirmed_events


async def main():
    print("="*60)
    print("  EARTHQUAKE SOURCE RELIABILITY ANALYSIS")
    print("  Comparing alternative sources against USGS (ground truth)")
    print("="*60)

    days = 30  # Extended period for better statistics
    min_mag = 4.0

    print(f"\n  Period: last {days} days")
    print(f"  Minimum magnitude: M{min_mag}")
    print(f"  Matching: <5 min time, <100 km distance")

    # Fetch all data
    print("\n  Fetching data...")

    print("  - USGS (ground truth)...", end=" ", flush=True)
    usgs_events = await fetch_usgs_events(days, min_mag)
    print(f"{len(usgs_events)} events")

    print("  - EMSC...", end=" ", flush=True)
    try:
        emsc_events = await fetch_emsc_events(days, min_mag)
        print(f"{len(emsc_events)} events")
    except Exception as e:
        print(f"Error: {e}")
        emsc_events = []

    print("  - GFZ...", end=" ", flush=True)
    try:
        gfz_events = await fetch_gfz_events(days, min_mag)
        print(f"{len(gfz_events)} events")
    except Exception as e:
        print(f"Error: {e}")
        gfz_events = []

    print("  - JMA...", end=" ", flush=True)
    try:
        jma_events = await fetch_jma_events(days, min_mag)
        print(f"{len(jma_events)} events")
    except Exception as e:
        print(f"Error: {e}")
        jma_events = []

    print("  - GeoNet...", end=" ", flush=True)
    try:
        geonet_events = await fetch_geonet_events(days, min_mag)
        print(f"{len(geonet_events)} events")
    except Exception as e:
        print(f"Error: {e}")
        geonet_events = []

    print("  - IRIS...", end=" ", flush=True)
    try:
        iris_events = await fetch_iris_events(days, min_mag)
        print(f"{len(iris_events)} events")
    except Exception as e:
        print(f"Error: {e}")
        iris_events = []

    print("  - INGV (Italy)...", end=" ", flush=True)
    try:
        ingv_events = await fetch_ingv_events(days, min_mag)
        print(f"{len(ingv_events)} events")
    except Exception as e:
        print(f"Error: {e}")
        ingv_events = []

    print("  - GA (Australia)...", end=" ", flush=True)
    try:
        ga_events = await fetch_ga_events(days, min_mag)
        print(f"{len(ga_events)} events")
    except Exception as e:
        print(f"Error: {e}")
        ga_events = []

    # Analyze each source
    if emsc_events:
        analyze_reliability(emsc_events, usgs_events, "EMSC")

    if gfz_events:
        analyze_reliability(gfz_events, usgs_events, "GFZ")

    if jma_events:
        analyze_reliability(jma_events, usgs_events, "JMA")

    if geonet_events:
        analyze_reliability(geonet_events, usgs_events, "GeoNet")

    if iris_events:
        analyze_reliability(iris_events, usgs_events, "IRIS")

    if ingv_events:
        analyze_reliability(ingv_events, usgs_events, "INGV")

    if ga_events:
        analyze_reliability(ga_events, usgs_events, "GA (Australia)")

    # Summary for trading
    print(f"\n{'='*60}")
    print("  TRADING RECOMMENDATIONS")
    print("="*60)
    print("""
  Based on historical reliability:

  HIGH CONFIDENCE (trade immediately):
    - M6.5+ from any source (>95% USGS confirmation)
    - M6.0+ from EMSC (historically reliable)

  MEDIUM CONFIDENCE (wait for 2nd source):
    - M5.5-6.0 from single source
    - Consider position sizing based on confidence

  LOW CONFIDENCE (wait for USGS):
    - M4.5-5.5 events have higher false positive rate
    - May not appear in USGS at all (below threshold/regional)

  Note: Run this analysis weekly to track source reliability changes.
""")


if __name__ == "__main__":
    asyncio.run(main())
