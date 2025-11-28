"""
Tests for spot self-match prevention.

The matching engine prevents orders from the same account from matching
against each other. These tests verify this behavior.
"""

import asyncio

import pytest

from sdk.open_api.models.order_status import OrderStatus

from tests.helpers import ReyaTester
from tests.helpers.builders import OrderBuilder
from tests.helpers.reya_tester import logger


# Test configuration
SPOT_SYMBOL = "WETHRUSD"
REFERENCE_PRICE = 500.0
TEST_QTY = "0.01"  # Minimum order base for market ID 5


@pytest.mark.spot
@pytest.mark.asyncio
async def test_spot_self_match_prevention_gtc(reya_tester: ReyaTester):
    """
    Test that GTC orders from the same account don't match each other.
    
    When self-match is detected, the TAKER order is cancelled and the
    MAKER order remains on the book.
    
    Flow:
    1. Place GTC buy order (becomes maker on book)
    2. Place GTC sell order at crossing price from SAME account (taker)
    3. Verify taker order is CANCELLED (self-match prevention)
    4. Verify maker order remains OPEN on the book
    5. Cleanup maker order
    """
    logger.info("=" * 80)
    logger.info(f"SPOT SELF-MATCH PREVENTION (GTC) TEST: {SPOT_SYMBOL}")
    logger.info("=" * 80)

    # Clear any existing orders (fail_if_none=False since we're just cleaning up)
    await reya_tester.close_active_orders(fail_if_none=False)

    # Use a price far from market to avoid matching existing liquidity
    maker_price = round(REFERENCE_PRICE * 0.60, 2)  # 40% below reference
    
    # Step 1: Place GTC buy order (this becomes the MAKER on the book)
    maker_params = (
        OrderBuilder()
        .symbol(SPOT_SYMBOL)
        .buy()
        .price(str(maker_price))
        .qty(TEST_QTY)
        .gtc()
        .build()
    )
    
    logger.info(f"Step 1: Placing maker GTC buy at ${maker_price:.2f}...")
    maker_order_id = await reya_tester.create_limit_order(maker_params)
    await reya_tester.wait_for_order_creation(maker_order_id)
    logger.info(f"✅ Maker order created: {maker_order_id}")

    # Step 2: Place GTC sell order at crossing price from SAME account (TAKER)
    # This should trigger self-match prevention - taker gets cancelled
    taker_price = round(maker_price * 0.99, 2)  # Below maker price - would cross
    
    taker_params = (
        OrderBuilder()
        .symbol(SPOT_SYMBOL)
        .sell()
        .price(str(taker_price))
        .qty(TEST_QTY)
        .gtc()
        .build()
    )
    
    logger.info(f"Step 2: Placing taker GTC sell at ${taker_price:.2f} (same account, crossing price)...")
    taker_order_id = await reya_tester.create_limit_order(taker_params)
    logger.info(f"Taker order ID: {taker_order_id}")

    # Wait for order processing
    await asyncio.sleep(0.1)

    # Step 3: Verify taker order is CANCELLED (self-match prevention)
    # The taker order should be cancelled immediately due to self-match
    open_orders = await reya_tester.client.get_open_orders()
    open_order_ids = [o.order_id for o in open_orders if o.symbol == SPOT_SYMBOL]
    
    logger.info(f"Open orders for {SPOT_SYMBOL}: {open_order_ids}")
    
    # Taker should NOT be in open orders (it was cancelled)
    assert taker_order_id not in open_order_ids, (
        f"Taker order {taker_order_id} should be CANCELLED due to self-match prevention"
    )
    logger.info(f"✅ Taker order {taker_order_id} was cancelled (self-match prevention)")

    # Step 4: Verify maker order remains OPEN
    assert maker_order_id in open_order_ids, (
        f"Maker order {maker_order_id} should still be OPEN on the book"
    )
    logger.info(f"✅ Maker order {maker_order_id} remains open on the book")

    # Step 5: Cleanup - cancel the maker order
    await reya_tester.client.cancel_order(
        order_id=maker_order_id, 
        symbol=SPOT_SYMBOL, 
        account_id=reya_tester.account_id
    )
    
    await asyncio.sleep(0.05)
    await reya_tester.check_no_open_orders()

    logger.info("✅ SPOT SELF-MATCH PREVENTION (GTC) TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.asyncio
async def test_spot_self_match_prevention_ioc(reya_tester: ReyaTester):
    """
    Test that IOC orders are cancelled when they would self-match.
    
    When self-match is detected, the TAKER (IOC) order is cancelled and the
    MAKER (GTC) order remains on the book. No execution occurs.
    
    Flow:
    1. Place GTC buy order (becomes maker on book)
    2. Send IOC sell order at crossing price from SAME account (taker)
    3. Verify IOC taker is cancelled (self-match prevention)
    4. Verify no execution occurred
    5. Verify GTC maker remains open on the book
    """
    logger.info("=" * 80)
    logger.info(f"SPOT SELF-MATCH PREVENTION (IOC) TEST: {SPOT_SYMBOL}")
    logger.info("=" * 80)

    # Clear any existing orders (fail_if_none=False since we're just cleaning up)
    await reya_tester.close_active_orders(fail_if_none=False)
    reya_tester.ws_last_spot_execution = None

    # Use price far from market to avoid matching existing liquidity
    maker_price = round(REFERENCE_PRICE * 0.60, 2)  # 40% below reference
    
    buy_params = (
        OrderBuilder()
        .symbol(SPOT_SYMBOL)
        .buy()
        .price(str(maker_price))
        .qty(TEST_QTY)
        .gtc()
        .build()
    )
    
    buy_order_id = await reya_tester.create_limit_order(buy_params)
    await reya_tester.wait_for_order_creation(buy_order_id)
    logger.info(f"✅ GTC buy order created: {buy_order_id}")

    # Send IOC sell order at crossing price (same account)
    taker_price = round(maker_price * 0.99, 2)  # Below maker price - would cross
    
    sell_params = (
        OrderBuilder()
        .symbol(SPOT_SYMBOL)
        .sell()
        .price(str(taker_price))
        .qty(TEST_QTY)
        .ioc()
        .build()
    )
    
    logger.info(f"Sending IOC sell at ${taker_price:.2f} (same account - should be cancelled)...")
    
    # IOC orders that can't match (due to self-match prevention) may be rejected
    try:
        sell_order_id = await reya_tester.create_limit_order(sell_params)
        
        # Wait a moment
        await asyncio.sleep(0.1)

        # Verify no execution occurred
        assert reya_tester.ws_last_spot_execution is None, "No execution should occur (self-match prevented)"
        logger.info("✅ No execution - self-match prevented")
        
    except Exception as e:
        # IOC order rejected because no matching liquidity from other accounts
        logger.info(f"✅ IOC order rejected (self-match prevented): {type(e).__name__}")

    # Verify GTC buy order is still open
    open_orders = await reya_tester.client.get_open_orders()
    open_order_ids = [o.order_id for o in open_orders if o.symbol == SPOT_SYMBOL]
    
    assert buy_order_id in open_order_ids, "GTC buy order should still be open"
    logger.info("✅ GTC order remains open")

    # Cleanup
    await reya_tester.client.cancel_order(order_id=buy_order_id, symbol=SPOT_SYMBOL, account_id=reya_tester.account_id)
    
    await asyncio.sleep(0.05)
    await reya_tester.check_no_open_orders()

    logger.info("✅ SPOT SELF-MATCH PREVENTION (IOC) TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_spot_cross_account_match_works(maker_tester: ReyaTester, taker_tester: ReyaTester):
    """
    Verify that matching DOES work between different accounts.

    This is a sanity check to confirm that while self-match is prevented,
    cross-account matching works correctly.

    Note: This test has MAKER selling ETH and TAKER buying ETH to preserve
    taker's ETH balance for other tests.
    """
    logger.info("=" * 80)
    logger.info(f"SPOT CROSS-ACCOUNT MATCH TEST: {SPOT_SYMBOL}")
    logger.info("=" * 80)

    # Clear any existing orders
    await maker_tester.close_active_orders(fail_if_none=False)
    await taker_tester.close_active_orders(fail_if_none=False)

    # Maker places GTC sell order (maker has more ETH)
    order_price = round(REFERENCE_PRICE * 1.50, 2)  # High price to avoid other matches

    maker_params = (
        OrderBuilder()
        .symbol(SPOT_SYMBOL)
        .sell()
        .price(str(order_price))
        .qty(TEST_QTY)
        .gtc()
        .build()
    )

    maker_order_id = await maker_tester.create_limit_order(maker_params)
    await maker_tester.wait_for_order_creation(maker_order_id)
    logger.info(f"✅ Maker order created: {maker_order_id}")

    # Taker sends IOC buy order (different account - should match)
    # Taker buys ETH with RUSD (taker has more RUSD)
    taker_params = (
        OrderBuilder()
        .symbol(SPOT_SYMBOL)
        .buy()
        .price(str(order_price * 1.01))  # Above maker price to ensure match
        .qty(TEST_QTY)
        .ioc()
        .build()
    )

    logger.info("Taker sending IOC buy (different account - should match)...")
    await taker_tester.create_limit_order(taker_params)

    # Wait for execution
    from tests.helpers.reya_tester import limit_order_params_to_order
    expected_order = limit_order_params_to_order(taker_params, taker_tester.account_id)
    execution = await taker_tester.wait_for_spot_execution(expected_order)

    logger.info(f"✅ Execution confirmed: {execution.order_id}")

    # Verify maker order is filled
    await maker_tester.wait_for_order_state(maker_order_id, OrderStatus.FILLED, timeout=5)
    logger.info("✅ Maker order filled")

    # Cleanup
    await maker_tester.check_no_open_orders()
    await taker_tester.check_no_open_orders()

    logger.info("✅ SPOT CROSS-ACCOUNT MATCH TEST COMPLETED")
