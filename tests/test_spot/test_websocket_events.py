"""
Spot WebSocket Event Verification Tests

Tests for verifying WebSocket events during spot trading:
- Order changes on create
- Order changes on fill
- Order changes on cancel
- Spot executions
- Balance updates

These tests verify both that events are received AND that the event
content matches expectations using centralized assertion helpers.
"""

import asyncio
import logging

import pytest

from sdk.open_api.models import OrderStatus
from tests.helpers import ReyaTester
from tests.helpers.builders.order_builder import OrderBuilder
from tests.helpers.reya_tester import limit_order_params_to_order
from tests.test_spot.spot_config import SpotTestConfig

logger = logging.getLogger("reya.integration_tests")


@pytest.mark.spot
@pytest.mark.websocket
@pytest.mark.asyncio
async def test_spot_ws_order_changes_on_create(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test WebSocket orderChanges event received on order creation.

    Flow:
    1. Clear order change tracking
    2. Place GTC order
    3. Verify orderChanges event received via WebSocket
    4. Verify event contains correct order data (symbol, side, qty)
    """
    logger.info("=" * 80)
    logger.info(f"SPOT WS ORDER CHANGES ON CREATE TEST: {spot_config.symbol}")
    logger.info("=" * 80)

    await spot_tester.orders.close_all(fail_if_none=False)

    # Clear WebSocket tracking
    spot_tester.ws.order_changes.clear()

    # Place GTC order
    order_price = spot_config.price(0.96)

    order_params = OrderBuilder.from_config(spot_config).buy().at_price(0.96).gtc().build()

    logger.info(f"Placing GTC buy: {spot_config.min_qty} @ ${order_price:.2f}")
    order_id = await spot_tester.orders.create_limit(order_params)
    await spot_tester.wait.for_order_creation(order_id)
    logger.info(f"✅ Order created: {order_id}")

    # Verify order change event using ReyaTester method (wait.for_order_creation already waits for WS)
    spot_tester.check.ws_order_change_received(
        order_id=order_id,
        expected_symbol=spot_config.symbol,
        expected_side="B",
        expected_qty=spot_config.min_qty,
    )

    # Cleanup
    await spot_tester.client.cancel_order(
        order_id=order_id, symbol=spot_config.symbol, account_id=spot_tester.account_id
    )
    await asyncio.sleep(0.05)
    await spot_tester.check.no_open_orders()

    logger.info("✅ SPOT WS ORDER CHANGES ON CREATE TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.websocket
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_spot_ws_order_changes_on_fill(
    spot_config: SpotTestConfig, maker_tester: ReyaTester, taker_tester: ReyaTester
):
    """
    Test WebSocket orderChanges event received on order fill.

    This test requires a controlled environment to verify order fill events.
    When external liquidity exists, we skip to avoid unpredictable matching.

    Flow:
    1. Check for external liquidity - skip if present
    2. Maker places GTC order
    3. Taker fills the order
    4. Verify orderChanges event shows FILLED status
    """
    logger.info("=" * 80)
    logger.info(f"SPOT WS ORDER CHANGES ON FILL TEST: {spot_config.symbol}")
    logger.info("=" * 80)

    await maker_tester.orders.close_all(fail_if_none=False)
    await taker_tester.orders.close_all(fail_if_none=False)

    # Check current order book state
    await spot_config.refresh_order_book(maker_tester.data)

    # Skip if external liquidity exists - this test needs controlled environment
    if spot_config.has_any_external_liquidity:
        pytest.skip(
            "Skipping WS order fill test: external liquidity exists. "
            "This test requires a controlled environment to verify order fill events."
        )

    # Clear WebSocket tracking
    maker_tester.ws.order_changes.clear()

    # Maker places GTC buy order
    maker_price = spot_config.price(0.97)

    maker_params = OrderBuilder.from_config(spot_config).buy().at_price(0.97).gtc().build()

    logger.info(f"Maker placing GTC buy: {spot_config.min_qty} @ ${maker_price:.2f}")
    maker_order_id = await maker_tester.orders.create_limit(maker_params)
    await maker_tester.wait.for_order_creation(maker_order_id)
    logger.info(f"✅ Maker order created: {maker_order_id}")

    # Taker fills the order
    taker_params = OrderBuilder.from_config(spot_config).sell().at_price(0.97).ioc().build()

    logger.info("Taker placing IOC sell to fill maker order...")
    await taker_tester.orders.create_limit(taker_params)

    # Wait for fill
    await asyncio.sleep(0.05)
    await maker_tester.wait.for_order_state(maker_order_id, OrderStatus.FILLED, timeout=5)
    logger.info("✅ Maker order filled")

    # Verify order change event using ReyaTester method
    maker_tester.check.ws_order_change_received(
        order_id=maker_order_id,
        expected_symbol=spot_config.symbol,
        expected_status=OrderStatus.FILLED,
    )

    # Verify no open orders
    await maker_tester.check.no_open_orders()
    await taker_tester.check.no_open_orders()

    logger.info("✅ SPOT WS ORDER CHANGES ON FILL TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.websocket
@pytest.mark.asyncio
async def test_spot_ws_order_changes_on_cancel(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test WebSocket orderChanges event received on order cancel.

    Flow:
    1. Place GTC order
    2. Cancel the order
    3. Verify orderChanges event shows CANCELLED status
    """
    logger.info("=" * 80)
    logger.info(f"SPOT WS ORDER CHANGES ON CANCEL TEST: {spot_config.symbol}")
    logger.info("=" * 80)

    await spot_tester.orders.close_all(fail_if_none=False)

    # Place GTC order
    order_price = spot_config.price(0.96)

    order_params = OrderBuilder.from_config(spot_config).buy().at_price(0.96).gtc().build()

    logger.info(f"Placing GTC buy: {spot_config.min_qty} @ ${order_price:.2f}")
    order_id = await spot_tester.orders.create_limit(order_params)
    await spot_tester.wait.for_order_creation(order_id)
    logger.info(f"✅ Order created: {order_id}")

    # Clear WebSocket tracking before cancel to capture the cancel event
    spot_tester.ws.order_changes.clear()

    # Cancel the order
    logger.info("Cancelling order...")
    await spot_tester.client.cancel_order(
        order_id=order_id, symbol=spot_config.symbol, account_id=spot_tester.account_id
    )

    # Wait for cancellation
    await spot_tester.wait.for_order_state(order_id, OrderStatus.CANCELLED, timeout=5)
    logger.info("✅ Order cancelled")

    # Verify order change event using ReyaTester method
    spot_tester.check.ws_order_change_received(
        order_id=order_id,
        expected_symbol=spot_config.symbol,
        expected_status=OrderStatus.CANCELLED,
    )

    await spot_tester.check.no_open_orders()

    logger.info("✅ SPOT WS ORDER CHANGES ON CANCEL TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.websocket
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_spot_ws_spot_executions(spot_config: SpotTestConfig, maker_tester: ReyaTester, taker_tester: ReyaTester):
    """
    Test WebSocket spotExecutions event received on trade.

    Supports both empty and non-empty order books:
    - If external bid liquidity exists, taker sells into it
    - If no external liquidity, maker provides bid liquidity first

    Flow:
    1. Check for external liquidity
    2. If needed, maker places GTC order
    3. Taker fills with IOC order
    4. Verify spotExecutions event received with correct details
    """
    logger.info("=" * 80)
    logger.info(f"SPOT WS SPOT EXECUTIONS TEST: {spot_config.symbol}")
    logger.info("=" * 80)

    await maker_tester.orders.close_all(fail_if_none=False)
    await taker_tester.orders.close_all(fail_if_none=False)

    # Clear WebSocket tracking
    taker_tester.ws.last_spot_execution = None

    # Check for external liquidity
    await spot_config.refresh_order_book(maker_tester.data)

    maker_order_id = None
    usable_bid_price = spot_config.get_usable_bid_price_for_qty(spot_config.min_qty)

    if usable_bid_price is not None:
        # External bid liquidity exists - use it
        fill_price = float(usable_bid_price)
        logger.info(f"Using external bid liquidity at ${fill_price:.2f}")
    else:
        # No external liquidity - provide our own
        fill_price = spot_config.price(0.97)

        maker_params = OrderBuilder.from_config(spot_config).buy().at_price(0.97).gtc().build()

        logger.info(f"Maker placing GTC buy: {spot_config.min_qty} @ ${fill_price:.2f}")
        maker_order_id = await maker_tester.orders.create_limit(maker_params)
        await maker_tester.wait.for_order_creation(maker_order_id)
        logger.info(f"✅ Maker order created: {maker_order_id}")

    # Taker fills with IOC sell
    taker_params = OrderBuilder.from_config(spot_config).sell().price(str(fill_price)).ioc().build()

    logger.info(f"Taker placing IOC sell: {spot_config.min_qty} @ ${fill_price:.2f}")
    taker_order_id = await taker_tester.orders.create_limit(taker_params)

    # Wait for spot execution event via WebSocket (strict matching on order_id and all fields)
    expected_order = limit_order_params_to_order(taker_params, taker_tester.account_id)
    execution = await taker_tester.wait.for_spot_execution(taker_order_id, expected_order, timeout=5)

    # Verify spot execution details
    assert execution is not None, "No spot execution event received via WebSocket"
    assert execution.symbol == spot_config.symbol
    assert execution.side.value == "A"  # Taker was selling
    assert execution.qty == spot_config.min_qty
    # Price may differ slightly due to order book changes, just verify it's within circuit breaker range
    exec_price = float(execution.price)
    assert spot_config.circuit_breaker_floor <= exec_price <= spot_config.circuit_breaker_ceiling, (
        f"Fill price ${exec_price} should be within circuit breaker range"
    )
    logger.info(f"✅ Spot execution received: {execution.order_id}")

    # Verify no open orders
    await maker_tester.check.no_open_orders()
    await taker_tester.check.no_open_orders()

    logger.info("✅ SPOT WS SPOT EXECUTIONS TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.websocket
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_spot_ws_balance_updates(spot_config: SpotTestConfig, maker_tester: ReyaTester, taker_tester: ReyaTester):
    """
    Test WebSocket accountBalances event received on trade.

    This test requires a controlled environment to verify balance updates
    between our maker and taker accounts. When external liquidity exists,
    we skip to avoid unpredictable balance changes.

    Flow:
    1. Check for external liquidity - skip if present
    2. Record initial balance update count
    3. Execute a trade between maker and taker
    4. Verify balance update events received via WebSocket
    """
    logger.info("=" * 80)
    logger.info(f"SPOT WS BALANCE UPDATES TEST: {spot_config.symbol}")
    logger.info("=" * 80)

    await maker_tester.orders.close_all(fail_if_none=False)
    await taker_tester.orders.close_all(fail_if_none=False)

    # Check current order book state
    await spot_config.refresh_order_book(maker_tester.data)

    # Skip if external liquidity exists - this test needs controlled environment for balance verification
    if spot_config.has_any_external_liquidity:
        pytest.skip(
            "Skipping WS balance updates test: external liquidity exists. "
            "This test requires a controlled environment to verify balance changes."
        )

    # Record initial balance update counts using ReyaTester method
    maker_initial_count = maker_tester.ws.get_balance_update_count()
    taker_initial_count = taker_tester.ws.get_balance_update_count()
    logger.info(f"Initial balance update counts - Maker: {maker_initial_count}, Taker: {taker_initial_count}")

    # Maker places GTC buy order (buying ETH with RUSD)
    maker_price = spot_config.price(0.97)

    maker_params = OrderBuilder.from_config(spot_config).buy().at_price(0.97).gtc().build()

    logger.info(f"Maker placing GTC buy: {spot_config.min_qty} @ ${maker_price:.2f}")
    maker_order_id = await maker_tester.orders.create_limit(maker_params)
    await maker_tester.wait.for_order_creation(maker_order_id)
    logger.info(f"✅ Maker order created: {maker_order_id}")

    # Taker fills with IOC sell (selling ETH for RUSD)
    taker_params = OrderBuilder.from_config(spot_config).sell().at_price(0.97).ioc().build()

    logger.info(f"Taker placing IOC sell: {spot_config.min_qty} @ ${maker_price:.2f}")
    await taker_tester.orders.create_limit(taker_params)

    # Wait for balance updates via WebSocket (with proper timeout)
    await maker_tester.wait.for_balance_updates(maker_initial_count, min_updates=1, timeout=5.0)
    await taker_tester.wait.for_balance_updates(taker_initial_count, min_updates=1, timeout=5.0)

    # Verify balance updates using ReyaTester methods
    maker_tester.check.ws_balance_updates_received(
        initial_update_count=maker_initial_count,
        min_updates=1,
        expected_assets=["ETH"],  # Maker bought ETH
    )

    taker_tester.check.ws_balance_updates_received(
        initial_update_count=taker_initial_count,
        min_updates=1,
        expected_assets=["RUSD"],  # Taker received RUSD
    )

    # Verify no open orders
    await maker_tester.check.no_open_orders()
    await taker_tester.check.no_open_orders()

    logger.info("✅ SPOT WS BALANCE UPDATES TEST COMPLETED")
