"""Example of monitoring wallet positions, orders, and balances.

This example connects to the Reya WebSocket API and subscribes to all
wallet-related data streams for a specific wallet address.
"""

import os
import json
import time
import logging
import threading
from dotenv import load_dotenv

# Import the new resource-oriented WebSocket client
from reya_data_feed import ReyaSocket

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

# Create a logger for this module
logger = logging.getLogger("reya.example")

# Ping sender function
def start_ping_sender(ws, interval=30):
    """Start a thread that sends periodic pings to the server."""
    stop_event = threading.Event()
    
    def send_pings():
        while not stop_event.is_set():
            try:
                # Sleep first to let connection establish
                time.sleep(interval)
                if not stop_event.is_set():
                    logger.info(f"Sending ping message")
                    ws.send(json.dumps({"type": "ping"}))
            except Exception as e:
                logger.error(f"Error sending ping: {e}")
                if stop_event.is_set():
                    break
    
    # Start ping sender thread
    ping_thread = threading.Thread(target=send_pings)
    ping_thread.daemon = True
    ping_thread.start()
    
    # Return the stop event for canceling
    return stop_event

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
    # ws.wallet.positions(wallet_address).subscribe()
    
    # Subscribe to wallet orders
    # ws.wallet.orders(wallet_address).subscribe()

    # Subscribe to wallet conditional orders
    # ws.wallet.conditional_orders(wallet_address).subscribe()
    
    # Subscribe to wallet account balances
    ws.wallet.balances(wallet_address).subscribe()

def on_message(ws, message):
    """Handle WebSocket messages."""
    # No need to reset ping timer anymore since we're sending at fixed intervals    
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
        
def main():
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
    
    # Connect to the WebSocket server (blocking call)
    logger.info("Connecting to WebSocket server")
    try:
        # Start the ping sender thread
        ping_stop_event = start_ping_sender(ws, interval=ping_interval)
        logger.info(f"Started ping sender thread (interval: {ping_interval}s)")
        
        # This call is blocking and will run until interrupted
        ws.connect()
    except KeyboardInterrupt:
        logger.info("Exiting gracefully")
    except Exception as e:
        logger.error(f"Error: {e}")
    finally:
        # Signal the ping thread to stop
        if 'ping_stop_event' in locals():
            ping_stop_event.set()
            logger.info("Ping sender stopped")
        logger.info("WebSocket connection closed")

if __name__ == "__main__":
    main()
