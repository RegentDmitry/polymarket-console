"""
Weather Trading Bot TUI — Textual-based terminal interface.

Panels:
- StatusBar: balance, scan timer, forecast freshness
- ScannerPanel: opportunities table (City, Date, Bucket, PM, Fair, Edge, K%, Liq, Size)
- PositionsPanel: open positions with P&L
- RiskPanel: portfolio breakdown + risk metrics
- ForecastPanel: per-city forecasts with model breakdown (3-column, scrollable)
- LogPanel: trade journal
"""

import asyncio
import re
import threading
import time
from typing import Dict, List, Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Static, Footer

from ..config import WeatherBotConfig
from ..models.signal import Signal, SignalType
from ..models.position import Position
from ..storage.positions import PositionStorage
from ..storage.history import HistoryStorage
from ..pricing.portfolio import allocate_sizes, get_portfolio_breakdown
from ..pricing.portfolio_mc import simulate_weather_portfolio, positions_to_specs, PortfolioOutcome
from ..logger import get_trade_journal

_MARKUP_RE = re.compile(r"\[/?[^\]]*\]")


class StatusBar(Static):
    """Top status bar with balance and scan info."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._balance: float = 0.0
        self._mode: str = "DRY RUN"
        self._scan_interval: int = 300
        self._last_scan: float = 0
        self._positions: int = 0
        self._invested: float = 0.0
        self._pnl: float = 0.0
        self._forecast_age: Optional[float] = None
        self._forecast_smart: bool = True

    def update_status(self, balance: float, mode: str, scan_interval: int,
                      positions: int = 0, invested: float = 0.0, pnl: float = 0.0,
                      forecast_age: Optional[float] = None,
                      forecast_smart: bool = True) -> None:
        self._balance = balance
        self._mode = mode
        self._scan_interval = scan_interval
        self._positions = positions
        self._invested = invested
        self._pnl = pnl
        self._forecast_age = forecast_age
        self._forecast_smart = forecast_smart
        self.refresh()

    def mark_scan(self):
        self._last_scan = time.monotonic()
        self.refresh()

    def tick(self):
        self.refresh()

    def render(self) -> str:
        since = int(time.monotonic() - self._last_scan) if self._last_scan else 0
        next_in = max(0, self._scan_interval - since)

        pnl_str = f"${self._pnl:+.2f}"

        fc_str = "N/A"
        if self._forecast_age is not None:
            if self._forecast_age < 60:
                fc_str = f"{self._forecast_age:.0f}m"
            else:
                fc_str = f"{self._forecast_age / 60:.1f}h"
            fc_mode = "smart" if self._forecast_smart else "TTL"
            fc_str = f"{fc_str} ({fc_mode})"

        return (
            f"  [{self._mode}]  "
            f"Balance: ${self._balance:,.2f}  "
            f"Invested: ${self._invested:,.2f}  "
            f"P&L: {pnl_str}  "
            f"Positions: {self._positions}  "
            f"Next scan: {next_in}s  "
            f"Forecast: {fc_str}"
        )


class ScannerPanel(Static):
    """Shows BUY/SELL signals from last scan."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._signals: List[Signal] = []
        self._scan_time: float = 0

    def update_signals(self, signals: List[Signal], scan_time: float = 0) -> None:
        self._signals = signals
        self._scan_time = scan_time
        self.refresh()

    def render(self):
        from rich.panel import Panel
        from rich.table import Table

        if not self._signals:
            return Panel("[dim]No opportunities found[/dim]",
                        title="SCANNER", border_style="cyan")

        table = Table(show_header=True, header_style="bold cyan",
                     box=None, padding=(0, 1))
        table.add_column("City", width=12)
        table.add_column("Date", width=6)
        table.add_column("Bucket", width=10)
        table.add_column("PM", width=5, justify="right")
        table.add_column("Fair", width=5, justify="right")
        table.add_column("Edge", width=6, justify="right")
        table.add_column("K%", width=4, justify="right")
        table.add_column("Liq", width=5, justify="right")
        table.add_column("Size", width=5, justify="right")

        for s in self._signals[:20]:
            edge_color = "green" if s.edge >= 0.10 else "yellow"
            city = s.city.replace("-", " ").title()[:12]
            date_short = s.date[5:] if s.date else ""

            size_str = f"${s.suggested_size:.0f}" if s.suggested_size > 0 else "-"
            kelly_str = f"{s.kelly:.0%}" if s.kelly > 0 else "-"
            kelly_color = "green" if s.kelly >= 0.10 else ("yellow" if s.kelly > 0 else "dim")

            table.add_row(
                city,
                date_short,
                s.bucket_label,
                f"{s.current_price:.0%}",
                f"{s.fair_price:.0%}",
                f"[{edge_color}]+{s.edge:.0%}[/{edge_color}]",
                f"[{kelly_color}]{kelly_str}[/{kelly_color}]",
                f"${s.liquidity:.0f}",
                size_str,
            )

        title = f"SCANNER ({len(self._signals)} signals, {self._scan_time:.0f}s)"
        return Panel(table, title=title, border_style="cyan")


