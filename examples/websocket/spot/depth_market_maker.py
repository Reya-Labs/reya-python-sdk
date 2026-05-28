#!/usr/bin/env python3
"""
Spot Market Maker (WebSocket Version) - Maintains realistic depth around current ETH price.

This version uses WebSocket for real-time updates instead of REST polling:
- Price updates via /v2/prices/{symbol}
- Balance updates via /v2/wallet/{address}/accountBalances
- Order changes via /v2/wallet/{address}/openOrders
- Spot executions via /v2/wallet/{address}/spotExecutions

On startup, uses REST to load initial state, then switches to WebSocket for updates.

Requirements:
- CHAIN_ID: The chain ID (1729 for mainnet, 89346162 for testnet)
- SPOT_ACCOUNT_ID_1: Your Reya SPOT account ID
- SPOT_PRIVATE_KEY_1: Your Ethereum private key
- SPOT_WALLET_ADDRESS_1: Your wallet address

Usage:
    python -m examples.websocket.spot.depth_market_maker

Press Ctrl+C to stop. All resting liquidity is cancelled during shutdown
(see the ``mass_cancel`` block at the bottom of ``main_async``). To leave
liquidity in the market on exit instead, comment that block out.
"""

from typing import Awaitable, Callable, Optional, TypeVar

import argparse
import asyncio
import logging
import os
import random
import threading
from dataclasses import dataclass, field
from decimal import ROUND_DOWN, Decimal

import aiohttp
from dotenv import load_dotenv  # pip install python-dotenv

from sdk.async_api.account_balance_update_payload import AccountBalanceUpdatePayload
from sdk.async_api.order_change_update_payload import OrderChangeUpdatePayload
from sdk.async_api.price_update_payload import PriceUpdatePayload
from sdk.async_api.subscribed_message_payload import SubscribedMessagePayload
from sdk.async_api.wallet_spot_execution_update_payload import WalletSpotExecutionUpdatePayload
from sdk.open_api.exceptions import ApiException, ServiceException
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api import ReyaTradingClient
from sdk.reya_rest_api.config import TradingConfig
from sdk.reya_rest_api.models.orders import LimitOrderParameters
from sdk.reya_websocket import ReyaSocket, WebSocketMessage

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("market_maker_ws")

# Enable verbose HTTP logging for debugging API issues
logging.getLogger("aiohttp.client").setLevel(logging.DEBUG)
logging.getLogger("urllib3").setLevel(logging.DEBUG)

# Market configuration (defaults, can be overridden via command line)
DEFAULT_SYMBOL = "WETHRUSD"  # Default spot trading pair symbol
DEFAULT_ORACLE_SYMBOL = "ETHRUSD"  # Default oracle price symbol for reference pricing
DEFAULT_MAX_SPREAD_PCT = Decimal("0.01")  # ±1% from reference price (configurable via --max-spread)
MAX_ORDER_QTY = Decimal("1")  # Maximum order quantity
NUM_LEVELS = 10  # Number of price levels on each side
REFRESH_INTERVAL = 5  # Seconds between quote adjustments
STATE_REFRESH_CYCLES = 30  # Refresh state from REST every N cycles to handle WS disconnects
MIN_BASE_BALANCE = Decimal("0.1")  # Minimum ETH balance - stop MM if below this

# HTTP statuses we consider transient and worth retrying.
_TRANSIENT_HTTP_STATUSES = {408, 425, 429, 500, 502, 503, 504}

T = TypeVar("T")


def _is_rate_limit_400(exc: "ApiException") -> bool:
    """Reya's matching engine surfaces rate-limit rejections as HTTP 400 with
    ``"rate limit"`` in the body, NOT as 429. The retry decision is body-based."""
    status = getattr(exc, "status", 0) or 0
    if status != 400:
        return False
    body = getattr(exc, "body", "") or ""
    return "rate limit" in str(body).lower()


async def with_http_retry(
    func: Callable[[], Awaitable[T]],
    *,
    op_name: str,
    is_idempotent: bool = False,
    max_retries: int = 4,
    initial_backoff: float = 0.5,
    backoff_factor: float = 2.0,
) -> T:
    """Run an async REST call with exponential-backoff retry on transient errors.

    Retries on: ServiceException (5xx), ApiException with status in 408/425/429/500/502/503/504
    or HTTP 400 with a "rate limit" body, aiohttp.ClientError, asyncio.TimeoutError, OSError.

    NON-idempotent calls (default: ``is_idempotent=False``) are NOT retried — the
    failure is re-raised on the first transient error so callers don't end up
    with duplicate orders on a 504-after-server-accept. Pass ``is_idempotent=True``
    only when the call is genuinely idempotent (a pre-computed clientOrderId
    that the server will dedupe on, ``cancel_order``, ``mass_cancel``, or any
    read). See README / CHANGELOG.

    Lets through unchanged (no retry, no wrap): ApiException with non-transient 4xx
    status, so the caller's branch logic (e.g. ``"Order not found"``,
    ``CANCEL_ORDER_OTHER_ERROR``) still runs.
    """
    backoff = initial_backoff
    last_exc: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        try:
            return await func()
        except ServiceException as e:
            last_exc = e
            status = getattr(e, "status", 0)
        except ApiException as e:
            status = getattr(e, "status", 0) or 0
            if status not in _TRANSIENT_HTTP_STATUSES and not _is_rate_limit_400(e):
                raise
            last_exc = e
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
            last_exc = e
            status = 0

        if not is_idempotent:
            # Non-idempotent calls get exactly one shot: if it fails transient,
            # the server may have still committed it (e.g. 504 after accept).
            # Re-firing would create a duplicate order.
            logger.error(
                f"{op_name}: transient error on non-idempotent call (status={status}); "
                f"refusing to retry to avoid duplicate-write — last_exc={last_exc!r}"
            )
            raise last_exc

        if attempt < max_retries:
            logger.warning(
                f"{op_name}: transient error (status={status}, {type(last_exc).__name__}: {last_exc}) "
                f"— retry {attempt + 1}/{max_retries} in {backoff:.1f}s"
            )
            await asyncio.sleep(backoff)
            backoff *= backoff_factor
        else:
            logger.error(f"{op_name}: gave up after {max_retries + 1} attempts ({last_exc})")
            raise last_exc

    # Defensive: by construction the loop above either returns from the try or
    # raises in the else branch. mypy doesn't see that — guard last_exc here.
    raise RuntimeError(f"{op_name}: with_http_retry exited loop without return or raise")


