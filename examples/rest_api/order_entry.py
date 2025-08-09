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

from sdk.reya_rest_api import ReyaTradingClient
from sdk.reya_rest_api.constants.enums import Limit, LimitOrderType, TimeInForce

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
    """Handle and log order response."""
    if hasattr(response, "raw_response"):
        raw = response.raw_response
        if raw.get("success", False):
            logger.info(f"✅ {order_type} order created successfully!")
            if "orderId" in raw:
                logger.info(f"   Order ID: {raw['orderId']}")
            if "transactionHash" in raw:
                logger.info(f"   Transaction Hash: {raw['transactionHash']}")
        else:
            logger.error(f"❌ {order_type} order failed:")
            logger.error(f"   Full error response: {raw}")
            if isinstance(raw, dict):
                logger.error(f"   Error message: {raw.get('error', 'Unknown error')}")
                if "details" in raw:
                    logger.error(f"   Error details: {raw['details']}")
                if "code" in raw:
                    logger.error(f"   Error code: {raw['code']}")
    else:
        logger.info(f"📝 {order_type} response: {response}")
    return response


async def test_ioc_limit_orders(client: ReyaTradingClient):
    """Test IOC (Immediate or Cancel) limit orders asynchronously."""
    print_separator("TESTING IOC LIMIT ORDERS")

    # Set order type
    order_type = LimitOrderType(limit=Limit(time_in_force=TimeInForce.IOC))

    # Test buy limit order
    logger.info("Creating IOC limit buy order...")
    response = await client.create_limit_order(
        market_id=1, is_buy=True, price="40000", size="0.02", order_type=order_type, reduce_only=False
    )
    handle_order_response("IOC Limit Buy", response)

    # Test sell limit order
    logger.info("Creating IOC limit sell order...")
    response = await client.create_limit_order(
        market_id=1,
        is_buy=False,
        price="20",
        size="0.01",
        order_type=order_type,
        reduce_only=False,
    )
    handle_order_response("IOC Limit Sell", response)

    # Test reduce-only limit order
    logger.info("Creating reduce-only IOC limit order...")
    response = await client.create_limit_order(
        market_id=1, is_buy=False, price="20", size="0.01", order_type=order_type, reduce_only=True
    )
    handle_order_response("IOC Reduce-Only Limit", response)


async def test_gtc_limit_orders(client: ReyaTradingClient):
    """Test GTC (Good Till Cancel) limit orders asynchronously."""
    print_separator("TESTING GTC LIMIT ORDERS")

    # Set order type
    order_type = LimitOrderType(limit=Limit(time_in_force=TimeInForce.GTC))

    # Test buy limit order
    logger.info("Creating GTC limit buy order...")
    response = await client.create_limit_order(
        market_id=1,
        is_buy=True,
        price="10",
        size="0.01",
        order_type=order_type,
    )
    buy_order_response = handle_order_response("GTC Limit Buy", response)

    # Test sell limit order
    logger.info("Creating GTC limit sell order...")
    response = await client.create_limit_order(
        market_id=1,
        is_buy=False,
        price="40000",
        size="0.01",
        order_type=order_type,
    )
    sell_order_response = handle_order_response("GTC Limit Sell", response)

    # Return order IDs for potential cancellation testing
    buy_order_id = None
    sell_order_id = None

    if hasattr(buy_order_response, "raw_response") and "orderId" in buy_order_response.raw_response:
        buy_order_id = buy_order_response.raw_response["orderId"]

    if hasattr(sell_order_response, "raw_response") and "orderId" in sell_order_response.raw_response:
        sell_order_id = sell_order_response.raw_response["orderId"]

    return buy_order_id, sell_order_id


