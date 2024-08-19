from web3 import Web3
from eth_abi import encode
from enum import Enum
import json
import os
import asyncio
from typing import List, Tuple
from dotenv import load_dotenv
from decimal import *
from sign import sign_core_commands

load_dotenv()

from web3.middleware import construct_sign_and_send_raw_middleware

class CommandType(Enum):
    Deposit = 0
    Withdraw = 1
    DutchLiquidation = 2
    MatchOrder = 3
    TransferBetweenMarginAccounts = 4

class OracleProvider(Enum):
    Stork = 0
    Pyth = 1

w3 = Web3(Web3.HTTPProvider('https://rpc.reya-cronos.gelato.digital'))

EXCHANGE_ID = 1
WAD = 10 ** 18

async def execute_trade(core_proxy_address, multicall3_address, oracle_adapter_proxy_address, base, price_limit, market_id, account_id, price_payloads): 
    with open('./abis/CoreProxy.json') as f:
        core_abi = json.load(f)
    if core_abi == None:
        print("failed to get Core ABI")
        pass

    # Private key of the sender (never share your private key!)
    private_key = os.environ['PRIVATE_KEY']
    account = w3.eth.account.from_key(private_key)
    w3.eth.default_account = account.address
    w3.middleware_onion.add(construct_sign_and_send_raw_middleware(account))

    # Core command
    core_proxy = w3.eth.contract(address=core_proxy_address, abi=core_abi)
    trade_inputs_encoded = encode(['int256', 'uint256'], [base, price_limit])
    counterparty_ids : list = [4]
    match_order_inputs_encoded = encode(['uint128[]', 'bytes'], [counterparty_ids, trade_inputs_encoded])

    command = (CommandType.MatchOrder.value, match_order_inputs_encoded, market_id, EXCHANGE_ID)
    commands : list = [command]
    
    extra_data = encode([],[])
    sig = await sign_core_commands(
        signer=account,
        reya_chain_id=89346162,
        caller=multicall3_address,
        account_id=account_id,
        commands=commands,
        nonce=72,
        deadline=10000000000000,
        extra_signature_data=extra_data,
        core_proxy_address=core_proxy_address
    )
    core_execution_calldata = core_proxy.encode_abi(fn_name="executeBySig", args=[account_id, commands, sig, extra_data])

    # Multicall command
    with open('./abis/Multicall3.json') as f:
        multicall3_abi = json.load(f)
        multicall3 = w3.eth.contract(address=multicall3_address, abi=multicall3_abi)
        
        oracle_update_calldata = encode_price_update_calls(price_payloads, oracle_adapter_proxy_address, multicall3)
        calls = [(core_proxy_address, core_execution_calldata), (multicall3_address, oracle_update_calldata)]

        tx_hash = multicall3.functions.tryAggregate(False, calls).transact({'from': account.address})
        tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
        print("Receipt", tx_receipt)


def encode_price_update_calls(pricePayloads, oracle_adapter_proxy_address, multicall3) -> bytes:
    with open('./abis/OracleAdapterProxy.json') as f:
        oracle_adapter_abi = json.load(f)
    if oracle_adapter_abi == None:
        print("failed to get Oracle adapter ABI")
        pass

    oracle_adapter = w3.eth.contract(address=oracle_adapter_proxy_address, abi=oracle_adapter_abi)

    encoded_calls : list = []
    for update in pricePayloads:
        encoded_payload = encode(['string','uint256', 'uint256'], [update['pricePayload']['assetPairId'], update['pricePayload']['timestamp'], update['pricePayload']['price']])
        encoded_calls.append((
            oracle_adapter_proxy_address,
            oracle_adapter.encode_abi(fn_name="fulfillOracleQuery", args=[OracleProvider.Stork.value, encoded_payload])
        ))

    calldata = encode_multicall(multicall3, False, encoded_calls)

    return calldata

def encode_multicall(multicall3, require_success, calls: List[Tuple[str, bytes]]) -> str:
    calldata = multicall3.encode_abi(fn_name="tryAggregate", args=[require_success, calls])
    return calldata

async def main():
    await execute_trade(
        core_proxy_address='0xC6fB022962e1426F4e0ec9D2F8861c57926E9f72',
        multicall3_address='0xd9f0F399f5264fAac91476Dbd950574726D18633',
        oracle_adapter_proxy_address='0xc501A2356703CD351703D68963c6F4136120f7CF',
        base=-Web3.to_wei(1, 'ether'),
        price_limit=0,
        market_id=3,
        account_id=12,
        price_payloads=[{
            'oraclePubKey': '0x0a803F9b1CCe32e2773e0d2e98b37E0775cA5d44',
            'pricePayload': {
                'assetPairId': 'SOLUSD',
                'timestamp': 1724082435245083759,
                'price': 144181178943749000000,
            },
            'r': '0x66f5b1a073d52d93149b80b69bebb0bee563eebd4370c1dd9c04ff7c1d62f425',
            's': '0x6d5c4d7aad09748a2f234d09e28b6075b8103dd7e45b941d0c60093a3149fc00',
            'v': '0x1b',
        }]
    )

if __name__ == "__main__":
    print("... Start")
    asyncio.run(main())
    print("... End")