@dataclass
class OpenOrder:
    """Represents an open order with its key attributes."""

    order_id: str
    price: Decimal
    qty: Decimal
    is_buy: bool


@dataclass
class MarketParams:
    """Market parameters fetched from /spotMarketDefinitions endpoint."""

    symbol: str
    base_asset: str
    quote_asset: str
    tick_size: Decimal
    min_order_qty: Decimal
    qty_step_size: Decimal


@dataclass
class MarketMakerState:
    """Thread-safe state container for the market maker."""

    # Symbol configuration (set once on startup)
    symbol: str = DEFAULT_SYMBOL
    oracle_symbol: str = DEFAULT_ORACLE_SYMBOL
    max_spread_pct: Decimal = DEFAULT_MAX_SPREAD_PCT  # Configurable max bid-ask spread

    # Market parameters (set once on startup)
    market_params: Optional[MarketParams] = None
    account_id: Optional[int] = None
    wallet_address: Optional[str] = None

    # Dynamic state (updated via WebSocket)
    reference_price: Decimal = Decimal("0")
    base_balance: Decimal = Decimal("0")  # ETH
    quote_balance: Decimal = Decimal("0")  # RUSD
    open_orders: dict[str, OpenOrder] = field(default_factory=dict)

    # Lock for thread-safe access
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def update_price(self, price: Decimal) -> None:
        """Update reference price (thread-safe)."""
        with self._lock:
            old_price = self.reference_price
            self.reference_price = price
            if old_price != price:
                logger.debug(f"📊 Price updated: ${old_price} → ${price}")

    def update_balance(self, asset: str, balance: Decimal) -> None:
        """Update balance for an asset (thread-safe)."""
        with self._lock:
            if self.market_params and asset == self.market_params.base_asset:
                old = self.base_balance
                self.base_balance = balance
                if old != balance:
                    logger.info(f"💰 {asset} balance: {old} → {balance}")
            elif self.market_params and asset == self.market_params.quote_asset:
                old = self.quote_balance
                self.quote_balance = balance
                if old != balance:
                    logger.info(f"💰 {asset} balance: {old} → {balance}")

    def update_order(
        self, order_id: str, status: str, price: Decimal, qty: Decimal, cum_qty: Decimal, is_buy: bool
    ) -> None:
        """Update or remove an order based on status (thread-safe)."""
        with self._lock:
            remaining_qty = qty - cum_qty

            if status in ("FILLED", "CANCELLED", "REJECTED", "EXPIRED"):
                if order_id in self.open_orders:
                    del self.open_orders[order_id]
                    logger.debug(f"📋 Order {order_id} removed (status: {status})")
            else:
                self.open_orders[order_id] = OpenOrder(
                    order_id=order_id,
                    price=price,
                    qty=remaining_qty,
                    is_buy=is_buy,
                )
                logger.debug(f"📋 Order {order_id} updated: {status}, remaining={remaining_qty}")

    def log_execution(self, order_id: str, qty: str, price: str, side: str, maker_account_id: int) -> None:
        """Log a spot execution."""
        side_str = "BOUGHT" if side == "B" else "SOLD"
        logger.info(f"🔔 FILL: {side_str} {qty} @ ${price} (order {order_id}, counterparty: {maker_account_id})")

    def remove_order(self, order_id: str) -> None:
        """Remove an order from local state (thread-safe). Used when cancel fails with 'Order not found'."""
        with self._lock:
            if order_id in self.open_orders:
                del self.open_orders[order_id]
                logger.debug(f"📋 Order {order_id} removed from local state (stale)")

    def sync_orders(self, fresh_orders: dict[str, OpenOrder]) -> None:
        """Replace local orders with fresh data from REST API (thread-safe)."""
        with self._lock:
            old_count = len(self.open_orders)
            self.open_orders = fresh_orders
            new_count = len(self.open_orders)
            if old_count != new_count:
                logger.info(f"🔄 State synced: {old_count} → {new_count} orders")

    def get_snapshot(self) -> tuple[Decimal, Decimal, Decimal, list[OpenOrder], list[OpenOrder]]:
        """Get a consistent snapshot of current state (thread-safe)."""
        with self._lock:
            bids = [o for o in self.open_orders.values() if o.is_buy]
            asks = [o for o in self.open_orders.values() if not o.is_buy]
            bids.sort(key=lambda o: o.price, reverse=True)
            asks.sort(key=lambda o: o.price)
            return (
                self.reference_price,
                self.base_balance,
                self.quote_balance,
                bids,
                asks,
            )


