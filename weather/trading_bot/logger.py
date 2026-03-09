"""
Detailed event logger for the weather trading bot.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

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
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.log_file = log_dir / f"bot_{date_str}.log"

    def _write(self, message: str):
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{timestamp}] {message}\n"
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(line)

    def _separator(self, char: str = "-", length: int = 60):
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(char * length + "\n")

    def log_startup(self, mode: str, interval: int, min_edge: float):
        self._separator("=")
        self._write("BOT STARTED")
        self._write(f"  Mode: {mode}")
        self._write(f"  Scan interval: {interval}s")
        self._write(f"  Min edge: {min_edge:.1%}")
        self._separator("=")

    def log_shutdown(self):
        self._separator("=")
        self._write("BOT STOPPED")
        self._separator("=")

    def log_scan_start(self):
        self._separator()
        self._write("SCAN STARTED")

    def log_scan_complete(self, buy_signals: int, sell_signals: int, skip_signals: int,
                          duration_sec: float):
        self._write(f"SCAN COMPLETE in {duration_sec:.1f}s")
        self._write(f"  Results: {buy_signals} BUY, {sell_signals} SELL, {skip_signals} SKIP")

    def log_signal(self, signal: Signal):
        self._write(f"SIGNAL: {signal.type.value}")
        self._write(f"  City: {signal.city}  Date: {signal.date}  Bucket: {signal.bucket_label}")
        self._write(f"  Slug: {signal.market_slug}")
        self._write(f"  Current price: {signal.current_price:.2%}")
        self._write(f"  Fair price: {signal.fair_price:.2%}")
        if signal.edge:
            self._write(f"  Edge: {signal.edge:.2%}")
        if signal.liquidity:
            self._write(f"  Liquidity: ${signal.liquidity:.2f}")
        if signal.reason:
            self._write(f"  Reason: {signal.reason}")

    def log_trade_executed(self, action: str, market_slug: str, outcome: str,
                           price: float, size: float, amount_usd: float,
                           dry_run: bool = False):
        prefix = "[DRY RUN] " if dry_run else ""
        self._write(f"{prefix}TRADE EXECUTED: {action}")
        self._write(f"  Market: {market_slug}")
        self._write(f"  Outcome: {outcome}")
        self._write(f"  Price: {price:.2%}")
        self._write(f"  Size: {size:.4f} shares")
        self._write(f"  Amount: ${amount_usd:.2f}")

    def log_trade_failed(self, action: str, market_slug: str, error: str):
        self._write(f"TRADE FAILED: {action}")
        self._write(f"  Market: {market_slug}")
        self._write(f"  Error: {error}")

    def log_position_opened(self, position: Position):
        self._write(f"POSITION OPENED")
        self._write(f"  City: {position.city}  Date: {position.date}")
        self._write(f"  Bucket: {position.bucket_label}")
        self._write(f"  Tokens: {position.tokens:.4f}")
        self._write(f"  Entry: {position.entry_price:.2%}")
        self._write(f"  Cost: ${position.entry_size:.2f}")

    def log_position_closed(self, position: Position, exit_price: float, pnl: float):
        self._write(f"POSITION CLOSED")
        self._write(f"  Market: {position.market_slug}")
        self._write(f"  Entry: {position.entry_price:.2%}")
        self._write(f"  Exit: {exit_price:.2%}")
        self._write(f"  P&L: ${pnl:+.2f}")

    def log_info(self, message: str):
        self._write(f"INFO: {message}")

    def log_warning(self, message: str):
        self._write(f"WARNING: {message}")

    def log_error(self, message: str):
        self._write(f"ERROR: {message}")


class TradeJournal:
    """Structured trade journal — one JSONL line per trading event."""

    def __init__(self, data_dir: Optional[str] = None):
        if data_dir is None:
            data_dir = Path(__file__).parent / "data"
        else:
            data_dir = Path(data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        self.journal_file = data_dir / "trades.jsonl"

    def _write(self, record: dict):
        record["ts"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        with open(self.journal_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")

    def log_buy(self, position: Position, signal: Signal, order_id: str = ""):
        self._write({
            "action": "BUY",
            "slug": position.market_slug,
            "city": position.city,
            "date": position.date,
            "bucket": position.bucket_label,
            "outcome": position.outcome,
            "entry_price": round(position.entry_price, 4),
            "fair_price": round(signal.fair_price, 4),
            "edge": round(signal.edge, 4),
            "size_usd": round(position.entry_size, 2),
            "tokens": round(position.tokens, 4),
            "forecast": round(signal.forecast, 1),
            "sigma": round(signal.sigma, 2),
            "model": signal.model_used,
            "order_id": order_id,
        })

    def log_resolution(self, position: Position, won: bool, pnl: float):
        self._write({
            "action": "RESOLVED",
            "slug": position.market_slug,
            "city": position.city,
            "date": position.date,
            "bucket": position.bucket_label,
            "outcome": position.outcome,
            "won": won,
            "entry_price": round(position.entry_price, 4),
            "pnl": round(pnl, 2),
            "tokens": round(position.tokens, 4),
            "hold_days": position.age_days,
        })

    def log_edge_exit(self, position: Position, signal: Signal, pnl: float):
        self._write({
            "action": "EDGE_EXIT",
            "slug": position.market_slug,
            "city": position.city,
            "date": position.date,
            "bucket": position.bucket_label,
            "outcome": position.outcome,
            "entry_price": round(position.entry_price, 4),
            "exit_price": round(signal.current_price, 4),
            "fair_price": round(signal.fair_price, 4),
            "pnl": round(pnl, 2),
        })


# Global instances
_logger: Optional[BotLogger] = None
_trade_journal: Optional[TradeJournal] = None


def get_logger() -> BotLogger:
    global _logger
    if _logger is None:
        _logger = BotLogger()
    return _logger


def get_trade_journal() -> TradeJournal:
    global _trade_journal
    if _trade_journal is None:
        _trade_journal = TradeJournal()
    return _trade_journal


def init_logger(log_dir: Optional[str] = None) -> BotLogger:
    global _logger
    _logger = BotLogger(log_dir)
    return _logger
