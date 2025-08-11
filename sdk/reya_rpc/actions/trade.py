from dataclasses import dataclass

from eth_abi import encode
from hexbytes import HexBytes
from web3 import Web3

from sdk.reya_rpc.exceptions import TransactionReceiptError
from sdk.reya_rpc.types import CommandType
from sdk.reya_rpc.utils.execute_core_commands import execute_core_commands


@dataclass
class TradeParams:
    """Data class to store trade parameters."""

    account_id: int  # ID of the margin account executing the trade
    market_id: int  # ID of the market where the trade occurs
    base: int  # Trade amount in base units (scaled by 10^18)
    price_limit: int  # Maximum price the trader is willing to pay (scaled by 10^18)


def _build_trade_command(params: TradeParams, config: dict):
    """Build the trade command for execution."""
    passive_pool_account_id = config["passive_pool_account_id"]
    exchange_id = config["exchange_id"]

    counterparty_ids = [passive_pool_account_id]
    trade_inputs_encoded = encode(["int256", "uint256"], [params.base, params.price_limit])
    match_order_inputs_encoded = encode(["uint128[]", "bytes"], [counterparty_ids, trade_inputs_encoded])

    return (
        CommandType.MatchOrder.value,
        match_order_inputs_encoded,
        params.market_id,
        exchange_id,
    )


def _extract_trade_execution_details(tx_receipt, passive_perp):
    """Extract execution price and fees from transaction receipt."""
    event_sig = Web3.keccak(
        text="PassivePerpMatchOrder(uint128,uint128,int256,(uint256,uint256,uint256,int256[],uint256),uint256,uint128,uint256)"
    ).hex()

    filtered_logs = [log for log in tx_receipt["logs"] if HexBytes(log["topics"][0]) == HexBytes(event_sig)]

    if len(filtered_logs) != 1:
        raise TransactionReceiptError("Failed to decode transaction receipt for trade")

    event = passive_perp.events.PassivePerpMatchOrder().process_log(filtered_logs[0])
    execution_price = int(event["args"]["executedOrderPrice"])
    fees = int(event["args"]["matchOrderFees"]["takerFeeDebit"])

    return execution_price, fees


def trade(config: dict, params: TradeParams):
    """
    Executes a trade on Reya DEX.

    Args:
        config (dict): Configuration dictionary containing Web3 contract instances and IDs. Check out config.py for more details.
        params (TradeParams): Trade parameters including margin account ID, market ID, base amount, and price limit.

    Returns:
        dict: Contains transaction receipt, execution price (scaled by 10^18), and fees in rUSD terms (scaled by 10^6).
    """
    # Build the trade command
    command = _build_trade_command(params, config)

    # Execute the trade transaction
    tx_receipt = execute_core_commands(config, params.account_id, [command])
    print(f"Executed trade: {tx_receipt['transactionHash'].hex()}")

    # Extract execution details
    passive_perp = config["w3contracts"]["passive_perp"]
    execution_price, fees = _extract_trade_execution_details(tx_receipt, passive_perp)

    return {
        "transaction_receipt": tx_receipt,
        "execution_price": execution_price,
        "fees": fees,
    }