def round_to_tick(price: Decimal, tick_size: Decimal) -> Decimal:
    """Round price down to nearest tick size."""
    return (price / tick_size).quantize(Decimal("1"), rounding=ROUND_DOWN) * tick_size


def round_to_qty_step(qty: Decimal, qty_step_size: Decimal) -> Decimal:
    """Round quantity down to nearest qty step size."""
    return (qty / qty_step_size).quantize(Decimal("1"), rounding=ROUND_DOWN) * qty_step_size


def generate_random_qty(min_qty: Decimal, max_qty: Decimal, qty_step_size: Decimal) -> str:
    """Generate a random quantity between min and max, rounded to qty step size."""
    if max_qty <= min_qty:
        return str(min_qty)

    qty_range = max_qty - min_qty
    random_offset = qty_range * Decimal(random.uniform(0.0, 1.0))  # nosec B311
    qty = round_to_qty_step(min_qty + random_offset, qty_step_size)

    qty = max(qty, min_qty)

    return str(qty)


def calculate_available_balance(
    base_balance: Decimal,
    quote_balance: Decimal,
    bids: list[OpenOrder],
    asks: list[OpenOrder],
) -> tuple[Decimal, Decimal]:
    """Calculate available balance after subtracting committed amounts."""
    # ``sum(..., start=0)`` returns ``int(0)`` on an empty sequence, breaking
    # the ``Decimal`` arithmetic that follows. Provide a ``Decimal('0')`` start.
    committed_quote = sum((o.price * o.qty for o in bids), Decimal("0"))
    committed_base = sum((o.qty for o in asks), Decimal("0"))

    return (
        max(Decimal("0"), base_balance - committed_base),
        max(Decimal("0"), quote_balance - committed_quote),
    )


def generate_quote_prices(
    reference: Decimal,
    max_deviation_pct: Decimal,
    num_levels: int,
    tick_size: Decimal,
) -> tuple[list[str], list[str]]:
    """Generate bid and ask prices around the reference price."""
    min_price = reference * (1 - max_deviation_pct)
    max_price = reference * (1 + max_deviation_pct)

    bid_range = reference - min_price
    bids = []
    for i in range(num_levels):
        offset = bid_range * Decimal(random.uniform(0.1, 1.0)) * Decimal(i + 1) / Decimal(num_levels)  # nosec B311
        price = round_to_tick(reference - offset, tick_size)
        if price >= min_price:
            bids.append(str(price))
    bids = sorted(set(bids), key=Decimal, reverse=True)[:num_levels]

    ask_range = max_price - reference
    asks = []
    for i in range(num_levels):
        offset = ask_range * Decimal(random.uniform(0.1, 1.0)) * Decimal(i + 1) / Decimal(num_levels)  # nosec B311
        price = round_to_tick(reference + offset, tick_size)
        if price <= max_price:
            asks.append(str(price))
    asks = sorted(set(asks), key=Decimal)[:num_levels]

    return bids, asks


def generate_single_price(
    is_buy: bool,
    reference: Decimal,
    max_deviation_pct: Decimal,
    tick_size: Decimal,
    best_bid: Decimal | None,
    best_ask: Decimal | None,
) -> Decimal:
    """Generate a single random price that doesn't cross the spread."""
    min_price = reference * (1 - max_deviation_pct)
    max_price = reference * (1 + max_deviation_pct)

    if is_buy:
        lower_bound = min_price
        upper_bound = reference - tick_size
        if best_ask is not None:
            upper_bound = min(upper_bound, best_ask - tick_size)
    else:
        lower_bound = reference + tick_size
        upper_bound = max_price
        if best_bid is not None:
            lower_bound = max(lower_bound, best_bid + tick_size)

    if lower_bound >= upper_bound:
        if is_buy:
            return round_to_tick(min_price, tick_size)
        else:
            return round_to_tick(max_price, tick_size)

    price_range = upper_bound - lower_bound
    random_offset = price_range * Decimal(random.uniform(0.0, 1.0))  # nosec B311
    return round_to_tick(lower_bound + random_offset, tick_size)


