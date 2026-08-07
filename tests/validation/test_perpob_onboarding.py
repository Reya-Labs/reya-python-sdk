"""Offline regression tests for the Perp OB onboarding surfaces."""

from typing import Any, cast

import importlib

import pytest
from pydantic import ValidationError

from sdk.async_api.account_update_data import AccountUpdateData
from sdk.async_api.account_update_payload import AccountUpdatePayload
from sdk.open_api.api.specs_api import SpecsApi
from sdk.open_api.configuration import Configuration
from sdk.open_api.models import TimeInForce
from sdk.reya_websocket.config import WebSocketConfig
from sdk.reya_websocket.resources.wallet import WalletResource
from sdk.reya_websocket.socket import ReyaSocket

pytestmark = pytest.mark.offline

_EXAMPLE = importlib.import_module("examples.websocket.exec.ws_exec")


class RecordingSocket:
    """Small socket double that records subscription frames."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def send_subscribe(self, channel: str, **kwargs: Any) -> None:
        self.calls.append(("subscribe", channel, kwargs))

    def send_unsubscribe(self, channel: str, **kwargs: Any) -> None:
        self.calls.append(("unsubscribe", channel, kwargs))


def test_ws_exec_example_defaults_to_current_devnet(monkeypatch) -> None:
    monkeypatch.delenv("REYA_WS_EXEC_URL", raising=False)

    assert _EXAMPLE.resolve_ws_exec_url() == _EXAMPLE.DEFAULT_WS_EXEC_URL
    assert _EXAMPLE.DEFAULT_WS_EXEC_URL == "wss://ws-exec-devnet.reya-cronos.network"


def test_ws_exec_example_accepts_environment_override(monkeypatch) -> None:
    monkeypatch.setenv("REYA_WS_EXEC_URL", "wss://ws-exec-staging.reya.xyz")

    assert _EXAMPLE.resolve_ws_exec_url() == "wss://ws-exec-staging.reya.xyz"


def test_ws_exec_example_order_builds_offline() -> None:
    order = _EXAMPLE.build_example_order()

    assert order.symbol == "WETHRUSD"
    assert order.limit_px == "1"
    assert order.qty == "0.001"
    assert order.time_in_force == TimeInForce.GTC
    assert order.reduce_only is None


def test_generated_rest_client_exposes_exec_asyncapi_spec() -> None:
    assert hasattr(SpecsApi, "get_async_exec_api_spec")


def test_generated_rest_client_exposes_devnet_server() -> None:
    hosts = Configuration().get_host_settings()

    assert any(host["url"] == "https://api-devnet.reya-cronos.network/v2" for host in hosts)


def test_wallet_accounts_subscription_uses_canonical_channel() -> None:
    socket = RecordingSocket()
    subscription = WalletResource(cast(Any, socket)).accounts("0xabc")

    assert subscription.path == "/v2/wallet/0xabc/accounts"
    subscription.subscribe()
    subscription.unsubscribe()

    assert socket.calls == [
        ("subscribe", "/v2/wallet/0xabc/accounts", {}),
        ("unsubscribe", "/v2/wallet/0xabc/accounts", {}),
    ]


def test_wallet_account_update_is_typed() -> None:
    socket = ReyaSocket(config=WebSocketConfig(url="wss://example.invalid"))
    channel = "/v2/wallet/0x1111111111111111111111111111111111111111/accounts"

    assert socket._get_payload_type(channel) is AccountUpdatePayload  # pylint: disable=protected-access

    message = socket._parse_message(  # pylint: disable=protected-access
        {
            "type": "channel_data",
            "timestamp": 1_753_100_000_000,
            "channel": channel,
            "data": [
                {
                    "accountId": "123",
                    "owner": "0x1111111111111111111111111111111111111111",
                    "mainAccountId": "123",
                    "spotAccountId": None,
                    "isMainPerpAccount": True,
                    "isSpotAccount": False,
                }
            ],
        }
    )

    assert isinstance(message, AccountUpdatePayload)
    assert message.data[0].account_id == "123"
    assert message.data[0].is_main_perp_account is True


def account_update_data() -> dict[str, Any]:
    """Return one schema-valid account update for validation regressions."""
    return {
        "accountId": "123",
        "owner": "0x1111111111111111111111111111111111111111",
        "mainAccountId": None,
        "spotAccountId": None,
        "isMainPerpAccount": False,
        "isSpotAccount": False,
    }


@pytest.mark.parametrize("field", ["mainAccountId", "spotAccountId"])
def test_wallet_account_update_requires_nullable_fields(field: str) -> None:
    data = account_update_data()
    data.pop(field)

    with pytest.raises(ValidationError):
        AccountUpdateData.model_validate(data)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("accountId", "not-decimal"),
        ("owner", "0x1234"),
        ("mainAccountId", "not-decimal"),
        ("spotAccountId", "not-decimal"),
    ],
)
def test_wallet_account_update_enforces_patterns(field: str, value: str) -> None:
    data = account_update_data()
    data[field] = value

    with pytest.raises(ValidationError):
        AccountUpdateData.model_validate(data)


def test_wallet_account_update_rejects_unspecified_properties() -> None:
    data = account_update_data()
    data["unexpected"] = True

    with pytest.raises(ValidationError):
        AccountUpdateData.model_validate(data)


def test_wallet_account_update_payload_rejects_unspecified_properties() -> None:
    with pytest.raises(ValidationError):
        AccountUpdatePayload.model_validate(
            {
                "type": "channel_data",
                "timestamp": 1_753_100_000_000,
                "channel": "/v2/wallet/0x1111111111111111111111111111111111111111/accounts",
                "data": [account_update_data()],
                "unexpected": True,
            }
        )
