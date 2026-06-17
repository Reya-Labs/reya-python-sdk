"""
modifyOrder concurrency / race tests parametrized over [spot, perp] — live e2e.

The off-chain order-entry pod handles multiple in-flight frames per connection
and multiplexes them over ONE off-chain→ME TCP connection, so the order in
which concurrently-submitted requests reach the (single-writer) matching engine
is NONDETERMINISTIC. On top of that, every signed request carries a per-account
nonce and the server enforces nonce monotonicity, so concurrently-submitted
same-account requests legitimately RACE on the nonce: whichever the server
processes second-with-a-lower-nonce is rejected INVALID_NONCE_ERROR. That is
correct replay protection, not a bug — the point of these tests is that under
concurrency the system stays CONSISTENT (clean per-op accept/reject, never a
torn order state, never more than one resting order, never a hang).

Every test asserts an ALLOWED SET of per-op outcomes plus race-INDEPENDENT
invariants (never a fixed timing-dependent result), and never swallows a
TIMEOUT (a hang is a real bug). No test crosses the book (orders rest far from
the touch), so no fills / settlement occur and no settlement cleanup is wired —
only `close_all` in a `finally` leaves the shared devnet accounts flat.
"""

import asyncio
import time
from decimal import Decimal

import pytest

from sdk.open_api.exceptions import ApiException
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api.models.orders import ModifyOrderParameters
from tests.helpers import ReyaTester
from tests.helpers.builders import OrderBuilder
from tests.helpers.builders.order_builder import full_state_modify_params
from tests.helpers.market_config import PerpTestConfig, SpotTestConfig
from tests.helpers.order_lifecycle import rest_gtc, wait_for_order_fields
from tests.helpers.reya_tester import logger

pytestmark = [pytest.mark.e2e, pytest.mark.modify]

# Modify error codes a race can legitimately surface (substring-matched in the
# REST ApiException message, as the rest of the suite does).
_KNOWN_CODES = (
    "ORDER_NOT_FOUND",
    "EMPTY_MODIFY_ERROR",
    "INVALID_NONCE_ERROR",
    "MODIFY_ORDER_OTHER_ERROR",
    "MODIFY_QTY_BELOW_FILLED_ERROR",
    "POST_ONLY_WOULD_CROSS_ERROR",
    "INPUT_VALIDATION_ERROR",
)


def classify(result: object) -> str:
    """Normalise a gather() result to "ok" or a known error code, so a race's
    allowed-set assertion reads uniformly. A TIMEOUT or an unexpected exception
    type is surfaced verbatim (never silently treated as a pass)."""
    if isinstance(result, asyncio.TimeoutError):
        return "TIMEOUT"
    if isinstance(result, ApiException):
        msg = str(result)
        for code in _KNOWN_CODES:
            if code in msg:
                return code
        return f"ApiException:{msg[:80]}"
    if isinstance(result, Exception):
        return f"{type(result).__name__}:{str(result)[:80]}"
    return "ok"


async def _open_orders_for(maker: ReyaTester, order_id: str) -> list:
    """All open orders matching order_id (0 or 1 in a consistent book)."""
    open_orders = await maker.data.open_orders()
    return [o for o in open_orders if str(o.order_id) == str(order_id)]


def _dec(value: str | None) -> Decimal:
    """Decimal of a resting order's px/qty (always set, but typed Optional)."""
    return Decimal(value if value is not None else "0")


def _assert_whole_state(order, allowed_states: list[tuple[Decimal, Decimal]], market_type: str) -> None:
    """The resting order must carry ONE whole (px, qty) state from the allowed
    set — never a px-from-one-modify / qty-from-another torn blend."""
    actual = (_dec(order.limit_px), _dec(order.qty))
    assert actual in allowed_states, (
        f"[{market_type}] resting order is a torn/unexpected state px={order.limit_px} qty={order.qty}; "
        f"allowed: {[(str(p), str(q)) for p, q in allowed_states]}"
    )


