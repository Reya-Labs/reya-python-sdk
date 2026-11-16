# pylint: disable=protected-access,redefined-outer-name
"""Custom leverage (POST /v2/updateLeverage, GET /v2/wallet/{address}/leverages).

Offline: the leverage -> customRisk conversion (the integer rule the server
verifies signatures against), the SetAccountMarketCustomRisk sign/recover round
trip, the client's guards ahead of the nonce, and the client methods end to end
with only the HTTP transport mocked, so the request body asserted here is the
exact JSON the API receives.
"""

from __future__ import annotations

from typing import Any

import json
from unittest.mock import AsyncMock, patch

import pytest
from eth_account import Account
from eth_account.messages import encode_typed_data

from sdk.async_exec_api.request_error_code import RequestErrorCode as WsExecRequestErrorCode
from sdk.open_api.exceptions import BadRequestException
from sdk.open_api.models.market_definition import MarketDefinition
from sdk.open_api.models.position_risk import PositionRisk
from sdk.open_api.models.request_error_code import RequestErrorCode
from sdk.open_api.models.update_leverage_response import UpdateLeverageResponse
from sdk.open_api.models.wallet_leverage import WalletLeverage
from sdk.reya_rest_api import ReyaTradingClient
from sdk.reya_rest_api.auth.custom_leverage import (
    MAX_CUSTOM_RISK_E18,
    RESET_CUSTOM_RISK,
    custom_risk_for_leverage_setting,
    custom_risk_from_leverage,
    parse_im_multiplier,
)
from sdk.reya_rest_api.auth.signatures import (
    _SET_ACCOUNT_MARKET_CUSTOM_RISK_TYPE,
    SET_ACCOUNT_MARKET_CUSTOM_RISK_PRIMARY_TYPE,
    set_account_market_custom_risk_message,
)
from sdk.reya_rest_api.client import DEFAULT_DEADLINE_S, UPDATE_LEVERAGE_MAX_DEADLINE_S
from sdk.reya_rest_api.config import MAINNET_ORDERS_GATEWAY, TradingConfig
from tests.offline_clock import OFFLINE_CLOCK_S

pytestmark = pytest.mark.offline

E18 = 10**18
E54 = E18**3
M_1_3 = 1_300_000_000_000_000_000

PRIVATE_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
SIGNER_ADDRESS = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
CHAIN_ID = 1729
API_URL = "https://api.example.test/v2"
ACCOUNT_ID = 4021
SYMBOL = "ETHRUSDPERP"
MARKET_ID = 3
MAX_LEVERAGE = 25
PINNED_NONCE = 1_700_000_000_000
PINNED_DEADLINE = OFFLINE_CLOCK_S + 120
TX_HASH = "0x" + "ab" * 32

TYPES = {SET_ACCOUNT_MARKET_CUSTOM_RISK_PRIMARY_TYPE: _SET_ACCOUNT_MARKET_CUSTOM_RISK_TYPE}
DOMAIN = {"name": "Reya", "version": "1", "verifyingContract": MAINNET_ORDERS_GATEWAY}


def market_payload(**overrides: Any) -> dict[str, Any]:
    return {
        "symbol": SYMBOL,
        "marketId": MARKET_ID,
        "minOrderQty": "0.01",
        "qtyStepSize": "0.01",
        "tickSize": "0.01",
        "liquidationMarginParameter": "0.02",
        "initialMarginParameter": "0.026",
        "imMultiplier": "1.3",
        "maxLeverage": MAX_LEVERAGE,
        "oiCap": "1000",
        **overrides,
    }


def market(**overrides: Any) -> MarketDefinition:
    definition = MarketDefinition.from_dict(market_payload(**overrides))
    assert definition is not None
    return definition


def recover(custom_risk: int, nonce: int, deadline: int, signature: str, account_id: int = ACCOUNT_ID) -> str:
    """Recover the signer the way the API does: over the value IT computes."""
    message = set_account_market_custom_risk_message(CHAIN_ID, account_id, MARKET_ID, custom_risk, nonce, deadline)
    return str(Account.recover_message(encode_typed_data(DOMAIN, TYPES, message), signature=signature))


