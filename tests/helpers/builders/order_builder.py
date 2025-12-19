"""
Fluent order builder for creating test orders.

This module provides a builder pattern for constructing order parameters,
inspired by the Rust OrderBuilder in reya-chain/crates/matching-engine/tests/helpers.rs.

Example usage:
    # Create a simple GTC buy order with SpotTestConfig
    params = (
        OrderBuilder(spot_config)
        .buy()
        .at_price(0.99)  # 99% of oracle price
        .build()
    )

    # Create an IOC sell order with explicit values
    params = (
        OrderBuilder()
        .symbol("ETHRUSDPERP")
        .sell()
        .qty("0.05")
        .price("3950.0")
        .ioc()
        .reduce_only()
        .build()
    )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional, Union

from sdk.open_api.models.order_type import OrderType
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api.models import LimitOrderParameters, TriggerOrderParameters

if TYPE_CHECKING:
    from tests.test_spot.spot_config import SpotTestConfig


@dataclass
class OrderBuilder:
    """
    Fluent builder for creating LimitOrderParameters.

    Provides a chainable API for constructing order parameters with sensible defaults.
    All setter methods return self to enable method chaining.

    Can optionally accept a SpotTestConfig for convenient price/qty/symbol defaults.

    Note: LimitOrderParameters only contains order-specific fields.
    Account/exchange IDs are handled by the SDK client.
    """

    _config: Optional["SpotTestConfig"] = None
    _symbol: str = "ETHRUSD"
    _is_buy: bool = True
    _qty: str = "0.01"
    _limit_px: str = "4000.0"
    _time_in_force: TimeInForce = field(default_factory=lambda: TimeInForce.GTC)
    _reduce_only: Optional[bool] = None
    _expires_after: Optional[int] = None
    _client_order_id: Optional[int] = None

    def __post_init__(self):
        """Apply config defaults if provided."""
        if self._config is not None:
            self._symbol = self._config.symbol
            self._qty = self._config.min_qty

    @classmethod
    def from_config(cls, config: "SpotTestConfig") -> "OrderBuilder":
        """Create an OrderBuilder pre-configured with SpotTestConfig defaults."""
        builder = cls()
        builder._config = config
        builder._symbol = config.symbol
        builder._qty = config.min_qty
        return builder

    def symbol(self, symbol: str) -> "OrderBuilder":
        """Set the trading symbol (e.g., 'ETHRUSD', 'ETHRUSDPERP')."""
        self._symbol = symbol
        return self

    def spot(self, base: str = "ETH") -> "OrderBuilder":
        """Set symbol to a spot market (e.g., 'ETHRUSD')."""
        self._symbol = f"{base}RUSD"
        return self

    def perp(self, base: str = "ETH") -> "OrderBuilder":
        """Set symbol to a perp market (e.g., 'ETHRUSDPERP')."""
        self._symbol = f"{base}RUSDPERP"
        return self

    def buy(self) -> "OrderBuilder":
        """Set order side to BUY."""
        self._is_buy = True
        return self

    def sell(self) -> "OrderBuilder":
        """Set order side to SELL."""
        self._is_buy = False
        return self

    def side(self, is_buy: bool) -> "OrderBuilder":
        """Set order side explicitly."""
        self._is_buy = is_buy
        return self

    def qty(self, qty: str) -> "OrderBuilder":
        """Set the order quantity."""
        self._qty = qty
        return self

    def price(self, price: str) -> "OrderBuilder":
        """Set the limit price."""
        self._limit_px = price
        return self

    def limit_px(self, price: str) -> "OrderBuilder":
        """Alias for price() - set the limit price."""
        self._limit_px = price
        return self

    def at_price(self, multiplier: float) -> "OrderBuilder":
        """
        Set price as a multiplier of the oracle price from config.
        
        Requires OrderBuilder to be created with a SpotTestConfig.
        
        Args:
            multiplier: Price multiplier (e.g., 0.99 for 99% of oracle)
            
        Returns:
            self for method chaining
            
        Raises:
            ValueError: If no config was provided to the builder
        """
        if self._config is None:
            raise ValueError("at_price() requires OrderBuilder to be created with a SpotTestConfig")
        self._limit_px = str(self._config.price(multiplier))
        return self

    def gtc(self) -> "OrderBuilder":
        """Set time-in-force to Good-Till-Cancelled."""
        self._time_in_force = TimeInForce.GTC
        return self

    def ioc(self) -> "OrderBuilder":
        """Set time-in-force to Immediate-Or-Cancel."""
        self._time_in_force = TimeInForce.IOC
        return self

    def time_in_force(self, tif: TimeInForce) -> "OrderBuilder":
        """Set time-in-force explicitly."""
        self._time_in_force = tif
        return self

    def reduce_only(self, value: bool = True) -> "OrderBuilder":
        """Set reduce_only flag (only valid for IOC orders)."""
        self._reduce_only = value
        return self

    def expires_after(self, timestamp_ms: int) -> "OrderBuilder":
        """Set expiration timestamp in milliseconds (IOC orders only)."""
        self._expires_after = timestamp_ms
        return self

    def client_order_id(self, client_order_id: int) -> "OrderBuilder":
        """Set a client-provided order ID for tracking."""
        self._client_order_id = client_order_id
        return self

    def build(self) -> LimitOrderParameters:
        """
        Build and return the LimitOrderParameters.

        Returns:
            LimitOrderParameters configured with all set values.
        """
        return LimitOrderParameters(
            symbol=self._symbol,
            is_buy=self._is_buy,
            qty=self._qty,
            limit_px=self._limit_px,
            time_in_force=self._time_in_force,
            reduce_only=self._reduce_only,
            expires_after=self._expires_after,
            client_order_id=self._client_order_id,
        )

    def copy(self) -> "OrderBuilder":
        """Create a copy of this builder with the same settings."""
        builder = OrderBuilder()
        builder._config = self._config
        builder._symbol = self._symbol
        builder._is_buy = self._is_buy
        builder._qty = self._qty
        builder._limit_px = self._limit_px
        builder._time_in_force = self._time_in_force
        builder._reduce_only = self._reduce_only
        builder._expires_after = self._expires_after
        builder._client_order_id = self._client_order_id
        return builder


@dataclass
class TriggerOrderBuilder:
    """
    Fluent builder for creating TriggerOrderParameters (TP/SL orders).

    Example usage:
        # Create a take-profit order
        params = (
            TriggerOrderBuilder()
            .symbol("ETHRUSDPERP")
            .take_profit()
            .trigger_price("4200.0")
            .buy()
            .build()
        )
    """

    _symbol: str = "ETHRUSDPERP"
    _is_buy: bool = True
    _trigger_px: str = "4000.0"
    _trigger_type: OrderType = field(default_factory=lambda: OrderType.TP)

    def symbol(self, symbol: str) -> "TriggerOrderBuilder":
        """Set the trading symbol."""
        self._symbol = symbol
        return self

    def perp(self, base: str = "ETH") -> "TriggerOrderBuilder":
        """Set symbol to a perp market."""
        self._symbol = f"{base}RUSDPERP"
        return self

    def buy(self) -> "TriggerOrderBuilder":
        """Set order side to BUY (close short position)."""
        self._is_buy = True
        return self

    def sell(self) -> "TriggerOrderBuilder":
        """Set order side to SELL (close long position)."""
        self._is_buy = False
        return self

    def trigger_price(self, price: str) -> "TriggerOrderBuilder":
        """Set the trigger price."""
        self._trigger_px = price
        return self

    def trigger_px(self, price: str) -> "TriggerOrderBuilder":
        """Alias for trigger_price()."""
        self._trigger_px = price
        return self

    def take_profit(self) -> "TriggerOrderBuilder":
        """Set trigger type to Take Profit."""
        self._trigger_type = OrderType.TP
        return self

    def stop_loss(self) -> "TriggerOrderBuilder":
        """Set trigger type to Stop Loss."""
        self._trigger_type = OrderType.SL
        return self

    def tp(self) -> "TriggerOrderBuilder":
        """Alias for take_profit()."""
        return self.take_profit()

    def sl(self) -> "TriggerOrderBuilder":
        """Alias for stop_loss()."""
        return self.stop_loss()

    def build(self) -> TriggerOrderParameters:
        """Build and return the TriggerOrderParameters."""
        return TriggerOrderParameters(
            symbol=self._symbol,
            is_buy=self._is_buy,
            trigger_px=self._trigger_px,
            trigger_type=self._trigger_type,
        )

    def copy(self) -> "TriggerOrderBuilder":
        """Create a copy of this builder."""
        builder = TriggerOrderBuilder()
        builder._symbol = self._symbol
        builder._is_buy = self._is_buy
        builder._trigger_px = self._trigger_px
        builder._trigger_type = self._trigger_type
        return builder
