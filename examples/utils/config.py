import json
import os
from web3 import Web3
from web3.middleware import construct_sign_and_send_raw_middleware
from dotenv import load_dotenv

'''Gathering configuration from environment variables and ABIs'''
def getConfigs() -> dict:
    load_dotenv()

    chain_id = int(os.environ['CHAIN_ID'])
    private_key = os.environ['PRIVATE_KEY']

    if chain_id == 1729:
        rpc_url = 'https://rpc.reya.network'
        passive_pool_account_id = 2
        exchange_id = 1

        core_address = "0xA763B6a5E09378434406C003daE6487FbbDc1a80" 
        multicall_address = "0xED28d27dFcA47AD2513C9f2e2d3C098C2eA5A47F"
        oracle_adapter_address = "0x32edABC058C1207fE0Ec5F8557643c28E4FF379e"
        passive_perp_address = "0x27E5cb712334e101B3c232eB0Be198baaa595F5F"
        passive_pool_address = "0xB4B77d6180cc14472A9a7BDFF01cc2459368D413"
        rusd_address = "0xa9F32a851B1800742e47725DA54a09A7Ef2556A3"
        periphery_address = "0xCd2869d1eb1BC8991Bc55de9E9B779e912faF736"
        usdc_address = "0x3B860c0b53f2e8bd5264AA7c3451d41263C933F2"
    elif chain_id == 89346162:
        rpc_url = 'https://rpc.reya-cronos.gelato.digital'
        passive_pool_account_id = 4
        exchange_id = 1

        core_address = "0xC6fB022962e1426F4e0ec9D2F8861c57926E9f72"
        multicall_address = "0x5abde4F0aF8Eaf3c9967f7fA126E59A103357b5C"
        oracle_adapter_address = "0xc501A2356703CD351703D68963c6F4136120f7CF"
        passive_perp_address = "0x9EC177fed042eF2307928BE2F5CDbf663B20244B"
        passive_pool_address = "0x9A3A664987b88790A6FDC1632e3b607813fd94fF"
        rusd_address = "0x9DE724e7b3facF87Ce39465D3D712717182e3e55"
        periphery_address = "0x94ccAe812f1647696754412082dd6684C2366A7f"
        usdc_address = "0xfA27c7c6051344263533cc365274d9569b0272A8"
    else:
        raise Exception("Invalid chain id! It's neither 1729 (Reya Network) nor 89346162 (Reya Cronos).")
    
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
    w3.middleware_onion.add(construct_sign_and_send_raw_middleware(w3account))

    w3core = w3.eth.contract(address=core_address, abi=core_abi)
    w3multicall = w3.eth.contract(address=multicall_address, abi=multicall_abi)
    w3oracle_adapter = w3.eth.contract(address=oracle_adapter_address, abi=oracle_adapter_abi)
    w3passive_perp = w3.eth.contract(address=passive_perp_address, abi=passive_perp_abi)
    w3passive_pool = w3.eth.contract(address=passive_pool_address, abi=passive_pool_abi)
    w3periphery = w3.eth.contract(address=periphery_address, abi=periphery_abi)
    w3rusd = w3.eth.contract(address=rusd_address, abi=erc20_abi)
    w3usdc = w3.eth.contract(address=usdc_address, abi=erc20_abi)

    return {
        'chain_id': chain_id,
        'exchange_id': exchange_id,
        'passive_pool_account_id': passive_pool_account_id,
        'private_key': private_key,
        'w3': w3,
        'w3account': w3account,
        'w3contracts': {
            'core': w3core,
            'multicall': w3multicall,
            'oracle_adapter': w3oracle_adapter,
            'passive_perp': w3passive_perp,
            'passive_pool': w3passive_pool,
            'periphery': w3periphery,
            'rusd': w3rusd,
            'usdc': w3usdc,
        }
    }
