"""Centralized per-market test configuration with smart liquidity detection.

This module provides:
1. SpotMarketConfig - Immutable spot market configuration fetched from API
2. MarketTestConfig - Test configuration with dynamic state and liquidity
   detection, shared by BOTH market types. ``SpotTestConfig`` and
   ``PerpTestConfig`` are compatibility aliases: the two classes were
   historically separate (PerpTestConfig lived in tests/engine/
   conftest.py mirroring SpotTestConfig's shape) but method-for-method
   identical, so they were unified when this module was promoted to
   tests/helpers/.
3. fetch_spot_market_configs() - Fetches market configs from /v2/spotMarketDefinitions

The config is injected via pytest fixtures (``spot_config`` /
``perp_market_config`` / the parametrized ``market_config``) - no global
state is used.

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

from __future__ import annotations

from typing import TYPE_CHECKING

import logging
from dataclasses import dataclass, field
from decimal import Decimal

from tests.helpers.liquidity_detector import (
    SAFE_NO_MATCH_BUY_PRICE,
    SAFE_NO_MATCH_SELL_PRICE,
    LiquidityDetector,
    OrderBookState,
    log_order_book_state,
)
from tests.helpers.price_helpers import (
    floor_price,
    format_price,
    limit_price,
    one_tick_below,
    one_tick_inside_spread,
    price_tick,
)

if TYPE_CHECKING:
    from sdk.reya_rest_api.client import ReyaTradingClient
    from tests.helpers.reya_tester.data import DataOperations

logger = logging.getLogger("reya.integration_tests")

# Buffer multiplier for minimum balance calculation
# We need enough balance for ~10 trades at min_qty with safety margin
BALANCE_BUFFER_MULTIPLIER = 50


@dataclass(frozen=True)
class SpotMarketConfig:
    """
    Configuration for a spot market fetched from the API.

    This is immutable (frozen) to prevent accidental modification.
    """

    symbol: str
    market_id: int
    base_asset: str
    quote_asset: str
    min_order_qty: str
    qty_step_size: str
    tick_size: str

    @property
    def oracle_symbol(self) -> str:
        """Derive the oracle price symbol (uses PERP symbol format)."""
        return f"{self.base_asset}RUSDPERP"

    @property
    def min_balance(self) -> Decimal:
        """
        Calculate minimum balance needed for tests.

        Uses min_order_qty * buffer to ensure enough balance for all trades.
        """
        return Decimal(self.min_order_qty) * BALANCE_BUFFER_MULTIPLIER

    @property
    def wrapped_base_asset(self) -> str:
        """Get the wrapped token symbol (e.g., ETH -> WETH, BTC -> WBTC)."""
        return f"W{self.base_asset}"


async def fetch_spot_market_configs(
    client: ReyaTradingClient,
) -> dict[str, SpotMarketConfig]:
    """
    Fetch all spot market configurations from the API.

    Args:
        client: ReyaTradingClient instance for making API calls.

    Returns:
        Dictionary mapping base_asset (e.g., "ETH", "BTC") to SpotMarketConfig.

    Raises:
        Exception: If API call fails or returns invalid data.
    """
    spot_definitions = await client.reference.get_spot_market_definitions()

    configs: dict[str, SpotMarketConfig] = {}

    for item in spot_definitions:
        config = SpotMarketConfig(
            symbol=item.symbol,
            market_id=item.market_id,
            base_asset=item.base_asset,
            quote_asset=item.quote_asset,
            min_order_qty=item.min_order_qty,
            qty_step_size=item.qty_step_size,
            tick_size=item.tick_size,
        )
        configs[config.base_asset] = config

    return configs


def get_available_assets(configs: dict[str, SpotMarketConfig]) -> list[str]:
    """Get list of available asset names from configs."""
    return sorted(configs.keys())


@dataclass
class MarketTestConfig:
    """
    Centralized per-market test configuration with smart liquidity detection.

    This dataclass is populated by a pytest fixture at session start,
    ensuring all tests use consistent, dynamically-fetched values. The same
    class serves spot markets (via the ``spot_config`` fixture) and perp
    markets (via ``perp_market_config``) — the liquidity-detection surface
    and price helpers are market-type-independent.

    Attributes:
        symbol: The market symbol (e.g., "WETHRUSD", "ETHRUSDPERP")
        market_id: The market ID (spot: on-chain id; perp: raw id)
        min_qty: Minimum order quantity as string (e.g., "0.001" for ETH)
        qty_step_size: Quantity step size for orders
        oracle_price: Current oracle price for the underlying asset
        base_asset: The base asset symbol (e.g., "ETH", "BTC") - used for balance checks
        min_balance: Minimum balance required for tests (computed from min_qty * buffer)
    """

    symbol: str
    market_id: int
    min_qty: str
    qty_step_size: str
    oracle_price: float
    base_asset: str
    min_balance: float
    tick_size: str = "0"
    _order_book: OrderBookState | None = field(default=None, repr=False)

    def price(self, multiplier: float = 1.0) -> float:
        """
        Get a price based on oracle price with optional multiplier.

        Args:
            multiplier: Price multiplier (e.g., 0.99 for 99% of oracle)

        Returns:
            Rounded price to 2 decimal places
        """
        return round(self.oracle_price * multiplier, 2)

    @property
    def price_tick(self) -> Decimal:
        return price_tick(self.tick_size)

    def limit_price(self, multiplier: Decimal | float | str = 1) -> Decimal:
        return limit_price(self.oracle_price, self.tick_size, multiplier)

    def limit_price_str(self, multiplier: Decimal | float | str = 1) -> str:
        return format_price(self.limit_price(multiplier))

    def floor_price(self, multiplier: Decimal | float | str = 1) -> Decimal:
        return floor_price(self.oracle_price, self.tick_size, multiplier)

    def buy_price(self, multiplier: float = 0.99) -> float:
        """Get a buy price (default 99% of oracle - within deviation limit)."""
        return self.price(multiplier)

    def sell_price(self, multiplier: float = 1.01) -> float:
        """Get a sell price (default 101% of oracle - within deviation limit)."""
        return self.price(multiplier)

    async def refresh_order_book(self, data_ops: DataOperations) -> OrderBookState:
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
    def order_book(self) -> OrderBookState | None:
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
    def best_bid_price(self) -> Decimal | None:
        """Get the best bid price, or None if no bids."""
        if self._order_book is None or not self._order_book.bids.has_liquidity:
            return None
        return self._order_book.bids.best_price

    @property
    def best_ask_price(self) -> Decimal | None:
        """Get the best ask price, or None if no asks."""
        if self._order_book is None or not self._order_book.asks.has_liquidity:
            return None
        return self._order_book.asks.best_price

    def maker_bid_above_external_bid(self) -> Decimal | None:
        return one_tick_inside_spread(
            best_bid=self.best_bid_price,
            best_ask=self.best_ask_price,
            tick_size=self.tick_size,
        )

    def maker_ask_below_external_ask(self) -> Decimal | None:
        if self.best_ask_price is None:
            return None

        candidate = one_tick_below(self.best_ask_price, self.tick_size)
        if candidate is None:
            return None
        if self.best_bid_price is not None and candidate <= self.best_bid_price:
            return None
        return candidate

    @property
    def circuit_breaker_floor(self) -> Decimal:
        """Minimum allowed price (oracle - 5%)."""
        return (Decimal(str(self.oracle_price)) * Decimal("0.95")).quantize(Decimal("0.01"))

    @property
    def circuit_breaker_ceiling(self) -> Decimal:
        """Maximum allowed price (oracle + 5%)."""
        return (Decimal(str(self.oracle_price)) * Decimal("1.05")).quantize(Decimal("0.01"))

    def get_usable_bid_price_for_qty(self, qty: str) -> Decimal | None:
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

    def get_usable_ask_price_for_qty(self, qty: str) -> Decimal | None:
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
        return SAFE_NO_MATCH_BUY_PRICE

    def get_safe_no_match_sell_price(self) -> Decimal:
        """
        Get a sell price guaranteed not to match any existing bids.

        Returns ``SAFE_NO_MATCH_SELL_PRICE`` - a high price that will never match.
        """
        return SAFE_NO_MATCH_SELL_PRICE


# Compatibility aliases — the historically-separate spot/perp config classes
# were method-for-method identical and are now one class. Existing signatures
# (`SpotTestConfig | PerpTestConfig` unions, `MarketConfig`) all resolve here.
SpotTestConfig = MarketTestConfig
PerpTestConfig = MarketTestConfig
MarketConfig = MarketTestConfig
