"""
IOC (Immediate-Or-Cancel) order tests parametrized over [spot, perp].

IOC orders execute immediately against available liquidity and the unfilled
portion is cancelled. Under v2.3.0 the matching-engine semantics are identical
for both market types.

These tests focus on order-lifecycle behaviour (fill / no-fill / partial-fill).
Asset-balance verification (spot-only — perp settles into positions, not asset
balances) lives in ``tests/test_spot/test_ioc_orders.py`` and ``test_balance_verification.py``.
"""

from __future__ import annotations

from typing import Union

import asyncio
from decimal import Decimal

import pytest

from sdk.open_api.models.order_status import OrderStatus
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api.models import LimitOrderParameters
from tests.helpers import ReyaTester
from tests.test_orderbook.conftest import PerpTestConfig
from tests.test_spot.spot_config import SpotTestConfig


@pytest.mark.asyncio
async def test_ioc_full_fill_against_resting_maker(
    market_config: Union[SpotTestConfig, PerpTestConfig],
    market_type: str,
    maker: ReyaTester,
    taker: ReyaTester,
) -> None:
    """Maker rests a GTC, taker IOC at the crossing price → full fill, no resting taker."""
    await market_config.refresh_order_book(taker.data)
    await maker.orders.close_all(fail_if_none=False)
    await taker.orders.close_all(fail_if_none=False)

    if market_config.has_any_external_liquidity:
        pytest.skip(
            "external liquidity present — IOC fill could match externally instead of our maker"
        )

    cross_px = str(market_config.price(0.99))

    maker_params = LimitOrderParameters(
        symbol=market_config.symbol,
        is_buy=True,
        limit_px=cross_px,
        qty=market_config.min_qty,
        time_in_force=TimeInForce.GTC,
    )
    maker_order_id = await maker.orders.create_limit(maker_params)
    assert maker_order_id is not None
    await maker.wait.for_order_creation(maker_order_id)

    taker_params = LimitOrderParameters(
        symbol=market_config.symbol,
        is_buy=False,
        limit_px=cross_px,
        qty=market_config.min_qty,
        time_in_force=TimeInForce.IOC,
    )
    taker_order_id = await taker.orders.create_limit(taker_params)
    assert taker_order_id is not None

    # Allow the ME to process and the maker to flip out of OPEN state.
    await asyncio.sleep(0.5)
    open_orders = await maker.client.get_open_orders()
    open_ids = {o.order_id for o in open_orders if o.symbol == market_config.symbol}
    assert maker_order_id not in open_ids, f"[{market_type}] maker should be filled"
    assert taker_order_id not in open_ids, f"[{market_type}] IOC taker should not rest"


@pytest.mark.asyncio
async def test_ioc_no_liquidity_unfills_immediately(
    market_config: Union[SpotTestConfig, PerpTestConfig],
    market_type: str,
    taker: ReyaTester,
) -> None:
    """An IOC priced where it can't match → cancelled immediately without resting."""
    await market_config.refresh_order_book(taker.data)
    await taker.orders.close_all(fail_if_none=False)

    # If external bids exist at our extreme sell price, the test would match — skip.
    if market_config.has_usable_bid_liquidity:
        pytest.skip("external bid liquidity present — IOC sell at extreme price would match")

    safe_no_match_px = str(market_config.get_safe_no_match_sell_price())

    params = LimitOrderParameters(
        symbol=market_config.symbol,
        is_buy=False,
        limit_px=safe_no_match_px,
        qty=market_config.min_qty,
        time_in_force=TimeInForce.IOC,
    )
    order_id = await taker.orders.create_limit(params)
    assert order_id is not None

    await asyncio.sleep(0.5)
    open_orders = await taker.client.get_open_orders()
    open_ids = {o.order_id for o in open_orders if o.symbol == market_config.symbol}
    assert order_id not in open_ids, f"[{market_type}] unfilled IOC must not rest"


@pytest.mark.asyncio
async def test_ioc_partial_fill_when_maker_smaller(
    market_config: Union[SpotTestConfig, PerpTestConfig],
    market_type: str,
    maker: ReyaTester,
    taker: ReyaTester,
) -> None:
    """IOC for 2x maker qty against a single maker level → partial fill, IOC remainder dropped."""
    await market_config.refresh_order_book(taker.data)
    await maker.orders.close_all(fail_if_none=False)
    await taker.orders.close_all(fail_if_none=False)

    if market_config.has_any_external_liquidity:
        pytest.skip("external liquidity present — partial-fill assertion requires controlled book")

    cross_px = str(market_config.price(0.99))
    maker_qty = market_config.min_qty
    taker_qty = str(Decimal(maker_qty) * 2)

    maker_params = LimitOrderParameters(
        symbol=market_config.symbol,
        is_buy=True,
        limit_px=cross_px,
        qty=maker_qty,
        time_in_force=TimeInForce.GTC,
    )
    maker_order_id = await maker.orders.create_limit(maker_params)
    assert maker_order_id is not None
    await maker.wait.for_order_creation(maker_order_id)

    taker_params = LimitOrderParameters(
        symbol=market_config.symbol,
        is_buy=False,
        limit_px=cross_px,
        qty=taker_qty,
        time_in_force=TimeInForce.IOC,
    )
    taker_order_id = await taker.orders.create_limit(taker_params)
    assert taker_order_id is not None

    await asyncio.sleep(0.5)

    # Maker fully consumed; taker did not rest (IOC drops the remainder).
    open_orders = await maker.client.get_open_orders()
    open_ids = {o.order_id for o in open_orders if o.symbol == market_config.symbol}
    assert maker_order_id not in open_ids, f"[{market_type}] maker should be filled"

    open_orders_taker = await taker.client.get_open_orders()
    open_ids_taker = {o.order_id for o in open_orders_taker if o.symbol == market_config.symbol}
    assert taker_order_id not in open_ids_taker, f"[{market_type}] IOC remainder must not rest"
