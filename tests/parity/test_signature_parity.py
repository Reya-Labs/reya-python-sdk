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

from sdk.open_api.models.order_type import OrderType
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api import ReyaTradingClient
from sdk.reya_rest_api.auth.signatures import OrderTypeInt, SignatureGenerator, TimeInForceInt
from sdk.reya_rest_api.config import TradingConfig
from sdk.reya_rest_api.models.orders import LimitOrderParameters, ModifyOrderParameters, TriggerOrderParameters

pytestmark = pytest.mark.offline

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
    # GTT *create*: same envelope as "order" but timeInForce=2 (GTT) and a
    # non-zero expiresAfter (1745000600) strictly after the deadline. Pins that
    # the SIGNED timeInForce is 2 and a non-zero expiresAfter is covered — a
    # silent GTT→0/1 mismap (with the wire string still "GTT") would slip past
    # every IOC-based vector. Only timeInForce + expiresAfter differ from "order".
    "order_gtt_create": (
        "0xe8df8d60e3f7381f3d23df513addfdf886cbac6e71c727e579cb038c2ce62498"
        "7515cca3414e88e2d241c152157ce12cb2454d86e5fb379c865fbe42c289e3fd"
        "1c"
    ),
    # GTT create + postOnly=true: the GTT create vector with the maker-only flag
    # set, pinning both timeInForce==2 AND postOnly==true signed together.
    "order_gtt_post_only": (
        "0xb23de139806533b33906e8c373054ff1a296d9e6d6a166c8c8ac65a991a529da"
        "4d9e939674c951be5b58a4059c0875ef02ade964cf4d06b8bc51d4e453a4746f"
        "1b"
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
    # CancelAllAfter (dead-man's-switch) ARM: timeoutMs=30000.
    "cancel_all_after_arm": (
        "0x7d8d65dc7949e42fea86da56df36fbfc0fa07bbd8f8f6be8b9f8f5b2f46031bc"
        "190cf90ff18671afe86dce8d27dfceff513e9c8b9df45c94c5bfb937b3b49514"
        "1c"
    ),
    # CancelAllAfter DISARM: timeoutMs=0 — pins the zero path distinctly
    # from the arm vector.
    "cancel_all_after_disarm": (
        "0x8ef177568c9b4077353f261779b284f33f39d95c73a720c332cf07304ee54091"
        "5380177af7b95dabfcd8efbe278ab440b82c6623944818d4a16228fce075beb2"
        "1b"
    ),
    # Modify signs the SAME Order envelope over the full post-modify state —
    # the "order" vector with all four modifiable fields changed (qty 0.75,
    # limitPrice 2950, postOnly true, expiresAfter 1745003600) on a resting GTT
    # (the modifiable order that carries a non-zero expiresAfter), and a fresh
    # nonce/deadline. Asserted twice below: once via sign_order (envelope
    # identity) and once via build_modify_order_payload (the real client path).
    "order_modify_state": (
        "0x3d63bcbae53e3d2d23a7930c68d80e98bd55953ce91553094d8dd87d930a8d36"
        "2b2fab25d5873619623813c5ba34d1b03f33f57478ebb58f5b122e4792ec6a82"
        "1b"
    ),
    # Trigger (STOP_LOSS, orderType=1): the SAME Order envelope as a LIMIT but
    # with orderType=1, quantity = +FULL_POSITION_STOP_SENTINEL (the ±int256.max
    # full-position sentinel, RAW — not E18-scaled; buy side, reya-network #738),
    # a real triggerPrice (2800) + limitPrice (2750), GTC, expiresAfter 0. Every
    # other golden pins orderType 0, so this is the ONLY cross-language check
    # that the trigger orderType→uint8 mapping (and the trigger struct hash)
    # matches an independent ethers impl — a wrong mapping would ship green off
    # a same-signer round-trip and fail only on-chain. Asserted via sign_order
    # (encoding) AND the create/modify trigger builders (the real client paths).
    "order_trigger_stop_loss": (
        "0x61def0f71af8cfc72a45cb46794b414d9e6cc6875f4698bb34a80e1f3c86448a"
        "0f30222a39062cd5452a832f030d488ce18a954db6b5283de8393554d580cb0e"
        "1c"
    ),
    # TAKE_PROFIT (orderType=2): identical to the STOP_LOSS vector except
    # orderType 1→2, so any drift isolates the SL-vs-TP orderType encoding.
    "order_trigger_take_profit": (
        "0x41ee81ec81cf0600490bdca38848bb88ea1e7cf20a6721095e9afc93cefe290e"
        "62728a51c70a8442e7205636f4aca33843d7e753d08b329475ac07b9f4702876"
        "1b"
    ),
    # Sell-side STOP_LOSS: identical to the STOP_LOSS vector except the quantity
    # sign (+sentinel → −sentinel), so any drift isolates the is_buy →
    # sentinel-sign path — a sign flip would close the WRONG side's position at
    # fire time, which is exactly the failure the ±sentinel encoding guards.
    "order_trigger_stop_loss_sell": (
        "0x7706ca235acf56e630e752d46953aa6f38f369e175089438617b5944ddb37211"
        "08cee55feda4bff5d6e930fa3c1d0372a99899b76911e36d79b1c9d6a5de5c61"
        "1c"
    ),
}

