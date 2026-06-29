#!/usr/bin/env python3
"""
Perp Market Maker (WebSocket Version) - Maintains realistic depth around current ETH price.

Port of ``examples/websocket/spot/depth_market_maker.py`` for perp markets.
Same architecture: REST bootstrap → WebSocket-driven adjustments → mass cancel on
shutdown. Adaptations vs the spot bot:

- Config: ``TradingConfig.from_env()`` (PERP_* env vars), not ``from_env_spot``.
- Market def: ``/perpMarketDefinitions`` (no base/quote token split — perps settle in
  rUSD; max_leverage drives margin sizing instead of token balances).
- Balance: tracks rUSD collateral only. "Available budget" is a fraction of
  rUSD; per-order required margin is approximated as ``price * qty /
  max_leverage``. Conservative on purpose — this is a depth source, not an
  alpha generator.
- WS executions: ``ws.wallet.perp_executions(wallet)`` instead of
  ``spot_executions``.

Primary motivation: devnet1 has no resident MM, so the perp orderbook for
ETHRUSDPERP is empty almost all the time. The candle task only emits when
both bid and ask are present (no mark-price fallback by design — see
``packages/api/src/tasks/oracle-updates/candles.task.ts:43`` in the off-chain
monorepo), so no candles get written, and the SDK suite's ``test_candles``
returns empty arrays. This bot maintains a thin two-sided depth ladder so
the candle service has continuous mid prices to record.

Requirements:
- CHAIN_ID: The chain ID (1729 for mainnet, 89346162 for testnet)
- PERP_ACCOUNT_ID_1: Your Reya PERP account ID
- PERP_PRIVATE_KEY_1: Your Ethereum private key
- PERP_WALLET_ADDRESS_1: Your wallet address

Usage:
    python -m examples.websocket.perps.depth_market_maker

Press Ctrl+C to stop (will mass-cancel all orders on exit).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import random
import threading
import time
from dataclasses import dataclass, field
from decimal import ROUND_DOWN, Decimal

from dotenv import load_dotenv  # pip install python-dotenv

from sdk.async_api.account_balance_update_payload import AccountBalanceUpdatePayload
from sdk.async_api.order_change_update_payload import OrderChangeUpdatePayload
from sdk.async_api.price_update_payload import PriceUpdatePayload
from sdk.async_api.subscribed_message_payload import SubscribedMessagePayload
from sdk.async_api.wallet_perp_execution_update_payload import WalletPerpExecutionUpdatePayload
from sdk.open_api.exceptions import ApiException
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api import ReyaTradingClient
from sdk.reya_rest_api.config import TradingConfig
from sdk.reya_rest_api.models.orders import LimitOrderParameters
from sdk.reya_websocket import ReyaSocket, WebSocketMessage

# Exceptions worth swallowing inside the MM loop. We want the bot to stay
# alive on transient REST hiccups (network blips → OSError) and SDK-side
# 4xx/5xx responses (ApiException + subclasses like BadRequestException) —
# the most common one in perp MM is "Order not found" when our cancel
# races a fill or expiry. Anything outside this set should propagate so we
# notice real bugs.
RECOVERABLE_EXC: tuple = (OSError, RuntimeError, ApiException)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("perp_market_maker_ws")

# Market configuration (defaults, can be overridden via command line).
# Defaults assume the standard devnet1 / cronos / mainnet ETH perp listing.
# Default to the perp symbol as its own oracle reference. /v2/prices/{symbol}
# returns `oraclePrice` for perp symbols on every env, whereas spot oracle
# pairs (e.g. "ETHRUSD") are not registered on perp-only envs like devnet1.
# Override with --oracle-symbol if you want to peg to a different reference.
DEFAULT_SYMBOL = "ETHRUSDPERP"
DEFAULT_ORACLE_SYMBOL = "ETHRUSDPERP"
DEFAULT_MAX_SPREAD_PCT = Decimal("0.01")  # ±1% from reference price
NUM_LEVELS = 5  # bids/asks per side — kept low for a low-volume devnet env
REFRESH_INTERVAL = 5  # seconds between quote adjustments
STATE_REFRESH_CYCLES = 30  # refresh state from REST every N cycles
MIN_COLLATERAL = Decimal("100")  # halt MM if rUSD collateral falls below this

# Fraction of rUSD collateral budgeted across all open orders. The remainder
# is reserve for unexpected fills + margin drift. 0.30 is conservative; bump
# higher if the book stays thin even with collateral headroom.
COLLATERAL_BUDGET_FRACTION = Decimal("0.30")

# Per-order qty cap (small on purpose — the goal is presence, not size).
MAX_ORDER_QTY = Decimal("0.01")

# Settle asset on Reya is rUSD across all envs at the time of writing.
COLLATERAL_ASSET = "RUSD"

# Resting depth is posted as GTT (Good-Till-Time): it rests like GTC but the
# matching engine auto-reaps it at `expires_after`, so a stale quote is cleaned
# up if a replace cycle is missed. 10 minutes is well above any single cycle's
# batch of placements + WS round-trip slack. (A true GTC would rest forever
# until explicitly cancelled — `expires_after=0`.)
GTT_LIFETIME_S = 60 * 10


@dataclass
class OpenOrder:
    """Represents an open order with its key attributes."""

    order_id: str
    price: Decimal
    qty: Decimal
    is_buy: bool


@dataclass
class MarketParams:
    """Subset of perp ``/perpMarketDefinitions`` we actually use."""

    symbol: str
    tick_size: Decimal
    min_order_qty: Decimal
    qty_step_size: Decimal
    # Max leverage drives per-order margin sizing — see
    # ``required_margin`` below for the formula.
    max_leverage: int


@dataclass
class MarketMakerState:
    """Thread-safe state container for the market maker."""

    symbol: str = DEFAULT_SYMBOL
    oracle_symbol: str = DEFAULT_ORACLE_SYMBOL
    max_spread_pct: Decimal = DEFAULT_MAX_SPREAD_PCT

    market_params: MarketParams | None = None
    account_id: int | None = None
    wallet_address: str | None = None

    # Dynamic state (updated via WebSocket)
    reference_price: Decimal = Decimal("0")
    collateral_balance: Decimal = Decimal("0")  # rUSD
    open_orders: dict[str, OpenOrder] = field(default_factory=dict)

    _lock: threading.Lock = field(default_factory=threading.Lock)

    def update_price(self, price: Decimal) -> None:
        with self._lock:
            old = self.reference_price
            self.reference_price = price
            if old != price:
                logger.debug(f"📊 Price updated: ${old} → ${price}")

    def update_collateral(self, balance: Decimal) -> None:
        with self._lock:
            old = self.collateral_balance
            self.collateral_balance = balance
            if old != balance:
                logger.info(f"💰 {COLLATERAL_ASSET} collateral: {old} → {balance}")

    def update_order(
        self, order_id: str, status: str, price: Decimal, qty: Decimal, cum_qty: Decimal, is_buy: bool
    ) -> None:
        with self._lock:
            remaining_qty = qty - cum_qty
            if status in ("FILLED", "CANCELLED", "REJECTED", "EXPIRED"):
                if order_id in self.open_orders:
                    del self.open_orders[order_id]
                    logger.debug(f"📋 Order {order_id} removed (status: {status})")
            else:
                self.open_orders[order_id] = OpenOrder(order_id=order_id, price=price, qty=remaining_qty, is_buy=is_buy)
                logger.debug(f"📋 Order {order_id} updated: {status}, remaining={remaining_qty}")

    def log_execution(self, order_id: str, qty: str, price: str, side: str) -> None:
        side_str = "BOUGHT" if side == "B" else "SOLD"
        logger.info(f"🔔 FILL: {side_str} {qty} @ ${price} (order {order_id})")

    def remove_order(self, order_id: str) -> None:
        """Drop an order from local state (used when cancel races a fill/expiry)."""
        with self._lock:
            if order_id in self.open_orders:
                del self.open_orders[order_id]

    def sync_orders(self, fresh_orders: dict[str, OpenOrder]) -> None:
        with self._lock:
            old_count = len(self.open_orders)
            self.open_orders = fresh_orders
            if old_count != len(fresh_orders):
                logger.info(f"🔄 State synced: {old_count} → {len(fresh_orders)} orders")

    def get_snapshot(self) -> tuple[Decimal, Decimal, list[OpenOrder], list[OpenOrder]]:
        """Atomic snapshot of current state for the adjustment loop."""
        with self._lock:
            bids = sorted((o for o in self.open_orders.values() if o.is_buy), key=lambda o: o.price, reverse=True)
            asks = sorted((o for o in self.open_orders.values() if not o.is_buy), key=lambda o: o.price)
            return self.reference_price, self.collateral_balance, bids, asks


# ---------------------------------------------------------------------------
# Pricing + sizing helpers
# ---------------------------------------------------------------------------


def round_to_tick(price: Decimal, tick_size: Decimal) -> Decimal:
    return (price / tick_size).quantize(Decimal("1"), rounding=ROUND_DOWN) * tick_size


def round_to_qty_step(qty: Decimal, qty_step_size: Decimal) -> Decimal:
    return (qty / qty_step_size).quantize(Decimal("1"), rounding=ROUND_DOWN) * qty_step_size


def required_margin(price: Decimal, qty: Decimal, max_leverage: int) -> Decimal:
    """Conservative IM estimate. Real margin includes funding accrual, OI cap,
    and risk-matrix terms — but for a thin always-on quote on a single market
    on devnet1, ``notional / max_leverage`` is a safe overestimate that keeps
    us well clear of the protocol's IM check."""
    if max_leverage <= 0:
        return price * qty  # degenerate; full notional reserve
    return (price * qty) / Decimal(max_leverage)


