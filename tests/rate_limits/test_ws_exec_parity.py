"""Rate-Limit v1 §8 — ws-exec parity for the gate and the place bucket.

The same verdicts REST maps to 429 / 403 arrive on ws-exec as the per-operation
error envelope ``{ok:false, error:{error, message, retryAfterMs?}}``. These
tests drive a RAW WebSocket (the harness the ws-exec suite already uses) rather
than :class:`ReyaWsExecClient`: what is under test is the RELAYER's envelope, so
reading it off the wire keeps the assertions independent of whatever the client
chooses to expose. ``WsExecOperationError`` does now carry ``retry_after_ms``
(see the suite README's findings), and ``test_offline_error_surface`` pins that
accessor — this module deliberately does not route through it.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

import pytest

from sdk.reya_rest_api import ReyaTradingClient
from tests.helpers.ws_exec_harness import raw_connect, raw_recv_until, raw_send_envelope
from tests.rate_limits.rl_actions import (
    RlMarket,
    ensure_flat,
    place_paced,
    resolve_market,
    resting_order,
    wait_for_open_order_count,
)
from tests.rate_limits.rl_config import (
    ACCOUNT_SUSPENDED_ERROR,
    NOT_WHITELISTED_ERROR,
    OPEN_ORDER_COUNT_EXCEEDED_ERROR,
    RATE_LIMITED_ERROR,
    RateLimitSuiteConfig,
    requires_rate_limits,
)
from tests.rate_limits.rl_errors import WsReject, ws_reject

logger = logging.getLogger("reya.rate_limits")

pytestmark = [pytest.mark.rate_limits, requires_rate_limits]

#: The shortest countdown the wire contract accepts; used for the arm that must
#: be REFUSED, so nothing is left armed even if the deployment admits it.
COD_ARM_TIMEOUT_MS = 5000


def _envelope_id() -> str:
    return uuid.uuid4().hex[:12]


def _send_create(ws, client: ReyaTradingClient, market: RlMarket) -> dict:
    """Sign a resting GTC, push it over the raw socket, return the reply frame."""
    payload, _nonce = client.build_create_limit_order_payload(resting_order(market))
    env_id = _envelope_id()
    raw_send_envelope(ws, "createOrder", env_id, payload)
    return raw_recv_until(ws, lambda frame: frame.get("id") == env_id and "ok" in frame)


def _send_cancel_all_after(ws, client: ReyaTradingClient, timeout_ms: int) -> dict:
    """Sign a cancelAllAfter, push it over the raw socket, return the reply frame."""
    payload = client.build_cancel_all_after_payload(timeout_ms=timeout_ms, account_id=client.config.account_id)
    env_id = _envelope_id()
    raw_send_envelope(ws, "cancelAllAfter", env_id, payload)
    return raw_recv_until(ws, lambda frame: frame.get("id") == env_id and "ok" in frame)


async def test_ws_exec_whitelist_gate_envelope(
    rl_non_whitelisted_client: ReyaTradingClient,
    rl_suite_config: RateLimitSuiteConfig,
    rl_ws_exec_url: str,
) -> None:
    """A non-whitelisted create over ws-exec → NOT_WHITELISTED_ERROR envelope."""
    market = await resolve_market(rl_non_whitelisted_client, rl_suite_config.symbol)

    ws = raw_connect(rl_ws_exec_url)
    try:
        frame = _send_create(ws, rl_non_whitelisted_client, market)
    finally:
        ws.close()

    reject: WsReject = ws_reject(frame, "ws-exec whitelist gate")
    logger.info("ws-exec whitelist gate: %s", reject.describe())
    assert reject.code == NOT_WHITELISTED_ERROR, f"expected {NOT_WHITELISTED_ERROR}; got {reject.describe()}"


async def test_ws_exec_place_bucket_envelope(
    rl_client: ReyaTradingClient,
    rl_market: RlMarket,
    rl_suite_config: RateLimitSuiteConfig,
    rl_ws_exec_url: str,
) -> None:
    """Burst creates over ws-exec → RATE_LIMITED_ERROR envelope (+ retryAfterMs).

    ``retryAfterMs`` is asserted only for plausibility when present: the edge
    may serve the verdict from its negative-verdict cache, which recomputes the
    remaining milliseconds rather than echoing the ME's original stamp.
    """
    await ensure_flat(rl_client, rl_suite_config, rl_market.symbol)

    bound = rl_suite_config.burst_attempt_bound()
    accepted = 0
    reject: WsReject | None = None

    ws = raw_connect(rl_ws_exec_url)
    try:
        for _ in range(bound):
            frame = _send_create(ws, rl_client, rl_market)
            if frame.get("ok"):
                accepted += 1
                continue
            reject = ws_reject(frame, "ws-exec place burst")
            break
    finally:
        ws.close()

    assert reject is not None, (
        f"no ws-exec rejection within {bound} creates ({accepted} accepted); "
        "either rate limiting is not enabled or the configured Standard limits are far too low"
    )
    logger.info("ws-exec place bucket after %d accepted creates: %s", accepted, reject.describe())
    assert reject.code == RATE_LIMITED_ERROR, f"expected {RATE_LIMITED_ERROR}; got {reject.describe()}"

    if reject.retry_after_ms is not None:
        assert (
            0 < reject.retry_after_ms <= rl_suite_config.timing.retry_after_max_s * 1000
        ), f"implausible retryAfterMs on the ws-exec envelope: {reject.describe()}"


async def test_ws_exec_count_cap_envelope(
    rl_client: ReyaTradingClient,
    rl_market: RlMarket,
    rl_suite_config: RateLimitSuiteConfig,
    rl_ws_exec_url: str,
) -> None:
    """A create at the COUNT cap over ws-exec → OPEN_ORDER_COUNT_EXCEEDED_ERROR.

    ``retryAfterMs`` must be ABSENT: the proto sets it only alongside
    ``RATE_LIMITED``, and rightly so — waiting does not clear a count cap,
    cancelling does. A hint here would advertise a backoff that resolves nothing.
    """
    cap = rl_suite_config.limits.open_order_count_cap
    await ensure_flat(rl_client, rl_suite_config, rl_market.symbol)

    await place_paced(rl_client, rl_market, rl_suite_config, cap)
    await wait_for_open_order_count(
        rl_client,
        cap,
        timeout_s=rl_suite_config.timing.settle_timeout_s,
        symbol=rl_market.symbol,
    )
    await asyncio.sleep(rl_suite_config.timing.place_pace_s)

    ws = raw_connect(rl_ws_exec_url)
    try:
        frame = _send_create(ws, rl_client, rl_market)
    finally:
        ws.close()

    reject = ws_reject(frame, "ws-exec count cap")
    logger.info("ws-exec count cap: %s", reject.describe())
    assert reject.code == OPEN_ORDER_COUNT_EXCEEDED_ERROR, (
        f"expected {OPEN_ORDER_COUNT_EXCEEDED_ERROR} at the count cap; got {reject.describe()} — "
        "if this is RATE_LIMITED_ERROR the pacing is too fast (raise RL_TEST_PLACE_PACE_S)"
    )
    assert (
        reject.retry_after_ms is None
    ), f"a cap reject must carry no retryAfterMs (waiting does not free a slot); got {reject.describe()}"


async def test_ws_exec_ejected_cancel_all_after_arm_envelope(
    rl_client: ReyaTradingClient,
    rl_suite_config: RateLimitSuiteConfig,
    rl_ws_exec_url: str,
    rl_eject_hook,
) -> None:
    """An ejected account's cancelAllAfter ARM over ws-exec → ACCOUNT_SUSPENDED_ERROR.

    The ws-exec twin of the REST option-(b) leg in ``test_eject_flow``, and for
    the same reason the only ws-exec probe that can pin
    ``ACCOUNT_SUSPENDED_ERROR``: cancelAllAfter is never whitelist-gated, so the
    edge's whitelist verdict cannot answer instead. A create would race it.
    """
    wallet = rl_client.config.owner_wallet_address
    account_id = rl_client.config.account_id
    assert account_id is not None

    await ensure_flat(rl_client, rl_suite_config, rl_suite_config.symbol)

    rl_eject_hook.eject(wallet=wallet, account_id=account_id)
    try:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + rl_suite_config.timing.eject_timeout_s
        reject: WsReject | None = None
        while loop.time() < deadline:
            ws = raw_connect(rl_ws_exec_url)
            try:
                frame = _send_cancel_all_after(ws, rl_client, COD_ARM_TIMEOUT_MS)
            finally:
                ws.close()
            if not frame.get("ok"):
                reject = ws_reject(frame, "ws-exec ejected cancelAllAfter arm")
                if reject.code == ACCOUNT_SUSPENDED_ERROR:
                    break
            await asyncio.sleep(rl_suite_config.timing.poll_interval_s)
    finally:
        rl_eject_hook.uneject(wallet=wallet, account_id=account_id)

    assert reject is not None, (
        f"the cancelAllAfter arm was still accepted {rl_suite_config.timing.eject_timeout_s}s after eject; "
        "design option (b) requires an ejected account's arm to be refused"
    )
    logger.info("ws-exec ejected cancelAllAfter arm: %s", reject.describe())
    assert (
        reject.code == ACCOUNT_SUSPENDED_ERROR
    ), f"expected {ACCOUNT_SUSPENDED_ERROR} on an ejected arm; got {reject.describe()}"
    assert (
        reject.retry_after_ms is None
    ), f"an access-control refusal carries no retryAfterMs (the answer is not-you, not not-yet); {reject.describe()}"

    # An arm that was refused leaves nothing armed, but disarm anyway: this test
    # must not depend on the refusal to keep the account's countdown clear.
    await rl_client.cancel_all_after(timeout_ms=0, account_id=account_id)
