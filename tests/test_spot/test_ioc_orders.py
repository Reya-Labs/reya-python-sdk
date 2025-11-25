"""
Tests for spot IOC (Immediate-Or-Cancel) orders.

IOC orders execute immediately against available liquidity and cancel
any unfilled portion. These tests verify IOC behavior for spot markets.
"""

import asyncio
import time

import pytest

from sdk.open_api.models.order_status import OrderStatus
from sdk.open_api.models.side import Side

from tests.helpers import ReyaTester
from tests.helpers.builders import OrderBuilder
from tests.helpers.reya_tester import limit_order_params_to_order, logger


# Test configuration
SPOT_SYMBOL = "WETHRUSD"
REFERENCE_PRICE = 4000.0
TEST_QTY = "0.0001"


@pytest.mark.spot
@pytest.mark.ioc
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_spot_ioc_full_fill(maker_tester: ReyaTester, taker_tester: ReyaTester):
    """
    Test IOC order that fully fills against existing liquidity.
    
    Flow:
    1. Maker places GTC order on the book
    2. Taker sends IOC order that matches completely
    3. Verify both orders are filled
    4. Verify execution details
    """
    logger.info("=" * 80)
    logger.info(f"SPOT IOC FULL FILL TEST: {SPOT_SYMBOL}")
    logger.info("=" * 80)

    # Clear any existing orders
    await maker_tester.check_no_open_orders()
    await taker_tester.check_no_open_orders()

    # Step 1: Maker places GTC buy order
    maker_price = REFERENCE_PRICE * 0.999
    
    maker_order_params = (
        OrderBuilder()
        .symbol(SPOT_SYMBOL)
        .buy()
        .price(str(maker_price))
        .qty(TEST_QTY)
        .gtc()
        .build()
    )

    maker_order_id = await maker_tester.create_limit_order(maker_order_params)
    await maker_tester.wait_for_order_creation(maker_order_id)
    logger.info(f"✅ Maker GTC order created: {maker_order_id}")

    # Step 2: Taker sends IOC sell order to match
    taker_price = REFERENCE_PRICE * 0.998  # Below maker to ensure match
    
    taker_order_params = (
        OrderBuilder()
        .symbol(SPOT_SYMBOL)
        .sell()
        .price(str(taker_price))
        .qty(TEST_QTY)
        .ioc()
        .reduce_only(False)
        .build()
    )

    taker_order_id = await taker_tester.create_limit_order(taker_order_params)
    logger.info(f"Taker IOC order sent: {taker_order_id}")

    # Step 3: Wait for execution
    expected_taker_order = limit_order_params_to_order(taker_order_params, taker_tester.account_id)
    execution = await taker_tester.wait_for_spot_execution(expected_taker_order)
    
    # Step 4: Verify execution
    await taker_tester.check_spot_execution(execution, expected_taker_order)
    logger.info(f"✅ Execution verified: {execution.order_id}")

    # Verify maker order is filled
    await maker_tester.wait_for_order_state(maker_order_id, OrderStatus.FILLED, timeout=5)
    logger.info("✅ Maker order filled")

    # Verify no open orders remain
    await maker_tester.check_no_open_orders()
    await taker_tester.check_no_open_orders()

    logger.info("✅ SPOT IOC FULL FILL TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.ioc
@pytest.mark.asyncio
async def test_spot_ioc_no_match_cancels(reya_tester: ReyaTester):
    """
    Test IOC order that finds no matching liquidity and cancels.
    
    Flow:
    1. Ensure order book is empty (no matching orders)
    2. Send IOC order with price that won't match
    3. Verify order is cancelled/rejected (not filled)
    4. Verify no execution occurred
    
    Note: IOC orders without matching liquidity may return a 400 error
    or return None for order_id, depending on the API implementation.
    """
    logger.info("=" * 80)
    logger.info(f"SPOT IOC NO MATCH TEST: {SPOT_SYMBOL}")
    logger.info("=" * 80)

    # Clear any existing orders
    await reya_tester.check_no_open_orders()

    # Clear execution tracking
    reya_tester.ws_last_spot_execution = None
    start_timestamp = int(time.time() * 1000)

    # Send IOC buy order at very low price (won't match any asks)
    low_price = REFERENCE_PRICE * 0.1  # 10% of reference - way below market
    
    order_params = (
        OrderBuilder()
        .symbol(SPOT_SYMBOL)
        .buy()
        .price(str(low_price))
        .qty(TEST_QTY)
        .ioc()
        .reduce_only(False)
        .build()
    )

    logger.info(f"Sending IOC buy at ${low_price:.2f} (expecting no match)...")
    
    # IOC orders without matching liquidity may raise an error or return None
    try:
        order_id = await reya_tester.create_limit_order(order_params)
        logger.info(f"IOC order response: {order_id}")
        
        # If we get here, wait and verify no execution
        await asyncio.sleep(0.5)
        
        if reya_tester.ws_last_spot_execution is not None:
            exec_time = reya_tester.ws_last_spot_execution.timestamp
            if exec_time and exec_time > start_timestamp:
                pytest.fail("IOC order should not have executed")
        
        logger.info("✅ IOC order returned but no execution occurred")
        
    except Exception as e:
        # IOC orders without liquidity may be rejected with an error
        logger.info(f"✅ IOC order rejected as expected: {type(e).__name__}")

    # Verify no open orders (IOC should be cancelled/rejected)
    await reya_tester.check_no_open_orders()

    logger.info("✅ SPOT IOC NO MATCH TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.ioc
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_spot_ioc_partial_fill(maker_tester: ReyaTester, taker_tester: ReyaTester):
    """
    Test IOC order that matches against available liquidity.
    
    When taker sends a larger IOC order than maker's available quantity,
    the IOC fills what it can and the remainder is cancelled.
    
    Flow:
    1. Maker places small GTC order at a specific price
    2. Taker sends larger IOC order that matches
    3. Verify execution occurred
    4. Verify maker order is filled
    5. Verify no open orders remain
    """
    logger.info("=" * 80)
    logger.info(f"SPOT IOC PARTIAL FILL TEST: {SPOT_SYMBOL}")
    logger.info("=" * 80)

    # Clear any existing orders for both accounts (fail_if_none=False since we're just cleaning up)
    await maker_tester.close_active_orders(fail_if_none=False)
    await taker_tester.close_active_orders(fail_if_none=False)

    # Use a price far from market to ensure only our orders interact
    maker_price = round(REFERENCE_PRICE * 0.70, 2)  # 30% below reference
    maker_qty = "0.0001"
    
    maker_order_params = (
        OrderBuilder()
        .symbol(SPOT_SYMBOL)
        .buy()
        .price(str(maker_price))
        .qty(maker_qty)
        .gtc()
        .build()
    )

    logger.info(f"Step 1: Maker placing GTC buy: {maker_qty} @ ${maker_price:.2f}")
    maker_order_id = await maker_tester.create_limit_order(maker_order_params)
    await maker_tester.wait_for_order_creation(maker_order_id)
    logger.info(f"✅ Maker order created: {maker_order_id}")

    # Taker sends larger IOC sell order at or below maker's price
    taker_price = round(maker_price * 0.99, 2)  # Below maker to ensure match
    taker_qty = "0.0002"  # Larger than maker - will partially fill
    
    taker_order_params = (
        OrderBuilder()
        .symbol(SPOT_SYMBOL)
        .sell()
        .price(str(taker_price))
        .qty(taker_qty)
        .ioc()
        .reduce_only(False)
        .build()
    )

    logger.info(f"Step 2: Taker sending IOC sell: {taker_qty} @ ${taker_price:.2f}")
    taker_tester.ws_last_spot_execution = None
    taker_order_id = await taker_tester.create_limit_order(taker_order_params)
    logger.info(f"Taker IOC order sent: {taker_order_id}")

    # Wait for execution - use a shorter timeout and check via REST
    await asyncio.sleep(1.0)
    
    # Verify maker order is filled (this confirms execution occurred)
    try:
        await maker_tester.wait_for_order_state(maker_order_id, OrderStatus.FILLED, timeout=5)
        logger.info("✅ Maker order fully filled - execution confirmed")
    except Exception as e:
        # Check if order is still open or was filled
        open_orders = await maker_tester.client.get_open_orders()
        maker_still_open = any(o.order_id == maker_order_id for o in open_orders)
        if maker_still_open:
            raise AssertionError(f"Maker order {maker_order_id} should have been filled but is still open")
        logger.info("✅ Maker order no longer open - execution confirmed")

    # Verify no open orders remain (IOC remainder was cancelled)
    await maker_tester.check_no_open_orders()
    await taker_tester.check_no_open_orders()

    logger.info("✅ SPOT IOC PARTIAL FILL TEST COMPLETED")
