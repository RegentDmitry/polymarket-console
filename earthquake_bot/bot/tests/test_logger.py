"""Tests for Logger module."""

import pytest
import tempfile
import os
from pathlib import Path

from ..logger import BotLogger, get_logger, init_logger
from ..models.signal import Signal, SignalType
from ..models.position import Position


class TestBotLogger:
    """Test BotLogger class."""

    @pytest.fixture
    def temp_log_dir(self):
        """Create a temporary directory for logs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    @pytest.fixture
    def logger(self, temp_log_dir):
        """Create a logger with temp directory."""
        return BotLogger(log_dir=temp_log_dir)

    def test_log_file_created(self, logger, temp_log_dir):
        """Test that log file is created."""
        logger.log_info("Test message")

        log_files = list(Path(temp_log_dir).glob("bot_*.log"))
        assert len(log_files) == 1

    def test_log_startup(self, logger, temp_log_dir):
        """Test startup logging."""
        logger.log_startup(
            mode="DRY RUN",
            interval=300,
            min_edge=0.03,
            min_apy=0.30
        )

        content = logger.log_file.read_text()
        assert "BOT STARTED" in content
        assert "Mode: DRY RUN" in content
        assert "Scan interval: 300s" in content
        assert "Min edge: 3.0%" in content
        assert "Min APY: 30%" in content

    def test_log_shutdown(self, logger):
        """Test shutdown logging."""
        logger.log_shutdown()

        content = logger.log_file.read_text()
        assert "BOT STOPPED" in content

    def test_log_scan_start(self, logger):
        """Test scan start logging."""
        logger.log_scan_start()

        content = logger.log_file.read_text()
        assert "SCAN STARTED" in content

    def test_log_scan_complete(self, logger):
        """Test scan complete logging."""
        logger.log_scan_complete(
            buy_signals=2,
            sell_signals=1,
            skip_signals=5,
            duration_sec=3.5
        )

        content = logger.log_file.read_text()
        assert "SCAN COMPLETE in 3.5s" in content
        assert "2 BUY, 1 SELL, 5 SKIP" in content

    def test_log_signal(self, logger):
        """Test signal logging."""
        signal = Signal(
            type=SignalType.BUY,
            market_id="cond123",
            market_slug="test-market-YES",
            market_name="Test Market (YES)",
            outcome="YES",
            current_price=0.05,
            fair_price=0.10,
            edge=0.05,
            annual_return=1.2,
            days_remaining=30.0,
            model_used="tested",
            liquidity=100.0,
            suggested_size=50.0,
        )

        logger.log_signal(signal)

        content = logger.log_file.read_text()
        assert "SIGNAL: BUY" in content
        assert "Market: Test Market (YES)" in content
        assert "Slug: test-market-YES" in content
        assert "Current price: 5.00%" in content
        assert "Fair price: 10.00%" in content
        assert "Edge: 5.00%" in content
        assert "APY: 120%" in content
        assert "Liquidity: $100.00" in content

    def test_log_trade_executed(self, logger):
        """Test trade execution logging."""
        logger.log_trade_executed(
            action="BUY",
            market_slug="test-market-YES",
            outcome="YES",
            price=0.05,
            size=200.0,
            amount_usd=10.0
        )

        content = logger.log_file.read_text()
        assert "TRADE EXECUTED: BUY" in content
        assert "Market: test-market-YES" in content
        assert "Price: 5.00%" in content
        assert "Size: 200.0000 shares" in content
        assert "Amount: $10.00" in content

    def test_log_trade_executed_dry_run(self, logger):
        """Test dry run trade logging."""
        logger.log_trade_executed(
            action="BUY",
            market_slug="test-market",
            outcome="YES",
            price=0.05,
            size=200.0,
            amount_usd=10.0,
            dry_run=True
        )

        content = logger.log_file.read_text()
        assert "[DRY RUN] TRADE EXECUTED: BUY" in content

    def test_log_trade_failed(self, logger):
        """Test failed trade logging."""
        logger.log_trade_failed(
            action="BUY",
            market_slug="test-market",
            error="Insufficient balance"
        )

        content = logger.log_file.read_text()
        assert "TRADE FAILED: BUY" in content
        assert "Error: Insufficient balance" in content

    def test_log_position_opened(self, logger):
        """Test position opened logging."""
        position = Position(
            market_slug="test-market-YES",
            outcome="YES",
            tokens=200.0,
            entry_price=0.05,
            entry_size=10.0,
        )

        logger.log_position_opened(position)

        content = logger.log_file.read_text()
        assert "POSITION OPENED" in content
        assert "Market: test-market-YES" in content
        assert "Tokens: 200.0000" in content
        assert "Entry: 5.00%" in content
        assert "Cost: $10.00" in content

    def test_log_position_closed(self, logger):
        """Test position closed logging."""
        position = Position(
            market_slug="test-market-YES",
            entry_price=0.05,
            entry_size=10.0,
            tokens=200.0,
        )

        logger.log_position_closed(position, exit_price=0.08, pnl=6.0)

        content = logger.log_file.read_text()
        assert "POSITION CLOSED" in content
        assert "Entry: 5.00%" in content
        assert "Exit: 8.00%" in content
        assert "P&L: $+6.00" in content

    def test_log_user_confirmed(self, logger):
        """Test user confirmation logging."""
        logger.log_user_confirmed("BUY", "test-market-YES")

        content = logger.log_file.read_text()
        assert "USER CONFIRMED: BUY on test-market-YES" in content

    def test_log_user_rejected(self, logger):
        """Test user rejection logging."""
        logger.log_user_rejected("SELL", "test-market-YES")

        content = logger.log_file.read_text()
        assert "USER REJECTED: SELL on test-market-YES" in content

    def test_log_info(self, logger):
        """Test info logging."""
        logger.log_info("Test info message")

        content = logger.log_file.read_text()
        assert "INFO: Test info message" in content

    def test_log_warning(self, logger):
        """Test warning logging."""
        logger.log_warning("Test warning")

        content = logger.log_file.read_text()
        assert "WARNING: Test warning" in content

    def test_log_error(self, logger):
        """Test error logging."""
        logger.log_error("Test error")

        content = logger.log_file.read_text()
        assert "ERROR: Test error" in content

    def test_timestamp_format(self, logger):
        """Test that logs include timestamps."""
        logger.log_info("Test message")

        content = logger.log_file.read_text()
        # Should have format like [2025-01-14 12:00:00]
        assert "[20" in content  # Year starts with 20
        assert ":" in content  # Time separator


class TestLoggerSingleton:
    """Test logger singleton functions."""

    def test_get_logger_returns_instance(self, tmp_path):
        """Test get_logger returns a BotLogger instance."""
        # Initialize with temp path first
        logger = init_logger(str(tmp_path))
        retrieved = get_logger()

        assert retrieved is logger
        assert isinstance(retrieved, BotLogger)

    def test_init_logger_creates_new(self, tmp_path):
        """Test init_logger creates new instance."""
        logger1 = init_logger(str(tmp_path / "logs1"))
        logger2 = init_logger(str(tmp_path / "logs2"))

        # Should be different instances
        assert logger1.log_file.parent != logger2.log_file.parent
