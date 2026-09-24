#!/usr/bin/env python3

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from sdk.async_api.order import Order as AsyncOrder
from sdk.open_api import RequestError, RequestErrorCode, TimeInForce
from sdk.open_api.exceptions import BadRequestException
from sdk.open_api.models.cancel_reason import CancelReason
from sdk.open_api.models.create_order_response import CreateOrderResponse
from sdk.open_api.models.order import Order
from sdk.open_api.models.order_status import OrderStatus
from sdk.open_api.models.order_type import OrderType
from sdk.open_api.models.position import Position
from sdk.reya_rest_api.config import REYA_DEX_ID
from sdk.reya_rest_api.models import LimitOrderParameters, TriggerOrderParameters
from tests.helpers import ReyaTester
from tests.helpers.liquidity_detector import skip_if_external_liquidity
from tests.helpers.price_helpers import format_price, quantize_price
from tests.helpers.reya_tester import limit_order_params_to_order, logger, trigger_order_params_to_order
from tests.helpers.reya_tester.matchers import ExecutionMatcher

# The engine arms a trigger on admission and evaluates it on each mark-price
# tick: a trigger that is already crossed when armed fires on the next tick,
# not at admission. The fired child keeps the trigger's order id, reports
# `triggered=True`, and fills like an ordinary order, so every firing test
# rests the maker liquidity the child needs before arming. Position-driven
# cancellation (close, flip, a crossed trigger with no position) follows the
# indexed on-chain position, so those waits are longer than a local fill's.
# All of these run on exact-source Localnet only.
_LOCALNET_CHAIN_ID = 31337


def _trigger_params(
    symbol: str,
    is_buy: bool,
    trigger_px: str,
    trigger_type: OrderType,
    time_in_force: TimeInForce = TimeInForce.IOC,
) -> TriggerOrderParameters:
    """A trigger pinned at its own trigger price.

    ``limit_px == trigger_px`` is the only limit price inside every market's
    admission band whatever the venue configures it to, so these tests stay
    admissible without reading per-market configuration.
    """
    return TriggerOrderParameters(
        symbol=symbol,
        is_buy=is_buy,
        trigger_px=trigger_px,
        trigger_type=trigger_type,
        limit_px=trigger_px,
        time_in_force=time_in_force,
    )


def _require_exact_source_localnet(reya_tester: ReyaTester) -> None:
    if reya_tester.chain_id != _LOCALNET_CHAIN_ID:
        pytest.skip("requires exact-source Localnet SL/TP backbone")


async def _confirm_open_fill_sequence(
    reya_tester: ReyaTester, expected_order: Order, baseline_seq: int, timeout: float = 10.0
) -> int:
    """Return the current opening order's wallet-execution sequence number.

    The session can receive a delayed position update from the preceding test after
    ``baseline_seq`` is read. Match the wallet execution to this exact order so that stale
    position state cannot become the baseline for ``check_no_order_execution_since``.
    """
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        execution = reya_tester.ws.perp_executions.find_last(
            lambda item: (item.sequence_number or 0) > baseline_seq
            and ExecutionMatcher.match_perp(item, expected_order)
        )
        if execution is not None and execution.sequence_number is not None:
            return execution.sequence_number
        await asyncio.sleep(0.1)
    raise AssertionError(f"Opening fill not observed past sequence {baseline_seq} within {timeout}s")


