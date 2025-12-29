#!/usr/bin/env python3
"""Tests for perp position management edge cases (increase, decrease, partial close)."""

import pytest

from sdk.open_api.models.side import Side
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api.config import REYA_DEX_ID
from sdk.reya_rest_api.models import LimitOrderParameters
from tests.helpers import ReyaTester
from tests.helpers.reya_tester import limit_order_params_to_order, logger


@pytest.mark.asyncio
async def test_position_increase_long(reya_tester: ReyaTester):
    """Test increasing a long position by adding more"""
    symbol = "ETHRUSDPERP"

    await reya_tester.check_no_open_orders()
    await reya_tester.check_position_not_open(symbol)

    market_price = await reya_tester.get_current_price()
    initial_qty = "0.01"

    initial_order = LimitOrderParameters(
        symbol=symbol,
        is_buy=True,
        limit_px=str(float(market_price) * 1.1),
        qty=initial_qty,
        time_in_force=TimeInForce.IOC,
        reduce_only=False,
    )

    await reya_tester.create_limit_order(initial_order)
    expected_order = limit_order_params_to_order(initial_order, reya_tester.account_id)
    await reya_tester.wait_for_order_execution(expected_order)

    await reya_tester.check_position(
        symbol=symbol,
        expected_exchange_id=REYA_DEX_ID,
        expected_account_id=reya_tester.account_id,
        expected_qty=initial_qty,
        expected_side=Side.B,
    )

    add_qty = "0.01"
    add_order = LimitOrderParameters(
        symbol=symbol,
        is_buy=True,
        limit_px=str(float(market_price) * 1.1),
        qty=add_qty,
        time_in_force=TimeInForce.IOC,
        reduce_only=False,
    )

    await reya_tester.create_limit_order(add_order)
    expected_add_order = limit_order_params_to_order(add_order, reya_tester.account_id)
    await reya_tester.wait_for_order_execution(expected_add_order)

    expected_total_qty = str(float(initial_qty) + float(add_qty))
    await reya_tester.check_position(
        symbol=symbol,
        expected_exchange_id=REYA_DEX_ID,
        expected_account_id=reya_tester.account_id,
        expected_qty=expected_total_qty,
        expected_side=Side.B,
    )

    logger.info("✅ Position increase (long) test completed successfully")


@pytest.mark.asyncio
async def test_position_increase_short(reya_tester: ReyaTester):
    """Test increasing a short position by adding more"""
    symbol = "ETHRUSDPERP"

    await reya_tester.check_no_open_orders()
    await reya_tester.check_position_not_open(symbol)

    market_price = await reya_tester.get_current_price()
    initial_qty = "0.01"

    initial_order = LimitOrderParameters(
        symbol=symbol,
        is_buy=False,
        limit_px=str(float(market_price) * 0.9),
        qty=initial_qty,
        time_in_force=TimeInForce.IOC,
        reduce_only=False,
    )

    await reya_tester.create_limit_order(initial_order)
    expected_order = limit_order_params_to_order(initial_order, reya_tester.account_id)
    await reya_tester.wait_for_order_execution(expected_order)

    await reya_tester.check_position(
        symbol=symbol,
        expected_exchange_id=REYA_DEX_ID,
        expected_account_id=reya_tester.account_id,
        expected_qty=initial_qty,
        expected_side=Side.A,
    )

    add_qty = "0.01"
    add_order = LimitOrderParameters(
        symbol=symbol,
        is_buy=False,
        limit_px=str(float(market_price) * 0.9),
        qty=add_qty,
        time_in_force=TimeInForce.IOC,
        reduce_only=False,
    )

    await reya_tester.create_limit_order(add_order)
    expected_add_order = limit_order_params_to_order(add_order, reya_tester.account_id)
    await reya_tester.wait_for_order_execution(expected_add_order)

    expected_total_qty = str(float(initial_qty) + float(add_qty))
    await reya_tester.check_position(
        symbol=symbol,
        expected_exchange_id=REYA_DEX_ID,
        expected_account_id=reya_tester.account_id,
        expected_qty=expected_total_qty,
        expected_side=Side.A,
    )

    logger.info("✅ Position increase (short) test completed successfully")


