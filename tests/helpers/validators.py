"""
Shared validation functions for order and execution response fields.

These validators ensure API responses have correct structure and data types.
They are automatically called by wait.for_order_creation() and wait.for_spot_execution().
"""

import logging
from decimal import Decimal, InvalidOperation
from typing import Optional, Union

from sdk.async_api.order import Order as AsyncOrder
from sdk.async_api.spot_execution import SpotExecution as AsyncSpotExecution
from sdk.open_api.models.order import Order
from sdk.open_api.models.spot_execution import SpotExecution

logger = logging.getLogger("reya.integration_tests")


def _is_numeric_string(value: str) -> bool:
    """Check if a string represents a valid numeric value."""
    try:
        Decimal(value)
        return True
    except (InvalidOperation, ValueError):
        return False


def validate_order_fields(
    order: Union[Order, AsyncOrder],
    expected_symbol: Optional[str] = None,
    is_gtc: bool = True,
    log_details: bool = False,
) -> None:
    """
    Validate all required order fields have correct types and values.

    Args:
        order: The order to validate (REST or WS order model)
        expected_symbol: Expected symbol to match (optional)
        is_gtc: Whether this is a GTC order (affects time_in_force validation)
        log_details: Whether to log each validated field

    Raises:
        AssertionError: If any field validation fails
    """
    # exchange_id
    assert hasattr(order, "exchange_id"), "Order should have 'exchange_id'"
    assert isinstance(order.exchange_id, int), f"exchange_id should be int, got {type(order.exchange_id)}"
    if log_details:
        logger.info(f"✅ exchange_id: {order.exchange_id}")

    # symbol
    assert hasattr(order, "symbol"), "Order should have 'symbol'"
    assert isinstance(order.symbol, str), f"symbol should be str, got {type(order.symbol)}"
    if expected_symbol:
        assert order.symbol == expected_symbol, f"Expected symbol {expected_symbol}, got {order.symbol}"
    if log_details:
        logger.info(f"✅ symbol: {order.symbol}")

    # account_id
    assert hasattr(order, "account_id"), "Order should have 'account_id'"
    assert isinstance(order.account_id, int), f"account_id should be int, got {type(order.account_id)}"
    if log_details:
        logger.info(f"✅ account_id: {order.account_id}")

    # order_id
    assert hasattr(order, "order_id"), "Order should have 'order_id'"
    assert isinstance(order.order_id, str), f"order_id should be str, got {type(order.order_id)}"
    assert len(order.order_id) > 0, "order_id should not be empty"
    if log_details:
        logger.info(f"✅ order_id: {order.order_id}")

    # qty (can be None for trigger orders)
    assert hasattr(order, "qty"), "Order should have 'qty'"
    if order.qty is not None:
        assert isinstance(order.qty, str), f"qty should be str, got {type(order.qty)}"
        assert _is_numeric_string(order.qty), f"qty should be numeric: {order.qty}"
        if log_details:
            logger.info(f"✅ qty: {order.qty}")

    # side
    assert hasattr(order, "side"), "Order should have 'side'"
    assert order.side is not None, "side should not be None"
    assert hasattr(order.side, "value"), f"side should be an enum, got {type(order.side)}"
    if log_details:
        logger.info(f"✅ side: {order.side}")

    # limit_px
    assert hasattr(order, "limit_px"), "Order should have 'limit_px'"
    if order.limit_px is not None:
        assert isinstance(order.limit_px, str), f"limit_px should be str, got {type(order.limit_px)}"
        assert _is_numeric_string(order.limit_px), f"limit_px should be numeric: {order.limit_px}"
        if log_details:
            logger.info(f"✅ limit_px: {order.limit_px}")

    # order_type
    assert hasattr(order, "order_type"), "Order should have 'order_type'"
    assert order.order_type is not None, "order_type should not be None"
    assert hasattr(order.order_type, "value"), f"order_type should be an enum, got {type(order.order_type)}"
    if log_details:
        logger.info(f"✅ order_type: {order.order_type}")

    # time_in_force (for GTC orders)
    if is_gtc:
        assert hasattr(order, "time_in_force"), "Order should have 'time_in_force'"
        if order.time_in_force is not None:
            assert hasattr(order.time_in_force, "value"), (
                f"time_in_force should be an enum, got {type(order.time_in_force)}"
            )
            if log_details:
                logger.info(f"✅ time_in_force: {order.time_in_force}")

    # status
    assert hasattr(order, "status"), "Order should have 'status'"
    assert order.status is not None, "status should not be None"
    assert hasattr(order.status, "value"), f"status should be an enum, got {type(order.status)}"
    if log_details:
        logger.info(f"✅ status: {order.status}")

    # created_at
    assert hasattr(order, "created_at"), "Order should have 'created_at'"
    assert isinstance(order.created_at, int), f"created_at should be int, got {type(order.created_at)}"
    assert order.created_at > 0, f"created_at should be positive timestamp, got {order.created_at}"
    if log_details:
        logger.info(f"✅ created_at: {order.created_at}")

    # last_update_at
    assert hasattr(order, "last_update_at"), "Order should have 'last_update_at'"
    assert isinstance(order.last_update_at, int), f"last_update_at should be int, got {type(order.last_update_at)}"
    assert order.last_update_at > 0, f"last_update_at should be positive timestamp, got {order.last_update_at}"
    if log_details:
        logger.info(f"✅ last_update_at: {order.last_update_at}")


