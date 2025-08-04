"""
Signature generation utilities for Reya Trading API authentication.

This module provides tools for creating EIP-712 signatures for order creation
and message signatures for order cancellation.
"""
import time
import json
from typing import Union, Optional
from decimal import Decimal
from eth_abi import encode

from eth_account import Account
from eth_account.messages import encode_defunct

from ..config import TradingConfig
from ..constants.enums import ConditionalOrderType, ConditionalOrderStatus, OrdersGatewayOrderType


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
        
        # Conditional orders use the same address for now
        self._conditional_orders_address = config.default_conditional_orders_address

        # Calculate public address from private key
        self._public_address = Account.from_key(self._private_key).address
    
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
    
    def encode_inputs(self, order_type: OrdersGatewayOrderType, is_buy=None, trigger_price=None, order_base=None, order_price_limit=None) -> str:
        """
        Encode order inputs for signature based on the conditional order type.

        - LIMIT_ORDER: ['int256', 'uint256'] → order_base (size), trigger_price (limit price)
        - STOP_LOSS / TAKE_PROFIT: ['bool', 'uint256', 'uint256'] → is_buy, trigger_price, order_price_limit
        """
        scaler = self.scale(18)


        if order_type in (ConditionalOrderType.STOP_LOSS, ConditionalOrderType.TAKE_PROFIT):
            if is_buy is None or trigger_price is None or order_price_limit is None:
                raise ValueError("STOP_LOSS / TAKE_PROFIT require is_buy, trigger_price, and order_price_limit")
            encoded = encode(
                ['bool', 'uint256', 'uint256'],
                [bool(is_buy), scaler(trigger_price), scaler(order_price_limit)]
            )
            return encoded.hex() if encoded.hex().startswith("0x") else f"0x{encoded.hex()}"


        if order_base is None or trigger_price is None:
            raise ValueError("LIMIT_ORDER requires order_base and trigger_price")
        encoded = encode(
            ['int256', 'uint256'],
            [scaler(order_base), scaler(trigger_price)]
        )
        return encoded.hex() if encoded.hex().startswith("0x") else f"0x{encoded.hex()}"

    def create_orders_gateway_nonce(
        self,
        account_id: int,
        market_id: int,
        timestamp_ms: int
    ) -> int:
        """Create a nonce for Orders Gateway orders."""
        # Validate the input ranges
        if market_id < 0 or market_id >= 2 ** 32:
            raise ValueError('marketId is out of range')
        if account_id < 0 or account_id >= 2 ** 128:
            raise ValueError('accountId is out of range')
        if timestamp_ms < 0 or timestamp_ms >= 2 ** 64:
            raise ValueError('timestamp is out of range')

        hash_uint256 = (
            (account_id << 98) |
            (timestamp_ms << 32) |
            market_id
        )

        return hash_uint256
    
    def sign_orders_gateway_order(
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
            "verifyingContract": self._conditional_orders_address
        }
        
        # Define the message types for EIP-712 (conditional order format)
        types = {
            "ConditionalOrder": [
                {"name": "verifyingChainId", "type": "uint256"},
                {"name": "deadline", "type": "uint256"},
                {"name": "order", "type": "ConditionalOrderDetails"}
            ],
            "ConditionalOrderDetails": [
                {"name": "accountId", "type": "uint128"},
                {"name": "marketId", "type": "uint128"},
                {"name": "exchangeId", "type": "uint128"},
                {"name": "counterpartyAccountIds", "type": "uint128[]"},
                {"name": "orderType", "type": "uint8"},
                {"name": "inputs", "type": "bytes"},
                {"name": "signer", "type": "address"},
                {"name": "nonce", "type": "uint256"}
            ]
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
                "nonce": nonce
            }
        }
        
        # Sign the message using the correct eth-account format
        signed_message = Account.sign_typed_data(
            self._private_key,
            domain,
            types,
            message
        )
        
        return signed_message.signature.hex() if signed_message.signature.hex().startswith("0x") else f"0x{signed_message.signature.hex()}"
    
    def sign_market_order(
        self,
        account_id: int,
        market_id: int,
        price: float,
        size: float,
        reduce_only: bool,
        nonce: int,
        deadline: int,
    ) -> str:
        """
        Sign a market (IOC) order using the Orders Gateway signature format.
        
        Args:
            account_id: The Reya account ID
            market_id: The market ID for this order
            price: Limit price for the order
            size: Order size (positive for buy, negative for sell)
            reduce_only: Whether this is a reduce-only order
            nonce: Random nonce
            deadline: Signature expiration timestamp
            
        Returns:
            Hex-encoded signature
        """
        if deadline is None:
            deadline = int(time.time()) + 3  # Current time + 3 seconds for market orders
        
        # ABI encode the inputs: ['int256', 'uint256'] → [size, price]
        # Size needs to be scaled to E18 and signed
        # Price needs to be scaled to E18
        scaler = self.scale(18)
        size_e18 = scaler(size)
        price_e18 = scaler(price)
        
        inputs = encode(
            ['int256', 'uint256'],
            [size_e18, price_e18]
        )
        inputs_encoded = inputs.hex() if inputs.hex().startswith("0x") else f"0x{inputs.hex()}"
        
        # Determine order type
        order_type = OrdersGatewayOrderType.REDUCE_ONLY_MARKET_ORDER if reduce_only else OrdersGatewayOrderType.MARKET_ORDER
        
        return self.sign_orders_gateway_order(
            account_id=account_id,
            market_id=market_id,
            exchange_id=self.config.dex_id,
            counterparty_account_ids=[self.config.pool_account_id],
            order_type=order_type,
            inputs=inputs_encoded,
            deadline=deadline,
            nonce=nonce
        )
    
    def sign_conditional_order(
        self,
        market_id: int,
        order_type: OrdersGatewayOrderType,
        is_buy: bool,
        trigger_price: Union[str, float],
        nonce: int,
        size: Optional[Union[str, float]] = None,
        order_price_limit: Optional[Union[str, float]] = None,
    ) -> str:
        """
        Sign a conditional order (limit, take profit, stop loss) using EIP-712.
        
        Args:
            market_id: The market ID for this order
            order_type: The type of conditional order
            is_buy: Whether this is a buy order
            trigger_price: Price at which the order triggers
            size: Base amount of the order
            order_price_limit: Limit price for the order
            nonce: Random nonce

        Returns:
            Hex-encoded signature
        """

        deadline = 10 ** 18  # CONDITIONAL_ORDER_SIG_DEADLINE
        
        # Encode inputs based on order type
        inputs = self.encode_inputs(
            order_type, is_buy, trigger_price, size, order_price_limit
        )
        
        return self.sign_orders_gateway_order(
            account_id=self.config.account_id,
            market_id=market_id,
            exchange_id=self.config.dex_id,
            counterparty_account_ids=[self.config.pool_account_id],
            order_type=order_type.value,
            inputs=inputs,
            deadline=deadline,
            nonce=nonce
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
            "status": ConditionalOrderStatus.CANCELLED,
            "actionType": "changeStatus",
        }

        # Convert to JSON string
        message_str = json.dumps(cancel_message, separators=(',', ':'))

        # Prepare an EIP-191 message
        signable_message = encode_defunct(text=message_str)

        # Sign the message
        signed_message = Account.sign_message(signable_message, private_key=self._private_key)
        
        return signed_message.signature.hex() if signed_message.signature.hex().startswith("0x") else f"0x{signed_message.signature.hex()}"