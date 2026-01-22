"""
Comprehensive tests for spot self-match prevention.

The matching engine prevents orders from the same account from matching
against each other. When self-match is detected, the TAKER order is cancelled
and the MAKER order remains on the book.

Test Categories:
1. Basic Self-Match Prevention (GTC and IOC takers)
2. Price Boundary Cases (exact price, crossing prices)
3. Quantity Scenarios (partial qty, different sizes)
4. Market Maker Scenarios (multiple levels, non-crossing orders)
5. Cross-Account Matching (sanity checks that matching works between accounts)

NOTE: Self-match prevention tests require a controlled environment where our
maker order is the only liquidity at the test price. When external liquidity
exists at crossing prices, these tests are skipped to avoid false failures.
"""

import asyncio

import pytest

from sdk.open_api.exceptions import ApiException
from sdk.open_api.models.order_status import OrderStatus
from tests.helpers import ReyaTester
from tests.helpers.builders import OrderBuilder
from tests.helpers.reya_tester import limit_order_params_to_order, logger
from tests.test_spot.spot_config import SpotTestConfig


async def _skip_if_external_liquidity_exists(spot_config: SpotTestConfig, tester: ReyaTester) -> None:
    """
    Skip the test if external liquidity exists that could interfere with self-match tests.

    Self-match tests need a controlled environment where our maker order is the only
    liquidity at the test price. If external liquidity exists, the taker order might
    match against it instead of triggering self-match prevention.
    """
    await spot_config.refresh_order_book(tester.data)

    if spot_config.has_any_external_liquidity:
        pytest.skip(
            "Skipping self-match test: external liquidity exists in order book. "
            "Self-match tests require a controlled environment."
        )


# SECTION 1: Basic Self-Match Prevention
# =============================================================================


