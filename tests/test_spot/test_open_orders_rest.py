"""
Open Orders REST API Tests

Tests for the GET /v2/wallet/:address/openOrders endpoint:
- Fetching wallet open orders
- Empty orders list handling
- Order filtering by wallet
"""

import asyncio
import logging

import pytest

from sdk.open_api.models.order import Order
from sdk.open_api.models.order_status import OrderStatus
from tests.helpers import ReyaTester
from tests.helpers.builders.order_builder import OrderBuilder
from tests.test_spot.spot_config import SpotTestConfig

logger = logging.getLogger("reya.integration_tests")


@pytest.mark.spot
@pytest.mark.rest_api
@pytest.mark.asyncio
async def test_rest_get_open_orders_with_orders(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test open orders REST endpoint returns orders when they exist.

    Flow:
    1. Clear existing orders
    2. Place a GTC order (won't match immediately)
    3. Fetch open orders via REST
    4. Verify order appears in response
    5. Cleanup
    """
    logger.info("=" * 80)
    logger.info("OPEN ORDERS REST - WITH ORDERS TEST")
    logger.info("=" * 80)

    await spot_tester.orders.close_all(fail_if_none=False)

    # Place a GTC order at a safe no-match price
    await spot_config.refresh_order_book(spot_tester.data)
    safe_price = spot_config.get_safe_no_match_buy_price()

    order_params = OrderBuilder.from_config(spot_config).buy().price(str(safe_price)).gtc().build()

    logger.info(f"Placing GTC buy order at ${safe_price} (safe no-match price)")
    order_id = await spot_tester.orders.create_limit(order_params)
    await spot_tester.wait.for_order_creation(order_id)
    logger.info(f"✅ Order created: {order_id}")

    # Fetch open orders via REST
    open_orders: list[Order] = await spot_tester.client.get_open_orders()

    logger.info(f"Open orders returned: {len(open_orders)}")

    # Verify our order is in the list
    assert len(open_orders) > 0, "Should have at least one open order"

    our_order = None
    for order in open_orders:
        if order.order_id == order_id:
            our_order = order
            break

    assert our_order is not None, f"Order {order_id} should be in open orders list"
    logger.info(f"✅ Found our order in open orders list")

    # Verify order structure
    assert isinstance(our_order, Order), f"Expected Order type, got {type(our_order)}"
    assert our_order.symbol == spot_config.symbol, f"Expected symbol {spot_config.symbol}"
    assert our_order.status == OrderStatus.OPEN, f"Expected OPEN status, got {our_order.status}"

    logger.info(f"   Order ID: {our_order.order_id}")
    logger.info(f"   Symbol: {our_order.symbol}")
    logger.info(f"   Status: {our_order.status}")

    # Cleanup
    await spot_tester.client.cancel_order(
        order_id=order_id, symbol=spot_config.symbol, account_id=spot_tester.account_id
    )
    await asyncio.sleep(0.1)
    await spot_tester.check.no_open_orders()

    logger.info("✅ OPEN ORDERS REST - WITH ORDERS TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.rest_api
@pytest.mark.asyncio
async def test_rest_get_open_orders_empty(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test open orders REST endpoint returns empty list when no orders exist.

    Flow:
    1. Cancel all existing orders
    2. Fetch open orders via REST
    3. Verify empty list is returned
    """
    logger.info("=" * 80)
    logger.info("OPEN ORDERS REST - EMPTY TEST")
    logger.info("=" * 80)

    # Cancel all existing orders
    await spot_tester.orders.close_all(fail_if_none=False)
    await asyncio.sleep(0.1)

    # Fetch open orders via REST
    open_orders: list[Order] = await spot_tester.client.get_open_orders()

    logger.info(f"Open orders returned: {len(open_orders)}")

    # Filter to only our account's orders for the spot symbol
    our_orders = [
        o for o in open_orders
        if o.account_id == spot_tester.account_id and o.symbol == spot_config.symbol
    ]

    assert len(our_orders) == 0, f"Should have no open orders for {spot_config.symbol}, got {len(our_orders)}"
    logger.info(f"✅ No open orders for account {spot_tester.account_id} on {spot_config.symbol}")

    logger.info("✅ OPEN ORDERS REST - EMPTY TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.rest_api
@pytest.mark.asyncio
async def test_rest_get_open_orders_multiple(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test open orders REST endpoint returns multiple orders correctly.

    Flow:
    1. Clear existing orders
    2. Place multiple GTC orders at different prices
    3. Fetch open orders via REST
    4. Verify all orders appear in response
    5. Cleanup
    """
    logger.info("=" * 80)
    logger.info("OPEN ORDERS REST - MULTIPLE ORDERS TEST")
    logger.info("=" * 80)

    await spot_tester.orders.close_all(fail_if_none=False)

    # Place multiple GTC orders at safe no-match prices
    await spot_config.refresh_order_book(spot_tester.data)
    base_price = float(spot_config.get_safe_no_match_buy_price())

    num_orders = 3
    order_ids = []

    for i in range(num_orders):
        price = base_price + i  # Slightly different prices
        order_params = OrderBuilder.from_config(spot_config).buy().price(str(price)).gtc().build()

        order_id = await spot_tester.orders.create_limit(order_params)
        await spot_tester.wait.for_order_creation(order_id)
        order_ids.append(order_id)
        logger.info(f"✅ Order {i + 1}/{num_orders} created: {order_id} @ ${price}")

    # Fetch open orders via REST
    open_orders: list[Order] = await spot_tester.client.get_open_orders()

    logger.info(f"Total open orders returned: {len(open_orders)}")

    # Verify all our orders are in the list
    found_orders = [o for o in open_orders if o.order_id in order_ids]
    assert len(found_orders) == num_orders, (
        f"Expected {num_orders} orders, found {len(found_orders)}"
    )
    logger.info(f"✅ All {num_orders} orders found in open orders list")

    # Cleanup
    for order_id in order_ids:
        try:
            await spot_tester.client.cancel_order(
                order_id=order_id, symbol=spot_config.symbol, account_id=spot_tester.account_id
            )
        except (OSError, RuntimeError):
            pass

    await asyncio.sleep(0.1)
    await spot_tester.check.no_open_orders()

    logger.info("✅ OPEN ORDERS REST - MULTIPLE ORDERS TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.rest_api
@pytest.mark.asyncio
async def test_rest_get_open_orders_filters_by_wallet(
    spot_config: SpotTestConfig, maker_tester: ReyaTester, taker_tester: ReyaTester
):
    """
    Test open orders are filtered by wallet address.

    Flow:
    1. Maker places a GTC order
    2. Taker fetches open orders
    3. Verify maker's order does NOT appear in taker's list
    4. Maker fetches open orders
    5. Verify maker's order DOES appear in maker's list
    """
    logger.info("=" * 80)
    logger.info("OPEN ORDERS REST - FILTER BY WALLET TEST")
    logger.info("=" * 80)

    await maker_tester.orders.close_all(fail_if_none=False)
    await taker_tester.orders.close_all(fail_if_none=False)

    # Maker places a GTC order
    await spot_config.refresh_order_book(maker_tester.data)
    safe_price = spot_config.get_safe_no_match_buy_price()

    order_params = OrderBuilder.from_config(spot_config).buy().price(str(safe_price)).gtc().build()

    logger.info(f"Maker placing GTC buy order at ${safe_price}")
    maker_order_id = await maker_tester.orders.create_limit(order_params)
    await maker_tester.wait.for_order_creation(maker_order_id)
    logger.info(f"✅ Maker order created: {maker_order_id}")

    # Taker fetches open orders
    taker_orders: list[Order] = await taker_tester.client.get_open_orders()
    taker_order_ids = [o.order_id for o in taker_orders]

    assert maker_order_id not in taker_order_ids, (
        f"Maker's order {maker_order_id} should NOT appear in taker's open orders"
    )
    logger.info("✅ Maker's order does NOT appear in taker's open orders")

    # Maker fetches open orders
    maker_orders: list[Order] = await maker_tester.client.get_open_orders()
    maker_order_ids = [o.order_id for o in maker_orders]

    assert maker_order_id in maker_order_ids, (
        f"Maker's order {maker_order_id} should appear in maker's open orders"
    )
    logger.info("✅ Maker's order DOES appear in maker's open orders")

    # Cleanup
    await maker_tester.client.cancel_order(
        order_id=maker_order_id, symbol=spot_config.symbol, account_id=maker_tester.account_id
    )
    await asyncio.sleep(0.1)
    await maker_tester.check.no_open_orders()

    logger.info("✅ OPEN ORDERS REST - FILTER BY WALLET TEST COMPLETED")