class PositionsPanel(Static):
    """Shows open positions with P&L."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._positions: List[Position] = []
        self._prices: Dict[str, float] = {}

    def update_positions(self, positions: List[Position],
                        prices: Dict[str, float]) -> None:
        self._positions = positions
        self._prices = prices
        self.refresh()

    def render(self):
        from rich.panel import Panel
        from rich.table import Table

        if not self._positions:
            return Panel("[dim]No open positions[/dim]",
                        title="POSITIONS (0)", border_style="green")

        table = Table(show_header=True, header_style="bold green",
                     box=None, padding=(0, 1))
        table.add_column("City", width=12)
        table.add_column("Date", width=6)
        table.add_column("Bucket", width=10)
        table.add_column("Entry", width=5, justify="right")
        table.add_column("Now", width=5, justify="right")
        table.add_column("Cost", width=6, justify="right")
        table.add_column("P&L", width=7, justify="right")

        total_pnl = 0.0
        for p in self._positions:
            current = self._prices.get(p.market_slug, p.entry_price)
            pnl = p.unrealized_pnl(current)
            total_pnl += pnl
            pnl_color = "green" if pnl >= 0 else "red"

            city = p.city.replace("-", " ").title()[:12] if p.city else "?"
            date_short = p.date[5:] if p.date else ""

            table.add_row(
                city,
                date_short,
                p.bucket_label,
                f"{p.entry_price:.0%}",
                f"{current:.0%}",
                f"${p.entry_size:.1f}",
                f"[{pnl_color}]{pnl:+.2f}[/{pnl_color}]",
            )

        pnl_color = "green" if total_pnl >= 0 else "red"
        title = f"POSITIONS ({len(self._positions)}) [{pnl_color}]P&L: ${total_pnl:+.2f}[/{pnl_color}]"
        return Panel(table, title=title, border_style="green")


class RiskPanel(Static):
    """Portfolio risk with MC outcome distribution."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._breakdown: dict = {}
        self._target_alloc: float = 1.0
        self._mc_outcome = None
        self._mc_computing: bool = False
        self._updated_at: float = 0

    def update_risk(self, breakdown: dict,
                    target_alloc: float = 1.0) -> None:
        self._breakdown = breakdown
        self._target_alloc = target_alloc
        self.refresh()

    def update_mc_outcome(self, outcome) -> None:
        self._mc_outcome = outcome
        self._mc_computing = False
        self._updated_at = time.monotonic()
        self.refresh()

    def set_mc_computing(self, computing: bool) -> None:
        self._mc_computing = computing
        self.refresh()

    def render(self):
        from rich.panel import Panel

        lines = []
        bd = self._breakdown

        if not bd or bd.get("total_portfolio", 0) <= 0:
            return Panel("[dim]No data[/dim]", title="PORTFOLIO RISK",
                        border_style="magenta")

        # MC Outcome Distribution
        if self._mc_computing:
            lines.append("[dim]Computing MC...[/dim]")
        elif self._mc_outcome and self._mc_outcome.n_paths > 0:
            mc = self._mc_outcome
            w = 8  # value column width
            for pct, label in [(5, "Worst 5%"), (25, "25th"),
                               (50, "Median"), (75, "75th"),
                               (95, "Best 95%")]:
                val = mc.percentiles.get(pct, 0)
                color = "green" if val >= 0 else "red"
                v = f"${val:+,.0f}"
                lines.append(f" {label:>8s} [{color}]{v:>{w}}[/{color}]")
            color_wp = "green" if mc.win_prob >= 0.5 else "red"
            v = f"{mc.win_prob:.0%}"
            lines.append(f" {'Win':>8s} [{color_wp}]{v:>{w}}[/{color_wp}]")
            color_ev = "green" if mc.mean_pnl >= 0 else "red"
            v = f"${mc.mean_pnl:+,.0f}"
            lines.append(f" {'E[P&L]':>8s} [{color_ev}]{v:>{w}}[/{color_ev}]")

            age_str = ""
            if self._updated_at > 0:
                age = int(time.monotonic() - self._updated_at)
                if age < 60:
                    age_str = f"  {age}s ago"
                else:
                    age_str = f"  {age // 60}m ago"
            lines.append(
                f"[dim]({mc.n_paths // 1000}k paths, "
                f"{mc.compute_time_ms:.0f}ms){age_str}[/dim]"
            )

        lines.append("")
        pos_count = bd.get("position_count", 0)
        invested = bd.get("total_invested", 0)
        bal = bd.get("balance", 0)
        total = bd["total_portfolio"]
        alloc_pct = invested / total if total > 0 else 0
        lines.append(
            f"Pos: {pos_count}  Alloc: {alloc_pct:.0%}/{self._target_alloc:.0%}"
        )
        lines.append(f"Inv: ${invested:,.0f}  Free: ${bal:,.0f}")

        return Panel("\n".join(lines), title="PORTFOLIO RISK \\[P]",
                    border_style="magenta")


