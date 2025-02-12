from dataclasses import dataclass
import json

@dataclass
class BridgeOutParams:
    amount: int
    fee_limit: int

f = open('reya_actions/abis/SocketControllerWithPayload.json')
controller_abi = json.load(f)

f = open('reya_actions/abis/Erc20.json')
erc20_abi = json.load(f)

def bridge_out_to_arbitrum(configs: dict, params: BridgeOutParams) -> bool:
    # Ensure Reya Network is configured in configs 
    chain_id = configs['chain_id']

    if not chain_id == 1729:
        raise Exception("Bridging function requires setup for Reya Network")
    
    w3 = configs['w3']
    account = configs['w3account']
    periphery = configs['w3contracts']['periphery']
    rusd = configs['w3contracts']['rusd']
    arbitrum_chain_id = 42161

    # Get the bridging fee, add 10% buffer and check against the set limit
    controller_address = '0x1d43076909Ca139BFaC4EbB7194518bE3638fc76'
    connector_address = '0x3F19417872BC9F5037Bc0D40cE7389D05Cf847Ad'
    socket_msg_gas_limit = 20_000_000
    socket_empty_payload_size = 160

    controller = w3.eth.contract(
        address=controller_address, abi=controller_abi
    )

    estimated_socket_fees = controller.functions.getMinFees(connector_address, socket_msg_gas_limit, socket_empty_payload_size).call()
    socket_fees = estimated_socket_fees * 110 // 100

    if socket_fees > params.fee_limit:
        raise Exception("Socket fee is higher than maximum allowed amount")

    # Approve the rUSD token to be used by the periphery
    tx_hash = rusd.functions.approve(periphery.address, params.amount).transact({'from': account.address})
    tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    print('rUSD approved for periphery:', tx_hash.hex())

    # Initiate the bridging transaction
    tx_hash = periphery.functions.withdraw((
        params.amount,
        rusd.address,
        socket_msg_gas_limit,
        arbitrum_chain_id,
        account.address,
    )).transact({'from': account.address, 'value': socket_fees})

    tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    print('Initiated bridge out:', tx_hash.hex())

    return tx_receipt