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

from eth_abi import encode
from eth_utils import keccak

from sdk.reya_rest_api.auth.signatures import _ORDER_DETAILS_TYPE

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


def test_order_details_has_post_only_after_reduce_only() -> None:
    names = [m["name"] for m in _ORDER_DETAILS_TYPE]
    assert (
        names.index("postOnly") == names.index("reduceOnly") + 1
    ), "postOnly must sit immediately after reduceOnly to match the on-chain typehash"
    assert len(_ORDER_DETAILS_TYPE) == 14, "OrderDetails must be the 14-field struct"
