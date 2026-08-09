"""Rate-Limit v1 §3 — the reactive eject.

One DB transaction removes the wallet from ``rl_whitelist`` and inserts its
accounts into ``rl_ejected_accounts``; from there the system converges with
nothing sent by anyone. The four observable consequences:

1. new **creates and modifies** are rejected 403-class — ``NOT_WHITELISTED_ERROR``
   from the edge or ``ACCOUNT_SUSPENDED_ERROR`` from the ME admission check,
   depending on which poll tick lands first. Either is correct; the test
   records which one the deployment actually produced. Modify is refused
   alongside create because both are risk-INCREASING: a qty-up or a reprice
   grows exposure just as a new order does;
2. **cancels still flow.** The whitelist gate covers create + modify only, and
   the ME's ejected-account check likewise refuses those two, not cancels — so
   an ejected owner can still unwind while the sweep runs. This is asserted
   explicitly: a cancel issued immediately after the eject must either succeed
   or lose the race to the sweep (an order-not-found-class error). A gate
   verdict fails;
3. **resting** orders are swept by the ME within one poll interval, while
   **armed SL/TP triggers are RETAINED** (design ruling, 2026-08). Ejecting an
   account does not close its positions, so cancelling its protective stops
   would leave it unprotected in exactly the situation the eject was meant to
   de-risk. The sweep is therefore "no resting liquidity", not "empty book";
4. un-ejecting restores trading.

``rl_ejected_accounts`` lives in the off-chain Postgres, which this repo cannot
reach, so the eject step is delegated to the operator-supplied
``RL_TEST_EJECT_CMD`` / ``RL_TEST_UNEJECT_CMD`` templates (see the suite
README). The test skips cleanly when they are unset.

Convergence is observed by polling ``openOrders``; the equivalent signal on the
``walletOrderChanges`` WebSocket stream is a stream of cancels for the same
orders. Polling is used here because it needs no subscription lifecycle and
tolerates the ME's poll cadence without racing it.
"""

from __future__ import annotations

import asyncio
import logging
from decimal import Decimal

import pytest

from sdk.open_api.exceptions import ApiException
from sdk.open_api.models.order_type import OrderType
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api import ReyaTradingClient
from sdk.reya_rest_api.models.orders import ModifyOrderParameters, TriggerOrderParameters
from tests.rate_limits.rl_actions import (
    RlMarket,
    armed_trigger_ids,
    assert_not_whitelist_gated,
    create_resting_order,
    ensure_flat,
    open_order_ids,
    quantize_down,
    resting_order,
    resting_order_ids,
    wire,
)
from tests.rate_limits.rl_config import (
    EJECT_REJECT_CODES,
    HTTP_FORBIDDEN,
    RateLimitSuiteConfig,
    requires_rate_limits,
)
from tests.rate_limits.rl_errors import RestReject, capture_rest_reject, rest_reject

logger = logging.getLogger("reya.rate_limits")

pytestmark = [pytest.mark.rate_limits, requires_rate_limits]

RESTING_ORDERS = 2

#: Order id for the modify probe. Both the edge gate and the ME's ejected-set
#: check run before the order lookup, so an absent id still yields the eject
#: verdict — and cannot race the sweep the way a real order id would.
ABSENT_ORDER_ID = 1

#: A stop this far below the mark rests armed for the whole test instead of
#: firing — the same "far out of the money" trick the spot resting order uses.
TRIGGER_PRICE_FACTOR = Decimal("0.5")


def _assert_eject_reject(reject: RestReject, label: str) -> None:
    """Assert a risk-increasing op was refused because the account is ejected.

    Either code is correct and which one appears depends on which poll tick
    landed first — the edge's whitelist poll (the eject transaction also
    removes the ``rl_whitelist`` row) or the ME's ejected-set poll. The
    observed code is logged by the caller so a run records which path fired.
    """
    assert (
        reject.code in EJECT_REJECT_CODES
    ), f"[{label}] must be rejected with one of {EJECT_REJECT_CODES}; got {reject.describe()}"
    assert reject.status == HTTP_FORBIDDEN, f"[{label}] eject rejects are 403-class; got {reject.describe()}"


def _poll_interval(config: RateLimitSuiteConfig) -> float:
    """Never probe faster than the sustained place rate: each probe is a create
    and would otherwise drain the GCRA bucket instead of testing the eject."""
    return max(config.timing.poll_interval_s, config.timing.place_pace_s)


