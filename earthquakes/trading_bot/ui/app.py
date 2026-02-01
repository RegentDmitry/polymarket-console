"""
Main TUI application using Textual.
"""

from datetime import datetime
from typing import List, Optional
import asyncio

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.widgets import Footer, Static, DataTable, Log
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
from ..storage.sell_orders import SellOrderStore
from ..executor.polymarket import PolymarketExecutor, OrderResult
from ..logger import get_logger
from ..monitor_data import load_monitor_data, format_extra_events, MonitorData


class StatusBar(Static):
    """Top status bar showing balance, positions, etc."""

    def __init__(self, config: BotConfig):
        super().__init__()
        self.config = config
        self.balance = 0.0
        self.matic_balance = 0.0
        self.positions_count = 0
        self.invested = 0.0
        self.unrealized_pnl = 0.0
        self.unrealized_pnl_pct = 0.0
        self.last_scan_time: Optional[datetime] = None

    def update_status(self, balance: float, positions_count: int, invested: float,
                      unrealized_pnl: float, unrealized_pnl_pct: float,
                      last_scan_time: Optional[datetime] = None,
                      matic_balance: float = 0.0) -> None:
        self.balance = balance
        self.matic_balance = matic_balance
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
        matic_str = f"{self.matic_balance:.4f}" if self.matic_balance < 1 else f"{self.matic_balance:.2f}"

        line1 = (
            f"  Balance: ${self.balance:,.2f}  |  "
            f"MATIC: {matic_str}  |  "
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
        self._rebuild()

    def set_next_scan(self, seconds: int) -> None:
        self.next_scan_seconds = seconds
        self._rebuild()

    def set_scanning(self, scanning: bool) -> None:
        self.scanning = scanning
        if not scanning:
            self.scan_status = ""  # Clear status when done
        self._rebuild()

    def set_scan_status(self, status: str) -> None:
        """Update scan progress status."""
        self.scan_status = status
        self._rebuild()

    def set_pending_confirmation(self, signal: Optional[Signal]) -> None:
        self.pending_confirmation = signal
        self._rebuild()

    def _rebuild(self) -> None:
        """Rebuild content and force layout recalculation."""
        self.update(self._build_content())

    def _build_content(self) -> Panel:
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

        for signal in skip_signals:
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


class PositionsPanel(VerticalScroll):
    """Right panel showing open positions with scroll."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.positions: List[Position] = []
        self.current_prices: dict[str, float] = {}
        self.total_invested = 0.0
        self.unrealized_pnl = 0.0
        self.unrealized_pnl_pct = 0.0

    def compose(self) -> ComposeResult:
        yield Static(id="positions-content")

    def on_mount(self) -> None:
        self.border_title = "MY POSITIONS"

    def update_positions(self, positions: List[Position], current_prices: dict[str, float],
                         fair_prices: dict[str, float] = None,
                         bid_prices: dict[str, float] = None,
                         sell_orders: dict[str, dict] = None) -> None:
        self.positions = positions
        self.current_prices = current_prices
        self.fair_prices = fair_prices or {}
        self.bid_prices = bid_prices or {}
        self.sell_orders = sell_orders or {}  # position_id -> {price, order_id, ...}

        # Calculate totals
        self.total_invested = sum(p.entry_size for p in positions)
        total_value = sum(p.current_value(current_prices.get(p.market_slug, p.entry_price))
                         for p in positions)
        self.unrealized_pnl = total_value - self.total_invested
        self.unrealized_pnl_pct = self.unrealized_pnl / self.total_invested if self.total_invested > 0 else 0

        self._rebuild()

    def _rebuild(self) -> None:
        lines = []

        if not self.positions:
            lines.append("[dim]No open positions[/dim]")
            lines.append("")
            lines.append("-" * 98)
            lines.append(f"Total invested:  $0.00")
            lines.append(f"Unrealized P&L:  $0.00 (+0.0%)")
        else:
            # Column widths — Market takes all remaining space
            EW = 6   # Entry
            FW = 6   # Fair
            BW = 6   # Bid
            SW = 6   # Sell
            CW = 6   # Curr
            OW = 8   # Cost
            VW = 9   # Value
            PW = 9   # P&L
            fixed = EW + FW + BW + SW + CW + OW + VW + PW + 8  # 8 spaces between columns
            panel_width = self.size.width - 2  # minus border
            MW = max(10, panel_width - fixed)

            header = (
                f"{'Market':<{MW}} {'Entry':>{EW}} {'Fair':>{FW}} {'Bid':>{BW}} {'Sell':>{SW}} {'Curr':>{CW}}"
                f" {'Cost':>{OW}} {'Value':>{VW}} {'P&L':>{PW}}"
            )
            lines.append(f"[bold]{header}[/bold]")

            for pos in self.positions:
                current = self.current_prices.get(pos.market_slug, pos.entry_price)
                fair = self.fair_prices.get(pos.market_slug, pos.fair_price_at_entry)
                bid = self.bid_prices.get(pos.market_slug)
                sell_order = self.sell_orders.get(pos.id)
                cost = pos.entry_size
                value = pos.current_value(current)
                pnl = value - cost

                bid_str = f"{bid:>{BW}.1%}" if bid is not None else f"{'--':>{BW}}"
                sell_str = f"{sell_order['price']:>{SW}.1%}" if sell_order else f"{'--':>{SW}}"

                # Format P&L without markup for alignment
                if pnl > 0.005:
                    pnl_raw = f"+${pnl:.2f}"
                elif pnl < -0.005:
                    pnl_raw = f"-${abs(pnl):.2f}"
                else:
                    pnl_raw = f"+$0.00"

                slug = pos.market_slug[:MW] if len(pos.market_slug) > MW else pos.market_slug

                row_prefix = (
                    f"{slug:<{MW}} {pos.entry_price:>{EW}.1%} {fair:>{FW}.1%} {bid_str} {sell_str} {current:>{CW}.1%}"
                    f" ${cost:>{OW-1}.2f} ${value:>{VW-1}.2f}"
                )
                pnl_col = f"{pnl_raw:>{PW}}"

                if pnl > 0.005:
                    lines.append(f"{row_prefix} [green]{pnl_col}[/green]")
                elif pnl < -0.005:
                    lines.append(f"{row_prefix} [red]{pnl_col}[/red]")
                else:
                    lines.append(f"{row_prefix} {pnl_col}")

            lines.append("-" * (MW + EW + FW + BW + SW + CW + OW + VW + PW + 8))
            lines.append(f"Total invested:  ${self.total_invested:.2f}")

            pnl_str = f"+${self.unrealized_pnl:.2f}" if self.unrealized_pnl >= 0 else f"-${abs(self.unrealized_pnl):.2f}"
            pnl_pct = f"+{self.unrealized_pnl_pct:.1%}" if self.unrealized_pnl_pct >= 0 else f"{self.unrealized_pnl_pct:.1%}"
            lines.append(f"Unrealized P&L:  {pnl_str} ({pnl_pct})")

        content = "\n".join(lines)
        try:
            self.query_one("#positions-content", Static).update(content)
        except Exception:
            pass


class RecentTradesPanel(Static):
    """Panel showing recent trades."""

    def __init__(self, history: HistoryStorage, **kwargs):
        super().__init__(**kwargs)
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

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
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

    TITLE = "Earthquake Trading Bot"

    CSS = """
    Screen {
        layout: vertical;
    }

    #app-header {
        dock: top;
        height: 1;
        background: #1a3a5c;
        color: #87ceeb;
    }

    #status-bar {
        height: 3;
        border: solid green;
        padding: 0 1;
        margin-top: 0;
    }

    #main-container {
        layout: horizontal;
        height: 1fr;
    }

    #left-panel {
        width: 40%;
        height: 100%;
        overflow-y: auto;
    }

    #right-panel {
        width: 60%;
        height: 100%;
        layout: vertical;
    }

    #positions-panel {
        height: 60%;
        border: solid green;
        border-title-color: green;
    }

    #trades-panel {
        height: 20%;
    }

    #extra-events-panel {
        height: 20%;
    }

    ScannerPanel {
        height: auto;
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
        self._shutting_down = False
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

        # Sell order management
        self.sell_order_store = SellOrderStore()

    def compose(self) -> ComposeResult:
        yield Static(self.title, id="app-header")
        yield Static(id="status-bar")
        with Horizontal(id="main-container"):
            with Vertical(id="left-panel"):
                yield ScannerPanel()
            with Vertical(id="right-panel"):
                yield PositionsPanel(id="positions-panel")
                yield RecentTradesPanel(self.history_storage, id="trades-panel")
                yield ExtraEventsPanel(id="extra-events-panel")
        yield Footer()

    def on_mount(self) -> None:
        """Called when app is mounted."""
        # Set header subtitle
        mode = "DRY RUN" if self.config.dry_run else ("AUTO" if self.config.auto_mode else "CONFIRM")
        header = self.query_one("#app-header", Static)
        header.update(f" ◉  {self.title} • {mode} • Scan: {format_interval(self.config.scan_interval)}")

        # Log startup
        logger = get_logger()
        logger.log_startup(mode, self.config.scan_interval, self.config.min_edge, self.config.min_apy)

        # Sync positions with Polymarket API (unless dry run)
        if not self.config.dry_run and self.executor.initialized:
            synced = self.executor.sync_positions(self.position_storage)
            if synced:
                self.notify(f"Synced {len(synced)} positions from Polymarket")

            # Clean up stale sell orders (filled or cancelled while bot was off)
            self._init_sell_orders()

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
        fair_prices = getattr(self, '_current_fair_prices', {})
        bid_prices = self.scanner._bid_prices if self.scanner else {}
        sell_orders = self.sell_order_store.load_all()
        positions_panel.update_positions(positions, self._current_prices, fair_prices, bid_prices, sell_orders)

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
                try:
                    self.call_from_thread(scanner_panel.set_scan_status, status)
                except RuntimeError:
                    pass  # App is shutting down

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

            # Note: BUY signals with zero liquidity are kept for visibility
            # (user can still place limit orders)
        else:
            entry_signals, exit_signals = [], []

        if self._shutting_down:
            return

        # Check for resolved markets
        if self.executor.initialized and positions:
            resolved_ids = set()
            # Group positions by condition_id to avoid duplicate API calls
            cid_positions: dict[str, list] = {}
            for pos in positions:
                if pos.market_id:
                    cid_positions.setdefault(pos.market_id, []).append(pos)

            def check_resolutions():
                results = []
                for cid, cid_poss in cid_positions.items():
                    try:
                        is_resolved, winning_outcome = self.executor.check_market_resolved(cid)
                        if is_resolved and winning_outcome:
                            for p in cid_poss:
                                won = p.outcome.upper() == winning_outcome.upper()
                                results.append((p, won))
                    except Exception:
                        pass
                return results

            loop2 = asyncio.get_event_loop()
            resolution_results = await loop2.run_in_executor(None, check_resolutions)

            for pos, won in resolution_results:
                result_str = "WON" if won else "LOST"
                pnl = pos.tokens - pos.entry_size if won else -pos.entry_size
                logger = get_logger()
                logger.log_info(
                    f"RESOLVED {result_str}: {pos.market_slug[:40]} - "
                    f"P&L: ${pnl:+.2f}"
                )
                self.position_storage.resolve_position(pos.id, won)
                resolved_ids.add(pos.id)
                self.notify(
                    f"Market resolved ({result_str}): {pos.market_slug[:30]} P&L: ${pnl:+.2f}",
                    markup=False,
                )

            if resolved_ids:
                # Refresh positions list after resolving
                positions = self._get_all_positions()

        # Record scan time
        self._last_scan_time = datetime.now()

        # Update UI
        scanner_panel.update_signals(entry_signals, exit_signals, self._last_scan_time)
        scanner_panel.set_scanning(False)
        self.scanning = False
        self.refresh_bindings()  # Re-enable R in footer

        # Update positions panel with current prices and fair prices
        self._current_prices = {s.market_slug: s.current_price for s in entry_signals}
        self._current_fair_prices = {s.market_slug: s.fair_price for s in entry_signals}

        # Map current/fair prices for synced positions (different slugs, same condition_id)
        cid_to_price = {}
        cid_to_fair = {}
        for s in entry_signals:
            if s.market_id:
                key = f"{s.market_id}-{s.outcome}"
                cid_to_price[key] = s.current_price
                cid_to_fair[key] = s.fair_price
        for pos in positions:
            if pos.market_id:
                key = f"{pos.market_id}-{pos.outcome.upper()}"
                if pos.market_slug not in self._current_prices and key in cid_to_price:
                    self._current_prices[pos.market_slug] = cid_to_price[key]
                if key in cid_to_fair:
                    self._current_fair_prices[pos.market_slug] = cid_to_fair[key]

        self._refresh_positions_panel()

        # Update trades panel
        trades_panel = self.query_one(RecentTradesPanel)
        trades_panel.update_trades()

        # Update extra events panel (from monitor_bot)
        extra_events_panel = self.query_one(ExtraEventsPanel)
        monitor_data = load_monitor_data()
        extra_events_panel.update_data(monitor_data)

        if self._shutting_down:
            return

        # Update MATIC balance (for gas fees display)
        if self.executor.initialized:
            self._matic_balance = self.executor.get_matic_balance()

        # Update status bar
        self.update_status_bar(positions, self._current_prices)

        if self._shutting_down:
            return

        # Manage sell limit orders (place/update at fair price)
        fair_prices = getattr(self, '_current_fair_prices', {})
        if fair_prices and positions:
            loop3 = asyncio.get_event_loop()
            await loop3.run_in_executor(
                None, self._manage_sell_orders, positions, fair_prices
            )
            # Refresh positions and balance after potential fills
            positions = self._get_all_positions()
            self._refresh_positions_panel()
            current_prices = self.scanner.get_current_prices() if self.scanner else {}
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
        matic = getattr(self, '_matic_balance', 0.0)

        status_bar = StatusBar(self.config)
        status_bar.update_status(
            balance=balance,
            positions_count=len(positions),
            invested=invested,
            unrealized_pnl=unrealized_pnl,
            unrealized_pnl_pct=unrealized_pnl_pct,
            last_scan_time=self._last_scan_time,
            matic_balance=matic,
        )

        status = self.query_one("#status-bar", Static)
        status.update(status_bar.render())

    def _init_sell_orders(self):
        """Clean up stale sell orders at startup."""
        logger = get_logger()
        try:
            open_ids = self.executor.get_open_order_ids()
            all_orders = self.sell_order_store.load_all()
            for pos_id, info in list(all_orders.items()):
                if info["order_id"] not in open_ids:
                    # Order was filled or cancelled while bot was off
                    pos = self.position_storage.load(pos_id)
                    if pos:
                        logger.log_info(f"SELL ORDER gone (filled?): {info['market_slug'][:40]} @ {info['price']:.1%}")
                        # Don't auto-close — could have been cancelled. Will re-place on next scan.
                    self.sell_order_store.remove(pos_id)
            remaining = len(self.sell_order_store.load_all())
            if remaining:
                logger.log_info(f"Loaded {remaining} active sell orders")
        except Exception as e:
            logger.log_warning(f"Error initializing sell orders: {e}")

    def _get_token_balance(self, token_id: str) -> Optional[float]:
        """Get on-chain token balance from API (in token units)."""
        try:
            from polymarket_console.clob_types import BalanceAllowanceParams, AssetType
            params = BalanceAllowanceParams(
                asset_type=AssetType.CONDITIONAL,
                token_id=token_id,
            )
            bal = self.executor.client.client.get_balance_allowance(params)
            raw = float(bal.get("balance", 0))
            return raw / 1e6
        except Exception:
            return None

    def _manage_sell_orders(self, positions: List[Position], fair_prices: dict[str, float]):
        """Place/update sell limit orders at fair price for all positions."""
        if self.config.dry_run or not self.executor.initialized or self._shutting_down:
            return

        logger = get_logger()
        try:
            open_ids = self.executor.get_open_order_ids()
        except Exception:
            return

        for pos in positions:
            fair = fair_prices.get(pos.market_slug)
            if not fair and pos.market_id and self.scanner:
                key = f"{pos.market_id}-{pos.outcome.upper()}"
                mapped_slug = self.scanner._condition_id_to_slug.get(key)
                if mapped_slug:
                    fair = fair_prices.get(mapped_slug)
            if not fair or fair <= 0 or fair >= 1:
                logger.log_info(
                    f"SELL SKIP (no fair): {pos.market_slug[:40]} fair={fair}"
                )
                continue

            if self._shutting_down:
                return

            existing = self.sell_order_store.get(pos.id)

            # Check if existing order was filled or cancelled
            if existing and existing["order_id"] not in open_ids:
                # Order gone from open orders — check if filled or cancelled
                is_filled = False
                try:
                    order_info = self.executor.client.client.get_order(existing["order_id"])
                    status = order_info.get("status", "").upper() if order_info else ""
                    size_matched = float(order_info.get("size_matched", 0)) if order_info else 0
                    logger.log_info(
                        f"SELL ORDER status: {existing['order_id'][:16]}... "
                        f"status={status} matched={size_matched}"
                    )
                    is_filled = status == "MATCHED" or size_matched > 0
                except Exception as e:
                    logger.log_warning(f"SELL ORDER check failed: {e}")

                if is_filled:
                    pnl = pos.tokens * existing["price"] - pos.entry_size
                    logger.log_info(
                        f"SELL FILLED: {pos.market_slug[:40]} @ {existing['price']:.1%} "
                        f"P&L: ${pnl:+.2f}"
                    )
                    self.position_storage.close_position(
                        pos.id, existing["price"], existing["order_id"]
                    )
                    self.sell_order_store.remove(pos.id)
                    self.notify(
                        f"SOLD: {pos.market_slug[:30]} @ {existing['price']:.1%} P&L: ${pnl:+.2f}",
                        markup=False,
                    )
                    continue
                else:
                    if status == "LIVE":
                        # Still live but not in open_ids yet (race condition) — skip
                        continue
                    # Cancelled or expired — remove and re-place on next iteration
                    logger.log_info(f"SELL ORDER cancelled/expired: {pos.market_slug[:40]} status={status}")
                    self.sell_order_store.remove(pos.id)
                    # Fall through to place new order below

            # Check if fair price changed enough to update order
            if existing:
                if abs(existing["price"] - fair) < 0.005:
                    continue  # Fair unchanged
                # Cancel old order
                self.executor.cancel_order(existing["order_id"])
                self.sell_order_store.remove(pos.id)
                logger.log_info(
                    f"SELL ORDER updated: {pos.market_slug[:40]} "
                    f"{existing['price']:.1%} -> {fair:.1%}"
                )

            # Get token_id for this position (try slug, then condition_id mapping)
            token_id = None
            if self.scanner:
                token_id = self.scanner._token_ids.get(pos.market_slug)
                if not token_id and pos.market_id:
                    key = f"{pos.market_id}-{pos.outcome.upper()}"
                    mapped_slug = self.scanner._condition_id_to_slug.get(key)
                    if mapped_slug:
                        token_id = self.scanner._token_ids.get(mapped_slug)
            # Fallback: fetch token_id from API by condition_id
            if not token_id and pos.market_id and self.executor.client:
                try:
                    market_data = self.executor.client.get_clob_market(pos.market_id)
                    if market_data:
                        tokens = market_data.get("tokens", [])
                        for t in tokens:
                            if t.get("outcome", "").upper() == pos.outcome.upper():
                                token_id = t.get("token_id")
                                # Cache for future use
                                if self.scanner and token_id:
                                    self.scanner._token_ids[pos.market_slug] = token_id
                                break
                except Exception as e:
                    logger.log_warning(f"SELL token_id API lookup failed: {e}")
            if not token_id:
                logger.log_info(
                    f"SELL SKIP (no token_id): {pos.market_slug[:40]} "
                    f"mid={pos.market_id[:16] if pos.market_id else 'none'}"
                )
                continue

            # Check how much is already reserved in sell orders for this token_id
            already_reserved = sum(
                info["size"] for pid, info in self.sell_order_store.load_all().items()
                if info.get("token_id") == token_id
            )
            # Get on-chain balance for this token
            on_chain = self._get_token_balance(token_id)
            if on_chain is not None:
                available = on_chain - already_reserved
                import math
                sell_size = min(pos.tokens, math.floor(available * 100) / 100)
                if sell_size < 0.01:
                    logger.log_info(
                        f"SELL SKIP (no balance): {pos.market_slug[:40]} "
                        f"on_chain={on_chain:.2f} reserved={already_reserved:.2f}"
                    )
                    continue  # No room left for this token
            else:
                sell_size = pos.tokens

            # Place new sell limit at fair price
            order_id = self.executor.place_sell_limit(token_id, fair, sell_size)
            if order_id:
                self.sell_order_store.save(
                    pos.id, order_id, fair, token_id, sell_size, pos.market_slug
                )
            else:
                logger.log_info(
                    f"SELL SKIP (place failed): {pos.market_slug[:40]} "
                    f"fair={fair:.4f} size={sell_size:.2f}"
                )

    async def process_signals(self, entry_signals: List[Signal],
                              exit_signals: List[Signal]) -> None:
        """Process trading signals based on mode."""
        # Filter out BUY signals if balance is too low
        balance = self.executor.get_balance() if self.executor.initialized else 0
        buy_signals = [s for s in entry_signals if s.type == SignalType.BUY] if balance >= 1.0 else []
        actionable = buy_signals + exit_signals

        if not actionable:
            return

        if self.config.auto_mode:
            # AUTO mode - execute immediately
            for signal in actionable:
                await self.execute_signal(signal)
        else:
            # CONFIRM mode - auto-execute sells, confirm only buys
            needs_confirm = []
            for signal in actionable:
                if signal.type == SignalType.BUY:
                    needs_confirm.append(signal)
                else:
                    await self.execute_signal(signal)
            if needs_confirm:
                self._pending_signals_queue = needs_confirm
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

        self.notify(f"Executing {signal.type.value} for {signal.market_slug}...", markup=False)

        # Get market from cache
        market = self._markets_cache.get(signal.market_slug)
        if not market:
            self.notify(f"Error: Market {signal.market_slug} not found in cache", markup=False)
            return

        if signal.type == SignalType.BUY:
            # Execute buy order
            result, position = self.executor.buy(signal, market)

            if result.success and position:
                # Save position
                self.position_storage.save(position)
                self.history_storage.record_buy(position, result.order_id)
                self.notify(f"BUY order placed: {result.order_id}", markup=False)

                logger.log_trade_executed(
                    "BUY", signal.market_slug, signal.outcome,
                    signal.current_price, position.tokens, position.entry_size
                )
                logger.log_position_opened(position)

                # Update current price for the new position and refresh UI
                self._current_prices[signal.market_slug] = signal.current_price
                self._refresh_positions_panel()
                positions = self._get_all_positions()
                self.update_status_bar(positions, self._current_prices)
            else:
                self.notify(f"BUY failed: {result.error}", markup=False)
                logger.log_trade_failed("BUY", signal.market_slug, result.error or "Unknown error")

        elif signal.type == SignalType.SELL and signal.position_id:
            # Get position
            position = self.position_storage.load(signal.position_id)
            if not position:
                self.notify(f"Error: Position {signal.position_id} not found", markup=False)
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
                self.notify(f"SELL order placed: {result.order_id}", markup=False)
                # Refresh positions panel and balance immediately
                self._refresh_positions_panel()
                positions = self._get_all_positions()
                self.update_status_bar(positions, self._current_prices)
            else:
                self.notify(f"SELL failed: {result.error}", markup=False)
                logger.log_trade_failed("SELL", signal.market_slug, result.error or "Unknown error")

    def action_quit(self) -> None:
        """Quit the application (with confirmation)."""
        if not self.quit_pending:
            self.quit_pending = True
            self.notify("Quit? Press ENTER to confirm, any other key to cancel")

    def on_key(self, event) -> None:
        """Handle key presses for quit confirmation."""
        if self.quit_pending:
            if event.key == "enter":
                self._shutting_down = True
                if self.scan_timer:
                    self.scan_timer.stop()
                if self.countdown_timer:
                    self.countdown_timer.stop()
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

        # Update header subtitle
        display_mode = "DRY RUN" if self.config.dry_run else mode
        self.sub_title = f"Mode: {display_mode} • Scan: {format_interval(self.config.scan_interval)}"

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