def affordable_qty(price: Decimal, available_margin: Decimal, market_params: MarketParams) -> Decimal:
    """Largest qty within MAX_ORDER_QTY that fits in ``available_margin``."""
    if available_margin <= 0 or price <= 0:
        return Decimal("0")
    max_qty_by_margin = available_margin * Decimal(market_params.max_leverage) / price
    qty = min(MAX_ORDER_QTY, max_qty_by_margin)
    return round_to_qty_step(qty, market_params.qty_step_size)


def generate_random_qty(min_qty: Decimal, max_qty: Decimal, qty_step_size: Decimal) -> str:
    """Random qty in [min, max], step-aligned. Spot bot uses this same shape;
    keeping it for parity so ladder behaviour is symmetric."""
    if max_qty <= min_qty:
        return str(min_qty)
    qty_range = max_qty - min_qty
    random_offset = qty_range * Decimal(random.uniform(0.0, 1.0))  # nosec B311
    qty = round_to_qty_step(min_qty + random_offset, qty_step_size)
    return str(max(qty, min_qty))


def generate_quote_prices(
    reference: Decimal, max_deviation_pct: Decimal, num_levels: int, tick_size: Decimal
) -> tuple[list[str], list[str]]:
    """Random bid/ask ladder around reference, distributed across the spread band."""
    min_price = reference * (1 - max_deviation_pct)
    max_price = reference * (1 + max_deviation_pct)

    bid_range = reference - min_price
    bids: list[str] = []
    for i in range(num_levels):
        offset = bid_range * Decimal(random.uniform(0.1, 1.0)) * Decimal(i + 1) / Decimal(num_levels)  # nosec B311
        price = round_to_tick(reference - offset, tick_size)
        if price >= min_price:
            bids.append(str(price))
    bids = sorted(set(bids), key=Decimal, reverse=True)[:num_levels]

    ask_range = max_price - reference
    asks: list[str] = []
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
    """Single random price that doesn't cross our own spread (avoids self-match)."""
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
        return round_to_tick(min_price if is_buy else max_price, tick_size)

    price_range = upper_bound - lower_bound
    random_offset = price_range * Decimal(random.uniform(0.0, 1.0))  # nosec B311
    return round_to_tick(lower_bound + random_offset, tick_size)


