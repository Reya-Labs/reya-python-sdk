#!/usr/bin/env python3
"""
Example of creating different types of orders with the Reya Trading API.

This example demonstrates how to cancel an order.

Before running this example, ensure you have a .env file with the following variables:
- PRIVATE_KEY: Your Ethereum private key
- ACCOUNT_ID: Your Reya account ID
- CHAIN_ID: The chain ID (1729 for mainnet, 89346162 for testnet)
"""
import os
import time
import logging
from decimal import Decimal
from dotenv import load_dotenv
from reya_trading import ReyaTradingClient
from reya_trading.constants.enums import LimitOrderType, Limit, TimeInForce

# Set up logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

# Create a logger for this module
logger = logging.getLogger("reya.example")

def main():
    """Run the example."""
    # Load environment variables
    load_dotenv()
    
    # Create a client instance with configuration from environment variables
    client = ReyaTradingClient()
    
    # Example 1: Cancel a limit order with known id
    # order_id = "62e52226-fae5-4b97-b68a-3b7fd13b5e66"

    # logger.info(f"Cancel order with id: {order_id}")
    # response = client.cancel_order(order_id=order_id)

    # logger.info(f"Response: {response}")

    logger.info("Adding limit order")
    client.create_limit_order(
        market_id=1,
        is_buy=True,
        price=10,
        size=1,
        type={
            "limit": {
                "timeInForce": "GTC"
            }
        }
    )

    logger.info("Limit order added")


if __name__ == "__main__":
    main()
