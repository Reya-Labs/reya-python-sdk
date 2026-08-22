"""
Liquidity detection utilities for smart SPOT test execution.

This module provides tools to detect and analyze order book liquidity,
enabling tests to automatically adapt to both empty and non-empty order books.
"""

from typing import TYPE_CHECKING, Optional

import logging
from dataclasses import dataclass, field
from decimal import Decimal

import pytest

if TYPE_CHECKING:
    from sdk.open_api.models.depth_snapshot import DepthSnapshot
    from sdk.open_api.models.level import Level
    from tests.helpers.reya_tester.data import DataOperations

logger = logging.getLogger("reya.integration_tests")


CIRCUIT_BREAKER_PCT = Decimal("0.05")  # ±5% from oracle price

# Extreme prices for safe no-match orders (guaranteed never to match).
#
# The matching engine validates `limit_px <= MAX_PRICE` where
# MAX_PRICE = 2^49 in E9-scaled units ≈ 562_949 in real units (~$562k).
# See protos/reya-chain/crates/matching-engine/src/base/validation/rules.rs
# in reya-off-chain-monorepo.
#
# We pick $400k for SAFE_NO_MATCH_SELL_PRICE: well below MAX_PRICE so the
# ME validator accepts it, yet still far above any realistic ETH/BTC price
# (current ETH ≈ $2k, BTC ≈ $60k — would need ~7× movement to even
# approach this), so the "guaranteed no match" intent is preserved.
# At qty=min_qty this is also ~$400 open notional, well under the
# server-side GTC notional caps (the concern behind main's old $1M value,
# which is moot here since $1M would exceed the ME MAX_PRICE anyway).
SAFE_NO_MATCH_BUY_PRICE = Decimal("10")  # $10 — far below any realistic ETH/BTC price
SAFE_NO_MATCH_SELL_PRICE = Decimal("400000")  # $400k — below ME MAX_PRICE (~$562k), above realistic crypto prices


@dataclass
class LiquidityInfo:
    """Liquidity information for one side of the order book."""

    has_liquidity: bool
    best_price: Optional[Decimal]
    total_qty: Decimal
    levels: list["Level"] = field(default_factory=list)
    within_circuit_breaker: bool = False

    @property
    def is_usable(self) -> bool:
        """True if liquidity exists and is within circuit breaker range."""
        return self.has_liquidity and self.within_circuit_breaker


@dataclass
class OrderBookState:
    """Complete order book state for a symbol."""

    symbol: str
    oracle_price: Decimal
    bids: LiquidityInfo
    asks: LiquidityInfo

    @property
    def has_any_liquidity(self) -> bool:
        """True if any liquidity exists on either side."""
        return self.bids.has_liquidity or self.asks.has_liquidity

    @property
    def has_usable_bid_liquidity(self) -> bool:
        """True if usable bid liquidity exists (for sell orders)."""
        return self.bids.is_usable

    @property
    def has_usable_ask_liquidity(self) -> bool:
        """True if usable ask liquidity exists (for buy orders)."""
        return self.asks.is_usable

    @property
    def circuit_breaker_floor(self) -> Decimal:
        """Minimum allowed price (oracle - 5%)."""
        return (self.oracle_price * (1 - CIRCUIT_BREAKER_PCT)).quantize(Decimal("0.01"))

    @property
    def circuit_breaker_ceiling(self) -> Decimal:
        """Maximum allowed price (oracle + 5%)."""
        return (self.oracle_price * (1 + CIRCUIT_BREAKER_PCT)).quantize(Decimal("0.01"))


