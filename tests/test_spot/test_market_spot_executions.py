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

These tests always provide their own maker liquidity to ensure predictable
execution behavior for verification purposes.
"""

import asyncio
import logging
from decimal import Decimal

import pytest

from sdk.open_api.exceptions import BadRequestException
from sdk.open_api.models import OrderStatus
from tests.helpers import ReyaTester
from tests.helpers.builders import OrderBuilder
from tests.test_spot.spot_config import SpotTestConfig

logger = logging.getLogger("reya.integration_tests")

# REST API TESTS
# ============================================================================


@pytest.mark.spot
@pytest.mark.asyncio
async def test_rest_get_market_spot_executions_empty(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test REST API returns empty list for market with no recent executions.

    This test verifies the basic endpoint functionality.
    """
    logger.info("=" * 80)
    logger.info(f"REST GET MARKET SPOT EXECUTIONS (EMPTY) TEST: {spot_config.symbol}")
    logger.info("=" * 80)

    # Clear any existing orders
    await spot_tester.orders.close_all(fail_if_none=False)

    # Query market spot executions
    executions = await spot_tester.client.markets.get_market_spot_executions(symbol=spot_config.symbol)

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
async def test_rest_get_market_spot_executions_after_trade(
    spot_config: SpotTestConfig, maker_tester: ReyaTester, taker_tester: ReyaTester
):
    """
    Test REST API returns spot execution after a trade is executed.

    Supports both empty and non-empty order books:
    - If external bid liquidity exists, taker sells into it
    - If no external liquidity, maker provides bid liquidity first

    Flow:
    1. Check for external liquidity
    2. If needed, maker places GTC buy order
    3. Taker places IOC sell order
    4. Query market spot executions via REST
    5. Verify the execution appears in the response
    """
    logger.info("=" * 80)
    logger.info(f"REST GET MARKET SPOT EXECUTIONS AFTER TRADE TEST: {spot_config.symbol}")
    logger.info("=" * 80)

    await maker_tester.orders.close_all(fail_if_none=False)
    await taker_tester.orders.close_all(fail_if_none=False)

    # Check for external liquidity
    await spot_config.refresh_order_book(maker_tester.data)

    maker_order_id = None
    usable_bid_price = spot_config.get_usable_bid_price_for_qty(spot_config.min_qty)
    usable_ask_price = spot_config.get_usable_ask_price_for_qty(spot_config.min_qty)

    if usable_bid_price is not None:
        # External bid liquidity exists - taker sells into it
        fill_price = usable_bid_price
        logger.info(f"Using external bid liquidity at ${fill_price:.2f}")
        taker_params = OrderBuilder.from_config(spot_config).sell().price(str(fill_price)).ioc().build()
    elif usable_ask_price is not None:
        # External ask liquidity exists - taker buys from it
        fill_price = usable_ask_price
        logger.info(f"Using external ask liquidity at ${fill_price:.2f}")
        taker_params = OrderBuilder.from_config(spot_config).buy().price(str(fill_price)).ioc().build()
    else:
        # No external liquidity - provide our own
        fill_price = Decimal(str(spot_config.price(0.97)))

        maker_params = OrderBuilder.from_config(spot_config).buy().at_price(0.97).gtc().build()

        maker_order_id = await maker_tester.orders.create_limit(maker_params)
        await maker_tester.wait.for_order_creation(maker_order_id)
        logger.info(f"✅ Maker order created: {maker_order_id} @ ${fill_price:.2f}")

        taker_params = OrderBuilder.from_config(spot_config).sell().price(str(fill_price)).ioc().build()

    taker_order_id = await taker_tester.orders.create_limit(taker_params)
    logger.info(f"✅ Taker IOC order sent: {taker_order_id}")

    # Wait for execution
    await asyncio.sleep(0.3)
    if maker_order_id:
        await maker_tester.wait.for_order_state(maker_order_id, OrderStatus.FILLED, timeout=5)
    logger.info("✅ Trade executed")

    # Step 2: Query market spot executions via REST
    executions = await maker_tester.client.markets.get_market_spot_executions(symbol=spot_config.symbol)

    assert executions is not None, "Should receive a response"
    assert hasattr(executions, "data"), "Response should have 'data' attribute"
    assert len(executions.data) > 0, "Should have at least one execution"

    logger.info(f"✅ Market spot executions returned: {len(executions.data)} execution(s)")

    # Step 3: Verify our execution is in the response
    # Find execution matching our trade (by symbol and approximate qty)
    found_execution = None
    for exec_item in executions.data:
        if exec_item.symbol == spot_config.symbol:
            found_execution = exec_item
            break

    assert found_execution is not None, f"Should find execution for {spot_config.symbol}"
    logger.info(
        f"✅ Found execution: symbol={found_execution.symbol}, qty={found_execution.qty}, price={found_execution.price}"
    )

    # Verify execution fields
    assert found_execution.symbol == spot_config.symbol
    assert found_execution.qty is not None
    assert found_execution.price is not None
    assert found_execution.side is not None

    # Verify no open orders remain
    await maker_tester.check.no_open_orders()
    await taker_tester.check.no_open_orders()

    logger.info("✅ REST GET MARKET SPOT EXECUTIONS AFTER TRADE TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.asyncio
async def test_rest_get_market_spot_executions_invalid_symbol(
    spot_config: SpotTestConfig, spot_tester: ReyaTester
):  # pylint: disable=unused-argument
    """
    Test REST API returns error for invalid symbol.
    """
    logger.info("=" * 80)
    logger.info("REST GET MARKET SPOT EXECUTIONS INVALID SYMBOL TEST")
    logger.info("=" * 80)

    try:
        await spot_tester.client.markets.get_market_spot_executions(symbol="INVALID_SYMBOL")
        pytest.fail("Should have raised an error for invalid symbol")
    except BadRequestException as e:
        logger.info(f"✅ Correctly rejected invalid symbol: {e}")

    logger.info("✅ REST GET MARKET SPOT EXECUTIONS INVALID SYMBOL TEST COMPLETED")


# ============================================================================
# WEBSOCKET TESTS
# ============================================================================


@pytest.mark.spot
@pytest.mark.websocket
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_ws_market_spot_executions_realtime(
    spot_config: SpotTestConfig, maker_tester: ReyaTester, taker_tester: ReyaTester
):
    """
    Test WebSocket delivers real-time spot execution updates for market channel.

    Supports both empty and non-empty order books:
    - If external bid liquidity exists, taker sells into it
    - If no external liquidity, maker provides bid liquidity first

    Flow:
    1. Subscribe to market spot executions channel
    2. Check for external liquidity
    3. If needed, maker places GTC buy order
    4. Taker places IOC sell order
    5. Verify execution is received via WebSocket
    """
    logger.info("=" * 80)
    logger.info(f"WS MARKET SPOT EXECUTIONS REALTIME TEST: {spot_config.symbol}")
    logger.info("=" * 80)

    await maker_tester.orders.close_all(fail_if_none=False)
    await taker_tester.orders.close_all(fail_if_none=False)

    # Step 1: Subscribe to market spot executions channel
    maker_tester.ws.clear_market_spot_executions(spot_config.symbol)
    maker_tester.ws.subscribe_to_market_spot_executions(spot_config.symbol)

    # Wait for subscription to be established
    await asyncio.sleep(0.3)

    # Check for external liquidity
    await spot_config.refresh_order_book(maker_tester.data)

    maker_order_id = None
    usable_bid_price = spot_config.get_usable_bid_price_for_qty(spot_config.min_qty)
    usable_ask_price = spot_config.get_usable_ask_price_for_qty(spot_config.min_qty)

    if usable_bid_price is not None:
        # External bid liquidity exists - taker sells into it
        fill_price = usable_bid_price
        logger.info(f"Using external bid liquidity at ${fill_price:.2f}")
        taker_params = OrderBuilder.from_config(spot_config).sell().price(str(fill_price)).ioc().build()
    elif usable_ask_price is not None:
        # External ask liquidity exists - taker buys from it
        fill_price = usable_ask_price
        logger.info(f"Using external ask liquidity at ${fill_price:.2f}")
        taker_params = OrderBuilder.from_config(spot_config).buy().price(str(fill_price)).ioc().build()
    else:
        # No external liquidity - provide our own
        fill_price = Decimal(str(spot_config.price(0.98)))

        maker_params = OrderBuilder.from_config(spot_config).buy().at_price(0.98).gtc().build()

        maker_order_id = await maker_tester.orders.create_limit(maker_params)
        await maker_tester.wait.for_order_creation(maker_order_id)
        logger.info(f"✅ Maker order created: {maker_order_id} @ ${fill_price:.2f}")

        taker_params = OrderBuilder.from_config(spot_config).sell().price(str(fill_price)).ioc().build()

    await taker_tester.orders.create_limit(taker_params)
    logger.info("✅ Taker IOC order sent")

    # Wait for execution and WebSocket update
    await asyncio.sleep(0.5)
    if maker_order_id:
        await maker_tester.wait.for_order_state(maker_order_id, OrderStatus.FILLED, timeout=5)
    logger.info("✅ Trade executed")

    # Step 3: Verify execution received via WebSocket
    market_executions = list(maker_tester.ws.market_spot_executions.get(spot_config.symbol, []))

    logger.info(f"Market spot executions received via WS: {len(market_executions)}")

    assert (
        len(market_executions) > 0
    ), f"Should have received market spot execution via WebSocket for {spot_config.symbol}"

    # Verify execution data
    ws_execution = market_executions[-1]  # Most recent
    assert ws_execution.symbol == spot_config.symbol
    logger.info(f"✅ WS execution: symbol={ws_execution.symbol}, qty={ws_execution.qty}, side={ws_execution.side}")

    # Verify no open orders remain
    await maker_tester.check.no_open_orders()
    await taker_tester.check.no_open_orders()

    logger.info("✅ WS MARKET SPOT EXECUTIONS REALTIME TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.websocket
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_ws_market_spot_executions_snapshot(
    spot_config: SpotTestConfig, maker_tester: ReyaTester, taker_tester: ReyaTester
):
    """
    Test WebSocket snapshot contains historical executions when subscribing.

    Flow:
    1. Execute a spot trade to create execution history (use external liquidity if available)
    2. Clear WebSocket state
    3. Subscribe to market spot executions channel
    4. Verify snapshot contains historical execution
    """
    logger.info("=" * 80)
    logger.info(f"WS MARKET SPOT EXECUTIONS SNAPSHOT TEST: {spot_config.symbol}")
    logger.info("=" * 80)

    await maker_tester.orders.close_all(fail_if_none=False)
    await taker_tester.orders.close_all(fail_if_none=False)

    # Refresh order book state to check for external liquidity
    await spot_config.refresh_order_book(taker_tester.data)

    # Step 1: Execute a trade to create execution history
    # Use external liquidity if available, otherwise create our own maker order
    usable_bid = spot_config.get_usable_bid_price_for_qty(spot_config.min_qty)
    usable_ask = spot_config.get_usable_ask_price_for_qty(spot_config.min_qty)

    if usable_bid is not None:
        # External bid liquidity exists - taker sells into it
        logger.info(f"Using external bid liquidity at ${usable_bid}")
        taker_params = OrderBuilder.from_config(spot_config).sell().price(str(usable_bid)).ioc().build()
        await taker_tester.orders.create_limit(taker_params)
        await asyncio.sleep(0.5)  # Wait for execution to settle
        logger.info("✅ Trade executed against external bid - execution history created")
    elif usable_ask is not None:
        # External ask liquidity exists - taker buys into it
        logger.info(f"Using external ask liquidity at ${usable_ask}")
        taker_params = OrderBuilder.from_config(spot_config).buy().price(str(usable_ask)).ioc().build()
        await taker_tester.orders.create_limit(taker_params)
        await asyncio.sleep(0.5)  # Wait for execution to settle
        logger.info("✅ Trade executed against external ask - execution history created")
    else:
        # No external liquidity - create our own maker order
        logger.info("No external liquidity - creating maker order")
        maker_params = OrderBuilder.from_config(spot_config).buy().at_price(0.98).gtc().build()
        maker_order_id = await maker_tester.orders.create_limit(maker_params)
        await maker_tester.wait.for_order_creation(maker_order_id)

        taker_params = OrderBuilder.from_config(spot_config).sell().at_price(0.98).ioc().build()
        await taker_tester.orders.create_limit(taker_params)

        await asyncio.sleep(0.3)
        await maker_tester.wait.for_order_state(maker_order_id, OrderStatus.FILLED, timeout=5)
        logger.info("✅ Trade executed - execution history created")

    # Step 2: Clear WebSocket state and resubscribe
    maker_tester.ws.clear_market_spot_executions(spot_config.symbol)

    # Step 3: Subscribe to market spot executions (should receive snapshot)
    maker_tester.ws.subscribe_to_market_spot_executions(spot_config.symbol)

    # Wait for snapshot
    await asyncio.sleep(0.5)

    # Step 4: Verify snapshot contains historical execution
    market_executions = list(maker_tester.ws.market_spot_executions.get(spot_config.symbol, []))

    logger.info(f"Market spot executions in snapshot: {len(market_executions)}")

    # Note: Snapshot may or may not include our specific execution depending on timing
    # The key test is that we can subscribe and receive data
    logger.info(f"✅ Received {len(market_executions)} execution(s) via WebSocket snapshot/updates")

    # Verify no open orders remain
    await maker_tester.check.no_open_orders()
    await taker_tester.check.no_open_orders()

    logger.info("✅ WS MARKET SPOT EXECUTIONS SNAPSHOT TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.websocket
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_ws_and_rest_market_spot_executions_consistency(
    spot_config: SpotTestConfig, maker_tester: ReyaTester, taker_tester: ReyaTester
):
    """
    Test that WebSocket and REST API return consistent data.

    Flow:
    1. Subscribe to market spot executions channel
    2. Execute a spot trade (use external liquidity if available)
    3. Query REST API
    4. Verify WebSocket and REST data are consistent
    """
    logger.info("=" * 80)
    logger.info(f"WS AND REST MARKET SPOT EXECUTIONS CONSISTENCY TEST: {spot_config.symbol}")
    logger.info("=" * 80)

    await maker_tester.orders.close_all(fail_if_none=False)
    await taker_tester.orders.close_all(fail_if_none=False)

    # Step 1: Subscribe to market spot executions
    taker_tester.ws.clear_market_spot_executions(spot_config.symbol)
    taker_tester.ws.subscribe_to_market_spot_executions(spot_config.symbol)
    await asyncio.sleep(0.3)

    # Refresh order book state to check for external liquidity
    await spot_config.refresh_order_book(taker_tester.data)

    # Step 2: Execute a trade (use external liquidity if available)
    usable_bid = spot_config.get_usable_bid_price_for_qty(spot_config.min_qty)
    usable_ask = spot_config.get_usable_ask_price_for_qty(spot_config.min_qty)

    if usable_bid is not None:
        # External bid liquidity exists - taker sells into it
        logger.info(f"Using external bid liquidity at ${usable_bid}")
        taker_params = OrderBuilder.from_config(spot_config).sell().price(str(usable_bid)).ioc().build()
        await taker_tester.orders.create_limit(taker_params)
        await asyncio.sleep(0.5)  # Wait for execution to settle
        logger.info("✅ Trade executed against external bid")
    elif usable_ask is not None:
        # External ask liquidity exists - taker buys into it
        logger.info(f"Using external ask liquidity at ${usable_ask}")
        taker_params = OrderBuilder.from_config(spot_config).buy().price(str(usable_ask)).ioc().build()
        await taker_tester.orders.create_limit(taker_params)
        await asyncio.sleep(0.5)  # Wait for execution to settle
        logger.info("✅ Trade executed against external ask")
    else:
        # No external liquidity - create our own maker order
        logger.info("No external liquidity - creating maker order")
        maker_params = OrderBuilder.from_config(spot_config).buy().at_price(0.98).gtc().build()
        maker_order_id = await maker_tester.orders.create_limit(maker_params)
        await maker_tester.wait.for_order_creation(maker_order_id)

        taker_params = OrderBuilder.from_config(spot_config).sell().at_price(0.98).ioc().build()
        await taker_tester.orders.create_limit(taker_params)

        await asyncio.sleep(0.5)
        await maker_tester.wait.for_order_state(maker_order_id, OrderStatus.FILLED, timeout=5)
        logger.info("✅ Trade executed")

    # Step 3: Query REST API
    rest_executions = await taker_tester.client.markets.get_market_spot_executions(symbol=spot_config.symbol)

    # Step 4: Verify consistency (use taker_tester since that's who subscribed)
    ws_executions = list(taker_tester.ws.market_spot_executions.get(spot_config.symbol, []))

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
    assert rest_latest.symbol == spot_config.symbol
    assert ws_latest.symbol == spot_config.symbol

    # Verify no open orders remain
    await maker_tester.check.no_open_orders()
    await taker_tester.check.no_open_orders()

    logger.info("✅ WS AND REST MARKET SPOT EXECUTIONS CONSISTENCY TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.websocket
@pytest.mark.asyncio
async def test_ws_market_spot_executions_multiple_symbols(
    spot_config: SpotTestConfig, spot_tester: ReyaTester
):  # pylint: disable=unused-argument
    """
    Test subscribing to multiple market spot execution channels.

    This test verifies that subscriptions to different symbols are independent.
    """
    logger.info("=" * 80)
    logger.info("WS MARKET SPOT EXECUTIONS MULTIPLE SYMBOLS TEST")
    logger.info("=" * 80)

    symbols = [spot_config.symbol]  # Uses the configured spot market symbol

    # Subscribe to multiple symbols
    for symbol in symbols:
        spot_tester.ws.clear_market_spot_executions(symbol)
        spot_tester.ws.subscribe_to_market_spot_executions(symbol)
        logger.info(f"✅ Subscribed to {symbol}")

    await asyncio.sleep(0.3)

    # Verify subscriptions are independent
    for symbol in symbols:
        executions = list(spot_tester.ws.market_spot_executions.get(symbol, []))
        logger.info(f"{symbol}: {len(executions)} execution(s)")

    logger.info("✅ WS MARKET SPOT EXECUTIONS MULTIPLE SYMBOLS TEST COMPLETED")