def compute_available_margin(
    collateral_balance: Decimal, open_orders: list[OpenOrder], market_params: MarketParams
) -> Decimal:
    """Budget minus the IM already locked up by our resting orders."""
    budget = collateral_balance * COLLATERAL_BUDGET_FRACTION
    committed = sum(
        (required_margin(o.price, o.qty, market_params.max_leverage) for o in open_orders),
        start=Decimal("0"),
    )
    return max(Decimal("0"), budget - committed)


# ---------------------------------------------------------------------------
# WebSocket handler
# ---------------------------------------------------------------------------


class WebSocketHandler:
    """Subscribes to oracle price, wallet balances, order changes, perp executions."""

    def __init__(self, state: MarketMakerState):
        self.state = state
        self._connected = threading.Event()

    def wait_for_connection(self, timeout: float = 10.0) -> bool:
        return self._connected.wait(timeout)

    def on_open(self, ws: ReyaSocket) -> None:
        logger.info("🔌 WebSocket connected, subscribing to channels...")
        wallet = self.state.wallet_address
        if not wallet:
            logger.error("No wallet address set in state")
            return

        ws.prices.price(self.state.oracle_symbol).subscribe()
        ws.wallet.balances(wallet).subscribe()
        ws.wallet.order_changes(wallet).subscribe()
        ws.wallet.perp_executions(wallet).subscribe()

        logger.info(f"   ✅ Subscribed to /v2/prices/{self.state.oracle_symbol}")
        logger.info(f"   ✅ Subscribed to /v2/wallet/{wallet}/accountBalances")
        logger.info(f"   ✅ Subscribed to /v2/wallet/{wallet}/openOrders")
        logger.info(f"   ✅ Subscribed to /v2/wallet/{wallet}/perpExecutions")

    def on_message(self, _ws: ReyaSocket, message: WebSocketMessage) -> None:
        # Subscription confirmation — flip the connected flag once we have all subs.
        if isinstance(message, SubscribedMessagePayload):
            logger.debug(f"Subscribed to {message.channel}")
            self._connected.set()
            return

        if isinstance(message, PriceUpdatePayload):
            if message.data and message.data.oracle_price:
                price = Decimal(message.data.oracle_price)
                if self.state.market_params:
                    price = round_to_tick(price, self.state.market_params.tick_size)
                self.state.update_price(price)
            return

        if isinstance(message, AccountBalanceUpdatePayload):
            for balance in message.data:
                if balance.account_id != self.state.account_id:
                    continue
                if balance.asset == COLLATERAL_ASSET:
                    self.state.update_collateral(Decimal(balance.real_balance))
            return

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

        if isinstance(message, WalletPerpExecutionUpdatePayload):
            for execution in message.data:
                if execution.symbol != self.state.symbol:
                    continue
                # Only log our side of the fill (taker or maker). Either way
                # the order_id is the one that hit, and we use it for logging.
                order_id = execution.taker_order_id or execution.maker_order_id
                if order_id is None:
                    continue
                self.state.log_execution(
                    order_id=order_id,
                    qty=execution.qty,
                    price=execution.price,
                    side=execution.side.value,
                )
            return

    def on_error(self, _ws: ReyaSocket, error: Exception) -> None:
        logger.error(f"WebSocket error: {error}")

    def on_close(self, _ws: ReyaSocket, close_status_code: int, close_msg: str) -> None:
        logger.warning(f"WebSocket closed: {close_status_code} - {close_msg}")
        self._connected.clear()


