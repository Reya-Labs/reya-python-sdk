"""
Orders resource for Reya Trading API.

This module provides resources for creating and managing orders.
"""
import time
from typing import Union, Optional
import logging

from ..models.orders import (
    MarketOrderRequest,
    LimitOrderRequest,
    TriggerOrderRequest,
    CancelOrderRequest,
    OrderResponse
)
from ..constants.enums import TpslType, OrdersGatewayOrderType, UnifiedOrderType, LimitOrderType, TimeInForce, Limit, TriggerOrderType, Trigger
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
        nonce = self.signature_generator.create_orders_gateway_nonce(self.config.account_id, market_id, int(time.time_ns() / 1000000))  # ms since epoch (int(time.time())
        deadline = self.signature_generator.get_signature_deadline()
        
        # Determine if this is a buy or sell order
        is_buy = float(size) > 0
        
        # Sign the order
        signature = self.signature_generator.sign_market_order(
            account_id=account_id,
            market_id=market_id,
            price=price,
            size=size,
            reduce_only=reduce_only,
            nonce=nonce,
            deadline=deadline
        )
        
        # Create the order request
        order_request = MarketOrderRequest(
            account_id=account_id,
            market_id=market_id,
            exchange_id=self.config.dex_id,
            is_buy=is_buy,
            price=price,
            size=abs(float(size)),  # API expects positive size
            reduce_only=reduce_only,
            order_type=LimitOrderType(limit=Limit(time_in_force=TimeInForce.IOC)),
            nonce=nonce,
            signature=signature,
            signer_wallet=self.config.wallet_address,
            expires_after=deadline
        )
        
        # Make the API request
        return self.create_order(order_request=order_request)
    
    def create_limit_order(
        self,
        market_id: int,
        is_buy: bool,
        price: Union[float, str],
        size: Union[float, str],
    ) -> OrderResponse:
        """
        Create a limit (GTC) order.
        
        Args:
            market_id: The market ID for this order
            is_buy: Whether this is a buy order
            size: Order size (positive for buy, negative for sell)
            price: Limit price for the order
            
        Returns:
            API response for the order creation
            
        Raises:
            ValueError: If the API returns an error
        """
        if self.signature_generator is None:
            raise ValueError("Private key is required for creating orders")

        nonce = self.signature_generator.create_orders_gateway_nonce(self.config.account_id, market_id, int(time.time_ns() / 1000000))  # ms since epoch (int(time.time())

        # Sign the order
        signature = self.signature_generator.sign_conditional_order(
            market_id=market_id,
            order_type=OrdersGatewayOrderType.LIMIT_ORDER,
            is_buy=is_buy,
            trigger_price=price,
            nonce=nonce,
            size=size
        )
        
        # Create the order request
        order_request = LimitOrderRequest(
            account_id=self.config.account_id,
            market_id=market_id,
            exchange_id=self.config.dex_id,
            is_buy=is_buy,
            price=price,
            size=size,
            reduce_only=False,
            order_type=LimitOrderType(limit=Limit(time_in_force=TimeInForce.GTC)),
            signature=signature,
            nonce=nonce,
            signer_wallet=self.config.wallet_address
        )
        
        # Make the API request
        return self.create_order(order_request=order_request)
    
    def create_trigger_order(
        self,
        account_id: int,
        market_id: int,
        trigger_price: Union[float, str],
        price: Union[float, str],
        is_buy: bool,
        trigger_type: TpslType,  # TP or SL
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
        
        order_type = OrdersGatewayOrderType.TAKE_PROFIT if trigger_type == TpslType.TP else OrdersGatewayOrderType.STOP_LOSS
        
        # Generate nonce and deadline
        nonce = self.signature_generator.create_orders_gateway_nonce(self.config.account_id, market_id, int(time.time_ns() / 1000000))  # ms since epoch (int(time.time())
        
        # Sign the order
        signature = self.signature_generator.sign_conditional_order(
            market_id=market_id,
            order_type=order_type,
            is_buy=is_buy,
            trigger_price=trigger_price,
            nonce=nonce,
            size=None,
            order_price_limit=price
        )
        
        # Create the trigger order type
        trigger = Trigger(
            trigger_px=str(trigger_price),
            tpsl=trigger_type
        )
        order_type = TriggerOrderType(trigger=trigger)
        
        # Create the order request
        order_request = TriggerOrderRequest(
            account_id=account_id,
            market_id=market_id,
            exchange_id=self.config.dex_id,
            price=price,
            is_buy=is_buy,
            size="",  # Size must be empty for SL/TP orders
            reduce_only=False,
            order_type=order_type,
            nonce=nonce,
            signature=signature,
            signer_wallet=self.config.wallet_address
        )
        
        # Make the API request
        return self.create_order(order_request=order_request)
    
    def create_order(self, order_request: Union[LimitOrderRequest, TriggerOrderRequest, MarketOrderRequest]) -> OrderResponse:
        """
        Create a new order.
        
        Args:
            order_request: Order request object
            
        Returns:
            API response for the order creation
            
        Raises:
            ValueError: If the API returns an error
        """
        endpoint = "api/trading/createOrder"
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
        endpoint = "api/trading/cancelOrder"
        response_data = self._post(endpoint, cancel_request.to_dict())
        
        return OrderResponse.from_api_response(response_data)
