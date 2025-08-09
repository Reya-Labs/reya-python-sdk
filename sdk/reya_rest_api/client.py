"""
Reya Trading Client - Main entry point for the Reya Trading API.

This module provides a client for interacting with the Reya Trading REST API.
"""

from typing import Any, Optional

import logging
from decimal import Decimal

from sdk.reya_rest_api.auth.signatures import SignatureGenerator
from sdk.reya_rest_api.config import TradingConfig, get_config
from sdk.reya_rest_api.constants.enums import LimitOrderType, TpslType
from sdk.reya_rest_api.models.orders import CancelOrderResponse, CreateOrderResponse
from sdk.reya_rest_api.resources.assets import AssetsResource
from sdk.reya_rest_api.resources.markets import MarketsResource
from sdk.reya_rest_api.resources.orders import OrdersResource
from sdk.reya_rest_api.resources.prices import PricesResource
from sdk.reya_rest_api.resources.wallet import WalletResource


class ResourceManager:
    """Manages all API resources."""

    def __init__(self, config: TradingConfig, signature_generator: SignatureGenerator):
        self.orders = OrdersResource(config, signature_generator)
        self.wallet = WalletResource(config, signature_generator)
        self.markets = MarketsResource(config, signature_generator)
        self.assets = AssetsResource(config, signature_generator)
        self.prices = PricesResource(config, signature_generator)


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
        wallet_address: Optional[str] = None,
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

        # Initialize resource manager
        self._resources = ResourceManager(self._config, self._signature_generator)

    @property
    def orders(self) -> OrdersResource:
        """Get the orders resource."""
        return self._resources.orders

    @property
    def wallet(self) -> WalletResource:
        """Get the wallet resource."""
        return self._resources.wallet

    @property
    def markets(self) -> MarketsResource:
        """Get the markets resource."""
        return self._resources.markets

    @property
    def assets(self) -> AssetsResource:
        """Get the assets resource."""
        return self._resources.assets

    @property
    def prices(self) -> PricesResource:
        """Get the prices resource."""
        return self._resources.prices

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
        return self._signature_generator.public_address if self._signature_generator else None

    def validate_inputs(self, **kwargs) -> None:
        """
        Validates that all provided keyword arguments are strings.

        Args:
            **kwargs: Key-value pairs to validate (e.g. price="123.45", size="10")

        Raises:
            ValueError: If any value is not a string.
        """
        for name, value in kwargs.items():
            if not isinstance(value, str):
                raise ValueError(f"{name} must be a string, got {type(value).__name__}")

    async def create_limit_order(
        self,
        market_id: int,
        is_buy: bool,
        price: str,
        size: str,
        order_type: LimitOrderType,
        reduce_only: Optional[bool] = False,
        expires_after: Optional[int] = None,
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

        self.validate_inputs(price=price, size=size)
        response = await self.orders.create_limit_order(
            market_id=market_id,
            is_buy=is_buy,
            price=Decimal(price),
            size=Decimal(size),
            order_type=order_type,
            reduce_only=reduce_only or False,
            expires_after=expires_after,
        )

        return response

    async def create_take_profit_order(
        self,
        market_id: int,
        is_buy: bool,
        trigger_price: str,
    ) -> CreateOrderResponse:
        """
        Create a take profit order asynchronously.

        Args:
            market_id: The market ID for this order
            is_buy: Whether this is a buy order
            trigger_price: Price at which the order triggers

        Returns:
            API response for the order creation
        """

        self.validate_inputs(trigger_price=trigger_price)
        response = await self.orders.create_trigger_order(
            market_id=market_id, is_buy=is_buy, trigger_price=Decimal(trigger_price), trigger_type=TpslType.TP
        )

        return response

    async def create_stop_loss_order(
        self,
        market_id: int,
        is_buy: bool,
        trigger_price: str,
    ) -> CreateOrderResponse:
        """
        Create a stop loss order asynchronously.

        Args:
            market_id: The market ID for this order
            is_buy: Whether this is a buy order
            trigger_price: Price at which the order triggers

        Returns:
            API response for the order creation
        """

        self.validate_inputs(trigger_price=trigger_price)
        response = await self.orders.create_trigger_order(
            market_id=market_id, is_buy=is_buy, trigger_price=Decimal(trigger_price), trigger_type=TpslType.SL
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

    async def get_positions(self, wallet_address: Optional[str] = None) -> list[dict[str, Any]]:
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

    async def get_open_orders(self) -> list[dict[str, Any]]:
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

    async def get_balances(self) -> list[dict[str, Any]]:
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

    async def get_configuration(self) -> list[dict[str, Any]]:
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

    async def get_trades(self) -> list[dict[str, Any]]:
        """
        Get trades for the authenticated wallet asynchronously.

        Returns:
            Dictionary containing trades data and metadata

        Raises:
            ValueError: If no wallet address is available or API returns an error
        """
        wallet = self.wallet_address
        if not wallet:
            raise ValueError("No wallet address available. Private key must be provided.")

        return await self.wallet.get_trades(wallet_address=wallet)

    async def get_accounts(self) -> list[dict[str, Any]]:
        """
        Get accounts for the authenticated wallet asynchronously.

        Returns:
            Account information

        Raises:
            ValueError: If no wallet address is available or API returns an error
        """
        wallet = self.wallet_address
        if not wallet:
            raise ValueError("No wallet address available. Private key must be provided.")

        return await self.wallet.get_accounts(wallet_address=wallet)

    async def get_leverages(self) -> list[dict[str, Any]]:
        """
        Get leverages for the authenticated wallet asynchronously.

        Returns:
            Leverage information

        Raises:
            ValueError: If no wallet address is available or API returns an error
        """
        wallet = self.wallet_address
        if not wallet:
            raise ValueError("No wallet address available. Private key must be provided.")

        return await self.wallet.get_leverages(wallet_address=wallet)

    async def get_auto_exchange(self) -> list[dict[str, Any]]:
        """
        Get auto exchange settings for the authenticated wallet asynchronously.

        Returns:
            Auto exchange settings

        Raises:
            ValueError: If no wallet address is available or API returns an error
        """
        wallet = self.wallet_address
        if not wallet:
            raise ValueError("No wallet address available. Private key must be provided.")

        return await self.wallet.get_auto_exchange(wallet_address=wallet)

    async def get_stats(self) -> list[dict[str, Any]]:
        """
        Get stats for the authenticated wallet asynchronously.

        Returns:
            Wallet stats information

        Raises:
            ValueError: If no wallet address is available or API returns an error
        """
        wallet = self.wallet_address
        if not wallet:
            raise ValueError("No wallet address available. Private key must be provided.")

        return await self.wallet.get_stats(wallet_address=wallet)
