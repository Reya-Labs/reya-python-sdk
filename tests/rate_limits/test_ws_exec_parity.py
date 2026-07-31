"""Rate-Limit v1 §8 — ws-exec parity for the gate and the place bucket.

The same verdicts REST maps to 429 / 403 arrive on ws-exec as the per-operation
error envelope ``{ok:false, error:{error, message, retryAfterMs?}}``. These
tests drive a RAW WebSocket (the harness the ws-exec suite already uses) rather
than :class:`ReyaWsExecClient`, because ``WsExecOperationError`` exposes only
``code`` / ``message`` / ``request_id`` and drops ``retryAfterMs`` — the field
the wire contract adds.
"""

from __future__ import annotations

import logging
import uuid

import pytest

from sdk.reya_rest_api import ReyaTradingClient
from tests.helpers.ws_exec_harness import raw_connect, raw_recv_until, raw_send_envelope
from tests.rate_limits.rl_actions import RlMarket, ensure_flat, resolve_market, resting_order
from tests.rate_limits.rl_config import (
    NOT_WHITELISTED_ERROR,
    RATE_LIMITED_ERROR,
    RateLimitSuiteConfig,
    requires_rate_limits,
)
from tests.rate_limits.rl_errors import WsReject, ws_reject

logger = logging.getLogger("reya.rate_limits")

pytestmark = [pytest.mark.rate_limits, requires_rate_limits]


def _envelope_id() -> str:
    return uuid.uuid4().hex[:12]


def _send_create(ws, client: ReyaTradingClient, market: RlMarket) -> dict:
    """Sign a resting GTC, push it over the raw socket, return the reply frame."""
    payload, _nonce = client.build_create_limit_order_payload(resting_order(market))
    env_id = _envelope_id()
    raw_send_envelope(ws, "createOrder", env_id, payload)
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
