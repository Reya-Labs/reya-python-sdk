"""Offline parity test: the SDK's EIP-712 ``OrderDetails`` struct hash must match
the on-chain canonical digest byte-for-byte.

The golden value is pinned from the on-chain contract's canonical OrderDetails
test vector. We re-derive the struct hash here from the SDK's own
``_ORDER_DETAILS_TYPE`` definition (the single source of truth that ``sign_order``
uses), so any drift in field order / types / the ``postOnly`` insertion point is
caught without a devnet round-trip.

This is the cross-implementation check for the 14-field OrderDetails (``postOnly``
inserted after ``reduceOnly``).
"""

import pytest
from eth_abi import encode
from eth_utils import keccak

from sdk.reya_rest_api.auth.signatures import _ORDER_DETAILS_TYPE, FULL_POSITION_STOP_SENTINEL

pytestmark = pytest.mark.offline

# Canonical on-chain OrderDetails struct hash for the reference order below.
GOLDEN_DIGEST = "0xafd76928ba06e123f0d14a403d91fdc8a4f653c55bae7282db60b5f0acdde258"

# Canonical reference-order field values, keyed by name so they bind to the SDK
# field order rather than a fragile positional list.
_CANONICAL_ORDER = {
    "accountId": 1,
    "marketId": 2,
    "exchangeId": 3,
    "orderType": 0,  # LIMIT
    "quantity": 2 * 10**18,  # 2e18, signed positive
    "limitPrice": 1000 * 10**18,
    "triggerPrice": 0,
    "timeInForce": 0,  # GTC
    "clientOrderId": 77,
    "reduceOnly": False,
    "postOnly": False,
    "expiresAfter": 0,  # perpetual
    "signer": "0x000000000000000000000000000000000000bEEF",  # address(0xBEEF)
    "nonce": 100,
}


def _order_details_typehash(members: list[dict[str, str]]) -> bytes:
    inner = ",".join(f"{m['type']} {m['name']}" for m in members)
    return keccak(text=f"OrderDetails({inner})")


def _hash_order_details(members: list[dict[str, str]], order: dict) -> bytes:
    """Mirror the on-chain ``keccak256(abi.encode(typehash, ...fields))``."""
    abi_types = ["bytes32"] + [m["type"] for m in members]
    abi_values = [_order_details_typehash(members)] + [order[m["name"]] for m in members]
    return keccak(encode(abi_types, abi_values))


def test_order_details_struct_hash_matches_on_chain_golden_vector() -> None:
    digest = "0x" + _hash_order_details(_ORDER_DETAILS_TYPE, _CANONICAL_ORDER).hex()
    assert digest.lower() == GOLDEN_DIGEST.lower(), (
        f"SDK OrderDetails struct hash {digest} != on-chain golden {GOLDEN_DIGEST}. "
        "The SDK EIP-712 OrderDetails definition has drifted from the on-chain "
        "OrderDetails typehash (field order / types / postOnly slot)."
    )


# The three armed SL/TP shapes a client signs, transcribed field-for-field from
# the contract-side fixtures in
# reya-network/orders-gateway/test/libraries/OrderHashing.t.sol, with the digests
# they pin. `quantity` is the RAW ±(2^255 - 1) full-position sentinel — never
# E18-scaled — and its sign carries the close side. `hashOrderDetails` is
# chain-independent, so no chain id enters these.
_S = FULL_POSITION_STOP_SENTINEL

_STOP_LOSS_IOC = {
    "accountId": 4021,
    "marketId": 3,
    "exchangeId": 1,
    "orderType": 1,  # STOP_LOSS
    "quantity": -_S,  # sells out a long
    "limitPrice": 1850 * 10**18,
    "triggerPrice": 1900 * 10**18,
    "timeInForce": 1,  # IOC
    "clientOrderId": 90_210,
    "reduceOnly": False,
    "postOnly": False,
    "expiresAfter": 0,
    "signer": "0x00000000000000000000000000000000000A11CE",
    "nonce": 1_700_000_001,
}

