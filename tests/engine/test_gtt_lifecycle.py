"""Good-Till-Time (GTT) lifecycle tests parametrized over [spot, perp] — live e2e.

GTT rests like GTC but the matching engine auto-reaps it at ``expiresAfter``
(GTC rests until cancelled; IOC never rests). These tests prove the full GTT
path on devnet:
- a GTT rests (OPEN), is reachable via REST carrying its ``expiresAfter``, and
  is cancellable,
- a GTT with a near-future expiry is AUTO-CANCELLED by the reaper at
  ``expiresAfter`` with no explicit cancel,
- a resting GTT can be modified to refresh its expiry (stays resting); a modify
  dropping the expiry to 0 is rejected client-side (the GTC/GTT coupling),
- a resting GTT that gets filled SETTLES on-chain — the fill calldata
  reproduces the signed ``timeInForce=GTT``, so settlement must NOT bust.

``maker`` is the aggressor (the account whose order crosses); ``taker`` is the
resting counterparty — matching the ``settlement_probe`` convention.
"""

from __future__ import annotations

import asyncio
import time
from decimal import Decimal

import pytest

from sdk.async_api.cancel_reason import CancelReason as WsCancelReason
from sdk.open_api.exceptions import ApiException
from sdk.open_api.models.order_status import OrderStatus
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api.models import LimitOrderParameters
from tests.helpers import ReyaTester
from tests.helpers.builders.order_builder import full_state_modify_params
from tests.helpers.gtt_timing import (
    GTT_REAP_DEADLINE_OFFSET_S,
    GTT_REAP_DETECT_BOUND_S,
    GTT_REAP_EXPIRY_OFFSET_S,
    GTT_REAP_PRE_EXPIRY_MARGIN_S,
    REAP_WAIT_TIMEOUT_S,
)
from tests.helpers.liquidity_detector import skip_if_external_config_liquidity
from tests.helpers.market_config import PerpTestConfig, SpotTestConfig
from tests.helpers.order_lifecycle import assert_resting_or_explain, rest_gtc, rest_gtt, wait_for_order_fields
from tests.helpers.reya_tester import logger
from tests.helpers.settlement import SettlementProbe

pytestmark = [pytest.mark.e2e, pytest.mark.gtt]

# Comfortable lifetime for tests that must NOT expire mid-run (rest / modify /
# fill all complete in well under a second).
GTT_LIFETIME_S = 300

# Bounded-timing constants for the auto-reap tests. GTT expiry is wall-clock
# (the ME reaper scans on a ~500ms interval), so these are REAL waits; the
# margins absorb ME<->test clock skew (cf. the COD tests' WSL2-skew note) and
# devnet Redis->indexer->WS propagation. Tune if devnet timing changes.


@pytest.mark.asyncio
async def test_gtt_rests_open_and_cancellable(
    market_config: SpotTestConfig | PerpTestConfig,
    market_type: str,
    maker: ReyaTester,
) -> None:
    """A GTT placed away from the touch rests (OPEN), is reachable via REST
    carrying its ``expiresAfter``, and is cancellable."""
    expires_after = int(time.time()) + GTT_LIFETIME_S
    order = await rest_gtt(maker, market_config, price_multiplier=0.5, expires_after=expires_after)

    assert order.status == OrderStatus.OPEN, f"[{market_type}] a GTT must rest OPEN, got {order.status}"
    assert (
        int(order.expires_after or 0) == expires_after
    ), f"[{market_type}] GTT must carry its expiresAfter: {order.expires_after} != {expires_after}"

    await maker.client.cancel_order(symbol=market_config.symbol, account_id=maker.account_id, order_id=order.order_id)
    await maker.wait.for_order_state(order.order_id, OrderStatus.CANCELLED)
    logger.info(f"[{market_type}] ✅ GTT rested OPEN with expiresAfter and was cancelled")


