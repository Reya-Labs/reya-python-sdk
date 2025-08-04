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
import os
import time
import logging
from decimal import Decimal
from dotenv import load_dotenv
from eth_keys.validation import validate_signature_r_or_s

from reya_trading import ReyaTradingClient
from reya_trading.constants.enums import LimitOrderType, Limit, TimeInForce, TriggerOrderType, Trigger, TpslType

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

# Create a logger for this module
logger = logging.getLogger("reya.order_entry")


def print_separator(title: str):
    """Print a section separator."""
    logger.info("=" * 60)
    logger.info(f" {title} ")
    logger.info("=" * 60)


def handle_order_response(order_type: str, response):
    """Handle and log order response."""
    if hasattr(response, 'raw_response'):
        raw = response.raw_response
        if raw.get('success', False):
            logger.info(f"✅ {order_type} order created successfully!")
            if 'orderId' in raw:
                logger.info(f"   Order ID: {raw['orderId']}")
            if 'transactionHash' in raw:
                logger.info(f"   Transaction Hash: {raw['transactionHash']}")
        else:
            logger.error(f"❌ {order_type} order failed:")
            logger.error(f"   Full error response: {raw}")
            if isinstance(raw, dict):
                logger.error(f"   Error message: {raw.get('error', 'Unknown error')}")
                if 'details' in raw:
                    logger.error(f"   Error details: {raw['details']}")
                if 'code' in raw:
                    logger.error(f"   Error code: {raw['code']}")
    else:
        logger.info(f"📝 {order_type} response: {response}")
    return response


def test_market_orders(client: ReyaTradingClient):
    """Test IOC (Immediate or Cancel) market orders."""
    print_separator("TESTING IOC MARKET ORDERS")

    # Test buy market order
    logger.info("Creating IOC market buy order...")
    response = client.create_market_order(
        market_id=1,
        size="0.1",  # Buy 0.1 units
        price="500",  # Max price willing to pay
        reduce_only=False
    )
    handle_order_response("IOC Market Buy", response)
    time.sleep(1)

    # Test sell market order
    logger.info("Creating IOC market sell order...")
    response = client.create_market_order(
        market_id=1,
        size="-0.1",  # Sell 0.1 units (negative size)
        price="40000",  # Min price willing to accept
        reduce_only=False
    )
    handle_order_response("IOC Market Sell", response)
    time.sleep(1)

    # Test reduce-only market order
    logger.info("Creating reduce-only IOC market order...")
    response = client.create_market_order(
        market_id=1,
        size="-0.05",  # Reduce position by 0.05 units
        price="45000",
        reduce_only=True
    )
    handle_order_response("IOC Reduce-Only Market", response)

def test_limit_orders(client: ReyaTradingClient):
    """Test GTC (Good Till Cancel) limit orders."""
    print_separator("TESTING GTC LIMIT ORDERS")

    # Test buy limit order
    logger.info("Creating GTC limit buy order...")
    limit_type = LimitOrderType(limit=Limit(time_in_force=TimeInForce.GTC))
    response = client.create_limit_order(
        market_id=1,
        is_buy=True,
        price="45000",  # Buy at or below $45,000
        size="0.1",
        type=limit_type
    )
    buy_order_response = handle_order_response("GTC Limit Buy", response)
    time.sleep(1)

    # Test sell limit order
    logger.info("Creating GTC limit sell order...")
    response = client.create_limit_order(
        market_id=1,
        is_buy=False,
        price="55000",  # Sell at or above $55,000
        size="0.1",
        type=limit_type
    )
    sell_order_response = handle_order_response("GTC Limit Sell", response)

    # Return order IDs for potential cancellation testing
    buy_order_id = None
    sell_order_id = None

    if hasattr(buy_order_response, 'raw_response') and 'orderId' in buy_order_response.raw_response:
        buy_order_id = buy_order_response.raw_response['orderId']

    if hasattr(sell_order_response, 'raw_response') and 'orderId' in sell_order_response.raw_response:
        sell_order_id = sell_order_response.raw_response['orderId']

    return buy_order_id, sell_order_id


