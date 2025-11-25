"""Utility functions for test helpers."""

from typing import Optional

from sdk.open_api.models.order import Order
from sdk.open_api.models.perp_execution import PerpExecution
from sdk.open_api.models.spot_execution import SpotExecution


def match_order(expected_order: Order, order_output: PerpExecution, expected_qty: Optional[str] = None):
    basic_match = (
        order_output.account_id == expected_order.account_id
        and order_output.symbol == expected_order.symbol
        and order_output.side == expected_order.side
    )

    if not basic_match:
        return False

    # For trigger orders (TP/SL), compare executed qty to WebSocket position qty
    if expected_qty:
        return expected_qty == order_output.qty

    # For regular orders, compare with expected order qty
    return order_output.qty == expected_order.qty


def match_spot_order(expected_order: Order, spot_execution: SpotExecution, expected_qty: Optional[str] = None):
    """Match a spot order against a spot execution"""
    basic_match = (
        spot_execution.account_id == expected_order.account_id
        and spot_execution.symbol == expected_order.symbol
        and spot_execution.side == expected_order.side
    )

    if not basic_match:
        return False

    # Compare quantity
    if expected_qty:
        return expected_qty == spot_execution.qty

    return spot_execution.qty == expected_order.qty