async def _open_localnet_long(
    maker: ReyaTester,
    taker: ReyaTester,
    symbol: str,
    qty: str = "0.01",
) -> tuple[Decimal, Decimal, int, Decimal]:
    """Open a deterministic long for the trigger owner on an empty Localnet book.

    Perp OB has no pool counterparty, so a standalone IOC is not a position
    fixture. Rest an explicit maker ask first, cross it from the trigger owner,
    and return both the observed position sequence and signed-quantity baseline
    used to prove that create/cancel did not fire the trigger.
    """
    baseline = await taker.positions.signed_qty(symbol)
    baseline_seq = await taker.get_last_perp_execution_sequence_number()
    market_price = Decimal(str(await taker.data.current_price(symbol)))
    tick_size = Decimal(str((await taker.data.market_definition(symbol)).tick_size))
    await skip_if_external_liquidity(
        maker.data,
        symbol,
        float(market_price),
        reason_prefix="_open_localnet_long",
    )

    maker_px = format_price(quantize_price(market_price * Decimal("0.99"), tick_size))
    taker_px = format_price(quantize_price(market_price * Decimal("1.05"), tick_size))
    maker_order_id = await maker.orders.create_limit(
        LimitOrderParameters(
            symbol=symbol,
            is_buy=False,
            limit_px=maker_px,
            qty=qty,
            time_in_force=TimeInForce.GTC,
        )
    )
    assert maker_order_id is not None
    await maker.wait.for_order_creation(maker_order_id)

    taker_params = LimitOrderParameters(
        symbol=symbol,
        is_buy=True,
        limit_px=taker_px,
        qty=qty,
        time_in_force=TimeInForce.IOC,
        reduce_only=False,
    )
    await taker.orders.create_limit(taker_params)
    expected_order = limit_order_params_to_order(taker_params, taker.account_id)
    sequence_after_position = await _confirm_open_fill_sequence(taker, expected_order, baseline_seq)
    await taker.check.position_delta(
        symbol=symbol,
        baseline=baseline,
        expected_delta=Decimal(qty),
        expected_exchange_id=REYA_DEX_ID,
        expected_account_id=taker.account_id,
    )
    return market_price, tick_size, sequence_after_position, baseline


def assert_tp_sl_order_submission(
    order_details: Order,
    expected_order_details: Order,
    position: Position,
    _reya_tester: ReyaTester,
):
    """Assert that order execution details are correct"""

    assert order_details is not None, "Should have order execution details"
    assert order_details.symbol == expected_order_details.symbol, "Market ID should match"
    assert order_details.trigger_px is not None
    assert order_details.limit_px is not None
    assert expected_order_details.limit_px is not None
    assert expected_order_details.trigger_px is not None
    assert float(order_details.trigger_px) == pytest.approx(
        float(expected_order_details.trigger_px), rel=1e-6
    ), "Trigger price should match"
    assert float(order_details.limit_px) == pytest.approx(
        float(expected_order_details.limit_px), rel=1e-6
    ), "Limit price should match"
    assert order_details.side == expected_order_details.side, "Executed base should match"
    assert expected_order_details.qty is not None
    assert float(position.qty) == float(expected_order_details.qty), "Order direction does not match"
    assert order_details.status == OrderStatus.OPEN, "Order status should be PENDING"

    logger.info("✅ Order submission confirmed correctly")


