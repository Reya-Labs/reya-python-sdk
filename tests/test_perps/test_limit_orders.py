#!/usr/bin/env python3

import pytest

from sdk.open_api import OrderStatus, RequestError, RequestErrorCode
from sdk.open_api.exceptions import BadRequestException
from sdk.open_api.models.perp_execution import PerpExecution
from sdk.open_api.models.position import Position
from sdk.open_api.models.side import Side
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api.models import LimitOrderParameters
from tests.helpers import ReyaTester
from tests.helpers.reya_tester import limit_order_params_to_order, logger


async def assert_position_changes(
    execution_details: PerpExecution,
    reya_tester: ReyaTester,
    position_before: Position | None = None,
):
    """Assert that positions have changed as expected"""
    if position_before is None:
        position_before = Position(
            exchangeId=execution_details.exchange_id,
            symbol=execution_details.symbol,
            accountId=execution_details.account_id,
            qty="0",
            side=Side.B,
            avgEntryPrice="0",
            avgEntryFundingValue="0",
            lastTradeSequenceNumber=int(execution_details.sequence_number) - 1,
        )
    position_after_qty = float(position_before.qty) + float(execution_details.qty)

    expected_average_entry_price = float(position_before.avg_entry_price)
    if float(position_before.qty) == 0 or (execution_details.side == position_before.side):
        expected_average_entry_price = (
            float(position_before.avg_entry_price) * float(position_before.qty)
            + float(execution_details.qty) * float(execution_details.price)
        ) / position_after_qty
    # Wait for position to be confirmed via both REST and WebSocket
    # await reya_tester.wait_for_position(execution_details.symbol)

    await reya_tester.check_position(
        symbol=execution_details.symbol,
        expected_exchange_id=execution_details.exchange_id,
        expected_account_id=execution_details.account_id,
        expected_qty=execution_details.qty,
        expected_side=execution_details.side,
        expected_avg_entry_price=str(expected_average_entry_price),
        expected_last_trade_sequence_number=int(execution_details.sequence_number),
    )
    logger.info("✅ New position recorded correctly")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "test_qty, test_is_buy",
    [
        (0.01, True),
        (0.01, False),
    ],
)
async def test_success_ioc(reya_tester: ReyaTester, test_qty, test_is_buy):
    """Test creating an order and confirming execution"""
    symbol = "ETHRUSDPERP"

    # Get current prices to determine order parameters
    market_price = await reya_tester.get_current_price()
    logger.info(f"Market price: {market_price}")

    # Get positions before order
    await reya_tester.check_position_not_open(symbol)
    await reya_tester.check_no_open_orders()

    price_with_offset = float(market_price) * 1.1 if test_is_buy else float(market_price) * 0.9
    limit_order_params = LimitOrderParameters(
        symbol=symbol,
        is_buy=test_is_buy,
        limit_px=str(price_with_offset),
        qty=str(test_qty),
        time_in_force=TimeInForce.IOC,
        reduce_only=False,
    )

    logger.info("Trade confirmation task")

    await reya_tester.create_limit_order(limit_order_params)

    # Validate
    expected_order = limit_order_params_to_order(limit_order_params, reya_tester.account_id)
    execution = await reya_tester.wait_for_order_execution(expected_order)
    assert execution is not None
    await reya_tester.check_no_open_orders()
    order_execution_details = await reya_tester.check_order_execution(execution, expected_order)
    await assert_position_changes(order_execution_details, reya_tester)

    logger.info("Order execution test complete")


@pytest.mark.asyncio
async def test_failure_ioc_with_reduce_only_on_empty_position(reya_tester: ReyaTester):
    """Test 1: Try IOC with reduce_only flag but the position is actually expanding (should error)"""
    symbol = "ETHRUSDPERP"

    # SETUP
    market_price = await reya_tester.get_current_price()
    logger.info(f"Market price: {market_price}")

    test_qty = 0.01
    test_is_buy = True
    price_with_offset = float(market_price) * 1.1 if test_is_buy else float(market_price) * 0.9

    await reya_tester.check_no_open_orders()
    await reya_tester.check_position_not_open(symbol)

    order_params_reduce = LimitOrderParameters(
        symbol=symbol,
        is_buy=test_is_buy,
        limit_px=str(price_with_offset),
        qty=str(test_qty),
        time_in_force=TimeInForce.IOC,
        reduce_only=True,
    )
    try:
        await reya_tester.create_limit_order(order_params_reduce)
        assert False, "Order should not have been accepted with reduce_only flag on no position"
    except BadRequestException as e:
        assert e.data is not None
        requestError: RequestError = e.data
        # API returns human-readable error message
        assert "Reduce-Only" in requestError.message or "ReduceOnly" in requestError.message
        assert requestError.error == RequestErrorCode.CREATE_ORDER_OTHER_ERROR