class WebSocketHandler:
    """Handles WebSocket events and updates market maker state."""

    def __init__(self, state: MarketMakerState):
        self.state = state
        self._connected = threading.Event()

    def wait_for_connection(self, timeout: float = 10.0) -> bool:
        """Wait for WebSocket to connect and subscribe."""
        return self._connected.wait(timeout)

    def on_open(self, ws: ReyaSocket) -> None:
        """Handle WebSocket connection open - subscribe to channels."""
        logger.info("🔌 WebSocket connected, subscribing to channels...")

        wallet = self.state.wallet_address
        if not wallet:
            logger.error("No wallet address set in state")
            return

        # Subscribe to price updates for oracle symbol
        ws.prices.price(self.state.oracle_symbol).subscribe()

        # Subscribe to wallet channels
        ws.wallet.balances(wallet).subscribe()
        ws.wallet.order_changes(wallet).subscribe()
        ws.wallet.spot_executions(wallet).subscribe()

        logger.info(f"   ✅ Subscribed to /v2/prices/{self.state.oracle_symbol}")
        logger.info(f"   ✅ Subscribed to /v2/wallet/{wallet}/accountBalances")
        logger.info(f"   ✅ Subscribed to /v2/wallet/{wallet}/openOrders")
        logger.info(f"   ✅ Subscribed to /v2/wallet/{wallet}/spotExecutions")

    def on_message(self, _ws: ReyaSocket, message: WebSocketMessage) -> None:
        """Handle incoming WebSocket messages."""

        # Handle subscription confirmations
        if isinstance(message, SubscribedMessagePayload):
            logger.debug(f"Subscribed to {message.channel}")
            # Mark as connected after we get subscription confirmations
            self._connected.set()
            return

        # Handle price updates
        if isinstance(message, PriceUpdatePayload):
            if message.data and message.data.oracle_price:
                price = Decimal(message.data.oracle_price)
                if price > 0:
                    if self.state.market_params:
                        price = round_to_tick(price, self.state.market_params.tick_size)
                    self.state.update_price(price)
            return

        # Handle balance updates
        if isinstance(message, AccountBalanceUpdatePayload):
            for balance in message.data:
                if balance.account_id == self.state.account_id:
                    self.state.update_balance(balance.asset, Decimal(balance.real_balance))
            return

        # Handle order changes
        if isinstance(message, OrderChangeUpdatePayload):
            for order in message.data:
                if order.symbol != self.state.symbol:
                    continue

                qty = Decimal(order.qty) if order.qty else Decimal("0")
                cum_qty = Decimal(order.cum_qty) if order.cum_qty else Decimal("0")
                is_buy = order.side.value == "B"

                self.state.update_order(
                    order_id=order.order_id,
                    status=order.status.value,
                    price=Decimal(order.limit_px),
                    qty=qty,
                    cum_qty=cum_qty,
                    is_buy=is_buy,
                )
            return

        # Handle spot executions
        if isinstance(message, WalletSpotExecutionUpdatePayload):
            for execution in message.data:
                if execution.symbol != self.state.symbol:
                    continue
                if execution.order_id is None:
                    continue
                self.state.log_execution(
                    order_id=execution.order_id,
                    qty=execution.qty,
                    price=execution.price,
                    side=execution.side.value,
                    maker_account_id=execution.maker_account_id,
                )
            return

    def on_error(self, _ws: ReyaSocket, error: Exception) -> None:
        """Handle WebSocket errors."""
        logger.error(f"WebSocket error: {error}")

    def on_close(self, _ws: ReyaSocket, close_status_code: int, close_msg: str) -> None:
        """Handle WebSocket close."""
        logger.warning(f"WebSocket closed: {close_status_code} - {close_msg}")
        self._connected.clear()


async def fetch_market_definition(client: ReyaTradingClient, symbol: str) -> MarketParams:
    """Fetch market definition from /spotMarketDefinitions endpoint."""
    spot_definitions = await client.reference.get_spot_market_definitions()

    for market in spot_definitions:
        if market.symbol == symbol:
            return MarketParams(
                symbol=market.symbol,
                base_asset=market.base_asset,
                quote_asset=market.quote_asset,
                tick_size=Decimal(market.tick_size),
                min_order_qty=Decimal(market.min_order_qty),
                qty_step_size=Decimal(market.qty_step_size),
            )

    raise RuntimeError(f"Market definition not found for symbol: {symbol}")


async def fetch_initial_state(
    client: ReyaTradingClient,
    state: MarketMakerState,
) -> None:
    """Fetch initial state via REST before WebSocket takes over."""
    market_params = state.market_params
    account_id = state.account_id

    if not market_params or not account_id:
        raise RuntimeError("Market params and account_id must be set before fetching initial state")

    # Fetch oracle price
    logger.info(f"   Fetching oracle price for {state.oracle_symbol}...")
    try:
        price_info = await client.markets.get_price(state.oracle_symbol)
        # Pydantic models don't define ``__bool__``, so a bare ``price_info``
        # check is unreachable-False; the real signal is the field value.
        if price_info.oracle_price and Decimal(price_info.oracle_price) > 0:
            state.reference_price = round_to_tick(Decimal(price_info.oracle_price), market_params.tick_size)
    except Exception as e:
        logger.warning(f"   Failed to fetch oracle price for {state.oracle_symbol}: {e}")

    if state.reference_price == Decimal("0"):
        logger.warning(
            f"   No oracle price available for {state.oracle_symbol} at startup. "
            "Market making remains paused until the oracle publishes a price; "
            "the first cycle that sees a non-zero reference_price will begin quoting."
        )

    # Fetch account balances
    logger.info("   Fetching account balances...")
    balances = await client.get_account_balances()
    for balance in balances:
        if balance.account_id == account_id:
            if balance.asset == market_params.base_asset:
                state.base_balance = Decimal(balance.real_balance)
            elif balance.asset == market_params.quote_asset:
                state.quote_balance = Decimal(balance.real_balance)

    # Fetch open orders
    logger.info("   Fetching open orders...")
    open_orders = await client.get_open_orders()
    for order in open_orders:
        if order.symbol != state.symbol:
            continue

        qty = Decimal(order.qty) if order.qty else Decimal("0")
        cum_qty = Decimal(order.cum_qty) if order.cum_qty else Decimal("0")
        remaining_qty = qty - cum_qty
        is_buy = order.side.value == "B"

        state.open_orders[order.order_id] = OpenOrder(
            order_id=order.order_id,
            price=Decimal(order.limit_px),
            qty=remaining_qty,
            is_buy=is_buy,
        )