@pytest.mark.asyncio
async def test_success_tp_order_create_cancel(
    perp_maker_tester: ReyaTester,
    perp_taker_tester: ReyaTester,
):
    """TP order, close right after creation"""
    reya_tester = perp_taker_tester
    _require_exact_source_localnet(reya_tester)
    symbol = "ETHRUSDPERP"
    market_price, tick_size, sequence_after_position, baseline = await _open_localnet_long(
        perp_maker_tester,
        reya_tester,
        symbol,
    )
    await reya_tester.check.no_open_orders()

    # SUBMIT TP
    tp_params = _trigger_params(
        symbol=symbol,
        is_buy=False,  # on long
        trigger_px=format_price(
            quantize_price(Decimal(market_price) * Decimal("2"), tick_size)
        ),  # above IOC limit price
        trigger_type=OrderType.TAKE_PROFIT,
    )
    tp_order: CreateOrderResponse = await reya_tester.orders.create_trigger(tp_params)
    logger.info(f"Created TP order with ID: {tp_order.order_id}")

    assert tp_order.order_id is not None
    active_tp_order = await reya_tester.wait.for_order_creation(order_id=tp_order.order_id)
    expected_tp_order = trigger_order_params_to_order(tp_params, reya_tester.account_id)
    await reya_tester.check.open_order_created(tp_order.order_id, expected_tp_order)
    await reya_tester.check.position_delta(
        symbol=symbol,
        baseline=baseline,
        expected_delta=Decimal("0.01"),
        expected_exchange_id=REYA_DEX_ID,
        expected_account_id=reya_tester.account_id,
    )

    # CANCEL order
    await reya_tester.client.cancel_order(
        symbol=symbol,
        account_id=reya_tester.account_id,
        order_id=active_tp_order.order_id,
    )

    await reya_tester.wait.for_order_state(active_tp_order.order_id, OrderStatus.CANCELLED)
    await reya_tester.check_no_order_execution_since(sequence_after_position)
    await reya_tester.check.position_delta(
        symbol=symbol,
        baseline=baseline,
        expected_delta=Decimal("0.01"),
        expected_exchange_id=REYA_DEX_ID,
        expected_account_id=reya_tester.account_id,
    )

    logger.info("TP order cancel test completed successfully")


@pytest.mark.asyncio
async def test_success_sl_order_create_cancel(
    perp_maker_tester: ReyaTester,
    perp_taker_tester: ReyaTester,
):
    """SL order, close right after creation"""
    reya_tester = perp_taker_tester
    _require_exact_source_localnet(reya_tester)
    symbol = "ETHRUSDPERP"
    market_price, tick_size, sequence_after_position, baseline = await _open_localnet_long(
        perp_maker_tester,
        reya_tester,
        symbol,
    )
    await reya_tester.check.no_open_orders()

    # SUBMIT SL
    sl_params = _trigger_params(
        symbol=symbol,
        is_buy=False,  # on long
        trigger_px=format_price(quantize_price(Decimal(market_price) * Decimal("0.9"), tick_size)),  # below entry
        trigger_type=OrderType.STOP_LOSS,
    )
    order_response = await reya_tester.orders.create_trigger(sl_params)
    logger.info(f"Created SL order with ID: {order_response.order_id}")

    assert order_response.order_id is not None
    active_sl_order = await reya_tester.wait.for_order_creation(order_id=order_response.order_id, timeout=10)
    expected_sl_order = trigger_order_params_to_order(sl_params, reya_tester.account_id)
    await reya_tester.check.open_order_created(order_response.order_id, expected_sl_order)
    await reya_tester.check.position_delta(
        symbol=symbol,
        baseline=baseline,
        expected_delta=Decimal("0.01"),
        expected_exchange_id=REYA_DEX_ID,
        expected_account_id=reya_tester.account_id,
    )

    # CANCEL
    await reya_tester.client.cancel_order(
        symbol=symbol,
        account_id=reya_tester.account_id,
        order_id=active_sl_order.order_id,
    )
    await reya_tester.wait.for_order_state(active_sl_order.order_id, OrderStatus.CANCELLED)
    await reya_tester.check_no_order_execution_since(sequence_after_position)
    await reya_tester.check.position_delta(
        symbol=symbol,
        baseline=baseline,
        expected_delta=Decimal("0.01"),
        expected_exchange_id=REYA_DEX_ID,
        expected_account_id=reya_tester.account_id,
    )

    logger.info("SL order cancel test completed successfully")


