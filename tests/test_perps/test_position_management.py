"""
Perp position-management tests using the maker/taker pattern.

Under perp orderbook every fill needs a counterparty, so position-formation
tests can no longer use a single account that hits the AMM pool. These tests
have ``perp_maker_tester`` rest GTC liquidity and ``perp_taker_tester`` cross
against it via IOC, then assert position state on the taker.

Scenarios covered:
- Open a long via taker IOC against maker sell.
- Open a short via taker IOC against maker buy.
- Increase an existing position with a same-side IOC.
- Close a position fully with an opposite-side reduce-only IOC.
"""

from __future__ import annotations

import pytest

from sdk.open_api.models.side import Side
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api.config import REYA_DEX_ID
from sdk.reya_rest_api.models import LimitOrderParameters
from tests.helpers import ReyaTester
from tests.helpers.reya_tester import logger

PERP_SYMBOL = "ETHRUSDPERP"
PERP_QTY = "0.01"


async def _rest_maker_sell(maker: ReyaTester, market_price: float, qty: str = PERP_QTY) -> str:
    """Place a maker sell at 1% below oracle. Returns the maker order_id."""
    price = str(round(market_price * 0.99, 2))
    order_id = await maker.orders.create_limit(
        LimitOrderParameters(
            symbol=PERP_SYMBOL,
            is_buy=False,
            limit_px=price,
            qty=qty,
            time_in_force=TimeInForce.GTC,
        )
    )
    assert order_id is not None
    await maker.wait.for_order_creation(order_id=order_id)
    return order_id


async def _rest_maker_buy(maker: ReyaTester, market_price: float, qty: str = PERP_QTY) -> str:
    """Place a maker buy at 1% above oracle. Returns the maker order_id."""
    price = str(round(market_price * 1.01, 2))
    order_id = await maker.orders.create_limit(
        LimitOrderParameters(
            symbol=PERP_SYMBOL,
            is_buy=True,
            limit_px=price,
            qty=qty,
            time_in_force=TimeInForce.GTC,
        )
    )
    assert order_id is not None
    await maker.wait.for_order_creation(order_id=order_id)
    return order_id


@pytest.mark.asyncio
async def test_position_open_long_via_taker_ioc(perp_maker_tester: ReyaTester, perp_taker_tester: ReyaTester) -> None:
    """Taker IOC buy lifts maker sell — taker accumulates a long."""
    await perp_taker_tester.check.position_not_open(PERP_SYMBOL)
    market_price = float(await perp_taker_tester.data.current_price(PERP_SYMBOL))

    await _rest_maker_sell(perp_maker_tester, market_price)

    await perp_taker_tester.orders.create_limit(
        LimitOrderParameters(
            symbol=PERP_SYMBOL,
            is_buy=True,
            limit_px=str(round(market_price * 1.05, 2)),
            qty=PERP_QTY,
            time_in_force=TimeInForce.IOC,
            reduce_only=False,
        )
    )

    await perp_taker_tester.check.position(
        symbol=PERP_SYMBOL,
        expected_exchange_id=REYA_DEX_ID,
        expected_account_id=perp_taker_tester.account_id,
        expected_qty=PERP_QTY,
        expected_side=Side.B,
    )
    logger.info("✅ taker holds a long after lifting maker sell")


@pytest.mark.asyncio
async def test_position_open_short_via_taker_ioc(perp_maker_tester: ReyaTester, perp_taker_tester: ReyaTester) -> None:
    """Taker IOC sell hits maker buy — taker accumulates a short."""
    await perp_taker_tester.check.position_not_open(PERP_SYMBOL)
    market_price = float(await perp_taker_tester.data.current_price(PERP_SYMBOL))

    await _rest_maker_buy(perp_maker_tester, market_price)

    await perp_taker_tester.orders.create_limit(
        LimitOrderParameters(
            symbol=PERP_SYMBOL,
            is_buy=False,
            limit_px=str(round(market_price * 0.95, 2)),
            qty=PERP_QTY,
            time_in_force=TimeInForce.IOC,
            reduce_only=False,
        )
    )

    await perp_taker_tester.check.position(
        symbol=PERP_SYMBOL,
        expected_exchange_id=REYA_DEX_ID,
        expected_account_id=perp_taker_tester.account_id,
        expected_qty=PERP_QTY,
        expected_side=Side.A,
    )


