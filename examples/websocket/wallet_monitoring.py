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
import time
from typing import Dict, Any, List

from dotenv import load_dotenv
from pydantic import ValidationError

# Import the new resource-oriented WebSocket client
from sdk.reya_websocket import ReyaSocket

# Import WebSocket message types for proper type conversion
from sdk.async_api.position_update_payload import PositionUpdatePayload
from sdk.async_api.open_order_update_payload import OpenOrderUpdatePayload
from sdk.async_api.account_balance_update_payload import AccountBalanceUpdatePayload
from sdk.async_api.wallet_perp_execution_update_payload import WalletPerpExecutionUpdatePayload
from sdk.async_api.position import Position
from sdk.async_api.order import Order
from sdk.async_api.account_balance import AccountBalance
from sdk.async_api.perp_execution import PerpExecution

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

# Create a logger for this module
logger = logging.getLogger("reya.wallet_monitoring")


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

    # Subscribe to wallet perpetual executions
    ws.wallet.perp_executions(wallet_address).subscribe()

    # Subscribe to wallet open orders
    # ws.wallet.open_orders(wallet_address).subscribe()

    # Subscribe to wallet account balances
    # ws.wallet.account_balances(wallet_address).subscribe()


def handle_wallet_positions_data(message: Dict[str, Any]) -> None:
    """Handle /v2/wallet/:address/positions channel data with proper type conversion."""
    try:
        # Convert raw message to typed payload
        payload = PositionUpdatePayload.model_validate(message)
        
        logger.info(f"💼 Wallet Positions Update:")
        logger.info(f"  ├─ Timestamp: {payload.timestamp}")
        logger.info(f"  ├─ Channel: {payload.channel}")
        logger.info(f"  └─ Positions Count: {len(payload.data)}")
        
        # Showcase individual position data structure
        for i, position in enumerate(payload.data[:5]):  # Show first 5 positions
            logger.info(f"    Position {i+1}: {position.symbol}")
            logger.info(f"      ├─ Exchange ID: {position.exchange_id}")
            logger.info(f"      ├─ Account ID: {position.account_id}")
            logger.info(f"      ├─ Quantity: {position.qty}")
            logger.info(f"      ├─ Side: {position.side.value}")
            logger.info(f"      ├─ Avg Entry Price: {position.avg_entry_price}")
            logger.info(f"      ├─ Avg Entry Funding: {position.avg_entry_funding_value}")
            logger.info(f"      └─ Last Trade Seq: {position.last_trade_sequence_number}")
        
        if len(payload.data) > 5:
            logger.info(f"    ... and {len(payload.data) - 5} more positions")
            
    except ValidationError as e:
        logger.error(f"Failed to parse wallet positions data: {e}")
    except Exception as e:
        logger.error(f"Unexpected error handling wallet positions: {e}")


def handle_wallet_orders_data(message: Dict[str, Any]) -> None:
    """Handle /v2/wallet/:address/openOrders channel data with proper type conversion."""
    try:
        # Convert raw message to typed payload
        payload = OpenOrderUpdatePayload.model_validate(message)
        
        logger.info(f"📋 Wallet Open Orders Update:")
        logger.info(f"  ├─ Timestamp: {payload.timestamp}")
        logger.info(f"  ├─ Channel: {payload.channel}")
        logger.info(f"  └─ Orders Count: {len(payload.data)}")
        
        # Showcase individual order data structure
        for i, order in enumerate(payload.data[:5]):  # Show first 5 orders
            logger.info(f"    Order {i+1}: {order.symbol}")
            logger.info(f"      ├─ Account ID: {order.account_id}")
            logger.info(f"      ├─ Side: {order.side.value}")
            logger.info(f"      ├─ Type: {order.type.value}")
            logger.info(f"      ├─ Quantity: {order.qty}")
            logger.info(f"      ├─ Price: {order.price}")
            logger.info(f"      └─ Status: {order.status.value}")
        
        if len(payload.data) > 5:
            logger.info(f"    ... and {len(payload.data) - 5} more orders")
            
    except ValidationError as e:
        logger.error(f"Failed to parse wallet orders data: {e}")
    except Exception as e:
        logger.error(f"Unexpected error handling wallet orders: {e}")