@pytest.mark.asyncio
async def test_failure_ioc_with_invalid_limit_px(reya_tester: ReyaTester):
    """Try IOC with limit price on the opposite side of the market, should revert"""
    symbol = "ETHRUSDPERP"

    # SETUP
    market_price = await reya_tester.get_current_price()
    logger.info(f"Market price: {market_price}")

    await reya_tester.check_no_open_orders()
    await reya_tester.check_position_not_open(symbol)

    test_qty = 0.01
    test_is_buy = True
    invalid_price = float(market_price) * 0.9  # Price below market for buy order (should be rejected)
    order_params_invalid = LimitOrderParameters(
        symbol=symbol,
        is_buy=test_is_buy,
        limit_px=str(invalid_price),
        qty=str(test_qty),
        time_in_force=TimeInForce.IOC,
        reduce_only=False,
    )
    try:
        response = await reya_tester.create_limit_order(order_params_invalid)
        # If we get here, the test failed - order should not have been accepted
        assert not response, f"Order should not have been accepted with invalid price {invalid_price} for buy order"
    except BadRequestException as e:
        assert e.data is not None
        requestError: RequestError = e.data
        assert requestError.message == "UnacceptableOrderPrice"
        assert requestError.error == RequestErrorCode.CREATE_ORDER_OTHER_ERROR


@pytest.mark.asyncio
async def test_failure_ioc_with_input_validation(reya_tester: ReyaTester):
    """Try various invalid inputs"""
    symbol = "ETHRUSDPERP"

    test_cases = [
        {
            "name": "Invalid symbol",
            "params": {
                "symbol": 100000,
                "is_buy": True,
                "limit_px": "100",
                "qty": "0.01",
                "time_in_force": TimeInForce.GTC,
                "reduce_only": False,
            },
        },
        {
            "name": "Wrong symbol",
            "params": {
                "symbol": "wrong",
                "is_buy": True,
                "limit_px": "100",
                "qty": "0.01",
                "time_in_force": TimeInForce.GTC,
                "reduce_only": False,
            },
        },
        {
            "name": "Empty symbol",
            "params": {
                "symbol": "",  # Empty symbol should fail validation
                "is_buy": True,
                "limit_px": "100",
                "qty": "0.01",
                "time_in_force": TimeInForce.GTC,
                "reduce_only": False,
            },
        },
        {
            "name": "Missing symbol",
            "params": {
                # symbol is missing - should raise KeyError
                "is_buy": True,
                "limit_px": "100",
                "qty": "0.01",
                "time_in_force": TimeInForce.GTC,
                "reduce_only": False,
            },
        },
        {
            "name": "Missing is_buy",
            "params": {
                "symbol": symbol,
                # is_buy is missing - should raise KeyError
                "limit_px": "100",
                "qty": "0.01",
                "time_in_force": TimeInForce.GTC,
                "reduce_only": False,
            },
        },
        {
            "name": "Invalid is_buy",
            "params": {
                "symbol": symbol,
                "is_buy": "invalid",
                "limit_px": "100",
                "qty": "0.01",
                "time_in_force": TimeInForce.GTC,
                "reduce_only": False,
            },
        },
        {
            "name": "Missing qty",
            "params": {
                "symbol": symbol,
                "is_buy": True,
                "limit_px": "100",
                # qty is missing - should raise KeyError
                "time_in_force": TimeInForce.GTC,
                "reduce_only": False,
            },
        },
        {
            "name": "Invalid qty",
            "params": {
                "symbol": symbol,
                "is_buy": True,
                "limit_px": "100",
                "qty": "invalid",
                "time_in_force": TimeInForce.GTC,
                "reduce_only": False,
            },
        },
        {
            "name": "Zero qty",
            "params": {
                "symbol": symbol,
                "is_buy": True,
                "limit_px": "100",
                "qty": "0",
                "time_in_force": TimeInForce.GTC,
                "reduce_only": False,
            },
        },
        {
            "name": "Negative qty",
            "params": {
                "symbol": symbol,
                "is_buy": True,
                "limit_px": "100",
                "qty": "-0.01",
                "time_in_force": TimeInForce.GTC,
                "reduce_only": False,
            },
        },
        {
            "name": "Missing time_in_force",
            "params": {
                "symbol": symbol,
                "is_buy": True,
                "limit_px": "100",
                "qty": "0.01",
                # time_in_force is missing - should raise KeyError
                "reduce_only": False,
            },
        },
        # IOC-specific validation (reduceOnly IS sent for IOC orders)
        {
            "name": "Invalid reduce_only for IOC",
            "params": {
                "symbol": symbol,
                "is_buy": True,
                "limit_px": "100",
                "qty": "0.01",
                "time_in_force": TimeInForce.IOC,
                "reduce_only": "invalid",  # Invalid type - should fail validation
            },
        },
    ]

    for test_case in test_cases:
        logger.info(f"Testing: {test_case['name']}")
        await reya_tester.check_no_open_orders()
        await reya_tester.check_position_not_open(symbol)
        try:
            # Build params dict - use values from test case, no defaults for required fields
            params: dict = test_case["params"]  # type: ignore[assignment]
            order_params_test = LimitOrderParameters(
                symbol=params["symbol"],
                is_buy=params["is_buy"],
                limit_px=params["limit_px"],
                qty=params["qty"],
                time_in_force=params["time_in_force"],
                reduce_only=params.get("reduce_only"),
            )
            await reya_tester.create_limit_order(order_params_test)
            assert False, f"{test_case['name']} should have failed"
        except (KeyError, TypeError) as e:
            # Missing required field - this is expected for "Missing X" test cases
            logger.info(f"Pass: Expected error for {test_case['name']}: {type(e).__name__}: {e}")
        except Exception as e:
            if "should have failed" not in str(e):
                await reya_tester.check_no_open_orders()
                await reya_tester.check_position_not_open(symbol)
                logger.info(f"Pass: Expected error for {test_case['name']}: {e}")
            else:
                logger.error(f"Error not found for {test_case['name']}: {e}")
                raise e

    logger.info("input_validation test completed successfully")
    await reya_tester.close_active_orders(fail_if_none=False)


