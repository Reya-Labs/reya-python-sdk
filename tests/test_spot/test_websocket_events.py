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

    await spot_tester.close_active_orders(fail_if_none=False)

    # Clear WebSocket tracking
    spot_tester.ws_order_changes.clear()

    # Place GTC order
    order_price = spot_config.price(0.96)

    order_params = OrderBuilder.from_config(spot_config).buy().at_price(0.96).gtc().build()

    logger.info(f"Placing GTC buy: {spot_config.min_qty} @ ${order_price:.2f}")
    order_id = await spot_tester.create_limit_order(order_params)
    await spot_tester.wait_for_order_creation(order_id)
    logger.info(f"✅ Order created: {order_id}")

    # Verify order change event using ReyaTester method (wait_for_order_creation already waits for WS)
    spot_tester.check_ws_order_change_received(
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
    await spot_tester.check_no_open_orders()

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

    Flow:
    1. Maker places GTC order
    2. Taker fills the order
    3. Verify orderChanges event shows FILLED status
    """
    logger.info("=" * 80)
    logger.info(f"SPOT WS ORDER CHANGES ON FILL TEST: {spot_config.symbol}")
    logger.info("=" * 80)

    await maker_tester.close_active_orders(fail_if_none=False)
    await taker_tester.close_active_orders(fail_if_none=False)

    # Clear WebSocket tracking
    maker_tester.ws_order_changes.clear()

    # Maker places GTC buy order
    maker_price = spot_config.price(0.97)

    maker_params = OrderBuilder.from_config(spot_config).buy().at_price(0.97).gtc().build()

    logger.info(f"Maker placing GTC buy: {spot_config.min_qty} @ ${maker_price:.2f}")
    maker_order_id = await maker_tester.create_limit_order(maker_params)
    await maker_tester.wait_for_order_creation(maker_order_id)
    logger.info(f"✅ Maker order created: {maker_order_id}")

    # Taker fills the order
    _ = maker_price  # taker_price - calculated for reference

    taker_params = OrderBuilder.from_config(spot_config).sell().at_price(0.97).ioc().build()

    logger.info("Taker placing IOC sell to fill maker order...")
    await taker_tester.create_limit_order(taker_params)

    # Wait for fill
    await asyncio.sleep(0.05)
    await maker_tester.wait_for_order_state(maker_order_id, OrderStatus.FILLED, timeout=5)
    logger.info("✅ Maker order filled")

    # Verify order change event using ReyaTester method
    maker_tester.check_ws_order_change_received(
        order_id=maker_order_id,
        expected_symbol=spot_config.symbol,
        expected_status=OrderStatus.FILLED,
    )

    # Verify no open orders
    await maker_tester.check_no_open_orders()
    await taker_tester.check_no_open_orders()

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

    await spot_tester.close_active_orders(fail_if_none=False)

    # Place GTC order
    order_price = spot_config.price(0.96)

    order_params = OrderBuilder.from_config(spot_config).buy().at_price(0.96).gtc().build()

    logger.info(f"Placing GTC buy: {spot_config.min_qty} @ ${order_price:.2f}")
    order_id = await spot_tester.create_limit_order(order_params)
    await spot_tester.wait_for_order_creation(order_id)
    logger.info(f"✅ Order created: {order_id}")

    # Clear WebSocket tracking before cancel to capture the cancel event
    spot_tester.ws_order_changes.clear()

    # Cancel the order
    logger.info("Cancelling order...")
    await spot_tester.client.cancel_order(
        order_id=order_id, symbol=spot_config.symbol, account_id=spot_tester.account_id
    )

    # Wait for cancellation
    await spot_tester.wait_for_order_state(order_id, OrderStatus.CANCELLED, timeout=5)
    logger.info("✅ Order cancelled")

    # Verify order change event using ReyaTester method
    spot_tester.check_ws_order_change_received(
        order_id=order_id,
        expected_symbol=spot_config.symbol,
        expected_status=OrderStatus.CANCELLED,
    )

    await spot_tester.check_no_open_orders()

    logger.info("✅ SPOT WS ORDER CHANGES ON CANCEL TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.websocket
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_spot_ws_spot_executions(spot_config: SpotTestConfig, maker_tester: ReyaTester, taker_tester: ReyaTester):
    """
    Test WebSocket spotExecutions event received on trade.

    Flow:
    1. Maker places GTC order
    2. Taker fills with IOC order
    3. Verify spotExecutions event received with correct details
    """
    logger.info("=" * 80)
    logger.info(f"SPOT WS SPOT EXECUTIONS TEST: {spot_config.symbol}")
    logger.info("=" * 80)

    await maker_tester.close_active_orders(fail_if_none=False)
    await taker_tester.close_active_orders(fail_if_none=False)

    # Clear WebSocket tracking
    taker_tester.ws_last_spot_execution = None

    # Maker places GTC buy order
    maker_price = spot_config.price(0.97)

    maker_params = OrderBuilder.from_config(spot_config).buy().at_price(0.97).gtc().build()

    logger.info(f"Maker placing GTC buy: {spot_config.min_qty} @ ${maker_price:.2f}")
    maker_order_id = await maker_tester.create_limit_order(maker_params)
    await maker_tester.wait_for_order_creation(maker_order_id)
    logger.info(f"✅ Maker order created: {maker_order_id}")

    # Taker fills with IOC sell
    taker_price = maker_price

    taker_params = OrderBuilder.from_config(spot_config).sell().at_price(0.97).ioc().build()

    logger.info(f"Taker placing IOC sell: {spot_config.min_qty} @ ${taker_price:.2f}")
    taker_order_id = await taker_tester.create_limit_order(taker_params)

    # Wait for spot execution event via WebSocket (strict matching on order_id and all fields)
    expected_order = limit_order_params_to_order(taker_params, taker_tester.account_id)
    execution = await taker_tester.wait_for_spot_execution(taker_order_id, expected_order, timeout=5)

    # Verify spot execution details
    assert execution is not None, "No spot execution event received via WebSocket"
    assert execution.symbol == spot_config.symbol
    assert execution.side.value == "A"  # Taker was selling
    assert execution.qty == spot_config.min_qty
    assert float(execution.price) == maker_price  # Compare as floats to handle "325" vs "325.0"
    logger.info(f"✅ Spot execution received: {execution.order_id}")

    # Verify no open orders
    await maker_tester.check_no_open_orders()
    await taker_tester.check_no_open_orders()

    logger.info("✅ SPOT WS SPOT EXECUTIONS TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.websocket
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_spot_ws_balance_updates(spot_config: SpotTestConfig, maker_tester: ReyaTester, taker_tester: ReyaTester):
    """
    Test WebSocket accountBalances event received on trade.

    Flow:
    1. Record initial balance update count
    2. Execute a trade between maker and taker
    3. Verify balance update events received via WebSocket
    """
    logger.info("=" * 80)
    logger.info(f"SPOT WS BALANCE UPDATES TEST: {spot_config.symbol}")
    logger.info("=" * 80)

    await maker_tester.close_active_orders(fail_if_none=False)
    await taker_tester.close_active_orders(fail_if_none=False)

    # Record initial balance update counts using ReyaTester method
    maker_initial_count = maker_tester.get_balance_update_count()
    taker_initial_count = taker_tester.get_balance_update_count()
    logger.info(f"Initial balance update counts - Maker: {maker_initial_count}, Taker: {taker_initial_count}")

    # Maker places GTC buy order (buying ETH with RUSD)
    maker_price = spot_config.price(0.97)

    maker_params = OrderBuilder.from_config(spot_config).buy().at_price(0.97).gtc().build()

    logger.info(f"Maker placing GTC buy: {spot_config.min_qty} @ ${maker_price:.2f}")
    maker_order_id = await maker_tester.create_limit_order(maker_params)
    await maker_tester.wait_for_order_creation(maker_order_id)
    logger.info(f"✅ Maker order created: {maker_order_id}")

    # Taker fills with IOC sell (selling ETH for RUSD)
    taker_price = maker_price

    taker_params = OrderBuilder.from_config(spot_config).sell().at_price(0.97).ioc().build()

    logger.info(f"Taker placing IOC sell: {spot_config.min_qty} @ ${taker_price:.2f}")
    await taker_tester.create_limit_order(taker_params)

    # Wait for balance updates via WebSocket (with proper timeout)
    await maker_tester.wait_for_balance_updates(maker_initial_count, min_updates=1, timeout=5.0)
    await taker_tester.wait_for_balance_updates(taker_initial_count, min_updates=1, timeout=5.0)

    # Verify balance updates using ReyaTester methods
    maker_tester.check_ws_balance_updates_received(
        initial_update_count=maker_initial_count,
        min_updates=1,
        expected_assets=["ETH"],  # Maker bought ETH
    )

    taker_tester.check_ws_balance_updates_received(
        initial_update_count=taker_initial_count,
        min_updates=1,
        expected_assets=["RUSD"],  # Taker received RUSD
    )

    # Verify no open orders
    await maker_tester.check_no_open_orders()
    await taker_tester.check_no_open_orders()

    logger.info("✅ SPOT WS BALANCE UPDATES TEST COMPLETED")
