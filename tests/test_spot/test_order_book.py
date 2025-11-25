"""
Tests for spot order book (L2 depth) verification.

These tests verify that orders appear correctly in the order book
and that depth updates are received via WebSocket.
"""

import asyncio

import pytest

from sdk.open_api.models.order_status import OrderStatus

from tests.helpers import ReyaTester
from tests.helpers.builders import OrderBuilder
from tests.helpers.reya_tester import logger


# Test configuration
SPOT_SYMBOL = "WETHRUSD"
REFERENCE_PRICE = 4000.0
TEST_QTY = "0.0001"


@pytest.mark.spot
@pytest.mark.websocket
@pytest.mark.asyncio
async def test_spot_order_appears_in_depth(reya_tester: ReyaTester):
    """
    Test that a GTC order appears in the L2 order book depth.
    
    Flow:
    1. Subscribe to market depth
    2. Place GTC order
    3. Verify order appears in L2 depth via REST
    4. Cancel order
    5. Verify order removed from depth
    """
    logger.info("=" * 80)
    logger.info(f"SPOT ORDER BOOK TEST: {SPOT_SYMBOL}")
    logger.info("=" * 80)

    # Clear any existing orders
    await reya_tester.check_no_open_orders()

    # Subscribe to market depth
    reya_tester.subscribe_to_market_depth(SPOT_SYMBOL)
    await asyncio.sleep(0.3)

    # Get initial depth
    initial_depth = await reya_tester.get_market_depth(SPOT_SYMBOL)
    initial_bid_count = len(initial_depth.get('bids', []))
    logger.info(f"Initial depth: {initial_bid_count} bids")

    # Place GTC buy order at specific price
    order_price = round(REFERENCE_PRICE * 0.85, 2)  # 15% below reference
    
    order_params = (
        OrderBuilder()
        .symbol(SPOT_SYMBOL)
        .buy()
        .price(str(order_price))
        .qty(TEST_QTY)
        .gtc()
        .build()
    )

    logger.info(f"Placing GTC buy at ${order_price:.2f}...")
    order_id = await reya_tester.create_limit_order(order_params)
    await reya_tester.wait_for_order_creation(order_id)
    logger.info(f"✅ Order created: {order_id}")

    # Wait for depth to update
    await asyncio.sleep(0.5)

    # Get updated depth
    updated_depth = await reya_tester.get_market_depth(SPOT_SYMBOL)
    bids = updated_depth.get('bids', [])
    
    # Find our order in bids
    order_found = False
    for bid in bids:
        bid_price = float(bid.get('price', 0))
        if abs(bid_price - order_price) < 0.01:
            order_found = True
            bid_qty = float(bid.get('quantity', 0))
            logger.info(f"✅ Found order in depth: ${bid_price:.2f} x {bid_qty}")
            break

    assert order_found, f"Order at ${order_price:.2f} not found in L2 depth"

    # Cancel the order
    logger.info("Cancelling order...")
    await reya_tester.client.cancel_order(
        order_id=order_id,
        symbol=SPOT_SYMBOL,
        account_id=reya_tester.account_id
    )
    await reya_tester.wait_for_order_state(order_id, OrderStatus.CANCELLED)

    # Wait for depth to update
    await asyncio.sleep(0.5)

    # Verify order removed from depth
    final_depth = await reya_tester.get_market_depth(SPOT_SYMBOL)
    final_bids = final_depth.get('bids', [])
    
    order_still_present = False
    for bid in final_bids:
        bid_price = float(bid.get('price', 0))
        if abs(bid_price - order_price) < 0.01:
            order_still_present = True
            break

    assert not order_still_present, f"Order at ${order_price:.2f} should be removed from depth"
    logger.info("✅ Order removed from depth after cancellation")

    logger.info("✅ SPOT ORDER BOOK TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.asyncio
async def test_spot_multiple_orders_aggregate_in_depth(reya_tester: ReyaTester):
    """
    Test that multiple orders at the same price aggregate in depth.
    
    Flow:
    1. Place two orders at the same price
    2. Verify depth shows aggregated quantity
    3. Cancel both orders
    """
    logger.info("=" * 80)
    logger.info(f"SPOT DEPTH AGGREGATION TEST: {SPOT_SYMBOL}")
    logger.info("=" * 80)

    # Clear any existing orders
    await reya_tester.check_no_open_orders()

    # Place two orders at the same price
    order_price = round(REFERENCE_PRICE * 0.80, 2)  # 20% below reference
    qty_per_order = "0.0001"
    
    order_ids = []
    for i in range(2):
        order_params = (
            OrderBuilder()
            .symbol(SPOT_SYMBOL)
            .buy()
            .price(str(order_price))
            .qty(qty_per_order)
            .gtc()
            .build()
        )
        
        order_id = await reya_tester.create_limit_order(order_params)
        await reya_tester.wait_for_order_creation(order_id)
        order_ids.append(order_id)
        logger.info(f"✅ Order {i+1} created: {order_id}")

    # Wait for depth to update
    await asyncio.sleep(0.5)

    # Get depth and verify aggregation
    depth = await reya_tester.get_market_depth(SPOT_SYMBOL)
    bids = depth.get('bids', [])
    
    aggregated_qty = None
    for bid in bids:
        bid_price = float(bid.get('price', 0))
        if abs(bid_price - order_price) < 0.01:
            aggregated_qty = float(bid.get('quantity', 0))
            break

    expected_total = float(qty_per_order) * 2
    assert aggregated_qty is not None, f"No bid found at ${order_price:.2f}"
    assert abs(aggregated_qty - expected_total) < 0.0001, (
        f"Aggregated qty should be {expected_total}, got {aggregated_qty}"
    )
    logger.info(f"✅ Depth shows aggregated qty: {aggregated_qty} (expected {expected_total})")

    # Cleanup - cancel all orders
    for order_id in order_ids:
        await reya_tester.client.cancel_order(
            order_id=order_id,
            symbol=SPOT_SYMBOL,
            account_id=reya_tester.account_id
        )
    
    await asyncio.sleep(0.3)
    await reya_tester.check_no_open_orders()

    logger.info("✅ SPOT DEPTH AGGREGATION TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_spot_bid_ask_spread(maker_tester: ReyaTester, taker_tester: ReyaTester):
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
    logger.info(f"SPOT BID/ASK SPREAD TEST: {SPOT_SYMBOL}")
    logger.info("=" * 80)

    # Clear any existing orders (fail_if_none=False since we're just cleaning up)
    await maker_tester.close_active_orders(fail_if_none=False)
    await taker_tester.close_active_orders(fail_if_none=False)

    # Use prices far from market to avoid matching existing liquidity
    bid_price = round(REFERENCE_PRICE * 0.50, 2)  # 50% below reference
    
    bid_params = (
        OrderBuilder()
        .symbol(SPOT_SYMBOL)
        .buy()
        .price(str(bid_price))
        .qty(TEST_QTY)
        .gtc()
        .build()
    )
    
    logger.info(f"Maker placing bid at ${bid_price:.2f}...")
    bid_order_id = await maker_tester.create_limit_order(bid_params)
    await maker_tester.wait_for_order_creation(bid_order_id)
    logger.info(f"✅ Bid order created: {bid_order_id}")

    # Taker places ask (sell) order at high price (taker has more ETH balance)
    ask_price = round(REFERENCE_PRICE * 1.50, 2)  # 50% above reference
    
    ask_params = (
        OrderBuilder()
        .symbol(SPOT_SYMBOL)
        .sell()
        .price(str(ask_price))
        .qty(TEST_QTY)
        .gtc()
        .build()
    )
    
    logger.info(f"Taker placing ask at ${ask_price:.2f}...")
    ask_order_id = await taker_tester.create_limit_order(ask_params)
    await taker_tester.wait_for_order_creation(ask_order_id)
    logger.info(f"✅ Ask order created: {ask_order_id}")

    # Wait for depth to update
    await asyncio.sleep(0.5)

    # Get depth
    depth = await maker_tester.get_market_depth(SPOT_SYMBOL)
    bids = depth.get('bids', [])
    asks = depth.get('asks', [])

    logger.info(f"Depth: {len(bids)} bids, {len(asks)} asks")

    # Find our orders
    our_bid = None
    our_ask = None
    
    for bid in bids:
        price = float(bid.get('price', 0))
        if abs(price - bid_price) < 1.0:  # Allow some tolerance
            our_bid = price
            logger.info(f"Found our bid at ${price:.2f}")
            break
    
    for ask in asks:
        price = float(ask.get('price', 0))
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
    await maker_tester.client.cancel_order(order_id=bid_order_id, symbol=SPOT_SYMBOL, account_id=maker_tester.account_id)
    await taker_tester.client.cancel_order(order_id=ask_order_id, symbol=SPOT_SYMBOL, account_id=taker_tester.account_id)
    
    await asyncio.sleep(0.3)
    await maker_tester.check_no_open_orders()
    await taker_tester.check_no_open_orders()

    logger.info("✅ SPOT BID/ASK SPREAD TEST COMPLETED")