@pytest.mark.asyncio
async def test_gtt_reaped_at_expiry(
    market_config: SpotTestConfig | PerpTestConfig,
    market_type: str,
    maker: ReyaTester,
) -> None:
    """A resting GTT is AUTO-CANCELLED by the matching engine's reaper at
    ``expiresAfter`` — no explicit cancel — and the reap is BOUNDED on both
    sides: it does NOT fire before the expiry, and it lands within a finite
    window after it (not merely "eventually"). The bounds catch a reaper that
    fires early or far too late, which an open-ended wait would miss."""
    now = int(time.time())
    deadline = now + GTT_REAP_DEADLINE_OFFSET_S
    expires_after = now + GTT_REAP_EXPIRY_OFFSET_S  # strictly after the deadline (the GTT coupling)
    params = LimitOrderParameters(
        symbol=market_config.symbol,
        is_buy=True,
        limit_px=str(market_config.price(0.5)),
        qty=market_config.min_qty,
        time_in_force=TimeInForce.GTT,
        expires_after=expires_after,
        deadline=deadline,
    )
    order_id = await maker.orders.create_limit(params)
    assert order_id is not None, f"[{market_type}] GTT creation must return an order_id"
    await maker.wait.for_order_creation(order_id)

    resting = await wait_for_order_fields(maker, order_id)
    assert resting.status == OrderStatus.OPEN, f"[{market_type}] GTT must rest before its expiry"

    # Lower bound: shortly BEFORE the expiry the GTT must STILL be resting —
    # proves the reaper does not fire early (the margin absorbs ME/test skew).
    remaining = (expires_after - GTT_REAP_PRE_EXPIRY_MARGIN_S) - time.time()
    if remaining > 0:
        await asyncio.sleep(remaining)
    # Reports the cancel REASON when the order is gone: an early GTT_EXPIRED is
    # the defect this test hunts, while a USER_CANCEL / MASS_CANCEL from another
    # actor on this shared account means the precondition was removed and the
    # behaviour simply cannot be observed.
    await assert_resting_or_explain(maker, order_id, label=f"[{market_type}]", expires_after=expires_after)

    # Upper bound: reaped within a finite window AFTER expiry — no explicit cancel.
    await maker.wait.for_order_state(order_id, OrderStatus.CANCELLED, timeout=REAP_WAIT_TIMEOUT_S)
    ws_order = maker.ws.orders.get(str(order_id))
    assert ws_order is not None, f"[{market_type}] missing WS cancelled GTT order {order_id}"
    assert (
        ws_order.cancel_reason == WsCancelReason.GTT_EXPIRED
    ), f"[{market_type}] expected GTT_EXPIRED cancelReason, got {ws_order.cancel_reason}"
    assert ws_order.cancel_reason_message
    lateness = time.time() - expires_after
    assert lateness <= GTT_REAP_DETECT_BOUND_S, (
        f"[{market_type}] GTT reap observed {lateness:.0f}s after expiry " f"(> {GTT_REAP_DETECT_BOUND_S}s bound)"
    )
    logger.info(f"[{market_type}] ✅ GTT auto-reaped {lateness:.0f}s after expiresAfter (bounded, not early)")


