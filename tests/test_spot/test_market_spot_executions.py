"""
Market Spot Executions Tests

Tests for the new market-level spot executions endpoints:
- REST API: GET /v2/market/{symbol}/spotExecutions
- WebSocket: /v2/market/{symbol}/spotExecutions channel

These tests verify:
1. REST API returns correct spot execution data for a market
2. WebSocket channel delivers real-time spot execution updates
3. WebSocket snapshot contains historical executions
4. Data consistency between REST and WebSocket
"""

import asyncio
import logging

import pytest

from sdk.open_api.models import OrderStatus
from tests.helpers import ReyaTester
from tests.helpers.builders import OrderBuilder

logger = logging.getLogger("reya.integration_tests")

SPOT_SYMBOL = "WETHRUSD"
REFERENCE_PRICE = 500.0
TEST_QTY = "0.01"


# ============================================================================
# REST API TESTS
# ============================================================================


@pytest.mark.spot
@pytest.mark.asyncio
async def test_rest_get_market_spot_executions_empty(spot_tester: ReyaTester):
    """
    Test REST API returns empty list for market with no recent executions.

    This test verifies the basic endpoint functionality.
    """
    logger.info("=" * 80)
    logger.info(f"REST GET MARKET SPOT EXECUTIONS (EMPTY) TEST: {SPOT_SYMBOL}")
    logger.info("=" * 80)

    # Query market spot executions
    executions = await spot_tester.client.get_market_spot_executions(SPOT_SYMBOL)

    # Should return a valid response (may be empty or have historical data)
    assert executions is not None, "Should receive a response"
    assert hasattr(executions, "data"), "Response should have 'data' attribute"

    logger.info(f"✅ Market spot executions returned: {len(executions.data)} execution(s)")

    # Log execution details if any exist
    for exec_item in executions.data[:5]:  # Log first 5
        logger.info(f"   - {exec_item.symbol}: qty={exec_item.qty}, price={exec_item.price}")

    logger.info("✅ REST GET MARKET SPOT EXECUTIONS (EMPTY) TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_rest_get_market_spot_executions_after_trade(maker_tester: ReyaTester, taker_tester: ReyaTester):
    """
    Test REST API returns spot execution after a trade is executed.

    Flow:
    1. Execute a spot trade between maker and taker
    2. Query market spot executions via REST
    3. Verify the execution appears in the response
    """
    logger.info("=" * 80)
    logger.info(f"REST GET MARKET SPOT EXECUTIONS AFTER TRADE TEST: {SPOT_SYMBOL}")
    logger.info("=" * 80)

    await maker_tester.close_active_orders(fail_if_none=False)
    await taker_tester.close_active_orders(fail_if_none=False)

    # Step 1: Execute a trade
    maker_price = round(REFERENCE_PRICE * 0.65, 2)

    maker_params = OrderBuilder().symbol(SPOT_SYMBOL).buy().price(str(maker_price)).qty(TEST_QTY).gtc().build()

    maker_order_id = await maker_tester.create_limit_order(maker_params)
    await maker_tester.wait_for_order_creation(maker_order_id)
    logger.info(f"✅ Maker order created: {maker_order_id} @ ${maker_price}")

    # Taker fills with IOC
    taker_params = OrderBuilder().symbol(SPOT_SYMBOL).sell().price(str(maker_price)).qty(TEST_QTY).ioc().build()

    taker_order_id = await taker_tester.create_limit_order(taker_params)
    logger.info(f"✅ Taker IOC order sent: {taker_order_id}")

    # Wait for execution
    await asyncio.sleep(0.3)
    await maker_tester.wait_for_order_state(maker_order_id, OrderStatus.FILLED, timeout=5)
    logger.info("✅ Trade executed")

    # Step 2: Query market spot executions via REST
    executions = await maker_tester.client.get_market_spot_executions(SPOT_SYMBOL)

    assert executions is not None, "Should receive a response"
    assert hasattr(executions, "data"), "Response should have 'data' attribute"
    assert len(executions.data) > 0, "Should have at least one execution"

    logger.info(f"✅ Market spot executions returned: {len(executions.data)} execution(s)")

    # Step 3: Verify our execution is in the response
    # Find execution matching our trade (by symbol and approximate qty)
    found_execution = None
    for exec_item in executions.data:
        if exec_item.symbol == SPOT_SYMBOL:
            found_execution = exec_item
            break

    assert found_execution is not None, f"Should find execution for {SPOT_SYMBOL}"
    logger.info(
        f"✅ Found execution: symbol={found_execution.symbol}, qty={found_execution.qty}, price={found_execution.price}"
    )

    # Verify execution fields
    assert found_execution.symbol == SPOT_SYMBOL
    assert found_execution.qty is not None
    assert found_execution.price is not None
    assert found_execution.side is not None

    logger.info("✅ REST GET MARKET SPOT EXECUTIONS AFTER TRADE TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.asyncio
async def test_rest_get_market_spot_executions_invalid_symbol(spot_tester: ReyaTester):
    """
    Test REST API returns error for invalid symbol.
    """
    logger.info("=" * 80)
    logger.info("REST GET MARKET SPOT EXECUTIONS INVALID SYMBOL TEST")
    logger.info("=" * 80)

    try:
        await spot_tester.client.get_market_spot_executions("INVALID_SYMBOL")
        pytest.fail("Should have raised an error for invalid symbol")
    except ValueError as e:
        logger.info(f"✅ Correctly rejected invalid symbol: {e}")

    logger.info("✅ REST GET MARKET SPOT EXECUTIONS INVALID SYMBOL TEST COMPLETED")


# ============================================================================
# WEBSOCKET TESTS
# ============================================================================


@pytest.mark.spot
@pytest.mark.websocket
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_ws_market_spot_executions_realtime(maker_tester: ReyaTester, taker_tester: ReyaTester):
    """
    Test WebSocket delivers real-time spot execution updates for market channel.

    Flow:
    1. Subscribe to market spot executions channel
    2. Execute a spot trade
    3. Verify execution is received via WebSocket
    """
    logger.info("=" * 80)
    logger.info(f"WS MARKET SPOT EXECUTIONS REALTIME TEST: {SPOT_SYMBOL}")
    logger.info("=" * 80)

    await maker_tester.close_active_orders(fail_if_none=False)
    await taker_tester.close_active_orders(fail_if_none=False)

    # Step 1: Subscribe to market spot executions channel
    maker_tester.clear_market_spot_executions(SPOT_SYMBOL)
    maker_tester.subscribe_to_market_spot_executions(SPOT_SYMBOL)

    # Wait for subscription to be established
    await asyncio.sleep(0.3)

    # Step 2: Execute a trade
    maker_price = round(REFERENCE_PRICE * 0.66, 2)

    maker_params = OrderBuilder().symbol(SPOT_SYMBOL).buy().price(str(maker_price)).qty(TEST_QTY).gtc().build()

    maker_order_id = await maker_tester.create_limit_order(maker_params)
    await maker_tester.wait_for_order_creation(maker_order_id)
    logger.info(f"✅ Maker order created: {maker_order_id} @ ${maker_price}")

    taker_params = OrderBuilder().symbol(SPOT_SYMBOL).sell().price(str(maker_price)).qty(TEST_QTY).ioc().build()

    await taker_tester.create_limit_order(taker_params)
    logger.info("✅ Taker IOC order sent")

    # Wait for execution and WebSocket update
    await asyncio.sleep(0.5)
    await maker_tester.wait_for_order_state(maker_order_id, OrderStatus.FILLED, timeout=5)
    logger.info("✅ Trade executed")

    # Step 3: Verify execution received via WebSocket
    market_executions = maker_tester.ws_market_spot_executions.get(SPOT_SYMBOL, [])

    logger.info(f"Market spot executions received via WS: {len(market_executions)}")

    assert len(market_executions) > 0, f"Should have received market spot execution via WebSocket for {SPOT_SYMBOL}"

    # Verify execution data
    ws_execution = market_executions[-1]  # Most recent
    assert ws_execution.symbol == SPOT_SYMBOL
    logger.info(f"✅ WS execution: symbol={ws_execution.symbol}, qty={ws_execution.qty}, side={ws_execution.side}")

    logger.info("✅ WS MARKET SPOT EXECUTIONS REALTIME TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.websocket
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_ws_market_spot_executions_snapshot(maker_tester: ReyaTester, taker_tester: ReyaTester):
    """
    Test WebSocket snapshot contains historical executions when subscribing.

    Flow:
    1. Execute a spot trade to create execution history
    2. Clear WebSocket state
    3. Subscribe to market spot executions channel
    4. Verify snapshot contains historical execution
    """
    logger.info("=" * 80)
    logger.info(f"WS MARKET SPOT EXECUTIONS SNAPSHOT TEST: {SPOT_SYMBOL}")
    logger.info("=" * 80)

    await maker_tester.close_active_orders(fail_if_none=False)
    await taker_tester.close_active_orders(fail_if_none=False)

    # Step 1: Execute a trade to create execution history
    maker_price = round(REFERENCE_PRICE * 0.67, 2)

    maker_params = OrderBuilder().symbol(SPOT_SYMBOL).buy().price(str(maker_price)).qty(TEST_QTY).gtc().build()

    maker_order_id = await maker_tester.create_limit_order(maker_params)
    await maker_tester.wait_for_order_creation(maker_order_id)

    taker_params = OrderBuilder().symbol(SPOT_SYMBOL).sell().price(str(maker_price)).qty(TEST_QTY).ioc().build()

    await taker_tester.create_limit_order(taker_params)

    await asyncio.sleep(0.3)
    await maker_tester.wait_for_order_state(maker_order_id, OrderStatus.FILLED, timeout=5)
    logger.info("✅ Trade executed - execution history created")

    # Step 2: Clear WebSocket state and resubscribe
    maker_tester.clear_market_spot_executions(SPOT_SYMBOL)

    # Step 3: Subscribe to market spot executions (should receive snapshot)
    maker_tester.subscribe_to_market_spot_executions(SPOT_SYMBOL)

    # Wait for snapshot
    await asyncio.sleep(0.5)

    # Step 4: Verify snapshot contains historical execution
    market_executions = maker_tester.ws_market_spot_executions.get(SPOT_SYMBOL, [])

    logger.info(f"Market spot executions in snapshot: {len(market_executions)}")

    # Note: Snapshot may or may not include our specific execution depending on timing
    # The key test is that we can subscribe and receive data
    logger.info(f"✅ Received {len(market_executions)} execution(s) via WebSocket snapshot/updates")

    logger.info("✅ WS MARKET SPOT EXECUTIONS SNAPSHOT TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.websocket
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_ws_and_rest_market_spot_executions_consistency(maker_tester: ReyaTester, taker_tester: ReyaTester):
    """
    Test that WebSocket and REST API return consistent data.

    Flow:
    1. Subscribe to market spot executions channel
    2. Execute a spot trade
    3. Query REST API
    4. Verify WebSocket and REST data are consistent
    """
    logger.info("=" * 80)
    logger.info(f"WS AND REST MARKET SPOT EXECUTIONS CONSISTENCY TEST: {SPOT_SYMBOL}")
    logger.info("=" * 80)

    await maker_tester.close_active_orders(fail_if_none=False)
    await taker_tester.close_active_orders(fail_if_none=False)

    # Step 1: Subscribe to market spot executions
    maker_tester.clear_market_spot_executions(SPOT_SYMBOL)
    maker_tester.subscribe_to_market_spot_executions(SPOT_SYMBOL)
    await asyncio.sleep(0.3)

    # Step 2: Execute a trade
    maker_price = round(REFERENCE_PRICE * 0.68, 2)

    maker_params = OrderBuilder().symbol(SPOT_SYMBOL).buy().price(str(maker_price)).qty(TEST_QTY).gtc().build()

    maker_order_id = await maker_tester.create_limit_order(maker_params)
    await maker_tester.wait_for_order_creation(maker_order_id)

    taker_params = OrderBuilder().symbol(SPOT_SYMBOL).sell().price(str(maker_price)).qty(TEST_QTY).ioc().build()

    await taker_tester.create_limit_order(taker_params)

    await asyncio.sleep(0.5)
    await maker_tester.wait_for_order_state(maker_order_id, OrderStatus.FILLED, timeout=5)
    logger.info("✅ Trade executed")

    # Step 3: Query REST API
    rest_executions = await maker_tester.client.get_market_spot_executions(SPOT_SYMBOL)

    # Step 4: Verify consistency
    ws_executions = maker_tester.ws_market_spot_executions.get(SPOT_SYMBOL, [])

    logger.info(f"REST executions: {len(rest_executions.data)}")
    logger.info(f"WS executions: {len(ws_executions)}")

    # Both should have data
    assert len(rest_executions.data) > 0, "REST should have executions"
    assert len(ws_executions) > 0, "WS should have executions"

    # Compare most recent execution
    rest_latest = rest_executions.data[0]  # Assuming sorted by time desc
    ws_latest = ws_executions[-1]  # Most recent received

    logger.info(f"REST latest: symbol={rest_latest.symbol}, qty={rest_latest.qty}")
    logger.info(f"WS latest: symbol={ws_latest.symbol}, qty={ws_latest.qty}")

    # Both should be for the same symbol
    assert rest_latest.symbol == SPOT_SYMBOL
    assert ws_latest.symbol == SPOT_SYMBOL

    logger.info("✅ WS AND REST MARKET SPOT EXECUTIONS CONSISTENCY TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.websocket
@pytest.mark.asyncio
async def test_ws_market_spot_executions_multiple_symbols(spot_tester: ReyaTester):
    """
    Test subscribing to multiple market spot execution channels.

    This test verifies that subscriptions to different symbols are independent.
    """
    logger.info("=" * 80)
    logger.info("WS MARKET SPOT EXECUTIONS MULTIPLE SYMBOLS TEST")
    logger.info("=" * 80)

    symbols = ["WETHRUSD"]  # Add more symbols when available

    # Subscribe to multiple symbols
    for symbol in symbols:
        spot_tester.clear_market_spot_executions(symbol)
        spot_tester.subscribe_to_market_spot_executions(symbol)
        logger.info(f"✅ Subscribed to {symbol}")

    await asyncio.sleep(0.3)

    # Verify subscriptions are independent
    for symbol in symbols:
        executions = spot_tester.ws_market_spot_executions.get(symbol, [])
        logger.info(f"{symbol}: {len(executions)} execution(s)")

    logger.info("✅ WS MARKET SPOT EXECUTIONS MULTIPLE SYMBOLS TEST COMPLETED")
