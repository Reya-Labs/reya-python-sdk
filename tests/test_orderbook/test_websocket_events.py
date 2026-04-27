"""
WebSocket event verification tests parametrized over [spot, perp].

The matching engine emits the same ``orderChanges`` events for both market
types under v2.3.0 — these tests verify create/cancel events fire and carry
the expected payload. Balance-update verification stays in
``tests/test_spot/`` because perp markets don't change asset balances on
match (positions accrue instead).
"""

from __future__ import annotations

import asyncio

import pytest

from sdk.open_api.models.order_status import OrderStatus
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api.models import LimitOrderParameters
from tests.helpers import ReyaTester
from tests.test_orderbook.conftest import PerpTestConfig
from tests.test_spot.spot_config import SpotTestConfig


@pytest.mark.asyncio
async def test_ws_order_change_on_create(
    market_config: SpotTestConfig | PerpTestConfig,
    market_type: str,
    maker: ReyaTester,
) -> None:
    """A new GTC order surfaces on the wallet ``orderChanges`` WS channel."""
    _ = market_type  # consumed by the [spot, perp] parametrization
    maker.ws.order_changes.clear()

    far_buy_px = str(round(market_config.oracle_price * 0.5, 2))
    params = LimitOrderParameters(
        symbol=market_config.symbol,
        is_buy=True,
        limit_px=far_buy_px,
        qty=market_config.min_qty,
        time_in_force=TimeInForce.GTC,
    )
    order_id = await maker.orders.create_limit(params)
    assert order_id is not None
    await maker.wait.for_order_creation(order_id)

    maker.check.ws_order_change_received(
        order_id=order_id,
        expected_symbol=market_config.symbol,
        expected_side="B",
        expected_qty=market_config.min_qty,
    )

    # Cleanup
    await maker.client.cancel_order(order_id=order_id, symbol=market_config.symbol, account_id=maker.account_id)
    await maker.wait.for_order_state(order_id, OrderStatus.CANCELLED)


@pytest.mark.asyncio
async def test_ws_order_change_on_cancel(
    market_config: SpotTestConfig | PerpTestConfig,
    market_type: str,
    maker: ReyaTester,
) -> None:
    """Cancelling an open order surfaces a CANCELLED orderChanges event on the WS channel."""
    far_buy_px = str(round(market_config.oracle_price * 0.5, 2))
    params = LimitOrderParameters(
        symbol=market_config.symbol,
        is_buy=True,
        limit_px=far_buy_px,
        qty=market_config.min_qty,
        time_in_force=TimeInForce.GTC,
    )
    order_id = await maker.orders.create_limit(params)
    assert order_id is not None
    await maker.wait.for_order_creation(order_id)

    await maker.client.cancel_order(order_id=order_id, symbol=market_config.symbol, account_id=maker.account_id)

    # ``for_order_state`` waits for WS confirmation as the primary signal.
    await maker.wait.for_order_state(order_id, OrderStatus.CANCELLED)

    ws_order = maker.ws.orders.get(str(order_id))
    assert ws_order is not None, f"[{market_type}] expected WS order entry for {order_id}"
    status_value = ws_order.status.value if hasattr(ws_order.status, "value") else ws_order.status
    assert status_value == "CANCELLED", f"[{market_type}] expected CANCELLED, got {status_value}"


@pytest.mark.asyncio
async def test_ws_depth_subscription_alive(
    market_config: SpotTestConfig | PerpTestConfig,
    market_type: str,
    maker: ReyaTester,
) -> None:
    """Subscribing to ``/depth`` and resting an order surfaces depth updates within a few seconds."""
    if maker.websocket is None:
        pytest.skip("WebSocket not connected")

    maker.ws.subscribe_to_market_depth(market_config.symbol)

    far_buy_px = str(round(market_config.oracle_price * 0.5, 2))
    params = LimitOrderParameters(
        symbol=market_config.symbol,
        is_buy=True,
        limit_px=far_buy_px,
        qty=market_config.min_qty,
        time_in_force=TimeInForce.GTC,
    )
    order_id = await maker.orders.create_limit(params)
    assert order_id is not None

    # Poll briefly for a depth event capturing our resting order.
    for _ in range(20):
        depth = maker.ws.depth.get(market_config.symbol)
        if depth is not None and depth.bids:
            break
        await asyncio.sleep(0.25)

    depth = maker.ws.depth.get(market_config.symbol)
    assert depth is not None, f"[{market_type}] expected depth update for {market_config.symbol}"

    # Cleanup
    await maker.client.cancel_order(order_id=order_id, symbol=market_config.symbol, account_id=maker.account_id)
    await maker.wait.for_order_state(order_id, OrderStatus.CANCELLED)
