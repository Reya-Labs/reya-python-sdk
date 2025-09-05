#!/usr/bin/env python3

import asyncio
import pytest

from sdk.open_api.models.create_order_response import CreateOrderResponse
from sdk.open_api.models.order import Order
from sdk.open_api.models.order_status import OrderStatus
from sdk.open_api.models.order_type import OrderType
from sdk.open_api.models.perp_execution import PerpExecution
from sdk.open_api.models.position import Position
from sdk.open_api.models.side import Side
from sdk.tests.models import OrderDetails
from sdk.tests.reya_tester import ReyaTester, logger


def assert_tp_sl_order_submission(order_details: Order, expected_order_details: OrderDetails, position: Position):
    """Assert that order execution details are correct"""

    assert order_details is not None, "Should have order execution details"
    assert order_details.symbol == expected_order_details.symbol, "Market ID should match"
    # TODO: uncomment after fix, trigger_px has 18 decimals
    # assert float(order_details.trigger_px) == pytest.approx(
    #     float(expected_order_details.price), rel=1e-6
    # ), "Trigger price should match"
    assert (order_details.side == Side.B) == expected_order_details.is_buy, "Executed base should match"
    assert float(position.qty) == float(expected_order_details.qty), "Order direction does not match"
    assert order_details.status == OrderStatus.PENDING, "Order status should be PENDING"

    logger.info("✅ Order submission confirmed correctly")


async def create_ioc_order(reya_tester: ReyaTester, symbol: str, price_with_offset: float, is_buy: bool, qty: str):
    order_details = OrderDetails(
        account_id=reya_tester.account_id,
        symbol=symbol,
        is_buy=is_buy,
        price=str(price_with_offset),
        qty=qty,
        order_type=OrderType.LIMIT,
    )

    # TODO: Claudiu - listen to WS for trade confirmation

    # Execute
    reya_tester.create_order(
        symbol=symbol, is_buy=order_details.is_buy, price=order_details.price, qty=order_details.qty, reduce_only=False
    )

    # Validate
    # TODO: Claudiu - validate WS trade confirmation


@pytest.mark.asyncio
async def test_success_tp_order_create_cancel(reya_tester: ReyaTester):
    """TP order, close right after creation"""
    await reya_tester.setup_websocket()
    symbol = "ETHRUSDPERP"

    try:
        # SETUP
        market_price = reya_tester.get_current_price(symbol)
        tp_order_details = OrderDetails(
            account_id=reya_tester.account_id,
            order_type=OrderType.TP,
            symbol=symbol,
            is_buy=False,  # on long
            price=str(market_price * 2),  # lower than IOC limit price
            qty="0.01",
        )
        await create_ioc_order(
            reya_tester,
            tp_order_details.symbol,
            market_price * 1.01,
            not tp_order_details.is_buy,
            qty=tp_order_details.qty,
        )
        position_before = reya_tester.get_position(tp_order_details.symbol)

        # SUBMIT TP
        tp_order: CreateOrderResponse | None = reya_tester.create_tp_order(
            symbol=tp_order_details.symbol,
            is_buy=tp_order_details.is_buy,
            trigger_price=tp_order_details.price,
        )
        assert tp_order is not None, "TP order creation should succeed"
        logger.info(f"Created TP order with ID: {tp_order.order_id}")

        # VALIDATE order was registered correctly
        # TODO: Claudiu - listen to WS order status update and validate

        active_tp_order = await reya_tester.wait_for_order_creation_via_rest(order_id=tp_order.order_id)
        assert_tp_sl_order_submission(active_tp_order, tp_order_details, position_before)

        # CANCEL order
        reya_tester.client.cancel_order(order_id=active_tp_order.order_id)
        cancelled_order_id = await reya_tester.wait_for_order_cancellation_via_rest(order_id=active_tp_order.order_id)
        assert cancelled_order_id == active_tp_order.order_id, "TP order was not cancelled"

        logger.info("✅ ✅ ✅ TP order cancel test completed successfully")

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
async def test_success_sl_order_create_cancel(reya_tester: ReyaTester):
    """1 SL order, close right after creation"""
    await reya_tester.setup_websocket()
    symbol = "ETHRUSDPERP"

    try:
        # SETUP
        market_price = reya_tester.get_current_price(symbol)
        sl_order_details = OrderDetails(
            symbol=symbol,
            is_buy=False,  # on long
            price=str(market_price * 0.9),  # higher than IOC limit price
            account_id=reya_tester.account_id,
            order_type=OrderType.SL,
            qty="0.01",
        )
        await create_ioc_order(
            reya_tester, sl_order_details.symbol, market_price * 1.1, not sl_order_details.is_buy, qty="0.01"
        )
        position_before = reya_tester.get_position(sl_order_details.symbol)

        # SUBMIT SL
        order_response = reya_tester.create_sl_order(
            symbol=sl_order_details.symbol,
            is_buy=sl_order_details.is_buy,
            trigger_price=sl_order_details.price,
        )
        logger.info(f"Created SL order with ID: {order_response.order_id}")

        # VALIDATE order was created correctly
        # TODO: Claudiu - listen to WS order status update and validate

        active_sl_order: Order = await reya_tester.wait_for_order_creation_via_rest(
            order_id=order_response.order_id, timeout=10
        )
        assert_tp_sl_order_submission(active_sl_order, sl_order_details, position_before)

        # CANCEL
        reya_tester.client.cancel_order(order_id=active_sl_order.order_id)
        cancelled_order_id = await reya_tester.wait_for_order_cancellation_via_rest(order_id=active_sl_order.order_id)
        assert cancelled_order_id == active_sl_order.order_id, "SL order was not cancelled"
        logger.info("SL order cancel test completed successfully")

    except Exception as e:
        logger.error(f"Error in test: {e}")
        raise
    finally:
        logger.info("----------- Closing position and active orders -----------")
        if reya_tester.websocket:
            reya_tester.websocket.close()
        await reya_tester.close_exposure(symbol, fail_if_none=False)
        await reya_tester.close_active_orders(fail_if_none=False)


