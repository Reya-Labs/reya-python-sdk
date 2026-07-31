"""Rate-Limit v1 §4.2 — the ME open-order caps (count + notional).

Both caps are ME-enforced on the book-mutation paths and are derived from the
resting book, so they are asserted the same way a user would experience them:
fill the book to the cap, watch the next create/modify get rejected, free some
room, watch it succeed again.

Placement here is PACED to the configured sustained place rate — otherwise the
GCRA rate bucket (§4.1) would reject first and the cap would never be reached.
"""

from __future__ import annotations

import asyncio
import logging
from decimal import Decimal

import pytest

from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api import ReyaTradingClient
from sdk.reya_rest_api.models.orders import LimitOrderParameters, ModifyOrderParameters
from tests.rate_limits.rl_actions import (
    RlMarket,
    create_resting_order,
    ensure_flat,
    open_order_ids,
    place_paced,
    quantize_down,
    rusd_balance,
    wait_for_open_order_count,
    wire,
)
from tests.rate_limits.rl_config import (
    HTTP_RATE_LIMITED,
    OPEN_ORDER_COUNT_EXCEEDED_ERROR,
    OPEN_ORDER_NOTIONAL_EXCEEDED_ERROR,
    RateLimitSuiteConfig,
    requires_rate_limits,
)
from tests.rate_limits.rl_errors import capture_rest_reject

logger = logging.getLogger("reya.rate_limits")

pytestmark = [pytest.mark.rate_limits, requires_rate_limits]

#: Fractions of the configured notional cap used to build the scenario.
RESTING_FRACTION = Decimal("0.8")
MODIFY_FRACTION = Decimal("1.2")
IOC_FRACTION = Decimal("0.5")
#: Free RUSD required before the notional test can run: the resting leg plus
#: the qty-up modify plus the exempt IOC must all clear the spot balance check,
#: otherwise the reject under test would be INSUFFICIENT_BALANCE_ERROR instead.
REQUIRED_BALANCE_MULTIPLE = Decimal("1.5")


async def test_open_order_count_cap(
    rl_client: ReyaTradingClient,
    rl_market: RlMarket,
    rl_suite_config: RateLimitSuiteConfig,
) -> None:
    """Fill the book to the count cap → next create is 429 COUNT_EXCEEDED."""
    cap = rl_suite_config.limits.open_order_count_cap
    await ensure_flat(rl_client, rl_suite_config, rl_market.symbol)

    placed = await place_paced(rl_client, rl_market, rl_suite_config, cap)
    resting = await wait_for_open_order_count(
        rl_client,
        cap,
        timeout_s=rl_suite_config.timing.settle_timeout_s,
        symbol=rl_market.symbol,
    )
    logger.info("open-order count cap: %d resting (placed %d)", len(resting), len(placed))

    await asyncio.sleep(rl_suite_config.timing.place_pace_s)
    reject = await capture_rest_reject(
        rl_client.create_limit_order(
            LimitOrderParameters(
                symbol=rl_market.symbol,
                is_buy=True,
                limit_px=wire(rl_market.resting_buy_price),
                qty=wire(rl_market.min_qty),
                time_in_force=TimeInForce.GTC,
            )
        ),
        "create at count cap",
    )
    logger.info("create at count cap: %s", reject.describe())
    assert reject.code == OPEN_ORDER_COUNT_EXCEEDED_ERROR, (
        f"expected {OPEN_ORDER_COUNT_EXCEEDED_ERROR} at the count cap; got {reject.describe()} — "
        "if this is RATE_LIMITED_ERROR the pacing is too fast (raise RL_TEST_PLACE_PACE_S); "
        "if the create was accepted, RL_TEST_STANDARD_OPEN_ORDER_COUNT_CAP is below the deployment's cap"
    )
    assert reject.status == HTTP_RATE_LIMITED, f"cap rejects map to HTTP 429; got {reject.describe()}"

    await rl_client.cancel_order(
        order_id=resting[0],
        symbol=rl_market.symbol,
        account_id=rl_client.config.account_id,
    )
    await wait_for_open_order_count(
        rl_client,
        cap - 1,
        timeout_s=rl_suite_config.timing.settle_timeout_s,
        symbol=rl_market.symbol,
    )

    await asyncio.sleep(rl_suite_config.timing.place_pace_s)
    order_id = await create_resting_order(rl_client, rl_market)
    logger.info("create admitted after freeing one slot: orderId=%s", order_id)


