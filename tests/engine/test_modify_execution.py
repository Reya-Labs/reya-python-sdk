"""
modifyOrder execution-semantics tests parametrized over [spot, perp] — live e2e.

- a non-post-only modify whose new limitPx crosses executes IMMEDIATELY
  (response FILLED + execQty, the execution carries BOTH orderIds, settlement
  lands — exact zero-fee balance deltas on spot / signed ±qty position deltas
  on perp, via the injected ``settlement_probe`` — and NO execution busts
  appear),
- a post-only modify that WOULD cross is rejected with
  POST_ONLY_WOULD_CROSS_ERROR and the resting order is left untouched,
- ``qty`` is the TOTAL order quantity and must exceed ``cumQty``
  (MODIFY_QTY_BELOW_FILLED_ERROR otherwise).

``maker`` is the aggressor (the account whose resting buy is modified up to
cross); ``taker`` is the resting counterparty (the ask). All tests need an
empty book — external liquidity would absorb or front-run the engineered
crosses. Fill cleanup: spot via ``spot_balance_guard``, perp via
``perp_baseline_restore`` — both wired by the autouse ``_settlement_cleanup``.
"""

from decimal import Decimal

import pytest

from sdk.open_api.exceptions import ApiException
from sdk.open_api.models.order_status import OrderStatus
from tests.helpers import ReyaTester
from tests.helpers.builders import OrderBuilder
from tests.helpers.builders.order_builder import full_state_modify_params
from tests.helpers.liquidity_detector import skip_if_external_config_liquidity
from tests.helpers.market_config import PerpTestConfig, SpotTestConfig
from tests.helpers.order_lifecycle import assert_px_qty, rest_gtc, wait_for_order_fields, wait_for_taker_execution
from tests.helpers.reya_tester import logger
from tests.helpers.settlement import SettlementProbe

pytestmark = [pytest.mark.e2e, pytest.mark.modify, pytest.mark.maker_taker]

EXECUTION_REASON = "Engineered crosses need a controlled (empty) book."


@pytest.fixture(autouse=True)
def _settlement_cleanup(settlement_cleanup_guard):  # pylint: disable=unused-argument
    """Every test in this module rests orders and two of three produce fills,
    so wire the per-market settlement cleanup (spot balance guard / perp
    baseline restore)."""
    yield


@pytest.mark.asyncio
async def test_crossing_modify_executes(
    market_config: SpotTestConfig | PerpTestConfig,
    market_type: str,
    maker: ReyaTester,
    taker: ReyaTester,
    settlement_probe: SettlementProbe,
) -> None:
    """Modifying a resting buy's px up to the counterparty's ask executes
    immediately: response FILLED with execQty, the execution carries BOTH
    orderIds (modified order as taker, counterparty as maker), settlement lands
    (balances on spot / positions on perp), and NO execution busts appear."""
    await skip_if_external_config_liquidity(market_config, maker, EXECUTION_REASON)
    qty = market_config.min_qty
    cross_px = str(market_config.price(1.01))

    await settlement_probe.capture_baseline()
    buyer_busts_before = await maker.data.execution_busts()
    seller_busts_before = await taker.data.execution_busts()

    buyer_order = await rest_gtc(maker, market_config, price_multiplier=0.96, is_buy=True)
    seller_order = await rest_gtc(taker, market_config, price_multiplier=1.01, is_buy=False)

    response = await maker.client.modify_order(full_state_modify_params(buyer_order, limit_px=cross_px))
    assert response.order_id == buyer_order.order_id, "orderId must be preserved through a crossing modify"
    assert response.status == OrderStatus.FILLED, f"[{market_type}] equal-sized cross must fully fill: {response}"
    assert response.exec_qty is not None and Decimal(response.exec_qty) == Decimal(
        qty
    ), f"[{market_type}] execQty must report the crossed quantity: {response}"
    logger.info(f"[{market_type}] ✅ crossing modify executed: {response.order_id} execQty={response.exec_qty}")

    execution = await wait_for_taker_execution(maker, market_type, buyer_order.order_id)
    assert str(execution.maker_order_id) == str(
        seller_order.order_id
    ), f"Execution maker {execution.maker_order_id} != resting counterparty {seller_order.order_id}"
    assert Decimal(execution.qty) == Decimal(qty), f"Execution qty {execution.qty} != {qty}"
    assert Decimal(execution.price) == Decimal(
        cross_px
    ), f"Execution must print at the resting ask {cross_px}, got {execution.price}"
    logger.info(f"[{market_type}] ✅ execution carries both orderIds at the resting px")

    await taker.wait.for_order_state(seller_order.order_id, OrderStatus.FILLED, timeout=5)

    # Settlement proof — spot: exact zero-fee balance deltas; perp: ±qty signed
    # position deltas. The probe encapsulates the only market-divergent assertion.
    await settlement_probe.assert_settled(qty=qty, price=cross_px)
    logger.info(f"[{market_type}] ✅ settlement landed for both accounts")

    # Settlement landed → the executionBusts streams must NOT have grown
    # (a bust = settlement failure).
    buyer_busts_after = await maker.data.execution_busts()
    seller_busts_after = await taker.data.execution_busts()
    assert len(buyer_busts_after) == len(
        buyer_busts_before
    ), f"Buyer wallet gained execution busts: {len(buyer_busts_before)} -> {len(buyer_busts_after)}"
    assert len(seller_busts_after) == len(
        seller_busts_before
    ), f"Seller wallet gained execution busts: {len(seller_busts_before)} -> {len(seller_busts_after)}"
    involved_order_ids = {str(buyer_order.order_id), str(seller_order.order_id)}
    busted = [
        bust
        for bust in buyer_busts_after + seller_busts_after
        if str(bust.order_id) in involved_order_ids or str(bust.maker_order_id) in involved_order_ids
    ]
    assert not busted, f"Execution busts reference the crossing-modify orders: {busted}"
    logger.info(f"[{market_type}] ✅ executionBusts empty for both wallets after the crossing modify")

    await maker.check.no_open_orders()
    await taker.check.no_open_orders()


