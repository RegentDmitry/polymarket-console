"""
Main TUI application for update bot using Textual.
"""

from datetime import datetime
from typing import Optional
import asyncio

from textual.app import App, ComposeResult
from textual.containers import Container, Vertical
from textual.widgets import Header, Footer, Static
from textual.binding import Binding
from rich.text import Text
from rich.panel import Panel
from rich.table import Table
from rich.box import ROUNDED

from ..config import UpdateBotConfig, format_interval
from ..scanner import PolymarketScanner
from ..updater import MarketsUpdater


class StatusBar(Static):
    """Top status bar showing update status."""

    def __init__(self, config: UpdateBotConfig):
        super().__init__()
        self.config = config
        self.last_update_time: Optional[datetime] = None
        self.next_update_seconds = 0
        self.markets_count = 0
        self.updating = False

    def update_status(self, last_update_time: Optional[datetime] = None,
                      next_update_seconds: int = 0, markets_count: int = 0,
                      updating: bool = False) -> None:
        if last_update_time:
            self.last_update_time = last_update_time
        self.next_update_seconds = next_update_seconds
        self.markets_count = markets_count
        self.updating = updating
        self.refresh()

    def render(self) -> Panel:
        # Last update time
        if self.last_update_time:
            update_time = self.last_update_time.strftime("%H:%M:%S")
        else:
            update_time = "Never"

        # Next update countdown
        if self.next_update_seconds > 0:
            hours = self.next_update_seconds // 3600
            minutes = (self.next_update_seconds % 3600) // 60
            next_update = f"{hours}h {minutes}m"
        else:
            next_update = "Now"

        status = "[yellow]UPDATING...[/yellow]" if self.updating else "[green]Idle[/green]"

        # Build status line
        status_line = (
            f"Status: {status}  |  "
            f"Markets: [cyan]{self.markets_count}[/cyan]  |  "
            f"Last Update: [dim]{update_time}[/dim]  |  "
            f"Interval: [dim]{format_interval(self.config.update_interval)}[/dim]  |  "
            f"Next Update: [cyan]{next_update}[/cyan]"
        )

        return Panel(
            status_line,
            border_style="green",
            box=ROUNDED,
            padding=(0, 1),
        )


