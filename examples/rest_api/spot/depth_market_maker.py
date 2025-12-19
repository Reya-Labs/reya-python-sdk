#!/usr/bin/env python3
"""
Spot Market Maker - Maintains realistic depth around current ETH price.

Continuously quotes bid/ask prices within ±2% of the reference price,
shifting quotes every 5 seconds to simulate realistic market activity.

Requirements:
- CHAIN_ID: The chain ID (1729 for mainnet, 89346162 for testnet)
- SPOT_ACCOUNT_ID_1: Your Reya SPOT account ID
- SPOT_PRIVATE_KEY_1: Your Ethereum private key
- SPOT_WALLET_ADDRESS_1: Your wallet address

Press Ctrl+C to stop (will cancel all orders on exit).
"""

import asyncio
import logging
import random
from decimal import Decimal, ROUND_DOWN

from dotenv import load_dotenv

from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api import ReyaTradingClient
from sdk.reya_rest_api.auth.signatures import SignatureGenerator
from sdk.reya_rest_api.config import TradingConfig
from sdk.reya_rest_api.models.orders import LimitOrderParameters

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("market_maker")

# Market configuration
SYMBOL = "WETHRUSD"
REFERENCE_PRICE = Decimal("2955.00")  # Current ETH price
MAX_DEVIATION_PCT = Decimal("0.02")   # ±2% from reference price
TICK_SIZE = Decimal("0.01")           # Price tick size for WETHRUSD
ORDER_QTY = "0.001"                   # Minimum order qty for WETHRUSD
NUM_LEVELS = 5                        # Number of price levels on each side
REFRESH_INTERVAL = 5                  # Seconds between quote updates


def round_to_tick(price: Decimal) -> Decimal:
    """Round price down to nearest tick size."""
    return (price / TICK_SIZE).quantize(Decimal("1"), rounding=ROUND_DOWN) * TICK_SIZE


def generate_quote_prices(reference: Decimal, max_deviation_pct: Decimal, num_levels: int) -> tuple[list[str], list[str]]:
    """
    Generate bid and ask prices around the reference price.
    
    Prices are randomly distributed within the ±max_deviation_pct range,
    ensuring bids are below reference and asks are above.
    """
    min_price = reference * (1 - max_deviation_pct)
    max_price = reference * (1 + max_deviation_pct)
    
    # Generate random bid prices (below reference, sorted descending)
    bid_range = reference - min_price
    bids = []
    for i in range(num_levels):
        offset = bid_range * Decimal(random.uniform(0.1, 1.0)) * Decimal(i + 1) / Decimal(num_levels)
        price = round_to_tick(reference - offset)
        if price >= min_price:
            bids.append(str(price))
    bids = sorted(set(bids), key=Decimal, reverse=True)[:num_levels]
    
    # Generate random ask prices (above reference, sorted ascending)
    ask_range = max_price - reference
    asks = []
    for i in range(num_levels):
        offset = ask_range * Decimal(random.uniform(0.1, 1.0)) * Decimal(i + 1) / Decimal(num_levels)
        price = round_to_tick(reference + offset)
        if price <= max_price:
            asks.append(str(price))
    asks = sorted(set(asks), key=Decimal)[:num_levels]
    
    return bids, asks


async def place_orders(client: ReyaTradingClient, symbol: str, bids: list[str], asks: list[str]) -> int:
    """Place bid and ask orders, return count of orders placed."""
    order_count = 0
    
    for price in bids:
        try:
            resp = await client.create_limit_order(
                LimitOrderParameters(
                    symbol=symbol,
                    is_buy=True,
                    limit_px=price,
                    qty=ORDER_QTY,
                    time_in_force=TimeInForce.GTC,
                )
            )
            logger.debug(f"BID @ ${price}: {resp.status}")
            order_count += 1
        except Exception as e:
            logger.warning(f"Failed to place bid @ ${price}: {e}")

    for price in asks:
        try:
            resp = await client.create_limit_order(
                LimitOrderParameters(
                    symbol=symbol,
                    is_buy=False,
                    limit_px=price,
                    qty=ORDER_QTY,
                    time_in_force=TimeInForce.GTC,
                )
            )
            logger.debug(f"ASK @ ${price}: {resp.status}")
            order_count += 1
        except Exception as e:
            logger.warning(f"Failed to place ask @ ${price}: {e}")
    
    return order_count


async def main():
    load_dotenv()

    min_price = REFERENCE_PRICE * (1 - MAX_DEVIATION_PCT)
    max_price = REFERENCE_PRICE * (1 + MAX_DEVIATION_PCT)

    logger.info("=" * 60)
    logger.info(f"🚀 SPOT Market Maker for {SYMBOL}")
    logger.info("=" * 60)
    logger.info(f"   Reference Price: ${REFERENCE_PRICE}")
    logger.info(f"   Price Range:     ${min_price:.2f} - ${max_price:.2f} (±{MAX_DEVIATION_PCT * 100}%)")
    logger.info(f"   Order Qty:       {ORDER_QTY}")
    logger.info(f"   Levels:          {NUM_LEVELS} bids / {NUM_LEVELS} asks")
    logger.info(f"   Refresh:         Every {REFRESH_INTERVAL}s")
    logger.info("   Press Ctrl+C to stop")
    logger.info("=" * 60)

    async with ReyaTradingClient() as client:
        spot_config = TradingConfig.from_env_spot(account_number=1)
        client._config = spot_config
        client._signature_generator = SignatureGenerator(spot_config)

        await client.start()
        account_id = spot_config.account_id

        if not account_id:
            raise ValueError("SPOT_ACCOUNT_ID_1 environment variable is required")

        logger.info(f"   Account ID:      {account_id}\n")

        # Clean up any existing orders from previous runs
        logger.info("Cleaning up existing orders...")
        await client.mass_cancel(symbol=SYMBOL, account_id=account_id)
        await asyncio.sleep(0.2)
        logger.info("✅ Order book cleaned\n")

        try:
            cycle = 0
            while True:
                cycle += 1
                
                # Generate new random prices within range
                bids, asks = generate_quote_prices(REFERENCE_PRICE, MAX_DEVIATION_PCT, NUM_LEVELS)

                # Cancel existing orders
                logger.info(f"[{cycle:04d}] Cleaning up orders...")
                await client.mass_cancel(symbol=SYMBOL, account_id=account_id)
                await asyncio.sleep(0.2)

                # Place new orders
                order_count = await place_orders(client, SYMBOL, bids, asks)

                # Log summary
                bid_str = ", ".join(f"${b}" for b in bids)
                ask_str = ", ".join(f"${a}" for a in asks)
                logger.info(f"[{cycle:04d}] Placed {order_count} orders | Bids: {bid_str} | Asks: {ask_str}")

                await asyncio.sleep(REFRESH_INTERVAL)

        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            logger.info("\n🛑 Shutting down...")
            logger.info("Cancelling all orders...")
            try:
                await client.mass_cancel(symbol=SYMBOL, account_id=account_id)
                logger.info("✅ Market maker stopped")
            except Exception as e:
                logger.warning(f"Cleanup failed: {e}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
