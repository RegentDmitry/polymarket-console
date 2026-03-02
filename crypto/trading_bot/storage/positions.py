"""
Position storage - save and load active positions from JSON files.

Each position is stored as a separate JSON file in the active directory.
When a position is closed, it's moved to the history directory.
"""

import json
from pathlib import Path
from typing import List, Optional

from ..models.position import Position, PositionStatus


class PositionStorage:
    """Manages persistence of trading positions."""

    def __init__(self, active_dir: Path, history_dir: Path):
        self.active_dir = active_dir
        self.history_dir = history_dir

        # Ensure directories exist
        self.active_dir.mkdir(parents=True, exist_ok=True)
        self.history_dir.mkdir(parents=True, exist_ok=True)

    def _position_path(self, position_id: str) -> Path:
        """Get file path for a position."""
        return self.active_dir / f"{position_id}.json"

    def _history_path(self, position_id: str) -> Path:
        """Get history file path for a position."""
        return self.history_dir / f"{position_id}.json"

    def save(self, position: Position) -> None:
        """Save a position to disk."""
        if position.status == PositionStatus.OPEN:
            # Save to active directory
            path = self._position_path(position.id)
            with open(path, "w") as f:
                f.write(position.to_json())
        else:
            # Move to history
            self.move_to_history(position)

    def load(self, position_id: str) -> Optional[Position]:
        """Load a position by ID."""
        path = self._position_path(position_id)
        if not path.exists():
            return None

        with open(path) as f:
            return Position.from_json(f.read())

    def load_all_active(self) -> List[Position]:
        """Load all active positions."""
        positions = []
        for path in self.active_dir.glob("*.json"):
            try:
                with open(path) as f:
                    pos = Position.from_json(f.read())
                    if pos.status == PositionStatus.OPEN:
                        positions.append(pos)
            except Exception as e:
                print(f"Warning: Failed to load {path}: {e}")

        # Sort by entry time (newest first)
        positions.sort(key=lambda p: p.entry_time, reverse=True)
        return positions

    def delete(self, position_id: str) -> bool:
        """Delete a position file."""
        path = self._position_path(position_id)
        if path.exists():
            path.unlink()
            return True
        return False

    def move_to_history(self, position: Position) -> None:
        """Move a closed position to history directory."""
        # Remove from active
        active_path = self._position_path(position.id)
        if active_path.exists():
            active_path.unlink()

        # Save to history
        history_path = self._history_path(position.id)
        with open(history_path, "w") as f:
            f.write(position.to_json())

    def close_position(self, position_id: str, exit_price: float,
                       order_id: Optional[str] = None) -> Optional[Position]:
        """Close a position and move to history."""
        position = self.load(position_id)
        if not position:
            return None

        position.close(exit_price, order_id)
        self.move_to_history(position)
        return position

    def resolve_position(self, position_id: str, won: bool) -> Optional[Position]:
        """Resolve a position (market resolved) and move to history."""
        position = self.load(position_id)
        if not position:
            return None

        position.resolve(won)
        self.move_to_history(position)
        return position

    def get_position_by_market(self, market_slug: str) -> Optional[Position]:
        """Find an active position for a specific market."""
        for position in self.load_all_active():
            if position.market_slug == market_slug:
                return position
        return None

    def find_matching_position(self, market_slug: str, outcome: str) -> Optional[Position]:
        """Find an active position for a specific market + outcome."""
        for position in self.load_all_active():
            if position.market_slug == market_slug and position.outcome == outcome:
                return position
        return None

    def merge_into(self, existing: Position, new_tokens: float,
                   new_entry_size: float, new_entry_price: float,
                   new_order_id: Optional[str] = None) -> Position:
        """Merge a new buy into an existing position (weighted average).

        Updates tokens, entry_size, entry_price in place and saves.
        """
        old_tokens = existing.tokens
        old_size = existing.entry_size

        existing.tokens = old_tokens + new_tokens
        existing.entry_size = old_size + new_entry_size
        existing.entry_price = (
            existing.entry_size / existing.tokens
            if existing.tokens > 0 else new_entry_price
        )
        # Keep the most recent order id for reference
        if new_order_id:
            existing.entry_order_id = new_order_id

        self.save(existing)
        return existing

    def count_active(self) -> int:
        """Count active positions."""
        return len(list(self.active_dir.glob("*.json")))

    def total_invested(self) -> float:
        """Calculate total $ invested in active positions."""
        return sum(p.entry_size for p in self.load_all_active())

    def calculate_unrealized_pnl(self, current_prices: dict[str, float]) -> tuple[float, float]:
        """
        Calculate total unrealized P&L.

        Args:
            current_prices: dict mapping market_slug to current price

        Returns:
            Tuple of (unrealized_pnl, unrealized_pnl_pct)
        """
        total_invested = 0.0
        total_current_value = 0.0

        for position in self.load_all_active():
            current_price = current_prices.get(position.market_slug, position.entry_price)
            total_invested += position.entry_size
            total_current_value += position.current_value(current_price)

        unrealized_pnl = total_current_value - total_invested
        unrealized_pnl_pct = unrealized_pnl / total_invested if total_invested > 0 else 0.0

        return unrealized_pnl, unrealized_pnl_pct