@pytest.mark.asyncio
async def test_post_only_modify_would_cross_rejected(
    market_config: SpotTestConfig | PerpTestConfig,
    market_type: str,
    maker: ReyaTester,
    taker: ReyaTester,
) -> None:
    """A would-cross modify with post_only=True is rejected with
    POST_ONLY_WOULD_CROSS_ERROR and the resting order keeps its old px/qty
    (priority intact)."""
    await skip_if_external_config_liquidity(market_config, maker, EXECUTION_REASON)

    buyer_order = await rest_gtc(maker, market_config, price_multiplier=0.96, is_buy=True)
    seller_order = await rest_gtc(taker, market_config, price_multiplier=1.01, is_buy=False)
    original_px = buyer_order.limit_px
    original_qty = buyer_order.qty
    assert original_px is not None and original_qty is not None
    cross_px = str(market_config.price(1.01))

    try:
        with pytest.raises(ApiException) as exc_info:
            await maker.client.modify_order(full_state_modify_params(buyer_order, limit_px=cross_px, post_only=True))
        error_msg = str(exc_info.value)
        assert (
            "POST_ONLY_WOULD_CROSS_ERROR" in error_msg
        ), f"[{market_type}] expected POST_ONLY_WOULD_CROSS_ERROR, got: {error_msg[:200]}"
        logger.info(f"[{market_type}] ✅ post-only would-cross modify rejected")

        untouched = await maker.data.open_order(buyer_order.order_id)
        assert untouched is not None, "Rejected modify must leave the order resting"
        assert_px_qty(untouched, expected_px=original_px, expected_qty=original_qty)
        assert not untouched.post_only, "Rejected modify must not flip postOnly"
        logger.info(f"[{market_type}] ✅ resting order untouched after the rejection")
    finally:
        await maker.orders.cancel(
            order_id=buyer_order.order_id, symbol=market_config.symbol, account_id=maker.account_id
        )
        await taker.orders.cancel(
            order_id=seller_order.order_id, symbol=market_config.symbol, account_id=taker.account_id
        )


@pytest.mark.asyncio
async def test_qty_below_filled_rejected(
    market_config: SpotTestConfig | PerpTestConfig,
    market_type: str,
    maker: ReyaTester,
    taker: ReyaTester,
) -> None:
    """Engineer a partial fill, then modify the TOTAL qty down to cumQty —
    rejected with MODIFY_QTY_BELOW_FILLED_ERROR (qty must be STRICTLY greater
    than the filled amount). Exercises cumQty accounting on a partially-filled
    order on both markets."""
    await skip_if_external_config_liquidity(market_config, maker, EXECUTION_REASON)
    min_qty = market_config.min_qty
    double_min_qty = str(Decimal(min_qty) * 2)
    queue_px = str(market_config.price(0.99))

    maker_order = await rest_gtc(maker, market_config, price_multiplier=0.99, qty=double_min_qty, is_buy=True)

    try:
        ioc = OrderBuilder().symbol(market_config.symbol).sell().price(queue_px).qty(min_qty).ioc().build()
        taker_order_id = await taker.orders.create_limit(ioc)
        assert taker_order_id is not None
        await wait_for_taker_execution(taker, market_type, taker_order_id)

        # cumQty propagates through the OrdersProvider cache — poll for it.
        partially_filled = await wait_for_order_fields(maker, maker_order.order_id, cum_qty=min_qty)
        logger.info(
            f"[{market_type}] maker partially filled: cumQty={partially_filled.cum_qty} of {partially_filled.qty}"
        )

        # TOTAL qty == cumQty (≤ filled) must be rejected.
        with pytest.raises(ApiException) as exc_info:
            await maker.client.modify_order(full_state_modify_params(partially_filled, qty=min_qty))
        error_msg = str(exc_info.value)
        assert (
            "MODIFY_QTY_BELOW_FILLED_ERROR" in error_msg
        ), f"[{market_type}] expected MODIFY_QTY_BELOW_FILLED_ERROR, got: {error_msg[:200]}"
        logger.info(f"[{market_type}] ✅ qty ≤ cumQty rejected with MODIFY_QTY_BELOW_FILLED_ERROR")

        still_open = await maker.data.open_order(maker_order.order_id)
        assert still_open is not None, "Rejected modify must leave the remainder resting"
    finally:
        await maker.orders.close_all(fail_if_none=False)
        await taker.orders.close_all(fail_if_none=False)
