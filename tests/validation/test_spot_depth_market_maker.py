"""Offline regression tests for the spot depth market maker."""

from __future__ import annotations

from decimal import Decimal
from importlib import import_module
from typing import Any

import pytest

from sdk.reya_rest_api.models.orders import LimitOrderParameters

pytestmark = pytest.mark.offline

_depth_market_maker = import_module("examples.websocket.spot.depth_market_maker")
MarketMakerState = getattr(_depth_market_maker, "MarketMakerState")
MarketParams = getattr(_depth_market_maker, "MarketParams")
OpenOrder = getattr(_depth_market_maker, "OpenOrder")
cancel_and_replace_order = getattr(_depth_market_maker, "cancel_and_replace_order")


class _RecordingClient:
    def __init__(
        self,
        *,
        create_error: Exception | None = None,
        cancel_error: Exception | None = None,
    ) -> None:
        self.create_error = create_error
        self.cancel_error = cancel_error
        self.events: list[str] = []
        self.created_order: LimitOrderParameters | None = None

    async def create_limit_order(self, order: LimitOrderParameters) -> None:
        self.events.append("place")
        self.created_order = order
        if self.create_error:
            raise self.create_error

    async def cancel_order(self, *, order_id: str, symbol: str, account_id: int) -> None:
        del order_id, symbol, account_id
        self.events.append("cancel")
        if self.cancel_error:
            raise self.cancel_error


def _market_params() -> Any:
    return MarketParams(
        symbol="WETHRUSD",
        base_asset="ETH",
        quote_asset="RUSD",
        tick_size=Decimal("0.01"),
        min_order_qty=Decimal("0.001"),
        qty_step_size=Decimal("0.001"),
    )


def _order() -> Any:
    return OpenOrder(
        order_id="existing-order",
        price=Decimal("100"),
        qty=Decimal("0.005"),
        is_buy=True,
    )


def _state(order: Any) -> Any:
    state = MarketMakerState()
    state.open_orders[order.order_id] = order
    return state


async def _replace(client: _RecordingClient) -> tuple[bool, Any]:
    order = _order()
    state = _state(order)
    result = await cancel_and_replace_order(
        client=client,
        symbol="WETHRUSD",
        account_id=10000000002,
        order=order,
        reference_price=Decimal("100"),
        market_params=_market_params(),
        available_base=Decimal("1"),
        available_quote=Decimal("1000"),
        remaining_bids=[],
        remaining_asks=[],
        cycle=1,
        state=state,
    )
    return result, state


@pytest.mark.asyncio
async def test_replacement_is_placed_before_existing_order_is_cancelled() -> None:
    client = _RecordingClient()

    result, _state_after = await _replace(client)

    assert result is True
    assert client.events == ["place", "cancel"]


@pytest.mark.asyncio
async def test_failed_replacement_preserves_existing_order() -> None:
    client = _RecordingClient(create_error=OSError("placement unavailable"))

    result, state = await _replace(client)

    assert result is False
    assert client.events == ["place"]
    assert "existing-order" in state.open_orders


@pytest.mark.asyncio
async def test_cancel_failure_keeps_successful_replacement_live() -> None:
    client = _RecordingClient(cancel_error=OSError("cancellation unavailable"))

    result, state = await _replace(client)

    assert result is True
    assert client.events == ["place", "cancel"]
    assert client.created_order is not None
    assert "existing-order" in state.open_orders