async def refresh_state_from_rest(
    client: ReyaTradingClient,
    state: MarketMakerState,
) -> None:
    """Refresh order state from REST API to handle WebSocket disconnections or stale state."""
    try:
        open_orders = await client.get_open_orders()
        fresh_orders: dict[str, OpenOrder] = {}

        for order in open_orders:
            if order.symbol != state.symbol:
                continue

            qty = Decimal(order.qty) if order.qty else Decimal("0")
            cum_qty = Decimal(order.cum_qty) if order.cum_qty else Decimal("0")
            remaining_qty = qty - cum_qty
            is_buy = order.side.value == "B"

            fresh_orders[order.order_id] = OpenOrder(
                order_id=order.order_id,
                price=Decimal(order.limit_px),
                qty=remaining_qty,
                is_buy=is_buy,
            )

        state.sync_orders(fresh_orders)
    except (OSError, RuntimeError, ApiException) as e:
        logger.warning(f"Failed to refresh state from REST: {e}")


async def place_single_order(
    client: ReyaTradingClient,
    symbol: str,
    price: str,
    is_buy: bool,
    market_params: MarketParams,
    available_balance: Decimal,
) -> tuple[bool, Decimal]:
    """Place a single order, attempting one downshift to ``min_order_qty`` on a
    balance error. Returns ``(success, qty_used)``.

    Single create-order attempt at each qty: never re-fire the same lambda,
    since each invocation of ``client.create_limit_order`` allocates a fresh
    nonce + EIP-712 signature and the server has no way to dedupe. The MM
    accepts a worse fill ratio over the risk of duplicate resting orders.
    """
    price_decimal = Decimal(price)
    side = "bid" if is_buy else "ask"

    # Calculate max affordable quantity based on local tracking
    if is_buy:
        max_affordable_qty = available_balance / price_decimal if price_decimal > 0 else Decimal("0")
    else:
        max_affordable_qty = available_balance

    max_qty = min(MAX_ORDER_QTY, max_affordable_qty)

    # Determine initial quantity to try
    if max_qty >= market_params.min_order_qty:
        qty = generate_random_qty(market_params.min_order_qty, max_qty, market_params.qty_step_size)
    else:
        # Local balance tracking says insufficient, but still try with min qty
        # The actual on-chain balance might have more available.
        qty = str(market_params.min_order_qty)
        logger.debug(f"   Local balance low, trying {side} @ ${price} with min qty={qty}")

    # Try once at the chosen qty; if a balance error comes back, try ONCE more
    # at min_qty. That's the cap — no inner retry on create-order.
    for is_downshift in (False, True):
        if is_downshift:
            qty = str(market_params.min_order_qty)
            logger.debug(f"   Retrying {side} @ ${price} with min qty={qty}")
        # Bind qty into the lambda so a future refactor of with_http_retry
        # that defers the callable can't accidentally read a mutated value.
        try:
            await client.create_limit_order(
                LimitOrderParameters(
                    symbol=symbol,
                    is_buy=is_buy,
                    limit_px=price,
                    qty=qty,
                    time_in_force=TimeInForce.GTC,
                )
            )
            logger.info(f"   Adding {side} @ ${price} qty={qty}")
            qty_used = price_decimal * Decimal(qty) if is_buy else Decimal(qty)
            return True, qty_used
        except (OSError, RuntimeError, ApiException) as e:
            error_str = str(e).lower()
            if "insufficient" in error_str or "balance" in error_str or "margin" in error_str:
                if not is_downshift:
                    continue
                logger.warning(f"   Skipping {side} @ ${price} — insufficient balance (confirmed by API)")
            else:
                logger.warning(f"Failed to place {side} @ ${price}: {e}")
            return False, Decimal("0")

    return False, Decimal("0")


async def place_orders(
    client: ReyaTradingClient,
    symbol: str,
    bids: list[str],
    asks: list[str],
    market_params: MarketParams,
    available_base: Decimal,
    available_quote: Decimal,
) -> int:
    """Place bid and ask orders with random quantities, respecting available balance."""
    order_count = 0
    remaining_quote = available_quote
    remaining_base = available_base

    for price in bids:
        success, qty_used = await place_single_order(
            client,
            symbol,
            price,
            is_buy=True,
            market_params=market_params,
            available_balance=remaining_quote,
        )
        if success:
            order_count += 1
            remaining_quote -= qty_used

    for price in asks:
        success, qty_used = await place_single_order(
            client,
            symbol,
            price,
            is_buy=False,
            market_params=market_params,
            available_balance=remaining_base,
        )
        if success:
            order_count += 1
            remaining_base -= qty_used

    return order_count


def find_out_of_range_orders(
    bids: list[OpenOrder],
    asks: list[OpenOrder],
    reference_price: Decimal,
    max_spread_pct: Decimal,
) -> list[OpenOrder]:
    """Find orders that are outside the allowed price range."""
    min_price = reference_price * (1 - max_spread_pct)
    max_price = reference_price * (1 + max_spread_pct)

    out_of_range = []
    for order in bids + asks:
        if order.price < min_price or order.price > max_price:
            out_of_range.append(order)

    return out_of_range


