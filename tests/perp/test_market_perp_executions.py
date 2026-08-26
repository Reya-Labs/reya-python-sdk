"""Market-level perp execution WebSocket coverage."""

import asyncio

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
    usable_bid = perp_market_config.get_usable_bid_price_for_qty(perp_market_config.min_qty)
    usable_ask = perp_market_config.get_usable_ask_price_for_qty(perp_market_config.min_qty)
    if usable_bid is not None or usable_ask is not None:
        pytest.skip("external liquidity is present, so the guarded test accounts cannot cross deterministically")

    crossing_price = str(perp_market_config.price(0.99))
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
