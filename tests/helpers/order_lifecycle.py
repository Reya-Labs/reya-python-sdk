"""Shared order-lifecycle helpers for the live e2e suites.

Resting helpers (`rest_spot_gtc` / `rest_perp_gtc`), openOrders/WS pollers
(`wait_for_order_fields` / `wait_for_ws_order_change` — the OrdersProvider
cache consumes the matching engine's Redis stream asynchronously w.r.t.
responses, so read-backs must poll, never single-shot), execution pollers
(`wait_for_taker_spot_execution` / `wait_for_taker_perp_execution`), and
numeric assertion helpers. Promoted from the modify suite's modify_helpers.py
when the modify, post-only and orderbook suites all needed them."""

from __future__ import annotations

import asyncio
import time
from decimal import Decimal

import pytest

from sdk.async_api.order import Order as AsyncOrder
from sdk.open_api.models.order import Order
from sdk.open_api.models.order_status import OrderStatus
from sdk.open_api.models.perp_execution import PerpExecution
from sdk.open_api.models.spot_execution import SpotExecution
from tests.helpers import ReyaTester
from tests.helpers.builders import OrderBuilder
from tests.helpers.liquidity_detector import skip_if_order_would_cross
from tests.helpers.market_config import MarketTestConfig, SpotTestConfig


async def rest_spot_gtc(
    tester: ReyaTester,
    spot_config: SpotTestConfig,
    price_multiplier: float = 0.96,
    qty: str | None = None,
    is_buy: bool = True,
    client_order_id: int | None = None,
) -> Order:
    """Place a GTC away from the touch, wait for creation, return the fetched Order."""
    builder = (
        OrderBuilder.from_config(spot_config)
        .side(is_buy)
        .price(str(spot_config.price(price_multiplier)))
        .qty(qty if qty is not None else spot_config.min_qty)
        .gtc()
    )
    if client_order_id is not None:
        builder = builder.client_order_id(client_order_id)
    order_id = await tester.orders.create_limit(builder.build())
    assert order_id is not None, "GTC creation must return an order_id"
    await tester.wait.for_order_creation(order_id)
    # for_order_creation may return on the WS-only path before the REST
    # openOrders cache (OrdersProvider) reflects the order — poll, never
    # single-shot.
    return await wait_for_order_fields(tester, order_id)


async def rest_perp_gtc(
    tester: ReyaTester,
    symbol: str,
    price: str,
    qty: str,
    is_buy: bool = True,
    client_order_id: int | None = None,
) -> Order:
    """Perp twin of `rest_spot_gtc`: place a GTC at the given price, wait for
    creation, return the fetched Order. Callers pick a price safely away from
    the touch (e.g. 0.50x oracle for a buy)."""
    builder = OrderBuilder().symbol(symbol).side(is_buy).price(price).qty(qty).gtc()
    if client_order_id is not None:
        builder = builder.client_order_id(client_order_id)
    order_id = await tester.orders.create_limit(builder.build())
    assert order_id is not None, "GTC creation must return an order_id"
    await tester.wait.for_order_creation(order_id)
    return await wait_for_order_fields(tester, order_id)


