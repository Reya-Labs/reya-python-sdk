# pylint: disable=redefined-outer-name
"""
TS↔Py signature parity test.

Pinned vector: hardhat test private key 0xac09…ff80 (signer 0xf39F…2266) signing
the Order envelope (14-field OrderDetails incl. postOnly) plus the OrderCancel
and MassCancel envelopes, against the testnet OrdersGateway at chain id 89346162.

Expected hex was produced by ``node tests/parity/sign_ts.mjs``, which uses
ethers v6's ``signTypedData`` against the same orderTypes / orderCancelTypes /
massCancelTypes as the canonical off-chain signer.

If this test ever fails, either:
- The Python ``sign_*`` helpers diverged from the canonical TS impl, or
- The TS impl evolved and you need to re-run ``node sign_ts.mjs`` and update
  the EXPECTED_SIGNATURES dict below.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from sdk.reya_rest_api.auth.signatures import OrderTypeInt, SignatureGenerator, TimeInForceInt
from sdk.reya_rest_api.config import TradingConfig

# === Fixed test vector ===
PRIVATE_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
SIGNER_ADDRESS = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
CHAIN_ID = 89346162  # cronos / devnet1
ORDERS_GATEWAY = "0x7Ec89E555c771D2B5939aBE5C4E4291852633D4D"

# Hex produced by tests/parity/sign_ts.mjs against the canonical TS sign impl
# (ethers v6 signTypedData with the orderTypes from the off-chain monorepo).
EXPECTED_SIGNATURES = {
    "order": (
        "0x8b7d36f622ad44815d66a6f75678f40c99cdb965088bcc857b53db5f8a7b272d"
        "0204574e11bb9c8109c132490b7f36ff4dc6ee1e93c277a616cf60c2db26cce6"
        "1c"
    ),
    # Same envelope as "order" but with a SELL (negative quantity) — pins the
    # is_buy=False sign-encoding path that the buy vector can't catch.
    "order_sell": (
        "0xf2d69b90a93c361c28a44483d26e6667721f63fb8c8ec1a12106773c663a4397"
        "2c42d4b2ea68531b9b63d25e3a90cf093892db8983525424a6b0044d4c6d28bf"
        "1b"
    ),
    # Same envelope as "order" but with postOnly=true — pins the 14th
    # OrderDetails field; only postOnly differs from "order".
    "order_post_only": (
        "0x752574d6c57c9b9736966f7ec318c9a5ee9dc0f1e7ea631b5056061c0241d0bf"
        "1cd0cc3b3bd3bbee542c7a95f2bc1ebd85afb25152f4b648f57989063745af9c"
        "1c"
    ),
    "order_cancel": (
        "0x90ddba6ff879dee4773c214c927a470720f42378574281866edce100ea8c59d7"
        "75fb29e4ab6108a9ea84bfe12fffcdbbd6dfff98ea6ae034bbd87f4c21254f94"
        "1b"
    ),
    "mass_cancel": (
        "0x86d4f060ffbba16698cf8f89fdeabb0397a814be6f54075f908ccbd73894a422"
        "7c33b0b77ac8c2e495eca0848e56e60711cd5fa60b657a4ba675fd6bd13be920"
        "1b"
    ),
}


@pytest.fixture(scope="module")
def signer() -> SignatureGenerator:
    """SignatureGenerator with the pinned test key + chain id."""
    config = TradingConfig(
        api_url="https://invalid.example",  # not used for signing
        chain_id=CHAIN_ID,
        owner_wallet_address=SIGNER_ADDRESS,
        private_key=PRIVATE_KEY,
        account_id=12345,
    )
    # Sanity: the test relies on the testnet OG address, which TradingConfig
    # derives from chain_id. If somebody flips that mapping, fail loudly.
    assert config.default_orders_gateway_address == ORDERS_GATEWAY, (
        f"OrdersGateway address mismatch: config returned "
        f"{config.default_orders_gateway_address}, parity vector expects {ORDERS_GATEWAY}"
    )
    return SignatureGenerator(config)


def test_signer_address_matches_test_vector(signer: SignatureGenerator) -> None:
    """Sanity: the configured private key derives to the address the TS vector signed with."""
    assert signer.signer_wallet_address.lower() == SIGNER_ADDRESS.lower()


def test_order_signature_parity(signer: SignatureGenerator) -> None:
    """Python sign_order produces the same bytes as ethers v6 signTypedData."""
    sig = signer.sign_order(
        account_id=12345,
        market_id=1,
        exchange_id=2,
        order_type=int(OrderTypeInt.LIMIT),
        is_buy=True,
        qty=Decimal("0.5"),
        limit_price=Decimal("3000"),
        trigger_price=Decimal("0"),
        time_in_force=int(TimeInForceInt.IOC),
        client_order_id=42,
        reduce_only=False,
        expires_after=0,
        nonce=1700000000000000,
        deadline=1745000000,
    )
    assert (
        sig == EXPECTED_SIGNATURES["order"]
    ), f"Order signature drift:\n  py:  {sig}\n  ts:  {EXPECTED_SIGNATURES['order']}"


def test_order_sell_signature_parity(signer: SignatureGenerator) -> None:
    """is_buy=False must encode a negative quantity identically to ethers v6.

    Identical to the buy vector except ``is_buy=False``; the only signed field
    that changes is the order quantity's sign, so any drift isolates the
    is_buy → signed-quantity encoding.
    """
    sig = signer.sign_order(
        account_id=12345,
        market_id=1,
        exchange_id=2,
        order_type=int(OrderTypeInt.LIMIT),
        is_buy=False,
        qty=Decimal("0.5"),
        limit_price=Decimal("3000"),
        trigger_price=Decimal("0"),
        time_in_force=int(TimeInForceInt.IOC),
        client_order_id=42,
        reduce_only=False,
        expires_after=0,
        nonce=1700000000000000,
        deadline=1745000000,
    )
    assert (
        sig == EXPECTED_SIGNATURES["order_sell"]
    ), f"Sell-order signature drift:\n  py:  {sig}\n  ts:  {EXPECTED_SIGNATURES['order_sell']}"


def test_order_post_only_signature_parity(signer: SignatureGenerator) -> None:
    """post_only=True must encode the 14th OrderDetails field identically to ethers v6.

    Identical to the buy vector except ``post_only=True``; the only signed field
    that changes is ``OrderDetails.postOnly``, so any drift isolates the postOnly
    encoding. This is an encoding-level parity check — the IOC + post_only combo
    is rejected at the client layer (``build_create_limit_order_payload``), but
    ``sign_order`` itself signs whatever field values it is handed.
    """
    sig = signer.sign_order(
        account_id=12345,
        market_id=1,
        exchange_id=2,
        order_type=int(OrderTypeInt.LIMIT),
        is_buy=True,
        qty=Decimal("0.5"),
        limit_price=Decimal("3000"),
        trigger_price=Decimal("0"),
        time_in_force=int(TimeInForceInt.IOC),
        client_order_id=42,
        reduce_only=False,
        expires_after=0,
        nonce=1700000000000000,
        deadline=1745000000,
        post_only=True,
    )
    assert (
        sig == EXPECTED_SIGNATURES["order_post_only"]
    ), f"post_only-order signature drift:\n  py:  {sig}\n  ts:  {EXPECTED_SIGNATURES['order_post_only']}"


def test_order_cancel_signature_parity(signer: SignatureGenerator) -> None:
    """Python sign_cancel_order produces the same bytes as ethers v6 signTypedData."""
    sig = signer.sign_cancel_order(
        account_id=12345,
        market_id=1,
        order_id=63552420354981888,
        client_order_id=0,
        nonce=1700000000000001,
        deadline=1745000060,
    )
    assert (
        sig == EXPECTED_SIGNATURES["order_cancel"]
    ), f"OrderCancel signature drift:\n  py:  {sig}\n  ts:  {EXPECTED_SIGNATURES['order_cancel']}"


def test_mass_cancel_signature_parity(signer: SignatureGenerator) -> None:
    """Python sign_mass_cancel produces the same bytes as ethers v6 signTypedData.

    market_id=0 corresponds to ``cancel across all markets`` (matches the TS
    SDK ``params.marketId ?? 0`` fallback in ``massCancelMEOrders``)."""
    sig = signer.sign_mass_cancel(
        account_id=12345,
        market_id=0,
        nonce=1700000000000002,
        deadline=1745000120,
    )
    assert (
        sig == EXPECTED_SIGNATURES["mass_cancel"]
    ), f"MassCancel signature drift:\n  py:  {sig}\n  ts:  {EXPECTED_SIGNATURES['mass_cancel']}"
