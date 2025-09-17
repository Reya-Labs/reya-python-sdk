"""WebSocket client implementation for the Reya API v2."""

from typing import Any, Callable, Optional

import json
import logging
import ssl
import threading

import websocket
from pydantic import BaseModel, ValidationError

from sdk.async_api.market_perp_execution_update_payload import (
    MarketPerpExecutionUpdatePayload,
)
from sdk.async_api.market_summary_update_payload import MarketSummaryUpdatePayload
from sdk.async_api.markets_summary_update_payload import MarketsSummaryUpdatePayload
from sdk.async_api.order_change_update_payload import OrderChangeUpdatePayload
from sdk.async_api.ping_message_payload import PingMessagePayload
from sdk.async_api.pong_message_payload import PongMessagePayload
from sdk.async_api.position_update_payload import PositionUpdatePayload
from sdk.async_api.price_update_payload import PriceUpdatePayload
from sdk.async_api.prices_update_payload import PricesUpdatePayload
from sdk.async_api.wallet_perp_execution_update_payload import (
    WalletPerpExecutionUpdatePayload,
)
from sdk.reya_websocket.config import WebSocketConfig, get_config
from sdk.reya_websocket.resources.market import MarketResource
from sdk.reya_websocket.resources.prices import PricesResource
from sdk.reya_websocket.resources.wallet import WalletResource

# Set up logging
logger = logging.getLogger("reya.websocket")


def as_json(
    on_message: Optional[Callable[[Any, Any], None]],
) -> Callable[[Any, str], None]:
    """Wrap a message handler to parse JSON messages.

    Args:
        on_message: The original message handler.

    Returns:
        A wrapped message handler that parses JSON messages before passing them to the original handler.
    """

    def wrapper(ws, message: str):
        # Always log raw message for debugging
        logger.debug(f"RAW WEBSOCKET MESSAGE: {message!r}")
        if on_message is not None:
            return on_message(ws, json.loads(message))
        return None

    return wrapper


class WebSocketDataError(Exception):
    """Exception raised when WebSocket data cannot be parsed."""


