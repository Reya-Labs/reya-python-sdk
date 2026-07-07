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
        if str(bust.taker_order_id) in involved_order_ids or str(bust.maker_order_id) in involved_order_ids
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


@pytest.mark.asyncio
async def test_crossing_modify_partial_fill_rests_remainder(
    market_config: SpotTestConfig | PerpTestConfig,
    market_type: str,
    maker: ReyaTester,
    taker: ReyaTester,
    settlement_probe: SettlementProbe,
) -> None:
    """A crossing modify whose qty EXCEEDS the resting counterparty partially
    fills and rests the remainder: response is non-terminal (OPEN /
    PARTIALLY_FILLED) with execQty == counterparty size, the remainder stays
    resting (cumQty == filled), orderId preserved, and the filled slice settles.
    Complements the full-fill test (which collapses to FILLED)."""
    await skip_if_external_config_liquidity(market_config, maker, EXECUTION_REASON)
    min_qty = market_config.min_qty
    double_min_qty = str(Decimal(min_qty) * 2)
    cross_px = str(market_config.price(1.01))

    await settlement_probe.capture_baseline()

    # maker buys 2×min; taker rests an ask of exactly min — the crossing modify
    # consumes the ask (min) and rests the remaining min.
    buyer_order = await rest_gtc(maker, market_config, price_multiplier=0.96, qty=double_min_qty, is_buy=True)
    seller_order = await rest_gtc(taker, market_config, price_multiplier=1.01, qty=min_qty, is_buy=False)

    try:
        response = await maker.client.modify_order(full_state_modify_params(buyer_order, limit_px=cross_px))
        assert response.order_id == buyer_order.order_id, "orderId must be preserved through a partial-fill modify"
        # The API collapses a resting partially-filled order to OPEN (no
        # PARTIALLY_FILLED status); the partial fill shows via execQty + cumQty.
        assert (
            response.status == OrderStatus.OPEN
        ), f"[{market_type}] a partially-filled crossing modify must rest the remainder OPEN, got {response.status}"
        assert response.exec_qty is not None and Decimal(response.exec_qty) == Decimal(
            min_qty
        ), f"[{market_type}] execQty must report only the crossed (counterparty) size: {response}"
        logger.info(f"[{market_type}] ✅ partial crossing modify: execQty={response.exec_qty} status={response.status}")

        execution = await wait_for_taker_execution(maker, market_type, buyer_order.order_id)
        assert Decimal(execution.qty) == Decimal(min_qty), f"Execution qty {execution.qty} != {min_qty}"
        await taker.wait.for_order_state(seller_order.order_id, OrderStatus.FILLED, timeout=5)

        # The remainder (min) keeps resting under the same orderId with cumQty == filled.
        remainder = await wait_for_order_fields(maker, buyer_order.order_id, cum_qty=min_qty)
        assert Decimal(remainder.qty or "0") == Decimal(
            double_min_qty
        ), f"[{market_type}] total qty unchanged by a partial fill: {remainder.qty}"
        logger.info(f"[{market_type}] ✅ remainder rests: qty={remainder.qty} cumQty={remainder.cum_qty}")

        await settlement_probe.assert_settled(qty=min_qty, price=cross_px)
        logger.info(f"[{market_type}] ✅ partial fill settled for the crossed slice")
    finally:
        await maker.orders.close_all(fail_if_none=False)
        await taker.orders.close_all(fail_if_none=False)


@pytest.mark.asyncio
async def test_crossing_modify_self_match_cancelled(
    market_config: SpotTestConfig | PerpTestConfig,
    market_type: str,
    maker: ReyaTester,
) -> None:
    """A modify that crosses the SAME account's resting order is self-match
    prevented: the modified order (the taker) is CANCELLED — the only modify
    path that yields status CANCELLED — and the resting counterparty survives.
    Single account, so no fill/settlement occurs."""
    await skip_if_external_config_liquidity(market_config, maker, EXECUTION_REASON)
    ask_px = str(market_config.price(1.04))

    # Same account rests an ASK above and a BID below; modifying the bid up to
    # the ask crosses the account's OWN order -> self-match -> bid cancelled.
    resting_ask = await rest_gtc(maker, market_config, price_multiplier=1.04, is_buy=False)
    resting_bid = await rest_gtc(maker, market_config, price_multiplier=0.96, is_buy=True)

    try:
        response = await maker.client.modify_order(full_state_modify_params(resting_bid, limit_px=ask_px))
        assert response.order_id == resting_bid.order_id, "orderId must be preserved through a self-match cancel"
        assert (
            response.status == OrderStatus.CANCELLED
        ), f"[{market_type}] self-matching modify must be CANCELLED (SMP), got {response.status}"
        logger.info(f"[{market_type}] ✅ self-matching modify cancelled: {response.order_id}")

        # The modified bid is gone; the resting ask is untouched.
        await maker.wait.for_order_state(resting_bid.order_id, OrderStatus.CANCELLED, timeout=5)
        surviving_ask = await maker.data.open_order(resting_ask.order_id)
        assert surviving_ask is not None, f"[{market_type}] resting counterparty (ask) must survive SMP"
        logger.info(f"[{market_type}] ✅ resting counterparty ask survived the self-match")
    finally:
        await maker.orders.close_all(fail_if_none=False)


