"""
modifyOrder happy-path tests parametrized over [spot, perp] — live e2e.

In-place modification of a resting GTC: the order keeps its `orderId`, the
four modifiable fields (`limitPx`, `qty`, `postOnly`, `expiresAfter`) carry
the COMPLETE post-modify state, and `full_state_modify_params` restates
everything from the fetched Order so each test only spells out what it
changes. Post-modify read-backs poll openOrders (the OrdersProvider cache
consumes the ME's Redis stream asynchronously w.r.t. the modify response) and
assert a fresh `lastUpdateAt` plus the WS `orderChange` carrying the SAME
orderId with the new fields. No fills occur (the orders rest far from the
book), so these run on a single account (`maker`) per market.

expiresAfter is TIF-bound: only GTT orders carry a lifetime. These happy-path
modifies all run on a resting GTC, so the flags-only test pins the (still-valid)
rule that a NON-GTT order's expiresAfter must be 0 — modifying a resting GTC to a
non-zero expiresAfter is rejected (INPUT_VALIDATION_ERROR, order untouched). GTT
creation IS supported by the SDK now (shipped in 98c10c2); the positive GTT
modify path (0→future→0 on a resting GTT) is intentionally DEFERRED until the
GTT matching-engine is on devnet1.
"""

import time
from decimal import Decimal

import pytest

from sdk.open_api.models.order_status import OrderStatus
from tests.helpers import ReyaTester
from tests.helpers.builders.order_builder import full_state_modify_params
from tests.helpers.market_config import PerpTestConfig, SpotTestConfig
from tests.helpers.order_lifecycle import rest_gtc, wait_for_order_fields, wait_for_ws_order_change
from tests.helpers.reya_tester import logger

pytestmark = [pytest.mark.e2e, pytest.mark.modify]


@pytest.mark.asyncio
async def test_modify_px_qty_happy(
    market_config: SpotTestConfig | PerpTestConfig, market_type: str, maker: ReyaTester
) -> None:
    """Modify px+qty of a resting GTC: orderId preserved, openOrders reflects
    the new values with a fresh lastUpdateAt, and the WS orderChange carries
    the SAME orderId with the new fields."""
    order = await rest_gtc(maker, market_config, price_multiplier=0.50)
    order_id = order.order_id

    new_px = str(market_config.price(0.52))
    new_qty = str(Decimal(market_config.min_qty) * 2)
    response = await maker.client.modify_order(full_state_modify_params(order, limit_px=new_px, qty=new_qty))

    assert response.order_id == order_id, f"orderId must be preserved: {response.order_id} != {order_id}"
    assert response.status == OrderStatus.OPEN, f"[{market_type}] non-crossing modify must stay OPEN: {response}"

    modified = await wait_for_order_fields(maker, order_id, limit_px=new_px, qty=new_qty)
    assert modified.last_update_at > order.last_update_at, (
        f"lastUpdateAt must be refreshed by the modify: "
        f"{modified.last_update_at} <= pre-modify {order.last_update_at}"
    )
    logger.info(f"[{market_type}] ✅ modify px+qty OK: {order_id} -> px={new_px} qty={new_qty} (fresh lastUpdateAt)")

    ws_change = await wait_for_ws_order_change(maker, order_id, limit_px=new_px, qty=new_qty)
    assert ws_change.order_id == order_id, f"WS orderChange must keep the orderId: {ws_change.order_id}"
    logger.info(f"[{market_type}] ✅ WS orderChange carries the same orderId with the new px/qty")

    await maker.orders.cancel(order_id=order_id, symbol=market_config.symbol, account_id=maker.account_id)
    await maker.check.no_open_orders()


