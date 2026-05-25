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
from tests.helpers.liquidity_detector import skip_if_external_liquidity
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

    # Skip if an external MM is on the book — the maker's −1% sell would
    # cross any bid within the ±5% circuit-breaker band and never rest, so
    # the IOC taker would have nothing to match against. Mirrors the spot
    # maker/taker e2e test in `tests/test_spot/test_maker_taker_matching.py`.
    await skip_if_external_liquidity(
        perp_taker_tester.data,
        PERP_SYMBOL,
        market_price,
        reason_prefix="test_perp_ioc_taker_buy_matches_maker_sell",
    )

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
async def test_perp_reduce_only_rejected_without_position(
    perp_maker_tester: ReyaTester,
    perp_taker_tester: ReyaTester,
) -> None:
    """``reduce_only=True`` IOC must not open a fresh position from zero.

    Enforced on-chain in reya-network ``orders-gateway/src/libraries/
    ExecutePartialFill.sol:159-174``: when ``accountOrder.reduceOnly`` is
    set, ``ExecutePartialFill`` reads the taker's live perp base from
    ``IPassivePerpInformationModule.getUpdatedPositionInfo`` and reverts
    with ``Errors.ReduceOnlyConditionFailed`` if the base is zero
    (``orders-gateway/src/libraries/execute-order-types/Utils.sol:46-51``).
    The ME currently has no reduce-only logic — the proto carries the
    flag through and on-chain settlement is the enforcement layer. (An ME
    pre-check is in development; once it lands races narrow but on-chain
    remains authoritative.)

    To deterministically exercise the on-chain check, we place a maker
    SELL at oracle*1.04 *before* submitting the taker IOC BUY at
    oracle*1.05. Without this setup the test would silently pass via
    the ME's CANCELLED-no-counterparty branch whenever no external ask
    liquidity is reachable — never actually verifying the invariant.

    Why oracle*1.04 for the maker:
      - below taker IOC limit (oracle*1.05) so they can cross
      - well above any sane external bid (bids sit below mid) so the
        maker won't be crossed by external flow before the taker arrives
      - inside the ±5% CB so the ME accepts it

    If external asks at lower prices exist, the IOC will hit those first
    (price-time priority) and our maker just rests until cleanup. Either
    path reaches chain.

    If this test ever sees ``FILLED``/``exec_qty>0``, on-chain
    ``positionBase`` was non-zero at fill time. Two likely causes:

      1. Test-isolation bug: ``perp_flatten_between_tests`` left chain
         debris that the API view hasn't caught up on
         (``check.position_not_open`` passes from API state but chain
         truth still has a residual base from a prior test).
      2. Real on-chain regression: reduce-only check skipped or position
         lookup is wrong.

    The diagnostic logs below print prior taker executions and the
    resulting position so a reviewer can tell the two apart from CI logs.
    """
    # Diagnostic snapshot pre-submit (see docstring for triage flow)
    pre_position = await perp_taker_tester.data.position(PERP_SYMBOL)
    pre_last_exec = await perp_taker_tester.get_last_wallet_perp_execution()
    logger.info(
        "🔍 reduce_only diagnostic (pre-submit): api_position=%s, last_exec=%s",
        pre_position,
        f"seq={pre_last_exec.sequence_number}, sym={pre_last_exec.symbol}, "
        f"qty={pre_last_exec.qty}, side={pre_last_exec.side}"
        if pre_last_exec is not None
        else "none",
    )

    await perp_taker_tester.check.position_not_open(PERP_SYMBOL)
    market_price = float(await perp_taker_tester.data.current_price(PERP_SYMBOL))

    # Guarantee the IOC has a counterparty so the on-chain check actually
    # runs — see docstring for rationale.
    maker_order_id = await perp_maker_tester.orders.create_limit(
        LimitOrderParameters(
            symbol=PERP_SYMBOL,
            is_buy=False,
            limit_px=str(round(market_price * 1.04, 2)),
            qty=PERP_QTY,
            time_in_force=TimeInForce.GTC,
        )
    )
    assert maker_order_id is not None
    await perp_maker_tester.wait.for_order_creation(order_id=maker_order_id)

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
        assert "reduce" in err or "position" in err or "400" in err, f"expected reduce-only rejection, got: {raised}"
        logger.info(f"✅ reduce_only without position rejected synchronously: {type(raised).__name__}")
    else:
        assert response is not None
        # Diagnostic snapshot: if response is FILLED, log the resulting position
        # so the next reader can tell whether chain truth grew from zero (real
        # regression) or merely tracked an already-non-zero chain position
        # (test-isolation bug — chain had debris before the order ran).
        if response.status == OrderStatus.FILLED or float(response.exec_qty or "0") > 0.0:
            post_position = await perp_taker_tester.data.position(PERP_SYMBOL)
            logger.warning(
                "⚠️  reduce_only diagnostic (post-fill): status=%s, exec_qty=%s, "
                "api_position_after=%s — see test docstring for triage",
                response.status,
                response.exec_qty,
                post_position,
            )

        # Under perpOB the order is accepted but the ME refuses to fill it.
        assert response.status in (
            OrderStatus.CANCELLED,
            OrderStatus.REJECTED,
        ), f"expected CANCELLED/REJECTED for reduce-only without position, got: {response.status}"
        assert (
            float(response.exec_qty or "0") == 0.0
        ), f"reduce-only without position should not fill, got exec_qty={response.exec_qty}"
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