async def test_open_order_notional_cap_with_ioc_exemption(
    rl_client: ReyaTradingClient,
    rl_market: RlMarket,
    rl_suite_config: RateLimitSuiteConfig,
) -> None:
    """A qty-up modify past the notional cap is rejected; an IOC is exempt.

    The notional cap sums ``remaining_qty x limit_px`` over GTC/GTT orders
    only, so an IOC larger than the remaining headroom must still be admitted.
    Admission is what is asserted — the IOC is priced far from the touch so it
    crosses nothing, keeping the test free of any liquidity or settlement
    dependency.
    """
    cap = rl_suite_config.limits.open_notional_cap
    price = rl_market.resting_buy_price
    assert price > 0, f"resting price must be positive; oracle={rl_market.oracle_price}"

    balance = await rusd_balance(rl_client)
    if balance < cap * REQUIRED_BALANCE_MULTIPLE:
        pytest.skip(
            f"notional-cap coverage needs >= {cap * REQUIRED_BALANCE_MULTIPLE} RUSD free "
            f"(cap {cap} x {REQUIRED_BALANCE_MULTIPLE}); account has {balance}"
        )

    def qty_for(fraction: Decimal) -> Decimal:
        return quantize_down(cap * fraction / price, rl_market.qty_step)

    resting_qty = qty_for(RESTING_FRACTION)
    modify_qty = qty_for(MODIFY_FRACTION)
    ioc_qty = qty_for(IOC_FRACTION)
    if min(resting_qty, ioc_qty) < rl_market.min_qty or modify_qty <= resting_qty:
        pytest.skip(
            f"RL_TEST_STANDARD_OPEN_NOTIONAL_CAP={cap} is too small for {rl_market.symbol} "
            f"(price {price}, minQty {rl_market.min_qty}, step {rl_market.qty_step})"
        )

    await ensure_flat(rl_client, rl_suite_config, rl_market.symbol)

    order_id = await create_resting_order(rl_client, rl_market, qty=resting_qty, price=price)
    await wait_for_open_order_count(
        rl_client,
        1,
        timeout_s=rl_suite_config.timing.settle_timeout_s,
        symbol=rl_market.symbol,
    )
    logger.info("notional cap: resting %s @ %s (~%s of cap %s)", resting_qty, price, RESTING_FRACTION, cap)

    await asyncio.sleep(rl_suite_config.timing.place_pace_s)
    reject = await capture_rest_reject(
        rl_client.modify_order(
            ModifyOrderParameters(
                symbol=rl_market.symbol,
                is_buy=True,
                limit_px=wire(price),
                qty=wire(modify_qty),
                post_only=False,
                expires_after=None,
                time_in_force=TimeInForce.GTC,
                order_id=int(order_id),
            )
        ),
        "qty-up modify past the notional cap",
    )
    logger.info("qty-up modify past the notional cap: %s", reject.describe())
    assert reject.code == OPEN_ORDER_NOTIONAL_EXCEEDED_ERROR, (
        f"expected {OPEN_ORDER_NOTIONAL_EXCEEDED_ERROR} on a qty-up past the cap; got {reject.describe()} — "
        "if the modify was accepted, RL_TEST_STANDARD_OPEN_NOTIONAL_CAP is below the deployment's cap"
    )

    await asyncio.sleep(rl_suite_config.timing.place_pace_s)
    ioc_response = await rl_client.create_limit_order(
        LimitOrderParameters(
            symbol=rl_market.symbol,
            is_buy=True,
            limit_px=wire(price),
            qty=wire(ioc_qty),
            time_in_force=TimeInForce.IOC,
        )
    )
    logger.info("IOC exempt from the notional cap: status=%s orderId=%s", ioc_response.status, ioc_response.order_id)
    assert ioc_response.order_id is not None, "the engine assigns an orderId to every admitted order"

    still_resting = await open_order_ids(rl_client, rl_market.symbol)
    assert order_id in still_resting, "the rejected modify must leave the original resting order untouched"
