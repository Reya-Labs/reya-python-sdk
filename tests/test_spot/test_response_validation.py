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

    # Validate required fields and types
    _validate_order_fields(order, spot_config, is_gtc=True)

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
            "Skipping partial fill test: external liquidity exists. "
            "This test requires a controlled environment."
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
    executions: SpotExecutionList = await taker_tester.client.wallet.get_wallet_spot_executions(
        address=taker_tester.owner_wallet_address
    )

    assert len(executions.data) > 0, "Should have at least one execution"

    # Validate the latest execution
    execution: SpotExecution = executions.data[0]
    _validate_spot_execution_fields(execution, spot_config)

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
            "Skipping side correctness test: external liquidity exists. "
            "This test requires a controlled environment."
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
    executions: SpotExecutionList = await taker_tester.client.wallet.get_wallet_spot_executions(
        address=taker_tester.owner_wallet_address
    )

    assert len(executions.data) > 0, "Should have at least one execution"

    latest = executions.data[0]

    # Taker was selling, so side should be A (Ask/Sell)
    assert latest.side.value == "A", f"Taker sold, expected side=A (Ask), got {latest.side}"
    logger.info(f"✅ Taker execution side is correct: {latest.side}")

    logger.info("✅ SPOT EXECUTION SIDE CORRECTNESS VALIDATION COMPLETED")


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
            "Skipping aggregation test: external liquidity exists. "
            "This test requires a controlled environment."
        )

    # Place multiple orders at the same price
    safe_price = spot_config.get_safe_no_match_buy_price()
    num_orders = 3
    order_ids = []

    for i in range(num_orders):
        order_params = OrderBuilder.from_config(spot_config).buy().price(str(safe_price)).gtc().build()
        order_id = await spot_tester.orders.create_limit(order_params)
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

    assert actual_qty >= expected_qty, (
        f"Aggregated qty should be at least {expected_qty}, got {actual_qty}"
    )
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


def _validate_order_fields(order: Order, spot_config: SpotTestConfig, is_gtc: bool = True) -> None:
    """Validate all required order fields."""
    # exchange_id
    assert hasattr(order, "exchange_id"), "Order should have 'exchange_id'"
    assert isinstance(order.exchange_id, int), f"exchange_id should be int, got {type(order.exchange_id)}"
    logger.info(f"✅ exchange_id: {order.exchange_id}")

    # symbol
    assert hasattr(order, "symbol"), "Order should have 'symbol'"
    assert isinstance(order.symbol, str), f"symbol should be str, got {type(order.symbol)}"
    assert order.symbol == spot_config.symbol, f"Expected {spot_config.symbol}, got {order.symbol}"
    logger.info(f"✅ symbol: {order.symbol}")

    # account_id
    assert hasattr(order, "account_id"), "Order should have 'account_id'"
    assert isinstance(order.account_id, int), f"account_id should be int, got {type(order.account_id)}"
    logger.info(f"✅ account_id: {order.account_id}")

    # order_id
    assert hasattr(order, "order_id"), "Order should have 'order_id'"
    assert isinstance(order.order_id, str), f"order_id should be str, got {type(order.order_id)}"
    assert len(order.order_id) > 0, "order_id should not be empty"
    logger.info(f"✅ order_id: {order.order_id}")

    # qty
    assert hasattr(order, "qty"), "Order should have 'qty'"
    if order.qty is not None:
        assert isinstance(order.qty, str), f"qty should be str, got {type(order.qty)}"
        assert _is_numeric_string(order.qty), f"qty should be numeric: {order.qty}"
        logger.info(f"✅ qty: {order.qty}")

    # side
    assert hasattr(order, "side"), "Order should have 'side'"
    assert order.side is not None, "side should not be None"
    assert hasattr(order.side, "value"), f"side should be an enum, got {type(order.side)}"
    logger.info(f"✅ side: {order.side}")

    # limit_px
    assert hasattr(order, "limit_px"), "Order should have 'limit_px'"
    assert isinstance(order.limit_px, str), f"limit_px should be str, got {type(order.limit_px)}"
    assert _is_numeric_string(order.limit_px), f"limit_px should be numeric: {order.limit_px}"
    logger.info(f"✅ limit_px: {order.limit_px}")

    # order_type
    assert hasattr(order, "order_type"), "Order should have 'order_type'"
    assert order.order_type is not None, "order_type should not be None"
    assert hasattr(order.order_type, "value"), f"order_type should be an enum, got {type(order.order_type)}"
    logger.info(f"✅ order_type: {order.order_type}")

    # time_in_force (for GTC orders)
    if is_gtc:
        assert hasattr(order, "time_in_force"), "Order should have 'time_in_force'"
        if order.time_in_force is not None:
            assert hasattr(order.time_in_force, "value"), (
                f"time_in_force should be an enum, got {type(order.time_in_force)}"
            )
            logger.info(f"✅ time_in_force: {order.time_in_force}")

    # status
    assert hasattr(order, "status"), "Order should have 'status'"
    assert order.status is not None, "status should not be None"
    assert hasattr(order.status, "value"), f"status should be an enum, got {type(order.status)}"
    logger.info(f"✅ status: {order.status}")

    # created_at
    assert hasattr(order, "created_at"), "Order should have 'created_at'"
    assert isinstance(order.created_at, int), f"created_at should be int, got {type(order.created_at)}"
    assert order.created_at > 0, f"created_at should be positive timestamp, got {order.created_at}"
    logger.info(f"✅ created_at: {order.created_at}")

    # last_update_at
    assert hasattr(order, "last_update_at"), "Order should have 'last_update_at'"
    assert isinstance(order.last_update_at, int), f"last_update_at should be int, got {type(order.last_update_at)}"
    assert order.last_update_at > 0, f"last_update_at should be positive timestamp, got {order.last_update_at}"
    logger.info(f"✅ last_update_at: {order.last_update_at}")


