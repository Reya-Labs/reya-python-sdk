"""
Self-match prevention EDGE choreographies parametrized over [spot, perp].

The basics (taker cancelled, maker untouched, cross-account matches fine)
live in test_self_match_prevention.py. This module covers the edge cases
that were historically spot-only (moved from
tests/spot/test_self_match_prevention.py and parametrized — the perp
book is a real second code path, and identifier/SMP interplay broke on perp
before):

- exact-price boundary (touch counts as a cross → SMP fires)
- non-crossing same-account orders coexist (price compatibility is checked
  BEFORE self-match, so the result is "no match", not "self-match")
- non-crossing same-account IOC cancels for NO-MATCH without touching the
  resting order
- qty relations: the taker is FULLY cancelled whether smaller or larger
  than the self maker (no partial self-fill)
- market-maker shape: multiple non-crossing levels on both sides from one
  account all rest
- a taker that would sweep multiple self-orders is cancelled outright
- partial fill against ANOTHER account followed by a would-be self-match
  cancels the remainder (the fill stands, the self-order is untouched)
- a same-account non-crossing pair still matches normally against a
  different account

All tests need a controlled (empty) book.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from sdk.open_api.models.order_status import OrderStatus
from tests.helpers import ReyaTester
from tests.helpers.builders import OrderBuilder
from tests.helpers.liquidity_detector import skip_if_external_config_liquidity
from tests.helpers.market_config import PerpTestConfig, SpotTestConfig
from tests.helpers.order_lifecycle import wait_for_taker_perp_execution
from tests.helpers.reya_tester import limit_order_params_to_order

EDGE_REASON = "Self-match edge choreographies need a controlled (empty) book."


def _no_execution_seen(tester: ReyaTester, market_type: str, symbol: str) -> bool:
    """True when no execution event for `symbol` has hit the tester's WS
    stores this test (the function-scoped fixtures clear WS state)."""
    if market_type == "spot":
        return tester.ws.last_spot_execution is None
    return tester.ws.perp_executions.find_last(lambda e: e.symbol == symbol) is None


async def _wait_for_taker_fill(taker: ReyaTester, market_type: str, order_id: str, params) -> None:
    """Market-agnostic 'taker order filled' wait."""
    if market_type == "spot":
        expected = limit_order_params_to_order(params, taker.account_id)
        await taker.wait.for_spot_execution(order_id, expected, timeout=10)
    else:
        await wait_for_taker_perp_execution(taker, order_id)


async def _rest_gtc(tester: ReyaTester, symbol: str, px: str, qty: str, is_buy: bool, market_type: str) -> str:
    params = OrderBuilder().symbol(symbol).side(is_buy).price(px).qty(qty).gtc().build()
    order_id = await tester.orders.create_limit(params)
    assert order_id is not None, f"[{market_type}] expected order_id"
    await tester.wait.for_order_creation(order_id)
    return order_id


async def _open_ids(tester: ReyaTester, symbol: str) -> set[str]:
    return {o.order_id for o in await tester.client.get_open_orders() if o.symbol == symbol}


@pytest.mark.asyncio
async def test_self_match_exact_price_boundary(
    market_config: SpotTestConfig | PerpTestConfig,
    market_type: str,
    maker: ReyaTester,
) -> None:
    """Buy and sell at the EXACT same price from one account: the touch counts
    as a cross, so self-match prevention cancels the taker."""
    await skip_if_external_config_liquidity(market_config, maker, EDGE_REASON)
    await maker.orders.close_all(fail_if_none=False)

    px = str(market_config.price(0.97))
    maker_order_id = await _rest_gtc(maker, market_config.symbol, px, market_config.min_qty, False, market_type)

    taker_params = OrderBuilder().symbol(market_config.symbol).buy().price(px).qty(market_config.min_qty).gtc().build()
    taker_order_id = await maker.orders.create_limit(taker_params)

    open_ids = await _open_ids(maker, market_config.symbol)
    assert taker_order_id not in open_ids, f"[{market_type}] taker must be cancelled at the touch"
    assert maker_order_id in open_ids, f"[{market_type}] maker must remain"
    assert _no_execution_seen(maker, market_type, market_config.symbol), f"[{market_type}] no self-fill"

    await maker.client.cancel_order(symbol=market_config.symbol, account_id=maker.account_id, order_id=maker_order_id)
    await maker.wait.for_order_state(maker_order_id, OrderStatus.CANCELLED)


@pytest.mark.asyncio
async def test_non_crossing_orders_no_self_match(
    market_config: SpotTestConfig | PerpTestConfig,
    market_type: str,
    maker: ReyaTester,
) -> None:
    """Non-crossing same-account orders coexist — price compatibility is
    checked BEFORE self-match, so both rest."""
    await skip_if_external_config_liquidity(market_config, maker, EDGE_REASON)
    await maker.orders.close_all(fail_if_none=False)

    sell_id = await _rest_gtc(
        maker, market_config.symbol, str(market_config.price(1.02)), market_config.min_qty, False, market_type
    )
    buy_id = await _rest_gtc(
        maker, market_config.symbol, str(market_config.price(0.99)), market_config.min_qty, True, market_type
    )

    open_ids = await _open_ids(maker, market_config.symbol)
    assert sell_id in open_ids, f"[{market_type}] non-crossing sell must rest"
    assert buy_id in open_ids, f"[{market_type}] non-crossing buy must rest"

    await maker.client.mass_cancel(symbol=market_config.symbol, account_id=maker.account_id)
    await maker.wait.for_order_state(sell_id, OrderStatus.CANCELLED)
    await maker.wait.for_order_state(buy_id, OrderStatus.CANCELLED)


@pytest.mark.asyncio
async def test_non_crossing_ioc_cancelled_no_match(
    market_config: SpotTestConfig | PerpTestConfig,
    market_type: str,
    maker: ReyaTester,
) -> None:
    """A non-crossing same-account IOC cancels for NO-MATCH (not self-match)
    and leaves the resting order untouched."""
    await skip_if_external_config_liquidity(market_config, maker, EDGE_REASON)
    await maker.orders.close_all(fail_if_none=False)

    sell_id = await _rest_gtc(
        maker, market_config.symbol, str(market_config.price(1.04)), market_config.min_qty, False, market_type
    )

    ioc_params = (
        OrderBuilder()
        .symbol(market_config.symbol)
        .buy()
        .price(str(market_config.price(0.96)))
        .qty(market_config.min_qty)
        .ioc()
        .build()
    )
    await maker.orders.create_limit(ioc_params)

    open_ids = await _open_ids(maker, market_config.symbol)
    assert sell_id in open_ids, f"[{market_type}] resting sell must survive the no-match IOC"
    assert _no_execution_seen(maker, market_type, market_config.symbol), f"[{market_type}] no execution"

    await maker.client.cancel_order(symbol=market_config.symbol, account_id=maker.account_id, order_id=sell_id)
    await maker.wait.for_order_state(sell_id, OrderStatus.CANCELLED)


@pytest.mark.asyncio
@pytest.mark.parametrize("taker_factor", [1, 2], ids=["smaller_taker", "larger_taker"])
async def test_self_match_taker_fully_cancelled_regardless_of_qty(
    market_config: SpotTestConfig | PerpTestConfig,
    market_type: str,
    maker: ReyaTester,
    taker_factor: int,
) -> None:
    """Self-match cancels the ENTIRE taker whether it is smaller than or
    larger than the resting self maker — there is never a partial self-fill
    and the maker's qty is untouched."""
    await skip_if_external_config_liquidity(market_config, maker, EDGE_REASON)
    await maker.orders.close_all(fail_if_none=False)

    maker_qty = str(Decimal(market_config.min_qty) * 2)
    taker_qty = str(Decimal(market_config.min_qty) * taker_factor)
    px = str(market_config.price(0.97))
    cross_px = str(round(market_config.price(0.97) * 1.01, 2))

    maker_order_id = await _rest_gtc(maker, market_config.symbol, px, maker_qty, False, market_type)

    taker_params = OrderBuilder().symbol(market_config.symbol).buy().price(cross_px).qty(taker_qty).gtc().build()
    taker_order_id = await maker.orders.create_limit(taker_params)

    open_orders = await maker.client.get_open_orders()
    open_ids = {o.order_id for o in open_orders if o.symbol == market_config.symbol}
    assert taker_order_id not in open_ids, f"[{market_type}] taker must be fully cancelled"
    assert maker_order_id in open_ids, f"[{market_type}] maker must remain"
    maker_order = next(o for o in open_orders if o.order_id == maker_order_id)
    assert maker_order.qty is not None
    assert Decimal(maker_order.qty) == Decimal(maker_qty), f"[{market_type}] maker qty must be untouched"
    assert _no_execution_seen(maker, market_type, market_config.symbol), f"[{market_type}] no self-fill"

    await maker.client.cancel_order(symbol=market_config.symbol, account_id=maker.account_id, order_id=maker_order_id)
    await maker.wait.for_order_state(maker_order_id, OrderStatus.CANCELLED)


