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

from dotenv import load_dotenv

# Import the resource-oriented WebSocket client
from sdk.reya_websocket import ReyaSocket

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

# Create a logger for this module
logger = logging.getLogger("reya.example")

# Asset pair to monitor
ASSET_PAIR = "ETHUSDMARK"


def on_open(ws):
    """Handle WebSocket connection open event."""
    logger.info(f"Connection established, subscribing to price data for {ASSET_PAIR}")

    # Subscribe to price data for the specified asset pair
    ws.prices.asset_pair_price(ASSET_PAIR).subscribe()


def on_message(ws, message):
    """Handle WebSocket messages."""
    message_type = message.get("type")

    if message_type == "subscribed":
        channel = message.get("channel", "unknown")
        logger.info(f"Successfully subscribed to {channel}")

        # Log the initial data from subscription
        if "contents" in message:
            logger.info(f"Initial price data: {message['contents']}")

    elif message_type == "channel_data":
        channel = message.get("channel", "unknown")
        contents = message.get("contents", {})

        if "prices" in channel:
            # Handle price data
            if isinstance(contents, dict) and "result" in contents:
                price_data = contents["result"]
                logger.info(f"Price update for {ASSET_PAIR}: {price_data}")

                # Extract specific price data if available
                if isinstance(price_data, dict):
                    price = price_data.get("price")
                    timestamp = price_data.get("timestamp")
                    if price:
                        logger.info(f"Current price: {price} (timestamp: {timestamp})")
            else:
                logger.warning(f"Received data in unexpected format: {contents}")

    elif message_type == "ping":
        logger.info("Received ping, sending pong response")
        ws.send(json.dumps({"type": "pong"}))

    elif message_type == "pong":
        logger.info("Connection confirmed via pong response")

    elif message_type == "error":
        logger.error(f"Error: {message.get('message', 'unknown error')}")

    else:
        logger.debug(f"Received message type: {message_type}")


async def periodic_task():
    """A simple task that runs concurrently with the WebSocket connection."""
    counter = 0
    while True:
        counter += 1
        logger.info(f"Monitoring {ASSET_PAIR} prices (iteration {counter})")

        # Simulate some work (e.g., data processing, calculations, etc.)
        await asyncio.sleep(5)  # Run every 5 seconds

        # Example of some additional operation
        timestamp = time.time()
        logger.info(f"Current timestamp: {timestamp:.2f} - Tracking price movements")


async def main():
    """Main entry point for the example."""
    # Load environment variables
    load_dotenv()

    # Get WebSocket URL from environment
    ws_url = os.environ.get("REYA_WS_URL", "wss://ws.reya.xyz/")

    # Create the WebSocket
    ws = ReyaSocket(
        url=ws_url,
        on_open=on_open,
        on_message=on_message,
    )

    logger.info(f"Connecting to WebSocket to monitor {ASSET_PAIR} prices")
    logger.info("Press Ctrl+C to exit")

    # Connect - this will return immediately
    ws.connect()

    # Start our concurrent task
    asyncio.create_task(periodic_task())

    # Keep the main task running
    while True:
        await asyncio.sleep(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Program interrupted by user. Exiting...")
