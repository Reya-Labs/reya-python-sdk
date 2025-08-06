"""Basic example of using the resource-oriented WebSocket API client.

This example connects to the Reya WebSocket API and subscribes to market data for a specific market.

Before running this example, ensure you have a .env file with the following variables:
- PRIVATE_KEY: Your Ethereum private key
- ACCOUNT_ID: Your Reya account ID
- CHAIN_ID: The chain ID (1729 for mainnet, 89346162 for testnet)
"""

import os
import logging
from dotenv import load_dotenv

# Import the new resource-oriented WebSocket client
from sdk.reya_websocket import ReyaSocket

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

# Create a logger for this module
logger = logging.getLogger("reya.example")

def on_open(ws):
    """Handle WebSocket connection open event."""
    logger.info("Connection established, subscribing to market data")

    # Subscribe to all market data
    # ws.market.all_markets.subscribe()

    # Subscribe to market data orders
    # ws.market.market_orders(1).subscribe()

    # Subscribe to market data for market ID 1
    ws.market.market_data(1).subscribe()

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
        contents = message.get("contents", {})
        
        if "market" in channel:
            # Handle nested structure with result object
            if isinstance(contents, dict) and "result" in contents:
                # Log the market data directly
                logger.info(f"Market update: {contents['result']}")
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


def main():
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

    # Connect to the WebSocket server - this is a blocking call
    logger.info("Connecting to WebSocket and starting event loop")
    logger.info("Press Ctrl+C to exit")
    
    try:
        # This will run forever until interrupted
        ws.connect()
    except KeyboardInterrupt:
        logger.info("Exiting gracefully")
    except Exception as e:
        logger.error(f"Error: {e}")
    finally:
        logger.info("WebSocket connection closed")

if __name__ == "__main__":
    main()
