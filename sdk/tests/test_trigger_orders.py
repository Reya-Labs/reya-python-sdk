#!/usr/bin/env python3

import asyncio
import time

import pytest

from sdk.open_api.models.order_type import OrderType
from sdk.open_api.models.perp_execution import PerpExecution
from sdk.open_api.models.position import Position
from sdk.open_api.models.side import Side
from sdk.reya_rest_api.constants.enums import Limit, LimitOrderType, TimeInForce
from sdk.tests.models import OrderDetails
from sdk.tests.reya_tester import ReyaTester, logger
from sdk.tests.utils import check_error_message


def assert_position_changes(
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
    reya_tester.check_position(
        symbol=execution_details.symbol,
        expected_exchange_id=execution_details.exchange_id,
        expected_account_id=execution_details.account_id,
        expected_qty=execution_details.qty,
        expected_side=execution_details.side,
        expected_avg_entry_price=expected_average_entry_price,
        expected_last_trade_sequence_number=int(execution_details.sequence_number),
    )
    logger.info("✅ New position recorded correctly")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "test_qty, test_is_buy",
    [
        (0.01, True),
        # (0.01, False),
    ],
)
async def test_success_ioc(reya_tester: ReyaTester, test_qty, test_is_buy):
    """Test creating an order and confirming execution"""
    # This test requires WebSocket for trade confirmation
    await reya_tester.setup_websocket()
    symbol = "ETHRUSDPERP"

    try:
        # Get current prices to determine order parameters
        market_price = reya_tester.get_current_price()
        logger.info(f"Market price: {market_price}")

        # Get positions before order
        reya_tester.check_position_not_open(symbol)
        reya_tester.check_no_open_orders()

        price_with_offset = market_price * 1.1 if test_is_buy else market_price * 0.9
        order_details = OrderDetails(
            order_type=OrderType.LIMIT,
            symbol=symbol,
            is_buy=test_is_buy,
            price=str(price_with_offset),
            qty=str(test_qty),
            account_id=reya_tester.account_id,
        )

        logger.info("Trade confirmation task")

        # Execute
        reya_tester.create_order(
            symbol=symbol,
            is_buy=order_details.is_buy,
            price=order_details.price,
            qty=order_details.qty,
            time_in_force=TimeInForce.IOC,
            reduce_only=False,
        )

        # Validate
        reya_tester.check_no_open_orders()
        order_execution_details = reya_tester.check_order_execution(order_details)

        assert_position_changes(order_execution_details, reya_tester)

        logger.info("Order execution test complete")
    except Exception as e:
        logger.error(f"Error in test: {e}")
        raise
    finally:
        if reya_tester.websocket:
            reya_tester.websocket.close()
        await reya_tester.close_exposure(symbol)


@pytest.mark.asyncio
@pytest.mark.parametrize("test_is_buy, test_symbol", [(True, "ETHRUSDPERP")])
async def test_success_ioc_with_any_market(reya_tester: ReyaTester, test_is_buy, test_symbol):
    """Test creating an order and confirming execution"""
    # This test requires WebSocket for trade confirmation
    await reya_tester.setup_websocket()

    try:
        # Get current prices to determine order parameters
        market_price = reya_tester.get_current_price(symbol=test_symbol)
        reya_tester.check_no_open_orders()

        market_config = reya_tester.get_market_definition(symbol=test_symbol)
        test_qty = market_config.min_order_qty if test_is_buy else -market_config.min_order_qty
        price_with_offset = market_price * 1.1 if test_is_buy else market_price * 0.9
        order_details = OrderDetails(
            order_type=OrderType.LIMIT,
            symbol=test_symbol,
            is_buy=test_is_buy,
            price=str(price_with_offset),
            qty=str(test_qty),
            account_id=reya_tester.account_id,
        )

        # Execute
        reya_tester.create_order(
            symbol=test_symbol,
            is_buy=order_details.is_buy,
            price=order_details.price,
            qty=order_details.qty,
            time_in_force=TimeInForce.IOC,
            reduce_only=False,
        )

        # Validate
        # TODO: Claudiu - listen to WS for trade confirmation and validate

        reya_tester.check_no_open_orders()
        order_execution_details = reya_tester.check_order_execution(order_details)
        assert_position_changes(order_execution_details, reya_tester)

        logger.info("Order execution test complete")
    except Exception as e:
        logger.error(f"Error in test_ioc_long: {e}")
        raise
    finally:
        if reya_tester.websocket:
            reya_tester.websocket.close()
        await reya_tester.close_exposure(test_symbol)


