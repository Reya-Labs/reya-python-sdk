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

    logger.info("TP order cancel test completed successfully")


@pytest.mark.asyncio
async def test_success_sl_order_create_cancel(reya_tester: ReyaTester):
    """SL order, close right after creation"""
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


# Constants for conditional order retry logic
CO_MAX_RETRIES = 5
CO_TIMEOUT_PER_ATTEMPT = 30


async def _cancel_order_if_open(reya_tester: ReyaTester, order_id: str) -> None:
    """Cancel an order if it's still open. Silently ignores errors."""
    try:
        ws_order = reya_tester.ws.orders.get(str(order_id))
        if ws_order and ws_order.status.value == "OPEN":
            await reya_tester.client.cancel_order(order_id=order_id)
            await reya_tester.wait_for_order_state(order_id, OrderStatus.CANCELLED, timeout=10)
    except Exception:
        pass


# Note: the CO bot may not be active on testnet
@pytest.mark.asyncio
async def test_tp_in_cross_executes_immediately(reya_tester: ReyaTester):
    """TP order executes immediately when trigger condition is already met (in-cross).
    
    Setup: SHORT position at ~1.0x market
    TP trigger: 1.1x (above market) - condition "price <= 1.1x" is already TRUE
    
    Note: This tests the CO bot's in-cross detection, not a semantically correct TP.
    A real TP for a short would have trigger BELOW entry (profit when price drops).
    
    Verifies:
    1. TP order executes immediately when in-cross
    2. Trade is consistent between REST and WS (same sequence number)
    3. Position is closed after execution
    """
    symbol = "ETHRUSDPERP"
    market_price = await reya_tester.get_current_price()
    qty = "0.01"

    # SETUP: Create short position
    async with reya_tester.perp_trade() as ctx:
        limit_order_params = LimitOrderParameters(
            symbol=symbol,
            limit_px=str(float(market_price) * 0.99),
            is_buy=False,
            time_in_force=TimeInForce.IOC,
            qty=qty,
            reduce_only=False,
        )
        await reya_tester.create_limit_order(limit_order_params)
        expected_order = limit_order_params_to_order(limit_order_params, reya_tester.account_id)
        result = await ctx.wait_for_execution(expected_order)
        logger.info(f"Position created with trade seq={result.sequence_number}")

    await reya_tester.check_no_open_orders()

    # RETRY LOOP: CO bot may be slow
    last_error: Exception | None = None
    for attempt in range(1, CO_MAX_RETRIES + 1):
        logger.info(f"🔄 TP execution attempt {attempt}/{CO_MAX_RETRIES}")
        
        # Verify position exists before placing CO
        if await reya_tester.data.position(symbol) is None:
            raise AssertionError(f"Position closed before placing TP (attempt {attempt})")
        
        async with reya_tester.perp_trade() as ctx:
            tp_params = TriggerOrderParameters(
                symbol=symbol,
                is_buy=True,
                trigger_px=str(float(market_price) * 1.1),
                trigger_type=OrderType.TP,
            )
            tp_order = await reya_tester.create_trigger_order(tp_params)
            logger.info(f"Created TP order: {tp_order.order_id}")
            
            try:
                # PerpTradeContext handles all verification:
                # - Waits for WS execution matching criteria
                # - Fetches same trade from REST by sequence number
                # - Verifies sequence numbers match
                # - Waits for position to close
                expected_tp = trigger_order_params_to_order(tp_params, reya_tester.account_id)
                result = await ctx.wait_for_closing_execution(expected_tp, qty, timeout=CO_TIMEOUT_PER_ATTEMPT)
                logger.info(f"✅ TP executed with trade seq={result.sequence_number}")
                
                # Verify execution fields match expected order
                await reya_tester.check_order_execution(result.rest_execution, expected_tp, qty)
                await reya_tester.check_position_not_open(symbol)
                return  # SUCCESS
                
            except RuntimeError as e:
                last_error = e
                logger.warning(f"⚠️ Attempt {attempt} failed: {e}")
                await _cancel_order_if_open(reya_tester, tp_order.order_id)
                continue
    
    raise AssertionError(f"TP order failed after {CO_MAX_RETRIES} attempts. Last error: {last_error}")


