"""
Main TUI application for crypto update bot using Textual.
"""

from datetime import datetime
from typing import Optional
import asyncio

from textual.app import App, ComposeResult
from textual.containers import Container, Vertical
from textual.widgets import Footer, Static
from textual.binding import Binding
from rich.text import Text
from rich.panel import Panel
from rich.box import ROUNDED

from ..config import UpdateBotConfig, format_interval
from ..scanner import CryptoScanner
from ..updater import CryptoMarketsUpdater


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
        if self.last_update_time:
            update_time = self.last_update_time.strftime("%H:%M:%S")
        else:
            update_time = "Never"

        if self.next_update_seconds > 0:
            hours = self.next_update_seconds // 3600
            minutes = (self.next_update_seconds % 3600) // 60
            next_update = f"{hours}h {minutes}m"
        else:
            next_update = "Now"

        status = "[yellow]UPDATING...[/yellow]" if self.updating else "[green]Idle[/green]"

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

        for timestamp, message, color, bold in self.log_lines:
            time_text = f"[dim]{timestamp}[/dim]"

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
    """Crypto Markets Update Bot TUI."""

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
        Binding("r", "run_update", "Run Update Now", key_display="R"),
    ]

    def __init__(self, config: UpdateBotConfig):
        super().__init__()
        self.config = config
        self.scanner = CryptoScanner(timeout=config.api_timeout)
        self.updater = CryptoMarketsUpdater(config.markets_json, self.scanner)

        self.status_bar: Optional[StatusBar] = None
        self.log_panel: Optional[UpdateLogPanel] = None
        self.next_update_seconds = 0
        self.last_update_time: Optional[datetime] = None
        self.markets_count = 0
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
        header.update(f" ◉  Crypto Markets Update Bot (BTC/ETH) • Interval: {format_interval(self.config.update_interval)}")

        self.log_message("Update Bot Started", color="green", bold=True)
        self.log_message(f"Update Interval: {format_interval(self.config.update_interval)}")
        self.log_message(f"Markets JSON: {self.config.markets_json}")
        self.log_message("")

        if self.updater.is_available():
            self.log_message("Updater ready (Polymarket Gamma API)", color="green")
        else:
            self.log_message("Updater not available", color="yellow")

        self.log_message("")

        # Load current markets count
        current_config = self.updater.load_current_config()
        self.markets_count = len(current_config)
        self.log_message(f"Loaded {self.markets_count} markets from config")

        if self.markets_count > 0:
            # Show summary of existing markets
            btc_count = sum(1 for d in current_config.values() if d.get("currency") == "BTC")
            eth_count = sum(1 for d in current_config.values() if d.get("currency") == "ETH")
            self.log_message(f"  BTC: {btc_count} markets, ETH: {eth_count} markets")

        # Run first update immediately
        self.next_update_seconds = 5  # Short delay for UI to render
        self.set_timer(1, self.tick_countdown)
        self.set_timer(5, lambda: self.run_worker(self.run_update_task(), exclusive=True))
        self.set_interval(self.config.update_interval * 3600, self.run_update_task)

        if self.status_bar:
            self.status_bar.update_status(
                last_update_time=self.last_update_time,
                next_update_seconds=self.next_update_seconds,
                markets_count=self.markets_count,
                updating=False,
            )

    def log_message(self, message: str, color: str = "", bold: bool = False):
        if self.log_panel:
            self.log_panel.add_log(message, color=color, bold=bold)

    def tick_countdown(self) -> None:
        if self.next_update_seconds > 0:
            self.next_update_seconds -= 1

        if self.status_bar:
            self.status_bar.update_status(
                next_update_seconds=self.next_update_seconds,
                updating=self.updating,
            )

        self.set_timer(1, self.tick_countdown)

    async def run_update_task(self) -> None:
        if self.updating:
            self.log_message("Update already in progress, skipping...", color="yellow")
            return

        self.updating = True

        if self.log_panel:
            self.log_panel.clear()

        self.log_message("Starting market update...", color="cyan", bold=True)
        self.log_message("")

        if self.status_bar:
            self.status_bar.update_status(updating=True)

        try:
            def output_callback(line: str):
                self.log_message(f"  {line}", color="bright_black")

            success, message, stats = await asyncio.to_thread(
                self.updater.update,
                output_callback=output_callback,
            )

            self.log_message("")
            if success:
                self.log_message(message, color="green", bold=True)

                if stats:
                    self.log_message("")
                    self.log_message("Update Summary:", bold=True)
                    if stats.get("added", 0) > 0:
                        self.log_message(f"  Added: {stats['added']} market(s)", color="green")
                    if stats.get("updated", 0) > 0:
                        self.log_message(f"  Updated: {stats['updated']} market(s)", color="green")
                    if stats.get("removed", 0) > 0:
                        self.log_message(f"  Removed: {stats['removed']} market(s)", color="green")
                    self.log_message(f"  Total markets: {stats.get('total', 0)}", color="cyan")

                    self.markets_count = stats.get("total", 0)
            else:
                self.log_message(f"Failed: {message}", color="red", bold=True)
                self.log_message("  Will retry on next scheduled update", color="bright_black")

            self.last_update_time = datetime.now()
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
        return not self.updating

    def action_run_update(self) -> None:
        self.log_message("Manual update triggered (R pressed)", color="cyan")
        self.run_worker(self.run_update_task(), exclusive=True)

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
                self.log_message("Shutting down...", color="red", bold=True)
                self.exit()
            else:
                self.quit_pending = False
                self.notify("Quit cancelled")
            return

        # Russian keyboard layout support
        key_map = {
            "й": "q",
            "к": "r",
        }

        if event.key.lower() in key_map:
            mapped_key = key_map[event.key.lower()]
            if mapped_key == "q":
                self.action_quit()
            elif mapped_key == "r":
                if not self.updating:
                    self.action_run_update()
            event.stop()
            event.prevent_default()


def run_update_bot(config: UpdateBotConfig) -> None:
    """Run the update bot TUI application."""
    app = UpdateBotApp(config)
    app.run()
