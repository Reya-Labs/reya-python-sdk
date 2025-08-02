"""
Signature generation utilities for Reya Trading API authentication.

This module provides tools for creating EIP-712 signatures for order creation
and message signatures for order cancellation.
"""
import time
import json
import secrets
from typing import Dict, Any, List, Union, Tuple, Optional
from decimal import Decimal
from eth_abi import encode
from eth_account.messages import encode_typed_data

from eth_account import Account
from eth_account.messages import encode_defunct
from web3 import Web3

from ..config import TradingConfig
from ..constants.enums import ConditionalOrderType, ConditionalOrderStatus


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
        
        self._orders_gateway_address = (
            config.orders_gateway_address or 
            config.default_orders_gateway_address
        )
        
        # Calculate public address from private key
        self._public_address = Account.from_key(self._private_key).address
    
    def generate_nonce(self) -> int:
        """Generate a time-based nonce for order signatures.
        
        Returns:
            Current timestamp in milliseconds as nonce
        """
        return int(time.time() * 1000)  # Current time in milliseconds
    
    def get_signature_deadline(self, validity_seconds: int = 60) -> int:
        """
        Get timestamp for signature deadline.
        
        Args:
            validity_seconds: Number of seconds the signature should be valid
            
        Returns:
            Unix timestamp when the signature expires
        """
        return int(time.time()) + validity_seconds

    def scale(self, decimals: int):
        """Returns a function that scales a number (str, int, float, or Decimal) to an integer."""
        factor = 10 ** decimals
        def _scale(value):
            return int(Decimal(value) * factor)
        return _scale
    
    def encode_inputs(self, order_type: ConditionalOrderType, is_long=None, trigger_price=None, order_base=None, order_price_limit=None) -> bytes:
        """
        Encode order inputs for signature based on the conditional order type.

        - LIMIT_ORDER: ['int256', 'uint256'] → order_base (size), trigger_price (limit price)
        - STOP_LOSS / TAKE_PROFIT: ['bool', 'uint256', 'uint256'] → is_long, trigger_price, order_price_limit
        """
        scaler = self.scale(18)

        if order_type == ConditionalOrderType.LIMIT_ORDER:
            if order_base is None or trigger_price is None:
                raise ValueError("LIMIT_ORDER requires order_base and trigger_price")
            return encode(
                ['int256', 'uint256'],
                [scaler(order_base), scaler(trigger_price)]
            )

        elif order_type in (ConditionalOrderType.STOP_LOSS, ConditionalOrderType.TAKE_PROFIT):
            if is_long is None or trigger_price is None or order_price_limit is None:
                raise ValueError("STOP_LOSS / TAKE_PROFIT require is_long, trigger_price, and order_price_limit")
            return encode(
                ['bool', 'uint256', 'uint256'],
                [bool(is_long), scaler(trigger_price), scaler(order_price_limit)]
            )

        else:
            raise ValueError(f"Unsupported order type: {order_type}")
    
    def sign_market_order(
        self,
        account_id: int,
        market_id: int,
        size: float,
        price: float,
        reduce_only: bool,
        nonce: Optional[int] = None,
        deadline: Optional[int] = None,
    ) -> str:
        """
        Sign a market (IOC) order using EIP-712.
        
        Args:
            account_id: The Reya account ID
            market_id: The market ID for this order
            size: Order size (positive for buy, negative for sell)
            price: Limit price for the order
            reduce_only: Whether this is a reduce-only order
            nonce: Random nonce (will generate one if not provided)
            deadline: Signature expiration timestamp (will generate one if not provided)
            
        Returns:
            Hex-encoded signature
        """
        if nonce is None:
            nonce = self.generate_nonce()
            
        if deadline is None:
            deadline = self.get_signature_deadline()
        
        # Define EIP-712 domain
        domain = {
            "name": "Reya",
            "version": "1",
            "chainId": self._chain_id,
            "verifyingContract": self._orders_gateway_address
        }
        
        # Define the message types for EIP-712
        types = {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"}
            ],
            "PlaceIOCOrder": [
                {"name": "sender", "type": "address"},
                {"name": "accountId", "type": "uint32"},
                {"name": "marketId", "type": "uint32"},
                {"name": "size", "type": "int128"},
                {"name": "price", "type": "uint128"},
                {"name": "reduceOnly", "type": "bool"},
                {"name": "nonce", "type": "uint32"},
                {"name": "deadline", "type": "uint64"},
            ]
        }
        
        # Convert size to smart contract format (positive for buy, negative for sell)
        size_value = abs(size) * 10**18
        if size < 0:
            size_value = -size_value
            
        # Convert price to smart contract format
        price_value = int(price * 10**6)
        
        # Create the message to sign
        message = {
            "sender": self._public_address,
            "accountId": account_id,
            "marketId": market_id,
            "size": int(size_value),
            "price": price_value,
            "reduceOnly": reduce_only,
            "nonce": nonce,
            "deadline": deadline
        }
        
        # Sign the message
        signed_message = Account.sign_typed_data(
            self._private_key,
            domain_data=domain,
            message_types=types,
            message_data=message
        )
        
        return signed_message.signature.hex()
    
    def sign_conditional_order(
        self,
        market_id: int,
        is_buy: bool,
        trigger_price: str,
        order_base: str,
        nonce: Optional[int] = None,
        deadline: Optional[int] = None,
        order_price_limit: Optional[str] = None,
    ) -> str:
        """
        Sign a conditional order (limit, take profit, stop loss) using EIP-712.
        
        Args:
            account_id: The Reya account ID
            market_id: The market ID for this order
            order_type: The type of conditional order
            is_buy: Whether this is a buy order
            trigger_price: Price at which the order triggers
            price_limit: Limit price for the order
            order_base: Base amount of the order
            nonce: Random nonce (will generate one if not provided)
            deadline: Signature expiration timestamp (will generate one if not provided)
            
        Returns:
            Hex-encoded signature
        """
        
        # Define EIP-712 domain
        domain = {
            "name": "Reya",
            "version": "1",
            "verifyingContract": self._orders_gateway_address
        }
        
        # Define the message types for EIP-712 to match conditionalOrderTypes
        types = {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "verifyingContract", "type": "address"},
            ],
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
        
        # Convert order inputs to contract format
        # TODO: for now, this only works for LIMIT_ORDER
        inputs = self.encode_inputs(ConditionalOrderType.LIMIT_ORDER, is_buy, trigger_price, order_base, order_price_limit)
        
        # Create the message to sign
        value = {
            "verifyingChainId": self._chain_id,
            "deadline": int(deadline),
            "order": {
                "accountId": int(self.config.account_id),
                "marketId": int(market_id),
                "exchangeId": 2, # TODO: figure out what to send here
                "counterpartyAccountIds": [2], # TODO: figure out what to send here
                "orderType": int(ConditionalOrderType.LIMIT_ORDER),
                "inputs": inputs,  # bytes
                "signer": self.config.wallet_address,
                "nonce": int(nonce),
            },
        }

        message = encode_typed_data({
            "types": types,
            "domain": domain,
            "primaryType": "ConditionalOrder",
            "message": value,
        })
        
        # Sign the message
        signed_message = Account.sign_message(message, self._private_key)
        
        return signed_message.signature.hex()
    
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
            "status": ConditionalOrderStatus.CANCELLED,
            "actionType": "changeStatus",
        }

        # Convert to JSON string
        message_str = json.dumps(cancel_message, separators=(',', ':'))

        # Prepare an EIP-191 message
        signable_message = encode_defunct(text=message_str)

        # Sign the message
        signed_message = Account.sign_message(signable_message, private_key=self._private_key)
        
        return signed_message.signature.hex()
