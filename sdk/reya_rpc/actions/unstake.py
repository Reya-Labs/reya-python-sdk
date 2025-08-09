from dataclasses import dataclass

from sdk.reya_rpc.utils.transaction_utils import extract_share_balance_updated_event


@dataclass
class UnstakingParams:
    """Data class to store unstaking parameters."""

    shares_amount: int  # Amount of liquidity shares to redeem (scaled by 10^30)
    min_tokens: int  # Minimum amount of rUSD expected from unstaking (scaled by 10^6)


def unstake(config: dict, params: UnstakingParams):
    """
    Unstakes rUSD from the passive pool on Reya DEX.

    Args:
        config (dict): Configuration dictionary containing Web3 contract instances and IDs. Check out config.py for more details.
        params (UnstakingParams): Unstaking parameters including liquidity shares amount and minimum rUSD expected.

    Returns:
        dict: Contains transaction receipt and the amount of rUSD received from unstaking.
    """

    # Retrieve relevant fields from config
    w3 = config["w3"]
    account = config["w3account"]
    passive_pool = config["w3contracts"]["passive_pool"]

    # Unstake rUSD from the passive pool
    tx_hash = passive_pool.functions.removeLiquidity(
        1, params.shares_amount, params.min_tokens, (2, account.address)
    ).transact({"from": account.address})
    tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    print(f"Unstaked from passive pool: {tx_receipt.transactionHash.hex()}")

    # Extract event data using shared utility
    _, balance_delta = extract_share_balance_updated_event(tx_receipt, passive_pool)
    token_amount = -balance_delta

    # Return transaction receipt and received rUSD amount (scaled by 10^6)
    return {
        "transaction_receipt": tx_receipt,
        "token_amount": token_amount,
    }
