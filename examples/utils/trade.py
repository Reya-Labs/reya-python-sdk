from dataclasses import dataclass
from examples.utils.consts import CommandType
from eth_abi import encode
from examples.utils.get_core_nonce import get_core_sig_nonce
from examples.utils.sign import sign_core_commands
from time import time

@dataclass
class MatchOrderParams:
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

def trade(configs: dict, params: MatchOrderParams) -> bool:
    try:
        # Get current core signature nonce
        w3 = configs['w3']
        core = configs['w3core']
        account_address = configs['w3account'].address

        core_sig_nonce = get_core_sig_nonce(core=core, account_id=params.account_id)
        
        command_args = encode_core_match_order(
            configs=configs, 
            params=params,
            core_sig_nonce=core_sig_nonce
        )
        
        tx_hash = core.functions.executeBySig(*command_args).transact({'from': account_address})
        tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
        print("Trade executed:", tx_receipt)

        return True
    except Exception as e:
        print("Failed to execute trade:", e)
        return False

def encode_core_match_order(configs: dict, params: MatchOrderParams, core_sig_nonce: int):
    pool_id = configs['pool_id']
    exchange_id = configs['exchange_id']
    account = configs['w3account']
    core_proxy_address = configs['core_proxy_address']
    chain_id = configs['chain_id']

    counterparty_ids: list = [pool_id]
    extra_data = encode([], [])  # empty for this example

    trade_inputs_encoded = encode(['int256', 'uint256'], [params.base, params.price_limit])
    match_order_inputs_encoded = encode(
        ['uint128[]', 'bytes'], [counterparty_ids, trade_inputs_encoded])

    command = (CommandType.MatchOrder.value,
               match_order_inputs_encoded, params.market_id, exchange_id)
    commands: list = [command]

    # Get EIP712 signature from margin account owner
    sig = sign_core_commands(
        signer=account,
        reya_chain_id=chain_id,
        caller=account.address,
        account_id=params.account_id,
        commands=commands,
        nonce=core_sig_nonce + 1,
        deadline=int(time()) + 60 * 5,  # 5 mins buffer
        extra_signature_data=extra_data,
        core_proxy_address=core_proxy_address
    )

    return [params.account_id, commands, sig, extra_data]
