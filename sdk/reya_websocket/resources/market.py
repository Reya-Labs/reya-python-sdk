"""Market-related WebSocket resources for v2 API."""

from typing import TYPE_CHECKING

from sdk.reya_websocket.resources.common import (
    SubscribableParameterizedResource,
    SubscribableResource,
)

if TYPE_CHECKING:
    from sdk.reya_websocket.socket import ReyaSocket


class MarketResource:
    """Container for all market-related WebSocket resources for v2 API."""

    def __init__(self, socket: "ReyaSocket"):
        """Initialize the market resource container.

        Args:
            socket: The WebSocket connection to use for this resource.
        """
        self.socket = socket
        self._all_markets_summary = AllMarketsSummaryResource(socket)
        self._market_summary = MarketSummaryResource(socket)
        self._market_perp_executions = MarketPerpExecutionsResource(socket)

    @property
    def all_markets_summary(self) -> "AllMarketsSummaryResource":
        """Access the all markets summary resource."""
        return self._all_markets_summary

    def summary(self, symbol: str) -> "MarketSummarySubscription":
        """Get market summary for a specific symbol.

        Args:
            symbol: The trading symbol (e.g., "BTCRUSDPERP", "ETHRUSD").

        Returns:
            A subscription object for the specified market summary.
        """
        return self._market_summary.for_symbol(symbol)

    def perp_executions(self, symbol: str) -> "MarketPerpExecutionsSubscription":
        """Get perpetual executions for a specific symbol.

        Args:
            symbol: The trading symbol (e.g., "BTCRUSDPERP", "ETHRUSD").

        Returns:
            A subscription object for the specified market perpetual executions.
        """
        return self._market_perp_executions.for_symbol(symbol)


class AllMarketsSummaryResource(SubscribableResource):
    """Resource for accessing all markets summary data."""

    def __init__(self, socket: "ReyaSocket"):
        """Initialize the all markets summary resource.

        Args:
            socket: The WebSocket connection to use for this resource.
        """
        super().__init__(socket)
        self.path = "/v2/markets/summary"

    def subscribe(self, batched: bool = False, **kwargs) -> None:
        """Subscribe to all markets summary data.

        Args:
            batched: Whether to receive updates in batches.
            **kwargs: Additional keyword arguments (unused).
        """
        self.socket.send_subscribe(channel=self.path, batched=batched)

    def unsubscribe(self, **kwargs) -> None:
        """Unsubscribe from all markets summary data.

        Args:
            **kwargs: Additional keyword arguments (unused).
        """
        self.socket.send_unsubscribe(channel=self.path)


class MarketSummaryResource(SubscribableParameterizedResource):
    """Resource for accessing market summary data."""

    def __init__(self, socket: "ReyaSocket"):
        """Initialize the market summary resource.

        Args:
            socket: The WebSocket connection to use for this resource.
        """
        super().__init__(socket, "/v2/market/{symbol}/summary")

    def for_symbol(self, symbol: str) -> "MarketSummarySubscription":
        """Create a subscription for a specific market's summary.

        Args:
            symbol: The trading symbol (e.g., "BTCRUSDPERP", "ETHRUSD").

        Returns:
            A subscription object for the specified market summary.
        """
        return MarketSummarySubscription(self.socket, symbol)


class MarketPerpExecutionsResource(SubscribableParameterizedResource):
    """Resource for accessing market perpetual executions."""

    def __init__(self, socket: "ReyaSocket"):
        """Initialize the market perpetual executions resource.

        Args:
            socket: The WebSocket connection to use for this resource.
        """
        super().__init__(socket, "/v2/market/{symbol}/perpExecutions")

    def for_symbol(self, symbol: str) -> "MarketPerpExecutionsSubscription":
        """Create a subscription for a specific market's perpetual executions.

        Args:
            symbol: The trading symbol (e.g., "BTCRUSDPERP", "ETHRUSD").

        Returns:
            A subscription object for the specified market perpetual executions.
        """
        return MarketPerpExecutionsSubscription(self.socket, symbol)


class MarketSummarySubscription:
    """Manages a subscription to market summary for a specific symbol."""

    def __init__(self, socket: "ReyaSocket", symbol: str):
        """Initialize a market summary subscription.

        Args:
            socket: The WebSocket connection to use for this subscription.
            symbol: The trading symbol (e.g., "BTCRUSDPERP", "ETHRUSD").
        """
        self.socket = socket
        self.symbol = symbol
        self.path = f"/v2/market/{symbol}/summary"

    def subscribe(self, batched: bool = False) -> None:
        """Subscribe to market summary.

        Args:
            batched: Whether to receive updates in batches.
        """
        self.socket.send_subscribe(channel=self.path, batched=batched)

    def unsubscribe(self) -> None:
        """Unsubscribe from market summary."""
        self.socket.send_unsubscribe(channel=self.path)


class MarketPerpExecutionsSubscription:
    """Manages a subscription to market perpetual executions for a specific symbol."""

    def __init__(self, socket: "ReyaSocket", symbol: str):
        """Initialize a market perpetual executions subscription.

        Args:
            socket: The WebSocket connection to use for this subscription.
            symbol: The trading symbol (e.g., "BTCRUSDPERP", "ETHRUSD").
        """
        self.socket = socket
        self.symbol = symbol
        self.path = f"/v2/market/{symbol}/perpExecutions"

    def subscribe(self, batched: bool = False) -> None:
        """Subscribe to market perpetual executions.

        Args:
            batched: Whether to receive updates in batches.
        """
        self.socket.send_subscribe(channel=self.path, batched=batched)

    def unsubscribe(self) -> None:
        """Unsubscribe from market perpetual executions."""
        self.socket.send_unsubscribe(channel=self.path)
