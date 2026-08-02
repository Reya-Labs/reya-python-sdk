"""Market-related WebSocket resources for v2 API."""

from typing import TYPE_CHECKING, Any, Callable, cast

from sdk.reya_websocket.resources.common import SubscribableParameterizedResource, SubscribableResource

if TYPE_CHECKING:
    from sdk.reya_websocket.socket import ReyaSocket


class MarketResource:
    """Container for all market-related WebSocket resources for v2 API."""

    def __init__(self, socket: "ReyaSocket") -> None:
        """Initialize the market resource container.

        Args:
            socket: The WebSocket connection to use for this resource.
        """
        self.socket = socket
        self._all_markets_summary = AllMarketsSummaryResource(socket)
        self._market_summary = MarketSummaryResource(socket)
        self._all_spot_markets_summary = AllSpotMarketsSummaryResource(socket)
        self._spot_market_summary = SpotMarketSummaryResource(socket)
        self._market_perp_executions = MarketPerpExecutionsResource(socket)
        self._market_spot_executions = MarketSpotExecutionsResource(socket)
        self._market_execution_busts = MarketExecutionBustsResource(socket)
        self._market_depth = MarketDepthResource(socket)

    @property
    def all_markets_summary(self) -> "AllMarketsSummaryResource":
        """Access the all markets summary resource."""
        return self._all_markets_summary

    @property
    def all_spot_markets_summary(self) -> "AllSpotMarketsSummaryResource":
        """Access the all spot markets summary resource."""
        return self._all_spot_markets_summary

    def summary(self, symbol: str) -> "MarketSummarySubscription":
        """Get market summary for a specific symbol.

        Args:
            symbol: The perp trading symbol (e.g., "BTCRUSDPERP").

        Returns:
            A subscription object for the specified market summary.
        """
        return self._market_summary.for_symbol(symbol)

    def spot_summary(self, symbol: str) -> "SpotMarketSummarySubscription":
        """Get spot market summary for a specific symbol.

        Args:
            symbol: The spot trading symbol (e.g., "WETHRUSD").

        Returns:
            A subscription object for the specified spot market summary.
        """
        return self._spot_market_summary.for_symbol(symbol)

    def perp_executions(self, symbol: str) -> "MarketPerpExecutionsSubscription":
        """Get perpetual executions for a specific symbol.

        Args:
            symbol: The trading symbol (e.g., "BTCRUSDPERP", "ETHRUSD").

        Returns:
            A subscription object for the specified market perpetual executions.
        """
        return self._market_perp_executions.for_symbol(symbol)

    def spot_executions(self, symbol: str) -> "MarketSpotExecutionsSubscription":
        """Get spot executions for a specific symbol.

        Args:
            symbol: The trading symbol (e.g., "WETHRUSD", "BTCRUSD").

        Returns:
            A subscription object for the specified market spot executions.
        """
        return self._market_spot_executions.for_symbol(symbol)

    def execution_busts(self, symbol: str) -> "MarketExecutionBustsSubscription":
        """Get execution busts (failed fills) for a specific symbol.

        Unified across spot and perp markets. Pass a spot symbol (e.g.
        ``ETHRUSD``) or a perp symbol (e.g. ``ETHRUSDPERP``).

        Args:
            symbol: The trading symbol.

        Returns:
            A subscription object for the specified market execution busts.
        """
        return self._market_execution_busts.for_symbol(symbol)

    def depth(self, symbol: str) -> "MarketDepthSubscription":
        """Get L2 market depth (orderbook) for a specific symbol.

        Args:
            symbol: The trading symbol (e.g., "BTCRUSDPERP", "WETHRUSD").

        Returns:
            A subscription object for the specified market depth.
        """
        return self._market_depth.for_symbol(symbol)


class _AllMarketSummaryResource(SubscribableResource):
    """Shared implementation for all-market summary subscriptions."""

    def __init__(self, socket: "ReyaSocket", path: str) -> None:
        """Initialize the all-market summary resource.

        Args:
            socket: The WebSocket connection to use for this resource.
            path: The channel path to subscribe to.
        """
        super().__init__(socket)
        self.path = path

    def subscribe(self, batched: bool = False, **kwargs: Any) -> None:
        """Subscribe to all-market summary data.

        Args:
            batched: Whether to receive updates in batches.
            **kwargs: Additional keyword arguments (unused).
        """
        self.socket.send_subscribe(channel=self.path, batched=batched)

    def unsubscribe(self, **kwargs: Any) -> None:
        """Unsubscribe from all-market summary data.

        Args:
            **kwargs: Additional keyword arguments (unused).
        """
        self.socket.send_unsubscribe(channel=self.path)