async def rest_gtc(
    tester: ReyaTester,
    market_config: MarketTestConfig,
    *,
    price_multiplier: float,
    is_buy: bool = True,
    qty: str | None = None,
    post_only: bool = False,
    client_order_id: int | None = None,
) -> Order:
    """Market-agnostic GTC rest for [spot, perp]-parametrized tests: place at
    ``market_config.price(multiplier)`` on the config's symbol, wait for
    creation, return the fetched Order. Unifies the `rest_spot_gtc` /
    `rest_perp_gtc` split."""
    # A maker order can only be MODIFIED, EXPIRED or CANCELLED if it actually
    # RESTS. When someone else's liquidity (typically a market-making bot on
    # the same env) sits inside the band, this order crosses it and fills
    # instantly instead of resting -- and the caller then dies in
    # `for_order_creation` with the opaque "Order X not created after 10
    # seconds", which points at order creation rather than at the book.
    #
    # Guarded HERE rather than per-test: every caller needs the order to rest,
    # so the precondition belongs with the helper. The perp suites already
    # skip on this via the same detector; the modify and GTT batteries reach
    # the book through this helper and failed instead of skipping.
    px_gtc = str(market_config.price(price_multiplier))
    await skip_if_order_would_cross(
        market_config,
        tester,
        price=px_gtc,
        is_buy=is_buy,
        reason="rest_gtc needs the maker order to REST.",
    )
    builder = (
        OrderBuilder()
        .symbol(market_config.symbol)
        .side(is_buy)
        .price(px_gtc)
        .qty(qty if qty is not None else market_config.min_qty)
        .gtc()
    )
    if post_only:
        builder = builder.post_only()
    if client_order_id is not None:
        builder = builder.client_order_id(client_order_id)
    order_id = await tester.orders.create_limit(builder.build())
    assert order_id is not None, "GTC creation must return an order_id"
    await tester.wait.for_order_creation(order_id)
    return await wait_for_order_fields(tester, order_id)


async def rest_gtt(
    tester: ReyaTester,
    market_config: MarketTestConfig,
    *,
    expires_after: int,
    price_multiplier: float = 1.0,
    price: str | None = None,
    is_buy: bool = True,
    qty: str | None = None,
    post_only: bool = False,
    client_order_id: int | None = None,
) -> Order:
    """Market-agnostic GTT rest for [spot, perp]-parametrized tests: place at an
    explicit ``price`` (or ``market_config.price(multiplier)`` when omitted) with
    a non-zero ``expires_after`` (strictly after the entry deadline), wait for
    creation, return the fetched Order. GTT rests like GTC but the matching
    engine auto-reaps it at ``expires_after`` — pass a comfortably-future value
    (default client deadline is now+60s) for tests that must not expire mid-run.
    Pass an absolute ``price`` (e.g. the re-fetched current mark) for crossing
    tests where the stale session-config price would drift off the perp band."""
    # A maker order can only be MODIFIED, EXPIRED or CANCELLED if it actually
    # RESTS. When someone else's liquidity (typically a market-making bot on
    # the same env) sits inside the band, this order crosses it and fills
    # instantly instead of resting -- and the caller then dies in
    # `for_order_creation` with the opaque "Order X not created after 10
    # seconds", which points at order creation rather than at the book.
    #
    # Guarded HERE rather than per-test: every caller needs the order to rest,
    # so the precondition belongs with the helper. The perp suites already
    # skip on this via the same detector; the modify and GTT batteries reach
    # the book through this helper and failed instead of skipping.
    px = price if price is not None else str(market_config.price(price_multiplier))
    await skip_if_order_would_cross(
        market_config,
        tester,
        price=px,
        is_buy=is_buy,
        reason="rest_gtt needs the maker order to REST.",
    )
    builder = (
        OrderBuilder()
        .symbol(market_config.symbol)
        .side(is_buy)
        .price(px)
        .qty(qty if qty is not None else market_config.min_qty)
        .gtt(expires_after)
    )
    if post_only:
        builder = builder.post_only()
    if client_order_id is not None:
        builder = builder.client_order_id(client_order_id)
    order_id = await tester.orders.create_limit(builder.build())
    assert order_id is not None, "GTT creation must return an order_id"
    await tester.wait.for_order_creation(order_id)
    return await wait_for_order_fields(tester, order_id)


async def wait_for_taker_execution(
    tester: ReyaTester, market_type: str, taker_order_id: str, timeout_s: float = 10.0
) -> SpotExecution | PerpExecution:
    """Dispatch to the spot/perp taker-execution poller by market type. Both
    execution models expose `maker_order_id` / `qty` / `price`, so the caller's
    assertions are market-agnostic."""
    if market_type == "spot":
        return await wait_for_taker_spot_execution(tester, taker_order_id, timeout_s)
    return await wait_for_taker_perp_execution(tester, taker_order_id, timeout_s)


