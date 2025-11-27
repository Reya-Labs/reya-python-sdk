"""Utility functions for ReyaTester."""

from sdk.open_api.models.order import Order
from sdk.open_api.models.order_status import OrderStatus
from sdk.open_api.models.order_type import OrderType
from sdk.open_api.models.side import Side
from sdk.reya_rest_api.models import LimitOrderParameters, TriggerOrderParameters


def limit_order_params_to_order(params: LimitOrderParameters, account_id: int) -> Order:
    """Convert LimitOrderParameters to Order object for testing"""
    return Order(
        exchange_id=5,  # REYA_DEX_ID
        symbol=params.symbol,
        account_id=account_id,
        order_id="",  # Will be set when order is created
        qty=params.qty,
        exec_qty="0",
        side=Side.B if params.is_buy else Side.A,
        limit_px=params.limit_px,
        order_type=OrderType.LIMIT,
        trigger_px=None,
        time_in_force=params.time_in_force,
        reduce_only=params.reduce_only or False,
        status=OrderStatus.OPEN,
        created_at=0,
        last_update_at=0,
    )


def trigger_order_params_to_order(params: TriggerOrderParameters, account_id: int) -> Order:
    """Convert TriggerOrderParameters to Order object for testing"""
    return Order(
        exchange_id=5,  # REYA_DEX_ID
        symbol=params.symbol,
        account_id=account_id,
        order_id="",  # Will be set when order is created
        qty=None,  # Trigger orders don't have qty until execution
        exec_qty="0",
        side=Side.B if params.is_buy else Side.A,
        limit_px=params.trigger_px,
        order_type=params.trigger_type,
        trigger_px=params.trigger_px,
        time_in_force=None,
        reduce_only=False,
        status=OrderStatus.OPEN,
        created_at=0,
        last_update_at=0,
    )