# === Trigger golden-vector field values (shared by the sign_order + builder
# parity tests below). Mirrors orderStopLossValue / orderTakeProfitValue in
# tests/parity/sign_ts.mjs. ===
TRIGGER_ACCOUNT_ID = 12345
TRIGGER_MARKET_ID = 1
TRIGGER_EXCHANGE_ID = 2
TRIGGER_LIMIT_PX = Decimal("2750")
TRIGGER_TRIGGER_PX = Decimal("2800")
TRIGGER_CLIENT_ORDER_ID = 42
TRIGGER_NONCE = 1700000000000006
TRIGGER_DEADLINE = 1745000360

# === cancelAllAfter ARM vector inputs (tamper table flips one at a time) ===
CANCEL_ALL_AFTER_ARM_PARAMS: dict[str, int] = {
    "account_id": 12345,
    "timeout_ms": 30000,
    "nonce": 1700000000000003,
    "deadline": 1745000180,
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


def test_order_gtt_create_signature_parity(signer: SignatureGenerator) -> None:
    """GTT create must sign timeInForce==2 (not the IOC 1 / GTC 0 the wire-string
    can't catch) and a non-zero expiresAfter.

    Identical to the "order" vector except ``time_in_force=int(TimeInForceInt.GTT)``
    (== 2) and ``expires_after=1745000600`` (strictly after the deadline). Any
    drift here isolates the GTT timeInForce + expiresAfter encoding. Falsifiable:
    a silent GTT→0/1 mismap would change these bytes and fail.
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
        time_in_force=int(TimeInForceInt.GTT),
        client_order_id=42,
        reduce_only=False,
        expires_after=1745000600,
        nonce=1700000000000000,
        deadline=1745000000,
    )
    assert (
        sig == EXPECTED_SIGNATURES["order_gtt_create"]
    ), f"GTT-create signature drift:\n  py:  {sig}\n  ts:  {EXPECTED_SIGNATURES['order_gtt_create']}"
    # Falsifiability: signing the SAME envelope as IOC (the legacy default) must
    # NOT match the GTT golden — proving the golden is sensitive to timeInForce.
    sig_ioc = signer.sign_order(
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
        expires_after=1745000600,
        nonce=1700000000000000,
        deadline=1745000000,
    )
    assert sig_ioc != EXPECTED_SIGNATURES["order_gtt_create"], "GTT golden is insensitive to timeInForce"


def test_order_gtt_post_only_signature_parity(signer: SignatureGenerator) -> None:
    """A GTT can also be post-only: timeInForce==2 AND postOnly==true both signed.

    Identical to the GTT-create vector except ``post_only=True``. Any drift here
    isolates the (GTT + postOnly) combination's encoding.
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
        time_in_force=int(TimeInForceInt.GTT),
        client_order_id=42,
        reduce_only=False,
        expires_after=1745000600,
        nonce=1700000000000000,
        deadline=1745000000,
        post_only=True,
    )
    assert (
        sig == EXPECTED_SIGNATURES["order_gtt_post_only"]
    ), f"GTT+postOnly signature drift:\n  py:  {sig}\n  ts:  {EXPECTED_SIGNATURES['order_gtt_post_only']}"


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


@pytest.mark.cod
def test_cancel_all_after_arm_signature_parity(signer: SignatureGenerator) -> None:
    """Python sign_cancel_all_after (arm, timeoutMs=30000) produces the same
    bytes as ethers v6 signTypedData."""
    sig = signer.sign_cancel_all_after(**CANCEL_ALL_AFTER_ARM_PARAMS)
    assert (
        sig == EXPECTED_SIGNATURES["cancel_all_after_arm"]
    ), f"CancelAllAfter arm signature drift:\n  py:  {sig}\n  ts:  {EXPECTED_SIGNATURES['cancel_all_after_arm']}"