@pytest.mark.asyncio
async def test_position_increase_long(perp_maker_tester: ReyaTester, perp_taker_tester: ReyaTester) -> None:
    """Two same-side taker IOCs against fresh maker liquidity stack into a 2x position."""
    await perp_taker_tester.check.position_not_open(PERP_SYMBOL)
    market_price = float(await perp_taker_tester.data.current_price(PERP_SYMBOL))

    # First leg
    await _rest_maker_sell(perp_maker_tester, market_price)
    await perp_taker_tester.orders.create_limit(
        LimitOrderParameters(
            symbol=PERP_SYMBOL,
            is_buy=True,
            limit_px=str(round(market_price * 1.05, 2)),
            qty=PERP_QTY,
            time_in_force=TimeInForce.IOC,
            reduce_only=False,
        )
    )

    # Second leg (more maker liquidity, then more taker IOC)
    await _rest_maker_sell(perp_maker_tester, market_price)
    await perp_taker_tester.orders.create_limit(
        LimitOrderParameters(
            symbol=PERP_SYMBOL,
            is_buy=True,
            limit_px=str(round(market_price * 1.05, 2)),
            qty=PERP_QTY,
            time_in_force=TimeInForce.IOC,
            reduce_only=False,
        )
    )

    expected_total = str(float(PERP_QTY) * 2)
    await perp_taker_tester.check.position(
        symbol=PERP_SYMBOL,
        expected_exchange_id=REYA_DEX_ID,
        expected_account_id=perp_taker_tester.account_id,
        expected_qty=expected_total,
        expected_side=Side.B,
    )


@pytest.mark.asyncio
async def test_position_increase_short(perp_maker_tester: ReyaTester, perp_taker_tester: ReyaTester) -> None:
    """Mirror of test_position_increase_long: two same-side IOC sells against fresh maker buys."""
    await perp_taker_tester.check.position_not_open(PERP_SYMBOL)
    market_price = float(await perp_taker_tester.data.current_price(PERP_SYMBOL))

    await _rest_maker_buy(perp_maker_tester, market_price)
    await perp_taker_tester.orders.create_limit(
        LimitOrderParameters(
            symbol=PERP_SYMBOL,
            is_buy=False,
            limit_px=str(round(market_price * 0.95, 2)),
            qty=PERP_QTY,
            time_in_force=TimeInForce.IOC,
            reduce_only=False,
        )
    )

    await _rest_maker_buy(perp_maker_tester, market_price)
    await perp_taker_tester.orders.create_limit(
        LimitOrderParameters(
            symbol=PERP_SYMBOL,
            is_buy=False,
            limit_px=str(round(market_price * 0.95, 2)),
            qty=PERP_QTY,
            time_in_force=TimeInForce.IOC,
            reduce_only=False,
        )
    )

    expected_total = str(float(PERP_QTY) * 2)
    await perp_taker_tester.check.position(
        symbol=PERP_SYMBOL,
        expected_exchange_id=REYA_DEX_ID,
        expected_account_id=perp_taker_tester.account_id,
        expected_qty=expected_total,
        expected_side=Side.A,
    )


@pytest.mark.asyncio
async def test_position_partial_close_long(perp_maker_tester: ReyaTester, perp_taker_tester: ReyaTester) -> None:
    """Open a 2x long, then close half via reduce-only IOC sell — half the position remains."""
    await perp_taker_tester.check.position_not_open(PERP_SYMBOL)
    market_price = float(await perp_taker_tester.data.current_price(PERP_SYMBOL))
    initial_qty = "0.02"
    close_qty = "0.01"

    # Open 0.02 long
    await _rest_maker_sell(perp_maker_tester, market_price, qty=initial_qty)
    await perp_taker_tester.orders.create_limit(
        LimitOrderParameters(
            symbol=PERP_SYMBOL,
            is_buy=True,
            limit_px=str(round(market_price * 1.05, 2)),
            qty=initial_qty,
            time_in_force=TimeInForce.IOC,
            reduce_only=False,
        )
    )

    # Partial close 0.01
    await _rest_maker_buy(perp_maker_tester, market_price, qty=close_qty)
    await perp_taker_tester.orders.create_limit(
        LimitOrderParameters(
            symbol=PERP_SYMBOL,
            is_buy=False,
            limit_px=str(round(market_price * 0.95, 2)),
            qty=close_qty,
            time_in_force=TimeInForce.IOC,
            reduce_only=True,
        )
    )

    expected_remaining = str(float(initial_qty) - float(close_qty))
    await perp_taker_tester.check.position(
        symbol=PERP_SYMBOL,
        expected_exchange_id=REYA_DEX_ID,
        expected_account_id=perp_taker_tester.account_id,
        expected_qty=expected_remaining,
        expected_side=Side.B,
    )


