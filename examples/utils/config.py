import json
import os
from web3 import Web3
from web3.middleware import construct_sign_and_send_raw_middleware
from dotenv import load_dotenv

'''Gathering configuration from environment variables and ABIs'''
def getConfigs() -> dict:
    load_dotenv()

    chain_id = int(os.environ['CHAIN_ID'])
    if chain_id not in [1729, 89346162]:
        raise Exception("Wrong chain id")

    pool_id = 2 if chain_id == 1729 else 4
    rpc_url = 'https://rpc.reya.network' if chain_id == 1729 else 'https://rpc.reya-cronos.gelato.digital'
    core_proxy_address = "0xA763B6a5E09378434406C003daE6487FbbDc1a80" if chain_id == 1729 else "0xC6fB022962e1426F4e0ec9D2F8861c57926E9f72"
    multicall_address = "0xED28d27dFcA47AD2513C9f2e2d3C098C2eA5A47F" if chain_id == 1729 else "0x5abde4F0aF8Eaf3c9967f7fA126E59A103357b5C"
    oracle_adapter_proxy_address = "0x32edABC058C1207fE0Ec5F8557643c28E4FF379e" if chain_id == 1729 else "0xc501A2356703CD351703D68963c6F4136120f7CF"
    passive_perp_proxy_address = "0x27e5cb712334e101b3c232eb0be198baaa595f5f" if chain_id == 1729 else "0x9ec177fed042ef2307928be2f5cdbf663b20244b"
    passive_pool_proxy_address = "0xb4b77d6180cc14472a9a7bdff01cc2459368d413" if chain_id == 1729 else "0x9a3a664987b88790a6fdc1632e3b607813fd94ff"
    rusd_address = "0xa9F32a851B1800742e47725DA54a09A7Ef2556A3" if chain_id == 1729 else "0x9DE724e7b3facF87Ce39465D3D712717182e3e55"
    private_key = os.environ['PRIVATE_KEY']

    f = open('examples/abis/CoreProxy.json')
    core_abi = json.load(f)

    f = open('examples/abis/Multicall.json')
    multicall_abi = json.load(f)

    f = open('examples/abis/OracleAdapterProxy.json')
    oracle_adapter_abi = json.load(f)

    f = open('examples/abis/PassivePerpProxy.json')
    passive_perp_abi = json.load(f)

    f = open('examples/abis/PassivePoolProxy.json')
    passive_pool_abi = json.load(f)

    w3 = Web3(Web3.HTTPProvider(rpc_url))
    w3account = w3.eth.account.from_key(private_key)
    w3.eth.default_account = w3account.address
    w3.middleware_onion.add(
        construct_sign_and_send_raw_middleware(w3account)
    )

    w3core = w3.eth.contract(
        address=core_proxy_address, abi=core_abi
    )

    w3multicall = w3.eth.contract(
        address=multicall_address, abi=multicall_abi
    )

    w3oracle_adapter = w3.eth.contract(
        address=oracle_adapter_proxy_address, abi=oracle_adapter_abi
    )

    w3passive_pool = w3.eth.contract(
        address=passive_pool_proxy_address, abi=passive_pool_abi
    )

    return {
        'chain_id': chain_id,
        'core_abi': core_abi,
        'core_proxy_address': core_proxy_address,
        'exchange_id': 1,
        'multicall_abi': multicall_abi,
        'multicall_address': multicall_address,
        'oracle_abi': oracle_adapter_abi,
        'oracle_adapter_proxy_address': oracle_adapter_proxy_address,
        'passive_perp_abi': passive_perp_abi,
        'passive_perp_proxy_address': passive_perp_proxy_address,
        'pool_id': pool_id,
        'rusd_address': rusd_address,
        'w3': w3,
        'w3account': w3account,
        'w3core': w3core,
        'w3multicall': w3multicall,
        'w3oracle_adapter': w3oracle_adapter,
        'w3passive_pool': w3passive_pool,
    }
