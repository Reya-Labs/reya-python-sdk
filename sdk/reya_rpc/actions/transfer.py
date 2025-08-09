from dataclasses import dataclass

from eth_abi import encode

from sdk.reya_rpc.types import CommandType
from sdk.reya_rpc.utils.execute_core_commands import execute_core_commands


@dataclass
class TransferParams:
    """Data class to store transfer parameters."""

    account_id: int  # ID of the margin account sending the funds
    amount: int  # Transfer amount in rUSD (scaled by 10^6)
    to_account_id: int  # ID of the margin account receiving the funds


def transfer(config: dict, params: TransferParams):
    """
    Transfers rUSD between margin accounts on Reya DEX.

    Args:
        config (dict): Configuration dictionary containing Web3 contract instances and IDs. Check out config.py for more details.
        params (TransferParams): Transfer parameters including sender account ID, receiver account ID, and transfer amount.

    Returns:
        dict: Contains transaction receipt of the transfer transaction.
    """

    # Retrieve rUSD contract from config
    rusd = config["w3contracts"]["rusd"]

    # Encode transfer parameters for the contract call
    inputs_encoded = encode(
        ["(uint128,address,uint256)"],
        [[params.to_account_id, rusd.address, params.amount]],
    )

    # Build the transfer command to be executed using core
    command = (CommandType.TransferBetweenMarginAccounts.value, inputs_encoded, 0, 0)
    commands: list = [command]

    # Execute the transfer transaction
    tx_receipt = execute_core_commands(config, params.account_id, commands)
    print(f"Transferred rUSD between margin accounts: {tx_receipt['transactionHash'].hex()}")

    # Return transaction receipt
    return {
        "transaction_receipt": tx_receipt,
    }