def handle_wallet_balances_data(message: Dict[str, Any]) -> None:
    """Handle /v2/wallet/:address/accountBalances channel data with proper type conversion."""
    try:
        # Convert raw message to typed payload
        payload = AccountBalanceUpdatePayload.model_validate(message)
        
        logger.info(f"💰 Wallet Account Balances Update:")
        logger.info(f"  ├─ Timestamp: {payload.timestamp}")
        logger.info(f"  ├─ Channel: {payload.channel}")
        logger.info(f"  └─ Balances Count: {len(payload.data)}")
        
        # Showcase individual balance data structure
        for i, balance in enumerate(payload.data[:3]):  # Show first 3 balances
            logger.info(f"    Balance {i+1}: Account {balance.account_id}")
            logger.info(f"      ├─ Symbol: {balance.symbol}")
            logger.info(f"      └─ Balance: {balance.balance}")
        
        if len(payload.data) > 3:
            logger.info(f"    ... and {len(payload.data) - 3} more balances")
            
    except ValidationError as e:
        logger.error(f"Failed to parse wallet balances data: {e}")
    except Exception as e:
        logger.error(f"Unexpected error handling wallet balances: {e}")


def handle_wallet_executions_data(message: Dict[str, Any]) -> None:
    """Handle /v2/wallet/:address/perpExecutions channel data with proper type conversion."""
    try:
        # Convert raw message to typed payload
        payload = WalletPerpExecutionUpdatePayload.model_validate(message)
        
        logger.info(f"⚡ Wallet Perpetual Executions Update:")
        logger.info(f"  ├─ Timestamp: {payload.timestamp}")
        logger.info(f"  ├─ Channel: {payload.channel}")
        logger.info(f"  └─ Executions Count: {len(payload.data)}")
        
        # Showcase individual execution data structure
        for i, execution in enumerate(payload.data[:5]):  # Show first 5 executions
            logger.info(f"    Execution {i+1}: {execution.symbol}")
            logger.info(f"      ├─ Account ID: {execution.account_id}")
            logger.info(f"      ├─ Side: {execution.side.value}")
            logger.info(f"      ├─ Quantity: {execution.qty}")
            logger.info(f"      ├─ Price: {execution.price}")
            logger.info(f"      ├─ Fee: {execution.fee}")
            logger.info(f"      └─ Type: {execution.type.value}")
        
        if len(payload.data) > 5:
            logger.info(f"    ... and {len(payload.data) - 5} more executions")
            
    except ValidationError as e:
        logger.error(f"Failed to parse wallet executions data: {e}")
    except Exception as e:
        logger.error(f"Unexpected error handling wallet executions: {e}")


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
        if "/v2/wallet/" in channel:
            if channel.endswith("/positions"):
                handle_wallet_positions_data(message)
            elif channel.endswith("/openOrders"):
                handle_wallet_orders_data(message)
            elif channel.endswith("/accountBalances"):
                handle_wallet_balances_data(message)
            elif channel.endswith("/perpExecutions"):
                handle_wallet_executions_data(message)
            else:
                logger.warning(f"🔍 Unhandled wallet channel: {channel}")
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


async def periodic_task(ws, wallet_address):
    """Enhanced periodic task with connection monitoring."""
    counter = 0
    start_time = time.time()
    
    while True:
        counter += 1
        uptime = time.time() - start_time
        
        logger.info(f"🔄 Monitoring wallet {wallet_address[:8]}... (iteration {counter}) - Uptime: {uptime:.1f}s")
        
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

    # Check if wallet address is set
    wallet_address = os.environ.get("WALLET_ADDRESS")
    if not wallet_address:
        logger.error("Please set the WALLET_ADDRESS environment variable")
        logger.error("Add WALLET_ADDRESS=0x... to your .env file")
        return

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

    logger.info(f"Connecting to WebSocket to monitor wallet: {wallet_address}")
    logger.info("Press Ctrl+C to exit")

    # Connect
    ws.connect()

    # Start our concurrent task with WebSocket reference
    asyncio.create_task(periodic_task(ws, wallet_address))

    # Main application loop
    while True:
        await asyncio.sleep(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Program interrupted by user. Exiting...")
