"""
Reya Trading Client - Main entry point for the Reya Trading API.

This module provides a client for interacting with the Reya Trading REST API.
The order entry surface is unified across spot and perp markets — all orders
flow through the same `Order` EIP-712 envelope and matching-engine pipeline.
"""

from typing import Any, Optional

import logging
import threading
import time
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from sdk._version import SDK_VERSION
from sdk.open_api.api.market_data_api import MarketDataApi
from sdk.open_api.api.order_entry_api import OrderEntryApi
from sdk.open_api.api.reference_data_api import ReferenceDataApi
from sdk.open_api.api.wallet_data_api import WalletDataApi
from sdk.open_api.api_client import ApiClient
from sdk.open_api.configuration import Configuration
from sdk.open_api.models.account import Account
from sdk.open_api.models.account_balance import AccountBalance
from sdk.open_api.models.asset_oracle_price import AssetOraclePrice
from sdk.open_api.models.cancel_all_after_request import CancelAllAfterRequest
from sdk.open_api.models.cancel_all_after_response import CancelAllAfterResponse
from sdk.open_api.models.cancel_order_request import CancelOrderRequest
from sdk.open_api.models.cancel_order_response import CancelOrderResponse
from sdk.open_api.models.create_order_request import CreateOrderRequest
from sdk.open_api.models.create_order_response import CreateOrderResponse
from sdk.open_api.models.execution_bust_list import ExecutionBustList
from sdk.open_api.models.market_definition import MarketDefinition
from sdk.open_api.models.mass_cancel_request import MassCancelRequest
from sdk.open_api.models.mass_cancel_response import MassCancelResponse
from sdk.open_api.models.modify_order_request import ModifyOrderRequest
from sdk.open_api.models.modify_order_response import ModifyOrderResponse
from sdk.open_api.models.order import Order
from sdk.open_api.models.order_history_list import OrderHistoryList
from sdk.open_api.models.order_type import OrderType
from sdk.open_api.models.perp_execution_list import PerpExecutionList
from sdk.open_api.models.position import Position
from sdk.open_api.models.spot_execution_list import SpotExecutionList
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.open_api.models.wallet_configuration import WalletConfiguration
from sdk.reya_rest_api.auth.signatures import OrderTypeInt, SignatureGenerator, TimeInForceInt
from sdk.reya_rest_api.config import TradingConfig, get_config

from .models.orders import LimitOrderParameters, ModifyOrderParameters, TriggerOrderParameters

# Two INDEPENDENT signed time fields:
#   - `deadline`     — EIP-712 signature-validity window, enforced off-chain at
#                      entry ONLY (on-chain verification skips the deadline).
#                      Bounds how long a signed payload may be submitted;
#                      irrelevant once the order rests.
#   - `expiresAfter` — the on-chain ORDER LIFETIME, enforced at settlement and by
#                      the matching engine. The signed no-expiry encoding is
#                      zero, but REST/WS JSON omits `expiresAfter` for no-expiry.
# The two are independent. GTC and IOC sign the no-expiry value (GTC rests until
# filled or cancelled; IOC never rests, so its lifetime is moot) with a short
# entry-only `deadline`. Only GTT orders carry a non-zero `expiresAfter`, which
# must be greater than the `deadline` (the order has to outlive its own entry
# window).
DEFAULT_DEADLINE_S = 60  # signature-validity window (entry only), all order types.
PERPETUAL_LIFETIME = 0  # signed no-expiry encoding (GTC rests; IOC moot).

# The ME's MAX_PRICE (2^49 in E9 fixed point ≈ 562,949.95): the binding price
# ceiling across the stack, independent of any per-market band. A market that
# publishes triggerLimitBandFraction "0" switches the band off, not the ceiling.
_ME_MAX_PRICE = Decimal(1 << 49) / Decimal(10**9)

# The trigger limit band is operationally tunable and the API serves it from a
# 60s cache, so a long-lived client re-reads market definitions on the same
# bound rather than enforcing the band that was live at process start.
_MARKET_DEFINITIONS_TTL_S = 60.0

# cancelAllAfter (dead-man's-switch) countdown bounds. `timeoutMs` must be 0
# (disarm) or within [min, max]; each call replaces the running countdown.
CANCEL_ALL_AFTER_DISARM_MS = 0
CANCEL_ALL_AFTER_MIN_TIMEOUT_MS = 5_000
CANCEL_ALL_AFTER_MAX_TIMEOUT_MS = 60_000

# Spot/perp namespace discriminator on the unified marketId, mirroring the
# off-chain market-id namespace convention. Perp market ids are the raw on-chain
# core id (well below 1e10); spot market ids are `core_id + 1e10`. Used to gate
# market-type-conditional wire fields like `reduceOnly` without an extra network
# round-trip.
_SPOT_MARKET_ID_OFFSET = 10_000_000_000


