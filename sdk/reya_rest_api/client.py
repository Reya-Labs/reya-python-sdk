"""
Reya Trading Client - Main entry point for the Reya Trading API.

This module provides a client for interacting with the Reya Trading REST API.
"""

import time
from typing import List, Optional

import logging
from decimal import Decimal

from reya_v2_api.api.market_data_api import MarketDataApi
from reya_v2_api.api.reference_data_api import ReferenceDataApi
from reya_v2_api.api.wallet_data_api import WalletDataApi
from reya_v2_api.configuration import Configuration
from reya_v2_api.models.account import Account
from reya_v2_api.models.cancel_order_request import CancelOrderRequest
from reya_v2_api.models.cancel_order_response import CancelOrderResponse
from reya_v2_api.models.create_order_response import CreateOrderResponse
from reya_v2_api.models.create_order_request import CreateOrderRequest
from reya_v2_api.models.order_type import OrderType
from reya_v2_api.models.perp_execution_list import PerpExecutionList
from reya_v2_api.models.spot_execution_list import SpotExecutionList
from reya_v2_api.models.time_in_force import TimeInForce
from reya_v2_api.models.wallet_configuration import WalletConfiguration
from reya_v2_api.models.account_balance import AccountBalance
from reya_v2_api.models.order import Order
from reya_v2_api.models.position import Position
from reya_v2_api.api_client import ApiClient
from reya_v2_api.api.order_entry_api import OrderEntryApi

from sdk.reya_rest_api.auth.signatures import SignatureGenerator
from sdk.reya_rest_api.config import TradingConfig, get_config
from sdk.reya_rest_api.constants.enums import OrdersGatewayOrderType
from sdk.reya_websocket.socket import logger
from .models.orders import LimitOrderParameters, TriggerOrderParameters

CONDITIONAL_ORDER_DEADLINE = 10**18
DEFAULT_DEADLINE_MS = 5000
BUY_TRIGGER_ORDER_PRICE_LIMIT = 100000000000000000000


