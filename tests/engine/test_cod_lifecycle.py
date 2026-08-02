"""
Cancel-on-disconnect (cancelAllAfter) lifecycle tests — live e2e.

Exercises the account-wide dead-man's-switch end to end:
- arm echoes the countdown deadline (`triggerAt` ≈ now + timeoutMs on the
  MATCHING-ENGINE clock — assertions use a ±2s window to absorb client↔ME
  clock skew and request latency),
- expiry mass-cancels ALL of the account's resting orders (same scope as
  an account-wide cancelAll),
- refresh replaces the countdown, disarm (timeoutMs=0) stops it,
- the switch is strictly account-scoped, and state fully resets after a fire.

The account-wide fire (`test_cod_fires_cancels_all_orders`) is parametrized
over [spot, perp]: it rests orders on whichever market the parametrization
picked and asserts the fire empties the account. The countdown STATE-MACHINE
tests (arm-echo, refresh, disarm, account-isolation, rearm) stay single-market
— COD is an account-level dead-man's-switch with no market dimension in its
request or countdown; the market only enters at fire SCOPE, which the
parametrized fire test covers.

RECORDED COVERAGE GAP: the devnet fixtures keep spot and perp credentials on
DISJOINT accounts (SPOT_ACCOUNT_ID_* cannot trade perps and the
PERP_ACCOUNT_ID_* margin accounts carry no spot balances), and devnet1 lists
exactly ONE spot and ONE perp market — so no configured account can rest
orders on two markets at once. The multi-MARKET scope of a single fire (one
expiry emptying an account's orders resting on >=2 markets) is therefore NOT
exercisable e2e — a reactor bug that scoped the fire's cancel to a single
market would pass this suite. That dimension is pinned at the matching-engine
level instead: reya-chain `crates/matching-engine-server/tests/
cancel_all_after_test.rs::cod_fire_cancels_only_target_account_across_markets`
rests target-account orders in two markets (plus a control account's order),
fires once, and asserts one MassCancel per affected market with both of the
target's books emptied and the control account untouched. Revisit if devnet1
ever lists a second market reachable from a single configured account.

The function-scoped tester fixtures disarm COD and close all orders in
teardown, so an assertion failure mid-test never leaks an armed countdown
or a resting order onto the shared devnet accounts.
"""

import asyncio
import time

import pytest

from sdk.async_api.cancel_reason import CancelReason as WsCancelReason
from sdk.open_api.models.cancel_all_after_response import CancelAllAfterResponse
from sdk.open_api.models.order_status import OrderStatus
from tests.helpers import ReyaTester
from tests.helpers.builders import OrderBuilder
from tests.helpers.market_config import PerpTestConfig, SpotTestConfig
from tests.helpers.reya_tester import logger

pytestmark = [pytest.mark.e2e, pytest.mark.cod]

# The ME scans armed countdowns on a ~500ms tick; allow that plus clock skew
# and request latency before declaring a fire missed (or a non-fire proven).
FIRE_MARGIN_S = 3.0
# triggerAt is stamped on the ME clock; this window absorbs client↔ME clock
# skew + round-trip latency. Dev runners (esp. WSL2) drift several seconds from
# the NTP-synced cluster — measured up to ~2.7s here — so 2s was too tight and
# flaked intermittently. 5s covers realistic dev skew while still catching any
# gross ME bug (wrong unit / missing timeout are off by ≥30,000ms).
TRIGGER_AT_WINDOW_MS = 5_000


def _assert_trigger_at_echo(response: CancelAllAfterResponse, timeout_ms: int, sent_at_ms: float) -> None:
    """Assert the arm response echoes timeoutMs and a triggerAt ≈ sent_at + timeoutMs.

    `triggerAt` is stamped by the MATCHING-ENGINE clock; ±TRIGGER_AT_WINDOW_MS
    absorbs client↔ME skew + round-trip latency.
    """
    assert response.timeout_ms == timeout_ms, f"Expected timeoutMs echo {timeout_ms}, got {response.timeout_ms}"
    assert response.trigger_at is not None, f"Armed countdown must echo triggerAt: {response}"
    expected = sent_at_ms + timeout_ms
    drift = response.trigger_at - expected
    assert abs(drift) <= TRIGGER_AT_WINDOW_MS, (
        f"triggerAt {response.trigger_at} drifts {drift:+.0f}ms from now+timeoutMs {expected:.0f} "
        f"(window ±{TRIGGER_AT_WINDOW_MS}ms; ME clock vs client clock)"
    )


