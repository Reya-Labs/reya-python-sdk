"""
Perp position-management tests using the maker/taker pattern.

Under perp orderbook every fill needs a counterparty, so position-formation
tests can no longer use a single account that hits the AMM pool. These tests
have ``perp_maker_tester`` rest GTC liquidity and ``perp_taker_tester`` cross
against it via IOC, then assert position state on the taker.

The tests are **baseline-relative**: the shared devnet accounts may carry
pre-existing positions, so each test reads the taker's signed position first
and asserts the signed delta its own trades caused (via
``check.position_delta``). Teardown (``perp_baseline_restore`` in conftest)
trades the delta back so accounts leave exactly as the test got them.
Choreographies whose reduce-only legs only make sense from a given baseline
sign skip themselves when the baseline doesn't cooperate.

Scenarios covered:
- Open a long via taker IOC against maker sell (+qty delta).
- Open a short via taker IOC against maker buy (−qty delta).
- Increase an existing exposure with a same-side IOC.
- Round-trip a position fully with an opposite-side reduce-only IOC.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api.config import REYA_DEX_ID
from sdk.reya_rest_api.models import LimitOrderParameters
from tests.helpers import ReyaTester
from tests.helpers.liquidity_detector import skip_if_external_liquidity

PERP_SYMBOL = "ETHRUSDPERP"
PERP_QTY = "0.01"
PERP_DELTA = Decimal(PERP_QTY)


# All tests in this module rest a maker order at oracle ±1% and then cross it
# with an opposite-side taker IOC at oracle ±5%. That pattern assumes nobody
# else is on the book — see the docstring of `skip_if_external_liquidity` for
# the rationale and the mirroring spot-suite precedent. The helpers below call
# the guard once before placing the maker order; if external liquidity is
# present, the test is skipped with a clear message rather than failing with
# an opaque ``RuntimeError: Order X not created after 10 seconds`` (which is
# what happens when the maker crosses external MM liquidity and gets
# instantly filled instead of resting).


async def _rest_maker_sell(maker: ReyaTester, market_price: float, qty: str = PERP_QTY) -> str:
    """Place a maker sell at 1% below oracle. Returns the maker order_id.

    Skips the calling test if external bid/ask liquidity is on the book —
    the −1% sell would cross any bid within the ±5% circuit-breaker band
    and never rest.
    """
    await skip_if_external_liquidity(maker.data, PERP_SYMBOL, market_price, reason_prefix="_rest_maker_sell")
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
    """Place a maker buy at 1% above oracle. Returns the maker order_id.

    Skips the calling test if external bid/ask liquidity is on the book —
    the +1% buy would cross any ask within the ±5% circuit-breaker band
    and never rest.
    """
    await skip_if_external_liquidity(maker.data, PERP_SYMBOL, market_price, reason_prefix="_rest_maker_buy")
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


async def _taker_ioc(
    taker: ReyaTester,
    market_price: float,
    is_buy: bool,
    qty: str = PERP_QTY,
    reduce_only: bool = False,
) -> None:
    """Cross the resting maker with an IOC at oracle ±5%."""
    multiplier = 1.05 if is_buy else 0.95
    await taker.orders.create_limit(
        LimitOrderParameters(
            symbol=PERP_SYMBOL,
            is_buy=is_buy,
            limit_px=str(round(market_price * multiplier, 2)),
            qty=qty,
            time_in_force=TimeInForce.IOC,
            reduce_only=reduce_only,
        )
    )


async def _wait_settled(taker: ReyaTester, baseline: Decimal, expected_delta: Decimal) -> None:
    """Block until the taker's opening fill is visible as settled position state.

    A reduce-only order is sized by the engine against the settled position; a
    fill the engine has acked but not yet settled on chain is not reducible.
    """
    await taker.check.position_delta(symbol=PERP_SYMBOL, baseline=baseline, expected_delta=expected_delta)


def _skip_unless_baseline_long_or_flat(baseline: Decimal) -> None:
    """Guard for choreographies whose reduce-only SELL leg assumes the taker
    is net long after the opening buys — impossible from a short baseline."""
    if baseline < 0:
        pytest.skip(
            f"account not flat (baseline {baseline}) — reduce-only sell leg needs a non-negative taker baseline"
        )


def _skip_unless_baseline_short_or_flat(baseline: Decimal) -> None:
    """Mirror guard: the reduce-only BUY leg assumes the taker is net short
    after the opening sells — impossible from a long baseline."""
    if baseline > 0:
        pytest.skip(f"account not flat (baseline {baseline}) — reduce-only buy leg needs a non-positive taker baseline")


@pytest.mark.asyncio
async def test_position_open_long_via_taker_ioc(perp_maker_tester: ReyaTester, perp_taker_tester: ReyaTester) -> None:
    """Taker IOC buy lifts maker sell — taker gains +PERP_QTY of exposure."""
    baseline = await perp_taker_tester.positions.signed_qty(PERP_SYMBOL)
    market_price = float(await perp_taker_tester.data.current_price(PERP_SYMBOL))

    await _rest_maker_sell(perp_maker_tester, market_price)
    await _taker_ioc(perp_taker_tester, market_price, is_buy=True)

    await perp_taker_tester.check.position_delta(
        symbol=PERP_SYMBOL,
        baseline=baseline,
        expected_delta=PERP_DELTA,
        expected_account_id=perp_taker_tester.account_id,
        expected_exchange_id=REYA_DEX_ID,
    )


@pytest.mark.asyncio
async def test_position_open_short_via_taker_ioc(perp_maker_tester: ReyaTester, perp_taker_tester: ReyaTester) -> None:
    """Taker IOC sell hits maker buy — taker gains −PERP_QTY of exposure."""
    baseline = await perp_taker_tester.positions.signed_qty(PERP_SYMBOL)
    market_price = float(await perp_taker_tester.data.current_price(PERP_SYMBOL))

    await _rest_maker_buy(perp_maker_tester, market_price)
    await _taker_ioc(perp_taker_tester, market_price, is_buy=False)

    await perp_taker_tester.check.position_delta(
        symbol=PERP_SYMBOL,
        baseline=baseline,
        expected_delta=-PERP_DELTA,
        expected_account_id=perp_taker_tester.account_id,
        expected_exchange_id=REYA_DEX_ID,
    )


@pytest.mark.asyncio
async def test_position_increase_long(perp_maker_tester: ReyaTester, perp_taker_tester: ReyaTester) -> None:
    """Two same-side taker IOCs against fresh maker liquidity stack to a 2x delta."""
    baseline = await perp_taker_tester.positions.signed_qty(PERP_SYMBOL)
    market_price = float(await perp_taker_tester.data.current_price(PERP_SYMBOL))

    # First leg
    await _rest_maker_sell(perp_maker_tester, market_price)
    await _taker_ioc(perp_taker_tester, market_price, is_buy=True)
    await _wait_settled(perp_taker_tester, baseline, PERP_DELTA)

    # Second leg (more maker liquidity, then more taker IOC)
    await _rest_maker_sell(perp_maker_tester, market_price)
    await _taker_ioc(perp_taker_tester, market_price, is_buy=True)

    await perp_taker_tester.check.position_delta(
        symbol=PERP_SYMBOL,
        baseline=baseline,
        expected_delta=PERP_DELTA * 2,
        expected_account_id=perp_taker_tester.account_id,
        expected_exchange_id=REYA_DEX_ID,
    )


@pytest.mark.asyncio
async def test_position_increase_short(perp_maker_tester: ReyaTester, perp_taker_tester: ReyaTester) -> None:
    """Mirror of test_position_increase_long: two same-side IOC sells against fresh maker buys."""
    baseline = await perp_taker_tester.positions.signed_qty(PERP_SYMBOL)
    market_price = float(await perp_taker_tester.data.current_price(PERP_SYMBOL))

    await _rest_maker_buy(perp_maker_tester, market_price)
    await _taker_ioc(perp_taker_tester, market_price, is_buy=False)
    await _wait_settled(perp_taker_tester, baseline, -PERP_DELTA)

    await _rest_maker_buy(perp_maker_tester, market_price)
    await _taker_ioc(perp_taker_tester, market_price, is_buy=False)

    await perp_taker_tester.check.position_delta(
        symbol=PERP_SYMBOL,
        baseline=baseline,
        expected_delta=-(PERP_DELTA * 2),
        expected_account_id=perp_taker_tester.account_id,
        expected_exchange_id=REYA_DEX_ID,
    )


@pytest.mark.asyncio
async def test_position_partial_close_long(perp_maker_tester: ReyaTester, perp_taker_tester: ReyaTester) -> None:
    """Buy 0.02 of exposure, then close half via reduce-only IOC sell — net delta +0.01."""
    baseline = await perp_taker_tester.positions.signed_qty(PERP_SYMBOL)
    _skip_unless_baseline_long_or_flat(baseline)
    market_price = float(await perp_taker_tester.data.current_price(PERP_SYMBOL))
    initial_qty = "0.02"
    close_qty = "0.01"

    # Open +0.02
    await _rest_maker_sell(perp_maker_tester, market_price, qty=initial_qty)
    await _taker_ioc(perp_taker_tester, market_price, is_buy=True, qty=initial_qty)

    # The reduce-only gate sizes against the SETTLED position (in-flight
    # increases are assumed to bust), so the opening fill must have settled
    # before a reduce-only close has anything to reduce. A FILLED ack alone is
    # not settlement — closing on the ack is refused with
    # `reduce-only order has nothing to reduce`.
    await _wait_settled(perp_taker_tester, baseline, Decimal(initial_qty))

    # Partial close 0.01
    await _rest_maker_buy(perp_maker_tester, market_price, qty=close_qty)
    await _taker_ioc(perp_taker_tester, market_price, is_buy=False, qty=close_qty, reduce_only=True)

    await perp_taker_tester.check.position_delta(
        symbol=PERP_SYMBOL,
        baseline=baseline,
        expected_delta=Decimal(initial_qty) - Decimal(close_qty),
        expected_account_id=perp_taker_tester.account_id,
        expected_exchange_id=REYA_DEX_ID,
    )


@pytest.mark.asyncio
async def test_position_partial_close_short(perp_maker_tester: ReyaTester, perp_taker_tester: ReyaTester) -> None:
    """Mirror of partial_close_long: sell 0.02 of exposure, close half via reduce-only IOC buy."""
    baseline = await perp_taker_tester.positions.signed_qty(PERP_SYMBOL)
    _skip_unless_baseline_short_or_flat(baseline)
    market_price = float(await perp_taker_tester.data.current_price(PERP_SYMBOL))
    initial_qty = "0.02"
    close_qty = "0.01"

    await _rest_maker_buy(perp_maker_tester, market_price, qty=initial_qty)
    await _taker_ioc(perp_taker_tester, market_price, is_buy=False, qty=initial_qty)

    # See test_position_partial_close_long: reduce-only sizes against settled
    # base, so the opening fill must settle before the close is reducible.
    await _wait_settled(perp_taker_tester, baseline, -Decimal(initial_qty))

    await _rest_maker_sell(perp_maker_tester, market_price, qty=close_qty)
    await _taker_ioc(perp_taker_tester, market_price, is_buy=True, qty=close_qty, reduce_only=True)

    await perp_taker_tester.check.position_delta(
        symbol=PERP_SYMBOL,
        baseline=baseline,
        expected_delta=-(Decimal(initial_qty) - Decimal(close_qty)),
        expected_account_id=perp_taker_tester.account_id,
        expected_exchange_id=REYA_DEX_ID,
    )


@pytest.mark.asyncio
async def test_position_decrease_without_reduce_only(
    perp_maker_tester: ReyaTester, perp_taker_tester: ReyaTester
) -> None:
    """Counter-trade fresh exposure with reduce_only=False — exposure still nets down."""
    baseline = await perp_taker_tester.positions.signed_qty(PERP_SYMBOL)
    market_price = float(await perp_taker_tester.data.current_price(PERP_SYMBOL))
    initial_qty = "0.02"
    counter_qty = "0.01"

    await _rest_maker_sell(perp_maker_tester, market_price, qty=initial_qty)
    await _taker_ioc(perp_taker_tester, market_price, is_buy=True, qty=initial_qty)

    await _rest_maker_buy(perp_maker_tester, market_price, qty=counter_qty)
    # explicitly NOT reduce-only
    await _taker_ioc(perp_taker_tester, market_price, is_buy=False, qty=counter_qty, reduce_only=False)

    await perp_taker_tester.check.position_delta(
        symbol=PERP_SYMBOL,
        baseline=baseline,
        expected_delta=Decimal(initial_qty) - Decimal(counter_qty),
        expected_account_id=perp_taker_tester.account_id,
        expected_exchange_id=REYA_DEX_ID,
    )


@pytest.mark.asyncio
async def test_position_close_via_reduce_only_ioc(perp_maker_tester: ReyaTester, perp_taker_tester: ReyaTester) -> None:
    """Round-trip: open +PERP_QTY, unwind it fully with a reduce-only IOC — net delta zero."""
    baseline = await perp_taker_tester.positions.signed_qty(PERP_SYMBOL)
    _skip_unless_baseline_long_or_flat(baseline)
    market_price = float(await perp_taker_tester.data.current_price(PERP_SYMBOL))

    # Open: maker sell + taker buy
    await _rest_maker_sell(perp_maker_tester, market_price)
    await _taker_ioc(perp_taker_tester, market_price, is_buy=True)

    await perp_taker_tester.check.position_delta(
        symbol=PERP_SYMBOL,
        baseline=baseline,
        expected_delta=PERP_DELTA,
    )

    # Close: maker buy + taker reduce-only sell
    await _rest_maker_buy(perp_maker_tester, market_price)
    await _taker_ioc(perp_taker_tester, market_price, is_buy=False, reduce_only=True)

    await perp_taker_tester.check.position_delta(
        symbol=PERP_SYMBOL,
        baseline=baseline,
        expected_delta=Decimal("0"),
    )
