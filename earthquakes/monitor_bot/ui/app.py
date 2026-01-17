"""
Monitor Bot TUI application using Textual.
"""

import asyncio
import sys
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional
from uuid import UUID

from textual.app import App, ComposeResult
from textual.containers import Container, Vertical, Horizontal, VerticalScroll
from textual.widgets import Header, Footer, Static, DataTable
from textual.binding import Binding
from rich.text import Text
from rich.panel import Panel
from rich.table import Table as RichTable

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

logger = logging.getLogger(__name__)

from monitor.database import Database
from monitor.models import SourceReport, EarthquakeEvent
from monitor.services.event_matcher import EventMatcher
from monitor.collectors import (
    JMACollector,
    EMSCCollector,
    GFZCollector,
    GeoNetCollector,
    USGSCollector,
)
from monitor_bot.config import config


class StatusBar(Static):
    """Top status bar showing monitoring status."""

    def __init__(self):
        super().__init__()
        self.active_sources = 0
        self.total_events = 0
        self.pending_events = 0
        self.last_update: Optional[datetime] = None

    def update_status(
        self,
        active_sources: int = None,
        total_events: int = None,
        pending_events: int = None,
        last_update: datetime = None,
    ) -> None:
        if active_sources is not None:
            self.active_sources = active_sources
        if total_events is not None:
            self.total_events = total_events
        if pending_events is not None:
            self.pending_events = pending_events
        if last_update is not None:
            self.last_update = last_update
        self.refresh()

    def render(self) -> Panel:
        update_time = (
            self.last_update.strftime("%H:%M:%S") if self.last_update else "Never"
        )

        status_line = (
            f"Status: [green]Running[/green]  |  "
            f"Sources: [cyan]{self.active_sources} active[/cyan]  |  "
            f"Events: [cyan]{self.total_events}[/cyan]  |  "
            f"Pending USGS: [yellow]{self.pending_events}[/yellow]  |  "
            f"Last Update: [dim]{update_time}[/dim]"
        )

        return Panel(
            status_line,
            border_style="green",
            padding=(0, 1),
        )


class SourcesPanel(Static):
    """Sources monitoring panel with sync status and timers."""

    def __init__(self):
        super().__init__()
        self.sources_status = {}  # {source_name: {last_poll, next_poll, is_syncing, interval}}

    def update_source(
        self,
        source: str,
        last_poll: Optional[datetime] = None,
        next_poll: Optional[datetime] = None,
        is_syncing: bool = False,
        interval: int = 60,
    ) -> None:
        """Update source status."""
        if source not in self.sources_status:
            self.sources_status[source] = {}

        if last_poll is not None:
            self.sources_status[source]["last_poll"] = last_poll
        if next_poll is not None:
            self.sources_status[source]["next_poll"] = next_poll
        if is_syncing is not None:
            self.sources_status[source]["is_syncing"] = is_syncing
        self.sources_status[source]["interval"] = interval

        self.refresh()

    def render(self) -> Panel:
        """Render sources panel."""
        now = datetime.now(timezone.utc)

        table = RichTable.grid(padding=(0, 1))
        table.add_column(justify="left", style="bold")
        table.add_column(justify="right")

        # Title with current time
        current_time = now.strftime("%H:%M:%S UTC")
        table.add_row("[bold cyan]Sources Monitor[/bold cyan]", "")
        table.add_row(f"[dim]{current_time}[/dim]", "")
        table.add_row("", "")

        # Each source
        for source_name in ["JMA", "EMSC", "GFZ", "GEONET", "USGS"]:
            source_key = source_name.lower()
            status = self.sources_status.get(source_key, {})

            is_syncing = status.get("is_syncing", False)
            next_poll = status.get("next_poll")

            # Status indicator
            if is_syncing:
                indicator = "[yellow]⟳ Syncing[/yellow]"
                timer = ""
            elif next_poll:
                seconds_left = (next_poll - now).total_seconds()
                if seconds_left < 0:
                    seconds_left = 0
                timer = f"[dim]{int(seconds_left)}s[/dim]"
                indicator = "[green]●[/green] Idle"
            else:
                indicator = "[dim]○[/dim] Waiting"
                timer = ""

            # Source line
            table.add_row(f"{indicator} {source_name}", timer)

        return Panel(
            table,
            title="[bold]Sources[/bold]",
            border_style="cyan",
            padding=(1, 2),
        )


