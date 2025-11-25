"""Order-related assertions for test verification."""

from typing import Optional
import logging

import pytest

from sdk.open_api.models.order import Order
from sdk.open_api.models.order_status import OrderStatus
from sdk.open_api.models.order_type import OrderType

from tests.helpers.clients.rest_client import RestClient
from tests.helpers.clients.websocket_client import WebSocketClient


logger = logging.getLogger("reya.test.assertions.order")


async def assert_order_created(
    rest_client: RestClient,
    ws_client: WebSocketClient,
    order_id: str,
    expected_order: Order,
) -> None:
    """
    Assert that an order was created with expected properties.
    
    Args:
        rest_client: REST client for verification
        ws_client: WebSocket client for verification
        order_id: Order ID to verify
        expected_order: Expected order properties
    """
    # Check REST API
    open_order = await rest_client.get_open_order(order_id)
    
    # For trigger orders, also check WebSocket if not found in REST
    if open_order is None and expected_order.order_type in [OrderType.SL, OrderType.TP]:
        open_order = ws_client.get_order_change(order_id)
        if open_order:
            logger.info(f"Trigger order found via WebSocket: {open_order}")
    
    assert open_order is not None, f"Order {order_id} not found in open orders or WebSocket"
    assert open_order.order_id == order_id, f"Wrong order ID: {open_order.order_id}"
    
    if expected_order.order_type == OrderType.LIMIT:
        # Compare prices as floats to handle formatting differences
        assert float(open_order.limit_px) == float(expected_order.limit_px), (
            f"Wrong limit price. Expected: {expected_order.limit_px}, Got: {open_order.limit_px}"
        )
        assert float(open_order.qty) == float(expected_order.qty), (
            f"Wrong qty. Expected: {expected_order.qty}, Got: {open_order.qty}"
        )
    else:
        # For trigger orders, use trigger_px field
        expected_trigger_px = expected_order.trigger_px or expected_order.limit_px
        assert float(open_order.trigger_px) == pytest.approx(float(expected_trigger_px), rel=1e-6), (
            f"Wrong trigger price. Expected: {expected_trigger_px}, Got: {open_order.trigger_px}"
        )
        assert open_order.qty is None, "Trigger order should not have qty"
    
    assert open_order.order_type == expected_order.order_type, (
        f"Wrong order type. Expected: {expected_order.order_type}, Got: {open_order.order_type}"
    )
    assert open_order.side == expected_order.side, (
        f"Wrong side. Expected: {expected_order.side}, Got: {open_order.side}"
    )
    assert open_order.status == OrderStatus.OPEN, (
        f"Wrong status. Expected: OPEN, Got: {open_order.status}"
    )
    
    logger.info(f"✅ Order {order_id} created successfully")


async def assert_order_filled(
    rest_client: RestClient,
    ws_client: WebSocketClient,
    order_id: str,
) -> None:
    """
    Assert that an order was filled.
    
    Args:
        rest_client: REST client for verification
        ws_client: WebSocket client for verification
        order_id: Order ID to verify
    """
    # Order should not be in open orders
    open_order = await rest_client.get_open_order(order_id)
    assert open_order is None, f"Order {order_id} still in open orders"
    
    # Check WebSocket for FILLED status
    ws_order = ws_client.get_order_change(order_id)
    if ws_order:
        assert ws_order.status == OrderStatus.FILLED, (
            f"Order status via WS: {ws_order.status}, expected FILLED"
        )
    
    logger.info(f"✅ Order {order_id} filled")


async def assert_order_cancelled(
    rest_client: RestClient,
    ws_client: WebSocketClient,
    order_id: str,
) -> None:
    """
    Assert that an order was cancelled.
    
    Args:
        rest_client: REST client for verification
        ws_client: WebSocket client for verification
        order_id: Order ID to verify
    """
    # Order should not be in open orders
    open_order = await rest_client.get_open_order(order_id)
    assert open_order is None, f"Order {order_id} still in open orders"
    
    # Check WebSocket for CANCELLED status
    ws_order = ws_client.get_order_change(order_id)
    if ws_order:
        assert ws_order.status == OrderStatus.CANCELLED, (
            f"Order status via WS: {ws_order.status}, expected CANCELLED"
        )
    
    logger.info(f"✅ Order {order_id} cancelled")


async def assert_no_open_orders(
    rest_client: RestClient,
    cleanup: bool = True,
) -> None:
    """
    Assert that there are no open orders.
    
    Args:
        rest_client: REST client for verification
        cleanup: If True, attempt to cancel any found orders
    """
    open_orders = await rest_client.get_open_orders()
    
    if len(open_orders) == 0:
        logger.info("✅ No open orders")
        return
    
    if not cleanup:
        assert False, f"Found {len(open_orders)} open orders"
    
    logger.warning(f"Found {len(open_orders)} open orders, attempting cleanup...")
    
    # Try to cancel stale orders
    legitimate_orders = []
    for order in open_orders:
        try:
            await rest_client.cancel_order(
                order_id=order.order_id,
                symbol=order.symbol,
                account_id=order.account_id,
            )
            legitimate_orders.append(order)
        except Exception as e:
            if "Missing order" in str(e):
                logger.info(f"Order {order.order_id} is stale, ignoring")
            else:
                logger.warning(f"Error cancelling order {order.order_id}: {e}")
                legitimate_orders.append(order)
    
    if len(legitimate_orders) == 0:
        logger.info("✅ All orders were stale, test can proceed")
        return
    
    # Wait and check again
    import asyncio
    await asyncio.sleep(0.3)
    
    remaining = await rest_client.get_open_orders()
    if len(remaining) > 0:
        logger.error(f"Still have {len(remaining)} open orders after cleanup")
        for order in remaining:
            logger.error(f"  - {order.order_id}: {order.symbol} {order.status}")
        assert False, "Open orders should be empty"
    
    logger.info("✅ All orders cleaned up")
