"""Wallet-related WebSocket resources for v2 API."""

from typing import TYPE_CHECKING

from sdk.reya_websocket.resources.common import SubscribableParameterizedResource

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
        self._spot_executions = WalletSpotExecutionsResource(socket)
        self._balances = WalletBalancesResource(socket)
        self._order_changes = WalletOrderChangesResource(socket)

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

    def spot_executions(self, address: str) -> "WalletSpotExecutionsSubscription":
        """Get spot executions for a specific wallet address.

        Args:
            address: The wallet address.

        Returns:
            A subscription object for the wallet spot executions.
        """
        return self._spot_executions.for_wallet(address)

    def balances(self, address: str) -> "WalletBalancesSubscription":
        """Get balances for a specific wallet address.

        Args:
            address: The wallet address.

        Returns:
            A subscription object for the wallet balances.
        """
        return self._balances.for_wallet(address)

    def order_changes(self, address: str) -> "WalletOrderChangesSubscription":
        """Get order changes for a specific wallet address.

        Args:
            address: The wallet address.

        Returns:
            A subscription object for the wallet order_changes.
        """
        return self._order_changes.for_wallet(address)


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


class WalletOrderChangesResource(SubscribableParameterizedResource):
    """Resource for accessing wallet open orders."""

    def __init__(self, socket: "ReyaSocket"):
        """Initialize the wallet open orders resource.

        Args:
            socket: The WebSocket connection to use for this resource.
        """
        super().__init__(socket, "/v2/wallet/{address}/orderChanges")

    def for_wallet(self, address: str) -> "WalletOrderChangesSubscription":
        """Create a subscription for a specific wallet's open orders.

        Args:
            address: The wallet address.

        Returns:
            A subscription object for the wallet open orders.
        """
        return WalletOrderChangesSubscription(self.socket, address)


class WalletOrderChangesSubscription:
    """Manages a subscription to open orders for a specific wallet."""

    def __init__(self, socket: "ReyaSocket", address: str):
        """Initialize a wallet open orders subscription.

        Args:
            socket: The WebSocket connection to use for this subscription.
            address: The wallet address.
        """
        self.socket = socket
        self.address = address
        self.path = f"/v2/wallet/{address}/orderChanges"

    def subscribe(self, batched: bool = False) -> None:
        """Subscribe to wallet open orders.

        Args:
            batched: Whether to receive updates in batches.
        """
        self.socket.send_subscribe(channel=self.path, batched=batched)

    def unsubscribe(self) -> None:
        """Unsubscribe from wallet open orders."""
        self.socket.send_unsubscribe(channel=self.path)


class WalletSpotExecutionsResource(SubscribableParameterizedResource):
    """Resource for accessing wallet spot executions."""

    def __init__(self, socket: "ReyaSocket"):
        """Initialize the wallet spot executions resource.

        Args:
            socket: The WebSocket connection to use for this resource.
        """
        super().__init__(socket, "/v2/wallet/{address}/spotExecutions")

    def for_wallet(self, address: str) -> "WalletSpotExecutionsSubscription":
        """Create a subscription for a specific wallet's spot executions.

        Args:
            address: The wallet address.

        Returns:
            A subscription object for the wallet spot executions.
        """
        return WalletSpotExecutionsSubscription(self.socket, address)


class WalletSpotExecutionsSubscription:
    """Manages a subscription to spot executions for a specific wallet."""

    def __init__(self, socket: "ReyaSocket", address: str):
        """Initialize a wallet spot executions subscription.

        Args:
            socket: The WebSocket connection to use for this subscription.
            address: The wallet address.
        """
        self.socket = socket
        self.address = address
        self.path = f"/v2/wallet/{address}/spotExecutions"

    def subscribe(self, batched: bool = False) -> None:
        """Subscribe to wallet spot executions.

        Args:
            batched: Whether to receive updates in batches.
        """
        self.socket.send_subscribe(channel=self.path, batched=batched)

    def unsubscribe(self) -> None:
        """Unsubscribe from wallet spot executions."""
        self.socket.send_unsubscribe(channel=self.path)


class WalletBalancesResource(SubscribableParameterizedResource):
    """Resource for accessing wallet balances."""

    def __init__(self, socket: "ReyaSocket"):
        """Initialize the wallet balances resource.

        Args:
            socket: The WebSocket connection to use for this resource.
        """
        super().__init__(socket, "/v2/wallet/{address}/balances")

    def for_wallet(self, address: str) -> "WalletBalancesSubscription":
        """Create a subscription for a specific wallet's balances.

        Args:
            address: The wallet address.

        Returns:
            A subscription object for the wallet balances.
        """
        return WalletBalancesSubscription(self.socket, address)


class WalletBalancesSubscription:
    """Manages a subscription to balances for a specific wallet."""

    def __init__(self, socket: "ReyaSocket", address: str):
        """Initialize a wallet balances subscription.

        Args:
            socket: The WebSocket connection to use for this subscription.
            address: The wallet address.
        """
        self.socket = socket
        self.address = address
        self.path = f"/v2/wallet/{address}/accountBalances"

    def subscribe(self, batched: bool = False) -> None:
        """Subscribe to wallet balances.

        Args:
            batched: Whether to receive updates in batches.
        """
        self.socket.send_subscribe(channel=self.path, batched=batched)

    def unsubscribe(self) -> None:
        """Unsubscribe from wallet balances."""
        self.socket.send_unsubscribe(channel=self.path)
