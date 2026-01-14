"""Tests for Signal model."""

import pytest

from ..models.signal import Signal, SignalType


class TestSignal:
    """Test Signal model."""

    def test_create_buy_signal(self):
        """Test creating a BUY signal."""
        signal = Signal(
            type=SignalType.BUY,
            market_id="cond123",
            market_slug="earthquake-5plus-YES",
            market_name="5 or more (YES)",
            outcome="YES",
            current_price=0.042,
            fair_price=0.095,
            edge=0.053,
            roi=1.26,
            days_remaining=45.0,
            token_id="token123",
            model_used="tested",
            annual_return=1.02,
            liquidity=100.0,
        )

        assert signal.type == SignalType.BUY
        assert signal.market_slug == "earthquake-5plus-YES"
        assert signal.outcome == "YES"
        assert signal.current_price == 0.042
        assert signal.fair_price == 0.095
        assert signal.edge == 0.053
        assert signal.liquidity == 100.0

    def test_create_sell_signal(self):
        """Test creating a SELL signal."""
        signal = Signal(
            type=SignalType.SELL,
            market_id="cond123",
            market_slug="earthquake-5plus-YES",
            market_name="5 or more (YES)",
            outcome="YES",
            current_price=0.10,  # bid price
            fair_price=0.095,
            position_id="pos123",
            reason="Bid 10.0% >= Fair 9.5%",
            suggested_size=15.0,
            liquidity=50.0,
            token_id="token123",
        )

        assert signal.type == SignalType.SELL
        assert signal.position_id == "pos123"
        assert signal.reason == "Bid 10.0% >= Fair 9.5%"
        assert signal.suggested_size == 15.0

    def test_create_skip_signal(self):
        """Test creating a SKIP signal (doesn't meet criteria)."""
        signal = Signal(
            type=SignalType.SKIP,
            market_id="cond123",
            market_slug="earthquake-3plus-NO",
            market_name="3 or more (NO)",
            outcome="NO",
            current_price=0.85,
            fair_price=0.87,
            edge=0.02,  # Below threshold
            annual_return=0.15,  # Below 30% APY threshold
        )

        assert signal.type == SignalType.SKIP
        assert signal.edge == 0.02

    def test_signal_defaults(self):
        """Test signal default values."""
        signal = Signal(
            type=SignalType.BUY,
            market_id="test",
            market_slug="test-slug",
            market_name="Test Market",
        )

        assert signal.current_price == 0.0
        assert signal.fair_price == 0.0
        assert signal.suggested_size == 0.0
        assert signal.liquidity == 0.0
        assert signal.kelly == 0.0
        assert signal.token_id == ""  # Empty string by default
        assert signal.position_id is None
        assert signal.reason == ""  # Empty string by default

    def test_signal_type_values(self):
        """Test SignalType enum values."""
        assert SignalType.BUY.value == "BUY"
        assert SignalType.SELL.value == "SELL"
        assert SignalType.SKIP.value == "SKIP"

    def test_signal_with_all_fields(self):
        """Test signal with all optional fields populated."""
        signal = Signal(
            type=SignalType.BUY,
            market_id="cond123",
            market_slug="test-market-YES",
            market_name="Test Market (YES)",
            outcome="YES",
            current_price=0.05,
            fair_price=0.10,
            target_price=0.05,
            edge=0.05,
            roi=1.0,
            days_remaining=30.0,
            position_id=None,
            suggested_size=50.0,
            reason="Good edge",
            token_id="token123",
            model_used="tested",
            annual_return=12.17,
            liquidity=100.0,
            kelly=0.5,
        )

        assert signal.target_price == 0.05
        assert signal.model_used == "tested"
        assert signal.annual_return == 12.17
        assert signal.kelly == 0.5