@pytest.mark.asyncio
async def test_success_gtc_with_order_and_cancel(reya_tester: ReyaTester):
    """1 GTC order, long, close right after creation"""
    symbol = "ETHRUSDPERP"

    # SETUP - capture sequence number BEFORE any actions
    last_sequence_before = await reya_tester.get_last_perp_execution_sequence_number()

    market_price = await reya_tester.get_current_price()
    logger.info(f"Market price: {market_price}")
    test_qty = 0.01

    order_params_buy = LimitOrderParameters(
        symbol=symbol,
        is_buy=True,
        limit_px=str(0),  # wide price
        qty=str(test_qty),
        time_in_force=TimeInForce.GTC,
    )

    await reya_tester.check_no_open_orders()
    await reya_tester.check_position_not_open(symbol)

    # Buy order slightly above market price to ensure it gets filled
    assert order_params_buy.limit_px is not None
    buy_order_id = await reya_tester.create_limit_order(order_params_buy)

    assert buy_order_id is not None

    # Wait for order creation to be confirmed via both REST and WebSocket
    await reya_tester.wait_for_order_creation(buy_order_id)
    expected_order = limit_order_params_to_order(order_params_buy, reya_tester.account_id)
    await reya_tester.check_open_order_created(buy_order_id, expected_order)
    await reya_tester.check_position_not_open(symbol)

    # cancel order
    await reya_tester.client.cancel_order(order_id=buy_order_id)

    # Note: this confirms trade has been registered, not neccesarely position
    cancelled_order_id = await reya_tester.wait_for_order_state(buy_order_id, OrderStatus.CANCELLED)
    assert cancelled_order_id == buy_order_id, "GTC order was not cancelled"

    await reya_tester.check_position_not_open(symbol)
    await reya_tester.check_no_order_execution_since(last_sequence_before)
    await reya_tester.check_no_open_orders()

    logger.info("GTC order cancel test completed successfully")


@pytest.mark.asyncio
async def test_success_gtc_orders_with_execution(reya_tester: ReyaTester):
    """Single GTC order filled against the pool (perp AMM)"""
    symbol = "ETHRUSDPERP"

    # Get current prices to determine order parameters
    market_price = await reya_tester.get_current_price()

    # For perp markets, GTC orders can fill against the pool (AMM)
    # Set limit price above market to ensure it crosses the spread and fills
    order_params_buy = LimitOrderParameters(
        symbol=symbol,
        is_buy=True,
        limit_px=str(float(market_price) * 1.01),  # 1% above market to cross spread
        qty=str(0.01),
        time_in_force=TimeInForce.GTC,
    )

    await reya_tester.check_no_open_orders()
    await reya_tester.check_position_not_open(symbol)

    # BUY
    assert order_params_buy.limit_px is not None
    buy_order_id = await reya_tester.create_limit_order(order_params_buy)
    logger.info(f"Created GTC BUY order with ID: {buy_order_id} at price {order_params_buy.limit_px}")

    await reya_tester.wait_for_order_state(buy_order_id, OrderStatus.FILLED)
    expected_order = limit_order_params_to_order(order_params_buy, reya_tester.account_id)
    execution = await reya_tester.wait_for_order_execution(expected_order)
    order_execution_details = await reya_tester.check_order_execution(execution, expected_order)
    await assert_position_changes(order_execution_details, reya_tester)

    logger.info("GTC market execution test completed successfully")
    await reya_tester.close_active_orders(fail_if_none=False)