# Note: This is an integration test, dependent on order price
@pytest.mark.skip
@pytest.mark.asyncio
async def test_success_sltp_when_tight_execution(reya_tester: ReyaTester):
    """SLTP order triggered"""
    await reya_tester.setup_websocket()
    symbol = "ETHRUSDPERP"

    try:
        # SETUP
        market_price = reya_tester.get_current_price()
        qty = "0.01"

        tp_order_details = OrderDetails(
            symbol=symbol,
            is_buy=True,  # on short position
            trigger_price=str(market_price * 1.1),
            qty=qty,
            account_id=reya_tester.account_id,
            order_type=OrderType.TP,
        )
        sl_order_details = OrderDetails(
            symbol=symbol,
            is_buy=tp_order_details.is_buy,
            trigger_price=str(market_price * 0.9),
            qty=qty,
            account_id=reya_tester.account_id,
            order_type=OrderType.SL,
        )
        # create short IOC

        await create_ioc_order(
            reya_tester,
            tp_order_details.symbol,
            str(market_price * 0.999),
            not tp_order_details.is_buy,
            qty=tp_order_details.qty,
        )
        position_before: Position = reya_tester.get_position(symbol)
        assert float(position_before.qty) == float(tp_order_details.qty), "Position was not created"

        # SUBMIT TP
        tp_order: CreateOrderResponse = reya_tester.create_tp_order(
            symbol=symbol,
            is_buy=tp_order_details.is_buy,
            trigger_price=tp_order_details.price,
        )
        logger.info(f"Created TP order with ID: {tp_order.order_id}")

        # SUBMIT SL
        sl_order: CreateOrderResponse = reya_tester.create_sl_order(
            symbol=symbol,
            is_buy=sl_order_details.is_buy,
            trigger_price=sl_order_details.price,
        )
        logger.info(f"Created SL order with ID: {sl_order.order_id}")

        # VALIDATE order was executed correctly
        # TODO: Claudiu - listen to WS for trade confirmation
        confirmed_sequence_number = None  # from WS

        if confirmed_sequence_number is not None:
            #  EITHER order was executed
            logger.info("👌 SLTP order executed")
            order_execution_details: PerpExecution = reya_tester.get_wallet_perp_execution(confirmed_sequence_number)
            assert float(order_execution_details.price) < float(tp_order_details.price) or float(
                order_execution_details.price
            ) > float(sl_order_details.price), "SLTP execution price out of bounds"

            position_after: Position = reya_tester.get_position(symbol)
            assert position_after.qty == 0, "Position was not closed"
        else:
            # OR neither order was executed
            logger.info("😒 SLTP order not executed")
            await reya_tester.close_active_orders(fail_if_none=True)

    except Exception as e:
        logger.error(f"Error in test: {e}")
        raise
    finally:
        logger.info("----------- Closing position and active orders -----------")
        if reya_tester.websocket:
            reya_tester.websocket.close()
        await reya_tester.close_exposure(symbol, fail_if_none=False)
        await reya_tester.close_active_orders(fail_if_none=False)


