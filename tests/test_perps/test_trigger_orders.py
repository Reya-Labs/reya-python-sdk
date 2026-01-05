#!/usr/bin/env python3

import asyncio

import pytest

from sdk.open_api import RequestError, RequestErrorCode, TimeInForce
from sdk.open_api.exceptions import BadRequestException
from sdk.open_api.models.create_order_response import CreateOrderResponse
from sdk.open_api.models.order import Order
from sdk.open_api.models.order_status import OrderStatus
from sdk.open_api.models.order_type import OrderType
from sdk.open_api.models.position import Position
from sdk.open_api.models.side import Side
from sdk.reya_rest_api.config import REYA_DEX_ID
from sdk.reya_rest_api.models import LimitOrderParameters, TriggerOrderParameters
from tests.helpers import ReyaTester
from tests.helpers.reya_tester import limit_order_params_to_order, logger, trigger_order_params_to_order


def assert_tp_sl_order_submission(
    order_details: Order,
    expected_order_details: Order,
    position: Position,
    _reya_tester: ReyaTester,
):
    """Assert that order execution details are correct"""

    assert order_details is not None, "Should have order execution details"
    assert order_details.symbol == expected_order_details.symbol, "Market ID should match"
    assert order_details.trigger_px is not None
    assert order_details.limit_px is not None
    assert expected_order_details.limit_px is not None
    assert expected_order_details.trigger_px is not None
    assert float(order_details.trigger_px) == pytest.approx(
        float(expected_order_details.trigger_px), rel=1e-6
    ), "Trigger price should match"
    assert float(order_details.limit_px) == pytest.approx(
        float(expected_order_details.limit_px), rel=1e-6
    ), "Limit price should match"
    assert order_details.side == expected_order_details.side, "Executed base should match"
    assert expected_order_details.qty is not None
    assert float(position.qty) == float(expected_order_details.qty), "Order direction does not match"
    assert order_details.status == OrderStatus.OPEN, "Order status should be PENDING"

    logger.info("✅ Order submission confirmed correctly")


@pytest.mark.asyncio
async def test_success_tp_order_create_cancel(reya_tester: ReyaTester):
    """TP order, close right after creation"""
    symbol = "ETHRUSDPERP"

    # SETUP - capture sequence number BEFORE any actions
    _ = await reya_tester.get_last_perp_execution_sequence_number()

    market_price = await reya_tester.get_current_price(symbol)
    limit_order_params = LimitOrderParameters(
        symbol=symbol,
        limit_px=str(float(market_price) * 1.1),
        is_buy=True,
        time_in_force=TimeInForce.IOC,
        qty="0.01",
        reduce_only=False,
    )
    await reya_tester.create_limit_order(limit_order_params)

    # Wait for execution
    expected_order = limit_order_params_to_order(limit_order_params, reya_tester.account_id)
    await reya_tester.wait_for_order_execution(expected_order)
    await reya_tester.check_no_open_orders()

    # SUBMIT TP
    tp_params = TriggerOrderParameters(
        symbol=symbol,
        is_buy=False,  # on long
        trigger_px=str(float(market_price) * 2),  # lower than IOC limit price
        trigger_type=OrderType.TP,
    )
    tp_order: CreateOrderResponse = await reya_tester.create_trigger_order(tp_params)
    logger.info(f"Created TP order with ID: {tp_order.order_id}")

    assert tp_order.order_id is not None
    active_tp_order = await reya_tester.wait_for_order_creation(order_id=tp_order.order_id)
    expected_tp_order = trigger_order_params_to_order(tp_params, reya_tester.account_id)
    await reya_tester.check_open_order_created(tp_order.order_id, expected_tp_order)
    # Get sequence after position was created (from initial limit order)
    sequence_after_position = await reya_tester.get_last_perp_execution_sequence_number()
    await reya_tester.check_position(
        expected_exchange_id=REYA_DEX_ID,
        expected_account_id=reya_tester.account_id,
        symbol=symbol,
        expected_qty="0.01",
        expected_side=Side.A if tp_params.is_buy else Side.B,
    )

    # CANCEL order
    await reya_tester.client.cancel_order(order_id=active_tp_order.order_id)

    await reya_tester.wait_for_order_state(active_tp_order.order_id, OrderStatus.CANCELLED)
    await reya_tester.check_no_order_execution_since(sequence_after_position)
    await reya_tester.check_position(
        expected_exchange_id=REYA_DEX_ID,
        expected_account_id=reya_tester.account_id,
        symbol=symbol,
        expected_qty="0.01",
        expected_side=Side.A if tp_params.is_buy else Side.B,
    )

    logger.info("✅ ✅ ✅ TP order cancel test completed successfully")