# ---------------------------------------------------------------------------
# REST helpers (used for bootstrap + periodic resync)
# ---------------------------------------------------------------------------


async def fetch_market_definition(client: ReyaTradingClient, symbol: str) -> MarketParams:
    """Look up perp market params via ``/perpMarketDefinitions``."""
    definitions = await client.reference.get_perp_market_definitions()
    for market in definitions:
        if market.symbol == symbol:
            return MarketParams(
                symbol=market.symbol,
                tick_size=Decimal(market.tick_size),
                min_order_qty=Decimal(market.min_order_qty),
                qty_step_size=Decimal(market.qty_step_size),
                max_leverage=market.max_leverage,
            )
    raise RuntimeError(f"Perp market definition not found for symbol: {symbol}")


async def fetch_initial_state(client: ReyaTradingClient, state: MarketMakerState) -> None:
    market_params = state.market_params
    account_id = state.account_id
    if not market_params or not account_id:
        raise RuntimeError("Market params and account_id must be set before fetching initial state")

    logger.info(f"   Fetching oracle price for {state.oracle_symbol}...")
    price_info = await client.markets.get_price(state.oracle_symbol)
    if price_info and price_info.oracle_price:
        state.reference_price = round_to_tick(Decimal(price_info.oracle_price), market_params.tick_size)

    logger.info("   Fetching account balances...")
    balances = await client.get_account_balances()
    for balance in balances:
        if balance.account_id == account_id and balance.asset == COLLATERAL_ASSET:
            state.collateral_balance = Decimal(balance.real_balance)

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
            order_id=order.order_id, price=Decimal(order.limit_px), qty=remaining_qty, is_buy=is_buy
        )