@pytest.fixture
def client() -> ReyaTradingClient:
    config = TradingConfig(
        api_url=API_URL,
        chain_id=CHAIN_ID,
        owner_wallet_address=SIGNER_ADDRESS,
        private_key=PRIVATE_KEY,
        account_id=ACCOUNT_ID,
    )
    return ReyaTradingClient(config)


def _last_nonce(client: ReyaTradingClient) -> int:
    return ReyaTradingClient._wallet_nonces.get(client.owner_wallet_address.lower(), 0)


# ============================================================================
# Conversion: leverage -> customRisk
# ============================================================================


@pytest.mark.parametrize(
    "leverage,expected",
    [
        (1, 591_715_976_331_360_947),
        (10, 5_917_159_763_313_610),
        (25, 946_745_562_130_178),
        (50, 236_686_390_532_545),
    ],
)
def test_conversion_table_for_a_1_3_multiplier(leverage: int, expected: int) -> None:
    assert custom_risk_from_leverage(leverage, parse_im_multiplier("1.3")) == expected


def test_conversion_is_exact_when_the_division_is() -> None:
    assert custom_risk_from_leverage(5, parse_im_multiplier("2")) == 10_000_000_000_000_000
    assert custom_risk_from_leverage(5, parse_im_multiplier("1.0")) == 40_000_000_000_000_000


@pytest.mark.parametrize(
    "leverage,multiplier",
    [
        (1, M_1_3),
        (7, M_1_3),
        (33, M_1_3),
        (100, M_1_3),
        (3, 1_123_456_789_012_345_678),
        (99, 1_999_999_999_999_999_999),
    ],
)
def test_conversion_rounds_up_to_the_smallest_value_not_exceeding_the_leverage(leverage: int, multiplier: int) -> None:
    value = custom_risk_from_leverage(leverage, multiplier)
    scale = multiplier * multiplier * leverage * leverage
    # value * m^2 * L^2 >= 10^54  <=>  1 / (m * sqrt(value)) <= L
    assert value * scale >= E54
    assert (value - 1) * scale < E54


