from web3 import Web3
from eth_abi import encode
from enum import Enum
import json
import os
from dotenv import load_dotenv
from decimal import *
from examples.utils.sign import sign_core_commands
from web3.middleware import construct_sign_and_send_raw_middleware
from time import time

load_dotenv()


class CommandType(Enum):
    Deposit                       = 0
    Withdraw                      = 1
    DutchLiquidation              = 2
    MatchOrder                    = 3
    TransferBetweenMarginAccounts = 4

# Note: the list of markets keeps updating, please check with the team for the updated ids
class MarketIds(Enum):
    ETHUSD    = 1
    BTCUSD    = 2
    SOLUSD    = 3
    ARBUSD    = 4
    OPUSD     = 5
    AVAXUSD   = 6
    MKRUSD    = 7
    LINKUSD   = 8
    AAVEUSD   = 9
    CRVUSD    = 10
    UNIUSD    = 11
    SUIUSD    = 12
    TIAUSD    = 13
    SEIUSD    = 14
    ZROUSD    = 15
    XRPUSD    = 16
    WIFUSD    = 17
    kPEPEUSD  = 18
    POPCATUSD = 19
    DOGEUSD   = 20
    kSHIBUSD  = 21
    kBONKUSD  = 22
    APTUSD    = 23
    BNBUSD    = 24
    JTOUSD    = 25


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

def execute_trade(configs, base, price_limit, market_id, account_id, price_payloads) -> bool:
    try:
        # Initialise provider with signer
        w3 = Web3(Web3.HTTPProvider(configs['rpc_url']))
        account = w3.eth.account.from_key(configs['private_key'])
        w3.eth.default_account = account.address
        w3.middleware_onion.add(
            construct_sign_and_send_raw_middleware(account))
        
        # Get current core signature nonce
        core_sig_nonce = _get_core_sig_nonce(w3, configs, account_id)

        # Encode Core Command
        core_execution_calldata = _encode_core_match_order(
            w3, account, configs, base, price_limit, market_id, account_id, core_sig_nonce)

        # Encode Multicall2 oracle updates
        multicall2 = w3.eth.contract(
            address=configs['multicall2_address'], abi=configs['multicall_abi'])
        oracle_update_calldata = _encode_price_update_calls(
            w3, configs['oracle_abi'], price_payloads, configs['oracle_adapter_proxy_address'], multicall2)

        # Aggregate oracle updates and match order
        calls = [(configs['multicall2_address'], oracle_update_calldata),
                 (configs['core_proxy_address'], core_execution_calldata)]
        # # Success is required
        tx_hash = multicall2.functions.tryAggregate(
            True, calls).transact({'from': account.address})
        tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
        print("Receipt", tx_receipt)

        # Simulation example
        # res = multicall2.functions.tryAggregate(
        #     False, calls).call({'from': account.address})
        # (succes1, output1) = res[0]
        # (succes2, output2) = res[1]
        # print("Result from oracle updates", succes1, output1.hex())
        # print("Result from trade", succes2, output2.hex())

        return True
    except Exception as e:
        print("Failed to execute trade:", e)
        return False

def _get_core_sig_nonce(w3, configs, account_id): 
    core_proxy = w3.eth.contract(
        address=configs['core_proxy_address'], abi=configs['core_abi'])
    
    core_sig_nonce = core_proxy.functions.getAccountOwnerNonce(account_id).call()

    return core_sig_nonce


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
    chain_id = int(os.environ['CHAIN_ID'])
    if chain_id not in [1729, 89346162]:
        raise Exception("Wrong chain id")

    pool_id = 2 if chain_id == 1729 else 4
    rpc_url = 'https://rpc.reya.network' if chain_id == 1729 else 'https://rpc.reya-cronos.gelato.digital'
    core_proxy_address = "0xA763B6a5E09378434406C003daE6487FbbDc1a80" if chain_id == 1729 else "0xC6fB022962e1426F4e0ec9D2F8861c57926E9f72"
    multicall2_address = "0x82a55eB2346277d85D5D0A60101ebD0F02680B0D" if chain_id == 1729 else "0xd9f0F399f5264fAac91476Dbd950574726D18633"
    oracle_adapter_proxy_address = "0x32edABC058C1207fE0Ec5F8557643c28E4FF379e" if chain_id == 1729 else "0xc501A2356703CD351703D68963c6F4136120f7CF"

    f = open('examples/abis/CoreProxy.json')
    core_abi = json.load(f)

    f = open('examples/abis/Multicall2.json')
    multicall_abi = json.load(f)

    f = open('examples/abis/OracleAdapterProxy.json')
    oracle_adapter_abi = json.load(f)

    return {
        'core_proxy_address': core_proxy_address,
        'multicall2_address': multicall2_address,
        'oracle_adapter_proxy_address': oracle_adapter_proxy_address,
        'private_key': os.environ['PRIVATE_KEY'],
        'pool_id': pool_id,
        'exchange_id': 1,
        'multicall_abi': multicall_abi,
        'oracle_abi': oracle_adapter_abi,
        'core_abi': core_abi,
        'rpc_url': rpc_url,
        'chain_id': chain_id,
        'account_id': int(os.environ['ACCOUNT_ID'])
    }
