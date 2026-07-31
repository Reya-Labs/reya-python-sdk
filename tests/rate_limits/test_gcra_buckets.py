"""Rate-Limit v1 §4.1 — the per-account GCRA buckets.

Two properties, both asserted structurally rather than arithmetically:

* bursting past the ``place`` budget produces ``RATE_LIMITED_ERROR`` on HTTP
  429 with a present, plausible ``Retry-After``, and a create succeeds again
  after waiting it out;
* the ``cancel`` bucket is INDEPENDENT of ``place`` — risk-off keeps flowing
  while an account is place-limited. That carve-out is the highest-value
  assertion in this file: it is the difference between "throttled" and
  "trapped in a position".
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from sdk.reya_rest_api import ReyaTradingClient
from tests.rate_limits.rl_actions import (
    RlMarket,
    assert_rate_limited,
    burst_until_rate_limited,
    create_resting_order,
    ensure_flat,
    open_order_ids,
)
from tests.rate_limits.rl_config import RateLimitSuiteConfig, requires_rate_limits
from tests.rate_limits.rl_errors import assert_retry_after_plausible

logger = logging.getLogger("reya.rate_limits")

pytestmark = [pytest.mark.rate_limits, requires_rate_limits]


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
