"""Price-related WebSocket resources for v2 API."""

from typing import TYPE_CHECKING

from sdk.reya_websocket.resources.common import (
    SubscribableParameterizedResource,
    SubscribableResource,
)

if TYPE_CHECKING:
    from sdk.reya_websocket.socket import ReyaSocket


class PricesResource:
    """Container for all price-related WebSocket resources for v2 API."""

    def __init__(self, socket: "ReyaSocket"):
        """Initialize the prices resource container.

        Args:
            socket: The WebSocket connection to use for this resource.
        """
        self.socket = socket
        self._all_prices = AllPricesResource(socket)
        self._price = PriceResource(socket)

    @property
    def all_prices(self) -> "AllPricesResource":
        """Access the all prices resource."""
        return self._all_prices

    def price(self, symbol: str) -> "PriceSubscription":
        """Get price data for a specific symbol.

        Args:
            symbol: The trading symbol (e.g., "BTCRUSDPERP", "ETHRUSD").

        Returns:
            A subscription object for the specified symbol's price data.
        """
        return self._price.for_symbol(symbol)


class AllPricesResource(SubscribableResource):
    """Resource for accessing all prices data."""

    def __init__(self, socket: "ReyaSocket"):
        """Initialize the all prices resource.

        Args:
            socket: The WebSocket connection to use for this resource.
        """
        super().__init__(socket)
        self.path = "/v2/prices"

    def subscribe(self, batched: bool = False, **kwargs) -> None:
        """Subscribe to all prices data.

        Args:
            batched: Whether to receive updates in batches.
            **kwargs: Additional keyword arguments (unused).
        """
        self.socket.send_subscribe(channel=self.path, batched=batched)

    def unsubscribe(self, **kwargs) -> None:
        """Unsubscribe from all prices data.

        Args:
            **kwargs: Additional keyword arguments (unused).
        """
        self.socket.send_unsubscribe(channel=self.path)


class PriceResource(SubscribableParameterizedResource):
    """Resource for accessing price data for specific symbols."""

    def __init__(self, socket: "ReyaSocket"):
        """Initialize the price resource.

        Args:
            socket: The WebSocket connection to use for this resource.
        """
        super().__init__(socket, "/v2/prices/{symbol}")

    def for_symbol(self, symbol: str) -> "PriceSubscription":
        """Create a subscription for a specific symbol's price data.

        Args:
            symbol: The trading symbol (e.g., "BTCRUSDPERP", "ETHRUSD").

        Returns:
            A subscription object for the specified symbol's price data.
        """
        return PriceSubscription(self.socket, symbol)


class PriceSubscription:
    """Manages a subscription to price data for a specific symbol."""

    def __init__(self, socket: "ReyaSocket", symbol: str):
        """Initialize a price subscription.

        Args:
            socket: The WebSocket connection to use for this subscription.
            symbol: The trading symbol (e.g., "BTCRUSDPERP", "ETHRUSD").
        """
        self.socket = socket
        self.symbol = symbol
        self.path = f"/v2/prices/{symbol}"

    def subscribe(self, batched: bool = False) -> None:
        """Subscribe to price data.

        Args:
            batched: Whether to receive updates in batches.
        """
        self.socket.send_subscribe(channel=self.path, batched=batched)

    def unsubscribe(self) -> None:
        """Unsubscribe from price data."""
        self.socket.send_unsubscribe(channel=self.path)