@pytest.mark.asyncio
async def test_failure_cancel_when_order_is_not_found(reya_tester: ReyaTester):
    """Cancelling a non-existent order returns proper error.

    Verifies:
    1. API returns BadRequestException for unknown order ID
    2. Error message indicates the order was not found
    3. Error code is ORDER_NOT_FOUND_ERROR
    """
    _require_exact_source_localnet(reya_tester)
    # A syntactically valid but nonexistent numeric order id. "unknown_id"
    # would crash client-side in build_cancel_order_payload (`int(order_id)`)
    # before the request ever reaches the server, so the not-found path could
    # never be exercised.
    nonexistent_order_id = "999999999999999"
    await reya_tester.check.no_open_orders()
    try:
        await reya_tester.client.cancel_order(
            symbol="ETHRUSDPERP",
            account_id=reya_tester.account_id,
            order_id=nonexistent_order_id,
        )
        raise RuntimeError("Should have failed")
    except BadRequestException as e:
        assert e.data is not None
        requestError: RequestError = e.data
        assert requestError.message is not None
        assert requestError.message.startswith(
            f"Order not found: {nonexistent_order_id}"
        ), f"Expected message to start with 'Order not found: {nonexistent_order_id}', got: {requestError.message}"
        assert requestError.error == RequestErrorCode.ORDER_NOT_FOUND_ERROR

    await reya_tester.check.no_open_orders()
    logger.info("✅ Cancel non-existent order returns proper error")


FIRE_TIMEOUT = 30
POSITION_EVENT_TIMEOUT = 60


async def _require_flat(taker: ReyaTester, symbol: str) -> None:
    baseline = await taker.positions.signed_qty(symbol)
    if baseline != 0:
        pytest.skip(f"account not flat (baseline {baseline}) — protective triggers act on the whole position")


async def _prices(taker: ReyaTester, symbol: str) -> tuple[Decimal, Decimal]:
    market_price = Decimal(str(await taker.data.current_price(symbol)))
    tick_size = Decimal(str((await taker.data.market_definition(symbol)).tick_size))
    return market_price, tick_size


def _px(market_price: Decimal, factor: str, tick_size: Decimal) -> str:
    return format_price(quantize_price(market_price * Decimal(factor), tick_size))


async def _open_localnet_position(
    maker: ReyaTester, taker: ReyaTester, symbol: str, is_long: bool, qty: str = "0.01"
) -> tuple[Decimal, Decimal]:
    """Open a taker position against a resting maker order on an empty book."""
    market_price, tick_size = await _prices(taker, symbol)
    await skip_if_external_liquidity(maker.data, symbol, float(market_price), reason_prefix="_open_localnet_position")
    maker_order_id = await maker.orders.create_limit(
        LimitOrderParameters(
            symbol=symbol,
            is_buy=not is_long,
            limit_px=_px(market_price, "0.99" if is_long else "1.01", tick_size),
            qty=qty,
            time_in_force=TimeInForce.GTC,
        )
    )
    assert maker_order_id is not None
    await maker.wait.for_order_creation(maker_order_id)
    await taker.orders.create_limit(
        LimitOrderParameters(
            symbol=symbol,
            is_buy=is_long,
            limit_px=_px(market_price, "1.05" if is_long else "0.95", tick_size),
            qty=qty,
            time_in_force=TimeInForce.IOC,
            reduce_only=False,
        )
    )
    expected = Decimal(qty) if is_long else -Decimal(qty)
    await _wait_for_position(taker, symbol, expected)
    return market_price, tick_size


async def _wait_for_position(taker: ReyaTester, symbol: str, expected: Decimal) -> None:
    deadline = asyncio.get_running_loop().time() + POSITION_EVENT_TIMEOUT
    current = await taker.positions.signed_qty(symbol)
    while current != expected:
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError(f"{symbol} position stayed {current}, expected {expected}")
        await asyncio.sleep(0.5)
        current = await taker.positions.signed_qty(symbol)


async def _rest_maker(maker: ReyaTester, symbol: str, is_buy: bool, limit_px: str, qty: str = "0.01") -> str:
    """Rest the liquidity a fired child (or a manual close) will cross."""
    order_id = await maker.orders.create_limit(
        LimitOrderParameters(symbol=symbol, is_buy=is_buy, limit_px=limit_px, qty=qty, time_in_force=TimeInForce.GTC)
    )
    assert order_id is not None
    await maker.wait.for_order_creation(order_id)
    return order_id


