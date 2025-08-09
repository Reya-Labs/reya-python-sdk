"""Market-related WebSocket resources."""

from typing import TYPE_CHECKING

from sdk.reya_websocket.resources.common import (
    SubscribableParameterizedResource,
    SubscribableResource,
)

if TYPE_CHECKING:
    from sdk.reya_websocket.socket import ReyaSocket


class MarketResource:
    """Container for all market-related WebSocket resources."""

    def __init__(self, socket: "ReyaSocket"):
        """Initialize the market resource container.

        Args:
            socket: The WebSocket connection to use for this resource.
        """
        self.socket = socket
        self._all_markets = AllMarketsResource(socket)
        self._market_data = MarketDataResource(socket)
        self._market_trades = MarketTradesResource(socket)

    @property
    def all_markets(self) -> "AllMarketsResource":
        """Access the all markets data resource."""
        return self._all_markets

    def market_data(self, market_id: str) -> "MarketDataSubscription":
        """Get market data for a specific market.

        Args:
            market_id: The ID of the market.

        Returns:
            A subscription object for the specified market data.
        """
        return self._market_data.for_market(market_id)

    def market_trades(self, market_id: str) -> "MarketTradesSubscription":
        """Get orders for a specific market.

        Args:
            market_id: The ID of the market.

        Returns:
            A subscription object for the specified market trades.
        """
        return self._market_trades.for_market(market_id)


class AllMarketsResource(SubscribableResource):
    """Resource for accessing all markets data."""

    def __init__(self, socket: "ReyaSocket"):
        """Initialize the all markets data resource.

        Args:
            socket: The WebSocket connection to use for this resource.
        """
        super().__init__(socket)
        self.path = "/api/trading/markets/data"

    def subscribe(self, batched: bool = False, **kwargs) -> None:
        """Subscribe to all markets data.

        Args:
            batched: Whether to receive updates in batches.
            **kwargs: Additional keyword arguments (unused).
        """
        self.socket.send_subscribe(channel=self.path, batched=batched)

    def unsubscribe(self, **kwargs) -> None:
        """Unsubscribe from all markets data.

        Args:
            **kwargs: Additional keyword arguments (unused).
        """
        self.socket.send_unsubscribe(channel=self.path)


class MarketDataResource(SubscribableParameterizedResource):
    """Resource for accessing market data."""

    def __init__(self, socket: "ReyaSocket"):
        """Initialize the market data resource.

        Args:
            socket: The WebSocket connection to use for this resource.
        """
        super().__init__(socket, "/api/trading/market/{market_id}/data")

    def for_market(self, market_id: str) -> "MarketDataSubscription":
        """Create a subscription for a specific market's data.

        Args:
            market_id: The ID of the market.

        Returns:
            A subscription object for the specified market data.
        """
        return MarketDataSubscription(self.socket, market_id)


class MarketTradesResource(SubscribableParameterizedResource):
    """Resource for accessing market trades."""

    def __init__(self, socket: "ReyaSocket"):
        """Initialize the market trades resource.

        Args:
            socket: The WebSocket connection to use for this resource.
        """
        super().__init__(socket, "/api/trading/market/{market_id}/trades")

    def for_market(self, market_id: str) -> "MarketTradesSubscription":
        """Create a subscription for a specific market's orders.

        Args:
            market_id: The ID of the market.

        Returns:
            A subscription object for the specified market trades.
        """
        return MarketTradesSubscription(self.socket, market_id)


class MarketDataSubscription:
    """Manages a subscription to market data for a specific market."""

    def __init__(self, socket: "ReyaSocket", market_id: str):
        """Initialize a market data subscription.

        Args:
            socket: The WebSocket connection to use for this subscription.
            market_id: The ID of the market.
        """
        self.socket = socket
        self.market_id = market_id
        self.path = f"/api/trading/market/{market_id}/data"

    def subscribe(self, batched: bool = False) -> None:
        """Subscribe to market data.

        Args:
            batched: Whether to receive updates in batches.
        """
        self.socket.send_subscribe(channel=self.path, batched=batched)

    def unsubscribe(self) -> None:
        """Unsubscribe from market data."""
        self.socket.send_unsubscribe(channel=self.path)


class MarketTradesSubscription:
    """Manages a subscription to market trades for a specific market."""

    def __init__(self, socket: "ReyaSocket", market_id: str):
        """Initialize a market trades subscription.

        Args:
            socket: The WebSocket connection to use for this subscription.
            market_id: The ID of the market.
        """
        self.socket = socket
        self.market_id = market_id
        self.path = f"/api/trading/market/{market_id}/trades"

    def subscribe(self, batched: bool = False) -> None:
        """Subscribe to market trades.

        Args:
            batched: Whether to receive updates in batches.
        """
        self.socket.send_subscribe(channel=self.path, batched=batched)

    def unsubscribe(self) -> None:
        """Unsubscribe from market trades."""
        self.socket.send_unsubscribe(channel=self.path)
