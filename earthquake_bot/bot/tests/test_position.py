"""Tests for Position model."""

import pytest
from datetime import datetime, timedelta

from ..models.position import Position, PositionStatus


class TestPosition:
    """Test Position model calculations."""

    def test_create_position(self):
        """Test basic position creation."""
        pos = Position(
            market_id="cond123",
            market_slug="earthquake-test",
            market_name="Test Market",
            outcome="YES",
            entry_price=0.05,
            entry_size=10.0,
            tokens=200.0,
        )

        assert pos.market_slug == "earthquake-test"
        assert pos.outcome == "YES"
        assert pos.entry_price == 0.05
        assert pos.entry_size == 10.0
        assert pos.tokens == 200.0
        assert pos.status == PositionStatus.OPEN

    def test_current_value(self):
        """Test current value calculation."""
        pos = Position(
            entry_price=0.05,
            entry_size=10.0,
            tokens=200.0,
        )

        # At same price
        assert pos.current_value(0.05) == 10.0

        # Price doubled
        assert pos.current_value(0.10) == 20.0

        # Price halved
        assert pos.current_value(0.025) == 5.0

    def test_unrealized_pnl(self):
        """Test unrealized P&L calculation."""
        pos = Position(
            entry_price=0.05,
            entry_size=10.0,
            tokens=200.0,
        )

        # No change
        assert pos.unrealized_pnl(0.05) == 0.0

        # Price increased - profit
        assert pos.unrealized_pnl(0.10) == 10.0

        # Price decreased - loss
        assert pos.unrealized_pnl(0.025) == -5.0

    def test_unrealized_pnl_pct(self):
        """Test unrealized P&L percentage."""
        pos = Position(
            entry_price=0.05,
            entry_size=10.0,
            tokens=200.0,
        )

        # No change
        assert pos.unrealized_pnl_pct(0.05) == 0.0

        # 100% profit
        assert pos.unrealized_pnl_pct(0.10) == 1.0

        # 50% loss
        assert pos.unrealized_pnl_pct(0.025) == -0.5

    def test_unrealized_pnl_pct_zero_entry(self):
        """Test unrealized P&L percentage with zero entry size."""
        pos = Position(entry_size=0.0)
        assert pos.unrealized_pnl_pct(0.10) == 0.0

    def test_close_position(self):
        """Test closing a position."""
        pos = Position(
            entry_price=0.05,
            entry_size=10.0,
            tokens=200.0,
        )

        pos.close(price=0.08, order_id="order123")

        assert pos.status == PositionStatus.CLOSED
        assert pos.exit_price == 0.08
        assert pos.exit_size == 16.0  # 200 * 0.08
        assert pos.exit_order_id == "order123"
        assert pos.exit_time is not None

    def test_realized_pnl_closed(self):
        """Test realized P&L for closed position."""
        pos = Position(
            entry_price=0.05,
            entry_size=10.0,
            tokens=200.0,
        )

        pos.close(price=0.08)

        # exit_size (16) - entry_size (10) = 6
        assert pos.realized_pnl() == 6.0

    def test_realized_pnl_open(self):
        """Test realized P&L for open position is zero."""
        pos = Position(
            entry_price=0.05,
            entry_size=10.0,
            tokens=200.0,
        )

        assert pos.realized_pnl() == 0.0

    def test_resolve_win(self):
        """Test resolving a winning position."""
        pos = Position(
            entry_price=0.05,
            entry_size=10.0,
            tokens=200.0,
        )

        pos.resolve(won=True)

        assert pos.status == PositionStatus.RESOLVED_WIN
        assert pos.exit_price == 1.0
        assert pos.exit_size == 200.0  # Each token worth $1

    def test_resolve_loss(self):
        """Test resolving a losing position."""
        pos = Position(
            entry_price=0.05,
            entry_size=10.0,
            tokens=200.0,
        )

        pos.resolve(won=False)

        assert pos.status == PositionStatus.RESOLVED_LOSS
        assert pos.exit_price == 0.0
        assert pos.exit_size == 0.0

    def test_realized_pnl_resolved_win(self):
        """Test realized P&L for resolved winning position."""
        pos = Position(
            entry_price=0.05,
            entry_size=10.0,
            tokens=200.0,
        )

        pos.resolve(won=True)

        # tokens (200) - entry_size (10) = 190
        assert pos.realized_pnl() == 190.0

    def test_realized_pnl_resolved_loss(self):
        """Test realized P&L for resolved losing position."""
        pos = Position(
            entry_price=0.05,
            entry_size=10.0,
            tokens=200.0,
        )

        pos.resolve(won=False)

        # Lost everything: -entry_size
        assert pos.realized_pnl() == -10.0

    def test_to_dict_and_back(self):
        """Test serialization round-trip."""
        pos = Position(
            market_id="cond123",
            market_slug="test-market",
            market_name="Test Market",
            outcome="YES",
            entry_price=0.05,
            entry_time="2025-01-14T10:00:00Z",
            entry_size=10.0,
            tokens=200.0,
            strategy="tested",
            fair_price_at_entry=0.08,
            edge_at_entry=0.03,
        )

        data = pos.to_dict()
        restored = Position.from_dict(data)

        assert restored.market_id == pos.market_id
        assert restored.market_slug == pos.market_slug
        assert restored.entry_price == pos.entry_price
        assert restored.tokens == pos.tokens
        assert restored.status == pos.status

    def test_to_json_and_back(self):
        """Test JSON serialization round-trip."""
        pos = Position(
            market_slug="test-market",
            entry_price=0.05,
            entry_size=10.0,
            tokens=200.0,
        )

        json_str = pos.to_json()
        restored = Position.from_json(json_str)

        assert restored.market_slug == pos.market_slug
        assert restored.entry_price == pos.entry_price

    def test_status_from_string(self):
        """Test that status can be initialized from string."""
        data = {
            "market_slug": "test",
            "status": "closed",
        }
        pos = Position.from_dict(data)
        assert pos.status == PositionStatus.CLOSED

    def test_age_str(self):
        """Test age string formatting."""
        # Position from today
        pos = Position(entry_time=datetime.utcnow().isoformat() + "Z")
        assert pos.age_str == "<1d"

        # Position from 3 days ago
        three_days_ago = datetime.utcnow() - timedelta(days=3)
        pos2 = Position(entry_time=three_days_ago.isoformat() + "Z")
        assert pos2.age_str == "3d"

        # Position from 2 weeks ago
        two_weeks_ago = datetime.utcnow() - timedelta(days=14)
        pos3 = Position(entry_time=two_weeks_ago.isoformat() + "Z")
        assert pos3.age_str == "2w"