async def _arm(taker: ReyaTester, params: TriggerOrderParameters) -> str:
    response: CreateOrderResponse = await taker.orders.create_trigger(params)
    assert response.order_id is not None
    assert response.status == OrderStatus.OPEN, f"a trigger is armed on admission, got {response.status}"
    await taker.wait.for_order_creation(order_id=response.order_id)
    return str(response.order_id)


async def _await_order(
    taker: ReyaTester, order_id: str, status: OrderStatus, timeout: int, cancel_reason: CancelReason | None = None
) -> AsyncOrder:
    """Wait for the order's terminal status, then read the WS order change for its details."""
    await taker.wait.for_order_state(order_id, status, timeout=timeout)
    order = taker.ws.orders.get(order_id)
    assert order is not None, f"no order change observed for {order_id}"
    if cancel_reason is not None:
        observed = order.cancel_reason.value if order.cancel_reason is not None else None
        assert observed == cancel_reason.value, f"{order_id}: cancelReason {observed} != {cancel_reason.value}"
    return order


async def _assert_fired_and_filled(taker: ReyaTester, order_id: str) -> None:
    order = await _await_order(taker, order_id, OrderStatus.FILLED, FIRE_TIMEOUT)
    assert order.triggered is True, f"{order_id} filled without reporting triggered=True"


@pytest.mark.asyncio
async def test_tp_in_cross_fires_on_next_mark(perp_maker_tester: ReyaTester, perp_taker_tester: ReyaTester):
    """A take-profit already crossed when armed fires on the next mark tick and closes the short.

    A short's TP buys when the mark falls to its trigger, so a trigger above the
    mark is already crossed. The child buys at the trigger, crossing a resting ask.
    """
    taker, symbol = perp_taker_tester, "ETHRUSDPERP"
    _require_exact_source_localnet(taker)
    await _require_flat(taker, symbol)
    market_price, tick_size = await _open_localnet_position(perp_maker_tester, taker, symbol, is_long=False)
    trigger_px = _px(market_price, "1.02", tick_size)
    await _rest_maker(perp_maker_tester, symbol, is_buy=False, limit_px=trigger_px)

    tp_id = await _arm(
        taker, _trigger_params(symbol, is_buy=True, trigger_px=trigger_px, trigger_type=OrderType.TAKE_PROFIT)
    )

    await _assert_fired_and_filled(taker, tp_id)
    await _wait_for_position(taker, symbol, Decimal(0))


@pytest.mark.asyncio
async def test_sl_in_cross_fires_on_next_mark(perp_maker_tester: ReyaTester, perp_taker_tester: ReyaTester):
    """A stop-loss already crossed when armed fires on the next mark tick and closes the short.

    A short's SL buys when the mark rises to its trigger, so a trigger below the
    mark is already crossed. The child buys at the trigger, crossing a resting ask.
    """
    taker, symbol = perp_taker_tester, "ETHRUSDPERP"
    _require_exact_source_localnet(taker)
    await _require_flat(taker, symbol)
    market_price, tick_size = await _open_localnet_position(perp_maker_tester, taker, symbol, is_long=False)
    trigger_px = _px(market_price, "0.98", tick_size)
    await _rest_maker(perp_maker_tester, symbol, is_buy=False, limit_px=trigger_px)

    sl_id = await _arm(
        taker, _trigger_params(symbol, is_buy=True, trigger_px=trigger_px, trigger_type=OrderType.STOP_LOSS)
    )

    await _assert_fired_and_filled(taker, sl_id)
    await _wait_for_position(taker, symbol, Decimal(0))


