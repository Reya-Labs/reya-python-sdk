"""Wallet-related WebSocket resources for v2 API."""

from typing import TYPE_CHECKING

from sdk.reya_websocket.resources.common import (
    SubscribableParameterizedResource,
)

if TYPE_CHECKING:
    from sdk.reya_websocket.socket import ReyaSocket


class WalletResource:
    """Container for all wallet-related WebSocket resources for v2 API."""

    def __init__(self, socket: "ReyaSocket"):
        """Initialize the wallet resource container.

        Args:
            socket: The WebSocket connection to use for this resource.
        """
        self.socket = socket
        self._positions = WalletPositionsResource(socket)
        self._perp_executions = WalletPerpExecutionsResource(socket)
        self._account_balances = WalletAccountBalancesResource(socket)
        self._open_orders = WalletOpenOrdersResource(socket)

    def positions(self, address: str) -> "WalletPositionsSubscription":
        """Get positions for a specific wallet address.

        Args:
            address: The wallet address.

        Returns:
            A subscription object for the wallet positions.
        """
        return self._positions.for_wallet(address)

    def perp_executions(self, address: str) -> "WalletPerpExecutionsSubscription":
        """Get perpetual executions for a specific wallet address.

        Args:
            address: The wallet address.

        Returns:
            A subscription object for the wallet perpetual executions.
        """
        return self._perp_executions.for_wallet(address)

    def account_balances(self, address: str) -> "WalletAccountBalancesSubscription":
        """Get account balances for a specific wallet address.

        Args:
            address: The wallet address.

        Returns:
            A subscription object for the wallet account balances.
        """
        return self._account_balances.for_wallet(address)

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
        super().__init__(socket, "/v2/wallet/{address}/positions")

    def for_wallet(self, address: str) -> "WalletPositionsSubscription":
        """Create a subscription for a specific wallet's positions.

        Args:
            address: The wallet address.

        Returns:
            A subscription object for the wallet positions.
        """
        return WalletPositionsSubscription(self.socket, address)


class WalletPerpExecutionsResource(SubscribableParameterizedResource):
    """Resource for accessing wallet perpetual executions."""

    def __init__(self, socket: "ReyaSocket"):
        """Initialize the wallet perpetual executions resource.

        Args:
            socket: The WebSocket connection to use for this resource.
        """
        super().__init__(socket, "/v2/wallet/{address}/perpExecutions")

    def for_wallet(self, address: str) -> "WalletPerpExecutionsSubscription":
        """Create a subscription for a specific wallet's perpetual executions.

        Args:
            address: The wallet address.

        Returns:
            A subscription object for the wallet perpetual executions.
        """
        return WalletPerpExecutionsSubscription(self.socket, address)


class WalletAccountBalancesResource(SubscribableParameterizedResource):
    """Resource for accessing wallet account balances."""

    def __init__(self, socket: "ReyaSocket"):
        """Initialize the wallet account balances resource.

        Args:
            socket: The WebSocket connection to use for this resource.
        """
        super().__init__(socket, "/v2/wallet/{address}/accountBalances")

    def for_wallet(self, address: str) -> "WalletAccountBalancesSubscription":
        """Create a subscription for a specific wallet's account balances.

        Args:
            address: The wallet address.

        Returns:
            A subscription object for the wallet account balances.
        """
        return WalletAccountBalancesSubscription(self.socket, address)


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
        self.path = f"/v2/wallet/{address}/positions"

    def subscribe(self, batched: bool = False) -> None:
        """Subscribe to wallet positions.

        Args:
            batched: Whether to receive updates in batches.
        """
        self.socket.send_subscribe(channel=self.path, batched=batched)

    def unsubscribe(self) -> None:
        """Unsubscribe from wallet positions."""
        self.socket.send_unsubscribe(channel=self.path)


class WalletPerpExecutionsSubscription:
    """Manages a subscription to perpetual executions for a specific wallet."""

    def __init__(self, socket: "ReyaSocket", address: str):
        """Initialize a wallet perpetual executions subscription.

        Args:
            socket: The WebSocket connection to use for this subscription.
            address: The wallet address.
        """
        self.socket = socket
        self.address = address
        self.path = f"/v2/wallet/{address}/perpExecutions"

    def subscribe(self, batched: bool = False) -> None:
        """Subscribe to wallet perpetual executions.

        Args:
            batched: Whether to receive updates in batches.
        """
        self.socket.send_subscribe(channel=self.path, batched=batched)

    def unsubscribe(self) -> None:
        """Unsubscribe from wallet perpetual executions."""
        self.socket.send_unsubscribe(channel=self.path)


class WalletAccountBalancesSubscription:
    """Manages a subscription to account balances for a specific wallet."""

    def __init__(self, socket: "ReyaSocket", address: str):
        """Initialize a wallet account balances subscription.

        Args:
            socket: The WebSocket connection to use for this subscription.
            address: The wallet address.
        """
        self.socket = socket
        self.address = address
        self.path = f"/v2/wallet/{address}/accountBalances"

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
        super().__init__(socket, "/v2/wallet/{address}/openOrders")

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
        self.path = f"/v2/wallet/{address}/openOrders"

    def subscribe(self, batched: bool = False) -> None:
        """Subscribe to wallet open orders.

        Args:
            batched: Whether to receive updates in batches.
        """
        self.socket.send_subscribe(channel=self.path, batched=batched)

    def unsubscribe(self) -> None:
        """Unsubscribe from wallet open orders."""
        self.socket.send_unsubscribe(channel=self.path)
