def execute_core_commands(config, account_id: int, commands: list):
    w3 = config['w3']
    account = config['w3account']
    core = config['w3contracts']['core']
        
    tx_hash = core.functions.execute(account_id, commands).transact({'from': account.address})
    tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)

    return tx_receipt