_STOP_LOSS_GTT = {
    "accountId": 4022,
    "marketId": 5,
    "exchangeId": 2,
    "orderType": 1,  # STOP_LOSS
    "quantity": -_S,
    "limitPrice": 61_500 * 10**18,
    "triggerPrice": 62_000 * 10**18,
    "timeInForce": 2,  # GTT
    "clientOrderId": 90_211,
    "reduceOnly": False,
    "postOnly": False,
    "expiresAfter": 1_800_000_000,
    "signer": "0x0000000000000000000000000000000000000B0B",
    "nonce": 1_700_000_002,
}

_TAKE_PROFIT_GTC = {
    "accountId": 4023,
    "marketId": 7,
    "exchangeId": 3,
    "orderType": 2,  # TAKE_PROFIT
    "quantity": _S,  # buys back a short
    "limitPrice": 2450 * 10**18,
    "triggerPrice": 2400 * 10**18,
    "timeInForce": 0,  # GTC
    "clientOrderId": 90_212,
    "reduceOnly": False,
    "postOnly": False,
    "expiresAfter": 0,
    "signer": "0x000000000000000000000000000000000000CAFE",
    "nonce": 1_700_000_003,
}

STOP_GOLDEN_VECTORS = [
    ("stop_loss_ioc", _STOP_LOSS_IOC, "0xd4c9a1c315228591d9d83e5ae5398320b0e9dfbe48600bb1490fd808e6978d40"),
    ("stop_loss_gtt", _STOP_LOSS_GTT, "0xe3a4f530ff74ad39d34968afc82a897bd5ee784da874d8c00f9db5f8ee5dde9c"),
    ("take_profit_gtc", _TAKE_PROFIT_GTC, "0x2343df956ef974f7bd344ab0fd555d1b16f0e869d6fd5d4dbe140f787de58679"),
]


@pytest.mark.trigger
@pytest.mark.parametrize(
    ("order", "golden"),
    [(order, golden) for _, order, golden in STOP_GOLDEN_VECTORS],
    ids=[name for name, _, _ in STOP_GOLDEN_VECTORS],
)
def test_full_position_stop_struct_hash_matches_on_chain_golden_vector(order: dict, golden: str) -> None:
    """Each armed stop shape hashes byte-identically to the contract's pin.

    Covers what the plain-limit vector cannot: the raw sentinel quantity in both
    signs, a non-zero triggerPrice, and each of the three time-in-force values a
    trigger create may choose.
    """
    digest = "0x" + _hash_order_details(_ORDER_DETAILS_TYPE, order).hex()
    assert digest.lower() == golden.lower(), (
        f"SDK OrderDetails struct hash {digest} != on-chain golden {golden}. "
        "The SDK EIP-712 OrderDetails definition has drifted from the on-chain "
        "OrderDetails typehash, or the full-position sentinel is being scaled."
    )


@pytest.mark.trigger
def test_stop_golden_vectors_are_distinct() -> None:
    """None of the four vectors could pass by accidentally hashing a shared
    shape — so a digest that matches matched the fields it was given."""
    digests = {_hash_order_details(_ORDER_DETAILS_TYPE, order) for _, order, _ in STOP_GOLDEN_VECTORS}
    digests.add(_hash_order_details(_ORDER_DETAILS_TYPE, _CANONICAL_ORDER))
    assert len(digests) == 4


@pytest.mark.trigger
def test_full_position_sentinel_is_the_raw_int256_max() -> None:
    """The sentinel is a raw int256 magnitude, not an E18 quantity: scaling it
    would overflow the signed field and silently change every stop digest."""
    assert FULL_POSITION_STOP_SENTINEL == (1 << 255) - 1


def test_order_details_has_post_only_after_reduce_only() -> None:
    names = [m["name"] for m in _ORDER_DETAILS_TYPE]
    assert (
        names.index("postOnly") == names.index("reduceOnly") + 1
    ), "postOnly must sit immediately after reduceOnly to match the on-chain typehash"
    assert len(_ORDER_DETAILS_TYPE) == 14, "OrderDetails must be the 14-field struct"
