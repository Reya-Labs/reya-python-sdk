"""Cancel-on-disconnect (cancelAllAfter) over ws-exec — live e2e, parametrized [spot, perp].

cancelAllAfter is an account-level dead-man's-switch: arm/refresh/disarm a
countdown that, on expiry, mass-cancels EVERY resting order on the account. The
request carries no market dimension and the switch is account-scoped, so the
arm/refresh/disarm STATE machine plus account isolation and rearm are pinned
transport-independently via REST in tests/engine/test_cod_lifecycle.py (the fire
there is itself [spot, perp]). This module pins the ws-exec TRANSPORT's own COD
surface over BOTH markets via the shared ``ws_exec_market`` fixture (the ws-exec
client reuses the REST ``sign_cancel_all_after`` signing path verbatim, so the
two transports put byte-identical signed envelopes on the wire):

* arm/disarm echo fidelity — an arm over ws-exec echoes ``timeoutMs`` and a
  ``triggerAt`` ≈ now+timeoutMs on the ME clock; a disarm (timeoutMs=0) echoes
  no triggerAt;
* arm-with-effect — an arm sent over ws-exec drives the REAL engine countdown
  (not a mere echo), proven by a resting order actually being mass-cancelled
  when the WS-armed countdown fires;
* error-envelope mapping, both halves — an out-of-range timeoutMs and a tampered
  signature each surface as the per-op error envelope with the structured code.

The bad-payload error-envelope cases reuse the raw-WebSocket helpers from
tests/helpers/ws_exec_harness.py — the high-level client rejects an out-of-range
timeoutMs locally (and never builds a tampered signature), so the negative
probes must go over a raw socket with a hand-built (correctly signed) payload;
both sign over ``m.rest``'s account, so they run on each market unchanged. Each
missing ws-exec URL/account prerequisite fails by default (see
``ws_exec_market``).
"""

from __future__ import annotations

import asyncio
import os
import re
import time
import uuid

import pytest

from sdk.open_api.models.order import Order
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api import ReyaTradingClient
from sdk.reya_rest_api.models.orders import LimitOrderParameters
from tests.helpers.ws_exec_harness import assert_per_op_error, raw_connect, raw_recv_until, raw_send_envelope
from tests.helpers.ws_exec_market import WsExecMarket

pytestmark = [pytest.mark.e2e, pytest.mark.cod]

_RETRY_HINT_RE = re.compile(r"retry after (\d+) ms", re.IGNORECASE)


async def _cod_with_retry(ws, timeout_ms: int, *, attempts: int = 6):
    """cancel_all_after that honours RATE_LIMITED_ERROR retry hints.

    The cod-control bucket is flat across tiers (~100/min, burst 10) and an
    unarmed nonce-bearing disarm is refusable by the ME's no-op guard, so
    probe-heavy runs must pace on the hint. Returns (response, sent_at_ms)
    with sent_at_ms captured just before the successful attempt, so
    triggerAt-drift assertions stay anchored to the real send.
    """
    last_exc: Exception | None = None
    for _ in range(attempts):
        sent_at_ms = time.time() * 1000
        try:
            return await ws.cancel_all_after(timeout_ms=timeout_ms), sent_at_ms
        except Exception as exc:  # pylint: disable=broad-exception-caught
            text = str(exc)
            if "RATE_LIMITED_ERROR" not in text:
                raise
            hint = getattr(exc, "retry_after_ms", None)
            if not isinstance(hint, int) or hint <= 0:
                match = _RETRY_HINT_RE.search(text)
                hint = int(match.group(1)) if match else 1_000
            last_exc = exc
            await asyncio.sleep((hint + 100) / 1000)
    raise last_exc  # type: ignore[misc]


# triggerAt is stamped on the ME clock; this window absorbs client↔ME clock
# skew + round-trip latency. Dev runners (esp. WSL2) drift several seconds from
# the NTP-synced cluster (measured up to ~2.7s), so 2s flaked intermittently.
# 5s covers realistic dev skew while still catching any gross ME bug (wrong
# unit / missing timeout are off by ≥30,000ms). Mirror of test_cod_lifecycle.py.
TRIGGER_AT_WINDOW_MS = 5_000

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


async def test_ws_exec_arm_disarm(ws_exec_market: WsExecMarket):
    """Arm + disarm over ws-exec: triggerAt echoed on arm (ME clock, ±5s),
    absent on disarm."""
    m = ws_exec_market

    armed, sent_at_ms = await _cod_with_retry(m.ws, 30_000)
    try:
        assert armed.timeout_ms == 30_000, f"[{m.market_type}] arm must echo timeoutMs=30000: {armed.timeout_ms}"
        assert armed.trigger_at is not None, f"[{m.market_type}] armed countdown must echo triggerAt: {armed}"
        drift = armed.trigger_at - (sent_at_ms + 30_000)
        assert (
            abs(drift) <= TRIGGER_AT_WINDOW_MS
        ), f"[{m.market_type}] triggerAt drift {drift:+.0f}ms exceeds ±{TRIGGER_AT_WINDOW_MS}ms"
        print(f"  [ws-exec {m.market_type}] armed OK triggerAt={armed.trigger_at}")
    finally:
        disarmed, _ = await _cod_with_retry(m.ws, 0)

    assert disarmed.timeout_ms == 0, f"[{m.market_type}] disarm must echo timeoutMs=0: {disarmed.timeout_ms}"
    assert disarmed.trigger_at is None, f"[{m.market_type}] disarm must not echo a triggerAt: {disarmed}"
    print(f"  [ws-exec {m.market_type}] disarm OK (no triggerAt)")


