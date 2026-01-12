"""
Position model - represents an open or closed trading position.
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Optional
import json
import uuid


class PositionStatus(Enum):
    """Position status."""
    OPEN = "open"
    CLOSED = "closed"        # Sold before resolution
    RESOLVED_WIN = "win"     # Resolved in our favor
    RESOLVED_LOSS = "loss"   # Resolved against us


@dataclass
class Position:
    """Trading position."""

    # Identity
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])

    # Market info
    market_id: str = ""
    market_slug: str = ""       # e.g., "megaquake-march-2026"
    market_name: str = ""       # e.g., "Megaquake in March 2026"
    outcome: str = "YES"        # YES or NO
    resolution_date: Optional[str] = None  # ISO format

    # Entry
    entry_price: float = 0.0    # 0-1 (e.g., 0.042 = 4.2%)
    entry_time: str = ""        # ISO format
    entry_size: float = 0.0     # $ spent
    tokens: float = 0.0         # tokens received

    # Exit (if closed)
    exit_price: Optional[float] = None
    exit_time: Optional[str] = None
    exit_size: Optional[float] = None  # $ received

    # Status
    status: PositionStatus = PositionStatus.OPEN

    # Strategy
    strategy: str = "tested"
    fair_price_at_entry: float = 0.0
    edge_at_entry: float = 0.0

    # Order IDs (from Polymarket)
    entry_order_id: Optional[str] = None
    exit_order_id: Optional[str] = None

    def __post_init__(self):
        """Convert status string to enum if needed."""
        if isinstance(self.status, str):
            self.status = PositionStatus(self.status)

    @property
    def age_days(self) -> int:
        """Days since entry."""
        if not self.entry_time:
            return 0
        entry_dt = datetime.fromisoformat(self.entry_time.replace("Z", "+00:00"))
        now = datetime.now(entry_dt.tzinfo)
        return (now - entry_dt).days

    @property
    def age_str(self) -> str:
        """Human readable age."""
        days = self.age_days
        if days == 0:
            return "<1d"
        elif days < 7:
            return f"{days}d"
        else:
            return f"{days // 7}w"

    def current_value(self, current_price: float) -> float:
        """Current value if sold at current_price."""
        return self.tokens * current_price

    def unrealized_pnl(self, current_price: float) -> float:
        """Unrealized P&L at current price."""
        return self.current_value(current_price) - self.entry_size

    def unrealized_pnl_pct(self, current_price: float) -> float:
        """Unrealized P&L as percentage."""
        if self.entry_size == 0:
            return 0.0
        return self.unrealized_pnl(current_price) / self.entry_size

    def realized_pnl(self) -> float:
        """Realized P&L (for closed positions)."""
        if self.status == PositionStatus.OPEN:
            return 0.0
        elif self.status == PositionStatus.CLOSED:
            return (self.exit_size or 0) - self.entry_size
        elif self.status == PositionStatus.RESOLVED_WIN:
            return self.tokens - self.entry_size  # Each token worth $1
        else:  # RESOLVED_LOSS
            return -self.entry_size  # Lost everything

    def close(self, price: float, order_id: Optional[str] = None) -> None:
        """Mark position as closed (sold)."""
        self.exit_price = price
        self.exit_time = datetime.utcnow().isoformat() + "Z"
        self.exit_size = self.tokens * price
        self.exit_order_id = order_id
        self.status = PositionStatus.CLOSED

    def resolve(self, won: bool) -> None:
        """Mark position as resolved."""
        self.exit_time = datetime.utcnow().isoformat() + "Z"
        self.exit_price = 1.0 if won else 0.0
        self.exit_size = self.tokens if won else 0.0
        self.status = PositionStatus.RESOLVED_WIN if won else PositionStatus.RESOLVED_LOSS

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        data = asdict(self)
        data["status"] = self.status.value
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Position":
        """Create from dictionary."""
        return cls(**data)

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_json(cls, json_str: str) -> "Position":
        """Deserialize from JSON string."""
        return cls.from_dict(json.loads(json_str))
