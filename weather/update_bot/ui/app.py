"""
TUI application for weather market update bot using Textual.
"""

from datetime import datetime
from typing import Optional
import asyncio

from textual.app import App, ComposeResult
from textual.widgets import Footer, Static
from textual.binding import Binding
from rich.panel import Panel
from rich.box import ROUNDED

from ..config import UpdateBotConfig, format_interval
from ..scanner import WeatherMarketScanner, WeatherMarketEntry


class StatusBar(Static):
    """Top status bar."""

    def __init__(self, config: UpdateBotConfig):
        super().__init__()
        self.config = config
        self.last_update_time: Optional[datetime] = None
        self.next_update_seconds = 0
        self.markets_count = 0
        self.events_count = 0
        self.updating = False

    def update_status(self, **kwargs) -> None:
        for k, v in kwargs.items():
            if hasattr(self, k):
                setattr(self, k, v)
        self.refresh()

    def render(self) -> Panel:
        update_time = self.last_update_time.strftime("%H:%M:%S") if self.last_update_time else "Never"

        if self.next_update_seconds > 0:
            h = self.next_update_seconds // 3600
            m = (self.next_update_seconds % 3600) // 60
            next_str = f"{h}h {m}m" if h > 0 else f"{m}m"
        else:
            next_str = "Now"

        status = "[yellow]SCANNING...[/yellow]" if self.updating else "[green]Idle[/green]"

        line = (
            f"Status: {status}  |  "
            f"Events: [cyan]{self.events_count}[/cyan]  "
            f"Buckets: [cyan]{self.markets_count}[/cyan]  |  "
            f"Last: [dim]{update_time}[/dim]  |  "
            f"Interval: [dim]{format_interval(self.config.update_interval)}[/dim]  |  "
            f"Next: [cyan]{next_str}[/cyan]"
        )

        return Panel(line, border_style="green", box=ROUNDED, padding=(0, 1))


class UpdateLogPanel(Static):
    """Main panel showing scan log."""

    def __init__(self):
        super().__init__()
        self.log_lines: list[tuple[str, str, str, bool]] = []
        self.max_lines = 100

    def add_log(self, message: str, color: str = "", bold: bool = False):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_lines.append((timestamp, message, color, bold))
        if len(self.log_lines) > self.max_lines:
            self.log_lines = self.log_lines[-self.max_lines:]
        self.refresh()

    def clear(self):
        self.log_lines.clear()
        self.refresh()

    def render(self) -> Panel:
        lines = []
        for ts, msg, color, bold in self.log_lines:
            time_text = f"[dim]{ts}[/dim]"
            if color or bold:
                style = ""
                if bold:
                    style += "bold "
                if color:
                    style += color
                msg_text = f"[{style.strip()}]{msg}[/]"
            else:
                msg_text = msg
            lines.append(f"{time_text} {msg_text}")

        if not lines:
            lines.append("[dim]Waiting for scan...[/dim]")

        return Panel(
            "\n".join(lines),
            title="[bold]Weather Market Scanner[/bold]",
            border_style="cyan",
            padding=(1, 2),
        )


