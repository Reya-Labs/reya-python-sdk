"""
Order-history end-to-end coverage for the matching-engine orderbook path.

This live devnet1 test expects `/v2/wallet/{address}/orderHistory` to be
deployed on the perpOB API branch.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable

import pytest

from sdk.open_api.models.order import Order
from sdk.open_api.models.order_history_list import OrderHistoryList
from sdk.open_api.models.order_status import OrderStatus
from sdk.open_api.models.order_type import OrderType
from sdk.open_api.models.side import Side
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api.models import LimitOrderParameters
from tests.helpers import ReyaTester
from tests.helpers.market_config import PerpTestConfig
from tests.helpers.order_lifecycle import assert_px_qty

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

    await _assert_time_window_refetch_contains_order(maker, maker_history_order)
    await _assert_time_window_refetch_contains_order(taker, taker_history_order)
