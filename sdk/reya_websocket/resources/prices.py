"""Price-related WebSocket resources for v2 API."""

from typing import TYPE_CHECKING

from sdk.reya_websocket.resources.common import SubscribableResource

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
        self._asset_oracle_prices = AssetOraclePricesResource(socket)

    @property
    def asset_oracle_prices(self) -> "AssetOraclePricesResource":
        """Access asset oracle prices."""
        return self._asset_oracle_prices


class AssetOraclePricesResource(SubscribableResource):
    """Resource for accessing asset oracle prices."""

    def __init__(self, socket: "ReyaSocket"):
        """Initialize the asset oracle prices resource.

        Args:
            socket: The WebSocket connection to use for this resource.
        """
        super().__init__(socket)
        self.path = "/v2/assetOraclePrices"

    def subscribe(self, batched: bool = False, **kwargs) -> None:
        """Subscribe to asset oracle prices.

        Args:
            batched: Whether to receive updates in batches.
            **kwargs: Additional keyword arguments (unused).
        """
        self.socket.send_subscribe(channel=self.path, batched=batched)

    def unsubscribe(self, **kwargs) -> None:
        """Unsubscribe from asset oracle prices.

        Args:
            **kwargs: Additional keyword arguments (unused).
        """
        self.socket.send_unsubscribe(channel=self.path)