@pytest.mark.asyncio
async def test_sl_in_cross_executes_immediately(reya_tester: ReyaTester):
    """SL order executes immediately when trigger condition is already met (in-cross).
    
    Setup: SHORT position at ~0.9x market
    SL trigger: 0.9x (at entry) - condition "price >= 0.9x" is already TRUE
    
    Note: This tests the CO bot's in-cross detection. The SL is at entry price,
    so it triggers immediately (current price ~1.0x >= 0.9x trigger).
    
    Verifies:
    1. SL order executes immediately when in-cross
    2. Trade is consistent between REST and WS (same sequence number)
    3. Position is closed after execution
    """
    symbol = "ETHRUSDPERP"
    market_price = await reya_tester.get_current_price()
    qty = "0.01"

    # SETUP: Create short position
    async with reya_tester.perp_trade() as ctx:
        limit_order_params = LimitOrderParameters(
            symbol=symbol,
            limit_px=str(float(market_price) * 0.9),
            is_buy=False,
            time_in_force=TimeInForce.IOC,
            qty=qty,
            reduce_only=False,
        )
        await reya_tester.create_limit_order(limit_order_params)
        expected_order = limit_order_params_to_order(limit_order_params, reya_tester.account_id)
        result = await ctx.wait_for_execution(expected_order)
        logger.info(f"Position created with trade seq={result.sequence_number}")

    await reya_tester.check_no_open_orders()

    # RETRY LOOP: CO bot may be slow
    last_error: Exception | None = None
    for attempt in range(1, CO_MAX_RETRIES + 1):
        logger.info(f"🔄 SL execution attempt {attempt}/{CO_MAX_RETRIES}")
        
        # Verify position exists before placing CO
        if await reya_tester.data.position(symbol) is None:
            raise AssertionError(f"Position closed before placing SL (attempt {attempt})")
        
        async with reya_tester.perp_trade() as ctx:
            sl_params = TriggerOrderParameters(
                symbol=symbol,
                is_buy=True,
                trigger_px=str(float(market_price) * 0.9),
                trigger_type=OrderType.SL,
            )
            sl_order = await reya_tester.create_trigger_order(sl_params)
            logger.info(f"Created SL order: {sl_order.order_id}")
            
            try:
                expected_sl = trigger_order_params_to_order(sl_params, reya_tester.account_id)
                result = await ctx.wait_for_closing_execution(expected_sl, qty, timeout=CO_TIMEOUT_PER_ATTEMPT)
                logger.info(f"✅ SL executed with trade seq={result.sequence_number}")
                
                await reya_tester.check_order_execution(result.rest_execution, expected_sl, qty)
                await reya_tester.check_position_not_open(symbol)
                return  # SUCCESS
                
            except RuntimeError as e:
                last_error = e
                logger.warning(f"⚠️ Attempt {attempt} failed: {e}")
                await _cancel_order_if_open(reya_tester, sl_order.order_id)
                continue
    
    raise AssertionError(f"SL order failed after {CO_MAX_RETRIES} attempts. Last error: {last_error}")


@pytest.mark.asyncio
async def test_failure_sltp_when_no_position(reya_tester: ReyaTester):
    """SL/TP orders are immediately cancelled when no position exists.
    
    Setup: No position
    Action: Submit SL and TP orders
    
    Verifies:
    1. SL order is immediately cancelled (not filled, not left open)
    2. TP order is immediately cancelled (not filled, not left open)
    3. No executions occur
    """
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
    """Cancelling a non-existent order returns proper error.
    
    Verifies:
    1. API returns BadRequestException for unknown order ID
    2. Error message indicates the order was not found
    3. Error code is CANCEL_ORDER_OTHER_ERROR
    """
    await reya_tester.check_no_open_orders()
    try:
        await reya_tester.client.cancel_order(order_id="unknown_id")
        raise RuntimeError("Should have failed")
    except BadRequestException as e:
        assert e.data is not None
        requestError: RequestError = e.data
        assert requestError.message is not None
        assert requestError.message.startswith(
            "Missing order with id unknown_id"
        ), f"Expected message to start with 'Missing order with id unknown_id', got: {requestError.message}"
        assert requestError.error == RequestErrorCode.CANCEL_ORDER_OTHER_ERROR

    await reya_tester.check_no_open_orders()
    logger.info("✅ Cancel non-existent order returns proper error")


