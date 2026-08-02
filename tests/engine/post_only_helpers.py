"""Shared helpers for the post-only e2e suite."""

from __future__ import annotations

import asyncio
import time

from sdk.async_api.order import Order as AsyncOrder
from sdk.open_api.models.order import Order
from tests.helpers import ReyaTester
from tests.helpers.builders import OrderBuilder
from tests.helpers.order_lifecycle import wait_for_order_fields

PERP_SYMBOL = "ETHRUSDPERP"


async def rest_gtc_at_price(
    tester: ReyaTester,
    symbol: str,
    price: str,
    qty: str,
    is_buy: bool = True,
    post_only: bool = False,
) -> Order:
    """Place a GTC at an explicit price, wait for creation, return the fetched Order.

    Symbol/price/qty are explicit (rather than config-derived as in
    tests/helpers/order_lifecycle.rest_spot_gtc) so the same helper rests
    both spot and perp makers.
    """
    builder = OrderBuilder().symbol(symbol).side(is_buy).price(price).qty(qty).gtc()
    if post_only:
        builder = builder.post_only()
    order_id = await tester.orders.create_limit(builder.build())
    assert order_id is not None, "GTC creation must return an order_id"
    await tester.wait.for_order_creation(order_id)
    # for_order_creation may return on the WS-only path before the REST
    # openOrders cache (OrdersProvider) reflects the order — poll, never
    # single-shot.
    return await wait_for_order_fields(tester, order_id)


async def wait_for_ws_order_event(tester: ReyaTester, order_id: str, timeout_s: float = 10.0) -> AsyncOrder:
    """Bounded poll of the tester's WS order-changes store for `order_id`.

    `wait.for_order_creation` may return on the REST-only path before the WS
    event lands, so tests that assert on WS-event FIELDS (e.g. postOnly) wait
    here first.
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        ws_order = tester.ws.orders.get(str(order_id))
        if ws_order is not None:
            return ws_order
        await asyncio.sleep(0.1)
    raise AssertionError(f"No WS order event for {order_id} within {timeout_s}s")


async def open_order_ids(tester: ReyaTester) -> set[str]:
    """Snapshot of the account's open order ids — for asserting that a
    rejected post-only probe left openOrders unchanged."""
    return {str(order.order_id) for order in await tester.client.get_open_orders()}


async def perp_min_qty_and_oracle(tester: ReyaTester, symbol: str = PERP_SYMBOL) -> tuple[str, float]:
    """Market min_order_qty and current oracle price (mirrors how the modify
    suite resolves perp market metadata inline)."""
    market_def = await tester.get_market_definition(symbol)
    oracle_price = float(await tester.data.current_price(symbol))
    return str(market_def.min_order_qty), oracle_price
