"""Offline tests for WebSocket reconnect and subscription recovery."""

# pylint: disable=protected-access

from __future__ import annotations

from typing import Any

import json
import logging
import threading

import pytest

from sdk.reya_websocket.config import WebSocketConfig
from sdk.reya_websocket.socket import ReyaSocket

pytestmark = pytest.mark.offline


def _config(*, reconnect_attempts: int = 3, reconnect_delay: int = 0) -> WebSocketConfig:
    return WebSocketConfig(
        url="wss://example.invalid",
        reconnect_attempts=reconnect_attempts,
        reconnect_delay=reconnect_delay,
    )


def test_reconnect_replays_the_full_subscription_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[dict[str, Any]] = []
    open_count = 0
    run_count = 0

    def on_open(_ws: Any) -> None:
        nonlocal open_count
        open_count += 1
        if open_count == 1:
            socket.send_subscribe(
                "/v2/wallet/0xabc/orderChanges",
                batched=True,
                snapshot={"depth": 25},
            )

    socket = ReyaSocket(config=_config(), on_open=on_open)
    monkeypatch.setattr(socket, "send", lambda payload: sent.append(json.loads(payload)))

    def run_forever(**_kwargs: Any) -> None:
        nonlocal run_count
        run_count += 1
        socket._handle_open(socket)
        if run_count == 2:
            socket.close()

    monkeypatch.setattr(socket, "run_forever", run_forever)

    socket.connect(blocking=True)

    expected = {
        "type": "subscribe",
        "channel": "/v2/wallet/0xabc/orderChanges",
        "batched": True,
        "snapshot": {"depth": 25},
    }
    assert sent == [expected, expected]
    assert open_count == 2
    assert run_count == 2


def test_reconnect_callback_subscription_is_not_duplicated(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[dict[str, Any]] = []
    open_count = 0
    run_count = 0

    def on_open(_ws: Any) -> None:
        nonlocal open_count
        open_count += 1
        socket.send_subscribe("/v2/assetOraclePrices", batched=open_count == 2)

    socket = ReyaSocket(config=_config(), on_open=on_open)
    monkeypatch.setattr(socket, "send", lambda payload: sent.append(json.loads(payload)))

    def run_forever(**_kwargs: Any) -> None:
        nonlocal run_count
        run_count += 1
        socket._handle_open(socket)
        if run_count == 2:
            socket.close()

    monkeypatch.setattr(socket, "run_forever", run_forever)

    socket.connect(blocking=True)

    assert sent == [
        {
            "type": "subscribe",
            "channel": "/v2/assetOraclePrices",
            "batched": False,
        },
        {
            "type": "subscribe",
            "channel": "/v2/assetOraclePrices",
            "batched": True,
        },
    ]


def test_unsubscribed_channel_is_not_restored(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[dict[str, Any]] = []
    open_count = 0
    run_count = 0

    def on_open(_ws: Any) -> None:
        nonlocal open_count
        open_count += 1
        if open_count == 1:
            socket.send_subscribe("/v2/perpMarkets/summary", batched=True)

    socket = ReyaSocket(config=_config(), on_open=on_open)
    monkeypatch.setattr(socket, "send", lambda payload: sent.append(json.loads(payload)))

    def run_forever(**_kwargs: Any) -> None:
        nonlocal run_count
        run_count += 1
        socket._handle_open(socket)
        if run_count == 1:
            socket.send_unsubscribe("/v2/perpMarkets/summary")
        else:
            socket.close()

    monkeypatch.setattr(socket, "run_forever", run_forever)

    socket.connect(blocking=True)

    assert sent == [
        {
            "type": "subscribe",
            "channel": "/v2/perpMarkets/summary",
            "batched": True,
        },
        {
            "type": "unsubscribe",
            "channel": "/v2/perpMarkets/summary",
        },
    ]
    assert socket.active_subscriptions == set()


def test_failed_subscribe_send_is_replayed_after_reconnect(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[dict[str, Any]] = []
    channel = "/v2/market/ETHRUSDPERP/perpExecutions"
    socket = ReyaSocket(config=_config(), on_open=lambda _ws: None)

    def fail_send(_payload: str) -> None:
        raise OSError("connection unavailable")

    monkeypatch.setattr(socket, "send", fail_send)
    with pytest.raises(OSError, match="connection unavailable"):
        socket.send_subscribe(channel, batched=True)

    assert channel in socket.active_subscriptions
    assert channel not in socket._sent_subscriptions_this_connection

    monkeypatch.setattr(socket, "send", lambda payload: sent.append(json.loads(payload)))
    socket._has_connected_once = True
    socket._handle_open(socket)

    assert sent == [{"type": "subscribe", "channel": channel, "batched": True}]


def test_reconnect_uses_bounded_exponential_backoff(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    run_count = 0
    delays: list[float] = []
    socket = ReyaSocket(config=_config(reconnect_attempts=3, reconnect_delay=2))

    def run_forever(**_kwargs: Any) -> None:
        nonlocal run_count
        run_count += 1

    def record_wait(delay: float) -> bool:
        delays.append(delay)
        return False

    monkeypatch.setattr(socket, "run_forever", run_forever)
    monkeypatch.setattr(socket._stop_event, "wait", record_wait)

    with caplog.at_level(logging.ERROR, logger="reya.websocket"):
        socket.connect(blocking=True)

    assert run_count == 4
    assert delays == [2, 4, 8]
    assert "reconnect attempts exhausted after 3 attempt(s)" in caplog.text


def test_close_interrupts_reconnect_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    first_run_finished = threading.Event()
    socket = ReyaSocket(config=_config(reconnect_delay=60))

    def run_forever(**_kwargs: Any) -> None:
        first_run_finished.set()

    monkeypatch.setattr(socket, "run_forever", run_forever)

    socket.connect()
    assert first_run_finished.wait(timeout=1)

    socket.close()
    assert socket._thread is not None
    socket._thread.join(timeout=1)
    assert not socket._thread.is_alive()