# Note: the CO bot may not be active on testnet
@pytest.mark.asyncio
async def test_success_tp_wide_when_executed(reya_tester: ReyaTester):
    """SL order triggered"""
    await reya_tester.setup_websocket()
    symbol = "ETHRUSDPERP"

    try:
        # SETUP
        market_price = reya_tester.get_current_price()
        qty = "0.01"
        tp_order_details = OrderDetails(
            symbol=symbol,
            is_buy=True,  # on short position
            price=str(market_price * 1.1),  # out of the money
            qty=qty,
            account_id=reya_tester.account_id,
            order_type=OrderType.TP,
        )
        await create_ioc_order(
            reya_tester,
            tp_order_details.symbol,
            str(market_price * 0.99),
            not tp_order_details.is_buy,
            qty=qty,
        )
        position_before: Position = reya_tester.get_position(tp_order_details.symbol)
        assert float(position_before.qty) == float(qty), "Position was not created"

        # SUBMIT TP
        tp_order: CreateOrderResponse = reya_tester.create_tp_order(
            symbol=tp_order_details.symbol,
            is_buy=tp_order_details.is_buy,
            trigger_price=tp_order_details.price,
        )
        logger.info(f"Created TP order with ID: {tp_order.order_id}")

        # VALIDATE order was executed correctly
        # TODO: Claudiu - listen to WS for trade confirmation and validate  

        # wait 5 seconds for the order to propagate (skip when WS is implemented)
        await asyncio.sleep(5)

        position_after: Position = reya_tester.get_position(tp_order_details.symbol)
        assert position_after is None, "Position was not closed"

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
async def test_success_sl_when_executed(reya_tester: ReyaTester):
    """SL order triggered"""
    await reya_tester.setup_websocket()
    symbol = "ETHRUSDPERP"

    try:
        # SETUP
        market_price = reya_tester.get_current_price()
        qty = "0.01"
        sl_order_details = OrderDetails(
            symbol=symbol,
            is_buy=True,  # on short position
            price=str(market_price * 0.9),  # in the money
            account_id=reya_tester.account_id,
            order_type=OrderType.SL,
            qty=qty,
        )
        await create_ioc_order(
            reya_tester,
            sl_order_details.symbol,
            str(market_price * 0.99),
            not sl_order_details.is_buy,
            qty=qty,
        )
        position_before: Position = reya_tester.get_position(sl_order_details.symbol)
        assert float(position_before.qty) == float(qty), "Position was not created"

        # SUBMIT SL
        order_response: CreateOrderResponse = reya_tester.create_sl_order(
            symbol=sl_order_details.symbol,
            is_buy=sl_order_details.is_buy,
            trigger_price=sl_order_details.price,
        )
        logger.info(f"Created SL order with ID: {order_response.order_id}")

        # VALIDATE order was executed correctly
        # TODO: Claudiu - listen to WS for trade confirmation and validate

        # wait 5 seconds for the order to propagate (skip when WS is implemented)
        await asyncio.sleep(5)

        position_after: Position = reya_tester.get_position(sl_order_details.symbol)
        assert position_after is None, "Position was not closed"

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
async def test_failure_sltp_when_no_position(reya_tester: ReyaTester):
    """SL order triggered"""
    await reya_tester.setup_websocket()
    symbol = "ETHRUSDPERP"

    try:
        # SETUP
        market_price = reya_tester.get_current_price()
        qty = "0.01"
        sl_order_details = OrderDetails(
            symbol=symbol,
            is_buy=False,  # on short position
            price=str(market_price * 0.9),  # in the money
            account_id=reya_tester.account_id,
            order_type=OrderType.SL,
            qty=qty,
        )
        # await create_ioc_order(reya_tester, sl_order_details["market_id"], str(market_price * 0.99), sl_order_details["is_buy"], qty="0.01")
        position_before = reya_tester.get_position(sl_order_details.symbol)
        assert position_before is None, "Position should be empty"

        # SUBMIT SL
        order_response: CreateOrderResponse = reya_tester.create_sl_order(
            symbol=sl_order_details.symbol,
            is_buy=sl_order_details.is_buy,
            trigger_price=sl_order_details.price,
        )
        # ENSURE IT WAS NOT FILLED NOR STILL OPENED
        # TODO: Claudiu - listen to WS for trade confirmation and validate
        cancelled_or_rejected_order_id = await reya_tester.wait_for_order_cancellation_via_rest(
            order_id=order_response.order_id
        )
        assert cancelled_or_rejected_order_id == order_response.order_id, "SL order should not be opened"

        # SUBMIT TP
        order_response: CreateOrderResponse = reya_tester.create_tp_order(
            symbol=sl_order_details.symbol,
            is_buy=sl_order_details.is_buy,
            trigger_price=sl_order_details.price,
        )
        # ENSURE IT WAS NOT FILLED NOR STILL OPENED
        # TODO: Claudiu - listen to WS for trade confirmation and validate
        cancelled_or_rejected_order_id = await reya_tester.wait_for_order_cancellation_via_rest(
            order_id=order_response.order_id
        )
        assert cancelled_or_rejected_order_id == order_response.order_id, "TP order should not be opened"

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
async def test_failure_sltp_input_validation(reya_tester: ReyaTester):
    """Input validation for TP/SL orders"""
    await reya_tester.setup_websocket()
    symbol = "ETHRUSDPERP"

    try:
        # SETUP
        market_price = reya_tester.get_current_price()
        tp_order_details = OrderDetails(
            symbol=symbol,
            is_buy=True,
            price=str(market_price * 1.1),  # higher than IOC limit price
            account_id=reya_tester.account_id,
            order_type=OrderType.TP,
        )
        await create_ioc_order(
            reya_tester, tp_order_details.symbol, market_price * 1.01, tp_order_details.is_buy, qty="0.01"
        )

        test_cases = [
            {
                "name": "Invalid market_id",
                "params": {
                    "symbol": "invalid",
                    "is_buy": tp_order_details.is_buy,
                    "trigger_price": tp_order_details.price,
                },
            },
            {
                "name": "Wrong market_id",
                "params": {
                    "symbol": 1000000,
                    "is_buy": tp_order_details.is_buy,
                    "trigger_price": tp_order_details.price,
                },
            },
            {
                "name": "Missing market_id",
                "params": {"is_buy": tp_order_details.is_buy, "trigger_price": tp_order_details.price},
            },
            {
                "name": "Missing is_buy",
                "params": {
                    "symbol": tp_order_details.symbol,
                    "trigger_price": tp_order_details.price,
                },
            },
            {
                "name": "Invalid is_buy",
                "params": {
                    "symbol": tp_order_details.symbol,
                    "is_buy": "invalid",
                    "trigger_price": tp_order_details.price,
                },
            },
            {
                "name": "Missing trigger_price",
                "params": {"symbol": tp_order_details.symbol, "is_buy": tp_order_details.is_buy},
            },
            {
                "name": "Invalid trigger_price",
                "params": {
                    "symbol": tp_order_details.symbol,
                    "is_buy": tp_order_details.is_buy,
                    "trigger_price": "invalid",
                },
            },
            {
                "name": "Negative trigger_price",
                "params": {
                    "symbol": tp_order_details.symbol,
                    "is_buy": tp_order_details.is_buy,
                    "trigger_price": "-0.01",
                },
            },
        ]

        for test_case in test_cases:
            logger.info(f"Testing SL: {test_case["name"]}")
            try:
                reya_tester.create_sl_order(
                    symbol=test_case["params"]["symbol"],
                    is_buy=test_case["params"]["is_buy"],
                    trigger_price=test_case["params"]["trigger_price"],
                    expect_error=True,
                )
                # this will raise the error too
                assert False, f"{test_case["name"]} should have failed"
            except Exception as e:
                if "should have failed" not in str(e):
                    logger.info(f"Expected error for {test_case["name"]}: {e}")
                    pass  # Expected error
                else:
                    logger.error(f"Error not found for {test_case["name"]}: {e}")
                    raise e

            logger.info(f"Testing TP: {test_case["name"]}")
            try:
                reya_tester.create_tp_order(
                    symbol=test_case["params"]["symbol"],
                    is_buy=test_case["params"]["is_buy"],
                    trigger_price=test_case["params"]["trigger_price"],
                    expect_error=True,
                )
                # this will raise the error too
                assert False, f"{test_case["name"]} should have failed"
            except Exception as e:
                if "should have failed" not in str(e):
                    logger.info(f"Pass: Expected error for {test_case["name"]}: {e}")
                    pass  # Expected error
                else:
                    logger.error(f"Error not found for {test_case["name"]}: {e}")
                    raise e

            logger.info("TP/SL input validation test completed successfully")
    except Exception as e:
        logger.error(f"Error in test: {e}")
        raise
    finally:
        logger.info("----------- Closing position and active orders -----------")
        if reya_tester.websocket:
            reya_tester.websocket.close()
        await reya_tester.close_exposure(symbol, fail_if_none=False)
        await reya_tester.close_active_orders(fail_if_none=False)