@pytest.mark.asyncio
async def test_success_sl_order_create_cancel(reya_tester: ReyaTester):
    """1 SL order, close right after creation"""
    symbol = "ETHRUSDPERP"

    # SETUP - capture sequence number BEFORE any actions
    _ = await reya_tester.get_last_perp_execution_sequence_number()

    market_price = await reya_tester.get_current_price(symbol)

    # Create initial limit order to establish position
    limit_order_params = LimitOrderParameters(
        symbol=symbol,
        limit_px=str(float(market_price) * 1.1),
        time_in_force=TimeInForce.IOC,
        is_buy=True,  # Create long position first
        qty="0.01",
        reduce_only=False,
    )
    await reya_tester.create_limit_order(limit_order_params)

    await reya_tester.check_no_open_orders()

    # SUBMIT SL
    sl_params = TriggerOrderParameters(
        symbol=symbol,
        is_buy=False,  # on long
        trigger_px=str(float(market_price) * 0.9),  # higher than IOC limit price
        trigger_type=OrderType.SL,
    )
    order_response = await reya_tester.create_trigger_order(sl_params)
    logger.info(f"Created SL order with ID: {order_response.order_id}")

    assert order_response.order_id is not None
    active_sl_order: Order = await reya_tester.wait_for_order_creation(order_id=order_response.order_id, timeout=10)
    expected_sl_order = trigger_order_params_to_order(sl_params, reya_tester.account_id)
    await reya_tester.check_open_order_created(order_response.order_id, expected_sl_order)
    # Get sequence after position was created (from initial limit order)
    sequence_after_position = await reya_tester.get_last_perp_execution_sequence_number()
    await reya_tester.check_position(
        expected_exchange_id=REYA_DEX_ID,
        expected_account_id=reya_tester.account_id,
        symbol=symbol,
        expected_qty="0.01",
        expected_side=Side.A if sl_params.is_buy else Side.B,
    )

    # CANCEL
    await reya_tester.client.cancel_order(order_id=active_sl_order.order_id)
    await reya_tester.wait_for_order_state(active_sl_order.order_id, OrderStatus.CANCELLED)
    await reya_tester.check_no_order_execution_since(sequence_after_position)
    await reya_tester.check_position(
        expected_exchange_id=REYA_DEX_ID,
        expected_account_id=reya_tester.account_id,
        symbol=symbol,
        expected_qty="0.01",
        expected_side=Side.A if sl_params.is_buy else Side.B,
    )

    logger.info("SL order cancel test completed successfully")


