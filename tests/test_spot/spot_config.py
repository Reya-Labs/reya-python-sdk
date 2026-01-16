"""
Centralized SPOT test configuration with smart liquidity detection.

This module provides a SpotTestConfig dataclass that holds all test configuration
including dynamically fetched oracle prices and order book liquidity state.
The config is injected via pytest fixtures - no global state is used.

Usage in test files:
    @pytest.mark.asyncio
    async def test_something(spot_config: SpotTestConfig, maker_tester: ReyaTester):
        # Refresh liquidity state before test logic
        await spot_config.refresh_order_book(maker_tester.data)
        
        # Check if external liquidity is available
        if spot_config.has_usable_bid_liquidity:
            fill_price = spot_config.best_bid_price
        else:
            # Provide our own liquidity
            ...
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

import logging

from tests.helpers.liquidity_detector import (
    LiquidityDetector,
    OrderBookState,
    log_order_book_state,
)

if TYPE_CHECKING:
    from tests.helpers.reya_tester.data import DataOperations

logger = logging.getLogger("reya.integration_tests")


@dataclass
class SpotTestConfig:
    """
    Centralized configuration for SPOT tests with smart liquidity detection.

    This dataclass is populated by a pytest fixture at session start,
    ensuring all tests use consistent, dynamically-fetched values.

    Attributes:
        symbol: The spot market symbol (e.g., "WETHRUSD")
        min_qty: Minimum order quantity as string (e.g., "0.001")
        oracle_price: Current oracle price for the underlying asset
    """

    symbol: str
    min_qty: str
    oracle_price: float
    _order_book: Optional[OrderBookState] = field(default=None, repr=False)

    def price(self, multiplier: float = 1.0) -> float:
        """
        Get a price based on oracle price with optional multiplier.

        Args:
            multiplier: Price multiplier (e.g., 0.99 for 99% of oracle)

        Returns:
            Rounded price to 2 decimal places
        """
        return round(self.oracle_price * multiplier, 2)

    def buy_price(self, multiplier: float = 0.99) -> float:
        """Get a buy price (default 99% of oracle - within deviation limit)."""
        return self.price(multiplier)

    def sell_price(self, multiplier: float = 1.01) -> float:
        """Get a sell price (default 101% of oracle - within deviation limit)."""
        return self.price(multiplier)

    async def refresh_order_book(self, data_ops: "DataOperations") -> OrderBookState:
        """
        Refresh the order book state from the API.

        This should be called at the start of each test that needs liquidity detection.
        Always fetches fresh data to ensure accuracy.

        Args:
            data_ops: DataOperations instance for API calls.

        Returns:
            Current OrderBookState.
        """
        detector = LiquidityDetector(self.oracle_price)
        self._order_book = await detector.get_order_book_state(data_ops, self.symbol)
        log_order_book_state(self._order_book)
        return self._order_book

    @property
    def order_book(self) -> Optional[OrderBookState]:
        """Get the current order book state (may be None if not refreshed)."""
        return self._order_book

    @property
    def has_any_external_liquidity(self) -> bool:
        """True if any external liquidity exists on either side."""
        if self._order_book is None:
            return False
        return self._order_book.has_any_liquidity

    @property
    def has_usable_bid_liquidity(self) -> bool:
        """True if usable bid liquidity exists (for sell orders to aggress)."""
        if self._order_book is None:
            return False
        return self._order_book.has_usable_bid_liquidity

    @property
    def has_usable_ask_liquidity(self) -> bool:
        """True if usable ask liquidity exists (for buy orders to aggress)."""
        if self._order_book is None:
            return False
        return self._order_book.has_usable_ask_liquidity

    @property
    def best_bid_price(self) -> Optional[Decimal]:
        """Get the best bid price, or None if no bids."""
        if self._order_book is None or not self._order_book.bids.has_liquidity:
            return None
        return self._order_book.bids.best_price

    @property
    def best_ask_price(self) -> Optional[Decimal]:
        """Get the best ask price, or None if no asks."""
        if self._order_book is None or not self._order_book.asks.has_liquidity:
            return None
        return self._order_book.asks.best_price

    @property
    def circuit_breaker_floor(self) -> Decimal:
        """Minimum allowed price (oracle - 5%)."""
        return (Decimal(str(self.oracle_price)) * Decimal("0.95")).quantize(Decimal("0.01"))

    @property
    def circuit_breaker_ceiling(self) -> Decimal:
        """Maximum allowed price (oracle + 5%)."""
        return (Decimal(str(self.oracle_price)) * Decimal("1.05")).quantize(Decimal("0.01"))

    def get_usable_bid_price_for_qty(self, qty: str) -> Optional[Decimal]:
        """
        Get best bid price with sufficient quantity for a sell order.

        Args:
            qty: Required quantity.

        Returns:
            Best usable bid price, or None if insufficient liquidity.
        """
        if self._order_book is None:
            return None
        detector = LiquidityDetector(self.oracle_price)
        return detector.get_usable_bid_price(self._order_book, qty)

    def get_usable_ask_price_for_qty(self, qty: str) -> Optional[Decimal]:
        """
        Get best ask price with sufficient quantity for a buy order.

        Args:
            qty: Required quantity.

        Returns:
            Best usable ask price, or None if insufficient liquidity.
        """
        if self._order_book is None:
            return None
        detector = LiquidityDetector(self.oracle_price)
        return detector.get_usable_ask_price(self._order_book, qty)

    def get_safe_no_match_buy_price(self) -> Decimal:
        """
        Get a buy price guaranteed not to match any existing asks.

        Returns $10 - an extreme low price that will never match.
        """
        from tests.helpers.liquidity_detector import SAFE_NO_MATCH_BUY_PRICE
        return SAFE_NO_MATCH_BUY_PRICE

    def get_safe_no_match_sell_price(self) -> Decimal:
        """
        Get a sell price guaranteed not to match any existing bids.

        Returns $10,000,000 - an extreme high price that will never match.
        """
        from tests.helpers.liquidity_detector import SAFE_NO_MATCH_SELL_PRICE
        return SAFE_NO_MATCH_SELL_PRICE
