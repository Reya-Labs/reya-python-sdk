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

from typing import TYPE_CHECKING, Any

from dataclasses import dataclass, field

from sdk.open_api.models.order import Order
from sdk.open_api.models.order_type import OrderType
from sdk.open_api.models.side import Side
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api.models import LimitOrderParameters, ModifyOrderParameters, TriggerOrderParameters

if TYPE_CHECKING:
    from tests.helpers.market_config import SpotTestConfig


def full_state_modify_params(order: Order, **overrides: Any) -> ModifyOrderParameters:
    """Build the COMPLETE post-modify state for a fetched resting order.

    ``modifyOrder`` has no omitted-means-inherited shorthand: the four
    modifiable fields (``limit_px``, ``qty``, ``post_only``, ``expires_after``)
    must all carry the full intended post-modify state, and the signed
    immutables (``is_buy``, ``time_in_force``, ``trigger_px``, ``reduce_only``)
    must restate the resting order's values. This helper restates everything
    from the fetched :class:`Order`, targets it by ``order_id``, then applies
    ``overrides`` — so a test only spells out what it actually changes::

        order = await tester.data.open_order(order_id)
        params = full_state_modify_params(order, limit_px="2950", qty="0.75")

    Overriding ``client_order_id`` switches targeting to it (clearing
    ``order_id``) unless ``order_id`` is also explicitly overridden, keeping
    the builder's exactly-one-target rule satisfied by default.

    With ``client_order_id`` targeting, the same value is also the restated
    immutable signed into ``OrderDetails.clientOrderId``.
    """
    if order.qty is None:
        raise ValueError(f"Order {order.order_id} has no qty; cannot restate the post-modify state")

    fields: dict[str, Any] = {
        "symbol": order.symbol,
        "is_buy": order.side == Side.B,
        "limit_px": order.limit_px,
        "qty": order.qty,
        "post_only": bool(order.post_only) if order.post_only is not None else False,
        "expires_after": order.expires_after if order.expires_after is not None else 0,
        "time_in_force": order.time_in_force if order.time_in_force is not None else TimeInForce.GTC,
        "order_id": int(order.order_id),
        "trigger_px": order.trigger_px,
        "reduce_only": bool(order.reduce_only) if order.reduce_only is not None else False,
    }
    if "client_order_id" in overrides and "order_id" not in overrides:
        fields["order_id"] = None
    fields.update(overrides)
    return ModifyOrderParameters(**fields)


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

    _config: SpotTestConfig | None = None
    _symbol: str = "ETHRUSD"
    _is_buy: bool = True
    _qty: str = "0.01"
    _limit_px: str = "4000.0"
    _time_in_force: TimeInForce = field(default_factory=lambda: TimeInForce.GTC)
    _reduce_only: bool | None = None
    _post_only: bool | None = None
    _expires_after: int | None = None
    _client_order_id: int | None = None

    def __post_init__(self):
        """Apply config defaults if provided."""
        if self._config is not None:
            self._symbol = self._config.symbol
            self._qty = self._config.min_qty

    @classmethod
    def from_config(cls, config: SpotTestConfig) -> OrderBuilder:
        """Create an OrderBuilder pre-configured with SpotTestConfig defaults."""
        builder = cls()
        builder._config = config
        builder._symbol = config.symbol
        builder._qty = config.min_qty
        return builder

    def symbol(self, symbol: str) -> OrderBuilder:
        """Set the trading symbol (e.g., 'ETHRUSD', 'ETHRUSDPERP')."""
        self._symbol = symbol
        return self

    def spot(self, base: str = "ETH") -> OrderBuilder:
        """Set symbol to a spot market (e.g., 'ETHRUSD')."""
        self._symbol = f"{base}RUSD"
        return self

    def perp(self, base: str = "ETH") -> OrderBuilder:
        """Set symbol to a perp market (e.g., 'ETHRUSDPERP')."""
        self._symbol = f"{base}RUSDPERP"
        return self

    def buy(self) -> OrderBuilder:
        """Set order side to BUY."""
        self._is_buy = True
        return self

    def sell(self) -> OrderBuilder:
        """Set order side to SELL."""
        self._is_buy = False
        return self

    def side(self, is_buy: bool) -> OrderBuilder:
        """Set order side explicitly."""
        self._is_buy = is_buy
        return self

    def qty(self, qty: str) -> OrderBuilder:
        """Set the order quantity."""
        self._qty = qty
        return self

    def price(self, price: str) -> OrderBuilder:
        """Set the limit price."""
        self._limit_px = price
        return self

    def limit_px(self, price: str) -> OrderBuilder:
        """Alias for price() - set the limit price."""
        self._limit_px = price
        return self

    def at_price(self, multiplier: float) -> OrderBuilder:
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

    def gtc(self) -> OrderBuilder:
        """Set time-in-force to Good-Till-Cancelled."""
        self._time_in_force = TimeInForce.GTC
        return self

    def ioc(self) -> OrderBuilder:
        """Set time-in-force to Immediate-Or-Cancel."""
        self._time_in_force = TimeInForce.IOC
        return self

    def gtt(self, expires_after: int | None = None) -> OrderBuilder:
        """Set time-in-force to Good-Till-Time (rests until it auto-expires).

        GTT is the ONLY time-in-force that carries an order lifetime: the SDK
        requires a non-zero ``expires_after`` strictly after the deadline (GTC
        rests until cancelled and IOC never rests, so both sign ``expires_after``
        0). Pass it here, or set it separately via :meth:`expires_after`; either
        way the build is rejected without one.
        """
        self._time_in_force = TimeInForce.GTT
        if expires_after is not None:
            self._expires_after = expires_after
        return self

    def time_in_force(self, tif: TimeInForce) -> OrderBuilder:
        """Set time-in-force explicitly."""
        self._time_in_force = tif
        return self

    def reduce_only(self, value: bool = True) -> OrderBuilder:
        """Set reduce_only flag (only valid for IOC orders)."""
        self._reduce_only = value
        return self

    def post_only(self, value: bool = True) -> OrderBuilder:
        """Set the post-only (maker-only) flag (GTC only; rejected on IOC)."""
        self._post_only = value
        return self

    def expires_after(self, timestamp_ms: int) -> OrderBuilder:
        """Set the on-chain order lifetime (``expiresAfter``).

        Only GTT carries a lifetime, and the SDK requires it non-zero and
        strictly after the deadline; GTC and IOC must sign ``expiresAfter`` 0
        (GTC rests until cancelled, IOC never rests). Use with :meth:`gtt`.
        """
        self._expires_after = timestamp_ms
        return self

    def client_order_id(self, client_order_id: int) -> OrderBuilder:
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
            post_only=self._post_only,
            expires_after=self._expires_after,
            client_order_id=self._client_order_id,
        )

    def copy(self) -> OrderBuilder:
        """Create a copy of this builder with the same settings."""
        builder = OrderBuilder()
        # Use object.__setattr__ to set dataclass fields directly
        # This avoids protected-access warnings while still copying internal state
        for field_name in [
            "_config",
            "_symbol",
            "_is_buy",
            "_qty",
            "_limit_px",
            "_time_in_force",
            "_reduce_only",
            "_post_only",
            "_expires_after",
            "_client_order_id",
        ]:
            setattr(builder, field_name, getattr(self, field_name))
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
    _qty: str = "0.01"
    _trigger_px: str = "4000.0"
    _limit_px: str = "4000.0"
    _trigger_type: OrderType = field(default_factory=lambda: OrderType.TAKE_PROFIT)
    _reduce_only: bool | None = None
    _client_order_id: int | None = None

    def symbol(self, symbol: str) -> TriggerOrderBuilder:
        """Set the trading symbol."""
        self._symbol = symbol
        return self

    def perp(self, base: str = "ETH") -> TriggerOrderBuilder:
        """Set symbol to a perp market."""
        self._symbol = f"{base}RUSDPERP"
        return self

    def buy(self) -> TriggerOrderBuilder:
        """Set order side to BUY (close short position)."""
        self._is_buy = True
        return self

    def sell(self) -> TriggerOrderBuilder:
        """Set order side to SELL (close long position)."""
        self._is_buy = False
        return self

    def qty(self, qty: str) -> TriggerOrderBuilder:
        """Set the quantity to execute when the trigger fires."""
        self._qty = qty
        return self

    def trigger_price(self, price: str) -> TriggerOrderBuilder:
        """Set the trigger price."""
        self._trigger_px = price
        return self

    def trigger_px(self, price: str) -> TriggerOrderBuilder:
        """Alias for trigger_price()."""
        self._trigger_px = price
        return self

    def limit_px(self, price: str) -> TriggerOrderBuilder:
        """Set the worst-acceptable execution price after the trigger fires."""
        self._limit_px = price
        return self

    def take_profit(self) -> TriggerOrderBuilder:
        """Set trigger type to Take Profit."""
        self._trigger_type = OrderType.TAKE_PROFIT
        return self

    def stop_loss(self) -> TriggerOrderBuilder:
        """Set trigger type to Stop Loss."""
        self._trigger_type = OrderType.STOP_LOSS
        return self

    def tp(self) -> TriggerOrderBuilder:
        """Alias for take_profit()."""
        return self.take_profit()

    def sl(self) -> TriggerOrderBuilder:
        """Alias for stop_loss()."""
        return self.stop_loss()

    def reduce_only(self, value: bool = True) -> TriggerOrderBuilder:
        """Mark the trigger order as reduce-only (cannot increase position size)."""
        self._reduce_only = value
        return self

    def client_order_id(self, client_order_id: int) -> TriggerOrderBuilder:
        """Set the caller-side order id used to dedupe + correlate cancels."""
        self._client_order_id = client_order_id
        return self

    def build(self) -> TriggerOrderParameters:
        """Build and return the TriggerOrderParameters."""
        return TriggerOrderParameters(
            symbol=self._symbol,
            is_buy=self._is_buy,
            qty=self._qty,
            trigger_px=self._trigger_px,
            limit_px=self._limit_px,
            trigger_type=self._trigger_type,
            reduce_only=self._reduce_only,
            client_order_id=self._client_order_id,
        )

    def copy(self) -> TriggerOrderBuilder:
        """Create a copy of this builder."""
        builder = TriggerOrderBuilder()
        for field_name in [
            "_symbol",
            "_is_buy",
            "_qty",
            "_trigger_px",
            "_limit_px",
            "_trigger_type",
            "_reduce_only",
            "_client_order_id",
        ]:
            setattr(builder, field_name, getattr(self, field_name))
        return builder