@pytest.mark.asyncio
async def test_market_maker_multiple_non_crossing_levels(
    market_config: SpotTestConfig | PerpTestConfig,
    market_type: str,
    maker: ReyaTester,
) -> None:
    """One account quoting three non-crossing levels per side: all six orders
    rest (historically a problematic shape)."""
    await skip_if_external_config_liquidity(market_config, maker, EDGE_REASON)
    await maker.orders.close_all(fail_if_none=False)

    sell_prices = [round(market_config.oracle_price * (1.02 + i * 0.02), 2) for i in range(3)]
    buy_prices = [round(market_config.oracle_price * (0.98 - i * 0.02), 2) for i in range(3)]

    placed: list[str] = []
    for px in sell_prices:
        placed.append(await _rest_gtc(maker, market_config.symbol, str(px), market_config.min_qty, False, market_type))
    for px in buy_prices:
        placed.append(await _rest_gtc(maker, market_config.symbol, str(px), market_config.min_qty, True, market_type))

    open_ids = await _open_ids(maker, market_config.symbol)
    for order_id in placed:
        assert order_id in open_ids, f"[{market_type}] level {order_id} must rest"

    await maker.client.mass_cancel(symbol=market_config.symbol, account_id=maker.account_id)
    for order_id in placed:
        await maker.wait.for_order_state(order_id, OrderStatus.CANCELLED)