async def cancel_and_replace_order(
    client: ReyaTradingClient,
    symbol: str,
    account_id: int,
    order: OpenOrder,
    reference_price: Decimal,
    market_params: MarketParams,
    available_base: Decimal,
    available_quote: Decimal,
    remaining_bids: list[OpenOrder],
    remaining_asks: list[OpenOrder],
    cycle: int,
    state: MarketMakerState,
    reason: str = "",
) -> bool:
    """Cancel a specific order and replace it with a new one at a valid price.

    If order placement fails due to insufficient balance, retries with minimum quantity.
    """
    side = "bid" if order.is_buy else "ask"

    best_bid = remaining_bids[0].price if remaining_bids else None
    best_ask = remaining_asks[0].price if remaining_asks else None

    new_price = generate_single_price(
        is_buy=order.is_buy,
        reference=reference_price,
        max_deviation_pct=state.max_spread_pct,
        tick_size=market_params.tick_size,
        best_bid=best_bid,
        best_ask=best_ask,
    )

    # Calculate available balance after cancelling this order
    if order.is_buy:
        freed_quote = order.price * order.qty
        total_available_quote = available_quote + freed_quote
        max_affordable_qty = total_available_quote / new_price if new_price > 0 else Decimal("0")
        max_qty = min(MAX_ORDER_QTY, max_affordable_qty)
    else:
        freed_base = order.qty
        total_available_base = available_base + freed_base
        max_qty = min(MAX_ORDER_QTY, total_available_base)

    if max_qty < market_params.min_order_qty:
        logger.warning(f"[{cycle:04d}] Skipping {side} replacement - insufficient balance")
        try:
            await with_http_retry(
                lambda oid=order.order_id: client.cancel_order(
                    order_id=oid,
                    symbol=symbol,
                    account_id=account_id,
                ),
                op_name=f"cancel_order {side} @ ${order.price}",
                is_idempotent=True,
            )
            logger.info(f"[{cycle:04d}] Cancelled {side} @ ${order.price} (no replacement - low balance)")
        except (OSError, RuntimeError, ApiException) as e:
            error_str = str(e)
            if "Order not found" in error_str or "CANCEL_ORDER_OTHER_ERROR" in error_str:
                state.remove_order(order.order_id)
                logger.info(f"[{cycle:04d}] Removed stale {side} @ ${order.price} from local state")
            else:
                logger.warning(f"[{cycle:04d}] Failed to cancel {side} @ ${order.price}: {e}")
        return False

    new_qty = generate_random_qty(market_params.min_order_qty, max_qty, market_params.qty_step_size)

    # Cancel the existing order first. cancel_order is idempotent on the server
    # (second cancel returns "Order not found"), so retrying transient errors
    # is safe.
    try:
        await with_http_retry(
            lambda oid=order.order_id: client.cancel_order(
                order_id=oid,
                symbol=symbol,
                account_id=account_id,
            ),
            op_name=f"cancel_order {side} @ ${order.price}",
            is_idempotent=True,
        )
        reason_str = f" ({reason})" if reason else ""
        logger.info(
            f"[{cycle:04d}] Cancelling {side} @ ${order.price}{reason_str} "
            f"→ Adding new {side} @ ${new_price} qty={new_qty}"
        )
    except (OSError, RuntimeError, ApiException) as e:
        error_str = str(e)
        if "Order not found" in error_str or "CANCEL_ORDER_OTHER_ERROR" in error_str:
            state.remove_order(order.order_id)
            logger.info(f"[{cycle:04d}] Removed stale {side} @ ${order.price} from local state")
        else:
            logger.warning(f"[{cycle:04d}] Failed to cancel {side} @ ${order.price}: {e}")
        return False

    await asyncio.sleep(0.1)

    # Single create-order attempt at the chosen qty; one downshift to
    # min_qty on a balance error. No inner retry on create — the new nonce
    # per call would otherwise cause duplicate resting orders.
    qty_to_use = new_qty
    for is_downshift in (False, True):
        if is_downshift:
            qty_to_use = str(market_params.min_order_qty)
            logger.debug(f"[{cycle:04d}] Retrying {side} @ ${new_price} with min qty={qty_to_use}")
        try:
            await client.create_limit_order(
                LimitOrderParameters(
                    symbol=symbol,
                    is_buy=order.is_buy,
                    limit_px=str(new_price),
                    qty=qty_to_use,
                    time_in_force=TimeInForce.GTC,
                )
            )
            return True
        except (OSError, RuntimeError, ApiException) as e:
            error_str = str(e).lower()
            if "insufficient" in error_str or "balance" in error_str or "margin" in error_str:
                if not is_downshift:
                    continue
            logger.warning(f"[{cycle:04d}] Failed to place new {side} @ ${new_price}: {e}")
            return False

    return False