class ResourceManager:
    """Manages all API resources."""

    def __init__(self, api_client: ApiClient):
        self.orders = OrderEntryApi(api_client)
        self.wallet = WalletDataApi(api_client)
        self.markets = MarketDataApi(api_client)
        self.reference = ReferenceDataApi(api_client)


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
        api_config = Configuration(host=self._config.api_url)
        self.logger.info(f"API URL: {api_config.host}")
        self.logger.info(f"API base path: {api_config._base_path}")
        api_client = ApiClient(api_config)
        
        # Verify ApiClient host configuration
        if hasattr(api_client, 'configuration'):
            self.logger.info(f"ApiClient configuration host: {api_client.configuration.host}")
        else:
            self.logger.warning("ApiClient does not have configuration attribute")
            
        self._resources = ResourceManager(api_client)

    @property
    def orders(self) -> OrderEntryApi:
        """Get the orders resource."""
        return self._resources.orders

    @property
    def wallet(self) -> WalletDataApi:
        """Get the wallet resource."""
        return self._resources.wallet

    @property
    def markets(self) -> MarketDataApi:
        """Get the markets resource."""
        return self._resources.markets

    @property
    def reference(self) -> ReferenceDataApi:
        """Get the reference data resource."""
        return self._resources.reference

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

    def create_limit_order(
        self,
        params: LimitOrderParameters
    ) -> CreateOrderResponse:
        """
        Create a limit (GTC) order synchronously.

        Args:
            params: Limit order parameters

        Returns:
            API response for the order creation
        """

        if self._signature_generator is None:
            raise ValueError("Private key is required for creating orders")

        if params.expires_after is not None and params.time_in_force != TimeInForce.IOC:
            raise ValueError("Parameter expires_after is only allowed for IOC orders")

        if params.time_in_force == TimeInForce.GTC and params.reduce_only is True:
            raise ValueError("Unexpected True value for parameter reduce_only for GTC orders")

        # Prepare signature data
        if self._signature_generator is None:
            raise ValueError("Signature generator is required for order signing")
        if self.config.account_id is None:
            raise ValueError("Account ID is required for order signing")

        nonce = self._signature_generator.create_orders_gateway_nonce(
            self.config.account_id, params.market_id, int(time.time_ns() / 1000000)
        )

        inputs = self._signature_generator.encode_inputs_limit_order(is_buy=params.is_buy, limit_price=params.price, qty=params.qty)

        if params.time_in_force != TimeInForce.IOC:
            deadline = CONDITIONAL_ORDER_DEADLINE
        elif params.expires_after is None:
            deadline = int(time.time() * 1000) + DEFAULT_DEADLINE_MS
        else:
            deadline = params.expires_after

        order_type_int = (
            OrdersGatewayOrderType.LIMIT_ORDER if params.time_in_force == TimeInForce.GTC else OrdersGatewayOrderType.MARKET_ORDER
        )

        signature = self._signature_generator.sign_raw_order(
            account_id=self.config.account_id,
            market_id=params.market_id,
            exchange_id=self.config.dex_id,
            counterparty_account_ids=[self.config.pool_account_id],
            order_type=order_type_int,
            inputs=inputs,
            deadline=deadline,
            nonce=nonce,
        )

        # Build the order request
        if self.config.account_id is None:
            raise ValueError("Account ID is required for order creation")
        if self.config.wallet_address is None:
            raise ValueError("Wallet address is required for order creation")
        
        

        order_request = CreateOrderRequest(
            account_id=self.config.account_id,
            symbol=params.symbol,
            exchange_id=self.config.dex_id,
            is_buy=params.is_buy,
            limit_px=params.price,
            qty=params.qty,
            order_type=OrderType.LIMIT,
            time_in_force=params.time_in_force,
            expires_after=deadline if params.time_in_force == TimeInForce.IOC else None,
            reduce_only=params.reduce_only,
            signature=signature,
            nonce=str(nonce),
            signer_wallet=self.config.wallet_address,
        )

        response = self.orders.create_order(create_order_request=order_request)

        return response


    def create_trigger_order(
        self,
        params: TriggerOrderParameters
    ) -> CreateOrderResponse:
        """
        Create a stop loss order synchronously.

        Args:
            params: Trigger order parameters

        Returns:
            API response for the order creation
        """
        if self._signature_generator is None:
            raise ValueError("Private key is required for creating orders")
        if self._signature_generator is None:
            raise ValueError("Signature generator is required for order signing")
        if self.config.account_id is None:
            raise ValueError("Account ID is required for order signing")

        limit_price = Decimal(BUY_TRIGGER_ORDER_PRICE_LIMIT) if params.is_buy else Decimal(0)

        order_type_int = (
            OrdersGatewayOrderType.TAKE_PROFIT if params.trigger_type == TpslType.TP else OrdersGatewayOrderType.STOP_LOSS
        )

        nonce = self._signature_generator.create_orders_gateway_nonce(
            self.config.account_id, params.market_id, int(time.time_ns() / 1000000)
        )

        inputs = self._signature_generator.encode_inputs_trigger_order(
            is_buy=params.is_buy, trigger_price=Decimal(str(params.trigger_price)), limit_price=limit_price
        )

        signature = self._signature_generator.sign_raw_order(
            account_id=self.config.account_id,
            market_id=params.market_id,
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

        order_request = CreateOrderRequest(
            account_id=self.config.account_id,
            market_id=params.market_id,
            exchange_id=self.config.dex_id,
            is_buy=params.is_buy,
            limit_px=limit_price,
            qty=Decimal("0"),
            order_type=OrderType.TP,
            expires_after=None,
            reduce_only=False,
            signature=signature,
            nonce=nonce,
            signer_wallet=self.config.wallet_address,
        )

        response = self.orders.create_order(create_order_request=order_request)

        return response

    def cancel_order(self, order_id: str) -> CancelOrderResponse:
        """
        Cancel an existing order synchronously.

        Args:
            order_id: ID of the order to cancel

        Returns:
            API response for the order cancellation

        Raises:
            ValueError: If the API returns an error
        """
        if self._signature_generator is None:
            raise ValueError("Private key is required for cancelling orders")

        # Sign the cancellation request
        signature = self._signature_generator.sign_cancel_order(order_id)

        cancel_order_request = CancelOrderRequest(order_id=order_id,signature=signature)

        response = self.orders.cancel_order(cancel_order_request)
        return response

    def get_positions(self, wallet_address: Optional[str] = None) -> List[Position]:
        """
        Get positions for a wallet address synchronously.

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

        return self.wallet.get_wallet_positions(address=wallet)

    def get_open_orders(self) -> List[Order]:
        """
        Get open orders for the authenticated wallet synchronously.

        Returns:
            List of open orders

        Raises:
            ValueError: If no wallet address is available or API returns an error
        """
        wallet = self.wallet_address
        if not wallet:
            raise ValueError("No wallet address available. Private key must be provided.")

        return self.wallet.get_wallet_open_orders(address=wallet)

    def get_balances(self) -> List[AccountBalance]:
        """
        Get account balance synchronously.

        Returns:
            Account balance information

        Raises:
            ValueError: If no wallet address is available or API returns an error
        """
        wallet = self.wallet_address
        if not wallet:
            raise ValueError("No wallet address available. Private key must be provided.")

        return self.wallet.get_wallet_account_balances(address=wallet)

    def get_configuration(self) -> WalletConfiguration:
        """
        Get account configuration synchronously.

        Returns:
            Account configuration information

        Raises:
            ValueError: If no wallet address is available or API returns an error
        """
        wallet = self.wallet_address
        if not wallet:
            raise ValueError("No wallet address available. Private key must be provided.")

        return self.wallet.get_wallet_configuration(address=wallet)

    def get_perp_executions(self) -> PerpExecutionList:
        """
        Get perp executions for the authenticated wallet synchronously.

        Returns:
            Dictionary containing trades data and metadata

        Raises:
            ValueError: If no wallet address is available or API returns an error
        """
        wallet = self.wallet_address
        if not wallet:
            raise ValueError("No wallet address available. Private key must be provided.")

        return self.wallet.get_wallet_perp_executions(address=wallet)

    def get_accounts(self) -> List[Account]:
        """
        Get accounts for the authenticated wallet synchronously.

        Returns:
            Account information

        Raises:
            ValueError: If no wallet address is available or API returns an error
        """
        wallet = self.wallet_address
        if not wallet:
            raise ValueError("No wallet address available. Private key must be provided.")

        return self.wallet.get_wallet_accounts(address=wallet)

    def get_spot_executions(self) -> SpotExecutionList:
        """
        Get spot executions (i.e. auto exchanges) for the authenticated wallet synchronously.

        Returns:
            Spot executions

        Raises:
            ValueError: If no wallet address is available or API returns an error
        """
        wallet = self.wallet_address
        if not wallet:
            raise ValueError("No wallet address available. Private key must be provided.")

        return self.wallet.get_wallet_spot_executions(address=wallet)