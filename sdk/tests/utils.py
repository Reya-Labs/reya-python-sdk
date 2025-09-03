from typing import Any

import json
import re
from venv import logger

from web3 import Web3

from sdk.open_api.models.perp_execution import PerpExecution
from sdk.open_api.models.side import Side
from sdk.reya_rest_api.constants.enums import Limit, LimitOrderType, TimeInForce, TpslType, Trigger, TriggerOrderType
from sdk.tests.models import OrderDetails


def parse_trade(trade):
    if trade is None:
        return None
    parsed_trade = {
        "market_id": int(trade.get("market_id", "0")),
        "account_id": int(trade.get("account_id", "0")),
        "executed_base": float(trade.get("executed_base", "0")) / 10**18,
        "executed_quote": float(trade.get("executed_quote", "0")) / 10**18,
        "execution_price": float(trade.get("price", "0")) / 10**18,
        "position_funding_value": float(trade.get("position_funding_value", "0")) / 10**18,
        "event_sequence_number": int(trade.get("event_sequence_number", "0")),
    }
    return parsed_trade


def parse_order(order):
    if order is None:
        return None
    parsed_order = {
        "id": order.get("id"),
        "market_id": int(order.get("market_id", "0")),
        "account_id": int(order.get("account_id", "0")),
        "order_type": (
            LimitOrderType(limit=Limit(time_in_force=TimeInForce.GTC))
            if order.get("order_type") == "Limit Order"
            else TriggerOrderType(trigger=Trigger(tpsl=TpslType.TP, trigger_px=order.get("trigger_price", "0")))
        ),
        "is_long": order.get("is_long"),
        "trigger_price": float(order.get("trigger_price", "0")),
        "order_base": float(order.get("order_base", "0")),
        "status": order.get("status"),
    }
    return parsed_order


def check_error_message(error_message: str, expected_keywords: list[str]):
    error_message_to_check = str(error_message).lower()
    found_keyword = any(keyword.lower() in error_message_to_check for keyword in expected_keywords)

    if not found_keyword:
        logger.info(f"Error message: {decode_error(str(error_message))} {found_keyword} {error_message_to_check}")
        error_message_to_check = decode_error(str(error_message)).lower()
        found_keyword = any(keyword.lower() in error_message_to_check for keyword in expected_keywords)

    assert (
        found_keyword
    ), f"Error should mention position reduction: '{error_message_to_check}', but was instead '{expected_keywords}'"


def match_order(order_details: OrderDetails, order_output: PerpExecution):
    return (
        order_output.account_id == order_details.account_id
        and order_output.symbol == order_details.symbol
        and (order_output.side == Side.B) == order_details.is_buy
        and order_output.qty == order_details.qty
    )


def match_order_WS(order_details: OrderDetails, order_output: dict):
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

    try:
        # Load the errors ABI
        import os

        error_abi_path = os.path.join(os.path.dirname(__file__), "abis", "Errors.json")

        with open(error_abi_path) as f:
            errors_abi = json.load(f)

        # Extract the error selector (first 4 bytes)
        selector = hex_error[2:10]

        # Web3 instance (no provider needed for this use case)
        w3 = Web3()

        # Find the matching error definition in the ABI
        matching_error = None
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

        if not matching_error:
            return None
        return error_name
    except Exception:
        return None


# create a mapping function between symbol and market id
# the symbols are 'ETHRUSDPERP' for matcket eth
