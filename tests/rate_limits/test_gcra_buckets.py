"""Rate-Limit v1 §4.1 — the four per-account GCRA buckets.

Every property is asserted structurally rather than arithmetically — the suite
never re-derives GCRA:

* **place** — bursting past the budget produces ``RATE_LIMITED_ERROR`` on HTTP
  429 with a present, plausible ``Retry-After``, and a create succeeds again
  after waiting it out. ``modify`` draws the SAME bucket (design §4.1: "modify
  == create — same weight"), which is proven by draining it with modifies only;
* **cancel** — INDEPENDENT of ``place``: risk-off keeps flowing while an account
  is place-limited. That carve-out is the highest-value assertion in this file —
  it is the difference between "throttled" and "trapped in a position". The
  bucket is nonetheless bounded, which the cancel burst pins;
* **bulk-cancel** — the tight O(M) bucket shared by mass-cancel and the CoD
  fire; explicit mass-cancels are hard-limited even on an empty book;
* **cod-control** — the cheap timer ops, with the asymmetry that gives the
  dead-man's switch its safety property: an unarmed, nonce-bearing disarm is
  refusable, while a REAL disarm (something is armed) is never refused. A
  blocked real disarm would let a false-positive fire flatten a live book.

Bucket probes fire ops the deployment often refuses for an unrelated reason (an
absent order id, an empty book). Those still debit the bucket — admission is
charged where a client message enters the reactor, ahead of handler dispatch —
so ``burst_until_code`` tolerates them and keeps going.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from sdk.open_api.exceptions import ApiException
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api import ReyaTradingClient
from sdk.reya_rest_api.models.orders import ModifyOrderParameters
from tests.rate_limits.rl_actions import (
    RlMarket,
    assert_rate_limited,
    burst_until_code,
    burst_until_rate_limited,
    cancel_all_after_paced,
    create_resting_order,
    ensure_flat,
    flatten_markets,
    open_order_ids,
    wait_for_open_order_count,
    wire,
)
from tests.rate_limits.rl_config import RATE_LIMITED_ERROR, RateLimitSuiteConfig, requires_rate_limits
from tests.rate_limits.rl_errors import assert_retry_after_plausible

logger = logging.getLogger("reya.rate_limits")

pytestmark = [pytest.mark.rate_limits, requires_rate_limits]

#: An order id that cannot exist. A cancel against it still enters the reactor
#: and still debits the cancel bucket, so it probes the bucket without needing
#: a book the place bucket would not let us build fast enough.
ABSENT_ORDER_ID = 1

#: The shortest countdown the wire contract accepts.
COD_ARM_TIMEOUT_MS = 5000
COD_DISARM_TIMEOUT_MS = 0

#: How much of the countdown must remain after the drain finishes. Inside it a
#: fire would land mid-drain, and "the book emptied" would no longer be evidence
#: about a drained bucket.
COD_DRAIN_MARGIN_S = 1.0

#: Extra budget for a fire's book effect to reach the REST openOrders view:
#: the ME scans countdowns on a ~500ms tick and the view trails it.
#: ``tests/engine/test_cod_lifecycle.py`` sizes the same margin at 5s after a
#: loaded WSL2 runner measured the lag at ~7.8s on a 5s countdown.
COD_FIRE_MARGIN_S = 5.0


async def test_place_burst_is_rate_limited_while_cancels_still_flow(
    rl_client: ReyaTradingClient,
    rl_market: RlMarket,
    rl_suite_config: RateLimitSuiteConfig,
) -> None:
    """Burst past ``place``; assert the reject, the risk-off carve-out, recovery."""
    await ensure_flat(rl_client, rl_suite_config, rl_market.symbol)

    result = await burst_until_rate_limited(rl_client, rl_market, rl_suite_config)
    assert_rate_limited(result.reject, "place burst")
    retry_after_s = assert_retry_after_plausible(result.reject, rl_suite_config.timing.retry_after_max_s, "place burst")
    logger.info(
        "place bucket: %d creates accepted, rejected on attempt %d, Retry-After=%ss",
        len(result.placed),
        result.attempts,
        retry_after_s,
    )

    assert result.placed, (
        "the very first create was rate limited, so the risk-off carve-out cannot be exercised; "
        "raise RL_TEST_BUCKET_RECOVERY_S so the place bucket refills between tests"
    )

    # THE carve-out: a cancel must be admitted while creates are still limited.
    # The cancel bucket is separate (~2x place) and is never CAPACITY_LIMITED.
    await rl_client.cancel_order(
        order_id=result.placed[-1],
        symbol=rl_market.symbol,
        account_id=rl_client.config.account_id,
    )
    logger.info("cancel admitted while place-limited (risk-off carve-out holds)")

    await asyncio.sleep(retry_after_s + rl_suite_config.timing.retry_after_slack_s)

    recovered_order_id = await create_resting_order(rl_client, rl_market)
    assert recovered_order_id, "a create must be admitted again after waiting out Retry-After"
    logger.info("create admitted again after Retry-After: orderId=%s", recovered_order_id)


async def test_cancel_bucket_is_independent_of_the_place_bucket(
    rl_client: ReyaTradingClient,
    rl_market: RlMarket,
    rl_suite_config: RateLimitSuiteConfig,
) -> None:
    """Exhaust ``place``, then drain the resting book one cancel at a time.

    Every cancel must be admitted — a ``place`` verdict must never suppress
    cancels (the negative-verdict edge cache short-circuits creates only).
    """
    await ensure_flat(rl_client, rl_suite_config, rl_market.symbol)

    result = await burst_until_rate_limited(rl_client, rl_market, rl_suite_config)
    assert_rate_limited(result.reject, "place burst before cancels")
    assert result.placed, "no resting orders to cancel; raise RL_TEST_BUCKET_RECOVERY_S"

    for index, order_id in enumerate(result.placed, start=1):
        await rl_client.cancel_order(
            order_id=order_id,
            symbol=rl_market.symbol,
            account_id=rl_client.config.account_id,
        )
        logger.info("cancel %d/%d admitted while place-limited", index, len(result.placed))

    cancel_budget = rl_suite_config.limits.cancel_burst
    if len(result.placed) < cancel_budget:
        logger.info(
            "drained all %d resting orders (below the configured cancel budget of %d)",
            len(result.placed),
            cancel_budget,
        )

    remaining = await open_order_ids(rl_client, rl_market.symbol)
    assert not remaining, f"cancels were admitted but orders are still resting: {remaining}"


async def test_modify_draws_the_place_bucket(
    rl_client: ReyaTradingClient,
    rl_market: RlMarket,
    rl_suite_config: RateLimitSuiteConfig,
) -> None:
    """Drain ``place`` with modifies only → ``RATE_LIMITED_ERROR``.

    Proves modify shares the create budget rather than being unlimited: one
    resting order is placed, then every subsequent op is a modify against it.
    """
    await ensure_flat(rl_client, rl_suite_config, rl_market.symbol)
    order_id = await create_resting_order(rl_client, rl_market)

    async def modify(_attempt: int) -> object:
        return await rl_client.modify_order(
            ModifyOrderParameters(
                symbol=rl_market.symbol,
                is_buy=True,
                limit_px=wire(rl_market.resting_buy_price),
                qty=wire(rl_market.min_qty),
                post_only=False,
                expires_after=None,
                time_in_force=TimeInForce.GTC,
                order_id=int(order_id),
            )
        )

    outcome = await burst_until_code(
        modify,
        expected_code=RATE_LIMITED_ERROR,
        attempts=rl_suite_config.burst_attempt_bound(),
        label="modify burst",
    )
    assert_rate_limited(outcome.reject, "modify burst")
    assert_retry_after_plausible(outcome.reject, rl_suite_config.timing.retry_after_max_s, "modify burst")


async def test_cancel_bucket_is_bounded(
    rl_client: ReyaTradingClient,
    rl_market: RlMarket,
    rl_suite_config: RateLimitSuiteConfig,
) -> None:
    """Burst single cancels past ``cancel_burst`` → ``RATE_LIMITED_ERROR``.

    Cancels are carved out of CAPACITY shedding, not out of rate limiting: the
    bucket is ~2x place, which is generous, not absent. Probed against an absent
    order id so the cancel budget — not the place budget spent building a book —
    is what the burst measures.
    """
    await ensure_flat(rl_client, rl_suite_config, rl_market.symbol)

    async def cancel(_attempt: int) -> object:
        return await rl_client.cancel_order(
            order_id=str(ABSENT_ORDER_ID),
            symbol=rl_market.symbol,
            account_id=rl_client.config.account_id,
        )

    outcome = await burst_until_code(
        cancel,
        expected_code=RATE_LIMITED_ERROR,
        attempts=rl_suite_config.bucket_attempt_bound(
            rl_suite_config.limits.cancel_per_min, rl_suite_config.limits.cancel_burst
        ),
        label="cancel burst",
    )
    assert_rate_limited(outcome.reject, "cancel burst")
    assert_retry_after_plausible(outcome.reject, rl_suite_config.timing.retry_after_max_s, "cancel burst")


async def test_bulk_cancel_bucket_is_bounded(
    rl_client: ReyaTradingClient,
    rl_market: RlMarket,
    rl_suite_config: RateLimitSuiteConfig,
) -> None:
    """Burst mass-cancels past ``bulk-cancel`` → ``RATE_LIMITED_ERROR``.

    The tightest Standard bucket, and the one an explicit mass-cancel shares
    with the CoD fire. An empty book is deliberately fine: an explicit
    mass-cancel is hard-limited whether or not it cancels anything, which is
    what keeps the O(M) shortcut from being a free loop.
    """
    await ensure_flat(rl_client, rl_suite_config, rl_market.symbol)

    async def mass_cancel(_attempt: int) -> object:
        return await rl_client.mass_cancel(symbol=rl_market.symbol, account_id=rl_client.config.account_id)

    outcome = await burst_until_code(
        mass_cancel,
        expected_code=RATE_LIMITED_ERROR,
        attempts=rl_suite_config.bucket_attempt_bound(
            rl_suite_config.limits.bulk_cancel_per_min, rl_suite_config.limits.bulk_cancel_burst
        ),
        label="bulk-cancel burst",
    )
    assert_rate_limited(outcome.reject, "bulk-cancel burst")
    assert_retry_after_plausible(outcome.reject, rl_suite_config.timing.retry_after_max_s, "bulk-cancel burst")


@pytest.mark.cod
async def test_a_drained_bulk_cancel_bucket_never_blocks_the_cod_fire(
    rl_client: ReyaTradingClient,
    rl_market: RlMarket,
    rl_second_market: RlMarket | None,
    rl_suite_config: RateLimitSuiteConfig,
) -> None:
    """Drain bulk-cancel, then let an armed countdown fire: the book still empties.

    A CoD fire is the same O(M) mass-cancel an explicit ``cancelAll`` is, and
    the two share the tightest Standard bucket — but only one of them is a
    client op. The fire is the system de-risking an account that has stopped
    talking to it, so it must never be refused: a rate-limited fire is a
    dead-man's switch that silently did not pull, which is worse than having no
    switch at all, because the client believed it had one.

    The drain runs on the SECOND market on purpose. Draining on the suite's own
    market would mass-cancel the very order the fire is supposed to take, and
    draining BEFORE the order was placed would leave the countdown running while
    the bucket refilled — at 10/min a token is back within seconds, so the fire
    could then be admitted on budget rather than on exemption. A mass-cancel of
    an empty foreign book debits the same per-ACCOUNT bucket while touching
    nothing.
    """
    if rl_second_market is None:
        pytest.skip(
            "the CoD-fire leg drains the bulk-cancel bucket on a market whose book it may empty; "
            "set RL_TEST_SECOND_SYMBOL to another spot market"
        )

    # Rebound with a non-optional type so the drain closure below stays typed.
    second_market: RlMarket = rl_second_market
    account_id = rl_client.config.account_id
    markets = [rl_market, second_market]
    await flatten_markets(rl_client, rl_suite_config, markets)

    order_id = await create_resting_order(rl_client, rl_market)
    await wait_for_open_order_count(
        rl_client,
        1,
        timeout_s=rl_suite_config.timing.settle_timeout_s,
        symbol=rl_market.symbol,
    )

    countdown_armed = False
    try:
        armed = await cancel_all_after_paced(
            rl_client,
            rl_suite_config,
            timeout_ms=COD_ARM_TIMEOUT_MS,
            account_id=account_id,
            label="cod fire arm",
        )
        countdown_armed = True
        armed_at = asyncio.get_running_loop().time()
        assert armed.trigger_at is not None, f"an armed countdown must echo triggerAt: {armed}"

        async def mass_cancel(_attempt: int) -> object:
            return await rl_client.mass_cancel(symbol=second_market.symbol, account_id=account_id)

        outcome = await burst_until_code(
            mass_cancel,
            expected_code=RATE_LIMITED_ERROR,
            attempts=rl_suite_config.bucket_attempt_bound(
                rl_suite_config.limits.bulk_cancel_per_min, rl_suite_config.limits.bulk_cancel_burst
            ),
            label="bulk-cancel drain before the fire",
        )
        assert_rate_limited(outcome.reject, "bulk-cancel drain before the fire")

        # The drain has to finish inside the countdown, or the fire lands
        # mid-drain and "the book emptied" says nothing about a drained bucket.
        # Refuse to assert rather than report the overrun as a CoD failure.
        drain_s = asyncio.get_running_loop().time() - armed_at
        if drain_s > COD_ARM_TIMEOUT_MS / 1000 - COD_DRAIN_MARGIN_S:
            pytest.fail(
                f"draining the bulk-cancel bucket took {drain_s:.1f}s of the {COD_ARM_TIMEOUT_MS / 1000:.0f}s "
                f"countdown ({outcome.accepted} mass-cancels accepted), leaving under {COD_DRAIN_MARGIN_S:.0f}s of "
                "margin: the fire would land mid-drain. Lower the deployment's bulk-cancel budget "
                "(RL_TEST_STANDARD_BULK_CANCEL_PER_MIN / _BURST describe it) — the countdown is already at the "
                "5000ms wire minimum, and a longer one would only let the bucket refill",
                pytrace=False,
            )

        surviving = await open_order_ids(rl_client, rl_market.symbol)
        assert order_id in surviving, (
            f"the drain must leave the sacrificial book alone — it mass-cancels {second_market.symbol} only — "
            f"otherwise an empty book proves nothing about the fire (open now: {surviving})"
        )

        try:
            await wait_for_open_order_count(
                rl_client,
                0,
                timeout_s=COD_ARM_TIMEOUT_MS / 1000 + COD_FIRE_MARGIN_S,
                symbol=rl_market.symbol,
            )
        except AssertionError as exc:
            pytest.fail(
                f"the countdown did not empty the book with the bulk-cancel bucket drained "
                f"({outcome.reject.describe()}): the fire is being rate limited like a client mass-cancel, "
                f"which turns the dead-man's switch off exactly when it is needed — {exc}",
                pytrace=False,
            )
        countdown_armed = False
        logger.info("cod fire emptied the book on a drained bulk-cancel bucket (order %s gone)", order_id)
    finally:
        if countdown_armed:
            # Best effort: by now the 5s countdown has almost certainly expired
            # on its own, which makes this a NO-OP disarm — and an unarmed
            # disarm is deliberately refusable (see the cod-control test below),
            # so a hard failure here would mask whatever actually went wrong.
            try:
                await cancel_all_after_paced(
                    rl_client,
                    rl_suite_config,
                    timeout_ms=COD_DISARM_TIMEOUT_MS,
                    account_id=account_id,
                    label="cod fire cleanup disarm",
                )
            except (AssertionError, ApiException, OSError, RuntimeError) as exc:
                logger.warning("cleanup disarm did not land (%s: %s)", type(exc).__name__, exc)
        await flatten_markets(rl_client, rl_suite_config, markets)


async def test_cod_control_bucket_bounds_no_op_disarms_but_never_a_real_one(
    rl_client: ReyaTradingClient,
    rl_market: RlMarket,
    rl_suite_config: RateLimitSuiteConfig,
) -> None:
    """The cod-control asymmetry, in one flow.

    1. An UNARMED, nonce-bearing disarm is a no-op write and IS refusable —
       otherwise the one op admission may never block would be a free write path
       into the WAL and the fatal-on-full reactor channel;
    2. a REAL disarm (something is armed) is never refused, even with the bucket
       drained. A blocked real disarm would let a false-positive fire cancel a
       live book, which is the outcome the dead-man's switch exists to avoid.

    "No-op" means *not currently armed* — never "an identical ``timeoutMs``": a
    refresh almost always repeats its ``timeoutMs`` with a fresh deadline, so
    coalescing on that would silently break the switch.
    """
    await ensure_flat(rl_client, rl_suite_config, rl_market.symbol)
    account_id = rl_client.config.account_id

    async def disarm(_attempt: int) -> object:
        return await rl_client.cancel_all_after(timeout_ms=COD_DISARM_TIMEOUT_MS, account_id=account_id)

    async def refresh(_attempt: int) -> object:
        return await rl_client.cancel_all_after(timeout_ms=COD_ARM_TIMEOUT_MS, account_id=account_id)

    cod_bound = rl_suite_config.bucket_attempt_bound(
        rl_suite_config.limits.cod_control_per_min, rl_suite_config.limits.cod_control_burst
    )

    unarmed = await burst_until_code(
        disarm,
        expected_code=RATE_LIMITED_ERROR,
        attempts=cod_bound,
        label="cod-control unarmed disarm burst",
    )
    assert_rate_limited(unarmed.reject, "cod-control unarmed disarm burst")
    backoff = assert_retry_after_plausible(
        unarmed.reject, rl_suite_config.timing.retry_after_max_s, "cod-control unarmed disarm burst"
    )

    await asyncio.sleep(backoff + rl_suite_config.timing.retry_after_slack_s)

    await rl_client.cancel_all_after(timeout_ms=COD_ARM_TIMEOUT_MS, account_id=account_id)
    logger.info("cod-control: countdown armed at %dms", COD_ARM_TIMEOUT_MS)

    # Re-drain the bucket while ARMED, so the disarm below is issued against an
    # exhausted budget — exactly the state the never-refused guarantee must hold
    # in. Any failure here is deferred: the countdown is live, so the disarm has
    # to be issued either way.
    drain_error: AssertionError | None = None
    try:
        drained = await burst_until_code(
            refresh,
            expected_code=RATE_LIMITED_ERROR,
            attempts=cod_bound,
            label="cod-control armed refresh burst",
        )
        assert_rate_limited(drained.reject, "cod-control armed refresh burst")
    except AssertionError as exc:
        drain_error = exc

    # THE guarantee: a real disarm, on a drained bucket, must be admitted.
    await rl_client.cancel_all_after(timeout_ms=COD_DISARM_TIMEOUT_MS, account_id=account_id)
    logger.info("cod-control: real disarm admitted on a drained bucket")
    if drain_error is not None:
        raise drain_error
