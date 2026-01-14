"""
Detailed event logger for the trading bot.

Logs all events with context to help reconstruct decision logic.
"""

import os
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Any

from .models.signal import Signal, SignalType
from .models.position import Position


class BotLogger:
    """Logger that writes detailed events to a text file."""

    def __init__(self, log_dir: Optional[str] = None):
        if log_dir is None:
            log_dir = Path(__file__).parent / "data" / "logs"
        else:
            log_dir = Path(log_dir)

        log_dir.mkdir(parents=True, exist_ok=True)

        # Log file named by date
        date_str = datetime.now().strftime("%Y-%m-%d")
        self.log_file = log_dir / f"bot_{date_str}.log"

    def _write(self, message: str):
        """Write a timestamped message to the log file."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{timestamp}] {message}\n"
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(line)

    def _separator(self, char: str = "-", length: int = 60):
        """Write a separator line."""
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(char * length + "\n")

    # ─────────────────────────────────────────────────────────────
    # Bot lifecycle
    # ─────────────────────────────────────────────────────────────

    def log_startup(self, mode: str, interval: int, min_edge: float, min_apy: float):
        """Log bot startup with configuration."""
        self._separator("=")
        self._write("BOT STARTED")
        self._write(f"  Mode: {mode}")
        self._write(f"  Scan interval: {interval}s")
        self._write(f"  Min edge: {min_edge:.1%}")
        self._write(f"  Min APY: {min_apy:.0%}")
        self._separator("=")

    def log_shutdown(self):
        """Log bot shutdown."""
        self._separator("=")
        self._write("BOT STOPPED")
        self._separator("=")

    # ─────────────────────────────────────────────────────────────
    # Scanning
    # ─────────────────────────────────────────────────────────────

    def log_scan_start(self):
        """Log start of a scan cycle."""
        self._separator()
        self._write("SCAN STARTED")

    def log_scan_complete(self, buy_signals: int, sell_signals: int, skip_signals: int,
                          duration_sec: float):
        """Log scan completion with summary."""
        self._write(f"SCAN COMPLETE in {duration_sec:.1f}s")
        self._write(f"  Results: {buy_signals} BUY, {sell_signals} SELL, {skip_signals} SKIP")

    def log_signal(self, signal: Signal):
        """Log a detected signal with full context."""
        self._write(f"SIGNAL: {signal.type.value}")
        self._write(f"  Market: {signal.market_name}")
        self._write(f"  Slug: {signal.market_slug}")
        self._write(f"  Outcome: {signal.outcome}")
        self._write(f"  Current price: {signal.current_price:.2%}")
        self._write(f"  Fair price: {signal.fair_price:.2%}")

        if signal.edge:
            self._write(f"  Edge: {signal.edge:.2%}")
        if signal.annual_return:
            self._write(f"  APY: {signal.annual_return:.0%}")
        if signal.days_remaining:
            self._write(f"  Days remaining: {signal.days_remaining:.1f}")
        if signal.model_used:
            self._write(f"  Model: {signal.model_used}")
        if signal.liquidity:
            self._write(f"  Liquidity: ${signal.liquidity:.2f}")
        if signal.suggested_size:
            self._write(f"  Suggested size: ${signal.suggested_size:.2f}")
        if signal.reason:
            self._write(f"  Reason: {signal.reason}")

    # ─────────────────────────────────────────────────────────────
    # Trading decisions
    # ─────────────────────────────────────────────────────────────

    def log_buy_decision(self, signal: Signal, balance: float, decision: str,
                         reason: str):
        """Log a buy decision with reasoning."""
        self._write(f"BUY DECISION: {decision}")
        self._write(f"  Market: {signal.market_slug}")
        self._write(f"  Balance: ${balance:.2f}")
        self._write(f"  Amount: ${signal.suggested_size:.2f}")
        self._write(f"  Reason: {reason}")

    def log_sell_decision(self, signal: Signal, position: Position, decision: str,
                          reason: str):
        """Log a sell decision with reasoning."""
        pnl = position.unrealized_pnl(signal.current_price)
        self._write(f"SELL DECISION: {decision}")
        self._write(f"  Market: {signal.market_slug}")
        self._write(f"  Position tokens: {position.tokens:.4f}")
        self._write(f"  Entry price: {position.entry_price:.2%}")
        self._write(f"  Current bid: {signal.current_price:.2%}")
        self._write(f"  Fair price: {signal.fair_price:.2%}")
        self._write(f"  P&L: ${pnl:+.2f}")
        self._write(f"  Reason: {reason}")

    # ─────────────────────────────────────────────────────────────
    # Trade execution
    # ─────────────────────────────────────────────────────────────

    def log_trade_executed(self, action: str, market_slug: str, outcome: str,
                           price: float, size: float, amount_usd: float,
                           dry_run: bool = False):
        """Log a successfully executed trade."""
        prefix = "[DRY RUN] " if dry_run else ""
        self._write(f"{prefix}TRADE EXECUTED: {action}")
        self._write(f"  Market: {market_slug}")
        self._write(f"  Outcome: {outcome}")
        self._write(f"  Price: {price:.2%}")
        self._write(f"  Size: {size:.4f} shares")
        self._write(f"  Amount: ${amount_usd:.2f}")

    def log_trade_failed(self, action: str, market_slug: str, error: str):
        """Log a failed trade attempt."""
        self._write(f"TRADE FAILED: {action}")
        self._write(f"  Market: {market_slug}")
        self._write(f"  Error: {error}")

    # ─────────────────────────────────────────────────────────────
    # Position updates
    # ─────────────────────────────────────────────────────────────

    def log_position_opened(self, position: Position):
        """Log a new position being opened."""
        self._write(f"POSITION OPENED")
        self._write(f"  Market: {position.market_slug}")
        self._write(f"  Outcome: {position.outcome}")
        self._write(f"  Tokens: {position.tokens:.4f}")
        self._write(f"  Entry: {position.entry_price:.2%}")
        self._write(f"  Cost: ${position.entry_size:.2f}")

    def log_position_closed(self, position: Position, exit_price: float, pnl: float):
        """Log a position being closed."""
        self._write(f"POSITION CLOSED")
        self._write(f"  Market: {position.market_slug}")
        self._write(f"  Entry: {position.entry_price:.2%}")
        self._write(f"  Exit: {exit_price:.2%}")
        self._write(f"  P&L: ${pnl:+.2f}")

    # ─────────────────────────────────────────────────────────────
    # User actions
    # ─────────────────────────────────────────────────────────────

    def log_user_confirmed(self, action: str, market_slug: str):
        """Log user confirmation of an action."""
        self._write(f"USER CONFIRMED: {action} on {market_slug}")

    def log_user_rejected(self, action: str, market_slug: str):
        """Log user rejection of an action."""
        self._write(f"USER REJECTED: {action} on {market_slug}")

    # ─────────────────────────────────────────────────────────────
    # Generic
    # ─────────────────────────────────────────────────────────────

    def log_info(self, message: str):
        """Log a generic info message."""
        self._write(f"INFO: {message}")

    def log_warning(self, message: str):
        """Log a warning."""
        self._write(f"WARNING: {message}")

    def log_error(self, message: str):
        """Log an error."""
        self._write(f"ERROR: {message}")


# Global logger instance
_logger: Optional[BotLogger] = None


def get_logger() -> BotLogger:
    """Get or create the global logger instance."""
    global _logger
    if _logger is None:
        _logger = BotLogger()
    return _logger


def init_logger(log_dir: Optional[str] = None) -> BotLogger:
    """Initialize the global logger with custom settings."""
    global _logger
    _logger = BotLogger(log_dir)
    return _logger
