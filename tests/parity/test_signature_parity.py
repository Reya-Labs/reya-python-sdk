# pylint: disable=redefined-outer-name
"""
TS↔Py signature parity test.

Pinned vector: hardhat test private key 0xac09…ff80 (signer 0xf39F…2266) signing
three v2.3.0 envelopes (Order, OrderCancel, MassCancel) against the testnet
OrdersGateway at chain id 89346162.

Expected hex was produced by ``node tests/parity/sign_ts.mjs``, which uses
ethers v6's ``signTypedData`` against the same orderTypes / orderCancelTypes /
massCancelTypes that the off-chain monorepo uses (see
``packages/common/src/transactions/sign.ts`` on the ``feat/perpOB`` branch).

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
        "0xc3b3bc8592d7777e325063b3882263c0e846c672f0d69661541df68931d4e454"
        "34eddedb9a363237bcdd61804d229bfa244263f93da409f364bc27d3b47b969e"
        "1b"
    ),
    # Same envelope as "order" but with a SELL (negative quantity) — pins the
    # is_buy=False sign-encoding path that the buy vector can't catch.
    "order_sell": (
        "0xc46e7e3ca39c19f1ec2a1c370d0c521271fca8ced7fd4f9cd0b74dbe25c19517"
        "2692285b50c0cc19ce021e94997b81f4b24bd7342ce32475170f6a281ef65c8e"
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
