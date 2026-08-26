"""Market-level perp execution WebSocket coverage."""

import asyncio
from decimal import Decimal

import pytest

from sdk.open_api.models.order_status import OrderStatus
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api.models import LimitOrderParameters
from tests.helpers import ReyaTester
from tests.helpers.market_config import PerpTestConfig


@pytest.mark.perp
@pytest.mark.websocket
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_ws_market_perp_executions_realtime(
    perp_market_config: PerpTestConfig,
    perp_maker_tester: ReyaTester,
    perp_taker_tester: ReyaTester,
) -> None:
    """A crossing trade emits a new event on the public market channel."""
    symbol = perp_market_config.symbol
    if symbol != "ETHRUSDPERP":
        pytest.skip("the shared perp position guard currently restores ETHRUSDPERP only")

    await perp_maker_tester.orders.close_all(fail_if_none=False)
    await perp_taker_tester.orders.close_all(fail_if_none=False)

    perp_taker_tester.ws.clear_market_perp_executions(symbol)
    perp_taker_tester.ws.subscribe_to_market_perp_executions(symbol)
    await asyncio.sleep(0.3)

    store = perp_taker_tester.ws.market_perp_executions.get(symbol)
    baseline_sequence = max((event.sequence_number for event in store), default=0) if store else 0

    await perp_market_config.refresh_order_book(perp_taker_tester.data)
    if perp_market_config.has_any_external_liquidity:
        maker_price = perp_market_config.maker_bid_above_external_bid()
        if maker_price is None:
            pytest.skip("the external spread has no tick available for an isolated maker bid")
    else:
        maker_price = perp_market_config.limit_price("0.99")

    if not perp_market_config.circuit_breaker_floor <= maker_price <= perp_market_config.circuit_breaker_ceiling:
        pytest.skip("the isolated maker bid would fall outside the market circuit breaker")

    crossing_price = str(maker_price)
    maker_order_id = None
    try:
        maker_order_id = await perp_maker_tester.orders.create_limit(
            LimitOrderParameters(
                symbol=symbol,
                is_buy=True,
                limit_px=crossing_price,
                qty=perp_market_config.min_qty,
                time_in_force=TimeInForce.GTC,
            )
        )
        assert maker_order_id is not None
        await perp_maker_tester.wait.for_order_creation(maker_order_id)
        await asyncio.sleep(0.1)
        depth = await perp_maker_tester.data.market_depth(symbol)
        best_bid = Decimal(depth.bids[0].px) if depth and depth.bids else None
        assert best_bid == maker_price, "the guarded maker order must be best bid before the taker crosses"

        taker_order_id = await perp_taker_tester.orders.create_limit(
            LimitOrderParameters(
                symbol=symbol,
                is_buy=False,
                limit_px=crossing_price,
                qty=perp_market_config.min_qty,
                time_in_force=TimeInForce.IOC,
            )
        )
        assert taker_order_id is not None
        await perp_maker_tester.wait.for_order_state(maker_order_id, OrderStatus.FILLED, timeout=10)

        market_event = None
        for _ in range(40):
            store = perp_taker_tester.ws.market_perp_executions.get(symbol)
            if store is not None:
                market_event = store.find_last(
                    lambda event: event.sequence_number > baseline_sequence and event.taker_order_id == taker_order_id
                )
            if market_event is not None:
                break
            await asyncio.sleep(0.25)

        assert market_event is not None, f"expected a new market perp execution event for {symbol}"
        assert market_event.symbol == symbol
        assert market_event.sequence_number > baseline_sequence
    finally:
        await perp_maker_tester.orders.close_all(fail_if_none=False)
        await perp_taker_tester.orders.close_all(fail_if_none=False)
