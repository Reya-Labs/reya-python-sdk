"""Rate-Limit v1 §3 (P14) — the READ-side inbound message-rate cap.

The market-data socket carries the same transport guard the ws-exec relayer
does: a per-connection token bucket checked **before the frame is parsed**, so a
malformed flood costs a bucket read and nothing else. A connection over the cap
is closed with :data:`WS_CLOSE_MSG_RATE_EXCEEDED` (4029) and a reason carrying
the backoff hint.

This is a **different surface** from ``test_ws_msg_rate_cap.py``, and flooding
one proves nothing about the other: separate processes, separate sizing knobs
(``RL_TEST_MD_WS_INBOUND_MSG_BURST`` vs ``RL_TEST_WS_INBOUND_MSG_BURST``), and
only this one is reachable without an order-entry credential — a public read
socket is the cheapest thing on the exchange to point a flood at.

What is asserted is the CROSS-SURFACE contract, not a second copy of the
order-entry module's:

1. the close code is ``4029`` — the same constant the ws-exec client exports,
   which is the point: one code across both sockets means a client needs one
   branch, not a per-surface special case;
2. the reason follows the shared ``MSG_RATE_EXCEEDED retry_after_ms=<n>``
   grammar, byte-for-byte what the order-entry side emits, with a plausible
   hint. Both AsyncAPI descriptions document that string, and both live floods
   assert it through the same helper;
3. reconnecting after the advertised hint works — the shed is per-connection,
   not a ban. That leg doubles as the non-vacuity control: a socket that closed
   every connection regardless would fail it.

The read side deliberately has no client-obligation legs (indeterminate
in-flight requests, cancel-on-disconnect): nothing here mutates state, so a
dropped market-data frame costs a resubscribe and a fresh snapshot.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from sdk.reya_ws_exec import WS_CLOSE_MSG_RATE_EXCEEDED
from tests.helpers.ws_exec_harness import raw_connect
from tests.rate_limits.rl_config import RateLimitSuiteConfig, requires_rate_limits
from tests.rate_limits.rl_errors import assert_msg_rate_close_reason
from tests.rate_limits.rl_ws_probe import await_close, close_quietly, flood_pings, recv_json, send_json

logger = logging.getLogger("reya.rate_limits")

pytestmark = [pytest.mark.rate_limits, pytest.mark.market_data, requires_rate_limits]

#: Multiple of the configured burst to fire. Generous: the flood has to out-run
#: a token bucket refilling underneath it.
FLOOD_MULTIPLE = 3

#: How long to wait for the close frame after the flood stops.
CLOSE_OBSERVE_TIMEOUT_S = 10.0

#: How long the reconnected socket may take to answer one ping.
RECONNECT_REPLY_TIMEOUT_S = 15.0


async def test_market_data_ws_flood_is_shed_with_4029_and_reconnects(
    rl_suite_config: RateLimitSuiteConfig,
    rl_md_ws_url: str,
) -> None:
    """Flood past the read-side inbound cap → 4029 + shared reason, then reconnect."""
    burst = rl_suite_config.md_ws_inbound_msg_burst
    if burst is None:
        pytest.skip(
            "read-side message-rate coverage needs RL_TEST_MD_WS_INBOUND_MSG_BURST (the market-data socket's "
            "INBOUND_MSG_RATE_BURST as deployed); it is a separate surface from RL_TEST_WS_INBOUND_MSG_BURST"
        )

    flood = max(2, burst * FLOOD_MULTIPLE)
    ws = raw_connect(rl_md_ws_url)
    try:
        sent = await asyncio.to_thread(flood_pings, ws, flood)
        code, reason = await asyncio.to_thread(await_close, ws, CLOSE_OBSERVE_TIMEOUT_S)
    finally:
        close_quietly(ws)

    assert code == WS_CLOSE_MSG_RATE_EXCEEDED, (
        f"flooding {flood} frames ({sent} accepted by the transport) must trip the market-data socket's "
        f"per-connection inbound cap and close {WS_CLOSE_MSG_RATE_EXCEEDED}; observed close code {code} — "
        "raise RL_TEST_MD_WS_INBOUND_MSG_BURST if the deployment's INBOUND_MSG_RATE_BURST is larger"
    )
    hint_s = assert_msg_rate_close_reason(reason, rl_suite_config.timing.retry_after_max_s, "market-data 4029 close")
    logger.info("market-data inbound cap: %d frames → close %s %r (hint %.3fs)", flood, code, reason, hint_s)

    # Reconnecting sooner than the hint is simply shed again, so wait it out —
    # and the fresh connection answering is what proves the shed was per
    # connection rather than a ban on the client.
    await asyncio.sleep(hint_s + rl_suite_config.timing.retry_after_slack_s)

    fresh = raw_connect(rl_md_ws_url)
    try:
        await asyncio.to_thread(send_json, fresh, {"type": "ping"})
        frame = await asyncio.to_thread(recv_json, fresh, RECONNECT_REPLY_TIMEOUT_S)
    finally:
        close_quietly(fresh)

    assert frame is not None, (
        f"a connection opened {hint_s:.3f}s after the {WS_CLOSE_MSG_RATE_EXCEEDED} close answered nothing within "
        f"{RECONNECT_REPLY_TIMEOUT_S}s: the shed must be per-connection, not a ban on the client"
    )
    logger.info("market-data reconnect after the 4029 shed answered: %s", frame.get("type"))
