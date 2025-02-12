from dataclasses import dataclass
from eth_abi import encode
from reya_actions.types import CommandType
from reya_actions.utils import execute_core_commands

@dataclass
class DepositParams:
    account_id: int
    amount: int

def deposit(configs: dict, params: DepositParams) -> bool:
    w3 = configs['w3']
    account = configs['w3account']
    core = configs['w3contracts']['core']
    rusd = configs['w3contracts']['rusd']

    # Approve the rUSD token to be used by the periphery
    tx_hash = rusd.functions.approve(core.address, params.amount).transact({'from': account.address})
    tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    print('Approved rUSD to core:', tx_receipt.transactionHash.hex())

    # Build the deposit command and execute it
    inputs_encoded = encode(
        ['(address,uint256)'], 
        [[rusd.address, params.amount]]
    )
    command = (CommandType.Deposit.value, inputs_encoded, 0, 0)
    commands: list = [command]
        
    tx_receipt = execute_core_commands(configs, params.account_id, commands)
    print("Deposited to margin account:", tx_receipt.transactionHash.hex())

    return tx_receipt