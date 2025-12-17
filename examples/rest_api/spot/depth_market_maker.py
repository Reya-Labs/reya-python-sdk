#!/usr/bin/env python3
"""
Persistent Market Maker - Maintains depth in the order book.

Runs continuously, replenishing depth every 60 seconds.
Press Ctrl+C to stop (will cancel all orders on exit).

Requirements:
- CHAIN_ID: The chain ID (1729 for mainnet, 89346162 for testnet)
- SPOT_ACCOUNT_ID_1: Your Reya SPOT account ID
- SPOT_PRIVATE_KEY_1: Your Ethereum private key
- SPOT_WALLET_ADDRESS_1: Your wallet address
"""

import asyncio
import logging

from dotenv import load_dotenv

from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api import ReyaTradingClient
from sdk.reya_rest_api.auth.signatures import SignatureGenerator
from sdk.reya_rest_api.config import TradingConfig
from sdk.reya_rest_api.models.orders import LimitOrderParameters

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("market_maker")

# Configuration
SYMBOL = "WETHRUSD"
BID_PRICES = ["498", "497", "496", "495", "494"]
ASK_PRICES = ["502", "503", "504", "505", "506"]
ORDER_QTY = "0.01"  # Minimum order size for WETHRUSD
REFRESH_INTERVAL = 60  # seconds


async def place_orders(client: ReyaTradingClient, symbol: str):
    """Place all bid and ask orders."""
    # Place bids
    for price in BID_PRICES:
        resp = await client.create_limit_order(
            LimitOrderParameters(
                symbol=symbol, is_buy=True, limit_px=price, qty=ORDER_QTY, time_in_force=TimeInForce.GTC
            )
        )
        logger.info(f"BID @ {price}: {resp.status}")

    # Place asks
    for price in ASK_PRICES:
        resp = await client.create_limit_order(
            LimitOrderParameters(
                symbol=symbol, is_buy=False, limit_px=price, qty=ORDER_QTY, time_in_force=TimeInForce.GTC
            )
        )
        logger.info(f"ASK @ {price}: {resp.status}")


async def main():
    load_dotenv()

    logger.info(f"🚀 Starting Market Maker for {SYMBOL}")
    logger.info(f"   Bids: {BID_PRICES}")
    logger.info(f"   Asks: {ASK_PRICES}")
    logger.info(f"   Qty per order: {ORDER_QTY}")
    logger.info(f"   Refresh interval: {REFRESH_INTERVAL}s")
    logger.info("   Press Ctrl+C to stop\n")

    async with ReyaTradingClient() as client:
        # Configure client for SPOT account
        spot_config = TradingConfig.from_env_spot(account_number=1)
        client._config = spot_config
        client._signature_generator = SignatureGenerator(spot_config)
        
        await client.start()
        account_id = spot_config.account_id
        
        if not account_id:
            raise ValueError("SPOT_ACCOUNT_ID_1 environment variable is required")
        
        logger.info(f"   Using SPOT Account: {account_id}")

        try:
            cycle = 0
            while True:
                cycle += 1
                logger.info(f"=== Cycle {cycle} ===")

                # Cancel all existing orders
                logger.info("Cancelling existing orders...")
                await client.mass_cancel(symbol=SYMBOL, account_id=account_id)
                await asyncio.sleep(0.5)

                # Place fresh orders
                logger.info("Placing orders...")
                await place_orders(client, SYMBOL)

                # Show current depth
                depth = await client.get_market_depth(SYMBOL)
                logger.info(f"Depth: {len(depth.bids)} bids, {len(depth.asks)} asks")

                # Wait for next cycle
                logger.info(f"Waiting {REFRESH_INTERVAL}s...\n")
                await asyncio.sleep(REFRESH_INTERVAL)

        except KeyboardInterrupt:
            logger.info("\n🛑 Stopping...")
        finally:
            # Cleanup on exit
            logger.info("Cleaning up orders...")
            await client.mass_cancel(symbol=SYMBOL, account_id=account_id)
            logger.info("Done!")


if __name__ == "__main__":
    asyncio.run(main())