@pytest.mark.asyncio
async def test_modify_by_client_order_id(
    market_config: SpotTestConfig | PerpTestConfig, market_type: str, maker: ReyaTester
) -> None:
    """Target the modify by client_order_id (order_id=None) — the same
    clientOrderId is restated into the signed envelope.

    The clientOrderId → order → marketId resolution is the exact path that
    broke twice for perp cancels (perpOB-6 Bug 11, PRO-143), so this is pinned
    on both markets."""
    client_order_id = int(time.time() * 1_000_000)
    order = await rest_gtc(maker, market_config, price_multiplier=0.50, client_order_id=client_order_id)
    order_id = order.order_id

    new_px = str(market_config.price(0.52))
    new_qty = str(Decimal(market_config.min_qty) * 2)
    response = await maker.client.modify_order(
        full_state_modify_params(
            order,
            client_order_id=client_order_id,
            limit_px=new_px,
            qty=new_qty,
        )
    )

    assert response.order_id == order_id, f"orderId must be preserved: {response.order_id} != {order_id}"
    assert response.status == OrderStatus.OPEN

    modified = await wait_for_order_fields(maker, order_id, limit_px=new_px, qty=new_qty)
    assert modified.last_update_at > order.last_update_at, (
        f"lastUpdateAt must be refreshed by the modify: "
        f"{modified.last_update_at} <= pre-modify {order.last_update_at}"
    )
    logger.info(f"[{market_type}] ✅ modify by clientOrderId={client_order_id} OK: {order_id} (fresh lastUpdateAt)")

    ws_change = await wait_for_ws_order_change(maker, order_id, limit_px=new_px, qty=new_qty)
    assert ws_change.order_id == order_id, f"WS orderChange must keep the orderId: {ws_change.order_id}"
    logger.info(f"[{market_type}] ✅ WS orderChange carries the same orderId with the new px/qty")

    await maker.orders.cancel(order_id=order_id, symbol=market_config.symbol, account_id=maker.account_id)
    await maker.check.no_open_orders()


@pytest.mark.asyncio
async def test_modify_flags_only(
    market_config: SpotTestConfig | PerpTestConfig, market_type: str, maker: ReyaTester
) -> None:
    """postOnly False→True→False, each step a single-field modify with the
    rest of the state restated unchanged; then a non-zero expiresAfter on the
    GTC is rejected client-side (only GTT carries a lifetime) leaving the order
    untouched."""
    order = await rest_gtc(maker, market_config, price_multiplier=0.50)
    order_id = order.order_id

    # postOnly False -> True (a resting order can't cross, so always legal).
    response = await maker.client.modify_order(full_state_modify_params(order, post_only=True))
    assert response.order_id == order_id
    order = await wait_for_order_fields(maker, order_id, post_only=True)
    logger.info(f"[{market_type}] ✅ postOnly False -> True read back")

    # postOnly True -> False.
    response = await maker.client.modify_order(full_state_modify_params(order, post_only=False))
    assert response.order_id == order_id
    order = await wait_for_order_fields(maker, order_id, post_only=False)
    logger.info(f"[{market_type}] ✅ postOnly True -> False read back")

    # expiresAfter is TIF-bound: the resting order is GTC, so any non-zero
    # expiresAfter is rejected by the client's fail-fast coupling guard in
    # build_modify_order_payload with a ValueError BEFORE the request is signed
    # or sent — GTC never expires; only GTT carries a lifetime. GTT creation IS
    # supported now; the positive GTT modify path (rest a GTT, then refresh its
    # expiry) is deferred until GTT is on devnet1. The off-chain server enforces
    # the same rule as defense-in-depth (covered by the off-chain handler tests).
    future_expiry = int(time.time()) + 3600
    with pytest.raises(ValueError, match="GTC orders must omit expires_after"):
        await maker.client.modify_order(full_state_modify_params(order, expires_after=future_expiry))
    logger.info(f"[{market_type}] ✅ non-zero expiresAfter on a GTC rejected client-side before send")

    untouched = await maker.data.open_order(order_id)
    assert untouched is not None, "Rejected expiresAfter modify must leave the order resting"
    assert int(untouched.expires_after or 0) == 0, f"expiresAfter must stay 0: {untouched.expires_after}"
    assert not untouched.post_only, f"Rejected modify must not flip postOnly: {untouched.post_only}"
    logger.info(f"[{market_type}] ✅ order untouched after the rejected expiresAfter modify")

    await maker.orders.cancel(order_id=order_id, symbol=market_config.symbol, account_id=maker.account_id)
    await maker.check.no_open_orders()