def _reject_zero_deadline(deadline: int) -> None:
    """A signed envelope deadline must be explicit.

    Zero is refused at intake: the settlement calldata builder would otherwise
    infer the deadline from `expiresAfter`, and the reconstructed digest would
    recover the wrong signer whenever the two differ.
    """
    if deadline == 0:
        raise ValueError("deadline must be an explicit non-zero signature-validity window")


def _require_settlement_headroom(expires_after: int, headroom_s: int, now_s: int) -> None:
    """Refuse a lifetime that does not outlast the settlement headroom.

    This is independent of the `expiresAfter > deadline` coupling: a caller that
    pins a short deadline satisfies that check while still signing a lifetime
    the engine refuses. Failing here names the rule instead of burning a nonce
    on an order that cannot be admitted.
    """
    if expires_after == PERPETUAL_LIFETIME:
        return
    earliest_admissible = now_s + headroom_s
    if expires_after <= earliest_admissible:
        raise ValueError(
            f"expires_after={expires_after} does not outlast the {headroom_s}s settlement "
            f"headroom (needs > {earliest_admissible}). Set REYA_SETTLEMENT_HEADROOM_S to the "
            "target deployment's value if it runs a different one."
        )


_RESTATE_IMMUTABLE_REMEDY = (
    " time_in_force and expires_after are restate-immutable on an armed trigger: restate the armed "
    "values, or cancel the trigger and create a new one to change them."
)


def _require_trigger_expiry_coupling(
    time_in_force: TimeInForce, expires_after: int, deadline: int, on_modify: bool = False
) -> None:
    """Enforce the TIF <-> `expiresAfter` coupling for a STOP_LOSS / TAKE_PROFIT.

    A trigger's signed `timeInForce` is what the stop BECOMES when it fires, and
    only GTT carries a lifetime. The single `expiresAfter` covers both phases —
    the armed trigger and the fired child's on-chain settlement — so a GTT
    trigger must carry a future one and GTC/IOC must omit it.

    A modify restates both immutably and the client cannot read the armed values
    back off the resting order, so these shape rules are the whole of what it can
    check: a TIF or expiry change that lands on an impossible shape is refused
    here, one that lands on a legal shape is the matching engine's
    INPUT_VALIDATION_ERROR. Every refusal on that path names the remedy.
    """
    remedy = _RESTATE_IMMUTABLE_REMEDY if on_modify else ""
    if time_in_force == TimeInForce.GTT:
        if expires_after == PERPETUAL_LIFETIME:
            raise ValueError("GTT trigger orders require a non-zero expires_after greater than deadline." + remedy)
        if expires_after <= deadline:
            raise ValueError("GTT expires_after must be greater than deadline." + remedy)
    elif expires_after != PERPETUAL_LIFETIME:
        raise ValueError(f"{time_in_force.value} trigger orders must omit expires_after." + remedy)


def _require_limit_expiry_coupling(time_in_force: TimeInForce, expires_after: int, deadline: int) -> None:
    """Enforce the TIF <-> `expiresAfter` coupling for a LIMIT order.

    Only GTT carries a lifetime. GTC rests until it is filled or cancelled and
    IOC never rests at all, so the off-chain validator refuses an `expiresAfter`
    on either — refuse it here instead of burning a nonce on it.
    """
    if time_in_force == TimeInForce.GTT:
        if expires_after == PERPETUAL_LIFETIME:
            raise ValueError("GTT orders require a non-zero expires_after greater than deadline")
        if expires_after <= deadline:
            raise ValueError("GTT expires_after must be greater than deadline")
    elif expires_after != PERPETUAL_LIFETIME:
        raise ValueError(f"{time_in_force.value} orders must omit expires_after")


def _floor_to_tick(price: Decimal, tick: Decimal) -> Decimal:
    return (price / tick).to_integral_value(rounding=ROUND_FLOOR) * tick


def _ceil_to_tick(price: Decimal, tick: Decimal) -> Decimal:
    # Decimal's `//` truncates toward zero rather than flooring, so the negation
    # trick used for ints rounds a lower band bound the wrong way (outward).
    return (price / tick).to_integral_value(rounding=ROUND_CEILING) * tick


def _reject_zero_client_order_id(client_order_id: Optional[int]) -> None:
    if client_order_id == 0:
        raise ValueError("client_order_id must be omitted rather than set to 0")


def _strip_absent_json_fields(payload: dict) -> dict:
    """Drop optional JSON fields that are absent after signature construction."""
    return {
        key: value
        for key, value in payload.items()
        if value is not None and not (key == "expiresAfter" and value == PERPETUAL_LIFETIME)
    }


