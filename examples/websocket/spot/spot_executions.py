"""
Spot Executions Monitoring - Listen to spot execution updates via WebSocket.

This example connects to the Reya WebSocket API and subscribes to spot execution
updates for a specific wallet address.

Requirements:
- CHAIN_ID: The chain ID (1729 for mainnet, 89346162 for testnet)
- SPOT_WALLET_ADDRESS_1: The wallet address to monitor for spot executions

Usage:
    python -m examples.websocket.spot.spot_executions
"""

from typing import Any

import asyncio
import json
import logging
import os
import time

from dotenv import load_dotenv

from sdk.async_api.error_message_payload import ErrorMessagePayload
from sdk.async_api.ping_message_payload import PingMessagePayload
from sdk.async_api.pong_message_payload import PongMessagePayload
from sdk.async_api.subscribed_message_payload import SubscribedMessagePayload
from sdk.async_api.wallet_spot_execution_update_payload import WalletSpotExecutionUpdatePayload
from sdk.reya_websocket import ReyaSocket, WebSocketMessage
from sdk.reya_websocket.config import WebSocketConfig

# =============================================================================
# LOGGING SETUP
# =============================================================================

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("reya.spot_executions")


# =============================================================================
# WEBSOCKET HANDLERS
# =============================================================================


def on_open(ws):
    """Handle WebSocket connection open event."""
    logger.info("Connection established, subscribing to spot executions")

    wallet_address = os.environ.get("SPOT_WALLET_ADDRESS_1")
    if not wallet_address:
        logger.error("SPOT_WALLET_ADDRESS_1 environment variable is required")
        return

    logger.info(f"Monitoring wallet: {wallet_address}")

    # Subscribe to wallet spot executions
    ws.wallet.spot_executions(wallet_address).subscribe()


def handle_wallet_spot_executions_data(payload: WalletSpotExecutionUpdatePayload) -> None:
    """Handle /v2/wallet/:address/spotExecutions channel data."""
    logger.info("⚡ Wallet Spot Executions Update:")
    logger.info(f"  ├─ Timestamp: {payload.timestamp}")
    logger.info(f"  ├─ Channel: {payload.channel}")
    logger.info(f"  └─ Executions Count: {len(payload.data)}")

    for i, execution in enumerate(payload.data[:5]):
        logger.info(f"    Execution {i + 1}: {execution.symbol}")
        logger.info(f"      ├─ Account ID: {execution.account_id}")
        logger.info(f"      ├─ Side: {execution.side.value}")
        logger.info(f"      ├─ Quantity: {execution.qty}")
        logger.info(f"      ├─ Price: {execution.price}")
        logger.info(f"      ├─ Fee: {execution.fee}")
        logger.info(f"      ├─ Type: {execution.type.value}")
        logger.info(f"      └─ Order ID: {execution.order_id}")

    if len(payload.data) > 5:
        logger.info(f"    ... and {len(payload.data) - 5} more executions")


def on_message(ws, message: WebSocketMessage):
    """Handle WebSocket messages - receives typed Pydantic models from the SDK."""
    if isinstance(message, SubscribedMessagePayload):
        logger.info(f"✅ Successfully subscribed to {message.channel}")
        if message.contents is not None:
            logger.info(f"📦 Initial data received: {len(str(message.contents))} characters")

    elif isinstance(message, WalletSpotExecutionUpdatePayload):
        handle_wallet_spot_executions_data(message)

    elif isinstance(message, PingMessagePayload):
        logger.info("🏓 Received ping from server, sending pong response")
        ws.send(json.dumps({"type": "pong"}))
        logger.debug("✅ Pong sent successfully")

    elif isinstance(message, PongMessagePayload):
        logger.info("🏓 Connection confirmed via pong response")

    elif isinstance(message, ErrorMessagePayload):
        logger.error(f"❌ Error: {message.message}")

    else:
        logger.debug(f"🔍 Received message type: {type(message).__name__}")


# =============================================================================
# PERIODIC TASK
# =============================================================================


async def periodic_task(ws, wallet_address: str):
    """Periodic task with connection monitoring."""
    counter = 0
    start_time = time.time()

    while True:
        counter += 1
        uptime = time.time() - start_time

        logger.info(f"🔄 Monitoring wallet {wallet_address[:8]}... (iteration {counter}) - Uptime: {uptime:.1f}s")

        active_subs = len(ws.active_subscriptions)
        logger.info(f"📊 Connection Status: {active_subs} active subscriptions")

        if counter % 10 == 0:
            logger.info("🏓 Sending manual ping to test connection")
            ws.send(json.dumps({"type": "ping"}))

        await asyncio.sleep(2)


# =============================================================================
# MAIN
# =============================================================================


async def main():
    """Main entry point for the example."""
    load_dotenv()

    wallet_address = os.environ.get("SPOT_WALLET_ADDRESS_1")
    if not wallet_address:
        logger.error("Please set the SPOT_WALLET_ADDRESS_1 environment variable")
        logger.error("Add SPOT_WALLET_ADDRESS_1=0x... to your .env file")
        return

    ws_url = os.environ.get("REYA_WS_URL", "wss://ws.reya.xyz/")

    def on_error(_ws, error):
        """Handle WebSocket errors."""
        logger.error(f"❌ WebSocket error: {error}")

    def on_close(_ws, close_status_code, close_reason):
        """Handle WebSocket close events."""
        logger.info(f"🔌 WebSocket closed: {close_status_code} - {close_reason}")
        if close_status_code != 1000:
            logger.warning(f"⚠️ Abnormal closure detected. Status: {close_status_code}")

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

    logger.info(f"Connecting to WebSocket to monitor spot executions for wallet: {wallet_address}")
    logger.info("Press Ctrl+C to exit")

    ws.connect()

    asyncio.create_task(periodic_task(ws, wallet_address))

    while True:
        await asyncio.sleep(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nProgram interrupted by user. Exiting...")
