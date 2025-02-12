from dataclasses import dataclass
from eth_abi import encode
from examples.utils import execute_core_commands
from examples.utils.consts import CommandType

@dataclass
class WithdrawParams:
    account_id: int
    amount: int

def withdraw(configs: dict, params: WithdrawParams) -> bool:
    rusd = configs['w3contracts']['rusd']

    inputs_encoded = encode(
        ['(address,uint256)'], 
        [[rusd.address, params.amount]]
    )
    command = (CommandType.Withdraw.value, inputs_encoded, 0, 0)
    commands: list = [command]
        
    tx_receipt = execute_core_commands(configs, params.account_id, commands)
    print("Withdrawal executed:", tx_receipt)

    return tx_receipt