"""Offline coverage for market-level perp execution state routing."""

from types import SimpleNamespace
from typing import Any, cast

from unittest.mock import MagicMock

import pytest

from sdk.async_api.market_perp_execution_update_payload import MarketPerpExecutionUpdatePayload
from sdk.async_api.subscribed_message_payload import SubscribedMessagePayload
from tests.helpers.reya_tester.websocket import WebSocketState

pytestmark = pytest.mark.offline

SYMBOL = "ETHRUSDPERP"
CHANNEL = f"/v2/market/{SYMBOL}/perpExecutions"
EXECUTION = {
    "exchangeId": 2,
    "symbol": SYMBOL,
    "takerAccountId": 42,
    "makerAccountId": 43,
    "takerOrderId": "1001",
    "makerOrderId": "1002",
    "qty": "0.01",
    "side": "B",
    "price": "3000",
    "takerFee": "0.1",
    "type": "ORDER_MATCH",
    "timestamp": 1_700_000_000_000,
    "sequenceNumber": 99,
}


def _state(websocket: Any = None) -> WebSocketState:
    return WebSocketState(cast(Any, SimpleNamespace(websocket=websocket)))


def test_market_perp_subscription_uses_the_public_market_channel() -> None:
    websocket = MagicMock()
    state = _state(websocket)

    state.subscribe_to_market_perp_executions(SYMBOL)

    websocket.market.perp_executions.assert_called_once_with(SYMBOL)
    websocket.market.perp_executions.return_value.subscribe.assert_called_once_with()


def test_market_perp_snapshot_and_updates_stay_out_of_the_wallet_store() -> None:
    state = _state()
    state.on_message(
        None,
        SubscribedMessagePayload.model_validate(
            {"type": "subscribed", "channel": CHANNEL, "contents": {"data": [EXECUTION]}}
        ),
    )
    state.on_message(
        None,
        MarketPerpExecutionUpdatePayload.model_validate(
            {"type": "channel_data", "timestamp": 1_700_000_000_001, "channel": CHANNEL, "data": [EXECUTION]}
        ),
    )

    market_events = state.market_perp_executions[SYMBOL]
    assert len(market_events) == 2
    assert market_events.last is not None
    assert market_events.last.sequence_number == 99
    assert len(state.perp_executions) == 0