@pytest.mark.asyncio
async def test_position_partial_close_short(perp_maker_tester: ReyaTester, perp_taker_tester: ReyaTester) -> None:
    """Mirror of partial_close_long: open 2x short, close half via reduce-only IOC buy."""
    await perp_taker_tester.check.position_not_open(PERP_SYMBOL)
    market_price = float(await perp_taker_tester.data.current_price(PERP_SYMBOL))
    initial_qty = "0.02"
    close_qty = "0.01"

    await _rest_maker_buy(perp_maker_tester, market_price, qty=initial_qty)
    await perp_taker_tester.orders.create_limit(
        LimitOrderParameters(
            symbol=PERP_SYMBOL,
            is_buy=False,
            limit_px=str(round(market_price * 0.95, 2)),
            qty=initial_qty,
            time_in_force=TimeInForce.IOC,
            reduce_only=False,
        )
    )

    await _rest_maker_sell(perp_maker_tester, market_price, qty=close_qty)
    await perp_taker_tester.orders.create_limit(
        LimitOrderParameters(
            symbol=PERP_SYMBOL,
            is_buy=True,
            limit_px=str(round(market_price * 1.05, 2)),
            qty=close_qty,
            time_in_force=TimeInForce.IOC,
            reduce_only=True,
        )
    )

    expected_remaining = str(float(initial_qty) - float(close_qty))
    await perp_taker_tester.check.position(
        symbol=PERP_SYMBOL,
        expected_exchange_id=REYA_DEX_ID,
        expected_account_id=perp_taker_tester.account_id,
        expected_qty=expected_remaining,
        expected_side=Side.A,
    )


@pytest.mark.asyncio
async def test_position_decrease_without_reduce_only(
    perp_maker_tester: ReyaTester, perp_taker_tester: ReyaTester
) -> None:
    """Counter-trade an existing position with reduce_only=False — position should still net down."""
    await perp_taker_tester.check.position_not_open(PERP_SYMBOL)
    market_price = float(await perp_taker_tester.data.current_price(PERP_SYMBOL))
    initial_qty = "0.02"
    counter_qty = "0.01"

    await _rest_maker_sell(perp_maker_tester, market_price, qty=initial_qty)
    await perp_taker_tester.orders.create_limit(
        LimitOrderParameters(
            symbol=PERP_SYMBOL,
            is_buy=True,
            limit_px=str(round(market_price * 1.05, 2)),
            qty=initial_qty,
            time_in_force=TimeInForce.IOC,
            reduce_only=False,
        )
    )

    await _rest_maker_buy(perp_maker_tester, market_price, qty=counter_qty)
    await perp_taker_tester.orders.create_limit(
        LimitOrderParameters(
            symbol=PERP_SYMBOL,
            is_buy=False,
            limit_px=str(round(market_price * 0.95, 2)),
            qty=counter_qty,
            time_in_force=TimeInForce.IOC,
            reduce_only=False,  # explicitly NOT reduce-only
        )
    )

    expected_remaining = str(float(initial_qty) - float(counter_qty))
    await perp_taker_tester.check.position(
        symbol=PERP_SYMBOL,
        expected_exchange_id=REYA_DEX_ID,
        expected_account_id=perp_taker_tester.account_id,
        expected_qty=expected_remaining,
        expected_side=Side.B,
    )


@pytest.mark.asyncio
async def test_position_close_via_reduce_only_ioc(perp_maker_tester: ReyaTester, perp_taker_tester: ReyaTester) -> None:
    """Open a long, then close it fully with an opposite-side reduce-only IOC."""
    await perp_taker_tester.check.position_not_open(PERP_SYMBOL)
    market_price = float(await perp_taker_tester.data.current_price(PERP_SYMBOL))

    # Open: maker sell + taker buy
    await _rest_maker_sell(perp_maker_tester, market_price)
    await perp_taker_tester.orders.create_limit(
        LimitOrderParameters(
            symbol=PERP_SYMBOL,
            is_buy=True,
            limit_px=str(round(market_price * 1.05, 2)),
            qty=PERP_QTY,
            time_in_force=TimeInForce.IOC,
            reduce_only=False,
        )
    )

    # Close: maker buy + taker reduce-only sell
    await _rest_maker_buy(perp_maker_tester, market_price)
    await perp_taker_tester.orders.create_limit(
        LimitOrderParameters(
            symbol=PERP_SYMBOL,
            is_buy=False,
            limit_px=str(round(market_price * 0.95, 2)),
            qty=PERP_QTY,
            time_in_force=TimeInForce.IOC,
            reduce_only=True,
        )
    )

    await perp_taker_tester.check.position_not_open(PERP_SYMBOL)
