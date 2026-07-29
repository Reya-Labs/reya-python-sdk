from typing import Optional

from dataclasses import dataclass

from eth_abi import encode

from sdk.reya_rpc.types import CommandType
from sdk.reya_rpc.utils.collateral_token import resolve_collateral_token
from sdk.reya_rpc.utils.execute_core_commands import execute_core_commands


@dataclass
class DepositParams:
    """Data class to store deposit parameters."""

    account_id: int  # ID of the margin account receiving the deposit
    amount: int  # Deposit amount, scaled by the token's own decimals (10^6 for rUSD)
    token_address: Optional[str] = None  # Collateral token; defaults to the configured rUSD


def deposit(config: dict, params: DepositParams):
    """
    Deposits collateral into a margin account on Reya DEX.

    The token defaults to the rUSD instance in ``config``. Deployments whose collateral is a
    different address must pass ``token_address``: on a local deployment, depositing the wrong
    token was observed to succeed and create a balance row that the risk engine did not count,
    leaving the account looking funded while margining at zero, with every order rejected for
    insufficient margin. The SDK cannot detect that for you - see ``utils/collateral_token.py``
    for why - so the caller owns this choice, and ``amount`` is scaled by the chosen token's
    decimals.

    Args:
        config (dict): Configuration dictionary containing Web3 contract instances and IDs. Check out config.py for more details.
        params (DepositParams): Deposit parameters including margin account ID, deposit amount, and optional collateral token.

    Returns:
        dict: Contains transaction receipt of the deposit transaction.

    Raises:
        InvalidTokenAddressError: If ``token_address`` is not a valid address. Raised before
            any funds move.
    """

    # Retrieve relevant fields from config
    w3 = config["w3"]
    account = config["w3account"]
    core = config["w3contracts"]["core"]

    # Resolve the collateral token before approving or spending anything
    token = resolve_collateral_token(config, params.token_address)

    # Execute the transaction to approve the collateral token to be spent by the core contract
    tx_hash = token.functions.approve(core.address, params.amount).transact({"from": account.address})
    tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    print(f"Approved {token.address} to core: {tx_receipt['transactionHash'].hex()}")

    # Encode deposit parameters for the contract call
    inputs_encoded = encode(["(address,uint256)"], [[token.address, params.amount]])

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
