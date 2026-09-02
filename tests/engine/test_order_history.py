"""
Order-history end-to-end coverage for the matching-engine orderbook path.

This live devnet1 test expects `/v2/wallet/{address}/orderHistory` to be
deployed on the perpOB API branch.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from decimal import Decimal

import pytest

from sdk.async_api.perp_execution import PerpExecution as AsyncPerpExecution
from sdk.open_api.models.order import Order
from sdk.open_api.models.order_history_list import OrderHistoryList
from sdk.open_api.models.order_status import OrderStatus
from sdk.open_api.models.order_type import OrderType
from sdk.open_api.models.perp_execution import PerpExecution
from sdk.open_api.models.side import Side
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api.models import LimitOrderParameters
from tests.helpers import ReyaTester
from tests.helpers.localnet_fee_v3 import RUSD_SCALE, WAD, configured_localnet_fee_v3, wait_for_indexed_fee_v3_row
from tests.helpers.market_config import PerpTestConfig
from tests.helpers.order_lifecycle import assert_px_qty, wait_for_taker_perp_execution

_REQUIRED_ORDER_HISTORY_E2E_ENV = (
    "PERP_ACCOUNT_ID_1",
    "PERP_PRIVATE_KEY_1",
    "PERP_WALLET_ADDRESS_1",
    "PERP_ACCOUNT_ID_2",
    "PERP_PRIVATE_KEY_2",
    "PERP_WALLET_ADDRESS_2",
)

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.perp,
    pytest.mark.skipif(
        not all(os.environ.get(name) for name in _REQUIRED_ORDER_HISTORY_E2E_ENV),
        reason="orderHistory E2E requires configured live perp maker/taker accounts",
    ),
]


_FEE_V3_COMPONENT_FIELDS = ("protocol_fee_credit", "referrer_fee_credit", "taker_rebate_credit", "pool_fee_credit")


def _fee_v3_breakdown(execution: PerpExecution | AsyncPerpExecution) -> dict[str, Decimal]:
    """The four public Fee v3 components as Decimals (PRO-853). A Fee v3 fill
    exposes every one of them; the API never synthesizes a partial set."""
    breakdown: dict[str, Decimal] = {}
    for field in _FEE_V3_COMPONENT_FIELDS:
        value = getattr(execution, field)
        assert value is not None, f"Fee v3 execution must expose {field}"
        breakdown[field] = Decimal(value)
    return breakdown


async def _wait_for_ws_perp_execution(
    tester: ReyaTester,
    sequence_number: int,
    timeout_s: float = 15.0,
) -> AsyncPerpExecution:
    """The wallet `perpExecutions` WS event for one settled fill, by sequence number."""
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        match = tester.ws.perp_executions.find_last(lambda e: e.sequence_number == sequence_number)
        if match is not None:
            return match
        await asyncio.sleep(0.2)
    raise AssertionError(f"No WS perp execution with sequenceNumber={sequence_number} within {timeout_s}s")


async def _wait_for_history_order(
    tester: ReyaTester,
    order_id: str,
    predicate: Callable[[Order], bool],
    timeout_s: float = 15.0,
) -> Order:
    deadline = asyncio.get_running_loop().time() + timeout_s
    last_history: OrderHistoryList | None = None

    while asyncio.get_running_loop().time() < deadline:
        last_history = await tester.client.get_order_history()
        for order in last_history.data:
            if order.order_id == order_id and predicate(order):
                return order
        await asyncio.sleep(0.5)

    seen_ids = [order.order_id for order in (last_history.data if last_history else [])[:10]]
    raise AssertionError(f"order {order_id} not found in orderHistory; first seen ids: {seen_ids}")


async def _assert_time_window_refetch_contains_order(tester: ReyaTester, expected_order: Order) -> None:
    assert expected_order.sequence_number is not None

    history = await tester.client.get_order_history(
        start_time=expected_order.last_update_at,
        end_time=expected_order.last_update_at,
    )

    sequence_numbers = {order.sequence_number for order in history.data}
    assert expected_order.sequence_number in sequence_numbers
    assert history.meta.count == len(history.data)
    assert history.meta.start_time == expected_order.last_update_at
    assert history.meta.end_time == expected_order.last_update_at
    assert all(order.last_update_at == expected_order.last_update_at for order in history.data)


def _assert_filled_order_projection(
    order: Order,
    *,
    account_id: int,
    symbol: str,
    side: Side,
    limit_px: str,
    qty: str,
) -> None:
    assert order.exchange_id >= 0
    assert order.account_id == account_id
    assert order.symbol == symbol
    assert order.side == side
    assert_px_qty(order, limit_px, qty)
    assert order.order_type == OrderType.LIMIT
    assert order.time_in_force in (TimeInForce.GTC, TimeInForce.IOC)
    assert order.status == OrderStatus.FILLED
    assert order.created_at > 0
    assert order.last_update_at >= order.created_at
    assert order.sequence_number is not None
    assert order.sequence_number >= 0
    assert order.first_fill_id is not None
    assert int(order.first_fill_id) > 0
    assert order.fill_count is not None
    assert order.fill_count >= 1


@pytest.mark.asyncio
async def test_perp_order_history_records_maker_and_taker_fill_e2e(
    perp_market_config: PerpTestConfig,
    perp_maker_tester: ReyaTester,
    perp_taker_tester: ReyaTester,
) -> None:
    """Crossing maker/taker GTC fill should appear in wallet orderHistory."""
    market_config = perp_market_config
    maker = perp_maker_tester
    taker = perp_taker_tester

    await market_config.refresh_order_book(maker.data)
    await maker.orders.close_all(fail_if_none=False)
    await taker.orders.close_all(fail_if_none=False)

    if market_config.has_any_external_liquidity:
        pytest.skip("external liquidity present — orderHistory assertions require a controlled maker/taker fill")

    cross_px = str(market_config.price(0.99))
    qty = market_config.min_qty
    assert taker.owner_wallet_address is not None

    with configured_localnet_fee_v3(
        taker_owner=taker.owner_wallet_address,
        pool_account_id=maker.account_id,
    ) as fee_v3_scenario:
        maker_order_id = await maker.orders.create_limit(
            LimitOrderParameters(
                symbol=market_config.symbol,
                is_buy=True,
                limit_px=cross_px,
                qty=qty,
                time_in_force=TimeInForce.GTC,
            )
        )
        assert maker_order_id is not None
        await maker.wait.for_order_creation(maker_order_id)

        taker_response = await taker.client.create_limit_order(
            LimitOrderParameters(
                symbol=market_config.symbol,
                is_buy=False,
                limit_px=cross_px,
                qty=qty,
                time_in_force=TimeInForce.GTC,
            )
        )
        taker_order_id = taker_response.order_id
        assert taker_order_id is not None

        maker_history_order = await _wait_for_history_order(
            maker,
            maker_order_id,
            lambda order: order.status == OrderStatus.FILLED and order.first_fill_id is not None,
        )
        taker_history_order = await _wait_for_history_order(
            taker,
            taker_order_id,
            lambda order: order.status == OrderStatus.FILLED and order.first_fill_id is not None,
        )

        _assert_filled_order_projection(
            maker_history_order,
            account_id=maker.account_id,
            symbol=market_config.symbol,
            side=Side.B,
            limit_px=cross_px,
            qty=qty,
        )
        _assert_filled_order_projection(
            taker_history_order,
            account_id=taker.account_id,
            symbol=market_config.symbol,
            side=Side.A,
            limit_px=cross_px,
            qty=qty,
        )

        assert maker_history_order.fill_count == 1, "maker should map to one fill"
        assert taker_history_order.fill_count == 1, "single-level taker should map to one fill"

        execution = await wait_for_taker_perp_execution(taker, taker_order_id, timeout_s=15.0)
        assert execution.fill_id == taker_history_order.first_fill_id
        assert execution.fill_id == maker_history_order.first_fill_id
        assert execution.maker_fee is None, "fee-model-v3 executions must not project the legacy makerFee field"

        # PRO-853: the public REST execution decomposes takerFee into its four
        # settlement buckets, and takerFee is exactly their sum.
        rest_breakdown = _fee_v3_breakdown(execution)
        assert sum(rest_breakdown.values()) == Decimal(execution.taker_fee)

        # The wallet WS event for the same fill must carry identical values.
        ws_execution = await _wait_for_ws_perp_execution(taker, execution.sequence_number)
        assert ws_execution.fill_id == execution.fill_id
        assert ws_execution.taker_fee == execution.taker_fee
        assert ws_execution.maker_fee is None
        assert _fee_v3_breakdown(ws_execution) == rest_breakdown
        for field in _FEE_V3_COMPONENT_FIELDS:
            assert getattr(ws_execution, field) == getattr(execution, field), field

        if fee_v3_scenario is not None:
            assert execution.fill_id is not None
            indexed = wait_for_indexed_fee_v3_row(execution.fill_id)
            assert indexed.account_id == taker.account_id
            assert indexed.counterparty_account_id == maker.account_id
            assert indexed.fee > 0
            assert indexed.referrer_fee_credit == 0

            expected_taker_rebate = indexed.fee * fee_v3_scenario.taker_rebate_rate // WAD
            remaining_after_taker = indexed.fee - expected_taker_rebate
            expected_pool_credit = remaining_after_taker * fee_v3_scenario.pool_rebate_rate // WAD
            expected_protocol_credit = remaining_after_taker - expected_pool_credit

            assert indexed.taker_rebate_credit == expected_taker_rebate
            assert indexed.pool_fee_credit == expected_pool_credit
            assert indexed.protocol_fee_credit == expected_protocol_credit
            assert indexed.fee == (
                indexed.protocol_fee_credit
                + indexed.referrer_fee_credit
                + indexed.taker_rebate_credit
                + indexed.pool_fee_credit
            )
            assert Decimal(execution.taker_fee) == Decimal(indexed.fee) / Decimal(RUSD_SCALE)
            # The public breakdown mirrors the persisted buckets exactly.
            assert rest_breakdown == {
                "protocol_fee_credit": Decimal(indexed.protocol_fee_credit) / Decimal(RUSD_SCALE),
                "referrer_fee_credit": Decimal(indexed.referrer_fee_credit) / Decimal(RUSD_SCALE),
                "taker_rebate_credit": Decimal(indexed.taker_rebate_credit) / Decimal(RUSD_SCALE),
                "pool_fee_credit": Decimal(indexed.pool_fee_credit) / Decimal(RUSD_SCALE),
            }
            assert indexed.exchange_fee_credit is None
            assert indexed.maker_fee_credit is None
            assert indexed.maker_fee_debit is None
            assert indexed.transaction_hash.startswith("0x")

    await _assert_time_window_refetch_contains_order(maker, maker_history_order)
    await _assert_time_window_refetch_contains_order(taker, taker_history_order)
