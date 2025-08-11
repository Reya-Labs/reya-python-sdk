"""
Orders resource for Reya Trading API.

This module provides resources for creating and managing orders.
"""

from typing import Optional, Union

import logging
import time
from decimal import Decimal

from sdk.reya_rest_api.constants.enums import (
    LimitOrderType,
    OrdersGatewayOrderType,
    TimeInForce,
    TpslType,
    Trigger,
    TriggerOrderType,
)
from sdk.reya_rest_api.models.orders import (
    CancelOrderRequest,
    CancelOrderResponse,
    CreateOrderResponse,
    LimitOrderRequest,
    TriggerOrderRequest,
)
from sdk.reya_rest_api.resources.base import BaseResource

CONDITIONAL_ORDER_DEADLINE = 10**18
DEFAULT_DEADLINE_MS = 5000
BUY_TRIGGER_ORDER_PRICE_LIMIT = 100000000000000000000


class OrdersResource(BaseResource):
    """Resource for managing orders."""

    logger = logging.getLogger(__name__)

    async def create_limit_order(
        self,
        market_id: int,
        is_buy: bool,
        price: Decimal,
        size: Decimal,
        order_type: LimitOrderType,
        reduce_only: bool,
        expires_after: Optional[int] = None,
    ) -> CreateOrderResponse:
        """
        Create a limit order (IOC / GTC).

        Args:
            market_id: The market ID for this order
            is_buy: Whether this is a buy order
            price: Limit price for the order
            size: Order size (always positive)
            order_type: The type of order (LimitOrderType)
            reduce_only: Whether this is a reduce-only order
            expires_after: The expiration time for the order (only allowed for IOC orders)

        Returns:
            API response for the order creation

        Raises:
            ValueError: If the API returns an error
        """
        if self.signature_generator is None:
            raise ValueError("Private key is required for creating orders")

        if expires_after is not None and order_type.limit.time_in_force != TimeInForce.IOC:
            raise ValueError("Parameter expires_after is only allowed for IOC orders")

        if order_type.limit.time_in_force == TimeInForce.GTC and reduce_only is True:
            raise ValueError("Unexpected True value for parameter reduce_only for GTC orders")

        # Prepare signature data
        if self.signature_generator is None:
            raise ValueError("Signature generator is required for order signing")
        if self.config.account_id is None:
            raise ValueError("Account ID is required for order signing")

        nonce = self.signature_generator.create_orders_gateway_nonce(
            self.config.account_id, market_id, int(time.time_ns() / 1000000)
        )

        inputs = self.signature_generator.encode_inputs_limit_order(is_buy=is_buy, limit_price=price, order_base=size)

        if order_type.limit.time_in_force != TimeInForce.IOC:
            deadline = CONDITIONAL_ORDER_DEADLINE
        elif expires_after is None:
            deadline = int(time.time() * 1000) + DEFAULT_DEADLINE_MS
        else:
            deadline = expires_after

        signature = self.signature_generator.sign_raw_order(
            account_id=self.config.account_id,
            market_id=market_id,
            exchange_id=self.config.dex_id,
            counterparty_account_ids=[self.config.pool_account_id],
            order_type=self.get_limit_order_gateway_type(order_type, reduce_only),
            inputs=inputs,
            deadline=deadline,
            nonce=nonce,
        )

        # Build the order request
        if self.config.account_id is None:
            raise ValueError("Account ID is required for order creation")
        if self.config.wallet_address is None:
            raise ValueError("Wallet address is required for order creation")

        order_request = LimitOrderRequest(
            account_id=self.config.account_id,
            market_id=market_id,
            exchange_id=self.config.dex_id,
            is_buy=is_buy,
            price=price,
            size=size,
            order_type=order_type,
            expires_after=deadline if order_type.limit.time_in_force == TimeInForce.IOC else None,
            reduce_only=reduce_only,
            signature=signature,
            nonce=nonce,
            signer_wallet=self.config.wallet_address,
        )

        # Make the API request
        return await self.create_order(order_request=order_request)

    async def create_trigger_order(
        self,
        market_id: int,
        is_buy: bool,
        trigger_price: Union[Decimal, str],
        trigger_type: TpslType,  # TP or SL
    ) -> CreateOrderResponse:
        """
        Create a trigger order (Take Profit or Stop Loss).

        Args:
            market_id: The market ID for this order
            is_buy: Whether this is a buy order
            trigger_price: Price at which the order triggers
            trigger_type: Type of trigger order (TP or SL)

        Returns:
            API response for the order creation

        Raises:
            ValueError: If the API returns an error
        """

        if self.signature_generator is None:
            raise ValueError("Private key is required for creating orders")
        if self.signature_generator is None:
            raise ValueError("Signature generator is required for order signing")
        if self.config.account_id is None:
            raise ValueError("Account ID is required for order signing")

        limit_price = Decimal(BUY_TRIGGER_ORDER_PRICE_LIMIT) if is_buy else Decimal(0)

        order_type_int = (
            OrdersGatewayOrderType.TAKE_PROFIT if trigger_type == TpslType.TP else OrdersGatewayOrderType.STOP_LOSS
        )

        nonce = self.signature_generator.create_orders_gateway_nonce(
            self.config.account_id, market_id, int(time.time_ns() / 1000000)
        )

        inputs = self.signature_generator.encode_inputs_trigger_order(
            is_buy=is_buy, trigger_price=Decimal(str(trigger_price)), limit_price=limit_price
        )

        signature = self.signature_generator.sign_raw_order(
            account_id=self.config.account_id,
            market_id=market_id,
            exchange_id=self.config.dex_id,
            counterparty_account_ids=[self.config.pool_account_id],
            order_type=order_type_int,
            inputs=inputs,
            deadline=CONDITIONAL_ORDER_DEADLINE,
            nonce=nonce,
        )

        if self.config.account_id is None:
            raise ValueError("Account ID is required for order creation")
        if self.config.wallet_address is None:
            raise ValueError("Wallet address is required for order creation")

        order_request = TriggerOrderRequest(
            account_id=self.config.account_id,
            market_id=market_id,
            exchange_id=self.config.dex_id,
            is_buy=is_buy,
            price=limit_price,
            size=Decimal("0"),
            order_type=TriggerOrderType(trigger=Trigger(trigger_px=str(trigger_price), tpsl=trigger_type)),
            expires_after=None,
            reduce_only=False,
            signature=signature,
            nonce=nonce,
            signer_wallet=self.config.wallet_address,
        )

        # Make the API request
        return await self.create_order(order_request=order_request)

    async def create_order(self, order_request: Union[LimitOrderRequest, TriggerOrderRequest]) -> CreateOrderResponse:
        """
        Create a new order asynchronously.

        Args:
            order_request: Order request object

        Returns:
            API response for the order creation

        Raises:
            ValueError: If the API returns an error
        """

        # Make the async API request
        endpoint = "api/trading/createOrder"
        response_data = await self._post(endpoint, order_request.to_dict())
        self.logger.debug(f"Response: {response_data}")

        return CreateOrderResponse.from_api_response(response_data)

    async def cancel_order(self, order_id: str) -> CancelOrderResponse:
        """
        Cancel an existing order asynchronously.

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
        cancel_request = CancelOrderRequest(order_id=order_id, signature=signature)

        # Make the async API request
        endpoint = "api/trading/cancelOrder"
        response_data = await self._post(endpoint, cancel_request.to_dict())
        self.logger.debug(f"Response: {response_data}")

        return CancelOrderResponse.from_api_response(response_data)

    def get_limit_order_gateway_type(self, order_type: LimitOrderType, reduce_only: bool) -> OrdersGatewayOrderType:
        if order_type.limit.time_in_force == TimeInForce.IOC:
            return (
                OrdersGatewayOrderType.REDUCE_ONLY_MARKET_ORDER if reduce_only else OrdersGatewayOrderType.MARKET_ORDER
            )
        else:
            return OrdersGatewayOrderType.LIMIT_ORDER
