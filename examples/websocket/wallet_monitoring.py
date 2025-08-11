"""Example of monitoring wallet positions, orders, and balances.

This example connects to the Reya WebSocket API and subscribes to all wallet-related data streams for a specific wallet address.

Before running this example, ensure you have a .env file with the following variables:
- PRIVATE_KEY: Your Ethereum private key
- ACCOUNT_ID: Your Reya account ID
- CHAIN_ID: The chain ID (1729 for mainnet, 89346162 for testnet)
- WALLET_ADDRESS: The wallet address to monitor
"""

import asyncio
import json
import logging
import os

from dotenv import load_dotenv

# Import the new resource-oriented WebSocket client
from sdk.reya_websocket import ReyaSocket

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

# Create a logger for this module
logger = logging.getLogger("reya.example")


# Ping sender function (using asyncio)
async def start_ping_sender(ws, interval=30):
    """Start an async task that sends periodic pings to the server."""
    stop_event = asyncio.Event()

    async def send_pings():
        while not stop_event.is_set():
            # Sleep first to let connection establish
            await asyncio.sleep(interval)
            if not stop_event.is_set():
                logger.info("Sending ping message")
                ws.send(json.dumps({"type": "ping"}))

    # Start ping sender task
    task = asyncio.create_task(send_pings())

    # Return both the task and stop event for canceling
    return task, stop_event


def on_open(ws):
    """Handle WebSocket connection open event."""
    logger.info("Connection established, subscribing to wallet data")

    # Get wallet address from environment
    wallet_address = os.environ.get("WALLET_ADDRESS")
    if not wallet_address:
        logger.error("WALLET_ADDRESS environment variable not set")
        return

    logger.info(f"Monitoring wallet: {wallet_address}")

    # Subscribe to wallet positions
    ws.wallet.positions(wallet_address).subscribe()

    # Subscribe to wallet orders
    ws.wallet.orders(wallet_address).subscribe()

    # Subscribe to wallet open orders
    ws.wallet.open_orders(wallet_address).subscribe()

    # Subscribe to wallet account balances
    ws.wallet.balances(wallet_address).subscribe()


def on_message(ws, message):
    """Handle WebSocket messages."""
    message_type = message.get("type")

    if message_type == "subscribed":
        channel = message.get("channel", "unknown")
        logger.info(f"Successfully subscribed to {channel}")

        # Log the initial data from subscription
        if "contents" in message:
            logger.info(f"Initial market data: {message['contents']}")

    elif message_type == "channel_data":
        channel = message.get("channel", "unknown")
        logger.info(f"Received data from {channel}")
        logger.info(f"Data: {message}")

    elif message_type == "error":
        logger.error(f"Error: {message.get('message', 'unknown error')}")

    elif message_type == "ping":
        logger.info("Received ping, sending pong response")
        ws.send(json.dumps({"type": "pong"}))

    elif message_type == "pong":
        logger.info("Connection confirmed via pong response")

    else:
        logger.debug(f"Received message of type: {message_type}")
        logger.debug(f"Message content: {message}")


async def main():
    """Main entry point for the example."""
    # Load environment variables
    load_dotenv()

    # Get WebSocket URL from environment
    ws_url = os.environ.get("REYA_WS_URL", "wss://ws.reya.xyz/")

    # Check if wallet address is set
    if not os.environ.get("WALLET_ADDRESS"):
        logger.error("Please set the WALLET_ADDRESS environment variable")
        logger.error("Add WALLET_ADDRESS=0x... to your .env file")
        return

    logger.info(f"Connecting to {ws_url}")

    # Create the WebSocket
    ws = ReyaSocket(
        url=ws_url,
        on_open=on_open,
        on_message=on_message,
    )

    # Set up ping interval (in seconds)
    ping_interval = int(os.environ.get("REYA_WS_PING_INTERVAL", "30"))

    # Connect to the WebSocket server (non-blocking)
    logger.info("Connecting to WebSocket server")

    ping_task = None
    ping_stop_event = None

    # Connect
    ws.connect()

    # Start the ping sender task
    ping_task, ping_stop_event = await start_ping_sender(ws, interval=ping_interval)
    logger.info(f"Started ping sender task (interval: {ping_interval}s)")

    # Keep the main task running
    while True:
        # Perform any periodic tasks here
        await asyncio.sleep(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Program interrupted by user. Exiting...")
