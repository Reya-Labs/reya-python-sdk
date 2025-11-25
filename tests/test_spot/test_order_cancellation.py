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
REFERENCE_PRICE = 4000.0
TEST_QTY = "0.0001"


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
    await asyncio.sleep(0.5)

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