_ORDER_TYPE_TO_INT: dict[OrderType, OrderTypeInt] = {
    OrderType.LIMIT: OrderTypeInt.LIMIT,
    OrderType.STOP_LOSS: OrderTypeInt.STOP_LOSS,
    OrderType.TAKE_PROFIT: OrderTypeInt.TAKE_PROFIT,
}

# Maps the public OpenAPI `TimeInForce` to the signed uint8 (0=GTC, 1=IOC, 2=GTT).
_TIME_IN_FORCE_TO_INT: dict[TimeInForce, TimeInForceInt] = {
    TimeInForce.GTC: TimeInForceInt.GTC,
    TimeInForce.IOC: TimeInForceInt.IOC,
    TimeInForce.GTT: TimeInForceInt.GTT,
}


def _order_type_int(order_type: Any) -> OrderTypeInt:
    """Resolve the signed uint8 for an order type, refusing anything unmapped.

    Resolved BEFORE a nonce is claimed: a bare `KeyError` out of the signing
    step would leave the per-wallet counter advanced for an order that was
    never signed.
    """
    try:
        return _ORDER_TYPE_TO_INT[order_type]
    except (KeyError, TypeError):
        raise ValueError(f"Unsupported order_type: {order_type!r}. Expected one of {[t.value for t in OrderType]}")