async def test_ws_exec_arm_fires_cancels_resting_order(ws_exec_market: WsExecMarket):
    """A cancelAllAfter ARM sent over ws-exec drives the REAL engine countdown.

    Transport-layer concern: request mapping/dispatch WITH effect — the arm
    must reach the matching engine's dead-man's-switch (not merely echo
    triggerAt back), proven by a resting order actually being mass-cancelled
    when the WS-armed countdown fires. The countdown's internal choreography
    is pinned REST-side in tests/engine/test_cod_lifecycle.py.
    """
    m = ws_exec_market

    create = await m.ws.create_limit_order(
        LimitOrderParameters(
            symbol=m.symbol,
            is_buy=True,
            limit_px=m.rest_buy_px,
            qty=m.min_qty,
            time_in_force=TimeInForce.GTC,
        )
    )
    assert create.order_id is not None, f"[{m.market_type}] GTC createOrder OK but missing orderId: {create}"
    order_id = create.order_id

    fired = False
    try:
        await _wait_for_open_order(m.rest, order_id)

        armed, _ = await _cod_with_retry(m.ws, FIRE_TIMEOUT_MS)
        assert armed.timeout_ms == FIRE_TIMEOUT_MS, f"[{m.market_type}] arm must echo timeoutMs: {armed.timeout_ms}"
        assert armed.trigger_at is not None, f"[{m.market_type}] armed countdown must echo triggerAt: {armed}"

        # timeout + ME scan granularity + clock-skew margin.
        await _wait_until_no_open_orders(m.rest, timeout_s=FIRE_TIMEOUT_MS / 1000 + FIRE_MARGIN_S)
        fired = True
        print(f"  [ws-exec {m.market_type}] COD fired: order {order_id} cancelled by the WS-armed countdown")

        disarmed, _ = await _cod_with_retry(m.ws, 0)
        assert disarmed.timeout_ms == 0, f"[{m.market_type}] post-fire disarm must echo timeoutMs=0: {disarmed}"
        assert disarmed.trigger_at is None, f"[{m.market_type}] post-fire disarm must not echo a triggerAt: {disarmed}"
    finally:
        if not fired:
            # Failure path: disarm first so the countdown can't race the
            # cleanup cancel, then clear the resting order.
            try:
                await m.ws.cancel_all_after(timeout_ms=0)
            except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught  # nosec B110
                pass  # best-effort failure-path disarm
            try:
                await m.ws.cancel_order(order_id=order_id, symbol=m.symbol, account_id=m.rest.config.account_id)
            except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught  # nosec B110
                pass  # order may already be gone (late fire) — best-effort


async def test_ws_exec_out_of_range_timeout_error_envelope(ws_exec_market: WsExecMarket):
    """A correctly-signed cancelAllAfter with timeoutMs=1 (below the 5000ms
    floor) comes back as a per-op error envelope with INPUT_VALIDATION_ERROR.

    Sent over a raw socket because the high-level client refuses to build the
    payload (client guard pinned in tests/validation/test_client_guards.py).
    """
    m = ws_exec_market
    rest = m.rest
    assert rest.config.account_id is not None

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
        err = assert_per_op_error(resp, ("INPUT_VALIDATION_ERROR",), f"[{m.market_type}] cancelAllAfter timeoutMs=1")
        print(f"  [ws-exec {m.market_type}] out-of-range timeoutMs rejected OK code={err.get('error')!r}")
    finally:
        raw_ws.close()


async def test_ws_exec_tampered_signature_error_envelope(ws_exec_market: WsExecMarket):
    """Sign timeoutMs=30000 but send timeoutMs=40000 — both in-range, so only
    signature recovery can reject — and expect the per-op error envelope with
    UNAUTHORIZED_SIGNATURE_ERROR.

    Transport-layer concern: signature-envelope mapping — ws-exec must route a
    signature-recovery failure into the per-op error envelope (mirror of REST
    tests/engine/test_cod_validation.py::test_tampered_signature_rejected),
    and the rejected request must not arm anything.
    """
    m = ws_exec_market
    rest = m.rest
    assert rest.config.account_id is not None

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
        err = assert_per_op_error(
            resp, ("UNAUTHORIZED_SIGNATURE_ERROR",), f"[{m.market_type}] cancelAllAfter tampered timeoutMs"
        )
        print(f"  [ws-exec {m.market_type}] tampered timeoutMs rejected OK code={err.get('error')!r}")
    finally:
        raw_ws.close()

    # Defensive: prove the rejected request armed nothing.
    disarmed, _ = await _cod_with_retry(m.ws, 0)
    assert disarmed.trigger_at is None, f"[{m.market_type}] rejected arm must not leave a countdown: {disarmed}"
