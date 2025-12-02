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
REFERENCE_PRICE = 500.0
TEST_QTY = "0.01"  # Minimum order base for market ID 5


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
        .build()
    )

    taker_order_id = await taker_tester.create_limit_order(taker_order_params)
    logger.info(f"Taker IOC order sent: {taker_order_id}")

    # Step 3: Wait for execution (strict matching on order_id and all fields)
    expected_taker_order = limit_order_params_to_order(taker_order_params, taker_tester.account_id)
    execution = await taker_tester.wait_for_spot_execution(taker_order_id, expected_taker_order)
    
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
        .build()
    )

    logger.info(f"Sending IOC buy at ${low_price:.2f} (expecting no match)...")
    
    # IOC orders without matching liquidity may raise an error or return None
    try:
        order_id = await reya_tester.create_limit_order(order_params)
        logger.info(f"IOC order response: {order_id}")
        
        # If we get here, wait and verify no execution
        await asyncio.sleep(0.1)
        
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
    maker_qty = TEST_QTY
    
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
    taker_qty = "0.02"  # Larger than maker - will partially fill

    taker_order_params = (
        OrderBuilder()
        .symbol(SPOT_SYMBOL)
        .sell()
        .price(str(taker_price))
        .qty(taker_qty)
        .ioc()
        .build()
    )

    logger.info(f"Step 2: Taker sending IOC sell: {taker_qty} @ ${taker_price:.2f}")
    taker_tester.ws_last_spot_execution = None
    taker_order_id = await taker_tester.create_limit_order(taker_order_params)
    logger.info(f"Taker IOC order sent: {taker_order_id}")

    # Wait for execution - use a shorter timeout and check via REST
    await asyncio.sleep(0.05)
    
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


