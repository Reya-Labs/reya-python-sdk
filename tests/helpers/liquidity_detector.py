"""
Liquidity detection utilities for smart SPOT test execution.

This module provides tools to detect and analyze order book liquidity,
enabling tests to automatically adapt to both empty and non-empty order books.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, List, Optional

import logging

if TYPE_CHECKING:
    from sdk.open_api.models.depth import Depth
    from sdk.open_api.models.level import Level
    from tests.helpers.reya_tester.data import DataOperations

logger = logging.getLogger("reya.integration_tests")


CIRCUIT_BREAKER_PCT = Decimal("0.05")  # ±5% from oracle price

# Extreme prices for safe no-match orders (guaranteed never to match)
SAFE_NO_MATCH_BUY_PRICE = Decimal("10")  # $10 - far below any realistic ETH price
SAFE_NO_MATCH_SELL_PRICE = Decimal("10000000")  # $10M - far above any realistic ETH price


@dataclass
class LiquidityInfo:
    """Liquidity information for one side of the order book."""

    has_liquidity: bool
    best_price: Optional[Decimal]
    total_qty: Decimal
    levels: List["Level"] = field(default_factory=list)
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

    async def get_order_book_state(
        self, data_ops: "DataOperations", symbol: str
    ) -> OrderBookState:
        """
        Fetch and analyze current order book state.

        Args:
            data_ops: DataOperations instance for API calls.
            symbol: Trading symbol to query.

        Returns:
            OrderBookState with analyzed liquidity information.
        """
        depth: Optional["Depth"] = await data_ops.market_depth(symbol)

        bids = self._analyze_side(
            levels=depth.bids if depth else [],
            is_bid=True,
        )
        asks = self._analyze_side(
            levels=depth.asks if depth else [],
            is_bid=False,
        )

        return OrderBookState(
            symbol=symbol,
            oracle_price=self._oracle_price,
            bids=bids,
            asks=asks,
        )

    def _analyze_side(
        self,
        levels: List["Level"],
        is_bid: bool,
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
        total_qty = sum(Decimal(level.qty) for level in levels)

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

    def get_usable_bid_price(
        self, state: OrderBookState, min_qty: str
    ) -> Optional[Decimal]:
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
            if not (state.circuit_breaker_floor <= price <= state.circuit_breaker_ceiling):
                continue

            cumulative_qty += qty
            if cumulative_qty >= min_qty_dec:
                return state.bids.best_price

        return None

    def get_usable_ask_price(
        self, state: OrderBookState, min_qty: str
    ) -> Optional[Decimal]:
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
            if not (state.circuit_breaker_floor <= price <= state.circuit_breaker_ceiling):
                continue

            cumulative_qty += qty
            if cumulative_qty >= min_qty_dec:
                return state.asks.best_price

        return None

    def get_safe_no_match_buy_price(self, state: OrderBookState) -> Decimal:
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

    def get_safe_no_match_sell_price(self, state: OrderBookState) -> Decimal:
        """
        Get a sell price guaranteed not to match any existing bids.

        Uses an extreme high price ($10M) that is far above any realistic market price,
        ensuring the order will never match regardless of order book state.

        Args:
            state: Current order book state (unused, kept for API compatibility).

        Returns:
            $10,000,000 - a safe sell price that will never match.
        """
        return SAFE_NO_MATCH_SELL_PRICE

    def get_qty_at_price_or_better(
        self, state: OrderBookState, side: str, price: Decimal
    ) -> Decimal:
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
    logger.info(
        f"  Circuit breaker range: ${state.circuit_breaker_floor:.2f} - ${state.circuit_breaker_ceiling:.2f}"
    )

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