# Note: the CO bot may not be active on testnet
@pytest.mark.asyncio
async def test_success_tp_wide_when_executed(reya_tester: ReyaTester):
    """SL order triggered"""
    symbol = "ETHRUSDPERP"

    # SETUP
    market_price = await reya_tester.get_current_price()
    qty = "0.01"

    # Create initial limit order to establish short position
    limit_order_params = LimitOrderParameters(
        symbol=symbol,
        limit_px=str(float(market_price) * 0.99),
        is_buy=False,  # Create short position
        time_in_force=TimeInForce.IOC,
        qty=qty,
        reduce_only=False,
    )
    await reya_tester.create_limit_order(limit_order_params)

    # Wait for the initial position to be created from the limit order
    expected_order = limit_order_params_to_order(limit_order_params, reya_tester.account_id)
    await reya_tester.wait_for_order_execution(expected_order)
    await reya_tester.check_no_open_orders()

    # Verify position exists (short position)
    await reya_tester.check_position(
        symbol=symbol,
        expected_exchange_id=REYA_DEX_ID,
        expected_account_id=reya_tester.account_id,
        expected_qty=qty,
        expected_side=Side.A,  # Short position (sell side)
    )

    # SUBMIT TP
    tp_params = TriggerOrderParameters(
        symbol=symbol,
        is_buy=True,  # on short position
        trigger_px=str(float(market_price) * 1.1),  # out of the money
        trigger_type=OrderType.TP,
    )
    tp_order: CreateOrderResponse = await reya_tester.create_trigger_order(tp_params)
    logger.info(f"Created TP order with ID: {tp_order.order_id}")

    assert tp_order.order_id is not None
    await reya_tester.wait_for_order_state(tp_order.order_id, OrderStatus.FILLED)

    # Wait for execution and validate
    # Note: Conditional orders bot may be slow on some environments, use longer timeout
    expected_tp_order = trigger_order_params_to_order(tp_params, reya_tester.account_id)
    execution = await reya_tester.wait_for_closing_order_execution(expected_tp_order, qty, timeout=30)
    await reya_tester.check_order_execution(execution, expected_tp_order, qty)

    # After TP execution, position should be closed
    await reya_tester.check_position_not_open(symbol)


@pytest.mark.asyncio
async def test_success_sl_when_executed(reya_tester: ReyaTester):
    """SL order triggered"""
    symbol = "ETHRUSDPERP"

    # SETUP
    market_price = await reya_tester.get_current_price()
    qty = "0.01"

    # Create initial limit order to establish short position
    limit_order_params = LimitOrderParameters(
        symbol=symbol,
        limit_px=str(float(market_price) * 0.9),
        is_buy=False,  # Create short position
        time_in_force=TimeInForce.IOC,
        qty=qty,
        reduce_only=False,
    )
    await reya_tester.create_limit_order(limit_order_params)

    # Wait for the initial position to be created from the limit order
    expected_order = limit_order_params_to_order(limit_order_params, reya_tester.account_id)
    await reya_tester.wait_for_order_execution(expected_order)
    await reya_tester.check_no_open_orders()

    # Verify position exists (short position)
    await reya_tester.check_position(
        symbol=symbol,
        expected_exchange_id=REYA_DEX_ID,
        expected_account_id=reya_tester.account_id,
        expected_qty=qty,
        expected_side=Side.A,  # Short position (sell side)
    )

    # SUBMIT SL
    sl_params = TriggerOrderParameters(
        symbol=symbol,
        is_buy=True,  # on short position
        trigger_px=str(float(market_price) * 0.9),  # in the money
        trigger_type=OrderType.SL,
    )
    order_response: CreateOrderResponse = await reya_tester.create_trigger_order(sl_params)
    logger.info(f"Created SL order with ID: {order_response.order_id}")

    assert order_response.order_id is not None
    await reya_tester.wait_for_order_state(order_response.order_id, OrderStatus.FILLED)

    # Wait for execution and validate
    # Note: Conditional orders bot may be slow on some environments, use longer timeout
    expected_sl_order = trigger_order_params_to_order(sl_params, reya_tester.account_id)
    execution = await reya_tester.wait_for_closing_order_execution(expected_sl_order, qty, timeout=30)
    await reya_tester.check_order_execution(execution, expected_sl_order, qty)

    # After SL execution, position should be closed
    await reya_tester.check_position_not_open(symbol)


