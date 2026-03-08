"""
Market model - represents a Polymarket prediction market.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class Market:
    """Polymarket prediction market."""

    # Identity
    id: str = ""
    slug: str = ""              # market slug
    name: str = ""              # question text
    description: str = ""

    # Tokens
    yes_token_id: str = ""
    no_token_id: str = ""

    # Prices (0-1)
    yes_price: float = 0.0
    no_price: float = 0.0

    # Liquidity
    volume: float = 0.0
    liquidity: float = 0.0

    # Dates
    end_date: Optional[str] = None
    created_at: Optional[str] = None

    # Status
    is_active: bool = True
    is_resolved: bool = False
    resolution: Optional[str] = None

    # Category
    category: str = "weather"

    @property
    def days_remaining(self) -> float:
        """Days until resolution."""
        if not self.end_date:
            return 0.0

        try:
            end_dt = datetime.fromisoformat(self.end_date.replace("Z", "+00:00"))
            now = datetime.now(end_dt.tzinfo)
            delta = end_dt - now
            return max(0, delta.total_seconds() / 86400)
        except Exception:
            return 0.0

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "yes_token_id": self.yes_token_id,
            "no_token_id": self.no_token_id,
            "yes_price": self.yes_price,
            "no_price": self.no_price,
            "volume": self.volume,
            "liquidity": self.liquidity,
            "end_date": self.end_date,
            "created_at": self.created_at,
            "is_active": self.is_active,
            "is_resolved": self.is_resolved,
            "resolution": self.resolution,
            "category": self.category,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Market":
        """Create from dictionary."""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    @classmethod
    def from_polymarket_api(cls, data: dict) -> "Market":
        """Create from Polymarket API response."""
        yes_token_id = ""
        no_token_id = ""
        yes_price = 0.0
        no_price = 0.0

        tokens = data.get("tokens", [])
        for token in tokens:
            if token.get("outcome") == "Yes":
                yes_token_id = token.get("token_id", "")
                yes_price = float(token.get("price", 0))
            elif token.get("outcome") == "No":
                no_token_id = token.get("token_id", "")
                no_price = float(token.get("price", 0))

        return cls(
            id=data.get("condition_id", data.get("id", "")),
            slug=data.get("market_slug", data.get("slug", "")),
            name=data.get("question", data.get("title", "")),
            description=data.get("description", ""),
            yes_token_id=yes_token_id,
            no_token_id=no_token_id,
            yes_price=yes_price,
            no_price=no_price,
            volume=float(data.get("volume", 0)),
            liquidity=float(data.get("liquidity", 0)),
            end_date=data.get("end_date_iso", data.get("end_date")),
            created_at=data.get("created_at"),
            is_active=data.get("active", True),
            is_resolved=data.get("closed", False),
            resolution=data.get("outcome"),
            category="weather",
        )