def validate_spot_execution_fields(
    execution: Union[SpotExecution, AsyncSpotExecution],
    expected_symbol: Optional[str] = None,
    log_details: bool = False,
) -> None:
    """
    Validate all required spot execution fields have correct types and values.

    Args:
        execution: The spot execution to validate (REST or WS model)
        expected_symbol: Expected symbol to match (optional)
        log_details: Whether to log each validated field

    Raises:
        AssertionError: If any field validation fails
    """
    # exchange_id (optional)
    if execution.exchange_id is not None:
        assert isinstance(execution.exchange_id, int), (
            f"exchange_id should be int, got {type(execution.exchange_id)}"
        )
        if log_details:
            logger.info(f"✅ exchange_id: {execution.exchange_id}")

    # symbol
    assert hasattr(execution, "symbol"), "Execution should have 'symbol'"
    assert isinstance(execution.symbol, str), f"symbol should be str, got {type(execution.symbol)}"
    if expected_symbol:
        assert execution.symbol == expected_symbol, f"Expected symbol {expected_symbol}, got {execution.symbol}"
    if log_details:
        logger.info(f"✅ symbol: {execution.symbol}")

    # account_id
    assert hasattr(execution, "account_id"), "Execution should have 'account_id'"
    assert isinstance(execution.account_id, int), f"account_id should be int, got {type(execution.account_id)}"
    if log_details:
        logger.info(f"✅ account_id: {execution.account_id}")

    # maker_account_id
    assert hasattr(execution, "maker_account_id"), "Execution should have 'maker_account_id'"
    assert isinstance(execution.maker_account_id, int), (
        f"maker_account_id should be int, got {type(execution.maker_account_id)}"
    )
    if log_details:
        logger.info(f"✅ maker_account_id: {execution.maker_account_id}")

    # order_id (optional)
    if execution.order_id is not None:
        assert isinstance(execution.order_id, str), f"order_id should be str, got {type(execution.order_id)}"
        if log_details:
            logger.info(f"✅ order_id: {execution.order_id}")

    # maker_order_id (optional)
    if execution.maker_order_id is not None:
        assert isinstance(execution.maker_order_id, str), (
            f"maker_order_id should be str, got {type(execution.maker_order_id)}"
        )
        if log_details:
            logger.info(f"✅ maker_order_id: {execution.maker_order_id}")

    # side
    assert hasattr(execution, "side"), "Execution should have 'side'"
    assert execution.side is not None, "side should not be None"
    assert hasattr(execution.side, "value"), f"side should be an enum, got {type(execution.side)}"
    if log_details:
        logger.info(f"✅ side: {execution.side}")

    # qty
    assert hasattr(execution, "qty"), "Execution should have 'qty'"
    assert isinstance(execution.qty, str), f"qty should be str, got {type(execution.qty)}"
    assert _is_numeric_string(execution.qty), f"qty should be numeric: {execution.qty}"
    assert Decimal(execution.qty) > 0, f"qty should be positive: {execution.qty}"
    if log_details:
        logger.info(f"✅ qty: {execution.qty}")

    # price
    assert hasattr(execution, "price"), "Execution should have 'price'"
    assert isinstance(execution.price, str), f"price should be str, got {type(execution.price)}"
    assert _is_numeric_string(execution.price), f"price should be numeric: {execution.price}"
    assert Decimal(execution.price) > 0, f"price should be positive: {execution.price}"
    if log_details:
        logger.info(f"✅ price: {execution.price}")

    # fee
    assert hasattr(execution, "fee"), "Execution should have 'fee'"
    assert isinstance(execution.fee, str), f"fee should be str, got {type(execution.fee)}"
    assert _is_numeric_string(execution.fee), f"fee should be numeric: {execution.fee}"
    if log_details:
        logger.info(f"✅ fee: {execution.fee}")

    # type
    assert hasattr(execution, "type"), "Execution should have 'type'"
    assert execution.type is not None, "type should not be None"
    assert hasattr(execution.type, "value"), f"type should be an enum, got {type(execution.type)}"
    if log_details:
        logger.info(f"✅ type: {execution.type}")

    # timestamp
    assert hasattr(execution, "timestamp"), "Execution should have 'timestamp'"
    assert isinstance(execution.timestamp, int), f"timestamp should be int, got {type(execution.timestamp)}"
    assert execution.timestamp > 0, f"timestamp should be positive: {execution.timestamp}"
    if log_details:
        logger.info(f"✅ timestamp: {execution.timestamp}")