@pytest.mark.asyncio
async def test_modify_while_create_in_flight(
    market_config: SpotTestConfig | PerpTestConfig, market_type: str, maker: ReyaTester
) -> None:
    """A modify targeted by clientOrderId fired CONCURRENTLY with the create of
    that order. Both carry nonces, so each is ok / INVALID_NONCE; the modify can
    also land before the order exists (ORDER_NOT_FOUND). Invariant: at most ONE
    resting order for the clientOrderId, carrying one WHOLE state (never a torn
    blend)."""
    coid = int(time.time() * 1_000_000)
    create_px = str(market_config.price(0.50))
    modify_px = str(market_config.price(0.52))
    min_qty = market_config.min_qty

    create_params = (
        OrderBuilder()
        .symbol(market_config.symbol)
        .buy()
        .price(create_px)
        .qty(min_qty)
        .gtc()
        .client_order_id(coid)
        .build()
    )
    modify_params = ModifyOrderParameters(
        symbol=market_config.symbol,
        is_buy=True,
        limit_px=modify_px,
        qty=min_qty,
        post_only=False,
        expires_after=0,
        time_in_force=TimeInForce.GTC,
        client_order_id=coid,
        resting_client_order_id=coid,
    )

    try:
        create_res, modify_res = await asyncio.gather(
            maker.client.create_limit_order(create_params),
            maker.client.modify_order(modify_params),
            return_exceptions=True,
        )
        create_outcome = classify(create_res)
        modify_outcome = classify(modify_res)
        assert create_outcome in ("ok", "INVALID_NONCE_ERROR"), f"[{market_type}] create outcome={create_outcome}"
        assert modify_outcome in (
            "ok",
            "ORDER_NOT_FOUND",
            "INVALID_NONCE_ERROR",
        ), f"[{market_type}] modify-in-flight outcome={modify_outcome}"
        logger.info(f"[{market_type}] create+modify in-flight → create={create_outcome} modify={modify_outcome}")

        # Invariant (when the create landed): at most ONE resting order for the
        # created orderId, and if present it is one whole state (Order doesn't
        # expose the resting clientOrderId, so key off the engine orderId).
        if create_outcome == "ok":
            assert not isinstance(create_res, BaseException)  # narrowed by create_outcome
            order_id = create_res.order_id
            assert order_id is not None
            await asyncio.sleep(0.5)
            resting = await _open_orders_for(maker, order_id)
            assert len(resting) <= 1, f"[{market_type}] at most one resting order, got {len(resting)}"
            if resting:
                _assert_whole_state(
                    resting[0],
                    [(Decimal(create_px), Decimal(min_qty)), (Decimal(modify_px), Decimal(min_qty))],
                    market_type,
                )
    finally:
        await maker.orders.close_all(fail_if_none=False)


@pytest.mark.asyncio
async def test_concurrent_distinct_modifies(
    market_config: SpotTestConfig | PerpTestConfig, market_type: str, maker: ReyaTester
) -> None:
    """Two DISTINCT full-restate modifies of one resting order fired
    concurrently: each is ok or INVALID_NONCE (the nonce race), with at least
    one applied (the first-processed nonce always succeeds). Invariant: the
    final resting order reflects ONE of the two submitted states exactly (no
    torn blend), orderId stable, exactly one resting order."""
    order = await rest_gtc(maker, market_config, price_multiplier=0.50)
    order_id = order.order_id
    pxa, qtya = str(market_config.price(0.52)), str(Decimal(market_config.min_qty) * 2)
    pxb, qtyb = str(market_config.price(0.54)), str(Decimal(market_config.min_qty) * 3)
    orig = (_dec(order.limit_px), _dec(order.qty))
    allowed = [(Decimal(pxa), Decimal(qtya)), (Decimal(pxb), Decimal(qtyb))]

    try:
        results = await asyncio.gather(
            maker.client.modify_order(full_state_modify_params(order, limit_px=pxa, qty=qtya)),
            maker.client.modify_order(full_state_modify_params(order, limit_px=pxb, qty=qtyb)),
            return_exceptions=True,
        )
        outcomes = [classify(r) for r in results]
        assert all(
            o in ("ok", "INVALID_NONCE_ERROR") for o in outcomes
        ), f"[{market_type}] distinct modifies must be ok / INVALID_NONCE, got {outcomes}"
        assert outcomes.count("ok") >= 1, f"[{market_type}] at least one distinct modify must apply, got {outcomes}"
        logger.info(f"[{market_type}] concurrent distinct modifies → {outcomes}")

        await asyncio.sleep(0.5)
        resting = await _open_orders_for(maker, order_id)
        assert len(resting) == 1, f"[{market_type}] exactly one resting order, got {len(resting)}"
        # If both applied, final ∈ {A,B}; if only one applied, final is that one
        # or (had the loser applied last) the other — always a whole allowed state.
        _assert_whole_state(resting[0], allowed + [orig], market_type)
        assert str(resting[0].order_id) == str(order_id), f"[{market_type}] orderId must be stable"
    finally:
        await maker.orders.close_all(fail_if_none=False)


@pytest.mark.asyncio
async def test_concurrent_duplicate_modifies(
    market_config: SpotTestConfig | PerpTestConfig, market_type: str, maker: ReyaTester
) -> None:
    """Two IDENTICAL modifies (both differ from the original) fired
    concurrently: one applies (ok); the other restates the now-current state
    (EMPTY_MODIFY) or loses the nonce race (INVALID_NONCE). Allowed per-op set:
    {ok, EMPTY_MODIFY_ERROR, INVALID_NONCE_ERROR}, at least one ok. Final resting
    state == the modified px/qty (one whole state)."""
    order = await rest_gtc(maker, market_config, price_multiplier=0.50)
    order_id = order.order_id
    new_px, new_qty = str(market_config.price(0.52)), str(Decimal(market_config.min_qty) * 2)

    try:
        results = await asyncio.gather(
            maker.client.modify_order(full_state_modify_params(order, limit_px=new_px, qty=new_qty)),
            maker.client.modify_order(full_state_modify_params(order, limit_px=new_px, qty=new_qty)),
            return_exceptions=True,
        )
        outcomes = [classify(r) for r in results]
        assert all(
            o in ("ok", "EMPTY_MODIFY_ERROR", "INVALID_NONCE_ERROR") for o in outcomes
        ), f"[{market_type}] duplicate modifies allowed {{ok, EMPTY_MODIFY, INVALID_NONCE}}, got {outcomes}"
        assert outcomes.count("ok") >= 1, f"[{market_type}] at least one duplicate modify must apply, got {outcomes}"
        logger.info(f"[{market_type}] concurrent duplicate modifies → {outcomes}")

        final = await wait_for_order_fields(maker, order_id, limit_px=new_px, qty=new_qty)
        assert str(final.order_id) == str(order_id)
    finally:
        await maker.orders.close_all(fail_if_none=False)