def test_conversion_bounds() -> None:
    assert custom_risk_from_leverage(1, E18) == MAX_CUSTOM_RISK_E18
    # m * L < 1 needs a value above 1, which Core rejects.
    with pytest.raises(ValueError, match="outside"):
        custom_risk_from_leverage(1, E18 // 2)
    assert custom_risk_from_leverage(2, E18 // 2) == MAX_CUSTOM_RISK_E18


@pytest.mark.parametrize("leverage", [0, -1, 1.5, True, "10", None])
def test_conversion_rejects_a_non_positive_or_non_integer_leverage(leverage: Any) -> None:
    with pytest.raises(ValueError, match="leverage"):
        custom_risk_from_leverage(leverage, M_1_3)


def test_leverage_at_the_maximum_signs_the_reset() -> None:
    assert custom_risk_for_leverage_setting(MAX_LEVERAGE, M_1_3, MAX_LEVERAGE) == RESET_CUSTOM_RISK == 0
    assert custom_risk_for_leverage_setting(1, M_1_3, 1) == 0
    assert custom_risk_for_leverage_setting(24, M_1_3, MAX_LEVERAGE) == custom_risk_from_leverage(24, M_1_3)


@pytest.mark.parametrize("leverage,max_leverage", [(0, 25), (26, 25), (2.5, 25), (10, 0), (10, 10.5)])
def test_setting_rejects_a_leverage_outside_one_to_max(leverage: Any, max_leverage: Any) -> None:
    with pytest.raises(ValueError):
        custom_risk_for_leverage_setting(leverage, M_1_3, max_leverage)


@pytest.mark.parametrize(
    "decimal,raw",
    [
        ("1.3", M_1_3),
        ("1", E18),
        ("1.0", E18),
        ("2", 2 * E18),
        ("1.2999999999999998", 1_299_999_999_999_999_800),
        ("0.000000000000000001", 1),
        # Beyond Decimal's default 28-digit context: still exact.
        ("123456789012.123456789012345678", 123_456_789_012_123_456_789_012_345_678),
    ],
)
def test_im_multiplier_parses_exactly(decimal: str, raw: int) -> None:
    assert parse_im_multiplier(decimal) == raw


@pytest.mark.parametrize(
    "value", ["", "-1.3", "1.3e0", "1E18", "0", "0.0", "1.0000000000000000001", " 1.3", "1,3", "NaN", 1.3, None]
)
def test_im_multiplier_rejects_anything_but_a_positive_plain_decimal(value: Any) -> None:
    with pytest.raises(ValueError, match="imMultiplier"):
        parse_im_multiplier(value)


# ============================================================================
# Signing
# ============================================================================


def test_signature_recovers_to_the_signer_over_the_signed_value(client: ReyaTradingClient) -> None:
    custom_risk = custom_risk_from_leverage(10, M_1_3)
    signature = client.signature_generator.sign_set_account_market_custom_risk(
        account_id=ACCOUNT_ID,
        market_id=MARKET_ID,
        custom_risk=custom_risk,
        nonce=PINNED_NONCE,
        deadline=PINNED_DEADLINE,
    )
    assert recover(custom_risk, PINNED_NONCE, PINNED_DEADLINE, signature) == SIGNER_ADDRESS
    # Any other value, e.g. a client that rounded down, recovers someone else.
    assert recover(custom_risk - 1, PINNED_NONCE, PINNED_DEADLINE, signature) != SIGNER_ADDRESS
    assert recover(custom_risk, PINNED_NONCE + 1, PINNED_DEADLINE, signature) != SIGNER_ADDRESS


@pytest.mark.parametrize("custom_risk", [-1, MAX_CUSTOM_RISK_E18 + 1])
def test_signer_refuses_a_value_core_rejects(client: ReyaTradingClient, custom_risk: int) -> None:
    with pytest.raises(ValueError, match="custom_risk"):
        client.signature_generator.sign_set_account_market_custom_risk(
            account_id=ACCOUNT_ID, market_id=MARKET_ID, custom_risk=custom_risk, nonce=1, deadline=PINNED_DEADLINE
        )


# ============================================================================
# Client payload builder
# ============================================================================


def test_payload_signs_the_server_computed_value(client: ReyaTradingClient) -> None:
    payload = client.build_update_leverage_payload(market(), 10, nonce=PINNED_NONCE, deadline=PINNED_DEADLINE)
    assert payload == {
        "accountId": ACCOUNT_ID,
        "symbol": SYMBOL,
        "leverage": 10,
        "nonce": str(PINNED_NONCE),
        "deadline": PINNED_DEADLINE,
        "signature": payload["signature"],
        "signerWallet": SIGNER_ADDRESS,
    }
    assert recover(5_917_159_763_313_610, PINNED_NONCE, PINNED_DEADLINE, payload["signature"]) == SIGNER_ADDRESS


def test_payload_at_max_leverage_signs_the_reset(client: ReyaTradingClient) -> None:
    payload = client.build_update_leverage_payload(market(), MAX_LEVERAGE, nonce=PINNED_NONCE, deadline=PINNED_DEADLINE)
    assert payload["leverage"] == MAX_LEVERAGE
    assert recover(0, PINNED_NONCE, PINNED_DEADLINE, payload["signature"]) == SIGNER_ADDRESS


def test_payload_defaults_deadline_nonce_and_account(client: ReyaTradingClient) -> None:
    before = _last_nonce(client)
    payload = client.build_update_leverage_payload(market(), 5, account_id=777)
    assert payload["accountId"] == 777
    assert payload["deadline"] == OFFLINE_CLOCK_S + DEFAULT_DEADLINE_S
    nonce = int(payload["nonce"])
    assert nonce > before and nonce == _last_nonce(client)
    custom_risk = custom_risk_from_leverage(5, M_1_3)
    assert recover(custom_risk, nonce, payload["deadline"], payload["signature"], account_id=777) == SIGNER_ADDRESS


@pytest.mark.parametrize(
    "kwargs,match",
    [
        ({"leverage": 0}, "leverage"),
        ({"leverage": MAX_LEVERAGE + 1}, "leverage"),
        ({"leverage": 2.0}, "leverage"),
        ({"deadline": OFFLINE_CLOCK_S}, "passed"),
        ({"deadline": OFFLINE_CLOCK_S + UPDATE_LEVERAGE_MAX_DEADLINE_S + 1}, "300 seconds"),
        ({"deadline": float(OFFLINE_CLOCK_S + 60)}, "deadline"),
        ({"nonce": -1}, "nonce"),
        ({"nonce": 1 << 256}, "nonce"),
        ({"nonce": "1"}, "nonce"),
        ({"market": market(imMultiplier="1.3e0")}, "imMultiplier"),
    ],
)
def test_payload_rejections_happen_before_a_nonce_is_claimed(
    client: ReyaTradingClient, kwargs: dict[str, Any], match: str
) -> None:
    arguments: dict[str, Any] = {"market": market(), "leverage": 10, **kwargs}
    before = _last_nonce(client)
    with pytest.raises(ValueError, match=match):
        client.build_update_leverage_payload(**arguments)
    assert _last_nonce(client) == before


def test_payload_requires_an_account(client: ReyaTradingClient) -> None:
    client.config.account_id = None
    with pytest.raises(ValueError, match="account_id"):
        client.build_update_leverage_payload(market(), 10)


def test_deadline_at_the_limit_is_accepted(client: ReyaTradingClient) -> None:
    deadline = OFFLINE_CLOCK_S + UPDATE_LEVERAGE_MAX_DEADLINE_S
    assert client.build_update_leverage_payload(market(), 10, deadline=deadline)["deadline"] == deadline


# ============================================================================
# Client methods, HTTP transport mocked
# ============================================================================


class FakeResponse:
    """The slice of ``sdk.open_api.rest.RESTResponse`` the generated client reads."""

    def __init__(self, status: int, body: Any) -> None:
        self.status = status
        self.reason = "OK" if status == 200 else "Bad Request"
        self.data = json.dumps(body).encode()
        self._headers = {"content-type": "application/json"}

    async def read(self) -> bytes:
        return self.data

    def getheaders(self) -> dict[str, str]:
        return self._headers

    def getheader(self, name: str, default: str | None = None) -> str | None:
        return self._headers.get(name, default)


def transport(routes: dict[tuple[str, str], tuple[int, Any]]) -> AsyncMock:
    """A stand-in for ``RESTClientObject.request`` that answers by (method, url)."""

    async def request(method: str, url: str, **_: Any) -> FakeResponse:
        status, body = routes[(method, url.split("?")[0])]
        return FakeResponse(status, body)

    return AsyncMock(side_effect=request)


async def test_update_leverage_posts_exactly_what_the_api_expects(client: ReyaTradingClient) -> None:
    sender = transport(
        {
            ("GET", f"{API_URL}/perpMarketDefinitions"): (
                200,
                [market_payload(symbol="BTCRUSDPERP", marketId=1), market_payload()],
            ),
            ("POST", f"{API_URL}/updateLeverage"): (
                200,
                {"accountId": ACCOUNT_ID, "symbol": SYMBOL, "leverage": 10, "txHash": TX_HASH},
            ),
        }
    )
    with patch.object(client._api_client.rest_client, "request", sender):
        response = await client.update_leverage(SYMBOL, 10, nonce=PINNED_NONCE, deadline=PINNED_DEADLINE)

    assert isinstance(response, UpdateLeverageResponse)
    assert (response.account_id, response.symbol, response.leverage, response.tx_hash) == (
        ACCOUNT_ID,
        SYMBOL,
        10,
        TX_HASH,
    )

    post = sender.await_args_list[-1]
    assert post.args == ("POST", f"{API_URL}/updateLeverage")
    body = post.kwargs["body"]
    assert body == {
        "accountId": ACCOUNT_ID,
        "symbol": SYMBOL,
        "leverage": 10,
        "nonce": str(PINNED_NONCE),
        "deadline": PINNED_DEADLINE,
        "signature": body["signature"],
        "signerWallet": SIGNER_ADDRESS,
    }
    # What the API does with the body: derive customRisk from its own market
    # definition (imMultiplier 1.3, maxLeverage 25) and recover the signer.
    server_custom_risk = custom_risk_for_leverage_setting(body["leverage"], M_1_3, MAX_LEVERAGE)
    assert recover(server_custom_risk, int(body["nonce"]), body["deadline"], body["signature"]) == SIGNER_ADDRESS


async def test_update_leverage_refuses_a_symbol_that_is_not_a_perp_market(client: ReyaTradingClient) -> None:
    sender = transport({("GET", f"{API_URL}/perpMarketDefinitions"): (200, [market_payload()])})
    with patch.object(client._api_client.rest_client, "request", sender):
        with pytest.raises(ValueError, match="not a perp market"):
            await client.update_leverage("WETHRUSD", 2)
    assert [call.args[0] for call in sender.await_args_list] == ["GET"]


@pytest.mark.parametrize(
    "code",
    [
        "LEVERAGE_UPDATE_IN_PROGRESS_ERROR",
        "LEVERAGE_NOT_ALLOWED_ERROR",
        "UPDATE_LEVERAGE_OTHER_ERROR",
        "ACCOUNT_BELOW_INITIAL_MARGIN_ERROR",
        "ORDER_OUTCOME_UNKNOWN_ERROR",
    ],
)
async def test_update_leverage_rejections_surface_as_typed_request_errors(client: ReyaTradingClient, code: str) -> None:
    sender = transport(
        {
            ("GET", f"{API_URL}/perpMarketDefinitions"): (200, [market_payload()]),
            ("POST", f"{API_URL}/updateLeverage"): (400, {"error": code, "message": "rejected"}),
        }
    )
    with patch.object(client._api_client.rest_client, "request", sender):
        with pytest.raises(BadRequestException) as raised:
            await client.update_leverage(SYMBOL, 10, nonce=PINNED_NONCE, deadline=PINNED_DEADLINE)
    assert raised.value.data is not None
    assert raised.value.data.error is RequestErrorCode(code)


async def test_get_leverages_reads_the_wallet_settings(client: ReyaTradingClient) -> None:
    rows = [
        {"accountId": ACCOUNT_ID, "symbol": "BTCRUSDPERP", "leverage": 5},
        {"accountId": ACCOUNT_ID, "symbol": SYMBOL, "leverage": 10},
    ]
    sender = transport({("GET", f"{API_URL}/wallet/{SIGNER_ADDRESS}/leverages"): (200, rows)})
    with patch.object(client._api_client.rest_client, "request", sender):
        leverages = await client.get_leverages(account_id=ACCOUNT_ID)

    assert all(isinstance(row, WalletLeverage) for row in leverages)
    assert [(row.account_id, row.symbol, row.leverage) for row in leverages] == [
        (ACCOUNT_ID, "BTCRUSDPERP", 5),
        (ACCOUNT_ID, SYMBOL, 10),
    ]
    assert sender.await_args is not None
    method, url = sender.await_args.args
    assert method == "GET"
    assert url == f"{API_URL}/wallet/{SIGNER_ADDRESS}/leverages?accountId={ACCOUNT_ID}"


# ============================================================================
# Generated models
# ============================================================================


def test_request_error_codes_carry_the_leverage_codes() -> None:
    codes = {"LEVERAGE_UPDATE_IN_PROGRESS_ERROR", "LEVERAGE_NOT_ALLOWED_ERROR", "UPDATE_LEVERAGE_OTHER_ERROR"}
    assert codes <= {code.value for code in RequestErrorCode}
    assert codes <= {code.value for code in WsExecRequestErrorCode}


def test_market_definition_carries_the_exact_im_multiplier() -> None:
    definition = market()
    assert definition.im_multiplier == "1.3"
    assert definition.to_dict() == market_payload()


def test_position_risk_carries_leverage_and_initial_margin() -> None:
    position = PositionRisk.from_dict(
        {
            "accountId": ACCOUNT_ID,
            "symbol": SYMBOL,
            "side": "B",
            "qty": "1",
            "avgEntryPrice": "2000",
            "lastTradeSequenceNumber": 7,
            "markPrice": "2000",
            "notional": "2000",
            "unrealizedPnl": "0",
            "fundingSinceChange": "0",
            "fundingSinceOpen": "0",
            "liquidationMarginRequirement": "40",
            "initialMarginRequirement": "200",
            "leverage": 10,
            "liquidationPrice": None,
            "hasCorrelatedCollateral": False,
            "updatedAt": 1,
            "pricesUpdatedAt": 1,
        }
    )
    assert position is not None
    assert position.leverage == 10
    assert position.initial_margin_requirement == "200"
