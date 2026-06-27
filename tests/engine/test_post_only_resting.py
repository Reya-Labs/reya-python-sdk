"""
Post-only resting-path tests parametrized over [spot, perp] — live e2e.

`postOnly` constrains ENTRY only: a post-only GTC that does not cross rests
like any other GTC — the flag reads back True via REST openOrders AND the WS
order event — the tightest legal placement (one tick away from the best
opposite price) is accepted, and once RESTING the maker fills normally when a
taker crosses it. The rejection half (would-cross / touch / triggers) lives in
tests/engine/test_post_only_rejection.py.

Only `test_resting_post_only_maker_fills_when_taken` produces a fill (min_qty
maker→taker every run), so the module wires the per-market settlement cleanup
(spot balance guard / perp baseline restore) via the autouse fixture.
"""

from decimal import Decimal

import pytest

from sdk.open_api.models.order_status import OrderStatus
from tests.engine.post_only_helpers import rest_gtc_at_price, wait_for_ws_order_event
from tests.helpers import ReyaTester
from tests.helpers.builders import OrderBuilder
from tests.helpers.liquidity_detector import skip_if_external_config_liquidity
from tests.helpers.market_config import PerpTestConfig, SpotTestConfig
from tests.helpers.order_lifecycle import assert_px_qty, wait_for_taker_execution
from tests.helpers.reya_tester import logger

pytestmark = [pytest.mark.e2e, pytest.mark.post_only]

RESTING_REASON = "Deterministic post-only placements need a controlled (empty) book."


@pytest.fixture(autouse=True)
def _settlement_cleanup(settlement_cleanup_guard):  # pylint: disable=unused-argument
    """`test_resting_post_only_maker_fills_when_taken` produces a fill, so wire
    the per-market settlement cleanup (spot balance guard / perp baseline
    restore)."""
    yield


@pytest.mark.asyncio
async def test_post_only_rests_away_from_book(
    market_config: SpotTestConfig | PerpTestConfig, market_type: str, maker: ReyaTester
) -> None:
    """A post-only GTC far from the touch rests OPEN; postOnly reads back True
    via REST openOrders AND the WS order event."""
    buy_px = str(market_config.price(0.50))
    order = await rest_gtc_at_price(
        maker, market_config.symbol, price=buy_px, qty=market_config.min_qty, is_buy=True, post_only=True
    )
    assert order.post_only is True, f"REST openOrders must read postOnly back as True: {order.post_only}"

    await wait_for_ws_order_event(maker, order.order_id)
    ws_order = maker.check.ws_order_change_received(
        order.order_id, expected_symbol=market_config.symbol, expected_status=OrderStatus.OPEN
    )
    assert ws_order.post_only is True, f"WS order event must carry postOnly=True: {ws_order.post_only}"
    logger.info(f"[{market_type}] ✅ post-only rested OPEN with postOnly=True via REST + WS: {order.order_id}")

    await maker.orders.cancel(order_id=order.order_id, symbol=market_config.symbol, account_id=maker.account_id)
    await maker.check.no_open_orders()


@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_post_only_one_tick_from_touch_rests(
    market_config: SpotTestConfig | PerpTestConfig, market_type: str, maker: ReyaTester, taker: ReyaTester
) -> None:
    """The tightest legal post-only placement — one tick away from the best
    opposite price — rests (only the touch itself counts as a cross). Tick size
    is per-market reference data, so this is pinned on both books."""
    await skip_if_external_config_liquidity(market_config, maker, RESTING_REASON)

    # Snap the ask to the tick grid so probe = ask - tick is also grid-valid.
    tick = Decimal(market_config.tick_size)
    oracle = Decimal(str(market_config.oracle_price))
    ask = (oracle * Decimal("1.01")) // tick * tick
    ask_px = str(ask)
    probe_px = str(ask - tick)

    counterparty = await rest_gtc_at_price(
        taker, market_config.symbol, price=ask_px, qty=market_config.min_qty, is_buy=False
    )

    try:
        order = await rest_gtc_at_price(
            maker, market_config.symbol, price=probe_px, qty=market_config.min_qty, is_buy=True, post_only=True
        )
        assert order.post_only is True, f"REST openOrders must read postOnly back as True: {order.post_only}"
        assert_px_qty(order, expected_px=probe_px, expected_qty=market_config.min_qty)
        logger.info(f"[{market_type}] ✅ post-only buy rested one tick ({tick}) below the best ask {ask_px}")

        untouched = await taker.data.open_order(counterparty.order_id)
        assert untouched is not None, "The opposite-side maker must stay resting (nothing crossed)"
    finally:
        await maker.orders.close_all(fail_if_none=False)
        await taker.orders.close_all(fail_if_none=False)


@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_resting_post_only_maker_fills_when_taken(
    market_config: SpotTestConfig | PerpTestConfig, market_type: str, maker: ReyaTester, taker: ReyaTester
) -> None:
    """post_only constrains ENTRY only: a RESTING post-only maker fills
    normally when a taker crosses it."""
    await skip_if_external_config_liquidity(market_config, maker, RESTING_REASON)
    queue_px = str(market_config.price(0.99))

    maker_order = await rest_gtc_at_price(
        maker, market_config.symbol, price=queue_px, qty=market_config.min_qty, is_buy=True, post_only=True
    )
    assert maker_order.post_only is True

    ioc = OrderBuilder().symbol(market_config.symbol).sell().price(queue_px).qty(market_config.min_qty).ioc().build()
    taker_order_id = await taker.orders.create_limit(ioc)
    assert taker_order_id is not None

    execution = await wait_for_taker_execution(taker, market_type, taker_order_id)
    assert str(execution.maker_order_id) == str(
        maker_order.order_id
    ), f"Taker filled maker {execution.maker_order_id}, expected the post-only maker {maker_order.order_id}"
    await maker.wait.for_order_state(maker_order.order_id, OrderStatus.FILLED, timeout=5)
    logger.info(f"[{market_type}] ✅ resting post-only maker filled normally when taken")

    await maker.check.no_open_orders()
    await taker.check.no_open_orders()
