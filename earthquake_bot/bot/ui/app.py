"""
Main TUI application using Textual.
"""

from datetime import datetime
from typing import List, Optional
import asyncio

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Header, Footer, Static, DataTable, Log
from textual.binding import Binding
from textual.timer import Timer
from rich.text import Text
from rich.table import Table
from rich.panel import Panel

from ..config import BotConfig, format_interval
from ..models.position import Position
from ..models.signal import Signal, SignalType
from ..models.market import Market
from ..storage.positions import PositionStorage
from ..storage.history import HistoryStorage
from ..executor.polymarket import PolymarketExecutor, OrderResult


class StatusBar(Static):
    """Top status bar showing balance, positions, etc."""

    def __init__(self, config: BotConfig):
        super().__init__()
        self.config = config
        self.balance = 0.0
        self.positions_count = 0
        self.invested = 0.0
        self.unrealized_pnl = 0.0
        self.unrealized_pnl_pct = 0.0

    def update_status(self, balance: float, positions_count: int, invested: float,
                      unrealized_pnl: float, unrealized_pnl_pct: float) -> None:
        self.balance = balance
        self.positions_count = positions_count
        self.invested = invested
        self.unrealized_pnl = unrealized_pnl
        self.unrealized_pnl_pct = unrealized_pnl_pct
        self.refresh()

    def render(self) -> Text:
        mode = "DRY RUN" if self.config.dry_run else ("AUTO" if self.config.auto_mode else "CONFIRM")
        now = datetime.now().strftime("%H:%M:%S")

        # First line
        line1 = (
            f"  Balance: ${self.balance:,.2f}  |  "
            f"Positions: {self.positions_count}  |  "
            f"Invested: ${self.invested:,.2f}  |  "
            f"{now}"
        )

        # Second line
        pnl_str = f"+${self.unrealized_pnl:.2f}" if self.unrealized_pnl >= 0 else f"-${abs(self.unrealized_pnl):.2f}"
        pnl_pct = f"+{self.unrealized_pnl_pct:.1%}" if self.unrealized_pnl_pct >= 0 else f"{self.unrealized_pnl_pct:.1%}"

        line2 = (
            f"  Mode: {mode}  |  "
            f"Scan: {format_interval(self.config.scan_interval)}  |  "
            f"Unrealized: {pnl_str} ({pnl_pct})"
        )

        text = Text()
        text.append(line1 + "\n", style="bold")
        text.append(line2, style="dim")
        return text


class ScannerPanel(Static):
    """Left panel showing scanner results."""

    def __init__(self):
        super().__init__()
        self.signals: List[Signal] = []
        self.exit_signals: List[Signal] = []
        self.next_scan_seconds = 0
        self.scanning = False
        self.pending_confirmation: Optional[Signal] = None

    def update_signals(self, signals: List[Signal], exit_signals: List[Signal]) -> None:
        self.signals = signals
        self.exit_signals = exit_signals
        self.refresh()

    def set_next_scan(self, seconds: int) -> None:
        self.next_scan_seconds = seconds
        self.refresh()

    def set_scanning(self, scanning: bool) -> None:
        self.scanning = scanning
        self.refresh()

    def set_pending_confirmation(self, signal: Optional[Signal]) -> None:
        self.pending_confirmation = signal
        self.refresh()

    def render(self) -> Panel:
        lines = []

        # Scanning status
        now = datetime.now().strftime("%H:%M:%S")
        if self.scanning:
            lines.append(f"[{now}] Scanning markets...")
        else:
            mins = self.next_scan_seconds // 60
            secs = self.next_scan_seconds % 60
            lines.append(f"[{now}] Next scan in: {mins}:{secs:02d}")

        lines.append("")

        # Entry signals
        buy_signals = [s for s in self.signals if s.type == SignalType.BUY]
        skip_signals = [s for s in self.signals if s.type == SignalType.SKIP]

        for signal in buy_signals:
            lines.append(f"[green]+ {signal.market_slug}[/green]")
            lines.append(f"  {signal.market_name}")
            lines.append(f"  Price: {signal.current_price:.1%}  Fair: {signal.fair_price:.1%}  ({signal.model_used.upper()})")
            lines.append(f"  Edge: {signal.edge:.1%}  ROI: {signal.roi:.0%}  APY: {signal.annual_return:.0%}")
            lines.append(f"  >>> BUY {signal.outcome} ${signal.suggested_size:.2f}")

            if self.pending_confirmation and self.pending_confirmation.market_slug == signal.market_slug:
                lines.append("  [yellow]Confirm? [Y/N][/yellow]")

            lines.append("")

        for signal in skip_signals[:5]:  # Show max 5 skip signals
            lines.append(f"[dim]- {signal.market_slug} ({signal.market_name})[/dim]")
            lines.append(f"[dim]  Price: {signal.current_price:.1%}  Fair: {signal.fair_price:.1%}  Edge: {signal.edge:.1%}  APY: {signal.annual_return:.0%}[/dim]")
            lines.append("")

        # Exit signals
        if self.exit_signals:
            lines.append("[yellow]EXIT SIGNALS:[/yellow]")
            lines.append("")

            for signal in self.exit_signals:
                lines.append(f"[yellow]! {signal.market_slug}[/yellow]")
                lines.append(f"  >>> SELL ${signal.suggested_size:.2f} @ {signal.target_price:.1%}")
                lines.append(f"  Reason: {signal.reason}")

                if self.pending_confirmation and self.pending_confirmation.position_id == signal.position_id:
                    lines.append("  [yellow]Confirm? [Y/N][/yellow]")

                lines.append("")

        content = "\n".join(lines) if lines else "No signals"
        return Panel(content, title="MARKET SCANNER", border_style="blue")