async def test_stop_loss_orders(client: ReyaTradingClient):
    """Test Stop Loss orders asynchronously."""
    print_separator("TESTING STOP LOSS ORDERS")

    # Test stop loss for long position (sell when price drops)
    logger.info("Creating stop loss for long position...")
    response = await client.create_stop_loss_order(
        market_id=1,
        is_buy=False,
        trigger_price="1000",
    )
    long_sl_response = handle_order_response("Stop Loss (Long Position)", response)

    # Test stop loss for short position (buy when price rises)
    logger.info("Creating stop loss for short position...")
    response = await client.create_stop_loss_order(
        market_id=1,
        is_buy=True,
        trigger_price="9000",
    )
    short_sl_response = handle_order_response("Stop Loss (Short Position)", response)

    # Return order IDs
    long_sl_id = None
    short_sl_id = None

    if hasattr(long_sl_response, "raw_response") and "orderId" in long_sl_response.raw_response:
        long_sl_id = long_sl_response.raw_response["orderId"]

    if hasattr(short_sl_response, "raw_response") and "orderId" in short_sl_response.raw_response:
        short_sl_id = short_sl_response.raw_response["orderId"]

    return long_sl_id, short_sl_id


async def test_take_profit_orders(client: ReyaTradingClient):
    """Test Take Profit orders asynchronously."""
    print_separator("TESTING TAKE PROFIT ORDERS")

    # Test take profit for long position (sell when price rises)
    logger.info("Creating take profit for long position...")
    response = await client.create_take_profit_order(
        market_id=1,
        is_buy=False,
        trigger_price="10000",
    )
    long_tp_response = handle_order_response("Take Profit (Long Position)", response)

    # Test take profit for short position (buy when price drops)
    logger.info("Creating take profit for short position...")
    response = await client.create_take_profit_order(
        market_id=1,
        is_buy=True,
        trigger_price="1500",
    )
    short_tp_response = handle_order_response("Take Profit (Short Position)", response)

    # Return order IDs
    long_tp_id = None
    short_tp_id = None

    if hasattr(long_tp_response, "raw_response") and "orderId" in long_tp_response.raw_response:
        long_tp_id = long_tp_response.raw_response["orderId"]

    if hasattr(short_tp_response, "raw_response") and "orderId" in short_tp_response.raw_response:
        short_tp_id = short_tp_response.raw_response["orderId"]

    return long_tp_id, short_tp_id


async def test_order_cancellation(client: ReyaTradingClient, order_ids: list):
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


async def test_order_retrieval(client: ReyaTradingClient):
    """Test retrieving orders and positions asynchronously."""
    print_separator("TESTING ORDER AND POSITION RETRIEVAL")

    # Get trades
    logger.info("Retrieving trades...")
    trades = await client.get_trades()
    logger.info(f"📊 Found {len(trades)} trades")

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

    # Create a client instance
    client = ReyaTradingClient()
    logger.info("✅ Client initialized successfully")
    logger.info(f"   Account ID: {client.config.account_id}")
    logger.info(f"   Chain ID: {client.config.chain_id}")
    logger.info(f"   API URL: {client.config.api_url}")
    logger.info(f"   Wallet: {client.wallet_address}")

    # Collect order IDs for cancellation testing
    all_order_ids = []

    # Test 1: IOC Limit Orders
    await test_ioc_limit_orders(client)

    # Test 2: GTC Limit Orders
    buy_limit_id, sell_limit_id = await test_gtc_limit_orders(client)
    all_order_ids.extend([buy_limit_id, sell_limit_id])

    # Test 3: Stop Loss Orders
    long_sl_id, short_sl_id = await test_stop_loss_orders(client)
    all_order_ids.extend([long_sl_id, short_sl_id])

    # Test 4: Take Profit Orders
    long_tp_id, short_tp_id = await test_take_profit_orders(client)
    all_order_ids.extend([long_tp_id, short_tp_id])

    # Test 5: Order Retrieval
    await test_order_retrieval(client)

    # Test 6: Order Cancellation (optional)
    # Uncomment the next line to test order cancellation
    await test_order_cancellation(client, all_order_ids)

    print_separator("TESTING COMPLETE")
    logger.info("🎉 All order type tests completed!")
    logger.info("💡 Review the logs above to see results for each order type.")
    logger.info("📝 Note: Some orders may fail due to market conditions, insufficient balance, or other constraints.")


if __name__ == "__main__":
    asyncio.run(main())
