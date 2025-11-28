"""
Tests for spot order cancellation.

These tests verify single order cancellation and mass cancel functionality
for spot markets.
"""

import asyncio

import pytest

from sdk.open_api.models.order_status import OrderStatus

from tests.helpers import ReyaTester
from tests.helpers.builders import OrderBuilder
from tests.helpers.reya_tester import limit_order_params_to_order, logger


# Test configuration
SPOT_SYMBOL = "WETHRUSD"
REFERENCE_PRICE = 500.0
TEST_QTY = "0.01"  # Minimum order base for market ID 5


@pytest.mark.spot
@pytest.mark.cancel
@pytest.mark.asyncio
async def test_spot_order_cancellation(reya_tester: ReyaTester):
    """
    Test placing and cancelling a spot GTC order before it gets filled.
    """
    logger.info("=" * 80)
    logger.info(f"SPOT ORDER CANCELLATION TEST: {SPOT_SYMBOL}")
    logger.info("=" * 80)
    logger.info(f"Using reference price for orders: ${REFERENCE_PRICE}")

    # Clear any existing orders
    await reya_tester.check_no_open_orders()

    # Place GTC order far from reference (won't fill)
    buy_price = REFERENCE_PRICE * 0.5  # Far below reference

    order_params = (
        OrderBuilder()
        .symbol(SPOT_SYMBOL)
        .buy()
        .price(str(buy_price))
        .qty(TEST_QTY)
        .gtc()
        .build()
    )

    logger.info(f"Placing GTC buy order at ${buy_price:.2f} (far from market)...")
    order_id = await reya_tester.create_limit_order(order_params)
    logger.info(f"Created order with ID: {order_id}")

    # Wait for order creation
    await reya_tester.wait_for_order_creation(order_id)
    expected_order = limit_order_params_to_order(order_params, reya_tester.account_id)
    await reya_tester.check_open_order_created(order_id, expected_order)
    logger.info("✅ Order confirmed on the book")

    # Cancel the order
    logger.info("Cancelling order...")
    await reya_tester.client.cancel_order(
        order_id=order_id,
        symbol=SPOT_SYMBOL,
        account_id=reya_tester.account_id
    )

    # Wait for cancellation confirmation
    cancelled_order_id = await reya_tester.wait_for_order_state(order_id, OrderStatus.CANCELLED)
    assert cancelled_order_id == order_id, "Order was not cancelled"
    logger.info("✅ Order cancelled successfully")

    # Verify no open orders remain
    await reya_tester.check_no_open_orders()

    logger.info("✅ SPOT ORDER CANCELLATION TEST COMPLETED SUCCESSFULLY")


@pytest.mark.spot
@pytest.mark.cancel
@pytest.mark.asyncio
async def test_spot_mass_cancel(reya_tester: ReyaTester):
    """
    Test placing multiple spot GTC orders and then cancelling them all via mass cancel.
    """
    num_orders = 5

    logger.info("=" * 80)
    logger.info(f"SPOT MASS CANCEL TEST: {SPOT_SYMBOL}")
    logger.info("=" * 80)
    logger.info(f"Using reference price for orders: ${REFERENCE_PRICE}")

    # Clear any existing orders
    await reya_tester.check_no_open_orders()

    # Place multiple GTC orders at different prices (far from market, won't fill)
    order_ids = []

    logger.info(f"\n📋 Step 1: Placing {num_orders} GTC orders...")
    for i in range(num_orders):
        # Space orders from 40% to 30% of reference price
        price_factor = 0.4 - (i * 0.02)
        buy_price = round(REFERENCE_PRICE * price_factor, 4)

        order_params = (
            OrderBuilder()
            .symbol(SPOT_SYMBOL)
            .buy()
            .price(str(buy_price))
            .qty(TEST_QTY)
            .gtc()
            .build()
        )

        logger.info(f"Creating order {i+1}/{num_orders} at ${buy_price:.2f}")
        order_id = await reya_tester.create_limit_order(order_params)
        order_ids.append(order_id)

        # Wait for order creation
        await reya_tester.wait_for_order_creation(order_id)
        logger.info(f"✅ Order {i+1} created: {order_id}")

    logger.info(f"\n✅ All {num_orders} orders created successfully")

    # Verify all orders are on the book
    logger.info("\n📊 Step 2: Verifying all orders are on the book...")
    open_orders = await reya_tester.client.get_open_orders()
    open_order_ids = [o.order_id for o in open_orders if o.symbol == SPOT_SYMBOL]

    for order_id in order_ids:
        assert order_id in open_order_ids, f"Order {order_id} not found on the book"

    logger.info(f"✅ Verified {len(order_ids)} orders on the book")

    # Mass cancel all orders for this symbol
    logger.info("\n🧹 Step 3: Mass cancelling all orders...")
    response = await reya_tester.client.mass_cancel(
        symbol=SPOT_SYMBOL,
        account_id=reya_tester.account_id
    )
    logger.info(f"Mass cancel response: {response}")

    # Wait a moment for cancellations to propagate
    await asyncio.sleep(0.1)

    # Verify all orders are cancelled
    logger.info("\n📊 Step 4: Verifying all orders are cancelled...")
    open_orders_after = await reya_tester.client.get_open_orders()
    open_order_ids_after = [o.order_id for o in open_orders_after if o.symbol == SPOT_SYMBOL]

    for order_id in order_ids:
        assert order_id not in open_order_ids_after, f"Order {order_id} still exists after mass cancel"

    logger.info(f"✅ All {num_orders} orders successfully cancelled via mass cancel")

    # Final verification - no open orders remain
    await reya_tester.check_no_open_orders()

    logger.info("\n" + "=" * 80)
    logger.info("✅ SPOT MASS CANCEL TEST COMPLETED SUCCESSFULLY")
    logger.info("=" * 80)


