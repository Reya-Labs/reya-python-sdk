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
    print("Executed trade:", tx_receipt.transactionHash.hex())

    return {
        'transaction_receipt': tx_receipt,
    }
