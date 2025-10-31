#!/usr/bin/env python3
"""
Comprehensive example of creating different types of orders with the Reya Trading API.

This example demonstrates all supported order types:
- IOC (Immediate or Cancel) Market Orders
- GTC (Good Till Cancel) Limit Orders
- Stop Loss (SL) Orders
- Take Profit (TP) Orders
- Order Cancellation

Before running this example, ensure you have a .env file with the following variables:
- PRIVATE_KEY: Your Ethereum private key
- ACCOUNT_ID: Your Reya account ID
- CHAIN_ID: The chain ID (1729 for mainnet, 89346162 for testnet)
- API_URL: The API URL (optional, defaults based on chain ID)
"""
import asyncio
import logging
import os

from dotenv import load_dotenv

from sdk.open_api.models.order_type import OrderType
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api import ReyaTradingClient
from sdk.reya_rest_api.models.orders import LimitOrderParameters, TriggerOrderParameters

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

# Create a logger for this module
logger = logging.getLogger("reya.order_entry")


def print_separator(title: str):
    """Print a section separator."""
    logger.info("=" * 60)
    logger.info(f" {title} ")
    logger.info("=" * 60)


def handle_order_response(order_type: str, response):
    logger.info(f"✅ {order_type} order created successfully!")
    if response.order_id:
        logger.info(f"   Order ID: {response.order_id}")
    logger.info(f"   Status: {response.status}")

    return response


async def run_ioc_limit_orders_test(client: ReyaTradingClient):
    """Test IOC (Immediate or Cancel) limit orders asynchronously."""
    print_separator("TESTING IOC LIMIT ORDERS")

    # Test buy limit order
    logger.info("Creating IOC limit buy order...")
    response = await client.create_limit_order(
        LimitOrderParameters(
            symbol="ETHRUSDPERP",
            is_buy=True,
            limit_px="40000",
            qty="0.02",
            time_in_force=TimeInForce.IOC,
            reduce_only=False,
        )
    )

    handle_order_response("IOC Limit Buy", response)

    # Test sell limit order
    logger.info("Creating IOC limit sell order...")
    response = await client.create_limit_order(
        LimitOrderParameters(
            symbol="ETHRUSDPERP",
            is_buy=False,
            limit_px="20",
            qty="0.01",
            time_in_force=TimeInForce.IOC,
            reduce_only=False,
        )
    )
    handle_order_response("IOC Limit Sell", response)

    # Test reduce-only limit order
    logger.info("Creating reduce-only IOC limit order...")
    response = await client.create_limit_order(
        LimitOrderParameters(
            symbol="ETHRUSDPERP",
            is_buy=False,
            limit_px="20",
            qty="0.01",
            time_in_force=TimeInForce.IOC,
            reduce_only=True,
        )
    )
    handle_order_response("IOC Reduce-Only Limit", response)


async def run_gtc_limit_orders_test(client: ReyaTradingClient):
    """Test GTC (Good Till Cancel) limit orders asynchronously."""
    print_separator("TESTING GTC LIMIT ORDERS")

    # Test buy limit order
    logger.info("Creating GTC limit buy order...")
    response = await client.create_limit_order(
        LimitOrderParameters(
            symbol="ETHRUSDPERP",
            is_buy=True,
            limit_px="10",
            qty="0.01",
            time_in_force=TimeInForce.GTC,
        )
    )
    buy_order_response = handle_order_response("GTC Limit Buy", response)

    # Test sell limit order
    logger.info("Creating GTC limit sell order...")
    response = await client.create_limit_order(
        LimitOrderParameters(
            symbol="ETHRUSDPERP",
            is_buy=False,
            limit_px="40000",
            qty="0.01",
            time_in_force=TimeInForce.GTC,
        )
    )
    sell_order_response = handle_order_response("GTC Limit Sell", response)

    return buy_order_response.order_id, sell_order_response.order_id


async def run_stop_loss_orders_test(client: ReyaTradingClient):
    """Test Stop Loss orders asynchronously."""
    print_separator("TESTING STOP LOSS ORDERS")

    # Test stop loss for long position (sell when price drops)
    logger.info("Creating stop loss for long position...")
    response = await client.create_trigger_order(
        TriggerOrderParameters(
            symbol="ETHRUSDPERP",
            is_buy=False,
            trigger_px="1000",
            trigger_type=OrderType.SL,
        )
    )
    long_sl_response = handle_order_response("Stop Loss (Long Position)", response)

    # Test stop loss for short position (buy when price rises)
    logger.info("Creating stop loss for short position...")
    response = await client.create_trigger_order(
        TriggerOrderParameters(
            symbol="ETHRUSDPERP",
            is_buy=True,
            trigger_px="9000",
            trigger_type=OrderType.SL,
        )
    )
    short_sl_response = handle_order_response("Stop Loss (Short Position)", response)

    return long_sl_response.order_id, short_sl_response.order_id


