"""Utility functions for ReyaTester."""

from sdk.open_api.models.order import Order
from sdk.open_api.models.order_status import OrderStatus
from sdk.open_api.models.order_type import OrderType
from sdk.open_api.models.side import Side
from sdk.reya_rest_api.models import LimitOrderParameters, TriggerOrderParameters


def limit_order_params_to_order(params: LimitOrderParameters, account_id: int) -> Order:
    """Convert LimitOrderParameters to Order object for testing"""
    return Order(
        exchangeId=5,  # REYA_DEX_ID
        symbol=params.symbol,
        accountId=account_id,
        orderId="",  # Will be set when order is created
        qty=params.qty,
        execQty="0",
        side=Side.B if params.is_buy else Side.A,
        limitPx=params.limit_px,
        orderType=OrderType.LIMIT,
        triggerPx=None,
        timeInForce=params.time_in_force,
        reduceOnly=params.reduce_only or False,
        status=OrderStatus.OPEN,
        createdAt=0,
        lastUpdateAt=0,
    )


def trigger_order_params_to_order(params: TriggerOrderParameters, account_id: int) -> Order:
    """Convert TriggerOrderParameters to Order object for testing"""
    return Order(
        exchangeId=5,  # REYA_DEX_ID
        symbol=params.symbol,
        accountId=account_id,
        orderId="",  # Will be set when order is created
        qty=None,  # Trigger orders don't have qty until execution
        execQty="0",
        side=Side.B if params.is_buy else Side.A,
        limitPx=params.trigger_px,
        orderType=params.trigger_type,
        triggerPx=params.trigger_px,
        timeInForce=None,
        reduceOnly=False,
        status=OrderStatus.OPEN,
        createdAt=0,
        lastUpdateAt=0,
    )
