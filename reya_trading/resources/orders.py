"""
Orders resource for Reya Trading API.

This module provides resources for creating and managing orders.
"""
from typing import Dict, Any, Optional, Union, List
import logging

from ..models.orders import (
    MarketOrderRequest,
    LimitOrderRequest,
    TriggerOrderRequest,
    CancelOrderRequest,
    OrderResponse
)
from ..constants.enums import TpslType, ConditionalOrderType, UnifiedOrderType
from .base import BaseResource


class OrdersResource(BaseResource):
    """Resource for managing orders."""
    
    def create_market_order(
        self,
        account_id: int,
        market_id: int,
        size: Union[float, str],
        price: Union[float, str],
        reduce_only: bool = False
    ) -> OrderResponse:
        """
        Create a market (IOC) order.
        
        Args:
            account_id: The Reya account ID
            market_id: The market ID for this order
            size: Order size (positive for buy, negative for sell)
            price: Limit price for the order
            reduce_only: Whether this is a reduce-only order
            
        Returns:
            API response for the order creation
            
        Raises:
            ValueError: If the API returns an error
        """
        if self.signature_generator is None:
            raise ValueError("Private key is required for creating orders")
            
        # Generate nonce and deadline
        nonce = self.signature_generator.generate_nonce()
        deadline = self.signature_generator.get_signature_deadline()
        
        # Convert size and price to strings if they are floats
        size_val = str(size) if isinstance(size, float) else size
        price_val = str(price) if isinstance(price, float) else price
        
        # Determine if this is a buy or sell order
        is_buy = float(size_val) > 0
        
        # Sign the order
        signature = self.signature_generator.sign_market_order(
            account_id=account_id,
            market_id=market_id,
            size=float(size_val),
            price=float(price_val),
            reduce_only=reduce_only,
            nonce=nonce,
            deadline=deadline
        )
        
        # Create the order request
        order_request = MarketOrderRequest(
            account_id=account_id,
            market_id=market_id,
            size=abs(float(size_val)),  # API expects positive size
            price=price_val,
            is_buy=is_buy,
            reduce_only=reduce_only,
            nonce=nonce,
            signature=signature,
            signer_wallet=self.signature_generator._public_address,
            expires_after=deadline
        )
        
        # Make the API request
        endpoint = "api/trading/create-order"
        response_data = self._post(endpoint, order_request.to_dict())
        
        return OrderResponse.from_api_response(response_data)
    
    def create_limit_order(
        self,
        market_id: int,
        is_buy: bool,
        price: Union[float, str],
        size: Union[float, str],
        type: UnifiedOrderType
    ) -> OrderResponse:
        """
        Create a limit (GTC) order.
        
        Args:
            market_id: The market ID for this order
            size: Order size (positive for buy, negative for sell)
            price: Limit price for the order
            
        Returns:
            API response for the order creation
            
        Raises:
            ValueError: If the API returns an error
        """
        if self.signature_generator is None:
            raise ValueError("Private key is required for creating orders")

        nonce = self.signature_generator.generate_nonce()
        deadline = self.signature_generator.get_signature_deadline()
        
        # Sign the order
        signature = self.signature_generator.sign_conditional_order(
            market_id=market_id,
            order_type=ConditionalOrderType.LIMIT_ORDER,
            is_buy=is_buy,
            trigger_price=price,
            order_base=size,
            nonce=nonce,
            deadline=deadline
        )
        
        # Create the order request
        order_request = LimitOrderRequest(
            account_id=self.config.account_id,
            market_id=market_id,
            is_buy=is_buy,
            price=price,
            size=size,
            reduce_only=False,
            type=type.to_dict() if hasattr(type, 'to_dict') else {"limit": {"timeInForce": "GTC"}},
            signature=signature,
            nonce=nonce,
            signer_wallet=self.signature_generator._public_address,
        )
        
        # Make the API request
        endpoint = "api/trading/create-order"
        response_data = self._post(endpoint, order_request.to_dict())
        
        return OrderResponse.from_api_response(response_data)
    
    def create_trigger_order(
        self,
        account_id: int,
        market_id: int,
        trigger_price: Union[float, str],
        price: Union[float, str],
        is_buy: bool,
        trigger_type: str  # TP or SL
    ) -> OrderResponse:
        """
        Create a trigger order (Take Profit or Stop Loss).
        
        Args:
            account_id: The Reya account ID
            market_id: The market ID for this order
            trigger_price: Price at which the order triggers
            price: Limit price for the order
            is_buy: Whether this is a buy order
            trigger_type: Type of trigger order (TP or SL)
            
        Returns:
            API response for the order creation
            
        Raises:
            ValueError: If the API returns an error
        """
        if self.signature_generator is None:
            raise ValueError("Private key is required for creating orders")
            
        # Validate trigger type
        if trigger_type not in (TpslType.TP, TpslType.SL):
            raise ValueError(f"Invalid trigger type: {trigger_type}. Must be 'TP' or 'SL'.")
        
        # Determine order type based on trigger type
        order_type = (
            ConditionalOrderType.TAKE_PROFIT if trigger_type == TpslType.TP
            else ConditionalOrderType.STOP_LOSS
        )
        
        # Generate nonce and deadline
        nonce = self.signature_generator.generate_nonce()
        deadline = self.signature_generator.get_signature_deadline()
        
        # Convert prices to strings if they are floats
        trigger_price_val = str(trigger_price) if isinstance(trigger_price, float) else trigger_price
        price_val = str(price) if isinstance(price, float) else price
        
        # For trigger orders, the order base is set based on position size
        # This is typically set to a reasonable value like 1.0 for the signature
        # The actual order size will be based on the position being closed
        order_base = 1.0
        
        # Sign the order
        signature = self.signature_generator.sign_conditional_order(
            market_id=market_id,
            order_type=order_type,
            is_buy=is_buy,
            trigger_price=float(trigger_price_val),
            order_price_limit=float(price_val),
            order_base=order_base,
            nonce=nonce,
            deadline=deadline
        )
        
        # Create the order request
        order_request = TriggerOrderRequest(
            account_id=account_id,
            market_id=market_id,
            trigger_price=trigger_price_val,
            price=price_val,
            is_buy=is_buy,
            trigger_type=trigger_type,
            size="",  # SL/TP orders typically have 0 size
            reduce_only=False,
            nonce=nonce,
            signature=signature,
            signer_wallet=self.signature_generator._public_address
        )
        
        # Make the API request
        endpoint = "api/trading/create-order"
        response_data = self._post(endpoint, order_request.to_dict())
        
        return OrderResponse.from_api_response(response_data)
    
    def cancel_order(self, order_id: str) -> OrderResponse:
        """
        Cancel an existing order.
        
        Args:
            order_id: ID of the order to cancel
            
        Returns:
            API response for the order cancellation
            
        Raises:
            ValueError: If the API returns an error
        """
        if self.signature_generator is None:
            raise ValueError("Private key is required for cancelling orders")
        
        # Sign the cancellation request
        signature = self.signature_generator.sign_cancel_order(order_id)
        
        # Create the cancellation request
        cancel_request = CancelOrderRequest(
            order_id=order_id,
            signature=signature
        )
        
        # Make the API request
        endpoint = "api/trading/cancel-order"
        response_data = self._post(endpoint, cancel_request.to_dict())
        
        return OrderResponse.from_api_response(response_data)
    
    def get_orders(
        self,
        wallet_address: str,
    ) -> Dict[str, Any]:
        """
        Get filled orders for a wallet address.
        
        Args:
            wallet_address: The wallet address to get orders for
            
        Returns:
            Dictionary containing orders data and metadata
            
        Raises:
            ValueError: If the API returns an error
        """

        # Make the API request
        endpoint = f"api/trading/wallet/{wallet_address}/orders"
        response_data = self._get(endpoint)
        
        return response_data
        
    def get_conditional_orders(
        self,
        wallet_address: str,
    ) -> List[Dict[str, Any]]:
        """
        Get conditional orders (limit, stop loss, take profit) for a wallet address.
        
        Args:
            wallet_address: The wallet address to get orders for
            
        Returns:
            List of conditional orders
            
        Raises:
            ValueError: If the API returns an error
        """
        
        # Make the API request
        endpoint = f"api/trading/wallet/{wallet_address}/conditionalOrders"
        response_data = self._get(endpoint)
        
        # The response is directly a list of orders
        return response_data