async def refresh_state_from_rest(client: ReyaTradingClient, state: MarketMakerState) -> None:
    """Re-sync open orders from REST (defends against WS gaps / missed events)."""
    try:
        open_orders = await client.get_open_orders()
        fresh: dict[str, OpenOrder] = {}
        for order in open_orders:
            if order.symbol != state.symbol:
                continue
            qty = Decimal(order.qty) if order.qty else Decimal("0")
            cum_qty = Decimal(order.cum_qty) if order.cum_qty else Decimal("0")
            remaining_qty = qty - cum_qty
            is_buy = order.side.value == "B"
            fresh[order.order_id] = OpenOrder(
                order_id=order.order_id, price=Decimal(order.limit_px), qty=remaining_qty, is_buy=is_buy
            )
        state.sync_orders(fresh)
    except RECOVERABLE_EXC as e:
        logger.warning(f"Failed to refresh state from REST: {e}")


# ---------------------------------------------------------------------------
# Order placement / replacement
# ---------------------------------------------------------------------------


async def place_single_order(
    client: ReyaTradingClient,
    symbol: str,
    price: str,
    is_buy: bool,
    market_params: MarketParams,
    available_margin: Decimal,
    max_retries: int = 3,
) -> tuple[bool, Decimal]:
    """Place a GTC limit order. Retries with min qty on margin-rejections so
    we always make at least *some* book contribution if margin headroom is tight."""
    price_decimal = Decimal(price)
    side = "bid" if is_buy else "ask"

    max_qty = affordable_qty(price_decimal, available_margin, market_params)
    if max_qty >= market_params.min_order_qty:
        qty = generate_random_qty(market_params.min_order_qty, max_qty, market_params.qty_step_size)
    else:
        # Local margin tracking says insufficient — still try with min qty
        # since real on-chain margin can have more headroom than our estimate.
        qty = str(market_params.min_order_qty)
        logger.debug(f"   Local budget low, trying {side} @ ${price} with min qty={qty}")

    for attempt in range(max_retries):
        try:
            expires_after = int(time.time()) + GTT_LIFETIME_S
            await client.create_limit_order(
                LimitOrderParameters(
                    symbol=symbol,
                    is_buy=is_buy,
                    limit_px=price,
                    qty=qty,
                    time_in_force=TimeInForce.GTT,
                    expires_after=expires_after,
                )
            )
            logger.info(f"   Placed {side} @ ${price} qty={qty}")
            return True, required_margin(price_decimal, Decimal(qty), market_params.max_leverage)
        except RECOVERABLE_EXC as e:
            err = str(e).lower()
            if "insufficient" in err or "margin" in err or "balance" in err:
                if attempt < max_retries - 1:
                    qty = str(market_params.min_order_qty)
                    logger.debug(f"   Retrying {side} @ ${price} with min qty={qty}")
                    continue
                logger.warning(f"   Skipping {side} @ ${price} — API rejected (insufficient margin)")
            else:
                logger.warning(f"   Failed to place {side} @ ${price}: {e}")
            return False, Decimal("0")
    return False, Decimal("0")


async def place_initial_ladder(
    client: ReyaTradingClient,
    symbol: str,
    bids: list[str],
    asks: list[str],
    market_params: MarketParams,
    available_margin: Decimal,
) -> int:
    """Place the initial bid+ask ladder, tracking remaining margin budget as we go."""
    order_count = 0
    remaining_margin = available_margin
    for price in bids:
        success, margin_used = await place_single_order(client, symbol, price, True, market_params, remaining_margin)
        if success:
            order_count += 1
            remaining_margin -= margin_used
    for price in asks:
        success, margin_used = await place_single_order(client, symbol, price, False, market_params, remaining_margin)
        if success:
            order_count += 1
            remaining_margin -= margin_used
    return order_count


def find_out_of_range_orders(
    bids: list[OpenOrder], asks: list[OpenOrder], reference: Decimal, max_spread_pct: Decimal
) -> list[OpenOrder]:
    """Orders sitting outside ±max_spread_pct from reference."""
    min_price = reference * (1 - max_spread_pct)
    max_price = reference * (1 + max_spread_pct)
    return [o for o in bids + asks if o.price < min_price or o.price > max_price]


