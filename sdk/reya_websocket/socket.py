"""WebSocket client implementation for the Reya API."""
import json
import ssl
import logging
import asyncio
import websocket
from typing import Any, Callable, Dict, Optional, Union

from sdk.reya_websocket.resources.market import MarketResource
from sdk.reya_websocket.resources.wallet import WalletResource
from sdk.reya_websocket.config import WebSocketConfig, get_config

# Set up logging
logger = logging.getLogger("reya.websocket")

def as_json(on_message: Optional[Callable[[Any, Any], None]]) -> Callable[[Any, str], None]:
    """Wrap a message handler to parse JSON messages.
    
    Args:
        on_message: The original message handler.
    
    Returns:
        A wrapped message handler that parses JSON messages before passing them to the original handler.
    """
    def wrapper(ws, message: str):
        # Always log raw message for debugging
        logger.debug(f"RAW WEBSOCKET MESSAGE: {message!r}")
        return on_message(ws, json.loads(message))
    
    return wrapper

class ReyaSocket(websocket.WebSocketApp):
    """WebSocket client for Reya API with resource-based access."""
    
    def __init__(
        self,
        url: str = None,
        on_open: Optional[Callable[[websocket.WebSocket], None]] = None,
        on_message: Optional[Callable[[websocket.WebSocket, Any], None]] = None,
        on_error: Optional[Callable[[websocket.WebSocket, Any], None]] = None,
        on_close: Optional[Callable[[websocket.WebSocket, int, str], None]] = None,
        config: Optional[WebSocketConfig] = None,
        *args,
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
            *args: Additional arguments for WebSocketApp.
            **kwargs: Additional keyword arguments for WebSocketApp.
        """
        # Set up configuration
        self.config = config or get_config()
        url = url or self.config.url
        
        # Initialize resources
        self._market = MarketResource(self)
        self._wallet = WalletResource(self)
        
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
        self.active_subscriptions = set()
        
        super().__init__(
            url=url,
            on_open=on_open,
            on_message=as_json(on_message),
            on_error=on_error,
            on_close=on_close,
            *args,
            **kwargs,
        )
    
    @property
    def market(self) -> MarketResource:
        """Access market-related resources."""
        return self._market
    
    @property
    def wallet(self) -> WalletResource:
        """Access wallet-related resources."""
        return self._wallet
    
    def send_subscribe(self, channel: str, **kwargs) -> None:
        """Send a subscription message.
        
        Args:
            channel: The channel to subscribe to.
            **kwargs: Additional subscription parameters.
        """
        self.active_subscriptions.add(channel)
        message = {
            "type": "subscribe",
            "channel": channel,
            **kwargs
        }
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
        
        message = {
            "type": "unsubscribe",
            "channel": channel,
            **kwargs
        }
        logger.info(f"Unsubscribing from {channel}")
        self.send(json.dumps(message))
    
    def connect(self, sslopt=None) -> None:
        """Connect to the WebSocket server.
        
        Args:
            sslopt: SSL options for the connection. If None, uses default options.
            
        Note:
            This method is a blocking call that runs the WebSocket connection.
            It will not return until the connection is closed.
        """
        if sslopt is None:
            if self.config.ssl_verify:
                sslopt = {}  # Use default SSL verification
            else:
                sslopt = {"cert_reqs": ssl.CERT_NONE}
        
        logger.info(f"Connecting to {self.url}")
        
        # Run the WebSocket directly
        self.run_forever(
            sslopt=sslopt,
            ping_interval=self.config.ping_interval,
            ping_timeout=self.config.ping_timeout
        )
    
    def _default_on_open(self, ws):
        """Default handler for connection open events."""
        logger.info("WebSocket connection established")
        # Send ping to confirm connection and trigger subscription workflow
        self.send(json.dumps({"type": "ping"}))
    
    def _default_on_message(self, ws, message):
        """Default handler for message events."""
        message_type = message.get("type")
        
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
            logger.debug(f"Received data from {channel}")
            
        elif message_type == "error":
            logger.error(f"Received error: {message.get('message', 'unknown')}")
            
        else:
            logger.debug(f"Received unknown message type: {message_type} - {message}")
    
    def _default_on_error(self, ws, error):
        """Default handler for error events."""
        logger.error(f"WebSocket error: {error}")
    
    def _default_on_close(self, ws, close_status_code, close_reason):
        """Default handler for connection close events."""
        logger.info(
            f"WebSocket connection closed: "
            f"status={close_status_code}, reason={close_reason}"
        )
