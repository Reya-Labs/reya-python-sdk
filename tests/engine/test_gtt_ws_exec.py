"""Good-Till-Time (GTT) over ws-exec — live e2e, parametrized [spot, perp].

Engine-side GTT semantics (resting, reaper-cancel, modify-refresh, settlement)
are proven transport-independently via REST in tests/engine/test_gtt_lifecycle.py
(also [spot, perp]). This module pins the ws-exec TRANSPORT's own GTT surface
over BOTH markets via the shared ``ws_exec_market`` fixture:

* request payload mapping + lifetime fidelity — a GTT createOrder over ws-exec
  reaches the real engine and rests OPEN, and its non-zero ``expiresAfter``
  round-trips to the resting order (asserted via REST openOrders read-back, the
  only fidelity oracle — the WS CreateOrderResponse carries no expiresAfter
  echo);
* full ws-exec lifetime — a GTT created over ws-exec with a near-future expiry
  is auto-reaped by the matching engine at ``expiresAfter`` with no explicit
  cancel (bounded: not before expiry, and within a finite window after),
  proving the ws-exec -> ME -> reaper path end-to-end on each market.

ws-exec routes createOrder through the shared TIF-agnostic handler (it has no
REST dispatcher order-class allow-list), so this also guards that a GTT is never
misrouted on the ws-exec transport. Missing ws-exec URL/account prerequisites
fail by default (see ``ws_exec_market``).
"""

from __future__ import annotations

import asyncio
import time

import pytest

from sdk.async_exec_api.order_status import OrderStatus
from sdk.open_api.models.order import Order
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api import ReyaTradingClient
from sdk.reya_rest_api.models.orders import LimitOrderParameters
from tests.helpers.ws_exec_market import WsExecMarket

pytestmark = [pytest.mark.e2e, pytest.mark.gtt]

# Comfortable lifetime for the rest/read-back test (completes in well under a
# second, so it never expires mid-test).
GTT_LIFETIME_S = 300

# Bounded-reap timing — mirrors tests/engine/test_gtt_lifecycle.py. GTT expiry is
# wall-clock (the ME reaper scans every ~500ms), so these are REAL waits; the
# margins absorb ME<->test clock skew and devnet Redis->indexer->openOrders lag.
GTT_REAP_DEADLINE_OFFSET_S = 20
GTT_REAP_EXPIRY_OFFSET_S = 55
GTT_REAP_PRE_EXPIRY_MARGIN_S = 15
GTT_REAP_DETECT_BOUND_S = 40


async def _wait_for_open_order(rest: ReyaTradingClient, order_id: str, timeout_s: float = 10.0) -> Order:
    """Poll openOrders until `order_id` appears — the OrdersProvider cache
    consumes the ME's Redis stream asynchronously w.r.t. the ws-exec ack."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        open_orders = await rest.get_open_orders()
        order = next((o for o in open_orders if o.order_id == order_id), None)
        if order is not None:
            return order
        await asyncio.sleep(0.2)
    raise AssertionError(f"Order {order_id} not visible via REST within {timeout_s}s")


async def _wait_for_order_gone(rest: ReyaTradingClient, order_id: str, timeout_s: float) -> None:
    """Poll openOrders until `order_id` is no longer resting — a reaped GTT is
    cancelled and drops out of openOrders."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        open_ids = {o.order_id for o in await rest.get_open_orders()}
        if order_id not in open_ids:
            return
        await asyncio.sleep(0.5)
    raise AssertionError(f"Order {order_id} still resting after {timeout_s}s; reaper did not cancel it")


async def test_ws_exec_gtt_rests_and_reads_back(ws_exec_market: WsExecMarket):
    """Transport concern: request payload mapping + lifetime fidelity. A GTT
    createOrder over ws-exec reaches the real engine and rests OPEN; its
    non-zero expiresAfter round-trips to the resting order via REST openOrders
    read-back (WsCreateOrderResponse carries no expiresAfter echo)."""
    m = ws_exec_market
    expires_after = int(time.time()) + GTT_LIFETIME_S

    create = await m.ws.create_limit_order(
        LimitOrderParameters(
            symbol=m.symbol,
            is_buy=True,
            limit_px=m.rest_buy_px,
            qty=m.min_qty,
            time_in_force=TimeInForce.GTT,
            expires_after=expires_after,
        )
    )
    order_id = create.order_id
    assert order_id is not None, f"[{m.market_type}] GTT createOrder OK but missing orderId: {create}"
    assert (
        create.status == OrderStatus.OPEN
    ), f"[{m.market_type}] GTT away from the touch must rest OPEN: {create.status}"

    try:
        order = await _wait_for_open_order(m.rest, order_id)
        assert (
            int(order.expires_after or 0) == expires_after
        ), f"[{m.market_type}] GTT expiresAfter must read back via REST: {order.expires_after} != {expires_after}"
        print(f"  [ws-exec {m.market_type}] GTT rested OPEN with expiresAfter={expires_after} orderId={order_id}")
    finally:
        await m.ws.cancel_order(order_id=order_id, symbol=m.symbol, account_id=m.rest.config.account_id)


async def test_ws_exec_gtt_reaped_at_expiry(ws_exec_market: WsExecMarket):
    """Full ws-exec lifetime: a GTT created over ws-exec with a near-future
    expiry is auto-reaped by the matching engine at expiresAfter — no explicit
    cancel — proving the ws-exec -> ME -> reaper path end-to-end. Bounded both
    sides: it must STILL rest just before expiry (reaper not early) and be gone
    within a finite window after it (not merely "eventually")."""
    m = ws_exec_market
    now = int(time.time())
    deadline = now + GTT_REAP_DEADLINE_OFFSET_S
    expires_after = now + GTT_REAP_EXPIRY_OFFSET_S  # strictly after the deadline (the GTT coupling)

    create = await m.ws.create_limit_order(
        LimitOrderParameters(
            symbol=m.symbol,
            is_buy=True,
            limit_px=m.rest_buy_px,
            qty=m.min_qty,
            time_in_force=TimeInForce.GTT,
            expires_after=expires_after,
            deadline=deadline,
        )
    )
    order_id = create.order_id
    assert order_id is not None, f"[{m.market_type}] GTT createOrder OK but missing orderId: {create}"

    await _wait_for_open_order(m.rest, order_id)  # rests before expiry

    # Lower bound: shortly BEFORE expiry the GTT must still be resting (not reaped early).
    remaining = (expires_after - GTT_REAP_PRE_EXPIRY_MARGIN_S) - time.time()
    if remaining > 0:
        await asyncio.sleep(remaining)
    open_ids = {o.order_id for o in await m.rest.get_open_orders()}
    assert order_id in open_ids, f"[{m.market_type}] GTT was reaped BEFORE its expiresAfter"

    # Upper bound: reaped (drops out of openOrders) within a finite window after expiry.
    await _wait_for_order_gone(m.rest, order_id, timeout_s=GTT_REAP_PRE_EXPIRY_MARGIN_S + GTT_REAP_DETECT_BOUND_S + 10)
    lateness = time.time() - expires_after
    assert (
        lateness <= GTT_REAP_DETECT_BOUND_S
    ), f"[{m.market_type}] GTT reap observed {lateness:.0f}s after expiry (> {GTT_REAP_DETECT_BOUND_S}s bound)"
    print(
        f"  [ws-exec {m.market_type}] GTT auto-reaped {lateness:.0f}s after expiresAfter (bounded, no explicit cancel)"
    )
