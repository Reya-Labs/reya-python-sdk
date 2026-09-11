"""Live publisher pressure, operator commands and independently configured tiers."""

from __future__ import annotations

import asyncio
import logging

import pytest

from sdk.open_api.exceptions import ApiException
from tests.rate_limits import rl_config
from tests.rate_limits.rl_actions import (
    assert_rate_limited,
    burst_until_rate_limited,
    create_resting_order,
    ensure_flat,
    resolve_market,
    wait_for_open_order_count,
)
from tests.rate_limits.rl_errors import assert_no_retry_hint, assert_venue_verdict, capture_rest_reject, rest_reject
from tests.rate_limits.rl_hooks import require_hook, run_hook

pytestmark = [pytest.mark.rate_limits, rl_config.requires_rate_limits]
logger = logging.getLogger("reya.rate_limits")


async def test_publisher_overload_sheds_creates_preserves_cancel_and_reopens(rl_client, rl_market, rl_suite_config):
    require_hook("overload")
    require_hook("unpause")
    wallet, account = rl_client.config.owner_wallet_address, rl_client.config.account_id
    await ensure_flat(rl_client, rl_suite_config, rl_market.symbol)
    resting = await create_resting_order(rl_client, rl_market)
    try:
        await run_hook("overload", wallet, account)
        # The publisher stops counting the batch once it is in flight. The first
        # create blocks that batch on Redis; the second leaves a queued head
        # behind it for the age watermark to observe.
        await create_resting_order(rl_client, rl_market)
        await create_resting_order(rl_client, rl_market)
        await asyncio.sleep(4)
        reject = await capture_rest_reject(create_resting_order(rl_client, rl_market), "publisher pressure")
        assert reject.code == rl_config.CAPACITY_LIMITED_ERROR, reject.describe()
        assert_venue_verdict(reject, "publisher pressure")
        assert_no_retry_hint(reject, "publisher pressure")
        await rl_client.cancel_order(order_id=resting, symbol=rl_market.symbol, account_id=account)
    finally:
        await run_hook("unpause", wallet, account)
    deadline = asyncio.get_running_loop().time() + 15
    while True:
        try:
            assert await create_resting_order(rl_client, rl_market)
            break
        except ApiException as exc:
            reject = rest_reject(exc)
            assert reject.code == rl_config.CAPACITY_LIMITED_ERROR, reject.describe()
            assert asyncio.get_running_loop().time() < deadline, "publisher never reopened below its low watermark"
            await asyncio.sleep(0.5)


async def test_operator_scripts_and_enumeration_gap(rl_client, rl_market, rl_suite_config):
    for action in ("eject", "uneject", "verify", "reconcile", "status_only"):
        require_hook(action)
    wallet, account = rl_client.config.owner_wallet_address, rl_client.config.account_id
    await ensure_flat(rl_client, rl_suite_config, rl_market.symbol)
    await create_resting_order(rl_client, rl_market)
    ejected = False
    try:
        await run_hook("eject", wallet, account)
        ejected = True
        await wait_for_open_order_count(rl_client, 0, timeout_s=60, symbol=rl_market.symbol)
        await run_hook("verify", wallet, account)
        await run_hook("uneject", wallet, account)
        ejected = False
        await run_hook("verify", wallet, account, expected=3)
        # Refusal to onboard or re-whitelist an already listed wallet is part of the CLI contract.
        await run_hook("uneject", wallet, account, expected=2)
        await asyncio.sleep(rl_suite_config.timing.poll_interval_s * 2)
        await create_resting_order(rl_client, rl_market)
        await run_hook("status_only", wallet, account)
        ejected = True
        output = await run_hook("reconcile", wallet, account, expected=3)
        assert "enumeration_gap" in output, output
    finally:
        if ejected:
            await run_hook("uneject", wallet, account)
        await asyncio.sleep(rl_suite_config.timing.poll_interval_s * 2)
        await ensure_flat(rl_client, rl_suite_config, rl_market.symbol)
    await run_hook("reconcile", wallet, account)


async def test_tiers_have_independent_budgets_and_promotion_is_polled(
    rl_client, rl_market, rl_suite_config, rl_standard_client_provider
):
    require_hook("promote")
    require_hook("demote")
    mm = await rl_standard_client_provider("mm")
    if mm is None:
        pytest.skip("tier comparison needs the explicit RL_TEST_MM identity")
    mm_config = rl_config.load_suite_config("mm")
    mm_market = await resolve_market(mm, mm_config.symbol)
    wallet, account = rl_client.config.owner_wallet_address, rl_client.config.account_id
    await ensure_flat(rl_client, rl_suite_config, rl_market.symbol)
    await ensure_flat(mm, mm_config, mm_market.symbol)
    await asyncio.sleep(max(rl_suite_config.timing.bucket_recovery_s, mm_config.timing.bucket_recovery_s))
    try:
        drained = await burst_until_rate_limited(rl_client, rl_market, rl_suite_config)
        assert_rate_limited(drained.reject, "Standard drain")
        assert await create_resting_order(mm, mm_market), "Standard exhaustion must not debit the MM account"
        await ensure_flat(rl_client, rl_suite_config, rl_market.symbol)
        await run_hook("promote", wallet, account)
        await asyncio.sleep(rl_suite_config.timing.poll_interval_s * 2)
        # Refill the promoted account, then measure enough accepted requests to
        # distinguish the configured MM burst from the Standard burst.
        await asyncio.sleep(mm_config.timing.bucket_recovery_s)
        promoted = await burst_until_rate_limited(rl_client, rl_market, mm_config)
        assert_rate_limited(promoted.reject, "promoted MM drain")
        assert len(promoted.placed) > rl_suite_config.limits.place_burst, "promotion did not apply MM limits"
        logger.info("polled tier promotion: %d creates before the MM verdict", len(promoted.placed))
    finally:
        await ensure_flat(rl_client, mm_config, rl_market.symbol)
        await run_hook("demote", wallet, account)
        await ensure_flat(mm, mm_config, mm_market.symbol)
        await asyncio.sleep(rl_suite_config.timing.poll_interval_s * 2)
