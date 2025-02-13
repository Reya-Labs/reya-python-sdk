from dataclasses import dataclass
from eth_abi import encode
from reya_actions.types import CommandType
from reya_actions.utils.execute_core_commands import execute_core_commands

@dataclass
class WithdrawParams:
    account_id: int
    amount: int

def withdraw(config: dict, params: WithdrawParams):
    rusd = config['w3contracts']['rusd']

    inputs_encoded = encode(
        ['(address,uint256)'], 
        [[rusd.address, params.amount]]
    )
    command = (CommandType.Withdraw.value, inputs_encoded, 0, 0)
    commands: list = [command]
        
    tx_receipt = execute_core_commands(config, params.account_id, commands)
    print(f'Withdrawn from margin account: {tx_receipt.transactionHash.hex()}')

    return {
        'transaction_receipt': tx_receipt,
    }