@pytest.mark.asyncio
async def test_sltp_cancelled_when_position_closed(reya_tester: ReyaTester):
    """SL/TP orders are cancelled when position is manually closed.
    
    Setup: LONG position with SL and TP orders (both not in-cross)
    Action: Manually close position with market order
    
    Verifies:
    1. Position opens correctly
    2. SL and TP orders are created
    3. Manual close executes and closes position
    4. Both SL and TP orders are automatically cancelled
    """
    symbol = "ETHRUSDPERP"
    market_price = await reya_tester.get_current_price()
    qty = "0.01"

    # SETUP: Create long position
    async with reya_tester.perp_trade() as ctx:
        limit_order_params = LimitOrderParameters(
            symbol=symbol,
            limit_px=str(float(market_price) * 1.01),
            is_buy=True,
            time_in_force=TimeInForce.IOC,
            qty=qty,
            reduce_only=False,
        )
        await reya_tester.create_limit_order(limit_order_params)
        expected_order = limit_order_params_to_order(limit_order_params, reya_tester.account_id)
        result = await ctx.wait_for_execution(expected_order)
        logger.info(f"Position created with trade seq={result.sequence_number}")

    await reya_tester.check_no_open_orders()
    await reya_tester.check_position(
        symbol=symbol,
        expected_exchange_id=REYA_DEX_ID,
        expected_account_id=reya_tester.account_id,
        expected_qty=qty,
        expected_side=Side.B,
    )

    # Create SL order (not in-cross: below market for long)
    sl_params = TriggerOrderParameters(
        symbol=symbol,
        is_buy=False,
        trigger_px=str(float(market_price) * 0.95),
        trigger_type=OrderType.SL,
    )
    sl_response = await reya_tester.create_trigger_order(sl_params)
    logger.info(f"Created SL order: {sl_response.order_id}")

    # Create TP order (not in-cross: above market for long)
    tp_params = TriggerOrderParameters(
        symbol=symbol,
        is_buy=False,
        trigger_px=str(float(market_price) * 1.05),
        trigger_type=OrderType.TP,
    )
    tp_response = await reya_tester.create_trigger_order(tp_params)
    logger.info(f"Created TP order: {tp_response.order_id}")

    await reya_tester.wait_for_order_creation(order_id=sl_response.order_id, timeout=10)
    await reya_tester.wait_for_order_creation(order_id=tp_response.order_id, timeout=10)

    # Manually close position
    async with reya_tester.perp_trade() as ctx:
        close_order_params = LimitOrderParameters(
            symbol=symbol,
            limit_px="0",
            is_buy=False,
            time_in_force=TimeInForce.IOC,
            qty=qty,
            reduce_only=True,
        )
        await reya_tester.create_limit_order(close_order_params)
        expected_close = limit_order_params_to_order(close_order_params, reya_tester.account_id)
        result = await ctx.wait_for_closing_execution(expected_close, qty, timeout=10)
        logger.info(f"Position closed with trade seq={result.sequence_number}")

    await reya_tester.check_position_not_open(symbol)

    # Verify both SL and TP orders are cancelled
    await reya_tester.wait_for_order_state(sl_response.order_id, OrderStatus.CANCELLED, timeout=10)
    await reya_tester.wait_for_order_state(tp_response.order_id, OrderStatus.CANCELLED, timeout=10)
    await reya_tester.check_no_open_orders()
    logger.info("✅ SL and TP orders cancelled when position was closed")


@pytest.mark.asyncio
async def test_sltp_cancelled_when_position_flipped(reya_tester: ReyaTester):
    """SL/TP orders are cancelled when position is flipped (long to short).
    
    Setup: LONG position with SL and TP orders (both not in-cross)
    Action: Flip position by selling 2x the position size
    
    Verifies:
    1. Long position opens correctly
    2. SL and TP orders are created
    3. Position flips to short after selling 2x size
    4. Both SL and TP orders are automatically cancelled
    """
    symbol = "ETHRUSDPERP"
    market_price = await reya_tester.get_current_price()
    qty = "0.01"

    # SETUP: Create long position
    async with reya_tester.perp_trade() as ctx:
        limit_order_params = LimitOrderParameters(
            symbol=symbol,
            limit_px=str(float(market_price) * 1.01),
            is_buy=True,
            time_in_force=TimeInForce.IOC,
            qty=qty,
            reduce_only=False,
        )
        await reya_tester.create_limit_order(limit_order_params)
        expected_order = limit_order_params_to_order(limit_order_params, reya_tester.account_id)
        result = await ctx.wait_for_execution(expected_order)
        logger.info(f"Long position created with trade seq={result.sequence_number}")

    await reya_tester.check_no_open_orders()
    await reya_tester.check_position(
        symbol=symbol,
        expected_exchange_id=REYA_DEX_ID,
        expected_account_id=reya_tester.account_id,
        expected_qty=qty,
        expected_side=Side.B,
    )

    # Create SL order (not in-cross: below market for long)
    sl_params = TriggerOrderParameters(
        symbol=symbol,
        is_buy=False,
        trigger_px=str(float(market_price) * 0.95),
        trigger_type=OrderType.SL,
    )
    sl_response = await reya_tester.create_trigger_order(sl_params)
    logger.info(f"Created SL order: {sl_response.order_id}")

    # Create TP order (not in-cross: above market for long)
    tp_params = TriggerOrderParameters(
        symbol=symbol,
        is_buy=False,
        trigger_px=str(float(market_price) * 1.05),
        trigger_type=OrderType.TP,
    )
    tp_response = await reya_tester.create_trigger_order(tp_params)
    logger.info(f"Created TP order: {tp_response.order_id}")

    await reya_tester.wait_for_order_creation(order_id=sl_response.order_id, timeout=10)
    await reya_tester.wait_for_order_creation(order_id=tp_response.order_id, timeout=10)

    # Flip position by selling 2x (0.01 long -> 0.01 short)
    async with reya_tester.perp_trade() as ctx:
        flip_order_params = LimitOrderParameters(
            symbol=symbol,
            limit_px="0",
            is_buy=False,
            time_in_force=TimeInForce.IOC,
            qty="0.02",
            reduce_only=False,
        )
        await reya_tester.create_limit_order(flip_order_params)
        expected_flip = limit_order_params_to_order(flip_order_params, reya_tester.account_id)
        result = await ctx.wait_for_execution(expected_flip, expected_qty="0.02")
        logger.info(f"Position flipped with trade seq={result.sequence_number}")

    # Verify position is now short
    await reya_tester.check_position(
        symbol=symbol,
        expected_exchange_id=REYA_DEX_ID,
        expected_account_id=reya_tester.account_id,
        expected_qty=qty,
        expected_side=Side.A,
    )

    # Verify both SL and TP orders are cancelled
    await reya_tester.wait_for_order_state(sl_response.order_id, OrderStatus.CANCELLED, timeout=10)
    await reya_tester.wait_for_order_state(tp_response.order_id, OrderStatus.CANCELLED, timeout=10)
    await reya_tester.check_no_open_orders()
    logger.info("✅ SL and TP orders cancelled when position was flipped")


