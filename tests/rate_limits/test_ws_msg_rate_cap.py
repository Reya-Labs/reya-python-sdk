"""Rate-Limit v1 §7 — the ws-exec per-connection inbound message-rate cap.

This is the one v1 control that is neither a REST status nor an error envelope:
the relayer checks the inbound rate **before parse and before ``ecrecover``**
(that placement is the point — a garbage-signed flood must cost nothing) and,
when a connection exceeds it, answers by **closing the connection** with status
:data:`WS_CLOSE_MSG_RATE_EXCEEDED` and a reason carrying the backoff hint.

Four client obligations follow from the AsyncAPI description, and all four are
asserted here:

1. the close code is readable — a client that swallows a server close alongside
   its own recv timeout cannot tell a rate kill from an idle socket;
2. **in-flight requests are INDETERMINATE**, not failed. The relayer may have
   forwarded them to the matching engine before closing, so the only correct
   recovery is to reconnect and reconcile against
   ``GET /v2/wallet/{address}/openOrders``. Surfacing them as a plain timeout
   would invite a client to re-send an order that already rested;
3. reconnecting works — the kill is per-connection, not a ban;
4. the close does **not** fire cancel-on-disconnect. An armed ``cancelAllAfter``
   countdown runs on its own clock, so a client killed on 4029 finds its book
   intact and must refresh the countdown over the new connection before it
   fires. Nothing else in the suite arms a countdown across a connection death,
   so without this leg a relayer that fired CoD on every transport close would
   still read green — and the recovery the spec prescribes would be advice no
   test had ever taken.

The suite's high-level :class:`ReyaWsExecClient` is used deliberately (the other
ws-exec modules use the raw harness): obligations 1 and 2 are properties of the
CLIENT, so a raw socket would prove nothing about them.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from sdk.async_exec_api.cancel_all_after_response import CancelAllAfterResponse as WsCancelAllAfterResponse
from sdk.open_api.exceptions import ApiException
from sdk.reya_rest_api import ReyaTradingClient
from sdk.reya_ws_exec import (
    WS_CLOSE_MSG_RATE_EXCEEDED,
    ReyaWsExecClient,
    WsExecConnectionClosedError,
    WsExecOperationError,
)
from tests.rate_limits.rl_actions import (
    RlMarket,
    create_resting_order,
    ensure_flat,
    open_order_ids,
    wait_for_open_order_count,
)
from tests.rate_limits.rl_config import RATE_LIMITED_ERROR, RateLimitSuiteConfig, requires_rate_limits
from tests.rate_limits.rl_errors import assert_msg_rate_close_reason, msg_rate_retry_after_s, rest_reject

logger = logging.getLogger("reya.rate_limits")

pytestmark = [pytest.mark.rate_limits, requires_rate_limits]

#: Multiple of the configured burst to fire. Generous: the flood has to out-run
#: a token bucket that is refilling underneath it.
FLOOD_MULTIPLE = 3

#: How long to wait for the close frame to reach the reader thread after the
#: flood stops.
CLOSE_OBSERVE_TIMEOUT_S = 10.0
CLOSE_POLL_S = 0.1

#: The countdown the cancel-on-disconnect leg arms. Long enough that arm →
#: flood → close → reconnect → dwell → refresh fits inside it with margin; the
#: wire contract's ceiling is 60000ms if a slower runner needs the headroom.
COD_ARM_TIMEOUT_MS = 30_000
COD_DISARM_TIMEOUT_MS = 0

#: How long after the reconnect the resting order must still be open. A fired
#: countdown mass-cancels at once, but the REST openOrders view trails the ME:
#: ``tests/engine/test_cod_lifecycle.py`` sizes FIRE_MARGIN_S at 5s for exactly
#: that lag on a loaded WSL2 runner. Dwelling past it is what makes "still
#: resting" evidence of a non-fire rather than of indexer lag.
COD_NON_FIRE_DWELL_S = 8.0

#: Margin the dwell must end ahead of the countdown's own deadline. Inside it a
#: fire and a non-fire stop being distinguishable, so the test refuses to assert
#: rather than report a slow runner as a CoD fire.
COD_BUDGET_MARGIN_S = 5.0

#: Bounded retry for cod-control ops, following the CoD suites' convention: the
#: bucket is flat across tiers (~100/min, burst 10) and an unarmed nonce-bearing
#: disarm is refusable, so a probe-heavy run can reach here with it spent. A
#: rate-limited reject burns no nonce, so the same op is safely re-sent.
COD_RETRY_ATTEMPTS = 6
COD_RETRY_FALLBACK_S = 1.0


async def _await_close(client: ReyaWsExecClient) -> int | None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + CLOSE_OBSERVE_TIMEOUT_S
    while loop.time() < deadline:
        code = client.last_close_code
        if code is not None:
            return code
        await asyncio.sleep(CLOSE_POLL_S)
    return client.last_close_code


def _flood_size(config: RateLimitSuiteConfig) -> int:
    """How many frames one flood fires at the per-connection inbound cap."""
    return max(2, config.ws_inbound_msg_burst * FLOOD_MULTIPLE)


async def _cod_over_ws(
    client: ReyaWsExecClient,
    *,
    timeout_ms: int,
    account_id: int | None,
    label: str,
    slack_s: float,
) -> tuple[float, WsCancelAllAfterResponse]:
    """``cancelAllAfter`` over ws-exec, paced on ``RATE_LIMITED_ERROR`` hints.

    Returns the monotonic instant captured just before the ACCEPTED attempt
    alongside the response, so a caller's countdown budget stays anchored to the
    real send rather than to a first attempt the bucket refused (same anchoring
    as ``_cod_with_retry`` in ``tests/engine/test_cod_ws_exec.py``).
    """
    loop = asyncio.get_running_loop()
    last: WsExecOperationError | None = None
    for _ in range(COD_RETRY_ATTEMPTS):
        sent_at = loop.time()
        try:
            return sent_at, await client.cancel_all_after(timeout_ms=timeout_ms, account_id=account_id)
        except WsExecOperationError as exc:
            if exc.code != RATE_LIMITED_ERROR:
                raise
            last = exc
            hint_s = exc.retry_after_ms / 1000 if exc.retry_after_ms else COD_RETRY_FALLBACK_S
            logger.info("[%s] cod-control rate limited; retrying in %.1fs", label, hint_s + slack_s)
            await asyncio.sleep(hint_s + slack_s)
    raise AssertionError(f"[{label}] cancelAllAfter stayed rate limited across {COD_RETRY_ATTEMPTS} attempts: {last}")


async def _disarm_over_rest(client: ReyaTradingClient, account_id: int | None, slack_s: float) -> None:
    """Best-effort disarm for the failure paths, over the other transport.

    The switch is transport-agnostic, so REST reaches the countdown the ws legs
    armed even when the socket is gone. Left armed, it would mass-cancel the
    NEXT test's book, so this is paced on the hint and only warns once it gives
    up — cleanup must never mask the test's own outcome.
    """
    for _ in range(COD_RETRY_ATTEMPTS):
        try:
            await client.cancel_all_after(timeout_ms=COD_DISARM_TIMEOUT_MS, account_id=account_id)
            return
        except ApiException as exc:
            reject = rest_reject(exc)
            if reject.code != RATE_LIMITED_ERROR:
                logger.warning("safety disarm rejected: %s", reject.describe())
                return
            await asyncio.sleep((reject.retry_after_s or COD_RETRY_FALLBACK_S) + slack_s)
    logger.warning("safety disarm stayed rate limited; a countdown may still be armed")


async def test_ws_exec_msg_rate_flood_closes_4029_and_surfaces_indeterminate(
    rl_client: ReyaTradingClient,
    rl_suite_config: RateLimitSuiteConfig,
    rl_ws_exec_url: str,
) -> None:
    """Flood past the inbound cap → 4029 close, indeterminate in-flight, reconnect."""
    client = ReyaWsExecClient(rest_client=rl_client, ws_url=rl_ws_exec_url)
    await client.connect()

    flood = _flood_size(rl_suite_config)
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

        # The grammar is shared byte-for-byte with the market-data socket's shed
        # (tests/rate_limits/test_md_ws_msg_rate_cap.py) — one helper, so the two
        # surfaces cannot drift into two client branches.
        reason = client.last_close_reason
        assert_msg_rate_close_reason(reason, rl_suite_config.timing.retry_after_max_s, "ws-exec 4029 close")

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


@pytest.mark.cod
async def test_ws_exec_4029_close_does_not_fire_cancel_on_disconnect(
    rl_client: ReyaTradingClient,
    rl_market: RlMarket,
    rl_suite_config: RateLimitSuiteConfig,
    rl_ws_exec_url: str,
) -> None:
    """A 4029 kill leaves the countdown running: book intact, refreshable after reconnect."""
    account_id = rl_client.config.account_id
    slack_s = rl_suite_config.timing.retry_after_slack_s
    budget_s = COD_ARM_TIMEOUT_MS / 1000
    loop = asyncio.get_running_loop()

    await ensure_flat(rl_client, rl_suite_config, rl_market.symbol)
    order_id = await create_resting_order(rl_client, rl_market)
    await wait_for_open_order_count(
        rl_client,
        1,
        timeout_s=rl_suite_config.timing.settle_timeout_s,
        symbol=rl_market.symbol,
    )

    close_reason: str | None = None
    countdown_armed = False
    try:
        victim = ReyaWsExecClient(rest_client=rl_client, ws_url=rl_ws_exec_url)
        await victim.connect()
        try:
            # Armed over the very connection the flood kills. Arming over REST
            # would leave nothing tying the countdown to the dead socket, so a
            # relayer that fired CoD on close could still pass.
            armed_at, armed = await _cod_over_ws(
                victim, timeout_ms=COD_ARM_TIMEOUT_MS, account_id=account_id, label="arm", slack_s=slack_s
            )
            countdown_armed = True
            assert armed.timeout_ms == COD_ARM_TIMEOUT_MS, f"arm must echo timeoutMs={COD_ARM_TIMEOUT_MS}: {armed}"
            armed_trigger_at = armed.trigger_at
            assert armed_trigger_at is not None, f"an armed countdown must echo triggerAt: {armed}"

            flood = _flood_size(rl_suite_config)
            await asyncio.gather(*(victim.ping() for _ in range(flood)), return_exceptions=True)
            close_code = await _await_close(victim)
            close_reason = victim.last_close_reason
            assert close_code == WS_CLOSE_MSG_RATE_EXCEEDED, (
                f"flooding {flood} messages must close {WS_CLOSE_MSG_RATE_EXCEEDED}; observed {close_code} — "
                "raise RL_TEST_WS_INBOUND_MSG_BURST if the deployment's burst is larger"
            )
        finally:
            await victim.close()

        # Reconnecting sooner than the hint is simply closed again, so a prompt
        # reconnect waits exactly that long rather than guessing.
        backoff_s = min(msg_rate_retry_after_s(close_reason) or 0.0, rl_suite_config.timing.retry_after_max_s)
        await asyncio.sleep(backoff_s + slack_s)

        async with ReyaWsExecClient(rest_client=rl_client, ws_url=rl_ws_exec_url) as reconnected:
            dwell_until = loop.time() + COD_NON_FIRE_DWELL_S
            spent_s = dwell_until - armed_at
            if spent_s > budget_s - COD_BUDGET_MARGIN_S:
                pytest.fail(
                    f"arm → close → reconnect → dwell would spend {spent_s:.1f}s of the {budget_s:.0f}s countdown, "
                    f"leaving under {COD_BUDGET_MARGIN_S:.0f}s of margin: a fire and a non-fire are no longer "
                    "distinguishable, so nothing is asserted. Raise COD_ARM_TIMEOUT_MS (the wire contract allows "
                    "up to 60000).",
                    pytrace=False,
                )
            await asyncio.sleep(max(0.0, dwell_until - loop.time()))

            still_open = await open_order_ids(rl_client, rl_market.symbol)
            assert order_id in still_open, (
                f"order {order_id} is gone {COD_NON_FIRE_DWELL_S:.0f}s after reconnecting from the "
                f"{WS_CLOSE_MSG_RATE_EXCEEDED} close, with {budget_s - (loop.time() - armed_at):.0f}s still left on "
                f"the countdown: the transport close fired cancel-on-disconnect, which the contract forbids "
                f"(open now: {still_open})"
            )
            logger.info(
                "cancel-on-disconnect survived the %s close: order %s still resting %.0fs in, %.0fs left on the "
                "countdown",
                WS_CLOSE_MSG_RATE_EXCEEDED,
                order_id,
                loop.time() - armed_at,
                budget_s - (loop.time() - armed_at),
            )

            _, refreshed = await _cod_over_ws(
                reconnected,
                timeout_ms=COD_ARM_TIMEOUT_MS,
                account_id=account_id,
                label="refresh after reconnect",
                slack_s=slack_s,
            )
            assert (
                refreshed.timeout_ms == COD_ARM_TIMEOUT_MS
            ), f"the refresh must echo timeoutMs={COD_ARM_TIMEOUT_MS}: {refreshed}"
            refreshed_trigger_at = refreshed.trigger_at
            assert refreshed_trigger_at is not None, f"a refresh must echo the new triggerAt: {refreshed}"
            assert refreshed_trigger_at > armed_trigger_at, (
                "the refresh must push the deadline out rather than echo the one armed before the close; "
                f"armed triggerAt={armed_trigger_at}, refreshed triggerAt={refreshed_trigger_at}"
            )
            logger.info(
                "countdown refreshed over the new connection: triggerAt %s → %s", armed_trigger_at, refreshed_trigger_at
            )

            _, disarmed = await _cod_over_ws(
                reconnected,
                timeout_ms=COD_DISARM_TIMEOUT_MS,
                account_id=account_id,
                label="disarm",
                slack_s=slack_s,
            )
            countdown_armed = False
            assert (
                disarmed.timeout_ms == COD_DISARM_TIMEOUT_MS
            ), f"disarm must echo timeoutMs={COD_DISARM_TIMEOUT_MS}: {disarmed}"
            assert disarmed.trigger_at is None, f"a disarmed countdown must not echo a triggerAt: {disarmed}"
    finally:
        if countdown_armed:
            await _disarm_over_rest(rl_client, account_id, slack_s)
