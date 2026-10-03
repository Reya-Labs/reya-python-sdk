import json
import pathlib
from dataclasses import dataclass

from sdk.reya_rpc.exceptions import NetworkConfigurationError
from sdk.reya_rpc.utils.bridge_utils import calculate_socket_fees


@dataclass
class BridgeOutParams:
    """Data class to store bridge-out parameters."""

    amount: int  # Amount of rUSD to bridge out (scaled by 10^6)
    fee_limit: int  # Maximum acceptable bridging fee in ETH (scaled by 10^18)


# Load contract ABI files
with open(pathlib.Path(__file__).parent.parent/"abis/SocketControllerWithPayload.json", encoding="utf-8") as f:
    controller_abi = json.load(f)

with open(pathlib.Path(__file__).parent.parent/"abis/Erc20.json", encoding="utf-8") as f:
    erc20_abi = json.load(f)


def bridge_out_to_arbitrum(config: dict, params: BridgeOutParams):
    """
    Bridges rUSD from Reya Network to Arbitrum.

    Args:
        config (dict): Configuration dictionary containing Web3 contract instances and IDs. Check out config.py for more details.
        params (BridgeOutParams): Bridging parameters including rUSD amount and maximum fee limit.

    Returns:
        dict: Contains transaction receipt of the bridging transaction.
    """

    # Define Arbitrum-specific parameters
    connector_address = "0x3F19417872BC9F5037Bc0D40cE7389D05Cf847Ad"
    controller_address = "0x1d43076909Ca139BFaC4EbB7194518bE3638fc76"
    socket_msg_gas_limit = 20_000_000
    arbitrum_chain_id = 42161
    chain_id = config["chain_id"]

    # Ensure Reya Network is correctly configured
    if not chain_id == 1729:
        raise NetworkConfigurationError("Bridging function requires setup for Reya Network")

    # Call the general bridge function with Arbitrum parameters
    return bridge_out(
        config=config,
        params=params,
        dest_chain_id=arbitrum_chain_id,
        connector_address=connector_address,
        controller_address=controller_address,
        socket_msg_gas_limit=socket_msg_gas_limit,
    )


def bridge_out_to_arbitrum_sepolia(config: dict, params: BridgeOutParams):
    """
    Bridges rUSD from Reya Cronos to Arbitrum Sepolia.

    Args:
        config (dict): Configuration dictionary containing Web3 contract instances and IDs. Check out config.py for more details.
        params (BridgeOutParams): Bridging parameters including rUSD amount and maximum fee limit.

    Returns:
        dict: Contains transaction receipt of the bridging transaction.
    """

    # Define Arbitrum-specific parameters
    connector_address = "0x41CC670dae3f91160f6B64AF46e939223E5C99F9"
    controller_address = "0xf565F766EcafEE809EBaF0c71dCd60ad5EfE0F9e"
    socket_msg_gas_limit = 20_000_000
    arbitrum_chain_id = 421614
    chain_id = config["chain_id"]

    # Ensure Reya Network is correctly configured
    if not chain_id == 89346162:
        raise NetworkConfigurationError("Bridging function requires setup for Reya Cronos")

    # Call the general bridge function with Arbitrum parameters
    return bridge_out(
        config=config,
        params=params,
        dest_chain_id=arbitrum_chain_id,
        connector_address=connector_address,
        controller_address=controller_address,
        socket_msg_gas_limit=socket_msg_gas_limit,
    )


def _calculate_bridge_out_fees(
    controller_address: str,
    connector_address: str,
    socket_msg_gas_limit: int,
    config: dict,
    params: BridgeOutParams,
):
    """Calculate and validate bridge out fees."""
    w3 = config["w3"]
    socket_empty_payload_size = 160

    controller = w3.eth.contract(address=controller_address, abi=controller_abi)
    socket_fees = calculate_socket_fees(
        controller, connector_address, socket_msg_gas_limit, socket_empty_payload_size, params.fee_limit
    )

    return socket_fees


def _approve_rusd_spending(config: dict, params: BridgeOutParams):
    """Approve rUSD to be spent by the periphery contract."""
    w3 = config["w3"]
    account = config["w3account"]
    periphery = config["w3contracts"]["periphery"]
    rusd = config["w3contracts"]["rusd"]

    tx_hash = rusd.functions.approve(periphery.address, params.amount).transact({"from": account.address})
    tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    print(f"Approved rUSD to periphery: {tx_receipt.transactionHash.hex()}")


def _execute_bridge_out_withdrawal(
    config: dict, params: BridgeOutParams, dest_chain_id: int, socket_msg_gas_limit: int, socket_fees: int
):
    """Execute the bridge out withdrawal transaction."""
    w3 = config["w3"]
    account = config["w3account"]
    periphery = config["w3contracts"]["periphery"]
    rusd = config["w3contracts"]["rusd"]

    tx_hash = periphery.functions.withdraw(
        (
            params.amount,
            rusd.address,
            socket_msg_gas_limit,
            dest_chain_id,
            account.address,
        )
    ).transact({"from": account.address, "value": socket_fees})

    tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    print(f"Initiated bridge out: {tx_receipt.transactionHash.hex()}")
    return tx_receipt


def bridge_out(
    config: dict,
    params: BridgeOutParams,
    dest_chain_id: int,
    connector_address: str,
    controller_address: str,
    socket_msg_gas_limit: int,
):
    """
    Bridges rUSD from Reya Network to an external chain.

    Args:
        config (dict): Configuration dictionary containing Web3 contract instances and account details.
        params (BridgeOutParams): Bridging parameters including rUSD amount and maximum fee limit.
        dest_chain_id (int): ID of the destination blockchain.
        connector_address (str): Address of the connector contract on the destination chain.
        controller_address (str): Address of the Socket controller contract on the destination chain.
        socket_msg_gas_limit (int): Gas limit for the socket bridge transaction.

    Returns:
        dict: Contains transaction receipt of the bridge-out transaction.
    """
    # Calculate and validate fees
    socket_fees = _calculate_bridge_out_fees(
        controller_address, connector_address, socket_msg_gas_limit, config, params
    )

    # Approve rUSD spending
    _approve_rusd_spending(config, params)

    # Execute the withdrawal
    tx_receipt = _execute_bridge_out_withdrawal(config, params, dest_chain_id, socket_msg_gas_limit, socket_fees)

    return {"transaction_receipt": tx_receipt}
