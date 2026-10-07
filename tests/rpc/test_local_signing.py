"""Every on-chain helper in sdk.reya_rpc signs locally and sends a raw transaction."""

from typing import Any

import ast
from importlib import import_module
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from hexbytes import HexBytes
from web3 import Web3
from web3.datastructures import AttributeDict

import sdk.reya_rpc
from sdk.reya_rpc import (
    BridgeOutParams,
    DepositParams,
    StakingParams,
    UnstakingParams,
    bridge_out_to_arbitrum,
    create_account,
    deposit,
    stake,
    unstake,
)
from sdk.reya_rpc.config import get_network_addresses
from sdk.reya_rpc.exceptions import InvalidChainIdError
from sdk.reya_rpc.types import CommandType
from sdk.reya_rpc.utils.transaction_utils import sign_and_send

pytestmark = pytest.mark.offline

# actions/__init__.py re-exports the functions under the submodule names, so take the module object.
bridge_out_module = import_module("sdk.reya_rpc.actions.bridge_out")

SENDER = "0x000000000000000000000000000000000000dEaD"
NONCE = 7
CHAIN_ID = 1729
SOCKET_FEE = 123
ACCOUNT_CREATED = "AccountCreated(uint128,address,address,uint256)"
SHARE_BALANCE_UPDATED = "ShareBalanceUpdated(uint128,address,int256,uint256,int256,uint256,address,int256)"


class _ContractCall:
    """A bound contract call that can be built into a transaction but refuses node-side signing."""

    def __init__(self, name: str, args: tuple):
        self.name = name
        self.args = args

    def build_transaction(self, params: dict) -> dict:
        return {"function": self.name, **params}

    def transact(self, *_args, **_kwargs):
        pytest.fail(f"{self.name} called .transact(); helpers must sign locally")


class _Functions:
    def __getattr__(self, name: str):
        return lambda *args: _ContractCall(name, args)


class _Contract:
    def __init__(self, address: str):
        self.address = address
        self.functions = _Functions()
        self.events = MagicMock()


class _Chain:
    """A config whose contracts refuse .transact() and whose w3 records what was signed and sent."""

    def __init__(self):
        self.receipt = AttributeDict(
            {
                "transactionHash": HexBytes(b"\x01" * 32),
                "logs": [
                    {"topics": [HexBytes(Web3.keccak(text=ACCOUNT_CREATED))]},
                    {"topics": [HexBytes(Web3.keccak(text=SHARE_BALANCE_UPDATED))]},
                ],
            }
        )
        self.w3 = MagicMock()
        self.w3.eth.get_transaction_count.return_value = NONCE
        self.w3.eth.account.sign_transaction.side_effect = lambda tx, private_key: MagicMock(raw_transaction=b"raw")
        self.w3.eth.wait_for_transaction_receipt.return_value = self.receipt

        contracts = {
            name: _Contract(f"0x{index:040x}")
            for index, name in enumerate(["core", "passive_pool", "periphery", "rusd", "usdc"], 1)
        }
        contracts["core"].events.AccountCreated.return_value.process_log.return_value = {"args": {"accountId": 42}}
        contracts["passive_pool"].events.ShareBalanceUpdated.return_value.process_log.return_value = {
            "args": {"sharesDelta": 10, "balanceDelta": -3}
        }

        self.config: dict[str, Any] = {
            "chain_id": CHAIN_ID,
            "w3": self.w3,
            "w3account": MagicMock(address=SENDER, key=b"key"),
            "w3contracts": contracts,
        }

    @property
    def signed(self) -> list[dict]:
        return [call.args[0] for call in self.w3.eth.account.sign_transaction.call_args_list]


def test_sign_and_send_builds_signs_and_sends_raw():
    chain = _Chain()

    receipt = sign_and_send(chain.config, _ContractCall("approve", ()), value=5)

    assert chain.signed == [{"function": "approve", "from": SENDER, "nonce": NONCE, "chainId": CHAIN_ID, "value": 5}]
    assert chain.w3.eth.account.sign_transaction.call_args.kwargs == {"private_key": b"key"}
    chain.w3.eth.send_raw_transaction.assert_called_once_with(b"raw")
    chain.w3.eth.wait_for_transaction_receipt.assert_called_once_with(chain.w3.eth.send_raw_transaction.return_value)
    assert receipt is chain.receipt


@pytest.mark.parametrize(
    ("action", "expected_functions"),
    [
        (create_account, ["createAccount"]),
        (lambda config: deposit(config, DepositParams(account_id=1, amount=100)), ["approve", "execute"]),
        (lambda config: stake(config, StakingParams(token_amount=100, min_shares=0)), ["approve", "addLiquidity"]),
        (lambda config: unstake(config, UnstakingParams(shares_amount=100, min_tokens=0)), ["removeLiquidity"]),
        (
            lambda config: bridge_out_to_arbitrum(config, BridgeOutParams(amount=100, fee_limit=10**18)),
            ["approve", "withdraw"],
        ),
    ],
    ids=["create_account", "deposit", "stake", "unstake", "bridge_out_to_arbitrum"],
)
def test_every_sender_signs_locally(monkeypatch, action, expected_functions):
    monkeypatch.setattr(bridge_out_module, "calculate_socket_fees", lambda *_args: SOCKET_FEE)
    chain = _Chain()

    action(chain.config)

    assert [tx["function"] for tx in chain.signed] == expected_functions
    assert all(tx["from"] == SENDER and tx["chainId"] == CHAIN_ID for tx in chain.signed)
    assert chain.w3.eth.send_raw_transaction.call_count == len(expected_functions)


def test_bridge_out_attaches_the_socket_fee_to_the_withdrawal(monkeypatch):
    monkeypatch.setattr(bridge_out_module, "calculate_socket_fees", lambda *_args: SOCKET_FEE)
    chain = _Chain()

    bridge_out_to_arbitrum(chain.config, BridgeOutParams(amount=100, fee_limit=10**18))

    assert [(tx["function"], tx["value"]) for tx in chain.signed] == [("approve", 0), ("withdraw", SOCKET_FEE)]


def test_no_helper_relies_on_node_side_signing():
    package = Path(sdk.reya_rpc.__file__).parent
    offenders = [
        f"{path.relative_to(package)}:{node.lineno}"
        for path in sorted(package.rglob("*.py"))
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "transact"
    ]

    assert not offenders


@pytest.mark.parametrize("chain_id", [89346162, 1])
def test_only_mainnet_is_configured(chain_id):
    with pytest.raises(InvalidChainIdError, match="docs/spot-account-topup.md"):
        get_network_addresses(chain_id)


def test_mainnet_core_is_unchanged():
    assert get_network_addresses(1729)["core_address"] == "0xA763B6a5E09378434406C003daE6487FbbDc1a80"


@pytest.mark.parametrize(
    "name",
    [
        "trade",
        "TradeParams",
        "update_oracle_prices",
        "bridge_in_from_arbitrum_sepolia",
        "bridge_out_to_arbitrum_sepolia",
    ],
)
def test_dead_helpers_are_not_exported(name):
    assert not hasattr(sdk.reya_rpc, name)


def test_legacy_match_order_command_is_gone():
    assert "MatchOrder" not in CommandType.__members__
