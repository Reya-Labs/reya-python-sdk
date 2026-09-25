"""Transfer subscriptions must deliver typed live callbacks without a network."""

import json
from unittest.mock import Mock

import pytest

from sdk.async_api.subscribed_message_payload import SubscribedMessagePayload
from sdk.async_api.transfer_type import TransferType
from sdk.async_api.wallet_transfer_update_payload import WalletTransferUpdatePayload
from sdk.reya_websocket.config import WebSocketConfig
from sdk.reya_websocket.socket import ReyaSocket, WebSocketDataError

pytestmark = pytest.mark.offline

ADDRESS = "0x" + "11" * 20
CHANNEL = f"/v2/wallet/{ADDRESS}/transfers"


def transfer(kind):
    return {
        "sequenceNumber": 2,
        "accountId": 1,
        "asset": "RUSD",
        "amount": "-1",
        "netDepositsAfter": "9",
        "type": kind,
        "timestamp": 1000,
        "transactionHash": "0x" + "ab" * 32,
    }


def test_transfer_resource_subscribes_and_unsubscribes(monkeypatch):
    socket = ReyaSocket(config=WebSocketConfig(url="wss://example.invalid"))
    send = Mock()
    monkeypatch.setattr(socket, "send", send)
    subscription = socket.wallet.transfers(ADDRESS)
    subscription.subscribe()
    assert json.loads(send.call_args.args[0]) == {"type": "subscribe", "channel": CHANNEL}
    assert CHANNEL in socket.active_subscriptions
    subscription.unsubscribe()
    assert json.loads(send.call_args.args[0]) == {"type": "unsubscribe", "channel": CHANNEL}
    assert CHANNEL not in socket.active_subscriptions


@pytest.mark.parametrize("kind", ["PERP_TAKER_FEE", "FUTURE_TRANSFER_TYPE"])
def test_live_transfer_reaches_typed_callback(kind):
    callback = Mock()
    socket = ReyaSocket(config=WebSocketConfig(url="wss://example.invalid"), on_message=callback)
    row = transfer(kind)
    assert socket.on_message is not None
    socket.on_message(socket, json.dumps({"type": "subscribed", "channel": CHANNEL, "contents": {"data": [row]}}))
    snapshot = callback.call_args.args[1]
    assert isinstance(snapshot, SubscribedMessagePayload)
    assert snapshot.contents == {"data": [row]}
    socket.on_message(
        socket,
        json.dumps({"type": "channel_data", "timestamp": 1000, "channel": CHANNEL, "data": [row]}),
    )
    update = callback.call_args.args[1]
    assert isinstance(update, WalletTransferUpdatePayload)
    assert len(update.data) == 1
    entry = update.data[0]
    assert entry.type is (TransferType.PERP_TAKER_FEE if kind == "PERP_TAKER_FEE" else TransferType.UNKNOWN)
    assert entry.sequence_number == 2
    assert entry.amount == "-1"
    assert entry.net_deposits_after == "9"
    assert callback.call_count == 2


def test_unknown_wallet_channel_still_fails():
    socket = ReyaSocket(config=WebSocketConfig(url="wss://example.invalid"))
    assert socket.on_message is not None
    with pytest.raises(WebSocketDataError, match="Unknown channel"):
        socket.on_message(
            socket,
            json.dumps({"type": "channel_data", "timestamp": 1000, "channel": CHANNEL + "Bogus", "data": []}),
        )
