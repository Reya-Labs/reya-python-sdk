"""Example of monitoring asset pair prices using the WebSocket API.

This example connects to the Reya WebSocket API and subscribes to price data for ETHUSDMARK.

Before running this example, ensure you have a .env file with the following variables:
- PRIVATE_KEY: Your Ethereum private key
- ACCOUNT_ID: Your Reya account ID
- CHAIN_ID: The chain ID (1729 for mainnet, 89346162 for testnet)
"""

import asyncio
import json
import logging
import os
import time
from typing import Dict, Any, List

from dotenv import load_dotenv
from pydantic import ValidationError

# Import the resource-oriented WebSocket client
from sdk.reya_websocket import ReyaSocket

# Import WebSocket message types for proper type conversion
from sdk.async_api.prices_update_payload import PricesUpdatePayload
from sdk.async_api.price_update_payload import PriceUpdatePayload
from sdk.async_api.price import Price

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

# Create a logger for this module
logger = logging.getLogger("reya.prices_monitoring")

# Symbol to monitor
SYMBOL = "ETHRUSDPERP"


def on_open(ws):
    """Handle WebSocket connection open event."""
    logger.info(f"Connection established, subscribing to price data for {SYMBOL}")

    # Subscribe to all prices
    # ws.prices.all_prices.subscribe()

    # Subscribe to price data for the specified symbol
    ws.prices.price(SYMBOL).subscribe()


def handle_all_prices_data(message: Dict[str, Any]) -> None:
    """Handle /v2/prices channel data with proper type conversion."""
    try:
        # Convert raw message to typed payload
        payload = PricesUpdatePayload.model_validate(message)
        
        logger.info(f"💰 All Prices Update:")
        logger.info(f"  ├─ Timestamp: {payload.timestamp}")
        logger.info(f"  ├─ Channel: {payload.channel}")
        logger.info(f"  └─ Prices Count: {len(payload.data)}")
        
        # Showcase individual price data structure
        for i, price in enumerate(payload.data[:3]):  # Show first 3 prices
            logger.info(f"    Price {i+1}: {price.symbol}")
            logger.info(f"      ├─ Oracle Price: {price.oracle_price or 'N/A'}")
            logger.info(f"      ├─ Pool Price: {price.pool_price or 'N/A'}")
            logger.info(f"      └─ Updated At: {price.updated_at}")
        
        if len(payload.data) > 3:
            logger.info(f"    ... and {len(payload.data) - 3} more prices")
            
    except ValidationError as e:
        logger.error(f"Failed to parse all prices data: {e}")
    except Exception as e:
        logger.error(f"Unexpected error handling all prices: {e}")


def handle_single_price_data(message: Dict[str, Any]) -> None:
    """Handle /v2/prices/:symbol channel data with proper type conversion."""
    try:
        # Convert raw message to typed payload
        payload = PriceUpdatePayload.model_validate(message)
        price = payload.data
        
        logger.info(f"💵 Price Update for {price.symbol}:")
        logger.info(f"  ├─ Timestamp: {payload.timestamp}")
        logger.info(f"  ├─ Channel: {payload.channel}")
        logger.info(f"  ├─ Oracle Price: {price.oracle_price or 'N/A'}")
        logger.info(f"  ├─ Pool Price: {price.pool_price or 'N/A'}")
        logger.info(f"  └─ Updated At: {price.updated_at}")
        
    except ValidationError as e:
        logger.error(f"Failed to parse single price data: {e}")
    except Exception as e:
        logger.error(f"Unexpected error handling single price: {e}")


def on_message(ws, message):
    """Handle WebSocket messages with proper type conversion and dedicated handlers."""
    message_type = message.get("type")

    if message_type == "subscribed":
        channel = message.get("channel", "unknown")
        logger.info(f"✅ Successfully subscribed to {channel}")

        # Log the initial data from subscription
        if "contents" in message:
            logger.info(f"📦 Initial data received: {len(str(message['contents']))} characters")

    elif message_type == "channel_data":
        channel = message.get("channel", "unknown")
        
        # Route to appropriate handler based on channel pattern
        if channel == "/v2/prices":
            handle_all_prices_data(message)
        elif "/v2/prices/" in channel:
            handle_single_price_data(message)
        else:
            logger.warning(f"🔍 Unhandled channel data: {channel}")

    elif message_type == "ping":
        logger.info("🏓 Received ping from server, sending pong response")
        try:
            ws.send(json.dumps({"type": "pong"}))
            logger.debug("✅ Pong sent successfully")
        except Exception as e:
            logger.error(f"❌ Failed to send pong: {e}")

    elif message_type == "pong":
        logger.info("🏓 Connection confirmed via pong response")

    elif message_type == "error":
        logger.error(f"❌ Error: {message.get('message', 'unknown error')}")

    else:
        logger.debug(f"🔍 Received message type: {message_type}")


async def periodic_task(ws):
    """Enhanced periodic task with connection monitoring."""
    counter = 0
    start_time = time.time()
    
    while True:
        counter += 1
        uptime = time.time() - start_time
        
        logger.info(f"🔄 Monitoring {SYMBOL} prices (iteration {counter}) - Uptime: {uptime:.1f}s")
        
        # Monitor connection health
        active_subs = len(ws.active_subscriptions)
        logger.info(f"📊 Connection Status: {active_subs} active subscriptions")
        
        # Send periodic ping to test connection (every 10 iterations = ~20 seconds)
        if counter % 10 == 0:
            try:
                logger.info("🏓 Sending manual ping to test connection")
                ws.send(json.dumps({"type": "ping"}))
            except Exception as e:
                logger.error(f"❌ Failed to send manual ping: {e}")
        
        await asyncio.sleep(2)  # Run every 2 seconds


async def main():
    """Main entry point for the example."""
    # Load environment variables
    load_dotenv()

    # Get WebSocket URL from environment
    ws_url = os.environ.get("REYA_WS_URL", "wss://ws.reya.xyz/")

    # Create enhanced error and close handlers for better connection monitoring
    def on_error(ws, error):
        """Enhanced error handler with detailed logging."""
        logger.error(f"❌ WebSocket error: {error}")
        
    def on_close(ws, close_status_code, close_reason):
        """Enhanced close handler with detailed logging."""
        logger.info(f"🔌 WebSocket closed: {close_status_code} - {close_reason}")
        if close_status_code != 1000:  # 1000 is normal closure
            logger.warning(f"⚠️ Abnormal closure detected. Status: {close_status_code}")

    # Create the WebSocket with enhanced configuration
    from sdk.reya_websocket.config import WebSocketConfig
    
    # Create custom config with more aggressive ping settings
    config = WebSocketConfig(
        url=ws_url,
        ping_interval=20,  # Ping every 20 seconds instead of 30
        ping_timeout=15,   # Wait 15 seconds for pong instead of 10
        connection_timeout=60,  # Longer initial connection timeout
        reconnect_attempts=5,   # More reconnection attempts
        reconnect_delay=3       # Shorter delay between reconnects
    )
    
    ws = ReyaSocket(
        config=config,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
    )

    logger.info(f"Connecting to WebSocket to monitor {SYMBOL} prices")
    logger.info("Press Ctrl+C to exit")

    # Connect
    ws.connect()

    # Start our concurrent task with WebSocket reference
    asyncio.create_task(periodic_task(ws))

    # Main application loop
    while True:
        await asyncio.sleep(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Program interrupted by user. Exiting...")
