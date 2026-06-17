"""
Cancel-on-disconnect (cancelAllAfter) over ws-exec — live e2e.

Same arm/refresh/disarm semantics as the REST surface (the switch is
transport-agnostic), driven through :class:`ReyaWsExecClient`. Gated on the
same env as tests/ws_exec/test_ws_exec.py: without `REYA_WS_EXEC_URL` +
SPOT_*_1 credentials the module collects-and-skips.

The bad-payload error-envelope cases reuse the raw-WebSocket helpers from
tests/ws_exec/test_ws_exec.py — the high-level client rejects an out-of-range
timeoutMs locally (and never builds a tampered signature), so the negative
probes must go over a raw socket with a hand-built (correctly signed) payload.

The fire test rests a real spot order and lets the WS-armed countdown
mass-cancel it, proving the arm is dispatched into the engine rather than
merely echoed. Engine-internal countdown choreography (refresh, disarm
prevents fire, account isolation, rearm) stays REST-only — see
tests/engine/test_cod_lifecycle.py.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid

import pytest
import pytest_asyncio
from dotenv import load_dotenv

from sdk.open_api.models.order import Order
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api import ReyaTradingClient
from sdk.reya_rest_api.config import TradingConfig
from sdk.reya_rest_api.models.orders import LimitOrderParameters
from sdk.reya_ws_exec import ReyaWsExecClient
from tests.helpers.ws_exec_harness import assert_per_op_error, raw_connect, raw_recv_until, raw_send_envelope

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
    pytest.mark.cod,
    pytest.mark.skipif(
        bool(_MISSING_ENV),
        reason="ws-exec COD tests need " + ", ".join(_REQUIRED_ENV) + "; missing: " + ", ".join(_MISSING_ENV),
    ),
]

# triggerAt is stamped on the ME clock; this window absorbs client↔ME clock
# skew + round-trip latency. Dev runners (esp. WSL2) drift several seconds from
# the NTP-synced cluster (measured up to ~2.7s), so 2s flaked intermittently.
# 5s covers realistic dev skew while still catching any gross ME bug (wrong
# unit / missing timeout are off by ≥30,000ms). Mirror of test_cod_lifecycle.py.
TRIGGER_AT_WINDOW_MS = 5_000

SPOT_SYMBOL = "WETHRUSD"
# Far-out resting price on an ETH-priced book (same convention as
# tests/ws_exec/test_ws_exec.py): the GTC rests until cancelled or COD fires.
REST_PX = "1"
FIRE_TIMEOUT_MS = 5_000
# The ME scans armed countdowns on a ~500ms tick; allow that plus clock skew
# and request latency before declaring a fire missed (mirrors
# tests/engine/test_cod_lifecycle.py).
FIRE_MARGIN_S = 3.0


async def _wait_for_open_order(rest: ReyaTradingClient, order_id: str, timeout_s: float = 10.0) -> Order:
    """Poll openOrders until `order_id` appears — the OrdersProvider cache
    consumes the ME's Redis stream asynchronously w.r.t. the ws-exec ack."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        order = next((o for o in await rest.get_open_orders() if o.order_id == order_id), None)
        if order is not None:
            return order
        await asyncio.sleep(0.2)
    raise AssertionError(f"Order {order_id} not visible via REST within {timeout_s}s")


async def _wait_until_no_open_orders(rest: ReyaTradingClient, timeout_s: float) -> None:
    """Poll openOrders until the account has none — proves the fire emptied it."""
    deadline = time.time() + timeout_s
    remaining: list[Order] = []
    while time.time() < deadline:
        remaining = await rest.get_open_orders()
        if not remaining:
            return
        await asyncio.sleep(0.5)
    raise AssertionError(
        f"Account still has {len(remaining)} open order(s) {timeout_s}s after arming: "
        f"{[o.order_id for o in remaining]}"
    )


