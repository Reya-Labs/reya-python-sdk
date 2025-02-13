from web3 import Web3
from hexbytes import HexBytes

def create_account(config: dict):
    w3 = config['w3']
    core = config['w3contracts']['core']
    account = config['w3account']
    
    # Send the transaction to create the account
    tx_hash = core.functions.createAccount(account.address).transact({'from': account.address})
    tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    print(f'Created account: ${tx_receipt.transactionHash.hex()}')

    # Decode the logs to get the newly created account id
    logs = tx_receipt["logs"]
    event_sig = Web3.keccak(text="AccountCreated(uint128,address,address,uint256)").hex()
    filtered_logs = [log for log in logs if HexBytes(log["topics"][0]) == HexBytes(event_sig)]

    if not len(filtered_logs) == 1:
        raise Exception("Failed to decode transaction receipt for account creations")
    
    event = core.events.AccountCreated().process_log(filtered_logs[0])
    account_id = int(event["args"]["accountId"])

    return {
        'transaction_receipt': tx_receipt,
        'account_id': account_id,
    }