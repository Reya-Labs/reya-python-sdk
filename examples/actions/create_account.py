def create_account(configs: dict) -> int:
    w3 = configs['w3']
    core = configs['w3contracts']['core']
    account = configs['w3account']
    
    tx_hash = core.functions.create_account(account.address).transact({'from': account.address})
    tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)

    # TODO: decode tx_receipt to get the create account id

    return 0