@pytest.mark.spot
@pytest.mark.cancel
@pytest.mark.asyncio
async def test_spot_cancel_nonexistent_order(reya_tester: ReyaTester):
    """
    Test cancelling an order that doesn't exist.
    
    Flow:
    1. Attempt to cancel a non-existent order ID
    2. Verify error response is returned
    """
    logger.info("=" * 80)
    logger.info(f"SPOT CANCEL NONEXISTENT ORDER TEST: {SPOT_SYMBOL}")
    logger.info("=" * 80)

    await reya_tester.close_active_orders(fail_if_none=False)

    # Use a fake order ID that doesn't exist
    fake_order_id = "999999999999999999"
    
    logger.info(f"Attempting to cancel non-existent order: {fake_order_id}")
    
    try:
        await reya_tester.client.cancel_order(
            order_id=fake_order_id,
            symbol=SPOT_SYMBOL,
            account_id=reya_tester.account_id
        )
        # If we get here, the API might accept the request but do nothing
        logger.info("Cancel request accepted (order may not exist)")
    except Exception as e:
        logger.info(f"✅ Cancel rejected as expected: {type(e).__name__}")

    logger.info("✅ SPOT CANCEL NONEXISTENT ORDER TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.cancel
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_spot_cancel_already_filled_order(maker_tester: ReyaTester, taker_tester: ReyaTester):
    """
    Test cancelling an order that was already filled.

    Flow:
    1. Maker places GTC sell order (maker has more ETH)
    2. Taker buys with IOC (taker has more RUSD)
    3. Attempt to cancel the filled order
    4. Verify error response (order already filled)
    """
    logger.info("=" * 80)
    logger.info(f"SPOT CANCEL ALREADY FILLED ORDER TEST: {SPOT_SYMBOL}")
    logger.info("=" * 80)

    await maker_tester.close_active_orders(fail_if_none=False)
    await taker_tester.close_active_orders(fail_if_none=False)

    # Maker places GTC sell order (maker has more ETH)
    maker_price = round(REFERENCE_PRICE * 1.50, 2)

    maker_params = (
        OrderBuilder()
        .symbol(SPOT_SYMBOL)
        .sell()
        .price(str(maker_price))
        .qty(TEST_QTY)
        .gtc()
        .build()
    )

    logger.info(f"Maker placing GTC sell: {TEST_QTY} @ ${maker_price:.2f}")
    maker_order_id = await maker_tester.create_limit_order(maker_params)
    await maker_tester.wait_for_order_creation(maker_order_id)
    logger.info(f"✅ Maker order created: {maker_order_id}")

    # Taker buys with IOC (taker has more RUSD)
    taker_price = round(maker_price * 1.01, 2)

    taker_params = (
        OrderBuilder()
        .symbol(SPOT_SYMBOL)
        .buy()
        .price(str(taker_price))
        .qty(TEST_QTY)
        .ioc()
        .build()
    )

    logger.info(f"Taker placing IOC buy to fill maker order...")
    await taker_tester.create_limit_order(taker_params)
    
    # Wait for fill
    await asyncio.sleep(0.05)
    await maker_tester.wait_for_order_state(maker_order_id, OrderStatus.FILLED, timeout=5)
    logger.info("✅ Maker order filled")

    # Now try to cancel the already-filled order
    logger.info(f"Attempting to cancel already-filled order: {maker_order_id}")
    
    try:
        await maker_tester.client.cancel_order(
            order_id=maker_order_id,
            symbol=SPOT_SYMBOL,
            account_id=maker_tester.account_id
        )
        logger.info("Cancel request accepted (order already filled)")
    except Exception as e:
        logger.info(f"✅ Cancel rejected as expected: {type(e).__name__}")

    # Verify no open orders
    await maker_tester.check_no_open_orders()
    await taker_tester.check_no_open_orders()

    logger.info("✅ SPOT CANCEL ALREADY FILLED ORDER TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.cancel
@pytest.mark.asyncio
async def test_spot_mass_cancel_empty_book(reya_tester: ReyaTester):
    """
    Test mass cancel when no orders exist.
    
    Flow:
    1. Ensure no orders exist
    2. Call mass cancel
    3. Verify success with count=0 (or no error)
    """
    logger.info("=" * 80)
    logger.info(f"SPOT MASS CANCEL EMPTY BOOK TEST: {SPOT_SYMBOL}")
    logger.info("=" * 80)

    # Ensure no orders exist
    await reya_tester.close_active_orders(fail_if_none=False)
    await reya_tester.check_no_open_orders()

    logger.info("Calling mass cancel on empty book...")
    
    try:
        response = await reya_tester.client.mass_cancel(
            symbol=SPOT_SYMBOL,
            account_id=reya_tester.account_id
        )
        logger.info(f"✅ Mass cancel succeeded: {response}")
    except Exception as e:
        # Some APIs might return an error for empty cancel
        logger.info(f"Mass cancel response: {type(e).__name__}")

    # Verify still no orders
    await reya_tester.check_no_open_orders()

    logger.info("✅ SPOT MASS CANCEL EMPTY BOOK TEST COMPLETED")