@pytest.mark.cod
def test_cancel_all_after_disarm_signature_parity(signer: SignatureGenerator) -> None:
    """timeoutMs=0 (disarm) must encode identically to ethers v6 — pins the
    zero path distinctly from the arm vector."""
    sig = signer.sign_cancel_all_after(
        account_id=12345,
        timeout_ms=0,
        nonce=1700000000000004,
        deadline=1745000240,
    )
    assert (
        sig == EXPECTED_SIGNATURES["cancel_all_after_disarm"]
    ), f"CancelAllAfter disarm signature drift:\n  py:  {sig}\n  ts:  {EXPECTED_SIGNATURES['cancel_all_after_disarm']}"


@pytest.mark.cod
@pytest.mark.parametrize(
    "tampered_field, tampered_value",
    [
        ("account_id", 12346),
        ("timeout_ms", 30001),
        ("nonce", 1700000000000004),
        ("deadline", 1745000181),
    ],
)
def test_cancel_all_after_tamper_changes_signature(
    signer: SignatureGenerator, tampered_field: str, tampered_value: int
) -> None:
    """Every CancelAllAfterDetails/envelope field is signature-bearing: flipping
    any one of accountId / timeoutMs / nonce / deadline must change the bytes.

    Catches a helper that silently drops a field from the typed data (the
    signature would still verify for the untampered struct, letting the server
    accept a request whose unsigned field was forged in transit)."""
    params = {**CANCEL_ALL_AFTER_ARM_PARAMS, tampered_field: tampered_value}
    sig = signer.sign_cancel_all_after(**params)
    assert sig != EXPECTED_SIGNATURES["cancel_all_after_arm"], (
        f"Tampering {tampered_field} ({CANCEL_ALL_AFTER_ARM_PARAMS[tampered_field]} -> {tampered_value}) "
        f"did not change the CancelAllAfter signature — the field is not being signed"
    )


@pytest.mark.modify
def test_order_modify_state_signature_parity(signer: SignatureGenerator) -> None:
    """Envelope identity: modify has NO dedicated typed-data schema — signing
    the full post-modify state through plain ``sign_order`` must reproduce the
    TS vector. Same fields as the "order" vector except the four modifiables
    (qty 0.75, limitPrice 2950, postOnly true, expiresAfter 1745003600), GTT
    TIF (the modifiable order that carries a non-zero expiresAfter), and a
    fresh nonce/deadline."""
    sig = signer.sign_order(
        account_id=12345,
        market_id=1,
        exchange_id=2,
        order_type=int(OrderTypeInt.LIMIT),
        is_buy=True,
        qty=Decimal("0.75"),
        limit_price=Decimal("2950"),
        trigger_price=Decimal("0"),
        time_in_force=int(TimeInForceInt.GTT),
        client_order_id=42,
        reduce_only=False,
        expires_after=1745003600,
        nonce=1700000000000005,
        deadline=1745000300,
        post_only=True,
    )
    assert (
        sig == EXPECTED_SIGNATURES["order_modify_state"]
    ), f"Post-modify Order signature drift:\n  py:  {sig}\n  ts:  {EXPECTED_SIGNATURES['order_modify_state']}"


def _sign_trigger(signer: SignatureGenerator, order_type: int, is_buy: bool = True) -> str:
    """Sign the pinned trigger OrderDetails (±sentinel quantity, GTC, no expiry)
    with the given ``order_type``. ``is_buy`` sets the sentinel's sign (the
    close side); ``order_type`` isolates SL / TP / (falsifiability) LIMIT over
    one struct."""
    return signer.sign_order(
        account_id=TRIGGER_ACCOUNT_ID,
        market_id=TRIGGER_MARKET_ID,
        exchange_id=TRIGGER_EXCHANGE_ID,
        order_type=order_type,
        is_buy=is_buy,  # sets the sentinel's sign for triggers
        qty=Decimal(0),
        limit_price=TRIGGER_LIMIT_PX,
        trigger_price=TRIGGER_TRIGGER_PX,
        time_in_force=int(TimeInForceInt.GTC),
        client_order_id=TRIGGER_CLIENT_ORDER_ID,
        reduce_only=False,
        expires_after=0,
        nonce=TRIGGER_NONCE,
        deadline=TRIGGER_DEADLINE,
    )


