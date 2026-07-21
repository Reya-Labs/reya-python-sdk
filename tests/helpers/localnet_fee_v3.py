"""Localnet-only fee-model-v3 configuration and indexed evidence helpers."""

from __future__ import annotations

from typing import Any

import json
import os
import shutil
import subprocess  # nosec B404
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from web3 import Web3

WAD = 10**18
RUSD_SCALE = 10**6

_TAKER_FEE_RATE = 10**15  # 10 bps
_TAKER_REBATE_RATE = 2 * 10**17  # 20%
_POOL_REBATE_RATE = 25 * 10**16  # 25% of the post-taker-rebate remainder
_FEE_V3_TEST_TIER_ID = 9001

_FEE_TIER_COMPONENTS = [
    {"name": "takerFee", "type": "uint256"},
    {"name": "makerFee_DEPRECATED", "type": "uint256"},
    {"name": "makerRebate_DEPRECATED", "type": "uint256"},
]
_GLOBAL_FEE_COMPONENTS = [
    {"name": "ogRebateRate", "type": "uint256"},
    {"name": "refereeRebateRate", "type": "uint256"},
    {"name": "referrerRebate", "type": "uint256"},
    {"name": "affiliateReferrerRebate_DEPRECATED", "type": "uint256"},
    {"name": "vltzRebateRate", "type": "uint256"},
    {"name": "spreadDiscount_DEPRECATED", "type": "uint256"},
    {"name": "poolRebate", "type": "uint256"},
    {"name": "poolAccountId", "type": "uint128"},
]
_ACCOUNT_FEE_COMPONENTS = [
    {"name": "tierId", "type": "uint256"},
    {"name": "ogStatus", "type": "bool"},
    {"name": "affiliateStatus_DEPRECATED", "type": "bool"},
    {"name": "vltzStatus", "type": "bool"},
    {"name": "spreadDiscountStatus_DEPRECATED", "type": "bool"},
    {"name": "customAffiliateRate", "type": "uint256"},
]

_FEE_CONFIGURATION_ABI: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "getFeeTierParameters",
        "stateMutability": "view",
        "inputs": [{"name": "tierId", "type": "uint256"}],
        "outputs": [{"name": "", "type": "tuple", "components": _FEE_TIER_COMPONENTS}],
    },
    {
        "type": "function",
        "name": "getGlobalFeeParameters",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "tuple", "components": _GLOBAL_FEE_COMPONENTS}],
    },
    {
        "type": "function",
        "name": "getAccountOwnerFeeConfiguration",
        "stateMutability": "view",
        "inputs": [{"name": "accountOwner", "type": "address"}],
        "outputs": [{"name": "", "type": "tuple", "components": _ACCOUNT_FEE_COMPONENTS}],
    },
    {
        "type": "function",
        "name": "getAccountOwnerFeeParameters",
        "stateMutability": "view",
        "inputs": [{"name": "accountOwner", "type": "address"}],
        "outputs": [
            {"name": "takerFeeParameter", "type": "uint256"},
            {"name": "takerRebateParameter", "type": "uint256"},
        ],
    },
    {
        "type": "function",
        "name": "getReferrerAccountOwner",
        "stateMutability": "view",
        "inputs": [{"name": "refereeAccountOwner", "type": "address"}],
        "outputs": [{"name": "referrerAccountOwner", "type": "address"}],
    },
    {
        "type": "function",
        "name": "setAccountOwnerTierIdFeeConfig",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "accountOwner", "type": "address"},
            {"name": "tierId", "type": "uint256"},
        ],
        "outputs": [],
    },
    {
        "type": "function",
        "name": "setAccountOwnerOgStatusFeeConfig",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "accountOwner", "type": "address"},
            {"name": "ogStatus", "type": "bool"},
        ],
        "outputs": [],
    },
]


@dataclass(frozen=True)
class FeeV3Scenario:
    """Deterministic rates installed for the controlled Localnet fill."""

    taker_fee_rate: int = _TAKER_FEE_RATE
    taker_rebate_rate: int = _TAKER_REBATE_RATE
    pool_rebate_rate: int = _POOL_REBATE_RATE


