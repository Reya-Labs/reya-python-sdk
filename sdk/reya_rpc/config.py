"""Gathering configuration from environment variables and ABIs"""

import json
import os

from dotenv import load_dotenv
from web3 import Web3

from sdk.reya_rpc.exceptions import InvalidChainIdError


def get_network_addresses(chain_id: int) -> dict:
    """Get network-specific contract addresses. Only Reya Network mainnet is supported."""
    if chain_id == 1729:
        return {
            "rpc_url": "https://rpc.reya.network",
            "core_address": "0xA763B6a5E09378434406C003daE6487FbbDc1a80",
            "passive_pool_address": "0xB4B77d6180cc14472A9a7BDFF01cc2459368D413",
            "rusd_address": "0xa9F32a851B1800742e47725DA54a09A7Ef2556A3",
            "periphery_address": "0xCd2869d1eb1BC8991Bc55de9E9B779e912faF736",
            "usdc_address": "0x3B860c0b53f2e8bd5264AA7c3451d41263C933F2",
        }
    raise InvalidChainIdError(
        f"reya_rpc supports only Reya Network mainnet (chain id 1729), got {chain_id}. "
        "To fund accounts on the 89346162 testnet, follow docs/spot-account-topup.md in the SDK repository."
    )


def load_contract_abis() -> dict:
    """Load all contract ABIs from files."""
    # Get the directory where this file is located
    current_dir = os.path.dirname(os.path.abspath(__file__))
    # Build path to the abis directory
    abis_dir = os.path.join(current_dir, "abis")

    abis = {}

    with open(os.path.join(abis_dir, "CoreProxy.json"), encoding="utf-8") as f:
        abis["core_abi"] = json.load(f)

    with open(os.path.join(abis_dir, "PassivePoolProxy.json"), encoding="utf-8") as f:
        abis["passive_pool_abi"] = json.load(f)

    with open(os.path.join(abis_dir, "PeripheryProxy.json"), encoding="utf-8") as f:
        abis["periphery_abi"] = json.load(f)

    with open(os.path.join(abis_dir, "Erc20.json"), encoding="utf-8") as f:
        abis["erc20_abi"] = json.load(f)

    return abis


def get_config() -> dict:
    """Get complete configuration for RPC operations."""
    load_dotenv()

    chain_id = int(os.environ["CHAIN_ID"])
    private_key = os.environ["PERP_PRIVATE_KEY_1"]

    # Get network-specific addresses
    network_config = get_network_addresses(chain_id)

    # Load contract ABIs
    abis = load_contract_abis()

    # Configure Web3 with modern approach for v7.x
    w3 = Web3(Web3.HTTPProvider(network_config["rpc_url"]))
    w3account = w3.eth.account.from_key(private_key)

    # Set default account
    w3.eth.default_account = w3account.address

    # Create contract instances
    w3core = w3.eth.contract(address=network_config["core_address"], abi=abis["core_abi"])
    w3passive_pool = w3.eth.contract(address=network_config["passive_pool_address"], abi=abis["passive_pool_abi"])
    w3periphery = w3.eth.contract(address=network_config["periphery_address"], abi=abis["periphery_abi"])
    w3rusd = w3.eth.contract(address=network_config["rusd_address"], abi=abis["erc20_abi"])
    w3usdc = w3.eth.contract(address=network_config["usdc_address"], abi=abis["erc20_abi"])

    return {
        "chain_id": chain_id,
        "private_key": private_key,
        "w3": w3,
        "w3account": w3account,
        "w3contracts": {
            "core": w3core,
            "passive_pool": w3passive_pool,
            "periphery": w3periphery,
            "rusd": w3rusd,
            "usdc": w3usdc,
        },
    }
