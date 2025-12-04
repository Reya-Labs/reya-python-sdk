"""Utility functions for test helpers."""

from typing import Any, Optional, Union

import logging

from sdk.async_api.spot_execution import SpotExecution as AsyncSpotExecution
from sdk.open_api.models.order import Order
from sdk.open_api.models.perp_execution import PerpExecution
from sdk.open_api.models.spot_execution import SpotExecution

logger = logging.getLogger("reya.integration_tests")


def _get_enum_value(val: Any) -> str:
    """Extract string value from enum or return string as-is."""
    if val is None:
        return ""
    return val.value if hasattr(val, "value") else str(val)


def _compare_qty(qty1: Optional[str], qty2: Optional[str]) -> bool:
    """Compare quantities with tolerance for floating point differences."""
    if qty1 is None or qty2 is None:
        return qty1 == qty2
    try:
        return abs(float(qty1) - float(qty2)) < 1e-9
    except (ValueError, TypeError):
        return qty1 == qty2


def match_order(expected: Order, execution: PerpExecution, expected_qty: Optional[str] = None) -> bool:
    """Match a perp execution against expected order values.

    Note: PerpExecution does NOT have order_id, so we match by:
    - account_id, symbol, side, qty

    Args:
        expected: The expected order to match against.
        execution: The perp execution to check.
        expected_qty: Optional override for qty comparison (used for closing orders).
    """
    if execution.account_id != expected.account_id:
        return False
    if execution.symbol != expected.symbol:
        return False
    if _get_enum_value(execution.side) != _get_enum_value(expected.side):
        return False
    # Use expected_qty if provided, otherwise use expected.qty
    qty_to_match = expected_qty if expected_qty is not None else expected.qty
    if not _compare_qty(execution.qty, qty_to_match):
        return False
    return True


def match_spot_order(order: Union[SpotExecution, AsyncSpotExecution], expected: Order) -> bool:
    """Match a spot execution against expected values.

    Performs strict matching on ALL important fields:
    - order_id, account_id, symbol, side, qty

    Handles both open_api.SpotExecution and async_api.SpotExecution types.
    """
    if str(order.order_id) != str(expected.order_id):
        return False
    if order.account_id != expected.account_id:
        return False
    if order.symbol != expected.symbol:
        return False
    if _get_enum_value(order.side) != _get_enum_value(expected.side):
        return False
    if not _compare_qty(order.qty, expected.qty):
        return False
    return True


def validate_spot_execution_fields(order: Union[SpotExecution, AsyncSpotExecution], expected: Order) -> list[str]:
    """Validate all key fields of a spot execution against expected values.

    Returns list of validation error messages (empty if all valid).
    """
    errors = []

    if str(order.order_id) != str(expected.order_id):
        errors.append(f"order_id mismatch: expected {expected.order_id}, got {order.order_id}")
    if order.account_id != expected.account_id:
        errors.append(f"account_id mismatch: expected {expected.account_id}, got {order.account_id}")
    if order.symbol != expected.symbol:
        errors.append(f"symbol mismatch: expected {expected.symbol}, got {order.symbol}")

    exec_side = _get_enum_value(order.side)
    expected_side = _get_enum_value(expected.side)
    if exec_side != expected_side:
        errors.append(f"side mismatch: expected {expected_side}, got {exec_side}")

    if not _compare_qty(order.qty, expected.qty):
        errors.append(f"qty mismatch: expected {expected.qty}, got {order.qty}")

    return errors


def validate_order_change_fields(order: Any, expected: Order) -> list[str]:
    """Validate all key fields of a WebSocket order change against expected values.

    Returns list of validation error messages (empty if all valid).
    """
    errors = []

    if str(order.order_id) != str(expected.order_id):
        errors.append(f"order_id mismatch: expected {expected.order_id}, got {order.order_id}")
    if order.account_id != expected.account_id:
        errors.append(f"account_id mismatch: expected {expected.account_id}, got {order.account_id}")
    if order.symbol != expected.symbol:
        errors.append(f"symbol mismatch: expected {expected.symbol}, got {order.symbol}")

    order_side = _get_enum_value(order.side)
    expected_side = _get_enum_value(expected.side)
    if order_side != expected_side:
        errors.append(f"side mismatch: expected {expected_side}, got {order_side}")

    if expected.qty and hasattr(order, "qty") and order.qty:
        if not _compare_qty(order.qty, expected.qty):
            errors.append(f"qty mismatch: expected {expected.qty}, got {order.qty}")

    if expected.order_type and hasattr(order, "order_type") and order.order_type:
        order_type = _get_enum_value(order.order_type)
        expected_type = _get_enum_value(expected.order_type)
        if order_type != expected_type:
            errors.append(f"order_type mismatch: expected {expected_type}, got {order_type}")

    if expected.time_in_force and hasattr(order, "time_in_force") and order.time_in_force:
        order_tif = _get_enum_value(order.time_in_force)
        expected_tif = _get_enum_value(expected.time_in_force)
        if order_tif != expected_tif:
            errors.append(f"time_in_force mismatch: expected {expected_tif}, got {order_tif}")

    if expected.limit_px and hasattr(order, "limit_px") and order.limit_px:
        try:
            if abs(float(order.limit_px) - float(expected.limit_px)) > 1e-6:
                errors.append(f"limit_px mismatch: expected {expected.limit_px}, got {order.limit_px}")
        except (ValueError, TypeError):
            pass

    return errors