@pytest.mark.trigger
def test_order_trigger_stop_loss_signature_parity(signer: SignatureGenerator) -> None:
    """INDEPENDENT cross-language check that ``orderType=STOP_LOSS`` (uint8 1)
    encodes byte-for-byte like ethers v6.

    Every other golden pins ``orderType=0`` (LIMIT), and a same-signer round-trip
    can't catch a wrong trigger orderType→uint8 mapping (it would sign AND verify
    the same wrong byte, shipping green and failing only on-chain). This vector,
    produced by an independent ethers signer in ``sign_ts.mjs``, is the check
    that closes that gap.
    """
    sig = _sign_trigger(signer, int(OrderTypeInt.STOP_LOSS))
    assert sig == EXPECTED_SIGNATURES["order_trigger_stop_loss"], (
        f"STOP_LOSS trigger signature drift:\n  py:  {sig}\n" f"  ts:  {EXPECTED_SIGNATURES['order_trigger_stop_loss']}"
    )
    # Falsifiability: the SAME struct signed as LIMIT (the LIMIT-only goldens'
    # orderType) must NOT match — proving the golden is sensitive to orderType,
    # so it would actually catch a STOP_LOSS→LIMIT (or →0) mismap.
    assert (
        _sign_trigger(signer, int(OrderTypeInt.LIMIT)) != EXPECTED_SIGNATURES["order_trigger_stop_loss"]
    ), "STOP_LOSS golden is insensitive to orderType (matches a LIMIT-signed struct)"


@pytest.mark.trigger
def test_order_trigger_take_profit_signature_parity(signer: SignatureGenerator) -> None:
    """INDEPENDENT cross-language check that ``orderType=TAKE_PROFIT`` (uint8 2)
    encodes byte-for-byte like ethers v6.

    Identical to the STOP_LOSS vector except ``orderType`` 1→2, so any drift
    isolates the SL-vs-TP orderType encoding. The two goldens must also differ
    (a mapping that collapsed 1 and 2 to the same byte would be caught here).
    """
    sig = _sign_trigger(signer, int(OrderTypeInt.TAKE_PROFIT))
    assert sig == EXPECTED_SIGNATURES["order_trigger_take_profit"], (
        f"TAKE_PROFIT trigger signature drift:\n  py:  {sig}\n"
        f"  ts:  {EXPECTED_SIGNATURES['order_trigger_take_profit']}"
    )
    assert (
        EXPECTED_SIGNATURES["order_trigger_take_profit"] != EXPECTED_SIGNATURES["order_trigger_stop_loss"]
    ), "STOP_LOSS and TAKE_PROFIT goldens are identical — orderType 1 and 2 collapse to the same byte"


@pytest.mark.trigger
def test_order_trigger_sell_sentinel_sign_parity(signer: SignatureGenerator) -> None:
    """INDEPENDENT cross-language check of the is_buy → sentinel-sign path.

    Identical to the STOP_LOSS vector except ``is_buy=False``, so the signed
    quantity is −sentinel instead of +sentinel. A sign flip here would close
    the WRONG side's position at fire time — the exact failure the ±sentinel
    encoding (reya-network #738) exists to fail closed on — and a same-signer
    round-trip cannot catch it (it would sign AND verify the same wrong sign).
    """
    sig = _sign_trigger(signer, int(OrderTypeInt.STOP_LOSS), is_buy=False)
    assert sig == EXPECTED_SIGNATURES["order_trigger_stop_loss_sell"], (
        f"Sell-trigger sentinel-sign drift:\n  py:  {sig}\n"
        f"  ts:  {EXPECTED_SIGNATURES['order_trigger_stop_loss_sell']}"
    )
    # Falsifiability: the buy-side golden must NOT match — proving the vector
    # actually discriminates on the sentinel's sign.
    assert (
        EXPECTED_SIGNATURES["order_trigger_stop_loss_sell"] != EXPECTED_SIGNATURES["order_trigger_stop_loss"]
    ), "sell-trigger golden is insensitive to the sentinel sign"


@pytest.fixture
def offline_client() -> ReyaTradingClient:
    """A ReyaTradingClient that can build payloads offline.

    Same seam as tests/parity/test_wire_serialization.py: seed the
    symbol→marketId map directly instead of calling ``start()`` (which loads
    market definitions over the network), and pin ``dex_id_override=2`` so the
    signed exchangeId matches the vector regardless of any REYA_DEX_ID env
    override picked up at import time.
    """
    config = TradingConfig(
        api_url="https://invalid.example",  # never called — building is pure
        chain_id=CHAIN_ID,
        owner_wallet_address=SIGNER_ADDRESS,
        private_key=PRIVATE_KEY,
        account_id=12345,
        dex_id_override=2,
    )
    client = ReyaTradingClient(config)
    client._symbol_to_market_id = {"ETHRUSDPERP": 1}  # pylint: disable=protected-access
    client._initialized = True  # pylint: disable=protected-access
    return client