@pytest.mark.asyncio
async def test_position_partial_close_long(reya_tester: ReyaTester):
    """Test partially closing a long position"""
    symbol = "ETHRUSDPERP"

    await reya_tester.check_no_open_orders()
    await reya_tester.check_position_not_open(symbol)

    market_price = await reya_tester.get_current_price()
    initial_qty = "0.02"

    initial_order = LimitOrderParameters(
        symbol=symbol,
        is_buy=True,
        limit_px=str(float(market_price) * 1.1),
        qty=initial_qty,
        time_in_force=TimeInForce.IOC,
        reduce_only=False,
    )

    await reya_tester.create_limit_order(initial_order)
    expected_order = limit_order_params_to_order(initial_order, reya_tester.account_id)
    await reya_tester.wait_for_order_execution(expected_order)

    await reya_tester.check_position(
        symbol=symbol,
        expected_exchange_id=REYA_DEX_ID,
        expected_account_id=reya_tester.account_id,
        expected_qty=initial_qty,
        expected_side=Side.B,
    )

    close_qty = "0.01"
    close_order = LimitOrderParameters(
        symbol=symbol,
        is_buy=False,
        limit_px=str(float(market_price) * 0.9),
        qty=close_qty,
        time_in_force=TimeInForce.IOC,
        reduce_only=True,
    )

    await reya_tester.create_limit_order(close_order)
    expected_close_order = limit_order_params_to_order(close_order, reya_tester.account_id)
    await reya_tester.wait_for_order_execution(expected_close_order)

    expected_remaining_qty = str(float(initial_qty) - float(close_qty))
    await reya_tester.check_position(
        symbol=symbol,
        expected_exchange_id=REYA_DEX_ID,
        expected_account_id=reya_tester.account_id,
        expected_qty=expected_remaining_qty,
        expected_side=Side.B,
    )

    logger.info("✅ Position partial close (long) test completed successfully")


@pytest.mark.asyncio
async def test_position_partial_close_short(reya_tester: ReyaTester):
    """Test partially closing a short position"""
    symbol = "ETHRUSDPERP"

    await reya_tester.check_no_open_orders()
    await reya_tester.check_position_not_open(symbol)

    market_price = await reya_tester.get_current_price()
    initial_qty = "0.02"

    initial_order = LimitOrderParameters(
        symbol=symbol,
        is_buy=False,
        limit_px=str(float(market_price) * 0.9),
        qty=initial_qty,
        time_in_force=TimeInForce.IOC,
        reduce_only=False,
    )

    await reya_tester.create_limit_order(initial_order)
    expected_order = limit_order_params_to_order(initial_order, reya_tester.account_id)
    await reya_tester.wait_for_order_execution(expected_order)

    await reya_tester.check_position(
        symbol=symbol,
        expected_exchange_id=REYA_DEX_ID,
        expected_account_id=reya_tester.account_id,
        expected_qty=initial_qty,
        expected_side=Side.A,
    )

    close_qty = "0.01"
    close_order = LimitOrderParameters(
        symbol=symbol,
        is_buy=True,
        limit_px=str(float(market_price) * 1.1),
        qty=close_qty,
        time_in_force=TimeInForce.IOC,
        reduce_only=True,
    )

    await reya_tester.create_limit_order(close_order)
    expected_close_order = limit_order_params_to_order(close_order, reya_tester.account_id)
    await reya_tester.wait_for_order_execution(expected_close_order)

    expected_remaining_qty = str(float(initial_qty) - float(close_qty))
    await reya_tester.check_position(
        symbol=symbol,
        expected_exchange_id=REYA_DEX_ID,
        expected_account_id=reya_tester.account_id,
        expected_qty=expected_remaining_qty,
        expected_side=Side.A,
    )

    logger.info("✅ Position partial close (short) test completed successfully")


