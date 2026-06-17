"""
modifyOrder queue-priority tests parametrized over [spot, perp] — live e2e,
two accounts.

Queue rules under test (FIFO book):
- qty DOWN at an unchanged limitPx keeps the order's place in the queue,
- qty UP re-queues the order at the back of its level,
- any limitPx change re-queues (even when moved back to the original level).

Setup pattern: maker1 (`maker`) rests FIRST at price P, maker2 (`taker`) rests
SECOND at the same P. After modifying maker1, a taker IOC crosses with exactly
one maker's size and the FIRST fill's `makerOrderId` reveals who held
priority. The IOC account is chosen per test so the sized-to-one-maker cross
can never reach its own resting order (no self-match):
- qty-down: IOC from `taker` (fills maker1, never reaches taker's maker2),
- qty-up / px-change: IOC from `maker` (fills maker2 — now first — and is
  fully consumed before reaching maker's own re-queued maker1).

All three tests need an empty book (external liquidity would absorb the
crosses) and produce a fill, so they wire the per-market settlement cleanup
(spot balance guard / perp baseline restore) via the autouse fixture.
Previously spot-only; parametrizing closes the perp queue-priority gap.
"""

import time
from decimal import Decimal

import pytest

from sdk.open_api.models.order_status import OrderStatus
from tests.helpers import ReyaTester
from tests.helpers.builders import OrderBuilder
from tests.helpers.builders.order_builder import full_state_modify_params
from tests.helpers.liquidity_detector import skip_if_external_config_liquidity
from tests.helpers.market_config import PerpTestConfig, SpotTestConfig
from tests.helpers.order_lifecycle import rest_gtc, rest_gtt, wait_for_order_fields, wait_for_taker_execution
from tests.helpers.reya_tester import logger

pytestmark = [pytest.mark.e2e, pytest.mark.modify, pytest.mark.maker_taker]

PRIORITY_REASON = "Queue-priority assertions need a controlled (empty) book."


@pytest.fixture(autouse=True)
def _settlement_cleanup(settlement_cleanup_guard):  # pylint: disable=unused-argument
    """All three tests produce a fill, so wire the per-market settlement
    cleanup (spot balance guard / perp baseline restore)."""
    yield


async def _taker_sell(tester: ReyaTester, market_config: SpotTestConfig | PerpTestConfig, price: str) -> str:
    """IOC sell sized to exactly one maker (min_qty) at the queue price."""
    params = OrderBuilder().symbol(market_config.symbol).sell().price(price).qty(market_config.min_qty).ioc().build()
    order_id = await tester.orders.create_limit(params)
    assert order_id is not None
    return order_id


@pytest.mark.asyncio
async def test_qty_down_keeps_priority(
    market_config: SpotTestConfig | PerpTestConfig, market_type: str, maker: ReyaTester, taker: ReyaTester
) -> None:
    """qty DOWN @ same px keeps queue priority: the taker's first (only) fill
    is against the modified maker1, not maker2."""
    await skip_if_external_config_liquidity(market_config, maker, PRIORITY_REASON)
    queue_px = str(market_config.price(0.99))
    double_qty = str(Decimal(market_config.min_qty) * 2)

    maker1 = await rest_gtc(maker, market_config, price_multiplier=0.99, qty=double_qty, is_buy=True)
    maker2 = await rest_gtc(taker, market_config, price_multiplier=0.99, is_buy=True)

    response = await maker.client.modify_order(full_state_modify_params(maker1, qty=market_config.min_qty))
    assert response.order_id == maker1.order_id
    assert response.status == OrderStatus.OPEN
    logger.info(f"[{market_type}] maker1 {maker1.order_id} qty {maker1.qty} -> {market_config.min_qty} (same px)")

    taker_order_id = await _taker_sell(taker, market_config, price=queue_px)
    execution = await wait_for_taker_execution(taker, market_type, taker_order_id)
    assert str(execution.maker_order_id) == str(maker1.order_id), (
        f"[{market_type}] qty-down lost queue priority: taker filled maker {execution.maker_order_id}, "
        f"expected maker1 {maker1.order_id}"
    )
    logger.info(f"[{market_type}] ✅ qty-down kept priority: taker filled maker1 first")

    await maker.wait.for_order_state(maker1.order_id, OrderStatus.FILLED, timeout=5)
    await taker.orders.cancel(order_id=maker2.order_id, symbol=market_config.symbol, account_id=taker.account_id)
    await maker.check.no_open_orders()
    await taker.check.no_open_orders()


