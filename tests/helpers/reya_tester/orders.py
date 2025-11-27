"""Order operations for ReyaTester."""

from typing import TYPE_CHECKING, Optional
import asyncio
import logging
import time

from sdk.open_api.models.create_order_response import CreateOrderResponse
from sdk.open_api.models.order import Order
from sdk.open_api.models.order_status import OrderStatus
from sdk.open_api.models.side import Side
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api.models import LimitOrderParameters, TriggerOrderParameters

if TYPE_CHECKING:
    from .tester import ReyaTester

logger = logging.getLogger("reya.integration_tests")


class OrderOperations:
    """Order creation and management operations."""

    def __init__(self, tester: "ReyaTester"):
        self._t = tester

    async def create_limit(self, params: LimitOrderParameters) -> Optional[str]:
        """Create a limit order with the specified parameters."""
        side_text = "BUY" if params.is_buy else "SELL"
        time_in_force_text = "IOC" if params.time_in_force == TimeInForce.IOC else "GTC"

        logger.info(
            f"📤 Creating {time_in_force_text} {side_text} order: symbol={params.symbol}, price=${params.limit_px}, qty={params.qty}"
        )

        response = await self._t.client.create_limit_order(params)
        logger.info(f"Response: {response}")

        return response.order_id

    async def create_trigger(self, params: TriggerOrderParameters) -> CreateOrderResponse:
        """Create a trigger order (TP/SL) with the specified parameters."""
        side_text = "BUY" if params.is_buy else "SELL"
        trigger_type_text = params.trigger_type.value

        logger.info(
            f"📤 Creating {trigger_type_text} {side_text} order: symbol={params.symbol}, trigger_px=${params.trigger_px}"
        )

        response = await self._t.client.create_trigger_order(params)

        logger.info(f"✅ {trigger_type_text} {side_text} order created with ID: {response.order_id}")
        return response

    async def cancel(self, order_id: str, symbol: str, account_id: int) -> None:
        """Cancel a specific order."""
        await self._t.client.cancel_order(
            order_id=order_id,
            symbol=symbol,
            account_id=account_id
        )

    async def close_all(self, fail_if_none: bool = True) -> None:
        """Cancel all active orders."""
        active_orders: list[Order] = await self._t.client.get_open_orders()

        if active_orders is None or len(active_orders) == 0:
            logger.warning("No active orders to close")
            if fail_if_none:
                assert False
            return None

        cancelled_order_id = None
        for order in active_orders:
            try:
                await self._t.client.cancel_order(
                    order_id=order.order_id,
                    symbol=order.symbol,
                    account_id=order.account_id
                )

                cancelled_order_id = await self._t.wait.for_order_state(
                    order_id=order.order_id, expected_status=OrderStatus.CANCELLED, timeout=10
                )
            except Exception as e:
                logger.warning(f"Failed to cancel order {order.order_id}: {e}")
                continue

        if fail_if_none and cancelled_order_id is None:
            assert False, "Failed to close position"
