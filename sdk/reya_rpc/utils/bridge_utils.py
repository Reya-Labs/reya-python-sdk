"""Bridge utility functions for RPC actions."""

from typing import Any

from sdk.reya_rpc.exceptions import BridgeFeeExceededError


def calculate_socket_fees(
    controller_or_vault: Any,
    connector_address: str,
    socket_msg_gas_limit: int,
    socket_empty_payload_size: int,
    fee_limit: int,
) -> int:
    """Calculate and validate socket bridge fees.

    Args:
        controller_or_vault: Contract instance (controller or vault) to call getMinFees on
        connector_address: Address of the connector contract
        socket_msg_gas_limit: Gas limit for the socket transaction
        socket_empty_payload_size: Size of empty payload
        fee_limit: Maximum acceptable fee

    Returns:
        int: Calculated socket fees with 10% buffer

    Raises:
        BridgeFeeExceededError: If calculated fees exceed the limit
    """
    estimated_fees = controller_or_vault.functions.getMinFees(
        connector_address, socket_msg_gas_limit, socket_empty_payload_size
    ).call()
    socket_fees = int(estimated_fees) * 110 // 100

    if socket_fees > fee_limit:
        raise BridgeFeeExceededError("Socket fee is higher than maximum allowed amount")

    return socket_fees