async def wait_for_taker_spot_execution(
    tester: ReyaTester, taker_order_id: str, timeout_s: float = 10.0
) -> SpotExecution:
    """Poll the tester's wallet spot executions for the taker order's fill.

    Returns the FIRST (most recent endpoint ordering aside, matched by
    taker_order_id) execution belonging to `taker_order_id` so callers can
    assert on `maker_order_id` — the queue-priority signal.
    """
    assert tester.owner_wallet_address is not None
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        executions = await tester.client.wallet.get_wallet_spot_executions(address=tester.owner_wallet_address)
        matched = [e for e in (executions.data or []) if str(e.taker_order_id) == str(taker_order_id)]
        if matched:
            return matched[-1]  # endpoint returns newest-first; [-1] is the FIRST fill of this order
        await asyncio.sleep(0.2)
    raise AssertionError(f"No spot execution for taker order {taker_order_id} within {timeout_s}s")


async def wait_for_taker_perp_execution(
    tester: ReyaTester, taker_order_id: str, timeout_s: float = 10.0
) -> PerpExecution:
    """Perp twin of `wait_for_taker_spot_execution`: poll the wallet perp
    executions for the taker order's fill (matched on takerOrderId) so callers
    can assert on `maker_order_id`."""
    assert tester.owner_wallet_address is not None
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        executions = await tester.client.wallet.get_wallet_perp_executions(address=tester.owner_wallet_address)
        matched = [e for e in (executions.data or []) if str(e.taker_order_id) == str(taker_order_id)]
        if matched:
            return matched[-1]  # endpoint returns newest-first; [-1] is the FIRST fill of this order
        await asyncio.sleep(0.2)
    raise AssertionError(f"No perp execution for taker order {taker_order_id} within {timeout_s}s")


def assert_px_qty(order: Order, expected_px: str, expected_qty: str) -> None:
    """Numeric (Decimal) comparison — the API may normalize trailing zeros."""
    assert order.limit_px is not None and order.qty is not None
    assert Decimal(order.limit_px) == Decimal(expected_px), f"limitPx {order.limit_px} != expected {expected_px}"
    assert Decimal(order.qty) == Decimal(expected_qty), f"qty {order.qty} != expected {expected_qty}"


def double_qty(spot_config: SpotTestConfig) -> str:
    return str(Decimal(spot_config.min_qty) * 2)


def _order_fields_match(
    order: Order,
    limit_px: str | None,
    qty: str | None,
    post_only: bool | None,
    expires_after: int | None,
    cum_qty: str | None,
) -> bool:
    if limit_px is not None and (order.limit_px is None or Decimal(order.limit_px) != Decimal(limit_px)):
        return False
    if qty is not None and (order.qty is None or Decimal(order.qty) != Decimal(qty)):
        return False
    if post_only is not None and bool(order.post_only) != post_only:
        return False
    if expires_after is not None and int(order.expires_after or 0) != expires_after:
        return False
    if cum_qty is not None and (order.cum_qty is None or Decimal(order.cum_qty) != Decimal(cum_qty)):
        return False
    return True


