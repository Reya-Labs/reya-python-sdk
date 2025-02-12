from dataclasses import dataclass
from eth_abi import encode
from reya_actions.types import CommandType
from reya_actions.utils import execute_core_commands

@dataclass
class TransferParams:
    account_id: int
    amount: int
    to_account_id: int

def transfer(configs: dict, params: TransferParams) -> bool:
    rusd = configs['w3contracts']['rusd']

    inputs_encoded = encode(
        ['(uint128,address,uint256)'], 
        [[params.to_account_id, rusd.address, params.amount]]
    )
    command = (CommandType.TransferBetweenMarginAccounts.value, inputs_encoded, 0, 0)
    commands: list = [command]
        
    tx_receipt = execute_core_commands(configs, params.account_id, commands)
    print("Transferred rUSD between margin accounts:", tx_receipt.transactionHash.hex())

    return tx_receipt
