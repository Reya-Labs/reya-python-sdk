#!/usr/bin/env python3

import pytest

from sdk.open_api.models.order import Order
from sdk.open_api.models.order_type import OrderType
from sdk.open_api.models.perp_execution import PerpExecution
from sdk.open_api.models.position import Position
from sdk.open_api.models.side import Side
from sdk.reya_rest_api.constants.enums import Limit, LimitOrderType, TimeInForce
from sdk.tests.models import OrderDetails
from sdk.tests.reya_tester import ReyaTester, logger
from sdk.tests.utils import check_error_message


def assert_order_execution(order_details: OrderDetails, order_execution_details: PerpExecution):
    """Assert that order execution details are correct"""

    assert order_execution_details is not None, "Should have order execution details"
    assert order_details.symbol == order_execution_details.symbol, "Symbol should match"
    assert order_execution_details.qty == order_details.qty, "Executed base should match"
    assert (order_execution_details.side == "B") == order_details.is_buy, "Executed base should match"
    if order_details.is_buy:
        assert float(order_execution_details.price) <= float(
            order_details.price
        ), "Execution price should be lower than order price"
    else:
        assert float(order_execution_details.price) >= float(
            order_details.price
        ), "Execution price should be higher than order price"

    logger.info("✅ Order execution confirmed correctly")


def assert_position_changes(
    positions_before: Position | None, positions_after: Position, execution_details: PerpExecution
):
    """Assert that positions have changed as expected"""

    if positions_before is None:
        positions_before = Position(
            exchangeId=execution_details.exchange_id,
            symbol=execution_details.symbol,
            accountId=execution_details.account_id,
            qty="0",
            side=Side.B,
            avgEntryPrice="0",
            avgEntryFundingValue="0",
            lastTradeSequenceNumber=int(execution_details.sequence_number) - 1,
        )

    assert positions_after is not None, "Should have positions after"
    assert positions_before.symbol == positions_after.symbol, "Market ID should match"

    expected_qty = float(positions_before.qty) + float(execution_details.qty)
    assert float(positions_after.qty) == pytest.approx(
        expected_qty, rel=1e-6
    ), "Position qty should have changed correctly"

    assert (
        positions_before.last_trade_sequence_number < positions_after.last_trade_sequence_number
    ), "Event sequence number should have changed"
    assert (
        execution_details.sequence_number == positions_after.last_trade_sequence_number
    ), "Trade should match the latest position sequence"

    logger.info(f"Position before: {positions_before}")
    logger.info(f"Position after: {positions_after}")

    average_entry_price = float(positions_before.avg_entry_price)
    if float(positions_before.qty) == 0 or (execution_details.side == positions_before.side):
        average_entry_price = (
            float(positions_before.avg_entry_price) * float(positions_before.qty)
            + float(execution_details.qty) * float(execution_details.price)
        ) / float(positions_after.qty)

    assert average_entry_price == float(positions_after.avg_entry_price), "Average entry price does not match"
    logger.info("✅ New position recorded correctly")