@pytest_asyncio.fixture(loop_scope="session", scope="module")
async def cod_ws_harness():
    """A started spot REST client + connected ws-exec client.

    Yields ``(rest, ws)``; disarms COD in teardown so an armed countdown
    never leaks from a failed assertion onto the shared devnet account.
    """
    config = TradingConfig.from_env_spot(account_number=1)
    rest = ReyaTradingClient(config)
    await rest.start()
    ws = ReyaWsExecClient(rest_client=rest, ws_url=os.environ["REYA_WS_EXEC_URL"])
    await ws.connect()
    try:
        yield rest, ws
    finally:
        try:
            await ws.cancel_all_after(timeout_ms=0)
        except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught  # nosec B110
            pass  # best-effort teardown disarm
        await ws.close()
        await rest.close()


async def test_ws_exec_arm_disarm(cod_ws_harness):  # pylint: disable=redefined-outer-name
    """Arm + disarm over ws-exec: triggerAt echoed on arm (ME clock, ±2s),
    absent on disarm."""
    _rest, ws = cod_ws_harness

    sent_at_ms = time.time() * 1000
    armed = await ws.cancel_all_after(timeout_ms=30_000)
    try:
        assert armed.timeout_ms == 30_000
        assert armed.trigger_at is not None, f"Armed countdown must echo triggerAt: {armed}"
        drift = armed.trigger_at - (sent_at_ms + 30_000)
        assert abs(drift) <= TRIGGER_AT_WINDOW_MS, f"triggerAt drift {drift:+.0f}ms exceeds ±{TRIGGER_AT_WINDOW_MS}ms"
        print(f"  [ws-exec] armed OK triggerAt={armed.trigger_at}")
    finally:
        disarmed = await ws.cancel_all_after(timeout_ms=0)

    assert disarmed.timeout_ms == 0
    assert disarmed.trigger_at is None, f"Disarm must not echo a triggerAt: {disarmed}"
    print("  [ws-exec] disarm OK (no triggerAt)")


async def test_ws_exec_arm_fires_cancels_resting_order(cod_ws_harness):  # pylint: disable=redefined-outer-name
    """A cancelAllAfter ARM sent over ws-exec drives the REAL engine countdown.

    Transport-layer concern: request mapping/dispatch WITH effect — the arm
    must reach the matching engine's dead-man's-switch (not merely echo
    triggerAt back), proven by a resting order actually being mass-cancelled
    when the WS-armed countdown fires. The countdown's internal choreography
    is pinned REST-side in tests/engine/test_cod_lifecycle.py.
    """
    rest, ws = cod_ws_harness

    markets = {m.symbol: m for m in await rest.reference.get_spot_market_definitions()}
    if SPOT_SYMBOL not in markets:
        pytest.skip(f"{SPOT_SYMBOL} not found in /spotMarketDefinitions")
    min_qty = str(markets[SPOT_SYMBOL].min_order_qty)

    create = await ws.create_limit_order(
        LimitOrderParameters(
            symbol=SPOT_SYMBOL,
            is_buy=True,
            limit_px=REST_PX,
            qty=min_qty,
            time_in_force=TimeInForce.GTC,
        )
    )
    assert create.order_id is not None
    order_id = create.order_id

    fired = False
    try:
        await _wait_for_open_order(rest, order_id)

        armed = await ws.cancel_all_after(timeout_ms=FIRE_TIMEOUT_MS)
        assert armed.timeout_ms == FIRE_TIMEOUT_MS
        assert armed.trigger_at is not None, f"Armed countdown must echo triggerAt: {armed}"

        # timeout + ME scan granularity + clock-skew margin.
        await _wait_until_no_open_orders(rest, timeout_s=FIRE_TIMEOUT_MS / 1000 + FIRE_MARGIN_S)
        fired = True
        print(f"  [ws-exec] COD fired: order {order_id} cancelled by the WS-armed countdown")

        disarmed = await ws.cancel_all_after(timeout_ms=0)
        assert disarmed.timeout_ms == 0
        assert disarmed.trigger_at is None, f"Post-fire disarm must not echo a triggerAt: {disarmed}"
    finally:
        if not fired:
            # Failure path: disarm first so the countdown can't race the
            # cleanup cancel, then clear the resting order.
            try:
                await ws.cancel_all_after(timeout_ms=0)
            except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught  # nosec B110
                pass  # best-effort failure-path disarm
            try:
                await ws.cancel_order(order_id=order_id, symbol=SPOT_SYMBOL, account_id=rest.config.account_id)
            except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught  # nosec B110
                pass  # order may already be gone (late fire) — best-effort


