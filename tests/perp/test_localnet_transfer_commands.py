"""Produce withdrawal/deposit ledger entries on Localnet and restore the funds."""

from __future__ import annotations

import asyncio
import json
import os
from decimal import Decimal
from pathlib import Path

import pytest
from eth_abi import encode
from web3 import Web3

import sdk.reya_rpc
from sdk.open_api.models.transfer_type import TransferType
from sdk.reya_rpc.actions.withdraw import WithdrawParams, withdraw
from sdk.reya_rpc.types import CommandType
from sdk.reya_rpc.utils.execute_core_commands import execute_core_commands
from tests.helpers import ReyaTester
from tests.helpers.localnet_fee_v3 import _send_transaction
from tests.helpers.wallet_transfers import (
    assert_net_deposits,
    assert_running_net_deposits,
    assert_transfer_snapshot,
    localnet_url,
    net_deposits,
    wait_for_transaction_transfers,
    wallet_transfers_socket,
)

pytestmark = [
    pytest.mark.localnet,
    pytest.mark.perp,
    pytest.mark.e2e,
    pytest.mark.skipif(os.environ.get("CHAIN_ID") != "31337", reason="requires the Localnet chain"),
]


def _collateral_config(tester: ReyaTester):
    """Use the generated quote collateral identity, never the distinct rUSD proxy."""
    w3 = Web3(Web3.HTTPProvider(localnet_url("NEXT_PUBLIC_LOCALNET_RPC_URL", "http")))
    assert w3.eth.chain_id == tester.chain_id == 31337
    account = w3.eth.account.from_key(os.environ["PERP_PRIVATE_KEY_1"])
    assert account.address.lower() == (tester.owner_wallet_address or "").lower()
    abi_dir = Path(sdk.reya_rpc.__file__).parent / "abis"
    core = w3.eth.contract(
        address=Web3.to_checksum_address(os.environ["CORE_PROXY_ADDRESS"]),
        abi=json.loads((abi_dir / "CoreProxy.json").read_text()),
    )
    token = w3.eth.contract(
        address=Web3.to_checksum_address(os.environ["RUSD_COLLATERAL_ADDRESS"]),
        abi=json.loads((abi_dir / "Erc20.json").read_text()),
    )
    assert token.functions.decimals().call() == 6, "the Localnet RUSD quote collateral has six decimals"
    return {"w3": w3, "w3account": account, "chain_id": 31337, "w3contracts": {"core": core, "rusd": token}}


def _deposit_back(config: dict, account_id: int, amount: int):
    """Sign the approval too: fixture owners need not be unlocked RPC accounts."""
    token = config["w3contracts"]["rusd"]
    core = config["w3contracts"]["core"]
    _send_transaction(config["w3"], config["w3account"].key, token.functions.approve(core.address, amount))
    inputs = encode(["(address,uint256)"], [[token.address, amount]])
    receipt = execute_core_commands(config, account_id, [(CommandType.Deposit.value, inputs, 0, 0)])
    assert receipt["status"] == 1, "deposit transaction reverted"
    return receipt


@pytest.mark.asyncio
async def test_localnet_withdrawal_and_deposit_transfers(perp_maker_tester: ReyaTester):
    """Both real commands reach REST, WS live/snapshot and the final balance."""
    tester = perp_maker_tester
    localnet_url("REYA_API_URL", "http")
    config = await asyncio.to_thread(_collateral_config, tester)
    token = config["w3contracts"]["rusd"]
    core = config["w3contracts"]["core"]
    owner = config["w3account"].address
    previous_allowance = await asyncio.to_thread(token.functions.allowance(owner, core.address).call)
    baseline = await net_deposits(tester)
    amount = 1_000_000  # One RUSD, withdrawn first so no mint or external funding is needed.
    deposited = False

    async with wallet_transfers_socket(tester) as observer:
        result = await asyncio.to_thread(withdraw, config, WithdrawParams(tester.account_id, amount, token.address))
        withdrawal = result["transaction_receipt"]
        assert withdrawal["status"] == 1, "withdrawal transaction reverted"
        try:
            withdrawn = await wait_for_transaction_transfers(tester, Web3.to_hex(withdrawal["transactionHash"]), 1)
            entry = withdrawn[0]
            assert entry.type == TransferType.WITHDRAWAL
            assert entry.asset == "RUSD" and Decimal(entry.amount) == Decimal(-1)
            assert entry.counterparty_account_id is None and entry.fill_id is None
            assert entry.symbol is None and entry.spot_execution_sequence_number is None
            after_withdrawal = assert_running_net_deposits(withdrawn, baseline)
            await assert_net_deposits(tester, after_withdrawal)
            await observer.assert_live_matches(withdrawn)

            deposit_receipt = await asyncio.to_thread(_deposit_back, config, tester.account_id, amount)
            deposited = True
            credited = await wait_for_transaction_transfers(tester, Web3.to_hex(deposit_receipt["transactionHash"]), 1)
            entry = credited[0]
            assert entry.type == TransferType.DEPOSIT
            assert entry.asset == "RUSD" and Decimal(entry.amount) == Decimal(1)
            assert entry.counterparty_account_id is None and entry.fill_id is None
            assert entry.symbol is None and entry.spot_execution_sequence_number is None
            assert credited[0].sequence_number > withdrawn[0].sequence_number
            assert assert_running_net_deposits(credited, after_withdrawal) == baseline
            await assert_net_deposits(tester, baseline)
            await observer.assert_live_matches(credited)
            await assert_transfer_snapshot(tester, withdrawn + credited)

            # Exercise the type filter on known produced rows, not an empty wallet.
            for expected in (withdrawn[0], credited[0]):
                page = await tester.client.get_transfers(types=[expected.type])
                assert all(row.type == expected.type for row in page.data)
                assert expected.sequence_number in {row.sequence_number for row in page.data}
        finally:
            try:
                if not deposited:
                    await asyncio.to_thread(_deposit_back, config, tester.account_id, amount)
                await assert_net_deposits(tester, baseline)
            finally:
                await asyncio.to_thread(
                    _send_transaction,
                    config["w3"],
                    config["w3account"].key,
                    token.functions.approve(core.address, previous_allowance),
                )
