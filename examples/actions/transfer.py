from dataclasses import dataclass
from examples.utils import execute_core_commands
from examples.utils.consts import CommandType
from eth_abi import encode

@dataclass
class TransferParams:
    account_id: int
    collateral: str
    amount: int
    to_account_id: int

def transfer(configs: dict, params: TransferParams) -> bool:
    try:
        inputs_encoded = encode(
            ['(uint128,address,uint256)'], 
            [[params.to_account_id, params.collateral, params.amount]]
        )
        command = (CommandType.TransferBetweenMarginAccounts.value, inputs_encoded, 0, 0)
        commands: list = [command]
        
        tx_receipt = execute_core_commands(configs, params.account_id, commands)
        print("Transfer executed:", tx_receipt)

        return True
    except Exception as e:
        print("Failed to execute transfer:", e)
        return False
