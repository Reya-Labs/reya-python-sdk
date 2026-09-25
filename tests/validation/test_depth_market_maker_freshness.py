"""Offline freshness and REST-recovery tests for the depth market makers."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from decimal import Decimal
from importlib import import_module

import pytest

pytestmark = pytest.mark.offline

PERP_MM = import_module("examples.websocket.perps.depth_market_maker")
SPOT_MM = import_module("examples.websocket.spot.depth_market_maker")


class _MassCancelClient:
    def __init__(self) -> None:
        self.mass_cancels: list[tuple[str, int]] = []

    async def mass_cancel(self, *, symbol: str, account_id: int) -> None:
        self.mass_cancels.append((symbol, account_id))


def _perp_state() -> Any:
    state = PERP_MM.MarketMakerState(symbol="BTCRUSDPERP", oracle_symbol="BTCRUSDPERP")
    state.account_id = 10
    state.market_params = PERP_MM.MarketParams(
        symbol="BTCRUSDPERP",
        tick_size=Decimal("0.001"),
        min_order_qty=Decimal("0.0001"),
        qty_step_size=Decimal("0.0001"),
        max_leverage=20,
    )
    return state


def _spot_state() -> Any:
    state = SPOT_MM.MarketMakerState(symbol="WBTCRUSD", oracle_symbol="WBTCRUSD")
    state.account_id = 10000000007
    state.market_params = SPOT_MM.MarketParams(
        symbol="WBTCRUSD",
        base_asset="WBTC",
        quote_asset="RUSD",
        tick_size=Decimal("0.01"),
        min_order_qty=Decimal("0.0001"),
        qty_step_size=Decimal("0.0001"),
    )
    return state


@pytest.mark.parametrize(
    ("module", "state_factory"),
    ((PERP_MM, _perp_state), (SPOT_MM, _spot_state)),
)
@pytest.mark.asyncio
async def test_stale_price_cancels_quotes_once(
    monkeypatch: pytest.MonkeyPatch,
    module: Any,
    state_factory: Any,
) -> None:
    state = state_factory()
    state.update_price(Decimal("100"), observed_at_monotonic=10.0)
    state.open_orders["stale-order"] = module.OpenOrder(
        order_id="stale-order",
        price=Decimal("99"),
        qty=Decimal("0.001"),
        is_buy=True,
    )
    monkeypatch.setattr(module.time, "monotonic", lambda: 10.0 + module.MAX_PRICE_AGE_S + 1.0)
    client = _MassCancelClient()

    await module.adjust_orders(client, state, cycle=1)
    await module.adjust_orders(client, state, cycle=2)

    assert client.mass_cancels == [(state.symbol, state.account_id)]
    assert state.open_orders == {}


class _PerpRestClient:
    async def get_market_mark_price(self, symbol: str) -> str:
        assert symbol == "BTCRUSDPERP"
        return "123.4567"

    async def get_account_balances(self) -> list[Any]:
        return [SimpleNamespace(account_id=10, asset="RUSD", real_balance="750")]

    async def get_open_orders(self) -> list[Any]:
        return [
            SimpleNamespace(
                symbol="BTCRUSDPERP",
                order_id="perp-order",
                qty="0.004",
                cum_qty="0.001",
                side=SimpleNamespace(value="B"),
                limit_px="123.000",
            )
        ]


@pytest.mark.asyncio
async def test_perp_rest_refresh_recovers_price_balance_and_orders(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _perp_state()
    monkeypatch.setattr(PERP_MM.time, "monotonic", lambda: 50.0)

    refreshed = await PERP_MM.refresh_state_from_rest(_PerpRestClient(), state)

    assert refreshed is True
    assert state.reference_price == Decimal("123.456")
    assert state.price_age_seconds(now_monotonic=55.0) == 5.0
    assert state.collateral_balance == Decimal("750")
    assert state.open_orders["perp-order"].qty == Decimal("0.003")


class _SpotMarkets:
    async def get_spot_market_summary(self, symbol: str) -> Any:
        assert symbol == "WBTCRUSD"
        return SimpleNamespace(oracle_price="234.567")


class _SpotRestClient:
    markets = _SpotMarkets()

    async def get_account_balances(self) -> list[Any]:
        return [
            SimpleNamespace(account_id=10000000007, asset="WBTC", real_balance="2"),
            SimpleNamespace(account_id=10000000007, asset="RUSD", real_balance="1000"),
        ]

    async def get_open_orders(self) -> list[Any]:
        return [
            SimpleNamespace(
                symbol="WBTCRUSD",
                order_id="spot-order",
                qty="0.005",
                cum_qty="0.002",
                side=SimpleNamespace(value="A"),
                limit_px="235.00",
            )
        ]


@pytest.mark.asyncio
async def test_spot_rest_refresh_recovers_price_balances_and_orders(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _spot_state()
    monkeypatch.setattr(SPOT_MM.time, "monotonic", lambda: 75.0)

    refreshed = await SPOT_MM.refresh_state_from_rest(_SpotRestClient(), state)

    assert refreshed is True
    assert state.reference_price == Decimal("234.56")
    assert state.price_age_seconds(now_monotonic=80.0) == 5.0
    assert state.base_balance == Decimal("2")
    assert state.quote_balance == Decimal("1000")
    assert state.open_orders["spot-order"].qty == Decimal("0.003")


class _PerpFailedOrdersClient(_PerpRestClient):
    async def get_open_orders(self) -> list[Any]:
        raise RuntimeError("orders unavailable")


class _SpotFailedOrdersClient(_SpotRestClient):
    async def get_open_orders(self) -> list[Any]:
        raise RuntimeError("orders unavailable")


@pytest.mark.parametrize(
    ("module", "state_factory", "client"),
    (
        (PERP_MM, _perp_state, _PerpFailedOrdersClient()),
        (SPOT_MM, _spot_state, _SpotFailedOrdersClient()),
    ),
)
@pytest.mark.asyncio
async def test_failed_rest_snapshot_does_not_mark_price_fresh(
    monkeypatch: pytest.MonkeyPatch,
    module: Any,
    state_factory: Any,
    client: Any,
) -> None:
    state = state_factory()
    state.update_price(Decimal("100"), observed_at_monotonic=10.0)
    monkeypatch.setattr(module.time, "monotonic", lambda: 50.0)

    refreshed = await module.refresh_state_from_rest(client, state)

    assert refreshed is False
    assert state.reference_price == Decimal("100")
    assert state.price_age_seconds(now_monotonic=50.0) == 40.0