class UpdateLogPanel(Static):
    """Main panel showing update log."""

    def __init__(self):
        super().__init__()
        self.log_lines: list[tuple[str, str, bool]] = []  # (timestamp, message, (color, bold))
        self.max_lines = 100

    def add_log(self, message: str, color: str = "", bold: bool = False):
        """Add a log message."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_lines.append((timestamp, message, color, bold))

        # Keep only recent lines
        if len(self.log_lines) > self.max_lines:
            self.log_lines = self.log_lines[-self.max_lines:]

        self.refresh()

    def clear(self):
        """Clear all log messages."""
        self.log_lines.clear()
        self.refresh()

    def render(self) -> Panel:
        """Render log panel with all messages."""
        lines = []

        for timestamp, message, color, bold in self.log_lines:
            # Format timestamp
            time_text = f"[dim]{timestamp}[/dim]"

            # Format message
            if color or bold:
                style = ""
                if bold:
                    style += "bold "
                if color:
                    style += color
                message_text = f"[{style.strip()}]{message}[/]"
            else:
                message_text = message

            lines.append(f"{time_text} {message_text}")

        # If no lines, show placeholder
        if not lines:
            lines.append("[dim]Waiting for updates...[/dim]")

        content = "\n".join(lines)

        return Panel(
            content,
            title="[bold]Update Log[/bold]",
            border_style="cyan",
            padding=(1, 2),
        )


class UpdateBotApp(App):
    """Earthquake Markets Update Bot TUI."""

    CSS = """
    Screen {
        background: $surface;
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
        Binding("r", "run_update", "Run Update Now", key_display="R"),
    ]

    def __init__(self, config: UpdateBotConfig):
        super().__init__()
        self.config = config
        self.scanner = PolymarketScanner()

        # Determine working directory (parent of json file)
        working_dir = config.markets_json.parent.absolute()
        self.updater = MarketsUpdater(config.markets_json, self.scanner, working_dir)

        self.status_bar: Optional[StatusBar] = None
        self.log_panel: Optional[UpdateLogPanel] = None
        self.next_update_seconds = 0
        self.last_update_time: Optional[datetime] = None
        self.markets_count = 0
        self.updating = False
        self.claude_available = False
        self.quit_pending = False  # For quit confirmation

    def compose(self) -> ComposeResult:
        yield Header()
        self.status_bar = StatusBar(self.config)
        yield self.status_bar
        self.log_panel = UpdateLogPanel()
        yield self.log_panel
        yield Footer()

    def on_mount(self) -> None:
        """Initialize the app when mounted."""
        self.title = "Earthquake Markets Update Bot"
        self.sub_title = f"Interval: {format_interval(self.config.update_interval)}"

        # Log startup
        self.log_message("Update Bot Started", color="green", bold=True)
        self.log_message(f"Update Interval: {format_interval(self.config.update_interval)}")
        self.log_message(f"Markets JSON: {self.config.markets_json}")
        self.log_message("")

        # Check updater availability
        self.claude_available = self.updater.is_claude_available()
        if self.claude_available:
            self.log_message("✓ Updater ready", color="green")
            self.log_message("  Updates via Polymarket Gamma API", color="bright_black")
        else:
            self.log_message("⚠ Updater not available", color="yellow")

        self.log_message("")

        # Load current markets count
        current_config = self.updater.load_current_config()
        self.markets_count = len(current_config)
        self.log_message(f"Loaded {self.markets_count} markets from config")

        # Schedule first update
        self.next_update_seconds = self.config.update_interval * 3600
        self.set_timer(1, self.tick_countdown)
        self.set_interval(self.config.update_interval * 3600, self.run_update_task)

        # Update status bar
        if self.status_bar:
            self.status_bar.update_status(
                last_update_time=self.last_update_time,
                next_update_seconds=self.next_update_seconds,
                markets_count=self.markets_count,
                updating=False,
            )

    def log_message(self, message: str, color: str = "", bold: bool = False):
        """Log a message to the update log."""
        if self.log_panel:
            self.log_panel.add_log(message, color=color, bold=bold)

    def tick_countdown(self) -> None:
        """Countdown timer tick."""
        if self.next_update_seconds > 0:
            self.next_update_seconds -= 1

        if self.status_bar:
            self.status_bar.update_status(
                next_update_seconds=self.next_update_seconds,
                updating=self.updating,
            )

        # Re-schedule tick
        self.set_timer(1, self.tick_countdown)

    async def run_update_task(self) -> None:
        """Run the update task."""
        if self.updating:
            self.log_message("Update already in progress, skipping...", color="yellow")
            return

        self.updating = True

        # Clear log before new update
        if self.log_panel:
            self.log_panel.clear()

        self.log_message("Starting market update...", color="cyan", bold=True)
        self.log_message("")

        if self.status_bar:
            self.status_bar.update_status(updating=True)

        try:
            if not self.claude_available:
                self.log_message("⚠ Updater not available", color="red", bold=True)
                self.log_message("  Will retry on next scheduled update", color="bright_black")
                return

            # Update markets from Polymarket API
            self.log_message("Starting update from Polymarket...", color="cyan")
            self.log_message("")

            def output_callback(line: str):
                """Callback to log update progress in real-time."""
                self.log_message(f"  {line}", color="bright_black")

            # Run Claude Code update
            success, message, stats = await asyncio.to_thread(
                self.updater.update_via_claude,
                output_callback=output_callback
            )

            self.log_message("")
            if success:
                self.log_message(message, color="green", bold=True)

                # Show statistics
                if stats:
                    self.log_message("")
                    self.log_message("Update Summary:", bold=True)
                    if stats.get("added", 0) > 0:
                        self.log_message(f"  ✓ Added: {stats['added']} market(s)", color="green")
                    if stats.get("updated", 0) > 0:
                        self.log_message(f"  ✓ Updated: {stats['updated']} market(s)", color="green")
                    if stats.get("removed", 0) > 0:
                        self.log_message(f"  ✓ Removed: {stats['removed']} market(s)", color="green")
                    self.log_message(f"  Total markets: {stats.get('total', 0)}", color="cyan")

                    self.markets_count = stats.get("total", 0)
            else:
                self.log_message(f"Failed: {message}", color="red", bold=True)
                self.log_message("  Will retry on next scheduled update", color="bright_black")

            # Update last update time
            self.last_update_time = datetime.now()

            # Reset countdown
            self.next_update_seconds = self.config.update_interval * 3600

        except Exception as e:
            self.log_message("")
            self.log_message(f"Error during update: {e}", color="red", bold=True)
            import traceback
            for line in traceback.format_exc().split("\n"):
                if line.strip():
                    self.log_message(f"  {line}", color="bright_black")

        finally:
            self.updating = False
            if self.status_bar:
                self.status_bar.update_status(
                    last_update_time=self.last_update_time,
                    next_update_seconds=self.next_update_seconds,
                    markets_count=self.markets_count,
                    updating=False,
                )

    def check_action_run_update(self) -> bool:
        """Disable R key while updating (grays out in footer)."""
        return not self.updating

    def action_run_update(self) -> None:
        """Action: Run update now."""
        self.log_message("Manual update triggered (R pressed)", color="cyan")
        self.run_worker(self.run_update_task(), exclusive=True)

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
        """Handle key presses for quit confirmation and Russian layout."""
        # Handle quit confirmation
        if self.quit_pending:
            if event.key == "enter":
                self.log_message("Shutting down...", color="red", bold=True)
                self.exit()
            else:
                self.quit_pending = False
                self.notify("Quit cancelled")
            return

        # Russian keyboard layout support
        key_map = {
            "й": "q",  # Russian Q -> English q (quit)
            "к": "r",  # Russian R -> English r (run update)
        }

        if event.key.lower() in key_map:
            mapped_key = key_map[event.key.lower()]
            # Trigger corresponding action
            if mapped_key == "q":
                self.action_quit()
            elif mapped_key == "r":
                if not self.updating:  # Only run if not already updating
                    self.action_run_update()
            event.stop()
            event.prevent_default()


def run_update_bot(config: UpdateBotConfig) -> None:
    """Run the update bot TUI application."""
    app = UpdateBotApp(config)
    app.run()
