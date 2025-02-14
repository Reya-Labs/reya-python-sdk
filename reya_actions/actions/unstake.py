from web3 import Web3
from hexbytes import HexBytes
from dataclasses import dataclass


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
        1, params.shares_amount, params.min_tokens
    ).transact({"from": account.address})
    tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    print(f"Unstaked from passive pool: {tx_receipt.transactionHash.hex()}")

    # Extract logs from the transaction receipt
    logs = tx_receipt["logs"]

    # Compute event signature for filtering relevant log
    event_sig = Web3.keccak(
        text="ShareBalanceUpdated(uint128,address,int256,uint256,int256,uint256,address,int256)"
    ).hex()

    # Filter logs for the expected event
    filtered_logs = [
        log for log in logs if HexBytes(log["topics"][0]) == HexBytes(event_sig)
    ]

    # Ensure exactly one matching event log is found
    if not len(filtered_logs) == 1:
        raise Exception(
            "Failed to decode transaction receipt for staking to passive pool"
        )

    # Decode event log to extract the received rUSD amount
    event = passive_pool.events.ShareBalanceUpdated().process_log(filtered_logs[0])
    token_amount = -int(event["args"]["tokenDelta"])

    # Return transaction receipt and received rUSD amount (scaled by 10^6)
    return {
        "transaction_receipt": tx_receipt,
        "token_amount": token_amount,
    }