def test_gtt_create_builder_signs_time_in_force_two(offline_client: ReyaTradingClient) -> None:
    """Builder-level GTT-create: ``build_create_limit_order_payload`` for a GTT
    must produce a signature that matches ``sign_order`` with
    ``time_in_force=int(TimeInForceInt.GTT)`` (== 2) — proving the client's
    ``_TIME_IN_FORCE_TO_INT[GTT]`` translation feeds the SIGNED int 2, not a
    silent 0/1 mismap. The wire string stays "GTT" either way, so only the
    signature can catch the mismap.

    The create builder auto-generates the nonce (no override hook), so this is
    self-consistency against ``sign_order`` over the SAME nonce/deadline (the
    cross-language TS golden for this struct is pinned separately via
    ``test_order_gtt_create_signature_parity``)."""
    expires_after = 1745000600
    deadline = 1745000000
    payload, nonce = offline_client.build_create_limit_order_payload(
        LimitOrderParameters(
            symbol="ETHRUSDPERP",
            is_buy=True,
            limit_px="3000",
            qty="0.5",
            time_in_force=TimeInForce.GTT,
            client_order_id=42,
            expires_after=expires_after,
            deadline=deadline,
        )
    )
    # The wire string is "GTT" regardless of the signed int, so it is NOT what
    # proves the mapping. The signature is.
    assert payload["timeInForce"] == TimeInForce.GTT.value
    assert payload["expiresAfter"] == expires_after

    def _resign(tif_int: int) -> str:
        return offline_client.signature_generator.sign_order(
            account_id=12345,
            market_id=1,
            exchange_id=2,
            order_type=int(OrderTypeInt.LIMIT),
            is_buy=True,
            qty=Decimal("0.5"),
            limit_price=Decimal("3000"),
            trigger_price=Decimal("0"),
            time_in_force=tif_int,
            client_order_id=42,
            reduce_only=False,
            expires_after=expires_after,
            nonce=nonce,
            deadline=deadline,
            post_only=False,
        )

    assert payload["signature"] == _resign(int(TimeInForceInt.GTT)), (
        "GTT-create builder did not sign timeInForce==2:\n"
        f"  payload: {payload['signature']}\n  GTT(2):  {_resign(int(TimeInForceInt.GTT))}"
    )
    # Falsifiability: a silent 0 (GTC) or 1 (IOC) mismap would sign different
    # bytes — confirm the assertion above actually discriminates.
    assert payload["signature"] != _resign(int(TimeInForceInt.GTC))
    assert payload["signature"] != _resign(int(TimeInForceInt.IOC))


@pytest.mark.modify
def test_modify_order_builder_signature_parity(offline_client: ReyaTradingClient) -> None:
    """Builder-level parity: ``build_modify_order_payload`` over matching
    inputs must emit the SAME pinned hex as the TS vector — proving modify
    == order signing over the post-modify struct through the real client
    path (param mapping, TIF translation, clientOrderId resolution),
    not just through a hand-fed ``sign_order``."""
    payload, nonce = offline_client.build_modify_order_payload(
        ModifyOrderParameters(
            symbol="ETHRUSDPERP",
            is_buy=True,
            limit_px="2950",
            qty="0.75",
            post_only=True,
            expires_after=1745003600,
            time_in_force=TimeInForce.GTT,
            order_id=63552420354981888,
            client_order_id=42,
            nonce=1700000000000005,
            deadline=1745000300,
        )
    )
    assert nonce == 1700000000000005
    assert payload["signature"] == EXPECTED_SIGNATURES["order_modify_state"], (
        f"Modify-builder signature drift:\n  py:  {payload['signature']}\n"
        f"  ts:  {EXPECTED_SIGNATURES['order_modify_state']}"
    )


