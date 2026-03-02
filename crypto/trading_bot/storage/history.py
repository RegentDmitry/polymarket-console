"""
History storage - manages trade history and statistics.
"""

import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Optional
from dataclasses import dataclass

from ..models.position import Position, PositionStatus


@dataclass
class TradeRecord:
    """Record of a single trade (buy or sell)."""
    timestamp: str
    action: str          # "BUY" or "SELL"
    market_slug: str
    price: float
    size: float          # $ amount
    tokens: float
    pnl: Optional[float] = None  # For SELL only
    order_id: Optional[str] = None

    def format_line(self) -> str:
        """Format as a single line for display."""
        time_str = self.timestamp[11:16] if len(self.timestamp) > 16 else self.timestamp
        pnl_str = f"  P&L: {self.pnl:+.2f}" if self.pnl is not None else ""
        return f"{time_str} {self.action:<4} {self.market_slug:<15} ${self.size:.2f} @ {self.price:.1%}{pnl_str}"


class HistoryStorage:
    """Manages trade history."""

    def __init__(self, history_dir: Path):
        self.history_dir = history_dir
        self.history_dir.mkdir(parents=True, exist_ok=True)

        # In-memory trade log (recent trades)
        self._recent_trades: List[TradeRecord] = []
        self._max_recent = 100

    def load_closed_positions(self) -> List[Position]:
        """Load all closed positions from history."""
        positions = []
        for path in self.history_dir.glob("*.json"):
            try:
                with open(path) as f:
                    pos = Position.from_json(f.read())
                    positions.append(pos)
            except Exception as e:
                print(f"Warning: Failed to load {path}: {e}")

        # Sort by exit time (newest first)
        positions.sort(key=lambda p: p.exit_time or "", reverse=True)
        return positions

    def add_trade(self, record: TradeRecord) -> None:
        """Add a trade to recent trades log."""
        self._recent_trades.insert(0, record)
        if len(self._recent_trades) > self._max_recent:
            self._recent_trades = self._recent_trades[:self._max_recent]

    def record_buy(self, position: Position, order_id: Optional[str] = None) -> None:
        """Record a buy trade."""
        self.add_trade(TradeRecord(
            timestamp=position.entry_time,
            action="BUY",
            market_slug=position.market_slug,
            price=position.entry_price,
            size=position.entry_size,
            tokens=position.tokens,
            order_id=order_id,
        ))

    def record_sell(self, position: Position, order_id: Optional[str] = None) -> None:
        """Record a sell trade."""
        self.add_trade(TradeRecord(
            timestamp=position.exit_time or datetime.utcnow().isoformat() + "Z",
            action="SELL",
            market_slug=position.market_slug,
            price=position.exit_price or 0,
            size=position.exit_size or 0,
            tokens=position.tokens,
            pnl=position.realized_pnl(),
            order_id=order_id,
        ))

    def get_recent_trades(self, limit: int = 10) -> List[TradeRecord]:
        """Get recent trades."""
        return self._recent_trades[:limit]

    def get_realized_pnl_today(self) -> float:
        """Calculate realized P&L for today."""
        today = datetime.utcnow().date()
        total = 0.0

        for position in self.load_closed_positions():
            if position.exit_time:
                try:
                    exit_date = datetime.fromisoformat(
                        position.exit_time.replace("Z", "+00:00")
                    ).date()
                    if exit_date == today:
                        total += position.realized_pnl()
                except:
                    pass

        return total

    def get_realized_pnl_period(self, days: int = 7) -> float:
        """Calculate realized P&L for the last N days."""
        cutoff = datetime.utcnow() - timedelta(days=days)
        total = 0.0

        for position in self.load_closed_positions():
            if position.exit_time:
                try:
                    exit_dt = datetime.fromisoformat(
                        position.exit_time.replace("Z", "+00:00")
                    )
                    if exit_dt.replace(tzinfo=None) >= cutoff:
                        total += position.realized_pnl()
                except:
                    pass

        return total

    def get_statistics(self) -> dict:
        """Get overall trading statistics."""
        positions = self.load_closed_positions()

        if not positions:
            return {
                "total_trades": 0,
                "wins": 0,
                "losses": 0,
                "win_rate": 0.0,
                "total_pnl": 0.0,
                "avg_pnl": 0.0,
                "best_trade": 0.0,
                "worst_trade": 0.0,
            }

        pnls = [p.realized_pnl() for p in positions]
        wins = sum(1 for p in pnls if p > 0)
        losses = sum(1 for p in pnls if p < 0)

        return {
            "total_trades": len(positions),
            "wins": wins,
            "losses": losses,
            "win_rate": wins / len(positions) if positions else 0.0,
            "total_pnl": sum(pnls),
            "avg_pnl": sum(pnls) / len(pnls) if pnls else 0.0,
            "best_trade": max(pnls) if pnls else 0.0,
            "worst_trade": min(pnls) if pnls else 0.0,
        }
