#!/usr/bin/env python3
"""
Create Orders - Creates a buy and sell order at extreme prices.

This script demonstrates how to place resting limit orders that sit in the order book:
1. BUY order at $10 (far below market - will not fill immediately)
2. SELL order at $1,000,000 (far above market - will not fill immediately)

Both orders are for 0.001 ETH. The time-in-force is derived from
``ORDER_EXPIRY_SECONDS``: an expiry makes them GTT (rest, then auto-expire at
``expires_after``); ``None`` makes them GTC (rest until explicitly cancelled).

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

# NOTE: The default prices below are intentionally far from any realistic
# market price so this example NEVER fills against a live order book. If you
# adjust BUY/SELL to near-mark values, this script will actually trade.
SPOT_SYMBOL = "WETHRUSD"
TRADE_QTY = "0.0001"
BUY_PRICE = "1"  # $1 - safely below any realistic ETH bid, will rest
SELL_PRICE = "1000000"  # $1M - safely above any realistic ETH ask, will rest
# An expiry makes the orders GTT (auto-expire after this many seconds); it must
# be longer than the ~60s signature deadline. Set to None for a true GTC order
# that rests until you cancel it.
ORDER_EXPIRY_SECONDS = 120

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

    logger.info(f"Symbol: {SPOT_SYMBOL}")
    logger.info(f"Quantity: {TRADE_QTY} ETH")
    logger.info(f"Buy Price: ${BUY_PRICE}")
    logger.info(f"Sell Price: ${SELL_PRICE}")
    logger.info(f"Account ID: {account_id}")
    # An expiry => GTT (rest, then auto-expire); no expiry => GTC (rest forever).
    order_tif = TimeInForce.GTT if ORDER_EXPIRY_SECONDS else TimeInForce.GTC
    if ORDER_EXPIRY_SECONDS:
        logger.info(f"Time-in-force: GTT (auto-expires {ORDER_EXPIRY_SECONDS}s after placement)")
    else:
        logger.info("Time-in-force: GTC (rests until cancelled)")
    logger.info("=" * 60)

    def expires_now() -> int | None:
        # Re-snapshot wall clock per order so the deadline budget is consistent
        # — using a single snapshot before both orders means the second order
        # eats the network round-trip of the first against the same budget.
        return int(time.time()) + ORDER_EXPIRY_SECONDS if ORDER_EXPIRY_SECONDS else None

    # Create trading client
    client = ReyaTradingClient(config)

    try:
        # Initialize client
        await client.start()
        logger.info("✅ Client initialized")

        # Place BUY order
        logger.info("-" * 60)
        logger.info(f"📈 Placing {order_tif.value} BUY order: {TRADE_QTY} ETH @ ${BUY_PRICE}")

        buy_params = LimitOrderParameters(
            symbol=SPOT_SYMBOL,
            is_buy=True,
            qty=TRADE_QTY,
            limit_px=BUY_PRICE,
            time_in_force=order_tif,
            expires_after=expires_now(),
        )

        buy_response = await client.create_limit_order(buy_params)
        logger.info(f"✅ BUY order placed: Order ID = {buy_response.order_id}")

        # Place SELL order
        logger.info("-" * 60)
        logger.info(f"📉 Placing {order_tif.value} SELL order: {TRADE_QTY} ETH @ ${SELL_PRICE}")

        sell_params = LimitOrderParameters(
            symbol=SPOT_SYMBOL,
            is_buy=False,
            qty=TRADE_QTY,
            limit_px=SELL_PRICE,
            time_in_force=order_tif,
            expires_after=expires_now(),
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
