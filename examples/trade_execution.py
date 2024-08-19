from web3 import Web3
from eth_abi import encode
from enum import Enum
import json
import os
import asyncio
from typing import List, Tuple
from dotenv import load_dotenv

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

async def execute_trade(core_proxy_address, multicall3_address, oracle_adapter_proxy_address, base, price_limit, market_id, account_id, price_payloads): 
    with open('./abis/CoreProxy.json') as f:
        core_abi = json.load(f)
    if core_abi == None:
        print("failed to get Core ABI")
        pass

    core_proxy = w3.eth.contract(address=core_proxy_address, abi=core_abi)
    trade_inputs_encoded = encode(['uint256', 'uint256'], [base, price_limit])
    command : tuple = (1, trade_inputs_encoded, market_id, EXCHANGE_ID)
    commands : list = [command]
    core_execution_calldata = core_proxy.encode_abi(fn_name="execute", args=[account_id, commands])

    print("core execution calldata", core_execution_calldata)

    # Load the ABI from a file
    with open('./abis/Multicall3.json') as f:
        multicall3_abi = json.load(f)
        multicall3 = w3.eth.contract(address=multicall3_address, abi=multicall3_abi)
        
        oracle_update_calldata = encode_price_update_calls(price_payloads, oracle_adapter_proxy_address, multicall3)
        calls = [(core_proxy_address, core_execution_calldata), (multicall3_address, oracle_update_calldata)]
        # tx_calldata = multicall3.encode_abi(fn_name="tryAggregate", args=[True, calls])

        # Private key of the sender (never share your private key!)
        private_key = os.environ['PRIVATE_KEY']
        account = w3.eth.account.from_key(private_key)
        w3.eth.default_account = account.address
        w3.middleware_onion.add(construct_sign_and_send_raw_middleware(account))

        res = multicall3.functions.tryAggregate(True, calls).call({'from': account.address})
        print(res)


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
        base=10000,
        price_limit=10000000000000000000,
        market_id=1,
        account_id=12,
        price_payloads=[{
            'oraclePubKey': '0x51aa9e9c781f85a2c0636a835eb80114c4553098',
            'pricePayload': {
                'assetPairId': 'ETHUSD',
                'timestamp': 1724057728439000000,
                'price': 2613205161000000000000,
            },
            'r': '0x0aa1a1df113ae500abe3e78bddef5bc40f5ffaf65c6c8e5d571b3885027edeab',
            's': '0x4700efd0b8fe5e1a84e617bc217701c142bf81b663a260efccbdc8b4fdc239f9',
            'v': 27,
        }]
    )

if __name__ == "__main__":
    print("... Start")
    asyncio.run(main())
    print("... End")