@pytest.mark.asyncio
async def test_post_modify_resting_maker_fill_settles(
    market_config: SpotTestConfig | PerpTestConfig,
    market_type: str,
    maker: ReyaTester,
    taker: ReyaTester,
    settlement_probe: SettlementProbe,
) -> None:
    """Settlement-critical: modify a resting order (re-signs + republishes the
    post-modify state), THEN have a taker cross into it so the modified order
    fills as a MAKER. The maker-side fill must settle, proving the REPUBLISHED
    (fresh) signature — not stale pre-modify bytes — is what settles. The
    crossing-execution test only exercises the modified order as a taker."""
    await skip_if_external_config_liquidity(market_config, maker, EXECUTION_REASON)
    min_qty = market_config.min_qty
    resting_px = str(market_config.price(0.97))

    # maker rests a buy, then modifies it (px change -> re-sign) while it stays
    # resting and non-crossing; the modified order keeps resting at resting_px.
    buyer_order = await rest_gtc(maker, market_config, price_multiplier=0.96, is_buy=True)
    modify_resp = await maker.client.modify_order(full_state_modify_params(buyer_order, limit_px=resting_px))
    assert modify_resp.order_id == buyer_order.order_id
    assert modify_resp.status == OrderStatus.OPEN, f"[{market_type}] non-crossing modify must stay OPEN"
    await wait_for_order_fields(maker, buyer_order.order_id, limit_px=resting_px)
    logger.info(f"[{market_type}] ✅ resting order modified + re-signed at {resting_px}")

    await settlement_probe.capture_baseline()

    try:
        # A taker SELLS into the modified resting buy -> the modified order fills
        # as the MAKER, using its republished post-modify signature.
        ioc = OrderBuilder().symbol(market_config.symbol).sell().price(resting_px).qty(min_qty).ioc().build()
        taker_order_id = await taker.orders.create_limit(ioc)
        assert taker_order_id is not None
        execution = await wait_for_taker_execution(taker, market_type, taker_order_id)
        assert str(execution.maker_order_id) == str(
            buyer_order.order_id
        ), f"[{market_type}] the modified order must be the maker side: {execution.maker_order_id}"
        await maker.wait.for_order_state(buyer_order.order_id, OrderStatus.FILLED, timeout=5)
        logger.info(f"[{market_type}] ✅ modified order filled as maker")

        await settlement_probe.assert_settled(qty=min_qty, price=resting_px)
        logger.info(f"[{market_type}] ✅ maker-side fill settled with the republished post-modify signature")
    finally:
        await maker.orders.close_all(fail_if_none=False)
        await taker.orders.close_all(fail_if_none=False)


@pytest.mark.asyncio
async def test_post_only_off_modify_crosses_and_fills(
    market_config: SpotTestConfig | PerpTestConfig,
    market_type: str,
    maker: ReyaTester,
    taker: ReyaTester,
    settlement_probe: SettlementProbe,
) -> None:
    """Flipping postOnly True->False on a modify whose new price crosses lets it
    execute as a taker (fills) — the mirror of the post-only-would-cross
    rejection. The resting post-only order, once postOnly is cleared, is free to
    take liquidity."""
    await skip_if_external_config_liquidity(market_config, maker, EXECUTION_REASON)
    min_qty = market_config.min_qty
    cross_px = str(market_config.price(1.01))

    await settlement_probe.capture_baseline()

    # maker rests a POST-ONLY buy below; taker rests the ask.
    post_only_buy = (
        OrderBuilder()
        .symbol(market_config.symbol)
        .buy()
        .price(str(market_config.price(0.96)))
        .qty(min_qty)
        .gtc()
        .post_only(True)
        .build()
    )
    buyer_order_id = await maker.orders.create_limit(post_only_buy)
    assert buyer_order_id is not None
    buyer_order = await wait_for_order_fields(maker, buyer_order_id, post_only=True)
    seller_order = await rest_gtc(taker, market_config, price_multiplier=1.01, qty=min_qty, is_buy=False)

    try:
        response = await maker.client.modify_order(
            full_state_modify_params(buyer_order, limit_px=cross_px, post_only=False)
        )
        assert response.order_id == buyer_order_id, "orderId must be preserved"
        assert (
            response.status == OrderStatus.FILLED
        ), f"[{market_type}] postOnly-off crossing modify must fill, got {response.status}"
        assert response.exec_qty is not None and Decimal(response.exec_qty) == Decimal(min_qty)
        logger.info(f"[{market_type}] ✅ postOnly True->False modify crossed and filled as taker")

        await taker.wait.for_order_state(seller_order.order_id, OrderStatus.FILLED, timeout=5)
        await settlement_probe.assert_settled(qty=min_qty, price=cross_px)
        logger.info(f"[{market_type}] ✅ settlement landed for the postOnly-off taker fill")
    finally:
        await maker.orders.close_all(fail_if_none=False)
        await taker.orders.close_all(fail_if_none=False)