@pytest.mark.asyncio
async def test_failure_ioc_with_reduce_only_on_empty_position(reya_tester: ReyaTester):
    """Test 1: Try IOC with reduce_only flag but the position is actually expanding (should error)"""
    await reya_tester.setup_websocket()
    symbol = "ETHRUSDPERP"

    try:
        # SETUP
        market_price = reya_tester.get_current_price()
        logger.info(f"Market price: {market_price}")

        test_qty = 0.01
        test_is_buy = True
        price_with_offset = market_price * 1.1 if test_is_buy else market_price * 0.9

        reya_tester.check_no_open_orders()
        reya_tester.check_position_not_open(symbol)

        try:
            reya_tester.create_order(
                symbol=symbol,
                is_buy=test_is_buy,
                price=str(price_with_offset),
                qty=str(test_qty),
                time_in_force=TimeInForce.IOC,
                reduce_only=True,
                expect_error=True,
            )
            # If we get here, the test failed - order should not have been accepted
            assert False, "Order should not have been accepted with reduce_only flag on no position"
        except Exception as e:
            check_error_message(e, ["validation error"])
            # no error code given
            # check_error_code(e, "400")

            reya_tester.check_no_open_orders()
            reya_tester.check_position_not_open(symbol)
            logger.info("✅ Error correctly mentions position reduction issue")
            pass  # Expected error
    except Exception as e:
        logger.error(f"Error in test_reduce_only_on_empty_position: {e}")
        raise
    finally:
        if reya_tester.websocket:
            reya_tester.websocket.close()
        await reya_tester.close_exposure(symbol, False)


@pytest.mark.asyncio
async def test_failure_ioc_with_invalid_limit_price(reya_tester: ReyaTester):
    """Try IOC with limit price on the opposite side of the market, should revert"""
    await reya_tester.setup_websocket()
    symbol = "ETHRUSDPERP"

    try:
        # SETUP
        market_price = reya_tester.get_current_price()
        logger.info(f"Market price: {market_price}")

        reya_tester.check_no_open_orders()
        reya_tester.check_position_not_open(symbol)

        test_qty = 0.01
        test_is_buy = True
        invalid_price = market_price * 0.9  # Price below market for buy order (should be rejected)
        try:
            reya_tester.create_order(
                symbol=symbol,
                is_buy=test_is_buy,
                price=str(invalid_price),
                qty=str(test_qty),
                time_in_force=TimeInForce.IOC,
                reduce_only=False,
                expect_error=True,
            )
            # If we get here, the test failed - order should not have been accepted
            assert False, f"Order should not have been accepted with invalid price {invalid_price} for buy order"
        except Exception as e:
            check_error_message(e, ["validation error"])
            # no error code given
            # check_error_code(e, "500")

            reya_tester.check_no_open_orders()
            reya_tester.check_position_not_open(symbol)
            logger.info("✅ Error correctly mentions unacceptable order price issue")
            pass  # Expected error
    except Exception as e:
        logger.error(f"Error in test_invalid_limit_price: {e}")
        raise
    finally:
        if reya_tester.websocket:
            reya_tester.websocket.close()
        await reya_tester.close_exposure(symbol, fail_if_none=False)


