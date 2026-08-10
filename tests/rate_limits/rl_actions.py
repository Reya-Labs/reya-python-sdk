"""Order-flow helpers shared by the live Rate-Limit v1 modules.

Everything here is deliberately conservative: the suite must prove "a reject
happens within N attempts" and "the retry-after is sane", never re-derive the
engine's GCRA arithmetic. Placement is paced where a test needs to reach a cap
WITHOUT tripping the rate bucket first, and unpaced only where tripping the
rate bucket is the point.
"""

from __future__ import annotations

from typing import Callable

import asyncio
import logging
from collections.abc import Awaitable
from dataclasses import dataclass
from decimal import Decimal

import pytest

from sdk.open_api.exceptions import ApiException
from sdk.open_api.models.order import Order
from sdk.open_api.models.order_type import OrderType
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api import ReyaTradingClient
from sdk.reya_rest_api.models.orders import LimitOrderParameters, TriggerOrderParameters
from tests.rate_limits.rl_config import (
    HTTP_FORBIDDEN,
    HTTP_RATE_LIMITED,
    NOT_WHITELISTED_ERROR,
    OPEN_ORDER_COUNT_EXCEEDED_ERROR,
    RATE_LIMITED_ERROR,
    RateLimitSuiteConfig,
)
from tests.rate_limits.rl_errors import RestReject, rest_reject

logger = logging.getLogger("reya.rate_limits")

#: A buy this far below the oracle price rests instead of crossing.
RESTING_BUY_FACTOR = Decimal("0.5")

#: A stop this far below the mark stays armed for the whole test instead of
#: firing — the same "far out of the money" trick the resting order uses.
TRIGGER_PRICE_FACTOR = Decimal("0.5")

#: Spot definitions expose the wrapped base asset; oracle prices use the
#: unwrapped symbol (same mapping the root conftest applies).
_ORACLE_ASSET_ALIASES = {"WETH": "ETH", "WBTC": "BTC"}

#: Order types that are ARMED protection rather than resting liquidity. The
#: eject sweep removes resting orders and deliberately keeps these (see
#: ``test_eject_flow``): ejecting an account does not close its positions, so
#: taking its stops away would leave it unprotected.
TRIGGER_ORDER_TYPES = (OrderType.STOP_LOSS, OrderType.TAKE_PROFIT)


@dataclass(frozen=True)
class RlMarket:
    """The market the suite trades on, resolved from the live definitions."""

    symbol: str
    market_id: int
    base_asset: str
    min_qty: Decimal
    qty_step: Decimal
    tick_size: Decimal
    oracle_price: Decimal

    @property
    def resting_buy_price(self) -> Decimal:
        """A tick-aligned buy price far below the touch, so GTCs just rest."""
        return quantize_down(self.oracle_price * RESTING_BUY_FACTOR, self.tick_size)