@pytest.mark.asyncio
async def test_failure_sltp_when_no_position(reya_tester: ReyaTester):
    """SL/TP orders should be cancelled when there's no position"""
    symbol = "ETHRUSDPERP"

    # SETUP - capture sequence number BEFORE any actions
    last_sequence_before = await reya_tester.get_last_perp_execution_sequence_number()

    market_price = await reya_tester.get_current_price()
    await reya_tester.check_position_not_open(symbol)

    # SUBMIT SL
    sl_params = TriggerOrderParameters(
        symbol=symbol,
        is_buy=False,  # on short position
        trigger_px=str(float(market_price) * 0.9),  # in the money
        trigger_type=OrderType.SL,
    )
    order_response_sl: CreateOrderResponse = await reya_tester.create_trigger_order(sl_params)
    # ENSURE IT WAS NOT FILLED NOR STILL OPENED
    assert order_response_sl.order_id is not None
    cancelled_or_rejected_order_id = await reya_tester.wait_for_order_state(
        order_response_sl.order_id, OrderStatus.CANCELLED
    )
    assert cancelled_or_rejected_order_id == order_response_sl.order_id, "SL order should not be opened"
    await reya_tester.check_no_open_orders()
    await reya_tester.check_no_order_execution_since(last_sequence_before)

    # SUBMIT TP
    tp_params = TriggerOrderParameters(
        symbol=symbol,
        is_buy=False,  # on short position
        trigger_px=str(float(market_price) * 0.9),  # in the money
        trigger_type=OrderType.TP,
    )
    order_response_tp: CreateOrderResponse = await reya_tester.create_trigger_order(tp_params)
    # ENSURE IT WAS NOT FILLED NOR STILL OPENED
    assert order_response_tp.order_id is not None
    cancelled_or_rejected_order_id = await reya_tester.wait_for_order_state(
        order_response_tp.order_id, OrderStatus.CANCELLED
    )
    assert cancelled_or_rejected_order_id == order_response_tp.order_id, "TP order should not be opened"
    await reya_tester.check_no_open_orders()
    await reya_tester.check_no_order_execution_since(last_sequence_before)


@pytest.mark.asyncio
async def test_failure_cancel_when_order_is_not_found(reya_tester: ReyaTester):
    """SL order triggered"""
    await reya_tester.check_no_open_orders()
    try:
        await reya_tester.client.cancel_order(order_id="unknown_id")
        raise RuntimeError("Should have failed")
    except BadRequestException as e:
        assert e.data is not None
        requestError: RequestError = e.data
        # Check that the message starts with the expected error (API may include additional guidance)
        assert requestError.message is not None
        assert requestError.message.startswith(
            "Missing order with id unknown_id"
        ), f"Expected message to start with 'Missing order with id unknown_id', got: {requestError.message}"
        assert requestError.error == RequestErrorCode.CANCEL_ORDER_OTHER_ERROR

    await reya_tester.check_no_open_orders()
    logger.info("✅ Error correctly mentions unacceptable order price issue")


@pytest.mark.asyncio
async def test_sltp_cancelled_when_position_closed(reya_tester: ReyaTester):
    """Test that SL/TP orders are cancelled when position is closed manually"""
    symbol = "ETHRUSDPERP"

    # SETUP - Get current market price
    market_price = await reya_tester.get_current_price()
    logger.info(f"Current market price: ${market_price}")

    # Step 1: Open position with a limit order
    limit_order_params = LimitOrderParameters(
        symbol=symbol,
        limit_px=str(float(market_price) * 1.01),  # Slightly above market
        is_buy=True,
        time_in_force=TimeInForce.IOC,
        qty="0.01",
        reduce_only=False,
    )
    await reya_tester.create_limit_order(limit_order_params)

    # Wait for the initial position to be created from the limit order
    expected_order = limit_order_params_to_order(limit_order_params, reya_tester.account_id)
    await reya_tester.wait_for_order_execution(expected_order)
    await reya_tester.check_no_open_orders()

    # Verify position was created
    await reya_tester.check_position(
        symbol=symbol,
        expected_exchange_id=REYA_DEX_ID,
        expected_account_id=reya_tester.account_id,
        expected_qty="0.01",
        expected_side=Side.B,  # Long position
    )

    # Step 2: Create SL order (stop loss below market price)
    sl_params = TriggerOrderParameters(
        symbol=symbol,
        is_buy=False,  # Sell to close long position
        trigger_px=str(float(market_price) * 0.95),  # 5% below market
        trigger_type=OrderType.SL,
    )
    sl_response = await reya_tester.create_trigger_order(sl_params)
    logger.info(f"Created SL order with ID: {sl_response.order_id}")

    # Step 3: Create TP order (take profit above market price)
    tp_params = TriggerOrderParameters(
        symbol=symbol,
        is_buy=False,  # Sell to close long position
        trigger_px=str(float(market_price) * 1.05),  # 5% above market
        trigger_type=OrderType.TP,
    )
    tp_response = await reya_tester.create_trigger_order(tp_params)
    logger.info(f"Created TP order with ID: {tp_response.order_id}")

    # Verify both SL and TP orders are created
    await reya_tester.wait_for_order_creation(order_id=sl_response.order_id, timeout=10)
    await reya_tester.wait_for_order_creation(order_id=tp_response.order_id, timeout=10)

    # Step 4: Manually close the position with a market order
    close_order_params = LimitOrderParameters(
        symbol=symbol,
        limit_px="0",  # Market order (very low price for sell)
        is_buy=False,  # Sell to close the long position
        time_in_force=TimeInForce.IOC,
        qty="0.01",
        reduce_only=True,
    )
    await reya_tester.create_limit_order(close_order_params)

    # Step 5: Verify position is closed
    expected_order = limit_order_params_to_order(close_order_params, reya_tester.account_id)
    execution = await reya_tester.wait_for_closing_order_execution(expected_order)
    await reya_tester.check_order_execution(execution, expected_order, "0.01")
    await reya_tester.check_position_not_open(symbol)

    # Step 6: Verify both SL and TP orders are cancelled
    # Add a small delay to allow for order cancellation processing
    await reya_tester.wait_for_order_state(sl_response.order_id, OrderStatus.CANCELLED, timeout=10)
    await reya_tester.wait_for_order_state(tp_response.order_id, OrderStatus.CANCELLED, timeout=10)

    # Final verification - no open orders
    await reya_tester.check_no_open_orders()
    logger.info("✅ SL and TP orders successfully cancelled when position was closed")


