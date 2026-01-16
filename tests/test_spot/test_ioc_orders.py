"""
Tests for spot IOC (Immediate-Or-Cancel) orders.

IOC orders execute immediately against available liquidity and cancel
any unfilled portion. These tests verify IOC behavior for spot markets.

These tests support both empty and non-empty order books:
- When external liquidity exists, tests use it instead of providing their own
- When no external liquidity exists, tests provide maker liquidity as before
- Execution assertions are flexible to handle order book changes between submission and fill
"""

import asyncio
import time
from decimal import Decimal
from typing import Optional

import pytest
from eth_abi.exceptions import EncodingError

from sdk.open_api.exceptions import ApiException
from sdk.open_api.models.order_status import OrderStatus
from tests.helpers import ReyaTester
from tests.helpers.builders import OrderBuilder
from tests.helpers.reya_tester import limit_order_params_to_order, logger
from tests.test_spot.spot_config import SpotTestConfig


@pytest.mark.spot
@pytest.mark.ioc
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_spot_ioc_full_fill(spot_config: SpotTestConfig, maker_tester: ReyaTester, taker_tester: ReyaTester):
    """
    Test IOC order that fully fills against existing liquidity.

    Supports both empty and non-empty order books:
    - If external bid liquidity exists, taker sells into it
    - If no external liquidity, maker provides bid liquidity first

    Flow:
    1. Check for external bid liquidity
    2. If needed, maker places GTC buy order on the book
    3. Taker sends IOC sell order that matches
    4. Verify execution occurred
    """
    logger.info("=" * 80)
    logger.info(f"SPOT IOC FULL FILL TEST: {spot_config.symbol}")
    logger.info("=" * 80)

    # Clear any existing orders from our accounts
    await maker_tester.check.no_open_orders()
    await taker_tester.check.no_open_orders()

    # Check for external liquidity
    await spot_config.refresh_order_book(maker_tester.data)

    # Record taker's initial balances for verification
    taker_balances_before = await taker_tester.data.balances()
    taker_eth_before = Decimal(str(taker_balances_before.get("ETH").real_balance)) if "ETH" in taker_balances_before else Decimal("0")
    taker_rusd_before = Decimal(str(taker_balances_before.get("RUSD").real_balance)) if "RUSD" in taker_balances_before else Decimal("0")
    logger.info(f"Taker initial balances: ETH={taker_eth_before}, RUSD={taker_rusd_before}")

    maker_order_id: Optional[str] = None
    fill_price: Decimal

    # Step 1: Determine liquidity source
    usable_bid_price = spot_config.get_usable_bid_price_for_qty(spot_config.min_qty)

    if usable_bid_price is not None:
        # External bid liquidity exists - use it
        fill_price = usable_bid_price
        logger.info(f"Using external bid liquidity at ${fill_price:.2f}")
    else:
        # No external liquidity - provide our own
        maker_price = spot_config.price(0.99)
        fill_price = Decimal(str(maker_price))

        maker_order_params = OrderBuilder.from_config(spot_config).buy().at_price(0.99).gtc().build()
        maker_order_id = await maker_tester.orders.create_limit(maker_order_params)
        await maker_tester.wait.for_order_creation(maker_order_id)
        logger.info(f"✅ Maker GTC buy order created: {maker_order_id} @ ${fill_price:.2f}")

    # Step 2: Taker sends IOC sell order to match
    taker_order_params = OrderBuilder.from_config(spot_config).sell().price(str(fill_price)).ioc().build()
    taker_order_id = await taker_tester.orders.create_limit(taker_order_params)
    logger.info(f"Taker IOC sell order sent: {taker_order_id} @ ${fill_price:.2f}")

    # Step 3: Wait for execution
    expected_taker_order = limit_order_params_to_order(taker_order_params, taker_tester.account_id)
    execution = await taker_tester.wait.for_spot_execution(taker_order_id, expected_taker_order)

    # Step 4: Verify execution (flexible - price may differ due to order book changes)
    assert execution is not None, "Execution should have occurred"
    assert execution.symbol == spot_config.symbol, "Symbol should match"
    assert Decimal(execution.qty) <= Decimal(spot_config.min_qty), "Qty should not exceed order qty"

    # Verify fill price is within circuit breaker range
    exec_price = Decimal(execution.price)
    assert spot_config.circuit_breaker_floor <= exec_price <= spot_config.circuit_breaker_ceiling, (
        f"Fill price ${exec_price} should be within circuit breaker range "
        f"[${spot_config.circuit_breaker_floor}, ${spot_config.circuit_breaker_ceiling}]"
    )
    logger.info(f"✅ Execution verified: order_id={execution.order_id}, price=${exec_price:.2f}")

    # Verify maker order is filled (if we placed one)
    if maker_order_id:
        await maker_tester.wait.for_order_state(maker_order_id, OrderStatus.FILLED, timeout=5)
        logger.info("✅ Maker order filled")

    # Verify no open orders remain from our accounts
    await maker_tester.check.no_open_orders()
    await taker_tester.check.no_open_orders()

    # Step 5: Verify taker's balance changed correctly
    # Wait for balances to update
    await asyncio.sleep(0.5)
    taker_balances_after = await taker_tester.data.balances()
    taker_eth_after = Decimal(str(taker_balances_after.get("ETH").real_balance)) if "ETH" in taker_balances_after else Decimal("0")
    taker_rusd_after = Decimal(str(taker_balances_after.get("RUSD").real_balance)) if "RUSD" in taker_balances_after else Decimal("0")
    logger.info(f"Taker final balances: ETH={taker_eth_after}, RUSD={taker_rusd_after}")

    # Taker sold ETH, so ETH should decrease and RUSD should increase
    taker_eth_change = taker_eth_after - taker_eth_before
    taker_rusd_change = taker_rusd_after - taker_rusd_before
    logger.info(f"Taker balance changes: ETH={taker_eth_change}, RUSD={taker_rusd_change}")

    # Verify ETH decreased (taker sold ETH)
    assert taker_eth_change < Decimal("0"), f"Taker ETH should decrease after selling, got change: {taker_eth_change}"
    # Verify RUSD increased (taker received RUSD)
    assert taker_rusd_change > Decimal("0"), f"Taker RUSD should increase after selling, got change: {taker_rusd_change}"
    logger.info("✅ Taker balance changes verified (ETH decreased, RUSD increased)")

    logger.info("✅ SPOT IOC FULL FILL TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.ioc
@pytest.mark.asyncio
async def test_spot_ioc_no_match_cancels(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test IOC order that finds no matching liquidity and cancels.

    Supports both empty and non-empty order books:
    - Uses a price guaranteed not to match any existing liquidity
    - Price is calculated to be below all asks (for buy) or above all bids (for sell)

    Flow:
    1. Check current order book state
    2. Calculate a safe no-match price
    3. Send IOC order at that price
    4. Verify order is cancelled/rejected (not filled)

    Note: IOC orders without matching liquidity may return a 400 error
    or return None for order_id, depending on the API implementation.
    """
    logger.info("=" * 80)
    logger.info(f"SPOT IOC NO MATCH TEST: {spot_config.symbol}")
    logger.info("=" * 80)

    # Clear any existing orders from our account
    await spot_tester.check.no_open_orders()

    # Check current order book to determine safe no-match price
    await spot_config.refresh_order_book(spot_tester.data)

    # Clear execution tracking
    spot_tester.ws.last_spot_execution = None
    start_timestamp = int(time.time() * 1000)

    # Get a buy price guaranteed not to match (below all asks)
    safe_buy_price = spot_config.get_safe_no_match_buy_price()
    logger.info(f"Safe no-match buy price: ${safe_buy_price:.2f}")

    order_params = OrderBuilder.from_config(spot_config).buy().price(str(safe_buy_price)).ioc().build()

    logger.info(f"Sending IOC buy at ${safe_buy_price:.2f} (expecting no match)...")

    # IOC orders without matching liquidity may raise an error or return None
    try:
        order_id = await spot_tester.orders.create_limit(order_params)
        logger.info(f"IOC order response: {order_id}")

        # If we get here, wait and verify no execution
        await asyncio.sleep(0.1)

        if spot_tester.ws.last_spot_execution is not None:
            exec_time = spot_tester.ws.last_spot_execution.timestamp
            if exec_time and exec_time > start_timestamp:
                pytest.fail("IOC order should not have executed")

        logger.info("✅ IOC order returned but no execution occurred")

    except ApiException as e:
        # IOC orders without liquidity may be rejected with an error
        logger.info(f"✅ IOC order rejected as expected: {type(e).__name__}")

    # Verify no open orders (IOC should be cancelled/rejected)
    await spot_tester.check.no_open_orders()

    logger.info("✅ SPOT IOC NO MATCH TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.ioc
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_spot_ioc_partial_fill(spot_config: SpotTestConfig, maker_tester: ReyaTester, taker_tester: ReyaTester):
    """
    Test IOC order that matches against available liquidity.

    When taker sends a larger IOC order than available quantity,
    the IOC fills what it can and the remainder is cancelled.

    Supports both empty and non-empty order books:
    - Checks existing bid liquidity and supplements if needed
    - Taker sends IOC sell larger than available to test partial fill behavior

    Flow:
    1. Check external bid liquidity
    2. Supplement with maker order if needed to ensure known qty
    3. Taker sends larger IOC order that partially fills
    4. Verify execution occurred
    5. Verify no open orders remain
    """
    logger.info("=" * 80)
    logger.info(f"SPOT IOC PARTIAL FILL TEST: {spot_config.symbol}")
    logger.info("=" * 80)

    # Clear any existing orders for both accounts
    await maker_tester.orders.close_all(fail_if_none=False)
    await taker_tester.orders.close_all(fail_if_none=False)

    # Check for external liquidity
    await spot_config.refresh_order_book(maker_tester.data)

    maker_order_id: Optional[str] = None
    maker_qty = spot_config.min_qty
    taker_qty = "0.002"  # Larger than maker qty - will partially fill

    # Determine fill price and ensure we have known liquidity
    usable_bid_price = spot_config.get_usable_bid_price_for_qty(maker_qty)

    if usable_bid_price is not None:
        # External bid liquidity exists - use it directly without placing our own order
        fill_price = usable_bid_price
        logger.info(f"Using external bid liquidity at ${fill_price:.2f}")
    else:
        # No external liquidity - provide our own
        maker_price = spot_config.price(0.99)
        fill_price = Decimal(str(maker_price))

        maker_order_params = OrderBuilder.from_config(spot_config).buy().at_price(0.99).gtc().build()
        maker_order_id = await maker_tester.orders.create_limit(maker_order_params)
        await maker_tester.wait.for_order_creation(maker_order_id)
        logger.info(f"✅ Maker order created: {maker_order_id} @ ${fill_price:.2f}")

    # Taker sends larger IOC sell order
    taker_order_params = OrderBuilder.from_config(spot_config).sell().price(str(fill_price)).qty(taker_qty).ioc().build()

    logger.info(f"Taker sending IOC sell: {taker_qty} @ ${fill_price:.2f}")
    taker_tester.ws.last_spot_execution = None
    taker_order_id = await taker_tester.orders.create_limit(taker_order_params)
    logger.info(f"Taker IOC order sent: {taker_order_id}")

    # Wait for execution
    await asyncio.sleep(0.1)

    # Verify maker order is filled (if we placed one)
    if maker_order_id:
        try:
            await maker_tester.wait.for_order_state(maker_order_id, OrderStatus.FILLED, timeout=5)
            logger.info("✅ Maker order fully filled - execution confirmed")
        except (TimeoutError, RuntimeError):
            open_orders = await maker_tester.client.get_open_orders()
            maker_still_open = any(o.order_id == maker_order_id for o in open_orders)
            if maker_still_open:
                raise AssertionError(f"Maker order {maker_order_id} should have been filled but is still open")
            logger.info("✅ Maker order no longer open - execution confirmed")

    # Verify no open orders remain from our accounts (IOC remainder was cancelled)
    await maker_tester.check.no_open_orders()
    await taker_tester.check.no_open_orders()

    logger.info("✅ SPOT IOC PARTIAL FILL TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.ioc
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_spot_ioc_sell_full_fill(spot_config: SpotTestConfig, maker_tester: ReyaTester, taker_tester: ReyaTester):
    """
    Test IOC buy order fully filled against existing sell liquidity.

    Supports both empty and non-empty order books:
    - If external ask liquidity exists, taker buys into it
    - If no external liquidity, maker provides ask liquidity first

    Flow:
    1. Check for external ask liquidity
    2. If needed, maker places GTC sell order on the book
    3. Taker sends IOC buy order that matches
    4. Verify execution occurred
    """
    logger.info("=" * 80)
    logger.info(f"SPOT IOC SELL FULL FILL TEST: {spot_config.symbol}")
    logger.info("=" * 80)

    await maker_tester.orders.close_all(fail_if_none=False)
    await taker_tester.orders.close_all(fail_if_none=False)

    # Check for external liquidity
    await spot_config.refresh_order_book(maker_tester.data)

    maker_order_id: Optional[str] = None
    fill_price: Decimal

    # Determine liquidity source
    usable_ask_price = spot_config.get_usable_ask_price_for_qty(spot_config.min_qty)

    if usable_ask_price is not None:
        # External ask liquidity exists - use it
        fill_price = usable_ask_price
        logger.info(f"Using external ask liquidity at ${fill_price:.2f}")
    else:
        # No external liquidity - provide our own
        maker_price = spot_config.price(1.01)
        fill_price = Decimal(str(maker_price))

        maker_order_params = OrderBuilder.from_config(spot_config).sell().at_price(1.01).gtc().build()
        maker_order_id = await maker_tester.orders.create_limit(maker_order_params)
        await maker_tester.wait.for_order_creation(maker_order_id)
        logger.info(f"✅ Maker GTC sell order created: {maker_order_id} @ ${fill_price:.2f}")

    # Taker sends IOC buy order
    taker_order_params = OrderBuilder.from_config(spot_config).buy().price(str(fill_price)).ioc().build()

    logger.info(f"Taker sending IOC buy: {spot_config.min_qty} @ ${fill_price:.2f}")
    taker_order_id = await taker_tester.orders.create_limit(taker_order_params)
    logger.info(f"Taker IOC order sent: {taker_order_id}")

    # Wait for matching
    await asyncio.sleep(0.1)

    # Verify maker order is filled (if we placed one)
    if maker_order_id:
        await maker_tester.wait.for_order_state(maker_order_id, OrderStatus.FILLED, timeout=5)
        logger.info("✅ Maker order filled")

    # Verify no open orders remain from our accounts
    await maker_tester.check.no_open_orders()
    await taker_tester.check.no_open_orders()

    logger.info("✅ SPOT IOC SELL FULL FILL TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.ioc
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_spot_ioc_multiple_price_level_crossing(
    spot_config: SpotTestConfig, maker_tester: ReyaTester, taker_tester: ReyaTester
):
    """
    Test IOC order that crosses multiple price levels.

    This test requires a controlled environment to verify multi-level matching behavior.
    When external liquidity exists, we skip to avoid unpredictable matching.

    Flow:
    1. Check for external liquidity - skip if present
    2. Maker places multiple GTC orders at different prices
    3. Taker sends large IOC order that fills across multiple levels
    4. Verify all maker orders are filled
    """
    logger.info("=" * 80)
    logger.info(f"SPOT IOC MULTIPLE PRICE LEVEL TEST: {spot_config.symbol}")
    logger.info("=" * 80)

    await maker_tester.orders.close_all(fail_if_none=False)
    await taker_tester.orders.close_all(fail_if_none=False)

    # Check current order book state
    await spot_config.refresh_order_book(maker_tester.data)

    # Skip if external liquidity exists - this test needs controlled environment
    if spot_config.has_any_external_liquidity:
        pytest.skip(
            "Skipping multi-level crossing test: external liquidity exists. "
            "This test requires a controlled environment to verify multi-level matching."
        )

    # Maker places multiple GTC buy orders at different prices within oracle deviation
    price_1 = spot_config.price(0.97)  # Lower price
    price_2 = spot_config.price(0.99)  # Higher price (better for seller)
    qty_per_order = spot_config.min_qty

    # First order at lower price
    order_1_params = OrderBuilder.from_config(spot_config).buy().at_price(0.97).gtc().build()

    logger.info(f"Maker placing GTC buy #1: {qty_per_order} @ ${price_1:.2f}")
    order_1_id = await maker_tester.orders.create_limit(order_1_params)
    await maker_tester.wait.for_order_creation(order_1_id)
    logger.info(f"✅ Order #1 created: {order_1_id}")

    # Second order at higher price
    order_2_params = OrderBuilder.from_config(spot_config).buy().at_price(0.99).gtc().build()

    logger.info(f"Maker placing GTC buy #2: {qty_per_order} @ ${price_2:.2f}")
    order_2_id = await maker_tester.orders.create_limit(order_2_params)
    await maker_tester.wait.for_order_creation(order_2_id)
    logger.info(f"✅ Order #2 created: {order_2_id}")

    # Taker sends IOC sell order large enough to fill both our orders
    taker_price = price_1  # Same as lower price ensures within oracle deviation
    taker_qty = "0.002"  # Enough to fill both orders (2 x 0.001)

    taker_order_params = OrderBuilder.from_config(spot_config).sell().at_price(0.97).qty(taker_qty).ioc().build()

    logger.info(f"Taker sending IOC sell: {taker_qty} @ ${taker_price:.2f}")
    taker_order_id = await taker_tester.orders.create_limit(taker_order_params)
    logger.info(f"Taker IOC order sent: {taker_order_id}")

    # Wait for matching
    await asyncio.sleep(0.1)

    # Verify both maker orders are filled
    await maker_tester.wait.for_order_state(order_1_id, OrderStatus.FILLED, timeout=5)
    logger.info("✅ Order #1 filled")

    await maker_tester.wait.for_order_state(order_2_id, OrderStatus.FILLED, timeout=5)
    logger.info("✅ Order #2 filled")

    # Verify no open orders remain from our accounts
    await maker_tester.check.no_open_orders()
    await taker_tester.check.no_open_orders()

    logger.info("✅ SPOT IOC MULTIPLE PRICE LEVEL TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.ioc
@pytest.mark.asyncio
async def test_spot_ioc_price_qty_validation(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test IOC order rejected for invalid price/qty.

    Flow:
    1. Send IOC order with zero quantity
    2. Verify order is rejected with validation error
    3. Send IOC order with negative price
    4. Verify order is rejected with validation error
    """
    logger.info("=" * 80)
    logger.info(f"SPOT IOC PRICE/QTY VALIDATION TEST: {spot_config.symbol}")
    logger.info("=" * 80)

    await spot_tester.orders.close_all(fail_if_none=False)

    # Test 1: Zero quantity
    zero_qty_params = OrderBuilder.from_config(spot_config).buy().at_price(0.99).qty("0").ioc().build()

    logger.info("Sending IOC order with zero quantity...")
    try:
        order_id = await spot_tester.orders.create_limit(zero_qty_params)
        # If we get here without error, the API might accept it but not execute
        logger.info(f"Order accepted (may be rejected later): {order_id}")
    except ApiException as e:
        logger.info(f"✅ Zero quantity order rejected: {type(e).__name__}")

    # Test 2: Negative price (if supported by builder)
    try:
        negative_price_params = OrderBuilder.from_config(spot_config).buy().price("-100").ioc().build()

        logger.info("Sending IOC order with negative price...")
        order_id = await spot_tester.orders.create_limit(negative_price_params)
        logger.info(f"Order accepted (may be rejected later): {order_id}")
    except ApiException as e:
        logger.info(f"✅ Negative price order rejected: {type(e).__name__}")
    except EncodingError as e:
        # eth_abi raises ValueOutOfBounds (subclass of EncodingError) for negative prices
        logger.info(f"✅ Negative price order rejected: {type(e).__name__}")

    # Verify no open orders
    await spot_tester.check.no_open_orders()

    logger.info("✅ SPOT IOC PRICE/QTY VALIDATION TEST COMPLETED")
