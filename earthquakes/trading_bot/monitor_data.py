"""
Module for reading earthquake data from monitor_bot JSON cache.

Provides early detection data that supplements USGS official data.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


@dataclass
class MonitorEvent:
    """Earthquake event from monitor_bot."""
    event_id: str
    magnitude: float
    magnitude_type: str
    latitude: float
    longitude: float
    depth_km: Optional[float]
    location_name: str
    event_time: datetime
    first_detected_at: datetime
    usgs_id: Optional[str]
    source_count: int
    # Source IDs
    jma_id: Optional[str] = None
    emsc_id: Optional[str] = None
    gfz_id: Optional[str] = None
    iris_id: Optional[str] = None
    ingv_id: Optional[str] = None

    @property
    def sources(self) -> list[str]:
        """List of sources that detected this event."""
        sources = []
        if self.jma_id:
            sources.append("JMA")
        if self.emsc_id:
            sources.append("EMSC")
        if self.gfz_id:
            sources.append("GFZ")
        if self.iris_id:
            sources.append("IRIS")
        if self.ingv_id:
            sources.append("INGV")
        if self.usgs_id:
            sources.append("USGS")
        return sources

    @property
    def is_usgs_confirmed(self) -> bool:
        """True if USGS has confirmed this event."""
        return self.usgs_id is not None


@dataclass
class MonitorData:
    """Container for monitor_bot data."""
    last_updated: Optional[datetime]
    events: list[MonitorEvent]
    extra_events: list[MonitorEvent]  # Events not yet confirmed by USGS

    @property
    def has_extra_events(self) -> bool:
        return len(self.extra_events) > 0


# Default path to monitor_bot JSON cache
DEFAULT_CACHE_PATH = Path(__file__).parent.parent / "monitor_bot" / "data" / "events_cache.json"


def load_monitor_data(cache_path: Optional[Path] = None) -> MonitorData:
    """
    Load earthquake data from monitor_bot JSON cache.

    Returns MonitorData with all events and extra events (not in USGS).
    If file doesn't exist or is invalid, returns empty data.
    """
    path = cache_path or DEFAULT_CACHE_PATH

    try:
        if not path.exists():
            return MonitorData(last_updated=None, events=[], extra_events=[])

        with open(path, "r") as f:
            data = json.load(f)

        # Parse last_updated
        last_updated = None
        if data.get("last_updated"):
            try:
                last_updated = datetime.fromisoformat(data["last_updated"])
            except (ValueError, TypeError):
                pass

        events = []
        extra_events = []

        for ev in data.get("events", []):
            try:
                # Parse event_time
                event_time = datetime.fromisoformat(ev["event_time"])
                if event_time.tzinfo is None:
                    event_time = event_time.replace(tzinfo=timezone.utc)

                # Parse first_detected_at
                first_detected = datetime.fromisoformat(ev["first_detected_at"])
                if first_detected.tzinfo is None:
                    first_detected = first_detected.replace(tzinfo=timezone.utc)

                event = MonitorEvent(
                    event_id=ev["event_id"],
                    magnitude=ev["best_magnitude"],
                    magnitude_type=ev.get("best_magnitude_type", ""),
                    latitude=ev["latitude"],
                    longitude=ev["longitude"],
                    depth_km=ev.get("depth_km"),
                    location_name=ev.get("location_name", "Unknown"),
                    event_time=event_time,
                    first_detected_at=first_detected,
                    usgs_id=ev.get("usgs_id"),
                    source_count=ev.get("source_count", 1),
                    jma_id=ev.get("jma_id"),
                    emsc_id=ev.get("emsc_id"),
                    gfz_id=ev.get("gfz_id"),
                    iris_id=ev.get("iris_id"),
                    ingv_id=ev.get("ingv_id"),
                )
                events.append(event)

                # Extra events = not confirmed by USGS
                if not event.is_usgs_confirmed:
                    extra_events.append(event)

            except (KeyError, ValueError, TypeError) as e:
                continue

        return MonitorData(
            last_updated=last_updated,
            events=events,
            extra_events=extra_events,
        )

    except (json.JSONDecodeError, IOError) as e:
        return MonitorData(last_updated=None, events=[], extra_events=[])


def format_extra_events(data: MonitorData) -> str:
    """
    Format extra events for display in TUI.

    Returns formatted string showing events not yet in USGS.
    Shows discounted magnitude and marks filtered-out events.
    """
    if not data.has_extra_events:
        return "[dim]No extra events[/dim]"

    from trading_bot.constants import get_mag_discount, EXTRA_EVENT_MAX_AGE_MINUTES

    lines = []
    for ev in data.extra_events:
        # Time since event
        now = datetime.now(timezone.utc)
        age_minutes = (now - ev.event_time).total_seconds() / 60

        if age_minutes < 60:
            age_str = f"{age_minutes:.0f}m ago"
        elif age_minutes < 1440:
            age_str = f"{age_minutes/60:.1f}h ago"
        else:
            age_str = f"{age_minutes/1440:.1f}d ago"

        # Sources
        sources_str = "+".join(ev.sources) if ev.sources else "?"

        # Format location (truncate if needed)
        loc = ev.location_name[:25] if len(ev.location_name) > 25 else ev.location_name

        # Discount info
        discount = get_mag_discount(ev.source_count)
        eff_mag = ev.magnitude - discount
        expired = age_minutes > EXTRA_EVENT_MAX_AGE_MINUTES

        if expired:
            lines.append(
                f"[dim]M{ev.magnitude:.1f}→{eff_mag:.1f} {loc} ({sources_str}) {age_str} EXPIRED[/dim]"
            )
        elif discount > 0:
            lines.append(
                f"[yellow]M{ev.magnitude:.1f}→{eff_mag:.1f}[/yellow] {loc} ({sources_str}) {age_str}"
            )
        else:
            lines.append(
                f"[yellow]M{ev.magnitude:.1f}[/yellow] {loc} ({sources_str}) {age_str}"
            )

    return "\n".join(lines)