async def adjust_orders(
    client: ReyaTradingClient,
    state: MarketMakerState,
    cycle: int,
) -> None:
    """Adjust orders based on current state from WebSocket updates."""
    market_params = state.market_params
    account_id = state.account_id

    if not market_params or not account_id:
        return

    # Get consistent snapshot of current state
    reference_price, base_balance, quote_balance, bids, asks = state.get_snapshot()

    if reference_price == Decimal("0"):
        # No reference price means the oracle hasn't published yet OR has dropped
        # out mid-run. Quoting on a synthetic fallback would expose the wallet to
        # being swept at a price unrelated to fair value (e.g. $0.10 for ETH if
        # we naively used a tiny constant). Halt this cycle instead; the next
        # cycle will re-check the oracle.
        logger.warning(
            f"[{cycle:04d}] No reference price from oracle for {state.symbol} — skipping cycle. "
            "Market making remains paused until the oracle publishes a price."
        )
        return

    # Calculate available balance
    available_base, available_quote = calculate_available_balance(base_balance, quote_balance, bids, asks)

    min_price = reference_price * (1 - state.max_spread_pct)
    max_price = reference_price * (1 + state.max_spread_pct)

    # Check for out-of-range orders first
    out_of_range = find_out_of_range_orders(bids, asks, reference_price, state.max_spread_pct)

    if out_of_range:
        logger.info(f"[{cycle:04d}] 📊 Oracle price: ${reference_price} | Range: ${min_price:.2f} - ${max_price:.2f}")
        logger.info(f"[{cycle:04d}] ⚠️  Found {len(out_of_range)} order(s) outside range, adjusting...")

        for order in out_of_range:
            remaining_bids = [o for o in bids if o.order_id != order.order_id]
            remaining_asks = [o for o in asks if o.order_id != order.order_id]

            await cancel_and_replace_order(
                client=client,
                symbol=state.symbol,
                account_id=account_id,
                order=order,
                reference_price=reference_price,
                market_params=market_params,
                available_base=available_base,
                available_quote=available_quote,
                remaining_bids=remaining_bids,
                remaining_asks=remaining_asks,
                cycle=cycle,
                state=state,
                reason="out of range",
            )

            if order.is_buy:
                bids = [o for o in bids if o.order_id != order.order_id]
            else:
                asks = [o for o in asks if o.order_id != order.order_id]

        return

    # Normal operation: pick one random order to adjust
    # First pick a side (50-50), then pick a random order from that side
    # This ensures balanced adjustment regardless of current order counts
    if not bids and not asks:
        logger.warning(f"[{cycle:04d}] No open orders to adjust")
        return

    # Determine which side to adjust (50-50 if both sides have orders)
    if bids and asks:
        adjust_bid_side = random.choice([True, False])  # nosec B311
    elif bids:
        adjust_bid_side = True
    else:
        adjust_bid_side = False

    if adjust_bid_side:
        order_to_cancel = random.choice(bids)  # nosec B311
    else:
        order_to_cancel = random.choice(asks)  # nosec B311

    remaining_bids = [o for o in bids if o.order_id != order_to_cancel.order_id]
    remaining_asks = [o for o in asks if o.order_id != order_to_cancel.order_id]

    await cancel_and_replace_order(
        client=client,
        symbol=state.symbol,
        account_id=account_id,
        order=order_to_cancel,
        reference_price=reference_price,
        market_params=market_params,
        available_base=available_base,
        available_quote=available_quote,
        remaining_bids=remaining_bids,
        remaining_asks=remaining_asks,
        cycle=cycle,
        state=state,
    )


