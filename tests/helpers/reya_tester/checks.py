"""Assertion/check operations for ReyaTester."""

from typing import TYPE_CHECKING, Optional, Union

import asyncio
import logging
import os

import pytest

from sdk.async_api.order import Order as AsyncOrder
from sdk.open_api.exceptions import ApiException
from sdk.open_api.models.account_balance import AccountBalance
from sdk.open_api.models.execution_type import ExecutionType
from sdk.open_api.models.order import Order
from sdk.open_api.models.order_status import OrderStatus
from sdk.open_api.models.order_type import OrderType
from sdk.open_api.models.perp_execution import PerpExecution
from sdk.open_api.models.side import Side
from sdk.open_api.models.spot_execution import SpotExecution

if TYPE_CHECKING:
    from .tester import ReyaTester

logger = logging.getLogger("reya.integration_tests")


class Checks:
    """Assertion operations for verifying test state."""

    def __init__(self, tester: "ReyaTester"):
        self._t = tester

    async def open_order_created(self, order_id: str, expected_order: Order) -> None:
        """Verify an open order was created with expected values."""
        open_order: Optional[Union[Order, AsyncOrder]] = await self._t.data.open_order(order_id)

        # For trigger orders (SL/TP), if not found in open orders, check WebSocket
        if open_order is None and expected_order.order_type in [OrderType.SL, OrderType.TP]:
            ws_order = self._t.ws.orders.get(str(order_id))
            if ws_order:
                open_order = ws_order
                logger.info(f"✅ Trigger order found via WebSocket: {open_order}")

        assert (
            open_order is not None
        ), f"check_open_order_created: Order {order_id} was not found in open orders or WebSocket"
        assert open_order.order_id == order_id, "check_open_order_created: Wrong order id"

        if expected_order.order_type == OrderType.LIMIT:
            assert float(open_order.limit_px) == float(
                expected_order.limit_px
            ), f"check_open_order_created: Wrong limit price. Expected: {expected_order.limit_px}, Got: {open_order.limit_px}"
            assert open_order.qty is not None and expected_order.qty is not None
            assert float(open_order.qty) == float(
                expected_order.qty
            ), f"check_open_order_created: Wrong qty. Expected: {expected_order.qty}, Got: {open_order.qty}"
        else:
            expected_trigger_px = expected_order.trigger_px if expected_order.trigger_px else expected_order.limit_px
            assert open_order.trigger_px is not None
            assert float(open_order.trigger_px) == pytest.approx(
                float(expected_trigger_px), rel=1e-6
            ), f"check_open_order_created: Wrong trigger price. Expected: {expected_trigger_px}, Got: {open_order.trigger_px}"
            assert open_order.qty is None, "check_open_order_created: Has qty"

        assert open_order.order_type == expected_order.order_type, "check_open_order_created: Wrong order type"
        assert open_order.side == expected_order.side, "check_open_order_created: Wrong order direction"
        assert open_order.status == OrderStatus.OPEN, "check_open_order_created: Wrong order status"

    async def no_open_orders(self) -> None:
        """Assert no open orders exist.
        
        Note:
            Set SPOT_PRESERVE_ACCOUNT1_ORDERS=true to skip this check for SPOT_ACCOUNT_ID_1.
            This is useful when testing with external liquidity from a depth script.
        """
        # Check if we should preserve orders for SPOT account 1
        preserve_account1 = os.getenv("SPOT_PRESERVE_ACCOUNT1_ORDERS", "").lower() == "true"
        if preserve_account1 and self._t._spot_account_number == 1:
            logger.info("⚠️ SPOT_PRESERVE_ACCOUNT1_ORDERS=true: Skipping no_open_orders check for SPOT account 1")
            return

        open_orders = await self._t.client.get_open_orders()
        if len(open_orders) == 0:
            return

        logger.warning(
            f"check_no_open_orders: Found {len(open_orders)} open orders from database, checking if they're stale:"
        )

        legitimate_orders = []
        for order in open_orders:
            logger.warning(f"  - Checking order ID: {order.order_id}, Symbol: {order.symbol}, Status: {order.status}")
            try:
                await self._t.client.cancel_order(
                    order_id=order.order_id, symbol=order.symbol, account_id=order.account_id
                )
                logger.warning(f"Order {order.order_id} exists in matching engine, waiting for cancellation...")
                legitimate_orders.append(order)
            except ApiException as e:
                if "Missing order" in str(e):
                    logger.info(f"Order {order.order_id} is stale (doesn't exist in matching engine), ignoring")
                else:
                    logger.warning(f"Unexpected error cancelling order {order.order_id}: {e}")
                    legitimate_orders.append(order)

        if len(legitimate_orders) == 0:
            logger.info("check_no_open_orders: All orders are stale, test can proceed")
            return

        logger.warning(f"Waiting for {len(legitimate_orders)} legitimate orders to be cancelled...")
        await asyncio.sleep(0.05)

        remaining_orders = await self._t.client.get_open_orders()
        remaining_legitimate = []
        for order in remaining_orders:
            try:
                await self._t.client.cancel_order(
                    order_id=order.order_id, symbol=order.symbol, account_id=order.account_id
                )
            except ApiException as e:
                if "Missing order" not in str(e):
                    remaining_legitimate.append(order)

        if len(remaining_legitimate) > 0:
            logger.error(f"check_no_open_orders: Still found {len(remaining_legitimate)} legitimate open orders:")
            for order in remaining_legitimate:
                logger.error(f"  - Order ID: {order.order_id}, Symbol: {order.symbol}, Status: {order.status}")
            assert False, "check_no_open_orders: Open orders should be empty"
        else:
            logger.info("check_no_open_orders: All legitimate orders cleaned up successfully")

    async def position(
        self,
        symbol: str,
        expected_exchange_id: int,
        expected_account_id: int,
        expected_qty: str,
        expected_side: Side,
        expected_avg_entry_price: Optional[str] = None,
        expected_last_trade_sequence_number: Optional[int] = None,
    ) -> None:
        """Verify position exists with expected values."""
        pos = await self._t.data.position(symbol)
        if pos is None:
            raise RuntimeError("check_position: Position not found")

        if expected_exchange_id is not None:
            assert pos.exchange_id == expected_exchange_id, "check_position: Exchange ID does not match"
        if expected_account_id is not None:
            assert pos.account_id == expected_account_id, "check_position: Account ID does not match"
        if expected_qty is not None:
            assert pos.qty == expected_qty, "check_position: Qty does not match"
        if expected_side is not None:
            assert pos.side == expected_side, "check_position: Side does not match"
        if expected_avg_entry_price is not None:
            assert float(pos.avg_entry_price) == pytest.approx(
                float(expected_avg_entry_price), rel=1e-6
            ), "check_position: Average entry price does not match"
        if expected_last_trade_sequence_number is not None:
            assert (
                pos.last_trade_sequence_number == expected_last_trade_sequence_number
            ), "check_position: Last trade sequence number does not match"

    async def position_not_open(self, symbol: str) -> None:
        """Assert position is closed via both REST and WebSocket."""
        pos = await self._t.data.position(symbol)
        assert pos is None, "check_position_not_open: Position should be empty"

        ws_position = self._t.ws.positions.get(symbol)
        assert (
            ws_position is None or ws_position.qty == "0"
        ), "check_position_not_open: WebSocket position should be empty"

    async def order_execution(
        self, order_execution: PerpExecution, expected_order: Order, expected_qty: Optional[str] = None
    ) -> PerpExecution:
        """Validate perp order execution details."""
        assert order_execution is not None, "check_order_execution: No order execution found"
        assert (
            order_execution.exchange_id == self._t.client.config.dex_id
        ), "check_order_execution: Exchange ID does not match"
        assert (
            order_execution.symbol == expected_order.symbol
        ), "check_order_execution: Order execution symbol does not match"
        assert (
            order_execution.account_id == expected_order.account_id
        ), "check_order_execution: Order execution account ID does not match"
        assert (
            order_execution.qty == expected_order.qty if expected_qty is None else expected_qty
        ), "check_order_execution: Order execution qty does not match"
        assert order_execution.side == expected_order.side, "check_order_execution: Order execution side does not match"
        assert (
            order_execution.type == ExecutionType.ORDER_MATCH
        ), "check_order_execution: Order execution type does not match"
        if expected_order.order_type == OrderType.LIMIT:
            assert expected_order.limit_px is not None
            if expected_order.side == Side.B:
                assert float(order_execution.price) <= float(
                    expected_order.limit_px
                ), "check_order_execution: Order execution price does not match"
            else:
                assert float(order_execution.price) >= float(
                    expected_order.limit_px
                ), "check_order_execution: Order execution price does not match"
        return order_execution

    async def no_order_execution_since(self, since_sequence_number: int) -> None:
        """Assert no order execution occurred since the given sequence number.

        Args:
            since_sequence_number: The sequence number to compare against.
                Only executions with sequence_number > since_sequence_number
                are considered "new" executions.
        """
        order_execution = await self._t.data.last_perp_execution()
        if order_execution is not None:
            assert (
                order_execution.sequence_number <= since_sequence_number
            ), f"check_no_order_execution_since: Found new execution with sequence_number {order_execution.sequence_number} > {since_sequence_number}"

    async def spot_execution(
        self, spot_execution: SpotExecution, expected_order: Order, expected_qty: Optional[str] = None
    ) -> SpotExecution:
        """Validate spot execution details."""
        assert spot_execution is not None, "check_spot_execution: No spot execution found"
        assert (
            spot_execution.exchange_id == self._t.client.config.dex_id
        ), "check_spot_execution: Exchange ID does not match"
        assert spot_execution.symbol == expected_order.symbol, "check_spot_execution: Symbol does not match"
        assert spot_execution.account_id == expected_order.account_id, "check_spot_execution: Account ID does not match"
        assert spot_execution.qty == (
            expected_order.qty if expected_qty is None else expected_qty
        ), "check_spot_execution: Quantity does not match"
        assert spot_execution.side == expected_order.side, "check_spot_execution: Side does not match"
        assert spot_execution.type == ExecutionType.ORDER_MATCH, "check_spot_execution: Execution type does not match"
        if expected_order.order_type == OrderType.LIMIT and expected_order.limit_px is not None:
            if expected_order.side == Side.B:
                assert float(spot_execution.price) <= float(
                    expected_order.limit_px
                ), "check_spot_execution: Execution price should be <= limit price for buy"
            else:
                assert float(spot_execution.price) >= float(
                    expected_order.limit_px
                ), "check_spot_execution: Execution price should be >= limit price for sell"
        return spot_execution

    async def balance(
        self,
        asset: str,
        expected_account_id: int,
        expected_min_balance: Optional[str] = None,
        expected_max_balance: Optional[str] = None,
    ) -> None:
        """Check account balance for a specific asset."""
        bal = await self._t.data.balance(asset)
        if bal is None:
            raise RuntimeError(f"check_balance: Balance not found for asset {asset}")

        if expected_account_id is not None:
            assert bal.account_id == expected_account_id, "check_balance: Account ID does not match"

        if expected_min_balance is not None:
            assert float(bal.real_balance) >= float(
                expected_min_balance
            ), f"check_balance: Balance {bal.real_balance} should be >= {expected_min_balance}"

        if expected_max_balance is not None:
            assert float(bal.real_balance) <= float(
                expected_max_balance
            ), f"check_balance: Balance {bal.real_balance} should be <= {expected_max_balance}"

    def ws_order_change_received(
        self,
        order_id: str,
        expected_symbol: Optional[str] = None,
        expected_side: Optional[str] = None,
        expected_status: Optional[OrderStatus] = None,
        expected_qty: Optional[str] = None,
    ) -> Order:
        """Assert that an order change event was received via WebSocket."""
        assert order_id in self._t.ws.order_changes, (
            f"Order {order_id} not found in WebSocket order changes. "
            f"Available orders: {list(self._t.ws.order_changes.keys())}"
        )

        ws_order = self._t.ws.order_changes[order_id]
        logger.info(f"✅ Order change event received via WebSocket for {order_id}")

        if expected_symbol is not None:
            assert (
                ws_order.symbol == expected_symbol
            ), f"Symbol mismatch: expected {expected_symbol}, got {ws_order.symbol}"
            logger.info(f"   ✅ Symbol: {ws_order.symbol}")

        if expected_side is not None:
            # Compare enum value (string) since async_api uses enum types
            ws_side_value = ws_order.side.value if hasattr(ws_order.side, "value") else ws_order.side
            assert ws_side_value == expected_side, f"Side mismatch: expected {expected_side}, got {ws_side_value}"
            side_name = "BUY" if expected_side == "B" else "SELL"
            logger.info(f"   ✅ Side: {ws_side_value} ({side_name})")

        if expected_status is not None:
            # Compare enum value (string) since async_api uses enum types
            ws_status_value = ws_order.status.value if hasattr(ws_order.status, "value") else ws_order.status
            assert (
                ws_status_value == expected_status
            ), f"Status mismatch: expected {expected_status}, got {ws_status_value}"
            logger.info(f"   ✅ Status: {ws_status_value}")

        if expected_qty is not None:
            assert ws_order.qty is not None, "ws_order.qty is None"
            ws_qty = float(ws_order.qty)
            exp_qty = float(expected_qty)
            assert abs(ws_qty - exp_qty) < 0.0001, f"Qty mismatch: expected {exp_qty}, got {ws_qty}"
            logger.info(f"   ✅ Qty: {ws_order.qty}")

        return ws_order  # type: ignore[return-value]

    def ws_spot_execution_received(
        self,
        expected_symbol: Optional[str] = None,
        expected_side: Optional[str] = None,
        expected_qty: Optional[str] = None,
        expected_price: Optional[str] = None,
    ) -> SpotExecution:
        """Assert that a spot execution event was received via WebSocket."""
        assert self._t.ws.last_spot_execution is not None, "No spot execution event received via WebSocket"

        execution = self._t.ws.last_spot_execution
        logger.info("✅ Spot execution event received via WebSocket")

        if expected_symbol is not None:
            assert (
                execution.symbol == expected_symbol
            ), f"Symbol mismatch: expected {expected_symbol}, got {execution.symbol}"
            logger.info(f"   ✅ Symbol: {execution.symbol}")

        if expected_side is not None:
            # Compare enum value (string) since async_api uses enum types
            exec_side_value = execution.side.value if hasattr(execution.side, "value") else execution.side
            assert exec_side_value == expected_side, f"Side mismatch: expected {expected_side}, got {exec_side_value}"
            side_name = "BUY" if expected_side == "B" else "SELL"
            logger.info(f"   ✅ Side: {exec_side_value} ({side_name})")

        if expected_qty is not None:
            exec_qty = float(execution.qty)
            exp_qty = float(expected_qty)
            assert abs(exec_qty - exp_qty) < 1e-9, f"Qty mismatch: expected {exp_qty}, got {exec_qty}"
            logger.info(f"   ✅ Qty: {execution.qty}")

        if expected_price is not None and hasattr(execution, "price") and execution.price:
            exec_price = float(execution.price)
            exp_price = float(expected_price)
            assert abs(exec_price - exp_price) < 1e-9, f"Price mismatch: expected {exp_price}, got {exec_price}"
            logger.info(f"   ✅ Price: {execution.price}")

        return execution  # type: ignore[return-value]

    def ws_balance_updates_received(
        self,
        initial_update_count: int,
        min_updates: int = 1,
        expected_assets: Optional[list[str]] = None,
    ) -> list[AccountBalance]:
        """Assert that balance update events were received via WebSocket."""
        new_update_count = len(self._t.ws.balance_updates) - initial_update_count

        assert (
            new_update_count >= min_updates
        ), f"Expected at least {min_updates} balance update(s), got {new_update_count}"
        logger.info(f"✅ Received {new_update_count} balance update(s) via WebSocket")

        new_updates = self._t.ws.balance_updates[initial_update_count:]

        account_updates = [u for u in new_updates if u.account_id == self._t.account_id]
        assets_updated = {u.asset for u in account_updates}
        logger.info(f"   Assets updated: {assets_updated}")

        if expected_assets is not None:
            for asset in expected_assets:
                if asset in assets_updated:
                    logger.info(f"   ✅ {asset} balance update received")
                else:
                    logger.warning(f"   ⚠️ {asset} balance update not found (may be delayed)")

        return new_updates  # type: ignore[return-value]