@pytest.mark.asyncio
async def test_integration_gtc_with_market_execution(reya_tester: ReyaTester):
    """2 GTC orders, long and short, very close to market price and wait for execution"""
    symbol = "ETHRUSDPERP"

    # Get the last execution BEFORE creating orders to compare later
    last_execution_before = await reya_tester.get_last_wallet_perp_execution()
    last_sequence_before = last_execution_before.sequence_number if last_execution_before else 0
    logger.info(f"Last execution sequence before test: {last_sequence_before}")

    # Get current prices to determine order parameters
    market_price = await reya_tester.get_current_price()

    order_params_buy = LimitOrderParameters(
        symbol=symbol,
        is_buy=True,
        limit_px=str(float(market_price) * 0.999),
        qty=str(0.01),
        time_in_force=TimeInForce.GTC,
    )

    # BUY
    assert order_params_buy.limit_px is not None
    buy_order_id = await reya_tester.create_limit_order(order_params_buy)

    order_params_sell = LimitOrderParameters(
        symbol=symbol,
        is_buy=False,
        limit_px=str(float(market_price) * 1.0001),
        qty=str(order_params_buy.qty),
        time_in_force=TimeInForce.GTC,
    )

    assert order_params_sell.limit_px is not None
    sell_order_id = await reya_tester.create_limit_order(order_params_sell)

    assert buy_order_id is not None
    assert sell_order_id is not None

    # Wait for trade confirmation on either order (whichever fills first)
    # Check if there's a NEW execution (with higher sequence_number than before)
    order_execution_details = await reya_tester.get_last_wallet_perp_execution()
    logger.info(f"Last execution after orders: {order_execution_details}")

    # Only consider it filled if there's a NEW execution (sequence_number increased)
    is_new_execution = (
        order_execution_details is not None and order_execution_details.sequence_number > last_sequence_before
    )

    if is_new_execution:
        logger.info(
            f"Order was filled (new sequence: {order_execution_details.sequence_number} > {last_sequence_before})"
        )
        if order_execution_details.side == Side.B:
            await assert_position_changes(order_execution_details, reya_tester)
            expected_buy_order = limit_order_params_to_order(order_params_buy, reya_tester.account_id)
            execution = await reya_tester.wait_for_order_execution(expected_buy_order)
            await reya_tester.check_order_execution(execution, expected_buy_order)
        else:
            await assert_position_changes(order_execution_details, reya_tester)
            expected_sell_order = limit_order_params_to_order(order_params_sell, reya_tester.account_id)
            execution = await reya_tester.wait_for_order_execution(expected_sell_order)
            await reya_tester.check_order_execution(execution, expected_sell_order)

        await reya_tester.wait_for_order_state(buy_order_id, OrderStatus.CANCELLED)
        await reya_tester.wait_for_order_state(sell_order_id, OrderStatus.CANCELLED)
    else:
        logger.info("Order was not filled (no new execution)")
        await reya_tester.wait_for_order_creation(buy_order_id)
        await reya_tester.wait_for_order_creation(sell_order_id)
        expected_buy_order = limit_order_params_to_order(order_params_buy, reya_tester.account_id)
        expected_sell_order = limit_order_params_to_order(order_params_sell, reya_tester.account_id)
        await reya_tester.check_open_order_created(buy_order_id, expected_buy_order)
        await reya_tester.check_open_order_created(sell_order_id, expected_sell_order)

    logger.info("GTC market execution test completed successfully")
    await reya_tester.close_active_orders()


@pytest.mark.asyncio
async def test_failure_cancel_gtc_when_order_is_not_found(reya_tester: ReyaTester):
    """Test cancelling a non-existent order returns appropriate error"""
    await reya_tester.check_no_open_orders()

    try:
        await reya_tester.client.cancel_order(order_id="non_existent_order_id_12345")
        assert False, "Cancel should have failed for non-existent order"
    except BadRequestException as e:
        assert e.data is not None
        request_error: RequestError = e.data
        assert request_error.message is not None
        assert request_error.message.startswith(
            "Missing order with id non_existent_order_id_12345"
        ), f"Expected message to start with 'Missing order with id', got: {request_error.message}"
        assert request_error.error == RequestErrorCode.CANCEL_ORDER_OTHER_ERROR

    await reya_tester.check_no_open_orders()
    logger.info("✅ Cancel non-existent order test completed successfully")