async def wait_for_order_fields(
    tester: ReyaTester,
    order_id: str,
    *,
    limit_px: str | None = None,
    qty: str | None = None,
    post_only: bool | None = None,
    expires_after: int | None = None,
    cum_qty: str | None = None,
    timeout_s: float = 10.0,
) -> Order:
    """Poll openOrders until `order_id` reflects the expected state, then
    return the fetched Order.

    The openOrders view is served from the OrdersProvider cache, which
    consumes the matching engine's Redis stream asynchronously w.r.t. the
    modify/create response — a single-shot read right after the response can
    briefly see the pre-modify state. Only the provided expectations are
    checked (Decimal compare for px/qty/cumQty)."""
    deadline = time.time() + timeout_s
    last: Order | None = None
    while time.time() < deadline:
        order = await tester.data.open_order(order_id)
        if order is not None:
            last = order
            if _order_fields_match(order, limit_px, qty, post_only, expires_after, cum_qty):
                return order
        await asyncio.sleep(0.2)
    raise AssertionError(
        f"Order {order_id} did not reach the expected state "
        f"(px={limit_px} qty={qty} postOnly={post_only} expiresAfter={expires_after} cumQty={cum_qty}) "
        f"within {timeout_s}s; last seen: "
        + (
            f"px={last.limit_px} qty={last.qty} postOnly={last.post_only} "
            f"expiresAfter={last.expires_after} cumQty={last.cum_qty}"
            if last is not None
            else "order not in openOrders"
        )
    )


async def wait_for_ws_order_change(
    tester: ReyaTester,
    order_id: str,
    *,
    limit_px: str,
    qty: str,
    timeout_s: float = 10.0,
) -> AsyncOrder:
    """Poll the wallet orderChanges WS store until the modify's order change
    arrives — SAME orderId carrying the NEW px/qty."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        ws_order = tester.ws.orders.get(str(order_id))
        if (
            ws_order is not None
            and Decimal(ws_order.limit_px) == Decimal(limit_px)
            and ws_order.qty is not None
            and Decimal(ws_order.qty) == Decimal(qty)
        ):
            assert ws_order.order_id == str(order_id), f"WS orderChange id mismatch: {ws_order.order_id}"
            return ws_order
        await asyncio.sleep(0.1)
    last_seen = tester.ws.orders.get(str(order_id))
    raise AssertionError(
        f"No WS orderChange with px={limit_px} qty={qty} for order {order_id} within {timeout_s}s; "
        f"last seen: {last_seen}"
    )


async def assert_resting_or_explain(
    tester,
    order_id,
    *,
    label: str,
    expires_after: int,
) -> None:
    """Assert an order is still resting, and when it is NOT, say WHY.

    The naive form of this check -- ``assert order is not None`` -- reads any
    disappearance as the reaper firing early, because that is the bug the
    caller is hunting. On a shared environment that is usually wrong: the
    order is far more often cancelled by somebody else's mass-cancel, a COD
    deadline, or the risk engine. The cancel reason distinguishes them
    unambiguously and is already on the WS order, so an assertion that omits
    it turns a one-line answer into an investigation.

    GTT_EXPIRED before ``expires_after`` is the real defect and FAILS loudly.
    Every other reason means the environment removed the precondition rather
    than the engine misbehaving, so the test SKIPS naming the reason -- same
    philosophy as the liquidity guards above.
    """
    order = await tester.data.open_order(order_id)
    if order is not None and order.status == OrderStatus.OPEN:
        return

    ws_order = tester.ws.orders.get(str(order_id))
    reason = getattr(ws_order, "cancel_reason", None)
    now = int(time.time())
    early_by = expires_after - now

    if reason is None:
        pytest.fail(
            f"{label} order {order_id} vanished {early_by}s before its expiry "
            f"({now} < {expires_after}) and no cancel reason reached the WS "
            f"client -- cannot tell an early reap from an external cancel."
        )

    if str(getattr(reason, "value", reason)).upper().endswith("GTT_EXPIRED"):
        pytest.fail(
            f"{label} GTT was reaped EARLY: cancelled GTT_EXPIRED at {now}, "
            f"{early_by}s before its expiresAfter ({expires_after}). This is "
            f"the matching engine reaping ahead of the deadline."
        )

    pytest.skip(
        f"{label} order {order_id} was cancelled by "
        f"{getattr(reason, 'value', reason)} {early_by}s before "
        f"its expiry -- something outside this test removed it (shared "
        f"environment), so the reap behaviour cannot be observed."
    )
