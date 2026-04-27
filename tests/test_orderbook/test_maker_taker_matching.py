"""
Maker/taker matching end-to-end tests parametrized over [spot, perp].

This is the matching-engine smoke for both market types: maker rests a GTC,
taker sends an IOC at a crossing price, both observe the fill via REST and
WebSocket.

Asset-balance accounting is spot-specific (perp markets settle to positions,
not asset balances) and lives in ``tests/test_spot/test_maker_taker_matching.py``;
those assertions (``verify_spot_trade_balance_changes``) need both base and
quote asset deltas, which only make sense on spot.
"""

from __future__ import annotations

from typing import Union

import asyncio

import pytest

from sdk.open_api.models.depth import Depth
from sdk.open_api.models.order_status import OrderStatus
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api.models import LimitOrderParameters
from tests.helpers import ReyaTester
from tests.helpers.reya_tester import logger
from tests.test_orderbook.conftest import PerpTestConfig
from tests.test_spot.spot_config import SpotTestConfig


@pytest.mark.asyncio
async def test_maker_taker_match_e2e(
    market_config: Union[SpotTestConfig, PerpTestConfig],
    market_type: str,
    maker: ReyaTester,
    taker: ReyaTester,
) -> None:
    """Maker GTC + taker IOC at the same price → maker filled, taker IOC consumed.

    Verifies the match end-to-end:

    1. Maker order rests on the book and shows up in REST depth.
    2. Taker IOC at maker's price executes immediately.
    3. Maker order moves out of OPEN.
    4. Order-changes event lands on the maker's wallet WS channel.
    5. Symbol-level execution event lands on the taker's wallet WS channel.
    """
    await market_config.refresh_order_book(maker.data)
    await maker.orders.close_all(fail_if_none=False)
    await taker.orders.close_all(fail_if_none=False)

    if market_config.has_any_external_liquidity:
        pytest.skip("external liquidity present — match could go to an external counterparty")

    cross_px_float = market_config.price(0.99)
    cross_px = str(cross_px_float)

    # Step 1: Maker places GTC buy.
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

    # Step 2: Maker order is visible in L2 depth.
    await asyncio.sleep(0.1)
    depth = await maker.data.market_depth(market_config.symbol)
    assert isinstance(depth, Depth), f"[{market_type}] expected Depth"
    found_in_depth = any(abs(float(bid.px) - cross_px_float) < 0.01 for bid in (depth.bids or []))
    assert found_in_depth, f"[{market_type}] maker order should appear at ${cross_px_float} in L2 depth"
    logger.info(f"[{market_type}] ✅ maker visible in L2 at ${cross_px_float}")

    # Step 3: Taker IOC sell at the same price.
    taker_params = LimitOrderParameters(
        symbol=market_config.symbol,
        is_buy=False,
        limit_px=cross_px,
        qty=market_config.min_qty,
        time_in_force=TimeInForce.IOC,
    )
    taker_order_id = await taker.orders.create_limit(taker_params)
    assert taker_order_id is not None

    # Step 4: Maker order moves out of OPEN (FILLED via the match).
    try:
        await maker.wait.for_order_state(maker_order_id, OrderStatus.FILLED, timeout=10)
        logger.info(f"[{market_type}] ✅ maker FILLED")
    except RuntimeError:
        # Some environments emit only PARTIALLY_FILLED then close — accept any non-OPEN final state.
        open_orders = await maker.client.get_open_orders()
        open_ids = {o.order_id for o in open_orders if o.symbol == market_config.symbol}
        assert maker_order_id not in open_ids, f"[{market_type}] maker must not remain OPEN after match"

    # Step 5: WS order-changes event for maker.
    assert maker_order_id in maker.ws.order_changes, (
        f"[{market_type}] expected maker order in WS order_changes; " f"got: {list(maker.ws.order_changes.keys())[:5]}"
    )

    # Step 6: WS execution event for taker — perp goes via perp_executions, spot via spot_executions.
    if market_type == "spot":
        assert taker.ws.last_spot_execution is not None, "[spot] expected last_spot_execution on taker WS"
        assert taker.ws.last_spot_execution.symbol == market_config.symbol
    else:
        # Perp: confirm at least one perp execution lands within a brief polling window.
        for _ in range(20):
            if taker.ws.perp_executions.find_last(lambda e: e.symbol == market_config.symbol) is not None:
                break
            await asyncio.sleep(0.25)
        last_perp = taker.ws.perp_executions.find_last(lambda e: e.symbol == market_config.symbol)
        assert last_perp is not None, "[perp] expected perp execution on taker WS"

    # Cleanup
    await maker.orders.close_all(fail_if_none=False)
    await taker.orders.close_all(fail_if_none=False)
