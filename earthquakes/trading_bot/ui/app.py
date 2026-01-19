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
from ..logger import get_logger
from ..monitor_data import load_monitor_data, format_extra_events, MonitorData


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
        self.last_scan_time: Optional[datetime] = None

    def update_status(self, balance: float, positions_count: int, invested: float,
                      unrealized_pnl: float, unrealized_pnl_pct: float,
                      last_scan_time: Optional[datetime] = None) -> None:
        self.balance = balance
        self.positions_count = positions_count
        self.invested = invested
        self.unrealized_pnl = unrealized_pnl
        self.unrealized_pnl_pct = unrealized_pnl_pct
        if last_scan_time:
            self.last_scan_time = last_scan_time
        self.refresh()

    def render(self) -> Text:
        mode = "DRY RUN" if self.config.dry_run else ("AUTO" if self.config.auto_mode else "CONFIRM")

        # Last scan time
        if self.last_scan_time:
            scan_time = self.last_scan_time.strftime("%H:%M:%S")
        else:
            scan_time = "--:--:--"

        # First line
        line1 = (
            f"  Balance: ${self.balance:,.2f}  |  "
            f"Positions: {self.positions_count}  |  "
            f"Invested: ${self.invested:,.2f}  |  "
            f"Scanned: {scan_time}"
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
        self.scan_status: str = ""  # Progress status during scan
        self.pending_confirmation: Optional[Signal] = None
        self.last_scan_time: Optional[datetime] = None

    def update_signals(self, signals: List[Signal], exit_signals: List[Signal],
                       last_scan_time: Optional[datetime] = None) -> None:
        self.signals = signals
        self.exit_signals = exit_signals
        if last_scan_time:
            self.last_scan_time = last_scan_time
        self.refresh()

    def set_next_scan(self, seconds: int) -> None:
        self.next_scan_seconds = seconds
        self.refresh()

    def set_scanning(self, scanning: bool) -> None:
        self.scanning = scanning
        if not scanning:
            self.scan_status = ""  # Clear status when done
        self.refresh()

    def set_scan_status(self, status: str) -> None:
        """Update scan progress status."""
        self.scan_status = status
        self.refresh()

    def set_pending_confirmation(self, signal: Optional[Signal]) -> None:
        self.pending_confirmation = signal
        self.refresh()

    def render(self) -> Panel:
        lines = []

        # Scanning status
        if self.scanning:
            status_text = self.scan_status or "Starting..."
            lines.append(f"[yellow]{status_text}[/yellow]")
        else:
            mins = self.next_scan_seconds // 60
            secs = self.next_scan_seconds % 60
            buy_count = len([s for s in self.signals if s.type == SignalType.BUY])
            total_count = len(self.signals)
            lines.append(f"Next: {mins}:{secs:02d}  |  Found: {buy_count}/{total_count}")

        lines.append("")

        # Entry signals
        buy_signals = [s for s in self.signals if s.type == SignalType.BUY]
        skip_signals = [s for s in self.signals if s.type == SignalType.SKIP]

        for signal in buy_signals:
            lines.append(f"[green]+ {signal.market_slug}[/green]")
            lines.append(f"  {signal.market_name}")
            lines.append(f"  Price: {signal.current_price:.1%}  Fair: {signal.fair_price:.1%}  ({signal.model_used.upper()})")
            lines.append(f"  Edge: {signal.edge:.1%}  ROI: {signal.roi:.0%}  APY: {signal.annual_return:.0%}")

            # Show available liquidity (filtered by edge/apy) and suggested size
            liq_str = f"${signal.liquidity:.0f}" if signal.liquidity else "$0"
            size_str = f"${signal.suggested_size:.0f}" if signal.suggested_size > 0 else "-"
            lines.append(f"  Available: {liq_str}  |  Buy: {size_str}")
            lines.append(f"  >>> BUY {signal.outcome}")

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
                lines.append(f"  {signal.market_name}")
                lines.append(f"  Bid: {signal.current_price:.1%}  Fair: {signal.fair_price:.1%}")
                liq_str = f"${signal.liquidity:.0f}" if signal.liquidity else "-"
                size_str = f"${signal.suggested_size:.0f}" if signal.suggested_size > 0 else "-"
                lines.append(f"  Bid liquidity: {liq_str}  |  Sell: {size_str}")
                lines.append(f"  >>> SELL {signal.outcome}")

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
        lines = []

        if not self.positions:
            # No positions - show empty state
            lines.append("[dim]No open positions[/dim]")
            lines.append("")
            lines.append("-" * 62)
            lines.append(f"Total invested:  $0.00")
            lines.append(f"Unrealized P&L:  $0.00 (+0.0%)")
        else:
            # Simple text table (Rich Table breaks on narrow width)
            lines.append("[bold]Market        Entry  Fair  Curr   Cost    Value    P&L[/bold]")

            for pos in self.positions[:10]:  # Max 10 positions
                current = self.current_prices.get(pos.market_slug, pos.entry_price)
                fair = pos.fair_price_at_entry
                cost = pos.entry_size
                value = pos.current_value(current)
                pnl = value - cost

                # Color P&L
                if pnl > 0:
                    pnl_str = f"[green]+${pnl:.2f}[/green]"
                elif pnl < 0:
                    pnl_str = f"[red]-${abs(pnl):.2f}[/red]"
                else:
                    pnl_str = f"${pnl:.2f}"

                # Truncate market slug
                slug = pos.market_slug[:12] if len(pos.market_slug) > 12 else pos.market_slug

                lines.append(f"{slug:<12} {pos.entry_price:>5.1%} {fair:>5.1%} {current:>5.1%} ${cost:>5.0f}  ${value:>6.2f} {pnl_str:>8}")
            lines.append("-" * 62)
            lines.append(f"Total invested:  ${self.total_invested:.2f}")

            pnl_str = f"+${self.unrealized_pnl:.2f}" if self.unrealized_pnl >= 0 else f"-${abs(self.unrealized_pnl):.2f}"
            pnl_pct = f"+{self.unrealized_pnl_pct:.1%}" if self.unrealized_pnl_pct >= 0 else f"{self.unrealized_pnl_pct:.1%}"
            lines.append(f"Unrealized P&L:  {pnl_str} ({pnl_pct})")

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


class ExtraEventsPanel(Static):
    """Panel showing extra earthquake events from monitor_bot (not yet in USGS)."""

    def __init__(self):
        super().__init__()
        self.monitor_data: Optional[MonitorData] = None

    def update_data(self, data: MonitorData) -> None:
        self.monitor_data = data
        self.refresh()

    def render(self) -> Panel:
        if self.monitor_data is None:
            content = "[dim]Loading...[/dim]"
        else:
            content = format_extra_events(self.monitor_data)

        return Panel(content, title="EXTRA EVENTS (not in USGS)", border_style="yellow")


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
        height: 45%;
    }

    #trades-panel {
        height: 30%;
    }

    #extra-events-panel {
        height: 25%;
    }

    ScannerPanel, PositionsPanel, RecentTradesPanel, ExtraEventsPanel {
        height: 100%;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit", key_display="Q"),
        Binding("r", "refresh", "Scan", key_display="R"),
        Binding("h", "history", "History", key_display="H"),
        Binding("m", "toggle_mode", "Mode", key_display="M"),
        Binding("y", "confirm_yes", "Yes", show=False),
        Binding("n", "confirm_no", "No", show=False),
        # Russian keyboard layout support
        Binding("й", "quit", "Quit", show=False),
        Binding("к", "refresh", "Scan", show=False),
        Binding("р", "history", "History", show=False),
        Binding("ь", "toggle_mode", "Mode", show=False),
        Binding("н", "confirm_yes", "Yes", show=False),
        Binding("т", "confirm_no", "No", show=False),
    ]

    def __init__(self, config: BotConfig, position_storage: PositionStorage,
                 history_storage: HistoryStorage, scanner=None, executor=None):
        super().__init__()
        self.config = config
        self.position_storage = position_storage
        self.history_storage = history_storage
        self.scanner = scanner
        self.executor = executor or PolymarketExecutor()

        self.scanning = False
        self.scan_timer: Optional[Timer] = None
        self.countdown_timer: Optional[Timer] = None
        self.next_scan_seconds = config.scan_interval

        # Pending confirmation queue
        self.pending_signal: Optional[Signal] = None
        self._pending_signals_queue: List[Signal] = []
        self.quit_pending = False

        # Virtual positions for DRY RUN mode (in-memory only)
        self._dry_run_positions: List[Position] = []
        self._dry_run_size = 10.0  # Default position size for dry run

        # Cache for markets and prices
        self._markets_cache: dict[str, Market] = {}
        self._current_prices: dict[str, float] = {}
        self._last_scan_time: Optional[datetime] = None

    def compose(self) -> ComposeResult:
        yield Static(id="status-bar")
        with Horizontal(id="main-container"):
            with Vertical(id="left-panel"):
                yield ScannerPanel()
            with Vertical(id="right-panel"):
                yield PositionsPanel()
                yield RecentTradesPanel(self.history_storage)
                yield ExtraEventsPanel()
        yield Footer()

    def on_mount(self) -> None:
        """Called when app is mounted."""
        # Log startup
        logger = get_logger()
        mode = "DRY RUN" if self.config.dry_run else ("AUTO" if self.config.auto_mode else "CONFIRM")
        logger.log_startup(mode, self.config.scan_interval, self.config.min_edge, self.config.min_apy)

        # Sync positions with Polymarket API (unless dry run)
        if not self.config.dry_run and self.executor.initialized:
            synced = self.executor.sync_positions(self.position_storage)
            if synced:
                self.notify(f"Synced {len(synced)} positions from Polymarket")

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

    def _get_all_positions(self) -> List[Position]:
        """Get all positions (real + dry run)."""
        real_positions = self.position_storage.load_all_active()
        if self.config.dry_run:
            return self._dry_run_positions + real_positions
        return real_positions

    def _refresh_positions_panel(self) -> None:
        """Refresh positions panel with current data."""
        positions = self._get_all_positions()
        positions_panel = self.query_one(PositionsPanel)
        positions_panel.update_positions(positions, self._current_prices)

    def update_countdown(self) -> None:
        """Update countdown to next scan."""
        if self.scanning:
            return

        self.next_scan_seconds -= 1
        if self.next_scan_seconds < 0:
            self.next_scan_seconds = self.config.scan_interval

        scanner_panel = self.query_one(ScannerPanel)
        scanner_panel.set_next_scan(self.next_scan_seconds)

    async def do_scan(self) -> None:
        """Perform market scan."""
        # Set scanning state (may already be set by action_refresh)
        if not self.scanning:
            self.scanning = True
            self.refresh_bindings()  # Gray out R in footer
            scanner_panel = self.query_one(ScannerPanel)
            scanner_panel.set_scanning(True)
        else:
            scanner_panel = self.query_one(ScannerPanel)

        # Reset countdown
        self.next_scan_seconds = self.config.scan_interval

        # Get current positions (real + dry run)
        positions = self._get_all_positions()

        # Run scanner in a separate thread to avoid blocking UI
        if self.scanner:
            loop = asyncio.get_event_loop()

            # Create progress callback that updates UI from thread
            def update_status(status: str) -> None:
                self.call_from_thread(scanner_panel.set_scan_status, status)

            def do_scan_with_progress():
                return self.scanner.scan(positions, progress_callback=update_status)

            entry_signals, exit_signals = await loop.run_in_executor(
                None, do_scan_with_progress
            )

            # Cache markets for executor
            for market in self.scanner.get_markets():
                self._markets_cache[market.slug] = market

            # Calculate suggested sizes: buy all available liquidity (or remaining balance)
            balance = self.executor.get_balance() if self.executor.initialized else 0
            for signal in entry_signals:
                if signal.liquidity > 0 and balance > 0:
                    # Buy all available at good prices, but not more than we have
                    signal.suggested_size = min(signal.liquidity, balance)

            # Filter out BUY signals with liquidity < $1 (not worth showing)
            MIN_LIQUIDITY = 1.0
            entry_signals = [
                s for s in entry_signals
                if s.type != SignalType.BUY or s.liquidity >= MIN_LIQUIDITY
            ]
        else:
            entry_signals, exit_signals = [], []

        # Record scan time
        self._last_scan_time = datetime.now()

        # Update UI
        scanner_panel.update_signals(entry_signals, exit_signals, self._last_scan_time)
        scanner_panel.set_scanning(False)
        self.scanning = False
        self.refresh_bindings()  # Re-enable R in footer

        # Update positions panel with current prices
        self._current_prices = {s.market_slug: s.current_price for s in entry_signals}
        self._refresh_positions_panel()

        # Update trades panel
        trades_panel = self.query_one(RecentTradesPanel)
        trades_panel.update_trades()

        # Update extra events panel (from monitor_bot)
        extra_events_panel = self.query_one(ExtraEventsPanel)
        monitor_data = load_monitor_data()
        extra_events_panel.update_data(monitor_data)

        # Update status bar
        self.update_status_bar(positions, self._current_prices)

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
            last_scan_time=self._last_scan_time,
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
            # CONFIRM mode - queue all signals and ask one at a time
            self._pending_signals_queue = actionable.copy()
            self._show_next_pending_signal()

    def _show_next_pending_signal(self) -> None:
        """Show the next signal in queue for confirmation."""
        scanner_panel = self.query_one(ScannerPanel)

        if self._pending_signals_queue:
            self.pending_signal = self._pending_signals_queue.pop(0)
            scanner_panel.set_pending_confirmation(self.pending_signal)
        else:
            self.pending_signal = None
            scanner_panel.set_pending_confirmation(None)

    async def execute_signal(self, signal: Signal) -> None:
        """Execute a trading signal via Polymarket API."""
        logger = get_logger()

        # Dry run mode - create virtual position in memory
        if self.config.dry_run:
            if signal.type == SignalType.BUY:
                # Create virtual position
                tokens = self._dry_run_size / signal.current_price if signal.current_price > 0 else 0
                position = Position(
                    market_id=signal.market_id,
                    market_slug=signal.market_slug,
                    market_name=signal.market_name,
                    outcome=signal.outcome,
                    entry_price=signal.current_price,
                    entry_time=datetime.now().isoformat() + "Z",
                    entry_size=self._dry_run_size,
                    tokens=tokens,
                    strategy="dry_run",
                    fair_price_at_entry=signal.fair_price,
                    edge_at_entry=signal.edge,
                )
                self._dry_run_positions.append(position)
                self.notify(f"[DRY] Bought {signal.market_slug} @ {signal.current_price:.1%}")

                logger.log_trade_executed(
                    "BUY", signal.market_slug, signal.outcome,
                    signal.current_price, tokens, self._dry_run_size, dry_run=True
                )
                logger.log_position_opened(position)

                # Refresh positions panel
                self._refresh_positions_panel()
            elif signal.type == SignalType.SELL and signal.position_id:
                # Find and remove virtual position
                sold_position = None
                for p in self._dry_run_positions:
                    if p.id == signal.position_id:
                        sold_position = p
                        break

                if sold_position:
                    pnl = sold_position.unrealized_pnl(signal.current_price)
                    self._dry_run_positions = [
                        p for p in self._dry_run_positions if p.id != signal.position_id
                    ]
                    self.notify(f"[DRY] Sold {signal.market_slug} @ {signal.current_price:.1%} (P&L: ${pnl:+.2f})")

                    logger.log_trade_executed(
                        "SELL", signal.market_slug, signal.outcome,
                        signal.current_price, sold_position.tokens, signal.suggested_size, dry_run=True
                    )
                    logger.log_position_closed(sold_position, signal.current_price, pnl)
                else:
                    self.notify(f"[DRY] Position not found: {signal.position_id}")
                    logger.log_warning(f"Position not found for sell: {signal.position_id}")
                self._refresh_positions_panel()
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

                logger.log_trade_executed(
                    "BUY", signal.market_slug, signal.outcome,
                    signal.current_price, position.size, position.entry_size
                )
                logger.log_position_opened(position)

                # Refresh positions panel immediately
                self._refresh_positions_panel()
            else:
                self.notify(f"BUY failed: {result.error}")
                logger.log_trade_failed("BUY", signal.market_slug, result.error or "Unknown error")

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
                    pnl = closed_position.realized_pnl()
                    logger.log_trade_executed(
                        "SELL", signal.market_slug, signal.outcome,
                        signal.current_price, position.tokens, signal.suggested_size
                    )
                    logger.log_position_closed(closed_position, signal.current_price, pnl)
                self.notify(f"SELL order placed: {result.order_id}")
                # Refresh positions panel immediately
                self._refresh_positions_panel()
            else:
                self.notify(f"SELL failed: {result.error}")
                logger.log_trade_failed("SELL", signal.market_slug, result.error or "Unknown error")

    def action_quit(self) -> None:
        """Quit the application (with confirmation)."""
        if self.quit_pending:
            self.exit()
        else:
            self.quit_pending = True
            self.notify("Quit? Press ENTER to confirm, any other key to cancel")

    def on_key(self, event) -> None:
        """Handle key presses for quit confirmation."""
        if self.quit_pending:
            if event.key == "enter":
                self.exit()
            else:
                self.quit_pending = False
                self.notify("Quit cancelled")

    def check_action_refresh(self) -> bool:
        """Disable R key while scanning (grays out in footer)."""
        return not self.scanning

    def action_refresh(self) -> None:
        """Manual refresh/scan."""
        # Show scanning state immediately
        self.scanning = True
        self.refresh_bindings()  # Update footer to gray out R
        scanner_panel = self.query_one(ScannerPanel)
        scanner_panel.set_scanning(True)
        asyncio.create_task(self.do_scan())

    def action_history(self) -> None:
        """Show history."""
        stats = self.history_storage.get_statistics()
        if stats['total_trades'] == 0:
            self.notify("No trading history yet")
        else:
            self.notify(f"Total trades: {stats['total_trades']}, Win rate: {stats['win_rate']:.0%}")

    def action_toggle_mode(self) -> None:
        """Toggle between AUTO and CONFIRM mode."""
        self.config.auto_mode = not self.config.auto_mode
        mode = "AUTO" if self.config.auto_mode else "CONFIRM"
        self.notify(f"Mode changed to {mode}")

        # Refresh status bar
        positions = self._get_all_positions()
        self.update_status_bar(positions, self._current_prices)

    def action_confirm_yes(self) -> None:
        """Confirm pending action."""
        if self.pending_signal:
            logger = get_logger()
            logger.log_user_confirmed(self.pending_signal.type.value, self.pending_signal.market_slug)
            asyncio.create_task(self.execute_signal(self.pending_signal))
            # Show next signal in queue
            self._show_next_pending_signal()

    def action_confirm_no(self) -> None:
        """Reject pending action."""
        if self.pending_signal:
            logger = get_logger()
            logger.log_user_rejected(self.pending_signal.type.value, self.pending_signal.market_slug)
            self.notify(f"Skipped {self.pending_signal.market_slug}")
            # Show next signal in queue
            self._show_next_pending_signal()
