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
import time

import pytest

from sdk.open_api.exceptions import ApiException
from sdk.open_api.models.order_status import OrderStatus
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api.models import LimitOrderParameters
from tests.helpers import ReyaTester
from tests.helpers.market_config import PerpTestConfig, SpotTestConfig


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
async def test_cancel_by_client_order_id(
    market_config: SpotTestConfig | PerpTestConfig,
    market_type: str,
    maker: ReyaTester,
) -> None:
    """Cancel targeting by clientOrderId ONLY (orderId deliberately omitted).

    The server resolves clientOrderId only when orderId is absent, so sending
    both (as the legacy spot test does) never exercises the resolution path.
    That clientOrderId → order → marketId routing broke twice on perp
    (perpOB-6 Bug 11, PRO-143), hence both markets pin it here.
    """
    client_order_id = int(time.time() * 1000) % (2**31 - 1)
    params = LimitOrderParameters(
        symbol=market_config.symbol,
        is_buy=True,
        limit_px=_safe_resting_price(market_config),
        qty=market_config.min_qty,
        time_in_force=TimeInForce.GTC,
        client_order_id=client_order_id,
    )
    response = await maker.client.create_limit_order(params)
    order_id = response.order_id
    assert order_id is not None, f"[{market_type}] expected order_id"
    # The create response must echo the clientOrderId it was created with
    # (folded from test_spot_gtc_with_client_order_id when that test was
    # retired as a weaker duplicate; the public Order model doesn't expose it).
    if response.client_order_id is not None:
        assert int(response.client_order_id) == client_order_id, f"[{market_type}] clientOrderId echo mismatch"
    await maker.wait.for_order_creation(order_id)

    await maker.client.cancel_order(
        symbol=market_config.symbol,
        account_id=maker.account_id,
        client_order_id=client_order_id,
    )

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


@pytest.mark.asyncio
async def test_cancel_already_filled_rejects(
    market_config: SpotTestConfig | PerpTestConfig,
    market_type: str,
    maker: ReyaTester,
    taker: ReyaTester,
) -> None:
    """Cancelling a FILLED order raises — the order is gone from the book.

    Moved from tests/spot/test_order_cancellation.py (which accepted
    both outcomes) and hardened: the rejection is now asserted, on both
    markets, against a controlled book.
    """
    await market_config.refresh_order_book(taker.data)
    await maker.orders.close_all(fail_if_none=False)
    await taker.orders.close_all(fail_if_none=False)
    if market_config.has_any_external_liquidity:
        pytest.skip("external liquidity present — the fill could route externally")

    cross_px = str(market_config.price(0.99))
    maker_params = LimitOrderParameters(
        symbol=market_config.symbol,
        is_buy=True,
        limit_px=cross_px,
        qty=market_config.min_qty,
        time_in_force=TimeInForce.GTC,
    )
    maker_order_id = await maker.orders.create_limit(maker_params)
    assert maker_order_id is not None
    await maker.wait.for_order_creation(maker_order_id)

    taker_params = LimitOrderParameters(
        symbol=market_config.symbol,
        is_buy=False,
        limit_px=cross_px,
        qty=market_config.min_qty,
        time_in_force=TimeInForce.IOC,
    )
    await taker.orders.create_limit(taker_params)
    await maker.wait.for_order_state(maker_order_id, OrderStatus.FILLED, timeout=10)

    with pytest.raises(ApiException) as exc_info:
        await maker.client.cancel_order(
            symbol=market_config.symbol,
            account_id=maker.account_id,
            order_id=maker_order_id,
        )
    err = str(exc_info.value).lower()
    assert (
        "missing" in err or "not found" in err or "cancel" in err or "400" in err or "404" in err
    ), f"[{market_type}] expected explicit rejection for a FILLED order, got: {exc_info.value}"


@pytest.mark.asyncio
async def test_mass_cancel_with_no_orders_is_noop(
    market_config: SpotTestConfig | PerpTestConfig,
    market_type: str,
    maker: ReyaTester,
) -> None:
    """Mass-cancel with nothing resting succeeds as a no-op (no error).

    Merged from two near-identical spot tests (mass_cancel_empty_book /
    mass_cancel_no_orders) and parametrized.
    """
    await maker.orders.close_all(fail_if_none=False)
    await maker.check.no_open_orders()

    response = await maker.client.mass_cancel(
        symbol=market_config.symbol,
        account_id=maker.account_id,
    )
    assert response is not None, f"[{market_type}] mass-cancel on an empty book must not error"

    await maker.check.no_open_orders()