class ActivityLogPanel(Static):
    """Activity log panel."""

    def __init__(self):
        super().__init__()
        self.log_lines: list[tuple[str, str, str]] = []  # (timestamp, message, color)
        self.max_lines = config.LOG_MAX_LINES

    def add_log(self, message: str, color: str = ""):
        """Add a log message."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_lines.append((timestamp, message, color))

        if len(self.log_lines) > self.max_lines:
            self.log_lines = self.log_lines[-self.max_lines :]

        self.refresh()

    def clear(self):
        """Clear log."""
        self.log_lines.clear()
        self.refresh()

    def render(self) -> Panel:
        """Render log panel."""
        lines = []

        for timestamp, message, color in self.log_lines[-15:]:  # Show last 15
            time_text = f"[dim]{timestamp}[/dim]"
            if color:
                message_text = f"[{color}]{message}[/]"
            else:
                message_text = message
            lines.append(f"{time_text} {message_text}")

        if not lines:
            lines.append("[dim]Waiting for earthquake data...[/dim]")

        content = "\n".join(lines)

        return Panel(
            content,
            title="[bold]Activity Log[/bold]",
            border_style="cyan",
            padding=(1, 2),
        )


class MonitorBotApp(App):
    """Earthquake Monitor Bot TUI."""

    CSS = """
    Screen {
        background: $surface;
    }

    StatusBar {
        dock: top;
        height: auto;
        margin: 1 1 0 1;
    }

    #main_container {
        height: 1fr;
        margin: 0 1;
    }

    #events_table {
        width: 3fr;
        height: 1fr;
    }

    SourcesPanel {
        width: 1fr;
        height: auto;
        max-width: 30;
    }

    ActivityLogPanel {
        height: 12;
        margin: 1 1;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit", key_display="Q", priority=True),
        Binding("c", "clear_log", "Clear Log", key_display="C"),
    ]

    def __init__(self):
        super().__init__()
        self.db = Database()
        self.matcher = EventMatcher()

        self.status_bar: Optional[StatusBar] = None
        self.events_table: Optional[DataTable] = None
        self.sources_panel: Optional[SourcesPanel] = None
        self.log_panel: Optional[ActivityLogPanel] = None

        self.collectors = []
        self.collector_tasks = []
        self.events_cache: dict[UUID, EarthquakeEvent] = {}

        self.total_events = 0
        self.pending_events = 0
        self.quit_pending = False  # For quit confirmation

        self.update_timer_task = None  # Timer for updating sources panel

    def compose(self) -> ComposeResult:
        yield Header()
        self.status_bar = StatusBar()
        yield self.status_bar

        # Main container with horizontal split
        with Horizontal(id="main_container"):
            # Events table (left, takes 3/4)
            self.events_table = DataTable(id="events_table")
            self.events_table.cursor_type = "row"
            self.events_table.zebra_stripes = True
            yield self.events_table

            # Sources panel (right, takes 1/4)
            self.sources_panel = SourcesPanel()
            yield self.sources_panel

        self.log_panel = ActivityLogPanel()
        yield self.log_panel
        yield Footer()

    async def on_mount(self) -> None:
        """Initialize the app when mounted."""
        self.title = "Earthquake Monitor Bot"
        self.sub_title = f"Tracking M{config.MIN_MAGNITUDE_TRACK}+ • All times in UTC"

        # Setup events table
        if self.events_table:
            self.events_table.add_columns(
                "Mag",
                "Location",
                "Srcs",
                "Detected",
                "USGS Pub",
                "Edge",
            )

        self.log_message("Monitor Bot started", color="green bold")
        self.log_message(
            f"Tracking: M{config.MIN_MAGNITUDE_TRACK}+, "
            f"Highlighting: M{config.MIN_MAGNITUDE_SIGNIFICANT}+"
        )
        self.log_message("")

        # Connect to database
        try:
            await self.db.connect()
            self.log_message(
                f"Connected to PostgreSQL: {config.DB_HOST}:{config.DB_PORT}/{config.DB_NAME}",
                color="green",
            )
        except Exception as e:
            self.log_message(f"Database connection failed: {e}", color="red bold")
            self.log_message(
                "Monitoring will continue but data will not be saved!", color="yellow"
            )

        self.log_message("")

        # Initialize collectors
        collector_map = {
            "jma": JMACollector,
            "emsc": EMSCCollector,
            "gfz": GFZCollector,
            "geonet": GeoNetCollector,
            "usgs": USGSCollector,
        }

        for name in config.ACTIVE_COLLECTORS:
            if name in collector_map:
                collector = collector_map[name]()
                self.collectors.append(collector)
                self.log_message(f"Initialized: {name.upper()}", color="cyan")

        # Load existing events from database
        await self._load_recent_events()

        # Start collectors
        self.log_message("")
        self.log_message(
            f"Starting {len(self.collectors)} collectors...", color="cyan bold"
        )

        for collector in self.collectors:
            task = asyncio.create_task(self._run_collector_with_tracking(collector))
            self.collector_tasks.append(task)

        # Update status bar
        if self.status_bar:
            self.status_bar.update_status(
                active_sources=len(self.collectors),
                total_events=self.total_events,
                pending_events=self.pending_events,
            )

        # Start update timer for sources panel
        self.update_timer_task = asyncio.create_task(self._update_sources_timer())

    async def _load_recent_events(self):
        """Load recent events from database to populate UI."""
        try:
            events = await self.db.get_recent_events(
                hours=24, min_magnitude=config.MIN_MAGNITUDE_TRACK
            )

            if events is None:
                events = []

            for event in events:
                self.events_cache[event.event_id] = event
                self._add_event_to_table(event)

            self.total_events = len(events)
            self.pending_events = sum(1 for e in events if not e.is_in_usgs)

            self.log_message(
                f"Loaded {len(events)} events from last 24 hours", color="cyan"
            )
        except Exception as e:
            self.log_message(f"Could not load recent events: {e}", color="yellow")

    async def _run_collector_with_tracking(self, collector):
        """Run collector with status tracking for UI."""
        source_name = collector.SOURCE_NAME
        interval = collector.POLL_INTERVAL

        # Initialize source in panel
        if self.sources_panel:
            now = datetime.now(timezone.utc)
            self.sources_panel.update_source(
                source_name,
                last_poll=None,
                next_poll=now,
                is_syncing=False,
                interval=interval,
            )

        # Run collector loop
        while True:
            try:
                # Mark as syncing
                if self.sources_panel:
                    self.sources_panel.update_source(source_name, is_syncing=True)

                # Do the poll
                reports = await collector.poll_once()
                for report in reports:
                    await self._handle_report(report)

                # Mark as idle and set next poll time
                now = datetime.now(timezone.utc)
                next_poll = now + timedelta(seconds=interval)
                if self.sources_panel:
                    self.sources_panel.update_source(
                        source_name,
                        last_poll=now,
                        next_poll=next_poll,
                        is_syncing=False,
                    )

            except Exception as e:
                logger.error(f"[{source_name.upper()}] Error in tracking loop: {e}")

            await asyncio.sleep(interval)

    async def _update_sources_timer(self):
        """Update sources panel every second to refresh timers."""
        while True:
            await asyncio.sleep(1)
            if self.sources_panel:
                self.sources_panel.refresh()

    def _add_event_to_table(self, event: EarthquakeEvent):
        """Add or update event in the table."""
        if not self.events_table:
            return

        # Format magnitude with color
        mag_text = self._format_magnitude(event.best_magnitude)

        # Location (truncate if too long)
        location = (event.location_name or "Unknown")[:35]

        # Sources count
        sources = str(event.source_count)

        # Detected time
        detected = event.first_detected_at.strftime("%H:%M:%S")

        # USGS published time
        if event.usgs_published_at:
            usgs_pub = event.usgs_published_at.strftime("%H:%M:%S")
        else:
            usgs_pub = Text("Pending", style="italic yellow")

        # Edge time
        if event.detection_advantage_minutes:
            edge_min = event.detection_advantage_minutes
            if edge_min >= config.EDGE_TIME_HIGHLIGHT:
                edge = Text(f"{edge_min:.0f}m", style="bold green")
            else:
                edge = Text(f"{edge_min:.0f}m", style="cyan")
        else:
            edge = Text("-", style="dim")

        # Add row
        self.events_table.add_row(
            mag_text,
            location,
            sources,
            detected,
            usgs_pub,
            edge,
            key=str(event.event_id),
        )

    def _format_magnitude(self, magnitude: float) -> Text:
        """Format magnitude with color based on threshold."""
        mag_str = f"M{magnitude:.1f}"

        if magnitude >= config.MIN_MAGNITUDE_SIGNIFICANT:
            return Text(mag_str, style="bold red")
        elif magnitude >= config.MIN_MAGNITUDE_WARNING:
            return Text(mag_str, style="bold yellow")
        else:
            return Text(mag_str, style="cyan")

    async def _handle_report(self, report: SourceReport):
        """Handle incoming earthquake report."""
        try:
            # Check if we already have this source event
            existing = await self.db.get_event_by_source_id(
                report.source, report.source_event_id
            )

            if existing:
                # Already processed
                return

            # Get recent events for matching
            recent_events = await self.db.get_recent_events(
                hours=24, min_magnitude=config.MIN_MAGNITUDE_TRACK - 0.5
            )

            if recent_events is None:
                recent_events = []

            # Try to match to existing event
            matched_id = self.matcher.find_matching_event(report, recent_events)

            if matched_id:
                # Update existing event
                event = next(e for e in recent_events if e.event_id == matched_id)
                event = self.matcher.update_event_from_report(event, report)

                try:
                    await self.db.update_event(event)
                    await self.db.insert_report(report, event.event_id)
                except Exception as db_error:
                    self.log_message(
                        f"DB error updating event: {db_error}",
                        color="red"
                    )
                    return

                self.events_cache[event.event_id] = event
                self._update_event_in_table(event)

                self.log_message(
                    f"[{report.source.upper()}] Matched M{report.magnitude} "
                    f"to existing event ({event.source_count} sources)",
                    color="cyan",
                )

                # If USGS just confirmed
                if report.source == "usgs" and event.detection_advantage_minutes:
                    self.log_message(
                        f"  → USGS confirmed! Edge: {event.detection_advantage_minutes:.1f} minutes",
                        color="green bold",
                    )
                    self.pending_events -= 1
            else:
                # Create new event
                event = self.matcher.create_event_from_report(report)

                try:
                    await self.db.insert_event(event)
                    await self.db.insert_report(report, event.event_id)
                except Exception as db_error:
                    self.log_message(
                        f"DB error creating event: {db_error}",
                        color="red"
                    )
                    return

                self.events_cache[event.event_id] = event
                self._add_event_to_table(event)

                self.total_events += 1
                if not event.is_in_usgs:
                    self.pending_events += 1

                # Log with emphasis
                if event.is_significant:
                    self.log_message(
                        f"[{report.source.upper()}] 🔴 NEW M{report.magnitude} "
                        f"at {report.location_name or 'Unknown'}",
                        color="red bold",
                    )
                else:
                    self.log_message(
                        f"[{report.source.upper()}] New M{report.magnitude} "
                        f"at {report.location_name or 'Unknown'}",
                        color="cyan",
                    )

            # Update status bar
            if self.status_bar:
                self.status_bar.update_status(
                    total_events=self.total_events,
                    pending_events=self.pending_events,
                    last_update=datetime.now(timezone.utc),
                )

        except Exception as e:
            self.log_message(f"Error handling report: {e}", color="red")

    def _update_event_in_table(self, event: EarthquakeEvent):
        """Update existing event in table."""
        if not self.events_table:
            return

        key = str(event.event_id)

        # Remove old row and add updated one
        try:
            self.events_table.remove_row(key)
        except KeyError:
            pass  # Row not found, will add new

        self._add_event_to_table(event)

    def log_message(self, message: str, color: str = ""):
        """Add message to activity log."""
        if self.log_panel:
            self.log_panel.add_log(message, color=color)

    def action_clear_log(self) -> None:
        """Clear activity log."""
        if self.log_panel:
            self.log_panel.clear()
            self.log_message("Log cleared", color="dim")

    def action_quit(self) -> None:
        """Action: Quit the app (with confirmation)."""
        if not self.quit_pending:
            self.quit_pending = True
            self.notify("Quit? Press ENTER to confirm, any other key to cancel")
        else:
            # Повторное нажатие Q -> отменяем выход
            self.quit_pending = False
            self.notify("Quit cancelled")

    def on_key(self, event) -> None:
        """Handle key presses for quit confirmation."""
        # Handle quit confirmation
        if self.quit_pending:
            if event.key == "enter":
                self.log_message("Shutting down...", color="yellow bold")
                self.exit()
            else:
                self.quit_pending = False
                self.notify("Quit cancelled")
            return


def run_monitor_bot() -> None:
    """Run the monitor bot TUI application."""
    app = MonitorBotApp()
    app.run()