@pytest.mark.asyncio
async def test_sltp_without_a_position(perp_maker_tester: ReyaTester, perp_taker_tester: ReyaTester):
    """Protection is admitted before its position exists; a crossed leg with nothing to close retires the pair.

    An uncrossed stop stays armed across mark ticks, waiting for a position. A
    crossed sibling fires, finds no position to protect, and the engine cancels
    both legs of the pair POSITION_CLOSED.
    """
    del perp_maker_tester  # only here so the baseline restore runs
    taker, symbol = perp_taker_tester, "ETHRUSDPERP"
    _require_exact_source_localnet(taker)
    await _require_flat(taker, symbol)
    market_price, tick_size = await _prices(taker, symbol)
    sequence_before = await taker.get_last_perp_execution_sequence_number()

    # Would close a long: the SL sells on a fall to 0.5x, which is not crossed.
    sl_id = await _arm(
        taker,
        _trigger_params(
            symbol, is_buy=False, trigger_px=_px(market_price, "0.5", tick_size), trigger_type=OrderType.STOP_LOSS
        ),
    )
    await asyncio.sleep(5)  # several mark ticks
    sl = taker.ws.orders.get(sl_id)
    assert sl is not None and sl.status.value == "OPEN", f"uncrossed pre-armed SL should stay armed, got {sl}"

    # The TP sells on a rise to 0.98x, which is already crossed.
    tp_id = await _arm(
        taker,
        _trigger_params(
            symbol, is_buy=False, trigger_px=_px(market_price, "0.98", tick_size), trigger_type=OrderType.TAKE_PROFIT
        ),
    )

    for order_id in (tp_id, sl_id):
        await _await_order(taker, order_id, OrderStatus.CANCELLED, FIRE_TIMEOUT, CancelReason.POSITION_CLOSED)
    await taker.check_no_order_execution_since(sequence_before)


@pytest.mark.asyncio
async def test_sltp_cancelled_when_position_closed(perp_maker_tester: ReyaTester, perp_taker_tester: ReyaTester):
    """Closing the position retires both armed legs with POSITION_CLOSED once the close is indexed."""
    taker, symbol = perp_taker_tester, "ETHRUSDPERP"
    _require_exact_source_localnet(taker)
    await _require_flat(taker, symbol)
    market_price, tick_size = await _open_localnet_position(perp_maker_tester, taker, symbol, is_long=True)
    sl_id = await _arm(
        taker,
        _trigger_params(
            symbol, is_buy=False, trigger_px=_px(market_price, "0.5", tick_size), trigger_type=OrderType.STOP_LOSS
        ),
    )
    tp_id = await _arm(
        taker,
        _trigger_params(
            symbol, is_buy=False, trigger_px=_px(market_price, "2", tick_size), trigger_type=OrderType.TAKE_PROFIT
        ),
    )

    close_px = _px(market_price, "0.99", tick_size)
    await _rest_maker(perp_maker_tester, symbol, is_buy=True, limit_px=close_px)
    await taker.orders.create_limit(
        LimitOrderParameters(
            symbol=symbol, is_buy=False, limit_px=close_px, qty="0.01", time_in_force=TimeInForce.IOC, reduce_only=True
        )
    )
    await _wait_for_position(taker, symbol, Decimal(0))

    for order_id in (sl_id, tp_id):
        await _await_order(taker, order_id, OrderStatus.CANCELLED, POSITION_EVENT_TIMEOUT, CancelReason.POSITION_CLOSED)


