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
ORDERS_GATEWAY = "0x5a0ac2f89e0bdeafc5c549e354842210a3e87ca5"

# Hex produced by tests/parity/sign_ts.mjs against the canonical TS sign impl
# (ethers v6 signTypedData with the orderTypes from the off-chain monorepo).
EXPECTED_SIGNATURES = {
    "order": (
        "0x7eb002513a43ffa8974ad0d1b17f0a70f954bae605ec8ddaab0aa6a0346fff68"
        "3a62255e2f7be29b9c64d0481816e3baacc728b4ef61300636437a653a18f380"
        "1c"
    ),
    # Same envelope as "order" but with a SELL (negative quantity) — pins the
    # is_buy=False sign-encoding path that the buy vector can't catch.
    "order_sell": (
        "0x4a9ea03e75d0fa8b5b59e1a9a14228b14d1a4bca0b1888b4fd346639135ddda5"
        "55f551472ab4ff8357ac82b580da1ab5e62211bb1c8dfc27a79c7e211c312a93"
        "1b"
    ),
    "order_cancel": (
        "0x5b68e16ff34ae2fa0b62acdc66c90f15784dc0940275b5d00d711d34185a8c80"
        "7df56678de28f079184c002dd195b4f7be7fd7760288a1410c0ac24d4ce1a0fc"
        "1b"
    ),
    "mass_cancel": (
        "0x2d95d9a00ceacd9af6291340a2c200b5b2d9bb7f4c8edb4fe960e22b09b19375"
        "7c506b87445f8f255abbc794183cc66fe7f90759ff41091c65159f278c55ee2e"
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
