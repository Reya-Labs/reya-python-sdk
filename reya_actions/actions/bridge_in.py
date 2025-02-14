from dataclasses import dataclass
import json
from web3 import Web3
from web3.middleware import construct_sign_and_send_raw_middleware


@dataclass
class BridgeInParams:
    """Data class to store bridge-in parameters."""

    amount: int  # Amount of USDC to bridge (scaled by 10^6)
    fee_limit: int  # Maximum acceptable bridging fee in native token (scaled by 10^18)


# Load contract ABI files
f = open("reya_actions/abis/SocketVaultWithPayload.json")
vault_abi = json.load(f)

f = open("reya_actions/abis/Erc20.json")
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
        raise Exception("Bridging function requires setup for Reya Network")

    # Call the general bridge function with Arbitrum parameters
    return bridge_in(
        config=config,
        params=params,
        chain_rpc_url=arbitrum_rpc_url,
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

    # Create Web3 instance for the specified chain and configure the account
    w3 = Web3(Web3.HTTPProvider(chain_rpc_url))
    w3account = w3.eth.account.from_key(private_key)
    account_address = w3account.address
    w3.eth.default_account = account_address
    w3.middleware_onion.add(construct_sign_and_send_raw_middleware(w3account))

    # Set parameters for the bridge transaction
    socket_msg_gas_limit = 20_000_000
    socket_empty_payload_size = 160

    # Retrieve the vault contract
    vault = w3.eth.contract(address=vault_address, abi=vault_abi)

    # Estimate Socket bridge fees and apply a 10% buffer
    estimated_socket_fees = vault.functions.getMinFees(
        connector_address, socket_msg_gas_limit, socket_empty_payload_size
    ).call()
    socket_fees = estimated_socket_fees * 110 // 100

    # Ensure estimated fees do not exceed the user-defined limit
    if socket_fees > params.fee_limit:
        raise Exception("Socket fee is higher than maximum allowed amount")

    # Retrieve the USDC token contract on the source chain
    chain_usdc_address = vault.functions.token().call()
    chain_usdc = w3.eth.contract(address=chain_usdc_address, abi=erc20_abi)

    # Execute the transaction to approve USDC to be spent by the vault contract
    tx_hash = chain_usdc.functions.approve(vault_address, params.amount).transact(
        {"from": account_address}
    )
    tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    print(f"Approved USDC to vault: {tx_receipt.transactionHash.hex()}")

    # Prepare periphery contract call data for deposit
    periphery = config["w3contracts"]["periphery"]
    reya_usdc = config["w3contracts"]["usdc"]
    socket_bridge_options = Web3.to_bytes(hexstr="0x")
    periphery_calldata = periphery.encodeABI(
        fn_name="deposit", args=[(account_address, reya_usdc.address)]
    )

    # Execute the bridging initiation transaction
    tx_hash = vault.functions.bridge(
        periphery.address,
        params.amount,
        socket_msg_gas_limit,
        connector_address,
        periphery_calldata,
        socket_bridge_options,
    ).transact({"from": account_address, "value": socket_fees})

    tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    print(f"Initiated bridge in: {tx_receipt.transactionHash.hex()}")

    # Return transaction receipt
    return {
        "transaction_receipt": tx_receipt,
    }
