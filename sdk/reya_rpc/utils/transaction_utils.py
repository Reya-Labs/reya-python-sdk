"""Transaction utility functions for RPC actions."""

from typing import Any, cast

from hexbytes import HexBytes
from web3 import Web3
from web3.types import TxReceipt

from sdk.reya_rpc.exceptions import TransactionReceiptError


def sign_and_send(config: dict[str, Any], contract_function: Any, value: int = 0) -> TxReceipt:
    """Build a contract call, sign it with the configured key, send it raw and wait for the receipt.

    Signing happens locally, so this works against any remote RPC; ``.transact()`` would instead need
    the node to hold the key.

    Args:
        config: Configuration dictionary from ``get_config()``.
        contract_function: A bound contract call, e.g. ``core.functions.createAccount(owner)``.
        value: Native token to attach, in wei.

    Returns:
        TxReceipt: The mined transaction's receipt.
    """
    w3 = config["w3"]
    account = config["w3account"]

    tx = contract_function.build_transaction(
        {
            "from": account.address,
            "nonce": w3.eth.get_transaction_count(account.address),
            "chainId": config["chain_id"],
            "value": value,
        }
    )
    signed_tx = w3.eth.account.sign_transaction(tx, private_key=account.key)
    tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)

    # web3 returns Any for the dynamic eth namespace.
    return cast(TxReceipt, w3.eth.wait_for_transaction_receipt(tx_hash))


def extract_share_balance_updated_event(tx_receipt: Any, passive_pool: Any) -> tuple[int, int]:
    """Extract ShareBalanceUpdated event from transaction receipt.

    Args:
        tx_receipt: Transaction receipt containing logs
        passive_pool: Passive pool contract instance

    Returns:
        tuple: (shares_delta, balance_delta) extracted from the event

    Raises:
        TransactionReceiptError: If event cannot be found or decoded
    """
    # Extract logs from the transaction receipt
    logs = tx_receipt["logs"]

    # Compute event signature for filtering relevant log
    event_sig = Web3.keccak(
        text="ShareBalanceUpdated(uint128,address,int256,uint256,int256,uint256,address,int256)"
    ).hex()

    # Filter logs for the expected event
    filtered_logs = [log for log in logs if HexBytes(log["topics"][0]) == HexBytes(event_sig)]

    # Ensure exactly one matching event log is found
    if not len(filtered_logs) == 1:
        raise TransactionReceiptError("Failed to decode transaction receipt for stake/unstake operation")

    # Decode event log to extract share and balance information
    event = passive_pool.events.ShareBalanceUpdated().process_log(filtered_logs[0])
    shares_delta = int(event["args"]["sharesDelta"])
    balance_delta = int(event["args"]["balanceDelta"])

    return shares_delta, balance_delta
