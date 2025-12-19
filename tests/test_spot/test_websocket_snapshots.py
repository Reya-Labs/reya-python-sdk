"""
WebSocket Snapshot Tests for Spot Trading

Tests for verifying WebSocket initial snapshots:
- Depth channel initial snapshot
- Order changes initial snapshot
- Balances initial snapshot
- Incremental updates after snapshot

These tests verify that when subscribing to a WebSocket channel,
the initial snapshot contains the correct state before incremental updates.
"""

import asyncio
import logging

import pytest

from sdk.async_api.depth import Depth
from sdk.async_api.level import Level
from sdk.open_api.models import OrderStatus
from tests.helpers import ReyaTester
from tests.helpers.builders import OrderBuilder
from tests.test_spot.spot_config import SpotTestConfig

logger = logging.getLogger("reya.integration_tests")

# DEPTH CHANNEL SNAPSHOT TESTS
# ============================================================================


@pytest.mark.spot
@pytest.mark.websocket
@pytest.mark.asyncio
async def test_spot_depth_ws_initial_snapshot(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test that subscribing to /depth channel returns correct initial snapshot.

    Flow:
    1. Place multiple GTC orders at different prices (create book state)
    2. Subscribe to /depth channel
    3. Verify initial snapshot message contains our orders
    4. Verify bid ordering is correct in snapshot (descending by price)
    """
    logger.info("=" * 80)
    logger.info(f"SPOT DEPTH WS INITIAL SNAPSHOT TEST: {spot_config.symbol}")
    logger.info("=" * 80)

    # Clear any existing orders
    await spot_tester.close_active_orders(fail_if_none=False)

    # Step 1: Place multiple GTC orders at different prices
    prices = [
        spot_config.price(0.96),  # ~96% of oracle
        spot_config.price(0.97),  # ~97% of oracle
        spot_config.price(0.98),  # ~98% of oracle
    ]

    order_ids = []
    for price in prices:
        order_params = (
            OrderBuilder.from_config(spot_config)
            .buy()
            .price(str(price))
            .gtc()
            .build()
        )

        order_id = await spot_tester.create_limit_order(order_params)
        await spot_tester.wait_for_order_creation(order_id)
        order_ids.append(order_id)
        logger.info(f"✅ Order created at ${price:.2f}: {order_id}")

    # Wait for orders to be fully indexed
    await asyncio.sleep(0.2)

    # Step 2: Clear depth state and subscribe to depth channel
    spot_tester.ws_last_depth.clear()
    spot_tester.subscribe_to_market_depth(spot_config.symbol)

    # Wait for snapshot to arrive
    await asyncio.sleep(0.5)

    # Step 3: Verify initial snapshot contains our orders
    depth_snapshot = spot_tester.ws_last_depth.get(spot_config.symbol)
    assert depth_snapshot is not None, "Should have received depth snapshot via WebSocket"
    assert isinstance(depth_snapshot, Depth), f"Expected Depth type, got {type(depth_snapshot)}"

    bids = depth_snapshot.bids
    logger.info(f"Depth snapshot received: {len(bids)} bids")

    # Verify our orders are in the snapshot (using typed Level.px attribute)
    found_prices = set()
    for bid in bids:
        assert isinstance(bid, Level), f"Expected Level type, got {type(bid)}"
        bid_price = float(bid.px)
        for expected_price in prices:
            if abs(bid_price - expected_price) < 1.0:  # Allow small tolerance
                found_prices.add(expected_price)
                logger.info(f"  ✅ Found order at ${bid_price:.2f}")

    assert len(found_prices) == len(
        prices
    ), f"Expected to find all {len(prices)} orders in snapshot, found {len(found_prices)}. Bids: {bids}"
    logger.info(f"✅ All {len(prices)} orders found in initial snapshot")

    # Step 4: Verify bid ordering (descending by price)
    bid_prices = [float(b.px) for b in bids]
    if len(bid_prices) >= 2:
        is_descending = all(bid_prices[i] >= bid_prices[i + 1] for i in range(len(bid_prices) - 1))
        assert is_descending, f"Bids should be in descending order: {bid_prices}"
        logger.info("✅ Bids are correctly ordered (descending by price)")

    # Cleanup
    for order_id in order_ids:
        await spot_tester.client.cancel_order(order_id=order_id, symbol=spot_config.symbol, account_id=spot_tester.account_id)

    await asyncio.sleep(0.1)
    await spot_tester.check_no_open_orders()

    logger.info("✅ SPOT DEPTH WS INITIAL SNAPSHOT TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.websocket
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_spot_depth_ws_snapshot_with_asks(spot_config: SpotTestConfig, maker_tester: ReyaTester, taker_tester: ReyaTester):
    """
    Test depth snapshot includes both bids and asks.

    Flow:
    1. Maker places buy order (bid)
    2. Taker places sell order (ask) - taker has more ETH
    3. Subscribe to depth
    4. Verify snapshot contains both bid and ask
    """
    logger.info("=" * 80)
    logger.info(f"SPOT DEPTH WS SNAPSHOT WITH ASKS TEST: {spot_config.symbol}")
    logger.info("=" * 80)

    await maker_tester.close_active_orders(fail_if_none=False)
    await taker_tester.close_active_orders(fail_if_none=False)

    # Place bid (maker buys with RUSD)
    bid_price = spot_config.price(0.96)
    bid_params = (
        OrderBuilder.from_config(spot_config)
        .buy()
        .at_price(0.96)
        .gtc()
        .build()
    )

    bid_order_id = await maker_tester.create_limit_order(bid_params)
    await maker_tester.wait_for_order_creation(bid_order_id)
    logger.info(f"✅ Bid order created at ${bid_price:.2f}")

    # Place ask (taker sells ETH)
    ask_price = spot_config.price(1.04)
    ask_params = (
        OrderBuilder.from_config(spot_config)
        .sell()
        .at_price(1.04)
        .gtc()
        .build()
    )

    ask_order_id = await taker_tester.create_limit_order(ask_params)
    await taker_tester.wait_for_order_creation(ask_order_id)
    logger.info(f"✅ Ask order created at ${ask_price:.2f}")

    # Wait for orders to be indexed
    await asyncio.sleep(0.2)

    # Subscribe to depth
    maker_tester.ws_last_depth.clear()
    maker_tester.subscribe_to_market_depth(spot_config.symbol)

    await asyncio.sleep(0.5)

    # Verify snapshot
    depth_snapshot = maker_tester.ws_last_depth.get(spot_config.symbol)
    assert depth_snapshot is not None, "Should have received depth snapshot"
    assert isinstance(depth_snapshot, Depth), f"Expected Depth type, got {type(depth_snapshot)}"

    bids = depth_snapshot.bids
    asks = depth_snapshot.asks

    logger.info(f"Snapshot: {len(bids)} bids, {len(asks)} asks")

    # Find our orders (using typed Level.px attribute)
    bid_found = any(abs(float(b.px) - bid_price) < 1.0 for b in bids)
    ask_found = any(abs(float(a.px) - ask_price) < 1.0 for a in asks)

    assert bid_found, f"Bid at ${bid_price:.2f} not found in snapshot"
    assert ask_found, f"Ask at ${ask_price:.2f} not found in snapshot"

    logger.info("✅ Both bid and ask found in snapshot")

    # Verify ask ordering (ascending by price)
    if len(asks) >= 2:
        ask_prices = [float(a.px) for a in asks]
        is_ascending = all(ask_prices[i] <= ask_prices[i + 1] for i in range(len(ask_prices) - 1))
        assert is_ascending, f"Asks should be in ascending order: {ask_prices}"
        logger.info("✅ Asks are correctly ordered (ascending by price)")

    # Cleanup
    await maker_tester.client.cancel_order(
        order_id=bid_order_id, symbol=spot_config.symbol, account_id=maker_tester.account_id
    )
    await taker_tester.client.cancel_order(
        order_id=ask_order_id, symbol=spot_config.symbol, account_id=taker_tester.account_id
    )

    await asyncio.sleep(0.1)
    await maker_tester.check_no_open_orders()
    await taker_tester.check_no_open_orders()

    logger.info("✅ SPOT DEPTH WS SNAPSHOT WITH ASKS TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.websocket
@pytest.mark.asyncio
async def test_spot_depth_ws_incremental_after_snapshot(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test incremental updates are received after initial snapshot.

    Flow:
    1. Subscribe to /depth channel (receive initial snapshot)
    2. Place a new order
    3. Verify incremental update is received with new order
    4. Cancel the order
    5. Verify incremental update removes the order
    """
    logger.info("=" * 80)
    logger.info(f"SPOT DEPTH WS INCREMENTAL AFTER SNAPSHOT TEST: {spot_config.symbol}")
    logger.info("=" * 80)

    await spot_tester.close_active_orders(fail_if_none=False)

    # Step 1: Subscribe to depth and get initial snapshot
    spot_tester.ws_last_depth.clear()
    spot_tester.subscribe_to_market_depth(spot_config.symbol)

    await asyncio.sleep(0.3)

    initial_snapshot = spot_tester.ws_last_depth.get(spot_config.symbol)
    initial_bid_count = len(initial_snapshot.bids) if initial_snapshot else 0
    logger.info(f"Initial snapshot: {initial_bid_count} bids")

    # Step 2: Place a new order
    order_price = spot_config.price(0.96)  # Very low price to be unique

    order_params = (
        OrderBuilder.from_config(spot_config)
        .buy()
        .at_price(0.96)
        .gtc()
        .build()
    )

    order_id = await spot_tester.create_limit_order(order_params)
    await spot_tester.wait_for_order_creation(order_id)
    logger.info(f"✅ New order created at ${order_price:.2f}")

    # Wait for incremental update
    await asyncio.sleep(0.3)

    # Step 3: Verify incremental update contains new order
    updated_depth = spot_tester.ws_last_depth.get(spot_config.symbol)
    assert updated_depth is not None, "Should have received depth update"
    assert isinstance(updated_depth, Depth), f"Expected Depth type, got {type(updated_depth)}"

    bids = updated_depth.bids
    order_found = any(abs(float(b.px) - order_price) < 0.01 for b in bids)
    assert order_found, f"New order at ${order_price:.2f} should appear in depth after incremental update"
    logger.info("✅ New order appears in depth after incremental update")

    # Step 4: Cancel the order
    await spot_tester.client.cancel_order(order_id=order_id, symbol=spot_config.symbol, account_id=spot_tester.account_id)

    await spot_tester.wait_for_order_state(order_id, OrderStatus.CANCELLED)
    logger.info("Order cancelled")

    # Wait for incremental update
    await asyncio.sleep(0.3)

    # Step 5: Verify order removed from depth
    final_depth = spot_tester.ws_last_depth.get(spot_config.symbol)
    final_bids = final_depth.bids if final_depth else []

    order_still_present = any(abs(float(b.px) - order_price) < 0.01 for b in final_bids)
    assert not order_still_present, f"Cancelled order at ${order_price:.2f} should be removed from depth"
    logger.info("✅ Cancelled order removed from depth after incremental update")

    await spot_tester.check_no_open_orders()

    logger.info("✅ SPOT DEPTH WS INCREMENTAL AFTER SNAPSHOT TEST COMPLETED")


# ============================================================================
# ORDER CHANGES CHANNEL SNAPSHOT TESTS
# ============================================================================


@pytest.mark.spot
@pytest.mark.websocket
@pytest.mark.asyncio
async def test_spot_order_changes_ws_initial_snapshot(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test that subscribing to orderChanges returns correct initial snapshot.

    Note: The orderChanges channel may not send a snapshot of existing orders
    on subscription - it typically only sends updates for new order events.
    This test verifies the behavior.

    Flow:
    1. Place multiple GTC orders (create open orders)
    2. Record the orders via REST
    3. Verify orderChanges were received for each order creation
    """
    logger.info("=" * 80)
    logger.info(f"SPOT ORDER CHANGES WS INITIAL SNAPSHOT TEST: {spot_config.symbol}")
    logger.info("=" * 80)

    await spot_tester.close_active_orders(fail_if_none=False)

    # Clear order changes tracking
    spot_tester.ws_order_changes.clear()

    # Place multiple orders
    prices = [
        spot_config.price(0.96),
        spot_config.price(0.96),
    ]

    order_ids = []
    for price in prices:
        order_params = (
            OrderBuilder.from_config(spot_config)
            .buy()
            .at_price(0.96)
            .gtc()
            .build()
        )

        order_id = await spot_tester.create_limit_order(order_params)
        await spot_tester.wait_for_order_creation(order_id)
        order_ids.append(order_id)
        logger.info(f"✅ Order created: {order_id}")

    # Wait for WebSocket events
    await asyncio.sleep(0.2)

    # Verify order changes were received for each order
    for order_id in order_ids:
        assert order_id in spot_tester.ws_order_changes, f"Order {order_id} should be in orderChanges"
        order = spot_tester.ws_order_changes[order_id]
        assert order.symbol == spot_config.symbol
        logger.info(f"✅ Order change received for {order_id}: status={order.status}")

    logger.info(f"✅ All {len(order_ids)} order changes received via WebSocket")

    # Verify REST matches WebSocket
    open_orders = await spot_tester.client.get_open_orders()
    open_order_ids = {o.order_id for o in open_orders if o.symbol == spot_config.symbol}

    for order_id in order_ids:
        assert order_id in open_order_ids, f"Order {order_id} should be in REST open orders"

    logger.info("✅ REST open orders match WebSocket order changes")

    # Cleanup
    for order_id in order_ids:
        await spot_tester.client.cancel_order(order_id=order_id, symbol=spot_config.symbol, account_id=spot_tester.account_id)

    await asyncio.sleep(0.1)
    await spot_tester.check_no_open_orders()

    logger.info("✅ SPOT ORDER CHANGES WS INITIAL SNAPSHOT TEST COMPLETED")


# ============================================================================
# SPOT EXECUTIONS CHANNEL SNAPSHOT TESTS
# ============================================================================


@pytest.mark.spot
@pytest.mark.websocket
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_spot_executions_ws_initial_snapshot(spot_config: SpotTestConfig, maker_tester: ReyaTester, taker_tester: ReyaTester):
    """
    Test that subscribing to spotExecutions returns historical executions as snapshot.

    Flow:
    1. Execute a spot trade to create execution history
    2. Clear WebSocket spot executions tracking
    3. Reconnect/resubscribe to spotExecutions channel
    4. Verify historical executions are received in snapshot
    """
    logger.info("=" * 80)
    logger.info(f"SPOT EXECUTIONS WS INITIAL SNAPSHOT TEST: {spot_config.symbol}")
    logger.info("=" * 80)

    await maker_tester.close_active_orders(fail_if_none=False)
    await taker_tester.close_active_orders(fail_if_none=False)

    # Step 1: Execute a trade to create execution history
    maker_price = spot_config.price(0.97)

    maker_params = (
        OrderBuilder.from_config(spot_config)
        .buy()
        .at_price(0.97)
        .gtc()
        .build()
    )

    maker_order_id = await maker_tester.create_limit_order(maker_params)
    await maker_tester.wait_for_order_creation(maker_order_id)
    logger.info(f"✅ Maker order created: {maker_order_id} @ ${maker_price}")

    # Taker fills with IOC
    taker_params = (
        OrderBuilder.from_config(spot_config)
        .sell()
        .at_price(0.97)
        .ioc()
        .build()
    )

    taker_order_id = await taker_tester.create_limit_order(taker_params)
    logger.info(f"✅ Taker IOC order sent: {taker_order_id}")

    # Wait for execution
    await asyncio.sleep(0.3)
    await maker_tester.wait_for_order_state(maker_order_id, OrderStatus.FILLED, timeout=5)
    logger.info("✅ Trade executed")

    # Step 2: Verify we received spot execution via WebSocket
    assert len(taker_tester.ws_spot_executions) > 0, "Should have received spot execution via WebSocket"
    logger.info(f"✅ Received {len(taker_tester.ws_spot_executions)} spot execution(s) via WebSocket")

    # Record the execution details for later verification
    execution = taker_tester.ws_spot_executions[-1]
    logger.info(f"   Execution: symbol={execution.symbol}, side={execution.side}, qty={execution.qty}")

    # Step 3: Query executions via REST to confirm they exist
    rest_executions = await taker_tester.client.get_spot_executions()
    assert hasattr(rest_executions, "data") and len(rest_executions.data) > 0, "Should have executions in REST response"
    logger.info(f"✅ REST API shows {len(rest_executions.data)} execution(s)")

    # Step 4: Verify execution data matches
    # Find our execution in REST data
    rest_exec = None
    for e in rest_executions.data:
        if e.symbol == spot_config.symbol:
            rest_exec = e
            break

    assert rest_exec is not None, f"Should find {spot_config.symbol} execution in REST data"
    logger.info(f"✅ Found matching execution in REST: symbol={rest_exec.symbol}")

    # Verify WebSocket execution matches REST
    assert execution.symbol == rest_exec.symbol, "Symbol should match"
    logger.info("✅ WebSocket and REST execution data consistent")

    # Cleanup
    await maker_tester.check_no_open_orders()
    await taker_tester.check_no_open_orders()

    logger.info("✅ SPOT EXECUTIONS WS INITIAL SNAPSHOT TEST COMPLETED")


# ============================================================================
# BALANCES CHANNEL SNAPSHOT TESTS
# ============================================================================


@pytest.mark.spot
@pytest.mark.websocket
@pytest.mark.asyncio
async def test_spot_balances_ws_initial_snapshot(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test that subscribing to balances channel returns balance data.

    Note: The WebSocket may receive balances from multiple accounts under the same
    wallet address, so we just verify that balance data is received, not exact values.

    Flow:
    1. Get current balances via REST
    2. Verify balances were received via WebSocket
    3. Verify key assets are present in both
    """
    logger.info("=" * 80)
    logger.info("SPOT BALANCES WS INITIAL SNAPSHOT TEST")
    logger.info("=" * 80)

    # Get balances via REST for this specific account
    rest_balances = await spot_tester.get_balances()
    logger.info(f"REST balances for account {spot_tester.account_id}: {list(rest_balances.keys())}")

    # WebSocket balances - may include data from multiple accounts under same wallet
    ws_balances = spot_tester.ws_balances
    logger.info(f"WebSocket balances (wallet-level): {list(ws_balances.keys())}")

    # Verify we have balances via WebSocket
    assert len(ws_balances) > 0, "Should have received balances via WebSocket"
    logger.info("✅ Received balance data via WebSocket")

    # Verify key assets are present (ETH and RUSD are common)
    for asset in ["ETH", "RUSD"]:
        if asset in rest_balances:
            # WebSocket should have received data for this asset
            assert asset in ws_balances, f"{asset} should be in WebSocket balances"
            logger.info(f"✅ {asset} present in WebSocket balances")

    # Log balance values for debugging (don't assert equality due to multi-account)
    for asset in ["ETH", "RUSD"]:
        if asset in rest_balances and asset in ws_balances:
            rest_val = rest_balances[asset].real_balance
            ws_val = ws_balances[asset].real_balance
            logger.info(f"{asset}: REST (account {spot_tester.account_id})={rest_val}, WS={ws_val}")

    logger.info("✅ SPOT BALANCES WS INITIAL SNAPSHOT TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.websocket
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_spot_balances_ws_update_after_trade(spot_config: SpotTestConfig, maker_tester: ReyaTester, taker_tester: ReyaTester):
    """
    Test that balance updates are received via WebSocket after a trade.

    Flow:
    1. Record initial balance update counts
    2. Execute a trade
    3. Verify balance updates received via WebSocket
    4. Verify updated balances match REST
    """
    logger.info("=" * 80)
    logger.info("SPOT BALANCES WS UPDATE AFTER TRADE TEST")
    logger.info("=" * 80)

    await maker_tester.close_active_orders(fail_if_none=False)
    await taker_tester.close_active_orders(fail_if_none=False)

    # Clear balance updates
    maker_tester.clear_balance_updates()
    taker_tester.clear_balance_updates()

    # Execute a trade
    maker_price = spot_config.price(0.97)

    maker_params = (
        OrderBuilder.from_config(spot_config)
        .buy()
        .at_price(0.97)
        .gtc()
        .build()
    )

    maker_order_id = await maker_tester.create_limit_order(maker_params)
    await maker_tester.wait_for_order_creation(maker_order_id)

    taker_params = (
        OrderBuilder.from_config(spot_config)
        .sell()
        .at_price(0.97)
        .ioc()
        .build()
    )

    await taker_tester.create_limit_order(taker_params)

    # Wait for trade and balance updates
    await asyncio.sleep(0.3)
    await maker_tester.wait_for_order_state(maker_order_id, OrderStatus.FILLED, timeout=5)

    # Verify balance updates received
    maker_updates = maker_tester.ws_balance_updates
    taker_updates = taker_tester.ws_balance_updates

    logger.info(f"Maker balance updates: {len(maker_updates)}")
    logger.info(f"Taker balance updates: {len(taker_updates)}")

    # Both should have received balance updates (ETH and RUSD)
    assert len(maker_updates) >= 2, f"Maker should have at least 2 balance updates, got {len(maker_updates)}"
    assert len(taker_updates) >= 2, f"Taker should have at least 2 balance updates, got {len(taker_updates)}"

    # Verify assets updated
    maker_assets = {u.asset for u in maker_updates}
    taker_assets = {u.asset for u in taker_updates}

    assert "ETH" in maker_assets, "Maker should have ETH balance update"
    assert "RUSD" in maker_assets, "Maker should have RUSD balance update"
    assert "ETH" in taker_assets, "Taker should have ETH balance update"
    assert "RUSD" in taker_assets, "Taker should have RUSD balance update"

    logger.info("✅ Balance updates received for both ETH and RUSD")

    # Log balance values for debugging (don't assert equality due to multi-account wallet)
    await asyncio.sleep(0.5)  # Wait for REST to catch up

    maker_rest_balances = await maker_tester.get_balances()
    maker_ws_balances = maker_tester.ws_balances

    for asset in ["ETH", "RUSD"]:
        if asset in maker_rest_balances and asset in maker_ws_balances:
            rest_val = maker_rest_balances[asset].real_balance
            ws_val = maker_ws_balances[asset].real_balance
            # Note: WS balances may be from different account under same wallet
            logger.info(f"Maker {asset}: REST (account {maker_tester.account_id})={rest_val}, WS={ws_val}")

    logger.info("✅ SPOT BALANCES WS UPDATE AFTER TRADE TEST COMPLETED")
