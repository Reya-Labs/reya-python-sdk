from web3 import Web3
from hexbytes import HexBytes

def create_account(configs: dict) -> int:
    w3 = configs['w3']
    core = configs['w3contracts']['core']
    account = configs['w3account']
    
    tx_hash = core.functions.createAccount(account.address).transact({'from': account.address})
    tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    print("Created account:", tx_receipt.transactionHash.hex())

    logs = tx_receipt["logs"]
    event_sig = Web3.keccak(text="AccountCreated(uint128,address,address,uint256)").hex()
    filtered_logs = [log for log in logs if HexBytes(log["topics"][0]) == HexBytes(event_sig)]

    if not len(filtered_logs) == 1:
        raise Exception("Failed to decode transaction receipt for account creations")
    
    event = core.events.AccountCreated().process_log(filtered_logs[0])
    
    account_id = event["args"]["accountId"]
    return account_id