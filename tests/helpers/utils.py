"""Utility functions for test helpers."""

from typing import Any, Optional, Union

from sdk.open_api.models.order import Order
from sdk.open_api.models.perp_execution import PerpExecution
from sdk.open_api.models.spot_execution import SpotExecution
from sdk.async_api.spot_execution import SpotExecution as AsyncSpotExecution


def _get_enum_value(val: Any) -> str:
    """Extract string value from enum or return string as-is."""
    return val.value if hasattr(val, 'value') else str(val)


def match_order(expected_order: Order, order_output: PerpExecution, expected_qty: Optional[str] = None):
    # Compare enum values (strings) since async_api and open_api have different enum classes
    output_side = _get_enum_value(order_output.side)
    expected_side = _get_enum_value(expected_order.side)
    
    basic_match = (
        order_output.account_id == expected_order.account_id
        and order_output.symbol == expected_order.symbol
        and output_side == expected_side
    )

    if not basic_match:
        return False

    # For trigger orders (TP/SL), compare executed qty to WebSocket position qty
    if expected_qty:
        return expected_qty == order_output.qty

    # For regular orders, compare with expected order qty
    return order_output.qty == expected_order.qty


def match_spot_order(
    expected_order: Order,
    spot_execution: Union[SpotExecution, AsyncSpotExecution],
    expected_qty: Optional[str] = None
):
    """Match a spot order against a spot execution.
    
    Handles both open_api.SpotExecution and async_api.SpotExecution types
    by comparing enum values as strings.
    """
    # Compare enum values (strings) since async_api and open_api have different enum classes
    exec_side = _get_enum_value(spot_execution.side)
    expected_side = _get_enum_value(expected_order.side)
    
    basic_match = (
        spot_execution.account_id == expected_order.account_id
        and spot_execution.symbol == expected_order.symbol
        and exec_side == expected_side
    )

    if not basic_match:
        return False

    # Compare quantity
    if expected_qty:
        return expected_qty == spot_execution.qty

    return spot_execution.qty == expected_order.qty