@pytest.mark.asyncio
async def test_failure_ioc_with_input_validation(reya_tester: ReyaTester):
    """Try various invalid inputs"""
    await reya_tester.setup_websocket()
    symbol = "ETHRUSDPERP"

    try:
        test_cases = [
            {
                "name": "Invalid symbol",
                "params": {
                    "symbol": 100000,
                    "is_buy": True,
                    "price": "100",
                    "qty": "0.01",
                    "order_type": LimitOrderType(limit=Limit(time_in_force=TimeInForce.IOC)),
                    "reduce_only": False,
                },
            },
            {
                "name": "Wrong symbol",
                "params": {
                    "symbol": "wrong",
                    "is_buy": True,
                    "price": "100",
                    "qty": "0.01",
                    "order_type": LimitOrderType(limit=Limit(time_in_force=TimeInForce.IOC)),
                    "reduce_only": False,
                },
            },
            {
                "name": "Missing symbol",
                "params": {
                    "is_buy": True,
                    "price": "100",
                    "qty": "0.01",
                    "order_type": LimitOrderType(limit=Limit(time_in_force=TimeInForce.IOC)),
                    "reduce_only": False,
                },
            },
            {
                "name": "Missing is_buy",
                "params": {
                    "symbol": symbol,
                    "price": "100",
                    "qty": "0.01",
                    "order_type": LimitOrderType(limit=Limit(time_in_force=TimeInForce.IOC)),
                    "reduce_only": False,
                },
            },
            {
                "name": "Invalid is_buy",
                "params": {
                    "symbol": symbol,
                    "is_buy": "invalid",
                    "price": "100",
                    "qty": "0.01",
                    "order_type": LimitOrderType(limit=Limit(time_in_force=TimeInForce.IOC)),
                    "reduce_only": False,
                },
            },
            {
                "name": "Missing qty",
                "params": {
                    "symbol": symbol,
                    "is_buy": True,
                    "price": "100",
                    "order_type": LimitOrderType(limit=Limit(time_in_force=TimeInForce.IOC)),
                    "reduce_only": False,
                },
            },
            {
                "name": "Invalid qty",
                "params": {
                    "symbol": symbol,
                    "is_buy": True,
                    "price": "100",
                    "qty": "invalid",
                    "order_type": LimitOrderType(limit=Limit(time_in_force=TimeInForce.IOC)),
                    "reduce_only": False,
                },
            },
            {
                "name": "Zero qty",
                "params": {
                    "symbol": symbol,
                    "is_buy": True,
                    "price": "100",
                    "qty": "0",
                    "order_type": LimitOrderType(limit=Limit(time_in_force=TimeInForce.IOC)),
                    "reduce_only": False,
                },
            },
            {
                "name": "Negative qty",
                "params": {
                    "symbol": symbol,
                    "is_buy": True,
                    "price": "100",
                    "qty": "-0.01",
                    "order_type": LimitOrderType(limit=Limit(time_in_force=TimeInForce.IOC)),
                    "reduce_only": False,
                },
            },
            {
                "name": "Missing Order type",
                "params": {
                    "symbol": symbol,
                    "is_buy": True,
                    "price": "100",
                    "qty": "0.01",
                    "reduce_only": False,
                },
            },
            {
                "name": "Invalid Order type",
                "params": {
                    "symbol": symbol,
                    "is_buy": True,
                    "price": "100",
                    "qty": "0.01",
                    "order_type": "invalid",
                    "reduce_only": False,
                },
            },
            {
                "name": "Missing reduce_only",
                "params": {
                    "symbol": symbol,
                    "is_buy": True,
                    "price": "100",
                    "qty": "0.01",
                    "order_type": LimitOrderType(limit=Limit(time_in_force=TimeInForce.IOC)),
                },
            },
            {
                "name": "Invalid reduce_only",
                "params": {
                    "symbol": symbol,
                    "is_buy": True,
                    "price": "100",
                    "qty": "0.01",
                    "order_type": LimitOrderType(limit=Limit(time_in_force=TimeInForce.IOC)),
                    "reduce_only": "invalid",
                },
            },
            # GTC
            {
                "name": "Invalid expires_after for GTC",
                "params": {
                    "symbol": symbol,
                    "is_buy": True,
                    "price": "100",
                    "qty": "0.01",
                    "order_type": LimitOrderType(limit=Limit(time_in_force=TimeInForce.GTC)),
                    "reduce_only": False,
                    "expires_after": 1000,
                },
            },
            {
                "name": "Invalid reduce_only for GTC",
                "params": {
                    "symbol": symbol,
                    "is_buy": True,
                    "price": "100",
                    "qty": "0.01",
                    "order_type": LimitOrderType(limit=Limit(time_in_force=TimeInForce.GTC)),
                    "reduce_only": True,
                },
            },
        ]

        for test_case in test_cases:
            logger.info(f"Testing: {test_case['name']}")
            reya_tester.check_no_open_orders()
            reya_tester.check_position_not_open(symbol)
            try:
                reya_tester.create_order(**test_case["params"], expect_error=True)
                assert False, f"{test_case['name']} should have failed"
            except Exception as e:
                if "should have failed" not in str(e):
                    reya_tester.check_no_open_orders()
                    reya_tester.check_position_not_open(symbol)
                    logger.info(f"Pass: Expected error for {test_case['name']}: {e}")
                    pass  # Expected error
                else:
                    logger.error(f"Error not found for {test_case["name"]}: {e}")
                    raise e

        logger.info("input_validation test completed successfully")
    except Exception as e:
        logger.error(f"Error in test_input_validation: {e}")
        raise
    finally:
        if reya_tester.websocket:
            reya_tester.websocket.close()
        await reya_tester.close_exposure(symbol, fail_if_none=False)
        await reya_tester.close_active_orders(fail_if_none=False)


