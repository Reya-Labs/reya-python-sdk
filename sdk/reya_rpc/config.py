"""Gathering configuration from environment variables and ABIs"""

import json
import os

from dotenv import load_dotenv
from web3 import Web3

from sdk.reya_rpc.exceptions import InvalidChainIdError


def get_network_addresses(chain_id: int) -> dict:
    """Get network-specific contract addresses."""
    if chain_id == 1729:
        return {
            "rpc_url": "https://rpc.reya.network",
            "passive_pool_account_id": 2,
            "exchange_id": 1,
            "core_address": "0xA763B6a5E09378434406C003daE6487FbbDc1a80",
            "multicall_address": "0xED28d27dFcA47AD2513C9f2e2d3C098C2eA5A47F",
            "oracle_adapter_address": "0x32edABC058C1207fE0Ec5F8557643c28E4FF379e",
            "passive_perp_address": "0x27E5cb712334e101B3c232eB0Be198baaa595F5F",
            "passive_pool_address": "0xB4B77d6180cc14472A9a7BDFF01cc2459368D413",
            "rusd_address": "0xa9F32a851B1800742e47725DA54a09A7Ef2556A3",
            "periphery_address": "0xCd2869d1eb1BC8991Bc55de9E9B779e912faF736",
            "usdc_address": "0x3B860c0b53f2e8bd5264AA7c3451d41263C933F2",
        }
    elif chain_id == 89346162:
        return {
            "rpc_url": "https://bartio.rpc.berachain.com/",
            "passive_pool_account_id": 2,
            "exchange_id": 1,
            "core_address": "0x77C9F40938db89E78e2071d007Dca31f07C59e2C",
            "multicall_address": "0x90C9c8047fE5CF0e59E9D0e80a2D46A2AD45b14B",
            "oracle_adapter_address": "0x2E3Dd3DA71c31E2C6ae87b20A6e4Ae85a01c30b1",
            "passive_perp_address": "0xE2d0E8a9E2B8E7F4cE5BBfF0a96b9e8B66b72AF8",
            "passive_pool_address": "0x0A97C52C1bE9Cff2c84f9dFeCd8e4c6f6FabAb2f",
            "rusd_address": "0x9DE724e7b3facF87Ce39465D3D712717182e3e55",
            "periphery_address": "0x94ccAe812f1647696754412082dd6684C2366A7f",
            "usdc_address": "0xfA27c7c6051344263533cc365274d9569b0272A8",
        }
    else:
        raise InvalidChainIdError("Invalid chain id! It's neither 1729 (Reya Network) nor 89346162 (Reya Cronos).")


def load_contract_abis() -> dict:
    """Load all contract ABIs from files."""
    # Get the directory where this file is located
    current_dir = os.path.dirname(os.path.abspath(__file__))
    # Build path to the abis directory
    abis_dir = os.path.join(current_dir, "abis")

    abis = {}

    with open(os.path.join(abis_dir, "CoreProxy.json"), encoding="utf-8") as f:
        abis["core_abi"] = json.load(f)

    with open(os.path.join(abis_dir, "Multicall.json"), encoding="utf-8") as f:
        abis["multicall_abi"] = json.load(f)

    with open(os.path.join(abis_dir, "OracleAdapterProxy.json"), encoding="utf-8") as f:
        abis["oracle_adapter_abi"] = json.load(f)

    with open(os.path.join(abis_dir, "PassivePerpProxy.json"), encoding="utf-8") as f:
        abis["passive_perp_abi"] = json.load(f)

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
    private_key = os.environ["PRIVATE_KEY"]

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
    w3multicall = w3.eth.contract(address=network_config["multicall_address"], abi=abis["multicall_abi"])
    w3oracle_adapter = w3.eth.contract(address=network_config["oracle_adapter_address"], abi=abis["oracle_adapter_abi"])
    w3passive_perp = w3.eth.contract(address=network_config["passive_perp_address"], abi=abis["passive_perp_abi"])
    w3passive_pool = w3.eth.contract(address=network_config["passive_pool_address"], abi=abis["passive_pool_abi"])
    w3periphery = w3.eth.contract(address=network_config["periphery_address"], abi=abis["periphery_abi"])
    w3rusd = w3.eth.contract(address=network_config["rusd_address"], abi=abis["erc20_abi"])
    w3usdc = w3.eth.contract(address=network_config["usdc_address"], abi=abis["erc20_abi"])

    return {
        "chain_id": chain_id,
        "exchange_id": network_config["exchange_id"],
        "passive_pool_account_id": network_config["passive_pool_account_id"],
        "private_key": private_key,
        "w3": w3,
        "w3account": w3account,
        "w3contracts": {
            "core": w3core,
            "multicall": w3multicall,
            "oracle_adapter": w3oracle_adapter,
            "passive_perp": w3passive_perp,
            "passive_pool": w3passive_pool,
            "periphery": w3periphery,
            "rusd": w3rusd,
            "usdc": w3usdc,
        },
    }