@pytest.mark.asyncio
async def test_sl_execution_cancels_tp(reya_tester: ReyaTester):
    """SL executes (in-cross) and cancels the TP order.
    
    Setup: LONG position with both SL and TP orders
    SL trigger: At entry price (in-cross for long SL)
    TP trigger: Above market (not in-cross)
    
    Verifies:
    1. SL order executes correctly when in-cross
    2. Trade is consistent between REST and WS (same sequence number)
    3. TP order gets cancelled when position closes
    """
    symbol = "ETHRUSDPERP"
    market_price = await reya_tester.get_current_price()
    qty = "0.01"

    # SETUP: Create long position
    async with reya_tester.perp_trade() as ctx:
        limit_order_params = LimitOrderParameters(
            symbol=symbol,
            limit_px=str(float(market_price) * 1.01),
            is_buy=True,
            time_in_force=TimeInForce.IOC,
            qty=qty,
            reduce_only=False,
        )
        await reya_tester.create_limit_order(limit_order_params)
        expected_order = limit_order_params_to_order(limit_order_params, reya_tester.account_id)
        result = await ctx.wait_for_execution(expected_order)
        logger.info(f"Position created with trade seq={result.sequence_number}")

    await reya_tester.check_no_open_orders()

    # RETRY LOOP: CO bot may be slow
    last_error: Exception | None = None
    for attempt in range(1, CO_MAX_RETRIES + 1):
        logger.info(f"🔄 SL execution attempt {attempt}/{CO_MAX_RETRIES}")
        
        # Verify position exists before placing CO
        if await reya_tester.data.position(symbol) is None:
            raise AssertionError(f"Position closed before placing SL (attempt {attempt})")
        
        async with reya_tester.perp_trade() as ctx:
            # Create SL order (in cross - should trigger)
            sl_params = TriggerOrderParameters(
                symbol=symbol,
                is_buy=False,
                trigger_px=str(float(market_price) * 1.01),
                trigger_type=OrderType.SL,
            )
            sl_order = await reya_tester.create_trigger_order(sl_params)
            logger.info(f"Created SL order: {sl_order.order_id}")

            # Create TP order (not in cross - should be cancelled when SL executes)
            tp_params = TriggerOrderParameters(
                symbol=symbol,
                is_buy=False,
                trigger_px=str(float(market_price) * 1.10),
                trigger_type=OrderType.TP,
            )
            tp_order = await reya_tester.create_trigger_order(tp_params)
            logger.info(f"Created TP order: {tp_order.order_id}")

            try:
                # Wait for SL execution
                expected_sl = trigger_order_params_to_order(sl_params, reya_tester.account_id)
                result = await ctx.wait_for_closing_execution(expected_sl, qty, timeout=CO_TIMEOUT_PER_ATTEMPT)
                logger.info(f"✅ SL executed with trade seq={result.sequence_number}")
                
                await reya_tester.check_order_execution(result.rest_execution, expected_sl, qty)
                await reya_tester.check_position_not_open(symbol)

                # Verify TP order gets cancelled
                await reya_tester.wait_for_order_state(tp_order.order_id, OrderStatus.CANCELLED, timeout=10)
                await reya_tester.check_no_open_orders()
                logger.info("✅ TP order cancelled when SL executed")
                return  # SUCCESS
                
            except RuntimeError as e:
                last_error = e
                logger.warning(f"⚠️ Attempt {attempt} failed: {e}")
                await _cancel_order_if_open(reya_tester, sl_order.order_id)
                await _cancel_order_if_open(reya_tester, tp_order.order_id)
                continue
    
    raise AssertionError(f"SL order failed after {CO_MAX_RETRIES} attempts. Last error: {last_error}")


