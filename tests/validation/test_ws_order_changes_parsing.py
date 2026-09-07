"""Offline: the `walletOrderChanges` WS path must parse every terminal state
the matching engine publishes.

`ReyaSocket._parse_message` turns a `ValidationError` into a raised
`WebSocketDataError` out of `on_message`, so a read-side enum that lags the
deployed matching engine does not degrade — it breaks the subscriber on the
first frame carrying the unknown member. `OCO_SIBLING_FIRED` publishes on every
stop that fires, which makes that the default case, not the edge case.
"""

from typing import Any

import pytest

from sdk.async_api.cancel_reason import CancelReason
from sdk.async_api.order_change_update_payload import OrderChangeUpdatePayload
from sdk.reya_websocket.config import WebSocketConfig
from sdk.reya_websocket.socket import ReyaSocket

pytestmark = pytest.mark.offline

WALLET = "0x1111111111111111111111111111111111111111"
ORDER_CHANGES_CHANNEL = f"/v2/wallet/{WALLET}/orderChanges"

# The reasons the SL/TP firing contract adds to the read side.
FIRING_CANCEL_REASONS = [
    "OCO_SIBLING_FIRED",
    "POSITION_CLOSED",
    "RISK_REJECTED",
    "PROTECTIVE_SELF_TRADE_SWEEP",
]


def _socket() -> ReyaSocket:
    return ReyaSocket(config=WebSocketConfig(url="wss://example.invalid"))


def _order(**overrides: Any) -> dict[str, Any]:
    """One wire-shaped armed STOP_LOSS, overridable field by field."""
    order: dict[str, Any] = {
        "exchangeId": 1,
        "symbol": "ETHRUSDPERP",
        "accountId": 12345,
        "orderId": "777",
        "sequenceNumber": 42,
        "qty": "1",
        "execQty": "0",
        "cumQty": "0",
        "side": "A",
        "limitPx": "2400",
        "orderType": "STOP_LOSS",
        "triggerPx": "2500",
        "timeInForce": "GTC",
        "status": "OPEN",
        "createdAt": 1747927089000,
        "lastUpdateAt": 1747927089000,
    }
    order.update(overrides)
    return order


def _parse_order_change(order: dict[str, Any]) -> OrderChangeUpdatePayload:
    message = _socket()._parse_message(  # pylint: disable=protected-access
        {
            "type": "channel_data",
            "timestamp": 1_747_927_089_000,
            "channel": ORDER_CHANGES_CHANNEL,
            "data": [order],
        }
    )
    assert isinstance(message, OrderChangeUpdatePayload)
    return message


@pytest.mark.trigger
@pytest.mark.websocket
@pytest.mark.parametrize("reason", FIRING_CANCEL_REASONS)
def test_firing_cancel_reasons_parse_off_the_wire(reason: str) -> None:
    """A subscriber watching its stops must receive the cancellation, not an
    exception out of `on_message`."""
    message = _parse_order_change(_order(status="CANCELLED", cancelReason=reason, cancelReasonMessage="stop cancelled"))

    assert message.data[0].cancel_reason is CancelReason(reason)
    assert message.data[0].cancel_reason_message == "stop cancelled"


@pytest.mark.trigger
@pytest.mark.websocket
@pytest.mark.parametrize("triggered", [False, True], ids=["armed", "fired"])
def test_triggered_round_trips_on_the_ws_order(triggered: bool) -> None:
    """`triggered` is the armed-vs-fired discriminator and both states surface
    as `OPEN`, so dropping it collapses two different orders into one."""
    message = _parse_order_change(_order(triggered=triggered))

    assert message.data[0].triggered is triggered
    assert message.data[0].model_dump(by_alias=True)["triggered"] is triggered


@pytest.mark.trigger
@pytest.mark.websocket
def test_an_omitted_triggered_flag_is_none() -> None:
    """Deployments predating matching-engine trigger firing omit the field; the
    spec tells callers to read that as not-fired rather than as a parse error."""
    order = _order()
    assert "triggered" not in order

    assert _parse_order_change(order).data[0].triggered is None
