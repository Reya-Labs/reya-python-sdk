"""
Market Monitoring - Monitor market data streams via WebSocket.

This example connects to the Reya WebSocket API and subscribes to market data streams.

Requirements:
- CHAIN_ID: The chain ID (1729 for mainnet, 89346162 for testnet)
- REYA_WS_URL: (optional) WebSocket URL, defaults based on chain ID

Usage:
    python -m examples.websocket.perps.market_monitoring
"""

import asyncio
import json
import logging
import os
import time

from dotenv import load_dotenv

from sdk.async_api.error_message_payload import ErrorMessagePayload
from sdk.async_api.market_perp_execution_update_payload import MarketPerpExecutionUpdatePayload
from sdk.async_api.market_summary_update_payload import MarketSummaryUpdatePayload
from sdk.async_api.markets_summary_update_payload import MarketsSummaryUpdatePayload
from sdk.async_api.ping_message_payload import PingMessagePayload
from sdk.async_api.pong_message_payload import PongMessagePayload
from sdk.async_api.subscribed_message_payload import SubscribedMessagePayload
from sdk.reya_websocket import ReyaSocket
from sdk.reya_websocket.config import WebSocketConfig

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

# Create a logger for this module
logger = logging.getLogger("reya.market_monitoring")


def on_open(ws):
    """Handle WebSocket connection open event."""
    logger.info("Connection established, subscribing to market data")

    # Subscribe to all markets summary
    # ws.market.all_markets_summary.subscribe()

    # Subscribe to market summary for BTCRUSDPERP
    ws.market.summary("ETHRUSDPERP").subscribe()

    # Subscribe to market perpetual executions for BTCRUSDPERP
    ws.market.perp_executions("ETHRUSDPERP").subscribe()


def handle_markets_summary_data(payload: MarketsSummaryUpdatePayload) -> None:
    """Handle /v2/markets/summary channel data."""
    logger.info("📊 All Markets Summary Update:")
    logger.info(f"  ├─ Timestamp: {payload.timestamp}")
    logger.info(f"  ├─ Channel: {payload.channel}")
    logger.info(f"  └─ Markets Count: {len(payload.data)}")

    # Showcase individual market data structure
    for i, market in enumerate(payload.data[:5]):  # Show first 5 markets
        logger.info(f"    Market {i + 1}: {market.symbol}")
        logger.info(f"      ├─ Volume 24h: {market.volume24h}")
        logger.info(f"      ├─ Funding Rate: {market.funding_rate}")
        logger.info(f"      ├─ Total OI: {market.oi_qty}")
        logger.info(f"      └─ Price Change 24h: {market.px_change24h or 'N/A'}")

    if len(payload.data) > 3:
        logger.info(f"    ... and {len(payload.data) - 3} more markets")


def handle_market_summary_data(payload: MarketSummaryUpdatePayload) -> None:
    """Handle /v2/market/:symbol/summary channel data."""
    market = payload.data

    logger.info(f"📈 Market Summary Update for {market.symbol}:")
    logger.info(f"  ├─ Timestamp: {payload.timestamp}")
    logger.info(f"  ├─ Channel: {payload.channel}")
    logger.info(f"  ├─ Updated At: {market.updated_at}")
    logger.info(f"  ├─ Volume 24h: {market.volume24h}")
    logger.info(f"  ├─ Price Change 24h: {market.px_change24h or 'N/A'}")
    logger.info(f"  ├─ Funding Rate: {market.funding_rate}")
    logger.info(f"  ├─ Long OI: {market.long_oi_qty}")
    logger.info(f"  ├─ Short OI: {market.short_oi_qty}")
    logger.info(f"  ├─ Total OI: {market.oi_qty}")
    logger.info(f"  ├─ Oracle Price: {market.throttled_oracle_price or 'N/A'}")
    logger.info(f"  └─ Pool Price: {market.throttled_pool_price or 'N/A'}")


def handle_market_perp_executions_data(payload: MarketPerpExecutionUpdatePayload) -> None:
    """Handle /v2/market/:symbol/perpExecutions channel data."""
    logger.info("⚡ Market Perpetual Executions Update:")
    logger.info(f"  ├─ Timestamp: {payload.timestamp}")
    logger.info(f"  ├─ Channel: {payload.channel}")
    logger.info(f"  └─ Executions Count: {len(payload.data)}")

    # Showcase individual execution data structure
    for i, execution in enumerate(payload.data[:5]):  # Show first 5 executions
        logger.info(f"    Execution {i + 1}: {execution.symbol}")
        logger.info(f"      ├─ Account ID: {execution.account_id}")
        logger.info(f"      ├─ Side: {execution.side.value}")
        logger.info(f"      ├─ Quantity: {execution.qty}")
        logger.info(f"      ├─ Price: {execution.price}")
        logger.info(f"      ├─ Fee: {execution.fee}")
        logger.info(f"      ├─ Type: {execution.type.value}")
        logger.info(f"      ├─ Timestamp: {execution.timestamp}")
        logger.info(f"      └─ Sequence: {execution.sequence_number}")

    if len(payload.data) > 5:
        logger.info(f"    ... and {len(payload.data) - 5} more executions")


def on_message(ws, message):
    """Handle WebSocket messages - receives typed Pydantic models from SDK."""
    # Handle subscription confirmations
    if isinstance(message, SubscribedMessagePayload):
        logger.info(f"✅ Successfully subscribed to {message.channel}")
        if message.contents:
            logger.info(f"📦 Initial data received: {len(str(message.contents))} characters")
        return

    # Handle market data updates
    if isinstance(message, MarketsSummaryUpdatePayload):
        handle_markets_summary_data(message)
        return

    if isinstance(message, MarketSummaryUpdatePayload):
        handle_market_summary_data(message)
        return

    if isinstance(message, MarketPerpExecutionUpdatePayload):
        handle_market_perp_executions_data(message)
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

        logger.info(f"🔄 Periodic task running (iteration {counter}) - Uptime: {uptime:.1f}s")

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

    # Create enhanced error and close handlers for better connection monitoring
    def on_error(_ws, error):
        logger.error(f"❌ WebSocket error: {error}")

    def on_close(_ws, close_status_code, close_reason):
        logger.info(f"🔌 WebSocket closed: {close_status_code} - {close_reason}")
        if close_status_code != 1000:  # 1000 is normal closure
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

    logger.info("Connecting to WebSocket asynchronously")
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