def _time_in_force_int(time_in_force: Any) -> TimeInForceInt:
    """Resolve the signed uint8 for a time in force, refusing anything unmapped."""
    try:
        return _TIME_IN_FORCE_TO_INT[time_in_force]
    except (KeyError, TypeError):
        raise ValueError(
            f"Unsupported time_in_force: {time_in_force!r}. Expected one of {[t.value for t in TimeInForce]}"
        )


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
        # Perp-only: triggers aren't supported on spot.
        self._symbol_to_tick_size: dict[str, str] = {}
        # Per-market trigger limit band, from MarketDefinition. A market that
        # publishes no fraction is ABSENT here rather than stored as "0": the
        # venue admits every positive limit price under "0" and refuses every
        # trigger when the band is unpublished, so the two must not collapse.
        self._symbol_to_trigger_band: dict[str, str] = {}
        self._initialized = False
        self._market_definitions_loaded_at: Optional[float] = None

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
        market_definitions: list[MarketDefinition] = await self.reference.get_perp_market_definitions()
        spot_market_definitions = await self.reference.get_spot_market_definitions()

        # Both fetches finish BEFORE anything is published, and the swap below
        # contains no await. `refresh_trigger_market_state` calls this mid-session,
        # so publishing the perp-only map and then awaiting the spot fetch would
        # make every spot symbol unresolvable for the length of that await — and
        # permanently if it raised, since the partial map would already be live.
        symbol_to_market_id = {market.symbol: market.market_id for market in market_definitions}
        symbol_to_tick_size = {market.symbol: market.tick_size for market in market_definitions}
        symbol_to_trigger_band = {
            market.symbol: market.trigger_limit_band_fraction
            for market in market_definitions
            if market.trigger_limit_band_fraction is not None
        }
        for market in spot_market_definitions:
            symbol_to_market_id[market.symbol] = market.market_id

        self._symbol_to_market_id = symbol_to_market_id
        self._symbol_to_tick_size = symbol_to_tick_size
        self._symbol_to_trigger_band = symbol_to_trigger_band
        self._initialized = True
        self._market_definitions_loaded_at = time.monotonic()

        perp_count = len(market_definitions)
        spot_count = len(spot_market_definitions)
        total_markets = perp_count + spot_count
        self.logger.info(f"Loaded {total_markets} market definitions ({perp_count} perp, {spot_count} spot)")

    async def refresh_trigger_market_state(self, order_type: OrderType) -> None:
        """Re-read market definitions before signing against a per-market trigger
        rule, once the cached copy has aged past the TTL.

        `triggerLimitBandFraction` and the tick it rounds to are operationally
        tunable, so a client that read them once at `start()` would enforce the
        band that was live at process start and sign a `limitPx` the venue now
        refuses. No-op for LIMIT orders, which are bound by neither.
        """
        if order_type not in (OrderType.STOP_LOSS, OrderType.TAKE_PROFIT):
            return
        if not self._initialized:
            return
        loaded_at = self._market_definitions_loaded_at
        if loaded_at is not None and time.monotonic() - loaded_at < _MARKET_DEFINITIONS_TTL_S:
            return
        await self._load_market_definitions()

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

        Perp-only — triggers aren't supported on spot.
        """
        if not self._initialized:
            raise ValueError("Client not initialized. Call start() first.")
        tick_size = self._symbol_to_tick_size.get(symbol)
        if tick_size is None:
            raise ValueError(f"No tick size for perp symbol '{symbol}'. Trigger orders are perp-only.")
        return tick_size

    def _require_trigger_limit_in_band(self, symbol: str, trigger_px: Decimal, limit_px: Decimal) -> None:
        """Mirror the venue's trigger limit-band admission rule, client-side.

        Admission enforces ``|limit_px - trigger_px| <= trigger_px * fraction``
        with the outermost legal price rounded INWARD to the market's tick, so a
        limit outside the band is refused before a nonce is burned on an order
        the venue answers with TRIGGER_LIMIT_OUTSIDE_BAND_ERROR.

        The fraction's presence is itself part of the rule: ``"0"`` disables the
        band and admits any positive limit, while an ABSENT fraction means the
        market has no band configured and the venue arms no trigger on it at all.
        The engine's MAX_PRICE ceiling is independent of all three: ``"0"``
        switches off the band, not the ceiling.
        """
        if limit_px <= 0:
            raise ValueError(f"limit_px must be a positive price (got {limit_px})")
        if limit_px > _ME_MAX_PRICE:
            raise ValueError(
                f"limit_px {limit_px} exceeds the matching engine's maximum price "
                f"{format(_ME_MAX_PRICE.normalize(), 'f')}"
            )
        # `trigger_px` carries the same (0, MAX_PRICE] domain as `limit_px` — the
        # engine's `validate_place` bounds both — and it is also the anchor the
        # band below is computed against. Checking it here keeps a zero from
        # producing a nonsense band message, and an over-ceiling value from
        # passing every client-side rule only to burn a nonce at the venue.
        if trigger_px <= 0:
            raise ValueError(f"trigger_px must be a positive price (got {trigger_px})")
        if trigger_px > _ME_MAX_PRICE:
            raise ValueError(
                f"trigger_px {trigger_px} exceeds the matching engine's maximum price "
                f"{format(_ME_MAX_PRICE.normalize(), 'f')}"
            )

        if not self._initialized:
            raise ValueError("Client not initialized. Call start() first.")
        if self.get_market_id_from_symbol(symbol) >= _SPOT_MARKET_ID_OFFSET:
            raise ValueError(f"STOP_LOSS / TAKE_PROFIT orders are perp-only; '{symbol}' is a spot market.")
        fraction_str = self._symbol_to_trigger_band.get(symbol)
        if fraction_str is None:
            raise ValueError(
                f"Market '{symbol}' publishes no triggerLimitBandFraction, so the venue refuses every "
                "STOP_LOSS / TAKE_PROFIT on it. An absent band is not a band of '0'; wait for the market "
                "to publish one."
            )

        fraction = Decimal(fraction_str)
        if fraction == 0:
            return

        tick = Decimal(self._tick_size_for(symbol))
        highest = _floor_to_tick(trigger_px * (1 + fraction), tick)
        lowest = _ceil_to_tick(trigger_px * (1 - fraction), tick)
        if not lowest <= limit_px <= highest:
            # Bound arithmetic leaves the exponent wherever the multiply and
            # divide put it, so render a stable decimal rather than 1.05E+3.
            raise ValueError(
                f"limit_px {limit_px} is outside {symbol}'s trigger limit band "
                f"[{format(lowest.normalize(), 'f')}, {format(highest.normalize(), 'f')}] "
                f"around trigger_px {trigger_px} "
                f"(triggerLimitBandFraction {fraction_str}, tick {tick})"
            )

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

    async def get_asset_oracle_price(self, asset: str) -> AssetOraclePrice:
        """Fetch a single asset Stork oracle price from /assetOraclePrices."""
        target = asset.upper()
        prices = await self.markets.get_asset_oracle_prices()
        for price in prices:
            if price.asset.upper() == target:
                return price

        available_assets = ", ".join(sorted(price.asset for price in prices))
        raise RuntimeError(f"Asset oracle price not found for {asset}. Available assets: {available_assets}")

    async def get_market_mark_price(self, symbol: str) -> str:
        """Fetch a perp market's mark price from /perpMarket/{symbol}/summary."""
        summary = await self.markets.get_perp_market_summary(symbol)
        if summary.mark_price is None:
            raise RuntimeError(f"Market summary for {symbol} did not include markPrice")
        return summary.mark_price

    async def get_market_mid_price(self, symbol: str) -> str:
        """Fetch a perp market's throttled mid price from /perpMarket/{symbol}/summary."""
        summary = await self.markets.get_perp_market_summary(symbol)
        if summary.throttled_mid_price is None:
            raise RuntimeError(f"Market summary for {symbol} did not include throttledMidPrice")
        return summary.throttled_mid_price

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
        _reject_zero_client_order_id(params.client_order_id)

        # `deadline` (entry-time signature validity) and `expiresAfter` (on-chain
        # order lifetime) are independent — see the constants block. Defaults:
        #   - deadline     → now + 60s for every order type (short entry window)
        #   - expiresAfter → omitted in JSON for no-expiry orders; signed as
        #                    the no-expiry value (GTC rests until filled or
        #                    cancelled; IOC never rests, so its lifetime is moot)
        # An explicit `params.deadline` / `params.expires_after` overrides each
        # field independently.
        now_s = int(time.time())
        deadline = params.deadline if params.deadline is not None else now_s + DEFAULT_DEADLINE_S
        expires_after = params.expires_after if params.expires_after is not None else PERPETUAL_LIFETIME
        client_order_id = params.client_order_id if params.client_order_id is not None else 0

        # TIF <-> expiresAfter coupling (mirrors the off-chain validator + the ME):
        # GTC never expires; GTT always expires strictly after the deadline.
        # IOC never rests, so a lifetime on one is refused too. Fail fast before signing.
        _reject_zero_deadline(deadline)
        _require_limit_expiry_coupling(params.time_in_force, expires_after, deadline)
        _require_settlement_headroom(expires_after, self._config.settlement_headroom_s, now_s)
        time_in_force_int = _time_in_force_int(params.time_in_force)

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
        # taker. The off-chain verifies the 14-field digest and the matching
        # engine enforces would-cross, so post_only=True travels end-to-end.
        # IOC is immediate-or-cancel (taker by nature), so post_only + IOC is
        # self-contradictory (the order could neither take nor rest) and is
        # always rejected.
        post_only = bool(params.post_only) if params.post_only is not None else False
        if post_only and is_ioc:
            raise ValueError(
                "post_only is not supported on IOC orders (IOC is taker-only; post_only requires the order to rest)"
            )

        # Every rejection above is unconditional, so the nonce is claimed only
        # once the request is known to be admissible.
        nonce = self._get_next_nonce()

        signature = self._signature_generator.sign_order(
            account_id=self.config.account_id,
            market_id=market_id,
            exchange_id=self.config.dex_id,
            order_type=int(OrderTypeInt.LIMIT),
            is_buy=params.is_buy,
            qty=Decimal(params.qty),
            limit_price=Decimal(params.limit_px),
            trigger_price=Decimal(0),
            time_in_force=int(time_in_force_int),
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
            "postOnly": post_only,
            "expiresAfter": expires_after,
            "clientOrderId": (str(params.client_order_id) if params.client_order_id is not None else None),
            "signature": signature,
            "nonce": str(nonce),
            "signerWallet": self.signer_wallet_address,
            "deadline": deadline,
        }
        return _strip_absent_json_fields(payload), nonce

    async def create_limit_order(self, params: LimitOrderParameters) -> CreateOrderResponse:
        """
        Create a LIMIT order (IOC or GTC) on either spot or perp markets.

        The matching engine routes by `symbol`. `reduce_only` is perp-only
        and the API rejects it on spot. `expires_after` (order lifetime) is
        signed and enforced on-chain at fill time; it is independent from
        `deadline` (signature validity, enforced by the API at entry).

        `reduce_only` is sized against your SETTLED position, so a FILLED ack is
        not yet something you can reduce. Close a just-opened position and the
        engine may refuse it with `reduce-only order has nothing to reduce`
        (or, part-way through settling, `...would increase or flip position
        direction`) until that opening fill settles on chain — milliseconds
        locally, a chain round-trip in production. This is deliberate: crediting
        an unsettled increase would let a "close" become an open if the increase
        busts, which is the thing reduce-only exists to prevent.

        So either wait for the position to be observable — poll `positions` (or
        the position-update stream) until it reflects the opening fill — before
        sending the reduce-only close, or send a plain opposite-side order with
        `reduce_only=False`, which is admitted throughout. Do NOT blind-retry:
        the refusal is a correct verdict about a real state, not a transient
        error, and it clears when the position settles, not when you retry.
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
        _reject_zero_client_order_id(params.client_order_id)
        if params.qty is not None:
            raise ValueError("qty on TP/SL trigger orders is not supported; omit qty")

        market_id = self.get_market_id_from_symbol(params.symbol)

        # `deadline` is the entry-time signature-validity window; `expiresAfter`
        # is the order lifetime the signed `timeInForce` chooses. On a trigger
        # the two phases share one lifetime, so GTT carries a future expiry and
        # GTC/IOC omit it. An explicit `params.deadline` still wins.
        now_s = int(time.time())
        deadline = params.deadline if params.deadline is not None else now_s + DEFAULT_DEADLINE_S
        _reject_zero_deadline(deadline)
        expires_after = params.expires_after if params.expires_after is not None else PERPETUAL_LIFETIME
        _require_trigger_expiry_coupling(params.time_in_force, expires_after, deadline)
        _require_settlement_headroom(expires_after, self._config.settlement_headroom_s, now_s)
        client_order_id = params.client_order_id if params.client_order_id is not None else 0

        # reduce-only is server-rejected on non-IOC orders, and a stop closes by
        # construction, so it signs `reduceOnly=false`. Reject an explicit
        # reduce_only rather than sign+send a field the validator forbids; the
        # wire omits `reduceOnly` entirely for triggers.
        if params.reduce_only:
            raise ValueError("reduce_only on TP/SL trigger orders is not supported yet")

        limit_price = Decimal(params.limit_px)
        trigger_price = Decimal(params.trigger_px)
        self._require_trigger_limit_in_band(params.symbol, trigger_price, limit_price)

        order_type_int = _order_type_int(params.trigger_type)
        time_in_force_int = _time_in_force_int(params.time_in_force)

        # Every rejection above is unconditional, so the nonce is claimed only
        # once the request is known to be admissible.
        nonce = self._get_next_nonce()

        signature = self._signature_generator.sign_order(
            account_id=self.config.account_id,
            market_id=market_id,
            exchange_id=self.config.dex_id,
            order_type=int(order_type_int),
            is_buy=params.is_buy,
            qty=Decimal(0),
            limit_price=limit_price,
            trigger_price=trigger_price,
            time_in_force=int(time_in_force_int),
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
            # Fixed-point, never scientific notation. A small price (e.g.
            # str(Decimal("0.0000001")) == "1E-7") would otherwise reach the
            # wire in sci notation, which the server's ethers FixedNumber parser
            # rejects with INVALID_ARGUMENT. format(..., "f") renders a plain
            # decimal for any caller-supplied price.
            "limitPx": format(limit_price, "f"),
            "triggerPx": str(params.trigger_px),
            "orderType": params.trigger_type.value,
            "timeInForce": params.time_in_force.value,
            "reduceOnly": None,
            "expiresAfter": expires_after,
            "clientOrderId": (str(params.client_order_id) if params.client_order_id is not None else None),
            "signature": signature,
            "nonce": str(nonce),
            "signerWallet": self.signer_wallet_address,
            "deadline": deadline,
        }
        return _strip_absent_json_fields(payload), nonce

    async def create_trigger_order(self, params: TriggerOrderParameters) -> CreateOrderResponse:
        """
        Create a STOP_LOSS or TAKE_PROFIT trigger order on a perp market.

        When the trigger price is hit, the matching engine fires a child order
        at `limit_px` for the account's full live position, under the
        `time_in_force` the create chose — GTC or GTT rests, IOC takes or
        cancels. The signer derives the direction-aware full-position sentinel;
        callers omit `qty`. Spot triggers are not supported by the API.
        """
        await self.refresh_trigger_market_state(params.trigger_type)
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
        Cancel a single open order. Provide `order_id`, or a non-zero
        `client_order_id` when `order_id` is absent. Works on both spot
        and perp markets.

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
        _reject_zero_client_order_id(client_order_id)

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

        return _strip_absent_json_fields(
            {
                "symbol": symbol,
                "accountId": resolved_account_id,
                "orderId": order_id,
                "clientOrderId": (str(client_order_id) if client_order_id is not None else None),
                "signature": signature,
                "nonce": str(nonce),
                "deadline": deadline,
            }
        )

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

    async def cancel_all_after(
        self,
        timeout_ms: int,
        account_id: Optional[int] = None,
        deadline: Optional[int] = None,
        nonce: Optional[int] = None,
    ) -> CancelAllAfterResponse:
        """
        Arm, refresh, or disarm the account-wide cancel-all-after countdown
        (dead-man's-switch). While armed, all of the account's open orders are
        mass-cancelled unless the countdown is refreshed with another call
        before `timeout_ms` elapses. `timeout_ms=0` disarms; non-zero values
        must be within [5000, 60000] ms. Each call replaces the countdown.
        Refresh is explicit only — order entry, ping/pong, and connection
        close neither refresh nor trigger it.
        """
        payload = self.build_cancel_all_after_payload(
            timeout_ms=timeout_ms,
            account_id=account_id,
            deadline=deadline,
            nonce=nonce,
        )
        return await self.orders.cancel_all_after(CancelAllAfterRequest(**payload))

    def build_cancel_all_after_payload(
        self,
        timeout_ms: int,
        account_id: Optional[int] = None,
        deadline: Optional[int] = None,
        nonce: Optional[int] = None,
    ) -> dict:
        """Build the camelCase wire payload for a cancelAllAfter request.

        Pure (no I/O). Shared by the REST sender above and the ws-exec
        transport. Same arg semantics as :meth:`cancel_all_after`.
        """
        if timeout_ms != CANCEL_ALL_AFTER_DISARM_MS and not (
            CANCEL_ALL_AFTER_MIN_TIMEOUT_MS <= timeout_ms <= CANCEL_ALL_AFTER_MAX_TIMEOUT_MS
        ):
            raise ValueError(
                f"timeout_ms must be {CANCEL_ALL_AFTER_DISARM_MS} (disarm) or within "
                f"[{CANCEL_ALL_AFTER_MIN_TIMEOUT_MS}, {CANCEL_ALL_AFTER_MAX_TIMEOUT_MS}] ms (got {timeout_ms})"
            )

        resolved_account_id = account_id if account_id is not None else self.config.account_id
        if resolved_account_id is None:
            raise ValueError("account_id is required (pass it or set in config)")
        if self._signature_generator is None:
            raise ValueError("Signature generator is required for signing")

        resolved_nonce = nonce if nonce is not None else self._get_next_nonce()
        resolved_deadline = deadline if deadline is not None else int(time.time()) + DEFAULT_DEADLINE_S

        signature = self._signature_generator.sign_cancel_all_after(
            account_id=resolved_account_id,
            timeout_ms=timeout_ms,
            nonce=resolved_nonce,
            deadline=resolved_deadline,
        )

        return {
            "accountId": resolved_account_id,
            "timeoutMs": timeout_ms,
            "signature": signature,
            "nonce": str(resolved_nonce),
            "signerWallet": self.signer_wallet_address,
            "deadline": resolved_deadline,
        }

    async def modify_order(self, params: ModifyOrderParameters) -> ModifyOrderResponse:
        """
        Modify a resting order in place on either spot or perp markets. The
        order keeps its `orderId` and, if it has one, its `clientOrderId`.
        Target by `order_id` or `client_order_id`; when `order_id` is present,
        `client_order_id` restates the resting order's non-zero client id for
        the signature, and is omitted when the resting order has none. The
        modifiable fields (`limit_px`, `qty`, `post_only`, `expires_after`, and
        `trigger_px` for trigger orders) all carry the complete post-modify
        state; the immutables (`is_buy`, `order_type`, `time_in_force`,
        `reduce_only`, `client_order_id`) must restate the resting order's
        values. `order_type` defaults to LIMIT; a STOP_LOSS/TAKE_PROFIT modify
        reprices a trigger and requires `trigger_px`. LIMIT modifies omit
        `trigger_px`.
        Both groups go into the fresh EIP-712 signature over the full
        post-modify state.
        """
        await self.refresh_trigger_market_state(params.order_type)
        payload, _nonce = self.build_modify_order_payload(params)
        return await self.orders.modify_order(ModifyOrderRequest(**payload))

    def build_modify_order_payload(self, params: ModifyOrderParameters) -> tuple[dict, int]:
        """Build the camelCase wire payload for a modifyOrder request and
        return ``(payload, nonce)``.

        Pure (no I/O). Shared by the REST sender above and the ws-exec
        transport. Signs the same 14-field `OrderDetails` envelope as
        createOrder, over the full post-modify order state.
        """
        has_order_id = params.order_id is not None
        has_client_order_id = params.client_order_id is not None
        if not has_order_id and not has_client_order_id:
            raise ValueError("Provide order_id or client_order_id")
        _reject_zero_client_order_id(params.client_order_id)
        if self.config.account_id is None:
            raise ValueError("Account ID is required for order signing")
        if self._signature_generator is None:
            raise ValueError("Signature generator is required for order signing")

        market_id = self.get_market_id_from_symbol(params.symbol)
        now_s = int(time.time())
        deadline = params.deadline if params.deadline is not None else now_s + DEFAULT_DEADLINE_S
        _reject_zero_deadline(deadline)
        order_type_int = _order_type_int(params.order_type)
        time_in_force_int = _time_in_force_int(params.time_in_force)
        is_trigger = params.order_type in (OrderType.STOP_LOSS, OrderType.TAKE_PROFIT)

        # TIF <-> expiresAfter coupling, checked against the RESTING order's
        # immutable TIF (a modify cannot change TIF — the caller passes the
        # resting order's TIF). GTC and IOC omit expiry; GTT must expire after
        # the deadline.
        expires_after = params.expires_after or 0
        if is_trigger:
            # A trigger create is never post-only or reduce-only, so a modify
            # restating either is a signature the ME must reject. Catching it
            # here matters because the nonce is minted and burnt further down:
            # sending it costs the caller a nonce for a guaranteed rejection.
            if params.post_only:
                raise ValueError("post_only on TP/SL trigger orders is not supported")
            if params.reduce_only:
                raise ValueError("reduce_only on TP/SL trigger orders is not supported yet")
            _require_trigger_expiry_coupling(params.time_in_force, expires_after, deadline, on_modify=True)
        else:
            _require_limit_expiry_coupling(params.time_in_force, expires_after, deadline)
        _require_settlement_headroom(expires_after, self._config.settlement_headroom_s, now_s)

        # Single-field PRO-438 contract: client_order_id is both the lookup key
        # when order_id is absent and the restated immutable signed into
        # OrderDetails.clientOrderId. With order_id targeting, omit it for a
        # no-tag resting order.
        signed_client_order_id = params.client_order_id or 0

        # qty and trigger_px are type-conditional and symmetric (mirrors the
        # create builders — build_create_trigger_order_payload has no qty and
        # build_create_limit_order_payload has no trigger_px on its params):
        #  - TP/SL: qty must be omitted (the signer derives the ±int256.max
        #    full-position sentinel from is_buy; dropped from the wire below)
        #    and trigger_px is REQUIRED to
        #    reprice the trigger (the ME re-validates it as positive).
        #  - LIMIT: qty is REQUIRED (signed as the total post-modify quantity) and
        #    trigger_px must be omitted — a LIMIT carries no trigger, so a stray
        #    trigger_px would sign a non-zero OrderDetails.triggerPrice (and emit
        #    triggerPx), diverging from the LIMIT create path that always signs
        #    triggerPrice 0 and omits triggerPx.
        if is_trigger:
            if params.qty is not None:
                raise ValueError("qty on TP/SL trigger orders is not supported; omit qty")
            if params.trigger_px is None:
                raise ValueError("trigger_px is required when modifying a STOP_LOSS/TAKE_PROFIT trigger order")
            # sign_order recognizes the trigger order type and replaces this
            # caller-facing zero with the direction-aware +/-int256.max sentinel.
            signed_qty = Decimal(0)
            trigger_price = Decimal(params.trigger_px)
            # A reprice moves limitPx and/or triggerPx, so the venue re-checks
            # the post-modify pair against the band exactly as at creation.
            self._require_trigger_limit_in_band(params.symbol, trigger_price, Decimal(params.limit_px))
        else:
            if params.qty is None:
                raise ValueError("qty is required when modifying a LIMIT order")
            if params.trigger_px is not None:
                raise ValueError("trigger_px on LIMIT orders is not supported; omit trigger_px")
            signed_qty = Decimal(params.qty)
            trigger_price = Decimal(0)

        # Every rejection above is unconditional, so the nonce is claimed only
        # once the request is known to be admissible.
        nonce = params.nonce if params.nonce is not None else self._get_next_nonce()

        signature = self._signature_generator.sign_order(
            account_id=self.config.account_id,
            market_id=market_id,
            exchange_id=self.config.dex_id,
            order_type=int(order_type_int),
            is_buy=params.is_buy,
            qty=signed_qty,
            limit_price=Decimal(params.limit_px),
            trigger_price=trigger_price,
            time_in_force=int(time_in_force_int),
            client_order_id=signed_client_order_id,
            reduce_only=params.reduce_only,
            expires_after=expires_after,
            nonce=nonce,
            deadline=deadline,
            post_only=params.post_only,
        )

        payload = {
            "orderId": str(params.order_id) if params.order_id is not None else None,
            "symbol": params.symbol,
            "accountId": self.config.account_id,
            # Restated immutables (full-restate): the request carries every signed
            # OrderDetails field. The ME verifies each equals the resting order
            # (else MODIFY_IMMUTABLE_MISMATCH). orderType defaults to LIMIT; a
            # trigger reprice restates STOP_LOSS/TAKE_PROFIT.
            "exchangeId": self.config.dex_id,
            "isBuy": params.is_buy,
            "orderType": params.order_type.value,
            "timeInForce": params.time_in_force.value,
            "triggerPx": params.trigger_px,
            "reduceOnly": params.reduce_only,
            "limitPx": params.limit_px,
            "qty": params.qty,
            "postOnly": params.post_only,
            "expiresAfter": expires_after,
            "signature": signature,
            "nonce": str(nonce),
            "signerWallet": self.signer_wallet_address,
            "deadline": deadline,
        }
        if params.client_order_id is not None:
            payload["clientOrderId"] = str(params.client_order_id)
        return _strip_absent_json_fields(payload), nonce

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

    async def get_order_history(
        self,
        wallet_address: Optional[str] = None,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
    ) -> OrderHistoryList:
        wallet = wallet_address or self.owner_wallet_address
        if not wallet:
            raise ValueError("No wallet address available.")
        return await self.wallet.get_wallet_order_history(
            address=wallet,
            start_time=start_time,
            end_time=end_time,
        )

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
