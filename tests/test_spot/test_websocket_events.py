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

import pytest
import asyncio
import logging

from tests.helpers import ReyaTester
from tests.helpers.builders.order_builder import OrderBuilder
from sdk.open_api.models import OrderStatus

logger = logging.getLogger("reya.integration_tests")

SPOT_SYMBOL = "WETHRUSD"
REFERENCE_PRICE = 4000.0
TEST_QTY = "0.0001"


@pytest.mark.spot
@pytest.mark.websocket
@pytest.mark.asyncio
async def test_spot_ws_order_changes_on_create(reya_tester: ReyaTester):
    """
    Test WebSocket orderChanges event received on order creation.
    
    Flow:
    1. Clear order change tracking
    2. Place GTC order
    3. Verify orderChanges event received via WebSocket
    4. Verify event contains correct order data (symbol, side, qty)
    """
    logger.info("=" * 80)
    logger.info(f"SPOT WS ORDER CHANGES ON CREATE TEST: {SPOT_SYMBOL}")
    logger.info("=" * 80)

    await reya_tester.close_active_orders(fail_if_none=False)

    # Clear WebSocket tracking
    reya_tester.ws_order_changes.clear()

    # Place GTC order
    order_price = round(REFERENCE_PRICE * 0.50, 2)
    
    order_params = (
        OrderBuilder()
        .symbol(SPOT_SYMBOL)
        .buy()
        .price(str(order_price))
        .qty(TEST_QTY)
        .gtc()
        .build()
    )

    logger.info(f"Placing GTC buy: {TEST_QTY} @ ${order_price:.2f}")
    order_id = await reya_tester.create_limit_order(order_params)
    await reya_tester.wait_for_order_creation(order_id)
    logger.info(f"✅ Order created: {order_id}")

    # Wait for WebSocket event
    await asyncio.sleep(0.1)

    # Verify order change event using ReyaTester method
    reya_tester.check_ws_order_change_received(
        order_id=order_id,
        expected_symbol=SPOT_SYMBOL,
        expected_side="B",
        expected_qty=TEST_QTY,
    )

    # Cleanup
    await reya_tester.client.cancel_order(
        order_id=order_id,
        symbol=SPOT_SYMBOL,
        account_id=reya_tester.account_id
    )
    await asyncio.sleep(0.05)
    await reya_tester.check_no_open_orders()

    logger.info("✅ SPOT WS ORDER CHANGES ON CREATE TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.websocket
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_spot_ws_order_changes_on_fill(maker_tester: ReyaTester, taker_tester: ReyaTester):
    """
    Test WebSocket orderChanges event received on order fill.
    
    Flow:
    1. Maker places GTC order
    2. Taker fills the order
    3. Verify orderChanges event shows FILLED status
    """
    logger.info("=" * 80)
    logger.info(f"SPOT WS ORDER CHANGES ON FILL TEST: {SPOT_SYMBOL}")
    logger.info("=" * 80)

    await maker_tester.close_active_orders(fail_if_none=False)
    await taker_tester.close_active_orders(fail_if_none=False)

    # Clear WebSocket tracking
    maker_tester.ws_order_changes.clear()

    # Maker places GTC buy order
    maker_price = round(REFERENCE_PRICE * 0.65, 2)
    
    maker_params = (
        OrderBuilder()
        .symbol(SPOT_SYMBOL)
        .buy()
        .price(str(maker_price))
        .qty(TEST_QTY)
        .gtc()
        .build()
    )

    logger.info(f"Maker placing GTC buy: {TEST_QTY} @ ${maker_price:.2f}")
    maker_order_id = await maker_tester.create_limit_order(maker_params)
    await maker_tester.wait_for_order_creation(maker_order_id)
    logger.info(f"✅ Maker order created: {maker_order_id}")

    # Taker fills the order
    taker_price = round(maker_price * 0.99, 2)
    
    taker_params = (
        OrderBuilder()
        .symbol(SPOT_SYMBOL)
        .sell()
        .price(str(taker_price))
        .qty(TEST_QTY)
        .gtc()
        .build()
    )

    logger.info(f"Taker placing GTC sell to fill maker order...")
    await taker_tester.create_limit_order(taker_params)

    # Wait for fill
    await asyncio.sleep(0.05)
    await maker_tester.wait_for_order_state(maker_order_id, OrderStatus.FILLED, timeout=5)
    logger.info("✅ Maker order filled")

    # Verify order change event using ReyaTester method
    maker_tester.check_ws_order_change_received(
        order_id=maker_order_id,
        expected_symbol=SPOT_SYMBOL,
        expected_status=OrderStatus.FILLED,
    )

    # Verify no open orders
    await maker_tester.check_no_open_orders()
    await taker_tester.check_no_open_orders()

    logger.info("✅ SPOT WS ORDER CHANGES ON FILL TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.websocket
@pytest.mark.asyncio
async def test_spot_ws_order_changes_on_cancel(reya_tester: ReyaTester):
    """
    Test WebSocket orderChanges event received on order cancel.
    
    Flow:
    1. Place GTC order
    2. Cancel the order
    3. Verify orderChanges event shows CANCELLED status
    """
    logger.info("=" * 80)
    logger.info(f"SPOT WS ORDER CHANGES ON CANCEL TEST: {SPOT_SYMBOL}")
    logger.info("=" * 80)

    await reya_tester.close_active_orders(fail_if_none=False)

    # Place GTC order
    order_price = round(REFERENCE_PRICE * 0.50, 2)
    
    order_params = (
        OrderBuilder()
        .symbol(SPOT_SYMBOL)
        .buy()
        .price(str(order_price))
        .qty(TEST_QTY)
        .gtc()
        .build()
    )

    logger.info(f"Placing GTC buy: {TEST_QTY} @ ${order_price:.2f}")
    order_id = await reya_tester.create_limit_order(order_params)
    await reya_tester.wait_for_order_creation(order_id)
    logger.info(f"✅ Order created: {order_id}")

    # Clear WebSocket tracking before cancel to capture the cancel event
    reya_tester.ws_order_changes.clear()

    # Cancel the order
    logger.info("Cancelling order...")
    await reya_tester.client.cancel_order(
        order_id=order_id,
        symbol=SPOT_SYMBOL,
        account_id=reya_tester.account_id
    )

    # Wait for cancellation
    await reya_tester.wait_for_order_state(order_id, OrderStatus.CANCELLED, timeout=5)
    logger.info("✅ Order cancelled")

    # Verify order change event using ReyaTester method
    reya_tester.check_ws_order_change_received(
        order_id=order_id,
        expected_symbol=SPOT_SYMBOL,
        expected_status=OrderStatus.CANCELLED,
    )

    await reya_tester.check_no_open_orders()

    logger.info("✅ SPOT WS ORDER CHANGES ON CANCEL TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.websocket
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_spot_ws_spot_executions(maker_tester: ReyaTester, taker_tester: ReyaTester):
    """
    Test WebSocket spotExecutions event received on trade.
    
    Flow:
    1. Maker places GTC order
    2. Taker fills with IOC order
    3. Verify spotExecutions event received with correct details
    """
    logger.info("=" * 80)
    logger.info(f"SPOT WS SPOT EXECUTIONS TEST: {SPOT_SYMBOL}")
    logger.info("=" * 80)

    await maker_tester.close_active_orders(fail_if_none=False)
    await taker_tester.close_active_orders(fail_if_none=False)

    # Clear WebSocket tracking
    taker_tester.ws_last_spot_execution = None

    # Maker places GTC buy order
    maker_price = round(REFERENCE_PRICE * 0.65, 2)
    
    maker_params = (
        OrderBuilder()
        .symbol(SPOT_SYMBOL)
        .buy()
        .price(str(maker_price))
        .qty(TEST_QTY)
        .gtc()
        .build()
    )

    logger.info(f"Maker placing GTC buy: {TEST_QTY} @ ${maker_price:.2f}")
    maker_order_id = await maker_tester.create_limit_order(maker_params)
    await maker_tester.wait_for_order_creation(maker_order_id)
    logger.info(f"✅ Maker order created: {maker_order_id}")

    # Taker fills with IOC sell
    taker_price = round(maker_price * 0.99, 2)
    
    taker_params = (
        OrderBuilder()
        .symbol(SPOT_SYMBOL)
        .sell()
        .price(str(taker_price))
        .qty(TEST_QTY)
        .ioc()
        .reduce_only(False)
        .build()
    )

    logger.info(f"Taker placing IOC sell: {TEST_QTY} @ ${taker_price:.2f}")
    await taker_tester.create_limit_order(taker_params)

    # Wait for execution
    await asyncio.sleep(0.05)

    # Verify spot execution event using ReyaTester method
    # Execution price should be the maker's price (exact match)
    taker_tester.check_ws_spot_execution_received(
        expected_symbol=SPOT_SYMBOL,
        expected_side="A",  # Taker was selling
        expected_qty=TEST_QTY,
        expected_price=str(maker_price),
    )

    # Verify no open orders
    await maker_tester.check_no_open_orders()
    await taker_tester.check_no_open_orders()

    logger.info("✅ SPOT WS SPOT EXECUTIONS TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.websocket
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_spot_ws_balance_updates(maker_tester: ReyaTester, taker_tester: ReyaTester):
    """
    Test WebSocket accountBalances event received on trade.
    
    Flow:
    1. Record initial balance update count
    2. Execute a trade between maker and taker
    3. Verify balance update events received via WebSocket
    """
    logger.info("=" * 80)
    logger.info(f"SPOT WS BALANCE UPDATES TEST: {SPOT_SYMBOL}")
    logger.info("=" * 80)

    await maker_tester.close_active_orders(fail_if_none=False)
    await taker_tester.close_active_orders(fail_if_none=False)

    # Record initial balance update counts using ReyaTester method
    maker_initial_count = maker_tester.get_balance_update_count()
    taker_initial_count = taker_tester.get_balance_update_count()
    logger.info(f"Initial balance update counts - Maker: {maker_initial_count}, Taker: {taker_initial_count}")

    # Maker places GTC buy order (buying ETH with RUSD)
    maker_price = round(REFERENCE_PRICE * 0.65, 2)
    
    maker_params = (
        OrderBuilder()
        .symbol(SPOT_SYMBOL)
        .buy()
        .price(str(maker_price))
        .qty(TEST_QTY)
        .gtc()
        .build()
    )

    logger.info(f"Maker placing GTC buy: {TEST_QTY} @ ${maker_price:.2f}")
    maker_order_id = await maker_tester.create_limit_order(maker_params)
    await maker_tester.wait_for_order_creation(maker_order_id)
    logger.info(f"✅ Maker order created: {maker_order_id}")

    # Taker fills with IOC sell (selling ETH for RUSD)
    taker_price = round(maker_price * 0.99, 2)
    
    taker_params = (
        OrderBuilder()
        .symbol(SPOT_SYMBOL)
        .sell()
        .price(str(taker_price))
        .qty(TEST_QTY)
        .ioc()
        .reduce_only(False)
        .build()
    )

    logger.info(f"Taker placing IOC sell: {TEST_QTY} @ ${taker_price:.2f}")
    await taker_tester.create_limit_order(taker_params)

    # Wait for trade and balance updates
    await asyncio.sleep(0.05)

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