@pytest.mark.asyncio
async def test_sltp_cancelled_when_position_flipped(reya_tester: ReyaTester):
    """Test that SL/TP orders are cancelled when position is flipped"""
    symbol = "ETHRUSDPERP"

    # SETUP - Get current market price
    market_price = await reya_tester.get_current_price()
    logger.info(f"Current market price: ${market_price}")

    # Step 1: Open long position
    limit_order_params = LimitOrderParameters(
        symbol=symbol,
        limit_px=str(float(market_price) * 1.01),  # Slightly above market
        is_buy=True,
        time_in_force=TimeInForce.IOC,
        qty="0.01",
        reduce_only=False,
    )
    await reya_tester.create_limit_order(limit_order_params)

    # Wait for the initial position to be created from the limit order
    expected_order = limit_order_params_to_order(limit_order_params, reya_tester.account_id)
    await reya_tester.wait_for_order_execution(expected_order)
    await reya_tester.check_no_open_orders()

    # Verify long position was created
    await reya_tester.check_position(
        symbol=symbol,
        expected_exchange_id=REYA_DEX_ID,
        expected_account_id=reya_tester.account_id,
        expected_qty="0.01",
        expected_side=Side.B,  # Long position
    )

    # Step 2: Create SL order for long position
    sl_params = TriggerOrderParameters(
        symbol=symbol,
        is_buy=False,  # Sell to close long position
        trigger_px=str(float(market_price) * 0.95),  # 5% below market
        trigger_type=OrderType.SL,
    )
    sl_response = await reya_tester.create_trigger_order(sl_params)
    logger.info(f"Created SL order with ID: {sl_response.order_id}")

    # Step 3: Create TP order for long position
    tp_params = TriggerOrderParameters(
        symbol=symbol,
        is_buy=False,  # Sell to close long position
        trigger_px=str(float(market_price) * 1.05),  # 5% above market
        trigger_type=OrderType.TP,
    )
    tp_response = await reya_tester.create_trigger_order(tp_params)
    logger.info(f"Created TP order with ID: {tp_response.order_id}")

    # Verify both SL and TP orders are created
    await reya_tester.wait_for_order_creation(order_id=sl_response.order_id, timeout=10)
    await reya_tester.wait_for_order_creation(order_id=tp_response.order_id, timeout=10)

    # Step 4: Flip position by selling more than current position (0.01 + 0.01 = 0.02 total)
    flip_order_params = LimitOrderParameters(
        symbol=symbol,
        limit_px="0",  # Market order (very low price for sell)
        is_buy=False,  # Sell
        time_in_force=TimeInForce.IOC,
        qty="0.02",  # More than current position to flip
        reduce_only=False,
    )
    await reya_tester.create_limit_order(flip_order_params)

    # Step 5: Verify position is now short (flipped)
    expected_order = limit_order_params_to_order(flip_order_params, reya_tester.account_id)
    execution = await reya_tester.wait_for_order_execution(expected_order)
    await reya_tester.check_order_execution(execution, expected_order, "0.02")

    # Give some time for position to be updated after flip
    await asyncio.sleep(2)
    await reya_tester.check_position(
        symbol=symbol,
        expected_exchange_id=REYA_DEX_ID,
        expected_account_id=reya_tester.account_id,
        expected_qty="0.01",
        expected_side=Side.A,  # Short position (flipped)
    )

    # Step 6: Verify both SL and TP orders are cancelled (since they were for the long position)
    await reya_tester.wait_for_order_state(sl_response.order_id, OrderStatus.CANCELLED, timeout=10)
    await reya_tester.wait_for_order_state(tp_response.order_id, OrderStatus.CANCELLED, timeout=10)

    # Final verification - no open orders
    await reya_tester.check_no_open_orders()
    logger.info("✅ SL and TP orders successfully cancelled when position was flipped")