async def _poll_until_create_rejected(
    client: ReyaTradingClient,
    market: RlMarket,
    config: RateLimitSuiteConfig,
) -> RestReject:
    """Probe with creates until one is rejected, or the eject deadline passes."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + config.timing.eject_timeout_s
    while loop.time() < deadline:
        try:
            response = await client.create_limit_order(resting_order(market))
            logger.info("eject not yet visible; create still accepted (orderId=%s)", response.order_id)
        except ApiException as exc:
            return rest_reject(exc)
        await asyncio.sleep(_poll_interval(config))
    raise AssertionError(
        f"creates were still accepted {config.timing.eject_timeout_s}s after eject; "
        "raise RL_TEST_EJECT_TIMEOUT_S if the deployment's poll interval is longer"
    )


async def _poll_until_create_accepted(
    client: ReyaTradingClient,
    market: RlMarket,
    config: RateLimitSuiteConfig,
) -> str:
    """Probe with creates until one is accepted, or the deadline passes."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + config.timing.eject_timeout_s
    last: RestReject | None = None
    while loop.time() < deadline:
        try:
            return await create_resting_order(client, market)
        except ApiException as exc:
            last = rest_reject(exc)
            logger.info("un-eject not yet visible: %s", last.describe())
        await asyncio.sleep(_poll_interval(config))
    raise AssertionError(
        f"trading did not resume {config.timing.eject_timeout_s}s after un-eject; last reject: "
        f"{last.describe() if last else '<none>'}"
    )


