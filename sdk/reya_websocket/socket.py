"""WebSocket client implementation for the Reya API v2.

This module provides a WebSocket client that follows the same patterns as the REST API:
- All messages are parsed into typed Pydantic models
- Callbacks receive typed payloads directly (no raw dict access)
- Parsing failures raise exceptions (fail-fast, like REST)
"""

from typing import Callable, Optional, Union, cast

import json
import logging
import ssl
import threading

from pydantic import BaseModel, ValidationError
from websocket import WebSocket, WebSocketApp  # type: ignore[attr-defined]  # pylint: disable=no-name-in-module

from sdk.async_api.account_balance_update_payload import AccountBalanceUpdatePayload
from sdk.async_api.error_message_payload import ErrorMessagePayload
from sdk.async_api.market_depth_update_payload import MarketDepthUpdatePayload
from sdk.async_api.market_perp_execution_update_payload import (
    MarketPerpExecutionUpdatePayload,
)
from sdk.async_api.market_spot_execution_update_payload import (
    MarketSpotExecutionUpdatePayload,
)
from sdk.async_api.market_summary_update_payload import MarketSummaryUpdatePayload
from sdk.async_api.markets_summary_update_payload import MarketsSummaryUpdatePayload
from sdk.async_api.order_change_update_payload import OrderChangeUpdatePayload
from sdk.async_api.ping_message_payload import PingMessagePayload
from sdk.async_api.pong_message_payload import PongMessagePayload
from sdk.async_api.position_update_payload import PositionUpdatePayload
from sdk.async_api.price_update_payload import PriceUpdatePayload
from sdk.async_api.prices_update_payload import PricesUpdatePayload
from sdk.async_api.subscribed_message_payload import SubscribedMessagePayload
from sdk.async_api.unsubscribed_message_payload import UnsubscribedMessagePayload
from sdk.async_api.wallet_perp_execution_update_payload import (
    WalletPerpExecutionUpdatePayload,
)
from sdk.async_api.wallet_spot_execution_update_payload import (
    WalletSpotExecutionUpdatePayload,
)
from sdk.reya_websocket.config import WebSocketConfig, get_config
from sdk.reya_websocket.resources.market import MarketResource
from sdk.reya_websocket.resources.prices import PricesResource
from sdk.reya_websocket.resources.wallet import WalletResource

# Set up logging
logger = logging.getLogger("reya.websocket")


# Type alias for all possible WebSocket message payloads
# Based on AsyncAPI spec: asyncapi-trading-v2.yaml
WebSocketMessage = Union[
    # Control messages
    PingMessagePayload,
    PongMessagePayload,
    SubscribedMessagePayload,
    UnsubscribedMessagePayload,
    ErrorMessagePayload,
    # Market channels
    MarketsSummaryUpdatePayload,  # /v2/markets/summary
    MarketSummaryUpdatePayload,  # /v2/market/{symbol}/summary
    MarketPerpExecutionUpdatePayload,  # /v2/market/{symbol}/perpExecutions
    MarketSpotExecutionUpdatePayload,  # /v2/market/{symbol}/spotExecutions
    MarketDepthUpdatePayload,  # /v2/market/{symbol}/depth
    # Wallet channels
    PositionUpdatePayload,  # /v2/wallet/{address}/positions
    OrderChangeUpdatePayload,  # /v2/wallet/{address}/orderChanges
    WalletPerpExecutionUpdatePayload,  # /v2/wallet/{address}/perpExecutions
    WalletSpotExecutionUpdatePayload,  # /v2/wallet/{address}/spotExecutions
    AccountBalanceUpdatePayload,  # /v2/wallet/{address}/accountBalances
    # Price channels
    PricesUpdatePayload,  # /v2/prices
    PriceUpdatePayload,  # /v2/prices/{symbol}
]


class WebSocketDataError(Exception):
    """Exception raised when WebSocket data cannot be parsed into a typed model."""


