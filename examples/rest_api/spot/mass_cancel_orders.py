#!/usr/bin/env python3
"""
Mass Cancel Orders - Cancel all open orders for configured SPOT accounts.

This script demonstrates how to mass cancel all open orders across multiple
spot markets (WETHRUSD, WBTCRUSD) for one or more accounts.

Requirements:
- CHAIN_ID: The chain ID (1729 for mainnet, 89346162 for testnet)
- SPOT_ACCOUNT_ID_1: First Reya account ID (Maker)
- SPOT_PRIVATE_KEY_1: Private key for first account
- SPOT_ACCOUNT_ID_2: Second Reya account ID (Taker) - optional
- SPOT_PRIVATE_KEY_2: Private key for second account - optional

Usage:
    python -m examples.rest_api.spot.mass_cancel_orders
"""

import asyncio
import logging
import sys

from dotenv import load_dotenv

from sdk.reya_rest_api import ReyaTradingClient, get_spot_config

SYMBOLS = ["WETHRUSD", "WBTCRUSD"]

logging.basicConfig(level=logging.WARNING, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("mass_cancel_orders")
logger.setLevel(logging.INFO)
logger.handlers = []
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
logger.addHandler(handler)
logger.propagate = False


async def mass_cancel_account(account_number: int, name: str) -> bool:
    """Mass cancel all orders for an account.

    Args:
        account_number: The account number (1 or 2) for config lookup
        name: Display name for logging (e.g., "Maker", "Taker")

    Returns:
        True if account was processed, False if config was missing
    """
    try:
        config = get_spot_config(account_number=account_number)
    except ValueError:
        logger.debug(f"Account {account_number} not configured, skipping")
        return False

    if config.account_id is None:
        logger.warning(f"SPOT_ACCOUNT_ID_{account_number} not set, skipping")
        return False

    if config.private_key is None:
        logger.warning(f"SPOT_PRIVATE_KEY_{account_number} not set, skipping")
        return False

    client = ReyaTradingClient(config)
    await client.start()

    try:
        for symbol in SYMBOLS:
            try:
                result = await client.mass_cancel(symbol=symbol, account_id=config.account_id)
                logger.info(f"✅ {name} ({config.account_id}): Cancelled {result.cancelled_count} orders for {symbol}")
            except Exception as e:  # pylint: disable=broad-exception-caught
                if "No orders" in str(e) or "no active" in str(e).lower():
                    logger.info(f"ℹ️  {name} ({config.account_id}): No orders to cancel for {symbol}")
                else:
                    logger.error(f"❌ {name} ({config.account_id}): Error cancelling {symbol}: {e}")
    finally:
        await client.close()

    return True


async def main() -> None:
    """Main entry point for the mass cancel orders example."""
    load_dotenv()

    logger.info("=" * 60)
    logger.info("MASS CANCEL ALL SPOT ORDERS")
    logger.info("=" * 60)

    accounts_processed = 0

    # Process Account 1 (Maker)
    if await mass_cancel_account(1, "Maker"):
        accounts_processed += 1

    # Process Account 2 (Taker)
    if await mass_cancel_account(2, "Taker"):
        accounts_processed += 1

    if accounts_processed == 0:
        logger.error("❌ No accounts configured. Set SPOT_ACCOUNT_ID_1 and SPOT_PRIVATE_KEY_1.")
        sys.exit(1)

    logger.info("=" * 60)
    logger.info(f"✅ Done! Processed {accounts_processed} account(s).")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nProgram interrupted by user. Exiting...")
