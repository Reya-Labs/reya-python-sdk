"""Price-related WebSocket resources."""

from typing import TYPE_CHECKING

from sdk.reya_websocket.resources.common import (
    SubscribableParameterizedResource,
)

if TYPE_CHECKING:
    from sdk.reya_websocket.socket import ReyaSocket


class PricesResource:
    """Container for all price-related WebSocket resources."""

    def __init__(self, socket: "ReyaSocket"):
        """Initialize the prices resource container.

        Args:
            socket: The WebSocket connection to use for this resource.
        """
        self.socket = socket
        self._asset_pair_price = AssetPairPriceResource(socket)

    def asset_pair_price(self, asset_pair_id: str) -> "AssetPairPriceSubscription":
        """Get price data for a specific asset pair.

        Args:
            asset_pair_id: The ID of the asset pair (e.g., "BTCUSDMARK", "ETHUSDMARK").

        Returns:
            A subscription object for the specified asset pair's price data.
        """
        return self._asset_pair_price.for_asset_pair(asset_pair_id)


class AssetPairPriceResource(SubscribableParameterizedResource):
    """Resource for accessing asset pair price data."""

    def __init__(self, socket: "ReyaSocket"):
        """Initialize the asset pair price resource.

        Args:
            socket: The WebSocket connection to use for this resource.
        """
        super().__init__(socket, "/api/trading/prices/{asset_pair_id}")

    def for_asset_pair(self, asset_pair_id: str) -> "AssetPairPriceSubscription":
        """Create a subscription for a specific asset pair's price data.

        Args:
            asset_pair_id: The ID of the asset pair (e.g., "BTCUSDMARK", "ETHUSDMARK").

        Returns:
            A subscription object for the specified asset pair's price data.
        """
        return AssetPairPriceSubscription(self.socket, asset_pair_id)


class AssetPairPriceSubscription:
    """Manages a subscription to price data for a specific asset pair."""

    def __init__(self, socket: "ReyaSocket", asset_pair_id: str):
        """Initialize an asset pair price subscription.

        Args:
            socket: The WebSocket connection to use for this subscription.
            asset_pair_id: The ID of the asset pair (e.g., "BTCUSDMARK", "ETHUSDMARK").
        """
        self.socket = socket
        self.asset_pair_id = asset_pair_id
        self.path = f"/api/trading/prices/{asset_pair_id}"

    def subscribe(self, batched: bool = False) -> None:
        """Subscribe to asset pair price data.

        Args:
            batched: Whether to receive updates in batches.
        """
        self.socket.send_subscribe(channel=self.path, batched=batched)

    def unsubscribe(self) -> None:
        """Unsubscribe from asset pair price data."""
        self.socket.send_unsubscribe(channel=self.path)
