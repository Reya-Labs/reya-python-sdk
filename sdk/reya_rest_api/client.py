"""
Reya Trading Client - Main entry point for the Reya Trading API.

This module provides a client for interacting with the Reya Trading REST API.
"""
import logging
import asyncio
from typing import Dict, Any, Optional, Union

from sdk.reya_rest_api.config import TradingConfig, get_config
from sdk.reya_rest_api.auth.signatures import SignatureGenerator
from sdk.reya_rest_api.resources.orders import OrdersResource
from sdk.reya_rest_api.resources.wallet import WalletResource
from sdk.reya_rest_api.constants.enums import TpslType, LimitOrderType
from sdk.reya_rest_api.models.orders import CreateOrderResponse, CancelOrderResponse


class ReyaTradingClient:
    """
    Client for interacting with the Reya Trading API.
    
    This class provides a high-level interface to the Reya Trading API,
    with resources for managing orders and accounts.
    """
    
    def __init__(
        self,
        config: Optional[TradingConfig] = None,
        private_key: Optional[str] = None,
        api_url: Optional[str] = None,
        chain_id: Optional[int] = None,
        account_id: Optional[int] = None,
        wallet_address: Optional[str] = None
    ):
        """
        Initialize the Reya Trading client.
        
        Args:
            config: Optional trading configuration object
            private_key: Optional private key for signing requests
            api_url: Optional API URL override
            chain_id: Optional chain ID override
            account_id: Optional default account ID
            
        If config is provided, it will be used as-is.
        If config is not provided, it will be loaded from environment variables.
        If any of private_key, api_url, or chain_id are provided, they will override
        the corresponding values in the config.
        """
        # Setup logging
        self.logger = logging.getLogger("reya_trading.client")
        
        # Get config from environment if not provided
        self._config = config or get_config()
        
        # Override config values if provided
        if private_key:
            self._config.private_key = private_key
        if api_url:
            self._config.api_url = api_url
        if chain_id:
            self._config.chain_id = chain_id
        if account_id:
            self._config.account_id = account_id
        if wallet_address:
            self._config.wallet_address = wallet_address
            
        # Create signature generator
        self._signature_generator = SignatureGenerator(self._config)
        
        # Initialize resources
        self._orders = OrdersResource(self._config, self._signature_generator)
        self._wallet = WalletResource(self._config, self._signature_generator)
    
    @property
    def orders(self) -> OrdersResource:
        """Get the orders resource."""
        return self._orders
    
    @property
    def wallet(self) -> WalletResource:
        """Get the wallet resource."""
        return self._wallet
    
    @property
    def config(self) -> TradingConfig:
        """Get the current configuration."""
        return self._config

    @property
    def wallet_address(self) -> Optional[str]:
        """Get the wallet address from config or signature generator."""
        # First check if wallet address is directly provided in config
        if self._config.wallet_address:
            return self._config.wallet_address
            
        # Otherwise derive it from private key if available
        return self._signature_generator._public_address if self._signature_generator else None
    
    async def create_limit_order(
        self,
        market_id: int,
        is_buy: bool,
        price: Union[float, str],
        size: Union[float, str],
        order_type: LimitOrderType,
        reduce_only: Optional[bool] = False,
        expires_after: Optional[int] = None
    ) -> CreateOrderResponse:
        """
        Create a limit (GTC) order asynchronously.
        
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
        """
        response = await self.orders.create_limit_order(
            market_id=market_id,
            is_buy=is_buy,
            price=price,
            size=size,
            order_type=order_type,
            reduce_only=reduce_only,
            expires_after=expires_after
        )
        
        return response
    
    async def create_take_profit_order(
        self,
        market_id: int,
        is_buy: bool,
        trigger_price: Union[float, str],
        price: Union[float, str],
    ) -> CreateOrderResponse:
        """
        Create a take profit order asynchronously.
        
        Args:
            market_id: The market ID for this order
            is_buy: Whether this is a buy order
            trigger_price: Price at which the order triggers
            price: Limit price for the order
            
        Returns:
            API response for the order creation
        """

        response = await self.orders.create_trigger_order(
            market_id=market_id,
            is_buy=is_buy,
            trigger_price=trigger_price,
            price=price,
            trigger_type=TpslType.TP
        )
        
        return response
    
    async def create_stop_loss_order(
        self,
        market_id: int,
        is_buy: bool,
        trigger_price: Union[float, str],
        price: Union[float, str],
    ) -> CreateOrderResponse:
        """
        Create a stop loss order asynchronously.
        
        Args:
            market_id: The market ID for this order
            is_buy: Whether this is a buy order
            trigger_price: Price at which the order triggers
            price: Limit price for the order
            
        Returns:
            API response for the order creation
        """

        response = await self.orders.create_trigger_order(
            market_id=market_id,
            is_buy=is_buy,
            trigger_price=trigger_price,
            price=price,
            trigger_type=TpslType.SL
        )
        
        return response
    
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
        response = await self.orders.cancel_order(order_id)
        return response

    async def get_positions(self, wallet_address: Optional[str] = None) -> Dict[str, Any]:
        """
        Get positions for a wallet address asynchronously.
        
        Args:
            wallet_address: Optional wallet address (defaults to current wallet)
            
        Returns:
            Positions data
            
        Raises:
            ValueError: If no wallet address is available or API returns an error
        """
        wallet = wallet_address or self.wallet_address
        if not wallet:
            raise ValueError("No wallet address available. Private key must be provided.")
            
        return await self.wallet.get_positions(wallet_address=wallet)
    
    async def get_open_orders(self) -> Dict[str, Any]:
        """
        Get open orders for the authenticated wallet asynchronously.
        
        Returns:
            List of open orders
            
        Raises:
            ValueError: If no wallet address is available or API returns an error
        """
        wallet = self.wallet_address
        if not wallet:
            raise ValueError("No wallet address available. Private key must be provided.")
            
        return await self.wallet.get_open_orders(wallet_address=wallet)
    
    async def get_balances(self) -> Dict[str, Any]:
        """
        Get account balance asynchronously.
        
        Returns:
            Account balance information
            
        Raises:
            ValueError: If no wallet address is available or API returns an error
        """
        wallet = self.wallet_address
        if not wallet:
            raise ValueError("No wallet address available. Private key must be provided.")
            
        return await self.wallet.get_balances(wallet_address=wallet)
    
    async def get_configuration(self) -> Dict[str, Any]:
        """
        Get account configuration asynchronously.
        
        Returns:
            Account configuration information
            
        Raises:
            ValueError: If no wallet address is available or API returns an error
        """
        wallet = self.wallet_address
        if not wallet:
            raise ValueError("No wallet address available. Private key must be provided.")
            
        return await self.wallet.get_configuration(wallet_address=wallet)
    
    async def get_orders(self) -> Dict[str, Any]:
        """
        Get filled orders for the authenticated wallet asynchronously.
        
        Returns:
            Dictionary containing orders data and metadata
            
        Raises:
            ValueError: If no wallet address is available or API returns an error
        """
        wallet = self.wallet_address
        if not wallet:
            raise ValueError("No wallet address available. Private key must be provided.")
            
        return await self.wallet.get_orders(wallet_address=wallet)
