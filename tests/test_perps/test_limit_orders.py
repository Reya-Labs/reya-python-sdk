"""
Perp orderbook limit-order tests using the maker/taker pattern.

Under v2.3.0 every perp fill needs a counterparty on the opposite side. These
tests put a GTC resting order on the book via PERP_ACCOUNT_ID_1 (maker) and
hit it with an IOC from PERP_ACCOUNT_ID_2 (taker), exercising perp-specific
semantics that don't apply to spot:

- IOC matched against a real OB resting order produces a position on the taker
  side (whereas spot produces a balance change).
- ``reduce_only`` flag is perp-only; the API rejects it on spot.
- A GTC perp order rests on the book and is observable via
  ``GET /v2/wallet/{address}/openOrders``.

The shared place/cancel/match-in-isolation lifecycle for both market types
lives in tests/test_orderbook/; this module covers what's genuinely
perp-specific.
"""

from __future__ import annotations

import asyncio

import pytest

from sdk.open_api.exceptions import ApiException
from sdk.open_api.models.order_status import OrderStatus
from sdk.open_api.models.side import Side
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api.config import REYA_DEX_ID
from sdk.reya_rest_api.models import LimitOrderParameters
from tests.helpers import ReyaTester
from tests.helpers.reya_tester import logger

PERP_SYMBOL = "ETHRUSDPERP"
PERP_QTY = "0.01"


def _maker_buy_price(market_price: float) -> str:
    """Generous bid: maker willing to pay 1% above oracle so IOC sells from taker hit."""
    return str(round(market_price * 1.01, 2))


def _maker_sell_price(market_price: float) -> str:
    """Generous ask: maker willing to sell 1% below oracle so IOC buys from taker hit."""
    return str(round(market_price * 0.99, 2))


@pytest.mark.asyncio
async def test_perp_ioc_taker_buy_matches_maker_sell(
    perp_maker_tester: ReyaTester, perp_taker_tester: ReyaTester
) -> None:
    """Maker rests a GTC sell, taker IOC buys, taker accrues a long position."""
    market_price = float(await perp_taker_tester.data.current_price(PERP_SYMBOL))

    # Maker posts a sell order below market — taker IOC will lift it.
    maker_order_id = await perp_maker_tester.orders.create_limit(
        LimitOrderParameters(
            symbol=PERP_SYMBOL,
            is_buy=False,
            limit_px=_maker_sell_price(market_price),
            qty=PERP_QTY,
            time_in_force=TimeInForce.GTC,
        )
    )
    assert maker_order_id is not None, "maker GTC was not accepted"
    await perp_maker_tester.wait.for_order_creation(order_id=maker_order_id)

    # Taker IOC buy crosses against the maker.
    taker_order_id = await perp_taker_tester.orders.create_limit(
        LimitOrderParameters(
            symbol=PERP_SYMBOL,
            is_buy=True,
            limit_px=str(round(market_price * 1.05, 2)),  # cross all the way
            qty=PERP_QTY,
            time_in_force=TimeInForce.IOC,
            reduce_only=False,
        )
    )
    assert taker_order_id is not None

    # Taker now holds a long position of size PERP_QTY.
    await perp_taker_tester.check.position(
        symbol=PERP_SYMBOL,
        expected_exchange_id=REYA_DEX_ID,
        expected_account_id=perp_taker_tester.account_id,
        expected_qty=PERP_QTY,
        expected_side=Side.B,
    )


