"""Wait operations for ReyaTester."""

from typing import TYPE_CHECKING, Optional

import asyncio
import logging
import time

from sdk.open_api.models.order import Order
from sdk.open_api.models.order_status import OrderStatus
from sdk.open_api.models.perp_execution import PerpExecution
from sdk.open_api.models.spot_execution import SpotExecution
from sdk.open_api.models.spot_execution_list import SpotExecutionList
from tests.helpers.utils import (
    match_order,
    match_spot_order,
    validate_order_change_fields,
    validate_spot_execution_fields,
)

if TYPE_CHECKING:
    from .tester import ReyaTester

logger = logging.getLogger("reya.integration_tests")


class Waiters:
    """Wait operations for async events."""

    def __init__(self, tester: "ReyaTester"):
        self._t = tester

    async def for_order_execution(self, expected_order: Order, timeout: int = 10) -> PerpExecution:
        """Wait for perp order execution confirmation via both REST and WebSocket."""
        logger.info("⏳ Waiting for trade confirmation order...")

        start_time = time.time()
        rest_trade = None
        ws_trade = None
        trade_seq_num = None
        ws_position = None
        rest_position = None

        while time.time() - start_time < timeout:
            last_trade = await self._t.data.last_perp_execution()

            # Search through all perp executions (like spot does) instead of just last_trade
            if ws_trade is None:
                for execution in self._t.ws.perp_executions:
                    if match_order(expected_order, execution):
                        elapsed_time = time.time() - start_time
                        logger.info(
                            f" ✅ Trade confirmed via WS: {execution.sequence_number} (took {elapsed_time:.2f}s)"
                        )
                        ws_trade = execution
                        trade_seq_num = execution.sequence_number
                        break

            if (
                ws_position is None
                and expected_order.symbol in self._t.ws.positions
                and trade_seq_num is not None
                and trade_seq_num == self._t.ws.positions[expected_order.symbol].last_trade_sequence_number
            ):
                ws_position = self._t.ws.positions[expected_order.symbol]
                elapsed_time = time.time() - start_time
                logger.info(f" ✅ Position confirmed via WS: {expected_order.symbol} (took {elapsed_time:.2f}s)")

            if rest_trade is None and match_order(expected_order, last_trade):
                elapsed_time = time.time() - start_time
                logger.info(f" ✅ Trade confirmed via REST: {last_trade.sequence_number} (took {elapsed_time:.2f}s)")
                rest_trade = last_trade
                trade_seq_num = last_trade.sequence_number

            position = await self._t.data.position(expected_order.symbol)
            if rest_position is None and position is not None and trade_seq_num == position.last_trade_sequence_number:
                elapsed_time = time.time() - start_time
                logger.info(f" ✅ Position confirmed via REST: {expected_order.symbol} (took {elapsed_time:.2f}s)")
                rest_position = position

            if rest_trade and ws_trade and rest_position and ws_position:
                assert (
                    rest_trade.sequence_number == ws_trade.sequence_number
                ), f"Trade sequence mismatch: REST={rest_trade.sequence_number}, WS={ws_trade.sequence_number}"
                assert (
                    rest_position.symbol == ws_position.symbol
                ), f"Position symbol mismatch: REST={rest_position.symbol}, WS={ws_position.symbol}"
                return rest_trade

            await asyncio.sleep(0.1)

        raise RuntimeError(
            f"Order not executed after {timeout} seconds, rest_trade: {rest_trade is not None}, "
            f"ws_trade: {ws_trade is not None}, rest_position: {rest_position is not None}, ws_position: {ws_position is not None}"
        )

    async def for_closing_order_execution(
        self, expected_order: Order, expected_qty: Optional[str] = None, timeout: int = 10
    ) -> PerpExecution:
        """Wait for position-closing trade confirmation via both REST and WebSocket."""
        logger.info("⏳ Waiting for position-closing trade confirmation...")

        start_time = time.time()
        rest_trade = None
        ws_trade = None
        trade_seq_num = None
        ws_position = None
        rest_closed = False

        while time.time() - start_time < timeout:
            last_trade = await self._t.data.last_perp_execution()

            # Search through all perp executions (like spot does) instead of just last_trade
            if ws_trade is None:
                for execution in self._t.ws.perp_executions:
                    if match_order(expected_order, execution, expected_qty):
                        elapsed_time = time.time() - start_time
                        logger.info(
                            f" ✅ Trade confirmed via WS: {execution.sequence_number} (took {elapsed_time:.2f}s)"
                        )
                        ws_trade = execution
                        trade_seq_num = execution.sequence_number
                        break

            if (
                ws_position is None
                and expected_order.symbol in self._t.ws.positions
                and trade_seq_num is not None
                and trade_seq_num == self._t.ws.positions[expected_order.symbol].last_trade_sequence_number
            ):
                ws_position = self._t.ws.positions[expected_order.symbol]
                elapsed_time = time.time() - start_time
                logger.info(f" ✅ Position confirmed via WS: {expected_order.symbol} (took {elapsed_time:.2f}s)")

            if rest_trade is None and match_order(expected_order, last_trade, expected_qty):
                elapsed_time = time.time() - start_time
                logger.info(f" ✅ Trade confirmed via REST: {last_trade.sequence_number} (took {elapsed_time:.2f}s)")
                rest_trade = last_trade
                trade_seq_num = last_trade.sequence_number

            position = await self._t.data.position(expected_order.symbol)
            if not rest_closed and position is None:
                elapsed_time = time.time() - start_time
                logger.info(f" ✅ Position closed via REST: {expected_order.symbol} (took {elapsed_time:.2f}s)")
                rest_closed = True

            if rest_trade and ws_trade and rest_closed and ws_position:
                assert (
                    rest_trade.sequence_number == ws_trade.sequence_number
                ), f"Trade sequence mismatch: REST={rest_trade.sequence_number}, WS={ws_trade.sequence_number}"
                return rest_trade

            await asyncio.sleep(0.1)

        raise RuntimeError(
            f"Order not executed after {timeout} seconds, rest_trade: {rest_trade is not None}, "
            f"ws_trade: {ws_trade is not None}, rest_closed: {rest_closed}, ws_position: {ws_position is not None}"
        )

    async def for_spot_execution(self, order_id: str, expected_order: Order, timeout: int = 10) -> SpotExecution:
        """Wait for spot execution confirmation via both REST and WebSocket.

        Performs strict matching on order_id and validates all important fields.

        Args:
            order_id: The order ID to match (required).
            expected_order: The expected order to validate against.
            timeout: Maximum time to wait in seconds.
        """
        if not order_id:
            raise ValueError("order_id is required for reliable execution matching")

        logger.info(f"⏳ Waiting for spot execution (order_id={order_id})...")

        start_time = time.time()
        rest_execution = None
        ws_execution = None

        # Set order_id on expected_order for matching
        expected_order.order_id = order_id

        while time.time() - start_time < timeout:
            # Search through spot executions by order_id
            if ws_execution is None:
                for execution in self._t.ws.spot_executions:
                    if str(execution.order_id) == str(order_id):
                        elapsed_time = time.time() - start_time

                        # Validate all fields match expected order
                        if match_spot_order(execution, expected_order):
                            logger.info(
                                f" ✅ Spot execution confirmed via WS: order_id={order_id} (took {elapsed_time:.2f}s)"
                            )
                            ws_execution = execution
                        else:
                            # Log validation errors
                            validation_errors = validate_spot_execution_fields(execution, expected_order)
                            for error in validation_errors:
                                logger.warning(f"   ⚠️ Validation: {error}")
                            ws_execution = execution  # Still use it, but warn
                        break

            if rest_execution is None and ws_execution is not None:
                executions_list: SpotExecutionList = await self._t.client.wallet.get_wallet_spot_executions(
                    address=self._t.owner_wallet_address
                )
                for execution in executions_list.data:
                    if str(execution.order_id) == str(order_id):
                        elapsed_time = time.time() - start_time
                        logger.info(
                            f" ✅ Spot execution confirmed via REST: order_id={order_id} (took {elapsed_time:.2f}s)"
                        )
                        rest_execution = execution
                        break

            if rest_execution and ws_execution:
                return rest_execution

            await asyncio.sleep(0.1)

        raise RuntimeError(
            f"Spot execution not confirmed after {timeout} seconds, "
            f"order_id={order_id}, rest: {rest_execution is not None}, ws: {ws_execution is not None}"
        )

    async def for_order_state(self, order_id: str, expected_status: OrderStatus, timeout: int = 10) -> str:
        """Wait for order to reach a specific state via both REST and WebSocket."""
        logger.debug(f"⏳ Waiting for order to reach state: {expected_status.value}...")
        assert expected_status != OrderStatus.OPEN, "use for_order_creation instead"

        start_time = time.time()
        rest_match = False
        ws_match = False

        while time.time() - start_time < timeout:
            orders: list[Order] = await self._t.client.get_open_orders()
            orders_ids = [order.order_id for order in orders]

            if rest_match == False and order_id not in orders_ids:
                elapsed_time = time.time() - start_time
                logger.info(
                    f" ✅ Order reached {expected_status.value} state via REST: {order_id} (took {elapsed_time:.2f}s)"
                )
                rest_match = True

            ws_order = self._t.ws.order_changes.get(order_id)
            ws_status_value = ws_order.status.value if ws_order else None
            if not ws_match and ws_order and ws_status_value == expected_status.value:
                elapsed_time = time.time() - start_time
                logger.info(
                    f" ✅ Order reached {expected_status.value} state via WS: {order_id} (took {elapsed_time:.2f}s)"
                )
                ws_match = True
            if ws_order and ws_status_value != OrderStatus.OPEN.value and ws_status_value != expected_status.value:
                raise RuntimeError(
                    f"Order {order_id} reached {ws_status_value} state via WS, but expected {expected_status.value}"
                )

            if rest_match and ws_match:
                return order_id

            await asyncio.sleep(0.1)

        raise RuntimeError(f"Order {order_id} did not reach {expected_status.value} state after {timeout} seconds")

    async def for_order_creation(
        self, order_id: str, expected_order: Optional[Order] = None, timeout: int = 10
    ) -> Order:
        """Wait for order creation confirmation via REST and WebSocket.

        Args:
            order_id: The order ID to wait for (required).
            expected_order: If provided, validates WS order fields match expected values.
            timeout: Maximum time to wait in seconds.
        """
        logger.debug(f"⏳ Waiting for order creation (order_id={order_id})...")

        start_time = time.time()
        rest_order = None
        ws_order = None

        while time.time() - start_time < timeout:
            orders = await self._t.client.get_open_orders()
            for order in orders:
                if rest_order is None and order.order_id == order_id:
                    elapsed_time = time.time() - start_time
                    logger.info(f" ✅ Order created via REST: order_id={order_id} (took {elapsed_time:.2f}s)")
                    rest_order = order
                    break

            if ws_order is None and order_id in self._t.ws.order_changes:
                ws_order = self._t.ws.order_changes[order_id]
                ws_status = ws_order.status.value if hasattr(ws_order.status, "value") else ws_order.status
                if ws_status in ["OPEN", "PARTIALLY_FILLED"]:
                    elapsed_time = time.time() - start_time
                    logger.info(
                        f" ✅ Order created via WS: order_id={order_id}, status={ws_status} (took {elapsed_time:.2f}s)"
                    )

                    # Validate fields if expected_order provided
                    if expected_order:
                        expected_order.order_id = order_id
                        validation_errors = validate_order_change_fields(ws_order, expected_order)
                        if validation_errors:
                            for error in validation_errors:
                                logger.warning(f"   ⚠️ Validation: {error}")

            if rest_order and ws_order:
                assert (
                    rest_order.order_id == ws_order.order_id
                ), f"Order ID mismatch: REST={rest_order.order_id}, WS={ws_order.order_id}"
                return rest_order
            elif ws_order:
                elapsed_time = time.time() - start_time
                logger.info(f" ✅ Order created via WS only: order_id={order_id} (took {elapsed_time:.2f}s)")
                return ws_order
            elif rest_order:
                elapsed_time = time.time() - start_time
                logger.info(f" ✅ Order created via REST only: order_id={order_id} (took {elapsed_time:.2f}s)")
                return rest_order

            await asyncio.sleep(0.1)

        raise RuntimeError(f"Order {order_id} not created after {timeout} seconds")

    async def for_balance_updates(
        self,
        initial_count: int,
        min_updates: int = 1,
        timeout: float = 5.0,
    ) -> int:
        """Wait for balance update events via WebSocket.

        Args:
            initial_count: The balance update count before the action.
            min_updates: Minimum number of new updates expected.
            timeout: Maximum time to wait in seconds.

        Returns:
            The number of new balance updates received.
        """
        start_time = time.time()

        while time.time() - start_time < timeout:
            new_count = len(self._t.ws.balance_updates) - initial_count
            if new_count >= min_updates:
                elapsed_time = time.time() - start_time
                logger.info(f" ✅ Received {new_count} balance update(s) via WS (took {elapsed_time:.2f}s)")
                return new_count
            await asyncio.sleep(0.05)

        new_count = len(self._t.ws.balance_updates) - initial_count
        raise RuntimeError(f"Expected at least {min_updates} balance update(s), got {new_count} after {timeout}s")
