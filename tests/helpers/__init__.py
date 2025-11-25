"""Test helpers for Reya Python SDK integration tests."""

# Legacy imports (for backwards compatibility during migration)
from .reya_tester import ReyaTester

# New modular imports
from .config import TestConfig, get_test_config
from .builders import OrderBuilder
from .clients import RestClient, WebSocketClient
from .waiters import EventWaiter
from .assertions import (
    assert_order_created,
    assert_order_filled,
    assert_order_cancelled,
    assert_no_open_orders,
    assert_position,
    assert_position_closed,
    assert_position_changes,
    assert_balance,
    assert_balance_change,
    assert_spot_trade_balance_changes,
)

__all__ = [
    # Legacy (for backwards compatibility)
    "ReyaTester",
    # Configuration
    "TestConfig",
    "get_test_config",
    # Builders
    "OrderBuilder",
    # Clients
    "RestClient",
    "WebSocketClient",
    # Waiters
    "EventWaiter",
    # Assertions
    "assert_order_created",
    "assert_order_filled",
    "assert_order_cancelled",
    "assert_no_open_orders",
    "assert_position",
    "assert_position_closed",
    "assert_position_changes",
    "assert_balance",
    "assert_balance_change",
    "assert_spot_trade_balance_changes",
]
