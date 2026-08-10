"""Rate-Limit v1 §7 — the ws-exec per-connection inbound message-rate cap.

This is the one v1 control that is neither a REST status nor an error envelope:
the relayer checks the inbound rate **before parse and before ``ecrecover``**
(that placement is the point — a garbage-signed flood must cost nothing) and,
when a connection exceeds it, answers by **closing the connection** with status
:data:`WS_CLOSE_MSG_RATE_EXCEEDED` and a reason carrying the backoff hint.

Three client obligations follow from the AsyncAPI description, and all three are
asserted here:

1. the close code is readable — a client that swallows a server close alongside
   its own recv timeout cannot tell a rate kill from an idle socket;
2. **in-flight requests are INDETERMINATE**, not failed. The relayer may have
   forwarded them to the matching engine before closing, so the only correct
   recovery is to reconnect and reconcile against
   ``GET /v2/wallet/{address}/openOrders``. Surfacing them as a plain timeout
   would invite a client to re-send an order that already rested;
3. reconnecting works — the kill is per-connection, not a ban.

The suite's high-level :class:`ReyaWsExecClient` is used deliberately (the other
ws-exec modules use the raw harness): obligations 1 and 2 are properties of the
CLIENT, so a raw socket would prove nothing about them.
"""

from __future__ import annotations

import asyncio
import logging
import re

import pytest

from sdk.reya_rest_api import ReyaTradingClient
from sdk.reya_ws_exec import WS_CLOSE_MSG_RATE_EXCEEDED, ReyaWsExecClient, WsExecConnectionClosedError
from tests.rate_limits.rl_config import RateLimitSuiteConfig, requires_rate_limits

logger = logging.getLogger("reya.rate_limits")

pytestmark = [pytest.mark.rate_limits, requires_rate_limits]

#: The reason string the relayer pairs with the 4029 close. Pinned as a pattern
#: rather than a literal because the hint value is deployment-dependent.
CLOSE_REASON_PATTERN = re.compile(r"^MSG_RATE_EXCEEDED retry_after_ms=\d+$")

#: Multiple of the configured burst to fire. Generous: the flood has to out-run
#: a token bucket that is refilling underneath it.
FLOOD_MULTIPLE = 3

#: How long to wait for the close frame to reach the reader thread after the
#: flood stops.
CLOSE_OBSERVE_TIMEOUT_S = 10.0
CLOSE_POLL_S = 0.1


async def _await_close(client: ReyaWsExecClient) -> int | None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + CLOSE_OBSERVE_TIMEOUT_S
    while loop.time() < deadline:
        code = client.last_close_code
        if code is not None:
            return code
        await asyncio.sleep(CLOSE_POLL_S)
    return client.last_close_code


async def test_ws_exec_msg_rate_flood_closes_4029_and_surfaces_indeterminate(
    rl_client: ReyaTradingClient,
    rl_suite_config: RateLimitSuiteConfig,
    rl_ws_exec_url: str,
) -> None:
    """Flood past the inbound cap → 4029 close, indeterminate in-flight, reconnect."""
    client = ReyaWsExecClient(rest_client=rl_client, ws_url=rl_ws_exec_url)
    await client.connect()

    flood = max(2, rl_suite_config.ws_inbound_msg_burst * FLOOD_MULTIPLE)
    try:
        # JSON-layer pings: the cheapest message that still counts against the
        # inbound cap, and one the relayer checks BEFORE parsing — so the flood
        # measures the rate guard and nothing downstream of it.
        results = await asyncio.gather(*(client.ping() for _ in range(flood)), return_exceptions=True)

        indeterminate = [r for r in results if isinstance(r, WsExecConnectionClosedError)]
        close_code = await _await_close(client)

        assert close_code == WS_CLOSE_MSG_RATE_EXCEEDED, (
            f"flooding {flood} messages must trip the per-connection inbound cap and close "
            f"{WS_CLOSE_MSG_RATE_EXCEEDED}; observed close code {close_code} — raise "
            "RL_TEST_WS_INBOUND_MSG_BURST if the deployment's WS_EXEC_INBOUND_MSG_RATE_BURST is larger"
        )

        reason = client.last_close_reason
        assert reason is not None and CLOSE_REASON_PATTERN.match(reason), (
            f"the {WS_CLOSE_MSG_RATE_EXCEEDED} close must carry the backoff hint in its reason "
            f"(expected {CLOSE_REASON_PATTERN.pattern}); got {reason!r}"
        )

        assert indeterminate, (
            "every request in flight when the connection died must surface as an explicit "
            "indeterminate outcome, not hang to its own response deadline; got "
            f"{sorted({type(r).__name__ for r in results if isinstance(r, BaseException)})}"
        )
        assert all(
            error.close_code == WS_CLOSE_MSG_RATE_EXCEEDED for error in indeterminate
        ), "the indeterminate error must name the close code the caller has to branch on"
        logger.info(
            "ws-exec inbound cap: %d messages → close %s %r, %d in-flight surfaced as indeterminate",
            flood,
            close_code,
            reason,
            len(indeterminate),
        )
    finally:
        await client.close()

    # The kill is per-connection, not a ban: a fresh connection works, which is
    # what makes "reconnect and reconcile" a recovery rather than an outage.
    await asyncio.sleep(rl_suite_config.timing.retry_after_slack_s)
    async with ReyaWsExecClient(rest_client=rl_client, ws_url=rl_ws_exec_url) as reconnected:
        await reconnected.ping()
    logger.info("ws-exec reconnect after the 4029 close succeeded")