@pytest.mark.asyncio
async def test_sltp_cancelled_when_position_flipped(perp_maker_tester: ReyaTester, perp_taker_tester: ReyaTester):
    """Flipping long to short retires both of the long's armed legs with POSITION_CLOSED."""
    taker, symbol = perp_taker_tester, "ETHRUSDPERP"
    _require_exact_source_localnet(taker)
    await _require_flat(taker, symbol)
    market_price, tick_size = await _open_localnet_position(perp_maker_tester, taker, symbol, is_long=True)
    sl_id = await _arm(
        taker,
        _trigger_params(
            symbol, is_buy=False, trigger_px=_px(market_price, "0.5", tick_size), trigger_type=OrderType.STOP_LOSS
        ),
    )
    tp_id = await _arm(
        taker,
        _trigger_params(
            symbol, is_buy=False, trigger_px=_px(market_price, "2", tick_size), trigger_type=OrderType.TAKE_PROFIT
        ),
    )

    flip_px = _px(market_price, "0.99", tick_size)
    await _rest_maker(perp_maker_tester, symbol, is_buy=True, limit_px=flip_px, qty="0.02")
    await taker.orders.create_limit(
        LimitOrderParameters(
            symbol=symbol, is_buy=False, limit_px=flip_px, qty="0.02", time_in_force=TimeInForce.IOC, reduce_only=False
        )
    )
    await _wait_for_position(taker, symbol, Decimal("-0.01"))

    for order_id in (sl_id, tp_id):
        await _await_order(taker, order_id, OrderStatus.CANCELLED, POSITION_EVENT_TIMEOUT, CancelReason.POSITION_CLOSED)


@pytest.mark.asyncio
async def test_sl_fire_cancels_tp(perp_maker_tester: ReyaTester, perp_taker_tester: ReyaTester):
    """A long's crossed SL fires and closes the position; its TP sibling is cancelled OCO_SIBLING_FIRED."""
    taker, symbol = perp_taker_tester, "ETHRUSDPERP"
    _require_exact_source_localnet(taker)
    await _require_flat(taker, symbol)
    market_price, tick_size = await _open_localnet_position(perp_maker_tester, taker, symbol, is_long=True)
    tp_id = await _arm(
        taker,
        _trigger_params(
            symbol, is_buy=False, trigger_px=_px(market_price, "2", tick_size), trigger_type=OrderType.TAKE_PROFIT
        ),
    )
    # A long's SL sells when the mark falls to its trigger: above the mark is already crossed.
    sl_px = _px(market_price, "1.02", tick_size)
    await _rest_maker(perp_maker_tester, symbol, is_buy=True, limit_px=sl_px)
    sl_id = await _arm(taker, _trigger_params(symbol, is_buy=False, trigger_px=sl_px, trigger_type=OrderType.STOP_LOSS))

    await _assert_fired_and_filled(taker, sl_id)
    await _await_order(taker, tp_id, OrderStatus.CANCELLED, FIRE_TIMEOUT, CancelReason.OCO_SIBLING_FIRED)
    await _wait_for_position(taker, symbol, Decimal(0))


@pytest.mark.asyncio
async def test_tp_fire_cancels_sl(perp_maker_tester: ReyaTester, perp_taker_tester: ReyaTester):
    """A long's crossed TP fires and closes the position; its SL sibling is cancelled OCO_SIBLING_FIRED."""
    taker, symbol = perp_taker_tester, "ETHRUSDPERP"
    _require_exact_source_localnet(taker)
    await _require_flat(taker, symbol)
    market_price, tick_size = await _open_localnet_position(perp_maker_tester, taker, symbol, is_long=True)
    sl_id = await _arm(
        taker,
        _trigger_params(
            symbol, is_buy=False, trigger_px=_px(market_price, "0.5", tick_size), trigger_type=OrderType.STOP_LOSS
        ),
    )
    # A long's TP sells when the mark rises to its trigger: below the mark is already crossed.
    tp_px = _px(market_price, "0.98", tick_size)
    await _rest_maker(perp_maker_tester, symbol, is_buy=True, limit_px=tp_px)
    tp_id = await _arm(
        taker, _trigger_params(symbol, is_buy=False, trigger_px=tp_px, trigger_type=OrderType.TAKE_PROFIT)
    )

    await _assert_fired_and_filled(taker, tp_id)
    await _await_order(taker, sl_id, OrderStatus.CANCELLED, FIRE_TIMEOUT, CancelReason.OCO_SIBLING_FIRED)
    await _wait_for_position(taker, symbol, Decimal(0))
