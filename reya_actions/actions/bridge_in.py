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


def bridge_in_from_arbitrum(configs: dict, params: BridgeInParams) -> bool:
    # Ensure Reya Network is configured in configs 
    arbitrum_rpc_url = 'https://arb1.arbitrum.io/rpc'
    private_key = configs['private_key']
    chain_id = configs['chain_id']

    if not chain_id == 1729:
        raise Exception("Bridging function requires setup for Reya Network")
    
    # Create w3 instance for Arbitrum using the given private key
    w3 = Web3(Web3.HTTPProvider(arbitrum_rpc_url))
    w3account = w3.eth.account.from_key(private_key)
    account_address = w3account.address
    w3.eth.default_account = account_address
    w3.middleware_onion.add(
        construct_sign_and_send_raw_middleware(w3account)
    )

    # Get the bridging fee, add 10% buffer and check against the set limit
    usdc_vault_address = '0x11B3a7E08Eb2FdEa2745e4CB64648b10B28524A8'
    usdc_connector_address = '0xb0d57301050710AF1145562b3386ff5eCFE9BE83'
    socket_msg_gas_limit = 20_000_000
    socket_empty_payload_size = 160

    vault = w3.eth.contract(
        address=usdc_vault_address, abi=vault_abi
    )

    estimated_socket_fees = vault.functions.getMinFees(usdc_connector_address, socket_msg_gas_limit, socket_empty_payload_size).call()
    socket_fees = estimated_socket_fees * 110 // 100

    if socket_fees > params.fee_limit:
        raise Exception("Socket fee is higher than maximum allowed amount")

    # Approve the USDC token to be used by the vault
    usdc_address = vault.functions.token().call()
    usdc = w3.eth.contract(
        address=usdc_address, abi=erc20_abi
    )

    tx_hash = usdc.functions.approve(usdc_vault_address, params.amount).transact({'from': account_address})
    tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    print('Approved USDC to vault:', tx_receipt.transactionHash.hex())

    # Initiate the bridging transaction
    periphery = configs['w3contracts']['periphery']
    reya_usdc_address = '0x3B860c0b53f2e8bd5264AA7c3451d41263C933F2'
    socket_bridge_options = Web3.to_bytes(hexstr='0x')

    periphery_calldata = periphery.encodeABI(fn_name="deposit", args=[(account_address, reya_usdc_address)])
    
    tx_hash = vault.functions.bridge(
        periphery.address,
        params.amount,
        socket_msg_gas_limit,
        usdc_connector_address,
        periphery_calldata,
        socket_bridge_options
    ).transact({'from': account_address, 'value': socket_fees})

    tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    print('Initiated bridge in:', tx_receipt.transactionHash.hex())

    return tx_receipt