@pytest.mark.asyncio
async def test_sl_execution_cancels_tp(reya_tester: ReyaTester):
    """Test that when SL executes (in cross), TP order gets cancelled"""
    symbol = "ETHRUSDPERP"

    # SETUP - Get current market price
    market_price = await reya_tester.get_current_price()
    logger.info(f"Current market price: ${market_price}")

    # Step 1: Open long position
    limit_order_params = LimitOrderParameters(
        symbol=symbol,
        limit_px=str(float(market_price) * 1.01),  # Slightly above market
        is_buy=True,
        time_in_force=TimeInForce.IOC,
        qty="0.01",
        reduce_only=False,
    )
    await reya_tester.create_limit_order(limit_order_params)

    # Wait for the initial position to be created from the limit order
    expected_order = limit_order_params_to_order(limit_order_params, reya_tester.account_id)
    await reya_tester.wait_for_order_execution(expected_order)
    await reya_tester.check_no_open_orders()

    # Verify long position was created
    await reya_tester.check_position(
        symbol=symbol,
        expected_exchange_id=REYA_DEX_ID,
        expected_account_id=reya_tester.account_id,
        expected_qty="0.01",
        expected_side=Side.B,  # Long position
    )

    # Step 2: Create SL order that is "in cross" (trigger price ABOVE current market for a long position SL)
    # SL for long triggers when price <= trigger_px, so setting trigger_px ABOVE market means it's already triggered
    sl_params = TriggerOrderParameters(
        symbol=symbol,
        is_buy=False,  # Sell to close long position
        trigger_px=str(float(market_price) * 1.01),  # Just above market - should trigger immediately
        trigger_type=OrderType.SL,
    )
    sl_response = await reya_tester.create_trigger_order(sl_params)
    logger.info(f"Created SL order with ID: {sl_response.order_id}")

    # Step 3: Create TP order that is NOT in cross (well below market price for a long position)
    # TP for long triggers when price >= trigger_px, so setting trigger_px well above market means it won't trigger
    tp_params = TriggerOrderParameters(
        symbol=symbol,
        is_buy=False,  # Sell to close long position
        trigger_px=str(float(market_price) * 1.10),  # 10% above market - not in cross
        trigger_type=OrderType.TP,
    )
    tp_response = await reya_tester.create_trigger_order(tp_params)
    logger.info(f"Created TP order with ID: {tp_response.order_id}")

    # Verify TP order is created first
    await reya_tester.wait_for_order_creation(order_id=tp_response.order_id, timeout=10)

    # Step 4: Wait for SL to execute (should happen quickly since it's in cross)
    # The SL execution should close the position
    # Note: Conditional orders bot may be slow on some environments, use longer timeout
    expected_sl_order = trigger_order_params_to_order(sl_params, reya_tester.account_id)
    execution = await reya_tester.wait_for_closing_order_execution(expected_sl_order, "0.01", timeout=30)
    await reya_tester.check_order_execution(execution, expected_sl_order, "0.01")
    await reya_tester.check_position_not_open(symbol)

    # Step 5: Verify TP order gets cancelled (since position is closed by SL)
    await reya_tester.wait_for_order_state(tp_response.order_id, OrderStatus.CANCELLED, timeout=10)

    # Final verification - no open orders
    await reya_tester.check_no_open_orders()
    logger.info("✅ TP order successfully cancelled when SL executed and closed position")