class LiquidityDetector:
    """Detects and analyzes order book liquidity for smart test execution."""

    def __init__(self, oracle_price: float):
        """
        Initialize the liquidity detector.

        Args:
            oracle_price: Current oracle price for the symbol.
        """
        self._oracle_price = Decimal(str(oracle_price))

    async def get_order_book_state(self, data_ops: "DataOperations", symbol: str) -> OrderBookState:
        """
        Fetch and analyze current order book state.

        Args:
            data_ops: DataOperations instance for API calls.
            symbol: Trading symbol to query.

        Returns:
            OrderBookState with analyzed liquidity information.
        """
        depth: Optional["DepthSnapshot"] = await data_ops.market_depth(symbol)

        bids = self._analyze_side(
            levels=depth.bids if depth else [],
            _is_bid=True,
        )
        asks = self._analyze_side(
            levels=depth.asks if depth else [],
            _is_bid=False,
        )

        return OrderBookState(
            symbol=symbol,
            oracle_price=self._oracle_price,
            bids=bids,
            asks=asks,
        )

    def _analyze_side(
        self,
        levels: list["Level"],
        _is_bid: bool,
    ) -> LiquidityInfo:
        """
        Analyze liquidity on one side of the order book.

        Args:
            levels: List of price levels from the order book.
            is_bid: True for bid side, False for ask side.

        Returns:
            LiquidityInfo with analysis results.
        """
        if not levels:
            return LiquidityInfo(
                has_liquidity=False,
                best_price=None,
                total_qty=Decimal("0"),
                levels=[],
                within_circuit_breaker=False,
            )

        best_price = Decimal(levels[0].px)
        total_qty = sum((Decimal(level.qty) for level in levels), Decimal("0"))

        # Check if best price is within circuit breaker range
        floor = self._oracle_price * (1 - CIRCUIT_BREAKER_PCT)
        ceiling = self._oracle_price * (1 + CIRCUIT_BREAKER_PCT)
        within_cb = floor <= best_price <= ceiling

        return LiquidityInfo(
            has_liquidity=True,
            best_price=best_price,
            total_qty=total_qty,
            levels=levels,
            within_circuit_breaker=within_cb,
        )

    def get_usable_bid_price(self, state: OrderBookState, min_qty: str) -> Optional[Decimal]:
        """
        Get best bid price with sufficient quantity within circuit breaker.

        Args:
            state: Current order book state.
            min_qty: Minimum required quantity.

        Returns:
            Best usable bid price, or None if no usable liquidity.
        """
        if not state.has_usable_bid_liquidity:
            return None

        min_qty_dec = Decimal(min_qty)
        cumulative_qty = Decimal("0")

        for level in state.bids.levels:
            price = Decimal(level.px)
            qty = Decimal(level.qty)

            # Check if this level is within circuit breaker
            if not state.circuit_breaker_floor <= price <= state.circuit_breaker_ceiling:
                continue

            cumulative_qty += qty
            if cumulative_qty >= min_qty_dec:
                return state.bids.best_price

        return None

    def get_usable_ask_price(self, state: OrderBookState, min_qty: str) -> Optional[Decimal]:
        """
        Get best ask price with sufficient quantity within circuit breaker.

        Args:
            state: Current order book state.
            min_qty: Minimum required quantity.

        Returns:
            Best usable ask price, or None if no usable liquidity.
        """
        if not state.has_usable_ask_liquidity:
            return None

        min_qty_dec = Decimal(min_qty)
        cumulative_qty = Decimal("0")

        for level in state.asks.levels:
            price = Decimal(level.px)
            qty = Decimal(level.qty)

            # Check if this level is within circuit breaker
            if not state.circuit_breaker_floor <= price <= state.circuit_breaker_ceiling:
                continue

            cumulative_qty += qty
            if cumulative_qty >= min_qty_dec:
                return state.asks.best_price

        return None

    def get_safe_no_match_buy_price(self, _state: OrderBookState) -> Decimal:
        """
        Get a buy price guaranteed not to match any existing asks.

        Uses an extreme low price ($10) that is far below any realistic market price,
        ensuring the order will never match regardless of order book state.

        Args:
            state: Current order book state (unused, kept for API compatibility).

        Returns:
            $10 - a safe buy price that will never match.
        """
        return SAFE_NO_MATCH_BUY_PRICE

    def get_safe_no_match_sell_price(self, _state: OrderBookState) -> Decimal:
        """
        Get a sell price guaranteed not to match any existing bids.

        Uses a high price well above any realistic market price (see
        ``SAFE_NO_MATCH_SELL_PRICE``), ensuring the order will never match
        regardless of order book state.

        Args:
            state: Current order book state (unused, kept for API compatibility).

        Returns:
            ``SAFE_NO_MATCH_SELL_PRICE`` - a safe sell price that will never match.
        """
        return SAFE_NO_MATCH_SELL_PRICE

    def get_qty_at_price_or_better(self, state: OrderBookState, side: str, price: Decimal) -> Decimal:
        """
        Get total quantity available at a price or better.

        Args:
            state: Current order book state.
            side: 'bid' or 'ask'.
            price: Target price.

        Returns:
            Total quantity available at price or better.
        """
        levels = state.bids.levels if side == "bid" else state.asks.levels
        total_qty = Decimal("0")

        for level in levels:
            level_price = Decimal(level.px)
            level_qty = Decimal(level.qty)

            if side == "bid":
                # For bids, "better" means higher price
                if level_price >= price:
                    total_qty += level_qty
            else:
                # For asks, "better" means lower price
                if level_price <= price:
                    total_qty += level_qty

        return total_qty


