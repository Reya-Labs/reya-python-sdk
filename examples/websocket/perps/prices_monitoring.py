"""
Prices Monitoring - Monitor asset oracle prices via WebSocket.

This example connects to the Reya WebSocket API and subscribes to the canonical
asset oracle price stream. The legacy /v2/prices channels remain available for
existing consumers but are deprecated.

Requirements:
- CHAIN_ID: The chain ID (1729 for mainnet, 89346162 for testnet)
- REYA_WS_URL: (optional) WebSocket URL, defaults based on chain ID

Usage:
    python -m examples.websocket.perps.prices_monitoring
"""

import asyncio
import json
import logging
import os
import time

from dotenv import load_dotenv

from sdk.async_api.asset_oracle_prices_update_payload import AssetOraclePricesUpdatePayload
from sdk.async_api.error_message_payload import ErrorMessagePayload
from sdk.async_api.ping_message_payload import PingMessagePayload
from sdk.async_api.pong_message_payload import PongMessagePayload
from sdk.async_api.price_update_payload import PriceUpdatePayload
from sdk.async_api.prices_update_payload import PricesUpdatePayload
from sdk.async_api.subscribed_message_payload import SubscribedMessagePayload
from sdk.reya_websocket import ReyaSocket
from sdk.reya_websocket.config import WebSocketConfig

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

# Create a logger for this module
logger = logging.getLogger("reya.prices_monitoring")

# Legacy symbol to monitor if you opt into /v2/prices/{symbol}.
LEGACY_SYMBOL = "ETHRUSDPERP"


def on_open(ws):
    """Handle WebSocket connection open event."""
    logger.info("Connection established, subscribing to asset oracle prices")

    ws.prices.asset_oracle_prices.subscribe()

    # Deprecated legacy channels:
    # ws.prices.all_prices.subscribe()
    # ws.prices.price(LEGACY_SYMBOL).subscribe()


def handle_asset_oracle_prices_data(payload: AssetOraclePricesUpdatePayload) -> None:
    """Handle /v2/assetOraclePrices channel data."""
    logger.info("💰 Asset Oracle Prices Update:")
    logger.info(f"  ├─ Timestamp: {payload.timestamp}")
    logger.info(f"  ├─ Channel: {payload.channel}")
    logger.info(f"  └─ Assets Count: {len(payload.data)}")

    for i, price in enumerate(payload.data[:5]):
        logger.info(f"    Asset {i + 1}: {price.asset}")
        logger.info(f"      ├─ Oracle Price: {price.oracle_price}")
        logger.info(f"      └─ Updated At: {price.updated_at}")

    if len(payload.data) > 5:
        logger.info(f"    ... and {len(payload.data) - 5} more assets")


def handle_all_prices_data(payload: PricesUpdatePayload) -> None:
    """Handle deprecated /v2/prices channel data."""
    logger.info("💰 Deprecated Legacy Prices Update:")
    logger.info(f"  ├─ Timestamp: {payload.timestamp}")
    logger.info(f"  ├─ Channel: {payload.channel}")
    logger.info(f"  └─ Prices Count: {len(payload.data)}")

    # Showcase individual price data structure
    for i, price in enumerate(payload.data[:5]):  # Show first 5 prices
        logger.info(f"    Price {i + 1}: {price.symbol}")
        logger.info(f"      ├─ Oracle Price: {price.oracle_price or 'N/A'}")
        logger.info(f"      ├─ Pool Price: {price.pool_price or 'N/A'}")
        logger.info(f"      └─ Updated At: {price.updated_at}")

    if len(payload.data) > 5:
        logger.info(f"    ... and {len(payload.data) - 5} more prices")


def handle_single_price_data(payload: PriceUpdatePayload) -> None:
    """Handle deprecated /v2/prices/:symbol channel data."""
    price = payload.data

    logger.info(f"💵 Deprecated Legacy Price Update for {price.symbol}:")
    logger.info(f"  ├─ Timestamp: {payload.timestamp}")
    logger.info(f"  ├─ Channel: {payload.channel}")
    logger.info(f"  ├─ Oracle Price: {price.oracle_price or 'N/A'}")
    logger.info(f"  ├─ Pool Price: {price.pool_price or 'N/A'}")
    logger.info(f"  └─ Updated At: {price.updated_at}")


def on_message(ws, message):
    """Handle WebSocket messages - receives typed Pydantic models from SDK."""
    # Handle subscription confirmations
    if isinstance(message, SubscribedMessagePayload):
        logger.info(f"✅ Successfully subscribed to {message.channel}")
        if message.contents:
            logger.info(f"📦 Initial data received: {len(str(message.contents))} characters")
        return

    if isinstance(message, AssetOraclePricesUpdatePayload):
        handle_asset_oracle_prices_data(message)
        return

    # Handle deprecated legacy price data updates
    if isinstance(message, PricesUpdatePayload):
        handle_all_prices_data(message)
        return

    if isinstance(message, PriceUpdatePayload):
        handle_single_price_data(message)
        return

    # Handle ping/pong
    if isinstance(message, PingMessagePayload):
        logger.info("🏓 Received ping from server, sending pong response")
        ws.send(json.dumps({"type": "pong"}))
        return

    if isinstance(message, PongMessagePayload):
        logger.info("🏓 Connection confirmed via pong response")
        return

    # Handle errors
    if isinstance(message, ErrorMessagePayload):
        logger.error(f"❌ Error: {message.message}")
        return

    # Unknown message type
    logger.debug(f"🔍 Received unhandled message type: {type(message).__name__}")


async def periodic_task(ws):
    """Enhanced periodic task with connection monitoring."""
    counter = 0
    start_time = time.time()

    while True:
        counter += 1
        uptime = time.time() - start_time

        logger.info(f"🔄 Monitoring asset oracle prices (iteration {counter}) - Uptime: {uptime:.1f}s")

        # Monitor connection health
        active_subs = len(ws.active_subscriptions)
        logger.info(f"📊 Connection Status: {active_subs} active subscriptions")

        # Send periodic ping to test connection (every 10 iterations = ~20 seconds)
        if counter % 10 == 0:
            logger.info("🏓 Sending manual ping to test connection")
            ws.send(json.dumps({"type": "ping"}))

        await asyncio.sleep(2)  # Run every 2 seconds


async def main():
    """Main entry point for the example."""
    # Load environment variables
    load_dotenv()

    # Get WebSocket URL from environment
    ws_url = os.environ.get("REYA_WS_URL", "wss://ws.reya.xyz/")

    def on_error(_ws, error):
        logger.error(f"❌ WebSocket error: {error}")

    def on_close(_ws, close_status_code, close_reason):
        logger.info(f"🔌 WebSocket closed: {close_status_code} - {close_reason}")
        if close_status_code != 1000:
            logger.warning(f"⚠️ Abnormal closure detected. Status: {close_status_code}")

    # Create custom config with more aggressive ping settings
    config = WebSocketConfig(
        url=ws_url,
        ping_interval=20,
        ping_timeout=15,
        connection_timeout=60,
        reconnect_attempts=5,
        reconnect_delay=3,
    )

    ws = ReyaSocket(
        config=config,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
    )

    logger.info("Connecting to WebSocket to monitor asset oracle prices")
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
