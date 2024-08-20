from web3 import Web3
from eth_abi import encode
from enum import Enum
import json
import os
import asyncio
from dotenv import load_dotenv
from decimal import *
from examples.sign import sign_core_commands
from web3.middleware import construct_sign_and_send_raw_middleware
from time import time

load_dotenv()


class CommandType(Enum):
    Deposit = 0
    Withdraw = 1
    DutchLiquidation = 2
    MatchOrder = 3
    TransferBetweenMarginAccounts = 4


class MarketIds(Enum):
    ETH = 1
    BTC = 2
    SOL = 3
    ARB = 4
    OP = 5
    AVAX = 6


class OracleProvider(Enum):
    Stork = 0
    Pyth = 1


''' Executes an on-chain trade

To execute an on-chain trade, prices need to be updated very closely to the trade to prevet price staleness.
This function appends the oracle price updates to a trade transaction and executes them together. 
Because the msg.sender will not be the account owner anymore, the trade is authorised using the owner's signature.

To encode the transaction, these steps are followed:
- encode trade command and sign the command using the margin account owner's private key
- encode call to 'executeBySig' function of Reya Core, pass the command and signature as arguments
- aggregate all price updates into an optional Mulicall2 'tryAggregate', get calldata
- aggregate the Multicall2 oracle updates and the Reya Core call into a strict an optional Mulicall2 'tryAggregate'
'''


def execute_trade(configs, base, price_limit, market_id, account_id, current_core_nonce, price_payloads):
    # Initialise provider with signer
    w3 = Web3(Web3.HTTPProvider(configs['rpc_url']))
    account = w3.eth.account.from_key(configs['private_key'])
    w3.eth.default_account = account.address
    w3.middleware_onion.add(construct_sign_and_send_raw_middleware(account))

    # Encode Core Command
    core_execution_calldata = _encode_core_match_order(
        w3, account, configs, base, price_limit, market_id, account_id, current_core_nonce)

    # Encode Multicall2 oracle updates
    multicall2 = w3.eth.contract(
        address=configs['multicall2_address'], abi=configs['multicall_abi'])
    oracle_update_calldata = _encode_price_update_calls(
        w3, configs['oracle_abi'], price_payloads, configs['oracle_adapter_proxy_address'], multicall2)

    # Aggregate oracle updates and match order
    calls = [(configs['multicall2_address'], oracle_update_calldata),
             (configs['core_proxy_address'], core_execution_calldata)]
    # Success is required
    # tx_hash = multicall2.functions.tryAggregate(True, calls).transact({'from': account.address})
    # tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    # print("Receipt", tx_receipt)

    # Simulation example
    res = multicall2.functions.tryAggregate(
        False, calls).call({'from': account.address})
    (succes1, output1) = res[0]
    (succes2, output2) = res[1]
    print("Result from oracle updates", succes1, output1.hex())
    print("Result from trade", succes2, output2.hex())


def _encode_core_match_order(w3, account, configs, base, price_limit, market_id, account_id, current_core_nonce):
    core_proxy = w3.eth.contract(
        address=configs['core_proxy_address'], abi=configs['core_abi'])

    counterparty_ids: list = [configs['pool_id']]
    extra_data = encode([], [])  # empty for this example

    trade_inputs_encoded = encode(['int256', 'uint256'], [base, price_limit])
    match_order_inputs_encoded = encode(
        ['uint128[]', 'bytes'], [counterparty_ids, trade_inputs_encoded])

    command = (CommandType.MatchOrder.value,
               match_order_inputs_encoded, market_id, configs['exchange_id'])
    commands: list = [command]

    # Get EIP712 signature from margin account owner
    sig = sign_core_commands(
        signer=account,
        reya_chain_id=configs['chain_id'],
        caller=configs['multicall2_address'],
        account_id=account_id,
        commands=commands,
        nonce=current_core_nonce + 1,
        deadline=int(time()) + 60 * 5,  # 5 mins buffer
        extra_signature_data=extra_data,
        core_proxy_address=configs['core_proxy_address']
    )

    return core_proxy.encode_abi(fn_name="executeBySig", args=[account_id, commands, sig, extra_data])