@pytest.mark.asyncio
async def test_qty_strictly_below_filled_rejected(
    market_config: SpotTestConfig | PerpTestConfig,
    market_type: str,
    maker: ReyaTester,
    taker: ReyaTester,
) -> None:
    """Boundary (lower arm): a partially-filled order with cumQty == 2×min,
    modified to a TOTAL qty strictly below cumQty (== min) is rejected with
    MODIFY_QTY_BELOW_FILLED_ERROR. Complements the existing ==cumQty arm and the
    accept arm below."""
    await skip_if_external_config_liquidity(market_config, maker, EXECUTION_REASON)
    min_qty = market_config.min_qty
    triple_min = str(Decimal(min_qty) * 3)
    double_min = str(Decimal(min_qty) * 2)
    queue_px = str(market_config.price(0.99))

    maker_order = await rest_gtc(maker, market_config, price_multiplier=0.99, qty=triple_min, is_buy=True)
    try:
        ioc = OrderBuilder().symbol(market_config.symbol).sell().price(queue_px).qty(double_min).ioc().build()
        taker_order_id = await taker.orders.create_limit(ioc)
        assert taker_order_id is not None
        await wait_for_taker_execution(taker, market_type, taker_order_id)
        partially_filled = await wait_for_order_fields(maker, maker_order.order_id, cum_qty=double_min)

        # total qty == min < cumQty (2×min) — strictly below filled.
        with pytest.raises(ApiException) as exc_info:
            await maker.client.modify_order(full_state_modify_params(partially_filled, qty=min_qty))
        assert "MODIFY_QTY_BELOW_FILLED_ERROR" in str(
            exc_info.value
        ), f"[{market_type}] expected MODIFY_QTY_BELOW_FILLED_ERROR, got: {str(exc_info.value)[:200]}"
        logger.info(f"[{market_type}] ✅ qty strictly < cumQty rejected")
        assert await maker.data.open_order(maker_order.order_id) is not None, "remainder must stay resting"
    finally:
        await maker.orders.close_all(fail_if_none=False)
        await taker.orders.close_all(fail_if_none=False)


@pytest.mark.asyncio
async def test_qty_above_filled_succeeds(
    market_config: SpotTestConfig | PerpTestConfig,
    market_type: str,
    maker: ReyaTester,
    taker: ReyaTester,
) -> None:
    """Boundary (accept arm): a partially-filled order (cumQty == 2×min)
    modified to a TOTAL qty strictly ABOVE cumQty (cum + one qty step) is
    accepted — the order stays OPEN with the new total and cumQty preserved.
    Closes the QtyBelowFilled boundary triple (< reject, == reject, +step
    accept)."""
    await skip_if_external_config_liquidity(market_config, maker, EXECUTION_REASON)
    min_qty = market_config.min_qty
    quad_min = str(Decimal(min_qty) * 4)
    double_min = str(Decimal(min_qty) * 2)
    # cumQty + one step; distinct from the resting total (4×min) so it's a real change.
    above_filled = str(Decimal(double_min) + Decimal(market_config.qty_step_size))
    queue_px = str(market_config.price(0.99))

    maker_order = await rest_gtc(maker, market_config, price_multiplier=0.99, qty=quad_min, is_buy=True)
    try:
        ioc = OrderBuilder().symbol(market_config.symbol).sell().price(queue_px).qty(double_min).ioc().build()
        taker_order_id = await taker.orders.create_limit(ioc)
        assert taker_order_id is not None
        await wait_for_taker_execution(taker, market_type, taker_order_id)
        partially_filled = await wait_for_order_fields(maker, maker_order.order_id, cum_qty=double_min)

        response = await maker.client.modify_order(full_state_modify_params(partially_filled, qty=above_filled))
        assert response.order_id == maker_order.order_id
        assert (
            response.status == OrderStatus.OPEN
        ), f"[{market_type}] qty above cumQty must be accepted (rests OPEN), got {response.status}"
        logger.info(f"[{market_type}] ✅ qty == cumQty + step accepted (total -> {above_filled})")

        updated = await wait_for_order_fields(maker, maker_order.order_id, qty=above_filled, cum_qty=double_min)
        assert Decimal(updated.cum_qty or "0") == Decimal(
            double_min
        ), f"[{market_type}] cumQty must be preserved by a qty-up modify: {updated.cum_qty}"
    finally:
        await maker.orders.close_all(fail_if_none=False)
        await taker.orders.close_all(fail_if_none=False)
