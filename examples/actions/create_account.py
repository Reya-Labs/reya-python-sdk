def create_account(configs: dict) -> int:
    w3 = configs['w3']
    core = configs['w3core']
    account_address = configs['w3account'].address
    
    tx_hash = core.functions.create_account(account_address).transact({'from': account_address})
    tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)

    # TODO: decode tx_receipt to get the create account id

    return 0