async def _poll_until_resting_orders_swept(
    client: ReyaTradingClient,
    market: RlMarket,
    config: RateLimitSuiteConfig,
) -> None:
    """Wait for the ME eject sweep to clear the account's RESTING orders.

    Read-only poll, and deliberately scoped to non-trigger orders: an armed
    SL/TP is not resting liquidity and the sweep keeps it (see the module
    docstring), so polling for an EMPTY book would be asserting the opposite of
    the ruling.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + config.timing.eject_timeout_s
    remaining: list[str] = []
    while loop.time() < deadline:
        remaining = await resting_order_ids(client, market.symbol)
        if not remaining:
            return
        await asyncio.sleep(config.timing.poll_interval_s)
    raise AssertionError(
        f"the eject sweep left {len(remaining)} resting orders after " f"{config.timing.eject_timeout_s}s: {remaining}"
    )


async def _arm_trigger_if_possible(
    client: ReyaTradingClient,
    config: RateLimitSuiteConfig,
) -> str | None:
    """Best-effort: arm one far-out-of-the-money stop so retention can be proven.

    Returns the armed order id, or ``None`` when the harness cannot arm one —
    in which case the caller records why and the rest of the eject flow still
    runs. SL/TP is perp-only while this suite is otherwise spot-only, so an arm
    is only attempted when the wiring points ``RL_TEST_TRIGGER_SYMBOL`` at a
    perp market. No position is needed: the close size is derived from the live
    position at FIRE time, so arming is gated on the market being a perp and on
    the one-STOP_LOSS-and-one-TAKE_PROFIT-per-(account, market) bound only.
    Every foreseeable refusal (unknown symbol, no mark price yet, a stop already
    armed there) is a skip rather than a failure: this probe strengthens the
    eject test, it must never be able to fail it.
    """
    symbol = config.trigger_symbol
    if symbol is None:
        logger.info("armed-trigger retention not probed: RL_TEST_TRIGGER_SYMBOL is unset")
        return None

    try:
        definitions = await client.reference.get_perp_market_definitions()
        definition = next((item for item in definitions if item.symbol == symbol), None)
        if definition is None:
            logger.info("armed-trigger retention not probed: %s is not a perp market", symbol)
            return None
        summary = await client.markets.get_perp_market_summary(symbol)
        if summary.mark_price is None:
            logger.info("armed-trigger retention not probed: %s has no mark price yet", symbol)
            return None
        trigger_px = quantize_down(Decimal(summary.mark_price) * TRIGGER_PRICE_FACTOR, Decimal(definition.tick_size))
        response = await client.create_trigger_order(
            TriggerOrderParameters(
                symbol=symbol,
                is_buy=False,
                trigger_px=wire(trigger_px),
                trigger_type=OrderType.STOP_LOSS,
            )
        )
    except (ApiException, ValueError) as exc:
        logger.info("armed-trigger retention not probed: could not arm on %s (%s)", symbol, exc)
        return None

    logger.info("armed a stop on %s at %s (orderId=%s)", symbol, trigger_px, response.order_id)
    return response.order_id


async def test_eject_blocks_creates_sweeps_resting_orders_keeps_stops_and_uneject_restores_trading(
    rl_client: ReyaTradingClient,
    rl_market: RlMarket,
    rl_suite_config: RateLimitSuiteConfig,
    rl_eject_hook,
) -> None:
    """The full eject lifecycle over one account."""
    wallet = rl_client.config.owner_wallet_address
    account_id = rl_client.config.account_id
    assert account_id is not None

    await ensure_flat(rl_client, rl_suite_config, rl_market.symbol)
    for _ in range(RESTING_ORDERS):
        await create_resting_order(rl_client, rl_market)
        await asyncio.sleep(rl_suite_config.timing.place_pace_s)
    resting = await open_order_ids(rl_client, rl_market.symbol)
    assert len(resting) >= RESTING_ORDERS, (
        f"the eject flow needs {RESTING_ORDERS} resting orders (one to cancel by hand, one for the "
        f"sweep to prove itself); only {len(resting)} are open"
    )

    armed_trigger_id = await _arm_trigger_if_possible(rl_client, rl_suite_config)

    ejected = False
    try:
        rl_eject_hook.eject(wallet=wallet, account_id=account_id)
        ejected = True

        # Risk-off first, before the sweep gets a chance to run: an ejected
        # owner must still be able to unwind by hand. Losing the race to the
        # sweep (order-not-found-class) is a pass; a gate verdict is not.
        cancel_outcome = await assert_not_whitelist_gated(
            rl_client.cancel_order(
                order_id=resting[0],
                symbol=rl_market.symbol,
                account_id=account_id,
            ),
            "ejected-account cancel",
        )
        logger.info(
            "ejected account can still cancel (outcome: %s)",
            cancel_outcome.describe() if cancel_outcome else "cancel accepted",
        )

        create_reject = await _poll_until_create_rejected(rl_client, rl_market, rl_suite_config)
        logger.info("eject reject observed (create): %s", create_reject.describe())
        _assert_eject_reject(create_reject, "ejected-account create")

        # Modify is refused for the same reason a create is: both are
        # risk-INCREASING (a qty-up or reprice grows exposure), so the ME
        # rejects them for an ejected account while cancels stay open. Probed
        # on an absent order id — both the edge gate and the ME's ejected-set
        # check run before the order lookup, so this cannot race the sweep.
        modify_reject = await capture_rest_reject(
            rl_client.modify_order(
                ModifyOrderParameters(
                    symbol=rl_market.symbol,
                    is_buy=True,
                    limit_px=wire(rl_market.resting_buy_price),
                    qty=wire(rl_market.min_qty),
                    post_only=False,
                    expires_after=None,
                    time_in_force=TimeInForce.GTC,
                    order_id=ABSENT_ORDER_ID,
                )
            ),
            "ejected-account modify",
        )
        logger.info("eject reject observed (modify): %s", modify_reject.describe())
        _assert_eject_reject(modify_reject, "ejected-account modify")

        await _poll_until_resting_orders_swept(rl_client, rl_market, rl_suite_config)
        logger.info("eject sweep cleared the resting orders")

        # The sweep is "no resting liquidity", NOT "empty book": an eject does
        # not close positions, so the armed stops protecting them are kept.
        if armed_trigger_id is not None:
            surviving = await armed_trigger_ids(rl_client)
            assert armed_trigger_id in surviving, (
                f"the eject sweep cancelled armed trigger {armed_trigger_id}; ejecting does not close "
                f"positions, so protective stops must survive it (armed triggers now open: {surviving})"
            )
            logger.info("armed trigger %s survived the eject sweep", armed_trigger_id)
    finally:
        if ejected:
            rl_eject_hook.uneject(wallet=wallet, account_id=account_id)
        if armed_trigger_id is not None:
            # Cleanup only — a failure here must never mask the test's own
            # outcome (same discipline as ``ensure_flat``).
            try:
                await rl_client.cancel_order(
                    order_id=armed_trigger_id,
                    symbol=str(rl_suite_config.trigger_symbol),
                    account_id=account_id,
                )
            except (ApiException, OSError, RuntimeError) as exc:
                logger.warning("could not disarm probe trigger %s (%s: %s)", armed_trigger_id, type(exc).__name__, exc)

    order_id = await _poll_until_create_accepted(rl_client, rl_market, rl_suite_config)
    logger.info("trading resumed after un-eject: orderId=%s", order_id)