@pytest.mark.asyncio
async def test_perp_gtc_rests_on_book(perp_maker_tester: ReyaTester) -> None:
    """A GTC perp order placed away from market rests on the book and is queryable."""
    market_price = float(await perp_maker_tester.data.current_price(PERP_SYMBOL))
    safe_resting_price = str(round(market_price * 0.5, 2))  # far below market — won't match

    order_id = await perp_maker_tester.orders.create_limit(
        LimitOrderParameters(
            symbol=PERP_SYMBOL,
            is_buy=True,
            limit_px=safe_resting_price,
            qty=PERP_QTY,
            time_in_force=TimeInForce.GTC,
        )
    )
    assert order_id is not None

    # The API pod's `OrdersProvider` stream consumer (in
    # packages/common-backend/src/providers/redis-stream-reader.ts) calls
    # `XREAD BLOCK 5000` against the `{orders}:changes` Redis Stream, and
    # measurement showed the BLOCK isn't being woken up by new messages —
    # propagation to `/v2/wallet/{address}/openOrders` is clamped at the
    # 5,000 ms timeout. The 6 s retry budget below covers that worst case
    # with a small margin; once the underlying lag is fixed (off-chain
    # repo issue Reya-Labs/reya-off-chain-monorepo#2663) we can drop this
    # back to ~500 ms.
    open_order = None
    for _ in range(60):
        open_order = await perp_maker_tester.data.open_order(order_id)
        if open_order is not None:
            break
        await asyncio.sleep(0.1)
    assert open_order is not None, "GTC perp order should be visible in open orders"
    assert open_order.status == OrderStatus.OPEN
    assert open_order.symbol == PERP_SYMBOL


@pytest.mark.asyncio
async def test_perp_reduce_only_rejected_without_position(perp_taker_tester: ReyaTester) -> None:
    """``reduce_only=True`` IOC must not open a fresh position from zero.

    AMM-era behavior: API rejected with a 4xx at submission. PerpOB-era: the
    API accepts the order, the matching engine processes it and marks it
    CANCELLED (because there's no position to reduce). Either way, no
    position can form from a reduce-only-without-position IOC — that's the
    invariant being tested.
    """
    await perp_taker_tester.check.position_not_open(PERP_SYMBOL)
    market_price = float(await perp_taker_tester.data.current_price(PERP_SYMBOL))

    response = None
    raised: ApiException | None = None
    try:
        response = await perp_taker_tester.client.create_limit_order(
            LimitOrderParameters(
                symbol=PERP_SYMBOL,
                is_buy=True,
                limit_px=str(round(market_price * 1.05, 2)),
                qty=PERP_QTY,
                time_in_force=TimeInForce.IOC,
                reduce_only=True,
            )
        )
    except ApiException as e:
        raised = e

    if raised is not None:
        err = str(raised).lower()
        assert (
            "reduce" in err or "position" in err or "400" in err
        ), f"expected reduce-only rejection, got: {raised}"
        logger.info(f"✅ reduce_only without position rejected synchronously: {type(raised).__name__}")
    else:
        assert response is not None
        # Under perpOB the order is accepted but the ME refuses to fill it.
        assert response.status in (OrderStatus.CANCELLED, OrderStatus.REJECTED), (
            f"expected CANCELLED/REJECTED for reduce-only without position, got: {response.status}"
        )
        assert float(response.exec_qty or "0") == 0.0, (
            f"reduce-only without position should not fill, got exec_qty={response.exec_qty}"
        )
        logger.info(f"✅ reduce_only without position rejected by ME: status={response.status}")

    # Final invariant either way: no position formed.
    await asyncio.sleep(0.5)
    await perp_taker_tester.check.position_not_open(PERP_SYMBOL)


@pytest.mark.asyncio
async def test_perp_gtc_cancel_via_mass_cancel(perp_maker_tester: ReyaTester) -> None:
    """Mass-cancel works on perp markets under v2.3.0 (was spot-only pre-perpOB)."""
    market_price = float(await perp_maker_tester.data.current_price(PERP_SYMBOL))
    safe_buy_px = str(round(market_price * 0.5, 2))

    placed_ids = []
    for _ in range(2):
        order_id = await perp_maker_tester.orders.create_limit(
            LimitOrderParameters(
                symbol=PERP_SYMBOL,
                is_buy=True,
                limit_px=safe_buy_px,
                qty=PERP_QTY,
                time_in_force=TimeInForce.GTC,
            )
        )
        assert order_id is not None
        placed_ids.append(order_id)

    await perp_maker_tester.client.mass_cancel(
        symbol=PERP_SYMBOL,
        account_id=perp_maker_tester.account_id,
    )

    # Allow a moment for ME to propagate; then assert all cancelled.
    await asyncio.sleep(1.0)
    for order_id in placed_ids:
        await perp_maker_tester.wait.for_order_state(order_id, OrderStatus.CANCELLED)