@pytest.mark.asyncio
async def test_tp_execution_cancels_sl(reya_tester: ReyaTester):
    """Test that when TP executes (in cross), SL order gets cancelled"""
    symbol = "ETHRUSDPERP"

    # SETUP - Get current market price
    market_price = await reya_tester.get_current_price()
    logger.info(f"Current market price: ${market_price}")

    # Step 1: Open long position
    limit_order_params = LimitOrderParameters(
        symbol=symbol,
        limit_px=str(float(market_price) * 1.01),  # Slightly above market
        is_buy=True,
        time_in_force=TimeInForce.IOC,
        qty="0.01",
        reduce_only=False,
    )
    await reya_tester.create_limit_order(limit_order_params)

    # Wait for the initial position to be created from the limit order
    expected_order = limit_order_params_to_order(limit_order_params, reya_tester.account_id)
    await reya_tester.wait_for_order_execution(expected_order)
    await reya_tester.check_no_open_orders()

    # Verify long position was created
    await reya_tester.check_position(
        symbol=symbol,
        expected_exchange_id=REYA_DEX_ID,
        expected_account_id=reya_tester.account_id,
        expected_qty="0.01",
        expected_side=Side.B,  # Long position
    )

    # Step 2: Create SL order that is NOT in cross (well above market price for a long position)
    # SL for long triggers when price <= trigger_px, so setting trigger_px well below market means it won't trigger
    sl_params = TriggerOrderParameters(
        symbol=symbol,
        is_buy=False,  # Sell to close long position
        trigger_px=str(float(market_price) * 0.90),  # 10% below market - not in cross
        trigger_type=OrderType.SL,
    )
    sl_response = await reya_tester.create_trigger_order(sl_params)
    logger.info(f"Created SL order with ID: {sl_response.order_id}")

    # Step 3: Create TP order that is "in cross" (trigger price BELOW current market for a long position TP)
    # TP for long triggers when price >= trigger_px, so setting trigger_px BELOW market means it's already triggered
    tp_params = TriggerOrderParameters(
        symbol=symbol,
        is_buy=False,  # Sell to close long position
        trigger_px=str(float(market_price) * 0.99),  # Just below market - should trigger immediately
        trigger_type=OrderType.TP,
    )
    tp_response = await reya_tester.create_trigger_order(tp_params)
    logger.info(f"Created TP order with ID: {tp_response.order_id}")

    # Verify SL order is created first
    await reya_tester.wait_for_order_creation(order_id=sl_response.order_id, timeout=10)

    # Step 4: Wait for TP to execute (should happen quickly since it's in cross)
    # The TP execution should close the position
    # Note: Conditional orders bot may be slow on some environments, use longer timeout
    expected_tp_order = trigger_order_params_to_order(tp_params, reya_tester.account_id)
    execution = await reya_tester.wait_for_closing_order_execution(expected_tp_order, "0.01", timeout=30)
    await reya_tester.check_order_execution(execution, expected_tp_order, "0.01")
    await reya_tester.check_position_not_open(symbol)

    # Step 5: Verify SL order gets cancelled (since position is closed by TP)
    await reya_tester.wait_for_order_state(sl_response.order_id, OrderStatus.CANCELLED, timeout=10)

    # Final verification - no open orders
    await reya_tester.check_no_open_orders()
    logger.info("✅ SL order successfully cancelled when TP executed and closed position")
