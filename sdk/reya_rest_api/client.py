"""
Reya Trading Client - Main entry point for the Reya Trading API.

This module provides a client for interacting with the Reya Trading REST API.
The order entry surface is unified across spot and perp markets — all orders
flow through the same `Order` EIP-712 envelope and matching-engine pipeline.
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
from sdk.open_api.models.execution_bust_list import ExecutionBustList
from sdk.open_api.models.market_definition import MarketDefinition
from sdk.open_api.models.mass_cancel_request import MassCancelRequest
from sdk.open_api.models.mass_cancel_response import MassCancelResponse
from sdk.open_api.models.order import Order
from sdk.open_api.models.order_type import OrderType
from sdk.open_api.models.perp_execution_list import PerpExecutionList
from sdk.open_api.models.position import Position
from sdk.open_api.models.spot_execution_list import SpotExecutionList
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.open_api.models.wallet_configuration import WalletConfiguration
from sdk.reya_rest_api.auth.signatures import OrderTypeInt, SignatureGenerator, TimeInForceInt
from sdk.reya_rest_api.config import TradingConfig, get_config

from .models.orders import LimitOrderParameters, TriggerOrderParameters

DEFAULT_DEADLINE_S = 60  # Signature validity window for entry-time orders.


_ORDER_TYPE_TO_INT: dict[OrderType, OrderTypeInt] = {
    OrderType.LIMIT: OrderTypeInt.LIMIT,
    OrderType.STOP_LOSS: OrderTypeInt.STOP_LOSS,
    OrderType.TAKE_PROFIT: OrderTypeInt.TAKE_PROFIT,
}

_TIME_IN_FORCE_TO_INT: dict[TimeInForce, TimeInForceInt] = {
    TimeInForce.GTC: TimeInForceInt.GTC,
    TimeInForce.IOC: TimeInForceInt.IOC,
}


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

    Order entry, cancellation, and mass-cancel are unified across spot and
    perp markets — the matching engine handles routing based on `symbol` and
    `orderType`.
    """

    # Class-level nonce tracking per wallet address (shared across instances)
    _wallet_nonces: dict[str, int] = {}
    _wallet_nonce_lock = threading.Lock()

    def __init__(self, config: Optional[TradingConfig] = None):
        self._symbol_to_market_id: dict[str, int] = {}
        self._initialized = False

        self.logger = logging.getLogger("reya_trading.client")
        self._config = config if config is not None else get_config()
        self._signature_generator = SignatureGenerator(self._config)

        api_config = Configuration(host=self._config.api_url)
        self.logger.info(f"API URL: {api_config.host}")
        api_client = ApiClient(api_config)

        api_client.set_default_header("X-SDK-Version", f"reya-python-sdk/{SDK_VERSION}")
        api_client.set_default_header("User-Agent", f"reya-python-sdk/{SDK_VERSION}")

        self._resources = ResourceManager(api_client)
        self._api_client = api_client

    async def start(self) -> None:
        await self._load_market_definitions()

    async def _load_market_definitions(self) -> None:
        """Load both perp and spot market definitions."""
        market_definitions: list[MarketDefinition] = await self.reference.get_market_definitions()
        self._symbol_to_market_id = {market.symbol: market.market_id for market in market_definitions}
        perp_count = len(market_definitions)

        spot_market_definitions = await self.reference.get_spot_market_definitions()
        for market in spot_market_definitions:
            self._symbol_to_market_id[market.symbol] = market.market_id
        spot_count = len(spot_market_definitions)

        self._initialized = True
        total_markets = perp_count + spot_count
        self.logger.info(f"Loaded {total_markets} market definitions ({perp_count} perp, {spot_count} spot)")

    def _get_next_nonce(self) -> int:
        """Generate a strictly-increasing per-wallet nonce.

        Microsecond timestamp as base, advanced past any prior nonce in the
        same wallet to prevent races when multiple orders are signed in
        quick succession. Per-wallet at the class level so multiple client
        instances sharing a wallet share the same counter.
        """
        wallet_address = self._config.owner_wallet_address.lower()

        with ReyaTradingClient._wallet_nonce_lock:
            current_time_nonce = int(time.time() * 1_000_000)
            last_nonce = ReyaTradingClient._wallet_nonces.get(wallet_address, 0)
            new_nonce = max(current_time_nonce, last_nonce + 1)
            ReyaTradingClient._wallet_nonces[wallet_address] = new_nonce
            return new_nonce

    def _get_market_id_from_symbol(self, symbol: str) -> int:
        if not self._initialized:
            raise ValueError("Client not initialized. Call start() first.")

        market_id = self._symbol_to_market_id.get(symbol)
        if market_id is None:
            available_symbols = list(self._symbol_to_market_id.keys())
            raise ValueError(f"Unknown symbol '{symbol}'. Available symbols: {available_symbols}")

        return market_id

    @property
    def orders(self) -> OrderEntryApi:
        return self._resources.orders

    @property
    def wallet(self) -> WalletDataApi:
        return self._resources.wallet

    @property
    def markets(self) -> MarketDataApi:
        return self._resources.markets

    @property
    def reference(self) -> ReferenceDataApi:
        return self._resources.reference

    @property
    def config(self) -> TradingConfig:
        return self._config

    @property
    def signature_generator(self) -> SignatureGenerator:
        return self._signature_generator

    def get_next_nonce(self) -> int:
        return self._get_next_nonce()

    @property
    def signer_wallet_address(self) -> str:
        return self._signature_generator.signer_wallet_address

    @property
    def owner_wallet_address(self) -> str:
        """
        Wallet that owns ACCOUNT_ID. The signer wallet is either this wallet
        or one with delegated trading permission.
        """
        return self._config.owner_wallet_address

    async def create_limit_order(self, params: LimitOrderParameters) -> CreateOrderResponse:
        """
        Create a LIMIT order (IOC or GTC) on either spot or perp markets.

        The matching engine routes by `symbol`. `reduce_only` is perp-only
        and the API rejects it on spot. `expires_after` (order lifetime) is
        signed and enforced on-chain at fill time; it is independent from
        `deadline` (signature validity, enforced by the API at entry).
        """
        if self.config.account_id is None:
            raise ValueError("Account ID is required for order signing")

        market_id = self._get_market_id_from_symbol(params.symbol)
        nonce = self._get_next_nonce()
        deadline = params.deadline if params.deadline is not None else int(time.time()) + DEFAULT_DEADLINE_S
        # `expires_after` is signed and sent on every order regardless of TIF.
        # IOC carries it as defense-in-depth so the settlement contract can
        # independently reject stale orders even if the off-chain layer
        # misroutes one. When the caller doesn't pin a lifetime we mirror
        # `deadline` to match the documented `deadline <= expires_after`
        # convention.
        expires_after = params.expires_after if params.expires_after is not None else deadline
        client_order_id = params.client_order_id if params.client_order_id is not None else 0
        reduce_only = bool(params.reduce_only) if params.reduce_only is not None else False

        signature = self._signature_generator.sign_order(
            account_id=self.config.account_id,
            market_id=market_id,
            exchange_id=self.config.dex_id,
            order_type=int(OrderTypeInt.LIMIT),
            is_buy=params.is_buy,
            qty=Decimal(params.qty),
            limit_price=Decimal(params.limit_px),
            trigger_price=Decimal(0),
            time_in_force=int(_TIME_IN_FORCE_TO_INT[params.time_in_force]),
            client_order_id=client_order_id,
            reduce_only=reduce_only,
            expires_after=expires_after,
            nonce=nonce,
            deadline=deadline,
        )

        order_request = CreateOrderRequest(
            accountId=self.config.account_id,
            symbol=params.symbol,
            exchangeId=self.config.dex_id,
            isBuy=params.is_buy,
            limitPx=params.limit_px,
            qty=params.qty,
            orderType=OrderType.LIMIT,
            timeInForce=params.time_in_force,
            reduceOnly=reduce_only if params.reduce_only is not None else None,
            expiresAfter=expires_after,
            clientOrderId=params.client_order_id,
            signature=signature,
            nonce=str(nonce),
            signerWallet=self.signer_wallet_address,
            deadline=deadline,
        )

        return await self.orders.create_order(create_order_request=order_request)

    async def create_trigger_order(self, params: TriggerOrderParameters) -> CreateOrderResponse:
        """
        Create a STOP_LOSS or TAKE_PROFIT trigger order on a perp market.

        When the trigger price is hit, the matching engine places a limit
        order at `limit_px` for the signed `qty`. Spot triggers are not
        supported by the API.
        """
        if params.trigger_type not in (OrderType.STOP_LOSS, OrderType.TAKE_PROFIT):
            raise ValueError(f"Unsupported trigger_type: {params.trigger_type}")
        if self.config.account_id is None:
            raise ValueError("Account ID is required for order signing")

        market_id = self._get_market_id_from_symbol(params.symbol)
        nonce = self._get_next_nonce()
        deadline = params.deadline if params.deadline is not None else int(time.time()) + DEFAULT_DEADLINE_S
        client_order_id = params.client_order_id if params.client_order_id is not None else 0

        # If the caller didn't pin a worst-acceptable execution price, sign a
        # sentinel that always lets the order through after trigger: huge for
        # buys (worst-case high price), tiny non-zero for sells (worst-case low
        # price; the spec rejects 0).
        if params.limit_px is not None:
            limit_price = Decimal(params.limit_px)
        else:
            limit_price = Decimal("100000000000000000000") if params.is_buy else Decimal("0.000000001")

        order_type_int = _ORDER_TYPE_TO_INT[params.trigger_type]

        # See note in create_limit_order: the matching engine rejects expires_after=0,
        # so we default to `deadline` to keep the trigger live for the same window.
        expires_after = deadline

        signature = self._signature_generator.sign_order(
            account_id=self.config.account_id,
            market_id=market_id,
            exchange_id=self.config.dex_id,
            order_type=int(order_type_int),
            is_buy=params.is_buy,
            qty=Decimal(params.qty),
            limit_price=limit_price,
            trigger_price=Decimal(params.trigger_px),
            time_in_force=int(TimeInForceInt.GTC),
            client_order_id=client_order_id,
            reduce_only=bool(params.reduce_only) if params.reduce_only is not None else False,
            expires_after=expires_after,
            nonce=nonce,
            deadline=deadline,
        )

        order_request = CreateOrderRequest(
            accountId=self.config.account_id,
            symbol=params.symbol,
            exchangeId=self.config.dex_id,
            isBuy=params.is_buy,
            limitPx=str(limit_price),
            qty=params.qty,
            triggerPx=str(params.trigger_px),
            orderType=params.trigger_type,
            reduceOnly=params.reduce_only,
            expiresAfter=expires_after,
            clientOrderId=params.client_order_id,
            signature=signature,
            nonce=str(nonce),
            signerWallet=self.signer_wallet_address,
            deadline=deadline,
        )

        return await self.orders.create_order(create_order_request=order_request)

    async def cancel_order(
        self,
        symbol: str,
        account_id: Optional[int] = None,
        order_id: Optional[str] = None,
        client_order_id: Optional[int] = None,
    ) -> CancelOrderResponse:
        """
        Cancel a single open order. At least one of `order_id` or
        `client_order_id` must be provided. Works on both spot and perp
        markets.

        Precedence note: the off-chain matching-engine controller accepts
        both fields and prefers `order_id` as the canonical identifier
        (falling back to `client_order_id` only when `order_id` is
        absent). See ``tradingPrivateV2.controller.matching-engine.ts`` in
        reya-off-chain-monorepo. The OpenAPI docstring on
        ``CancelOrderRequest.orderId`` historically says "not both", but
        that's a recommended client contract — the server tolerates both
        and resolves deterministically. We therefore only enforce
        "at least one" here, matching the on-the-wire behaviour rather
        than the stricter docstring.
        """
        if order_id is None and client_order_id is None:
            raise ValueError("Provide either order_id or client_order_id")

        resolved_account_id = account_id if account_id is not None else self.config.account_id
        if resolved_account_id is None:
            raise ValueError("account_id is required (pass it or set in config)")

        market_id = self._get_market_id_from_symbol(symbol)
        nonce = self._get_next_nonce()
        deadline = int(time.time()) + DEFAULT_DEADLINE_S

        order_id_int = int(order_id) if order_id is not None else 0
        client_order_id_int = client_order_id if client_order_id is not None else 0

        signature = self._signature_generator.sign_cancel_order(
            account_id=resolved_account_id,
            market_id=market_id,
            order_id=order_id_int,
            client_order_id=client_order_id_int,
            nonce=nonce,
            deadline=deadline,
        )

        cancel_request = CancelOrderRequest(
            symbol=symbol,
            accountId=resolved_account_id,
            orderId=order_id,
            clientOrderId=client_order_id,
            signature=signature,
            nonce=str(nonce),
            deadline=deadline,
        )

        return await self.orders.cancel_order(cancel_request)

    async def mass_cancel(
        self,
        symbol: Optional[str] = None,
        account_id: Optional[int] = None,
    ) -> MassCancelResponse:
        """
        Cancel all open orders for an account, optionally filtered by
        market. Works on both spot and perp markets. Pass `symbol=None` to
        cancel across all markets the account has orders in.
        """
        resolved_account_id = account_id if account_id is not None else self.config.account_id
        if resolved_account_id is None:
            raise ValueError("account_id is required (pass it or set in config)")

        market_id = self._get_market_id_from_symbol(symbol) if symbol is not None else 0
        nonce = self._get_next_nonce()
        deadline = int(time.time()) + DEFAULT_DEADLINE_S

        signature = self._signature_generator.sign_mass_cancel(
            account_id=resolved_account_id,
            market_id=market_id,
            nonce=nonce,
            deadline=deadline,
        )

        mass_cancel_request = MassCancelRequest(
            accountId=resolved_account_id,
            symbol=symbol,
            signature=signature,
            nonce=str(nonce),
            deadline=deadline,
        )

        return await self.orders.cancel_all(mass_cancel_request)

    async def get_positions(self, wallet_address: Optional[str] = None) -> list[Position]:
        wallet = wallet_address or self.owner_wallet_address
        if not wallet:
            raise ValueError("No wallet address available.")
        return await self.wallet.get_wallet_positions(address=wallet)

    async def get_open_orders(self) -> list[Order]:
        wallet = self.owner_wallet_address
        if not wallet:
            raise ValueError("No wallet address available.")
        return await self.wallet.get_wallet_open_orders(address=wallet)

    async def get_configuration(self) -> WalletConfiguration:
        wallet = self.owner_wallet_address
        if not wallet:
            raise ValueError("No wallet address available.")
        return await self.wallet.get_wallet_configuration(address=wallet)

    async def get_perp_executions(self) -> PerpExecutionList:
        wallet = self.owner_wallet_address
        if not wallet:
            raise ValueError("No wallet address available.")
        return await self.wallet.get_wallet_perp_executions(address=wallet)

    async def get_accounts(self) -> list[Account]:
        wallet = self.owner_wallet_address
        if not wallet:
            raise ValueError("No wallet address available.")
        return await self.wallet.get_wallet_accounts(address=wallet)

    async def get_account_balances(self) -> list[AccountBalance]:
        wallet = self.owner_wallet_address
        if not wallet:
            raise ValueError("No wallet address available.")
        return await self.wallet.get_wallet_account_balances(address=wallet)

    async def get_spot_executions(self) -> SpotExecutionList:
        wallet = self.owner_wallet_address
        if not wallet:
            raise ValueError("No wallet address available.")
        return await self.wallet.get_wallet_spot_executions(address=wallet)

    async def get_execution_busts(self) -> ExecutionBustList:
        """Get execution busts (failed fills) across spot and perp markets
        for the owner wallet."""
        wallet = self.owner_wallet_address
        if not wallet:
            raise ValueError("No wallet address available.")
        return await self.wallet.get_wallet_execution_busts(address=wallet)

    async def close(self) -> None:
        if hasattr(self._api_client, "rest_client") and self._api_client.rest_client:
            await self._api_client.rest_client.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