async def _wait_until_no_open_orders(tester: ReyaTester, timeout_s: float) -> None:
    """Poll REST open orders until the account has none (bounded)."""
    deadline = time.time() + timeout_s
    remaining: list = []
    while time.time() < deadline:
        remaining = await tester.client.get_open_orders()
        if not remaining:
            return
        await asyncio.sleep(0.5)
    raise AssertionError(
        f"Account {tester.account_id} still has {len(remaining)} open order(s) after {timeout_s}s: "
        f"{[o.order_id for o in remaining]}"
    )


async def _order_is_open(tester: ReyaTester, order_id: str) -> bool:
    return await tester.data.open_order(order_id) is not None


@pytest.mark.spot
@pytest.mark.asyncio
async def test_arm_echoes_trigger_at(spot_tester: ReyaTester):
    """arm(30000) echoes timeoutMs and triggerAt ≈ now+30s (ME clock, ±2s)."""
    sent_at_ms = time.time() * 1000
    response = await spot_tester.arm_cod(timeout_ms=30_000)
    logger.info(f"armed: {response}")

    try:
        _assert_trigger_at_echo(response, timeout_ms=30_000, sent_at_ms=sent_at_ms)
        assert response.account_id == spot_tester.account_id
    finally:
        disarm = await spot_tester.disarm_cod()

    assert disarm.timeout_ms == 0
    assert disarm.trigger_at is None, f"Disarm must not echo a triggerAt: {disarm}"


@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_cod_fires_cancels_all_orders(
    market_config: SpotTestConfig | PerpTestConfig, market_type: str, maker: ReyaTester
) -> None:
    """COD expiry cancels EVERY resting order on the account, across BOTH book
    sides.

    Account-wide scope is exercised on whichever market the parametrization
    picked, with multiple resting GTC orders on both sides of the book (no
    fills — the orders rest far from oracle and are cancelled by the fire).
    The cross-MARKET scope of a single fire (one expiry emptying an account's
    orders resting on >=2 markets at once) is NOT reachable e2e because no
    configured devnet account spans both market types — see the module
    docstring; that dimension is pinned at the matching-engine level instead.
    """
    far_buy = str(market_config.price(0.50))
    far_sell = str(market_config.price(1.50))

    order_ids = []
    for params in (
        OrderBuilder().symbol(market_config.symbol).buy().price(far_buy).qty(market_config.min_qty).gtc().build(),
        OrderBuilder().symbol(market_config.symbol).buy().price(far_buy).qty(market_config.min_qty).gtc().build(),
        OrderBuilder().symbol(market_config.symbol).sell().price(far_sell).qty(market_config.min_qty).gtc().build(),
    ):
        order_id = await maker.orders.create_limit(params)
        assert order_id is not None
        order_ids.append(order_id)
    for order_id in order_ids:
        await maker.wait.for_order_creation(order_id)
    logger.info(f"[{market_type}] resting orders before arm: {order_ids}")

    await maker.arm_cod(timeout_ms=5_000)

    # timeout + scan granularity + clock-skew margin.
    await _wait_until_no_open_orders(maker, timeout_s=5.0 + FIRE_MARGIN_S)
    logger.info(f"[{market_type}] ✅ COD fired: all orders cancelled")

    # The fire must also notify over WS: an orderChange with status CANCELLED
    # per order (WS is the authoritative status source in for_order_state).
    for order_id in order_ids:
        await maker.wait.for_order_state(order_id, OrderStatus.CANCELLED)
        ws_order = maker.ws.orders.get(str(order_id))
        assert ws_order is not None, f"[{market_type}] missing WS cancelled order {order_id}"
        assert (
            ws_order.cancel_reason == WsCancelReason.CANCEL_ALL_AFTER
        ), f"[{market_type}] expected CANCEL_ALL_AFTER cancelReason for {order_id}, got {ws_order.cancel_reason}"
        assert ws_order.cancel_reason_message
    logger.info(f"[{market_type}] ✅ WS delivered CANCELLED orderChanges for all {len(order_ids)} orders")