class _MarketSummarySubscriptionBase:
    """Shared implementation for a symbol-specific market summary subscription."""

    def __init__(self, socket: "ReyaSocket", symbol: str, path_template: str) -> None:
        """Initialize a market summary subscription.

        Args:
            socket: The WebSocket connection to use for this subscription.
            symbol: The trading symbol.
            path_template: The channel path template for this market type.
        """
        self.socket = socket
        self.symbol = symbol
        self.path = path_template.format(symbol=symbol)

    def subscribe(self, batched: bool = False) -> None:
        """Subscribe to market summary.

        Args:
            batched: Whether to receive updates in batches.
        """
        self.socket.send_subscribe(channel=self.path, batched=batched)

    def unsubscribe(self) -> None:
        """Unsubscribe from market summary."""
        self.socket.send_unsubscribe(channel=self.path)


class MarketSummarySubscription(_MarketSummarySubscriptionBase):
    """Manages a subscription to perp market summary for a specific symbol."""

    def __init__(self, socket: "ReyaSocket", symbol: str) -> None:
        """Initialize a market summary subscription.

        Args:
            socket: The WebSocket connection to use for this subscription.
            symbol: The perp trading symbol (e.g., "BTCRUSDPERP").
        """
        super().__init__(socket, symbol, "/v2/perpMarket/{symbol}/summary")


class SpotMarketSummarySubscription(_MarketSummarySubscriptionBase):
    """Manages a subscription to spot market summary for a specific symbol."""

    def __init__(self, socket: "ReyaSocket", symbol: str) -> None:
        """Initialize a spot market summary subscription.

        Args:
            socket: The WebSocket connection to use for this subscription.
            symbol: The spot trading symbol (e.g., "WETHRUSD").
        """
        super().__init__(socket, symbol, "/v2/spotMarket/{symbol}/summary")


class _MarketSummaryResourceBase(SubscribableParameterizedResource):
    """Shared implementation for symbol-specific market summary resources."""

    def __init__(
        self,
        socket: "ReyaSocket",
        path_template: str,
        subscription_factory: Callable[..., _MarketSummarySubscriptionBase],
    ) -> None:
        """Initialize the market summary resource.

        Args:
            socket: The WebSocket connection to use for this resource.
            path_template: A template string for the channel path.
            subscription_factory: Constructor for a symbol-specific subscription.
        """
        super().__init__(socket, path_template)
        self._subscription_factory = subscription_factory

    def for_symbol(self, symbol: str) -> _MarketSummarySubscriptionBase:
        """Create a subscription for a specific market's summary.

        Args:
            symbol: The trading symbol.

        Returns:
            A subscription object for the specified market summary.
        """
        return self._subscription_factory(self.socket, symbol)


class AllMarketsSummaryResource(_AllMarketSummaryResource):
    """Resource for accessing all markets summary data."""

    def __init__(self, socket: "ReyaSocket") -> None:
        """Initialize the all markets summary resource.

        Args:
            socket: The WebSocket connection to use for this resource.
        """
        super().__init__(socket, "/v2/perpMarkets/summary")


class MarketSummaryResource(_MarketSummaryResourceBase):
    """Resource for accessing perp market summary data."""

    def __init__(self, socket: "ReyaSocket") -> None:
        """Initialize the market summary resource.

        Args:
            socket: The WebSocket connection to use for this resource.
        """
        super().__init__(socket, "/v2/perpMarket/{symbol}/summary", MarketSummarySubscription)

    def for_symbol(self, symbol: str) -> "MarketSummarySubscription":
        """Create a subscription for a specific market's summary.

        Args:
            symbol: The trading symbol (e.g., "BTCRUSDPERP", "ETHRUSD").

        Returns:
            A subscription object for the specified market summary.
        """
        return cast("MarketSummarySubscription", super().for_symbol(symbol))


class AllSpotMarketsSummaryResource(_AllMarketSummaryResource):
    """Resource for accessing all spot markets summary data."""

    def __init__(self, socket: "ReyaSocket") -> None:
        """Initialize the all spot markets summary resource.

        Args:
            socket: The WebSocket connection to use for this resource.
        """
        super().__init__(socket, "/v2/spotMarkets/summary")


class SpotMarketSummaryResource(_MarketSummaryResourceBase):
    """Resource for accessing spot market summary data."""

    def __init__(self, socket: "ReyaSocket") -> None:
        """Initialize the spot market summary resource.

        Args:
            socket: The WebSocket connection to use for this resource.
        """
        super().__init__(socket, "/v2/spotMarket/{symbol}/summary", SpotMarketSummarySubscription)

    def for_symbol(self, symbol: str) -> "SpotMarketSummarySubscription":
        """Create a subscription for a specific spot market's summary.

        Args:
            symbol: The spot trading symbol (e.g., "WETHRUSD").

        Returns:
            A subscription object for the specified spot market summary.
        """
        return cast("SpotMarketSummarySubscription", super().for_symbol(symbol))


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


