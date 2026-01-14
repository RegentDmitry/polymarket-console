"""
Signal model - represents a trading signal (BUY or SELL).
"""

from dataclasses import dataclass
from datetime import datetime
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

    # For SELL signals
    position_id: Optional[str] = None
    reason: str = ""              # e.g., "take-profit", "stop-loss"

    # Suggested action
    suggested_size: float = 0.0   # $ to buy/sell
    kelly: float = 0.0            # Kelly criterion (fraction of bankroll)

    # Liquidity
    liquidity: float = 0.0        # Available liquidity at current price ($)

    # Extra info
    token_id: str = ""            # Polymarket token ID
    model_used: str = ""          # Model used for prediction
    annual_return: float = 0.0    # Annualized return (APY)

    # Timestamp
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.utcnow().isoformat() + "Z"

    @property
    def is_actionable(self) -> bool:
        """Whether this signal should trigger a trade."""
        return self.type in (SignalType.BUY, SignalType.SELL)

    def format_short(self) -> str:
        """Short format for display."""
        if self.type == SignalType.BUY:
            return f"+ {self.market_slug}: {self.current_price:.1%} -> {self.fair_price:.1%} (edge {self.edge:.1%})"
        elif self.type == SignalType.SELL:
            return f"! {self.market_slug}: SELL @ {self.target_price:.1%} ({self.reason})"
        else:
            return f"- {self.market_slug}: {self.current_price:.1%} (no edge)"

    def format_detailed(self) -> list[str]:
        """Detailed multi-line format for scanner display."""
        lines = []

        if self.type == SignalType.BUY:
            lines.append(f"+ {self.market_slug}")
            lines.append(f"  Price: {self.current_price:.1%}  Fair: {self.fair_price:.1%}")
            lines.append(f"  Edge: {self.edge:.1%}   ROI: {self.roi:.0%}")
            lines.append(f"  >>> BUY {self.outcome}")

        elif self.type == SignalType.SELL:
            lines.append(f"! {self.market_slug}: price {self.current_price:.1%}")
            lines.append(f"  >>> SELL @ {self.target_price:.1%}")
            lines.append(f"  Reason: {self.reason}")

        else:  # SKIP
            lines.append(f"- {self.market_slug}")
            lines.append(f"  Price: {self.current_price:.1%}  Fair: {self.fair_price:.1%}")
            lines.append(f"  Edge: {self.edge:.1%}   Skip")

        return lines