@pytest.mark.asyncio
async def test_qty_up_loses_priority(
    market_config: SpotTestConfig | PerpTestConfig, market_type: str, maker: ReyaTester, taker: ReyaTester
) -> None:
    """qty UP re-queues: maker2 becomes first, so the taker's first fill is
    against maker2."""
    await skip_if_external_config_liquidity(market_config, maker, PRIORITY_REASON)
    queue_px = str(market_config.price(0.99))
    double_qty = str(Decimal(market_config.min_qty) * 2)

    maker1 = await rest_gtc(maker, market_config, price_multiplier=0.99, is_buy=True)
    maker2 = await rest_gtc(taker, market_config, price_multiplier=0.99, is_buy=True)

    response = await maker.client.modify_order(full_state_modify_params(maker1, qty=double_qty))
    assert response.order_id == maker1.order_id
    assert response.status == OrderStatus.OPEN
    logger.info(f"[{market_type}] maker1 {maker1.order_id} qty {maker1.qty} -> {double_qty} (same px)")

    # IOC from `maker` (account A): sized to maker2's qty, fully consumed by
    # maker2 (now first), so it can never self-match A's re-queued maker1.
    taker_order_id = await _taker_sell(maker, market_config, price=queue_px)
    execution = await wait_for_taker_execution(maker, market_type, taker_order_id)
    assert str(execution.maker_order_id) == str(maker2.order_id), (
        f"[{market_type}] qty-up kept queue priority: taker filled maker {execution.maker_order_id}, "
        f"expected maker2 {maker2.order_id}"
    )
    logger.info(f"[{market_type}] ✅ qty-up lost priority: taker filled maker2 first")

    await taker.wait.for_order_state(maker2.order_id, OrderStatus.FILLED, timeout=5)
    await maker.orders.cancel(order_id=maker1.order_id, symbol=market_config.symbol, account_id=maker.account_id)
    await maker.check.no_open_orders()
    await taker.check.no_open_orders()


@pytest.mark.asyncio
async def test_expiry_only_modify_keeps_priority(
    market_config: SpotTestConfig | PerpTestConfig, market_type: str, maker: ReyaTester, taker: ReyaTester
) -> None:
    """An expiry-only modify on a GTT (px+qty unchanged) keeps queue priority:
    the in-place re-key preserves the FIFO spot, so the taker's first (only)
    fill is still against the modified maker1 (rested first), not maker2.
    Mirrors qty-down-keeps-priority but exercises the expiry-only in-place
    path (S3)."""
    await skip_if_external_config_liquidity(market_config, maker, PRIORITY_REASON)
    queue_px = str(market_config.price(0.99))
    expires_after = int(time.time()) + 3600

    maker1 = await rest_gtt(maker, market_config, expires_after=expires_after, price_multiplier=0.99, is_buy=True)
    maker2 = await rest_gtt(taker, market_config, expires_after=expires_after, price_multiplier=0.99, is_buy=True)

    new_expiry = int(time.time()) + 7200
    response = await maker.client.modify_order(full_state_modify_params(maker1, expires_after=new_expiry))
    assert response.order_id == maker1.order_id
    assert response.status == OrderStatus.OPEN
    await wait_for_order_fields(maker, maker1.order_id, expires_after=new_expiry)
    logger.info(f"[{market_type}] maker1 {maker1.order_id} expiry-only modify (px/qty unchanged)")

    # IOC from `taker`: sized to one maker (min_qty), fills maker1 (still first)
    # and never reaches taker's own maker2 (no self-match).
    taker_order_id = await _taker_sell(taker, market_config, price=queue_px)
    execution = await wait_for_taker_execution(taker, market_type, taker_order_id)
    assert str(execution.maker_order_id) == str(maker1.order_id), (
        f"[{market_type}] expiry-only modify lost queue priority: taker filled maker {execution.maker_order_id}, "
        f"expected maker1 {maker1.order_id}"
    )
    logger.info(f"[{market_type}] ✅ expiry-only modify kept priority: taker filled maker1 first")

    await maker.wait.for_order_state(maker1.order_id, OrderStatus.FILLED, timeout=5)
    await taker.orders.cancel(order_id=maker2.order_id, symbol=market_config.symbol, account_id=taker.account_id)
    await maker.check.no_open_orders()
    await taker.check.no_open_orders()


@pytest.mark.asyncio
async def test_px_change_loses_priority(
    market_config: SpotTestConfig | PerpTestConfig, market_type: str, maker: ReyaTester, taker: ReyaTester
) -> None:
    """Any px change re-queues — even moving away and straight back to the
    ORIGINAL level puts maker1 behind maker2."""
    await skip_if_external_config_liquidity(market_config, maker, PRIORITY_REASON)
    queue_px = str(market_config.price(0.99))
    away_px = str(market_config.price(0.97))

    maker1 = await rest_gtc(maker, market_config, price_multiplier=0.99, is_buy=True)
    maker2 = await rest_gtc(taker, market_config, price_multiplier=0.99, is_buy=True)

    response = await maker.client.modify_order(full_state_modify_params(maker1, limit_px=away_px))
    assert response.order_id == maker1.order_id
    # Poll for the moved state — the openOrders cache lags the modify response.
    moved = await wait_for_order_fields(maker, maker1.order_id, limit_px=away_px)
    response = await maker.client.modify_order(full_state_modify_params(moved, limit_px=queue_px))
    assert response.order_id == maker1.order_id
    logger.info(f"[{market_type}] maker1 {maker1.order_id} px {queue_px} -> {away_px} -> {queue_px} (round trip)")

    # IOC from `maker` (account A) — see module docstring for the self-match reasoning.
    taker_order_id = await _taker_sell(maker, market_config, price=queue_px)
    execution = await wait_for_taker_execution(maker, market_type, taker_order_id)
    assert str(execution.maker_order_id) == str(maker2.order_id), (
        f"[{market_type}] px round-trip kept queue priority: taker filled maker {execution.maker_order_id}, "
        f"expected maker2 {maker2.order_id}"
    )
    logger.info(f"[{market_type}] ✅ px change lost priority: taker filled maker2 first")

    await taker.wait.for_order_state(maker2.order_id, OrderStatus.FILLED, timeout=5)
    await maker.orders.cancel(order_id=maker1.order_id, symbol=market_config.symbol, account_id=maker.account_id)
    await maker.check.no_open_orders()
    await taker.check.no_open_orders()
