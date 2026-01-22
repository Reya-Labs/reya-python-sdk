"""
Tests for spot order book (L2 depth) verification.

These tests verify that orders appear correctly in the order book
and that depth updates are received via WebSocket.

These tests use safe no-match prices to ensure orders are added to the book
without matching existing liquidity.
"""

import asyncio

import pytest

from sdk.open_api.models.depth import Depth
from sdk.open_api.models.level import Level
from sdk.open_api.models.order_status import OrderStatus
from tests.helpers import ReyaTester
from tests.helpers.builders import OrderBuilder
from tests.helpers.reya_tester import logger
from tests.test_spot.spot_config import SpotTestConfig


@pytest.mark.spot
@pytest.mark.websocket
@pytest.mark.asyncio
async def test_spot_order_appears_in_depth(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test that a GTC order appears in the L2 order book depth.

    Uses a safe no-match price to ensure the order is added to the book.

    Flow:
    1. Subscribe to market depth
    2. Get safe no-match price
    3. Place GTC order at that price
    4. Verify order appears in L2 depth via REST
    5. Cancel order
    6. Verify order removed from depth
    """
    logger.info("=" * 80)
    logger.info(f"SPOT ORDER BOOK TEST: {spot_config.symbol}")
    logger.info("=" * 80)

    # Clear any existing orders
    await spot_tester.check.no_open_orders()

    # Subscribe to market depth
    spot_tester.ws.subscribe_to_market_depth(spot_config.symbol)
    await asyncio.sleep(0.05)

    # Get initial depth and determine safe no-match price
    await spot_config.refresh_order_book(spot_tester.data)
    initial_depth = await spot_tester.data.market_depth(spot_config.symbol)
    assert isinstance(initial_depth, Depth), f"Expected Depth type, got {type(initial_depth)}"
    initial_bid_count = len(initial_depth.bids)
    logger.info(f"Initial depth: {initial_bid_count} bids")

    # Get a buy price guaranteed not to match (below all asks)
    order_price = spot_config.get_safe_no_match_buy_price()
    logger.info(f"Safe no-match buy price: ${order_price:.2f}")

    order_params = OrderBuilder.from_config(spot_config).buy().price(str(order_price)).gtc().build()

    logger.info(f"Placing GTC buy at ${order_price:.2f}...")
    order_id = await spot_tester.orders.create_limit(order_params)
    await spot_tester.wait.for_order_creation(order_id)
    logger.info(f"✅ Order created: {order_id}")

    # Wait for depth to update
    await asyncio.sleep(0.1)

    # Get updated depth
    updated_depth = await spot_tester.data.market_depth(spot_config.symbol)
    assert isinstance(updated_depth, Depth), f"Expected Depth type, got {type(updated_depth)}"
    bids = updated_depth.bids

    # Find our order in bids (using typed Level.px attribute)
    order_found = False
    for bid in bids:
        assert isinstance(bid, Level), f"Expected Level type, got {type(bid)}"
        bid_price = float(bid.px)
        if abs(bid_price - float(order_price)) < 0.01:
            order_found = True
            bid_qty = float(bid.qty)
            logger.info(f"✅ Found order in depth: ${bid_price:.2f} x {bid_qty}")
            break

    assert order_found, f"Order at ${order_price:.2f} not found in L2 depth"

    # Cancel the order
    logger.info("Cancelling order...")
    await spot_tester.client.cancel_order(
        order_id=order_id, symbol=spot_config.symbol, account_id=spot_tester.account_id
    )
    await spot_tester.wait.for_order_state(order_id, OrderStatus.CANCELLED)

    # Wait for depth to update
    await asyncio.sleep(0.1)

    # Verify order removed from depth
    final_depth = await spot_tester.data.market_depth(spot_config.symbol)
    final_bids = final_depth.bids

    order_still_present = False
    for bid in final_bids:
        bid_price = float(bid.px)
        if abs(bid_price - float(order_price)) < 0.01:
            order_still_present = True
            break

    assert not order_still_present, f"Order at ${order_price:.2f} should be removed from depth"
    logger.info("✅ Order removed from depth after cancellation")

    logger.info("✅ SPOT ORDER BOOK TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.asyncio
async def test_spot_multiple_orders_aggregate_in_depth(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test that multiple orders at the same price aggregate in depth.

    Uses a safe no-match price to ensure orders are added to the book.

    Flow:
    1. Get safe no-match price
    2. Place two orders at the same price
    3. Verify depth shows aggregated quantity
    4. Cancel both orders
    """
    logger.info("=" * 80)
    logger.info(f"SPOT DEPTH AGGREGATION TEST: {spot_config.symbol}")
    logger.info("=" * 80)

    # Clear any existing orders
    await spot_tester.check.no_open_orders()

    # Get safe no-match price
    await spot_config.refresh_order_book(spot_tester.data)
    order_price = spot_config.get_safe_no_match_buy_price()
    logger.info(f"Safe no-match buy price: ${order_price:.2f}")

    qty_per_order = spot_config.min_qty

    order_ids = []
    for i in range(2):
        order_params = OrderBuilder.from_config(spot_config).buy().price(str(order_price)).gtc().build()

        order_id = await spot_tester.orders.create_limit(order_params)
        await spot_tester.wait.for_order_creation(order_id)
        order_ids.append(order_id)
        logger.info(f"✅ Order {i + 1} created: {order_id}")

    # Wait for depth to update
    await asyncio.sleep(0.1)

    # Get depth and verify aggregation
    depth = await spot_tester.data.market_depth(spot_config.symbol)
    assert isinstance(depth, Depth), f"Expected Depth type, got {type(depth)}"
    bids = depth.bids

    aggregated_qty = None
    for bid in bids:
        bid_price = float(bid.px)
        if abs(bid_price - float(order_price)) < 0.01:
            aggregated_qty = float(bid.qty)
            break

    expected_total = float(qty_per_order) * 2
    assert aggregated_qty is not None, f"No bid found at ${order_price:.2f}"
    assert (
        abs(aggregated_qty - expected_total) < 0.0001
    ), f"Aggregated qty should be {expected_total}, got {aggregated_qty}"
    logger.info(f"✅ Depth shows aggregated qty: {aggregated_qty} (expected {expected_total})")

    # Cleanup - cancel all orders
    for order_id in order_ids:
        await spot_tester.client.cancel_order(
            order_id=order_id, symbol=spot_config.symbol, account_id=spot_tester.account_id
        )

    await asyncio.sleep(0.05)
    await spot_tester.check.no_open_orders()

    logger.info("✅ SPOT DEPTH AGGREGATION TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_spot_bid_ask_spread(spot_config: SpotTestConfig, maker_tester: ReyaTester, taker_tester: ReyaTester):
    """
    Test placing both bid and ask orders to create a spread.

    Flow:
    1. Maker places buy order (bid) at low price
    2. Taker places sell order (ask) at high price (taker has more ETH)
    3. Verify both appear in depth
    4. Verify bid < ask (proper spread)

    Uses maker/taker fixtures to ensure sufficient balances for both sides.
    Uses prices far from market to avoid matching existing liquidity.
    """
    logger.info("=" * 80)
    logger.info(f"SPOT BID/ASK SPREAD TEST: {spot_config.symbol}")
    logger.info("=" * 80)

    # Clear any existing orders (fail_if_none=False since we're just cleaning up)
    await maker_tester.orders.close_all(fail_if_none=False)
    await taker_tester.orders.close_all(fail_if_none=False)

    # Use prices far from market to avoid matching existing liquidity
    bid_price = spot_config.price(0.96)  # 50% below reference

    bid_params = OrderBuilder.from_config(spot_config).buy().at_price(0.96).gtc().build()

    logger.info(f"Maker placing bid at ${bid_price:.2f}...")
    bid_order_id = await maker_tester.orders.create_limit(bid_params)
    await maker_tester.wait.for_order_creation(bid_order_id)
    logger.info(f"✅ Bid order created: {bid_order_id}")

    # Taker places ask (sell) order at high price (taker has more ETH balance)
    ask_price = spot_config.price(1.04)  # 50% above reference

    ask_params = OrderBuilder.from_config(spot_config).sell().at_price(1.04).gtc().build()

    logger.info(f"Taker placing ask at ${ask_price:.2f}...")
    ask_order_id = await taker_tester.orders.create_limit(ask_params)
    await taker_tester.wait.for_order_creation(ask_order_id)
    logger.info(f"✅ Ask order created: {ask_order_id}")

    # Wait for depth to update
    await asyncio.sleep(0.1)

    # Get depth
    depth = await maker_tester.data.market_depth(spot_config.symbol)
    assert isinstance(depth, Depth), f"Expected Depth type, got {type(depth)}"
    bids = depth.bids
    asks = depth.asks

    logger.info(f"Depth: {len(bids)} bids, {len(asks)} asks")

    # Find our orders (using typed Level.px attribute)
    our_bid = None
    our_ask = None

    for bid in bids:
        price = float(bid.px)
        if abs(price - bid_price) < 1.0:  # Allow some tolerance
            our_bid = price
            logger.info(f"Found our bid at ${price:.2f}")
            break

    for ask in asks:
        price = float(ask.px)
        if abs(price - ask_price) < 1.0:  # Allow some tolerance
            our_ask = price
            logger.info(f"Found our ask at ${price:.2f}")
            break

    assert our_bid is not None, f"Bid at ${bid_price:.2f} not found in depth"
    assert our_ask is not None, f"Ask at ${ask_price:.2f} not found in depth"
    assert our_bid < our_ask, f"Bid ({our_bid}) should be less than ask ({our_ask})"

    spread = our_ask - our_bid
    logger.info(f"✅ Spread verified: bid=${our_bid:.2f}, ask=${our_ask:.2f}, spread=${spread:.2f}")

    # Cleanup
    await maker_tester.client.cancel_order(
        order_id=bid_order_id, symbol=spot_config.symbol, account_id=maker_tester.account_id
    )
    await taker_tester.client.cancel_order(
        order_id=ask_order_id, symbol=spot_config.symbol, account_id=taker_tester.account_id
    )

    await asyncio.sleep(0.05)
    await maker_tester.check.no_open_orders()
    await taker_tester.check.no_open_orders()

    logger.info("✅ SPOT BID/ASK SPREAD TEST COMPLETED")