def test_stop_loss_orders(client: ReyaTradingClient):
    """Test Stop Loss orders."""
    print_separator("TESTING STOP LOSS ORDERS")
    
    # Test stop loss for long position (sell when price drops)
    logger.info("Creating stop loss for long position...")
    response = client.create_stop_loss_order(
        market_id=1,
        trigger_price="47000",  # Trigger when price drops to $47,000
        price="46500",  # Execute at minimum $46,500
        is_buy=False  # Sell to close long position
    )
    long_sl_response = handle_order_response("Stop Loss (Long Position)", response)
    time.sleep(1)

    # Test stop loss for short position (buy when price rises)
    logger.info("Creating stop loss for short position...")
    response = client.create_stop_loss_order(
        market_id=1,
        trigger_price="53000",  # Trigger when price rises to $53,000
        price="53500",  # Execute at maximum $53,500
        is_buy=True  # Buy to close short position
    )
    short_sl_response = handle_order_response("Stop Loss (Short Position)", response)

    # Return order IDs
    long_sl_id = None
    short_sl_id = None

    if hasattr(long_sl_response, 'raw_response') and 'orderId' in long_sl_response.raw_response:
        long_sl_id = long_sl_response.raw_response['orderId']

    if hasattr(short_sl_response, 'raw_response') and 'orderId' in short_sl_response.raw_response:
        short_sl_id = short_sl_response.raw_response['orderId']

    return long_sl_id, short_sl_id


def test_take_profit_orders(client: ReyaTradingClient):
    """Test Take Profit orders."""
    print_separator("TESTING TAKE PROFIT ORDERS")

    # Test take profit for long position (sell when price rises)
    logger.info("Creating take profit for long position...")
    response = client.create_take_profit_order(
        market_id=1,
        trigger_price="55000",  # Trigger when price rises to $55,000
        price="54500",  # Execute at minimum $54,500
        is_buy=False  # Sell to close long position
    )
    long_tp_response = handle_order_response("Take Profit (Long Position)", response)
    time.sleep(1)

    # Test take profit for short position (buy when price drops)
    logger.info("Creating take profit for short position...")
    response = client.create_take_profit_order(
        market_id=1,
        trigger_price="45000",  # Trigger when price drops to $45,000
        price="45500",  # Execute at maximum $45,500
        is_buy=True  # Buy to close short position
    )
    short_tp_response = handle_order_response("Take Profit (Short Position)", response)

    # Return order IDs
    long_tp_id = None
    short_tp_id = None

    if hasattr(long_tp_response, 'raw_response') and 'orderId' in long_tp_response.raw_response:
        long_tp_id = long_tp_response.raw_response['orderId']

    if hasattr(short_tp_response, 'raw_response') and 'orderId' in short_tp_response.raw_response:
        short_tp_id = short_tp_response.raw_response['orderId']

    return long_tp_id, short_tp_id


def test_order_cancellation(client: ReyaTradingClient, order_ids: list):
    """Test order cancellation."""
    print_separator("TESTING ORDER CANCELLATION")

    valid_order_ids = ['6222318d-7b98-4550-b778-c1d68aa17ca0']

    #valid_order_ids = [oid for oid in order_ids if oid is not None]
    
    if not valid_order_ids:
        logger.warning("⚠️  No valid order IDs available for cancellation testing")
        return

    # Cancel the first available order
    order_id = valid_order_ids[0]
    logger.info(f"Attempting to cancel order: {order_id}")

    response = client.cancel_order(order_id=order_id)
    handle_order_response("Order Cancellation", response)