async def main(symbol: str, oracle_symbol: str, max_spread_pct: Decimal):
    load_dotenv()

    logger.info("=" * 60)
    logger.info(f"🚀 SPOT Market Maker (WebSocket) for {symbol}")
    logger.info("=" * 60)

    # Initialize state with symbol configuration
    state = MarketMakerState(symbol=symbol, oracle_symbol=oracle_symbol, max_spread_pct=max_spread_pct)

    # Create config for SPOT account (uses SPOT_* env vars instead of PERP_*)
    spot_config = TradingConfig.from_env_spot(account_number=1)

    async with ReyaTradingClient(config=spot_config) as client:

        await client.start()

        account_id = spot_config.account_id
        wallet_address = spot_config.owner_wallet_address

        if not account_id:
            raise ValueError("SPOT_ACCOUNT_ID_1 environment variable is required")
        if not wallet_address:
            raise ValueError("SPOT_WALLET_ADDRESS_1 environment variable is required")

        # Set up state with account info
        state.account_id = account_id
        state.wallet_address = wallet_address

        # Fetch market definition (REST - one time)
        logger.info(f"   Fetching market definition for {symbol}...")
        state.market_params = await fetch_market_definition(client, symbol)
        market_params = state.market_params

        # Fetch initial state via REST
        await fetch_initial_state(client, state)

        min_price = state.reference_price * (1 - state.max_spread_pct)
        max_price = state.reference_price * (1 + state.max_spread_pct)

        logger.info(f"   Reference Price: ${state.reference_price} (from {oracle_symbol} oracle)")
        logger.info(f"   Price Range:     ${min_price:.2f} - ${max_price:.2f} (±{state.max_spread_pct * 100}%)")
        logger.info(f"   Tick Size:       {market_params.tick_size}")
        logger.info(f"   Min Order Qty:   {market_params.min_order_qty}")
        logger.info(f"   Max Order Qty:   {MAX_ORDER_QTY}")
        logger.info(f"   Qty Step Size:   {market_params.qty_step_size}")
        logger.info(f"   {market_params.base_asset} Balance: {state.base_balance}")
        logger.info(f"   {market_params.quote_asset} Balance: {state.quote_balance}")
        logger.info(f"   Open Orders:     {len(state.open_orders)}")
        logger.info(f"   Levels:          {NUM_LEVELS} bids / {NUM_LEVELS} asks")
        logger.info(f"   Refresh:         Every {REFRESH_INTERVAL}s")
        logger.info(f"   Account ID:      {account_id}")
        logger.info("   Press Ctrl+C to stop")
        logger.info("%s\n", "=" * 60)

        # Set up WebSocket
        ws_url = os.environ.get("REYA_WS_URL", "wss://ws.reya.xyz/")
        ws_handler = WebSocketHandler(state)

        websocket = ReyaSocket(
            url=ws_url,
            on_open=ws_handler.on_open,
            on_message=ws_handler.on_message,
            on_error=ws_handler.on_error,
            on_close=ws_handler.on_close,
        )

        # Connect WebSocket in background thread
        logger.info("🔌 Connecting WebSocket...")
        websocket.connect()

        # Wait for WebSocket to connect and subscribe
        if not ws_handler.wait_for_connection(timeout=10.0):
            logger.warning("WebSocket connection timeout, continuing with REST fallback")

        # Clean up any existing orders from previous runs. Wrap in
        # ``with_http_retry`` so a transient 504 on startup doesn't hard-fail
        # the MM before the main loop even begins. Use ``sync_orders`` (which
        # holds the state lock) instead of touching ``state.open_orders``
        # directly — the WebSocket daemon thread can fire ``on_message``
        # mutations concurrently.
        logger.info("Cleaning up existing orders...")
        await with_http_retry(
            lambda: client.mass_cancel(symbol=symbol, account_id=account_id),
            op_name="mass_cancel (startup)",
            is_idempotent=True,
        )
        await asyncio.sleep(0.2)
        state.sync_orders({})
        logger.info("✅ Order book cleaned\n")

        try:
            # Initial setup: place all orders
            logger.info("Placing initial liquidity...")
            available_base, available_quote = calculate_available_balance(
                state.base_balance, state.quote_balance, [], []
            )
            bid_prices, ask_prices = generate_quote_prices(
                state.reference_price, state.max_spread_pct, NUM_LEVELS, market_params.tick_size
            )
            order_count = await place_orders(
                client, symbol, bid_prices, ask_prices, market_params, available_base, available_quote
            )

            bid_str = ", ".join(f"${b}" for b in bid_prices)
            ask_str = ", ".join(f"${a}" for a in ask_prices)
            logger.info(f"✅ Initial setup complete: {order_count} orders")
            logger.info(f"   Bids: {bid_str}")
            logger.info(f"   Asks: {ask_str}\n")

            # Main loop: adjust orders based on WebSocket state
            cycle = 0
            while True:
                await asyncio.sleep(REFRESH_INTERVAL)
                cycle += 1

                # Check for low balance - stop MM if ETH balance is too low
                if state.base_balance < MIN_BASE_BALANCE:
                    logger.warning(
                        f"[{cycle:04d}] ⚠️  ETH balance ({state.base_balance}) below minimum ({MIN_BASE_BALANCE})"
                    )
                    logger.warning(f"[{cycle:04d}] 🛑 Stopping market maker due to low balance...")
                    break

                # Periodically refresh state from REST to handle WS disconnections
                if cycle % STATE_REFRESH_CYCLES == 0:
                    logger.info(f"[{cycle:04d}] 🔄 Refreshing state from REST API...")
                    await refresh_state_from_rest(client, state)

                # State is automatically updated via WebSocket
                # Just run the adjustment logic
                await adjust_orders(client, state, cycle)

        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            logger.info("\n🛑 Shutting down...")

            # Close WebSocket
            logger.info("Closing WebSocket...")
            websocket.close()

            # NOTE: Cancelling all liquidity on shutdown.
            # Comment out the block below and uncomment the last line to leave liquidity in market on exit.
            logger.info("Cancelling all orders...")
            try:
                await with_http_retry(
                    lambda: client.mass_cancel(symbol=symbol, account_id=account_id),
                    op_name="mass_cancel (cleanup)",
                    is_idempotent=True,
                )
                logger.info("✅ All orders cancelled")
            except (OSError, RuntimeError, ApiException) as e:
                logger.warning(f"Cleanup failed: {e}")
            # logger.info("✅ Market maker stopped (liquidity left in market)")


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Spot Market Maker - Maintains realistic depth around current price")
    parser.add_argument(
        "--symbol", type=str, default=DEFAULT_SYMBOL, help=f"Spot trading pair symbol (default: {DEFAULT_SYMBOL})"
    )
    parser.add_argument(
        "--oracle-symbol",
        type=str,
        default=DEFAULT_ORACLE_SYMBOL,
        help=f"Oracle price symbol for reference pricing (default: {DEFAULT_ORACLE_SYMBOL})",
    )
    parser.add_argument(
        "--max-spread",
        type=float,
        default=float(DEFAULT_MAX_SPREAD_PCT),
        help=f"Maximum bid-ask spread as decimal (e.g., 0.01 for 1%%, default: {DEFAULT_MAX_SPREAD_PCT})",
    )
    return parser.parse_args()


if __name__ == "__main__":
    try:
        args = parse_args()
        asyncio.run(
            main(symbol=args.symbol, oracle_symbol=args.oracle_symbol, max_spread_pct=Decimal(str(args.max_spread)))
        )
    except KeyboardInterrupt:
        pass