@dataclass(frozen=True)
class IndexedFeeV3Row:
    """The fee columns persisted from one PassivePerpExecutionV3 event."""

    account_id: int
    counterparty_account_id: int
    fee: int
    protocol_fee_credit: int
    referrer_fee_credit: int
    taker_rebate_credit: int
    pool_fee_credit: int
    exchange_fee_credit: int | None
    maker_fee_credit: int | None
    maker_fee_debit: int | None
    transaction_hash: str


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is required for the Localnet fee-v3 evidence test")
    return value


def _send_transaction(w3: Web3, private_key: str, function: Any) -> None:
    account = w3.eth.account.from_key(private_key)
    transaction = function.build_transaction(
        {
            "chainId": w3.eth.chain_id,
            "from": account.address,
            "nonce": w3.eth.get_transaction_count(account.address, "pending"),
        }
    )
    signed = account.sign_transaction(transaction)
    receipt = w3.eth.wait_for_transaction_receipt(w3.eth.send_raw_transaction(signed.raw_transaction))
    if int(receipt["status"]) != 1:
        raise RuntimeError(f"Localnet fee-v3 configuration transaction reverted: {receipt['transactionHash'].hex()}")


@contextmanager
def configured_localnet_fee_v3(
    *,
    taker_owner: str,
    pool_account_id: int,
) -> Iterator[FeeV3Scenario | None]:
    """Install deterministic non-zero fee-v3 state on Localnet and restore it.

    Non-Localnet runs are a no-op so the same test remains useful on devnet.
    """

    if os.environ.get("CHAIN_ID") != "31337":
        yield None
        return

    rpc_url = _required_env("NEXT_PUBLIC_LOCALNET_RPC_URL")
    proxy_address = Web3.to_checksum_address(_required_env("PASSIVE_PERP_PROXY_ADDRESS"))
    configurator_private_key = _required_env("PERP_PRIVATE_KEY_2")
    taker_owner_address = Web3.to_checksum_address(taker_owner)

    w3 = Web3(Web3.HTTPProvider(rpc_url))
    if not w3.is_connected() or w3.eth.chain_id != 31337:
        raise RuntimeError("Localnet fee-v3 test requires the exact loopback chain 31337 RPC")

    contract = w3.eth.contract(address=proxy_address, abi=_FEE_CONFIGURATION_ABI)
    configurator = Web3.to_checksum_address(w3.eth.account.from_key(configurator_private_key).address)
    if configurator != taker_owner_address:
        raise RuntimeError("Localnet fee-v3 test requires PERP_PRIVATE_KEY_2 to own the taker account")

    account_config = tuple(contract.functions.getAccountOwnerFeeConfiguration(taker_owner_address).call())
    previous_tier_id = int(account_config[0])
    previous_og_status = bool(account_config[1])
    test_tier = tuple(contract.functions.getFeeTierParameters(_FEE_V3_TEST_TIER_ID).call())
    global_config = tuple(contract.functions.getGlobalFeeParameters().call())
    expected_tier = (_TAKER_FEE_RATE, 0, 0)
    expected_global = (_TAKER_REBATE_RATE, 0, 0, 0, 0, 0, _POOL_REBATE_RATE, pool_account_id)
    if test_tier != expected_tier or global_config != expected_global:
        raise RuntimeError(
            "Localnet fee-v3 bootstrap state is stale; rebuild the warm snapshot with make localnet-resnapshot"
        )

    referrer = Web3.to_checksum_address(contract.functions.getReferrerAccountOwner(taker_owner_address).call())
    if referrer != Web3.to_checksum_address("0x0000000000000000000000000000000000000000"):
        raise RuntimeError("Localnet fee-v3 test requires an un-referred taker for an isolated waterfall")

    applied_account_tier = False
    applied_og_status = False

    try:
        _send_transaction(
            w3,
            configurator_private_key,
            contract.functions.setAccountOwnerTierIdFeeConfig(taker_owner_address, _FEE_V3_TEST_TIER_ID),
        )
        applied_account_tier = True
        _send_transaction(
            w3,
            configurator_private_key,
            contract.functions.setAccountOwnerOgStatusFeeConfig(taker_owner_address, True),
        )
        applied_og_status = True

        resolved_fee, resolved_rebate = contract.functions.getAccountOwnerFeeParameters(taker_owner_address).call()
        if (int(resolved_fee), int(resolved_rebate)) != (_TAKER_FEE_RATE, _TAKER_REBATE_RATE):
            raise RuntimeError(
                "Localnet fee-v3 configuration did not resolve to the deterministic taker fee/rebate rates"
            )

        yield FeeV3Scenario()
    finally:
        cleanup_errors: list[Exception] = []

        def restore(function: Any) -> None:
            try:
                _send_transaction(w3, configurator_private_key, function)
            except Exception as error:  # pylint: disable=broad-exception-caught
                cleanup_errors.append(error)

        if applied_og_status:
            restore(contract.functions.setAccountOwnerOgStatusFeeConfig(taker_owner_address, previous_og_status))
        if applied_account_tier:
            restore(contract.functions.setAccountOwnerTierIdFeeConfig(taker_owner_address, previous_tier_id))

        if cleanup_errors:
            raise RuntimeError("Failed to restore the original Localnet fee-v3 configuration") from cleanup_errors[0]