@pytest.mark.spot
@pytest.mark.asyncio
async def test_self_match_gtc_taker_sell_cancelled(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    GTC buy maker + GTC sell taker (crossing) → taker cancelled.

    Flow:
    1. Place GTC buy order (becomes maker on book)
    2. Place GTC sell order at crossing price from SAME account (taker)
    3. Verify taker is CANCELLED, maker remains OPEN
    """
    logger.info("=" * 80)
    logger.info("TEST: GTC taker sell cancelled on self-match")
    logger.info("=" * 80)

    # Skip if external liquidity exists
    await _skip_if_external_liquidity_exists(spot_config, spot_tester)

    await spot_tester.orders.close_all(fail_if_none=False)

    maker_price = spot_config.price(0.97)
    _ = maker_price  # taker_price - would cross

    # Place maker buy
    maker_params = OrderBuilder.from_config(spot_config).buy().at_price(0.97).gtc().build()
    maker_order_id = await spot_tester.orders.create_limit(maker_params)
    await spot_tester.wait.for_order_creation(maker_order_id)
    logger.info(f"✅ Maker buy: {maker_order_id} at ${maker_price:.2f}")

    # Place taker sell (same account, crossing)
    taker_params = OrderBuilder.from_config(spot_config).sell().at_price(0.97).gtc().build()
    taker_order_id = await spot_tester.orders.create_limit(taker_params)
    await asyncio.sleep(0.1)

    # Verify
    open_orders = await spot_tester.client.get_open_orders()
    open_order_ids = [o.order_id for o in open_orders if o.symbol == spot_config.symbol]

    assert taker_order_id not in open_order_ids, "Taker should be CANCELLED"
    assert maker_order_id in open_order_ids, "Maker should remain OPEN"
    logger.info("✅ Taker cancelled, maker remains open")

    # Cleanup
    await spot_tester.client.cancel_order(
        order_id=maker_order_id, symbol=spot_config.symbol, account_id=spot_tester.account_id
    )
    await asyncio.sleep(0.05)
    await spot_tester.check.no_open_orders()


@pytest.mark.spot
@pytest.mark.asyncio
async def test_self_match_gtc_taker_buy_cancelled(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    GTC sell maker + GTC buy taker (crossing) → taker cancelled.

    Flow:
    1. Place GTC sell order (becomes maker on book)
    2. Place GTC buy order at crossing price from SAME account (taker)
    3. Verify taker is CANCELLED, maker remains OPEN
    """
    logger.info("=" * 80)
    logger.info("TEST: GTC taker buy cancelled on self-match")
    logger.info("=" * 80)

    # Skip if external liquidity exists
    await _skip_if_external_liquidity_exists(spot_config, spot_tester)

    await spot_tester.orders.close_all(fail_if_none=False)

    maker_price = spot_config.price(0.97)
    _ = maker_price  # taker_price - would cross

    # Place maker sell
    maker_params = OrderBuilder.from_config(spot_config).sell().at_price(0.97).gtc().build()
    maker_order_id = await spot_tester.orders.create_limit(maker_params)
    await spot_tester.wait.for_order_creation(maker_order_id)
    logger.info(f"✅ Maker sell: {maker_order_id} at ${maker_price:.2f}")

    # Place taker buy (same account, crossing)
    taker_params = OrderBuilder.from_config(spot_config).buy().at_price(0.97).gtc().build()
    taker_order_id = await spot_tester.orders.create_limit(taker_params)
    await asyncio.sleep(0.1)

    # Verify
    open_orders = await spot_tester.client.get_open_orders()
    open_order_ids = [o.order_id for o in open_orders if o.symbol == spot_config.symbol]

    assert taker_order_id not in open_order_ids, "Taker should be CANCELLED"
    assert maker_order_id in open_order_ids, "Maker should remain OPEN"
    logger.info("✅ Taker cancelled, maker remains open")

    # Cleanup
    await spot_tester.client.cancel_order(
        order_id=maker_order_id, symbol=spot_config.symbol, account_id=spot_tester.account_id
    )
    await asyncio.sleep(0.05)
    await spot_tester.check.no_open_orders()


@pytest.mark.spot
@pytest.mark.asyncio
async def test_self_match_ioc_taker_cancelled(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    GTC buy maker + IOC sell taker (crossing) → IOC taker cancelled, no execution.

    Flow:
    1. Place GTC buy order (becomes maker on book)
    2. Send IOC sell order at crossing price from SAME account (taker)
    3. Verify IOC taker is cancelled, no execution occurs
    4. Verify GTC maker remains open on the book
    """
    logger.info("=" * 80)
    logger.info("TEST: IOC taker cancelled on self-match")
    logger.info("=" * 80)

    # Skip if external liquidity exists
    await _skip_if_external_liquidity_exists(spot_config, spot_tester)

    await spot_tester.orders.close_all(fail_if_none=False)
    spot_tester.ws.last_spot_execution = None

    maker_price = spot_config.price(0.97)
    _ = maker_price  # taker_price - calculated for reference

    # Place GTC maker buy
    maker_params = OrderBuilder.from_config(spot_config).buy().at_price(0.97).gtc().build()
    maker_order_id = await spot_tester.orders.create_limit(maker_params)
    await spot_tester.wait.for_order_creation(maker_order_id)
    logger.info(f"✅ GTC maker buy: {maker_order_id}")

    # Send IOC taker sell (same account, crossing)
    taker_params = OrderBuilder.from_config(spot_config).sell().at_price(0.97).ioc().build()

    try:
        await spot_tester.orders.create_limit(taker_params)
        await asyncio.sleep(0.1)
        assert spot_tester.ws.last_spot_execution is None, "No execution should occur"
        logger.info("✅ No execution - IOC cancelled")
    except ApiException as e:
        logger.info(f"✅ IOC rejected (self-match prevented): {type(e).__name__}")

    # Verify maker remains
    open_orders = await spot_tester.client.get_open_orders()
    open_order_ids = [o.order_id for o in open_orders if o.symbol == spot_config.symbol]
    assert maker_order_id in open_order_ids, "Maker should remain open"
    logger.info("✅ GTC maker remains open")

    # Cleanup
    await spot_tester.client.cancel_order(
        order_id=maker_order_id, symbol=spot_config.symbol, account_id=spot_tester.account_id
    )
    await asyncio.sleep(0.05)
    await spot_tester.check.no_open_orders()


# =============================================================================
# SECTION 2: Price Boundary Cases
# =============================================================================


@pytest.mark.spot
@pytest.mark.asyncio
async def test_self_match_exact_price_boundary(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Exact same price from same account triggers self-match prevention.

    When buy and sell are at the exact same price from the same account,
    this is considered "in cross" and should trigger self-match prevention.
    """
    logger.info("=" * 80)
    logger.info("TEST: Exact price boundary triggers self-match")
    logger.info("=" * 80)

    # Skip if external liquidity exists
    await _skip_if_external_liquidity_exists(spot_config, spot_tester)

    await spot_tester.orders.close_all(fail_if_none=False)
    spot_tester.ws.last_spot_execution = None

    exact_price = spot_config.price(0.97)

    # Place maker sell
    maker_params = OrderBuilder.from_config(spot_config).sell().at_price(0.97).gtc().build()
    maker_order_id = await spot_tester.orders.create_limit(maker_params)
    await spot_tester.wait.for_order_creation(maker_order_id)
    logger.info(f"✅ Maker sell at ${exact_price:.2f}")

    # Place taker buy at EXACT same price
    taker_params = OrderBuilder.from_config(spot_config).buy().at_price(0.97).gtc().build()
    taker_order_id = await spot_tester.orders.create_limit(taker_params)
    await asyncio.sleep(0.1)

    # Verify
    assert spot_tester.ws.last_spot_execution is None, "No execution at exact price"

    open_orders = await spot_tester.client.get_open_orders()
    open_order_ids = [o.order_id for o in open_orders if o.symbol == spot_config.symbol]

    assert taker_order_id not in open_order_ids, "Taker should be cancelled"
    assert maker_order_id in open_order_ids, "Maker should remain"
    logger.info("✅ Self-match prevented at exact price boundary")

    # Cleanup
    await spot_tester.client.cancel_order(
        order_id=maker_order_id, symbol=spot_config.symbol, account_id=spot_tester.account_id
    )
    await asyncio.sleep(0.05)
    await spot_tester.check.no_open_orders()


@pytest.mark.spot
@pytest.mark.asyncio
async def test_non_crossing_orders_no_self_match(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Non-crossing orders from same account are NOT self-match.

    Same account places sell at high price, then buy at low price.
    Since prices don't cross, both orders should be on the book.
    """
    logger.info("=" * 80)
    logger.info("TEST: Non-crossing orders are not self-match")
    logger.info("=" * 80)

    await spot_tester.orders.close_all(fail_if_none=False)

    sell_price = spot_config.price(1.02)  # 10% above
    buy_price = spot_config.price(0.99)  # 10% below

    # Place sell
    sell_params = OrderBuilder.from_config(spot_config).sell().at_price(1.02).gtc().build()
    sell_order_id = await spot_tester.orders.create_limit(sell_params)
    await spot_tester.wait.for_order_creation(sell_order_id)
    logger.info(f"✅ Sell at ${sell_price:.2f}")

    # Place buy (non-crossing)
    buy_params = OrderBuilder.from_config(spot_config).buy().at_price(0.99).gtc().build()
    buy_order_id = await spot_tester.orders.create_limit(buy_params)
    await spot_tester.wait.for_order_creation(buy_order_id)
    logger.info(f"✅ Buy at ${buy_price:.2f}")

    # Verify both are on book
    open_orders = await spot_tester.client.get_open_orders()
    open_order_ids = [o.order_id for o in open_orders if o.symbol == spot_config.symbol]

    assert sell_order_id in open_order_ids, "Sell should be on book"
    assert buy_order_id in open_order_ids, "Buy should be on book"
    logger.info("✅ Both non-crossing orders are on the book")

    # Cleanup
    for order_id in [sell_order_id, buy_order_id]:
        await spot_tester.client.cancel_order(
            order_id=order_id, symbol=spot_config.symbol, account_id=spot_tester.account_id
        )
    await asyncio.sleep(0.05)
    await spot_tester.check.no_open_orders()


@pytest.mark.spot
@pytest.mark.asyncio
async def test_non_crossing_ioc_cancelled_no_match(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Non-crossing IOC is cancelled due to no match, NOT self-match.

    Same account places sell at high price, then IOC buy at low price.
    The IOC should be cancelled because there's no match available.
    """
    logger.info("=" * 80)
    logger.info("TEST: Non-crossing IOC cancelled (no match)")
    logger.info("=" * 80)

    await spot_tester.orders.close_all(fail_if_none=False)
    spot_tester.ws.last_spot_execution = None

    sell_price = spot_config.price(1.04)
    _ = spot_config.price(0.96)  # buy_price - calculated for reference

    # Place GTC sell
    sell_params = OrderBuilder.from_config(spot_config).sell().at_price(1.04).gtc().build()
    sell_order_id = await spot_tester.orders.create_limit(sell_params)
    await spot_tester.wait.for_order_creation(sell_order_id)
    logger.info(f"✅ GTC sell at ${sell_price:.2f}")

    # Place IOC buy (non-crossing)
    buy_params = OrderBuilder.from_config(spot_config).buy().at_price(0.96).ioc().build()

    try:
        await spot_tester.orders.create_limit(buy_params)
        await asyncio.sleep(0.1)
        assert spot_tester.ws.last_spot_execution is None, "No execution"
        logger.info("✅ IOC cancelled - no match available")
    except ApiException as e:
        logger.info(f"✅ IOC rejected (no match): {type(e).__name__}")

    # Verify GTC sell remains
    open_orders = await spot_tester.client.get_open_orders()
    open_order_ids = [o.order_id for o in open_orders if o.symbol == spot_config.symbol]
    assert sell_order_id in open_order_ids, "GTC sell should remain"

    # Cleanup
    await spot_tester.client.cancel_order(
        order_id=sell_order_id, symbol=spot_config.symbol, account_id=spot_tester.account_id
    )
    await asyncio.sleep(0.05)
    await spot_tester.check.no_open_orders()


# =============================================================================
# SECTION 3: Quantity Scenarios
# =============================================================================


@pytest.mark.spot
@pytest.mark.asyncio
async def test_self_match_partial_qty_taker_fully_cancelled(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Self-match with different quantities: taker is FULLY cancelled.

    When taker would partially match a self-order, the ENTIRE taker
    is cancelled (not just the self-matching portion).
    """
    logger.info("=" * 80)
    logger.info("TEST: Partial qty self-match - taker fully cancelled")
    logger.info("=" * 80)

    # Skip if external liquidity exists
    await _skip_if_external_liquidity_exists(spot_config, spot_tester)

    await spot_tester.orders.close_all(fail_if_none=False)
    spot_tester.ws.last_spot_execution = None

    order_price = spot_config.price(0.97)
    maker_qty = "0.02"  # Use smaller qty to conserve funds
    taker_qty = "0.01"  # Minimum order size

    # Place maker sell with large qty
    maker_params = OrderBuilder.from_config(spot_config).sell().at_price(0.97).qty(maker_qty).gtc().build()
    maker_order_id = await spot_tester.orders.create_limit(maker_params)
    await spot_tester.wait.for_order_creation(maker_order_id)
    logger.info(f"✅ Maker sell: qty={maker_qty}")

    # Place taker buy with smaller qty
    taker_params = (
        OrderBuilder.from_config(spot_config)
        .buy()
        .price(str(round(order_price * 1.01, 2)))
        .qty(taker_qty)
        .gtc()
        .build()
    )
    taker_order_id = await spot_tester.orders.create_limit(taker_params)
    await asyncio.sleep(0.1)

    # Verify no execution
    assert spot_tester.ws.last_spot_execution is None, "No execution"

    # Verify taker cancelled, maker unchanged
    open_orders = await spot_tester.client.get_open_orders()
    open_order_ids = [o.order_id for o in open_orders if o.symbol == spot_config.symbol]

    assert taker_order_id not in open_order_ids, "Taker should be cancelled"
    assert maker_order_id in open_order_ids, "Maker should remain"

    # Verify maker qty unchanged
    maker_order = next(o for o in open_orders if o.order_id == maker_order_id)
    assert maker_order.qty == maker_qty, f"Maker qty should be {maker_qty}"
    logger.info(f"✅ Taker fully cancelled, maker unchanged (qty={maker_order.qty})")

    # Cleanup
    await spot_tester.client.cancel_order(
        order_id=maker_order_id, symbol=spot_config.symbol, account_id=spot_tester.account_id
    )
    await asyncio.sleep(0.05)
    await spot_tester.check.no_open_orders()


@pytest.mark.spot
@pytest.mark.asyncio
async def test_self_match_larger_taker_fully_cancelled(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Self-match with larger taker: taker is FULLY cancelled.

    Even when taker qty > maker qty, the entire taker is cancelled.
    """
    logger.info("=" * 80)
    logger.info("TEST: Larger taker self-match - fully cancelled")
    logger.info("=" * 80)

    # Skip if external liquidity exists
    await _skip_if_external_liquidity_exists(spot_config, spot_tester)

    await spot_tester.orders.close_all(fail_if_none=False)
    spot_tester.ws.last_spot_execution = None

    order_price = spot_config.price(0.97)
    maker_qty = "0.01"  # Minimum order size
    taker_qty = "0.02"  # Slightly larger to test larger taker scenario

    # Place maker sell with smaller qty
    maker_params = OrderBuilder.from_config(spot_config).sell().at_price(0.97).qty(maker_qty).gtc().build()
    maker_order_id = await spot_tester.orders.create_limit(maker_params)
    await spot_tester.wait.for_order_creation(maker_order_id)
    logger.info(f"✅ Maker sell: qty={maker_qty}")

    # Place taker buy with larger qty
    taker_params = (
        OrderBuilder.from_config(spot_config)
        .buy()
        .price(str(round(order_price * 1.01, 2)))
        .qty(taker_qty)
        .gtc()
        .build()
    )
    taker_order_id = await spot_tester.orders.create_limit(taker_params)
    await asyncio.sleep(0.1)

    # Verify no execution
    assert spot_tester.ws.last_spot_execution is None, "No execution"

    # Verify taker cancelled, maker unchanged
    open_orders = await spot_tester.client.get_open_orders()
    open_order_ids = [o.order_id for o in open_orders if o.symbol == spot_config.symbol]

    assert taker_order_id not in open_order_ids, "Taker should be cancelled"
    assert maker_order_id in open_order_ids, "Maker should remain"
    logger.info("✅ Larger taker fully cancelled")

    # Cleanup
    await spot_tester.client.cancel_order(
        order_id=maker_order_id, symbol=spot_config.symbol, account_id=spot_tester.account_id
    )
    await asyncio.sleep(0.05)
    await spot_tester.check.no_open_orders()


# =============================================================================
# SECTION 4: Market Maker Scenarios
# =============================================================================


@pytest.mark.spot
@pytest.mark.asyncio
async def test_market_maker_multiple_non_crossing_levels(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Market maker scenario: same account has multiple price levels on both sides.

    This was a previously problematic scenario - placing multiple sell orders
    at different prices, then multiple buy orders at different prices
    (all non-crossing). All orders should be added to the book.
    """
    logger.info("=" * 80)
    logger.info("TEST: Market maker multiple non-crossing levels")
    logger.info("=" * 80)

    await spot_tester.orders.close_all(fail_if_none=False)

    # Sells: 102%, 104%, 106% of reference
    # Buys: 98%, 96%, 94% of reference
    sell_prices = [round(spot_config.oracle_price * (1.02 + i * 0.02), 2) for i in range(3)]
    buy_prices = [round(spot_config.oracle_price * (0.98 - i * 0.02), 2) for i in range(3)]

    sell_order_ids = []
    buy_order_ids = []

    # Place 3 sell orders
    logger.info("Placing 3 sell orders at increasing prices...")
    for i, price in enumerate(sell_prices):
        params = OrderBuilder.from_config(spot_config).sell().price(str(price)).gtc().build()
        order_id = await spot_tester.orders.create_limit(params)
        await spot_tester.wait.for_order_creation(order_id)
        sell_order_ids.append(order_id)
        logger.info(f"  Sell {i + 1}: ${price:.2f}")

    # Place 3 buy orders
    logger.info("Placing 3 buy orders at decreasing prices...")
    for i, price in enumerate(buy_prices):
        params = OrderBuilder.from_config(spot_config).buy().price(str(price)).gtc().build()
        order_id = await spot_tester.orders.create_limit(params)
        await spot_tester.wait.for_order_creation(order_id)
        buy_order_ids.append(order_id)
        logger.info(f"  Buy {i + 1}: ${price:.2f}")

    # Verify all 6 orders on book
    await asyncio.sleep(0.1)
    open_orders = await spot_tester.client.get_open_orders()
    open_order_ids = [o.order_id for o in open_orders if o.symbol == spot_config.symbol]

    for order_id in sell_order_ids:
        assert order_id in open_order_ids, f"Sell {order_id} should be on book"
    for order_id in buy_order_ids:
        assert order_id in open_order_ids, f"Buy {order_id} should be on book"
    logger.info("✅ All 6 orders are on the book")

    # Cleanup
    for order_id in sell_order_ids + buy_order_ids:
        await spot_tester.client.cancel_order(
            order_id=order_id, symbol=spot_config.symbol, account_id=spot_tester.account_id
        )
    await asyncio.sleep(0.1)
    await spot_tester.check.no_open_orders()


@pytest.mark.spot
@pytest.mark.asyncio
async def test_multiple_self_matches_in_sequence(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Multiple potential self-matches in sequence.

    Place multiple maker orders from same account, then send a taker
    that would cross multiple of them. Taker should be cancelled on
    first self-match detection.
    """
    logger.info("=" * 80)
    logger.info("TEST: Multiple self-matches in sequence")
    logger.info("=" * 80)

    # Skip if external liquidity exists
    await _skip_if_external_liquidity_exists(spot_config, spot_tester)

    await spot_tester.orders.close_all(fail_if_none=False)
    spot_tester.ws.last_spot_execution = None

    base_price = spot_config.price(0.97)
    maker_order_ids = []

    # Place 3 maker sells at increasing prices
    for i in range(3):
        price = round(base_price * (1 + i * 0.01), 2)
        params = OrderBuilder.from_config(spot_config).sell().price(str(price)).gtc().build()
        order_id = await spot_tester.orders.create_limit(params)
        await spot_tester.wait.for_order_creation(order_id)
        maker_order_ids.append(order_id)
        logger.info(f"  Maker sell {i + 1}: ${price:.2f}")

    # Place taker buy that would cross all makers
    taker_price = round(base_price * 1.10, 2)  # Above all makers
    taker_params = (
        OrderBuilder()
        .symbol(spot_config.symbol)
        .buy()
        .price(str(taker_price))
        .qty(str(float(spot_config.min_qty) * 3))  # Enough to match all
        .gtc()
        .build()
    )
    taker_order_id = await spot_tester.orders.create_limit(taker_params)
    await asyncio.sleep(0.1)

    # Verify no execution
    assert spot_tester.ws.last_spot_execution is None, "No execution"

    # Verify taker cancelled, all makers remain
    open_orders = await spot_tester.client.get_open_orders()
    open_order_ids = [o.order_id for o in open_orders if o.symbol == spot_config.symbol]

    assert taker_order_id not in open_order_ids, "Taker should be cancelled"
    for order_id in maker_order_ids:
        assert order_id in open_order_ids, f"Maker {order_id} should remain"
    logger.info("✅ Taker cancelled, all makers remain")

    # Cleanup
    for order_id in maker_order_ids:
        await spot_tester.client.cancel_order(
            order_id=order_id, symbol=spot_config.symbol, account_id=spot_tester.account_id
        )
    await asyncio.sleep(0.1)
    await spot_tester.check.no_open_orders()


# =============================================================================
# SECTION 5: Partial Fill Then Self-Match Scenarios
# =============================================================================


@pytest.mark.spot
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_partial_fill_then_self_match_cancels_remainder(
    spot_config: SpotTestConfig, maker_tester: ReyaTester, taker_tester: ReyaTester
):
    """
    Taker partially fills against another account, then hits self-match.

    This is a critical edge case:
    - Account 1 has BUY 10 @ 100 (best bid)
    - Account 2 has BUY 10 @ 99 (second best bid)
    - Account 2 sends SELL 15 @ 99 GTC

    Expected behavior:
    1. Account 2's SELL matches Account 1's BUY: 10 lots @ 100 (execution)
    2. Account 2's SELL (5 remaining) would match Account 2's BUY @ 99 (self-match!)
    3. Account 2's SELL is CANCELLED (5 lots remaining, not added to book)
    4. Account 2's BUY @ 99 remains on book (untouched)

    Result: 1 execution, taker cancelled after partial fill, self-order untouched.
    """
    # Skip if external liquidity exists - this test requires controlled price levels
    if spot_config.has_any_external_liquidity:
        pytest.skip(
            "Skipping partial fill self-match test: external liquidity exists. "
            "Test requires controlled environment for specific matching behavior."
        )

    logger.info("=" * 80)
    logger.info("TEST: Partial fill then self-match cancels remainder")
    logger.info("=" * 80)

    await maker_tester.orders.close_all(fail_if_none=False)
    await taker_tester.orders.close_all(fail_if_none=False)

    # Prices: Account 1 BUY @ 100, Account 2 BUY @ 99
    account1_buy_price = spot_config.price(0.97)  # Best bid (higher)
    account2_buy_price = spot_config.price(0.97)  # Second best bid (lower)
    account2_sell_price = account2_buy_price  # Sell at same price as own buy

    fill_qty = spot_config.min_qty  # Each order is this qty
    taker_qty = str(float(spot_config.min_qty) * 2)  # 2x to ensure partial fill + remainder (must be valid qty step)

    # Step 1: Account 1 (maker_tester) places BUY @ 100 (best bid)
    account1_buy_params = OrderBuilder.from_config(spot_config).buy().at_price(0.97).qty(fill_qty).gtc().build()
    account1_buy_id = await maker_tester.orders.create_limit(account1_buy_params)
    await maker_tester.wait.for_order_creation(account1_buy_id)
    logger.info(f"✅ Account 1 BUY: {account1_buy_id} @ ${account1_buy_price:.2f}")

    # Step 2: Account 2 (taker_tester) places BUY @ 99 (second best bid)
    account2_buy_params = OrderBuilder.from_config(spot_config).buy().at_price(0.97).qty(fill_qty).gtc().build()
    account2_buy_id = await taker_tester.orders.create_limit(account2_buy_params)
    await taker_tester.wait.for_order_creation(account2_buy_id)
    logger.info(f"✅ Account 2 BUY: {account2_buy_id} @ ${account2_buy_price:.2f}")

    # Verify both buys are on book
    open_orders_maker = await maker_tester.client.get_open_orders()
    open_orders_taker = await taker_tester.client.get_open_orders()
    assert any(o.order_id == account1_buy_id for o in open_orders_maker), "Account 1 buy should be on book"
    assert any(o.order_id == account2_buy_id for o in open_orders_taker), "Account 2 buy should be on book"
    logger.info("✅ Both BUY orders on book")

    # Step 3: Account 2 sends SELL @ 99 with qty = 1.5x (will partially fill then self-match)
    account2_sell_params = OrderBuilder.from_config(spot_config).sell().at_price(0.97).qty(taker_qty).gtc().build()
    logger.info(f"Account 2 sending SELL @ ${account2_sell_price:.2f}, qty={taker_qty}...")
    account2_sell_id = await taker_tester.orders.create_limit(account2_sell_params)

    # Wait for execution (strict matching on order_id and all fields)
    expected_order = limit_order_params_to_order(account2_sell_params, taker_tester.account_id)
    execution = await taker_tester.wait.for_spot_execution(account2_sell_id, expected_order, timeout=5)
    logger.info(f"✅ Execution: Account 2 SELL matched Account 1 BUY, qty={execution.qty}")

    # Step 4: Verify Account 1's BUY is filled
    await maker_tester.wait.for_order_state(account1_buy_id, OrderStatus.FILLED, timeout=5)
    logger.info("✅ Account 1's BUY filled")

    # Step 5: Verify Account 2's SELL is CANCELLED (not on book, not partially filled on book)
    await asyncio.sleep(0.2)  # Allow time for order state to propagate

    open_orders_taker = await taker_tester.client.get_open_orders()
    taker_order_ids = [o.order_id for o in open_orders_taker if o.symbol == spot_config.symbol]

    assert (
        account2_sell_id not in taker_order_ids
    ), f"Account 2's SELL {account2_sell_id} should be CANCELLED after self-match, not on book"
    logger.info("✅ Account 2's SELL cancelled after partial fill (self-match prevention)")

    # Step 6: Verify Account 2's BUY @ 99 is STILL on book (untouched by self-match)
    assert (
        account2_buy_id in taker_order_ids
    ), f"Account 2's BUY {account2_buy_id} should still be on book (self-match doesn't cancel maker)"

    # Verify the buy order quantity is unchanged
    account2_buy_order = next(o for o in open_orders_taker if o.order_id == account2_buy_id)
    assert (
        account2_buy_order.qty == fill_qty
    ), f"Account 2's BUY qty should be unchanged: expected {fill_qty}, got {account2_buy_order.qty}"
    logger.info(f"✅ Account 2's BUY remains on book, qty={account2_buy_order.qty} (untouched)")

    # Cleanup
    await taker_tester.client.cancel_order(
        order_id=account2_buy_id, symbol=spot_config.symbol, account_id=taker_tester.account_id
    )
    await asyncio.sleep(0.05)
    await maker_tester.check.no_open_orders()
    await taker_tester.check.no_open_orders()

    logger.info("✅ PARTIAL FILL THEN SELF-MATCH TEST COMPLETED")


# =============================================================================
# SECTION 6: Cross-Account Matching (Sanity Checks)
# =============================================================================


@pytest.mark.spot
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_cross_account_match_works(
    spot_config: SpotTestConfig, maker_tester: ReyaTester, taker_tester: ReyaTester
):
    """
    Sanity check: matching DOES work between different accounts.

    Confirms that while self-match is prevented, cross-account matching
    works correctly.
    """
    # Skip if external liquidity exists - taker would match external orders instead of our maker
    if spot_config.has_any_external_liquidity:
        pytest.skip(
            "Skipping cross-account match test: external liquidity exists. "
            "Taker orders would match external liquidity first."
        )

    logger.info("=" * 80)
    logger.info("TEST: Cross-account matching works")
    logger.info("=" * 80)

    await maker_tester.orders.close_all(fail_if_none=False)
    await taker_tester.orders.close_all(fail_if_none=False)

    order_price = spot_config.price(1.04)

    # Maker places GTC sell
    maker_params = OrderBuilder.from_config(spot_config).sell().at_price(1.04).gtc().build()
    maker_order_id = await maker_tester.orders.create_limit(maker_params)
    await maker_tester.wait.for_order_creation(maker_order_id)
    logger.info(f"✅ Maker sell: {maker_order_id}")

    # Taker sends IOC buy (different account)
    taker_params = OrderBuilder.from_config(spot_config).buy().price(str(round(order_price * 1.01, 2))).ioc().build()
    taker_order_id = await taker_tester.orders.create_limit(taker_params)

    # Wait for execution (strict matching on order_id and all fields)
    expected_order = limit_order_params_to_order(taker_params, taker_tester.account_id)
    execution = await taker_tester.wait.for_spot_execution(taker_order_id, expected_order)
    logger.info(f"✅ Execution: {execution.order_id}")

    # Verify maker filled
    await maker_tester.wait.for_order_state(maker_order_id, OrderStatus.FILLED, timeout=5)
    logger.info("✅ Maker filled")

    await maker_tester.check.no_open_orders()
    await taker_tester.check.no_open_orders()


@pytest.mark.spot
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_non_crossing_orders_can_match_other_accounts(
    spot_config: SpotTestConfig, maker_tester: ReyaTester, taker_tester: ReyaTester
):
    """
    Non-crossing orders from same account can still match with other accounts.

    Account 1 has non-crossing orders on both sides.
    Account 2 places an order that crosses Account 1's order.
    The cross-account match should succeed.
    """
    # Skip if external liquidity exists - taker would match external orders instead of our maker
    if spot_config.has_any_external_liquidity:
        pytest.skip(
            "Skipping non-crossing orders test: external liquidity exists. "
            "Taker orders would match external liquidity first."
        )

    logger.info("=" * 80)
    logger.info("TEST: Non-crossing orders can match other accounts")
    logger.info("=" * 80)

    await maker_tester.orders.close_all(fail_if_none=False)
    await taker_tester.orders.close_all(fail_if_none=False)

    account1_sell_price = spot_config.price(1.04)
    account1_buy_price = spot_config.price(0.97)
    _ = spot_config.price(0.96)  # account2_sell_price - calculated for reference

    # Account 1 places sell at high price
    sell_params = OrderBuilder.from_config(spot_config).sell().at_price(1.04).gtc().build()
    account1_sell_id = await maker_tester.orders.create_limit(sell_params)
    await maker_tester.wait.for_order_creation(account1_sell_id)
    logger.info(f"✅ Account 1 sell: ${account1_sell_price:.2f}")

    # Account 1 places buy at low price (non-crossing)
    buy_params = OrderBuilder.from_config(spot_config).buy().at_price(0.97).gtc().build()
    account1_buy_id = await maker_tester.orders.create_limit(buy_params)
    await maker_tester.wait.for_order_creation(account1_buy_id)
    logger.info(f"✅ Account 1 buy: ${account1_buy_price:.2f}")

    # Verify both on book
    open_orders = await maker_tester.client.get_open_orders()
    open_order_ids = [o.order_id for o in open_orders if o.symbol == spot_config.symbol]
    assert account1_sell_id in open_order_ids
    assert account1_buy_id in open_order_ids
    logger.info("✅ Both Account 1 orders on book")

    # Account 2 places sell that crosses Account 1's buy
    taker_params = OrderBuilder.from_config(spot_config).sell().at_price(0.96).ioc().build()
    taker_order_id = await taker_tester.orders.create_limit(taker_params)

    # Wait for execution (strict matching on order_id and all fields)
    expected_order = limit_order_params_to_order(taker_params, taker_tester.account_id)
    execution = await taker_tester.wait.for_spot_execution(taker_order_id, expected_order)
    logger.info(f"✅ Execution: {execution.order_id}")

    # Verify Account 1's buy filled, sell remains
    await maker_tester.wait.for_order_state(account1_buy_id, OrderStatus.FILLED, timeout=5)
    logger.info("✅ Account 1's buy filled")

    open_orders = await maker_tester.client.get_open_orders()
    open_order_ids = [o.order_id for o in open_orders if o.symbol == spot_config.symbol]
    assert account1_sell_id in open_order_ids, "Account 1 sell should remain"
    logger.info("✅ Account 1's sell remains on book")

    # Cleanup
    await maker_tester.client.cancel_order(
        order_id=account1_sell_id, symbol=spot_config.symbol, account_id=maker_tester.account_id
    )
    await asyncio.sleep(0.05)
    await maker_tester.check.no_open_orders()
    await taker_tester.check.no_open_orders()