class MarketSpotExecutionsResource(SubscribableParameterizedResource):
    """Resource for accessing market spot executions."""

    def __init__(self, socket: "ReyaSocket"):
        """Initialize the market spot executions resource.

        Args:
            socket: The WebSocket connection to use for this resource.
        """
        super().__init__(socket, "/v2/market/{symbol}/spotExecutions")

    def for_symbol(self, symbol: str) -> "MarketSpotExecutionsSubscription":
        """Create a subscription for a specific market's spot executions.

        Args:
            symbol: The trading symbol (e.g., "WETHRUSD", "BTCRUSD").

        Returns:
            A subscription object for the specified market spot executions.
        """
        return MarketSpotExecutionsSubscription(self.socket, symbol)


class MarketSpotExecutionsSubscription:
    """Manages a subscription to market spot executions for a specific symbol."""

    def __init__(self, socket: "ReyaSocket", symbol: str):
        """Initialize a market spot executions subscription.

        Args:
            socket: The WebSocket connection to use for this subscription.
            symbol: The trading symbol (e.g., "WETHRUSD", "BTCRUSD").
        """
        self.socket = socket
        self.symbol = symbol
        self.path = f"/v2/market/{symbol}/spotExecutions"

    def subscribe(self, batched: bool = False) -> None:
        """Subscribe to market spot executions.

        Args:
            batched: Whether to receive updates in batches.
        """
        self.socket.send_subscribe(channel=self.path, batched=batched)

    def unsubscribe(self) -> None:
        """Unsubscribe from market spot executions."""
        self.socket.send_unsubscribe(channel=self.path)


class MarketExecutionBustsResource(SubscribableParameterizedResource):
    """Resource for accessing market execution busts (unified spot + perp)."""

    def __init__(self, socket: "ReyaSocket"):
        """Initialize the market execution busts resource.

        Args:
            socket: The WebSocket connection to use for this resource.
        """
        super().__init__(socket, "/v2/market/{symbol}/executionBusts")

    def for_symbol(self, symbol: str) -> "MarketExecutionBustsSubscription":
        """Create a subscription for a specific market's execution busts.

        Args:
            symbol: The trading symbol (spot or perp, e.g. "WETHRUSD", "ETHRUSDPERP").

        Returns:
            A subscription object for the specified market's execution busts.
        """
        return MarketExecutionBustsSubscription(self.socket, symbol)


class MarketExecutionBustsSubscription:
    """Manages a subscription to market execution busts for a specific symbol."""

    def __init__(self, socket: "ReyaSocket", symbol: str):
        """Initialize a market execution busts subscription.

        Args:
            socket: The WebSocket connection to use for this subscription.
            symbol: The trading symbol (spot or perp).
        """
        self.socket = socket
        self.symbol = symbol
        self.path = f"/v2/market/{symbol}/executionBusts"

    def subscribe(self, batched: bool = False) -> None:
        """Subscribe to market execution busts.

        Args:
            batched: Whether to receive updates in batches.
        """
        self.socket.send_subscribe(channel=self.path, batched=batched)

    def unsubscribe(self) -> None:
        """Unsubscribe from market execution busts."""
        self.socket.send_unsubscribe(channel=self.path)


class MarketDepthResource(SubscribableParameterizedResource):
    """Resource for accessing market depth (L2 orderbook)."""

    def __init__(self, socket: "ReyaSocket"):
        """Initialize the market depth resource.

        Args:
            socket: The WebSocket connection to use for this resource.
        """
        super().__init__(socket, "/v2/market/{symbol}/depth")

    def for_symbol(self, symbol: str) -> "MarketDepthSubscription":
        """Create a subscription for a specific market's depth.

        Args:
            symbol: The trading symbol (e.g., "WETHRUSD", "BTCRUSD").

        Returns:
            A subscription object for the specified market depth.
        """
        return MarketDepthSubscription(self.socket, symbol)


class MarketDepthSubscription:
    """Manages a subscription to market depth for a specific symbol."""

    def __init__(self, socket: "ReyaSocket", symbol: str):
        """Initialize a market depth subscription.

        Args:
            socket: The WebSocket connection to use for this subscription.
            symbol: The trading symbol (e.g., "WETHRUSD", "BTCRUSD").
        """
        self.socket = socket
        self.symbol = symbol
        self.path = f"/v2/market/{symbol}/depth"

    def subscribe(self, batched: bool = False) -> None:
        """Subscribe to market depth.

        Args:
            batched: Whether to receive updates in batches.
        """
        self.socket.send_subscribe(channel=self.path, batched=batched)

    def unsubscribe(self) -> None:
        """Unsubscribe from market depth."""
        self.socket.send_unsubscribe(channel=self.path)
