"""
Smoke tests for the unified ``executionBusts`` endpoints (REST + WS).

Under v2.3.0 spot and perp share one bust surface. These tests exercise the
endpoint shape and the WebSocket subscription handshake — they don't deliberately
trigger busts (those scenarios were spot-specific in the AMM era and need to be
re-derived for the OB matching engine; tracked as follow-up).
"""

from __future__ import annotations

import asyncio

import pytest

from sdk.open_api.models.execution_bust import ExecutionBust
from sdk.open_api.models.execution_bust_list import ExecutionBustList
from tests.helpers import ReyaTester
from tests.helpers.market_config import PerpTestConfig, SpotTestConfig


@pytest.mark.asyncio
async def test_wallet_execution_busts_endpoint_shape(reya_tester: ReyaTester) -> None:
    """``GET /v2/wallet/{address}/executionBusts`` returns a well-formed list."""
    bust_list = await reya_tester.client.get_execution_busts()

    assert isinstance(bust_list, ExecutionBustList)
    assert isinstance(bust_list.data, list)
    for bust in bust_list.data:
        assert isinstance(bust, ExecutionBust)
        assert bust.symbol, "bust.symbol must be populated"
        assert bust.taker_account_id is not None
        assert bust.exchange_id is not None


@pytest.mark.asyncio
async def test_market_execution_busts_endpoint_shape(
    reya_tester: ReyaTester,
    market_config: SpotTestConfig | PerpTestConfig,
    market_type: str,
) -> None:
    """``GET /v2/market/{symbol}/executionBusts`` returns a well-formed list for both market types.

    The symbol comes from the env-resolved ``market_config`` (not a hardcoded
    literal): a hardcoded "ETHRUSD" silently skipped the spot leg on every
    devnet run because deployed spot symbols are W-prefixed (WETHRUSD).
    """
    symbol = market_config.symbol
    bust_list = await reya_tester.client.markets.get_market_execution_busts(symbol=symbol)
    assert isinstance(bust_list, ExecutionBustList)
    for bust in bust_list.data:
        assert isinstance(bust, ExecutionBust)
        assert bust.symbol == symbol, f"[{market_type}] bust symbol mismatch"


@pytest.mark.asyncio
async def test_wallet_execution_busts_websocket_subscribes(reya_tester: ReyaTester) -> None:
    """The wallet ``executionBusts`` WS subscription is established on session start.

    ``ReyaTester.setup`` already subscribes via ``ws.wallet.execution_busts(addr).subscribe()``;
    this test just verifies the channel is in the active set without forcing a bust event.
    """
    websocket = reya_tester.websocket
    if websocket is None:
        pytest.skip("WebSocket not connected")
    assert websocket is not None  # narrow after the skip above

    expected_channel = f"/v2/wallet/{reya_tester.owner_wallet_address}/executionBusts"

    # Give the subscribe message a brief moment to register if a fresh ws is in flight.
    for _ in range(5):
        if expected_channel in websocket.active_subscriptions:
            return
        await asyncio.sleep(0.1)

    assert expected_channel in websocket.active_subscriptions, (
        f"Expected {expected_channel} in active subscriptions, got " f"{sorted(websocket.active_subscriptions)}"
    )
