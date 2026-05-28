"""
Reya Trading Client - Main entry point for the Reya Trading API.

This module provides a client for interacting with the Reya Trading REST API.
"""

from typing import Optional

import logging
import threading
import time
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
from sdk.open_api.models.market_definition import MarketDefinition
from sdk.open_api.models.mass_cancel_request import MassCancelRequest
from sdk.open_api.models.mass_cancel_response import MassCancelResponse
from sdk.open_api.models.order import Order
from sdk.open_api.models.order_type import OrderType
from sdk.open_api.models.perp_execution_list import PerpExecutionList
from sdk.open_api.models.position import Position
from sdk.open_api.models.spot_execution_bust_list import SpotExecutionBustList
from sdk.open_api.models.spot_execution_list import SpotExecutionList
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.open_api.models.wallet_configuration import WalletConfiguration
from sdk.reya_rest_api.auth.signatures import SignatureGenerator
from sdk.reya_rest_api.config import TradingConfig, get_config
from sdk.reya_rest_api.constants.enums import OrdersGatewayOrderType

from .models.orders import LimitOrderParameters, TriggerOrderParameters

CONDITIONAL_ORDER_DEADLINE = 10**18
DEFAULT_DEADLINE_S = 10  # Default deadline for IOC orders and cancel operations
GTC_DEADLINE_S = 86400  # 24 hours for GTC spot orders
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

    def __init__(self, config: Optional[TradingConfig] = None):
        """
        Initialize the Reya Trading client.

        Args:
            config: Optional trading configuration object. If provided, it will be used
                    directly. If not provided, config will be loaded from environment
                    variables using get_config().
        """
        # Initialize symbol to market_id mapping
        self._symbol_to_market_id: dict[str, int] = {}
        self._initialized = False

        # Setup logging
        self.logger = logging.getLogger("reya_trading.client")

        # Use provided config or load from environment
        self._config = config if config is not None else get_config()

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

    def get_market_id_from_symbol(self, symbol: str) -> int:
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
    def signature_generator(self) -> SignatureGenerator:
        """Get the signature generator for creating order signatures."""
        return self._signature_generator

    def get_next_nonce(self) -> int:
        """Get the next nonce for order signing.

        Returns:
            A unique nonce guaranteed to be greater than any previously returned nonce.
        """
        return self._get_next_nonce()

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

    def build_create_limit_order_payload(self, params: LimitOrderParameters) -> tuple[dict, int]:
        """Build the wire-shape payload (camelCase, JSON-ready) for a createOrder
        limit-order request, and return ``(payload, nonce)``.

        Pure (no I/O). The same payload shape is consumed by both the REST
        ``OrderEntryApi`` and the ws-exec WebSocket transport — the generated
        OpenAPI ``CreateOrderRequest`` and AsyncAPI ``CreateOrderRequest``
        models share field names, so the dict round-trips through either.
        """
        if self._signature_generator is None:
            raise ValueError("Signature generator is required for order signing")
        if self.config.account_id is None:
            raise ValueError("Account ID is required for order signing")

        is_spot = self._is_spot_market(params.symbol)
        market_id = self.get_market_id_from_symbol(params.symbol)

        if params.expires_after is not None and params.time_in_force != TimeInForce.IOC and not is_spot:
            raise ValueError("Parameter expires_after is only allowed for IOC orders on perp markets")

        if params.time_in_force == TimeInForce.GTC and params.reduce_only is True:
            raise ValueError("Unexpected True value for parameter reduce_only for GTC orders")

        # Spot markets use a monotonically-increasing wall-clock-derived nonce
        # (fits in uint64); perp markets use the OrdersGateway encoded nonce.
        if is_spot:
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

        deadline = self._resolve_limit_order_deadline(params, is_spot)

        order_type_int = self._resolve_limit_order_type(params, is_spot)

        # Spot trades are matched against the orderbook; perps fill against the
        # pool counterparty.
        counterparty_ids = [] if is_spot else [self.config.pool_account_id]

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

        # `expiresAfter` is only sent on the wire for IOC perp + any spot;
        # `reduceOnly` is only meaningful for perp IOC.
        is_ioc_or_spot = params.time_in_force == TimeInForce.IOC or is_spot
        is_perp_ioc = params.time_in_force == TimeInForce.IOC and not is_spot

        payload = {
            "accountId": self.config.account_id,
            "symbol": params.symbol,
            "exchangeId": self.config.dex_id,
            "isBuy": params.is_buy,
            "limitPx": params.limit_px,
            "qty": params.qty,
            "orderType": OrderType.LIMIT.value,
            "timeInForce": params.time_in_force.value if params.time_in_force is not None else None,
            "expiresAfter": deadline if is_ioc_or_spot else None,
            "reduceOnly": params.reduce_only if is_perp_ioc else None,
            "signature": signature,
            "nonce": str(nonce),
            "signerWallet": self.signer_wallet_address,
            "clientOrderId": params.client_order_id,
        }
        return payload, nonce

    @staticmethod
    def _resolve_limit_order_deadline(params: LimitOrderParameters, is_spot: bool) -> int:
        """Resolve the EIP-712 deadline for a limit order based on TIF and market kind."""
        if params.time_in_force == TimeInForce.IOC:
            return params.expires_after if params.expires_after is not None else int(time.time()) + DEFAULT_DEADLINE_S
        # GTC branch
        if not is_spot:
            return CONDITIONAL_ORDER_DEADLINE
        # GTC spot
        if params.expires_after is None:
            return int(time.time()) + GTC_DEADLINE_S
        now = int(time.time())
        if params.expires_after <= now:
            raise ValueError(
                f"expires_after must be in the future for spot GTC orders "
                f"(got {params.expires_after}, now is {now})"
            )
        if params.expires_after > now + GTC_DEADLINE_S:
            raise ValueError(
                f"expires_after for spot GTC must be within {GTC_DEADLINE_S}s of now "
                f"(got {params.expires_after - now}s in the future)"
            )
        return params.expires_after

    @staticmethod
    def _resolve_limit_order_type(params: LimitOrderParameters, is_spot: bool) -> int:
        """Spot uses ``LIMIT_ORDER_SPOT`` regardless of TIF; perp branches on TIF + reduce-only."""
        if is_spot:
            return int(OrdersGatewayOrderType.LIMIT_ORDER_SPOT)
        if params.time_in_force == TimeInForce.GTC:
            return int(OrdersGatewayOrderType.LIMIT_ORDER)
        if params.reduce_only is True:
            return int(OrdersGatewayOrderType.REDUCE_ONLY_MARKET_ORDER)
        return int(OrdersGatewayOrderType.MARKET_ORDER)

    async def create_limit_order(self, params: LimitOrderParameters) -> CreateOrderResponse:
        """
        Create a limit (IOC/GTC) order asynchronously.

        Args:
            params: Limit order parameters

        Returns:
            API response for the order creation
        """
        payload, _nonce = self.build_create_limit_order_payload(params)
        order_request = CreateOrderRequest(**payload)

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

        payload, _nonce = self.build_create_trigger_order_payload(params)
        order_request = CreateOrderRequest(**payload)
        return await self.orders.create_order(create_order_request=order_request)

    def build_create_trigger_order_payload(self, params: TriggerOrderParameters) -> tuple[dict, int]:
        """Build the wire-shape payload for a TP/SL trigger order.

        Pure; the same dict shape is consumed by REST and ws-exec.
        """
        if self._signature_generator is None:
            raise ValueError("Signature generator is required for order signing")
        if self.config.account_id is None:
            raise ValueError("Account ID is required for order signing")
        if self._is_spot_market(params.symbol):
            raise ValueError("Trigger orders are not supported for spot markets")

        market_id = self.get_market_id_from_symbol(params.symbol)

        limit_px = Decimal(BUY_TRIGGER_ORDER_PRICE_LIMIT) if params.is_buy else Decimal(0)

        order_type_int = int(
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

        payload = {
            "accountId": self.config.account_id,
            "symbol": params.symbol,
            "exchangeId": self.config.dex_id,
            "isBuy": params.is_buy,
            "triggerPx": str(params.trigger_px),
            "limitPx": str(limit_px),
            "orderType": params.trigger_type.value,
            "expiresAfter": None,
            "signature": signature,
            "nonce": str(nonce),
            "signerWallet": self.signer_wallet_address,
        }
        return payload, nonce

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
        payload = self.build_cancel_order_payload(
            order_id=order_id,
            symbol=symbol,
            account_id=account_id,
            client_order_id=client_order_id,
        )
        cancel_order_request = CancelOrderRequest(**payload)
        return await self.orders.cancel_order(cancel_order_request)

    def build_cancel_order_payload(
        self,
        order_id: Optional[str] = None,
        symbol: Optional[str] = None,
        account_id: Optional[int] = None,
        client_order_id: Optional[int] = None,
    ) -> dict:
        """Build the wire-shape payload for a cancelOrder request. Pure; reused by REST and ws-exec."""
        if self._signature_generator is None:
            raise ValueError("Signature generator is required for cancelling orders")

        is_spot_order = bool(symbol and "RUSD" in symbol and "PERP" not in symbol)

        if is_spot_order:
            if symbol is None:
                raise ValueError("symbol is required for spot market order cancellation")
            if account_id is None:
                raise ValueError(f"account_id is required for spot market order cancellation (symbol: {symbol})")
            if order_id is None and client_order_id is None:
                raise ValueError("For spot orders, must provide either order_id or client_order_id")
        else:
            if order_id is None:
                raise ValueError("order_id is required for perp market order cancellation")

        nonce: Optional[int]
        deadline: Optional[int]

        if is_spot_order:
            assert symbol is not None
            assert account_id is not None
            market_id = self.get_market_id_from_symbol(symbol)
            nonce = self._get_next_nonce()
            deadline = int(time.time()) + DEFAULT_DEADLINE_S

            # The EIP-712 schema needs both ids; zero acts as the "absent" sentinel.
            order_id_int = int(order_id) if order_id is not None else 0
            client_order_id_int = client_order_id if client_order_id is not None else 0

            signature = self._signature_generator.sign_cancel_order_spot(
                account_id=account_id,
                market_id=market_id,
                order_id=order_id_int,
                client_order_id=client_order_id_int,
                nonce=nonce,
                deadline=deadline,
            )
        else:
            assert order_id is not None
            signature = self._signature_generator.sign_cancel_order_perps(order_id)
            nonce = None
            deadline = None

        return {
            "orderId": order_id,
            "clientOrderId": client_order_id,
            "signature": signature,
            "nonce": str(nonce) if nonce is not None else None,
            "symbol": symbol,
            "accountId": account_id,
            "expiresAfter": deadline,
        }

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
        payload = self.build_mass_cancel_payload(symbol=symbol, account_id=account_id)
        mass_cancel_request = MassCancelRequest(**payload)
        return await self.orders.cancel_all(mass_cancel_request)

    def build_mass_cancel_payload(
        self,
        symbol: Optional[str],
        account_id: Optional[int] = None,
    ) -> dict:
        """Build the wire-shape payload for a mass-cancel request.

        ``symbol=None`` is account-wide cancel; the EIP-712 typed data is then
        signed with ``market_id=0`` (the server reconstructs the same hash).
        """
        if self._signature_generator is None:
            raise ValueError("Signature generator is required for mass cancel")

        if symbol is not None and not self._is_spot_market(symbol):
            raise ValueError(
                f"Mass cancel is only supported for spot markets. Symbol '{symbol}' appears to be a perp market."
            )

        if account_id is None:
            account_id = self.config.account_id
            if account_id is None:
                raise ValueError("account_id is required for mass cancel")

        market_id = self.get_market_id_from_symbol(symbol) if symbol is not None else 0
        nonce = self._get_next_nonce()
        deadline = int(time.time()) + DEFAULT_DEADLINE_S

        signature = self._signature_generator.sign_mass_cancel(
            account_id=account_id,
            market_id=market_id,
            nonce=nonce,
            deadline=deadline,
        )

        return {
            "accountId": account_id,
            "symbol": symbol,
            "signature": signature,
            "nonce": str(nonce),
            "expiresAfter": deadline,
        }

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

    async def get_spot_execution_busts(self) -> SpotExecutionBustList:
        """
        Get spot execution busts (failed spot fills) for the owner wallet asynchronously.

        Returns:
            Spot execution busts

        Raises:
            ValueError: If no wallet address is available or API returns an error
        """
        wallet = self.owner_wallet_address
        if not wallet:
            raise ValueError("No wallet address available. Private key must be provided.")

        return await self.wallet.get_wallet_spot_execution_busts(address=wallet)

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
