#!/usr/bin/env python3

import asyncio
import time

import pytest

from sdk.open_api.models.create_order_response import CreateOrderResponse
from sdk.open_api.models.order import Order
from sdk.open_api.models.order_status import OrderStatus
from sdk.open_api.models.order_type import OrderType
from sdk.open_api.models.perp_execution import PerpExecution
from sdk.open_api.models.position import Position
from sdk.open_api.models.side import Side
from tests.models import OrderDetails
from tests.reya_tester import ReyaTester, logger


def assert_tp_sl_order_submission(
    order_details: Order,
    expected_order_details: OrderDetails,
    position: Position,
    _reya_tester: ReyaTester,
):
    """Assert that order execution details are correct"""

    assert order_details is not None, "Should have order execution details"
    assert order_details.symbol == expected_order_details.symbol, "Market ID should match"
    assert float(order_details.trigger_px) == pytest.approx(
        float(expected_order_details.limit_px), rel=1e-6
    ), "Trigger price should match"
    assert (order_details.side == Side.B) == expected_order_details.is_buy, "Executed base should match"
    assert float(position.qty) == float(expected_order_details.qty), "Order direction does not match"
    assert order_details.status == OrderStatus.OPEN, "Order status should be PENDING"

    logger.info("✅ Order submission confirmed correctly")


