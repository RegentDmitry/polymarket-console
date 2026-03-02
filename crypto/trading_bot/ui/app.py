"""
Main TUI application for the crypto trading bot.

Adapted from earthquakes/trading_bot/ui/app.py.
Key differences:
- No ExtraEventsPanel / reserve balance
- LiveDataPanel (BTC/ETH spot + IV)
- FuturesCurvePanel (Deribit futures curve)
- Background data update threads (Binance 10s, Deribit 5min)
- Rotation proposals display in ScannerPanel
"""

import math
import time
import threading
from datetime import datetime, timezone
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
from ..pricing.portfolio import allocate_sizes, get_portfolio_breakdown
from ..logger import get_logger


class StatusBar(Static):
    """Top status bar showing balance, positions, etc."""

    def __init__(self, config: BotConfig):
        super().__init__()
        self.config = config
        self.balance = 0.0
        self.pol_balance = 0.0
        self.positions_count = 0
        self.invested = 0.0
        self.unrealized_pnl = 0.0
        self.unrealized_pnl_pct = 0.0
        self.last_scan_time: Optional[datetime] = None
        self.missed_liquidity = 0.0

    def update_status(self, balance: float, positions_count: int, invested: float,
                      unrealized_pnl: float, unrealized_pnl_pct: float,
                      last_scan_time: Optional[datetime] = None,
                      pol_balance: float = 0.0,
                      missed_liquidity: float = 0.0) -> None:
        self.balance = balance
        self.pol_balance = pol_balance
        self.missed_liquidity = missed_liquidity
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
        utc_now = datetime.now(timezone.utc).strftime("%H:%M:%S")
        pol_str = f"{self.pol_balance:.4f}" if self.pol_balance < 1 else f"{self.pol_balance:.2f}"

        line1_parts = [
            f"  UTC: {utc_now}",
            f"Balance: ${self.balance:,.2f}",
            f"MATIC: {pol_str}",
            f"Positions: {self.positions_count}",
            f"Invested: ${self.invested:,.2f}",
        ]
        line1 = "  |  ".join(line1_parts)

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
        text.append_text(Text.from_markup(line2))
        return text


