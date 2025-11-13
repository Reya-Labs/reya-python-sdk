from typing import Any, Optional

import json
import os
import re
from venv import logger

from web3 import Web3

from sdk.open_api.models.order import Order
from sdk.open_api.models.order_type import OrderType
from sdk.open_api.models.perp_execution import PerpExecution
from sdk.open_api.models.position import Position
from sdk.open_api.models.side import Side


def check_error_message(error_message: str, expected_keywords: list[str]):
    error_message_to_check = str(error_message).lower()
    found_keyword = any(keyword.lower() in error_message_to_check for keyword in expected_keywords)

    if not found_keyword:
        logger.info(f"Error message: {decode_error(str(error_message))} {found_keyword} {error_message_to_check}")
        error_message_to_check = decode_error(str(error_message))
        found_keyword = any(keyword.lower() in error_message_to_check for keyword in expected_keywords)

    assert (
        found_keyword
    ), f"Error should mention position reduction: '{error_message_to_check}', but was instead '{expected_keywords}'"


def check_error_code(error_message: str, expected_code: str):
    error_message_str = str(error_message)

    # Extract HTTP status code from patterns like (500)
    http_code_match = re.search(r"\((\d+)\)", error_message_str)
    if http_code_match:
        actual_code = http_code_match.group(1)
        assert (
            actual_code == expected_code
        ), f"check_error_code: Expected HTTP status code '{expected_code}', but got '{actual_code}'"
        return

    assert False, f"check_error_code: No HTTP status code found in error message: '{error_message_str}'"


def match_order(expected_order: Order, order_output: PerpExecution, expected_qty: Optional[str] = None):
    basic_match = (
        order_output.account_id == expected_order.account_id
        and order_output.symbol == expected_order.symbol
        and order_output.side == expected_order.side
    )

    if not basic_match:
        return False

    # For trigger orders (TP/SL), compare executed qty to WebSocket position qty
    if expected_qty:
        return expected_qty == order_output.qty

    # For regular orders, compare with expected order qty
    return order_output.qty == expected_order.qty


def match_order_WS(order_details: Order, order_output: dict):
    return (
        int(order_output["account_id"]) == order_details.account_id
        # and order_output['symbol'] == order_details.symbol TODO uncomment
        and (float(order_output.get("executed_base")) > 0) == order_details.is_buy
        and str(abs(float(order_output.get("executed_base")) / 10**18)) == order_details.qty
    )


def decode_error(error_string: str) -> dict[str, Any]:
    """
    Decode an on-chain error using web3.py and the Errors.json ABI

    Args:
        error_string: A string containing a hex error code

    Returns:
        Dictionary with decoded error information
    """
    # Extract hex error from the string
    hex_pattern = r"0x[a-fA-F0-9]+"
    match = re.search(hex_pattern, str(error_string))

    if not match:
        return {"error": "No hex error found", "original": str(error_string)}

    hex_error = match.group(0)

    # Load the errors ABI

    error_abi_path = os.path.join(os.path.dirname(__file__), "abis", "Errors.json")

    with open(error_abi_path, encoding="utf-8") as f:
        errors_abi = json.load(f)

    # Extract the error selector (first 4 bytes)
    selector = hex_error[2:10]

    # Web3 instance (no provider needed for this use case)
    w3 = Web3()

    # Find the matching error definition in the ABI
    matching_error = None
    error_name = None
    for item in errors_abi:
        if item.get("type") == "error":
            # Calculate the error selector from the signature
            error_name = item.get("name", "")
            input_types = [input_["type"] for input_ in item.get("inputs", [])]
            signature = f"{error_name}({','.join(input_types)})"
            error_selector = w3.keccak(text=signature).hex()[:8]

            if error_selector == selector:
                matching_error = item
                break

    if not matching_error or not error_name:
        raise RuntimeError("Error not found")

    return error_name
