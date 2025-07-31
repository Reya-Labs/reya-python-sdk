"""Configuration settings for the Reya WebSocket client."""
import os
from typing import Dict, Optional, Any
from dataclasses import dataclass
from dotenv import load_dotenv

# Default values
DEFAULT_TRADING_API_PREFIX = "/api/trading/"

@dataclass
class WebSocketConfig:
    """Configuration for the WebSocket client."""
    url: str
    ping_interval: int = 30
    ping_timeout: int = 10
    connection_timeout: int = 30
    reconnect_attempts: int = 3
    reconnect_delay: int = 5
    enable_compression: bool = True
    ssl_verify: bool = True
    
    # API-specific settings
    trading_api_prefix: str = DEFAULT_TRADING_API_PREFIX
    subscription_batch_size: int = 10
    
    @classmethod
    def from_env(cls) -> 'WebSocketConfig':
        """Create a config instance from environment variables."""
        load_dotenv()
        
        return cls(
            url=os.environ.get("REYA_WS_URL", "wss://ws.reya.xyz/"),  # Use direct URL from .env
            ping_interval=int(os.environ.get("REYA_WS_PING_INTERVAL", "30")),
            ping_timeout=int(os.environ.get("REYA_WS_PING_TIMEOUT", "10")),
            connection_timeout=int(os.environ.get("REYA_WS_CONNECTION_TIMEOUT", "30")),
            reconnect_attempts=int(os.environ.get("REYA_WS_RECONNECT_ATTEMPTS", "3")),
            reconnect_delay=int(os.environ.get("REYA_WS_RECONNECT_DELAY", "5")),
            enable_compression=os.environ.get("REYA_WS_ENABLE_COMPRESSION", "True").lower() == "true",
            ssl_verify=os.environ.get("REYA_WS_SSL_VERIFY", "True").lower() == "true",
            trading_api_prefix=os.environ.get("REYA_TRADING_API_PREFIX", DEFAULT_TRADING_API_PREFIX),
        )

def get_config() -> WebSocketConfig:
    """Get configuration from environment."""
    return WebSocketConfig.from_env()