@pytest.mark.asyncio
async def test_multiple_self_matches_in_sequence(
    market_config: SpotTestConfig | PerpTestConfig,
    market_type: str,
    maker: ReyaTester,
) -> None:
    """A taker that would sweep THREE self-orders is cancelled outright on the
    first self-match; all makers remain."""
    await skip_if_external_config_liquidity(market_config, maker, EDGE_REASON)
    await maker.orders.close_all(fail_if_none=False)

    base = market_config.price(0.97)
    maker_ids = []
    for i in range(3):
        px = str(round(base * (1 + i * 0.01), 2))
        maker_ids.append(await _rest_gtc(maker, market_config.symbol, px, market_config.min_qty, False, market_type))

    sweep_px = str(round(base * 1.10, 2))
    sweep_qty = str(Decimal(market_config.min_qty) * 3)
    taker_params = OrderBuilder().symbol(market_config.symbol).buy().price(sweep_px).qty(sweep_qty).gtc().build()
    taker_order_id = await maker.orders.create_limit(taker_params)

    open_ids = await _open_ids(maker, market_config.symbol)
    assert taker_order_id not in open_ids, f"[{market_type}] sweeping taker must be cancelled"
    for order_id in maker_ids:
        assert order_id in open_ids, f"[{market_type}] maker {order_id} must remain"
    assert _no_execution_seen(maker, market_type, market_config.symbol), f"[{market_type}] no self-fill"

    await maker.client.mass_cancel(symbol=market_config.symbol, account_id=maker.account_id)
    for order_id in maker_ids:
        await maker.wait.for_order_state(order_id, OrderStatus.CANCELLED)


