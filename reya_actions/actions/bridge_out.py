from dataclasses import dataclass
import json


@dataclass
class BridgeOutParams:
    """Data class to store bridge-out parameters."""

    amount: int  # Amount of rUSD to bridge out (scaled by 10^6)
    fee_limit: int  # Maximum acceptable bridging fee in ETH (scaled by 10^18)


# Load contract ABI files
f = open("reya_actions/abis/SocketControllerWithPayload.json")
controller_abi = json.load(f)

f = open("reya_actions/abis/Erc20.json")
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
    socket_msg_gas_limit = 20_000_000
    arbitrum_chain_id = 42161
    chain_id = config["chain_id"]

    # Ensure Reya Network is correctly configured
    if not chain_id == 1729:
        raise Exception("Bridging function requires setup for Reya Network")

    # Call the general bridge function with Arbitrum parameters
    return bridge_out(
        config=config,
        params=params,
        dest_chain_id=arbitrum_chain_id,
        connector_address=connector_address,
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
    socket_msg_gas_limit = 20_000_000
    arbitrum_chain_id = 421614
    chain_id = config["chain_id"]

    # Ensure Reya Network is correctly configured
    if not chain_id == 89346162:
        raise Exception("Bridging function requires setup for Reya Cronos")

    # Call the general bridge function with Arbitrum parameters
    return bridge_out(
        config=config,
        params=params,
        dest_chain_id=arbitrum_chain_id,
        connector_address=connector_address,
        socket_msg_gas_limit=socket_msg_gas_limit,
    )

def bridge_out(
    config: dict,
    params: BridgeOutParams,
    dest_chain_id: int,
    connector_address: str,
    socket_msg_gas_limit: int,
):
    """
    Bridges rUSD from Reya Network to an external chain.

    Args:
        config (dict): Configuration dictionary containing Web3 contract instances and account details.
        params (BridgeOutParams): Bridging parameters including rUSD amount and maximum fee limit.
        dest_chain_id (int): ID of the destination blockchain.
        connector_address (str): Address of the connector contract on the destination chain.
        socket_msg_gas_limit (int): Gas limit for the socket bridge transaction.

    Returns:
        dict: Contains transaction receipt of the bridge-out transaction.
    """

    # Retrieve relevant fields from config
    w3 = config["w3"]
    account = config["w3account"]
    periphery = config["w3contracts"]["periphery"]
    rusd = config["w3contracts"]["rusd"]

    # Set parameters for the bridge transaction
    controller_address = "0x1d43076909Ca139BFaC4EbB7194518bE3638fc76"
    socket_empty_payload_size = 160

    # Build the Socket controller contract
    controller = w3.eth.contract(address=controller_address, abi=controller_abi)

    # Estimate Socket bridge fees and apply a 10% buffer
    estimated_socket_fees = controller.functions.getMinFees(
        connector_address, socket_msg_gas_limit, socket_empty_payload_size
    ).call()
    socket_fees = estimated_socket_fees * 110 // 100

    # Ensure estimated fees do not exceed the user-defined limit
    if socket_fees > params.fee_limit:
        raise Exception("Socket fee is higher than maximum allowed amount")

    # Execute the transaction to approve rUSD to be spent by the periphery
    tx_hash = rusd.functions.approve(periphery.address, params.amount).transact(
        {"from": account.address}
    )
    tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    print(f"Approved rUSD to periphery: {tx_receipt.transactionHash.hex()}")

    # Execute the bridging transaction
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

    # Return transaction receipt
    return {
        "transaction_receipt": tx_receipt,
    }