async def run_take_profit_orders_test(client: ReyaTradingClient):
    """Test Take Profit orders asynchronously."""
    print_separator("TESTING TAKE PROFIT ORDERS")

    # Test take profit for long position (sell when price rises)
    logger.info("Creating take profit for long position...")
    response = await client.create_trigger_order(
        TriggerOrderParameters(
            symbol="ETHRUSDPERP",
            is_buy=False,
            trigger_px="10000",
            trigger_type=OrderType.TP,
        )
    )
    long_tp_response = handle_order_response("Take Profit (Long Position)", response)

    # Test take profit for short position (buy when price drops)
    logger.info("Creating take profit for short position...")
    response = await client.create_trigger_order(
        TriggerOrderParameters(
            symbol="ETHRUSDPERP",
            is_buy=True,
            trigger_px="1500",
            trigger_type=OrderType.TP,
        )
    )
    short_tp_response = handle_order_response("Take Profit (Short Position)", response)

    return long_tp_response.order_id, short_tp_response.order_id


async def run_order_cancellation_test(client: ReyaTradingClient, order_ids: list):
    """Test order cancellation asynchronously."""
    print_separator("TESTING ORDER CANCELLATION")

    valid_order_ids = [oid for oid in order_ids if oid is not None]

    if not valid_order_ids:
        logger.warning("⚠️  No valid order IDs available for cancellation testing")
        return

    # Cancel the first available order
    order_id = valid_order_ids[0]
    logger.info(f"Attempting to cancel order: {order_id}")

    response = await client.cancel_order(order_id=order_id)
    handle_order_response("Order Cancellation", response)


async def run_order_retrieval_test(client: ReyaTradingClient):
    """Test retrieving orders and positions asynchronously."""
    print_separator("TESTING ORDER AND POSITION RETRIEVAL")

    # Get trades
    logger.info("Retrieving trades...")
    trades = await client.wallet.get_wallet_perp_executions(address=client.owner_wallet_address or "")
    logger.info(f"📊 Found {len(trades.data)} trades")

    # Get open orders
    logger.info("Retrieving open orders...")
    open_orders = await client.get_open_orders()
    logger.info(f"📊 Found {len(open_orders)} open orders")

    # Get positions
    logger.info("Retrieving positions...")
    positions = await client.get_positions()
    logger.info(f"📊 Found {len(positions)} positions")


async def main():
    """Run comprehensive order testing asynchronously."""
    print_separator("REYA TRADING API - COMPREHENSIVE ORDER ENTRY EXAMPLES")
    logger.info("🚀 Starting comprehensive order testing...")

    # Load environment variables
    load_dotenv()

    # Verify required environment variables
    required_vars = ["PRIVATE_KEY", "ACCOUNT_ID"]
    missing_vars = [var for var in required_vars if not os.getenv(var)]

    if missing_vars:
        logger.error(f"❌ Missing required environment variables: {', '.join(missing_vars)}")
        logger.error("Please check your .env file and ensure all required variables are set.")
        return

    # Create a client instance with proper session management
    async with ReyaTradingClient() as client:
        logger.info("✅ Client initialized successfully")
        logger.info(f"   Account ID: {client.config.account_id}")
        logger.info(f"   Chain ID: {client.config.chain_id}")
        logger.info(f"   API URL: {client.config.api_url}")
        logger.info(f"   Wallet: {client.owner_wallet_address}")

        await client.start()

        # Collect order IDs for cancellation testing
        all_order_ids = []

        # Test 1: IOC Limit Orders
        await run_ioc_limit_orders_test(client)

        # Test 2: GTC Limit Orders
        buy_limit_id, sell_limit_id = await run_gtc_limit_orders_test(client)
        all_order_ids.extend([buy_limit_id, sell_limit_id])

        # Test 3: Stop Loss Orders
        long_sl_id, short_sl_id = await run_stop_loss_orders_test(client)
        all_order_ids.extend([long_sl_id, short_sl_id])

        # Test 4: Take Profit Orders
        long_tp_id, short_tp_id = await run_take_profit_orders_test(client)
        all_order_ids.extend([long_tp_id, short_tp_id])

        # Test 5: Order Retrieval
        await run_order_retrieval_test(client)

        # Test 6: Order Cancellation (optional)
        # Uncomment the next line to test order cancellation
        await run_order_cancellation_test(client, all_order_ids)

        print_separator("TESTING COMPLETE")
        logger.info("🎉 All order type tests completed!")
        logger.info("💡 Review the logs above to see results for each order type.")
        logger.info(
            "📝 Note: Some orders may fail due to market conditions, insufficient balance, or other constraints."
        )


if __name__ == "__main__":
    asyncio.run(main())