class ReyaSocket(websocket.WebSocketApp):
    """WebSocket client for Reya API v2 with resource-based access and type safety."""

    # Channel to payload type mapping for V2
    CHANNEL_PAYLOAD_MAP: dict[str, type[BaseModel]] = {
        # Ping/Pong
        "ping": PingMessagePayload,
        "pong": PongMessagePayload,
        # Markets
        "/v2/markets/summary": MarketsSummaryUpdatePayload,
        # Market-specific (regex patterns handled in _get_payload_type)
        "/v2/market/": MarketSummaryUpdatePayload,  # /v2/market/{symbol}/summary
        "/v2/market/perpExecutions": MarketPerpExecutionUpdatePayload,  # /v2/market/{symbol}/perpExecutions
        # Wallet-specific (regex patterns handled in _get_payload_type)
        "/v2/wallet/positions": PositionUpdatePayload,  # /v2/wallet/{address}/positions
        "/v2/wallet/orderChanges": OrderChangeUpdatePayload,  # /v2/wallet/{address}/orderChanges
        "/v2/wallet/perpExecutions": WalletPerpExecutionUpdatePayload,  # /v2/wallet/{address}/perpExecutions
        # Prices
        "/v2/prices": PricesUpdatePayload,
        "/v2/prices/": PriceUpdatePayload,  # /v2/prices/{symbol}
    }

    def __init__(
        self,
        url: Optional[str] = None,
        on_open: Optional[Callable[[websocket.WebSocket], None]] = None,
        on_message: Optional[Callable[[websocket.WebSocket, Any], None]] = None,
        on_error: Optional[Callable[[websocket.WebSocket, Any], None]] = None,
        on_close: Optional[Callable[[websocket.WebSocket, int, str], None]] = None,
        config: Optional[WebSocketConfig] = None,
        **kwargs,
    ):
        """Initialize the WebSocket client with resources.

        Args:
            url: The WebSocket server URL. If None, uses the URL from config.
            on_open: Callback for connection open events.
            on_message: Callback for message events.
            on_error: Callback for error events.
            on_close: Callback for connection close events.
            config: WebSocket configuration. If None, loads from sdk.reya_websocketenv file.
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

        # Default handlers if none provided
        if on_open is None:
            on_open = self._default_on_open
        if on_message is None:
            on_message = self._default_on_message
        if on_error is None:
            on_error = self._default_on_error
        if on_close is None:
            on_close = self._default_on_close

        # Track subscriptions
        self.active_subscriptions: set[str] = set()

        super().__init__(
            url=url,
            on_open=on_open,
            on_message=as_json(on_message),
            on_error=on_error,
            on_close=on_close,
            **kwargs,
        )

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
        elif "/v2/wallet/" in channel:
            if channel.endswith("/positions"):
                return PositionUpdatePayload
            elif channel.endswith("/orderChanges"):
                return OrderChangeUpdatePayload
            elif channel.endswith("/perpExecutions"):
                return WalletPerpExecutionUpdatePayload
        elif "/v2/prices/" in channel and not channel == "/v2/prices":
            return PriceUpdatePayload

        return None

    def _parse_message(self, message: dict) -> Optional[BaseModel]:
        """Parse a WebSocket message into the appropriate Pydantic model.

        Args:
            message: The raw message dictionary.

        Returns:
            Parsed Pydantic model or None if parsing fails.
        """
        message_type = message.get("type")

        if message_type in ["ping", "pong"]:
            payload_type = self.CHANNEL_PAYLOAD_MAP.get(message_type)
            if payload_type:
                try:
                    return payload_type.model_validate(message)
                except ValidationError as e:
                    logger.error(f"Failed to parse {message_type} message: {e}")
                    raise WebSocketDataError(f"Invalid {message_type} message format")

        elif message_type == "channel_data":
            channel = message.get("channel")
            if channel:
                payload_type = self._get_payload_type(channel)
                if payload_type:
                    try:
                        return payload_type.model_validate(message)
                    except ValidationError as e:
                        logger.error(f"Failed to parse channel_data for {channel}: {e}")
                        raise WebSocketDataError(f"Invalid data format for channel {channel}")

        return None

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

    def _default_on_message(self, _ws, message):
        """Default handler for message events with V2 support."""
        message_type = message.get("type")

        # Try to parse message with Pydantic models for type safety
        try:
            parsed_message = self._parse_message(message)
            if parsed_message:
                logger.debug(f"Parsed message as {type(parsed_message).__name__}")
        except WebSocketDataError as e:
            logger.warning(f"Message parsing failed: {e}")
            parsed_message = None

        if message_type == "connected":
            logger.info("Connection established with server")

        elif message_type == "pong":
            logger.info("Connection confirmed via pong response")

        elif message_type == "subscribed":
            channel = message.get("channel", "unknown")
            logger.info(f"Successfully subscribed to {channel}")

        elif message_type == "unsubscribed":
            channel = message.get("channel", "unknown")
            logger.info(f"Successfully unsubscribed from {channel}")

        elif message_type == "channel_data":
            channel = message.get("channel", "unknown")
            timestamp = message.get("timestamp")

            logger.debug(f"Received data from {channel} at {timestamp}")

            # Log structured data if parsing succeeded
            if parsed_message and hasattr(parsed_message, "data"):
                data_type = type(parsed_message.data)
                if hasattr(data_type, "__origin__") and data_type.__origin__ is list:
                    logger.debug(f"Received {len(parsed_message.data)} items")
                else:
                    logger.debug(f"Received {type(parsed_message.data).__name__} data")

        elif message_type == "error":
            logger.error(f"Received error: {message.get('message', 'unknown')}")

        elif message_type == "ping":
            logger.debug("Received ping from server")

        else:
            logger.debug(f"Received unknown message type: {message_type} - {message}")

    def _default_on_error(self, _ws, error):
        """Default handler for error events."""
        logger.error(f"WebSocket error: {error}")

    def _default_on_close(self, _ws, close_status_code, close_reason):
        """Default handler for connection close events."""
        logger.info(f"WebSocket connection closed: " f"status={close_status_code}, reason={close_reason}")
