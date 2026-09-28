"""Read-only mainnet inspection and unsigned account setup transactions."""

from typing import Any

import json
from decimal import Decimal
from pathlib import Path

import requests
from dotenv import dotenv_values
from eth_abi import encode
from eth_account import Account
from web3 import Web3
from web3.exceptions import ContractCustomError

from examples.liquidation_test.sizing import decimal, positive
from sdk.reya_rest_api.config import MAINNET_ORDERS_GATEWAY
from sdk.reya_rpc.config import get_network_addresses, load_contract_abis

OWNER = "0x48D677a576fC9010f73e0413513f1A8817EAc7eC"
API = "https://api.reya.xyz/v2"
RPC = "https://rpc.reya.network"
SPOT_OFFSET = 10_000_000_000


def view_abi(name: str, inputs: list[str], outputs: list[dict]) -> dict:
    return {
        "type": "function",
        "name": name,
        "stateMutability": "view",
        "inputs": [{"name": f"arg{i}", "type": t} for i, t in enumerate(inputs)],
        "outputs": outputs,
    }


class Readiness:
    def __init__(self, owner: str = OWNER):
        self.owner = Web3.to_checksum_address(owner)
        self.web3 = Web3(Web3.HTTPProvider(RPC, request_kwargs={"timeout": 20}))
        if self.web3.eth.chain_id != 1729:
            raise ValueError("RPC is not Reya mainnet")
        addresses = get_network_addresses(1729)
        abis = load_contract_abis()
        extra = [
            view_abi("getAccountOwner", ["uint128"], [{"type": "address"}]),
            view_abi(
                "getAccountPermissions",
                ["uint128"],
                [
                    {
                        "type": "tuple[]",
                        "components": [
                            {"name": "user", "type": "address"},
                            {"name": "permissions", "type": "bytes32[]"},
                        ],
                    }
                ],
            ),
        ]
        self.core = self.web3.eth.contract(address=addresses["core_address"], abi=abis["core_abi"] + extra)
        self.rusd = self.web3.eth.contract(address=addresses["rusd_address"], abi=abis["erc20_abi"])
        self.gateway = self.web3.eth.contract(
            address=Web3.to_checksum_address(MAINNET_ORDERS_GATEWAY),
            abi=[view_abi("hasPermission", ["address", "address"], [{"type": "bool"}])],
        )
        if self.rusd.functions.decimals().call() != 6:
            raise ValueError("Unexpected rUSD token decimals")

    @staticmethod
    def api(path: str) -> Any:
        response = requests.get(API + path, timeout=20)
        if response.status_code != 200:
            raise ValueError(f"Read API {path} returned HTTP {response.status_code}; check cutover readiness")
        return response.json()

    def wallet(self, resource: str) -> Any:
        return self.api(f"/wallet/{self.owner}/{resource}")

    def assert_owned_perp(self, account_id: int) -> None:
        if not 0 < account_id < SPOT_OFFSET:
            raise ValueError("A perp account ID is required")
        if self.core.functions.getAccountOwner(account_id).call().lower() != self.owner.lower():
            raise ValueError(f"Account {account_id} belongs to a different wallet")

    def margin(self, account_id: int) -> dict:
        self.assert_owned_perp(account_id)
        block = self.web3.eth.block_number
        try:
            values = self.core.functions.getUsdNodeMarginInfo(account_id).call(block_identifier=block)
        except ContractCustomError as error:
            # Oracle-manager INodeModule.StalePriceDetected(bytes32).
            if str(error.data).startswith("0xb12dbe62"):
                raise ValueError(
                    "Core oracle price is stale; wait for current prices before sizing or funding"
                ) from None
            raise
        return {
            "account_id": account_id,
            "block": block,
            "margin_balance": str(Decimal(values[1]) / 10**18),
            "real_balance": str(Decimal(values[2]) / 10**18),
            "liquidation_delta": str(Decimal(values[5]) / 10**18),
            "lmr": str(Decimal(values[9]) / 10**18),
        }

    def assert_empty(self, account_ids: list[int]) -> None:
        for account in account_ids:
            self.assert_owned_perp(account)
        for position in self.wallet("positions"):
            if position["accountId"] in account_ids and decimal(position["qty"]) != 0:
                raise ValueError("Test accounts must start with no positions")
        if any(order["accountId"] in account_ids for order in self.wallet("openOrders")):
            raise ValueError("Test accounts must start with no open orders or triggers")
        for balance in self.wallet("accountBalances"):
            if balance["accountId"] in account_ids and balance["asset"] != "RUSD":
                if decimal(balance["realBalance"]) != 0:
                    raise ValueError("Sizing supports rUSD-only accounts")
        for account in account_ids:
            if decimal(self.margin(account)["lmr"]) != 0:
                raise ValueError("Core still reports exposure; wait for settlement/indexer reconciliation")

    def unsigned(self, function: Any, description: str) -> dict:
        return {
            "description": description,
            "chainId": 1729,
            "from": self.owner,
            "to": self.core.address,
            "value": "0x0",
            "data": function._encode_transaction_data(),
        }

    def create_plan(self, count: int) -> list[dict]:
        if count not in (1, 2):
            raise ValueError("Create exactly one or two test accounts")
        return [
            self.unsigned(
                self.core.functions.createAccount(self.owner), f"Create perp account {i + 1} owned by {self.owner}"
            )
            for i in range(count)
        ]

    def funding_plan(self, source: int, targets: list[int], amount: Decimal) -> dict:
        positive(amount)
        if source in targets or len(targets) != 2 or len(set(targets)) != 2:
            raise ValueError("Use two distinct fresh destinations and a separate source")
        self.assert_empty([source] + targets)
        units = amount * 10**6
        if units != units.to_integral_value():
            raise ValueError("rUSD transfers support at most six decimals")
        # Both destinations must be empty, making accidental re-funding a hard error.
        if any(decimal(self.margin(account)["margin_balance"]) != 0 for account in targets):
            raise ValueError("A destination is already funded; inspect before making another transfer")
        if decimal(self.margin(source)["real_balance"]) < amount * 2:
            raise ValueError("Source has insufficient current on-chain rUSD")
        commands = [
            (4, encode(["(uint128,address,uint256)"], [[target, self.rusd.address, int(units)]]), 0, 0)
            for target in targets
        ]
        function = self.core.functions.execute(source, commands)
        # Simulate from the owner; eth_call does not sign or broadcast a transaction.
        function.call({"from": self.owner})
        return self.unsigned(function, f"Transfer {amount} rUSD from {source} to each of {targets}")


def signer_from_env(path: str, key_var: str, owner: str) -> tuple[str, str]:
    # Explicit file only: never load the checkout's default .env (it may be devnet).
    values = dotenv_values(Path(path), interpolate=False)
    if values.get("CHAIN_ID") != "1729":
        raise ValueError("Signer env must explicitly specify mainnet CHAIN_ID=1729")
    if str(values.get("PERP_WALLET_ADDRESS_1", "")).lower() != owner.lower():
        raise ValueError("Signer env names a different owner wallet")
    key = values.get(key_var)
    if not key:
        raise ValueError(f"Missing key variable {key_var}")
    try:
        address = Account.from_key(key).address
    except Exception:
        raise ValueError("Invalid signing key; value redacted") from None
    return key, address


def emit(value: Any) -> None:
    print(json.dumps(value, indent=2))
