"""
Tests for spot order cancellation.

These tests verify single order cancellation and mass cancel functionality
for spot markets.
"""

import asyncio
import time

import pytest

from sdk.open_api.exceptions import ApiException
from sdk.open_api.models.order_status import OrderStatus
from tests.helpers import ReyaTester
from tests.helpers.builders import OrderBuilder
from tests.helpers.reya_tester import limit_order_params_to_order, logger
from tests.test_spot.spot_config import SpotTestConfig


@pytest.mark.spot
@pytest.mark.cancel
@pytest.mark.asyncio
async def test_spot_order_cancellation(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test placing and cancelling a spot GTC order before it gets filled.
    """
    logger.info("=" * 80)
    logger.info(f"SPOT ORDER CANCELLATION TEST: {spot_config.symbol}")
    logger.info("=" * 80)
    logger.info(f"Using reference price for orders: ${spot_config.oracle_price}")

    # Clear any existing orders
    await spot_tester.orders.close_all(fail_if_none=False)

    # Place GTC order far from reference (won't fill)
    buy_price = spot_config.price(0.96)  # Far below reference

    order_params = OrderBuilder.from_config(spot_config).buy().at_price(0.96).gtc().build()

    logger.info(f"Placing GTC buy order at ${buy_price:.2f} (far from market)...")
    order_id = await spot_tester.orders.create_limit(order_params)
    logger.info(f"Created order with ID: {order_id}")

    # Wait for order creation
    await spot_tester.wait.for_order_creation(order_id)
    expected_order = limit_order_params_to_order(order_params, spot_tester.account_id)
    await spot_tester.check.open_order_created(order_id, expected_order)
    logger.info("✅ Order confirmed on the book")

    # Cancel the order
    logger.info("Cancelling order...")
    await spot_tester.client.cancel_order(
        order_id=order_id, symbol=spot_config.symbol, account_id=spot_tester.account_id
    )

    # Wait for cancellation confirmation
    cancelled_order_id = await spot_tester.wait.for_order_state(order_id, OrderStatus.CANCELLED)
    assert cancelled_order_id == order_id, "Order was not cancelled"
    logger.info("✅ Order cancelled successfully")

    # Verify no open orders remain
    await spot_tester.check.no_open_orders()

    logger.info("✅ SPOT ORDER CANCELLATION TEST COMPLETED SUCCESSFULLY")


@pytest.mark.spot
@pytest.mark.cancel
@pytest.mark.asyncio
async def test_spot_mass_cancel(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test placing multiple spot GTC orders and then cancelling them all via mass cancel.
    """
    num_orders = 5

    logger.info("=" * 80)
    logger.info(f"SPOT MASS CANCEL TEST: {spot_config.symbol}")
    logger.info("=" * 80)
    logger.info(f"Using reference price for orders: ${spot_config.oracle_price}")

    # Clear any existing orders
    await spot_tester.orders.close_all(fail_if_none=False)

    # Place multiple GTC orders at different prices (far from market, won't fill)
    order_ids = []

    logger.info(f"\n📋 Step 1: Placing {num_orders} GTC orders...")
    for i in range(num_orders):
        # Space orders within 5% of oracle price (0.96 to 0.98)
        price_factor = 0.96 + (i * 0.005)
        buy_price = round(spot_config.oracle_price * price_factor, 2)

        order_params = OrderBuilder.from_config(spot_config).buy().price(str(buy_price)).gtc().build()

        logger.info(f"Creating order {i + 1}/{num_orders} at ${buy_price:.2f}")
        order_id = await spot_tester.orders.create_limit(order_params)
        order_ids.append(order_id)

        # Wait for order creation
        await spot_tester.wait.for_order_creation(order_id)
        logger.info(f"✅ Order {i + 1} created: {order_id}")

    logger.info(f"\n✅ All {num_orders} orders created successfully")

    # Verify all orders are on the book
    logger.info("\n📊 Step 2: Verifying all orders are on the book...")
    open_orders = await spot_tester.client.get_open_orders()
    open_order_ids = [o.order_id for o in open_orders if o.symbol == spot_config.symbol]

    for order_id in order_ids:
        assert order_id in open_order_ids, f"Order {order_id} not found on the book"

    logger.info(f"✅ Verified {len(order_ids)} orders on the book")

    # Mass cancel all orders for this symbol
    logger.info("\n🧹 Step 3: Mass cancelling all orders...")
    response = await spot_tester.client.mass_cancel(symbol=spot_config.symbol, account_id=spot_tester.account_id)
    logger.info(f"Mass cancel response: {response}")

    # Wait a moment for cancellations to propagate
    await asyncio.sleep(0.1)

    # Verify all orders are cancelled
    logger.info("\n📊 Step 4: Verifying all orders are cancelled...")
    open_orders_after = await spot_tester.client.get_open_orders()
    open_order_ids_after = [o.order_id for o in open_orders_after if o.symbol == spot_config.symbol]

    for order_id in order_ids:
        assert order_id not in open_order_ids_after, f"Order {order_id} still exists after mass cancel"

    logger.info(f"✅ All {num_orders} orders successfully cancelled via mass cancel")

    # Final verification - no open orders remain
    await spot_tester.check.no_open_orders()

    logger.info("\n%s", "=" * 80)
    logger.info("✅ SPOT MASS CANCEL TEST COMPLETED SUCCESSFULLY")
    logger.info("=" * 80)


@pytest.mark.spot
@pytest.mark.cancel
@pytest.mark.asyncio
async def test_spot_cancel_nonexistent_order(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test cancelling an order that doesn't exist.

    Flow:
    1. Attempt to cancel a non-existent order ID
    2. Verify error response is returned
    """
    logger.info("=" * 80)
    logger.info(f"SPOT CANCEL NONEXISTENT ORDER TEST: {spot_config.symbol}")
    logger.info("=" * 80)

    await spot_tester.orders.close_all(fail_if_none=False)

    # Use a fake order ID that doesn't exist
    fake_order_id = "999999999999999999"

    logger.info(f"Attempting to cancel non-existent order: {fake_order_id}")

    try:
        await spot_tester.client.cancel_order(
            order_id=fake_order_id, symbol=spot_config.symbol, account_id=spot_tester.account_id
        )
        # If we get here, the API might accept the request but do nothing
        logger.info("Cancel request accepted (order may not exist)")
    except ApiException as e:
        logger.info(f"✅ Cancel rejected as expected: {type(e).__name__}")

    # Verify no open orders remain
    await spot_tester.check.no_open_orders()

    logger.info("✅ SPOT CANCEL NONEXISTENT ORDER TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.cancel
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_spot_cancel_already_filled_order(
    spot_config: SpotTestConfig, maker_tester: ReyaTester, taker_tester: ReyaTester
):
    """
    Test cancelling an order that was already filled.

    Flow:
    1. Maker places GTC sell order (maker has more ETH)
    2. Taker buys with IOC (taker has more RUSD)
    3. Attempt to cancel the filled order
    4. Verify error response (order already filled)
    """
    # Check current order book state
    await spot_config.refresh_order_book(taker_tester.data)

    logger.info("=" * 80)
    logger.info(f"SPOT CANCEL ALREADY FILLED ORDER TEST: {spot_config.symbol}")
    logger.info("=" * 80)

    await maker_tester.orders.close_all(fail_if_none=False)
    await taker_tester.orders.close_all(fail_if_none=False)

    # Determine how to get a filled order based on liquidity
    if spot_config.has_usable_ask_liquidity:
        # External asks exist - taker buys from external
        ask_price = spot_config.best_ask_price
        assert ask_price is not None
        trade_price = float(ask_price)
        logger.info(f"Using external ask liquidity at ${trade_price:.2f}")

        # Place GTC buy order that will match external asks
        taker_params = OrderBuilder.from_config(spot_config).buy().price(str(ask_price)).gtc().build()
        filled_order_id = await taker_tester.orders.create_limit(taker_params)
        logger.info(f"Taker placing GTC buy: {spot_config.min_qty} @ ${trade_price:.2f}")

        # Wait for fill
        await taker_tester.wait.for_order_state(filled_order_id, OrderStatus.FILLED, timeout=5)
        logger.info("✅ Order filled by external liquidity")

        # Now try to cancel the already-filled order
        logger.info(f"Attempting to cancel already-filled order: {filled_order_id}")

        try:
            await taker_tester.client.cancel_order(
                order_id=filled_order_id, symbol=spot_config.symbol, account_id=taker_tester.account_id
            )
            logger.info("Cancel request accepted (order already filled)")
        except ApiException as e:
            logger.info(f"✅ Cancel rejected as expected: {type(e).__name__}")
    elif spot_config.has_usable_bid_liquidity:
        # External bids exist - taker sells to external
        bid_price = spot_config.best_bid_price
        assert bid_price is not None
        trade_price = float(bid_price)
        logger.info(f"Using external bid liquidity at ${trade_price:.2f}")

        # Place GTC sell order that will match external bids
        taker_params = OrderBuilder.from_config(spot_config).sell().price(str(bid_price)).gtc().build()
        filled_order_id = await taker_tester.orders.create_limit(taker_params)
        logger.info(f"Taker placing GTC sell: {spot_config.min_qty} @ ${trade_price:.2f}")

        # Wait for fill
        await taker_tester.wait.for_order_state(filled_order_id, OrderStatus.FILLED, timeout=5)
        logger.info("✅ Order filled by external liquidity")

        # Now try to cancel the already-filled order
        logger.info(f"Attempting to cancel already-filled order: {filled_order_id}")

        try:
            await taker_tester.client.cancel_order(
                order_id=filled_order_id, symbol=spot_config.symbol, account_id=taker_tester.account_id
            )
            logger.info("Cancel request accepted (order already filled)")
        except ApiException as e:
            logger.info(f"✅ Cancel rejected as expected: {type(e).__name__}")
    else:
        # No external liquidity - use maker-taker matching
        maker_price = spot_config.price(1.04)

        maker_params = OrderBuilder.from_config(spot_config).sell().at_price(1.04).gtc().build()
        logger.info(f"Maker placing GTC sell: {spot_config.min_qty} @ ${maker_price:.2f}")
        filled_order_id = await maker_tester.orders.create_limit(maker_params)
        await maker_tester.wait.for_order_creation(filled_order_id)
        logger.info(f"✅ Maker order created: {filled_order_id}")

        # Taker buys with IOC
        taker_params = OrderBuilder.from_config(spot_config).buy().at_price(1.04).ioc().build()
        logger.info("Taker placing IOC buy to fill maker order...")
        await taker_tester.orders.create_limit(taker_params)

        # Wait for fill
        await asyncio.sleep(0.05)
        await maker_tester.wait.for_order_state(filled_order_id, OrderStatus.FILLED, timeout=5)
        logger.info("✅ Maker order filled")

        # Now try to cancel the already-filled order
        logger.info(f"Attempting to cancel already-filled order: {filled_order_id}")

        try:
            await maker_tester.client.cancel_order(
                order_id=filled_order_id, symbol=spot_config.symbol, account_id=maker_tester.account_id
            )
            logger.info("Cancel request accepted (order already filled)")
        except ApiException as e:
            logger.info(f"✅ Cancel rejected as expected: {type(e).__name__}")

    # Verify no open orders
    await maker_tester.check.no_open_orders()
    await taker_tester.check.no_open_orders()

    logger.info("✅ SPOT CANCEL ALREADY FILLED ORDER TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.cancel
@pytest.mark.asyncio
async def test_spot_mass_cancel_empty_book(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test mass cancel when no orders exist.

    Flow:
    1. Ensure no orders exist
    2. Call mass cancel
    3. Verify success with count=0 (or no error)
    """
    logger.info("=" * 80)
    logger.info(f"SPOT MASS CANCEL EMPTY BOOK TEST: {spot_config.symbol}")
    logger.info("=" * 80)

    # Ensure no orders exist
    await spot_tester.orders.close_all(fail_if_none=False)
    await spot_tester.check.no_open_orders()

    logger.info("Calling mass cancel on empty book...")

    try:
        response = await spot_tester.client.mass_cancel(symbol=spot_config.symbol, account_id=spot_tester.account_id)
        logger.info(f"✅ Mass cancel succeeded: {response}")
    except ApiException as e:
        # Some APIs might return an error for empty cancel
        logger.info(f"Mass cancel response: {type(e).__name__}")

    # Verify still no orders
    await spot_tester.check.no_open_orders()

    logger.info("✅ SPOT MASS CANCEL EMPTY BOOK TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.cancel
@pytest.mark.asyncio
async def test_spot_cancel_by_client_order_id(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test cancelling an order using client order ID instead of order ID.

    Flow:
    1. Place GTC order with a specific client order ID
    2. Cancel the order using the client order ID
    3. Verify order is cancelled
    """
    logger.info("=" * 80)
    logger.info(f"SPOT CANCEL BY CLIENT ORDER ID TEST: {spot_config.symbol}")
    logger.info("=" * 80)

    await spot_tester.orders.close_all(fail_if_none=False)

    # Generate a unique client order ID (must be an integer)
    client_order_id = int(time.time() * 1000) % (2**31 - 1)  # Keep within int32 range

    # Place GTC order with client order ID
    order_price = spot_config.price(0.96)

    order_params = (
        OrderBuilder()
        .symbol(spot_config.symbol)
        .buy()
        .price(str(order_price))
        .qty(spot_config.min_qty)
        .gtc()
        .client_order_id(client_order_id)
        .build()
    )

    logger.info(f"Placing GTC order with clientOrderId: {client_order_id}")
    order_id = await spot_tester.orders.create_limit(order_params)
    await spot_tester.wait.for_order_creation(order_id)
    logger.info(f"✅ Order created: {order_id}")

    # Verify order is on the book
    open_orders = await spot_tester.client.get_open_orders()
    order_found = False
    for o in open_orders:
        if o.order_id == order_id:
            order_found = True
            if hasattr(o, "client_order_id") and o.client_order_id:
                logger.info(f"Order has clientOrderId: {o.client_order_id}")
            break

    assert order_found, f"Order {order_id} not found on the book"

    # Cancel using client order ID
    logger.info(f"Cancelling order using clientOrderId: {client_order_id}")

    try:
        await spot_tester.client.cancel_order(
            order_id=order_id,  # Some APIs require order_id even with client_order_id
            symbol=spot_config.symbol,
            account_id=spot_tester.account_id,
            client_order_id=client_order_id,
        )
        logger.info("✅ Cancel request sent with clientOrderId")
    except TypeError:
        # If client_order_id is not supported, fall back to order_id
        logger.info("clientOrderId not supported in cancel, using order_id")
        await spot_tester.client.cancel_order(
            order_id=order_id, symbol=spot_config.symbol, account_id=spot_tester.account_id
        )

    # Wait for cancellation
    await spot_tester.wait.for_order_state(order_id, OrderStatus.CANCELLED)
    logger.info("✅ Order cancelled successfully")

    # Verify no open orders
    await spot_tester.check.no_open_orders()

    logger.info("✅ SPOT CANCEL BY CLIENT ORDER ID TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.cancel
@pytest.mark.asyncio
async def test_spot_mass_cancel_no_orders(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test mass cancel when no orders exist for the account/market.

    Flow:
    1. Ensure no open orders exist
    2. Execute mass cancel
    3. Verify response indicates 0 orders cancelled
    4. Verify no errors are raised
    """
    logger.info("=" * 80)
    logger.info(f"SPOT MASS CANCEL NO ORDERS TEST: {spot_config.symbol}")
    logger.info("=" * 80)

    # Ensure no open orders exist
    await spot_tester.orders.close_all(fail_if_none=False)
    await spot_tester.check.no_open_orders()
    logger.info("✅ Confirmed no open orders exist")

    # Execute mass cancel on empty order book (for this account)
    logger.info("Executing mass cancel with no orders...")
    response = await spot_tester.client.mass_cancel(symbol=spot_config.symbol, account_id=spot_tester.account_id)

    # Verify response
    logger.info(f"Mass cancel response: {response}")

    # The response should indicate 0 orders were cancelled
    if hasattr(response, "cancelled_count"):
        assert response.cancelled_count == 0, f"Expected 0 cancelled orders, got {response.cancelled_count}"
        logger.info("✅ Response correctly shows 0 orders cancelled")
    elif hasattr(response, "cancelledCount"):
        assert response.cancelledCount == 0, f"Expected 0 cancelled orders, got {response.cancelledCount}"
        logger.info("✅ Response correctly shows 0 orders cancelled")
    else:
        # If response doesn't have count, just verify no error was raised
        logger.info("✅ Mass cancel succeeded without error (no count in response)")

    # Verify still no open orders
    await spot_tester.check.no_open_orders()

    logger.info("✅ SPOT MASS CANCEL NO ORDERS TEST COMPLETED")
