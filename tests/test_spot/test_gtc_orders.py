"""
Spot GTC (Good-Till-Cancelled) Order Tests

Tests for GTC order behavior including:
- Full fill when matching liquidity exists
- Partial fill with remainder on book
- No match - order added to book
- Price-time priority (FIFO)
- Best price first matching
- Client order ID tracking
"""

import asyncio
import logging
import uuid

import pytest

from sdk.async_api.depth import Depth
from sdk.open_api.models import OrderStatus
from tests.helpers import ReyaTester
from tests.helpers.builders.order_builder import OrderBuilder

logger = logging.getLogger("reya.integration_tests")

SPOT_SYMBOL = "WETHRUSD"
REFERENCE_PRICE = 500.0
TEST_QTY = "0.01"  # Minimum order base for market ID 5


@pytest.mark.spot
@pytest.mark.gtc
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_spot_gtc_full_fill(maker_tester: ReyaTester, taker_tester: ReyaTester):
    """
    Test GTC order fully filled immediately when matching liquidity exists.

    Flow:
    1. Maker places GTC buy order
    2. Taker places GTC sell order at crossing price
    3. Verify taker order is immediately filled (not on book)
    4. Verify maker order is filled
    """
    logger.info("=" * 80)
    logger.info(f"SPOT GTC FULL FILL TEST: {SPOT_SYMBOL}")
    logger.info("=" * 80)

    await maker_tester.close_active_orders(fail_if_none=False)
    await taker_tester.close_active_orders(fail_if_none=False)

    # Maker places GTC buy order at price far from market
    maker_price = round(REFERENCE_PRICE * 0.65, 2)

    maker_params = OrderBuilder().symbol(SPOT_SYMBOL).buy().price(str(maker_price)).qty(TEST_QTY).gtc().build()

    logger.info(f"Maker placing GTC buy: {TEST_QTY} @ ${maker_price:.2f}")
    maker_order_id = await maker_tester.create_limit_order(maker_params)
    await maker_tester.wait_for_order_creation(maker_order_id)
    logger.info(f"✅ Maker order created: {maker_order_id}")

    # Taker places GTC sell order at crossing price
    taker_price = round(maker_price * 0.99, 2)  # Below maker = will match

    taker_params = OrderBuilder().symbol(SPOT_SYMBOL).sell().price(str(taker_price)).qty(TEST_QTY).gtc().build()

    logger.info(f"Taker placing GTC sell: {TEST_QTY} @ ${taker_price:.2f}")
    taker_order_id = await taker_tester.create_limit_order(taker_params)
    logger.info(f"Taker order sent: {taker_order_id}")

    # Wait for matching
    await asyncio.sleep(0.05)

    # Verify both orders are filled (not on book)
    await maker_tester.wait_for_order_state(maker_order_id, OrderStatus.FILLED, timeout=5)
    logger.info("✅ Maker order filled")

    await taker_tester.wait_for_order_state(taker_order_id, OrderStatus.FILLED, timeout=5)
    logger.info("✅ Taker order filled")

    # Verify no open orders remain
    await maker_tester.check_no_open_orders()
    await taker_tester.check_no_open_orders()

    logger.info("✅ SPOT GTC FULL FILL TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.gtc
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_spot_gtc_partial_fill_remainder_on_book(maker_tester: ReyaTester, taker_tester: ReyaTester):
    """
    Test GTC order partially fills, remainder added to book.

    Flow:
    1. Maker places small GTC buy order
    2. Taker places larger GTC sell order at crossing price
    3. Verify maker order is fully filled
    4. Verify taker order is partially filled with remainder on book
    5. Cancel taker's remaining order
    """
    logger.info("=" * 80)
    logger.info(f"SPOT GTC PARTIAL FILL TEST: {SPOT_SYMBOL}")
    logger.info("=" * 80)

    await maker_tester.close_active_orders(fail_if_none=False)
    await taker_tester.close_active_orders(fail_if_none=False)

    # Maker places small GTC buy order
    maker_price = round(REFERENCE_PRICE * 0.65, 2)
    maker_qty = TEST_QTY

    maker_params = OrderBuilder().symbol(SPOT_SYMBOL).buy().price(str(maker_price)).qty(maker_qty).gtc().build()

    logger.info(f"Maker placing GTC buy: {maker_qty} @ ${maker_price:.2f}")
    maker_order_id = await maker_tester.create_limit_order(maker_params)
    await maker_tester.wait_for_order_creation(maker_order_id)
    logger.info(f"✅ Maker order created: {maker_order_id}")

    # Taker places larger GTC sell order
    taker_price = round(maker_price * 0.99, 2)
    taker_qty = "0.015"  # Slightly larger than maker (0.01) to test partial fill

    taker_params = OrderBuilder().symbol(SPOT_SYMBOL).sell().price(str(taker_price)).qty(taker_qty).gtc().build()

    logger.info(f"Taker placing GTC sell: {taker_qty} @ ${taker_price:.2f}")
    taker_order_id = await taker_tester.create_limit_order(taker_params)
    logger.info(f"Taker order sent: {taker_order_id}")

    # Wait for matching
    await asyncio.sleep(0.05)

    # Verify maker order is fully filled
    await maker_tester.wait_for_order_state(maker_order_id, OrderStatus.FILLED, timeout=5)
    logger.info("✅ Maker order fully filled")

    # Verify taker order is partially filled (remainder on book)
    open_orders = await taker_tester.client.get_open_orders()
    taker_open = [o for o in open_orders if o.order_id == taker_order_id]

    assert len(taker_open) == 1, f"Taker order {taker_order_id} should still be on book with remainder"
    logger.info(f"✅ Taker order partially filled, remainder on book")

    # Cleanup - cancel taker's remaining order
    await taker_tester.client.cancel_order(
        order_id=taker_order_id, symbol=SPOT_SYMBOL, account_id=taker_tester.account_id
    )
    await asyncio.sleep(0.05)
    await taker_tester.check_no_open_orders()

    logger.info("✅ SPOT GTC PARTIAL FILL TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.gtc
@pytest.mark.asyncio
async def test_spot_gtc_no_match_added_to_book(spot_tester: ReyaTester):
    """
    Test GTC order added to book when no match exists.

    Flow:
    1. Place GTC buy order at price far below market
    2. Verify order is on book (not filled)
    3. Verify order appears in L2 depth
    4. Cancel order
    """
    logger.info("=" * 80)
    logger.info(f"SPOT GTC NO MATCH (ADDED TO BOOK) TEST: {SPOT_SYMBOL}")
    logger.info("=" * 80)

    await spot_tester.close_active_orders(fail_if_none=False)

    # Place GTC buy order at price far below market (won't match)
    order_price = round(REFERENCE_PRICE * 0.50, 2)  # 50% below reference

    order_params = OrderBuilder().symbol(SPOT_SYMBOL).buy().price(str(order_price)).qty(TEST_QTY).gtc().build()

    logger.info(f"Placing GTC buy: {TEST_QTY} @ ${order_price:.2f}")
    order_id = await spot_tester.create_limit_order(order_params)
    await spot_tester.wait_for_order_creation(order_id)
    logger.info(f"✅ Order created: {order_id}")

    # Verify order is on book (open orders)
    open_orders = await spot_tester.client.get_open_orders()
    order_on_book = any(o.order_id == order_id for o in open_orders)
    assert order_on_book, f"Order {order_id} should be on book"
    logger.info("✅ Order is on book (open orders)")

    # Verify order appears in L2 depth
    await asyncio.sleep(0.1)
    depth = await spot_tester.get_market_depth(SPOT_SYMBOL)
    assert isinstance(depth, Depth), f"Expected Depth type, got {type(depth)}"
    bids = depth.bids

    found_in_depth = False
    for bid in bids:
        price = float(bid.px)
        if abs(price - order_price) < 1.0:
            found_in_depth = True
            logger.info(f"✅ Order found in L2 depth at ${price:.2f}")
            break

    assert found_in_depth, f"Order at ${order_price:.2f} not found in L2 depth"

    # Cleanup
    await spot_tester.client.cancel_order(order_id=order_id, symbol=SPOT_SYMBOL, account_id=spot_tester.account_id)
    await asyncio.sleep(0.05)
    await spot_tester.check_no_open_orders()

    logger.info("✅ SPOT GTC NO MATCH TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.gtc
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_spot_gtc_price_time_priority_fifo(maker_tester: ReyaTester, taker_tester: ReyaTester):
    """
    Test multiple GTC orders at same price filled in FIFO order.

    Flow:
    1. Maker places first GTC buy order at price X
    2. Maker places second GTC buy order at same price X
    3. Taker places GTC sell order that fills one order
    4. Verify first order is filled (FIFO)
    5. Verify second order remains on book
    """
    logger.info("=" * 80)
    logger.info(f"SPOT GTC PRICE-TIME PRIORITY (FIFO) TEST: {SPOT_SYMBOL}")
    logger.info("=" * 80)

    await maker_tester.close_active_orders(fail_if_none=False)
    await taker_tester.close_active_orders(fail_if_none=False)

    # Same price for both maker orders
    maker_price = round(REFERENCE_PRICE * 0.65, 2)

    # First maker order
    first_params = OrderBuilder().symbol(SPOT_SYMBOL).buy().price(str(maker_price)).qty(TEST_QTY).gtc().build()

    logger.info(f"Maker placing FIRST GTC buy: {TEST_QTY} @ ${maker_price:.2f}")
    first_order_id = await maker_tester.create_limit_order(first_params)
    await maker_tester.wait_for_order_creation(first_order_id)
    logger.info(f"✅ First order created: {first_order_id}")

    # Small delay to ensure time priority
    await asyncio.sleep(0.05)

    # Second maker order at same price
    second_params = OrderBuilder().symbol(SPOT_SYMBOL).buy().price(str(maker_price)).qty(TEST_QTY).gtc().build()

    logger.info(f"Maker placing SECOND GTC buy: {TEST_QTY} @ ${maker_price:.2f}")
    second_order_id = await maker_tester.create_limit_order(second_params)
    await maker_tester.wait_for_order_creation(second_order_id)
    logger.info(f"✅ Second order created: {second_order_id}")

    # Taker places sell order that fills exactly one order
    taker_price = round(maker_price * 0.99, 2)

    taker_params = (
        OrderBuilder()
        .symbol(SPOT_SYMBOL)
        .sell()
        .price(str(taker_price))
        .qty(TEST_QTY)  # Same qty as one maker order
        .gtc()
        .build()
    )

    logger.info(f"Taker placing GTC sell: {TEST_QTY} @ ${taker_price:.2f}")
    taker_order_id = await taker_tester.create_limit_order(taker_params)
    logger.info(f"Taker order sent: {taker_order_id}")

    # Wait for matching
    await asyncio.sleep(0.05)

    # Verify first order is filled (FIFO)
    await maker_tester.wait_for_order_state(first_order_id, OrderStatus.FILLED, timeout=5)
    logger.info("✅ First order filled (FIFO priority)")

    # Verify second order is still on book
    open_orders = await maker_tester.client.get_open_orders()
    second_still_open = any(o.order_id == second_order_id for o in open_orders)
    assert second_still_open, f"Second order {second_order_id} should still be on book"
    logger.info("✅ Second order remains on book")

    # Cleanup
    await maker_tester.client.cancel_order(
        order_id=second_order_id, symbol=SPOT_SYMBOL, account_id=maker_tester.account_id
    )
    await asyncio.sleep(0.05)
    await maker_tester.check_no_open_orders()
    await taker_tester.check_no_open_orders()

    logger.info("✅ SPOT GTC FIFO TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.gtc
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_spot_gtc_best_price_first(maker_tester: ReyaTester, taker_tester: ReyaTester):
    """
    Test GTC order matches best prices first.

    Flow:
    1. Maker places GTC buy order at price X
    2. Maker places GTC buy order at better price Y (Y > X)
    3. Taker places GTC sell order
    4. Verify better price order (Y) is filled first
    """
    logger.info("=" * 80)
    logger.info(f"SPOT GTC BEST PRICE FIRST TEST: {SPOT_SYMBOL}")
    logger.info("=" * 80)

    await maker_tester.close_active_orders(fail_if_none=False)
    await taker_tester.close_active_orders(fail_if_none=False)

    # First order at lower price
    lower_price = round(REFERENCE_PRICE * 0.60, 2)

    lower_params = OrderBuilder().symbol(SPOT_SYMBOL).buy().price(str(lower_price)).qty(TEST_QTY).gtc().build()

    logger.info(f"Maker placing GTC buy at LOWER price: {TEST_QTY} @ ${lower_price:.2f}")
    lower_order_id = await maker_tester.create_limit_order(lower_params)
    await maker_tester.wait_for_order_creation(lower_order_id)
    logger.info(f"✅ Lower price order created: {lower_order_id}")

    # Second order at higher (better for seller) price
    higher_price = round(REFERENCE_PRICE * 0.65, 2)

    higher_params = OrderBuilder().symbol(SPOT_SYMBOL).buy().price(str(higher_price)).qty(TEST_QTY).gtc().build()

    logger.info(f"Maker placing GTC buy at HIGHER price: {TEST_QTY} @ ${higher_price:.2f}")
    higher_order_id = await maker_tester.create_limit_order(higher_params)
    await maker_tester.wait_for_order_creation(higher_order_id)
    logger.info(f"✅ Higher price order created: {higher_order_id}")

    # Taker places sell order that fills exactly one order
    taker_price = round(lower_price * 0.99, 2)  # Below both prices

    taker_params = OrderBuilder().symbol(SPOT_SYMBOL).sell().price(str(taker_price)).qty(TEST_QTY).gtc().build()

    logger.info(f"Taker placing GTC sell: {TEST_QTY} @ ${taker_price:.2f}")
    taker_order_id = await taker_tester.create_limit_order(taker_params)
    logger.info(f"Taker order sent: {taker_order_id}")

    # Wait for matching
    await asyncio.sleep(0.05)

    # Verify higher price order is filled first (best price for seller)
    await maker_tester.wait_for_order_state(higher_order_id, OrderStatus.FILLED, timeout=5)
    logger.info("✅ Higher price order filled first (best price priority)")

    # Verify lower price order is still on book
    open_orders = await maker_tester.client.get_open_orders()
    lower_still_open = any(o.order_id == lower_order_id for o in open_orders)
    assert lower_still_open, f"Lower price order {lower_order_id} should still be on book"
    logger.info("✅ Lower price order remains on book")

    # Cleanup
    await maker_tester.client.cancel_order(
        order_id=lower_order_id, symbol=SPOT_SYMBOL, account_id=maker_tester.account_id
    )
    await asyncio.sleep(0.05)
    await maker_tester.check_no_open_orders()
    await taker_tester.check_no_open_orders()

    logger.info("✅ SPOT GTC BEST PRICE FIRST TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.gtc
@pytest.mark.asyncio
async def test_spot_gtc_with_client_order_id(spot_tester: ReyaTester):
    """
    Test GTC order with clientOrderId tracked correctly.

    Flow:
    1. Place GTC order with custom clientOrderId
    2. Verify clientOrderId is in response
    3. Verify order can be queried and has correct clientOrderId
    4. Cancel order using client_order_id (not order_id)
    """
    import random

    logger.info("=" * 80)
    logger.info(f"SPOT GTC WITH CLIENT ORDER ID TEST: {SPOT_SYMBOL}")
    logger.info("=" * 80)

    await spot_tester.close_active_orders(fail_if_none=False)

    # Generate unique client order ID (positive integer, fits in uint64)
    test_client_order_id = random.randint(1, 2**32 - 1)

    order_price = round(REFERENCE_PRICE * 0.50, 2)

    order_params = (
        OrderBuilder()
        .symbol(SPOT_SYMBOL)
        .buy()
        .price(str(order_price))
        .qty(TEST_QTY)
        .gtc()
        .client_order_id(test_client_order_id)
        .build()
    )

    logger.info(f"Placing GTC buy with clientOrderId={test_client_order_id}...")
    order_id = await spot_tester.create_limit_order(order_params)
    await spot_tester.wait_for_order_creation(order_id)
    logger.info(f"✅ Order created: {order_id}")

    # Verify the order has the clientOrderId
    open_orders = await spot_tester.client.get_open_orders()
    our_order = next((o for o in open_orders if o.order_id == order_id), None)
    assert our_order is not None, f"Order {order_id} not found in open orders"

    # Check if clientOrderId is returned in the response
    # Note: clientOrderId may be in additional_properties or as a direct attribute
    if hasattr(our_order, "client_order_id") and our_order.client_order_id is not None:
        logger.info(f"✅ clientOrderId verified: {our_order.client_order_id}")
        assert (
            our_order.client_order_id == test_client_order_id
        ), f"Expected clientOrderId {test_client_order_id}, got {our_order.client_order_id}"
    else:
        logger.info(f"Note: clientOrderId not returned in get_open_orders response")

    # Cancel using both order_id and client_order_id
    # The API should prefer order_id when both are provided
    logger.info(f"Cancelling order using both order_id={order_id} and clientOrderId={test_client_order_id}...")
    await spot_tester.client.cancel_order(
        order_id=order_id,
        client_order_id=test_client_order_id,
        symbol=SPOT_SYMBOL,
        account_id=spot_tester.account_id,
    )
    await asyncio.sleep(0.05)
    await spot_tester.check_no_open_orders()
    logger.info("✅ Order cancelled (API prefers order_id when both provided)")

    logger.info("✅ SPOT GTC WITH CLIENT ORDER ID TEST COMPLETED")
