from hexbytes import HexBytes
from web3 import Web3

from sdk.reya_rpc.exceptions import TransactionReceiptError
from sdk.reya_rpc.utils.transaction_utils import sign_and_send


def create_account(config: dict):
    """
    Creates a new margin account on Reya DEX.

    Args:
        config (dict): Configuration dictionary containing Web3 contract instances and IDs. Check out config.py for more details.

    Returns:
        dict: Contains transaction receipt and the newly created margin account ID.
    """

    # Retrieve relevant fields from config
    core = config["w3contracts"]["core"]
    account = config["w3account"]

    # Execute the transaction to create a new margin account
    tx_receipt = sign_and_send(config, core.functions.createAccount(account.address))
    print(f"Created account: {tx_receipt['transactionHash'].hex()}")

    # Extract logs from the transaction receipt
    logs = tx_receipt["logs"]

    # Compute event signature for filtering relevant log
    event_sig = Web3.keccak(text="AccountCreated(uint128,address,address,uint256)").hex()

    # Filter logs for the expected event
    filtered_logs = [log for log in logs if HexBytes(log["topics"][0]) == HexBytes(event_sig)]

    # Ensure exactly one matching event log is found
    if not len(filtered_logs) == 1:
        raise TransactionReceiptError("Failed to decode transaction receipt for account creations")

    # Decode event log to extract the new margin account ID
    event = core.events.AccountCreated().process_log(filtered_logs[0])
    account_id = int(event["args"]["accountId"])

    # Return transaction receipt and margin account ID
    return {
        "transaction_receipt": tx_receipt,
        "account_id": account_id,
    }