async def cancel_and_replace_order(
    client: ReyaTradingClient,
    symbol: str,
    account_id: int,
    order: OpenOrder,
    reference_price: Decimal,
    market_params: MarketParams,
    available_margin: Decimal,
    remaining_bids: list[OpenOrder],
    remaining_asks: list[OpenOrder],
    cycle: int,
    state: MarketMakerState,
    reason: str = "",
    max_retries: int = 3,
) -> bool:
    """Cancel an order and place a new one at a fresh in-range price.
    On stale-order cancel errors (race with fill/expiry), drop local state."""
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

    # Margin available after cancelling this order (frees up its committed IM).
    freed = required_margin(order.price, order.qty, market_params.max_leverage)
    total_available_margin = available_margin + freed
    max_qty = affordable_qty(new_price, total_available_margin, market_params)

    if max_qty < market_params.min_order_qty:
        logger.warning(f"[{cycle:04d}] Skipping {side} replacement — insufficient margin")
        await _safe_cancel(client, order, symbol, account_id, state, cycle, side)
        return False

    new_qty = generate_random_qty(market_params.min_order_qty, max_qty, market_params.qty_step_size)

    cancelled = await _safe_cancel(client, order, symbol, account_id, state, cycle, side)
    if not cancelled:
        return False
    reason_str = f" ({reason})" if reason else ""
    logger.info(
        f"[{cycle:04d}] Cancelled {side} @ ${order.price}{reason_str} "
        f"→ placing new {side} @ ${new_price} qty={new_qty}"
    )

    await asyncio.sleep(0.1)

    qty_to_use = new_qty
    for attempt in range(max_retries):
        try:
            expires_after = int(time.time()) + GTT_LIFETIME_S
            await client.create_limit_order(
                LimitOrderParameters(
                    symbol=symbol,
                    is_buy=order.is_buy,
                    limit_px=str(new_price),
                    qty=qty_to_use,
                    time_in_force=TimeInForce.GTT,
                    expires_after=expires_after,
                )
            )
            return True
        except RECOVERABLE_EXC as e:
            err = str(e).lower()
            if "insufficient" in err or "margin" in err or "balance" in err:
                if attempt < max_retries - 1:
                    qty_to_use = str(market_params.min_order_qty)
                    logger.debug(f"[{cycle:04d}] Retrying {side} @ ${new_price} with min qty={qty_to_use}")
                    continue
            logger.warning(f"[{cycle:04d}] Failed to place new {side} @ ${new_price}: {e}")
            return False
    return False


async def _safe_cancel(
    client: ReyaTradingClient,
    order: OpenOrder,
    symbol: str,
    account_id: int,
    state: MarketMakerState,
    cycle: int,
    side: str,
) -> bool:
    """Cancel, tolerating 'Order not found' (cancel raced a fill/expiry).
    Returns True if the order is gone after the call (either cancelled or
    already missing — both are fine for the caller's purposes)."""
    try:
        await client.cancel_order(order_id=order.order_id, symbol=symbol, account_id=account_id)
        return True
    except RECOVERABLE_EXC as e:
        err = str(e)
        if "Order not found" in err or "CANCEL_ORDER_OTHER_ERROR" in err:
            state.remove_order(order.order_id)
            logger.info(f"[{cycle:04d}] Removed stale {side} @ ${order.price} from local state")
            return True
        logger.warning(f"[{cycle:04d}] Failed to cancel {side} @ ${order.price}: {e}")
        return False


