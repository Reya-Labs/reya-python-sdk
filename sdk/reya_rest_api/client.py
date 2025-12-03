"""
Reya Trading Client - Main entry point for the Reya Trading API.

This module provides a client for interacting with the Reya Trading REST API.
"""

from typing import Optional

import logging
import time
import threading
from decimal import Decimal

from sdk._version import SDK_VERSION
from sdk.open_api.api.market_data_api import MarketDataApi
from sdk.open_api.api.order_entry_api import OrderEntryApi
from sdk.open_api.api.reference_data_api import ReferenceDataApi
from sdk.open_api.api.wallet_data_api import WalletDataApi
from sdk.open_api.api_client import ApiClient
from sdk.open_api.configuration import Configuration
from sdk.open_api.models.account import Account
from sdk.open_api.models.account_balance import AccountBalance
from sdk.open_api.models.cancel_order_request import CancelOrderRequest
from sdk.open_api.models.cancel_order_response import CancelOrderResponse
from sdk.open_api.models.create_order_request import CreateOrderRequest
from sdk.open_api.models.create_order_response import CreateOrderResponse
from sdk.async_api.depth import Depth
from sdk.open_api.models.mass_cancel_request import MassCancelRequest
from sdk.open_api.models.mass_cancel_response import MassCancelResponse
from sdk.open_api.models.market_definition import MarketDefinition
from sdk.open_api.models.order import Order
from sdk.open_api.models.order_type import OrderType
from sdk.open_api.models.perp_execution_list import PerpExecutionList
from sdk.open_api.models.position import Position
from sdk.open_api.models.spot_execution_list import SpotExecutionList
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.open_api.models.wallet_configuration import WalletConfiguration
from sdk.reya_rest_api.auth.signatures import SignatureGenerator
from sdk.reya_rest_api.config import TradingConfig, get_config
from sdk.reya_rest_api.constants.enums import OrdersGatewayOrderType

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
    
    # Class-level nonce tracking per wallet address (shared across all instances)
    _wallet_nonces: dict[str, int] = {}
    _wallet_nonce_lock = threading.Lock()

    def __init__(self):
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
        # Initialize symbol to market_id mapping
        self._symbol_to_market_id: dict[str, int] = {}
        self._initialized = False

        # Setup logging
        self.logger = logging.getLogger("reya_trading.client")

        # Get config from environment if not provided
        self._config = get_config()

        # Create signature generator
        self._signature_generator = SignatureGenerator(self._config)

        # Initialize resource manager
        api_config = Configuration(host=self._config.api_url)
        self.logger.info(f"API URL: {api_config.host}")
        self.logger.info(f"API base path: {api_config._base_path}")
        api_client = ApiClient(api_config)

        # Set custom SDK headers for all requests
        api_client.set_default_header("X-SDK-Version", f"reya-python-sdk/{SDK_VERSION}")
        api_client.set_default_header("User-Agent", f"reya-python-sdk/{SDK_VERSION}")

        # Verify ApiClient host configuration
        if hasattr(api_client, "configuration"):
            self.logger.info(f"ApiClient configuration host: {api_client.configuration.host}")
        else:
            self.logger.warning("ApiClient does not have configuration attribute")

        self._resources = ResourceManager(api_client)
        self._api_client = api_client

    async def start(self) -> None:
        await self._load_market_definitions()

    async def _load_market_definitions(self) -> None:
        """Load both perp and spot market definitions."""
        perp_count = 0
        spot_count = 0

        # Try to load perp market definitions (may fail if risk matrix data is missing)
        market_definitions: list[MarketDefinition] = await self.reference.get_market_definitions()
        self._symbol_to_market_id = {market.symbol: market.market_id for market in market_definitions}
        perp_count = len(market_definitions)
        self.logger.info(f"Loaded {perp_count} perp market definitions")

        # Load spot market definitions from /spotMarketDefinitions endpoint
        spot_market_definitions = await self.reference.get_spot_market_definitions()
        for market in spot_market_definitions:
            self._symbol_to_market_id[market.symbol] = market.market_id
        spot_count = len(spot_market_definitions)
        self.logger.info(f"Loaded {spot_count} spot market definitions from /spotMarketDefinitions")

        self._initialized = True
        total_markets = perp_count + spot_count
        self.logger.info(f"Loaded {total_markets} total market definitions ({perp_count} perp, {spot_count} spot)")

    def _is_spot_market(self, symbol: str) -> bool:
        """
        Determine if a symbol represents a spot market.

        Logic: If the symbol does NOT end with 'PERP', it's a spot market.
        Examples: ETHRUSD (spot), BTCRUSD (spot), ETHRUSDPERP (perp)
        """
        return not symbol.upper().endswith("PERP")

    def _get_next_nonce(self) -> int:
        """
        Generate a monotonically increasing nonce for spot market operations.
        
        Uses microsecond timestamp as base, but ensures the nonce is always
        greater than the last used nonce to prevent race conditions when
        multiple orders are created in quick succession.
        
        Nonces are tracked per-wallet at the class level, so multiple client
        instances sharing the same wallet will use the same nonce counter.
        
        Returns:
            A unique nonce guaranteed to be greater than any previously returned nonce.
        """
        wallet_address = self._config.owner_wallet_address.lower()
        
        with ReyaTradingClient._wallet_nonce_lock:
            current_time_nonce = int(time.time() * 1_000_000)
            last_nonce = ReyaTradingClient._wallet_nonces.get(wallet_address, 0)
            # Ensure nonce is always greater than the last used nonce
            new_nonce = max(current_time_nonce, last_nonce + 1)
            ReyaTradingClient._wallet_nonces[wallet_address] = new_nonce
            return new_nonce

    def _get_market_id_from_symbol(self, symbol: str) -> int:
        """Get market_id from symbol. Raises ValueError if symbol not found."""
        if not self._initialized:
            raise ValueError("Client not initialized. Call start() first.")

        market_id = self._symbol_to_market_id.get(symbol)
        if market_id is None:
            available_symbols = list(self._symbol_to_market_id.keys())
            raise ValueError(f"Unknown symbol '{symbol}'. Available symbols: {available_symbols}")

        is_spot = self._is_spot_market(symbol)
        self.logger.debug(f"Symbol '{symbol}' resolved to market_id {market_id} ({'spot' if is_spot else 'perp'})")

        return market_id

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
    def signer_wallet_address(self) -> str:
        """Get the signer wallet address (derived from private key)."""
        return self._signature_generator.signer_wallet_address

    @property
    def owner_wallet_address(self) -> str:
        """
        Get the owner wallet address for querying wallet data.

        Wallet that owns ACCOUNT_ID, the signer_wallet will either be the same as owner_wallet_address, or a wallet
        that was given permissions to trade on behalf ot he owner_wallet_address
        """
        return self._config.owner_wallet_address

    async def create_limit_order(self, params: LimitOrderParameters) -> CreateOrderResponse:
        """
        Create a limit (IOC/GTC) order asynchronously.

        Args:
            params: Limit order parameters

        Returns:
            API response for the order creation
        """

        # Resolve symbol to market_id
        market_id = self._get_market_id_from_symbol(params.symbol)

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

        # For spot markets, use monotonically increasing nonce (fits in uint64)
        # For perp markets, use 32-byte nonce
        if self._is_spot_market(params.symbol):
            nonce = self._get_next_nonce()
        else:
            nonce = self._signature_generator.create_orders_gateway_nonce(
                self.config.account_id, market_id, int(time.time_ns() / 1000000)
            )

        inputs = self._signature_generator.encode_inputs_limit_order(
            is_buy=params.is_buy,
            limit_px=Decimal(params.limit_px),
            qty=Decimal(params.qty),
        )

        # Determine deadline based on order type and market type
        if params.time_in_force != TimeInForce.IOC:
            # For GTC orders: use real timestamp for spot markets, 10^18 for perp markets
            if self._is_spot_market(params.symbol):
                deadline = int(time.time() * 1000) + DEFAULT_DEADLINE_MS
            else:
                deadline = CONDITIONAL_ORDER_DEADLINE
        elif params.expires_after is None:
            deadline = int(time.time() * 1000) + DEFAULT_DEADLINE_MS
        else:
            deadline = params.expires_after

        # For spot markets, ALWAYS use LIMIT_ORDER_SPOT (6) regardless of timeInForce
        # The blockchain only supports matching LimitOrderSpot against LimitOrderSpot for spot trades
        # TimeInForce behavior is encoded in the inputs field, not in the orderType
        if self._is_spot_market(params.symbol):
            order_type_int = OrdersGatewayOrderType.LIMIT_ORDER_SPOT
        else:
            # For perp markets, use orderType based on timeInForce
            order_type_int = (
                OrdersGatewayOrderType.LIMIT_ORDER
                if params.time_in_force == TimeInForce.GTC
                else (
                    OrdersGatewayOrderType.REDUCE_ONLY_MARKET_ORDER
                    if params.reduce_only is True
                    else OrdersGatewayOrderType.MARKET_ORDER
                )
            )

        # For spot markets, counterparty_account_ids should be empty []
        # Spot trades are matched against an orderbook, rather than directly against the pool.
        counterparty_ids = [] if self._is_spot_market(params.symbol) else [self.config.pool_account_id]

        signature = self._signature_generator.sign_raw_order(
            account_id=self.config.account_id,
            market_id=market_id,
            exchange_id=self.config.dex_id,
            counterparty_account_ids=counterparty_ids,
            order_type=order_type_int,
            inputs=inputs,
            deadline=deadline,
            nonce=nonce,
        )

        # Build the order request
        if self.config.account_id is None:
            raise ValueError("Account ID is required for order creation")

        # Only include expiresAfter for IOC orders and spot markets
        # GTC perp orders don't support expiresAfter
        is_ioc_or_spot = params.time_in_force == TimeInForce.IOC or self._is_spot_market(params.symbol)
        
        # reduceOnly is only supported for perp IOC orders
        is_perp_ioc = params.time_in_force == TimeInForce.IOC and not self._is_spot_market(params.symbol)
        
        order_request = CreateOrderRequest(
            accountId=self.config.account_id,
            symbol=params.symbol,
            exchangeId=self.config.dex_id,
            isBuy=params.is_buy,
            limitPx=params.limit_px,
            qty=params.qty,
            orderType=OrderType.LIMIT,
            timeInForce=params.time_in_force,
            expiresAfter=deadline if is_ioc_or_spot else None,
            reduceOnly=params.reduce_only if is_perp_ioc else None,
            signature=signature,
            nonce=str(nonce),
            signerWallet=self.signer_wallet_address,
            clientOrderId=params.client_order_id,
        )

        response = await self.orders.create_order(create_order_request=order_request)

        return response

    async def create_trigger_order(self, params: TriggerOrderParameters) -> CreateOrderResponse:
        """
        Create a stop loss order asynchronously.

        Args:
            params: Trigger order parameters

        Returns:
            API response for the order creation
        """

        # Resolve symbol to market_id

        if self._is_spot_market(params.symbol):
            raise ValueError("Trigger orders are not supported for spot markets")

        market_id = self._get_market_id_from_symbol(params.symbol)

        if self._signature_generator is None:
            raise ValueError("Private key is required for creating orders")
        if self._signature_generator is None:
            raise ValueError("Signature generator is required for order signing")
        if self.config.account_id is None:
            raise ValueError("Account ID is required for order signing")

        limit_px = Decimal(BUY_TRIGGER_ORDER_PRICE_LIMIT) if params.is_buy else Decimal(0)

        order_type_int = (
            OrdersGatewayOrderType.TAKE_PROFIT
            if params.trigger_type == OrderType.TP
            else OrdersGatewayOrderType.STOP_LOSS
        )

        nonce = self._signature_generator.create_orders_gateway_nonce(
            self.config.account_id, market_id, int(time.time_ns() / 1000000)
        )

        inputs = self._signature_generator.encode_inputs_trigger_order(
            is_buy=params.is_buy,
            trigger_px=Decimal(str(params.trigger_px)),
            limit_px=limit_px,
        )

        signature = self._signature_generator.sign_raw_order(
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

        order_request = CreateOrderRequest(
            accountId=self.config.account_id,
            symbol=params.symbol,
            exchangeId=self.config.dex_id,
            isBuy=params.is_buy,
            triggerPx=str(params.trigger_px),
            limitPx=str(limit_px),
            orderType=params.trigger_type,
            expiresAfter=None,
            signature=signature,
            nonce=str(nonce),
            signerWallet=self.signer_wallet_address,
        )

        response = await self.orders.create_order(create_order_request=order_request)

        return response

    async def cancel_order(
        self,
        order_id: Optional[str] = None,
        symbol: Optional[str] = None,
        account_id: Optional[int] = None,
        client_order_id: Optional[int] = None,
    ) -> CancelOrderResponse:
        """
        Cancel an existing order asynchronously.

        For spot markets, you must provide EITHER order_id OR client_order_id (not both).
        For perp markets, order_id is required.

        Args:
            order_id: ID of the order to cancel (required for perp, optional for spot if client_order_id provided)
            symbol: Trading symbol (required for spot market orders, e.g., ETHRUSD, BTCRUSD)
            account_id: Account ID (required for spot market orders)
            client_order_id: Client order ID (optional for spot, alternative to order_id)

        Returns:
            API response for the order cancellation

        Raises:
            ValueError: If symbol and account_id are not provided for spot orders
            ValueError: If neither order_id nor client_order_id is provided for spot orders
        """
        if self._signature_generator is None:
            raise ValueError("Private key is required for cancelling orders")

        # Determine if this is a spot market order
        is_spot_order = symbol and "RUSD" in symbol and "PERP" not in symbol

        # For spot markets, symbol and account_id are required
        if is_spot_order:
            if symbol is None:
                raise ValueError("symbol is required for spot market order cancellation")
            if account_id is None:
                raise ValueError(
                    f"account_id is required for spot market order cancellation (symbol: {symbol})"
                )
            # For spot markets: must provide at least one of order_id or client_order_id
            # If both are provided, the API will prefer order_id
            if not order_id and not client_order_id:
                raise ValueError(
                    "For spot orders, must provide either order_id or client_order_id"
                )
        else:
            # For perp markets, order_id is required
            if not order_id:
                raise ValueError("order_id is required for perp market order cancellation")

        if is_spot_order:
            # Get market_id from symbol
            market_id = self._get_market_id_from_symbol(symbol)

            # Generate monotonically increasing nonce
            nonce = self._get_next_nonce()

            # Generate deadline (current time + 5 seconds)
            deadline = int(time.time() * 1000) + DEFAULT_DEADLINE_MS

            # For EIP-712 signature, we need both orderId and clOrdId
            # If one is not provided, use 0 as placeholder
            order_id_int = int(order_id) if order_id else 0
            client_order_id_int = client_order_id if client_order_id is not None else 0

            # Generate EIP-712 signature for SPOT orders
            signature = self._signature_generator.sign_cancel_order_spot(
                account_id=account_id,
                market_id=market_id,
                order_id=order_id_int,
                client_order_id=client_order_id_int,
                nonce=nonce,
                deadline=deadline,
            )
        else:
            signature = self._signature_generator.sign_cancel_order_perps(order_id)
            nonce = None
            deadline = None

        cancel_order_request = CancelOrderRequest(
            orderId=order_id,
            clientOrderId=client_order_id,
            signature=signature,
            nonce=str(nonce) if nonce is not None else None,
            symbol=symbol,
            accountId=account_id,
            expiresAfter=deadline,
        )

        response = await self.orders.cancel_order(cancel_order_request)
        return response

    async def mass_cancel(
        self,
        symbol: str,
        account_id: Optional[int] = None,
    ) -> MassCancelResponse:
        """
        Cancel all orders for a specific market asynchronously.

        This operation is only supported for SPOT markets.

        Args:
            symbol: Trading symbol (e.g., ETHRUSD, BTCRUSD)
            account_id: Account ID (optional, defaults to config account_id)

        Returns:
            API response for the mass cancellation

        Raises:
            ValueError: If symbol is not a spot market or account_id is missing
        """
        if self._signature_generator is None:
            raise ValueError("Private key is required for mass cancel")

        # Verify this is a spot market
        if not self._is_spot_market(symbol):
            raise ValueError(
                f"Mass cancel is only supported for spot markets. "
                f"Symbol '{symbol}' appears to be a perp market."
            )

        # Use config account_id if not provided
        if account_id is None:
            account_id = self.config.account_id
            if account_id is None:
                raise ValueError("account_id is required for mass cancel")

        # Get market_id from symbol
        market_id = self._get_market_id_from_symbol(symbol)

        # Generate monotonically increasing nonce
        nonce = self._get_next_nonce()

        # Generate deadline (current time + 5 seconds)
        deadline = int(time.time() * 1000) + DEFAULT_DEADLINE_MS

        # Generate EIP-712 signature for mass cancel
        signature = self._signature_generator.sign_mass_cancel(
            account_id=account_id,
            market_id=market_id,
            nonce=nonce,
            deadline=deadline,
        )

        mass_cancel_request = MassCancelRequest(
            accountId=account_id,
            symbol=symbol,
            signature=signature,
            nonce=str(nonce),
            expiresAfter=deadline,
        )

        response = await self.orders.cancel_all(mass_cancel_request)
        return response

    async def get_positions(self, wallet_address: Optional[str] = None) -> list[Position]:
        """
        Get positions for a wallet address asynchronously.

        Args:
            wallet_address: Optional wallet address (defaults to owner_wallet_address)

        Returns:
            Positions data

        Raises:
            ValueError: If no wallet address is available or API returns an error
        """
        wallet = wallet_address or self.owner_wallet_address
        if not wallet:
            raise ValueError("No wallet address available. Private key must be provided.")

        return await self.wallet.get_wallet_positions(address=wallet)

    async def get_open_orders(self) -> list[Order]:
        """
        Get open orders for the owner wallet asynchronously.

        Returns:
            List of open orders

        Raises:
            ValueError: If no wallet address is available or API returns an error
        """
        wallet = self.owner_wallet_address
        if not wallet:
            raise ValueError("No wallet address available. Private key must be provided.")

        return await self.wallet.get_wallet_open_orders(address=wallet)

    async def get_configuration(self) -> WalletConfiguration:
        """
        Get account configuration for the owner wallet asynchronously.

        Returns:
            Account configuration information

        Raises:
            ValueError: If no wallet address is available or API returns an error
        """
        wallet = self.owner_wallet_address
        if not wallet:
            raise ValueError("No wallet address available. Private key must be provided.")

        return await self.wallet.get_wallet_configuration(address=wallet)

    async def get_perp_executions(self) -> PerpExecutionList:
        """
        Get perp executions for the owner wallet asynchronously.

        Returns:
            Dictionary containing trades data and metadata

        Raises:
            ValueError: If no wallet address is available or API returns an error
        """
        wallet = self.owner_wallet_address
        if not wallet:
            raise ValueError("No wallet address available. Private key must be provided.")

        return await self.wallet.get_wallet_perp_executions(address=wallet)

    async def get_accounts(self) -> list[Account]:
        """
        Get accounts for the owner wallet asynchronously.

        Returns:
            Account information

        Raises:
            ValueError: If no wallet address is available or API returns an error
        """
        wallet = self.owner_wallet_address
        if not wallet:
            raise ValueError("No wallet address available. Private key must be provided.")

        return await self.wallet.get_wallet_accounts(address=wallet)

    async def get_account_balances(self) -> list[AccountBalance]:
        """
        Get account balances for the owner wallet asynchronously.

        Returns:
            Account balances

        Raises:
            ValueError: If no wallet address is available or API returns an error
        """
        wallet = self.owner_wallet_address
        if not wallet:
            raise ValueError("No wallet address available. Private key must be provided.")

        return await self.wallet.get_wallet_account_balances(address=wallet)

    async def get_spot_executions(self) -> SpotExecutionList:
        """
        Get spot executions (i.e. auto exchanges) for the owner wallet asynchronously.

        Returns:
            Spot executions

        Raises:
            ValueError: If no wallet address is available or API returns an error
        """
        wallet = self.owner_wallet_address
        if not wallet:
            raise ValueError("No wallet address available. Private key must be provided.")

        return await self.wallet.get_wallet_spot_executions(address=wallet)

    async def get_market_depth(self, symbol: str) -> Depth:
        """
        Get L2 market depth (orderbook) for a given symbol.

        Args:
            symbol: Market symbol (e.g., 'WETHRUSD', 'BTCRUSD')

        Returns:
            Depth: Market depth with bids and asks (typed from spec)

        Raises:
            ValueError: If symbol is invalid or API returns an error
        """
        # Direct HTTP request to depth endpoint (not in generated API yet)
        import aiohttp
        url = f"{self._config.api_url}/market/{symbol}/depth"
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status != 200:
                    raise ValueError(f"Failed to get market depth: {response.status}")
                data = await response.json()
                return Depth.model_validate(data)

    async def get_market_spot_executions(self, symbol: str) -> SpotExecutionList:
        """
        Get spot executions for a specific market.

        Args:
            symbol: Market symbol (e.g., 'WETHRUSD', 'BTCRUSD')

        Returns:
            SpotExecutionList: List of spot executions for the market

        Raises:
            ValueError: If symbol is invalid or API returns an error
        """
        # Direct HTTP request to market spot executions endpoint
        import aiohttp
        url = f"{self._config.api_url}/market/{symbol}/spotExecutions"
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status != 200:
                    raise ValueError(f"Failed to get market spot executions: {response.status}")
                data = await response.json()
                return SpotExecutionList.from_dict(data)

    async def close(self) -> None:
        """
        Close the underlying HTTP client session.

        This should be called when the client is no longer needed to properly
        cleanup HTTP connections and avoid resource leaks.
        """
        if hasattr(self._api_client, "rest_client") and self._api_client.rest_client:
            await self._api_client.rest_client.close()

    async def __aenter__(self):
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit - automatically closes the client session."""
        await self.close()