@pytest.mark.asyncio
async def test_partial_fill_then_self_match_cancels_remainder(
    market_config: SpotTestConfig | PerpTestConfig,
    market_type: str,
    maker: ReyaTester,
    taker: ReyaTester,
) -> None:
    """Taker fills against ANOTHER account first, then would self-match — the
    fill stands, the remainder is cancelled, the self-order is untouched.

    On perp this drives a real fill mid-sequence (settlement path engaged);
    the perp fixtures restore both accounts' position deltas afterwards.
    """
    await skip_if_external_config_liquidity(market_config, maker, EDGE_REASON)
    await maker.orders.close_all(fail_if_none=False)
    await taker.orders.close_all(fail_if_none=False)

    px = str(market_config.price(0.97))
    fill_qty = market_config.min_qty
    sweep_qty = str(Decimal(fill_qty) * 2)

    # Account A (maker fixture) rests the bid that WILL fill.
    account_a_buy = await _rest_gtc(maker, market_config.symbol, px, fill_qty, True, market_type)
    # Account B (taker fixture) rests its own bid at the same price.
    account_b_buy = await _rest_gtc(taker, market_config.symbol, px, fill_qty, True, market_type)

    # Account B sells 2x: leg 1 fills A's bid (FIFO: A rested first), leg 2
    # would hit B's own bid → self-match → remainder cancelled.
    sell_params = OrderBuilder().symbol(market_config.symbol).sell().price(px).qty(sweep_qty).gtc().build()
    account_b_sell = await taker.orders.create_limit(sell_params)
    assert account_b_sell is not None

    await _wait_for_taker_fill(taker, market_type, account_b_sell, sell_params)
    await maker.wait.for_order_state(account_a_buy, OrderStatus.FILLED, timeout=10)

    taker_open = await _open_ids(taker, market_config.symbol)
    assert account_b_sell not in taker_open, f"[{market_type}] remainder must be cancelled after self-match"
    assert account_b_buy in taker_open, f"[{market_type}] B's own bid must be untouched"
    open_orders = await taker.client.get_open_orders()
    b_buy = next(o for o in open_orders if o.order_id == account_b_buy)
    assert b_buy.qty is not None
    assert Decimal(b_buy.qty) == Decimal(fill_qty), f"[{market_type}] B's bid qty must be unchanged"

    await taker.client.cancel_order(symbol=market_config.symbol, account_id=taker.account_id, order_id=account_b_buy)
    await taker.wait.for_order_state(account_b_buy, OrderStatus.CANCELLED)


@pytest.mark.asyncio
async def test_non_crossing_orders_can_match_other_accounts(
    market_config: SpotTestConfig | PerpTestConfig,
    market_type: str,
    maker: ReyaTester,
    taker: ReyaTester,
) -> None:
    """A same-account non-crossing pair still matches normally against a
    DIFFERENT account: the crossed side fills, the far side keeps resting."""
    await skip_if_external_config_liquidity(market_config, maker, EDGE_REASON)
    await maker.orders.close_all(fail_if_none=False)
    await taker.orders.close_all(fail_if_none=False)

    a_sell = await _rest_gtc(
        maker, market_config.symbol, str(market_config.price(1.04)), market_config.min_qty, False, market_type
    )
    a_buy = await _rest_gtc(
        maker, market_config.symbol, str(market_config.price(0.97)), market_config.min_qty, True, market_type
    )

    ioc_params = (
        OrderBuilder()
        .symbol(market_config.symbol)
        .sell()
        .price(str(market_config.price(0.96)))
        .qty(market_config.min_qty)
        .ioc()
        .build()
    )
    taker_order_id = await taker.orders.create_limit(ioc_params)
    assert taker_order_id is not None

    await _wait_for_taker_fill(taker, market_type, taker_order_id, ioc_params)
    await maker.wait.for_order_state(a_buy, OrderStatus.FILLED, timeout=10)

    open_ids = await _open_ids(maker, market_config.symbol)
    assert a_sell in open_ids, f"[{market_type}] the non-crossed sell must keep resting"

    await maker.client.cancel_order(symbol=market_config.symbol, account_id=maker.account_id, order_id=a_sell)
    await maker.wait.for_order_state(a_sell, OrderStatus.CANCELLED)