async def create_ioc_order(reya_tester: ReyaTester, symbol: str, price_with_offset: str, is_buy: bool, qty: str):
    order_details = OrderDetails(
        account_id=reya_tester.account_id,
        symbol=symbol,
        is_buy=is_buy,
        limit_px=price_with_offset,
        qty=qty,
        order_type=OrderType.LIMIT,
    )

    # TODO: Claudiu - listen to WS for trade confirmation

    # Execute
    await reya_tester.create_limit_order(
        symbol=symbol,
        is_buy=order_details.is_buy,
        limit_px=order_details.limit_px,
        qty=order_details.qty,
        reduce_only=False,
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
        market_price = await reya_tester.get_current_price(symbol)
        tp_order_details = OrderDetails(
            account_id=reya_tester.account_id,
            order_type=OrderType.TP,
            symbol=symbol,
            is_buy=False,  # on long
            limit_px=str(market_price * 2),  # lower than IOC limit price
            qty="0.01",
        )
        await create_ioc_order(
            reya_tester,
            tp_order_details.symbol,
            str(market_price * 1.1),
            not tp_order_details.is_buy,
            qty=tp_order_details.qty,
        )

        await reya_tester.check_no_open_orders()

        # SUBMIT TP
        tp_order: CreateOrderResponse | None = await reya_tester.create_tp_order(
            symbol=tp_order_details.symbol,
            is_buy=tp_order_details.is_buy,
            trigger_px=tp_order_details.limit_px,
        )
        assert tp_order is not None, "TP order creation should succeed"
        logger.info(f"Created TP order with ID: {tp_order.order_id}")

        # VALIDATE order was registered correctly
        # TODO: Claudiu - listen to WS order status update and validate

        active_tp_order = await reya_tester.wait_for_order_creation_via_rest(order_id=tp_order.order_id)
        await reya_tester.check_open_order_created(tp_order.order_id, tp_order_details)
        await reya_tester.check_no_order_execution_since(active_tp_order.created_at)
        await reya_tester.check_position(
            symbol=tp_order_details.symbol,
            expected_qty=tp_order_details.qty,
            expected_side=Side.A if tp_order_details.is_buy else Side.B,
        )

        # CANCEL order
        await reya_tester.client.cancel_order(order_id=active_tp_order.order_id)
        cancelled_order_id = await reya_tester.wait_for_order_cancellation_via_rest(order_id=active_tp_order.order_id)
        assert cancelled_order_id == active_tp_order.order_id, "TP order was not cancelled"

        await reya_tester.check_order_not_open(active_tp_order.order_id)
        await reya_tester.check_no_order_execution_since(active_tp_order.created_at)
        await reya_tester.check_position(
            symbol=tp_order_details.symbol,
            expected_qty=tp_order_details.qty,
            expected_side=Side.A if tp_order_details.is_buy else Side.B,
        )

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
        market_price = await reya_tester.get_current_price(symbol)
        sl_order_details = OrderDetails(
            symbol=symbol,
            is_buy=False,  # on long
            limit_px=str(market_price * 0.9),  # higher than IOC limit price
            account_id=reya_tester.account_id,
            order_type=OrderType.SL,
            qty="0.01",
        )
        await create_ioc_order(
            reya_tester,
            sl_order_details.symbol,
            str(market_price * 1.1),
            not sl_order_details.is_buy,
            qty="0.01",
        )

        await reya_tester.check_no_open_orders()

        # SUBMIT SL
        order_response = await reya_tester.create_sl_order(
            symbol=sl_order_details.symbol,
            is_buy=sl_order_details.is_buy,
            trigger_px=sl_order_details.limit_px,
        )
        logger.info(f"Created SL order with ID: {order_response.order_id}")

        # VALIDATE order was created correctly
        # TODO: Claudiu - listen to WS order status update and validate

        active_sl_order: Order = await reya_tester.wait_for_order_creation_via_rest(
            order_id=order_response.order_id, timeout=10
        )
        await reya_tester.check_open_order_created(order_response.order_id, sl_order_details)
        await reya_tester.check_no_order_execution_since(active_sl_order.created_at)
        await reya_tester.check_position(
            symbol=sl_order_details.symbol,
            expected_qty=sl_order_details.qty,
            expected_side=Side.A if sl_order_details.is_buy else Side.B,
        )

        # CANCEL
        await reya_tester.client.cancel_order(order_id=active_sl_order.order_id)
        cancelled_order_id = await reya_tester.wait_for_order_cancellation_via_rest(order_id=active_sl_order.order_id)
        assert cancelled_order_id == active_sl_order.order_id, "SL order was not cancelled"
        await reya_tester.check_order_not_open(active_sl_order.order_id)
        await reya_tester.check_no_order_execution_since(active_sl_order.created_at)
        await reya_tester.check_position(
            symbol=sl_order_details.symbol,
            expected_qty=sl_order_details.qty,
            expected_side=Side.A if sl_order_details.is_buy else Side.B,
        )

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
    symbol = "ETHRUSDPERP"

    try:
        # SETUP
        market_price = await reya_tester.get_current_price()
        qty = "0.01"

        tp_order_details = OrderDetails(
            symbol=symbol,
            is_buy=True,  # on short position
            trigger_px=str(market_price * 1.1),
            qty=qty,
            account_id=reya_tester.account_id,
            order_type=OrderType.TP,
        )
        sl_order_details = OrderDetails(
            symbol=symbol,
            is_buy=tp_order_details.is_buy,
            trigger_px=str(market_price * 0.9),
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
        tp_order: CreateOrderResponse = await reya_tester.create_tp_order(
            symbol=symbol,
            is_buy=tp_order_details.is_buy,
            trigger_px=tp_order_details.trigger_px,
        )
        logger.info(f"Created TP order with ID: {tp_order.order_id}")

        # SUBMIT SL
        sl_order: CreateOrderResponse = await reya_tester.create_sl_order(
            symbol=symbol,
            is_buy=sl_order_details.is_buy,
            trigger_px=sl_order_details.trigger_px,
        )
        logger.info(f"Created SL order with ID: {sl_order.order_id}")

        # VALIDATE order was executed correctly
        # TODO: Claudiu - listen to WS for trade confirmation
        confirmed_sequence_number = None  # from WS

        if confirmed_sequence_number is not None:
            #  EITHER order was executed
            logger.info("👌 SLTP order executed")
            order_execution_details: PerpExecution = await reya_tester.get_wallet_perp_execution(
                confirmed_sequence_number
            )
            assert float(order_execution_details.limit_px) < float(tp_order_details.limit_px) or float(
                order_execution_details.limit_px
            ) > float(sl_order_details.limit_px), "SLTP execution price out of bounds"

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
        market_price = await reya_tester.get_current_price()
        qty = "0.01"
        tp_order_details = OrderDetails(
            symbol=symbol,
            is_buy=True,  # on short position
            limit_px=str(market_price * 1.1),  # out of the money
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

        await reya_tester.check_no_open_orders()

        # SUBMIT TP
        tp_order: CreateOrderResponse = await reya_tester.create_tp_order(
            symbol=tp_order_details.symbol,
            is_buy=tp_order_details.is_buy,
            trigger_px=tp_order_details.limit_px,
        )
        logger.info(f"Created TP order with ID: {tp_order.order_id}")

        # VALIDATE order was executed correctly
        # TODO: Claudiu - listen to WS for trade confirmation and validate

        # wait 5 seconds for the order to propagate (skip when WS is implemented)
        await asyncio.sleep(5)
        await reya_tester.check_position_not_open(tp_order_details.symbol)
        await reya_tester.check_order_not_open(tp_order.order_id)
        await reya_tester.check_order_execution(tp_order_details)

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
        market_price = await reya_tester.get_current_price()
        qty = "0.01"
        sl_order_details = OrderDetails(
            symbol=symbol,
            is_buy=True,  # on short position
            limit_px=str(market_price * 0.9),  # in the money
            account_id=reya_tester.account_id,
            order_type=OrderType.SL,
            qty=qty,
        )
        await create_ioc_order(
            reya_tester,
            sl_order_details.symbol,
            str(market_price * 0.9),
            not sl_order_details.is_buy,
            qty=qty,
        )

        await reya_tester.check_no_open_orders()

        # SUBMIT SL
        order_response: CreateOrderResponse = await reya_tester.create_sl_order(
            symbol=sl_order_details.symbol,
            is_buy=sl_order_details.is_buy,
            trigger_px=sl_order_details.limit_px,
        )
        logger.info(f"Created SL order with ID: {order_response.order_id}")

        # VALIDATE order was executed correctly
        # TODO: Claudiu - listen to WS for trade confirmation and validate

        # wait 5 seconds for the order to propagate (skip when WS is implemented)
        await asyncio.sleep(5)
        await reya_tester.check_position_not_open(sl_order_details.symbol)
        await reya_tester.check_order_not_open(order_response.order_id)
        await reya_tester.check_order_execution(sl_order_details)

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
        market_price = await reya_tester.get_current_price()
        qty = "0.01"
        start_time = int(time.time() * 1000)
        sl_order_details = OrderDetails(
            symbol=symbol,
            is_buy=False,  # on short position
            limit_px=str(market_price * 0.9),  # in the money
            account_id=reya_tester.account_id,
            order_type=OrderType.SL,
            qty=qty,
        )
        # await create_ioc_order(reya_tester, sl_order_details["market_id"],
        #                       str(market_price * 0.99), sl_order_details["is_buy"], qty="0.01")
        await reya_tester.check_position_not_open(sl_order_details.symbol)

        # SUBMIT SL
        order_response: CreateOrderResponse = await reya_tester.create_sl_order(
            symbol=sl_order_details.symbol,
            is_buy=sl_order_details.is_buy,
            trigger_px=sl_order_details.limit_px,
        )
        # ENSURE IT WAS NOT FILLED NOR STILL OPENED
        # TODO: Claudiu - listen to WS for trade confirmation and validate
        cancelled_or_rejected_order_id = await reya_tester.wait_for_order_cancellation_via_rest(
            order_id=order_response.order_id
        )
        assert cancelled_or_rejected_order_id == order_response.order_id, "SL order should not be opened"
        await reya_tester.check_no_open_orders()
        await reya_tester.check_no_order_execution_since(start_time)

        # SUBMIT TP
        order_response: CreateOrderResponse = await reya_tester.create_tp_order(
            symbol=sl_order_details.symbol,
            is_buy=sl_order_details.is_buy,
            trigger_px=sl_order_details.limit_px,
        )
        # ENSURE IT WAS NOT FILLED NOR STILL OPENED
        # TODO: Claudiu - listen to WS for trade confirmation and validate
        cancelled_or_rejected_order_id = await reya_tester.wait_for_order_cancellation_via_rest(
            order_id=order_response.order_id
        )
        assert cancelled_or_rejected_order_id == order_response.order_id, "TP order should not be opened"
        await reya_tester.check_no_open_orders()
        await reya_tester.check_no_order_execution_since(start_time)

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
        market_price = await reya_tester.get_current_price()
        tp_order_details = OrderDetails(
            symbol=symbol,
            is_buy=True,
            limit_px=str(market_price * 1.1),  # higher than IOC limit price
            account_id=reya_tester.account_id,
            order_type=OrderType.TP,
        )

        await create_ioc_order(
            reya_tester,
            tp_order_details.symbol,
            str(market_price * 1.01),
            tp_order_details.is_buy,
            qty="0.01",
        )

        test_cases = [
            {
                "name": "Invalid market_id",
                "params": {
                    "symbol": "invalid",
                    "is_buy": tp_order_details.is_buy,
                    "trigger_px": tp_order_details.limit_px,
                },
            },
            {
                "name": "Wrong market_id",
                "params": {
                    "symbol": 1000000,
                    "is_buy": tp_order_details.is_buy,
                    "trigger_px": tp_order_details.limit_px,
                },
            },
        ]

        await asyncio.sleep(5)
        start_timestamp = int(time.time() * 1000)

        for test_case in test_cases:
            logger.info(f"Testing SL: {test_case["name"]}")
            await reya_tester.check_no_open_orders()
            await reya_tester.check_position(
                symbol=tp_order_details.symbol,
                expected_qty="0.01",
                expected_side=Side.B if tp_order_details.is_buy else Side.A,
            )

            response = await reya_tester.create_sl_order(
                symbol=test_case["params"]["symbol"],
                is_buy=test_case["params"]["is_buy"],
                trigger_px=test_case["params"]["trigger_px"],
                expect_error=True,
            )
            # this will raise the error too
            assert not response, f"{test_case["name"]} should have failed"
            await reya_tester.check_no_open_orders()
            await reya_tester.check_no_order_execution_since(start_timestamp)

            logger.info(f"Testing TP: {test_case["name"]}")
            response = await reya_tester.create_tp_order(
                symbol=test_case["params"]["symbol"],
                is_buy=test_case["params"]["is_buy"],
                trigger_px=test_case["params"]["trigger_px"],
                expect_error=True,
            )
            # this will raise the error too
            assert not response, f"{test_case["name"]} should have failed"
            await reya_tester.check_no_open_orders()
            await reya_tester.check_no_order_execution_since(start_timestamp)
            logger.info("TP/SL input validation test completed successfully")

    finally:
        logger.info("----------- Closing position and active orders -----------")
        if reya_tester.websocket:
            reya_tester.websocket.close()
        await reya_tester.close_exposure(symbol, fail_if_none=False)
        await reya_tester.close_active_orders(fail_if_none=False)


@pytest.mark.asyncio
async def test_failure_cancel_when_order_is_not_found(reya_tester: ReyaTester):
    """SL order triggered"""
    await reya_tester.setup_websocket()
    symbol = "ETHRUSDPERP"

    try:
        await reya_tester.check_no_open_orders()
        response = await reya_tester.client.cancel_order(order_id="unknown_id")
        # If we get here, the test failed - order should not have been accepted
        assert not response, "No order to cancel, should have failed"

        await reya_tester.check_no_open_orders()
        logger.info("✅ Error correctly mentions unacceptable order price issue")

    finally:
        logger.info("----------- Closing position and active orders -----------")
        if reya_tester.websocket:
            reya_tester.websocket.close()
        await reya_tester.close_exposure(symbol, fail_if_none=False)
        await reya_tester.close_active_orders(fail_if_none=False)