class PositionsPanel(Static):
    """Right panel showing open positions."""

    def __init__(self):
        super().__init__()
        self.positions: List[Position] = []
        self.current_prices: dict[str, float] = {}
        self.total_invested = 0.0
        self.unrealized_pnl = 0.0
        self.unrealized_pnl_pct = 0.0

    def update_positions(self, positions: List[Position], current_prices: dict[str, float]) -> None:
        self.positions = positions
        self.current_prices = current_prices

        # Calculate totals
        self.total_invested = sum(p.entry_size for p in positions)
        total_value = sum(p.current_value(current_prices.get(p.market_slug, p.entry_price))
                         for p in positions)
        self.unrealized_pnl = total_value - self.total_invested
        self.unrealized_pnl_pct = self.unrealized_pnl / self.total_invested if self.total_invested > 0 else 0

        self.refresh()

    def render(self) -> Panel:
        # Build table
        table = Table(show_header=True, header_style="bold", box=None, padding=(0, 1))
        table.add_column("Market", width=15)
        table.add_column("Entry", justify="right", width=6)
        table.add_column("Curr", justify="right", width=6)
        table.add_column("P&L", justify="right", width=8)

        for pos in self.positions[:10]:  # Max 10 positions
            current = self.current_prices.get(pos.market_slug, pos.entry_price)
            pnl = pos.unrealized_pnl(current)

            # Color P&L
            if pnl > 0:
                pnl_str = f"[green]+${pnl:.2f}[/green]"
            elif pnl < 0:
                pnl_str = f"[red]-${abs(pnl):.2f}[/red]"
            else:
                pnl_str = f"${pnl:.2f}"

            # Truncate market slug
            slug = pos.market_slug[:15] if len(pos.market_slug) > 15 else pos.market_slug

            table.add_row(
                slug,
                f"{pos.entry_price:.1%}",
                f"{current:.1%}",
                pnl_str,
            )

        # Summary lines
        lines = []

        from io import StringIO
        from rich.console import Console
        console = Console(file=StringIO(), force_terminal=True)
        console.print(table)
        table_str = console.file.getvalue()

        lines.append(table_str)
        lines.append("-" * 38)
        lines.append(f"Total invested:       ${self.total_invested:>10.2f}")

        pnl_str = f"+${self.unrealized_pnl:.2f}" if self.unrealized_pnl >= 0 else f"-${abs(self.unrealized_pnl):.2f}"
        pnl_pct = f"+{self.unrealized_pnl_pct:.1%}" if self.unrealized_pnl_pct >= 0 else f"{self.unrealized_pnl_pct:.1%}"
        lines.append(f"Unrealized P&L:       {pnl_str:>10} ({pnl_pct})")

        content = "\n".join(lines)
        return Panel(content, title="MY POSITIONS", border_style="green")


class RecentTradesPanel(Static):
    """Panel showing recent trades."""

    def __init__(self, history: HistoryStorage):
        super().__init__()
        self.history = history
        self.realized_pnl_today = 0.0

    def update_trades(self) -> None:
        self.realized_pnl_today = self.history.get_realized_pnl_today()
        self.refresh()

    def render(self) -> Panel:
        lines = []

        for trade in self.history.get_recent_trades(5):
            lines.append(trade.format_line())

        lines.append("")
        pnl_str = f"+${self.realized_pnl_today:.2f}" if self.realized_pnl_today >= 0 else f"-${abs(self.realized_pnl_today):.2f}"
        lines.append(f"Realized P&L today:   {pnl_str}")

        content = "\n".join(lines) if lines else "No trades yet"
        return Panel(content, title="RECENT TRADES", border_style="cyan")


