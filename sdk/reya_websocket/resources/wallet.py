"""Wallet-related WebSocket resources."""

from typing import TYPE_CHECKING

from sdk.reya_websocket.resources.common import (
    SubscribableParameterizedResource,
)

if TYPE_CHECKING:
    from sdk.reya_websocket.socket import ReyaSocket


class WalletResource:
    """Container for all wallet-related WebSocket resources."""

    def __init__(self, socket: "ReyaSocket"):
        """Initialize the wallet resource container.

        Args:
            socket: The WebSocket connection to use for this resource.
        """
        self.socket = socket
        self._positions = WalletPositionsResource(socket)
        self._trades = WalletTradesResource(socket)
        self._balances = WalletBalancesResource(socket)
        self._open_orders = WalletOpenOrdersResource(socket)

    def positions(self, address: str) -> "WalletPositionsSubscription":
        """Get positions for a specific wallet address.

        Args:
            address: The wallet address.

        Returns:
            A subscription object for the wallet positions.
        """
        return self._positions.for_wallet(address)

    def trades(self, address: str) -> "WalletTradesSubscription":
        """Get trades for a specific wallet address.

        Args:
            address: The wallet address.

        Returns:
            A subscription object for the wallet trades.
        """
        return self._trades.for_wallet(address)

    def balances(self, address: str) -> "WalletBalancesSubscription":
        """Get account balances for a specific wallet address.

        Args:
            address: The wallet address.

        Returns:
            A subscription object for the wallet balances.
        """
        return self._balances.for_wallet(address)

    def open_orders(self, address: str) -> "WalletOpenOrdersSubscription":
        """Get open orders for a specific wallet address.

        Args:
            address: The wallet address.

        Returns:
            A subscription object for the wallet open orders.
        """
        return self._open_orders.for_wallet(address)


class WalletPositionsResource(SubscribableParameterizedResource):
    """Resource for accessing wallet positions."""

    def __init__(self, socket: "ReyaSocket"):
        """Initialize the wallet positions resource.

        Args:
            socket: The WebSocket connection to use for this resource.
        """
        super().__init__(socket, "/api/trading/wallet/{address}/positions")

    def for_wallet(self, address: str) -> "WalletPositionsSubscription":
        """Create a subscription for a specific wallet's positions.

        Args:
            address: The wallet address.

        Returns:
            A subscription object for the wallet positions.
        """
        return WalletPositionsSubscription(self.socket, address)


class WalletTradesResource(SubscribableParameterizedResource):
    """Resource for accessing wallet trades."""

    def __init__(self, socket: "ReyaSocket"):
        """Initialize the wallet trades resource.

        Args:
            socket: The WebSocket connection to use for this resource.
        """
        super().__init__(socket, "/api/trading/wallet/{address}/trades")

    def for_wallet(self, address: str) -> "WalletTradesSubscription":
        """Create a subscription for a specific wallet's trades.

        Args:
            address: The wallet address.

        Returns:
            A subscription object for the wallet trades.
        """
        return WalletTradesSubscription(self.socket, address)


class WalletBalancesResource(SubscribableParameterizedResource):
    """Resource for accessing wallet account balances."""

    def __init__(self, socket: "ReyaSocket"):
        """Initialize the wallet balances resource.

        Args:
            socket: The WebSocket connection to use for this resource.
        """
        super().__init__(socket, "/api/trading/wallet/{address}/accounts/balances")

    def for_wallet(self, address: str) -> "WalletBalancesSubscription":
        """Create a subscription for a specific wallet's account balances.

        Args:
            address: The wallet address.

        Returns:
            A subscription object for the wallet balances.
        """
        return WalletBalancesSubscription(self.socket, address)


class WalletPositionsSubscription:
    """Manages a subscription to positions for a specific wallet."""

    def __init__(self, socket: "ReyaSocket", address: str):
        """Initialize a wallet positions subscription.

        Args:
            socket: The WebSocket connection to use for this subscription.
            address: The wallet address.
        """
        self.socket = socket
        self.address = address
        self.path = f"/api/trading/wallet/{address}/positions"

    def subscribe(self, batched: bool = False) -> None:
        """Subscribe to wallet positions.

        Args:
            batched: Whether to receive updates in batches.
        """
        self.socket.send_subscribe(channel=self.path, batched=batched)

    def unsubscribe(self) -> None:
        """Unsubscribe from wallet positions."""
        self.socket.send_unsubscribe(channel=self.path)


class WalletTradesSubscription:
    """Manages a subscription to trades for a specific wallet."""

    def __init__(self, socket: "ReyaSocket", address: str):
        """Initialize a wallet trades subscription.

        Args:
            socket: The WebSocket connection to use for this subscription.
            address: The wallet address.
        """
        self.socket = socket
        self.address = address
        self.path = f"/api/trading/wallet/{address}/trades"

    def subscribe(self, batched: bool = False) -> None:
        """Subscribe to wallet trades.

        Args:
            batched: Whether to receive updates in batches.
        """
        self.socket.send_subscribe(channel=self.path, batched=batched)

    def unsubscribe(self) -> None:
        """Unsubscribe from wallet trades."""
        self.socket.send_unsubscribe(channel=self.path)


class WalletBalancesSubscription:
    """Manages a subscription to account balances for a specific wallet."""

    def __init__(self, socket: "ReyaSocket", address: str):
        """Initialize a wallet balances subscription.

        Args:
            socket: The WebSocket connection to use for this subscription.
            address: The wallet address.
        """
        self.socket = socket
        self.address = address
        self.path = f"/api/trading/wallet/{address}/accounts/balances"

    def subscribe(self, batched: bool = False) -> None:
        """Subscribe to wallet account balances.

        Args:
            batched: Whether to receive updates in batches.
        """
        self.socket.send_subscribe(channel=self.path, batched=batched)

    def unsubscribe(self) -> None:
        """Unsubscribe from wallet account balances."""
        self.socket.send_unsubscribe(channel=self.path)


class WalletOpenOrdersResource(SubscribableParameterizedResource):
    """Resource for accessing wallet open orders."""

    def __init__(self, socket: "ReyaSocket"):
        """Initialize the wallet open orders resource.

        Args:
            socket: The WebSocket connection to use for this resource.
        """
        super().__init__(socket, "/api/trading/wallet/{address}/openOrders")

    def for_wallet(self, address: str) -> "WalletOpenOrdersSubscription":
        """Create a subscription for a specific wallet's open orders.

        Args:
            address: The wallet address.

        Returns:
            A subscription object for the wallet open orders.
        """
        return WalletOpenOrdersSubscription(self.socket, address)


class WalletOpenOrdersSubscription:
    """Manages a subscription to open orders for a specific wallet."""

    def __init__(self, socket: "ReyaSocket", address: str):
        """Initialize a wallet open orders subscription.

        Args:
            socket: The WebSocket connection to use for this subscription.
            address: The wallet address.
        """
        self.socket = socket
        self.address = address
        self.path = f"/api/trading/wallet/{address}/openOrders"

    def subscribe(self, batched: bool = False) -> None:
        """Subscribe to wallet open orders.

        Args:
            batched: Whether to receive updates in batches.
        """
        self.socket.send_subscribe(channel=self.path, batched=batched)

    def unsubscribe(self) -> None:
        """Unsubscribe from wallet open orders."""
        self.socket.send_unsubscribe(channel=self.path)
