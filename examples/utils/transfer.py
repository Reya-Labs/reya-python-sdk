from dataclasses import dataclass
from examples.utils.consts import CommandType
from eth_abi import encode
from examples.utils.get_core_nonce import get_core_sig_nonce
from examples.utils.sign import sign_core_commands
from time import time

@dataclass
class TransferParams:
    account_id: int
    collateral: str
    amount: int
    to_account_id: int

def transfer(configs: dict, params: TransferParams) -> bool:
    try:
        # Get current core signature nonce
        w3 = configs['w3']
        core = configs['w3core']
        account_address = configs['w3account'].address

        core_sig_nonce = get_core_sig_nonce(core=core, account_id=params.account_id)
        
        command_args = encode_transfer(
            configs=configs, 
            params=params,
            core_sig_nonce=core_sig_nonce
        )
        
        tx_hash = core.functions.executeBySig(*command_args).transact({'from': account_address})
        tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
        print("Transfer executed:", tx_receipt)

        return True
    except Exception as e:
        print("Failed to execute transfer:", e)
        return False

def encode_transfer(configs: dict, params: TransferParams, core_sig_nonce: int):
    account = configs['w3account']
    core_proxy_address = configs['core_proxy_address']
    chain_id = configs['chain_id']

    extra_data = encode([], [])  # empty for this example
    inputs_encoded = encode(
        ['(uint128,address,uint256)'], [[params.to_account_id, params.collateral, params.amount]])

    command = (CommandType.TransferBetweenMarginAccounts.value, inputs_encoded, 0, 0)
    commands: list = [command]

    # Get EIP712 signature from margin account owner
    sig = sign_core_commands(
        signer=account,
        reya_chain_id=chain_id,
        caller=account.address,
        account_id=params.account_id,
        commands=commands,
        nonce=core_sig_nonce + 1,
        deadline=int(time()) + 60 * 5,  # 5 mins buffer
        extra_signature_data=encode([], []),
        core_proxy_address=core_proxy_address
    )

    return [params.account_id, commands, sig, extra_data]
