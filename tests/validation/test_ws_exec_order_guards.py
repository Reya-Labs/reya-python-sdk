# pylint: disable=protected-access,redefined-outer-name
"""The ws-exec order surface refuses a non-bool ``is_buy`` before it signs.

Offline (no devnet, no socket): ``ReyaWsExecClient`` composes
``ReyaTradingClient``'s payload builders, so the guard lives there and both
transports inherit it.

Why ws-exec specifically: a non-empty string is truthy in Python, so
``is_buy="false"`` signed the +sentinel (buy side) while the ws-exec wire
serialized ``isBuy: false``. REST caught the mismatch through the generated
model's ``StrictBool``, but only after the builder had already claimed the
nonce; ws-exec has no such model on the request path and sent the pair, leaving
the venue to report a signature mismatch on an order the caller meant to place.

The client is never connected, so a build that got as far as the transport
would raise ``WsExecProtocolError("NOT_CONNECTED")`` instead — which is what
makes these assertions evidence that the refusal precedes the send.
"""

from __future__ import annotations

from typing import Any

import pytest

from sdk.open_api.models.order_type import OrderType
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api import ReyaTradingClient
from sdk.reya_rest_api.config import TradingConfig
from sdk.reya_rest_api.models.orders import LimitOrderParameters, ModifyOrderParameters, TriggerOrderParameters
from sdk.reya_ws_exec import ReyaWsExecClient, WsExecProtocolError

pytestmark = pytest.mark.offline

PRIVATE_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
SIGNER_ADDRESS = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
CHAIN_ID = 89346162
PERP_SYMBOL = "ETHRUSDPERP"
PINNED_DEADLINE = 1745000300


@pytest.fixture
def ws_exec_client() -> ReyaWsExecClient:
    """A ws-exec client over an offline REST client, deliberately unconnected."""
    config = TradingConfig(
        api_url="https://invalid.example",
        chain_id=CHAIN_ID,
        owner_wallet_address=SIGNER_ADDRESS,
        private_key=PRIVATE_KEY,
        account_id=12345,
        dex_id_override=2,
    )
    rest = ReyaTradingClient(config)
    rest._symbol_to_market_id = {PERP_SYMBOL: 1}
    rest._initialized = True
    return ReyaWsExecClient(rest_client=rest, ws_url="wss://invalid.example")


def _last_nonce() -> int:
    return ReyaTradingClient._wallet_nonces.get(SIGNER_ADDRESS.lower(), 0)


def _limit_params(**overrides: Any) -> LimitOrderParameters:
    fields: dict[str, Any] = {
        "symbol": PERP_SYMBOL,
        "is_buy": True,
        "limit_px": "3000",
        "qty": "0.01",
        "time_in_force": TimeInForce.GTC,
        "deadline": PINNED_DEADLINE,
    }
    fields.update(overrides)
    return LimitOrderParameters(**fields)


def _trigger_params(**overrides: Any) -> TriggerOrderParameters:
    fields: dict[str, Any] = {
        "symbol": PERP_SYMBOL,
        "is_buy": False,
        "trigger_px": "1000",
        "limit_px": "990",
        "trigger_type": OrderType.STOP_LOSS,
        "time_in_force": TimeInForce.GTC,
        "deadline": PINNED_DEADLINE,
    }
    fields.update(overrides)
    return TriggerOrderParameters(**fields)


def _modify_params(**overrides: Any) -> ModifyOrderParameters:
    fields: dict[str, Any] = {
        "symbol": PERP_SYMBOL,
        "is_buy": True,
        "limit_px": "2950",
        "qty": "0.75",
        "post_only": False,
        "expires_after": None,
        "time_in_force": TimeInForce.GTC,
        "order_id": 63552420354981888,
        "deadline": PINNED_DEADLINE,
    }
    fields.update(overrides)
    return ModifyOrderParameters(**fields)


@pytest.mark.parametrize("is_buy", ["false", "true", 1, 0], ids=["str-false", "str-true", "int-1", "int-0"])
async def test_ws_exec_create_limit_order_refuses_a_non_bool_is_buy(
    ws_exec_client: ReyaWsExecClient, is_buy: Any
) -> None:
    nonce_before = _last_nonce()
    with pytest.raises(ValueError, match="is_buy must be a bool"):
        await ws_exec_client.create_limit_order(_limit_params(is_buy=is_buy))
    assert _last_nonce() == nonce_before, "a refused ws-exec create consumed a nonce"


@pytest.mark.trigger
async def test_ws_exec_create_trigger_order_refuses_a_non_bool_is_buy(ws_exec_client: ReyaWsExecClient) -> None:
    nonce_before = _last_nonce()
    with pytest.raises(ValueError, match="is_buy must be a bool"):
        await ws_exec_client.create_trigger_order(_trigger_params(is_buy="false"))
    assert _last_nonce() == nonce_before, "a refused ws-exec trigger consumed a nonce"


@pytest.mark.modify
async def test_ws_exec_modify_order_refuses_a_non_bool_is_buy(ws_exec_client: ReyaWsExecClient) -> None:
    nonce_before = _last_nonce()
    with pytest.raises(ValueError, match="is_buy must be a bool"):
        await ws_exec_client.modify_order(_modify_params(is_buy="false"))
    assert _last_nonce() == nonce_before, "a refused ws-exec modify consumed a nonce"


async def test_a_real_bool_still_reaches_the_transport(ws_exec_client: ReyaWsExecClient) -> None:
    """Falsifiability: the guard must not be refusing everything. A proper bool
    gets past the builder and fails on the connection this fixture never opened."""
    with pytest.raises(WsExecProtocolError, match="NOT_CONNECTED"):
        await ws_exec_client.create_limit_order(_limit_params(is_buy=True))
