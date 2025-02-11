def execute_core_commands(configs, account_id: int, commands: list):
    w3 = configs['w3']
    core = configs['w3core']
    account_address = configs['w3account'].address
        
    tx_hash = core.functions.execute(account_id, commands).transact({'from': account_address})
    tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)

    return tx_receipt