@pytest.mark.trigger
@pytest.mark.modify
def test_trigger_modify_builder_signature_parity(offline_client: ReyaTradingClient) -> None:
    """Builder-level trigger parity: ``build_modify_order_payload`` for a
    STOP_LOSS reprice must emit the SAME pinned hex as the independent ethers
    vector — proving the real modify client path (orderType→uint8, qty→0
    coercion, trigger_px, GTC TIF, clientOrderId restatement) encodes the
    trigger orderType identically to ethers, not just self-consistently.

    Same OrderDetails as ``order_trigger_stop_loss`` (nonce/deadline pinned via
    the modify builder's override hooks): STOP_LOSS, qty omitted (signed 0),
    limitPx 2750, triggerPx 2800, GTC, no expiry, clientOrderId 42.
    """
    payload, nonce = offline_client.build_modify_order_payload(
        ModifyOrderParameters(
            symbol="ETHRUSDPERP",
            is_buy=True,
            limit_px=str(TRIGGER_LIMIT_PX),
            qty=None,  # trigger modify omits qty; sign_order derives the ±sentinel
            post_only=False,
            expires_after=None,  # GTC → expiresAfter 0
            time_in_force=TimeInForce.GTC,
            client_order_id=TRIGGER_CLIENT_ORDER_ID,  # target + restated signed id
            trigger_px=str(TRIGGER_TRIGGER_PX),
            reduce_only=False,
            deadline=TRIGGER_DEADLINE,
            nonce=TRIGGER_NONCE,
            order_type=OrderType.STOP_LOSS,
        )
    )
    assert nonce == TRIGGER_NONCE
    assert "qty" not in payload  # trigger modify drops qty from the wire
    assert payload["triggerPx"] == str(TRIGGER_TRIGGER_PX)
    assert payload["signature"] == EXPECTED_SIGNATURES["order_trigger_stop_loss"], (
        f"Trigger-modify-builder signature drift:\n  py:  {payload['signature']}\n"
        f"  ts:  {EXPECTED_SIGNATURES['order_trigger_stop_loss']}"
    )


@pytest.mark.trigger
def test_trigger_create_builder_signs_order_type(offline_client: ReyaTradingClient) -> None:
    """Builder-level trigger-create: ``build_create_trigger_order_payload`` for a
    STOP_LOSS must sign ``orderType=1`` (not a silent LIMIT/0 mismap) — the wire
    string stays "STOP_LOSS" either way, so only the signature can catch a
    mismap.

    The create builder auto-generates the nonce (no override hook), so this is
    self-consistency against ``sign_order`` over the SAME captured nonce (the
    cross-language TS golden for this struct is pinned via
    ``test_order_trigger_stop_loss_signature_parity``). Mirrors the GTT-create
    builder test."""
    payload, nonce = offline_client.build_create_trigger_order_payload(
        TriggerOrderParameters(
            symbol="ETHRUSDPERP",
            is_buy=True,
            trigger_px=str(TRIGGER_TRIGGER_PX),
            trigger_type=OrderType.STOP_LOSS,
            limit_px=str(TRIGGER_LIMIT_PX),
            client_order_id=TRIGGER_CLIENT_ORDER_ID,
            deadline=TRIGGER_DEADLINE,
        )
    )
    assert payload["orderType"] == OrderType.STOP_LOSS.value
    assert "qty" not in payload  # trigger create omits qty

    def _resign(order_type_int: int) -> str:
        return offline_client.signature_generator.sign_order(
            account_id=TRIGGER_ACCOUNT_ID,
            market_id=TRIGGER_MARKET_ID,
            exchange_id=TRIGGER_EXCHANGE_ID,
            order_type=order_type_int,
            is_buy=True,
            qty=Decimal(0),
            limit_price=TRIGGER_LIMIT_PX,
            trigger_price=TRIGGER_TRIGGER_PX,
            time_in_force=int(TimeInForceInt.GTC),
            client_order_id=TRIGGER_CLIENT_ORDER_ID,
            reduce_only=False,
            expires_after=0,
            nonce=nonce,
            deadline=TRIGGER_DEADLINE,
        )

    assert payload["signature"] == _resign(int(OrderTypeInt.STOP_LOSS)), (
        "trigger-create builder did not sign orderType==STOP_LOSS(1):\n"
        f"  payload:      {payload['signature']}\n  STOP_LOSS(1): {_resign(int(OrderTypeInt.STOP_LOSS))}"
    )
    # Falsifiability: a silent LIMIT(0) or TAKE_PROFIT(2) mismap would sign
    # different bytes — confirm the assertion above actually discriminates.
    assert payload["signature"] != _resign(int(OrderTypeInt.LIMIT))
    assert payload["signature"] != _resign(int(OrderTypeInt.TAKE_PROFIT))
