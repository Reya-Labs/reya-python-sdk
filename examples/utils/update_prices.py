import math
from web3 import Web3
from eth_abi import encode
from decimal import *

def update_oracle_prices(configs, signed_payloads) -> bool:
    try:
        w3 = configs['w3']
        account_address = configs['w3account'].address
        multicall = configs['w3multicall']
        oracle_adapter = configs['w3oracle_adapter']
        oracle_adapter_proxy_address = configs['oracle_adapter_proxy_address']

        calls = get_oracle_update_calls(
            oracle_adapter=oracle_adapter,
            signed_payloads=signed_payloads,
            oracle_adapter_proxy_address=oracle_adapter_proxy_address
        )

        tx_hash = multicall.functions.tryAggregatePreservingError(False, calls).transact({'from': account_address})
        tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
        print("Prices updated:", tx_receipt)

        return True
    except Exception as e:
        print("Failed to update prices:", e)
        return False


def get_oracle_update_calls(oracle_adapter, signed_payloads, oracle_adapter_proxy_address):
    # list of payloads should contain an update for every market
    encoded_calls: list = []
    for signed_payload in signed_payloads:
        price_payload = signed_payload['pricePayload']
        encoded_payload = encode(
            ['(address,(string,uint256,uint256),bytes32,bytes32,uint8)'],
            [[
                signed_payload['oraclePubKey'],
                [price_payload['assetPairId'], math.floor(int(price_payload['timestamp']) / 1e9), int(price_payload['price'])],
                Web3.to_bytes(hexstr=signed_payload['r']),
                Web3.to_bytes(hexstr=signed_payload['s']),
                signed_payload['v'],
            ]]
        )

        encoded_calls.append((
            oracle_adapter_proxy_address,
            oracle_adapter.encode_abi(fn_name="fulfillOracleQuery", args=[encoded_payload])
        ))

    # Returns the encoded calls
    return encoded_calls