@pytest.mark.asyncio
async def test_position_full_close_with_reduce_only(reya_tester: ReyaTester):
    """Test fully closing a position using reduce_only flag"""
    symbol = "ETHRUSDPERP"

    await reya_tester.check_no_open_orders()
    await reya_tester.check_position_not_open(symbol)

    market_price = await reya_tester.get_current_price()
    position_qty = "0.01"

    open_order = LimitOrderParameters(
        symbol=symbol,
        is_buy=True,
        limit_px=str(float(market_price) * 1.1),
        qty=position_qty,
        time_in_force=TimeInForce.IOC,
        reduce_only=False,
    )

    await reya_tester.create_limit_order(open_order)
    expected_open_order = limit_order_params_to_order(open_order, reya_tester.account_id)
    await reya_tester.wait_for_order_execution(expected_open_order)

    await reya_tester.check_position(
        symbol=symbol,
        expected_exchange_id=REYA_DEX_ID,
        expected_account_id=reya_tester.account_id,
        expected_qty=position_qty,
        expected_side=Side.B,
    )

    close_order = LimitOrderParameters(
        symbol=symbol,
        is_buy=False,
        limit_px=str(float(market_price) * 0.9),
        qty=position_qty,
        time_in_force=TimeInForce.IOC,
        reduce_only=True,
    )

    await reya_tester.create_limit_order(close_order)
    expected_close_order = limit_order_params_to_order(close_order, reya_tester.account_id)
    # Use wait_for_closing_order_execution since position will be fully closed
    await reya_tester.wait_for_closing_order_execution(expected_close_order, position_qty)

    await reya_tester.check_position_not_open(symbol)

    logger.info("✅ Position full close with reduce_only test completed successfully")


@pytest.mark.asyncio
async def test_position_decrease_without_reduce_only(reya_tester: ReyaTester):
    """Test decreasing a position without reduce_only flag (counter-trade)"""
    symbol = "ETHRUSDPERP"

    await reya_tester.check_no_open_orders()
    await reya_tester.check_position_not_open(symbol)

    market_price = await reya_tester.get_current_price()
    initial_qty = "0.02"

    open_order = LimitOrderParameters(
        symbol=symbol,
        is_buy=True,
        limit_px=str(float(market_price) * 1.1),
        qty=initial_qty,
        time_in_force=TimeInForce.IOC,
        reduce_only=False,
    )

    await reya_tester.create_limit_order(open_order)
    expected_open_order = limit_order_params_to_order(open_order, reya_tester.account_id)
    await reya_tester.wait_for_order_execution(expected_open_order)

    await reya_tester.check_position(
        symbol=symbol,
        expected_exchange_id=REYA_DEX_ID,
        expected_account_id=reya_tester.account_id,
        expected_qty=initial_qty,
        expected_side=Side.B,
    )

    counter_qty = "0.01"
    counter_order = LimitOrderParameters(
        symbol=symbol,
        is_buy=False,
        limit_px=str(float(market_price) * 0.9),
        qty=counter_qty,
        time_in_force=TimeInForce.IOC,
        reduce_only=False,
    )

    await reya_tester.create_limit_order(counter_order)
    expected_counter_order = limit_order_params_to_order(counter_order, reya_tester.account_id)
    await reya_tester.wait_for_order_execution(expected_counter_order)

    expected_remaining_qty = str(float(initial_qty) - float(counter_qty))
    await reya_tester.check_position(
        symbol=symbol,
        expected_exchange_id=REYA_DEX_ID,
        expected_account_id=reya_tester.account_id,
        expected_qty=expected_remaining_qty,
        expected_side=Side.B,
    )

    logger.info("✅ Position decrease without reduce_only test completed successfully")
