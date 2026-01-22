#!/usr/bin/env python3
"""
Spot Trade - Simple buy and sell example using the SPOT order book.

This script demonstrates how to:
1. Check account balance (RUSD)
2. Fetch order book depth to determine execution prices
3. Buy 0.001 ETH using an IOC order
4. Sell the 0.001 ETH using an IOC order

Requirements:
- CHAIN_ID: The chain ID (1729 for mainnet, 89346162 for testnet)
- SPOT_ACCOUNT_ID_1: Your Reya account ID
- SPOT_PRIVATE_KEY_1: Your Ethereum private key

Usage:
    python -m examples.rest_api.spot.spot_trade
"""

import asyncio
import logging
import sys
from decimal import Decimal

from dotenv import load_dotenv

from sdk.open_api.models import TimeInForce
from sdk.reya_rest_api import ReyaTradingClient, get_spot_config
from sdk.reya_rest_api.models.orders import LimitOrderParameters

# =============================================================================
# CONFIGURATION
# =============================================================================

SPOT_SYMBOL = "WETHRUSD"
TRADE_QTY = "0.001"

# =============================================================================
# LOGGING SETUP
# =============================================================================

logging.basicConfig(level=logging.WARNING, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("spot_trade")
logger.setLevel(logging.INFO)
logger.handlers = []
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
logger.addHandler(handler)
logger.propagate = False


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


async def get_rusd_balance(client: ReyaTradingClient, account_id: int) -> Decimal:
    """Get the RUSD balance for a specific account."""
    balances = await client.get_account_balances()
    for balance in balances:
        if balance.account_id == account_id and balance.asset == "RUSD":
            return Decimal(balance.real_balance)
    return Decimal("0")


async def get_best_ask_price(client: ReyaTradingClient, symbol: str) -> Decimal | None:
    """Get the best ask price from the order book (for buying)."""
    depth = await client.markets.get_market_depth(symbol=symbol)
    if depth.asks and len(depth.asks) > 0:
        return Decimal(depth.asks[0].px)
    return None


async def get_best_bid_price(client: ReyaTradingClient, symbol: str) -> Decimal | None:
    """Get the best bid price from the order book (for selling)."""
    depth = await client.markets.get_market_depth(symbol=symbol)
    if depth.bids and len(depth.bids) > 0:
        return Decimal(depth.bids[0].px)
    return None


# =============================================================================
# MAIN TRADING LOGIC
# =============================================================================


async def main() -> None:
    """Main entry point for the spot trade example."""
    load_dotenv()

    # Get config from environment (uses SPOT_ACCOUNT_ID_1, SPOT_PRIVATE_KEY_1, SPOT_WALLET_ADDRESS_1)
    try:
        config = get_spot_config(account_number=1)
    except ValueError as e:
        logger.error(f"❌ Configuration error: {e}")
        sys.exit(1)

    if config.account_id is None:
        logger.error("❌ SPOT_ACCOUNT_ID_1 environment variable is required")
        sys.exit(1)

    if config.private_key is None:
        logger.error("❌ SPOT_PRIVATE_KEY_1 environment variable is required")
        sys.exit(1)

    account_id = config.account_id

    logger.info("=" * 60)
    logger.info("SPOT TRADE EXAMPLE")
    logger.info("=" * 60)
    logger.info(f"Symbol: {SPOT_SYMBOL}")
    logger.info(f"Quantity: {TRADE_QTY} ETH")
    logger.info(f"Account ID: {account_id}")
    logger.info("=" * 60)

    # Create trading client
    client = ReyaTradingClient(config)

    try:
        # Initialize client (loads market definitions)
        await client.start()
        logger.info("✅ Client initialized")

        # Step 1: Check RUSD balance
        rusd_balance = await get_rusd_balance(client, account_id)
        logger.info(f"📊 RUSD Balance: {rusd_balance}")

        # Step 2: Fetch order book depth
        logger.info(f"📖 Fetching order book for {SPOT_SYMBOL}...")
        best_ask = await get_best_ask_price(client, SPOT_SYMBOL)
        best_bid = await get_best_bid_price(client, SPOT_SYMBOL)

        if best_ask is None:
            logger.error("❌ No asks in order book - cannot buy")
            sys.exit(1)

        if best_bid is None:
            logger.error("❌ No bids in order book - cannot sell")
            sys.exit(1)

        logger.info(f"  Best Ask (buy price): ${best_ask}")
        logger.info(f"  Best Bid (sell price): ${best_bid}")

        # Step 3: Check if we have enough RUSD to buy
        trade_qty = Decimal(TRADE_QTY)
        required_rusd = trade_qty * best_ask
        logger.info(f"💰 Required RUSD for buy: {required_rusd}")

        if rusd_balance < required_rusd:
            logger.error(f"❌ Insufficient RUSD balance. Have: {rusd_balance}, Need: {required_rusd}")
            sys.exit(1)

        logger.info("✅ Sufficient balance for trade")

        # Step 4: Place IOC BUY order
        logger.info("-" * 60)
        logger.info(f"📈 Placing IOC BUY order: {TRADE_QTY} ETH @ ${best_ask}")

        buy_params = LimitOrderParameters(
            symbol=SPOT_SYMBOL,
            is_buy=True,
            qty=TRADE_QTY,
            limit_px=str(best_ask),
            time_in_force=TimeInForce.IOC,
        )

        buy_response = await client.create_limit_order(buy_params)
        logger.info(f"✅ BUY order submitted: Order ID = {buy_response.order_id}")

        # Small delay to allow order to settle
        await asyncio.sleep(1)

        # Step 5: Place IOC SELL order
        # Refresh best bid price as it may have changed
        best_bid = await get_best_bid_price(client, SPOT_SYMBOL)
        if best_bid is None:
            logger.error("❌ No bids in order book - cannot sell")
            sys.exit(1)

        logger.info("-" * 60)
        logger.info(f"📉 Placing IOC SELL order: {TRADE_QTY} ETH @ ${best_bid}")

        sell_params = LimitOrderParameters(
            symbol=SPOT_SYMBOL,
            is_buy=False,
            qty=TRADE_QTY,
            limit_px=str(best_bid),
            time_in_force=TimeInForce.IOC,
        )

        sell_response = await client.create_limit_order(sell_params)
        logger.info(f"✅ SELL order submitted: Order ID = {sell_response.order_id}")

        # Step 6: Summary
        logger.info("=" * 60)
        logger.info("✅ TRADE COMPLETE")
        logger.info(f"  Buy Order ID: {buy_response.order_id}")
        logger.info(f"  Sell Order ID: {sell_response.order_id}")
        logger.info("=" * 60)

    finally:
        await client.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nProgram interrupted by user. Exiting...")
