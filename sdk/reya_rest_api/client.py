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

# Two INDEPENDENT signed time fields:
#   - `deadline`     — EIP-712 signature-validity window, enforced off-chain at
#                      entry ONLY (on-chain verification skips the deadline).
#                      Bounds how long a signed payload may be submitted;
#                      irrelevant once the order rests.
#   - `expiresAfter` — the on-chain ORDER LIFETIME, enforced at settlement and by
#                      the matching engine (`expiresAfter == 0` => never expires).
#                      THIS is what keeps a resting order alive.
# The two are independent. GTC and IOC always sign `expiresAfter = 0` (GTC rests
# until filled or cancelled; IOC never rests, so its lifetime is moot) with a
# short entry-only `deadline`. Only GTT orders carry a non-zero `expiresAfter`,
# which must be greater than the `deadline` (the order has to outlive its own
# entry window). The matching engine accepts `expiresAfter == 0` as "no expiry".
DEFAULT_DEADLINE_S = 60  # signature-validity window (entry only), all order types.
PERPETUAL_LIFETIME = 0  # `expiresAfter` sentinel: never expires (GTC rests; IOC moot).

# Spot/perp namespace discriminator on the unified marketId, mirroring the
# off-chain market-id namespace convention. Perp market ids are the raw on-chain
# core id (well below 1e10); spot market ids are `core_id + 1e10`. Used to gate
# market-type-conditional wire fields like `reduceOnly` without an extra network
# round-trip.
_SPOT_MARKET_ID_OFFSET = 10_000_000_000


_ORDER_TYPE_TO_INT: dict[OrderType, OrderTypeInt] = {
    OrderType.LIMIT: OrderTypeInt.LIMIT,
    OrderType.STOP_LOSS: OrderTypeInt.STOP_LOSS,
    OrderType.TAKE_PROFIT: OrderTypeInt.TAKE_PROFIT,
}

