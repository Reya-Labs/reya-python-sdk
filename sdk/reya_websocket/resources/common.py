"""Base classes for WebSocket resources."""

from typing import TYPE_CHECKING

from abc import ABC, abstractmethod

if TYPE_CHECKING:
    from sdk.reya_websocket.socket import ReyaSocket


class WebSocketResource(ABC):
    """Base class for all WebSocket resources."""

    def __init__(self, socket: "ReyaSocket"):
        """Initialize the resource with a socket connection.

        Args:
            socket: The WebSocket connection to use for this resource.
        """
        self.socket = socket


class SubscribableResource(WebSocketResource, ABC):
    """Base class for resources that can be subscribed to."""

    @abstractmethod
    def subscribe(self, **kwargs) -> None:
        """Subscribe to this resource."""

    @abstractmethod
    def unsubscribe(self, **kwargs) -> None:
        """Unsubscribe from this resource."""


class ParameterizedResource(WebSocketResource):
    """Resource that requires path parameters."""

    def __init__(self, socket: "ReyaSocket", path_template: str):
        """Initialize a parameterized resource.

        Args:
            socket: The WebSocket connection to use for this resource.
            path_template: A template string for the resource path with placeholders.
        """
        super().__init__(socket)
        self.path_template = path_template

    def get_path(self, **kwargs) -> str:
        """Format the path template with the provided parameters.

        Args:
            **kwargs: The parameters to substitute in the path template.

        Returns:
            The formatted path string.

        Raises:
            ValueError: If a required parameter is missing.
        """
        try:
            return self.path_template.format(**kwargs)
        except KeyError as e:
            raise ValueError(f"Missing required parameter: {e}")


class SubscribableParameterizedResource(ParameterizedResource, SubscribableResource):
    """Parameterized resource that can be subscribed to."""

    def subscribe(self, **kwargs) -> None:
        """Subscribe to this resource with the specified parameters.

        Args:
            **kwargs: Parameters to substitute in the path template and subscription options.
        """
        batched = kwargs.pop("batched", False)
        path = self.get_path(**kwargs)
        self.socket.send_subscribe(channel=path, batched=batched)

    def unsubscribe(self, **kwargs) -> None:
        """Unsubscribe from this resource with the specified parameters.

        Args:
            **kwargs: Parameters to substitute in the path template.
        """
        path = self.get_path(**kwargs)
        self.socket.send_unsubscribe(channel=path)