@pytest.mark.asyncio
async def test_success_gtc_with_order_and_cancel(reya_tester: ReyaTester):
    """1 GTC order, long, close right after creation"""
    await reya_tester.setup_websocket()
    symbol = "ETHRUSDPERP"

    try:
        # SETUP
        market_price = reya_tester.get_current_price()
        logger.info(f"Market price: {market_price}")
        test_qty = 0.01
        start_timestamp = int(time.time() * 1000)

        order_details_buy = OrderDetails(
            account_id=reya_tester.account_id,
            order_type=OrderType.LIMIT,
            symbol=symbol,
            is_buy=True,
            price=str(0),  # wide price
            qty=str(test_qty),
        )

        reya_tester.check_no_open_orders()
        reya_tester.check_position_not_open(symbol)

        # Buy order slightly above market price to ensure it gets filled
        buy_order_id = reya_tester.create_order(
            symbol=order_details_buy.symbol,
            is_buy=order_details_buy.is_buy,
            price=order_details_buy.price,
            qty=order_details_buy.qty,
            time_in_force=TimeInForce.GTC,
        )

        # Wait for trade confirmation on either order (whichever fills first)
        # TODO: Claudiu - listen to WS for NO trade confirmation

        reya_tester.check_open_order_created(buy_order_id, order_details_buy)
        reya_tester.check_position_not_open(symbol)

        # cancel order
        reya_tester.client.cancel_order(order_id=buy_order_id)

        # Note: this confirms trade has been registered, not neccesarely position
        cancelled_order_id = await reya_tester.wait_for_order_cancellation_via_rest(order_id=buy_order_id)
        assert cancelled_order_id == buy_order_id, "GTC order was not cancelled"

        reya_tester.check_position_not_open(symbol)
        reya_tester.check_no_order_execution_since(start_timestamp)
        reya_tester.check_no_open_orders()

        logger.info("GTC order cancel test completed successfully")
    except Exception as e:
        logger.error(f"Error in test: {e}")
        raise
    finally:
        logger.info("----------- Closing position and active orders -----------")
        if reya_tester.websocket:
            reya_tester.websocket.close()
        await reya_tester.close_exposure(symbol, fail_if_none=False)
        await reya_tester.close_active_orders(fail_if_none=False)


