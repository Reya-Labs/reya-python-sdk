"""
Signature generation utilities for Reya Trading API authentication.

This module provides tools for creating EIP-712 signatures for order creation
and message signatures for order cancellation.
"""

import json
from decimal import Decimal

from eth_abi import encode
from eth_account import Account
from eth_account.messages import encode_defunct

from sdk.reya_rest_api.config import TradingConfig


class SignatureGenerator:
    """Generate signatures for Reya Trading API requests."""

    def __init__(self, config: TradingConfig):
        """
        Initialize the signature generator with configuration.

        Args:
            config: Trading API configuration
        """
        self.config = config
        self._private_key = config.private_key
        self._chain_id = config.chain_id

        if not self._private_key:
            raise ValueError("Private key is required for signing")

        # Calculate public address from private key
        self._public_address: str = str(Account.from_key(self._private_key).address)

    @property
    def public_address(self) -> str:
        """Get the public address derived from the private key."""
        return self._public_address

    def scale(self, decimals: int):
        """Returns a function that scales a number (str, int, float, or Decimal) to an integer."""
        factor = 10**decimals

        def _scale(value):
            return int(Decimal(value) * factor)

        return _scale

    def encode_inputs_limit_order(
        self,
        is_buy: bool,
        limit_px: Decimal,
        qty: Decimal,
    ) -> str:
        scaler = self.scale(18)

        # Negate qty if it's a sell order
        signed_qty = qty if is_buy else -qty

        encoded = encode(["int256", "uint256"], [scaler(signed_qty), scaler(limit_px)])
        return encoded.hex() if encoded.hex().startswith("0x") else f"0x{encoded.hex()}"

    def encode_inputs_trigger_order(
        self,
        is_buy: bool,
        trigger_px: Decimal,
        limit_px: Decimal,
    ) -> str:
        scaler = self.scale(18)

        encoded = encode(
            ["bool", "uint256", "uint256"],
            [bool(is_buy), scaler(trigger_px), scaler(limit_px)],
        )
        return encoded.hex() if encoded.hex().startswith("0x") else f"0x{encoded.hex()}"

    def create_orders_gateway_nonce(
        self,
        account_id: int,
        market_id: int,
        timestamp_ms: int,
    ) -> int:
        """Create a nonce for Orders Gateway orders."""
        # Validate the input ranges
        if market_id < 0 or market_id >= 2**32:
            raise ValueError("marketId is out of range")
        if account_id < 0 or account_id >= 2**128:
            raise ValueError("accountId is out of range")
        if timestamp_ms < 0 or timestamp_ms >= 2**64:
            raise ValueError("timestamp is out of range")

        hash_uint256 = (account_id << 98) | (timestamp_ms << 32) | market_id

        return hash_uint256

    def sign_raw_order(
        self,
        account_id: int,
        market_id: int,
        exchange_id: int,
        counterparty_account_ids: list,
        order_type: int,
        inputs: str,  # hex-encoded ABI data
        deadline: int,
        nonce: int,
    ) -> str:
        """
        Sign an Orders Gateway order using EIP-712.

        Args:
            account_id: The Reya account ID
            market_id: The market ID for this order
            exchange_id: Exchange ID (usually 2)
            counterparty_account_ids: List of counterparty account IDs
            order_type: Order type enum value
            inputs: ABI-encoded order inputs
            deadline: Signature expiration timestamp
            nonce: The nonce to use for this order (must match the nonce passed to the API)

        Returns:
            Hex-encoded signature
        """
        # Define EIP-712 domain
        domain = {
            "name": "Reya",
            "version": "1",
            "verifyingContract": self.config.default_orders_gateway_address,
        }

        # Define the message types for EIP-712 (conditional order format)
        types = {
            "ConditionalOrder": [
                {"name": "verifyingChainId", "type": "uint256"},
                {"name": "deadline", "type": "uint256"},
                {"name": "order", "type": "ConditionalOrderDetails"},
            ],
            "ConditionalOrderDetails": [
                {"name": "accountId", "type": "uint128"},
                {"name": "marketId", "type": "uint128"},
                {"name": "exchangeId", "type": "uint128"},
                {"name": "counterpartyAccountIds", "type": "uint128[]"},
                {"name": "orderType", "type": "uint8"},
                {"name": "inputs", "type": "bytes"},
                {"name": "signer", "type": "address"},
                {"name": "nonce", "type": "uint256"},
            ],
        }

        # Create the message to sign
        message = {
            "verifyingChainId": self._chain_id,
            "deadline": deadline,
            "order": {
                "accountId": account_id,
                "marketId": market_id,
                "exchangeId": exchange_id,
                "counterpartyAccountIds": counterparty_account_ids,
                "orderType": order_type,
                "inputs": inputs,
                "signer": self._public_address,
                "nonce": nonce,
            },
        }

        # Sign the message using the correct eth-account format
        signed_message = Account.sign_typed_data(self._private_key, domain, types, message)

        return (
            signed_message.signature.hex()
            if signed_message.signature.hex().startswith("0x")
            else f"0x{signed_message.signature.hex()}"
        )

    def sign_cancel_order(self, order_id: str) -> str:
        """
        Sign an order cancellation message using personal_sign.

        Args:
            order_id: ID of the order to cancel

        Returns:
            Hex-encoded signature
        """
        # Create cancellation message
        cancel_message = {
            "orderId": order_id,
            "status": "cancelled",
            "actionType": "changeStatus",
        }

        # Convert to JSON string
        message_str = json.dumps(cancel_message, separators=(",", ":"))

        # Prepare an EIP-191 message
        signable_message = encode_defunct(text=message_str)

        # Sign the message
        signed_message = Account.sign_message(signable_message, private_key=self._private_key)

        return (
            signed_message.signature.hex()
            if signed_message.signature.hex().startswith("0x")
            else f"0x{signed_message.signature.hex()}"
        )