async def test_ws_exec_out_of_range_timeout_error_envelope(cod_ws_harness):  # pylint: disable=redefined-outer-name
    """A correctly-signed cancelAllAfter with timeoutMs=1 (below the 5000ms
    floor) comes back as a per-op error envelope with INPUT_VALIDATION_ERROR.

    Sent over a raw socket because the high-level client refuses to build the
    payload (client guard pinned in tests/validation/test_client_guards.py).
    """
    rest, _ws = cod_ws_harness

    nonce = rest.get_next_nonce()
    deadline = int(time.time()) + 60
    payload = {
        "accountId": rest.config.account_id,
        "timeoutMs": 1,
        "signature": rest.signature_generator.sign_cancel_all_after(
            account_id=rest.config.account_id,
            timeout_ms=1,
            nonce=nonce,
            deadline=deadline,
        ),
        "nonce": str(nonce),
        "signerWallet": rest.signer_wallet_address,
        "deadline": deadline,
    }

    raw_ws = raw_connect(os.environ["REYA_WS_EXEC_URL"])
    try:
        env_id = uuid.uuid4().hex[:12]
        raw_send_envelope(raw_ws, "cancelAllAfter", env_id, payload)
        resp = raw_recv_until(raw_ws, lambda f: f.get("id") == env_id and "ok" in f)
        err = assert_per_op_error(resp, ("INPUT_VALIDATION_ERROR",), "cancelAllAfter timeoutMs=1")
        print(f"  [ws-exec] out-of-range timeoutMs rejected OK code={err.get('error')!r}")
    finally:
        raw_ws.close()


async def test_ws_exec_tampered_signature_error_envelope(cod_ws_harness):  # pylint: disable=redefined-outer-name
    """Sign timeoutMs=30000 but send timeoutMs=40000 — both in-range, so only
    signature recovery can reject — and expect the per-op error envelope with
    UNAUTHORIZED_SIGNATURE_ERROR.

    Transport-layer concern: signature-envelope mapping — ws-exec must route a
    signature-recovery failure into the per-op error envelope (mirror of REST
    tests/engine/test_cod_validation.py::test_tampered_signature_rejected),
    and the rejected request must not arm anything.
    """
    rest, ws = cod_ws_harness

    nonce = rest.get_next_nonce()
    deadline = int(time.time()) + 60
    payload = {
        "accountId": rest.config.account_id,
        "timeoutMs": 40_000,
        # Signed over 30_000 while the wire says 40_000: the recovered signer
        # saw different bytes than the payload, so recovery diverges.
        "signature": rest.signature_generator.sign_cancel_all_after(
            account_id=rest.config.account_id,
            timeout_ms=30_000,
            nonce=nonce,
            deadline=deadline,
        ),
        "nonce": str(nonce),
        "signerWallet": rest.signer_wallet_address,
        "deadline": deadline,
    }

    raw_ws = raw_connect(os.environ["REYA_WS_EXEC_URL"])
    try:
        env_id = uuid.uuid4().hex[:12]
        raw_send_envelope(raw_ws, "cancelAllAfter", env_id, payload)
        resp = raw_recv_until(raw_ws, lambda f: f.get("id") == env_id and "ok" in f)
        err = assert_per_op_error(resp, ("UNAUTHORIZED_SIGNATURE_ERROR",), "cancelAllAfter tampered timeoutMs")
        print(f"  [ws-exec] tampered timeoutMs rejected OK code={err.get('error')!r}")
    finally:
        raw_ws.close()

    # Defensive: prove the rejected request armed nothing.
    disarmed = await ws.cancel_all_after(timeout_ms=0)
    assert disarmed.trigger_at is None, f"Rejected arm must not leave a countdown: {disarmed}"
