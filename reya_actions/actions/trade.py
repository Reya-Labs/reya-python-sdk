from web3 import Web3
from hexbytes import HexBytes
from dataclasses import dataclass
from eth_abi import encode
from reya_actions.types import CommandType
from reya_actions.utils.execute_core_commands import execute_core_commands

@dataclass
class TradeParams:
    account_id: int
    market_id: int
    base: float
    price_limit: float

def trade(config: dict, params: TradeParams):
    passive_perp = config['w3contracts']['passive_perp']
    passive_pool_account_id = config['passive_pool_account_id']
    exchange_id = config['exchange_id']

    counterparty_ids: list = [passive_pool_account_id]
    trade_inputs_encoded = encode(['int256', 'uint256'], [params.base, params.price_limit])
    match_order_inputs_encoded = encode(
        ['uint128[]', 'bytes'], [counterparty_ids, trade_inputs_encoded])

    command = (
        CommandType.MatchOrder.value,
        match_order_inputs_encoded, 
        params.market_id, 
        exchange_id
    )

    commands: list = [command]

    tx_receipt = execute_core_commands(config, params.account_id, commands)
    print(f'Executed trade: ${tx_receipt.transactionHash.hex()}')

    # Decode the logs to get the resulting shares amount
    logs = tx_receipt["logs"]
    event_sig = Web3.keccak(text="PassivePerpMatchOrder(uint128,uint128,int256,(uint256,uint256,uint256,int256[],uint256),uint256,uint128,uint256)").hex()
    filtered_logs = [log for log in logs if HexBytes(log["topics"][0]) == HexBytes(event_sig)]

    if not len(filtered_logs) == 1:
        raise Exception("Failed to decode transaction receipt for trade")
    
    event = passive_perp.events.PassivePerpMatchOrder().process_log(filtered_logs[0])
    execution_price = int(event["args"]["executedOrderPrice"])
    fees = int(event["args"]["matchOrderFees"]["takerFeeDebit"])

    return {
        'transaction_receipt': tx_receipt,
        'execution_price': execution_price,
        'fees': fees,
    }
