from dataclasses import dataclass
from examples.utils import execute_core_commands
from examples.utils.consts import CommandType
from eth_abi import encode

@dataclass
class TransferParams:
    account_id: int
    amount: int
    to_account_id: int

def transfer(configs: dict, params: TransferParams) -> bool:
    rusd_address = configs['rusd_address']

    inputs_encoded = encode(
        ['(uint128,address,uint256)'], 
        [[params.to_account_id, rusd_address, params.amount]]
    )
    command = (CommandType.TransferBetweenMarginAccounts.value, inputs_encoded, 0, 0)
    commands: list = [command]
        
    tx_receipt = execute_core_commands(configs, params.account_id, commands)
    print("Transfer executed:", tx_receipt)

    return tx_receipt
