from dataclasses import dataclass
from eth_abi import encode
from reya_actions.types import CommandType
from reya_actions.utils.execute_core_commands import execute_core_commands

@dataclass
class DepositParams:
    account_id: int
    amount: int

def deposit(config: dict, params: DepositParams):
    w3 = config['w3']
    account = config['w3account']
    core = config['w3contracts']['core']
    rusd = config['w3contracts']['rusd']

    # Approve the rUSD token to be used by the periphery
    tx_hash = rusd.functions.approve(core.address, params.amount).transact({'from': account.address})
    tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    print(f'Approved rUSD to core: ${tx_receipt.transactionHash.hex()}')

    # Build the deposit command and execute it
    inputs_encoded = encode(
        ['(address,uint256)'], 
        [[rusd.address, params.amount]]
    )
    command = (CommandType.Deposit.value, inputs_encoded, 0, 0)
    commands: list = [command]
        
    tx_receipt = execute_core_commands(config, params.account_id, commands)
    print(f'Deposited to margin account: ${tx_receipt.transactionHash.hex()}')

    return {
        'transaction_receipt': tx_receipt,
    }