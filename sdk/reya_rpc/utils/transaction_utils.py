"""Transaction utility functions for RPC actions."""

from typing import Any

from hexbytes import HexBytes
from web3 import Web3

from sdk.reya_rpc.exceptions import TransactionReceiptError


def extract_share_balance_updated_event(tx_receipt: Any, passive_perp: Any) -> tuple[int, int]:
    """Extract ShareBalanceUpdated event from transaction receipt.

    Args:
        tx_receipt: Transaction receipt containing logs
        passive_perp: Passive perp contract instance

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
    event = passive_perp.events.ShareBalanceUpdated().process_log(filtered_logs[0])
    shares_delta = int(event["args"]["sharesDelta"])
    balance_delta = int(event["args"]["balanceDelta"])

    return shares_delta, balance_delta