async def adjust_orders(client: ReyaTradingClient, state: MarketMakerState, cycle: int) -> None:
    """One adjustment pass — prioritises evicting out-of-range orders first,
    then nudges a random in-range order so the ladder gets refreshed over time."""
    market_params = state.market_params
    account_id = state.account_id
    if not market_params or not account_id:
        return

    reference_price, collateral_balance, bids, asks = state.get_snapshot()
    if reference_price == Decimal("0"):
        logger.warning(f"[{cycle:04d}] No reference price available, skipping")
        return

    available_margin = compute_available_margin(collateral_balance, bids + asks, market_params)
    min_price = reference_price * (1 - state.max_spread_pct)
    max_price = reference_price * (1 + state.max_spread_pct)

    # Pass 0: refill missing levels (self-healing — handles orders that
    # disappeared without us cancelling them: expiry, ME restart, fills,
    # anything outside the bot's view). Keeps both sides at NUM_LEVELS.
    needed_bids = NUM_LEVELS - len(bids)
    needed_asks = NUM_LEVELS - len(asks)
    if needed_bids > 0 or needed_asks > 0:
        logger.info(
            f"[{cycle:04d}] 📉 Refilling: have {len(bids)} bids / {len(asks)} asks "
            f"(target {NUM_LEVELS} each) — placing {needed_bids} bid(s) + {needed_asks} ask(s)"
        )
        best_bid = bids[0].price if bids else None
        best_ask = asks[0].price if asks else None
        remaining_margin = available_margin
        for _ in range(max(needed_bids, 0)):
            new_price = generate_single_price(
                is_buy=True,
                reference=reference_price,
                max_deviation_pct=state.max_spread_pct,
                tick_size=market_params.tick_size,
                best_bid=best_bid,
                best_ask=best_ask,
            )
            success, margin_used = await place_single_order(
                client, state.symbol, str(new_price), True, market_params, remaining_margin
            )
            if success:
                remaining_margin -= margin_used
                if best_bid is None or new_price > best_bid:
                    best_bid = new_price
        for _ in range(max(needed_asks, 0)):
            new_price = generate_single_price(
                is_buy=False,
                reference=reference_price,
                max_deviation_pct=state.max_spread_pct,
                tick_size=market_params.tick_size,
                best_bid=best_bid,
                best_ask=best_ask,
            )
            success, margin_used = await place_single_order(
                client, state.symbol, str(new_price), False, market_params, remaining_margin
            )
            if success:
                remaining_margin -= margin_used
                if best_ask is None or new_price < best_ask:
                    best_ask = new_price
        return

    # Pass 1: evict orders sitting outside the band.
    out_of_range = find_out_of_range_orders(bids, asks, reference_price, state.max_spread_pct)
    if out_of_range:
        logger.info(
            f"[{cycle:04d}] 📊 Oracle ${reference_price} | Range ${min_price:.2f} – ${max_price:.2f} | "
            f"⚠️  {len(out_of_range)} order(s) out of range"
        )
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
                available_margin=available_margin,
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
        return  # one batch per cycle

    # Pass 2: pick one random order to refresh (keeps the ladder dynamic
    # without churning the whole book every cycle).
    if not bids and not asks:
        logger.warning(f"[{cycle:04d}] No open orders to adjust")
        return

    if bids and asks:
        adjust_bid_side = random.choice([True, False])  # nosec B311
    else:
        adjust_bid_side = bool(bids)

    order_to_cancel = random.choice(bids) if adjust_bid_side else random.choice(asks)  # nosec B311
    remaining_bids = [o for o in bids if o.order_id != order_to_cancel.order_id]
    remaining_asks = [o for o in asks if o.order_id != order_to_cancel.order_id]
    await cancel_and_replace_order(
        client=client,
        symbol=state.symbol,
        account_id=account_id,
        order=order_to_cancel,
        reference_price=reference_price,
        market_params=market_params,
        available_margin=available_margin,
        remaining_bids=remaining_bids,
        remaining_asks=remaining_asks,
        cycle=cycle,
        state=state,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def main(symbol: str, oracle_symbol: str, max_spread_pct: Decimal) -> None:
    load_dotenv()

    logger.info("=" * 60)
    logger.info(f"🚀 PERP Market Maker (WebSocket) for {symbol}")
    logger.info("=" * 60)

    state = MarketMakerState(symbol=symbol, oracle_symbol=oracle_symbol, max_spread_pct=max_spread_pct)
    perp_config = TradingConfig.from_env()

    async with ReyaTradingClient(config=perp_config) as client:
        await client.start()

        account_id = perp_config.account_id
        wallet_address = perp_config.owner_wallet_address
        if not account_id:
            raise ValueError("PERP_ACCOUNT_ID_1 environment variable is required")
        if not wallet_address:
            raise ValueError("PERP_WALLET_ADDRESS_1 environment variable is required")

        state.account_id = account_id
        state.wallet_address = wallet_address

        logger.info(f"   Fetching market definition for {symbol}...")
        state.market_params = await fetch_market_definition(client, symbol)
        market_params = state.market_params

        await fetch_initial_state(client, state)

        min_price = state.reference_price * (1 - state.max_spread_pct)
        max_price = state.reference_price * (1 + state.max_spread_pct)
        logger.info(f"   Reference Price: ${state.reference_price} (from {oracle_symbol} oracle)")
        logger.info(f"   Price Range:     ${min_price:.2f} – ${max_price:.2f} (±{state.max_spread_pct * 100}%)")
        logger.info(f"   Tick Size:       {market_params.tick_size}")
        logger.info(f"   Min Order Qty:   {market_params.min_order_qty}")
        logger.info(f"   Max Order Qty:   {MAX_ORDER_QTY}")
        logger.info(f"   Qty Step Size:   {market_params.qty_step_size}")
        logger.info(f"   Max Leverage:    {market_params.max_leverage}x")
        logger.info(f"   {COLLATERAL_ASSET} Collateral: {state.collateral_balance}")
        logger.info(f"   Budget Fraction: {COLLATERAL_BUDGET_FRACTION} ({COLLATERAL_BUDGET_FRACTION * 100}%)")
        logger.info(f"   Open Orders:     {len(state.open_orders)}")
        logger.info(f"   Levels:          {NUM_LEVELS} bids / {NUM_LEVELS} asks")
        logger.info(f"   Refresh:         Every {REFRESH_INTERVAL}s")
        logger.info(f"   Account ID:      {account_id}")
        logger.info("   Press Ctrl+C to stop")
        logger.info("%s\n", "=" * 60)

        ws_url = os.environ.get("REYA_WS_URL", "wss://ws.reya.xyz/")
        ws_handler = WebSocketHandler(state)
        websocket = ReyaSocket(
            url=ws_url,
            on_open=ws_handler.on_open,
            on_message=ws_handler.on_message,
            on_error=ws_handler.on_error,
            on_close=ws_handler.on_close,
        )

        logger.info("🔌 Connecting WebSocket...")
        websocket.connect()
        if not ws_handler.wait_for_connection(timeout=10.0):
            logger.warning("WebSocket connection timeout, continuing with REST-only state refresh")

        logger.info("Cleaning up existing orders...")
        await client.mass_cancel(symbol=symbol, account_id=account_id)
        await asyncio.sleep(0.2)
        state.open_orders.clear()
        logger.info("✅ Order book cleaned\n")

        try:
            logger.info("Placing initial depth ladder...")
            available_margin = compute_available_margin(state.collateral_balance, [], market_params)
            bid_prices, ask_prices = generate_quote_prices(
                state.reference_price, state.max_spread_pct, NUM_LEVELS, market_params.tick_size
            )
            order_count = await place_initial_ladder(
                client, symbol, bid_prices, ask_prices, market_params, available_margin
            )
            logger.info(f"✅ Initial setup complete: {order_count} orders")
            logger.info(f"   Bids: {', '.join(f'${b}' for b in bid_prices)}")
            logger.info(f"   Asks: {', '.join(f'${a}' for a in ask_prices)}\n")

            cycle = 0
            while True:
                await asyncio.sleep(REFRESH_INTERVAL)
                cycle += 1

                # Halt if collateral falls below floor (e.g. drained by adverse selection).
                if state.collateral_balance < MIN_COLLATERAL:
                    logger.warning(
                        f"[{cycle:04d}] ⚠️  {COLLATERAL_ASSET} collateral "
                        f"({state.collateral_balance}) below floor ({MIN_COLLATERAL})"
                    )
                    logger.warning(f"[{cycle:04d}] 🛑 Stopping MM due to low collateral...")
                    break

                # Periodic REST resync defends against WS gaps / missed events.
                if cycle % STATE_REFRESH_CYCLES == 0:
                    logger.info(f"[{cycle:04d}] 🔄 Refreshing state from REST API...")
                    await refresh_state_from_rest(client, state)

                await adjust_orders(client, state, cycle)

        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            logger.info("\n🛑 Shutting down...")
            logger.info("Closing WebSocket...")
            websocket.close()
            logger.info("Mass-cancelling all orders...")
            try:
                await client.mass_cancel(symbol=symbol, account_id=account_id)
                logger.info("✅ Market maker stopped")
            except RECOVERABLE_EXC as e:
                logger.warning(f"Cleanup failed: {e}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Perp Market Maker — maintains a thin always-on depth ladder around the oracle price."
    )
    parser.add_argument("--symbol", type=str, default=DEFAULT_SYMBOL, help=f"Perp symbol (default: {DEFAULT_SYMBOL})")
    parser.add_argument(
        "--oracle-symbol",
        type=str,
        default=DEFAULT_ORACLE_SYMBOL,
        help=f"Oracle reference symbol (default: {DEFAULT_ORACLE_SYMBOL})",
    )
    parser.add_argument(
        "--max-spread",
        type=float,
        default=float(DEFAULT_MAX_SPREAD_PCT),
        help=f"Max ±spread from reference as decimal (default: {DEFAULT_MAX_SPREAD_PCT})",
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