@pytest.mark.spot
@pytest.mark.asyncio
async def test_refresh_extends_deadline(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """Re-arming replaces the countdown: a refresh before expiry pushes the
    deadline out, so the order survives past the ORIGINAL deadline."""
    params = OrderBuilder.from_config(spot_config).buy().price(str(spot_config.get_safe_no_match_buy_price())).build()
    order_id = await spot_tester.orders.create_limit(params)
    assert order_id is not None
    await spot_tester.wait.for_order_creation(order_id)

    try:
        armed = await spot_tester.arm_cod(timeout_ms=5_000)
        assert armed.trigger_at is not None
        # Sleep meaningfully into the original countdown, but leave ~3s of
        # budget (the ME-side deadline started BEFORE the arm response came
        # back) so a slow devnet/WAF round trip can't let the original fire.
        await asyncio.sleep(2.0)

        sent_at_ms = time.time() * 1000
        refresh = await spot_tester.arm_cod(timeout_ms=60_000)
        _assert_trigger_at_echo(refresh, timeout_ms=60_000, sent_at_ms=sent_at_ms)
        assert refresh.trigger_at is not None
        # Both triggerAts are on the SAME (ME) clock: triggerAt - timeoutMs is
        # when the ME processed each arm. If the refresh provably landed after
        # the original deadline, the fire was legitimate — skip, don't flake.
        if refresh.trigger_at - 60_000 >= armed.trigger_at:
            pytest.skip(
                f"refresh round trip too slow: ME processed it at {refresh.trigger_at - 60_000} "
                f"≥ original deadline {armed.trigger_at} (ME clock) — cannot prove refresh semantics"
            )

        # t≈7s > original 5s deadline; only the refreshed 60s countdown runs.
        await asyncio.sleep(5.0)
        assert await _order_is_open(spot_tester, order_id), (
            f"Order {order_id} was cancelled past the ORIGINAL deadline — " "the refresh did not replace the countdown"
        )
        logger.info("✅ Refresh extended the deadline; order survived the original expiry")
    finally:
        await spot_tester.disarm_cod()
        await spot_tester.orders.close_all(fail_if_none=False)


@pytest.mark.spot
@pytest.mark.asyncio
async def test_disarm_prevents_fire(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """Disarming (timeoutMs=0) stops the countdown: nothing fires after the
    would-have-been deadline."""
    params = OrderBuilder.from_config(spot_config).buy().price(str(spot_config.get_safe_no_match_buy_price())).build()
    order_id = await spot_tester.orders.create_limit(params)
    assert order_id is not None
    await spot_tester.wait.for_order_creation(order_id)

    try:
        await spot_tester.arm_cod(timeout_ms=5_000)
        disarm = await spot_tester.disarm_cod()
        assert disarm.timeout_ms == 0
        assert disarm.trigger_at is None, f"Disarm must not echo a triggerAt: {disarm}"

        # Past the original 5s deadline + scan/clock margin: nothing may fire.
        await asyncio.sleep(7.0)
        assert await _order_is_open(spot_tester, order_id), f"Order {order_id} was cancelled after a disarm"
        logger.info("✅ Disarm prevented the fire; order still resting")
    finally:
        await spot_tester.orders.close_all(fail_if_none=False)


@pytest.mark.spot
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_account_isolation(spot_config: SpotTestConfig, maker_tester: ReyaTester, taker_tester: ReyaTester):
    """Account A arming + firing must not touch account B's resting orders."""
    buy_px = str(spot_config.get_safe_no_match_buy_price())

    maker_order_id = await maker_tester.orders.create_limit(
        OrderBuilder.from_config(spot_config).buy().price(buy_px).gtc().build()
    )
    assert maker_order_id is not None
    await maker_tester.wait.for_order_creation(maker_order_id)

    taker_order_id = await taker_tester.orders.create_limit(
        OrderBuilder.from_config(spot_config).buy().price(buy_px).gtc().build()
    )
    assert taker_order_id is not None
    await taker_tester.wait.for_order_creation(taker_order_id)

    try:
        await maker_tester.arm_cod(timeout_ms=5_000)
        await _wait_until_no_open_orders(maker_tester, timeout_s=5.0 + FIRE_MARGIN_S)
        logger.info("✅ Account A's COD fired")

        assert await _order_is_open(taker_tester, taker_order_id), (
            f"Account B's order {taker_order_id} was cancelled by account A's COD fire — " "the switch leaked scope"
        )
        logger.info("✅ Account B's resting order untouched")
    finally:
        await taker_tester.orders.close_all(fail_if_none=False)


@pytest.mark.spot
@pytest.mark.asyncio
async def test_rearm_after_fire(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """After a fire completes, the account can arm again and disarm cleanly —
    the fired countdown leaves no stuck server-side state."""
    params = OrderBuilder.from_config(spot_config).buy().price(str(spot_config.get_safe_no_match_buy_price())).build()
    order_id = await spot_tester.orders.create_limit(params)
    assert order_id is not None
    await spot_tester.wait.for_order_creation(order_id)

    await spot_tester.arm_cod(timeout_ms=5_000)
    await _wait_until_no_open_orders(spot_tester, timeout_s=5.0 + FIRE_MARGIN_S)
    logger.info("✅ First COD fire completed")

    sent_at_ms = time.time() * 1000
    rearm = await spot_tester.arm_cod(timeout_ms=30_000)
    _assert_trigger_at_echo(rearm, timeout_ms=30_000, sent_at_ms=sent_at_ms)
    logger.info("✅ Re-arm accepted after the fire")

    disarm = await spot_tester.disarm_cod()
    assert disarm.timeout_ms == 0
    assert disarm.trigger_at is None
    logger.info("✅ Disarm after re-arm: COD state fully reset")