def _encode_price_update_calls(w3, oracle_adapter_abi, pricePayloads, oracle_adapter_proxy_address, multicall2):
    oracle_adapter = w3.eth.contract(
        address=oracle_adapter_proxy_address, abi=oracle_adapter_abi)

    # list of payloads should contain an update for every market
    encoded_calls: list = []
    for update in pricePayloads:
        payload = update['pricePayload']
        encoded_payload = encode(['string', 'uint256', 'uint256'], [
                                 payload['assetPairId'], payload['timestamp'], payload['price']])
        encoded_calls.append((
            oracle_adapter_proxy_address,
            oracle_adapter.encode_abi(fn_name="fulfillOracleQuery", args=[
                                      OracleProvider.Stork.value, encoded_payload])
        ))

    # Encode an Multicall2 call, without required success for each call
    return multicall2.encode_abi(fn_name="tryAggregate", args=[False, encoded_calls])


'''Gathering configuration from environemnt variables and ABIs'''


def getConfigs():
    chain_id = os.environ['CHAIN_ID']
    if int(chain_id) not in [1729, 89346162]:
        raise Exception("Wrong chain id")

    pool_id = 2 if chain_id == 1729 else 4
    rpc_url = 'https://rpc.reya.network' if chain_id == 1729 else 'https://rpc.reya-cronos.gelato.digital'

    f = open('examples/abis/CoreProxy.json')
    core_abi = json.load(f)

    f = open('examples/abis/Multicall2.json')
    multicall_abi = json.load(f)

    f = open('examples/abis/OracleAdapterProxy.json')
    oracle_adapter_abi = json.load(f)

    return {
        'core_proxy_address': os.environ['CORE_PROXY_ADDRESS'],
        'multicall2_address': os.environ['MULTICALL2_ADDRESS'],
        'oracle_adapter_proxy_address': os.environ['ORACLE_ADAPTER_PROXY_ADDRESS'],
        'private_key': os.environ['PRIVATE_KEY'],
        'pool_id': pool_id,
        'exchange_id': 1,
        'multicall_abi': multicall_abi,
        'oracle_abi': oracle_adapter_abi,
        'core_abi': core_abi,
        'rpc_url': rpc_url,
        'chain_id': chain_id
    }


async def main():
    '''Example trade
    This executes a short trade on account 12, of base 1 (notional = base * price).
    The price limit constrains the slippage. For simplicity, any slippage is allowed, meaning the limit
    is 0 for short trades or max uint256 for long trades.

    The price_payloads list only includes an update for the SOL market, but to avoid staleness errors,
    all markets should be included and timestamps must be within seconds of the transaction execution.
    Payloads can be queried from Reya's websocket, on the price channel. The signatures are generated by a
    trusted producer, they are verified on-chain (do not modify).
    '''
    configs = getConfigs()
    await execute_trade(
        configs=configs,
        base=-Web3.to_wei(1, 'ether'),  # WAD precision
        price_limit=0,  # WAD precision
        market_id=MarketIds.SOL.value,
        account_id=12,  # your margin account id
        current_core_nonce=72,  # sigature nonce of owner address stored in Reya Core
        price_payloads=[{
            'oraclePubKey': '0x0a803F9b1CCe32e2773e0d2e98b37E0775cA5d44',
            'pricePayload': {
                'assetPairId': 'SOLUSD',
                'timestamp': 1724082435245083759,  # in MS * 10 ^ 6
                'price': 144181178943749000000,  # WAD precision
            },
            # EIP712 signature of the price data
            'r': '0x66f5b1a073d52d93149b80b69bebb0bee563eebd4370c1dd9c04ff7c1d62f425',
            's': '0x6d5c4d7aad09748a2f234d09e28b6075b8103dd7e45b941d0c60093a3149fc00',
            'v': '0x1b',
        }]
    )

if __name__ == "__main__":
    print("... Start")
    asyncio.run(main())
    print("... End")
