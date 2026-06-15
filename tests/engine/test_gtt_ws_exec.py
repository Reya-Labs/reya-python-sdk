"""Good-Till-Time (GTT) over ws-exec — live e2e.

Engine-side GTT semantics (resting, reaper-cancel, modify-refresh, settlement)
are proven transport-independently via REST in tests/engine/test_gtt_lifecycle.py
(parametrized [spot, perp]). This module pins the ws-exec transport's OWN GTT
surface, spot-pinned like the modify / post-only / cod ws-exec suites:

* request payload mapping + lifetime fidelity — a GTT createOrder over ws-exec
  reaches the real engine and rests OPEN, and its non-zero ``expiresAfter``
  round-trips to the resting order (asserted via REST openOrders read-back, the
  only fidelity oracle — the WS CreateOrderResponse carries no expiresAfter
  echo);
* full ws-exec lifetime — a GTT created over ws-exec with a near-future expiry
  is auto-reaped by the matching engine at ``expiresAfter`` with no explicit
  cancel, proving the ws-exec → ME → reaper path end-to-end.

ws-exec routes createOrder through the shared TIF-agnostic handler (it does not
have the REST dispatcher's order-class allow-list), so this also guards that a
GTT is never misrouted on the ws-exec transport. Gated on the same env as
tests/ws_exec/test_ws_exec.py.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass

import pytest
import pytest_asyncio
from dotenv import load_dotenv

from sdk.async_exec_api.order_status import OrderStatus
from sdk.open_api.models.order import Order
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api import ReyaTradingClient
from sdk.reya_rest_api.config import TradingConfig
from sdk.reya_rest_api.models.orders import LimitOrderParameters
from sdk.reya_ws_exec import ReyaWsExecClient

load_dotenv()

_REQUIRED_ENV = (
    "REYA_WS_EXEC_URL",
    "SPOT_PRIVATE_KEY_1",
    "SPOT_ACCOUNT_ID_1",
    "SPOT_WALLET_ADDRESS_1",
)
_MISSING_ENV = [_k for _k in _REQUIRED_ENV if not os.environ.get(_k)]

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.gtt,
    pytest.mark.skipif(
        bool(_MISSING_ENV),
        reason="ws-exec GTT tests need " + ", ".join(_REQUIRED_ENV) + "; missing: " + ", ".join(_MISSING_ENV),
    ),
]

SPOT_SYMBOL = "WETHRUSD"
# Far-out resting buy on an ETH-priced book (same convention as the other
# ws-exec suites): the GTT rests until it expires or is cancelled.
REST_BUY_PX = "1"
# Comfortable lifetime for the rest/read-back test (completes in well under a
# second, so it never expires mid-test).
GTT_LIFETIME_S = 300


@dataclass
class _GttWsHarness:
    """Shared, module-scoped state for the GTT ws-exec flows."""

    rest: ReyaTradingClient
    ws: ReyaWsExecClient
    min_qty: str


@pytest_asyncio.fixture(loop_scope="session", scope="module")
async def gtt_ws_harness():
    """A started spot REST client + connected ws-exec client + market metadata.

    Mirrors post_only_ws_harness / modify_ws_harness: everything after start()
    runs inside the try so a skip (or a connect failure) never leaks the
    aiohttp session.
    """
    config = TradingConfig.from_env_spot(account_number=1)
    rest = ReyaTradingClient(config)
    await rest.start()
    ws: ReyaWsExecClient | None = None
    try:
        markets = {m.symbol: m for m in await rest.reference.get_spot_market_definitions()}
        if SPOT_SYMBOL not in markets:
            pytest.skip(f"{SPOT_SYMBOL} not found in /spotMarketDefinitions")
        market = markets[SPOT_SYMBOL]
        ws = ReyaWsExecClient(rest_client=rest, ws_url=os.environ["REYA_WS_EXEC_URL"])
        await ws.connect()
        yield _GttWsHarness(rest=rest, ws=ws, min_qty=str(market.min_order_qty))
    finally:
        if ws is not None:
            await ws.close()
        await rest.close()


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


async def _wait_for_order_gone(rest: ReyaTradingClient, order_id: str, timeout_s: float = 90.0) -> None:
    """Poll openOrders until `order_id` is no longer resting — a reaped GTT is
    cancelled and drops out of openOrders."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        open_ids = {o.order_id for o in await rest.get_open_orders()}
        if order_id not in open_ids:
            return
        await asyncio.sleep(0.5)
    raise AssertionError(f"Order {order_id} still resting after {timeout_s}s; reaper did not cancel it")


async def test_ws_exec_gtt_rests_and_reads_back(gtt_ws_harness):  # pylint: disable=redefined-outer-name
    """Transport concern: request payload mapping + lifetime fidelity. A GTT
    createOrder over ws-exec reaches the real engine and rests OPEN; its
    non-zero expiresAfter round-trips to the resting order via REST openOrders
    read-back (WsCreateOrderResponse carries no expiresAfter echo)."""
    h = gtt_ws_harness
    expires_after = int(time.time()) + GTT_LIFETIME_S

    create = await h.ws.create_limit_order(
        LimitOrderParameters(
            symbol=SPOT_SYMBOL,
            is_buy=True,
            limit_px=REST_BUY_PX,
            qty=h.min_qty,
            time_in_force=TimeInForce.GTT,
            expires_after=expires_after,
        )
    )
    order_id = create.order_id
    assert order_id is not None, f"GTT createOrder OK but missing orderId: {create}"
    assert create.status == OrderStatus.OPEN, f"GTT away from the touch must rest OPEN: {create.status}"

    try:
        order = await _wait_for_open_order(h.rest, order_id)
        assert (
            int(order.expires_after or 0) == expires_after
        ), f"GTT expiresAfter must read back via REST: {order.expires_after} != {expires_after}"
        print(f"  [ws-exec] GTT rested OPEN with expiresAfter={expires_after} orderId={order_id}")
    finally:
        await h.ws.cancel_order(order_id=order_id, symbol=SPOT_SYMBOL, account_id=h.rest.config.account_id)


async def test_ws_exec_gtt_reaped_at_expiry(gtt_ws_harness):  # pylint: disable=redefined-outer-name
    """Full ws-exec lifetime: a GTT created over ws-exec with a near-future
    expiry is auto-reaped by the matching engine at expiresAfter — no explicit
    cancel — proving the ws-exec → ME → reaper path end-to-end. Short
    deadline + expiry (expiresAfter must be strictly after the deadline) keeps
    the reap inside the poll window."""
    h = gtt_ws_harness
    now = int(time.time())
    deadline = now + 25
    expires_after = now + 35  # strictly after the deadline; reaped shortly after

    create = await h.ws.create_limit_order(
        LimitOrderParameters(
            symbol=SPOT_SYMBOL,
            is_buy=True,
            limit_px=REST_BUY_PX,
            qty=h.min_qty,
            time_in_force=TimeInForce.GTT,
            expires_after=expires_after,
            deadline=deadline,
        )
    )
    order_id = create.order_id
    assert order_id is not None, f"GTT createOrder OK but missing orderId: {create}"

    resting = await _wait_for_open_order(h.rest, order_id)
    assert resting.status is not None  # rests before expiry
    # Do NOT cancel — the reaper must auto-cancel it at expiresAfter.
    await _wait_for_order_gone(h.rest, order_id)
    print(f"  [ws-exec] GTT auto-reaped at expiresAfter (no explicit cancel) orderId={order_id}")
