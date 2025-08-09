"""
Conversion utilities for the Reya Trading API.

This module provides functions for converting between different formats and units
used in the Reya API.
"""

from typing import Union

from decimal import Decimal

# Constants for conversions
DECIMALS_WEI = 18  # Token base units (same as ETH wei)
DECIMALS_PRICE = 6  # Price precision in Reya API


def format_decimal(value: Union[str, int, float, Decimal], decimals: int = 18) -> str:
    """
    Format a value as a string with specified decimals.

    Args:
        value: Value to format
        decimals: Number of decimal places

    Returns:
        Formatted string
    """
    if not isinstance(value, Decimal):
        value = Decimal(str(value))

    return f"{value:.{decimals}f}"


def to_wei(amount: Union[str, int, float, Decimal]) -> int:
    """
    Convert a token amount to wei (base units).

    Args:
        amount: Amount in token units

    Returns:
        Amount in wei (base units)
    """
    if not isinstance(amount, Decimal):
        amount = Decimal(str(amount))

    return int(amount * Decimal(10**DECIMALS_WEI))


def from_wei(wei_amount: Union[str, int], as_decimal: bool = False) -> Union[Decimal, float]:
    """
    Convert wei (base units) to token amount.

    Args:
        wei_amount: Amount in wei
        as_decimal: Whether to return a Decimal (True) or float (False)

    Returns:
        Amount in token units
    """
    wei_value = int(wei_amount)
    decimal_value = Decimal(wei_value) / Decimal(10**DECIMALS_WEI)

    return decimal_value if as_decimal else float(decimal_value)


def to_price_value(price: Union[str, int, float, Decimal]) -> int:
    """
    Convert a price to Reya's price format.

    Args:
        price: Price in standard format

    Returns:
        Price in Reya API format
    """
    if not isinstance(price, Decimal):
        price = Decimal(str(price))

    return int(price * Decimal(10**DECIMALS_PRICE))


def from_price_value(price_value: Union[str, int], as_decimal: bool = False) -> Union[Decimal, float]:
    """
    Convert Reya's price format to standard price.

    Args:
        price_value: Price in Reya API format
        as_decimal: Whether to return a Decimal (True) or float (False)

    Returns:
        Price in standard format
    """
    price_int = int(price_value)
    decimal_value = Decimal(price_int) / Decimal(10**DECIMALS_PRICE)

    return decimal_value if as_decimal else float(decimal_value)


def calculate_size_in_base_units(size: Union[str, float, Decimal], price: Union[str, float, Decimal]) -> int:
    """
    Calculate the base units (collateral value) for an order.

    Args:
        size: Order size
        price: Order price

    Returns:
        Order base in base units
    """
    if not isinstance(size, Decimal):
        size = Decimal(str(size))

    if not isinstance(price, Decimal):
        price = Decimal(str(price))

    # Base units = absolute size * price
    return to_wei(abs(size) * price)