class ForecastPanel(Static):
    """Shows forecast data per city/date in 3-column layout."""

    MODEL_SHORT = {
        "icon_seamless": "IC",
        "gfs_seamless": "GF",
        "ecmwf_ifs025": "EC",
        "jma_seamless": "JM",
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._forecasts: Dict[str, dict] = {}
        self._held_cities: set = set()

    def update_forecasts(self, forecasts: Dict[str, dict],
                        held_cities: set = None) -> None:
        self._forecasts = forecasts
        self._held_cities = held_cities or set()
        self.refresh()

    def _render_city(self, city: str, data: dict) -> List[str]:
        """Render one city block as lines."""
        unit = data.get("unit", "F")
        dates = data.get("dates", {})
        if not dates:
            return []

        name = city.replace("-", " ").title()[:11]
        marker = "[green]*[/green]" if city in self._held_cities else " "
        lines = [f"{marker}[bold]{name}[/bold] °{unit}"]

        for date_str in sorted(dates.keys()):
            day = dates[date_str]
            fc = day.get("forecast", 0)
            sigma = day.get("sigma", 0)
            models = day.get("models", {})
            d = date_str[5:] if len(date_str) >= 10 else date_str

            vals = [models.get(mk) for mk in self.MODEL_SHORT]
            model_str = "/".join(f"{v:.0f}" for v in vals if v is not None)

            lines.append(
                f" {d} [cyan]{fc:4.0f}[/cyan]±{sigma:.0f} [dim]{model_str}[/dim]"
            )
        return lines

    def render(self):
        from rich.panel import Panel

        if not self._forecasts:
            return Panel("[dim]Waiting for forecast data...[/dim]",
                        title="FORECASTS", border_style="blue")

        # Sort: held cities first, then alphabetical
        cities = sorted(self._forecasts.keys(),
                       key=lambda c: (c not in self._held_cities, c))

        # Render each city block
        blocks = []
        for city in cities:
            block = self._render_city(city, self._forecasts[city])
            if block:
                blocks.append(block)

        if not blocks:
            return Panel("[dim]No data[/dim]", title="FORECASTS", border_style="blue")

        # Split into 3 columns
        n = len(blocks)
        col_size = (n + 2) // 3
        columns = [
            blocks[:col_size],
            blocks[col_size:col_size * 2],
            blocks[col_size * 2:],
        ]

        # Flatten each column's blocks with blank line separator
        col_lines = []
        for col_blocks in columns:
            lines = []
            for b in col_blocks:
                lines.extend(b)
                lines.append("")
            if lines and lines[-1] == "":
                lines.pop()
            col_lines.append(lines)

        # Merge columns side by side
        col_width = 26
        max_rows = max(len(cl) for cl in col_lines) if col_lines else 0
        merged = []
        for i in range(max_rows):
            parts = []
            for ci, cl in enumerate(col_lines):
                line = cl[i] if i < len(cl) else ""
                plain_len = len(_MARKUP_RE.sub("", line))
                pad = max(1, col_width - plain_len) if ci < len(col_lines) - 1 else 0
                parts.append(f"{line}{' ' * pad}")
            merged.append("".join(parts))

        return Panel("\n".join(merged), title="FORECASTS", border_style="blue")


class TradeLogPanel(Static):
    """Shows recent trade log entries."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._lines: List[str] = []

    def add_line(self, line: str) -> None:
        ts = time.strftime("%H:%M:%S")
        self._lines.insert(0, f"[dim]{ts}[/dim] {line}")
        if len(self._lines) > 50:
            self._lines = self._lines[:50]
        self.refresh()

    def render(self):
        from rich.panel import Panel

        if not self._lines:
            content = "[dim]Waiting for first scan...[/dim]"
        else:
            content = "\n".join(self._lines[:15])
        return Panel(content, title="LOG", border_style="yellow")


class TradingBotApp(App):
    """Main weather trading bot TUI application."""

    from .. import __version__
    TITLE = f"Weather Trading Bot v{__version__}"

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
    }
    #main-container {
        layout: horizontal;
        height: 1fr;
    }
    #left-panel {
        width: 60%;
        height: 100%;
        overflow-y: auto;
    }
    #right-panel {
        width: 40%;
        height: 100%;
    }
    #positions-scroll {
        height: auto;
        max-height: 50%;
        min-height: 6;
    }
    #positions-panel {
        height: auto;
    }
    #portfolio-panel {
        height: auto;
    }
    TradeLogPanel {
        height: auto;
    }
    #forecast-scroll {
        height: 1fr;
    }
    #forecast-panel {
        height: auto;
    }
    ScannerPanel {
        height: auto;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit", key_display="Q"),
        Binding("r", "refresh", "Scan", key_display="R"),
        Binding("f", "refresh_forecast", "Forecast", key_display="F"),
        Binding("y", "confirm_yes", "Yes", show=False),
        Binding("n", "confirm_no", "No", show=False),
        Binding("p", "refresh_portfolio", "Risk [P]", key_display="P"),
        Binding("й", "quit", "Quit", show=False),
        Binding("к", "refresh", "Scan", show=False),
        Binding("а", "refresh_forecast", "Forecast", show=False),
        Binding("з", "refresh_portfolio", "Risk", show=False),
    ]

    def __init__(self, config: WeatherBotConfig, position_storage: PositionStorage,
                 history_storage: HistoryStorage, scanner=None, executor=None):
        super().__init__()
        self.config = config
        self.position_storage = position_storage
        self.history_storage = history_storage
        self.scanner = scanner
        self.executor = executor

        self._cached_balance: float = 0.0
        self._cached_signals: List[Signal] = []
        self._scan_running: bool = False
        self._pending_signals: List[Signal] = []
        self._pending_idx: int = 0
        self._mc_thread_running: bool = False
        self._portfolio_update_counter: int = 2  # triggers MC on first scan
        self._portfolio_update_interval: int = 3  # MC every 3 scans

    def compose(self) -> ComposeResult:
        mode = "DRY RUN" if self.config.dry_run else (
            "AUTO" if self.config.auto_mode else "CONFIRM"
        )
        yield Static(f" Weather Bot [{mode}]", id="app-header")
        yield StatusBar(id="status-bar")
        with Horizontal(id="main-container"):
            with Vertical(id="left-panel"):
                yield ScannerPanel(id="scanner-panel")
                yield TradeLogPanel(id="log-panel")
                with VerticalScroll(id="forecast-scroll"):
                    yield ForecastPanel(id="forecast-panel")
            with Vertical(id="right-panel"):
                with VerticalScroll(id="positions-scroll"):
                    yield PositionsPanel(id="positions-panel")
                yield RiskPanel(id="portfolio-panel")
        yield Footer()

    async def on_mount(self) -> None:
        # Initial balance
        if self.executor and self.executor.initialized:
            try:
                self._cached_balance = self.executor.get_balance()
            except Exception:
                self._cached_balance = 0.0
        else:
            self._cached_balance = 0.0

        self._update_ui()

        # Start scan timer
        self.set_interval(1, self._tick)
        self.set_interval(self.config.scan_interval, self._auto_scan)

        # Run first scan
        self.run_worker(self._run_scan())

    def _tick(self) -> None:
        self.query_one("#status-bar", StatusBar).tick()

    async def _auto_scan(self) -> None:
        if not self._scan_running:
            await self._run_scan()

    async def _run_scan(self) -> None:
        if self._scan_running or not self.scanner:
            return

        self._scan_running = True
        log = self.query_one("#log-panel", TradeLogPanel)
        log.add_line("Scanning...")

        try:
            t0 = time.time()

            # Get held slugs
            positions = self.position_storage.load_all_active()
            held_slugs = {p.market_slug for p in positions}

            # Run scanner in thread to avoid blocking UI
            scanner = self.scanner
            entry_signals = await asyncio.to_thread(
                scanner.scan_for_entries,
                progress_callback=lambda msg: self.call_from_thread(log.add_line, msg),
                held_slugs=held_slugs,
            )

            # Get current prices and exit signals
            current_prices = await asyncio.to_thread(scanner.get_current_prices)
            exit_signals = scanner.scan_for_exits(positions, current_prices)

            scan_time = time.time() - t0

            # Allocate sizes (Kelly proportional)
            allocate_sizes(entry_signals, self._cached_balance, positions, self.config)

            self._cached_signals = entry_signals

            # Update UI
            scanner_panel = self.query_one("#scanner-panel", ScannerPanel)
            scanner_panel.update_signals(entry_signals, scan_time)

            # Reload positions and update all panels at once
            self._update_ui()

            # Periodic MC update
            self._portfolio_update_counter += 1
            if self._portfolio_update_counter >= self._portfolio_update_interval:
                self._portfolio_update_counter = 0
                self._launch_mc()

            status_bar = self.query_one("#status-bar", StatusBar)
            status_bar.mark_scan()

            buys = sum(1 for s in entry_signals if s.suggested_size > 0)
            sells = len(exit_signals)
            log.add_line(f"Scan done: {buys} buys, {sells} sells ({scan_time:.0f}s)")

            # Handle signals
            if self.config.auto_mode and not self.config.dry_run:
                await self._auto_execute(entry_signals, exit_signals, positions)
            elif not self.config.dry_run and (buys > 0 or sells > 0):
                # Queue for confirmation
                actionable = [s for s in entry_signals if s.suggested_size > 0]
                actionable.extend(exit_signals)
                if actionable:
                    self._pending_signals = actionable
                    self._pending_idx = 0
                    self._show_confirmation()

            # Refresh balance from API (authoritative after scan)
            if self.executor and self.executor.initialized:
                try:
                    self._cached_balance = self.executor.get_balance()
                except Exception:
                    pass

        except Exception as e:
            log.add_line(f"[red]Scan error: {e}[/red]")
        finally:
            self._scan_running = False

    async def _auto_execute(self, entries: List[Signal], exits: List[Signal],
                           positions: List[Position]) -> None:
        """Execute all signals automatically."""
        for signal in exits:
            pos = next((p for p in positions if p.id == signal.position_id), None)
            if pos:
                await self._execute_sell(signal, pos)

        for signal in entries:
            if signal.suggested_size > 0:
                await self._execute_buy(signal)

    async def _execute_buy(self, signal: Signal) -> None:
        """Execute a single buy."""
        log = self.query_one("#log-panel", TradeLogPanel)
        journal = get_trade_journal()

        if self.config.dry_run:
            log.add_line(
                f"[yellow][DRY] BUY {signal.city} {signal.bucket_label} "
                f"${signal.suggested_size:.0f} @ {signal.current_price:.0%} "
                f"(edge {signal.edge:.0%})[/yellow]"
            )
            return

        if not self.executor:
            return

        market = self._signal_to_market(signal)
        result, position = self.executor.buy(signal, market)

        if result.success and position:
            existing = self.position_storage.find_matching_position(
                signal.market_slug, signal.outcome
            )
            if existing:
                self.position_storage.merge_into(
                    existing, position.tokens, position.entry_size,
                    position.entry_price, result.order_id
                )
                log.add_line(
                    f"[green]MERGED into {signal.city} {signal.bucket_label} "
                    f"+${position.entry_size:.2f}[/green]"
                )
            else:
                self.position_storage.save(position)
                self.history_storage.record_buy(position, result.order_id)
                journal.log_buy(position, signal, result.order_id or "")
                log.add_line(
                    f"[green]BUY {signal.city} {signal.bucket_label} "
                    f"${position.entry_size:.2f} @ {position.entry_price:.0%}[/green]"
                )

            self._cached_balance -= position.entry_size
            self._update_ui()
            self._launch_mc()
        else:
            log.add_line(f"[red]BUY FAILED: {result.error}[/red]")

    async def _execute_sell(self, signal: Signal, position: Position) -> None:
        """Execute a sell (edge_exit)."""
        log = self.query_one("#log-panel", TradeLogPanel)
        journal = get_trade_journal()

        if self.config.dry_run:
            log.add_line(
                f"[yellow][DRY] SELL {signal.city} {signal.bucket_label} "
                f"({signal.reason})[/yellow]"
            )
            return

        if not self.executor:
            return

        market = self._signal_to_market(signal)
        result = self.executor.sell(signal, position, market)

        if result.success:
            pnl = (result.filled_size or 0) - position.entry_size
            self.position_storage.close_position(
                position.id, signal.current_price, result.order_id
            )
            journal.log_edge_exit(position, signal, pnl)
            self._cached_balance += result.filled_size or 0
            log.add_line(
                f"[red]SELL {signal.city} {signal.bucket_label} "
                f"P&L: ${pnl:+.2f} ({signal.reason})[/red]"
            )
            self._update_ui()
            self._launch_mc()
        else:
            log.add_line(f"[red]SELL FAILED: {result.error}[/red]")

    def _show_confirmation(self) -> None:
        """Show next pending signal for confirmation."""
        log = self.query_one("#log-panel", TradeLogPanel)
        if self._pending_idx >= len(self._pending_signals):
            self._pending_signals = []
            return

        s = self._pending_signals[self._pending_idx]
        action = "BUY" if s.type == SignalType.BUY else "SELL"
        size_str = f"${s.suggested_size:.0f}" if s.suggested_size > 0 else ""
        log.add_line(
            f"[bold]{action} {s.city} {s.bucket_label} "
            f"{size_str} @ {s.current_price:.0%} "
            f"(edge {s.edge:.0%})? [Y/N][/bold]"
        )

    def action_confirm_yes(self) -> None:
        if not self._pending_signals or self._pending_idx >= len(self._pending_signals):
            return
        signal = self._pending_signals[self._pending_idx]
        self._pending_idx += 1

        if signal.type == SignalType.BUY:
            self.run_worker(self._execute_buy(signal))
        elif signal.type == SignalType.SELL:
            positions = self.position_storage.load_all_active()
            pos = next((p for p in positions if p.id == signal.position_id), None)
            if pos:
                self.run_worker(self._execute_sell(signal, pos))

        self._show_confirmation()

    def action_confirm_no(self) -> None:
        log = self.query_one("#log-panel", TradeLogPanel)
        if self._pending_signals and self._pending_idx < len(self._pending_signals):
            s = self._pending_signals[self._pending_idx]
            log.add_line(f"Skipped {s.city} {s.bucket_label}")
            self._pending_idx += 1
            self._show_confirmation()

    def action_refresh(self) -> None:
        if not self._scan_running:
            self.run_worker(self._run_scan())

    def action_refresh_portfolio(self) -> None:
        """Manually refresh portfolio MC simulation."""
        log = self.query_one("#log-panel", TradeLogPanel)
        log.add_line("Refreshing portfolio risk...")
        self._launch_mc()

    def action_refresh_forecast(self) -> None:
        """Force refresh all forecasts."""
        log = self.query_one("#log-panel", TradeLogPanel)
        if self.scanner:
            status = self.scanner.forecast.tracker.get_status()
            for model, info in status.items():
                log.add_line(f"  {model}: {info}")
            log.add_line("Refreshing forecasts...")
            count = self.scanner.forecast.refresh_all()
            log.add_line(f"Refreshed {count} cities")
            self._update_forecasts()

    def _update_ui(self) -> None:
        """Single method to update all panels. Loads positions once."""
        positions = self.position_storage.load_all_active()
        prices = self.scanner.get_current_prices() if self.scanner else {}

        # Positions panel
        self.query_one("#positions-panel", PositionsPanel).update_positions(
            positions, prices
        )

        # Status bar
        invested = sum(p.entry_size for p in positions)
        pnl = sum(p.unrealized_pnl(prices.get(p.market_slug, p.entry_price))
                  for p in positions)

        fc_age = None
        fc_smart = True
        if self.scanner:
            ages = self.scanner.get_forecast_cache_info()
            valid_ages = [a for a in ages.values() if a is not None]
            fc_age = max(valid_ages) if valid_ages else None
            fc_smart = self.scanner.forecast.tracker.is_s3_available

        mode = "DRY RUN" if self.config.dry_run else (
            "AUTO" if self.config.auto_mode else "CONFIRM"
        )
        self.query_one("#status-bar", StatusBar).update_status(
            balance=self._cached_balance,
            mode=mode,
            scan_interval=self.config.scan_interval,
            positions=len(positions),
            invested=invested,
            pnl=pnl,
            forecast_age=fc_age,
            forecast_smart=fc_smart,
        )

        # Risk panel (breakdown only — MC updates separately)
        breakdown = get_portfolio_breakdown(positions, self._cached_balance)
        self.query_one("#portfolio-panel", RiskPanel).update_risk(
            breakdown, self.config.target_alloc
        )

        # Forecasts
        self._update_forecasts(positions)

    def _launch_mc(self) -> None:
        """Launch MC simulation in background thread."""
        if self._mc_thread_running or not self.scanner:
            return
        positions = self.position_storage.load_all_active()
        if not positions:
            return
        self._mc_thread_running = True
        try:
            self.query_one("#portfolio-panel", RiskPanel).set_mc_computing(True)
        except Exception:
            pass
        t = threading.Thread(
            target=self._run_portfolio_mc,
            args=(list(positions), self._cached_balance),
            name="weather-mc",
            daemon=True,
        )
        t.start()

    def _run_portfolio_mc(self, positions: list, balance: float) -> None:
        """Run MC simulation in background thread."""
        try:
            specs = positions_to_specs(positions, self.scanner)
            if not specs:
                return
            outcome = simulate_weather_portfolio(specs, balance, n_paths=100_000)
            try:
                self.call_from_thread(
                    self.query_one("#portfolio-panel", RiskPanel).update_mc_outcome,
                    outcome,
                )
            except Exception:
                pass
        except Exception as e:
            try:
                log = self.query_one("#log-panel", TradeLogPanel)
                self.call_from_thread(log.add_line, f"[red]MC error: {e}[/red]")
                self.call_from_thread(
                    self.query_one("#portfolio-panel", RiskPanel).set_mc_computing,
                    False,
                )
            except Exception:
                pass
        finally:
            self._mc_thread_running = False

    def _update_forecasts(self, positions: list = None) -> None:
        """Update forecast panel with cached data."""
        if not self.scanner:
            return
        forecasts = self.scanner.get_cached_forecasts()
        if positions is None:
            positions = self.position_storage.load_all_active()
        held_cities = {p.city for p in positions}
        self.query_one("#forecast-panel", ForecastPanel).update_forecasts(
            forecasts, held_cities
        )

    def _signal_to_market(self, signal: Signal):
        """Create a Market object from a Signal for executor."""
        from ..models.market import Market
        return Market(
            id=signal.market_id,
            slug=signal.market_slug,
            name=signal.market_name,
            yes_token_id=signal.token_id,
            category="weather",
        )
