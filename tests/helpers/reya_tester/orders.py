"""Order operations for ReyaTester."""

from typing import TYPE_CHECKING, Optional

import asyncio
import logging
import os

from sdk.open_api.exceptions import ApiException
from sdk.open_api.models.create_order_response import CreateOrderResponse
from sdk.open_api.models.order import Order
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api.models import LimitOrderParameters, TriggerOrderParameters

from .retry import with_retry

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

        response = await with_retry(
            lambda: self._t.client.create_limit_order(params),
            max_retries=3,
            retry_delay=1.0,
            operation_name=f"create_limit_order({params.symbol})",
        )
        logger.info(f"Response: {response}")

        return response.order_id

    async def create_trigger(self, params: TriggerOrderParameters) -> CreateOrderResponse:
        """Create a trigger order (TP/SL) with the specified parameters."""
        side_text = "BUY" if params.is_buy else "SELL"
        trigger_type_text = params.trigger_type.value

        logger.info(
            f"📤 Creating {trigger_type_text} {side_text} order: symbol={params.symbol}, trigger_px=${params.trigger_px}"
        )

        response = await with_retry(
            lambda: self._t.client.create_trigger_order(params),
            max_retries=3,
            retry_delay=1.0,
            operation_name=f"create_trigger_order({params.symbol})",
        )

        logger.info(f"✅ {trigger_type_text} {side_text} order created with ID: {response.order_id}")
        return response

    async def cancel(self, order_id: str, symbol: str, account_id: int) -> None:
        """Cancel a specific order."""
        await self._t.client.cancel_order(order_id=order_id, symbol=symbol, account_id=account_id)

    async def close_all(self, fail_if_none: bool = True, wait_for_confirmation: bool = False) -> None:
        """Cancel all active orders.

        Uses mass_cancel for spot markets to avoid nonce race conditions.

        Args:
            fail_if_none: If True, assert failure when no orders to close
            wait_for_confirmation: If True, wait for cancellation confirmation (slower but safer)
        
        Note:
            Set SPOT_PRESERVE_ACCOUNT1_ORDERS=true to skip order cancellation for SPOT_ACCOUNT_ID_1.
            This is useful when testing with external liquidity from a depth script.
        """
        # Check if we should preserve orders for SPOT account 1
        preserve_account1 = os.getenv("SPOT_PRESERVE_ACCOUNT1_ORDERS", "").lower() == "true"
        if preserve_account1 and self._t._spot_account_number == 1:
            logger.info("⚠️ SPOT_PRESERVE_ACCOUNT1_ORDERS=true: Skipping close_all for SPOT account 1")
            return None

        active_orders: list[Order] = await self._t.client.get_open_orders()

        if active_orders is None or len(active_orders) == 0:
            logger.warning("No active orders to close")
            if fail_if_none:
                assert False
            return None

        # Group orders by symbol for mass cancel
        orders_by_symbol: dict[str, list[Order]] = {}
        for order in active_orders:
            if order.symbol not in orders_by_symbol:
                orders_by_symbol[order.symbol] = []
            orders_by_symbol[order.symbol].append(order)

        cancelled_count = 0
        for symbol, orders in orders_by_symbol.items():
            try:
                # Use mass_cancel for spot markets (single nonce per symbol)
                if not symbol.upper().endswith("PERP"):
                    await self._t.client.mass_cancel(symbol=symbol, account_id=self._t.account_id)
                    cancelled_count += len(orders)
                    logger.info(f"Mass cancelled {len(orders)} orders for {symbol}")
                else:
                    # For perp markets, cancel individually
                    for order in orders:
                        try:
                            await self._t.client.cancel_order(
                                order_id=order.order_id, symbol=order.symbol, account_id=order.account_id
                            )
                            cancelled_count += 1
                        except ApiException as e:
                            logger.warning(f"Failed to cancel order {order.order_id}: {e}")
            except ApiException as e:
                logger.warning(f"Failed to mass cancel orders for {symbol}: {e}")
                # Fall back to individual cancellation
                for order in orders:
                    try:
                        await self._t.client.cancel_order(
                            order_id=order.order_id, symbol=order.symbol, account_id=order.account_id
                        )
                        cancelled_count += 1
                    except ApiException as e2:
                        logger.warning(f"Failed to cancel order {order.order_id}: {e2}")

        if fail_if_none and cancelled_count == 0:
            assert False, "Failed to close any orders"

        # Brief pause to let cancellations propagate
        if cancelled_count > 0 and not wait_for_confirmation:
            await asyncio.sleep(0.2)