@pytest.mark.asyncio
async def test_gtc_survives_while_gtt_is_reaped(
    market_config: SpotTestConfig | PerpTestConfig,
    market_type: str,
    maker: ReyaTester,
) -> None:
    """Differential proof of the GTC/GTT contract: a GTC (no expiry) placed
    ALONGSIDE a GTT (near-future expiry) must STILL be resting after the GTT is
    auto-reaped — the matching engine never auto-clears a GTC; only an explicit
    cancel (or fill) removes it. The GTC's survival horizon is anchored to an
    OBSERVED reap, not a fixed sleep — so it directly proves "GTC outlived the
    point at which a GTT expired"."""
    now = int(time.time())
    deadline = now + GTT_REAP_DEADLINE_OFFSET_S
    expires_after = now + GTT_REAP_EXPIRY_OFFSET_S

    # GTC far from the touch (a passive bid at 0.3x — never crosses, no expiry):
    # this is the order under test.
    gtc = await rest_gtc(maker, market_config, price_multiplier=0.3)
    assert gtc.status == OrderStatus.OPEN, f"[{market_type}] GTC must rest OPEN"

    # GTT alongside it, with a near-future expiry the reaper will act on.
    gtt_id = await maker.orders.create_limit(
        LimitOrderParameters(
            symbol=market_config.symbol,
            is_buy=True,
            limit_px=str(market_config.price(0.5)),
            qty=market_config.min_qty,
            time_in_force=TimeInForce.GTT,
            expires_after=expires_after,
            deadline=deadline,
        )
    )
    assert gtt_id is not None, f"[{market_type}] GTT creation must return an order_id"
    await maker.wait.for_order_creation(gtt_id)

    try:
        # Before the expiry: BOTH orders rest.
        remaining = (expires_after - GTT_REAP_PRE_EXPIRY_MARGIN_S) - time.time()
        if remaining > 0:
            await asyncio.sleep(remaining)
        await assert_resting_or_explain(maker, gtt_id, label=f"[{market_type}]", expires_after=expires_after)
        gtc_pre = await maker.data.open_order(gtc.order_id)
        assert gtc_pre is not None and gtc_pre.status == OrderStatus.OPEN, f"[{market_type}] GTC must rest"

        # The GTT is auto-reaped at its expiry (no explicit cancel)...
        await maker.wait.for_order_state(gtt_id, OrderStatus.CANCELLED, timeout=REAP_WAIT_TIMEOUT_S)

        # ...and the GTC is STILL OPEN — it has no expiry and the reaper never
        # touches it. This is the load-bearing assertion: a GTC is removed ONLY
        # by an explicit cancel/fill, never by the passage of time.
        gtc_post = await maker.data.open_order(gtc.order_id)
        assert gtc_post is not None and gtc_post.status == OrderStatus.OPEN, (
            f"[{market_type}] GTC was auto-cleared after the GTT reap horizon — "
            f"a GTC must only be removed on explicit cancel/fill"
        )
        logger.info(f"[{market_type}] ✅ GTC survived past the GTT reap horizon (never auto-cleared)")
    finally:
        # Always clean up the resting GTC (the GTT is already reaped).
        #
        # Tolerant of the order already being gone. On a shared account it may
        # have been cancelled by somebody else, and the body above may exit via
        # pytest.skip for exactly that reason -- a raising cleanup would then
        # REPLACE the skip with its own 400 and report the test as failed.
        # That is not hypothetical: it is why this test failed every run while
        # the skip was working correctly underneath.
        try:
            await maker.client.cancel_order(
                symbol=market_config.symbol, account_id=maker.account_id, order_id=gtc.order_id
            )
            await maker.wait.for_order_state(gtc.order_id, OrderStatus.CANCELLED)
        except ApiException as e:
            if "not found" not in str(e).lower() and "missing order" not in str(e).lower():
                raise
            logger.info(f"[{market_type}] cleanup: GTC {gtc.order_id} already gone ({e})")