def test_order_retrieval(client: ReyaTradingClient):
    """Test retrieving orders and positions."""
    print_separator("TESTING ORDER AND POSITION RETRIEVAL")
    
    try:
        # Get filled orders
        logger.info("Retrieving filled orders...")
        orders = client.get_orders()
        logger.info(f"📊 Found {len(orders.get('data', []))} filled orders")
        
        # Get conditional orders
        logger.info("Retrieving conditional orders...")
        conditional_orders = client.get_conditional_orders()
        logger.info(f"📊 Found {len(conditional_orders)} conditional orders")
        
        # Get positions
        logger.info("Retrieving positions...")
        positions = client.get_positions()
        data = client.get_positions().get("data", positions) if isinstance(positions, dict) else (positions or [])
        logger.info(f"📊 Found {len(data)} positions")
        
        # Print summary of first few items (if any)
        if orders.get('data'):
            logger.info(f"📈 Latest filled order: {orders['data'][0].get('marketName', 'Unknown')} - {orders['data'][0].get('side', 'Unknown')}")
            
        if conditional_orders:
            logger.info(f"📋 Latest conditional order: {conditional_orders[0].get('orderType', 'Unknown')} - Status: {conditional_orders[0].get('status', 'Unknown')}")
            
        if isinstance(positions, dict) and positions.get('data'):
            logger.info(f"💼 Position: {positions['data'][0].get('marketName', 'Unknown')} - Size: {positions['data'][0].get('baseAmount', '0')}")
        elif isinstance(positions, list) and positions:
            logger.info(f"💼 Position: {positions[0].get('marketName', 'Unknown')} - Size: {positions[0].get('baseAmount', '0')}")
        else:
            logger.info("💼 No positions found")
            
    except Exception as e:
        logger.error(f"❌ Error retrieving orders/positions: {e}")


def main():
    """Run comprehensive order testing."""
    logger.info("🚀 Starting comprehensive Reya DEX order testing...")
    
    # Load environment variables
    load_dotenv()
    
    # Verify required environment variables
    required_vars = ['PRIVATE_KEY', 'ACCOUNT_ID']
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    
    if missing_vars:
        logger.error(f"❌ Missing required environment variables: {', '.join(missing_vars)}")
        logger.error("Please check your .env file and ensure all required variables are set.")
        return
    
    try:
        # Create a client instance
        client = ReyaTradingClient()
        logger.info(f"✅ Client initialized successfully")
        logger.info(f"   Account ID: {client.config.account_id}")
        logger.info(f"   Chain ID: {client.config.chain_id}")
        logger.info(f"   API URL: {client.config.api_url}")
        logger.info(f"   Wallet: {client.wallet_address}")
        
        # Collect order IDs for cancellation testing
        all_order_ids = []
        
        # Test 1: IOC Market Orders
        #test_market_orders(client)
        #time.sleep(2)
        
        # Test 2: GTC Limit Orders
        buy_limit_id, sell_limit_id = test_limit_orders(client)
        # all_order_ids.extend([buy_limit_id, sell_limit_id])
        # time.sleep(2)
        
        # Test 3: Stop Loss Orders
        # long_sl_id, short_sl_id = test_stop_loss_orders(client)
        # all_order_ids.extend([long_sl_id, short_sl_id])
        # time.sleep(2)
        
        # Test 4: Take Profit Orders
        # long_tp_id, short_tp_id = test_take_profit_orders(client)
        # all_order_ids.extend([long_tp_id, short_tp_id])
        # time.sleep(2)
        
        # Test 5: Order Retrieval
        # test_order_retrieval(client)
        # time.sleep(2)
        
        # Test 6: Order Cancellation (optional)
        # Uncomment the next line to test order cancellation
        # test_order_cancellation(client, all_order_ids)
        
        print_separator("TESTING COMPLETE")
        logger.info("🎉 All order type tests completed!")
        logger.info("💡 Review the logs above to see results for each order type.")
        logger.info("📝 Note: Some orders may fail due to market conditions, insufficient balance, or other constraints.")
        
    except Exception as e:
        logger.error(f"❌ Error during testing: {e}")
        raise


if __name__ == "__main__":
    main()
