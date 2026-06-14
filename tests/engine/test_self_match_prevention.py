"""
Self-match prevention tests parametrized over [spot, perp].

The matching engine prevents an account from matching against itself: when an
incoming taker would cross with a resting order from the same account, the
TAKER is cancelled and the MAKER stays on the book. This holds regardless of
market type because both spot and perp share the same matching engine under
v2.3.0.

These tests need a controlled environment — if external liquidity exists at
the test price, the taker would match externally instead of triggering the
self-match path. We refresh the depth and skip when external liquidity is
present at crossing prices.
"""

from __future__ import annotations

import asyncio

import pytest

from sdk.open_api.models.order_status import OrderStatus
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api.models import LimitOrderParameters
from tests.helpers import ReyaTester
from tests.helpers.market_config import PerpTestConfig, SpotTestConfig
from tests.helpers.reya_tester import logger


async def _skip_if_external_liquidity(market_config: SpotTestConfig | PerpTestConfig, tester: ReyaTester) -> None:
    """Skip if external liquidity is on the book — would interfere with self-match assertions."""
    await market_config.refresh_order_book(tester.data)
    if market_config.has_any_external_liquidity:
        pytest.skip("external liquidity present — self-match tests need a controlled book")


@pytest.mark.asyncio
async def test_self_match_gtc_taker_sell_cancelled(
    market_config: SpotTestConfig | PerpTestConfig,
    market_type: str,
    maker: ReyaTester,
) -> None:
    """GTC buy + GTC sell crossing from SAME account → taker cancelled, maker stays."""
    await _skip_if_external_liquidity(market_config, maker)
    await maker.orders.close_all(fail_if_none=False)

    cross_price = str(market_config.price(0.97))

    # Maker buy at 97% of oracle.
    maker_params = LimitOrderParameters(
        symbol=market_config.symbol,
        is_buy=True,
        limit_px=cross_price,
        qty=market_config.min_qty,
        time_in_force=TimeInForce.GTC,
    )
    maker_order_id = await maker.orders.create_limit(maker_params)
    assert maker_order_id is not None
    await maker.wait.for_order_creation(maker_order_id)

    # Taker sell at the same price (crossing, same account → self-match).
    taker_params = LimitOrderParameters(
        symbol=market_config.symbol,
        is_buy=False,
        limit_px=cross_price,
        qty=market_config.min_qty,
        time_in_force=TimeInForce.GTC,
    )
    taker_order_id = await maker.orders.create_limit(taker_params)
    assert taker_order_id is not None

    # Allow ME to process.
    await asyncio.sleep(0.3)

    open_orders = await maker.client.get_open_orders()
    open_ids = {o.order_id for o in open_orders if o.symbol == market_config.symbol}

    assert taker_order_id not in open_ids, f"[{market_type}] taker should be CANCELLED on self-match"
    assert maker_order_id in open_ids, f"[{market_type}] maker should remain OPEN"
    logger.info(f"[{market_type}] ✅ self-match: taker cancelled, maker preserved")

    # Cleanup
    await maker.client.cancel_order(
        order_id=maker_order_id,
        symbol=market_config.symbol,
        account_id=maker.account_id,
    )
    await maker.wait.for_order_state(maker_order_id, OrderStatus.CANCELLED)


@pytest.mark.asyncio
async def test_self_match_ioc_taker_buy_cancelled(
    market_config: SpotTestConfig | PerpTestConfig,
    market_type: str,
    maker: ReyaTester,
) -> None:
    """GTC sell maker + IOC buy taker (same account, crossing) → IOC cancelled with no fill."""
    await _skip_if_external_liquidity(market_config, maker)
    await maker.orders.close_all(fail_if_none=False)

    cross_price = str(market_config.price(1.03))

    maker_params = LimitOrderParameters(
        symbol=market_config.symbol,
        is_buy=False,
        limit_px=cross_price,
        qty=market_config.min_qty,
        time_in_force=TimeInForce.GTC,
    )
    maker_order_id = await maker.orders.create_limit(maker_params)
    assert maker_order_id is not None
    await maker.wait.for_order_creation(maker_order_id)

    taker_params = LimitOrderParameters(
        symbol=market_config.symbol,
        is_buy=True,
        limit_px=cross_price,
        qty=market_config.min_qty,
        time_in_force=TimeInForce.IOC,
    )
    taker_order_id = await maker.orders.create_limit(taker_params)
    assert taker_order_id is not None

    await asyncio.sleep(0.3)

    open_orders = await maker.client.get_open_orders()
    open_ids = {o.order_id for o in open_orders if o.symbol == market_config.symbol}

    # IOC taker is gone (either cancelled by self-match or naturally not resting). Maker stays.
    assert taker_order_id not in open_ids, f"[{market_type}] IOC taker should not rest"
    assert maker_order_id in open_ids, f"[{market_type}] GTC maker should remain after self-match block"

    await maker.client.cancel_order(
        order_id=maker_order_id,
        symbol=market_config.symbol,
        account_id=maker.account_id,
    )


@pytest.mark.asyncio
async def test_cross_account_match_fills_normally(
    market_config: SpotTestConfig | PerpTestConfig,
    market_type: str,
    maker: ReyaTester,
    taker: ReyaTester,
) -> None:
    """Sanity: when maker and taker are different accounts, the match goes through."""
    await _skip_if_external_liquidity(market_config, maker)
    await maker.orders.close_all(fail_if_none=False)
    await taker.orders.close_all(fail_if_none=False)

    cross_price = str(market_config.price(0.99))

    maker_params = LimitOrderParameters(
        symbol=market_config.symbol,
        is_buy=True,
        limit_px=cross_price,
        qty=market_config.min_qty,
        time_in_force=TimeInForce.GTC,
    )
    maker_order_id = await maker.orders.create_limit(maker_params)
    assert maker_order_id is not None
    await maker.wait.for_order_creation(maker_order_id)

    taker_params = LimitOrderParameters(
        symbol=market_config.symbol,
        is_buy=False,
        limit_px=cross_price,
        qty=market_config.min_qty,
        time_in_force=TimeInForce.IOC,
    )
    taker_order_id = await taker.orders.create_limit(taker_params)
    assert taker_order_id is not None

    # Maker order should be FILLED (or at minimum gone from open orders).
    try:
        await maker.wait.for_order_state(maker_order_id, OrderStatus.FILLED, timeout=10)
    except RuntimeError:
        # Allow PARTIALLY_FILLED tolerant path
        open_orders = await maker.client.get_open_orders()
        open_ids = {o.order_id for o in open_orders if o.symbol == market_config.symbol}
        assert maker_order_id not in open_ids, f"[{market_type}] cross-account match should consume the maker"
