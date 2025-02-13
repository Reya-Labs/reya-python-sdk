from dataclasses import dataclass
import json
from web3 import Web3
from web3.middleware import construct_sign_and_send_raw_middleware

@dataclass
class BridgeInParams:
    amount: int
    fee_limit: int

f = open('reya_actions/abis/SocketVaultWithPayload.json')
vault_abi = json.load(f)

f = open('reya_actions/abis/Erc20.json')
erc20_abi = json.load(f)

def bridge_in_from_arbitrum(config: dict, params: BridgeInParams):
    arbitrum_rpc_url = 'https://arb1.arbitrum.io/rpc'
    vault_address = '0x11B3a7E08Eb2FdEa2745e4CB64648b10B28524A8'
    connector_address = '0xb0d57301050710AF1145562b3386ff5eCFE9BE83'
    chain_id = config['chain_id']

    # Ensure Reya Network is configured in configs
    if not chain_id == 1729:
        raise Exception("Bridging function requires setup for Reya Network")
    
    return bridge_in(
        config=config, 
        params=params, 
        chain_rpc_url=arbitrum_rpc_url,
        vault_address=vault_address,
        connector_address=connector_address
    )

def bridge_in(config: dict, params: BridgeInParams, chain_rpc_url: str, vault_address: str, connector_address: str): 
    # Ensure Reya Network is configured in configs 
    private_key = config['private_key']
    
    # Create w3 instance for the specified chain and private key
    w3 = Web3(Web3.HTTPProvider(chain_rpc_url))
    w3account = w3.eth.account.from_key(private_key)
    account_address = w3account.address
    w3.eth.default_account = account_address
    w3.middleware_onion.add(
        construct_sign_and_send_raw_middleware(w3account)
    )

    # Get the bridging fee, add 10% buffer and check against the set limit
    socket_msg_gas_limit = 20_000_000
    socket_empty_payload_size = 160

    vault = w3.eth.contract(
        address=vault_address, abi=vault_abi
    )

    estimated_socket_fees = vault.functions.getMinFees(connector_address, socket_msg_gas_limit, socket_empty_payload_size).call()
    socket_fees = estimated_socket_fees * 110 // 100

    if socket_fees > params.fee_limit:
        raise Exception("Socket fee is higher than maximum allowed amount")

    # Approve the USDC token to be used by the vault
    chain_usdc_address = vault.functions.token().call()
    chain_usdc = w3.eth.contract(
        address=chain_usdc_address, abi=erc20_abi
    )

    tx_hash = chain_usdc.functions.approve(vault_address, params.amount).transact({'from': account_address})
    tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    print(f'Approved USDC to vault: {tx_receipt.transactionHash.hex()}')

    # Initiate the bridging transaction
    periphery = config['w3contracts']['periphery']
    reya_usdc = config['w3contracts']['usdc']
    socket_bridge_options = Web3.to_bytes(hexstr='0x')

    periphery_calldata = periphery.encodeABI(fn_name="deposit", args=[(account_address, reya_usdc.address)])
    
    tx_hash = vault.functions.bridge(
        periphery.address,
        params.amount,
        socket_msg_gas_limit,
        connector_address,
        periphery_calldata,
        socket_bridge_options
    ).transact({'from': account_address, 'value': socket_fees})

    tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    print(f'Initiated bridge in: {tx_receipt.transactionHash.hex()}')

    return {
        'transaction_receipt': tx_receipt,
    }