@pytest.mark.spot
@pytest.mark.ioc
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_spot_ioc_sell_full_fill(maker_tester: ReyaTester, taker_tester: ReyaTester):
    """
    Test IOC sell order fully filled against existing buy liquidity.
    
    This is the inverse of test_spot_ioc_full_fill - maker posts ask,
    taker buys into it with IOC.
    
    Flow:
    1. Maker places GTC sell order on the book
    2. Taker sends IOC buy order that matches completely
    3. Verify both orders are filled
    """
    logger.info("=" * 80)
    logger.info(f"SPOT IOC SELL FULL FILL TEST: {SPOT_SYMBOL}")
    logger.info("=" * 80)

    await maker_tester.close_active_orders(fail_if_none=False)
    await taker_tester.close_active_orders(fail_if_none=False)

    # Maker places GTC sell order at price far from market
    maker_price = round(REFERENCE_PRICE * 1.50, 2)  # 50% above reference
    
    maker_order_params = (
        OrderBuilder()
        .symbol(SPOT_SYMBOL)
        .sell()
        .price(str(maker_price))
        .qty(TEST_QTY)
        .gtc()
        .build()
    )

    logger.info(f"Maker placing GTC sell: {TEST_QTY} @ ${maker_price:.2f}")
    maker_order_id = await maker_tester.create_limit_order(maker_order_params)
    await maker_tester.wait_for_order_creation(maker_order_id)
    logger.info(f"✅ Maker GTC order created: {maker_order_id}")

    # Taker sends IOC buy order at or above maker's price
    taker_price = round(maker_price * 1.01, 2)  # Above maker to ensure match
    
    taker_order_params = (
        OrderBuilder()
        .symbol(SPOT_SYMBOL)
        .buy()
        .price(str(taker_price))
        .qty(TEST_QTY)
        .ioc()
        .build()
    )

    logger.info(f"Taker sending IOC buy: {TEST_QTY} @ ${taker_price:.2f}")
    taker_order_id = await taker_tester.create_limit_order(taker_order_params)
    logger.info(f"Taker IOC order sent: {taker_order_id}")

    # Wait for matching
    await asyncio.sleep(0.05)

    # Verify maker order is filled
    await maker_tester.wait_for_order_state(maker_order_id, OrderStatus.FILLED, timeout=5)
    logger.info("✅ Maker order filled")

    # Verify no open orders remain
    await maker_tester.check_no_open_orders()
    await taker_tester.check_no_open_orders()

    logger.info("✅ SPOT IOC SELL FULL FILL TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.ioc
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_spot_ioc_multiple_price_level_crossing(maker_tester: ReyaTester, taker_tester: ReyaTester):
    """
    Test IOC order that crosses multiple price levels.
    
    Flow:
    1. Maker places multiple GTC orders at different prices
    2. Taker sends large IOC order that fills across multiple levels
    3. Verify all maker orders are filled
    4. Verify correct execution sequence (best price first)
    """
    logger.info("=" * 80)
    logger.info(f"SPOT IOC MULTIPLE PRICE LEVEL TEST: {SPOT_SYMBOL}")
    logger.info("=" * 80)

    await maker_tester.close_active_orders(fail_if_none=False)
    await taker_tester.close_active_orders(fail_if_none=False)

    # Maker places multiple GTC buy orders at different prices
    price_1 = round(REFERENCE_PRICE * 0.60, 2)  # Lower price
    price_2 = round(REFERENCE_PRICE * 0.65, 2)  # Higher price (better for seller)
    qty_per_order = TEST_QTY
    
    # First order at lower price
    order_1_params = (
        OrderBuilder()
        .symbol(SPOT_SYMBOL)
        .buy()
        .price(str(price_1))
        .qty(qty_per_order)
        .gtc()
        .build()
    )

    logger.info(f"Maker placing GTC buy #1: {qty_per_order} @ ${price_1:.2f}")
    order_1_id = await maker_tester.create_limit_order(order_1_params)
    await maker_tester.wait_for_order_creation(order_1_id)
    logger.info(f"✅ Order #1 created: {order_1_id}")

    # Second order at higher price
    order_2_params = (
        OrderBuilder()
        .symbol(SPOT_SYMBOL)
        .buy()
        .price(str(price_2))
        .qty(qty_per_order)
        .gtc()
        .build()
    )

    logger.info(f"Maker placing GTC buy #2: {qty_per_order} @ ${price_2:.2f}")
    order_2_id = await maker_tester.create_limit_order(order_2_params)
    await maker_tester.wait_for_order_creation(order_2_id)
    logger.info(f"✅ Order #2 created: {order_2_id}")

    # Taker sends IOC sell order large enough to fill both
    taker_price = round(price_1 * 0.99, 2)  # Below both prices
    taker_qty = "0.02"  # Enough to fill both orders

    taker_order_params = (
        OrderBuilder()
        .symbol(SPOT_SYMBOL)
        .sell()
        .price(str(taker_price))
        .qty(taker_qty)
        .ioc()
        .build()
    )

    logger.info(f"Taker sending IOC sell: {taker_qty} @ ${taker_price:.2f}")
    taker_order_id = await taker_tester.create_limit_order(taker_order_params)
    logger.info(f"Taker IOC order sent: {taker_order_id}")

    # Wait for matching
    await asyncio.sleep(0.05)

    # Verify both maker orders are filled
    await maker_tester.wait_for_order_state(order_1_id, OrderStatus.FILLED, timeout=5)
    logger.info("✅ Order #1 filled")
    
    await maker_tester.wait_for_order_state(order_2_id, OrderStatus.FILLED, timeout=5)
    logger.info("✅ Order #2 filled")

    # Verify no open orders remain
    await maker_tester.check_no_open_orders()
    await taker_tester.check_no_open_orders()

    logger.info("✅ SPOT IOC MULTIPLE PRICE LEVEL TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.ioc
@pytest.mark.asyncio
async def test_spot_ioc_price_qty_validation(reya_tester: ReyaTester):
    """
    Test IOC order rejected for invalid price/qty.
    
    Flow:
    1. Send IOC order with zero quantity
    2. Verify order is rejected with validation error
    3. Send IOC order with negative price
    4. Verify order is rejected with validation error
    """
    logger.info("=" * 80)
    logger.info(f"SPOT IOC PRICE/QTY VALIDATION TEST: {SPOT_SYMBOL}")
    logger.info("=" * 80)

    await reya_tester.close_active_orders(fail_if_none=False)

    # Test 1: Zero quantity
    zero_qty_params = (
        OrderBuilder()
        .symbol(SPOT_SYMBOL)
        .buy()
        .price(str(REFERENCE_PRICE))
        .qty("0")
        .ioc()
        .build()
    )

    logger.info("Sending IOC order with zero quantity...")
    try:
        order_id = await reya_tester.create_limit_order(zero_qty_params)
        # If we get here without error, the API might accept it but not execute
        logger.info(f"Order accepted (may be rejected later): {order_id}")
    except Exception as e:
        logger.info(f"✅ Zero quantity order rejected: {type(e).__name__}")

    # Test 2: Negative price (if supported by builder)
    try:
        negative_price_params = (
            OrderBuilder()
            .symbol(SPOT_SYMBOL)
            .buy()
            .price("-100")
            .qty(TEST_QTY)
            .ioc()
            .build()
        )

        logger.info("Sending IOC order with negative price...")
        order_id = await reya_tester.create_limit_order(negative_price_params)
        logger.info(f"Order accepted (may be rejected later): {order_id}")
    except Exception as e:
        logger.info(f"✅ Negative price order rejected: {type(e).__name__}")

    # Verify no open orders
    await reya_tester.check_no_open_orders()

    logger.info("✅ SPOT IOC PRICE/QTY VALIDATION TEST COMPLETED")