def _optional_int(value: object) -> int | None:
    return None if value is None else int(str(value))


def _query_indexed_fee_v3_row(fill_id: int) -> IndexedFeeV3Row | None:
    kubeconfig = _required_env("LOCALNET_KUBECONFIG")
    kubectl = shutil.which("kubectl")
    if kubectl is None:
        raise RuntimeError("kubectl is required for the Localnet fee-v3 database probe")

    sql = """
SELECT json_build_object(
  'account_id', account_id::text,
  'counterparty_account_id', counterparty_account_id::text,
  'fee', fee::text,
  'protocol_fee_credit', protocol_fee_credit::text,
  'referrer_fee_credit', referrer_fee_credit::text,
  'taker_rebate_credit', taker_rebate_credit::text,
  'pool_fee_credit', pool_fee_credit::text,
  'exchange_fee_credit', exchange_fee_credit::text,
  'maker_fee_credit', maker_fee_credit::text,
  'maker_fee_debit', maker_fee_debit::text,
  'transaction_hash', transaction_hash
)
FROM orders
WHERE me_nonce = current_setting('localnet.fill_id')::numeric
ORDER BY event_sequence_number DESC
LIMIT 1;
""".strip()
    result = subprocess.run(  # nosec B603
        [
            kubectl,
            "--kubeconfig",
            kubeconfig,
            "--context",
            "k3d-reya-localnet",
            "exec",
            "-n",
            "reya-localnet",
            "deployment/reya-localnet-postgres",
            "--",
            "env",
            "PGPASSWORD=postgres",
            f"PGOPTIONS=-c localnet.fill_id={fill_id}",
            "psql",
            "-U",
            "postgres",
            "-d",
            "postgres",
            "-tAc",
            sql,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Localnet fee-v3 database probe failed: {result.stderr.strip()}")
    if not result.stdout.strip():
        return None

    document = json.loads(result.stdout.strip())
    return IndexedFeeV3Row(
        account_id=int(document["account_id"]),
        counterparty_account_id=int(document["counterparty_account_id"]),
        fee=int(document["fee"]),
        protocol_fee_credit=int(document["protocol_fee_credit"]),
        referrer_fee_credit=int(document["referrer_fee_credit"]),
        taker_rebate_credit=int(document["taker_rebate_credit"]),
        pool_fee_credit=int(document["pool_fee_credit"]),
        exchange_fee_credit=_optional_int(document["exchange_fee_credit"]),
        maker_fee_credit=_optional_int(document["maker_fee_credit"]),
        maker_fee_debit=_optional_int(document["maker_fee_debit"]),
        transaction_hash=str(document["transaction_hash"]),
    )


def wait_for_indexed_fee_v3_row(fill_id: str, timeout_s: float = 30.0) -> IndexedFeeV3Row:
    """Wait for the fast indexer to persist the V3 event identified by ME fill nonce."""

    if not fill_id.isdigit() or int(fill_id) <= 0:
        raise ValueError(f"fill_id must be a positive integer string, got {fill_id!r}")

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        row = _query_indexed_fee_v3_row(int(fill_id))
        if row is not None:
            return row
        time.sleep(0.25)

    raise AssertionError(f"fee-v3 row for fillId/me_nonce {fill_id} was not indexed within {timeout_s}s")
