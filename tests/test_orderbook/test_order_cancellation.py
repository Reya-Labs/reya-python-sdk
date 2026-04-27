"""
Order cancellation tests parametrized over [spot, perp].

Cancellation behaviour is identical for both market types under v2.3.0 — the
matching engine resolves the order to its book, applies the EIP-712-signed
``OrderCancel`` envelope, and acks. These tests exercise:

- Single-order cancel by ``order_id``.
- Mass cancel of all open orders on a symbol.
- Re-cancel of an already-cancelled order (idempotent / explicit error).
"""

from __future__ import annotations

import asyncio

import pytest

from sdk.open_api.exceptions import ApiException
from sdk.open_api.models.order_status import OrderStatus
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api.models import LimitOrderParameters
from tests.helpers import ReyaTester
from tests.test_orderbook.conftest import PerpTestConfig
from tests.test_spot.spot_config import SpotTestConfig


def _safe_resting_price(market_config: SpotTestConfig | PerpTestConfig) -> str:
    """Price far below oracle so a buy GTC will rest without crossing."""
    return str(round(market_config.oracle_price * 0.5, 2))


@pytest.mark.asyncio
async def test_cancel_single_open_order(
    market_config: SpotTestConfig | PerpTestConfig,
    market_type: str,
    maker: ReyaTester,
) -> None:
    """Place a far-from-market GTC, cancel by ``order_id``, observe CANCELLED state."""
    params = LimitOrderParameters(
        symbol=market_config.symbol,
        is_buy=True,
        limit_px=_safe_resting_price(market_config),
        qty=market_config.min_qty,
        time_in_force=TimeInForce.GTC,
    )
    order_id = await maker.orders.create_limit(params)
    assert order_id is not None, f"[{market_type}] expected order_id"

    await maker.wait.for_order_creation(order_id)

    await maker.client.cancel_order(
        symbol=market_config.symbol,
        account_id=maker.account_id,
        order_id=order_id,
    )

    await maker.wait.for_order_state(order_id, OrderStatus.CANCELLED)
    await maker.check.no_open_orders()


@pytest.mark.asyncio
async def test_mass_cancel_clears_multiple_orders(
    market_config: SpotTestConfig | PerpTestConfig,
    market_type: str,
    maker: ReyaTester,
) -> None:
    """Place several orders at distinct prices, mass-cancel, observe all CANCELLED."""
    placed: list[str] = []
    for offset in range(3):
        # Spread within the circuit-breaker band but stay far enough not to fill.
        px = str(round(market_config.oracle_price * (0.5 - 0.001 * offset), 2))
        params = LimitOrderParameters(
            symbol=market_config.symbol,
            is_buy=True,
            limit_px=px,
            qty=market_config.min_qty,
            time_in_force=TimeInForce.GTC,
        )
        order_id = await maker.orders.create_limit(params)
        assert order_id is not None, f"[{market_type}] expected order_id at offset={offset}"
        placed.append(order_id)

    await maker.client.mass_cancel(
        symbol=market_config.symbol,
        account_id=maker.account_id,
    )

    # Allow the matching engine a moment to apply the mass-cancel before polling.
    await asyncio.sleep(0.5)
    for order_id in placed:
        await maker.wait.for_order_state(order_id, OrderStatus.CANCELLED)
    await maker.check.no_open_orders()


@pytest.mark.asyncio
async def test_cancel_unknown_order_id_rejects(
    market_config: SpotTestConfig | PerpTestConfig,
    market_type: str,
    maker: ReyaTester,
) -> None:
    """Cancelling an order_id that was never placed should raise — protects against typos / replay."""
    bogus_order_id = "9999999999999999999"

    with pytest.raises(ApiException) as exc_info:
        await maker.client.cancel_order(
            symbol=market_config.symbol,
            account_id=maker.account_id,
            order_id=bogus_order_id,
        )

    err = str(exc_info.value).lower()
    assert (
        "missing" in err or "not found" in err or "400" in err or "404" in err
    ), f"[{market_type}] expected unknown-order rejection, got: {exc_info.value}"


@pytest.mark.asyncio
async def test_cancel_already_cancelled_rejects(
    market_config: SpotTestConfig | PerpTestConfig,
    market_type: str,
    maker: ReyaTester,
) -> None:
    """A second cancel for the same order_id should raise — the order is gone from the book."""
    params = LimitOrderParameters(
        symbol=market_config.symbol,
        is_buy=True,
        limit_px=_safe_resting_price(market_config),
        qty=market_config.min_qty,
        time_in_force=TimeInForce.GTC,
    )
    order_id = await maker.orders.create_limit(params)
    assert order_id is not None
    await maker.wait.for_order_creation(order_id)

    # First cancel — succeeds.
    await maker.client.cancel_order(
        symbol=market_config.symbol,
        account_id=maker.account_id,
        order_id=order_id,
    )
    await maker.wait.for_order_state(order_id, OrderStatus.CANCELLED)

    # Second cancel — API rejects: order no longer open.
    with pytest.raises(ApiException) as exc_info:
        await maker.client.cancel_order(
            symbol=market_config.symbol,
            account_id=maker.account_id,
            order_id=order_id,
        )

    err = str(exc_info.value).lower()
    assert (
        "missing" in err or "not found" in err or "cancel" in err or "400" in err or "404" in err
    ), f"[{market_type}] expected explicit cancel rejection, got: {exc_info.value}"
