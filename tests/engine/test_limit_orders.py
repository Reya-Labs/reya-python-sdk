"""
Shared limit-order lifecycle tests parametrized over [spot, perp].

Under v2.3.0 both market types route through the same matching engine, so a
single test body verifies the place→fill→position-or-balance flow for both.
Spot-only behaviours (auto-exchange busts) live in tests/spot/; perp-only
behaviours (triggers, funding, positions) live in tests/perp/.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from sdk.open_api.models.order_status import OrderStatus
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api.models import LimitOrderParameters
from tests.helpers import ReyaTester
from tests.helpers.market_config import PerpTestConfig, SpotTestConfig


async def _rest_gtc_buy(
    maker: ReyaTester, market_config: SpotTestConfig | PerpTestConfig, px: str, market_type: str
) -> str:
    params = LimitOrderParameters(
        symbol=market_config.symbol,
        is_buy=True,
        limit_px=px,
        qty=market_config.min_qty,
        time_in_force=TimeInForce.GTC,
    )
    order_id = await maker.orders.create_limit(params)
    assert order_id is not None, f"[{market_type}] expected order_id"
    await maker.wait.for_order_creation(order_id)
    return order_id


async def _ioc_sell(taker: ReyaTester, market_config: SpotTestConfig | PerpTestConfig, px: str, qty: str) -> None:
    await taker.orders.create_limit(
        LimitOrderParameters(
            symbol=market_config.symbol,
            is_buy=False,
            limit_px=px,
            qty=qty,
            time_in_force=TimeInForce.IOC,
        )
    )


@pytest.mark.asyncio
async def test_gtc_place_and_cancel(
    market_config: SpotTestConfig | PerpTestConfig,
    market_type: str,
    maker: ReyaTester,
) -> None:
    """A GTC limit order placed far from market is reachable via REST + cancellable."""
    safe_buy_px = str(round(market_config.oracle_price * 0.5, 2))

    params = LimitOrderParameters(
        symbol=market_config.symbol,
        is_buy=True,
        limit_px=safe_buy_px,
        qty=market_config.min_qty,
        time_in_force=TimeInForce.GTC,
    )
    response = await maker.client.create_limit_order(params)
    assert response.order_id is not None, f"[{market_type}] no order_id in response"

    open_order = await maker.data.open_order(response.order_id)
    assert open_order is not None, f"[{market_type}] order not visible via REST after placement"
    assert open_order.status == OrderStatus.OPEN

    cancel_response = await maker.client.cancel_order(
        symbol=market_config.symbol,
        account_id=maker.account_id,
        order_id=response.order_id,
    )
    assert cancel_response is not None

    await maker.wait.for_order_state(response.order_id, OrderStatus.CANCELLED)


@pytest.mark.asyncio
async def test_gtc_price_time_priority_fifo(
    market_config: SpotTestConfig | PerpTestConfig,
    market_type: str,
    maker: ReyaTester,
    taker: ReyaTester,
) -> None:
    """Two same-price GTC buys fill in arrival order: a taker sell sized to
    exactly one order fills the FIRST one; the second keeps resting.

    Previously pinned only on spot (tests/spot/test_gtc_orders.py) —
    the perp book's price-time ordering was asserted nowhere e2e.
    """
    await market_config.refresh_order_book(taker.data)
    await maker.orders.close_all(fail_if_none=False)
    await taker.orders.close_all(fail_if_none=False)
    if market_config.has_any_external_liquidity:
        pytest.skip("external liquidity present — the taker could match externally instead of our makers")

    level_px = str(market_config.price(0.99))
    first_order_id = await _rest_gtc_buy(maker, market_config, level_px, market_type)
    await asyncio.sleep(0.05)
    second_order_id = await _rest_gtc_buy(maker, market_config, level_px, market_type)

    await _ioc_sell(taker, market_config, level_px, market_config.min_qty)

    await maker.wait.for_order_state(first_order_id, OrderStatus.FILLED, timeout=5)
    open_orders = await maker.client.get_open_orders()
    open_ids = {o.order_id for o in open_orders}
    assert second_order_id in open_ids, f"[{market_type}] second same-price order must keep resting (FIFO)"

    await maker.client.cancel_order(symbol=market_config.symbol, account_id=maker.account_id, order_id=second_order_id)
    await maker.wait.for_order_state(second_order_id, OrderStatus.CANCELLED)


@pytest.mark.asyncio
async def test_gtc_best_price_filled_first(
    market_config: SpotTestConfig | PerpTestConfig,
    market_type: str,
    maker: ReyaTester,
    taker: ReyaTester,
) -> None:
    """With bids at two levels, a crossing sell fills the BETTER (higher) bid
    first; the worse level keeps resting."""
    await market_config.refresh_order_book(taker.data)
    await maker.orders.close_all(fail_if_none=False)
    await taker.orders.close_all(fail_if_none=False)
    if market_config.has_any_external_liquidity:
        pytest.skip("external liquidity present — the taker could match externally instead of our makers")

    worse_px = str(market_config.price(0.97))
    better_px = str(market_config.price(0.99))
    worse_order_id = await _rest_gtc_buy(maker, market_config, worse_px, market_type)
    better_order_id = await _rest_gtc_buy(maker, market_config, better_px, market_type)

    # Sell limit at the worse price crosses BOTH levels; qty covers only one.
    await _ioc_sell(taker, market_config, worse_px, market_config.min_qty)

    await maker.wait.for_order_state(better_order_id, OrderStatus.FILLED, timeout=5)
    open_orders = await maker.client.get_open_orders()
    open_ids = {o.order_id for o in open_orders}
    assert worse_order_id in open_ids, f"[{market_type}] worse-priced order must keep resting (best price first)"

    await maker.client.cancel_order(symbol=market_config.symbol, account_id=maker.account_id, order_id=worse_order_id)
    await maker.wait.for_order_state(worse_order_id, OrderStatus.CANCELLED)


@pytest.mark.asyncio
async def test_mass_cancel_clears_open_orders(
    market_config: SpotTestConfig | PerpTestConfig,
    market_type: str,
    maker: ReyaTester,
) -> None:
    """Mass-cancel removes all open orders on a symbol (works on both spot and perp under v2.3.0)."""
    safe_buy_px = str(round(market_config.oracle_price * 0.5, 2))

    placed_ids = []
    for _ in range(2):
        params = LimitOrderParameters(
            symbol=market_config.symbol,
            is_buy=True,
            limit_px=safe_buy_px,
            qty=market_config.min_qty,
            time_in_force=TimeInForce.GTC,
        )
        response = await maker.client.create_limit_order(params)
        assert response.order_id is not None
        placed_ids.append(response.order_id)

    await maker.client.mass_cancel(
        symbol=market_config.symbol,
        account_id=maker.account_id,
    )

    for order_id in placed_ids:
        await maker.wait.for_order_state(order_id, OrderStatus.CANCELLED, timeout=10)
    # market_type is consumed by the parametrization; tag log lines so failures are easier to triage.
    _ = market_type


@pytest.mark.asyncio
async def test_gtc_partial_fill_remainder_rests(
    market_config: SpotTestConfig | PerpTestConfig,
    market_type: str,
    maker: ReyaTester,
    taker: ReyaTester,
) -> None:
    """A crossing GTC larger than the resting maker fills partially and the
    remainder RESTS on the book (unlike IOC, which drops it).

    Moved from tests/spot/test_gtc_orders.py and parametrized — the
    GTC-remainder-rests path was previously untested on perp.
    """
    await market_config.refresh_order_book(taker.data)
    await maker.orders.close_all(fail_if_none=False)
    await taker.orders.close_all(fail_if_none=False)
    if market_config.has_any_external_liquidity:
        pytest.skip("external liquidity present — the taker could match externally instead of our maker")

    cross_px = str(market_config.price(0.99))
    maker_order_id = await _rest_gtc_buy(maker, market_config, cross_px, market_type)

    taker_qty = str(Decimal(market_config.min_qty) * 2)
    taker_params = LimitOrderParameters(
        symbol=market_config.symbol,
        is_buy=False,
        limit_px=cross_px,
        qty=taker_qty,
        time_in_force=TimeInForce.GTC,
    )
    taker_order_id = await taker.orders.create_limit(taker_params)
    assert taker_order_id is not None

    await maker.wait.for_order_state(maker_order_id, OrderStatus.FILLED, timeout=5)

    open_orders = await taker.client.get_open_orders()
    taker_open = [o for o in open_orders if o.order_id == taker_order_id]
    assert len(taker_open) == 1, f"[{market_type}] GTC remainder must rest on the book"

    await taker.client.cancel_order(symbol=market_config.symbol, account_id=taker.account_id, order_id=taker_order_id)
    await taker.wait.for_order_state(taker_order_id, OrderStatus.CANCELLED)
