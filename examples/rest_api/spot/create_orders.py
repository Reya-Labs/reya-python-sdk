#!/usr/bin/env python3
"""
Create Orders - Creates a buy and sell order at extreme prices.

This script demonstrates how to place GTC limit orders that will sit in the order book:
1. BUY order at $10 (far below market - will not fill immediately)
2. SELL order at $1,000,000 (far above market - will not fill immediately)

Both orders are for 0.001 ETH.

Requirements:
- CHAIN_ID: The chain ID (1729 for mainnet, 89346162 for testnet)
- SPOT_ACCOUNT_ID_1 (or _2): Your Reya account ID
- SPOT_PRIVATE_KEY_1 (or _2): Your Ethereum private key

Usage:
    python -m examples.rest_api.spot.create_orders        # uses account 1
    python -m examples.rest_api.spot.create_orders 2      # uses account 2
"""

import asyncio
import logging
import sys
import time

from dotenv import load_dotenv

from sdk.open_api.models import TimeInForce
from sdk.reya_rest_api import ReyaTradingClient, get_spot_config
from sdk.reya_rest_api.models.orders import LimitOrderParameters

# =============================================================================
# CONFIGURATION
# =============================================================================

SPOT_SYMBOL = "WETHRUSD"
TRADE_QTY = "0.001"
BUY_PRICE = "1900"
SELL_PRICE = "2200"
ORDER_EXPIRY_SECONDS = 10  # Orders expire after 2 minutes (set to None for default 24h)

# =============================================================================
# LOGGING SETUP
# =============================================================================

logging.basicConfig(level=logging.WARNING, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("create_orders")
logger.setLevel(logging.INFO)
logger.handlers = []
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
logger.addHandler(handler)
logger.propagate = False


# =============================================================================
# MAIN LOGIC
# =============================================================================


async def main() -> None:
    """Main entry point for the create orders example."""
    load_dotenv()

    # Pick account from CLI argument (default: 1)
    account_number = int(sys.argv[1]) if len(sys.argv) > 1 else 1

    # Get config from environment
    try:
        config = get_spot_config(account_number=account_number)
    except ValueError as e:
        logger.error(f"❌ Configuration error: {e}")
        sys.exit(1)

    if config.account_id is None:
        logger.error(f"❌ SPOT_ACCOUNT_ID_{account_number} environment variable is required")
        sys.exit(1)

    if config.private_key is None:
        logger.error(f"❌ SPOT_PRIVATE_KEY_{account_number} environment variable is required")
        sys.exit(1)

    account_id = config.account_id

    logger.info("=" * 60)
    logger.info("CREATE ORDERS EXAMPLE")
    logger.info("=" * 60)
    expires_after = int(time.time()) + ORDER_EXPIRY_SECONDS if ORDER_EXPIRY_SECONDS else None

    logger.info(f"Symbol: {SPOT_SYMBOL}")
    logger.info(f"Quantity: {TRADE_QTY} ETH")
    logger.info(f"Buy Price: ${BUY_PRICE}")
    logger.info(f"Sell Price: ${SELL_PRICE}")
    logger.info(f"Account ID: {account_id}")
    if ORDER_EXPIRY_SECONDS:
        logger.info(f"Order Expiry: {ORDER_EXPIRY_SECONDS}s (deadline: {expires_after})")
    else:
        logger.info("Order Expiry: default (24h)")
    logger.info("=" * 60)

    # Create trading client
    client = ReyaTradingClient(config)

    try:
        # Initialize client
        await client.start()
        logger.info("✅ Client initialized")

        # Place GTC BUY order at $10
        logger.info("-" * 60)
        logger.info(f"📈 Placing GTC BUY order: {TRADE_QTY} ETH @ ${BUY_PRICE}")

        buy_params = LimitOrderParameters(
            symbol=SPOT_SYMBOL,
            is_buy=True,
            qty=TRADE_QTY,
            limit_px=BUY_PRICE,
            time_in_force=TimeInForce.GTC,
            expires_after=expires_after,
        )

        buy_response = await client.create_limit_order(buy_params)
        logger.info(f"✅ BUY order placed: Order ID = {buy_response.order_id}")

        # Place GTC SELL order at $1,000,000
        logger.info("-" * 60)
        logger.info(f"📉 Placing GTC SELL order: {TRADE_QTY} ETH @ ${SELL_PRICE}")

        sell_params = LimitOrderParameters(
            symbol=SPOT_SYMBOL,
            is_buy=False,
            qty=TRADE_QTY,
            limit_px=SELL_PRICE,
            time_in_force=TimeInForce.GTC,
            expires_after=expires_after,
        )

        sell_response = await client.create_limit_order(sell_params)
        logger.info(f"✅ SELL order placed: Order ID = {sell_response.order_id}")

        # Summary
        logger.info("=" * 60)
        logger.info("✅ ORDERS PLACED SUCCESSFULLY")
        logger.info(f"  Buy Order ID: {buy_response.order_id} @ ${BUY_PRICE}")
        logger.info(f"  Sell Order ID: {sell_response.order_id} @ ${SELL_PRICE}")
        logger.info("=" * 60)
        logger.info("ℹ️  These orders will sit in the order book until cancelled or filled.")

    finally:
        await client.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nProgram interrupted by user. Exiting...")
