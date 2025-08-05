"""
Orders resource for Reya Trading API.

This module provides resources for creating and managing orders.
"""
import time
from typing import Union, Optional
import logging

from sdk.reya_rest_api.models.orders import (
    MarketOrderRequest,
    LimitOrderRequest,
    TriggerOrderRequest,
    CancelOrderRequest,
    OrderResponse
)
from sdk.reya_rest_api.constants.enums import TpslType, OrdersGatewayOrderType, LimitOrderType, TimeInForce, Limit, TriggerOrderType, Trigger
from sdk.reya_rest_api.resources.base import BaseResource


class OrdersResource(BaseResource):
    """Resource for managing orders."""
    
    logger = logging.getLogger(__name__)
    
    def create_market_order(
        self,
        market_id: int,
        is_buy: bool,
        price: Union[float, str],
        size: Union[float, str],
        reduce_only: bool = False
    ) -> OrderResponse:
        """
        Create a market (IOC) order.
        
        Args:
            market_id: The market ID for this order
            is_buy: Whether this is a buy order
            price: Limit price for the order
            size: Order size (always positive)
            reduce_only: Whether this is a reduce-only order

        Returns:
            API response for the order creation
            
        Raises:
            ValueError: If the API returns an error
        """
        if self.signature_generator is None:
            raise ValueError("Private key is required for creating orders")
            
        # Determine order type
        order_type = OrdersGatewayOrderType.REDUCE_ONLY_MARKET_ORDER if reduce_only else OrdersGatewayOrderType.MARKET_ORDER
        
        # Generate nonce and deadline
        nonce = self.signature_generator.create_orders_gateway_nonce(self.config.account_id, market_id, int(time.time_ns() / 1000000))  # ms since epoch (int(time.time())
        
        # Sign the order
        signature = self.signature_generator.sign_order(
            market_id=market_id,
            order_type=order_type,
            nonce=nonce,
            is_buy=is_buy,
            price=price,
            size=size
        )
        
        # Create the order request
        order_request = MarketOrderRequest(
            account_id=self.config.account_id,
            market_id=market_id,
            exchange_id=self.config.dex_id,
            is_buy=is_buy,
            price=price,
            size=size,
            reduce_only=reduce_only,
            order_type=LimitOrderType(limit=Limit(time_in_force=TimeInForce.IOC)),
            nonce=nonce,
            signature=signature,
            signer_wallet=self.config.wallet_address,
            expires_after=self.signature_generator.get_signature_deadline()
        )
        
        # Make the API request
        return self.create_order(order_request=order_request)
    
    def create_limit_order(
        self,
        market_id: int,
        is_buy: bool,
        price: Union[float, str],
        size: Union[float, str],
        order_type: LimitOrderType,
    ) -> OrderResponse:
        """
        Create a limit (GTC) order.
        
        Args:
            market_id: The market ID for this order
            is_buy: Whether this is a buy order
            price: Limit price for the order
            size: Order size (always positive)
            
        Returns:
            API response for the order creation
            
        Raises:
            ValueError: If the API returns an error
        """
        if self.signature_generator is None:
            raise ValueError("Private key is required for creating orders")

        nonce = self.signature_generator.create_orders_gateway_nonce(self.config.account_id, market_id, int(time.time_ns() / 1000000))  # ms since epoch (int(time.time())

        # Sign the order
        signature = self.signature_generator.sign_order(
            market_id=market_id,
            order_type=OrdersGatewayOrderType.LIMIT_ORDER,
            nonce=nonce,
            is_buy=is_buy,
            price=price,
            size=size,
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
            order_type=order_type,
            signature=signature,
            nonce=nonce,
            signer_wallet=self.config.wallet_address
        )
        
        # Make the API request
        return self.create_order(order_request=order_request)
    
    def create_trigger_order(
        self,
        market_id: int,
        is_buy: bool,
        trigger_price: Union[float, str],
        price: Union[float, str],
        trigger_type: TpslType,  # TP or SL
    ) -> OrderResponse:
        """
        Create a trigger order (Take Profit or Stop Loss).
        
        Args:
            market_id: The market ID for this order
            is_buy: Whether this is a buy order
            trigger_price: Price at which the order triggers
            price: Limit price for the order
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
        signature = self.signature_generator.sign_order(
            market_id=market_id,
            order_type=order_type,
            nonce=nonce,
            is_buy=is_buy,
            price=trigger_price,
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
            account_id=self.config.account_id,
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

        # Make the API request
        endpoint = "api/trading/createOrder"
        self.logger.debug(f"POST {endpoint} with data: {order_request.to_dict()}")
        response_data = self._post(endpoint, order_request.to_dict())
        self.logger.debug(f"Response: {response_data}")
        
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
        self.logger.debug(f"POST {endpoint} with data: {cancel_request.to_dict()}")
        response_data = self._post(endpoint, cancel_request.to_dict())
        self.logger.debug(f"Response: {response_data}")
        
        return OrderResponse.from_api_response(response_data)
