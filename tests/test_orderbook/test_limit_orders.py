"""
Shared limit-order lifecycle tests parametrized over [spot, perp].

Under v2.3.0 both market types route through the same matching engine, so a
single test body verifies the place→fill→position-or-balance flow for both.
Spot-only behaviours (auto-exchange busts) live in tests/test_spot/; perp-only
behaviours (triggers, funding, positions) live in tests/test_perps/.
"""

from __future__ import annotations

from typing import Union

import pytest

from sdk.open_api.models.order_status import OrderStatus
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api.models import LimitOrderParameters
from tests.helpers import ReyaTester
from tests.test_orderbook.conftest import PerpTestConfig
from tests.test_spot.spot_config import SpotTestConfig


@pytest.mark.asyncio
async def test_gtc_place_and_cancel(
    market_config: Union[SpotTestConfig, PerpTestConfig],
    market_type: str,
    maker_tester: ReyaTester,
) -> None:
    """A GTC limit order placed far from market is reachable via REST + cancellable."""
    safe_buy_px = str(round(market_config.oracle_price * 0.5, 2))

    params = LimitOrderParameters(
        symbol=market_config.symbol,
        is_buy=True,
        limit_px=safe_buy_px,
        qty=market_config.min_qty,
        time_in_force=TimeInForce.GTC,
    )
    response = await maker_tester.client.create_limit_order(params)
    assert response.order_id is not None, f"[{market_type}] no order_id in response"

    open_order = await maker_tester.data.open_order(response.order_id)
    assert open_order is not None, f"[{market_type}] order not visible via REST after placement"
    assert open_order.status == OrderStatus.OPEN

    cancel_response = await maker_tester.client.cancel_order(
        symbol=market_config.symbol,
        account_id=maker_tester.account_id,
        order_id=response.order_id,
    )
    assert cancel_response is not None

    await maker_tester.wait.for_order_state(response.order_id, OrderStatus.CANCELLED)


@pytest.mark.asyncio
async def test_mass_cancel_clears_open_orders(
    market_config: Union[SpotTestConfig, PerpTestConfig],
    market_type: str,
    maker_tester: ReyaTester,
) -> None:
    """Mass-cancel removes all open orders on a symbol (works on both spot and perp under v2.3.0)."""
    safe_buy_px = str(round(market_config.oracle_price * 0.5, 2))

    placed_ids = []
    for _ in range(2):
        params = LimitOrderParameters(
            symbol=market_config.symbol,
            is_buy=True,
            limit_px=safe_buy_px,
            qty=market_config.min_qty,
            time_in_force=TimeInForce.GTC,
        )
        response = await maker_tester.client.create_limit_order(params)
        assert response.order_id is not None
        placed_ids.append(response.order_id)

    await maker_tester.client.mass_cancel(
        symbol=market_config.symbol,
        account_id=maker_tester.account_id,
    )

    for order_id in placed_ids:
        await maker_tester.wait.for_order_state(order_id, OrderStatus.CANCELLED)
