"""
Signal model - represents a trading signal (BUY or SELL).
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class SignalType(Enum):
    """Type of trading signal."""
    BUY = "BUY"
    SELL = "SELL"
    SKIP = "SKIP"


@dataclass
class Signal:
    """Trading signal from scanner."""

    # Signal type
    type: SignalType

    # Market info
    market_id: str
    market_slug: str
    market_name: str
    outcome: str = "YES"  # What to buy/sell

    # Prices
    current_price: float = 0.0    # Market price
    fair_price: float = 0.0       # Model price
    target_price: float = 0.0     # For SELL: exit target

    # Metrics
    edge: float = 0.0             # fair - current (for BUY)
    roi: float = 0.0              # Expected ROI
    days_remaining: float = 0.0   # Days until resolution
    kelly: float = 0.0            # Half-Kelly fraction (set by allocate_sizes)

    # For SELL signals
    position_id: Optional[str] = None
    reason: str = ""              # e.g., "forecast_change", "edge_gone"

    # Suggested action
    suggested_size: float = 0.0   # $ to buy/sell

    # Liquidity
    liquidity: float = 0.0        # Available liquidity at current price ($)

    # Extra info
    token_id: str = ""            # Polymarket token ID
    model_used: str = ""          # Model used for prediction

    # Weather-specific
    city: str = ""
    date: str = ""                # "2026-03-07"
    bucket_label: str = ""        # "44-45°F"
    forecast: float = 0.0         # Model forecast temperature
    sigma: float = 0.0            # Model sigma

    # Timestamp
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    @property
    def is_actionable(self) -> bool:
        """Whether this signal should trigger a trade."""
        return self.type in (SignalType.BUY, SignalType.SELL)

    def format_short(self) -> str:
        """Short format for display."""
        if self.type == SignalType.BUY:
            return f"+ {self.city} {self.date} {self.bucket_label}: {self.current_price:.0%} -> {self.fair_price:.0%} (edge {self.edge:.1%})"
        elif self.type == SignalType.SELL:
            return f"! {self.city} {self.date} {self.bucket_label}: SELL ({self.reason})"
        else:
            return f"- {self.city} {self.date} {self.bucket_label}: no edge"
