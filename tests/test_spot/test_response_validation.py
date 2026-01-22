"""
Response Validation Tests

Tests for validating the structure and data types of API responses:
- Order response fields validation
- Spot execution response fields validation
- Depth levels structure validation
"""

import asyncio
import logging
from decimal import Decimal

import pytest

from sdk.open_api.models import OrderStatus
from sdk.open_api.models.depth import Depth
from sdk.open_api.models.level import Level
from sdk.open_api.models.order import Order
from sdk.open_api.models.spot_execution import SpotExecution
from sdk.open_api.models.spot_execution_list import SpotExecutionList
from tests.helpers import ReyaTester
from tests.helpers.builders.order_builder import OrderBuilder
from tests.helpers.validators import validate_order_fields, validate_spot_execution_fields
from tests.test_spot.spot_config import SpotTestConfig

logger = logging.getLogger("reya.integration_tests")


# =============================================================================
# ORDER RESPONSE VALIDATION
# =============================================================================


@pytest.mark.spot
@pytest.mark.validation
@pytest.mark.asyncio
async def test_order_response_fields_gtc(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test GTC order response contains all required fields with correct types.

    Validates:
    - exchange_id: int
    - symbol: str
    - account_id: int
    - order_id: str (non-empty)
    - qty: str (numeric)
    - side: Side enum
    - limit_px: str (numeric)
    - order_type: OrderType enum
    - time_in_force: TimeInForce enum
    - status: OrderStatus enum
    - created_at: int (timestamp)
    - last_update_at: int (timestamp)
    """
    logger.info("=" * 80)
    logger.info("ORDER RESPONSE FIELDS VALIDATION - GTC")
    logger.info("=" * 80)

    await spot_tester.orders.close_all(fail_if_none=False)

    # Place a GTC order at safe no-match price
    await spot_config.refresh_order_book(spot_tester.data)
    safe_price = spot_config.get_safe_no_match_buy_price()

    order_params = OrderBuilder.from_config(spot_config).buy().price(str(safe_price)).gtc().build()

    logger.info(f"Placing GTC buy order at ${safe_price}")
    order_id = await spot_tester.orders.create_limit(order_params)
    await spot_tester.wait.for_order_creation(order_id)

    # Fetch the order via REST
    open_orders: list[Order] = await spot_tester.client.get_open_orders()
    order = next((o for o in open_orders if o.order_id == order_id), None)

    assert order is not None, f"Order {order_id} not found in open orders"

    # Validate required fields and types using shared validator
    validate_order_fields(order, expected_symbol=spot_config.symbol, is_gtc=True, log_details=True)

    logger.info("✅ All GTC order fields validated successfully")

    # Cleanup
    await spot_tester.client.cancel_order(
        order_id=order_id, symbol=spot_config.symbol, account_id=spot_tester.account_id
    )
    await asyncio.sleep(0.1)

    logger.info("✅ ORDER RESPONSE FIELDS VALIDATION - GTC COMPLETED")


@pytest.mark.spot
@pytest.mark.validation
@pytest.mark.asyncio
async def test_order_response_fields_after_partial_fill(
    spot_config: SpotTestConfig, maker_tester: ReyaTester, taker_tester: ReyaTester
):
    """
    Test order response fields after partial fill.

    This test requires a controlled environment.
    When external liquidity exists, we skip.

    Validates:
    - exec_qty: str (filled quantity)
    - cum_qty: str (cumulative filled quantity)
    - status: PARTIALLY_FILLED or OPEN
    """
    logger.info("=" * 80)
    logger.info("ORDER RESPONSE FIELDS VALIDATION - PARTIAL FILL")
    logger.info("=" * 80)

    await maker_tester.orders.close_all(fail_if_none=False)
    await taker_tester.orders.close_all(fail_if_none=False)

    # Check for external liquidity
    await spot_config.refresh_order_book(maker_tester.data)

    if spot_config.has_any_external_liquidity:
        pytest.skip(
            "Skipping partial fill test: external liquidity exists. This test requires a controlled environment."
        )

    # Place a larger maker order
    maker_qty = str(Decimal(spot_config.min_qty) * 3)
    maker_price = spot_config.price(0.97)

    maker_params = OrderBuilder.from_config(spot_config).buy().price(str(maker_price)).qty(maker_qty).gtc().build()

    logger.info(f"Maker placing GTC buy: {maker_qty} @ ${maker_price}")
    maker_order_id = await maker_tester.orders.create_limit(maker_params)
    await maker_tester.wait.for_order_creation(maker_order_id)

    # Taker partially fills with smaller quantity
    taker_params = OrderBuilder.from_config(spot_config).sell().price(str(maker_price)).ioc().build()

    logger.info(f"Taker placing IOC sell: {spot_config.min_qty} @ ${maker_price}")
    await taker_tester.orders.create_limit(taker_params)
    await asyncio.sleep(0.3)

    # Fetch maker's order
    maker_orders: list[Order] = await maker_tester.client.get_open_orders()
    maker_order = next((o for o in maker_orders if o.order_id == maker_order_id), None)

    if maker_order is not None:
        # Order still open (partially filled)
        logger.info(f"Order status: {maker_order.status}")
        logger.info(f"exec_qty: {maker_order.exec_qty}")
        logger.info(f"cum_qty: {maker_order.cum_qty}")

        # Validate exec_qty and cum_qty are present and numeric
        if maker_order.exec_qty is not None:
            assert _is_numeric_string(maker_order.exec_qty), f"exec_qty should be numeric: {maker_order.exec_qty}"
            logger.info("✅ exec_qty is valid numeric string")

        if maker_order.cum_qty is not None:
            assert _is_numeric_string(maker_order.cum_qty), f"cum_qty should be numeric: {maker_order.cum_qty}"
            logger.info("✅ cum_qty is valid numeric string")

        # Cleanup remaining order
        await maker_tester.client.cancel_order(
            order_id=maker_order_id, symbol=spot_config.symbol, account_id=maker_tester.account_id
        )
    else:
        logger.info("Order fully filled (not in open orders)")

    await asyncio.sleep(0.1)

    logger.info("✅ ORDER RESPONSE FIELDS VALIDATION - PARTIAL FILL COMPLETED")


# =============================================================================
# SPOT EXECUTION RESPONSE VALIDATION
# =============================================================================


@pytest.mark.spot
@pytest.mark.validation
@pytest.mark.asyncio
async def test_spot_execution_response_fields(
    spot_config: SpotTestConfig, maker_tester: ReyaTester, taker_tester: ReyaTester
):
    """
    Test spot execution response contains all required fields with correct types.

    Validates:
    - exchange_id: int (optional)
    - symbol: str
    - account_id: int
    - maker_account_id: int
    - order_id: str (optional)
    - maker_order_id: str (optional)
    - side: Side enum
    - qty: str (numeric, positive)
    - price: str (numeric, positive)
    - fee: str (numeric)
    - type: ExecutionType enum
    - timestamp: int (unix timestamp)
    """
    logger.info("=" * 80)
    logger.info("SPOT EXECUTION RESPONSE FIELDS VALIDATION")
    logger.info("=" * 80)

    await maker_tester.orders.close_all(fail_if_none=False)
    await taker_tester.orders.close_all(fail_if_none=False)

    # Refresh order book state
    await spot_config.refresh_order_book(taker_tester.data)

    # Execute a trade
    usable_bid = spot_config.get_usable_bid_price_for_qty(spot_config.min_qty)
    usable_ask = spot_config.get_usable_ask_price_for_qty(spot_config.min_qty)

    maker_order_id = None

    if usable_bid is not None:
        logger.info(f"Using external bid liquidity at ${usable_bid}")
        taker_params = OrderBuilder.from_config(spot_config).sell().price(str(usable_bid)).ioc().build()
        await taker_tester.orders.create_limit(taker_params)
        await asyncio.sleep(0.5)
    elif usable_ask is not None:
        logger.info(f"Using external ask liquidity at ${usable_ask}")
        taker_params = OrderBuilder.from_config(spot_config).buy().price(str(usable_ask)).ioc().build()
        await taker_tester.orders.create_limit(taker_params)
        await asyncio.sleep(0.5)
    else:
        logger.info("No external liquidity - creating maker order")
        maker_params = OrderBuilder.from_config(spot_config).buy().at_price(0.97).gtc().build()
        maker_order_id = await maker_tester.orders.create_limit(maker_params)
        await maker_tester.wait.for_order_creation(maker_order_id)

        taker_params = OrderBuilder.from_config(spot_config).sell().at_price(0.97).ioc().build()
        await taker_tester.orders.create_limit(taker_params)
        await asyncio.sleep(0.3)
        await maker_tester.wait.for_order_state(maker_order_id, OrderStatus.FILLED, timeout=5)

    logger.info("✅ Trade executed")

    # Fetch spot executions
    assert taker_tester.owner_wallet_address is not None, "Taker wallet address should not be None"
    executions: SpotExecutionList = await taker_tester.client.wallet.get_wallet_spot_executions(
        address=taker_tester.owner_wallet_address
    )

    assert len(executions.data) > 0, "Should have at least one execution"

    # Validate the latest execution using shared validator
    execution: SpotExecution = executions.data[0]
    validate_spot_execution_fields(execution, expected_symbol=spot_config.symbol, log_details=True)

    logger.info("✅ All spot execution fields validated successfully")
    logger.info("✅ SPOT EXECUTION RESPONSE FIELDS VALIDATION COMPLETED")


@pytest.mark.spot
@pytest.mark.validation
@pytest.mark.asyncio
async def test_spot_execution_side_correctness(
    spot_config: SpotTestConfig, maker_tester: ReyaTester, taker_tester: ReyaTester
):
    """
    Test spot execution side field reflects the correct trade direction.

    This test requires a controlled environment.
    When external liquidity exists, we skip.

    Flow:
    1. Maker places buy order
    2. Taker sells into it
    3. Verify taker's execution shows correct side
    """
    logger.info("=" * 80)
    logger.info("SPOT EXECUTION SIDE CORRECTNESS VALIDATION")
    logger.info("=" * 80)

    await maker_tester.orders.close_all(fail_if_none=False)
    await taker_tester.orders.close_all(fail_if_none=False)

    # Check for external liquidity
    await spot_config.refresh_order_book(maker_tester.data)

    if spot_config.has_any_external_liquidity:
        pytest.skip(
            "Skipping side correctness test: external liquidity exists. This test requires a controlled environment."
        )

    # Maker places buy order
    maker_price = spot_config.price(0.97)
    maker_params = OrderBuilder.from_config(spot_config).buy().price(str(maker_price)).gtc().build()

    maker_order_id = await maker_tester.orders.create_limit(maker_params)
    await maker_tester.wait.for_order_creation(maker_order_id)

    # Taker sells into it
    taker_params = OrderBuilder.from_config(spot_config).sell().price(str(maker_price)).ioc().build()
    await taker_tester.orders.create_limit(taker_params)
    await asyncio.sleep(0.3)
    await maker_tester.wait.for_order_state(maker_order_id, OrderStatus.FILLED, timeout=5)

    # Fetch taker's executions
    assert taker_tester.owner_wallet_address is not None, "Taker wallet address should not be None"
    executions: SpotExecutionList = await taker_tester.client.wallet.get_wallet_spot_executions(
        address=taker_tester.owner_wallet_address
    )

    assert len(executions.data) > 0, "Should have at least one execution"

    latest = executions.data[0]

    # Taker was selling, so side should be A (Ask/Sell)
    assert latest.side.value == "A", f"Taker sold, expected side=A (Ask), got {latest.side}"
    logger.info(f"✅ Taker execution side is correct: {latest.side}")

    logger.info("✅ SPOT EXECUTION SIDE CORRECTNESS VALIDATION COMPLETED")


@pytest.mark.spot
@pytest.mark.validation
@pytest.mark.asyncio
async def test_spot_execution_maker_vs_taker_fields(
    spot_config: SpotTestConfig, maker_tester: ReyaTester, taker_tester: ReyaTester
):
    """
    Test that maker and taker see consistent execution data with correct account references.

    This test works with both empty and non-empty order books by always creating
    our own maker order at a price that will be matched by our taker.

    Validates:
    - Taker execution: account_id = taker, order_id = taker's order
    - Maker execution: account_id = maker, order_id = maker's order
    - Both have same maker_account_id and maker_order_id (pointing to maker)
    - Both have matching qty, price, symbol
    """
    logger.info("=" * 80)
    logger.info("SPOT EXECUTION MAKER VS TAKER FIELDS VALIDATION")
    logger.info("=" * 80)

    await maker_tester.orders.close_all(fail_if_none=False)
    await taker_tester.orders.close_all(fail_if_none=False)

    await spot_config.refresh_order_book(maker_tester.data)

    # Determine the best price for our maker order
    # We need to ensure our maker order is at the BEST price so taker matches with us
    # Check both bid and ask liquidity to decide which direction to trade
    best_external_bid = spot_config.get_usable_bid_price_for_qty(spot_config.min_qty)
    best_external_ask = spot_config.get_usable_ask_price_for_qty(spot_config.min_qty)

    logger.info(f"External liquidity: bid={best_external_bid}, ask={best_external_ask}")

    # Decide trade direction based on what external liquidity exists
    # Case 1: No external liquidity - default to maker buys
    # Case 2: Only bids exist - maker buys at better price than external bids
    # Case 3: Only asks exist - maker sells at better price than external asks
    # Case 4: Both exist - maker buys at better price than external bids
    if best_external_ask is not None and best_external_bid is None:
        # Only external asks exist - maker places SELL, taker BUYS
        # Place our ask BELOW the best external ask so we get matched first
        maker_price = round(float(best_external_ask) * 0.999, 2)
        logger.info(f"Only external asks exist - placing maker SELL at ${maker_price} (below ${best_external_ask})")

        maker_params = OrderBuilder.from_config(spot_config).sell().price(str(maker_price)).gtc().build()
        taker_params = OrderBuilder.from_config(spot_config).buy().price(str(maker_price)).ioc().build()
    else:
        # No external liquidity, only bids, or both - maker places BUY, taker SELLS
        if best_external_bid is not None:
            # Place our bid ABOVE the best external bid so we get matched first
            maker_price = round(float(best_external_bid) * 1.001, 2)
            logger.info(f"External bids exist - placing maker BUY at ${maker_price} (above ${best_external_bid})")
        else:
            maker_price = spot_config.price(0.99)
            logger.info(f"No external liquidity - placing maker BUY at ${maker_price}")

        maker_params = OrderBuilder.from_config(spot_config).buy().price(str(maker_price)).gtc().build()
        taker_params = OrderBuilder.from_config(spot_config).sell().price(str(maker_price)).ioc().build()

    maker_order_id = await maker_tester.orders.create_limit(maker_params)
    await maker_tester.wait.for_order_creation(maker_order_id)
    logger.info(f"✅ Maker order created: {maker_order_id}")

    taker_response = await taker_tester.orders.create_limit(taker_params)
    taker_order_id = taker_response
    logger.info(f"Taker order sent: {taker_order_id}")

    # Wait for maker order to be filled
    await asyncio.sleep(0.3)
    await maker_tester.wait.for_order_state(maker_order_id, OrderStatus.FILLED, timeout=5)
    logger.info("✅ Trade executed")

    # Fetch executions from BOTH wallets
    await asyncio.sleep(0.3)  # Allow indexing

    assert taker_tester.owner_wallet_address is not None, "Taker wallet address should not be None"
    assert maker_tester.owner_wallet_address is not None, "Maker wallet address should not be None"
    taker_executions: SpotExecutionList = await taker_tester.client.wallet.get_wallet_spot_executions(
        address=taker_tester.owner_wallet_address
    )
    maker_executions: SpotExecutionList = await maker_tester.client.wallet.get_wallet_spot_executions(
        address=maker_tester.owner_wallet_address
    )

    assert len(taker_executions.data) > 0, "Taker should have at least one execution"
    assert len(maker_executions.data) > 0, "Maker should have at least one execution"

    # Find the matching executions
    # Taker's execution: order_id = taker's order
    # Maker's execution: maker_order_id = maker's order (the order_id field is always the taker's order)
    taker_exec = next((e for e in taker_executions.data if str(e.order_id) == str(taker_order_id)), None)
    maker_exec = next((e for e in maker_executions.data if str(e.maker_order_id) == str(maker_order_id)), None)

    assert taker_exec is not None, f"Taker execution for order {taker_order_id} not found"
    assert maker_exec is not None, f"Maker execution for maker_order {maker_order_id} not found"

    logger.info("Found matching executions for both maker and taker")

    # Understanding the execution structure:
    # - account_id: ALWAYS the taker's account
    # - order_id: ALWAYS the taker's order
    # - maker_account_id: ALWAYS the maker's account
    # - maker_order_id: ALWAYS the maker's order
    # Both taker and maker see the SAME execution record with these fields

    # Validate taker's view of the execution
    logger.info("\n📋 Validating TAKER's view of execution:")
    assert (
        taker_exec.account_id == taker_tester.account_id
    ), f"account_id should be taker's account {taker_tester.account_id}, got {taker_exec.account_id}"
    logger.info(f"  ✅ account_id = {taker_exec.account_id} (taker's account)")

    assert str(taker_exec.order_id) == str(
        taker_order_id
    ), f"order_id should be taker's order {taker_order_id}, got {taker_exec.order_id}"
    logger.info(f"  ✅ order_id = {taker_exec.order_id} (taker's order)")

    assert (
        taker_exec.maker_account_id == maker_tester.account_id
    ), f"maker_account_id should be maker's account {maker_tester.account_id}, got {taker_exec.maker_account_id}"
    logger.info(f"  ✅ maker_account_id = {taker_exec.maker_account_id} (maker's account)")

    assert str(taker_exec.maker_order_id) == str(
        maker_order_id
    ), f"maker_order_id should be maker's order {maker_order_id}, got {taker_exec.maker_order_id}"
    logger.info(f"  ✅ maker_order_id = {taker_exec.maker_order_id} (maker's order)")

    # Validate maker's view of the execution (should be identical)
    logger.info("\n📋 Validating MAKER's view of execution:")
    assert (
        maker_exec.account_id == taker_tester.account_id
    ), f"account_id should be taker's account {taker_tester.account_id}, got {maker_exec.account_id}"
    logger.info(f"  ✅ account_id = {maker_exec.account_id} (taker's account - same as taker's view)")

    assert str(maker_exec.order_id) == str(
        taker_order_id
    ), f"order_id should be taker's order {taker_order_id}, got {maker_exec.order_id}"
    logger.info(f"  ✅ order_id = {maker_exec.order_id} (taker's order - same as taker's view)")

    assert (
        maker_exec.maker_account_id == maker_tester.account_id
    ), f"maker_account_id should be maker's account {maker_tester.account_id}, got {maker_exec.maker_account_id}"
    logger.info(f"  ✅ maker_account_id = {maker_exec.maker_account_id} (maker's account)")

    assert str(maker_exec.maker_order_id) == str(
        maker_order_id
    ), f"maker_order_id should be maker's order {maker_order_id}, got {maker_exec.maker_order_id}"
    logger.info(f"  ✅ maker_order_id = {maker_exec.maker_order_id} (maker's order)")

    # Validate both executions have matching trade details
    logger.info("\n📋 Validating MATCHING trade details:")
    assert (
        taker_exec.symbol == maker_exec.symbol == spot_config.symbol
    ), f"Symbol mismatch: taker={taker_exec.symbol}, maker={maker_exec.symbol}, expected={spot_config.symbol}"
    logger.info(f"  ✅ symbol matches: {taker_exec.symbol}")

    assert taker_exec.qty == maker_exec.qty, f"Qty mismatch: taker={taker_exec.qty}, maker={maker_exec.qty}"
    logger.info(f"  ✅ qty matches: {taker_exec.qty}")

    assert taker_exec.price == maker_exec.price, f"Price mismatch: taker={taker_exec.price}, maker={maker_exec.price}"
    logger.info(f"  ✅ price matches: {taker_exec.price}")

    # Timestamps should be very close (within 1 second)
    timestamp_diff = abs(taker_exec.timestamp - maker_exec.timestamp)
    assert timestamp_diff < 1000, f"Timestamp difference too large: {timestamp_diff}ms"  # 1000ms = 1 second
    logger.info(f"  ✅ timestamps match (diff={timestamp_diff}ms)")

    logger.info("\n✅ SPOT EXECUTION MAKER VS TAKER FIELDS VALIDATION COMPLETED")


# =============================================================================
# DEPTH LEVELS VALIDATION
# =============================================================================


@pytest.mark.spot
@pytest.mark.validation
@pytest.mark.asyncio
async def test_depth_response_structure(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test depth response contains all required fields with correct types.

    Validates:
    - symbol: str
    - type: DepthType enum (SNAPSHOT or UPDATE)
    - bids: list[Level]
    - asks: list[Level]
    - updated_at: int (timestamp)
    """
    logger.info("=" * 80)
    logger.info("DEPTH RESPONSE STRUCTURE VALIDATION")
    logger.info("=" * 80)

    # Fetch depth via REST
    depth: Depth = await spot_tester.data.market_depth(spot_config.symbol)

    # Validate type
    assert isinstance(depth, Depth), f"Expected Depth type, got {type(depth)}"

    # Validate required fields
    assert hasattr(depth, "symbol"), "Depth should have 'symbol' field"
    assert isinstance(depth.symbol, str), f"symbol should be str, got {type(depth.symbol)}"
    assert depth.symbol == spot_config.symbol, f"Expected symbol {spot_config.symbol}, got {depth.symbol}"
    logger.info(f"✅ symbol: {depth.symbol}")

    assert hasattr(depth, "type"), "Depth should have 'type' field"
    assert depth.type is not None, "type should not be None"
    assert hasattr(depth.type, "value"), f"type should be an enum, got {type(depth.type)}"
    logger.info(f"✅ type: {depth.type}")

    assert hasattr(depth, "bids"), "Depth should have 'bids' field"
    assert isinstance(depth.bids, list), f"bids should be list, got {type(depth.bids)}"
    logger.info(f"✅ bids: {len(depth.bids)} levels")

    assert hasattr(depth, "asks"), "Depth should have 'asks' field"
    assert isinstance(depth.asks, list), f"asks should be list, got {type(depth.asks)}"
    logger.info(f"✅ asks: {len(depth.asks)} levels")

    assert hasattr(depth, "updated_at"), "Depth should have 'updated_at' field"
    assert isinstance(depth.updated_at, int), f"updated_at should be int, got {type(depth.updated_at)}"
    assert depth.updated_at > 0, f"updated_at should be positive timestamp, got {depth.updated_at}"
    logger.info(f"✅ updated_at: {depth.updated_at}")

    logger.info("✅ DEPTH RESPONSE STRUCTURE VALIDATION COMPLETED")


@pytest.mark.spot
@pytest.mark.validation
@pytest.mark.asyncio
async def test_depth_level_structure(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test depth level structure contains correct fields.

    Validates each Level:
    - px: str (numeric, positive)
    - qty: str (numeric, positive)
    """
    logger.info("=" * 80)
    logger.info("DEPTH LEVEL STRUCTURE VALIDATION")
    logger.info("=" * 80)

    await spot_tester.orders.close_all(fail_if_none=False)

    # Place an order to ensure we have at least one level
    await spot_config.refresh_order_book(spot_tester.data)
    safe_price = spot_config.get_safe_no_match_buy_price()

    order_params = OrderBuilder.from_config(spot_config).buy().price(str(safe_price)).gtc().build()
    order_id = await spot_tester.orders.create_limit(order_params)
    await spot_tester.wait.for_order_creation(order_id)

    await asyncio.sleep(0.1)

    # Fetch depth
    depth: Depth = await spot_tester.data.market_depth(spot_config.symbol)

    # Validate bid levels
    if len(depth.bids) > 0:
        for i, level in enumerate(depth.bids[:5]):  # Check first 5
            _validate_level_structure(level, f"bid[{i}]")
        logger.info(f"✅ Validated {min(5, len(depth.bids))} bid levels")
    else:
        logger.info("ℹ️ No bid levels to validate")

    # Validate ask levels
    if len(depth.asks) > 0:
        for i, level in enumerate(depth.asks[:5]):  # Check first 5
            _validate_level_structure(level, f"ask[{i}]")
        logger.info(f"✅ Validated {min(5, len(depth.asks))} ask levels")
    else:
        logger.info("ℹ️ No ask levels to validate")

    # Cleanup
    await spot_tester.client.cancel_order(
        order_id=order_id, symbol=spot_config.symbol, account_id=spot_tester.account_id
    )
    await asyncio.sleep(0.1)

    logger.info("✅ DEPTH LEVEL STRUCTURE VALIDATION COMPLETED")


@pytest.mark.spot
@pytest.mark.validation
@pytest.mark.asyncio
async def test_depth_price_sorting(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test depth levels are correctly sorted.

    Validates:
    - Bids: sorted descending by price (highest first)
    - Asks: sorted ascending by price (lowest first)
    """
    logger.info("=" * 80)
    logger.info("DEPTH PRICE SORTING VALIDATION")
    logger.info("=" * 80)

    # Fetch depth
    depth: Depth = await spot_tester.data.market_depth(spot_config.symbol)

    # Validate bid sorting (descending)
    if len(depth.bids) >= 2:
        bid_prices = [Decimal(level.px) for level in depth.bids]
        is_descending = all(bid_prices[i] >= bid_prices[i + 1] for i in range(len(bid_prices) - 1))
        assert is_descending, f"Bids should be sorted descending: {bid_prices[:5]}"
        logger.info(f"✅ Bids sorted descending: {[float(p) for p in bid_prices[:3]]}...")
    else:
        logger.info(f"ℹ️ Only {len(depth.bids)} bid level(s), skipping sort validation")

    # Validate ask sorting (ascending)
    if len(depth.asks) >= 2:
        ask_prices = [Decimal(level.px) for level in depth.asks]
        is_ascending = all(ask_prices[i] <= ask_prices[i + 1] for i in range(len(ask_prices) - 1))
        assert is_ascending, f"Asks should be sorted ascending: {ask_prices[:5]}"
        logger.info(f"✅ Asks sorted ascending: {[float(p) for p in ask_prices[:3]]}...")
    else:
        logger.info(f"ℹ️ Only {len(depth.asks)} ask level(s), skipping sort validation")

    logger.info("✅ DEPTH PRICE SORTING VALIDATION COMPLETED")


@pytest.mark.spot
@pytest.mark.validation
@pytest.mark.asyncio
async def test_depth_quantity_aggregation(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test depth correctly aggregates quantities at the same price level.

    This test requires a controlled environment.
    When external liquidity exists, we skip.

    Flow:
    1. Place multiple orders at the same price
    2. Verify depth shows aggregated quantity
    """
    logger.info("=" * 80)
    logger.info("DEPTH QUANTITY AGGREGATION VALIDATION")
    logger.info("=" * 80)

    await spot_tester.orders.close_all(fail_if_none=False)

    # Check for external liquidity
    await spot_config.refresh_order_book(spot_tester.data)

    if spot_config.has_any_external_liquidity:
        pytest.skip(
            "Skipping aggregation test: external liquidity exists. This test requires a controlled environment."
        )

    # Place multiple orders at the same price
    safe_price = spot_config.get_safe_no_match_buy_price()
    num_orders = 3
    order_ids = []

    for _ in range(num_orders):
        order_params = OrderBuilder.from_config(spot_config).buy().price(str(safe_price)).gtc().build()
        order_id = await spot_tester.orders.create_limit(order_params)
        assert order_id is not None, "Order creation should return order_id for GTC orders"
        await spot_tester.wait.for_order_creation(order_id)
        order_ids.append(order_id)

    await asyncio.sleep(0.1)

    # Fetch depth
    depth: Depth = await spot_tester.data.market_depth(spot_config.symbol)

    # Find the level at our price
    our_level = None
    for level in depth.bids:
        if abs(Decimal(level.px) - Decimal(str(safe_price))) < Decimal("0.01"):
            our_level = level
            break

    assert our_level is not None, f"Should find level at price {safe_price}"

    # Verify aggregated quantity
    expected_qty = Decimal(spot_config.min_qty) * num_orders
    actual_qty = Decimal(our_level.qty)

    assert actual_qty >= expected_qty, f"Aggregated qty should be at least {expected_qty}, got {actual_qty}"
    logger.info(f"✅ Quantity correctly aggregated: {actual_qty} (expected >= {expected_qty})")

    # Cleanup
    for order_id in order_ids:
        try:
            await spot_tester.client.cancel_order(
                order_id=order_id, symbol=spot_config.symbol, account_id=spot_tester.account_id
            )
        except (OSError, RuntimeError):
            pass

    await asyncio.sleep(0.1)

    logger.info("✅ DEPTH QUANTITY AGGREGATION VALIDATION COMPLETED")


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


def _is_numeric_string(value: str) -> bool:
    """Check if a string represents a valid numeric value."""
    try:
        Decimal(value)
        return True
    except (ValueError, TypeError):
        return False


def _validate_level_structure(level: Level, level_name: str) -> None:
    """Validate a single depth level structure."""
    assert isinstance(level, Level), f"{level_name}: Expected Level type, got {type(level)}"

    # px
    assert hasattr(level, "px"), f"{level_name}: Level should have 'px'"
    assert isinstance(level.px, str), f"{level_name}: px should be str, got {type(level.px)}"
    assert _is_numeric_string(level.px), f"{level_name}: px should be numeric: {level.px}"
    assert Decimal(level.px) > 0, f"{level_name}: px should be positive: {level.px}"

    # qty
    assert hasattr(level, "qty"), f"{level_name}: Level should have 'qty'"
    assert isinstance(level.qty, str), f"{level_name}: qty should be str, got {type(level.qty)}"
    assert _is_numeric_string(level.qty), f"{level_name}: qty should be numeric: {level.qty}"
    assert Decimal(level.qty) > 0, f"{level_name}: qty should be positive: {level.qty}"
