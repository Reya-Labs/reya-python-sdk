"""
Spot State Resilience Tests

Tests for verifying state consistency and resilience:
- WebSocket reconnection handling
- REST/WebSocket state consistency after activity
- Rapid order operations (stress test)

These tests validate that the backend's internal state (including Redis-based
order tracking with sequence numbers) correctly propagates to API surfaces.
"""

import asyncio
import pytest
import logging

from sdk.open_api.models import OrderStatus

from tests.helpers import ReyaTester
from tests.helpers.builders import OrderBuilder

logger = logging.getLogger("reya.integration_tests")

SPOT_SYMBOL = "WETHRUSD"
REFERENCE_PRICE = 500.0
TEST_QTY = "0.01"  # Minimum order base for market ID 5


@pytest.mark.spot
@pytest.mark.websocket
@pytest.mark.asyncio
async def test_spot_order_survives_ws_reconnect(reya_tester: ReyaTester):
    """
    Test that open orders are correctly reflected after WebSocket reconnection.
    
    This tests that the backend correctly maintains order state and provides
    accurate snapshots when a client reconnects. This is critical for scenarios
    like ME restarts where the backend's Redis state is rebuilt.
    
    Flow:
    1. Place GTC order
    2. Verify order in open orders via REST and WS
    3. Disconnect WebSocket (real disconnect)
    4. Verify order still exists via REST (backend state intact)
    5. Reconnect WebSocket
    6. Verify WebSocket receives order updates for new activity
    7. Cancel order and verify cleanup
    """
    logger.info("=" * 80)
    logger.info(f"SPOT ORDER SURVIVES WS RECONNECT TEST: {SPOT_SYMBOL}")
    logger.info("=" * 80)

    # Clear any existing orders
    await reya_tester.close_active_orders(fail_if_none=False)

    # Step 1: Place GTC order
    order_price = round(REFERENCE_PRICE * 0.45, 2)  # Far from market
    
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

    # Step 2: Verify order exists via REST and WS
    open_orders = await reya_tester.client.get_open_orders()
    order_ids_before = {o.order_id for o in open_orders if o.symbol == SPOT_SYMBOL}
    assert order_id in order_ids_before, f"Order {order_id} should be in REST open orders"
    assert order_id in reya_tester.ws_order_changes, f"Order {order_id} should be in WS order changes"
    logger.info(f"✅ Order confirmed in both REST and WS: {order_id}")

    # Step 3: Disconnect WebSocket (real disconnect)
    logger.info("Disconnecting WebSocket...")
    if reya_tester._websocket:
        reya_tester._websocket.close()
    
    # Wait for disconnect to complete
    await asyncio.sleep(0.5)
    logger.info("✅ WebSocket disconnected")

    # Step 4: Verify order still exists via REST (backend state should be intact)
    open_orders_after = await reya_tester.client.get_open_orders()
    order_ids_after = {o.order_id for o in open_orders_after if o.symbol == SPOT_SYMBOL}
    assert order_id in order_ids_after, (
        f"Order {order_id} should still exist in REST after WS disconnect. "
        f"This validates backend state persistence."
    )
    logger.info(f"✅ Order still exists in REST after WS disconnect: {order_id}")

    # Step 5: Reconnect WebSocket
    logger.info("Reconnecting WebSocket...")
    
    # Clear old state before reconnect
    reya_tester.ws.clear()
    
    # Create new WebSocket connection
    import os
    from sdk.reya_websocket import ReyaSocket
    
    ws_url = os.environ.get("REYA_WS_URL", "wss://ws.reya.xyz/")
    reya_tester._websocket = ReyaSocket(
        url=ws_url,
        on_open=reya_tester.ws.on_open,
        on_message=reya_tester.ws.on_message,
    )
    reya_tester._websocket.connect()
    
    # Wait for connection and subscriptions to be established
    await asyncio.sleep(0.5)
    logger.info("✅ WebSocket reconnected")

    # Step 6: Verify WebSocket receives updates for new activity
    # Place another order to trigger WebSocket activity
    order_price_2 = round(REFERENCE_PRICE * 0.44, 2)
    order_params_2 = (
        OrderBuilder()
        .symbol(SPOT_SYMBOL)
        .buy()
        .price(str(order_price_2))
        .qty(TEST_QTY)
        .gtc()
        .build()
    )

    logger.info(f"Placing second order to verify WS receives updates after reconnect...")
    order_id_2 = await reya_tester.create_limit_order(order_params_2)
    await reya_tester.wait_for_order_creation(order_id_2)
    
    # Verify new order appears in WebSocket state
    assert order_id_2 in reya_tester.ws_order_changes, (
        f"New order {order_id_2} should appear in WS order changes after reconnect"
    )
    logger.info(f"✅ WebSocket receiving updates after reconnect: {order_id_2}")

    # Step 7: Cleanup - cancel both orders
    await reya_tester.client.cancel_order(
        order_id=order_id,
        symbol=SPOT_SYMBOL,
        account_id=reya_tester.account_id
    )
    await reya_tester.client.cancel_order(
        order_id=order_id_2,
        symbol=SPOT_SYMBOL,
        account_id=reya_tester.account_id
    )
    
    await asyncio.sleep(0.1)
    await reya_tester.check_no_open_orders()

    logger.info("✅ SPOT ORDER SURVIVES WS RECONNECT TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.websocket
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_spot_ws_rest_consistency_after_activity(
    maker_tester: ReyaTester, taker_tester: ReyaTester
):
    """
    Test that WebSocket and REST API remain consistent after multiple operations.
    
    This validates that the backend's internal state (including Redis-based
    order tracking with sequence numbers) correctly propagates to both API surfaces.
    
    Flow:
    1. Place multiple maker orders
    2. Execute partial fills via taker
    3. Cancel remaining orders
    4. Compare final WS state vs REST state
    5. Verify complete consistency
    """
    logger.info("=" * 80)
    logger.info(f"SPOT WS/REST CONSISTENCY TEST: {SPOT_SYMBOL}")
    logger.info("=" * 80)

    await maker_tester.close_active_orders(fail_if_none=False)
    await taker_tester.close_active_orders(fail_if_none=False)

    # Clear WebSocket tracking
    maker_tester.ws_order_changes.clear()

    # Step 1: Place multiple maker orders at different prices
    maker_prices = [
        round(REFERENCE_PRICE * 0.60, 2),
        round(REFERENCE_PRICE * 0.61, 2),
        round(REFERENCE_PRICE * 0.62, 2),
    ]
    
    maker_order_ids = []
    for price in maker_prices:
        order_params = (
            OrderBuilder()
            .symbol(SPOT_SYMBOL)
            .buy()
            .price(str(price))
            .qty(TEST_QTY)
            .gtc()
            .build()
        )
        
        order_id = await maker_tester.create_limit_order(order_params)
        await maker_tester.wait_for_order_creation(order_id)
        maker_order_ids.append(order_id)
        logger.info(f"✅ Maker order created at ${price:.2f}: {order_id}")

    # Wait for all orders to be indexed
    await asyncio.sleep(0.2)

    # Step 2: Verify all orders appear in both REST and WS
    rest_orders = await maker_tester.client.get_open_orders()
    rest_order_ids = {o.order_id for o in rest_orders if o.symbol == SPOT_SYMBOL}
    ws_order_ids = set(maker_tester.ws_order_changes.keys())

    for order_id in maker_order_ids:
        assert order_id in rest_order_ids, f"Order {order_id} should be in REST"
        assert order_id in ws_order_ids, f"Order {order_id} should be in WS"
    
    logger.info(f"✅ All {len(maker_order_ids)} orders confirmed in both REST and WS")

    # Step 3: Execute a fill on the best price order (highest price = first to match)
    taker_price = round(maker_prices[-1] * 0.99, 2)  # Below best maker price
    
    taker_params = (
        OrderBuilder()
        .symbol(SPOT_SYMBOL)
        .sell()
        .price(str(taker_price))
        .qty(TEST_QTY)
        .ioc()
        .build()
    )

    logger.info(f"Taker placing IOC sell to fill one maker order...")
    await taker_tester.create_limit_order(taker_params)
    
    # Wait for fill to propagate
    await asyncio.sleep(0.3)

    # Step 4: Verify one order was filled
    rest_orders_after_fill = await maker_tester.client.get_open_orders()
    rest_order_ids_after = {o.order_id for o in rest_orders_after_fill if o.symbol == SPOT_SYMBOL}
    
    filled_count = len(maker_order_ids) - len(rest_order_ids_after)
    assert filled_count == 1, f"Expected 1 order filled, got {filled_count}"
    logger.info(f"✅ One order filled, {len(rest_order_ids_after)} remaining")

    # Step 5: Verify WS shows filled status for the matched order
    filled_order_id = None
    for order_id in maker_order_ids:
        if order_id not in rest_order_ids_after:
            filled_order_id = order_id
            break
    
    assert filled_order_id is not None, "Should have found filled order"
    
    ws_order = maker_tester.ws_order_changes.get(filled_order_id)
    assert ws_order is not None, f"Filled order {filled_order_id} should be in WS"
    ws_status = ws_order.status.value if hasattr(ws_order.status, 'value') else ws_order.status
    assert ws_status == "FILLED", f"WS should show FILLED status, got {ws_status}"
    logger.info(f"✅ WS correctly shows FILLED status for {filled_order_id}")

    # Step 6: Cancel remaining orders
    remaining_order_ids = [oid for oid in maker_order_ids if oid in rest_order_ids_after]
    for order_id in remaining_order_ids:
        await maker_tester.client.cancel_order(
            order_id=order_id,
            symbol=SPOT_SYMBOL,
            account_id=maker_tester.account_id
        )
    
    await asyncio.sleep(0.2)

    # Step 7: Final consistency check
    final_rest_orders = await maker_tester.client.get_open_orders()
    final_rest_ids = {o.order_id for o in final_rest_orders if o.symbol == SPOT_SYMBOL}
    
    # All our orders should be gone from REST
    for order_id in maker_order_ids:
        assert order_id not in final_rest_ids, f"Order {order_id} should not be in REST"
    
    # WS should show CANCELLED for remaining orders
    for order_id in remaining_order_ids:
        ws_order = maker_tester.ws_order_changes.get(order_id)
        assert ws_order is not None, f"Order {order_id} should be in WS"
        ws_status = ws_order.status.value if hasattr(ws_order.status, 'value') else ws_order.status
        assert ws_status == "CANCELLED", f"WS should show CANCELLED for {order_id}, got {ws_status}"
    
    logger.info("✅ Final state: REST and WS are consistent")

    await maker_tester.check_no_open_orders()
    await taker_tester.check_no_open_orders()

    logger.info("✅ SPOT WS/REST CONSISTENCY TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.asyncio
async def test_spot_rapid_order_operations(reya_tester: ReyaTester):
    """
    Test rapid order creation and cancellation.
    
    This stress-tests the backend's event ordering and ensures
    sequence-based processing handles high-frequency operations correctly.
    
    Flow:
    1. Rapidly create 10 orders
    2. Verify all appear in open orders
    3. Rapidly cancel all orders
    4. Verify all cancelled
    5. Verify no orphaned state
    """
    logger.info("=" * 80)
    logger.info(f"SPOT RAPID ORDER OPERATIONS TEST: {SPOT_SYMBOL}")
    logger.info("=" * 80)

    await reya_tester.close_active_orders(fail_if_none=False)

    # Clear WebSocket tracking
    reya_tester.ws_order_changes.clear()

    # Step 1: Rapidly create 10 orders at different prices
    num_orders = 10
    base_price = round(REFERENCE_PRICE * 0.40, 2)
    
    order_ids = []
    logger.info(f"Creating {num_orders} orders rapidly...")
    
    for i in range(num_orders):
        order_price = round(base_price + (i * 0.5), 2)  # 0.5 increments
        
        order_params = (
            OrderBuilder()
            .symbol(SPOT_SYMBOL)
            .buy()
            .price(str(order_price))
            .qty(TEST_QTY)
            .gtc()
            .build()
        )
        
        order_id = await reya_tester.create_limit_order(order_params)
        order_ids.append(order_id)
        logger.debug(f"  Created order {i+1}/{num_orders}: {order_id} @ ${order_price}")
        
        # Small delay to avoid overwhelming the system
        await asyncio.sleep(0.02)

    logger.info(f"✅ Created {len(order_ids)} orders")

    # Step 2: Wait for all orders to be confirmed
    await asyncio.sleep(0.5)

    # Verify all orders appear in REST
    rest_orders = await reya_tester.client.get_open_orders()
    rest_order_ids = {o.order_id for o in rest_orders if o.symbol == SPOT_SYMBOL}
    
    missing_in_rest = [oid for oid in order_ids if oid not in rest_order_ids]
    assert len(missing_in_rest) == 0, (
        f"All orders should appear in REST. Missing: {missing_in_rest}"
    )
    logger.info(f"✅ All {num_orders} orders confirmed in REST")

    # Verify all orders appear in WebSocket
    ws_order_ids = set(reya_tester.ws_order_changes.keys())
    missing_in_ws = [oid for oid in order_ids if oid not in ws_order_ids]
    assert len(missing_in_ws) == 0, (
        f"All orders should appear in WS. Missing: {missing_in_ws}"
    )
    logger.info(f"✅ All {num_orders} orders confirmed in WS")

    # Step 3: Rapidly cancel all orders
    logger.info(f"Cancelling {num_orders} orders rapidly...")
    
    for i, order_id in enumerate(order_ids):
        await reya_tester.client.cancel_order(
            order_id=order_id,
            symbol=SPOT_SYMBOL,
            account_id=reya_tester.account_id
        )
        logger.debug(f"  Cancelled order {i+1}/{num_orders}: {order_id}")
        
        # Small delay to avoid overwhelming the system
        await asyncio.sleep(0.02)

    logger.info(f"✅ Sent cancel requests for {len(order_ids)} orders")

    # Step 4: Wait for all cancellations to propagate
    await asyncio.sleep(0.5)

    # Verify all orders are cancelled in REST
    final_rest_orders = await reya_tester.client.get_open_orders()
    final_rest_ids = {o.order_id for o in final_rest_orders if o.symbol == SPOT_SYMBOL}
    
    still_open_in_rest = [oid for oid in order_ids if oid in final_rest_ids]
    assert len(still_open_in_rest) == 0, (
        f"All orders should be cancelled in REST. Still open: {still_open_in_rest}"
    )
    logger.info(f"✅ All {num_orders} orders cancelled in REST")

    # Verify all orders show CANCELLED in WebSocket
    not_cancelled_in_ws = []
    for order_id in order_ids:
        ws_order = reya_tester.ws_order_changes.get(order_id)
        if ws_order:
            ws_status = ws_order.status.value if hasattr(ws_order.status, 'value') else ws_order.status
            if ws_status != "CANCELLED":
                not_cancelled_in_ws.append((order_id, ws_status))
    
    assert len(not_cancelled_in_ws) == 0, (
        f"All orders should show CANCELLED in WS. Not cancelled: {not_cancelled_in_ws}"
    )
    logger.info(f"✅ All {num_orders} orders show CANCELLED in WS")

    # Step 5: Final verification - no orphaned state
    await reya_tester.check_no_open_orders()
    
    logger.info("✅ No orphaned state detected")
    logger.info("✅ SPOT RAPID ORDER OPERATIONS TEST COMPLETED")