def _validate_spot_execution_fields(execution: SpotExecution, spot_config: SpotTestConfig) -> None:
    """Validate all required spot execution fields."""
    # exchange_id (optional)
    if execution.exchange_id is not None:
        assert isinstance(execution.exchange_id, int), (
            f"exchange_id should be int, got {type(execution.exchange_id)}"
        )
        logger.info(f"✅ exchange_id: {execution.exchange_id}")

    # symbol
    assert hasattr(execution, "symbol"), "Execution should have 'symbol'"
    assert isinstance(execution.symbol, str), f"symbol should be str, got {type(execution.symbol)}"
    assert execution.symbol == spot_config.symbol, f"Expected {spot_config.symbol}, got {execution.symbol}"
    logger.info(f"✅ symbol: {execution.symbol}")

    # account_id
    assert hasattr(execution, "account_id"), "Execution should have 'account_id'"
    assert isinstance(execution.account_id, int), f"account_id should be int, got {type(execution.account_id)}"
    logger.info(f"✅ account_id: {execution.account_id}")

    # maker_account_id
    assert hasattr(execution, "maker_account_id"), "Execution should have 'maker_account_id'"
    assert isinstance(execution.maker_account_id, int), (
        f"maker_account_id should be int, got {type(execution.maker_account_id)}"
    )
    logger.info(f"✅ maker_account_id: {execution.maker_account_id}")

    # order_id (optional)
    if execution.order_id is not None:
        assert isinstance(execution.order_id, str), f"order_id should be str, got {type(execution.order_id)}"
        logger.info(f"✅ order_id: {execution.order_id}")

    # maker_order_id (optional)
    if execution.maker_order_id is not None:
        assert isinstance(execution.maker_order_id, str), (
            f"maker_order_id should be str, got {type(execution.maker_order_id)}"
        )
        logger.info(f"✅ maker_order_id: {execution.maker_order_id}")

    # side
    assert hasattr(execution, "side"), "Execution should have 'side'"
    assert execution.side is not None, "side should not be None"
    assert hasattr(execution.side, "value"), f"side should be an enum, got {type(execution.side)}"
    logger.info(f"✅ side: {execution.side}")

    # qty
    assert hasattr(execution, "qty"), "Execution should have 'qty'"
    assert isinstance(execution.qty, str), f"qty should be str, got {type(execution.qty)}"
    assert _is_numeric_string(execution.qty), f"qty should be numeric: {execution.qty}"
    assert Decimal(execution.qty) > 0, f"qty should be positive: {execution.qty}"
    logger.info(f"✅ qty: {execution.qty}")

    # price
    assert hasattr(execution, "price"), "Execution should have 'price'"
    assert isinstance(execution.price, str), f"price should be str, got {type(execution.price)}"
    assert _is_numeric_string(execution.price), f"price should be numeric: {execution.price}"
    assert Decimal(execution.price) > 0, f"price should be positive: {execution.price}"
    logger.info(f"✅ price: {execution.price}")

    # fee
    assert hasattr(execution, "fee"), "Execution should have 'fee'"
    assert isinstance(execution.fee, str), f"fee should be str, got {type(execution.fee)}"
    assert _is_numeric_string(execution.fee), f"fee should be numeric: {execution.fee}"
    logger.info(f"✅ fee: {execution.fee}")

    # type
    assert hasattr(execution, "type"), "Execution should have 'type'"
    assert execution.type is not None, "type should not be None"
    assert hasattr(execution.type, "value"), f"type should be an enum, got {type(execution.type)}"
    logger.info(f"✅ type: {execution.type}")

    # timestamp
    assert hasattr(execution, "timestamp"), "Execution should have 'timestamp'"
    assert isinstance(execution.timestamp, int), f"timestamp should be int, got {type(execution.timestamp)}"
    assert execution.timestamp > 0, f"timestamp should be positive: {execution.timestamp}"
    logger.info(f"✅ timestamp: {execution.timestamp}")


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
