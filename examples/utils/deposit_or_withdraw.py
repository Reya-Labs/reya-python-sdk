from dataclasses import dataclass
from examples.utils.consts import CommandType
from eth_abi import encode
from examples.utils.get_core_nonce import get_core_sig_nonce
from examples.utils.sign import sign_core_commands
from time import time

@dataclass
class DepositOrWithdrawParams:
    account_id: int
    collateral: str
    amount: int

def deposit_or_withdraw(configs: dict, params: DepositOrWithdrawParams, is_deposit: bool) -> bool:
    try:
        # Get current core signature nonce
        w3 = configs['w3']
        core = configs['w3core']
        account_address = configs['w3account'].address

        core_sig_nonce = get_core_sig_nonce(core=core, account_id=params.account_id)
        
        command_args = encode_deposit_or_withdraw(
            configs=configs, 
            params=params,
            core_sig_nonce=core_sig_nonce,
            is_deposit=is_deposit
        )
        
        tx_hash = core.functions.executeBySig(*command_args).transact({'from': account_address})
        tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
        print("Deposit or withdrawal executed:", tx_receipt)

        return True
    except Exception as e:
        print("Failed to deposit or withdraw:", e)
        return False

def encode_deposit_or_withdraw(configs: dict, params: DepositOrWithdrawParams, core_sig_nonce: int, is_deposit: bool):
    account = configs['w3account']
    core_proxy_address = configs['core_proxy_address']
    chain_id = configs['chain_id']

    extra_data = encode([], [])  # empty for this example
    match_order_inputs_encoded = encode(
        ['address', 'uint256'], [params.collateral, params.amount])

    command = (
        CommandType.Deposit.value if is_deposit else CommandType.Withdraw.value,
        match_order_inputs_encoded, 
        0, 
        0
    )
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
        extra_signature_data=extra_data,
        core_proxy_address=core_proxy_address
    )

    return [params.account_id, commands, sig, extra_data]