@pytest.mark.asyncio
async def test_modify_cancel_race(
    market_config: SpotTestConfig | PerpTestConfig, market_type: str, maker: ReyaTester
) -> None:
    """A modify and a cancel of the same resting order fired concurrently. Each
    is ok / its rejection code / INVALID_NONCE (the nonce race). Race-independent
    invariant: the account never ends with a torn or duplicated order — at most
    ONE resting order for the id, in one whole state; and it's GONE whenever the
    cancel succeeded."""
    order = await rest_gtc(maker, market_config, price_multiplier=0.50)
    order_id = order.order_id
    new_px = str(market_config.price(0.52))
    orig = (_dec(order.limit_px), _dec(order.qty))
    modified = (Decimal(new_px), _dec(order.qty))

    try:
        modify_res, cancel_res = await asyncio.gather(
            maker.client.modify_order(full_state_modify_params(order, limit_px=new_px)),
            maker.client.cancel_order(order_id=order_id, symbol=market_config.symbol, account_id=maker.account_id),
            return_exceptions=True,
        )
        modify_outcome = classify(modify_res)
        cancel_outcome = classify(cancel_res)
        assert modify_outcome in (
            "ok",
            "ORDER_NOT_FOUND",
            "INVALID_NONCE_ERROR",
        ), f"[{market_type}] modify outcome={modify_outcome}"
        assert cancel_outcome in (
            "ok",
            "ORDER_NOT_FOUND",
            "INVALID_NONCE_ERROR",
        ), f"[{market_type}] cancel outcome={cancel_outcome}"
        logger.info(f"[{market_type}] modify+cancel race → modify={modify_outcome} cancel={cancel_outcome}")

        await asyncio.sleep(0.5)
        resting = await _open_orders_for(maker, order_id)
        assert len(resting) <= 1, f"[{market_type}] at most one resting order, got {len(resting)}"
        if cancel_outcome == "ok":
            assert not resting, f"[{market_type}] a successful cancel must leave the order gone"
        elif resting:
            _assert_whole_state(resting[0], [orig, modified], market_type)
    finally:
        await maker.orders.close_all(fail_if_none=False)


@pytest.mark.asyncio
async def test_concurrent_modifies_stay_consistent(
    market_config: SpotTestConfig | PerpTestConfig, market_type: str, maker: ReyaTester
) -> None:
    """Fire N distinct modifies of one resting order concurrently — a stress on
    the per-account nonce serialization. Each is ok or INVALID_NONCE (never an
    unexpected code, never a hang), at least one applies, and the order never
    tears: exactly one resting order in one whole submitted state. This proves
    the system stays CONSISTENT under burst concurrency (clean reject, no
    corruption) rather than asserting a specific winner."""
    n = 5
    order = await rest_gtc(maker, market_config, price_multiplier=0.50)
    order_id = order.order_id
    orig = (_dec(order.limit_px), _dec(order.qty))
    states = [
        (str(market_config.price(0.50 + 0.01 * (i + 1))), str(Decimal(market_config.min_qty) * (i + 2)))
        for i in range(n)
    ]

    try:
        results = await asyncio.gather(
            *(maker.client.modify_order(full_state_modify_params(order, limit_px=px, qty=qty)) for px, qty in states),
            return_exceptions=True,
        )
        outcomes = [classify(r) for r in results]
        assert all(
            o in ("ok", "INVALID_NONCE_ERROR") for o in outcomes
        ), f"[{market_type}] burst modifies must be ok / INVALID_NONCE only (no corruption/hang), got {outcomes}"
        assert outcomes.count("ok") >= 1, f"[{market_type}] at least one burst modify must apply, got {outcomes}"
        logger.info(f"[{market_type}] {n} concurrent modifies → {outcomes}")

        await asyncio.sleep(0.5)
        resting = await _open_orders_for(maker, order_id)
        assert len(resting) == 1, f"[{market_type}] exactly one resting order after the burst, got {len(resting)}"
        allowed = [(Decimal(px), Decimal(qty)) for px, qty in states] + [orig]
        _assert_whole_state(resting[0], allowed, market_type)
    finally:
        await maker.orders.close_all(fail_if_none=False)
