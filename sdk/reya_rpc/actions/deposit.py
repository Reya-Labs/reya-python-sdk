from dataclasses import dataclass

from eth_abi import encode

from sdk.reya_rpc.types import CommandType
from sdk.reya_rpc.utils.execute_core_commands import execute_core_commands


@dataclass
class DepositParams:
    """Data class to store deposit parameters."""

    account_id: int  # ID of the margin account receiving the deposit
    amount: int  # Deposit amount in rUSD (scaled by 10^6)


def deposit(config: dict, params: DepositParams):
    """
    Deposits rUSD into a margin account on Reya DEX.

    Args:
        config (dict): Configuration dictionary containing Web3 contract instances and IDs. Check out config.py for more details.
        params (DepositParams): Deposit parameters including margin account ID and deposit amount.

    Returns:
        dict: Contains transaction receipt of the deposit transaction.
    """

    # Retrieve relevant fields from config
    w3 = config["w3"]
    account = config["w3account"]
    core = config["w3contracts"]["core"]
    rusd = config["w3contracts"]["rusd"]

    # Execute the transaction to approve rUSD to be spent by the core contract
    tx_hash = rusd.functions.approve(core.address, params.amount).transact({"from": account.address})
    tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    print(f"Approved rUSD to core: {tx_receipt['transactionHash'].hex()}")

    # Encode deposit parameters for the contract call
    inputs_encoded = encode(["(address,uint256)"], [[rusd.address, params.amount]])

    # Build the deposit command to be executed using core
    command = (CommandType.Deposit.value, inputs_encoded, 0, 0)
    commands: list = [command]

    # Execute the deposit transaction
    tx_receipt = execute_core_commands(config, params.account_id, commands)
    print(f"Deposited to margin account: {tx_receipt['transactionHash'].hex()}")

    # Return transaction receipt
    return {
        "transaction_receipt": tx_receipt,
    }