"""
Create WS listner that records "what we expect" and check what is received is ONLY what we expect
"""


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
    # This test requires WebSocket for trade confirmation
    await reya_tester.setup_websocket()
    symbol = "ETHRUSDPERP"

    try:
        # Get current prices to determine order parameters
        market_price = reya_tester.get_current_price()
        logger.info(f"Market price: {market_price}")

        # Get positions before order
        position_before = reya_tester.get_position(symbol=symbol)
        logger.info(f"Position before: {position_before}")
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
        )

        # Validate
        persisted_trade_sequence_number = await reya_tester.wait_for_trade_confirmation_via_rest(
            order_details, int(position_before.last_trade_sequence_number) + 1
        )
        assert persisted_trade_sequence_number is not None, "Order creation should succeed"

        order_execution_details = reya_tester.get_wallet_perp_execution(persisted_trade_sequence_number)
        assert (
            persisted_trade_sequence_number == order_execution_details.sequence_number
        ), "Sequence number should match"
        assert_order_execution(order_details, order_execution_details)

        position_after = reya_tester.get_position(symbol)
        assert position_after is not None, "Position after should not be None"
        assert_position_changes(position_before, position_after, order_execution_details)

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

        # Get positions before order
        position_before = reya_tester.get_position(test_symbol)

        # Execute
        reya_tester.create_order(
            symbol=test_symbol,
            is_buy=order_details.is_buy,
            price=order_details.price,
            qty=order_details.qty,
            time_in_force=TimeInForce.IOC,
        )

        # Validate
        # TODO: Claudiu - listen to WS for trade confirmation and validate
        persisted_trade_sequence_number = await reya_tester.wait_for_trade_confirmation_via_rest(
            order_details, int(position_before.last_trade_sequence_number) + 1
        )
        assert persisted_trade_sequence_number is not None, "Order creation should succeed"

        order_execution_details = reya_tester.get_wallet_perp_execution(persisted_trade_sequence_number)
        assert (
            persisted_trade_sequence_number == order_execution_details.sequence_number
        ), "Sequence number should match"
        assert_order_execution(order_details, order_execution_details)

        position_after = reya_tester.get_position(test_symbol)
        assert position_after is not None, "Position after should not be None"
        assert_position_changes(position_before, position_after, order_execution_details)

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

        await reya_tester.close_exposure(symbol, fail_if_none=False)

        # Create a reduce-only order, which should fail since we have no position to reduce
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
            check_error_message(e, ["ReduceOnlyConditionFailed"])
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
                expect_error=True,
            )
            # If we get here, the test failed - order should not have been accepted
            assert False, f"Order should not have been accepted with invalid price {invalid_price} for buy order"
        except Exception as e:
            check_error_message(e, ["UnacceptableOrderPrice"])
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
            try:
                reya_tester.create_order(**test_case["params"], expect_error=True)
                assert False, f"{test_case['name']} should have failed"
            except Exception as e:
                if "should have failed" not in str(e):
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

        order_details_buy = OrderDetails(
            account_id=reya_tester.account_id,
            order_type=OrderType.LIMIT,
            symbol=symbol,
            is_buy=True,
            price=str(0),  # wide price
            qty=str(test_qty),
        )

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

        active_buy_order: Order = reya_tester.get_open_order(buy_order_id)
        assert active_buy_order is not None, "GTC order was not found"
        assert active_buy_order.order_id == buy_order_id
        assert float(active_buy_order.limit_px) == float(order_details_buy.price), "GTC order price does not match"
        assert float(active_buy_order.qty) == float(order_details_buy.qty), "GTC order qty does not match"
        assert active_buy_order.order_type == order_details_buy.order_type, "GTC order type does not match"
        assert (active_buy_order.side == Side.B) == order_details_buy.is_buy, "GTC order direction does not match"

        # cancel order
        reya_tester.client.cancel_order(order_id=active_buy_order.order_id)

        # Note: this confirms trade has been registered, not neccesarely position
        cancelled_order_id = await reya_tester.wait_for_order_cancellation_via_rest(order_id=active_buy_order.order_id)
        assert cancelled_order_id == active_buy_order.order_id, "GTC order was not cancelled"

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
async def test_success_gtc_orders_market_execution(reya_tester: ReyaTester):
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

        position_before = reya_tester.get_position(symbol)
        assert float(position_before.qty) == float(order_details_buy.qty), "Position was not created"

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
async def test_integration_gtc_tight_orders_market_execution(reya_tester: ReyaTester):
    """2 GTC orders, long and short, very close to market price and wait for execution"""
    await reya_tester.setup_websocket()
    symbol = "ETHRUSDPERP"

    try:
        # Get current prices to determine order parameters
        market_price = reya_tester.get_current_price()
        position_before = reya_tester.get_position(symbol)

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
            order_type=order_details_buy.order_type,
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
            order_type=order_details_sell.order_type,
        )

        # Wait for trade confirmation on either order (whichever fills first)
        seq_increment = 1
        confirmed_transaction_hash_buy = await reya_tester.wait_for_trade_confirmation_via_rest(
            order_details=order_details_buy,
            sequence_number=int(position_before.last_trade_sequence_number) + seq_increment,
        )
        if confirmed_transaction_hash_buy is not None:
            logger.info("Buy order was filled")
            seq_increment += 1
        else:
            active_buy_order = reya_tester.get_open_order(buy_order_id)
            if active_buy_order is not None:
                logger.info("Buy order was not filled")
            else:
                logger.info("Buy order was not found")

        # TODO: Claudiu - listen to WS for trade confirmation and validate
        confirmed_transaction_hash_sell = await reya_tester.wait_for_trade_confirmation_via_rest(
            order_details=order_details_sell,
            sequence_number=int(position_before.last_trade_sequence_number) + seq_increment,
        )
        if confirmed_transaction_hash_sell is not None:
            logger.info("Sell order was filled")
        else:
            active_sell_order = reya_tester.get_open_order(sell_order_id)
            if active_sell_order is not None:
                logger.info("Sell order was not filled")
            else:
                logger.info("Sell order was not found")

        logger.info("GTC market execution test completed successfully")
    except Exception as e:
        logger.error(f"Error in test_gtc_orders_market_execution: {e}")
        raise
    finally:
        if reya_tester.websocket:
            reya_tester.websocket.close()
        await reya_tester.close_exposure(symbol, fail_if_none=False)
        await reya_tester.close_active_orders()
