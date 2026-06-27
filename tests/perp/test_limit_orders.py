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
lives in tests/engine/; this module covers what's genuinely
perp-specific.
"""

from __future__ import annotations

import asyncio

import pytest

from sdk.open_api.exceptions import ApiException
from sdk.open_api.models.time_in_force import TimeInForce
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
async def test_perp_reduce_only_rejected_without_position(
    perp_maker_tester: ReyaTester,
    perp_taker_tester: ReyaTester,
) -> None:
    """``reduce_only=True`` IOC must not open a fresh position from zero.

    Settlement signal sourced from the WebSocket ``/executionBusts`` feed,
    NOT the REST ``createOrder`` response status. This is intentional in
    the off-chain design (confirmed by Daniel in
    Reya-Labs/reya-off-chain-monorepo#2575): the REST response status
    only signals "ME matched" — settlement failures (including on-chain
    ``ReduceOnlyConditionFailed`` reverts) surface exclusively on the
    bust + executions WS channels. A test that inspects only the REST
    status would have a false-positive on every reverted settlement,
    because the REST response will return ``FILLED`` even when the chain
    rolled back the fill.

    Enforced on-chain in reya-network ``orders-gateway/src/libraries/
    ExecutePartialFill.sol:159-174``: when ``accountOrder.reduceOnly`` is
    set, ``ExecutePartialFill`` reads the taker's live perp base from
    ``IPassivePerpInformationModule.getUpdatedPositionInfo`` and reverts
    with ``Errors.ReduceOnlyConditionFailed`` if the base is zero
    (``orders-gateway/src/libraries/execute-order-types/Utils.sol:46-51``).
    The ME currently has no reduce-only pre-check — the proto carries the
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

    Failure modes this test catches:
      - No bust event ever arrives (within ``BUST_TIMEOUT_S``) → on-chain
        reduce-only check didn't fire or the bust indexer is broken
      - Bust arrives but reason doesn't mention reduce-only/position →
        a different revert reached chain (e.g. nonce, signature) — the
        test's invariant isn't being exercised
      - Taker position ends up non-zero → chain DID settle the fill (the
        reduce-only check was skipped) — this is the headline regression
        the test exists to catch
    """
    market_price = float(await perp_taker_tester.data.current_price(PERP_SYMBOL))

    # Skip if an external MM is on the book: the setup maker SELL at oracle*1.04
    # below would cross any external bid above that price and fill immediately
    # instead of resting, so the reduce-only IOC would settle against external
    # liquidity rather than exercise the zero-position on-chain check (and that
    # settlement currently 500s). Mirrors `test_perp_ioc_taker_buy_matches_maker_sell`
    # and the other controlled-book tests that self-skip on a dirty devnet book.
    await skip_if_external_liquidity(
        perp_taker_tester.data,
        PERP_SYMBOL,
        market_price,
        reason_prefix="test_perp_reduce_only_rejected_without_position",
    )

    # Absolute-state test: the on-chain reduce-only-from-zero revert only
    # fires when the taker's base is actually zero. With any pre-existing
    # short, a reduce-only BUY is legitimately valid (it reduces), so the
    # invariant under test can't be exercised — skip rather than fail.
    baseline = await perp_taker_tester.positions.signed_qty(PERP_SYMBOL)
    if baseline != 0:
        pytest.skip(f"account not flat (baseline {baseline}) — absolute-state test (reduce-only-from-zero)")

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

    # Submit the reduce-only IOC. Two paths the on-chain reduce-only revert
    # can surface, both acceptable for proving the invariant:
    #
    #   1. REST FILLED + WS bust event (the design intent — REST status
    #      conveys "ME matched" only, settlement signal lives on the
    #      `/executionBusts` channel)
    #   2. REST 4xx with the decoded reason in the body (the transitional
    #      consumer-fix behavior — gets reverted to path 1 eventually)
    #
    # The test accepts either path because we want it green during the
    # deploy-timing window when devnet may be running either version of
    # the consumer. Once the revert has propagated everywhere and we're
    # confident path 2 is gone, the try/except can be removed and the
    # test simplifies to just the path-1 branch.
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
        assert response is not None
        assert response.order_id is not None, "ME should have returned an order_id"
        logger.info(
            "REST createOrder response (status not authoritative): "
            f"status={response.status}, order_id={response.order_id}, exec_qty={response.exec_qty}"
        )

        # Path 1: authoritative invariant via the bust feed.
        bust = await perp_taker_tester.wait.for_execution_bust(
            order_id=str(response.order_id),
            timeout=15,
        )
        bust_reason_lower = (bust.reason or "").lower()
        assert (
            "reduce" in bust_reason_lower or "position" in bust_reason_lower
        ), f"expected reduce-only revert reason on bust, got: {bust.reason!r}"
        logger.info(f"✅ Bust feed surfaced expected reason: {bust.reason}")

    except ApiException as e:
        # Path 2: transitional — REST surfaces the decoded reason directly.
        err = str(e).lower()
        assert "reduce" in err or "position" in err, f"REST 4xx but message didn't surface reduce-only reason: {e}"
        logger.info(
            "✅ Reduce-only invariant verified via REST 4xx (transitional " f"consumer-fix path): {type(e).__name__}"
        )

    # Belt-and-suspenders: confirm chain truth — no position formed on the
    # taker. If the bust fired but a position still shows up here, that
    # would indicate the bust feed reported a phantom failure while the
    # fill actually settled — i.e. an indexer-vs-chain divergence worth
    # investigating separately.
    await asyncio.sleep(0.5)
    await perp_taker_tester.check.position_not_open(PERP_SYMBOL)