class TradingBotApp(App):
    """Main trading bot TUI application."""

    CSS = """
    Screen {
        layout: vertical;
    }

    #status-bar {
        height: 3;
        border: solid green;
        padding: 0 1;
    }

    #main-container {
        layout: horizontal;
        height: 1fr;
    }

    #left-panel {
        width: 50%;
        height: 100%;
    }

    #right-panel {
        width: 50%;
        height: 100%;
        layout: vertical;
    }

    #positions-panel {
        height: 60%;
    }

    #trades-panel {
        height: 40%;
    }

    ScannerPanel, PositionsPanel, RecentTradesPanel {
        height: 100%;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("h", "history", "History"),
        Binding("p", "pause", "Pause"),
        Binding("l", "logs", "Logs"),
        Binding("m", "toggle_mode", "Mode"),
        Binding("y", "confirm_yes", "Yes", show=False),
        Binding("n", "confirm_no", "No", show=False),
    ]

    def __init__(self, config: BotConfig, position_storage: PositionStorage,
                 history_storage: HistoryStorage, scanner=None, executor=None):
        super().__init__()
        self.config = config
        self.position_storage = position_storage
        self.history_storage = history_storage
        self.scanner = scanner
        self.executor = executor or PolymarketExecutor()

        self.paused = False
        self.scan_timer: Optional[Timer] = None
        self.countdown_timer: Optional[Timer] = None
        self.next_scan_seconds = config.scan_interval

        # Pending confirmation
        self.pending_signal: Optional[Signal] = None

        # Cache for markets (for executor)
        self._markets_cache: dict[str, Market] = {}

    def compose(self) -> ComposeResult:
        yield Static(id="status-bar")
        with Horizontal(id="main-container"):
            with Vertical(id="left-panel"):
                yield ScannerPanel()
            with Vertical(id="right-panel"):
                yield PositionsPanel()
                yield RecentTradesPanel(self.history_storage)
        yield Footer()

    def on_mount(self) -> None:
        """Called when app is mounted."""
        # Initialize status bar
        status = self.query_one("#status-bar", Static)
        status_bar = StatusBar(self.config)
        status.update(status_bar.render())

        # Start countdown timer immediately
        self.countdown_timer = self.set_interval(1, self.update_countdown)

        # Delay first scan by 5 seconds to let UI render
        self.next_scan_seconds = 5
        self.set_timer(5, self.start_scanning)

    def start_scanning(self) -> None:
        """Start scanning after initial delay."""
        # Start the regular scan timer
        self.scan_timer = self.set_interval(self.config.scan_interval, self.do_scan)
        # Do first scan now
        self.call_later(self.do_scan)

    def update_countdown(self) -> None:
        """Update countdown to next scan."""
        if self.paused:
            return

        self.next_scan_seconds -= 1
        if self.next_scan_seconds < 0:
            self.next_scan_seconds = self.config.scan_interval

        scanner_panel = self.query_one(ScannerPanel)
        scanner_panel.set_next_scan(self.next_scan_seconds)

    async def do_scan(self) -> None:
        """Perform market scan."""
        if self.paused:
            return

        scanner_panel = self.query_one(ScannerPanel)
        scanner_panel.set_scanning(True)

        # Reset countdown
        self.next_scan_seconds = self.config.scan_interval

        # Get current positions
        positions = self.position_storage.load_all_active()

        # Run scanner
        if self.scanner:
            entry_signals, exit_signals = self.scanner.scan(positions)

            # Cache markets for executor
            for market in self.scanner.get_markets():
                self._markets_cache[market.slug] = market
        else:
            entry_signals, exit_signals = [], []

        # Update UI
        scanner_panel.update_signals(entry_signals, exit_signals)
        scanner_panel.set_scanning(False)

        # Update positions panel
        positions_panel = self.query_one(PositionsPanel)
        current_prices = {s.market_slug: s.current_price for s in entry_signals}
        positions_panel.update_positions(positions, current_prices)

        # Update trades panel
        trades_panel = self.query_one(RecentTradesPanel)
        trades_panel.update_trades()

        # Update status bar
        self.update_status_bar(positions, current_prices)

        # Handle signals based on mode
        await self.process_signals(entry_signals, exit_signals)

    def update_status_bar(self, positions: List[Position],
                          current_prices: dict[str, float]) -> None:
        """Update status bar with current data."""
        invested = sum(p.entry_size for p in positions)
        total_value = sum(p.current_value(current_prices.get(p.market_slug, p.entry_price))
                         for p in positions)
        unrealized_pnl = total_value - invested
        unrealized_pnl_pct = unrealized_pnl / invested if invested > 0 else 0

        # Get real balance from executor
        balance = self.executor.get_balance()

        status_bar = StatusBar(self.config)
        status_bar.update_status(
            balance=balance,
            positions_count=len(positions),
            invested=invested,
            unrealized_pnl=unrealized_pnl,
            unrealized_pnl_pct=unrealized_pnl_pct,
        )

        status = self.query_one("#status-bar", Static)
        status.update(status_bar.render())

    async def process_signals(self, entry_signals: List[Signal],
                              exit_signals: List[Signal]) -> None:
        """Process trading signals based on mode."""
        actionable = [s for s in entry_signals if s.type == SignalType.BUY] + exit_signals

        if not actionable:
            return

        if self.config.auto_mode:
            # AUTO mode - execute immediately
            for signal in actionable:
                await self.execute_signal(signal)
        else:
            # CONFIRM mode - ask for confirmation
            for signal in actionable:
                self.pending_signal = signal
                scanner_panel = self.query_one(ScannerPanel)
                scanner_panel.set_pending_confirmation(signal)
                break  # One at a time

    async def execute_signal(self, signal: Signal) -> None:
        """Execute a trading signal via Polymarket API."""
        # Dry run mode - don't execute, just log
        if self.config.dry_run:
            self.notify(f"[DRY RUN] Would {signal.type.value} {signal.market_slug}")
            return

        self.notify(f"Executing {signal.type.value} for {signal.market_slug}...")

        # Get market from cache
        market = self._markets_cache.get(signal.market_slug)
        if not market:
            self.notify(f"Error: Market {signal.market_slug} not found in cache")
            return

        if signal.type == SignalType.BUY:
            # Execute buy order
            result, position = self.executor.buy(signal, market)

            if result.success and position:
                # Save position
                self.position_storage.save(position)
                self.history_storage.record_buy(position, result.order_id)
                self.notify(f"BUY order placed: {result.order_id}")
            else:
                self.notify(f"BUY failed: {result.error}")

        elif signal.type == SignalType.SELL and signal.position_id:
            # Get position
            position = self.position_storage.load(signal.position_id)
            if not position:
                self.notify(f"Error: Position {signal.position_id} not found")
                return

            # Execute sell order
            result = self.executor.sell(signal, position, market)

            if result.success:
                # Close position
                closed_position = self.position_storage.close_position(
                    signal.position_id, signal.current_price, result.order_id
                )
                if closed_position:
                    self.history_storage.record_sell(closed_position, result.order_id)
                self.notify(f"SELL order placed: {result.order_id}")
            else:
                self.notify(f"SELL failed: {result.error}")

    def action_quit(self) -> None:
        """Quit the application."""
        self.exit()

    def action_refresh(self) -> None:
        """Manual refresh/scan."""
        asyncio.create_task(self.do_scan())

    def action_history(self) -> None:
        """Show history."""
        stats = self.history_storage.get_statistics()
        self.notify(f"Total trades: {stats['total_trades']}, Win rate: {stats['win_rate']:.0%}")

    def action_pause(self) -> None:
        """Pause/resume scanning."""
        self.paused = not self.paused
        status = "PAUSED" if self.paused else "RESUMED"
        self.notify(f"Scanning {status}")

    def action_logs(self) -> None:
        """Show logs."""
        self.notify("Logs view not implemented yet")

    def action_toggle_mode(self) -> None:
        """Toggle between AUTO and CONFIRM mode."""
        self.config.auto_mode = not self.config.auto_mode
        mode = "AUTO" if self.config.auto_mode else "CONFIRM"
        self.notify(f"Mode changed to {mode}")

        # Refresh status bar
        positions = self.position_storage.load_all_active()
        self.update_status_bar(positions, {})

    def action_confirm_yes(self) -> None:
        """Confirm pending action."""
        if self.pending_signal:
            asyncio.create_task(self.execute_signal(self.pending_signal))
            self.pending_signal = None
            scanner_panel = self.query_one(ScannerPanel)
            scanner_panel.set_pending_confirmation(None)

    def action_confirm_no(self) -> None:
        """Reject pending action."""
        if self.pending_signal:
            self.notify(f"Rejected {self.pending_signal.type.value} for {self.pending_signal.market_slug}")
            self.pending_signal = None
            scanner_panel = self.query_one(ScannerPanel)
            scanner_panel.set_pending_confirmation(None)