def log_order_book_state(state: OrderBookState) -> None:
    """Log a summary of the order book state."""
    logger.info(f"Order book state for {state.symbol}:")
    logger.info(f"  Oracle price: ${state.oracle_price:.2f}")
    logger.info(f"  Circuit breaker range: ${state.circuit_breaker_floor:.2f} - ${state.circuit_breaker_ceiling:.2f}")

    if state.bids.has_liquidity:
        logger.info(
            f"  Bids: best=${state.bids.best_price:.2f}, "
            f"total_qty={state.bids.total_qty}, "
            f"usable={state.bids.is_usable}"
        )
    else:
        logger.info("  Bids: empty")

    if state.asks.has_liquidity:
        logger.info(
            f"  Asks: best=${state.asks.best_price:.2f}, "
            f"total_qty={state.asks.total_qty}, "
            f"usable={state.asks.is_usable}"
        )
    else:
        logger.info("  Asks: empty")


async def skip_if_external_liquidity(
    data_ops: "DataOperations",
    symbol: str,
    oracle_price: float,
    *,
    reason_prefix: str = "",
) -> None:
    """Skip the calling pytest test if any external liquidity is on the orderbook.

    Mirrors the pattern used by `tests/spot/test_maker_taker_matching.py`
    (which calls ``pytest.skip`` when ``spot_config.has_any_external_liquidity``
    is true): tests that need to *rest* a maker order at oracle ±1% and then
    cross it with a same-side taker only work in a controlled environment.
    If somebody else's liquidity (typically a market-making bot running on
    the same env) is inside ±5% of oracle, the maker's order would cross
    that liquidity instead of resting on the book, and the test fails in a
    way unrelated to what it's actually exercising.

    Use at the top of any perp maker/taker test, or inside helpers that
    rest a maker order at near-oracle prices.

    Args:
        data_ops: ReyaTester data operations, used to fetch the depth.
        symbol: Market symbol (e.g. ``ETHRUSDPERP``).
        oracle_price: Current oracle price; used to log circuit-breaker
            bounds for context when skipping.
        reason_prefix: Optional prefix to prepend to the skip message so
            callers can identify which leg of the test caused the skip
            (e.g. ``"_rest_maker_sell"``).
    """
    detector = LiquidityDetector(oracle_price)
    state = await detector.get_order_book_state(data_ops, symbol)
    if not state.has_any_liquidity:
        return

    log_order_book_state(state)
    prefix = f"{reason_prefix}: " if reason_prefix else ""
    pytest.skip(
        f"{prefix}external {symbol} liquidity present "
        f"(likely a market-making bot on the same env). "
        "This test rests a maker order at oracle ±1% which would cross "
        "any liquidity within the ±5% circuit-breaker band rather than "
        "resting on the book. Rerun against a controlled environment."
    )


async def skip_if_external_config_liquidity(config, tester, reason: str) -> None:
    """Config-flavoured twin of `skip_if_external_liquidity` for tests that
    hold a `MarketTestConfig`: refreshes the config's cached book via the
    tester's data ops and skips when ANY external liquidity is present.

    Use the positional variant above when you only have data-ops + symbol +
    oracle price (no config object).
    """
    await config.refresh_order_book(tester.data)
    if config.has_any_external_liquidity:
        pytest.skip(f"Skipping: external liquidity exists. {reason}")
