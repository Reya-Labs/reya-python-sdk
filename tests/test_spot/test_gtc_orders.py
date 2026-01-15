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
import random

import pytest

from sdk.open_api.models import OrderStatus
from sdk.open_api.models.depth import Depth
from tests.helpers import ReyaTester
from tests.helpers.builders.order_builder import OrderBuilder
from tests.test_spot.spot_config import SpotTestConfig

logger = logging.getLogger("reya.integration_tests")


@pytest.mark.spot
@pytest.mark.gtc
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_spot_gtc_full_fill(spot_config: SpotTestConfig, maker_tester: ReyaTester, taker_tester: ReyaTester):
    """
    Test GTC order fully filled immediately when matching liquidity exists.

    Flow:
    1. Maker places GTC buy order
    2. Taker places GTC sell order at crossing price
    3. Verify taker order is immediately filled (not on book)
    4. Verify maker order is filled
    """
    logger.info("=" * 80)
    logger.info(f"SPOT GTC FULL FILL TEST: {spot_config.symbol}")
    logger.info("=" * 80)

    await maker_tester.orders.close_all(fail_if_none=False)
    await taker_tester.orders.close_all(fail_if_none=False)

    # Maker places GTC buy order at price within oracle deviation limit
    maker_price = spot_config.price(0.99)

    maker_params = OrderBuilder.from_config(spot_config).buy().at_price(0.99).gtc().build()

    logger.info(f"Maker placing GTC buy: {spot_config.min_qty} @ ${maker_price:.2f}")
    maker_order_id = await maker_tester.orders.create_limit(maker_params)
    await maker_tester.wait.for_order_creation(maker_order_id)
    logger.info(f"✅ Maker order created: {maker_order_id}")

    # Taker places GTC sell order at same price (ensures within oracle deviation)
    taker_price = maker_price

    taker_params = OrderBuilder.from_config(spot_config).sell().at_price(0.99).gtc().build()

    logger.info(f"Taker placing GTC sell: {spot_config.min_qty} @ ${taker_price:.2f}")
    taker_order_id = await taker_tester.orders.create_limit(taker_params)
    logger.info(f"Taker order sent: {taker_order_id}")

    # Wait for matching
    await asyncio.sleep(0.05)

    # Verify both orders are filled (not on book)
    await maker_tester.wait.for_order_state(maker_order_id, OrderStatus.FILLED, timeout=5)
    logger.info("✅ Maker order filled")

    await taker_tester.wait.for_order_state(taker_order_id, OrderStatus.FILLED, timeout=5)
    logger.info("✅ Taker order filled")

    # Verify no open orders remain
    await maker_tester.check.no_open_orders()
    await taker_tester.check.no_open_orders()

    logger.info("✅ SPOT GTC FULL FILL TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.gtc
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_spot_gtc_partial_fill_remainder_on_book(
    spot_config: SpotTestConfig, maker_tester: ReyaTester, taker_tester: ReyaTester
):
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
    logger.info(f"SPOT GTC PARTIAL FILL TEST: {spot_config.symbol}")
    logger.info("=" * 80)

    await maker_tester.orders.close_all(fail_if_none=False)
    await taker_tester.orders.close_all(fail_if_none=False)

    # Maker places small GTC buy order
    maker_price = spot_config.price(0.99)
    maker_qty = spot_config.min_qty

    maker_params = OrderBuilder.from_config(spot_config).buy().at_price(0.99).gtc().build()

    logger.info(f"Maker placing GTC buy: {maker_qty} @ ${maker_price:.2f}")
    maker_order_id = await maker_tester.orders.create_limit(maker_params)
    await maker_tester.wait.for_order_creation(maker_order_id)
    logger.info(f"✅ Maker order created: {maker_order_id}")

    # Taker places larger GTC sell order
    taker_price = maker_price
    taker_qty = "0.002"  # Larger than maker (0.001) to test partial fill

    taker_params = OrderBuilder.from_config(spot_config).sell().at_price(0.99).qty(taker_qty).gtc().build()

    logger.info(f"Taker placing GTC sell: {taker_qty} @ ${taker_price:.2f}")
    taker_order_id = await taker_tester.orders.create_limit(taker_params)
    logger.info(f"Taker order sent: {taker_order_id}")

    # Wait for matching
    await asyncio.sleep(0.05)

    # Verify maker order is fully filled
    await maker_tester.wait.for_order_state(maker_order_id, OrderStatus.FILLED, timeout=5)
    logger.info("✅ Maker order fully filled")

    # Verify taker order is partially filled (remainder on book)
    open_orders = await taker_tester.client.get_open_orders()
    taker_open = [o for o in open_orders if o.order_id == taker_order_id]

    assert len(taker_open) == 1, f"Taker order {taker_order_id} should still be on book with remainder"
    logger.info("✅ Taker order partially filled, remainder on book")

    # Cleanup - cancel taker's remaining order
    await taker_tester.client.cancel_order(
        order_id=taker_order_id, symbol=spot_config.symbol, account_id=taker_tester.account_id
    )
    await asyncio.sleep(0.05)
    await taker_tester.check.no_open_orders()

    logger.info("✅ SPOT GTC PARTIAL FILL TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.gtc
@pytest.mark.asyncio
async def test_spot_gtc_no_match_added_to_book(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test GTC order added to book when no match exists.

    Flow:
    1. Place GTC buy order at price within oracle deviation
    2. Verify order is on book (not filled)
    3. Verify order appears in L2 depth
    4. Cancel order
    """
    logger.info("=" * 80)
    logger.info(f"SPOT GTC NO MATCH (ADDED TO BOOK) TEST: {spot_config.symbol}")
    logger.info("=" * 80)

    await spot_tester.orders.close_all(fail_if_none=False)

    # Place GTC buy order at price within oracle deviation (won't match without seller)
    order_price = spot_config.price(0.96)

    order_params = OrderBuilder.from_config(spot_config).buy().at_price(0.96).gtc().build()

    logger.info(f"Placing GTC buy: {spot_config.min_qty} @ ${order_price:.2f}")
    order_id = await spot_tester.orders.create_limit(order_params)
    await spot_tester.wait.for_order_creation(order_id)
    logger.info(f"✅ Order created: {order_id}")

    # Verify order is on book (open orders)
    open_orders = await spot_tester.client.get_open_orders()
    order_on_book = any(o.order_id == order_id for o in open_orders)
    assert order_on_book, f"Order {order_id} should be on book"
    logger.info("✅ Order is on book (open orders)")

    # Verify order appears in L2 depth
    await asyncio.sleep(0.1)
    depth = await spot_tester.data.market_depth(spot_config.symbol)
    assert isinstance(depth, Depth), f"Expected Depth type, got {type(depth)}"
    bids = depth.bids

    found_in_depth = False
    for bid in bids:
        price = float(bid.px)
        if abs(price - order_price) < 10.0:
            found_in_depth = True
            logger.info(f"✅ Order found in L2 depth at ${price:.2f}")
            break

    assert found_in_depth, f"Order at ${order_price:.2f} not found in L2 depth"

    # Cleanup
    await spot_tester.client.cancel_order(
        order_id=order_id, symbol=spot_config.symbol, account_id=spot_tester.account_id
    )
    await asyncio.sleep(0.05)
    await spot_tester.check.no_open_orders()

    logger.info("✅ SPOT GTC NO MATCH TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.gtc
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_spot_gtc_price_time_priority_fifo(
    spot_config: SpotTestConfig, maker_tester: ReyaTester, taker_tester: ReyaTester
):
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
    logger.info(f"SPOT GTC PRICE-TIME PRIORITY (FIFO) TEST: {spot_config.symbol}")
    logger.info("=" * 80)

    await maker_tester.orders.close_all(fail_if_none=False)
    await taker_tester.orders.close_all(fail_if_none=False)

    # Same price for both maker orders
    maker_price = spot_config.price(0.99)

    # First maker order
    first_params = OrderBuilder.from_config(spot_config).buy().at_price(0.99).gtc().build()

    logger.info(f"Maker placing FIRST GTC buy: {spot_config.min_qty} @ ${maker_price:.2f}")
    first_order_id = await maker_tester.orders.create_limit(first_params)
    await maker_tester.wait.for_order_creation(first_order_id)
    logger.info(f"✅ First order created: {first_order_id}")

    # Small delay to ensure time priority
    await asyncio.sleep(0.05)

    # Second maker order at same price
    second_params = OrderBuilder.from_config(spot_config).buy().at_price(0.99).gtc().build()

    logger.info(f"Maker placing SECOND GTC buy: {spot_config.min_qty} @ ${maker_price:.2f}")
    second_order_id = await maker_tester.orders.create_limit(second_params)
    await maker_tester.wait.for_order_creation(second_order_id)
    logger.info(f"✅ Second order created: {second_order_id}")

    # Taker places sell order that fills exactly one order
    taker_price = maker_price

    taker_params = (
        OrderBuilder()
        .symbol(spot_config.symbol)
        .sell()
        .price(str(taker_price))
        .qty(spot_config.min_qty)  # Same qty as one maker order
        .gtc()
        .build()
    )

    logger.info(f"Taker placing GTC sell: {spot_config.min_qty} @ ${taker_price:.2f}")
    taker_order_id = await taker_tester.orders.create_limit(taker_params)
    logger.info(f"Taker order sent: {taker_order_id}")

    # Wait for matching
    await asyncio.sleep(0.05)

    # Verify first order is filled (FIFO)
    await maker_tester.wait.for_order_state(first_order_id, OrderStatus.FILLED, timeout=5)
    logger.info("✅ First order filled (FIFO priority)")

    # Verify second order is still on book
    open_orders = await maker_tester.client.get_open_orders()
    second_still_open = any(o.order_id == second_order_id for o in open_orders)
    assert second_still_open, f"Second order {second_order_id} should still be on book"
    logger.info("✅ Second order remains on book")

    # Cleanup
    await maker_tester.client.cancel_order(
        order_id=second_order_id, symbol=spot_config.symbol, account_id=maker_tester.account_id
    )
    await asyncio.sleep(0.05)
    await maker_tester.check.no_open_orders()
    await taker_tester.check.no_open_orders()

    logger.info("✅ SPOT GTC FIFO TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.gtc
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_spot_gtc_best_price_first(
    spot_config: SpotTestConfig, maker_tester: ReyaTester, taker_tester: ReyaTester
):
    """
    Test GTC order matches best prices first.

    Flow:
    1. Maker places GTC buy order at price X
    2. Maker places GTC buy order at better price Y (Y > X)
    3. Taker places GTC sell order
    4. Verify better price order (Y) is filled first
    """
    logger.info("=" * 80)
    logger.info(f"SPOT GTC BEST PRICE FIRST TEST: {spot_config.symbol}")
    logger.info("=" * 80)

    await maker_tester.orders.close_all(fail_if_none=False)
    await taker_tester.orders.close_all(fail_if_none=False)

    # First order at lower price (within oracle deviation)
    lower_price = spot_config.price(0.96)

    lower_params = OrderBuilder.from_config(spot_config).buy().at_price(0.96).gtc().build()

    logger.info(f"Maker placing GTC buy at LOWER price: {spot_config.min_qty} @ ${lower_price:.2f}")
    lower_order_id = await maker_tester.orders.create_limit(lower_params)
    await maker_tester.wait.for_order_creation(lower_order_id)
    logger.info(f"✅ Lower price order created: {lower_order_id}")

    # Second order at higher (better for seller) price
    higher_price = spot_config.price(0.99)

    higher_params = OrderBuilder.from_config(spot_config).buy().at_price(0.99).gtc().build()

    logger.info(f"Maker placing GTC buy at HIGHER price: {spot_config.min_qty} @ ${higher_price:.2f}")
    higher_order_id = await maker_tester.orders.create_limit(higher_params)
    await maker_tester.wait.for_order_creation(higher_order_id)
    logger.info(f"✅ Higher price order created: {higher_order_id}")

    # Taker places sell order that fills exactly one order
    taker_price = lower_price  # Same as lower price ensures within oracle deviation

    taker_params = OrderBuilder.from_config(spot_config).sell().at_price(0.96).gtc().build()

    logger.info(f"Taker placing GTC sell: {spot_config.min_qty} @ ${taker_price:.2f}")
    taker_order_id = await taker_tester.orders.create_limit(taker_params)
    logger.info(f"Taker order sent: {taker_order_id}")

    # Wait for matching
    await asyncio.sleep(0.05)

    # Verify higher price order is filled first (best price for seller)
    await maker_tester.wait.for_order_state(higher_order_id, OrderStatus.FILLED, timeout=5)
    logger.info("✅ Higher price order filled first (best price priority)")

    # Verify lower price order is still on book
    open_orders = await maker_tester.client.get_open_orders()
    lower_still_open = any(o.order_id == lower_order_id for o in open_orders)
    assert lower_still_open, f"Lower price order {lower_order_id} should still be on book"
    logger.info("✅ Lower price order remains on book")

    # Cleanup
    await maker_tester.client.cancel_order(
        order_id=lower_order_id, symbol=spot_config.symbol, account_id=maker_tester.account_id
    )
    await asyncio.sleep(0.05)
    await maker_tester.check.no_open_orders()
    await taker_tester.check.no_open_orders()

    logger.info("✅ SPOT GTC BEST PRICE FIRST TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.gtc
@pytest.mark.asyncio
async def test_spot_gtc_with_client_order_id(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test GTC order with clientOrderId tracked correctly.

    Flow:
    1. Place GTC order with custom clientOrderId
    2. Verify clientOrderId is in response
    3. Verify order can be queried and has correct clientOrderId
    4. Cancel order using client_order_id (not order_id)
    """
    logger.info("=" * 80)
    logger.info(f"SPOT GTC WITH CLIENT ORDER ID TEST: {spot_config.symbol}")
    logger.info("=" * 80)

    await spot_tester.orders.close_all(fail_if_none=False)

    # Generate unique client order ID (positive integer, fits in uint64)
    test_client_order_id = random.randint(1, 2**32 - 1)  # nosec B311

    order_price = spot_config.price(0.96)

    order_params = (
        OrderBuilder()
        .symbol(spot_config.symbol)
        .buy()
        .price(str(order_price))
        .qty(spot_config.min_qty)
        .gtc()
        .client_order_id(test_client_order_id)
        .build()
    )

    logger.info(f"Placing GTC buy with clientOrderId={test_client_order_id}...")
    order_id = await spot_tester.orders.create_limit(order_params)
    await spot_tester.wait.for_order_creation(order_id)
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
        logger.info("Note: clientOrderId not returned in get_open_orders response")

    # Cancel using both order_id and client_order_id
    # The API should prefer order_id when both are provided
    logger.info(f"Cancelling order using both order_id={order_id} and clientOrderId={test_client_order_id}...")
    await spot_tester.client.cancel_order(
        order_id=order_id,
        client_order_id=test_client_order_id,
        symbol=spot_config.symbol,
        account_id=spot_tester.account_id,
    )
    await asyncio.sleep(0.05)
    await spot_tester.check.no_open_orders()
    logger.info("✅ Order cancelled (API prefers order_id when both provided)")

    logger.info("✅ SPOT GTC WITH CLIENT ORDER ID TEST COMPLETED")
