import math
from examples.utils.consts import CommandType
from web3 import Web3
from eth_abi import encode
import json
import os
from dotenv import load_dotenv
from decimal import *
from examples.utils.sign import sign_core_commands
from web3.middleware import construct_sign_and_send_raw_middleware
from time import time

load_dotenv()

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

def execute_trade(configs, base, price_limit, market_id, account_id, signed_payloads) -> bool:
    try:
        # Initialise provider with signer
        w3 = Web3(Web3.HTTPProvider(configs['rpc_url']))
        account = w3.eth.account.from_key(configs['private_key'])
        w3.eth.default_account = account.address
        w3.middleware_onion.add(
            construct_sign_and_send_raw_middleware(account))

        # Update oracle prices
        if len(signed_payloads) > 0:
            multicall = w3.eth.contract(
                address=configs['multicall_address'], abi=configs['multicall_abi'])
            
            calls = _get_oracle_update_calls(
                w3, configs['oracle_abi'], signed_payloads, configs['oracle_adapter_proxy_address'])

            tx_hash = multicall.functions.tryAggregatePreservingError(False, calls).transact({'from': account.address})
            tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
            print("Prices updated:", tx_receipt)

        # Execute core commands
        core = w3.eth.contract(
            address=configs['core_proxy_address'], abi=configs['core_abi'])
        
        # Get current core signature nonce
        core_sig_nonce = _get_core_sig_nonce(w3, configs, account_id)
        
        command_args = _encode_core_match_order(
            account, configs, base, price_limit, market_id, account_id, core_sig_nonce)
        
        tx_hash = core.functions.executeBySig(*command_args).transact({'from': account.address})
        tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
        print("Trade executed:", tx_receipt)

        return True
    except Exception as e:
        print("Failed to execute trade:", e)
        return False

def _get_core_sig_nonce(w3, configs, account_id): 
    core_proxy = w3.eth.contract(
        address=configs['core_proxy_address'], abi=configs['core_abi'])
    
    core_sig_nonce = core_proxy.functions.getAccountOwnerNonce(account_id).call()

    return core_sig_nonce


def _encode_core_match_order(account, configs, base, price_limit, market_id, account_id, current_core_nonce):
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
        caller=account.address,
        account_id=account_id,
        commands=commands,
        nonce=current_core_nonce + 1,
        deadline=int(time()) + 60 * 5,  # 5 mins buffer
        extra_signature_data=extra_data,
        core_proxy_address=configs['core_proxy_address']
    )

    return [account_id, commands, sig, extra_data]


def _get_oracle_update_calls(w3, oracle_adapter_abi, signed_payloads, oracle_adapter_proxy_address):
    oracle_adapter = w3.eth.contract(
        address=oracle_adapter_proxy_address, abi=oracle_adapter_abi)

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


'''Gathering configuration from environment variables and ABIs'''


def getConfigs():
    chain_id = int(os.environ['CHAIN_ID'])
    if chain_id not in [1729, 89346162]:
        raise Exception("Wrong chain id")

    pool_id = 2 if chain_id == 1729 else 4
    rpc_url = 'https://rpc.reya.network' if chain_id == 1729 else 'https://rpc.reya-cronos.gelato.digital'
    core_proxy_address = "0xA763B6a5E09378434406C003daE6487FbbDc1a80" if chain_id == 1729 else "0xC6fB022962e1426F4e0ec9D2F8861c57926E9f72"
    multicall_address = "0xED28d27dFcA47AD2513C9f2e2d3C098C2eA5A47F" if chain_id == 1729 else "0x5abde4F0aF8Eaf3c9967f7fA126E59A103357b5C"
    oracle_adapter_proxy_address = "0x32edABC058C1207fE0Ec5F8557643c28E4FF379e" if chain_id == 1729 else "0xc501A2356703CD351703D68963c6F4136120f7CF"
    passive_perp_proxy_address = "0x27e5cb712334e101b3c232eb0be198baaa595f5f" if chain_id == 1729 else "0x9ec177fed042ef2307928be2f5cdbf663b20244b"

    f = open('examples/abis/CoreProxy.json')
    core_abi = json.load(f)

    f = open('examples/abis/Multicall.json')
    multicall_abi = json.load(f)

    f = open('examples/abis/OracleAdapterProxy.json')
    oracle_adapter_abi = json.load(f)

    f = open('examples/abis/PassivePerpProxy.json')
    passive_perp_abi = json.load(f)

    return {
        'core_proxy_address': core_proxy_address,
        'multicall_address': multicall_address,
        'oracle_adapter_proxy_address': oracle_adapter_proxy_address,
        'passive_perp_proxy_address': passive_perp_proxy_address,
        'private_key': os.environ['PRIVATE_KEY'],
        'pool_id': pool_id,
        'exchange_id': 1,
        'multicall_abi': multicall_abi,
        'oracle_abi': oracle_adapter_abi,
        'core_abi': core_abi,
        'passive_perp_abi': passive_perp_abi,
        'rpc_url': rpc_url,
        'chain_id': chain_id,
        'account_id': int(os.environ['ACCOUNT_ID'])
    }
