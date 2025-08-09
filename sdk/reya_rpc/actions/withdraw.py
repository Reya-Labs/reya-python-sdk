from dataclasses import dataclass

from eth_abi import encode
from web3.types import TxReceipt

from sdk.reya_rpc.types import CommandType
from sdk.reya_rpc.utils.execute_core_commands import execute_core_commands


@dataclass
class WithdrawParams:
    """Data class to store withdrawal parameters."""

    account_id: int  # ID of the margin account performing the withdrawal
    amount: int  # Withdrawal amount in rUSD (scaled by 10^6)


def withdraw(config: dict, params: WithdrawParams) -> dict[str, TxReceipt]:
    """
    Withdraws rUSD from a margin account on Reya DEX.

    Args:
        config (dict): Configuration dictionary containing Web3 contract instances and IDs. Check out config.py for more details.
        params (WithdrawParams): Withdrawal parameters including margin account ID and withdrawal amount.

    Returns:
        dict: Contains transaction receipt of the withdrawal transaction.
    """

    # Retrieve rUSD contract from config
    rusd = config["w3contracts"]["rusd"]

    # Encode withdrawal parameters for the contract call
    inputs_encoded = encode(["(address,uint256)"], [[rusd.address, params.amount]])

    # Build the withdrawal command to be executed using core
    command = (CommandType.Withdraw.value, inputs_encoded, 0, 0)
    commands: list = [command]

    # Execute the withdrawal transaction
    tx_receipt = execute_core_commands(config, params.account_id, commands)
    print(f"Withdrawn from margin account: {tx_receipt['transactionHash'].hex()}")

    # Return transaction receipt
    return {
        "transaction_receipt": tx_receipt,
    }
