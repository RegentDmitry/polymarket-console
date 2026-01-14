"""Tests for PolymarketExecutor."""

import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime

from ..executor.polymarket import PolymarketExecutor, OrderResult
from ..models.position import Position, PositionStatus
from ..models.signal import Signal, SignalType
from ..models.market import Market


class TestOrderResult:
    """Test OrderResult dataclass."""

    def test_successful_result(self):
        """Test creating a successful order result."""
        result = OrderResult(
            success=True,
            order_id="order123",
            filled_price=0.05,
            filled_size=10.0,
            tokens=200.0,
        )

        assert result.success is True
        assert result.order_id == "order123"
        assert result.filled_price == 0.05
        assert result.error is None

    def test_failed_result(self):
        """Test creating a failed order result."""
        result = OrderResult(
            success=False,
            error="Insufficient balance"
        )

        assert result.success is False
        assert result.error == "Insufficient balance"
        assert result.order_id is None

    def test_timestamp_auto_set(self):
        """Test that timestamp is automatically set."""
        result = OrderResult(success=True)

        assert result.timestamp != ""
        assert "T" in result.timestamp  # ISO format


class TestPolymarketExecutorSync:
    """Test position sync functionality."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock Polymarket client."""
        client = Mock()
        client.get_address.return_value = "0x1234567890"
        return client

    @pytest.fixture
    def mock_storage(self):
        """Create a mock position storage."""
        storage = Mock()
        storage.load_all_active.return_value = []
        storage.save = Mock()
        return storage

    @pytest.fixture
    def executor(self, mock_client):
        """Create executor with mocked client."""
        with patch('earthquakes.trading_bot.executor.polymarket.PolymarketClient') as MockClient:
            MockClient.return_value = mock_client
            with patch('earthquakes.trading_bot.executor.polymarket.POLYMARKET_AVAILABLE', True):
                exec = PolymarketExecutor()
                exec.client = mock_client
                exec.initialized = True
                return exec

    def test_get_api_positions_empty(self, executor, mock_client):
        """Test getting positions when none exist."""
        mock_client.get_positions.return_value = []

        positions = executor.get_api_positions()

        assert positions == []
        mock_client.get_positions.assert_called_once()

    def test_get_api_positions_with_data(self, executor, mock_client):
        """Test getting positions with data."""
        mock_client.get_positions.return_value = [
            {
                "asset": "token123",
                "size": "100.5",
                "avgCost": "0.05",
                "market": {
                    "conditionId": "cond123",
                    "question": "Test Market",
                }
            }
        ]

        positions = executor.get_api_positions()

        assert len(positions) == 1
        assert positions[0]["asset"] == "token123"

    def test_sync_positions_no_client(self, mock_storage):
        """Test sync when client not initialized."""
        executor = PolymarketExecutor()
        executor.client = None
        executor.initialized = False

        result = executor.sync_positions(mock_storage)

        assert result == []

    def test_sync_positions_empty_api(self, executor, mock_client, mock_storage):
        """Test sync when API returns no positions."""
        mock_client.get_positions.return_value = []

        result = executor.sync_positions(mock_storage)

        assert result == []

    def test_sync_positions_creates_new(self, executor, mock_client, mock_storage):
        """Test sync creates new positions from API."""
        mock_client.get_positions.return_value = [
            {
                "asset": "token123",
                "size": "100",
                "avgCost": "0.05",
                "outcome": "Yes",
                "slug": "test-market",
                "market": {
                    "conditionId": "cond123",
                    "question": "Test Market Question",
                    "endDateIso": "2025-06-30T00:00:00Z",
                }
            }
        ]
        mock_storage.load_all_active.return_value = []

        result = executor.sync_positions(mock_storage)

        assert len(result) == 1
        assert result[0].market_slug == "test-market"
        assert result[0].tokens == 100.0
        assert result[0].entry_price == 0.05
        mock_storage.save.assert_called_once()

    def test_sync_positions_skips_existing(self, executor, mock_client, mock_storage):
        """Test sync skips positions that already exist locally."""
        mock_client.get_positions.return_value = [
            {
                "asset": "token123",
                "size": "100",
                "avgCost": "0.05",
                "outcome": "Yes",
                "slug": "existing-market",
                "market": {"conditionId": "cond123", "question": "Test"}
            }
        ]
        # Already have this position locally
        existing = Position(market_slug="existing-market")
        mock_storage.load_all_active.return_value = [existing]

        result = executor.sync_positions(mock_storage)

        assert result == []
        mock_storage.save.assert_not_called()

    def test_sync_positions_skips_zero_size(self, executor, mock_client, mock_storage):
        """Test sync skips positions with zero size."""
        mock_client.get_positions.return_value = [
            {
                "asset": "token123",
                "size": "0",
                "avgCost": "0.05",
                "slug": "empty-market",
                "market": {"conditionId": "cond123", "question": "Test"}
            }
        ]
        mock_storage.load_all_active.return_value = []

        result = executor.sync_positions(mock_storage)

        assert result == []


class TestPolymarketExecutorBalance:
    """Test balance functionality."""

    def test_get_balance_no_client(self):
        """Test get_balance returns 0 when no client."""
        executor = PolymarketExecutor()
        executor.client = None

        assert executor.get_balance() == 0.0

    def test_get_balance_with_client(self):
        """Test get_balance with mocked client."""
        executor = PolymarketExecutor()
        executor.client = Mock()
        executor.client.get_balance.return_value = {"balance": 1000000}  # 1 USDC in raw

        balance = executor.get_balance()

        assert balance == 1.0  # Converted from 6 decimals

    def test_get_balance_error(self):
        """Test get_balance handles errors."""
        executor = PolymarketExecutor()
        executor.client = Mock()
        executor.client.get_balance.side_effect = Exception("API Error")

        balance = executor.get_balance()

        assert balance == 0.0
