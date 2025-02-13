import math
from web3 import Web3
from eth_abi import encode

def update_oracle_prices(config, signed_payloads):
    w3 = config['w3']
    account = config['w3account']
    multicall = config['w3contracts']['multicall']
    oracle_adapter = config['w3contracts']['oracle_adapter']

    calls = get_oracle_update_calls(
        oracle_adapter=oracle_adapter,
        signed_payloads=signed_payloads,
    )

    tx_hash = multicall.functions.tryAggregatePreservingError(False, calls).transact({'from': account.address})
    tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    print(f'Updated oracle prices: {tx_receipt.transactionHash.hex()}')

    return {
        'transaction_receipt': tx_receipt,
    }

def get_oracle_update_calls(oracle_adapter, signed_payloads):
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
            oracle_adapter.address,
            oracle_adapter.encode_abi(fn_name="fulfillOracleQuery", args=[encoded_payload])
        ))

    # Returns the encoded calls
    return encoded_calls