@pytest.mark.asyncio
async def test_success_gtc_orders_with_execution(reya_tester: ReyaTester):
    """2 GTC orders, long and short, very close to market price and wait for execution"""
    await reya_tester.setup_websocket()
    symbol = "ETHRUSDPERP"

    try:
        # Get current prices to determine order parameters
        market_price = reya_tester.get_current_price()

        order_details_buy = OrderDetails(
            symbol=symbol,
            is_buy=True,
            price=str(market_price * 1.0001),
            qty=str(0.01),
            order_type=OrderType.LIMIT,
            account_id=reya_tester.account_id,
        )

        reya_tester.check_no_open_orders()
        reya_tester.check_position_not_open(symbol)

        # BUY
        buy_order_id = reya_tester.create_order(
            symbol=order_details_buy.symbol,
            is_buy=order_details_buy.is_buy,
            price=order_details_buy.price,
            qty=order_details_buy.qty,
            time_in_force=TimeInForce.GTC,
        )
        logger.info(f"Created GTC BUY order with ID: {buy_order_id} at price {order_details_buy.price}")

        # VALIDATE
        # TODO: Claudiu - listen to WS for trade confirmation and validate

        # wait 5 seconds for the order to propagate (skip when WS is implemented)
        await asyncio.sleep(5)
        reya_tester.check_no_open_orders()
        order_execution_details = reya_tester.check_order_execution(order_details_buy)
        assert_position_changes(order_execution_details, reya_tester)

        logger.info("GTC market execution test completed successfully")
    except Exception as e:
        logger.error(f"Error in test_gtc_orders_market_execution: {e}")
        raise
    finally:
        if reya_tester.websocket:
            reya_tester.websocket.close()
        await reya_tester.close_exposure(symbol, fail_if_none=False)
        await reya_tester.close_active_orders(fail_if_none=False)


@pytest.mark.asyncio
async def test_integration_gtc_with_market_execution(reya_tester: ReyaTester):
    """2 GTC orders, long and short, very close to market price and wait for execution"""
    await reya_tester.setup_websocket()
    symbol = "ETHRUSDPERP"

    try:
        # Get current prices to determine order parameters
        start_timestamp = int(time.time() * 1000)
        market_price = reya_tester.get_current_price()

        order_details_buy = OrderDetails(
            symbol=symbol,
            is_buy=True,
            price=str(market_price * 0.999),
            qty=str(0.01),
            order_type=OrderType.LIMIT,
            account_id=reya_tester.account_id,
        )

        # BUY
        buy_order_id = reya_tester.create_order(
            symbol=order_details_buy.symbol,
            is_buy=order_details_buy.is_buy,
            price=order_details_buy.price,
            qty=order_details_buy.qty,
            time_in_force=TimeInForce.GTC,
        )
        order_details_sell = OrderDetails(
            symbol=symbol,
            is_buy=False,
            price=str(market_price * 1.0001),
            qty=str(order_details_buy.qty),
            order_type=OrderType.LIMIT,
            account_id=reya_tester.account_id,
        )
        sell_order_id = reya_tester.create_order(
            symbol=order_details_sell.symbol,
            is_buy=order_details_sell.is_buy,
            price=order_details_sell.price,
            qty=order_details_sell.qty,
            time_in_force=TimeInForce.GTC,
        )

        # Wait for trade confirmation on either order (whichever fills first)
        order_execution_details = reya_tester.get_last_wallet_perp_execution()
        logger.info(f"Last execution sequence number: {order_execution_details}")

        if order_execution_details is not None and order_execution_details.timestamp > start_timestamp:
            logger.info("Order was filled")
            if order_execution_details.side == Side.B:
                assert_position_changes(order_execution_details, reya_tester)
                reya_tester.check_order_execution(order_details_buy)
            else:
                assert_position_changes(order_execution_details, reya_tester)
                reya_tester.check_order_execution(order_details_sell)
            reya_tester.check_order_not_open(buy_order_id)
            reya_tester.check_order_not_open(sell_order_id)
        else:
            logger.info("Order was not filled")
            reya_tester.check_open_order_created(buy_order_id, order_details_buy)
            reya_tester.check_open_order_created(sell_order_id, order_details_sell)

        logger.info("GTC market execution test completed successfully")
    except Exception as e:
        logger.error(f"Error in test_gtc_orders_market_execution: {e}")
        raise
    finally:
        if reya_tester.websocket:
            reya_tester.websocket.close()
        await reya_tester.close_exposure(symbol, fail_if_none=False)
        await reya_tester.close_active_orders()


@pytest.mark.asyncio
async def test_failure_cancel_gtc_when_order_is_not_found(reya_tester: ReyaTester):
    return