# Maps the public OpenAPI `TimeInForce` to the signed uint8. The OpenAPI enum now
# exposes GTT, but it's intentionally absent here: GTT entry is gated in
# `build_create_limit_order_payload` (rejected until off-chain support lands), so
# only GTC/IOC reach this map. Add `TimeInForce.GTT: TimeInForceInt.GTT` when the
# gate lifts.
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
        # Perp tick size (price spacing) per symbol, from MarketDefinition.
        # Used to pick a price-spacing-conforming sentinel limit price for
        # TP/SL triggers when the caller doesn't pin one. Perp-only: triggers
        # aren't supported on spot.
        self._symbol_to_tick_size: dict[str, str] = {}
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
        self._symbol_to_tick_size = {market.symbol: market.tick_size for market in market_definitions}
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

    def get_market_id_from_symbol(self, symbol: str) -> int:
        if not self._initialized:
            raise ValueError("Client not initialized. Call start() first.")

        market_id = self._symbol_to_market_id.get(symbol)
        if market_id is None:
            available_symbols = list(self._symbol_to_market_id.keys())
            raise ValueError(f"Unknown symbol '{symbol}'. Available symbols: {available_symbols}")

        return market_id

    def _tick_size_for(self, symbol: str) -> str:
        """Return the perp market's tick size (price spacing) for ``symbol``.

        Used to pick a price-spacing-conforming sentinel limit price for TP/SL
        triggers. Perp-only — triggers aren't supported on spot.
        """
        if not self._initialized:
            raise ValueError("Client not initialized. Call start() first.")
        tick_size = self._symbol_to_tick_size.get(symbol)
        if tick_size is None:
            raise ValueError(f"No tick size for perp symbol '{symbol}'. Trigger orders are perp-only.")
        return tick_size

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

    def build_create_limit_order_payload(self, params: LimitOrderParameters) -> tuple[dict, int]:
        """Build the camelCase wire payload for a createOrder LIMIT request and
        return ``(payload, nonce)``.

        Pure (no I/O). The payload round-trips through both the REST
        ``CreateOrderRequest`` (OpenAPI) and the ws-exec ``CreateOrderRequest``
        (AsyncAPI) — they share field names — so the ws-exec transport reuses
        this exact unified signing path instead of duplicating it.
        """
        if self.config.account_id is None:
            raise ValueError("Account ID is required for order signing")
        if self._signature_generator is None:
            raise ValueError("Signature generator is required for order signing")

        market_id = self.get_market_id_from_symbol(params.symbol)
        is_spot_market = market_id >= _SPOT_MARKET_ID_OFFSET
        is_ioc = params.time_in_force == TimeInForce.IOC
        is_perp_ioc = is_ioc and not is_spot_market

        # GTT entry is signing-capable (`TimeInForceInt.GTT`) and now exposed in the
        # OpenAPI enum, but it can't travel end-to-end yet: the off-chain still
        # reconstructs the 13-field digest, and GTT needs its own `expiresAfter`
        # validation (non-zero, and greater than `deadline`). Reject it at entry
        # until off-chain support lands, rather than sign an un-settleable order.
        # (Lift this together with the `_TIME_IN_FORCE_TO_INT` GTT mapping and the
        # GTT expiresAfter rule.)
        if params.time_in_force == TimeInForce.GTT:
            raise ValueError(
                "GTT time-in-force is not yet supported end-to-end (pending off-chain "
                "14-field digest reconstruction and GTT expiresAfter validation)"
            )

        nonce = self._get_next_nonce()

        # `deadline` (entry-time signature validity) and `expiresAfter` (on-chain
        # order lifetime) are independent — see the constants block. Defaults:
        #   - deadline     → now + 60s for every order type (short entry window)
        #   - expiresAfter → 0 / perpetual (GTC rests until filled or cancelled;
        #                    IOC never rests, so its lifetime is moot)
        # An explicit `params.deadline` / `params.expires_after` overrides each
        # field independently.
        deadline = params.deadline if params.deadline is not None else int(time.time()) + DEFAULT_DEADLINE_S
        expires_after = params.expires_after if params.expires_after is not None else PERPETUAL_LIFETIME
        client_order_id = params.client_order_id if params.client_order_id is not None else 0

        # `reduceOnly` is accepted by the server ONLY on perp IOC orders; it must
        # be ABSENT on spot ("not supported for spot markets") and perp GTC
        # (rejected by the off-chain order validator). So gate the wire field to
        # perp IOC, and reject an explicit reduce-only elsewhere rather than
        # silently dropping the caller's intent. The signed `OrderDetails.reduceOnly`
        # mirrors the wire (False when not sent) so the on-chain digest matches.
        if params.reduce_only and not is_perp_ioc:
            raise ValueError("reduce_only is only supported on perp IOC orders")
        reduce_only = bool(params.reduce_only) if is_perp_ioc else False
        reduce_only_wire: Optional[bool] = reduce_only if is_perp_ioc else None

        # `post_only` marks a maker-only order: it must REST, never cross as a
        # taker. IOC is immediate-or-cancel (taker by nature), so post_only + IOC
        # is self-contradictory (the order could neither take nor rest) and is
        # always rejected. On-chain taker-side enforcement is deferred for now;
        # this is the entry guard.
        #
        # Rollout gate: the flag is signed into the 14-field `OrderDetails.postOnly`
        # digest and the `CreateOrderRequest` wire model now carries it, but
        # `post_only=True` still can't travel end-to-end — the off-chain digest
        # reconstruction is still 13-field, so a signed postOnly=true would fail
        # signer recovery off-chain. Reject True until off-chain reconstructs 14
        # fields, rather than emit an un-settleable order. The default False is
        # unaffected: signed as False and reconstructed as False either way.
        post_only = bool(params.post_only) if params.post_only is not None else False
        if post_only:
            if is_ioc:
                raise ValueError(
                    "post_only is not supported on IOC orders "
                    "(IOC is taker-only; post_only requires the order to rest)"
                )
            raise ValueError(
                "post_only=True is not yet supported end-to-end "
                "(pending the off-chain 14-field digest reconstruction)"
            )

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
            post_only=post_only,
        )

        payload = {
            "accountId": self.config.account_id,
            "symbol": params.symbol,
            "exchangeId": self.config.dex_id,
            "isBuy": params.is_buy,
            "limitPx": params.limit_px,
            "qty": params.qty,
            "orderType": OrderType.LIMIT.value,
            "timeInForce": params.time_in_force.value if params.time_in_force is not None else None,
            "reduceOnly": reduce_only_wire,
            # Signed into the 14-field digest above and carried on the wire. The
            # `CreateOrderRequest` model now has a `postOnly` field, so this is
            # transported (no longer dropped); `post_only` is gated to False above
            # until the off-chain side reconstructs 14 fields.
            "postOnly": post_only,
            "expiresAfter": expires_after,
            "clientOrderId": params.client_order_id,
            "signature": signature,
            "nonce": str(nonce),
            "signerWallet": self.signer_wallet_address,
            "deadline": deadline,
        }
        return payload, nonce

    async def create_limit_order(self, params: LimitOrderParameters) -> CreateOrderResponse:
        """
        Create a LIMIT order (IOC or GTC) on either spot or perp markets.

        The matching engine routes by `symbol`. `reduce_only` is perp-only
        and the API rejects it on spot. `expires_after` (order lifetime) is
        signed and enforced on-chain at fill time; it is independent from
        `deadline` (signature validity, enforced by the API at entry).
        """
        payload, _nonce = self.build_create_limit_order_payload(params)
        return await self.orders.create_order(create_order_request=CreateOrderRequest(**payload))

    def build_create_trigger_order_payload(self, params: TriggerOrderParameters) -> tuple[dict, int]:
        """Build the camelCase wire payload for a STOP_LOSS / TAKE_PROFIT trigger
        order and return ``(payload, nonce)``.

        Pure (no I/O). Shared verbatim by the REST sender below and the ws-exec
        transport, so both produce identical signed envelopes.
        """
        if params.trigger_type not in (OrderType.STOP_LOSS, OrderType.TAKE_PROFIT):
            raise ValueError(f"Unsupported trigger_type: {params.trigger_type}")
        if self.config.account_id is None:
            raise ValueError("Account ID is required for order signing")
        if self._signature_generator is None:
            raise ValueError("Signature generator is required for order signing")

        market_id = self.get_market_id_from_symbol(params.symbol)
        nonce = self._get_next_nonce()

        # A TP/SL rests until its trigger fires, so its on-chain LIFETIME
        # (`expiresAfter`) is 0 / perpetual — otherwise the stop is silently
        # killed and the position left unprotected. `deadline` is the independent
        # entry-time signature-validity window (now decoupled from `expiresAfter`;
        # the deployed matching engine accepts `expiresAfter == 0`). An explicit
        # `params.deadline` still wins.
        deadline = params.deadline if params.deadline is not None else int(time.time()) + DEFAULT_DEADLINE_S
        expires_after = PERPETUAL_LIFETIME
        client_order_id = params.client_order_id if params.client_order_id is not None else 0

        # reduce-only is server-rejected on non-IOC orders, and reduce-only /
        # close-on-trigger TP/SL is still being designed. Reject an explicit
        # reduce_only rather than sign+send a field the validator forbids; the
        # wire omits `reduceOnly` entirely for triggers.
        if params.reduce_only:
            raise ValueError("reduce_only on TP/SL trigger orders is not supported yet")

        # If the caller didn't pin a worst-acceptable execution price, sign a
        # sentinel that always lets the order through after the trigger fires:
        # a huge price for buys (worst-case high), and the market's smallest
        # tick for sells (worst-case low). The sell sentinel must be non-zero
        # AND conform to the market's price spacing — an arbitrary tiny value
        # like 0.000000001 is rejected by the matching engine as off-grid
        # ("does not conform to price spacing"), so we use exactly one tick.
        # The sentinel model itself (vs requiring an explicit limit_px, slippage
        # bounds, etc.) is being revisited.
        if params.limit_px is not None:
            limit_price = Decimal(params.limit_px)
        elif params.is_buy:
            limit_price = Decimal("100000000000000000000")
        else:
            limit_price = Decimal(self._tick_size_for(params.symbol))

        order_type_int = _ORDER_TYPE_TO_INT[params.trigger_type]

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
            reduce_only=False,
            expires_after=expires_after,
            nonce=nonce,
            deadline=deadline,
        )

        payload = {
            "accountId": self.config.account_id,
            "symbol": params.symbol,
            "exchangeId": self.config.dex_id,
            "isBuy": params.is_buy,
            # Fixed-point, never scientific notation. A small tick (e.g.
            # str(Decimal("0.0000001")) == "1E-7") would otherwise reach the
            # wire in sci notation, which the server's ethers FixedNumber parser
            # rejects with INVALID_ARGUMENT. format(..., "f") renders a plain
            # decimal for any tick size or caller-supplied price.
            "limitPx": format(limit_price, "f"),
            "qty": params.qty,
            "triggerPx": str(params.trigger_px),
            "orderType": params.trigger_type.value,
            "reduceOnly": None,
            "expiresAfter": expires_after,
            "clientOrderId": params.client_order_id,
            "signature": signature,
            "nonce": str(nonce),
            "signerWallet": self.signer_wallet_address,
            "deadline": deadline,
        }
        return payload, nonce

    async def create_trigger_order(self, params: TriggerOrderParameters) -> CreateOrderResponse:
        """
        Create a STOP_LOSS or TAKE_PROFIT trigger order on a perp market.

        When the trigger price is hit, the matching engine places a limit
        order at `limit_px` for the signed `qty`. Spot triggers are not
        supported by the API.
        """
        payload, _nonce = self.build_create_trigger_order_payload(params)
        return await self.orders.create_order(create_order_request=CreateOrderRequest(**payload))

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
        (falling back to `client_order_id` only when `order_id` is absent).
        The OpenAPI docstring on ``CancelOrderRequest.orderId`` historically
        says "not both", but that's a recommended client contract — the server
        tolerates both and resolves deterministically. We therefore only
        enforce "at least one" here, matching the on-the-wire behaviour rather
        than the stricter docstring.
        """
        payload = self.build_cancel_order_payload(
            symbol=symbol,
            account_id=account_id,
            order_id=order_id,
            client_order_id=client_order_id,
        )
        return await self.orders.cancel_order(CancelOrderRequest(**payload))

    def build_cancel_order_payload(
        self,
        symbol: Optional[str] = None,
        account_id: Optional[int] = None,
        order_id: Optional[str] = None,
        client_order_id: Optional[int] = None,
    ) -> dict:
        """Build the camelCase wire payload for a cancelOrder request.

        Pure (no I/O). Shared by the REST sender above and the ws-exec
        transport. Same arg semantics as :meth:`cancel_order`.

        `symbol` is typed Optional only to match the ws-exec client's
        forwarding signature; it is required in practice (the cancel
        EIP-712 envelope signs the resolved marketId), so a missing symbol
        raises rather than silently producing an unsigned-for-market cancel.
        """
        if not symbol:
            raise ValueError("symbol is required to cancel an order")
        if order_id is None and client_order_id is None:
            raise ValueError("Provide either order_id or client_order_id")

        resolved_account_id = account_id if account_id is not None else self.config.account_id
        if resolved_account_id is None:
            raise ValueError("account_id is required (pass it or set in config)")
        if self._signature_generator is None:
            raise ValueError("Signature generator is required for order signing")

        market_id = self.get_market_id_from_symbol(symbol)
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

        return {
            "symbol": symbol,
            "accountId": resolved_account_id,
            "orderId": order_id,
            "clientOrderId": client_order_id,
            "signature": signature,
            "nonce": str(nonce),
            "deadline": deadline,
        }

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
        payload = self.build_mass_cancel_payload(symbol=symbol, account_id=account_id)
        return await self.orders.cancel_all(MassCancelRequest(**payload))

    def build_mass_cancel_payload(
        self,
        symbol: Optional[str] = None,
        account_id: Optional[int] = None,
    ) -> dict:
        """Build the camelCase wire payload for a cancelAll (mass-cancel) request.

        Pure (no I/O). Shared by the REST sender above and the ws-exec
        transport. Pass ``symbol=None`` to cancel across all markets.
        """
        resolved_account_id = account_id if account_id is not None else self.config.account_id
        if resolved_account_id is None:
            raise ValueError("account_id is required (pass it or set in config)")
        if self._signature_generator is None:
            raise ValueError("Signature generator is required for order signing")

        market_id = self.get_market_id_from_symbol(symbol) if symbol is not None else 0
        nonce = self._get_next_nonce()
        deadline = int(time.time()) + DEFAULT_DEADLINE_S

        signature = self._signature_generator.sign_mass_cancel(
            account_id=resolved_account_id,
            market_id=market_id,
            nonce=nonce,
            deadline=deadline,
        )

        return {
            "accountId": resolved_account_id,
            "symbol": symbol,
            "signature": signature,
            "nonce": str(nonce),
            "deadline": deadline,
        }

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