class ReyaSocket(WebSocketApp):
    """WebSocket client for Reya API v2 with resource-based access and type safety."""

    # Channel to payload type mapping for V2
    # Note: Parameterized channels (with {symbol} or {address}) are handled in _get_payload_type()
    # This map is only for exact matches and control messages
    CHANNEL_PAYLOAD_MAP: dict[str, type[BaseModel]] = {
        # Control messages (matched by message type, not channel)
        "ping": PingMessagePayload,
        "pong": PongMessagePayload,
        # All markets summary (exact match)
        "/v2/markets/summary": MarketsSummaryUpdatePayload,
        # All prices (exact match)
        "/v2/prices": PricesUpdatePayload,
    }

    def __init__(
        self,
        url: Optional[str] = None,
        on_open: Optional[Callable[[WebSocket], None]] = None,
        on_message: Optional[Callable[[WebSocket, WebSocketMessage], None]] = None,
        on_error: Optional[Callable[[WebSocket, Exception], None]] = None,
        on_close: Optional[Callable[[WebSocket, int, str], None]] = None,
        config: Optional[WebSocketConfig] = None,
        **kwargs,
    ):
        """Initialize the WebSocket client with resources.

        Args:
            url: The WebSocket server URL. If None, uses the URL from config.
            on_open: Callback for connection open events.
            on_message: Callback for message events. Receives typed Pydantic models
                        directly (same pattern as REST API).
            on_error: Callback for error events.
            on_close: Callback for connection close events.
            config: WebSocket configuration. If None, loads from env file.
            **kwargs: Additional keyword arguments for WebSocketApp.
        """
        # Set up configuration
        self.config = config or get_config()
        url = url or self.config.url

        # Initialize resources
        self._market = MarketResource(self)
        self._wallet = WalletResource(self)
        self._prices = PricesResource(self)

        # Initialize thread attribute
        self._thread: Optional[threading.Thread] = None

        # Store user callback for wrapping
        self._user_on_message = on_message

        # Default handlers if none provided
        if on_open is None:
            on_open = self._default_on_open
        if on_error is None:
            on_error = self._default_on_error
        if on_close is None:
            on_close = self._default_on_close

        # Track subscriptions
        self.active_subscriptions: set[str] = set()

        super().__init__(
            url=url,
            on_open=on_open,
            on_message=self._wrap_message_handler(),
            on_error=on_error,
            on_close=on_close,
            **kwargs,
        )

    def _wrap_message_handler(self) -> Callable[[WebSocket, str], None]:
        """Create a message handler that parses JSON into typed Pydantic models.

        Following REST API patterns:
        - All messages are parsed into typed models
        - Parsing failures raise WebSocketDataError
        - Callbacks receive typed payloads directly
        """

        def wrapper(ws: WebSocket, message: str) -> None:
            logger.debug(f"RAW WEBSOCKET MESSAGE: {message!r}")
            raw = json.loads(message)

            # Parse into typed model (raises WebSocketDataError on failure)
            typed_message = self._parse_message(raw)

            # Call user callback or default with typed message
            if self._user_on_message is not None:
                self._user_on_message(ws, typed_message)
            else:
                self._default_on_message(ws, typed_message)

        return wrapper

    def _get_payload_type(self, channel: str) -> Optional[type[BaseModel]]:
        """Get the appropriate payload type for a channel.

        Args:
            channel: The channel path or message type.

        Returns:
            The corresponding Pydantic model class or None if not found.
        """
        # Direct match first
        if channel in self.CHANNEL_PAYLOAD_MAP:
            return self.CHANNEL_PAYLOAD_MAP[channel]

        # Pattern matching for parameterized channels
        if "/v2/market/" in channel:
            if channel.endswith("/summary"):
                return MarketSummaryUpdatePayload
            elif channel.endswith("/perpExecutions"):
                return MarketPerpExecutionUpdatePayload
            elif channel.endswith("/spotExecutions"):
                return MarketSpotExecutionUpdatePayload
            elif channel.endswith("/depth"):
                return MarketDepthUpdatePayload
        elif "/v2/wallet/" in channel:
            if channel.endswith("/positions"):
                return PositionUpdatePayload
            elif channel.endswith("/orderChanges"):
                return OrderChangeUpdatePayload
            elif channel.endswith("/perpExecutions"):
                return WalletPerpExecutionUpdatePayload
            elif channel.endswith("/spotExecutions"):
                return WalletSpotExecutionUpdatePayload
            elif channel.endswith("/accountBalances"):
                return AccountBalanceUpdatePayload
        elif "/v2/prices/" in channel and channel != "/v2/prices":
            return PriceUpdatePayload

        return None

    def _parse_message(self, message: dict) -> WebSocketMessage:
        """Parse a WebSocket message into the appropriate typed Pydantic model.

        Following REST API patterns, this method always returns a typed model
        or raises an exception. No raw dict fallback.

        Args:
            message: The raw message dictionary.

        Returns:
            Typed Pydantic model for the message.

        Raises:
            WebSocketDataError: If the message cannot be parsed into a typed model.
        """
        message_type = message.get("type")

        try:
            if message_type == "ping":
                return PingMessagePayload.model_validate(message)

            elif message_type == "pong":
                return PongMessagePayload.model_validate(message)

            elif message_type == "subscribed":
                return SubscribedMessagePayload.model_validate(message)

            elif message_type == "unsubscribed":
                return UnsubscribedMessagePayload.model_validate(message)

            elif message_type == "error":
                return ErrorMessagePayload.model_validate(message)

            elif message_type == "channel_data":
                channel = message.get("channel", "")
                payload_type = self._get_payload_type(channel)
                if payload_type is None:
                    raise WebSocketDataError(f"Unknown channel: {channel}")
                return cast(WebSocketMessage, payload_type.model_validate(message))

            else:
                raise WebSocketDataError(f"Unknown message type: {message_type}")

        except ValidationError as e:
            logger.error(f"Failed to parse {message_type} message: {e}")
            raise WebSocketDataError(f"Invalid {message_type} message format: {e}")

    @property
    def market(self) -> MarketResource:
        """Access market-related resources."""
        return self._market

    @property
    def wallet(self) -> WalletResource:
        """Access wallet-related resources."""
        return self._wallet

    @property
    def prices(self) -> PricesResource:
        """Access price-related resources."""
        return self._prices

    def send_subscribe(self, channel: str, **kwargs) -> None:
        """Send a subscription message.

        Args:
            channel: The channel to subscribe to.
            **kwargs: Additional subscription parameters.
        """
        self.active_subscriptions.add(channel)
        message = {"type": "subscribe", "channel": channel, **kwargs}
        logger.info(f"Subscribing to {channel}")
        self.send(json.dumps(message))

    def send_unsubscribe(self, channel: str, **kwargs) -> None:
        """Send an unsubscription message.

        Args:
            channel: The channel to unsubscribe from.
            **kwargs: Additional unsubscription parameters.
        """
        if channel in self.active_subscriptions:
            self.active_subscriptions.remove(channel)

        message = {"type": "unsubscribe", "channel": channel, **kwargs}
        logger.info(f"Unsubscribing from {channel}")
        self.send(json.dumps(message))

    def connect(self, sslopt=None, blocking=False) -> None:
        """Connect to the WebSocket server.

        Args:
            sslopt: SSL options for the connection. If None, uses default options.
            blocking: If True, this method is a blocking call that runs the WebSocket connection
                     and will not return until the connection is closed. If False, it starts the
                     connection in a background thread and returns immediately.

        Note:
            When blocking=True, this method will not return until the connection is closed.
            When blocking=False, this method will return immediately and the connection will
            run in a background thread.
        """
        if sslopt is None:
            if self.config.ssl_verify:
                sslopt = {}  # Use default SSL verification
            else:
                sslopt = {"cert_reqs": ssl.CERT_NONE}

        logger.info(f"Connecting to {self.url}")

        if blocking:
            # Run the WebSocket directly (blocking)
            self.run_forever(
                sslopt=sslopt,
                ping_interval=self.config.ping_interval,
                ping_timeout=self.config.ping_timeout,
            )
        else:
            # Run the WebSocket in a thread (non-blocking)
            self._thread = threading.Thread(
                target=self.run_forever,
                kwargs={
                    "sslopt": sslopt,
                    "ping_interval": self.config.ping_interval,
                    "ping_timeout": self.config.ping_timeout,
                },
            )
            self._thread.daemon = True
            self._thread.start()

    def _default_on_open(self, _ws):
        """Default handler for connection open events."""
        logger.info("WebSocket connection established")
        # Send ping to confirm connection and trigger subscription workflow
        self.send(json.dumps({"type": "ping"}))

    def _default_on_message(self, _ws: WebSocket, message: WebSocketMessage) -> None:
        """Default handler for message events.

        Args:
            message: Typed Pydantic model for the WebSocket message.
        """
        logger.debug(f"Received {type(message).__name__}")

        if isinstance(message, PongMessagePayload):
            logger.info("Connection confirmed via pong response")

        elif isinstance(message, SubscribedMessagePayload):
            logger.info(f"Successfully subscribed to {message.channel}")

        elif isinstance(message, UnsubscribedMessagePayload):
            logger.info(f"Successfully unsubscribed from {message.channel}")

        elif isinstance(message, ErrorMessagePayload):
            logger.error(f"Received error: {message.message}")

        elif isinstance(message, PingMessagePayload):
            logger.debug("Received ping from server")

        elif hasattr(message, "data"):
            # Channel data messages have a 'data' attribute
            channel = getattr(message, "channel", "unknown")
            data = message.data
            if isinstance(data, list):
                logger.debug(f"Received {len(data)} items from {channel}")
            else:
                logger.debug(f"Received {type(data).__name__} from {channel}")

    def _default_on_error(self, _ws, error):
        """Default handler for error events."""
        logger.error(f"WebSocket error: {error}")

    def _default_on_close(self, _ws, close_status_code, close_reason):
        """Default handler for connection close events."""
        logger.info(f"WebSocket connection closed: " f"status={close_status_code}, reason={close_reason}")