class ScannerPanel(Static):
    """Left panel showing scanner results."""

    def __init__(self):
        super().__init__()
        self.signals: List[Signal] = []
        self.exit_signals: List[Signal] = []
        self.rotation_proposals: List[dict] = []
        self.next_scan_seconds = 0
        self.scanning = False
        self.scan_status: str = ""
        self.pending_confirmation: Optional[Signal] = None
        self.last_scan_time: Optional[datetime] = None

    def update_signals(self, signals: List[Signal], exit_signals: List[Signal],
                       last_scan_time: Optional[datetime] = None,
                       rotation_proposals: List[dict] = None) -> None:
        self.signals = signals
        self.exit_signals = exit_signals
        self.rotation_proposals = rotation_proposals or []
        if last_scan_time:
            self.last_scan_time = last_scan_time
        self._rebuild()

    def set_next_scan(self, seconds: int) -> None:
        self.next_scan_seconds = seconds
        self._rebuild()

    def set_scanning(self, scanning: bool) -> None:
        self.scanning = scanning
        if not scanning:
            self.scan_status = ""
        self._rebuild()

    def set_scan_status(self, status: str) -> None:
        self.scan_status = status
        self._rebuild()

    def set_pending_confirmation(self, signal: Optional[Signal]) -> None:
        self.pending_confirmation = signal
        self._rebuild()

    def _rebuild(self) -> None:
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
            lines.append(f"  Price: {signal.current_price:.1%}  Fair: {signal.fair_price:.1%}")
            kelly_str = f"  Kelly: {signal.kelly:.0%}" if signal.kelly > 0 else ""
            lines.append(f"  APY: {signal.annual_return:.0%}  Edge: {signal.edge:.1%}  Days: {signal.days_remaining}{kelly_str}")

            liq_str = f"${signal.liquidity:.0f}" if signal.liquidity else "$0"
            size_str = f"${signal.suggested_size:.0f}" if signal.suggested_size > 0 else "-"
            lines.append(f"  Available: {liq_str}  |  Buy: {size_str}")
            cost_str = f" (${signal.suggested_size:.2f})" if signal.suggested_size > 0 else ""
            lines.append(f"  >>> BUY {signal.outcome}{cost_str}")

            if self.pending_confirmation and self.pending_confirmation.market_slug == signal.market_slug:
                lines.append("  [yellow]Confirm? [Y/N][/yellow]")

            lines.append("")

        for signal in skip_signals:
            lines.append(f"[dim]- {signal.market_slug} ({signal.market_name})[/dim]")
            lines.append(f"[dim]  Price: {signal.current_price:.1%}  Fair: {signal.fair_price:.1%}  Edge: {signal.edge:.1%}  APY: {signal.annual_return:.0%}  Days: {signal.days_remaining}[/dim]")
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

        # Rotation proposals
        if self.rotation_proposals:
            lines.append("[magenta]ROTATION PROPOSALS:[/magenta]")
            lines.append("")
            for r in self.rotation_proposals[:3]:
                sell_pos = r["sell_position"]
                buy_sig = r["buy_signal"]
                bid_liq = r.get("sell_bid_liquidity", 0)
                buy_kelly = r.get("buy_kelly", 0)
                lines.append(f"[magenta]\u21bb Sell {sell_pos.market_slug[:25]}[/magenta]")
                lines.append(f"  Bid: {r['sell_bid']:.1%}  Liq: ${bid_liq:.0f}  P&L: ${r['sell_loss']:+.2f}")
                lines.append(f"  \u2192 Buy {buy_sig.market_slug[:25]}")
                lines.append(f"  Edge: {r['buy_edge']:.1%}  Kelly: {buy_kelly:.0%}  Net: +${r['net_improvement']:.2f}")
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
        self.positions = sorted(positions, key=lambda p: (p.market_slug, -p.entry_size))
        self.current_prices = current_prices
        self.fair_prices = fair_prices or {}
        self.bid_prices = bid_prices or {}
        self.sell_orders = sell_orders or {}

        self.total_invested = sum(p.entry_size for p in positions)
        total_value = sum(p.current_value(self.fair_prices.get(p.market_slug, p.fair_price_at_entry))
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
            EW = 6   # Entry
            FW = 6   # Fair
            CW = 6   # Curr
            BW = 6   # Bid
            SW = 6   # Sell
            OW = 8   # Cost
            VW = 9   # Value
            PW = 9   # P&L
            fixed = EW + FW + CW + BW + SW + OW + VW + PW + 8
            panel_width = self.size.width - 2
            MW = max(10, panel_width - fixed)

            header = (
                f"{'Market':<{MW}} {'Entry':>{EW}} {'Fair':>{FW}} {'Curr':>{CW}} {'Bid':>{BW}} {'Sell':>{SW}}"
                f" {'Cost':>{OW}} {'Value':>{VW}} {'P&L':>{PW}}"
            )
            lines.append(f"[bold]{header}[/bold]")

            for pos in self.positions:
                current = self.current_prices.get(pos.market_slug, pos.entry_price)
                fair = self.fair_prices.get(pos.market_slug, pos.fair_price_at_entry)
                bid = self.bid_prices.get(pos.market_slug, 0)
                sell_order = self.sell_orders.get(pos.id)
                cost = pos.entry_size
                value = pos.current_value(fair)
                pnl = value - cost

                sell_str = f"{sell_order['price']:>{SW}.1%}" if sell_order else f"{'--':>{SW}}"
                bid_str = f"{bid:>{BW}.1%}" if bid > 0 else f"{'--':>{BW}}"

                if pnl > 0.005:
                    pnl_raw = f"+${pnl:.2f}"
                elif pnl < -0.005:
                    pnl_raw = f"-${abs(pnl):.2f}"
                else:
                    pnl_raw = f"+$0.00"

                slug = pos.market_slug[:MW] if len(pos.market_slug) > MW else pos.market_slug

                row_prefix = (
                    f"{slug:<{MW}} {pos.entry_price:>{EW}.1%} {fair:>{FW}.1%} {current:>{CW}.1%} {bid_str} {sell_str}"
                    f" ${cost:>{OW-1}.2f} ${value:>{VW-1}.2f}"
                )
                pnl_col = f"{pnl_raw:>{PW}}"

                if pnl > 0.005:
                    lines.append(f"{row_prefix} [green]{pnl_col}[/green]")
                elif pnl < -0.005:
                    lines.append(f"{row_prefix} [red]{pnl_col}[/red]")
                else:
                    lines.append(f"{row_prefix} {pnl_col}")

            lines.append("-" * (MW + EW + FW + CW + BW + SW + OW + VW + PW + 8))
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

        content = "\n".join(lines) if lines else "No trades yet"
        return Panel(content, title="RECENT TRADES", border_style="cyan")


class LiveDataPanel(Static):
    """Panel showing live BTC/ETH spot + IV from Deribit/Binance."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.btc_spot = 0.0
        self.eth_spot = 0.0
        self.btc_iv = 0.0
        self.eth_iv = 0.0
        self._updated_at: float = 0.0  # time.monotonic() of last update

    def update_data(self, btc_spot: float, eth_spot: float,
                    btc_iv: float, eth_iv: float, age: int = 0) -> None:
        self.btc_spot = btc_spot
        self.eth_spot = eth_spot
        self.btc_iv = btc_iv
        self.eth_iv = eth_iv
        self._updated_at = time.monotonic()
        self.refresh()

    def tick(self) -> None:
        """Called every second to update age display."""
        if self._updated_at > 0:
            self.refresh()

    def render(self) -> Panel:
        lines = []

        if self.btc_spot > 0:
            lines.append(f"BTC  ${self.btc_spot:>10,.0f}   IV: {self.btc_iv:>5.1%}")
        else:
            lines.append("BTC  --           IV: --")

        if self.eth_spot > 0:
            lines.append(f"ETH  ${self.eth_spot:>10,.0f}   IV: {self.eth_iv:>5.1%}")
        else:
            lines.append("ETH  --           IV: --")

        lines.append("")
        if self._updated_at > 0:
            age = int(time.monotonic() - self._updated_at)
            lines.append(f"Updated: {age}s ago")
        else:
            lines.append("Updated: --")

        content = "\n".join(lines)
        return Panel(content, title="LIVE DATA", border_style="green")


class FuturesCurvePanel(Static):
    """Panel showing Deribit futures curve with drift."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.btc_curve: list = []
        self.eth_curve: list = []
        self._updated_at: float = 0.0

    def update_curve(self, btc_curve: list, eth_curve: list, age: int = 0) -> None:
        self.btc_curve = btc_curve
        self.eth_curve = eth_curve
        self._updated_at = time.monotonic()
        self.refresh()

    def tick(self) -> None:
        """Called every second to update age display."""
        if self._updated_at > 0:
            self.refresh()

    def render(self) -> Panel:
        lines = []

        if not self.btc_curve and not self.eth_curve:
            lines.append("[dim]No futures data[/dim]")
        else:
            for curve_list in [self.btc_curve, self.eth_curve]:
                for item in curve_list:
                    if isinstance(item, (list, tuple)):
                        _days, drift, price, name = item
                    else:
                        name = item.get("name", "?")
                        price = item.get("price", 0)
                        drift = item.get("drift", 0)
                    lines.append(f"{name:<16} ${price:>10,.0f}  drift {drift:>+6.1%}")
                if curve_list is not self.eth_curve and self.eth_curve:
                    lines.append("")  # separator between BTC and ETH

        lines.append("")
        if self._updated_at > 0:
            age = int(time.monotonic() - self._updated_at)
            lines.append(f"Updated: {age}s ago")

        content = "\n".join(lines)
        return Panel(content, title="FUTURES CURVE", border_style="yellow")


class PortfolioRiskPanel(Static):
    """Panel showing portfolio risk breakdown by currency and direction."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._breakdown: dict = {}
        self._total_kelly: float = 0.0
        self._max_positions: int = 20
        self._updated_at: float = 0.0

    def update_risk(self, positions: list, balance: float,
                    total_kelly: float = 0.0, max_positions: int = 20) -> None:
        self._breakdown = get_portfolio_breakdown(positions, balance)
        self._total_kelly = total_kelly
        self._max_positions = max_positions
        self._updated_at = time.monotonic()
        self.refresh()

    def tick(self) -> None:
        if self._updated_at > 0:
            self.refresh()

    def render(self) -> Panel:
        lines = []
        bd = self._breakdown

        if not bd or bd.get("total_portfolio", 0) <= 0:
            lines.append("[dim]No data[/dim]")
        else:
            total = bd["total_portfolio"]
            bar_width = 20

            # Currency breakdown
            for cur in ["BTC", "ETH"]:
                amt = bd["currency"].get(cur, 0)
                pct = amt / total if total > 0 else 0
                filled = int(pct * bar_width)
                bar = "\u2588" * filled + "\u2591" * (bar_width - filled)
                lines.append(f"{cur}  {bar} {pct:>5.0%} (${amt:,.0f})")

            lines.append("")

            # Direction breakdown
            labels = {"up": "\u2191 UP ", "down": "\u2193 DN "}
            for d in ["up", "down"]:
                amt = bd["direction"].get(d, 0)
                pct = amt / total if total > 0 else 0
                filled = int(pct * bar_width)
                bar = "\u2588" * filled + "\u2591" * (bar_width - filled)
                lines.append(f"{labels[d]} {bar} {pct:>5.0%} (${amt:,.0f})")

            lines.append("")
            pos_count = bd.get("position_count", 0)
            invested = bd.get("total_invested", 0)
            bal = bd.get("balance", 0)
            lines.append(
                f"Positions: {pos_count}/{self._max_positions}  "
                f"Kelly: {self._total_kelly:.0%}  "
                f"Inv: ${invested:,.0f}  Free: ${bal:,.0f}"
            )

        lines.append("")
        if self._updated_at > 0:
            age = int(time.monotonic() - self._updated_at)
            lines.append(f"Updated: {age}s ago")

        content = "\n".join(lines)
        return Panel(content, title="PORTFOLIO RISK", border_style="magenta")


class TradingBotApp(App):
    """Main crypto trading bot TUI application."""

    TITLE = "Crypto Trading Bot"

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
        height: 4;
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
        overflow-y: auto;
    }

    #positions-panel {
        height: auto;
        min-height: 8;
        border: solid green;
        border-title-color: green;
    }

    #trades-panel {
        height: auto;
        min-height: 4;
    }

    #live-data-panel {
        height: auto;
    }

    #futures-panel {
        height: auto;
    }

    #portfolio-panel {
        height: auto;
    }

    ScannerPanel {
        height: auto;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit", key_display="Q"),
        Binding("r", "refresh", "Scan", key_display="R"),
        Binding("h", "history", "History", key_display="H"),
        Binding("p", "refresh_portfolio", "Portfolio", key_display="P"),
        Binding("y", "confirm_yes", "Yes", show=False),
        Binding("n", "confirm_no", "No", show=False),
        # Russian keyboard layout support
        Binding("й", "quit", "Quit", show=False),
        Binding("к", "refresh", "Scan", show=False),
        Binding("р", "history", "History", show=False),
        Binding("з", "refresh_portfolio", "Portfolio", show=False),
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

        # Track buys in current scan cycle
        self._had_buys_this_cycle = False

        # Virtual positions for DRY RUN mode
        self._dry_run_positions: List[Position] = []
        self._dry_run_size = 10.0

        # Cache for markets and prices
        self._markets_cache: dict[str, Market] = {}
        self._current_prices: dict[str, float] = {}
        self._current_fair_prices: dict[str, float] = {}
        self._last_scan_time: Optional[datetime] = None

        # Sell order management
        self.sell_order_store = SellOrderStore()

        # Tracking
        self._missed_liquidity = 0.0
        self._pol_balance = 0.0

        # Background data threads
        self._data_threads_started = False

        # Portfolio risk tracking
        self._portfolio_update_counter = 0
        self._portfolio_update_interval = 10  # scans between portfolio updates

    def compose(self) -> ComposeResult:
        yield Static(self.title, id="app-header")
        yield Static(id="status-bar")
        with Horizontal(id="main-container"):
            with Vertical(id="left-panel"):
                yield ScannerPanel()
            with VerticalScroll(id="right-panel"):
                yield PositionsPanel(id="positions-panel")
                yield PortfolioRiskPanel(id="portfolio-panel")
                yield RecentTradesPanel(self.history_storage, id="trades-panel")
                yield LiveDataPanel(id="live-data-panel")
                yield FuturesCurvePanel(id="futures-panel")
        yield Footer()

    def on_mount(self) -> None:
        """Called when app is mounted."""
        mode = "DRY RUN" if self.config.dry_run else ("AUTO" if self.config.auto_mode else "CONFIRM")
        header = self.query_one("#app-header", Static)
        header.update(f" ◉  {self.title} • {mode} • Scan: {format_interval(self.config.scan_interval)}")

        logger = get_logger()
        logger.log_startup(mode, self.config.scan_interval, self.config.min_edge, self.config.min_apy)

        # Sync positions with Polymarket API (unless dry run)
        if not self.config.dry_run and self.executor.initialized:
            self._init_sell_orders()
            self.call_later(self._check_balance_discrepancies)

        # Initialize status bar
        status = self.query_one("#status-bar", Static)
        status_bar = StatusBar(self.config)
        status.update(status_bar.render())

        # Start background data threads
        self._start_data_threads()

        # Initial portfolio panel
        self._refresh_portfolio_panel()

        # Start countdown timer immediately
        self.countdown_timer = self.set_interval(1, self.update_countdown)

        # Delay first scan by 5 seconds to let UI render and data load
        self.next_scan_seconds = 5
        self.set_timer(5, self.start_scanning)

    def _start_data_threads(self) -> None:
        """Start background threads for data updates."""
        if self._data_threads_started or not self.scanner:
            return
        self._data_threads_started = True

        # Binance spot: update immediately, then every 10 seconds
        def binance_loop():
            while not self._shutting_down:
                try:
                    self.scanner.binance.update()
                except Exception:
                    pass
                for _ in range(100):  # 10s in 0.1s increments
                    if self._shutting_down:
                        return
                    time.sleep(0.1)

        # Deribit spot + IV + futures: update immediately, then every 30 seconds
        def deribit_loop():
            while not self._shutting_down:
                try:
                    self.scanner.deribit.update()
                    snap = self.scanner.deribit.get_snapshot()
                    binance_snap = self.scanner.binance.get_snapshot()
                    btc_spot = snap.get("btc_spot", 0) or binance_snap.get("btc_price", 0)
                    eth_spot = snap.get("eth_spot", 0) or binance_snap.get("eth_price", 0)
                    deribit_age = self.scanner.deribit.age_seconds
                    age = int(deribit_age) if deribit_age < 1e9 else 0
                    try:
                        self.call_from_thread(
                            self._update_live_data_panel,
                            btc_spot, eth_spot,
                            snap.get("btc_iv", 0), snap.get("eth_iv", 0),
                        )
                        self.call_from_thread(
                            self._update_futures_panel,
                            snap.get("btc_curve", []),
                            snap.get("eth_curve", []),
                            age,
                        )
                    except RuntimeError:
                        pass  # App shutting down
                except Exception:
                    pass
                for _ in range(300):  # 30s in 0.1s increments
                    if self._shutting_down:
                        return
                    time.sleep(0.1)

        # Polymarket markets: update every 5 minutes
        def polymarket_loop():
            while not self._shutting_down:
                try:
                    self.scanner.polymarket.update()
                except Exception:
                    pass
                for _ in range(3000):  # 5min
                    if self._shutting_down:
                        return
                    time.sleep(0.1)

        for fn, name in [(binance_loop, "binance"), (deribit_loop, "deribit"),
                         (polymarket_loop, "polymarket")]:
            t = threading.Thread(target=fn, name=f"data-{name}", daemon=True)
            t.start()

    def _update_live_data_panel(self, btc_spot, eth_spot, btc_iv, eth_iv):
        """Update live data panel (called from thread via call_from_thread)."""
        try:
            panel = self.query_one(LiveDataPanel)
            age = int(self.scanner.binance.age_seconds) if self.scanner else 0
            panel.update_data(btc_spot, eth_spot, btc_iv, eth_iv, age)
        except Exception:
            pass

    def _update_futures_panel(self, btc_curve, eth_curve, age=0):
        """Update futures curve panel (called from thread)."""
        try:
            panel = self.query_one(FuturesCurvePanel)
            panel.update_curve(btc_curve, eth_curve, age)
        except Exception:
            pass

    def start_scanning(self) -> None:
        """Start scanning after initial delay."""
        self.scan_timer = self.set_interval(self.config.scan_interval, self.do_scan)
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
        fair_prices = self._current_fair_prices
        bid_prices = self.scanner._bid_prices if self.scanner else {}
        sell_orders = self.sell_order_store.load_all()
        positions_panel.update_positions(positions, self._current_prices, fair_prices, bid_prices, sell_orders)

    def _refresh_portfolio_panel(self, positions=None, signals=None) -> None:
        """Refresh portfolio risk panel."""
        try:
            if positions is None:
                positions = self._get_all_positions()
            balance = self.executor.get_balance() if self.executor.initialized else 0
            if self.config.dry_run:
                balance = max(balance, 1000.0)
            total_kelly = sum(s.kelly for s in (signals or [])
                              if s.type == SignalType.BUY and s.kelly > 0)
            panel = self.query_one(PortfolioRiskPanel)
            panel.update_risk(positions, balance, total_kelly, self.config.max_positions)
        except Exception:
            pass

    def update_countdown(self) -> None:
        """Update countdown to next scan + tick data panels."""
        if self.scanning:
            return

        self.next_scan_seconds -= 1
        if self.next_scan_seconds < 0:
            self.next_scan_seconds = self.config.scan_interval

        scanner_panel = self.query_one(ScannerPanel)
        scanner_panel.set_next_scan(self.next_scan_seconds)

        # Tick live data panels (updates "Xs ago" every second)
        try:
            self.query_one(LiveDataPanel).tick()
            self.query_one(FuturesCurvePanel).tick()
            self.query_one(PortfolioRiskPanel).tick()
        except Exception:
            pass

    async def do_scan(self) -> None:
        """Perform market scan."""
        self._had_buys_this_cycle = False

        # Clear pending confirmations
        if self.pending_signal or self._pending_signals_queue:
            self.pending_signal = None
            self._pending_signals_queue = []
            scanner_panel = self.query_one(ScannerPanel)
            scanner_panel.set_pending_confirmation(None)

        if not self.scanning:
            self.scanning = True
            self.refresh_bindings()
            scanner_panel = self.query_one(ScannerPanel)
            scanner_panel.set_scanning(True)
        else:
            scanner_panel = self.query_one(ScannerPanel)

        self.next_scan_seconds = self.config.scan_interval

        positions = self._get_all_positions()

        if self.scanner:
            loop = asyncio.get_event_loop()

            def update_status(status: str) -> None:
                try:
                    self.call_from_thread(scanner_panel.set_scan_status, status)
                except RuntimeError:
                    pass

            def do_scan_with_progress():
                entry_signals = self.scanner.scan_for_entries(progress_callback=update_status)
                current_prices = self.scanner.get_current_prices()
                exit_signals = self.scanner.scan_for_exits(positions, current_prices)
                rotation_proposals = self.scanner.scan_for_rotations(
                    positions,
                    [s for s in entry_signals if s.type == SignalType.BUY],
                    self.executor.get_balance() if self.executor.initialized else 0,
                )
                return entry_signals, exit_signals, rotation_proposals

            entry_signals, exit_signals, rotation_proposals = await loop.run_in_executor(
                None, do_scan_with_progress
            )

            # Cache markets for executor
            for market in self.scanner.get_markets():
                self._markets_cache[market.slug] = market

            # Sort by APY (primary) → Edge (secondary)
            # Prefer short-term positions with high annualized return
            entry_signals.sort(key=lambda s: (s.annual_return, s.edge), reverse=True)

            # Kelly-based allocation
            if self.config.dry_run:
                total_balance = max(self.executor.get_balance() if self.executor.initialized else 0, 1000.0)
            else:
                total_balance = self.executor.get_balance() if self.executor.initialized else 0

            allocate_sizes(entry_signals, total_balance, positions, self.config)

            # Track missed liquidity
            total_liquidity = sum(s.liquidity for s in entry_signals if s.liquidity > 0)
            allocated = sum(s.suggested_size for s in entry_signals if s.suggested_size > 0)
            self._missed_liquidity = max(0, total_liquidity - allocated)
        else:
            entry_signals, exit_signals, rotation_proposals = [], [], []

        if self._shutting_down:
            return

        # Check resolved markets
        if self.executor.initialized and positions:
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
                logger.log_info(f"RESOLVED {result_str}: {pos.market_slug[:40]} - P&L: ${pnl:+.2f}")

                if won and pos.market_id:
                    token_id = None
                    if pos.market_slug in self._markets_cache:
                        market = self._markets_cache[pos.market_slug]
                        token_id = market.yes_token_id if pos.outcome.upper() == "YES" else market.no_token_id
                    if not token_id and self.scanner:
                        token_id = self.scanner._token_ids.get(pos.market_slug)
                    if token_id:
                        try:
                            redeemed = self.executor.redeem_neg_risk(pos.market_id, pos.outcome, token_id)
                            if redeemed:
                                logger.log_info(f"REDEEMED: {pos.market_slug[:40]} - ${pos.tokens:.2f}")
                        except Exception as e:
                            logger.log_warning(f"REDEEM ERROR: {pos.market_slug[:40]} - {e}")

                self.position_storage.resolve_position(pos.id, won)
                self.notify(
                    f"Market resolved ({result_str}): {pos.market_slug[:30]} P&L: ${pnl:+.2f}",
                    markup=False,
                )

        # Record scan time
        self._last_scan_time = datetime.now()

        # Update UI
        scanner_panel.update_signals(entry_signals, exit_signals, self._last_scan_time, rotation_proposals)
        scanner_panel.set_scanning(False)
        self.scanning = False
        self.refresh_bindings()

        # Update prices cache
        self._current_prices = {s.market_slug: s.current_price for s in entry_signals}
        self._current_fair_prices = {s.market_slug: s.fair_price for s in entry_signals}

        # Map prices for synced positions
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

        # Update live data panel
        if self.scanner:
            dsnap = self.scanner.deribit.get_snapshot()
            bsnap = self.scanner.binance.get_snapshot()
            btc_spot = dsnap.get("btc_spot", 0) or bsnap.get("btc_price", 0)
            eth_spot = dsnap.get("eth_spot", 0) or bsnap.get("eth_price", 0)
            self._update_live_data_panel(
                btc_spot, eth_spot,
                dsnap.get("btc_iv", 0), dsnap.get("eth_iv", 0),
            )
            deribit_age = self.scanner.deribit.age_seconds
            age = int(deribit_age) if deribit_age < 1e9 else 0
            self._update_futures_panel(
                dsnap.get("btc_curve", []),
                dsnap.get("eth_curve", []),
                age,
            )

        if self._shutting_down:
            return

        # Update POL balance (gas)
        if self.executor.initialized:
            self._pol_balance = self.executor.get_matic_balance()

        # Update status bar
        self.update_status_bar(positions, self._current_prices)

        if self._shutting_down:
            return

        # Manage sell limit orders
        fair_prices = self._current_fair_prices
        if fair_prices and positions:
            loop3 = asyncio.get_event_loop()
            await loop3.run_in_executor(
                None, self._manage_sell_orders, positions, fair_prices
            )
            positions = self._get_all_positions()
            self._refresh_positions_panel()
            self.update_status_bar(positions, self._current_prices)

        # Redeem resolved NegRisk positions
        if self.executor.initialized and positions:
            loop4 = asyncio.get_event_loop()
            await loop4.run_in_executor(None, self._redeem_resolved_positions, positions)
            positions = self._get_all_positions()

        # Auto-correct balance discrepancies
        if self.executor.initialized and positions:
            await self._check_balance_discrepancies()

        # Update portfolio risk panel (every N scans or after trades)
        self._portfolio_update_counter += 1
        if self._portfolio_update_counter >= self._portfolio_update_interval:
            self._portfolio_update_counter = 0
            self._refresh_portfolio_panel(positions, entry_signals)

        # Handle signals
        await self.process_signals(entry_signals, exit_signals, rotation_proposals)

        # Immediate rescan if buys were made
        if self._had_buys_this_cycle:
            get_logger().log_info("Buys detected — triggering immediate rescan")
            self.next_scan_seconds = 0
            self.call_later(self.do_scan)

    def update_status_bar(self, positions: List[Position],
                          current_prices: dict[str, float]) -> None:
        """Update status bar with current data."""
        fair_prices = self._current_fair_prices
        invested = sum(p.entry_size for p in positions)
        total_value = sum(p.current_value(fair_prices.get(p.market_slug, p.fair_price_at_entry))
                         for p in positions)
        unrealized_pnl = total_value - invested
        unrealized_pnl_pct = unrealized_pnl / invested if invested > 0 else 0

        balance = self.executor.get_balance()

        status_bar = StatusBar(self.config)
        status_bar.update_status(
            balance=balance,
            positions_count=len(positions),
            invested=invested,
            unrealized_pnl=unrealized_pnl,
            unrealized_pnl_pct=unrealized_pnl_pct,
            last_scan_time=self._last_scan_time,
            pol_balance=self._pol_balance,
            missed_liquidity=self._missed_liquidity,
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
                    pos = self.position_storage.load(pos_id)
                    if pos:
                        logger.log_info(f"SELL ORDER gone (filled?): {info['market_slug'][:40]} @ {info['price']:.1%}")
                    self.sell_order_store.remove(pos_id)
            remaining = len(self.sell_order_store.load_all())
            if remaining:
                logger.log_info(f"Loaded {remaining} active sell orders")
        except Exception as e:
            logger.log_warning(f"Error initializing sell orders: {e}")

    def _get_token_balance(self, token_id: str) -> Optional[float]:
        """Get on-chain token balance from API."""
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

    async def _check_balance_discrepancies(self) -> None:
        """Compare storage tokens vs on-chain balances.
        Auto-corrects by closing oldest positions (FIFO).
        """
        positions = self._get_all_positions()
        if not positions or not self.executor.initialized:
            return

        sell_orders = self.sell_order_store.load_all()
        token_positions: dict[str, list] = {}

        for pos in positions:
            so = sell_orders.get(pos.id)
            if so and so.get("token_id"):
                token_positions.setdefault(so["token_id"], []).append(pos)

        if not token_positions:
            return

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._do_balance_correction, token_positions, sell_orders)

    def _do_balance_correction(self, token_positions: dict, sell_orders: dict) -> None:
        """Sync worker for balance correction."""
        logger = get_logger()
        total_corrected = 0

        for token_id, group in token_positions.items():
            storage_total = sum(p.tokens for p in group)
            on_chain = self._get_token_balance(token_id)
            if on_chain is None:
                continue
            diff = on_chain - storage_total
            if abs(diff) > 1 and diff < 0:
                excess = -diff
                slug = group[0].market_slug[:45]
                logger.log_warning(
                    f"BALANCE MISMATCH: {slug} | "
                    f"storage={storage_total:.2f} on_chain={on_chain:.2f} diff={diff:+.2f}"
                )
                sorted_group = sorted(group, key=lambda p: p.entry_time or "")
                for pos in sorted_group:
                    if excess <= 0.01:
                        break
                    so = sell_orders.get(pos.id)
                    sell_price = so["price"] if so else pos.entry_price
                    if pos.tokens <= excess + 0.01:
                        pnl = pos.tokens * sell_price - pos.entry_size
                        logger.log_info(
                            f"  AUTO-CLOSE: {pos.id} ({pos.tokens:.2f} tokens) "
                            f"@ {sell_price:.1%} P&L: ${pnl:+.2f}"
                        )
                        self.position_storage.close_position(pos.id, sell_price)
                        self.sell_order_store.remove(pos.id)
                        excess -= pos.tokens
                        total_corrected += 1
                    else:
                        import copy
                        old_tokens = pos.tokens
                        removed = min(excess, old_tokens - 0.01)
                        proportion = removed / old_tokens
                        removed_entry_size = pos.entry_size * proportion

                        partial = copy.deepcopy(pos)
                        partial.id = f"{pos.id[:6]}p{int(datetime.utcnow().timestamp()) % 100000:05d}"
                        partial.tokens = removed
                        partial.entry_size = removed_entry_size
                        partial.close(sell_price)
                        self.position_storage.move_to_history(partial)

                        pos.tokens = old_tokens - removed
                        pos.entry_size = pos.entry_size - removed_entry_size
                        pos.entry_price = (
                            pos.entry_size / pos.tokens
                            if pos.tokens > 0 else pos.entry_price
                        )
                        self.position_storage.save(pos)

                        pnl = partial.exit_size - partial.entry_size
                        logger.log_info(
                            f"  PARTIAL-CLOSE: {pos.id} "
                            f"{old_tokens:.2f} -> {pos.tokens:.2f} tokens "
                            f"(removed {removed:.2f}, P&L: ${pnl:+.2f})"
                        )
                        total_corrected += 1
                        excess = 0

        if total_corrected:
            try:
                self.call_from_thread(
                    self.notify,
                    f"Auto-corrected {total_corrected} positions — check log",
                    severity="warning",
                )
                self.call_from_thread(self._refresh_positions_panel)
            except RuntimeError:
                pass

    def _redeem_resolved_positions(self, positions: List[Position]) -> None:
        """Check for resolved markets and redeem winning positions on-chain."""
        logger = get_logger()
        now = datetime.now(timezone.utc)

        for pos in positions:
            if self._shutting_down:
                return
            if not pos.resolution_date:
                continue
            try:
                res_date = datetime.fromisoformat(pos.resolution_date.replace("Z", "+00:00"))
                if isinstance(res_date, datetime) and res_date.tzinfo is None:
                    res_date = res_date.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                try:
                    from datetime import date
                    d = date.fromisoformat(pos.resolution_date)
                    res_date = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
                except (ValueError, TypeError):
                    continue
            if now < res_date:
                continue

            market = self.executor.get_market_info(pos.market_id)
            if not market or not market.get("closed"):
                continue

            tokens = market.get("tokens", [])
            our_outcome = pos.outcome.upper()
            we_won = False
            for t in tokens:
                if t.get("outcome", "").upper() == our_outcome and t.get("winner"):
                    we_won = True
                    break

            if not we_won:
                logger.log_info(
                    f"RESOLVED LOSS: {pos.market_slug[:40]} "
                    f"({pos.outcome}) — closing at $0"
                )
                self.position_storage.close_position(pos.id, 0.0)
                self.sell_order_store.remove(pos.id)
                continue

            token_id = None
            for t in tokens:
                if t.get("outcome", "").upper() == our_outcome:
                    token_id = t.get("token_id")
                    break
            if not token_id and self.scanner:
                token_id = self.scanner._token_ids.get(pos.market_slug)
            if not token_id:
                continue

            if not market.get("neg_risk"):
                logger.log_info(
                    f"RESOLVED WIN (non-NegRisk): {pos.market_slug[:40]} — manual redeem needed"
                )
                continue

            so = self.sell_order_store.get(pos.id)
            if so and so.get("order_id"):
                self.executor.cancel_order(so["order_id"])
                self.sell_order_store.remove(pos.id)
                time.sleep(2)

            condition_id = market.get("condition_id", pos.market_id)
            success = self.executor.redeem_neg_risk(condition_id, pos.outcome, token_id)
            if success:
                logger.log_info(f"REDEEMED: {pos.market_slug[:40]} ({pos.outcome}) — closing position")
                self.position_storage.close_position(pos.id, 1.0)
            else:
                logger.log_warning(f"REDEEM FAILED: {pos.market_slug[:40]} — will retry next cycle")

    def _calculate_sell_price(self, entry_price: float, fair_price: float,
                              bid_price: float = 0.0) -> float:
        """Calculate sell price = max(fair_price, bid * 0.9)."""
        bid_floor = bid_price * 0.9 if bid_price > 0 else 0.0
        sell = max(fair_price, bid_floor)
        sell = max(0.01, min(0.99, sell))
        return round(sell, 3)

    def _manage_sell_orders(self, positions: List[Position], fair_prices: dict[str, float]):
        """Place/update sell limit orders for all positions.

        Groups positions by token_id and places one sell order per token.
        """
        if self.config.dry_run or not self.executor.initialized or self._shutting_down:
            return

        logger = get_logger()
        try:
            open_orders = self.executor.get_open_orders()
            open_ids = set()
            open_orders_by_token: dict[str, list[str]] = {}
            for o in open_orders:
                oid = o.get("id") or o.get("order_id") or o.get("orderID")
                tid = o.get("asset_id") or o.get("token_id") or ""
                if oid:
                    open_ids.add(oid)
                    if tid:
                        open_orders_by_token.setdefault(tid, []).append(oid)
        except Exception:
            return

        # Phase 1: Check filled/cancelled orders
        for pos in positions:
            if self._shutting_down:
                return
            existing = self.sell_order_store.get(pos.id)
            if not existing:
                continue
            if existing["order_id"] in open_ids:
                continue

            is_filled = False
            status = ""
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
            elif status == "LIVE":
                pass
            else:
                logger.log_info(f"SELL ORDER cancelled/expired: {pos.market_slug[:40]} status={status}")
                self.sell_order_store.remove(pos.id)

        if self._shutting_down:
            return

        # Phase 2: Group positions by token_id, place consolidated sell orders
        token_groups: dict[str, list] = {}

        for pos in positions:
            fair = fair_prices.get(pos.market_slug)
            if not fair or fair <= 0 or fair >= 1:
                continue

            token_id = None
            if self.scanner:
                token_id = self.scanner._token_ids.get(pos.market_slug)
            if not token_id and pos.market_id and self.executor.client:
                try:
                    market_data = self.executor.client.get_clob_market(pos.market_id)
                    if market_data:
                        tokens = market_data.get("tokens", [])
                        for t in tokens:
                            if t.get("outcome", "").upper() == pos.outcome.upper():
                                token_id = t.get("token_id")
                                if self.scanner and token_id:
                                    self.scanner._token_ids[pos.market_slug] = token_id
                                break
                except Exception as e:
                    logger.log_warning(f"SELL token_id API lookup failed: {e}")
            if not token_id:
                continue

            token_groups.setdefault(token_id, []).append((pos, fair))

        # Calculate sell prices with bid floor
        final_groups: dict[str, list] = {}
        for token_id, group in token_groups.items():
            bid_price = 0.0
            for pos, fair in group:
                sell_price = self._calculate_sell_price(pos.entry_price, fair, bid_price)
                final_groups.setdefault(token_id, []).append((pos, sell_price))
                logger.log_info(
                    f"SELL GROUP: {pos.market_slug[:40]} token={token_id[:12]}... sell={sell_price:.1%}"
                )

        token_groups = final_groups

        if self._shutting_down:
            return

        # Place consolidated orders
        for token_id, group in token_groups.items():
            if self._shutting_down:
                return

            target_price = min(sp for _, sp in group)
            total_tokens = sum(pos.tokens for pos, _ in group)
            target_price = max(0.01, min(0.99, round(target_price, 3)))

            target_2d = round(target_price, 2)
            all_have_orders = True
            for pos, _ in group:
                existing = self.sell_order_store.get(pos.id)
                if not existing or existing["order_id"] not in open_ids:
                    all_have_orders = False
                    break
                if round(existing["price"], 2) != target_2d:
                    all_have_orders = False
                    break

            if all_have_orders:
                continue

            # Cancel existing orders for this token
            cancelled_ids = set()
            for oid in open_orders_by_token.get(token_id, []):
                if oid not in cancelled_ids:
                    self.executor.cancel_order(oid)
                    cancelled_ids.add(oid)
            for pos, _ in group:
                self.sell_order_store.remove(pos.id)

            if cancelled_ids:
                time.sleep(3)

            on_chain = self._get_token_balance(token_id)
            if on_chain is not None:
                sell_size = math.floor(on_chain * 100) / 100
            else:
                sell_size = math.floor(total_tokens * 100) / 100

            if sell_size < 5:
                slug_sample = group[0][0].market_slug[:40]
                logger.log_info(f"SELL SKIP (< 5 tokens): {slug_sample}")
                if on_chain is not None and on_chain < 0.01:
                    for pos, _ in group:
                        if pos.tokens > 0:
                            logger.log_info(f"AUTO-CLOSE (0 on-chain): {pos.market_slug[:40]}")
                            self.position_storage.close_position(pos.id, 0.0)
                continue

            order_id = self.executor.place_sell_limit(token_id, target_price, sell_size)
            if order_id:
                placed_price = round(target_price, 2)
                for pos, sp in group:
                    self.sell_order_store.save(
                        pos.id, order_id, placed_price, token_id, pos.tokens, pos.market_slug
                    )
                slug_sample = group[0][0].market_slug[:40]
                logger.log_info(
                    f"SELL LIMIT placed: {slug_sample} "
                    f"price={target_price:.1%} size={sell_size:.2f} "
                    f"({len(group)} positions)"
                )
            else:
                # Retry after cancelling all orders for this token
                try:
                    fresh_orders = self.executor.get_open_orders()
                    retry_cancelled = False
                    for o in fresh_orders:
                        tid = o.get("asset_id") or o.get("token_id") or ""
                        if tid == token_id:
                            oid = o.get("id") or o.get("order_id") or o.get("orderID")
                            if oid:
                                self.executor.cancel_order(oid)
                                retry_cancelled = True
                    if retry_cancelled:
                        time.sleep(3)
                        order_id = self.executor.place_sell_limit(token_id, target_price, sell_size)
                        if order_id:
                            placed_price = round(target_price, 2)
                            for pos, sp in group:
                                self.sell_order_store.save(
                                    pos.id, order_id, placed_price, token_id, pos.tokens, pos.market_slug
                                )
                            continue
                except Exception:
                    pass
                slug_sample = group[0][0].market_slug[:40]
                logger.log_info(
                    f"SELL SKIP (place failed): {slug_sample} "
                    f"price={target_price:.4f} size={sell_size:.2f}"
                )

    async def process_signals(self, entry_signals: List[Signal],
                              exit_signals: List[Signal],
                              rotation_proposals: List[dict] = None) -> None:
        """Process trading signals based on mode."""
        balance = self.executor.get_balance() if self.executor.initialized else 0
        buy_signals = [
            s for s in entry_signals
            if s.type == SignalType.BUY and s.suggested_size >= 1.0
        ] if balance >= 1.0 else []
        actionable = buy_signals + exit_signals

        # Convert rotation proposals to SELL+BUY signal pairs
        rotation_signals = []
        if rotation_proposals and not buy_signals:
            # Only do rotations when we can't afford direct buys
            for r in rotation_proposals[:1]:  # Best rotation only
                sell_pos = r["sell_position"]
                buy_sig = r["buy_signal"]
                market = self._markets_cache.get(sell_pos.market_slug)
                if not market:
                    continue
                sell_signal = Signal(
                    type=SignalType.SELL,
                    market_id=sell_pos.market_id,
                    market_slug=sell_pos.market_slug,
                    market_name=sell_pos.market_name,
                    outcome=sell_pos.outcome,
                    current_price=r["sell_bid"],
                    fair_price=0.0,
                    position_id=sell_pos.id,
                    reason=f"Rotation → {buy_sig.market_slug[:25]}",
                    suggested_size=r["sell_proceeds"],
                    token_id=sell_pos.token_id or "",
                )
                rotation_signals.append(sell_signal)
                # BUY signal already exists with correct suggested_size
                buy_sig_copy = Signal(
                    type=SignalType.BUY,
                    market_id=buy_sig.market_id,
                    market_slug=buy_sig.market_slug,
                    market_name=buy_sig.market_name,
                    outcome=buy_sig.outcome,
                    current_price=buy_sig.current_price,
                    fair_price=buy_sig.fair_price,
                    edge=buy_sig.edge,
                    roi=buy_sig.roi,
                    days_remaining=buy_sig.days_remaining,
                    token_id=buy_sig.token_id,
                    annual_return=buy_sig.annual_return,
                    liquidity=buy_sig.liquidity,
                    kelly=buy_sig.kelly,
                    suggested_size=r["sell_proceeds"],  # Use sell proceeds as buy size
                )
                rotation_signals.append(buy_sig_copy)

        all_signals = actionable + rotation_signals

        if not all_signals:
            return

        if self.config.auto_mode:
            for signal in all_signals:
                await self.execute_signal(signal)
                # After rotation trades, update portfolio panel
                if rotation_signals:
                    self._portfolio_update_counter = 0
                    self._refresh_portfolio_panel()
        else:
            needs_confirm = []
            for signal in all_signals:
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

        # Dry run mode
        if self.config.dry_run:
            if signal.type == SignalType.BUY:
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
                self._refresh_positions_panel()
            elif signal.type == SignalType.SELL and signal.position_id:
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
                self._refresh_positions_panel()
            return

        self.notify(f"Executing {signal.type.value} for {signal.market_slug}...", markup=False)

        market = self._markets_cache.get(signal.market_slug)
        if not market:
            logger.log_warning(f"Market not found in cache: {signal.market_slug}")
            self.notify(f"Error: Market {signal.market_slug} not found in cache", markup=False)
            return

        if signal.type == SignalType.BUY:
            result, position = self.executor.buy(signal, market)

            if result.success and position:
                existing = self.position_storage.find_matching_position(
                    position.market_slug, position.outcome
                )
                if existing:
                    self.position_storage.merge_into(
                        existing, position.tokens, position.entry_size,
                        position.entry_price, position.entry_order_id
                    )
                    logger.log_info(
                        f"MERGED into {existing.id}: "
                        f"+{position.tokens:.2f} tokens, "
                        f"total={existing.tokens:.2f}, "
                        f"avg={existing.entry_price:.4f}"
                    )
                else:
                    self.position_storage.save(position)
                    logger.log_position_opened(position)
                self.history_storage.record_buy(position, result.order_id)
                self._had_buys_this_cycle = True
                self.notify(f"BUY order placed: {result.order_id}", markup=False)

                logger.log_trade_executed(
                    "BUY", signal.market_slug, signal.outcome,
                    signal.current_price, position.tokens, position.entry_size
                )

                self._current_prices[signal.market_slug] = signal.current_price
                self._refresh_positions_panel()
                self._refresh_portfolio_panel()
                positions = self._get_all_positions()
                self.update_status_bar(positions, self._current_prices)
            else:
                self.notify(f"BUY failed: {result.error}", markup=False)
                logger.log_trade_failed("BUY", signal.market_slug, result.error or "Unknown error")

        elif signal.type == SignalType.SELL and signal.position_id:
            position = self.position_storage.load(signal.position_id)
            if not position:
                self.notify(f"Error: Position {signal.position_id} not found", markup=False)
                return

            result = self.executor.sell(signal, position, market)

            if result.success:
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
                self._refresh_positions_panel()
                self._refresh_portfolio_panel()
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
        """Disable R key while scanning."""
        return not self.scanning

    def action_refresh(self) -> None:
        """Manual refresh/scan."""
        self.scanning = True
        self.refresh_bindings()
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

    def action_refresh_portfolio(self) -> None:
        """Manually refresh portfolio risk panel."""
        self._refresh_portfolio_panel()
        self.notify("Portfolio risk updated")

    def action_toggle_mode(self) -> None:
        """Toggle between AUTO and CONFIRM mode."""
        self.config.auto_mode = not self.config.auto_mode
        mode = "AUTO" if self.config.auto_mode else "CONFIRM"
        self.notify(f"Mode changed to {mode}")

        display_mode = "DRY RUN" if self.config.dry_run else mode
        self.sub_title = f"Mode: {display_mode} • Scan: {format_interval(self.config.scan_interval)}"

        positions = self._get_all_positions()
        self.update_status_bar(positions, self._current_prices)

    def action_confirm_yes(self) -> None:
        """Confirm pending action."""
        if self.scanning:
            self.notify("Scan in progress, please wait...")
            return
        if self.pending_signal:
            logger = get_logger()
            logger.log_user_confirmed(self.pending_signal.type.value, self.pending_signal.market_slug)
            signal = self.pending_signal
            self._show_next_pending_signal()
            asyncio.create_task(self._execute_and_refresh(signal))

    async def _execute_and_refresh(self, signal) -> None:
        """Execute signal and block new scans until done."""
        self.scanning = True
        self.refresh_bindings()
        try:
            await self.execute_signal(signal)
        finally:
            self.scanning = False
            self.refresh_bindings()

    def action_confirm_no(self) -> None:
        """Reject pending action."""
        if self.scanning:
            self.notify("Scan in progress, please wait...")
            return
        if self.pending_signal:
            logger = get_logger()
            logger.log_user_rejected(self.pending_signal.type.value, self.pending_signal.market_slug)
            self.notify(f"Skipped {self.pending_signal.market_slug}")
            self._show_next_pending_signal()
