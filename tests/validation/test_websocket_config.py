"""Offline tests for chain-aware market-data WebSocket configuration."""

import pytest

from sdk.reya_websocket import config as websocket_config

pytestmark = pytest.mark.offline


def _disable_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(websocket_config, "load_dotenv", lambda: None)


def test_mainnet_websocket_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _disable_dotenv(monkeypatch)
    monkeypatch.setenv("CHAIN_ID", "1729")
    monkeypatch.delenv("REYA_WS_URL", raising=False)

    config = websocket_config.WebSocketConfig.from_env()

    assert config.url == "wss://ws.reya.xyz/"


def test_devnet_websocket_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _disable_dotenv(monkeypatch)
    monkeypatch.setenv("CHAIN_ID", "89346162")
    monkeypatch.delenv("REYA_WS_URL", raising=False)

    config = websocket_config.WebSocketConfig.from_env()

    assert config.url == "wss://websocket-devnet.reya-cronos.network/"


def test_explicit_websocket_url_overrides_chain_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _disable_dotenv(monkeypatch)
    monkeypatch.setenv("CHAIN_ID", "89346162")
    monkeypatch.setenv("REYA_WS_URL", "ws://127.0.0.1:8082")

    config = websocket_config.WebSocketConfig.from_env()

    assert config.url == "ws://127.0.0.1:8082"