@pytest.mark.asyncio
async def test_tp_execution_cancels_sl(reya_tester: ReyaTester):
    """TP executes (in-cross) and cancels the SL order.
    
    Setup: LONG position with both SL and TP orders
    SL trigger: Below market (not in-cross)
    TP trigger: Below market (in-cross for long TP)
    
    Verifies:
    1. TP order executes correctly when in-cross
    2. Trade is consistent between REST and WS (same sequence number)
    3. SL order gets cancelled when position closes
    """
    symbol = "ETHRUSDPERP"
    market_price = await reya_tester.get_current_price()
    qty = "0.01"

    # SETUP: Create long position
    async with reya_tester.perp_trade() as ctx:
        limit_order_params = LimitOrderParameters(
            symbol=symbol,
            limit_px=str(float(market_price) * 1.01),
            is_buy=True,
            time_in_force=TimeInForce.IOC,
            qty=qty,
            reduce_only=False,
        )
        await reya_tester.create_limit_order(limit_order_params)
        expected_order = limit_order_params_to_order(limit_order_params, reya_tester.account_id)
        result = await ctx.wait_for_execution(expected_order)
        logger.info(f"Position created with trade seq={result.sequence_number}")

    await reya_tester.check_no_open_orders()

    # RETRY LOOP: CO bot may be slow
    last_error: Exception | None = None
    for attempt in range(1, CO_MAX_RETRIES + 1):
        logger.info(f"🔄 TP execution attempt {attempt}/{CO_MAX_RETRIES}")
        
        # Verify position exists before placing CO
        if await reya_tester.data.position(symbol) is None:
            raise AssertionError(f"Position closed before placing TP (attempt {attempt})")
        
        async with reya_tester.perp_trade() as ctx:
            # Create SL order (not in cross - should be cancelled when TP executes)
            sl_params = TriggerOrderParameters(
                symbol=symbol,
                is_buy=False,
                trigger_px=str(float(market_price) * 0.90),
                trigger_type=OrderType.SL,
            )
            sl_order = await reya_tester.create_trigger_order(sl_params)
            logger.info(f"Created SL order: {sl_order.order_id}")

            # Create TP order (in cross - should trigger)
            tp_params = TriggerOrderParameters(
                symbol=symbol,
                is_buy=False,
                trigger_px=str(float(market_price) * 0.99),
                trigger_type=OrderType.TP,
            )
            tp_order = await reya_tester.create_trigger_order(tp_params)
            logger.info(f"Created TP order: {tp_order.order_id}")

            try:
                # Wait for TP execution
                expected_tp = trigger_order_params_to_order(tp_params, reya_tester.account_id)
                result = await ctx.wait_for_closing_execution(expected_tp, qty, timeout=CO_TIMEOUT_PER_ATTEMPT)
                logger.info(f"✅ TP executed with trade seq={result.sequence_number}")
                
                await reya_tester.check_order_execution(result.rest_execution, expected_tp, qty)
                await reya_tester.check_position_not_open(symbol)

                # Verify SL order gets cancelled
                await reya_tester.wait_for_order_state(sl_order.order_id, OrderStatus.CANCELLED, timeout=10)
                await reya_tester.check_no_open_orders()
                logger.info("✅ SL order cancelled when TP executed")
                return  # SUCCESS
                
            except RuntimeError as e:
                last_error = e
                logger.warning(f"⚠️ Attempt {attempt} failed: {e}")
                await _cancel_order_if_open(reya_tester, sl_order.order_id)
                await _cancel_order_if_open(reya_tester, tp_order.order_id)
                continue
    
    raise AssertionError(f"TP order failed after {CO_MAX_RETRIES} attempts. Last error: {last_error}")
