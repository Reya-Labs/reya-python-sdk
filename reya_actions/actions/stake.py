from web3 import Web3
from hexbytes import HexBytes
from dataclasses import dataclass


@dataclass
class StakingParams:
    """Data class to store staking parameters."""

    token_amount: int  # Amount of rUSD to stake (scaled by 10^6)
    min_shares: int  # Minimum amount of liquidity shares expected from the stake (scaled by 10^30)


def stake(config: dict, params: StakingParams):
    """
    Stakes rUSD into the passive pool on Reya DEX.

    Args:
        config (dict): Configuration dictionary containing Web3 contract instances and IDs. Check out config.py for more details.
        params (StakingParams): Staking parameters including rUSD amount and minimum liquidity shares expected.

    Returns:
        dict: Contains transaction receipt and the amount of liquidity shares received.
    """

    # Retrieve relevant fields from config
    w3 = config["w3"]
    account = config["w3account"]
    passive_pool = config["w3contracts"]["passive_pool"]
    rusd = config["w3contracts"]["rusd"]

    # Execute the transaction to approve rUSD to be spent by the passive pool contract
    tx_hash = rusd.functions.approve(
        passive_pool.address, params.token_amount
    ).transact({"from": account.address})
    tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    print(f"Approved rUSD to core: {tx_receipt.transactionHash.hex()}")

    # Stake rUSD in the passive pool
    tx_hash = passive_pool.functions.addLiquidity(
        1, account.address, params.token_amount, params.min_shares, (1, account.address)
    ).transact({"from": account.address})
    tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    print(f"Staked in passive pool: {tx_receipt.transactionHash.hex()}")

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

    # Decode event log to extract the received liquidity shares amount
    event = passive_pool.events.ShareBalanceUpdated().process_log(filtered_logs[0])
    shares_amount = int(event["args"]["shareDelta"])

    # Return transaction receipt and received shares amount (scaled by 10^30)
    return {
        "transaction_receipt": tx_receipt,
        "shares_amount": shares_amount,
    }