class UpdateBotApp(App):
    """Weather Markets Update Bot TUI."""

    CSS = """
    Screen {
        background: $surface;
    }

    #app-header {
        dock: top;
        height: 1;
        background: #1a3a5c;
        color: #87ceeb;
    }

    StatusBar {
        dock: top;
        height: auto;
        margin: 1 1 0 1;
    }

    UpdateLogPanel {
        height: 1fr;
        margin: 1 1;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit", key_display="Q", priority=True),
        Binding("r", "run_update", "Scan Now", key_display="R"),
    ]

    def __init__(self, config: UpdateBotConfig):
        super().__init__()
        self.config = config
        self.scanner = WeatherMarketScanner()

        self.status_bar: Optional[StatusBar] = None
        self.log_panel: Optional[UpdateLogPanel] = None
        self.next_update_seconds = 0
        self.last_update_time: Optional[datetime] = None
        self.markets_count = 0
        self.events_count = 0
        self.updating = False
        self.quit_pending = False

    def compose(self) -> ComposeResult:
        yield Static(id="app-header")
        self.status_bar = StatusBar(self.config)
        yield self.status_bar
        self.log_panel = UpdateLogPanel()
        yield self.log_panel
        yield Footer()

    def on_mount(self) -> None:
        header = self.query_one("#app-header", Static)
        header.update(
            f" ◉  Weather Markets Update Bot • "
            f"Interval: {format_interval(self.config.update_interval)}"
        )

        self.log_message("Update Bot Started", color="green", bold=True)
        self.log_message(f"Interval: {format_interval(self.config.update_interval)}")
        self.log_message(f"Output: {self.config.markets_json}")
        self.log_message("")

        # Load existing markets
        existing = self.scanner.load_from_json(self.config.markets_json)
        if existing:
            events = {e.event_slug for e in existing}
            self.markets_count = len(existing)
            self.events_count = len(events)
            self.log_message(
                f"Loaded {self.markets_count} buckets from {self.events_count} events",
                color="cyan",
            )
        else:
            self.log_message("No existing markets file — will create on first scan")

        self.log_message("")

        # Start timers
        self.next_update_seconds = 3
        self.set_timer(1, self.tick_countdown)
        self.set_timer(3, lambda: self.run_worker(self.run_scan(), exclusive=True))

        self._refresh_status()

    def log_message(self, message: str, color: str = "", bold: bool = False):
        if self.log_panel:
            self.log_panel.add_log(message, color=color, bold=bold)

    def _refresh_status(self):
        if self.status_bar:
            self.status_bar.update_status(
                last_update_time=self.last_update_time,
                next_update_seconds=self.next_update_seconds,
                markets_count=self.markets_count,
                events_count=self.events_count,
                updating=self.updating,
            )

    def tick_countdown(self) -> None:
        if self.next_update_seconds > 0:
            self.next_update_seconds -= 1
        self._refresh_status()
        self.set_timer(1, self.tick_countdown)

    async def run_scan(self) -> None:
        if self.updating:
            self.log_message("Scan already in progress...", color="yellow")
            return

        self.updating = True
        self.log_panel.clear()
        self.log_message("Scanning Gamma API for weather markets...", color="cyan", bold=True)
        self.log_message("")
        self._refresh_status()

        try:
            def progress(msg: str):
                self.log_message(f"  {msg}", color="bright_black")

            entries = await asyncio.to_thread(
                self.scanner.search_markets,
                progress_callback=progress,
            )

            self.log_message("")

            if entries:
                # Group by event
                events = {}
                for e in entries:
                    events.setdefault(e.event_slug, []).append(e)

                self.markets_count = len(entries)
                self.events_count = len(events)

                self.log_message(
                    f"Found {len(entries)} buckets across {len(events)} events",
                    color="green", bold=True,
                )
                self.log_message("")

                # Show by city+date
                for slug in sorted(events.keys()):
                    buckets = events[slug]
                    city = buckets[0].city.replace("-", " ").title()
                    date = buckets[0].date
                    self.log_message(
                        f"  {city:<16} {date}  ({len(buckets)} buckets)"
                    )

                # Save
                self.scanner.save_to_json(entries, self.config.markets_json)
                self.log_message("")
                self.log_message(
                    f"Saved to {self.config.markets_json}", color="green"
                )
            else:
                self.log_message("No weather markets found", color="yellow")

            self.last_update_time = datetime.now()
            self.next_update_seconds = self.config.update_interval * 3600

        except Exception as e:
            self.log_message("")
            self.log_message(f"Error: {e}", color="red", bold=True)
            import traceback
            for line in traceback.format_exc().split("\n"):
                if line.strip():
                    self.log_message(f"  {line}", color="bright_black")

        finally:
            self.updating = False
            self._refresh_status()

        # Schedule next scan
        interval_sec = self.config.update_interval * 3600
        self.set_timer(interval_sec, lambda: self.run_worker(self.run_scan(), exclusive=True))

    def check_action_run_update(self) -> bool:
        return not self.updating

    def action_run_update(self) -> None:
        self.log_message("Manual scan triggered (R)", color="cyan")
        self.run_worker(self.run_scan(), exclusive=True)

    def action_quit(self) -> None:
        if not self.quit_pending:
            self.quit_pending = True
            self.notify("Quit? Press ENTER to confirm, any other key to cancel")
        else:
            self.quit_pending = False
            self.notify("Quit cancelled")

    def on_key(self, event) -> None:
        if self.quit_pending:
            if event.key == "enter":
                self.exit()
            else:
                self.quit_pending = False
                self.notify("Quit cancelled")
            return

        key_map = {"й": "q", "к": "r"}
        if event.key.lower() in key_map:
            mapped = key_map[event.key.lower()]
            if mapped == "q":
                self.action_quit()
            elif mapped == "r" and not self.updating:
                self.action_run_update()
            event.stop()
            event.prevent_default()


def run_update_bot(config: UpdateBotConfig) -> None:
    """Run the update bot TUI."""
    app = UpdateBotApp(config)
    app.run()
