"""
Pytest configuration for bot tests.

IMPORTANT: All tests use mock objects and NEVER make real API calls.
No real money operations are performed in any test.
"""

import pytest
from unittest.mock import Mock


@pytest.fixture
def safe_mock_client():
    """
    Create a safe mock client for tests that need one.
    All methods return empty/safe values by default.
    """
    client = Mock()
    client.get_address.return_value = "0xTEST_ADDRESS_NOT_REAL"
    client.get_balance.return_value = {"balance": 0}
    client.get_positions.return_value = []
    client.get_orderbook.return_value = {"asks": [], "bids": []}
    client.create_limit_order.return_value = {}
    client.get_orders.return_value = []
    return client
