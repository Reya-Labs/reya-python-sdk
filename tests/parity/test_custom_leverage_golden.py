"""Offline parity test: the SDK's EIP-712 ``SetAccountMarketCustomRisk`` struct hash
(POST /v2/updateLeverage) must match the canonical golden vector byte-for-byte.

The golden value is the orders gateway's own
(reya-network #770, ``orders-gateway/test/libraries/OrderHashing.t.sol``
``test_hashSetAccountMarketCustomRisk_GoldenVector``): the struct carries no
nonce, so the signed deadline is its only replay bound. It is re-derived
here twice from the SDK's own ``_SET_ACCOUNT_MARKET_CUSTOM_RISK_TYPE`` (the
definition ``sign_set_account_market_custom_risk`` signs with): once through an
independent ``abi.encode`` of the struct and once through eth_account's EIP-712
encoder over the exact message the signer builds. The conversion from a
leverage is then exercised end to end into the same hash.
"""

import pytest
from eth_abi import encode
from eth_account.messages import encode_typed_data
from eth_utils import keccak

from sdk.reya_rest_api.auth.custom_leverage import custom_risk_for_leverage_setting, parse_im_multiplier
from sdk.reya_rest_api.auth.signatures import (
    _SET_ACCOUNT_MARKET_CUSTOM_RISK_TYPE,
    SET_ACCOUNT_MARKET_CUSTOM_RISK_PRIMARY_TYPE,
    SignatureGenerator,
    set_account_market_custom_risk_message,
)
from sdk.reya_rest_api.config import MAINNET_ORDERS_GATEWAY, TradingConfig

pytestmark = pytest.mark.offline

GOLDEN_STRUCT_HASH = "0xbcfd6efb4a78f57181770361b2b8af5e410f5760fd54a2d759ed233b1e1e833c"
TYPE_STRING = (
    "SetAccountMarketCustomRisk(uint256 verifyingChainId,uint128 accountId,uint128 marketId,"
    "int64 customRisk,uint256 deadline)"
)

GOLDEN_CHAIN_ID = 1729
GOLDEN_ACCOUNT_ID = 4021
GOLDEN_MARKET_ID = 3
GOLDEN_CUSTOM_RISK = 40_000_000_000_000_000  # 0.04e18
GOLDEN_DEADLINE = 1_762_819_200

PRIVATE_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"


def _golden_message(custom_risk: int = GOLDEN_CUSTOM_RISK) -> dict[str, int]:
    return set_account_market_custom_risk_message(
        chain_id=GOLDEN_CHAIN_ID,
        account_id=GOLDEN_ACCOUNT_ID,
        market_id=GOLDEN_MARKET_ID,
        custom_risk=custom_risk,
        deadline=GOLDEN_DEADLINE,
    )


def _abi_struct_hash(message: dict[str, int]) -> str:
    """Mirror ``keccak256(abi.encode(typehash, ...fields))``."""
    members = _SET_ACCOUNT_MARKET_CUSTOM_RISK_TYPE
    typehash = keccak(text=TYPE_STRING)
    encoded = encode(["bytes32"] + [m["type"] for m in members], [typehash] + [message[m["name"]] for m in members])
    return "0x" + keccak(encoded).hex()


def _eip712_struct_hash(message: dict[str, int]) -> str:
    """The struct hash eth_account signs over (``SignableMessage.body``)."""
    signer = _mainnet_signer()
    types = {SET_ACCOUNT_MARKET_CUSTOM_RISK_PRIMARY_TYPE: _SET_ACCOUNT_MARKET_CUSTOM_RISK_TYPE}
    return "0x" + encode_typed_data(signer._domain, types, message).body.hex()  # pylint: disable=protected-access


def _mainnet_signer() -> SignatureGenerator:
    return SignatureGenerator(
        TradingConfig(
            api_url="https://invalid.example",
            chain_id=GOLDEN_CHAIN_ID,
            owner_wallet_address="0x" + "00" * 20,
            private_key=PRIVATE_KEY,
        )
    )


def test_type_string_matches_the_gateway() -> None:
    members = ",".join(f"{m['type']} {m['name']}" for m in _SET_ACCOUNT_MARKET_CUSTOM_RISK_TYPE)
    assert f"{SET_ACCOUNT_MARKET_CUSTOM_RISK_PRIMARY_TYPE}({members})" == TYPE_STRING


def test_struct_hash_matches_golden_vector() -> None:
    message = _golden_message()
    assert _abi_struct_hash(message) == GOLDEN_STRUCT_HASH
    assert _eip712_struct_hash(message) == GOLDEN_STRUCT_HASH


def test_leverage_conversion_lands_on_the_golden_struct_hash() -> None:
    # imMultiplier 1.0 at 5x is exactly 1 / (1 * 5)^2 = 0.04, the golden value.
    custom_risk = custom_risk_for_leverage_setting(5, parse_im_multiplier("1.0"), max_leverage=25)
    assert custom_risk == GOLDEN_CUSTOM_RISK
    assert _eip712_struct_hash(_golden_message(custom_risk)) == GOLDEN_STRUCT_HASH


def test_signs_over_the_gateway_domain_without_chain_id() -> None:
    # Same domain as orders: the chain id travels in the struct instead.
    domain = _mainnet_signer()._domain  # pylint: disable=protected-access
    assert domain == {"name": "Reya", "version": "1", "verifyingContract": MAINNET_ORDERS_GATEWAY}
