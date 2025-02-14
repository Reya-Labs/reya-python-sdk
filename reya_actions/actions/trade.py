from web3 import Web3
from hexbytes import HexBytes
from dataclasses import dataclass
from eth_abi import encode
from reya_actions.types import CommandType
from reya_actions.utils.execute_core_commands import execute_core_commands


@dataclass
class TradeParams:
    """Data class to store trade parameters."""

    account_id: int  # ID of the margin account executing the trade
    market_id: int  # ID of the market where the trade occurs
    base: int  # Trade amount in base units (scaled by 10^18)
    price_limit: int  # Maximum price the trader is willing to pay (scaled by 10^18)


def trade(config: dict, params: TradeParams):
    """
    Executes a trade on Reya DEX.

    Args:
        config (dict): Configuration dictionary containing Web3 contract instances and IDs. Check out config.py for more details.
        params (TradeParams): Trade parameters including margin account ID, market ID, base amount, and price limit.

    Returns:
        dict: Contains transaction receipt, execution price (scaled by 10^18), and fees in rUSD terms (scaled by 10^6).
    """

    # Retrieve relevant fields from config
    passive_perp = config["w3contracts"]["passive_perp"]
    passive_pool_account_id = config["passive_pool_account_id"]
    exchange_id = config["exchange_id"]

    # Define counterparty IDs (in this case, only the passive pool)
    counterparty_ids: list = [passive_pool_account_id]

    # Encode trade parameters for the contract call
    trade_inputs_encoded = encode(
        ["int256", "uint256"], [params.base, params.price_limit]
    )
    match_order_inputs_encoded = encode(
        ["uint128[]", "bytes"], [counterparty_ids, trade_inputs_encoded]
    )

    # Build the trade command to be executed using core
    command = (
        CommandType.MatchOrder.value,
        match_order_inputs_encoded,
        params.market_id,
        exchange_id,
    )
    commands: list = [command]

    # Execute the trade transaction
    tx_receipt = execute_core_commands(config, params.account_id, commands)
    print(f"Executed trade: {tx_receipt.transactionHash.hex()}")

    # Extract logs from the transaction receipt
    logs = tx_receipt["logs"]

    # Compute event signature for filtering relevant log
    event_sig = Web3.keccak(
        text="PassivePerpMatchOrder(uint128,uint128,int256,(uint256,uint256,uint256,int256[],uint256),uint256,uint128,uint256)"
    ).hex()

    # Filter logs for the expected event
    filtered_logs = [
        log for log in logs if HexBytes(log["topics"][0]) == HexBytes(event_sig)
    ]

    # Ensure exactly one matching event log is found
    if not len(filtered_logs) == 1:
        raise Exception("Failed to decode transaction receipt for trade")

    # Decode event log to extract execution details
    event = passive_perp.events.PassivePerpMatchOrder().process_log(filtered_logs[0])
    execution_price = int(event["args"]["executedOrderPrice"])
    fees = int(event["args"]["matchOrderFees"]["takerFeeDebit"])

    # Return trade execution details
    return {
        "transaction_receipt": tx_receipt,
        "execution_price": execution_price,
        "fees": fees,
    }
