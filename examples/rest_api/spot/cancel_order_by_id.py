#!/usr/bin/env python3
"""
Cancel Order by ID - Cancel a specific order on the SPOT order book.

This script demonstrates how to cancel a specific order by its order ID.
Simply update the configuration variables below with your order details.

Requirements:
- CHAIN_ID: The chain ID (1729 for mainnet, 89346162 for testnet)
- PRIVATE_KEY: Your Ethereum private key

Usage:
    1. Update ORDER_ID, SYMBOL, and ACCOUNT_ID below
    2. Run: python -m examples.rest_api.spot.cancel_order_by_id
"""

import asyncio
import os

from dotenv import load_dotenv
from eth_account import Account

from sdk.reya_rest_api import ReyaTradingClient
from sdk.reya_rest_api.config import MAINNET_CHAIN_ID, TradingConfig

ORDER_ID = "1856060584567504896"  # The order ID to cancel
SYMBOL = "WETHRUSD"  # The trading symbol (WETHRUSD, WBTCRUSD)
ACCOUNT_ID = 10000000002  # Your Reya account ID


async def main() -> None:
    """Cancel an order by ID."""
    load_dotenv()

    # Get credentials from environment
    private_key = os.getenv("PRIVATE_KEY", "")
    chain_id = int(os.getenv("CHAIN_ID", str(MAINNET_CHAIN_ID)))

    if not private_key:
        print("❌ PRIVATE_KEY environment variable is required.")
        return

    if not ORDER_ID:
        print("❌ ORDER_ID is required. Update the ORDER_ID variable in the script.")
        return

    # Derive wallet address from private key
    wallet = Account.from_key(private_key)
    wallet_address = wallet.address

    # Determine API URL based on chain
    api_url = "https://api.reya.xyz/v2" if chain_id == MAINNET_CHAIN_ID else "https://api-cronos.reya.xyz/v2"

    print("=" * 60)
    print("CANCEL ORDER BY ID")
    print("=" * 60)
    print(f"Order ID:   {ORDER_ID}")
    print(f"Symbol:     {SYMBOL}")
    print(f"Account ID: {ACCOUNT_ID}")
    print("=" * 60)

    # Create config and client
    config = TradingConfig(
        api_url=api_url,
        chain_id=chain_id,
        owner_wallet_address=wallet_address,
        private_key=private_key,
        account_id=ACCOUNT_ID,
    )

    client = ReyaTradingClient(config)
    await client.start()

    try:
        print(f"\n📤 Sending cancel request for order {ORDER_ID}...")
        result = await client.cancel_order(order_id=ORDER_ID, symbol=SYMBOL, account_id=ACCOUNT_ID)
        print(f"✅ Cancel response: {result}")
    except Exception as e:  # pylint: disable=broad-exception-caught
        print(f"❌ Error cancelling order: {e}")
    finally:
        await client.close()

    print("=" * 60)
    print("Done!")


if __name__ == "__main__":
    asyncio.run(main())
