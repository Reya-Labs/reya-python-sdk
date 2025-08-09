import json
from dataclasses import dataclass

from web3 import Web3
from web3.types import HexStr

from sdk.reya_rpc.exceptions import BridgeFeeExceededError, NetworkConfigurationError


@dataclass
class BridgeInParams:
    """Data class to store bridge-in parameters."""

    amount: int  # Amount of USDC to bridge (scaled by 10^6)
    fee_limit: int  # Maximum acceptable bridging fee in native token (scaled by 10^18)


# Load contract ABI files
with open("sdk/reya_rpc/abis/SocketVaultWithPayload.json", encoding="utf-8") as f:
    vault_abi = json.load(f)

with open("sdk/reya_rpc/abis/Erc20.json", encoding="utf-8") as f:
    erc20_abi = json.load(f)


def bridge_in_from_arbitrum(config: dict, params: BridgeInParams):
    """
    Bridges USDC into Reya Network from Arbitrum.

    Args:
        config (dict): Configuration dictionary containing Web3 contract instances and IDs. Check out config.py for more details.
        params (BridgeInParams): Bridging parameters including USDC amount and maximum fee limit.

    Returns:
        dict: Contains transaction receipt of the bridging transaction.
    """

    # Define Arbitrum-specific parameters
    arbitrum_rpc_url = "https://arb1.arbitrum.io/rpc"
    vault_address = "0x11B3a7E08Eb2FdEa2745e4CB64648b10B28524A8"
    connector_address = "0xb0d57301050710AF1145562b3386ff5eCFE9BE83"
    chain_id = config["chain_id"]

    # Ensure Reya Network is correctly configured
    if not chain_id == 1729:
        raise NetworkConfigurationError("Bridging function requires setup for Reya Network")

    # Call the general bridge function with Arbitrum parameters
    return bridge_in(
        config=config,
        params=params,
        chain_rpc_url=arbitrum_rpc_url,
        vault_address=vault_address,
        connector_address=connector_address,
    )


def bridge_in_from_arbitrum_sepolia(config: dict, params: BridgeInParams):
    """
    Bridges USDC into Reya Cronos from Arbitrum Sepolia.

    Args:
        config (dict): Configuration dictionary containing Web3 contract instances and IDs. Check out config.py for more details.
        params (BridgeInParams): Bridging parameters including USDC amount and maximum fee limit.

    Returns:
        dict: Contains transaction receipt of the bridging transaction.
    """

    # Define Arbitrum-specific parameters
    arbitrum_sepolia_rpc_url = "https://sepolia-rollup.arbitrum.io/rpc"
    vault_address = "0x8fa5F65033193e8e0b4880C256B199916d6965F8"
    connector_address = "0xDefB236eB69b7f94490786375B093C6c8271214A"
    chain_id = config["chain_id"]

    # Ensure Reya Network is correctly configured
    if not chain_id == 89346162:
        raise NetworkConfigurationError("Bridging function requires setup for Reya Cronos")

    # Call the general bridge function with Arbitrum parameters
    return bridge_in(
        config=config,
        params=params,
        chain_rpc_url=arbitrum_sepolia_rpc_url,
        vault_address=vault_address,
        connector_address=connector_address,
    )


def bridge_in(
    config: dict,
    params: BridgeInParams,
    chain_rpc_url: str,
    vault_address: str,
    connector_address: str,
):
    """
    Bridges USDC from an external chain into Reya Network.

    Args:
        config (dict): Configuration dictionary containing private key and contract references.
        params (BridgeInParams): Bridging parameters including USDC amount and maximum fee limit.
        chain_rpc_url (str): RPC URL of the source chain.
        vault_address (str): Address of the vault contract on the source chain.
        connector_address (str): Address of the connector contract on the source chain.

    Returns:
        dict: Contains transaction receipt of the bridging transaction.
    """

    # Retrieve the private key from config
    private_key = config["private_key"]

    # Create Web3 instance for the specified chain
    w3 = Web3(Web3.HTTPProvider(chain_rpc_url))
    account = w3.eth.account.from_key(private_key)
    account_address = account.address

    # Set parameters for the bridge transaction
    socket_msg_gas_limit = 20_000_000
    socket_empty_payload_size = 160

    # Retrieve the vault contract
    vault = w3.eth.contract(address=Web3.to_checksum_address(vault_address), abi=vault_abi)

    # Estimate Socket bridge fees and apply a 10% buffer
    estimated_socket_fees = vault.functions.getMinFees(
        connector_address, socket_msg_gas_limit, socket_empty_payload_size
    ).call()
    socket_fees = estimated_socket_fees * 110 // 100

    # Ensure estimated fees do not exceed the user-defined limit
    if socket_fees > params.fee_limit:
        raise BridgeFeeExceededError("Socket fee is higher than maximum allowed amount")

    # Retrieve the USDC token contract on the source chain
    chain_usdc_address = vault.functions.token().call()
    chain_usdc = w3.eth.contract(address=chain_usdc_address, abi=erc20_abi)

    # Build, sign, and send the transaction to approve USDC to be spent by the vault contract
    approval_tx = chain_usdc.functions.approve(vault_address, params.amount).build_transaction(
        {
            "from": account_address,
            "nonce": w3.eth.get_transaction_count(account_address),
        }
    )
    signed_approval_tx = w3.eth.account.sign_transaction(approval_tx, private_key=private_key)
    tx_hash = w3.eth.send_raw_transaction(signed_approval_tx.raw_transaction)
    tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    print(f"Approved USDC to vault: {tx_receipt['transactionHash'].hex()}")  # type: ignore[typeddict-item]

    # Prepare periphery contract call data for deposit
    periphery = config["w3contracts"]["periphery"]
    reya_usdc = config["w3contracts"]["usdc"]
    socket_bridge_options = Web3.to_bytes(hexstr=HexStr("0x"))
    periphery_calldata = periphery.encodeABI(fn_name="deposit", args=[(account_address, reya_usdc.address)])

    # Build, sign, and send the bridging initiation transaction
    bridge_tx = vault.functions.bridge(
        periphery.address,
        params.amount,
        socket_msg_gas_limit,
        connector_address,
        periphery_calldata,
        socket_bridge_options,
    ).build_transaction(
        {
            "from": account_address,
            "value": socket_fees,
            "nonce": w3.eth.get_transaction_count(account_address),
        }
    )
    signed_bridge_tx = w3.eth.account.sign_transaction(bridge_tx, private_key=private_key)
    tx_hash = w3.eth.send_raw_transaction(signed_bridge_tx.raw_transaction)
    tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    print(f"Initiated bridge in: {tx_receipt['transactionHash'].hex()}")  # type: ignore[typeddict-item]

    # Return transaction receipt
    return {
        "transaction_receipt": tx_receipt,
    }