def quantize_down(value: Decimal, step: Decimal) -> Decimal:
    """Floor ``value`` onto ``step``'s grid (identity when the step is 0)."""
    if step <= 0:
        return value
    return (value // step) * step


def wire(value: Decimal) -> str:
    """Fixed-point rendering; never scientific notation (the server rejects it)."""
    return format(value, "f")


async def resolve_market(client: ReyaTradingClient, symbol: str) -> RlMarket:
    """Resolve ``symbol`` from the live spot definitions plus its oracle price."""
    definitions = await client.reference.get_spot_market_definitions()
    definition = next((item for item in definitions if item.symbol == symbol), None)
    if definition is None:
        available = ", ".join(sorted(item.symbol for item in definitions)) or "<none>"
        pytest.skip(f"RL_TEST_SYMBOL={symbol} not in /v2/spotMarketDefinitions; available: {available}")

    base_asset = definition.base_asset
    oracle_asset = _ORACLE_ASSET_ALIASES.get(base_asset.upper(), base_asset)
    oracle_price = Decimal((await client.get_asset_oracle_price(oracle_asset)).oracle_price)

    return RlMarket(
        symbol=definition.symbol,
        market_id=definition.market_id,
        base_asset=base_asset,
        min_qty=Decimal(str(definition.min_order_qty)),
        qty_step=Decimal(str(definition.qty_step_size)),
        tick_size=Decimal(str(definition.tick_size)),
        oracle_price=oracle_price,
    )


async def resolve_perp_market(client: ReyaTradingClient, symbol: str) -> RlMarket:
    """Resolve ``symbol`` from the live PERP definitions plus its mark price.

    Only the trigger-bearing legs need this: SL/TP is perp-only. ``oracle_price``
    carries the mark price, which is the reference a perp trigger and a
    far-from-touch resting order are both priced off.
    """
    definitions = await client.reference.get_perp_market_definitions()
    definition = next((item for item in definitions if item.symbol == symbol), None)
    if definition is None:
        available = ", ".join(sorted(item.symbol for item in definitions)) or "<none>"
        raise AssertionError(f"{symbol} is not in /v2/perpMarketDefinitions; available: {available}")

    summary = await client.markets.get_perp_market_summary(symbol)
    if summary.mark_price is None:
        raise AssertionError(f"{symbol} has no mark price yet; the deployment is not ready for a trigger probe")

    return RlMarket(
        symbol=definition.symbol,
        market_id=definition.market_id,
        base_asset="",
        min_qty=Decimal(str(definition.min_order_qty)),
        qty_step=Decimal(str(definition.qty_step_size)),
        tick_size=Decimal(str(definition.tick_size)),
        oracle_price=Decimal(str(summary.mark_price)),
    )


def resting_order(market: RlMarket, *, qty: Decimal | None = None, price: Decimal | None = None):
    """Build the far-out GTC BUY the suite uses as its unit of "an open order"."""
    return LimitOrderParameters(
        symbol=market.symbol,
        is_buy=True,
        limit_px=wire(price if price is not None else market.resting_buy_price),
        qty=wire(qty if qty is not None else market.min_qty),
        time_in_force=TimeInForce.GTC,
    )


async def create_resting_order(
    client: ReyaTradingClient,
    market: RlMarket,
    *,
    qty: Decimal | None = None,
    price: Decimal | None = None,
) -> str:
    """Place one resting GTC and return its order id (raises on rejection)."""
    response = await client.create_limit_order(resting_order(market, qty=qty, price=price))
    assert response.order_id is not None, f"createOrder returned no orderId: {response!r}"
    return response.order_id


async def arm_protective_stop(client: ReyaTradingClient, market: RlMarket) -> str:
    """Arm one far-out-of-the-money STOP_LOSS on a perp market; return its id.

    No position is required: the trigger contract is whole-position-at-fire-time
    (the client omits ``qty``, the envelope signs the full-position sentinel and
    the close size is derived when the trigger fires). The arm-time bounds are
    only "perp market" and "at most one STOP_LOSS and one TAKE_PROFIT per
    (account, market)".
    """
    trigger_px = quantize_down(market.oracle_price * TRIGGER_PRICE_FACTOR, market.tick_size)
    response = await client.create_trigger_order(
        TriggerOrderParameters(
            symbol=market.symbol,
            is_buy=False,
            trigger_px=wire(trigger_px),
            trigger_type=OrderType.STOP_LOSS,
        )
    )
    assert response.order_id is not None, f"createOrder (trigger) returned no orderId: {response!r}"
    return response.order_id


async def place_paced(
    client: ReyaTradingClient,
    market: RlMarket,
    config: RateLimitSuiteConfig,
    count: int,
    *,
    qty: Decimal | None = None,
    price: Decimal | None = None,
) -> list[str]:
    """Place ``count`` resting GTCs at the sustained place rate.

    Pacing is what lets a cap test reach the OPEN-ORDER cap without the GCRA
    place bucket rejecting first. A rate-limit reject here means the pacing is
    too aggressive for the deployment's configured rate — fail with the exact
    knobs to fix rather than skipping and hiding the cap coverage.
    """
    order_ids: list[str] = []
    for index in range(count):
        if index:
            await asyncio.sleep(config.timing.place_pace_s)
        try:
            order_ids.append(await create_resting_order(client, market, qty=qty, price=price))
        except ApiException as exc:
            reject = rest_reject(exc)
            if reject.code == RATE_LIMITED_ERROR:
                pytest.fail(
                    f"paced placement hit the GCRA place bucket after {index} orders ({reject.describe()}); "
                    "raise RL_TEST_PLACE_PACE_S or align RL_TEST_STANDARD_PLACE_PER_MIN with the deployment",
                    pytrace=False,
                )
            raise
    return order_ids


@dataclass(frozen=True)
class BurstResult:
    """The outcome of bursting creates until the place bucket rejects."""

    placed: list[str]
    reject: RestReject
    attempts: int


async def burst_until_rate_limited(
    client: ReyaTradingClient,
    market: RlMarket,
    config: RateLimitSuiteConfig,
    *,
    qty: Decimal | None = None,
    price: Decimal | None = None,
) -> BurstResult:
    """Fire creates as fast as the transport allows until one is rate limited.

    Returns the accepted order ids plus the reject. Fails with an actionable
    message when the open-order COUNT cap bites first (that means the
    deployment's count cap is tighter than its place burst, so the two knobs
    need reconciling) or when no reject appears within the attempt bound.
    """
    placed: list[str] = []
    bound = config.burst_attempt_bound()

    for attempt in range(1, bound + 1):
        reject: RestReject | None = None
        try:
            placed.append(await create_resting_order(client, market, qty=qty, price=price))
        except ApiException as exc:
            reject = rest_reject(exc)
        if reject is None:
            continue

        if reject.code == RATE_LIMITED_ERROR:
            logger.info("place bucket tripped after %d accepted creates (%s)", len(placed), reject.describe())
            return BurstResult(placed=placed, reject=reject, attempts=attempt)

        if reject.code == OPEN_ORDER_COUNT_EXCEEDED_ERROR:
            pytest.fail(
                f"open-order count cap ({len(placed)} resting) bit before the place bucket; "
                "the deployment's count cap is tighter than its place burst — raise "
                "RL_TEST_STANDARD_OPEN_ORDER_COUNT_CAP on the deployment or lower the place rate",
                pytrace=False,
            )

        raise AssertionError(f"unexpected rejection while bursting creates: {reject.describe()}")

    raise AssertionError(
        f"no RATE_LIMITED_ERROR within {bound} creates (accepted {len(placed)}); "
        "either rate limiting is not enabled on this deployment or "
        "RL_TEST_STANDARD_PLACE_PER_MIN / RL_TEST_STANDARD_PLACE_BURST are far below the real values"
    )


@dataclass(frozen=True)
class BurstOutcome:
    """The outcome of bursting one op until the deployment answered ``code``."""

    accepted: int
    attempts: int
    reject: RestReject
    tolerated: tuple[str | None, ...]


async def burst_until_code(
    operation: Callable[[int], Awaitable[object]],
    *,
    expected_code: str,
    attempts: int,
    label: str,
) -> BurstOutcome:
    """Fire ``operation(attempt)`` unpaced until it answers ``expected_code``.

    Rejects with any OTHER code are tolerated and counted, not fatal: probing a
    bucket often means firing ops the deployment refuses for an unrelated
    reason (an absent order id, an empty book). Admission is charged where a
    client message enters the reactor, ahead of handler dispatch, so those
    still debit the bucket under test — which is what makes the probe valid.
    """
    accepted = 0
    tolerated: list[str | None] = []
    for attempt in range(1, attempts + 1):
        try:
            await operation(attempt)
        except ApiException as exc:
            reject = rest_reject(exc)
            if reject.code == expected_code:
                logger.info(
                    "[%s] %s after %d accepted + %d tolerated (%s)",
                    label,
                    expected_code,
                    accepted,
                    len(tolerated),
                    reject.describe(),
                )
                return BurstOutcome(
                    accepted=accepted,
                    attempts=attempt,
                    reject=reject,
                    tolerated=tuple(tolerated),
                )
            tolerated.append(reject.code)
            continue
        accepted += 1

    raise AssertionError(
        f"[{label}] no {expected_code} within {attempts} attempts "
        f"({accepted} accepted, tolerated codes: {sorted({str(code) for code in tolerated})}); "
        "either the bucket is not enforced on this deployment or the configured limits are far below the real ones"
    )


def assert_rate_limited(reject: RestReject, label: str) -> None:
    """Assert a reject is the per-account GCRA verdict on the 429 status."""
    assert reject.code == RATE_LIMITED_ERROR, f"[{label}] expected {RATE_LIMITED_ERROR}; got {reject.describe()}"
    assert reject.status == HTTP_RATE_LIMITED, f"[{label}] expected HTTP {HTTP_RATE_LIMITED}; got {reject.describe()}"


async def assert_not_whitelist_gated(awaitable: object, label: str) -> RestReject | None:
    """Await a risk-off op that the whitelist gate must NEVER block.

    The gate covers create + modify only: a de-whitelisted (or ejected) owner
    must still be able to cancel resting orders and keep its dead-man's switch
    alive, or removing a wallet from the whitelist would trap it in its
    positions.

    Returns ``None`` when the op succeeded, or the reject when the deployment
    refused it for an UNRELATED reason (e.g. cancelling an order that does not
    exist). Only a gate verdict — ``NOT_WHITELISTED_ERROR``, or any 403-class
    status — fails the assertion.
    """
    try:
        await awaitable  # type: ignore[misc]  # caller passes an awaitable client call
        return None
    except ApiException as exc:
        reject = rest_reject(exc)

    assert reject.code != NOT_WHITELISTED_ERROR, (
        f"[{label}] risk-off must never be whitelist-gated, but the edge returned "
        f"{NOT_WHITELISTED_ERROR}: {reject.describe()}"
    )
    assert (
        reject.status != HTTP_FORBIDDEN
    ), f"[{label}] risk-off must never be rejected 403-class by the gate; got {reject.describe()}"
    logger.info("[%s] not gated; rejected for an unrelated reason: %s", label, reject.describe())
    return reject


async def open_orders(client: ReyaTradingClient, symbol: str | None = None) -> list[Order]:
    """Open orders for the client's ACCOUNT, optionally narrowed to one symbol.

    ``get_open_orders`` queries ``/v2/wallet/{address}/openOrders``, which is
    owner-WALLET scoped and therefore returns every account that wallet owns.
    The ME's caps and its ejected set are both ``account_id``-keyed, so the
    suite must observe the same population it is asserting on — otherwise a
    test wallet owning a second account reads counts that no cap is computed
    over, and ``ensure_flat`` (which cancels per-account) could never converge.
    """
    account_id = client.config.account_id
    orders = await client.get_open_orders()
    return [
        order
        for order in orders
        if order.order_id is not None
        and (symbol is None or order.symbol == symbol)
        and (account_id is None or order.account_id == account_id)
    ]


async def open_order_ids(client: ReyaTradingClient, symbol: str | None = None) -> list[str]:
    """Order ids currently open for the client's account (optionally per symbol)."""
    return [order.order_id for order in await open_orders(client, symbol)]


async def resting_order_ids(client: ReyaTradingClient, symbol: str | None = None) -> list[str]:
    """Ids of RESTING (non-trigger) orders — what the eject sweep removes."""
    return [
        order.order_id for order in await open_orders(client, symbol) if order.order_type not in TRIGGER_ORDER_TYPES
    ]


async def armed_trigger_ids(client: ReyaTradingClient, symbol: str | None = None) -> list[str]:
    """Ids of ARMED SL/TP triggers — what the eject sweep deliberately keeps."""
    return [order.order_id for order in await open_orders(client, symbol) if order.order_type in TRIGGER_ORDER_TYPES]


async def wait_for_open_order_count(
    client: ReyaTradingClient,
    expected: int,
    *,
    timeout_s: float,
    poll_s: float = 0.5,
    symbol: str | None = None,
) -> list[str]:
    """Poll ``openOrders`` until the account under test shows exactly ``expected``."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    observed: list[str] = []
    while loop.time() < deadline:
        observed = await open_order_ids(client, symbol)
        if len(observed) == expected:
            return observed
        await asyncio.sleep(poll_s)
    raise AssertionError(f"openOrders did not reach {expected} orders within {timeout_s}s; last seen {observed}")


async def ensure_flat(client: ReyaTradingClient, config: RateLimitSuiteConfig, symbol: str | None = None) -> None:
    """Best-effort mass-cancel so a test starts (and leaves) with no resting orders.

    Tolerates a rate-limited mass-cancel (the bulk-cancel bucket is the
    tightest Standard bucket) by backing off once and retrying. Transport
    failures degrade to a warning: cleanup must never mask the test's own
    outcome (same discipline as the root conftest's COD disarm).
    """
    for attempt in range(2):
        try:
            if not await open_order_ids(client, symbol):
                return
            await client.mass_cancel(symbol=symbol, account_id=client.config.account_id)
            await asyncio.sleep(config.timing.poll_interval_s)
            if not await open_order_ids(client, symbol):
                return
        except ApiException as exc:
            reject = rest_reject(exc)
            backoff = reject.retry_after_s or config.timing.bucket_recovery_s
            logger.warning(
                "mass-cancel attempt %d rejected (%s); backing off %ss", attempt + 1, reject.describe(), backoff
            )
            await asyncio.sleep(min(backoff, config.timing.retry_after_max_s) + config.timing.retry_after_slack_s)
        except (OSError, RuntimeError) as exc:
            logger.warning("cleanup could not reach the API (%s: %s); skipping flatten", type(exc).__name__, exc)
            return

    try:
        remaining = await open_order_ids(client, symbol)
    except (ApiException, OSError, RuntimeError):
        return
    if remaining:
        logger.warning("account %s still has %d resting orders after cleanup", client.config.account_id, len(remaining))


async def rusd_balance(client: ReyaTradingClient) -> Decimal:
    """The account's spendable RUSD, used to gate the notional-cap test.

    Scoped to ``client.config.account_id`` for the same reason as
    :func:`open_orders`: the balances endpoint is wallet-scoped and returns one
    RUSD row per owned account, so taking the first match can read a sibling
    account's balance and gate the test on the wrong number.
    """
    account_id = client.config.account_id
    for balance in await client.get_account_balances():
        if balance.asset.upper() != "RUSD" or balance.real_balance is None:
            continue
        if account_id is not None and balance.account_id != account_id:
            continue
        return Decimal(str(balance.real_balance))
    return Decimal(0)
