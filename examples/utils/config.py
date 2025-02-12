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

    pool_account_id = 2 if chain_id == 1729 else 4
    rpc_url = 'https://rpc.reya.network' if chain_id == 1729 else 'https://rpc.reya-cronos.gelato.digital'
    core_address = "0xA763B6a5E09378434406C003daE6487FbbDc1a80" if chain_id == 1729 else "0xC6fB022962e1426F4e0ec9D2F8861c57926E9f72"
    multicall_address = "0xED28d27dFcA47AD2513C9f2e2d3C098C2eA5A47F" if chain_id == 1729 else "0x5abde4F0aF8Eaf3c9967f7fA126E59A103357b5C"
    oracle_adapter_address = "0x32edABC058C1207fE0Ec5F8557643c28E4FF379e" if chain_id == 1729 else "0xc501A2356703CD351703D68963c6F4136120f7CF"
    passive_perp_address = "0x27E5cb712334e101B3c232eB0Be198baaa595F5F" if chain_id == 1729 else "0x9EC177fed042eF2307928BE2F5CDbf663B20244B"
    passive_pool_address = "0xB4B77d6180cc14472A9a7BDFF01cc2459368D413" if chain_id == 1729 else "0x9A3A664987b88790A6FDC1632e3b607813fd94fF"
    rusd_address = "0xa9F32a851B1800742e47725DA54a09A7Ef2556A3" if chain_id == 1729 else "0x9DE724e7b3facF87Ce39465D3D712717182e3e55"
    periphery_address = "0xCd2869d1eb1BC8991Bc55de9E9B779e912faF736" if chain_id == 1729 else "0x94ccAe812f1647696754412082dd6684C2366A7f"
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

    f = open('examples/abis/PeripheryProxy.json')
    periphery_abi = json.load(f)
    
    f = open('examples/abis/Erc20.json')
    erc20_abi = json.load(f)

    w3 = Web3(Web3.HTTPProvider(rpc_url))
    w3account = w3.eth.account.from_key(private_key)
    w3.eth.default_account = w3account.address
    w3.middleware_onion.add(
        construct_sign_and_send_raw_middleware(w3account)
    )

    w3core = w3.eth.contract(
        address=core_address, abi=core_abi
    )

    w3multicall = w3.eth.contract(
        address=multicall_address, abi=multicall_abi
    )

    w3oracle_adapter = w3.eth.contract(
        address=oracle_adapter_address, abi=oracle_adapter_abi
    )

    w3passive_pool = w3.eth.contract(
        address=passive_pool_address, abi=passive_pool_abi
    )

    w3periphery = w3.eth.contract(
        address=periphery_address, abi=periphery_abi
    )

    w3rusd = w3.eth.contract(
        address=rusd_address, abi=erc20_abi
    )

    return {
        'arbitrum_rpc_url': 'https://arb1.arbitrum.io/rpc',
        'chain_id': chain_id,
        'core_abi': core_abi,
        'core_address': core_address,
        'exchange_id': 1,
        'multicall_abi': multicall_abi,
        'multicall_address': multicall_address,
        'oracle_abi': oracle_adapter_abi,
        'oracle_adapter_address': oracle_adapter_address,
        'passive_perp_abi': passive_perp_abi,
        'passive_perp_address': passive_perp_address,
        'periphery_address': periphery_address,
        'pool_account_id': pool_account_id,
        'private_key': private_key,
        'rusd_address': rusd_address,
        'w3': w3,
        'w3account': w3account,
        'w3core': w3core,
        'w3multicall': w3multicall,
        'w3oracle_adapter': w3oracle_adapter,
        'w3passive_pool': w3passive_pool,
        'w3periphery': w3periphery,
        'w3rusd': w3rusd,
    }
