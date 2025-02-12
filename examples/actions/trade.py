from dataclasses import dataclass
from examples.utils import execute_core_commands
from examples.utils.consts import CommandType
from eth_abi import encode

@dataclass
class TradeParams:
    account_id: int
    market_id: int
    base: float
    price_limit: float

''' Executes an on-chain trade

To execute an on-chain trade, prices need to be updated very closely to the trade to prevet price staleness.
This function appends the oracle price updates to a trade transaction and executes them together. 
Because the msg.sender will not be the account owner anymore, the trade is authorised using the owner's signature.

To encode the transaction, these steps are followed:
- encode trade command and sign the command using the margin account owner's private key
- encode call to 'executeBySig' function of Reya Core, pass the command and signature as arguments
- aggregate all price updates into an optional Mulicall2 'tryAggregatePreservingError', get calldata
- aggregate the Multicall oracle updates and the Reya Core call into a strict an optional Mulicall 'tryAggregatePreservingError'
'''

def trade(configs: dict, params: TradeParams) -> bool:
    passive_pool_account_id = configs['passive_pool_account_id']
    exchange_id = configs['exchange_id']

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

    tx_receipt = execute_core_commands(configs, params.account_id, commands)
    print("Trade executed:", tx_receipt)

    return tx_receipt
