from typing import Optional

from dataclasses import dataclass

from eth_abi import encode
from web3.types import TxReceipt

from sdk.reya_rpc.types import CommandType
from sdk.reya_rpc.utils.collateral_token import resolve_collateral_token
from sdk.reya_rpc.utils.execute_core_commands import execute_core_commands


@dataclass
class WithdrawParams:
    """Data class to store withdrawal parameters."""

    account_id: int  # ID of the margin account performing the withdrawal
    amount: int  # Withdrawal amount, scaled by the token's own decimals (10^6 for rUSD)
    token_address: Optional[str] = None  # Collateral token; defaults to the configured rUSD


def withdraw(config: dict, params: WithdrawParams) -> dict[str, TxReceipt]:
    """
    Withdraws collateral from a margin account on Reya DEX.

    Mirrors :func:`~sdk.reya_rpc.actions.deposit.deposit`: the token defaults to the configured
    rUSD, and deployments using a different collateral token pass it explicitly. This is also
    the recovery path for funds already deposited under a non-default token.

    Args:
        config (dict): Configuration dictionary containing Web3 contract instances and IDs. Check out config.py for more details.
        params (WithdrawParams): Withdrawal parameters including margin account ID, withdrawal amount, and optional collateral token.

    Returns:
        dict: Contains transaction receipt of the withdrawal transaction.

    Raises:
        InvalidTokenAddressError: If ``token_address`` is not a valid address.
    """

    # Resolve the collateral token before building the command
    token = resolve_collateral_token(config, params.token_address)

    # Encode withdrawal parameters for the contract call
    inputs_encoded = encode(["(address,uint256)"], [[token.address, params.amount]])

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