@pytest.mark.asyncio
async def test_gtt_modify_refresh_expiry(
    market_config: SpotTestConfig | PerpTestConfig,
    market_type: str,
    maker: ReyaTester,
) -> None:
    """A resting GTT can be modified to a NEW future ``expiresAfter`` (stays
    resting with the refreshed lifetime); a modify dropping the expiry to 0 is
    rejected client-side (a GTT must carry a lifetime — 0 would be GTC)."""
    expires_after = int(time.time()) + GTT_LIFETIME_S
    order = await rest_gtt(maker, market_config, price_multiplier=0.5, expires_after=expires_after)

    new_expires_after = int(time.time()) + GTT_LIFETIME_S + 120
    response = await maker.client.modify_order(full_state_modify_params(order, expires_after=new_expires_after))
    assert response.order_id == order.order_id, f"[{market_type}] orderId must be preserved through modify"

    refreshed = await wait_for_order_fields(maker, order.order_id, expires_after=new_expires_after)
    assert refreshed.status == OrderStatus.OPEN, f"[{market_type}] GTT must stay resting after an expiry refresh"

    # Dropping the expiry to 0 on a resting GTT is rejected client-side before
    # signing — that would turn it into a never-expiring (GTC) order.
    with pytest.raises(ValueError, match="GTT orders require a non-zero expires_after"):
        await maker.client.modify_order(full_state_modify_params(refreshed, expires_after=0))

    await maker.client.cancel_order(symbol=market_config.symbol, account_id=maker.account_id, order_id=order.order_id)
    await maker.wait.for_order_state(order.order_id, OrderStatus.CANCELLED)
    logger.info(f"[{market_type}] ✅ GTT expiry refreshed via modify; drop-to-0 rejected client-side")


@pytest.mark.asyncio
@pytest.mark.maker_taker
@pytest.mark.usefixtures("settlement_cleanup_guard")
async def test_gtt_resting_fill_settles_on_chain(
    market_config: SpotTestConfig | PerpTestConfig,
    market_type: str,
    maker: ReyaTester,
    taker: ReyaTester,
    settlement_probe: SettlementProbe,
) -> None:
    """A resting GTT that gets filled settles on-chain: the fill calldata
    reproduces the signed ``timeInForce=GTT``, so settlement must NOT bust
    InvalidSignature. The resting counterparty (``taker``) rests a GTT SELL;
    the aggressor (``maker``) crosses it with an IOC BUY; settlement lands
    (spot balance deltas / perp signed-position deltas) with no execution
    busts (asserted session-wide by ``execution_busts_guard``)."""
    await skip_if_external_config_liquidity(market_config, maker, "Engineered cross needs an empty book.")
    qty = market_config.min_qty
    expires_after = int(time.time()) + GTT_LIFETIME_S

    if market_type == "perp":
        # Cross at the CURRENT mark (re-fetched), not the stale session-start
        # config price: over a long suite run the oracle drifts, and a cross far
        # from the live mark gets blocked by the perp execution band (the
        # conftest perp-cross helpers re-fetch the current oracle for the same
        # reason). The perp symbol IS its own oracle symbol.
        mark = Decimal(str(await maker.data.current_price(market_config.symbol)))
        cross_px = str(mark.quantize(Decimal("0.01")))
    else:
        # Spot prices come from the perp oracle (not the spot symbol) and there
        # is no execution band / dynamic depth, so the config price is reliable.
        cross_px = str(market_config.price(0.99))

    await settlement_probe.capture_baseline()

    await rest_gtt(taker, market_config, price=cross_px, expires_after=expires_after, is_buy=False)
    buyer_order_id = await maker.orders.create_limit(
        LimitOrderParameters(
            symbol=market_config.symbol,
            is_buy=True,
            limit_px=cross_px,
            qty=qty,
            time_in_force=TimeInForce.IOC,
        )
    )
    assert buyer_order_id is not None, f"[{market_type}] aggressor IOC must return an order_id"

    # The authoritative proof that the resting GTT FILLED and SETTLED on-chain is
    # the position/balance delta (via REST), which settlement_probe polls and
    # which times out if the two-party cross never landed. We deliberately do NOT
    # gate on the resting order's WS status first: the WS order-change store can
    # lag or go stale over a long session (a false "not found"), whereas the
    # on-chain delta is both stronger (settlement actually moved funds) and
    # market-correct (the probe requires BOTH our accounts to have moved ±qty, so
    # it only passes if our two orders crossed EACH OTHER, not algorithmic depth).
    await settlement_probe.assert_settled(qty=qty, price=cross_px, timeout_s=25)
    logger.info(f"[{market_type}] ✅ a resting GTT filled and settled on-chain (no bust)")
