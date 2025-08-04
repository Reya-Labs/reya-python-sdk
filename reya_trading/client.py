"""
Reya Trading Client - Main entry point for the Reya Trading API.

This module provides a client for interacting with the Reya Trading REST API.
"""
import logging
from typing import Dict, Any, Optional, Union, List

from .config import TradingConfig, get_config
from .auth.signatures import SignatureGenerator
from .resources.orders import OrdersResource
from .resources.wallet import WalletResource
from .constants.enums import UnifiedOrderType, LimitOrderType, TriggerOrderType


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
    
    def create_market_order(
        self,
        market_id: int,
        size: Union[float, str],
        price: Union[float, str],
        account_id: Optional[int] = None,
        reduce_only: bool = False
    ) -> Dict[str, Any]:
        """
        Create a market (IOC) order.
        
        Args:
            market_id: The market ID for this order
            size: Order size (positive for buy, negative for sell)
            price: Limit price for the order
            account_id: Optional account ID (defaults to config.account_id)
            reduce_only: Whether this is a reduce-only order
            
        Returns:
            API response for the order creation
            
        Raises:
            ValueError: If no account ID is available or API returns an error
        """
        account_id_to_use = account_id or self._config.account_id
        if account_id_to_use is None:
            raise ValueError("account_id must be provided or set in the config")
            
        response = self.orders.create_market_order(
            account_id=account_id_to_use,
            market_id=market_id,
            size=size,
            price=price,
            reduce_only=reduce_only
        )
        
        return response
    
    def create_limit_order(
        self,
        market_id: int,
        is_buy: bool,
        price: Union[float, str],
        size: Union[float, str],
        type: UnifiedOrderType
    ) -> Dict[str, Any]:
        """
        Create a limit (GTC) order.
        
        Args:
            market_id: The market ID for this order
            is_buy: Whether this is a buy order
            price: Limit price for the order
            size: Order size (positive for buy, negative for sell)
            reduce_only: Whether this is a reduce-only order
            type: The type of order (defaults to LimitOrderType)
            
        Returns:
            API response for the order creation
            
        Raises:
            ValueError: If no account ID is available or API returns an error
        """
        
        response = self.orders.create_limit_order(
            market_id=market_id,
            is_buy=is_buy,
            price=price,
            size=size,
            type=type
        )
        
        return response
    
    def create_take_profit_order(
        self,
        market_id: int,
        trigger_price: Union[float, str],
        price: Union[float, str],
        is_buy: bool,
        account_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Create a take profit order.
        
        Args:
            market_id: The market ID for this order
            trigger_price: Price at which the order triggers
            price: Limit price for the order
            is_buy: Whether this is a buy order
            account_id: Optional account ID (defaults to config.account_id)
            
        Returns:
            API response for the order creation
            
        Raises:
            ValueError: If no account ID is available or API returns an error
        """
        account_id_to_use = account_id or self._config.account_id
        if account_id_to_use is None:
            raise ValueError("account_id must be provided or set in the config")
            
        response = self.orders.create_trigger_order(
            account_id=account_id_to_use,
            market_id=market_id,
            trigger_price=trigger_price,
            price=price,
            is_buy=is_buy,
            trigger_type="TP"
        )
        
        return response
    
    def create_stop_loss_order(
        self,
        market_id: int,
        trigger_price: Union[float, str],
        price: Union[float, str],
        is_buy: bool,
        account_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Create a stop loss order.
        
        Args:
            market_id: The market ID for this order
            trigger_price: Price at which the order triggers
            price: Limit price for the order
            is_buy: Whether this is a buy order
            account_id: Optional account ID (defaults to config.account_id)
            
        Returns:
            API response for the order creation
            
        Raises:
            ValueError: If no account ID is available or API returns an error
        """
        account_id_to_use = account_id or self._config.account_id
        if account_id_to_use is None:
            raise ValueError("account_id must be provided or set in the config")
            
        response = self.orders.create_trigger_order(
            account_id=account_id_to_use,
            market_id=market_id,
            trigger_price=trigger_price,
            price=price,
            is_buy=is_buy,
            trigger_type="SL"
        )
        
        return response
    
    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """
        Cancel an existing order.
        
        Args:
            order_id: ID of the order to cancel
            
        Returns:
            API response for the order cancellation
            
        Raises:
            ValueError: If the API returns an error
        """
        response = self.orders.cancel_order(order_id)
        return response

    def get_positions(self, wallet_address: Optional[str] = None) -> Dict[str, Any]:
        """
        Get positions for a wallet address.
        
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
            
        return self.wallet.get_positions(wallet_address=wallet)
    
    def get_conditional_orders(self) -> List[Dict[str, Any]]:
        """
        Get conditional orders (limit, stop loss, take profit) for the authenticated wallet.
        
        Returns:
            List of conditional orders
            
        Raises:
            ValueError: If no wallet address is available or API returns an error
        """
        wallet = self.wallet_address
        if not wallet:
            raise ValueError("No wallet address available. Private key must be provided.")
            
        return self.wallet.get_conditional_orders(wallet_address=wallet)
    
    def get_balances(self) -> Dict[str, Any]:
        """
        Get account balance.
        
        Returns:
            Account balance information
            
        Raises:
            ValueError: If no wallet address is available or API returns an error
        """
        wallet = self.wallet_address
        if not wallet:
            raise ValueError("No wallet address available. Private key must be provided.")
            
        return self.wallet.get_balances(wallet_address=wallet)
    
    def get_configuration(self) -> Dict[str, Any]:
        """
        Get account configuration.
        
        Returns:
            Account configuration information
            
        Raises:
            ValueError: If no wallet address is available or API returns an error
        """
        wallet = self.wallet_address
        if not wallet:
            raise ValueError("No wallet address available. Private key must be provided.")
            
        return self.wallet.get_configuration(wallet_address=wallet)
    
    def get_orders(self) -> Dict[str, Any]:
        """
        Get filled orders for the authenticated wallet.
        
        Returns:
            Dictionary containing orders data and metadata
            
        Raises:
            ValueError: If no wallet address is available or API returns an error
        """
        wallet = self.wallet_address
        if not wallet:
            raise ValueError("No wallet address available. Private key must be provided.")
            
        return self.wallet.get_orders(wallet_address=wallet)
