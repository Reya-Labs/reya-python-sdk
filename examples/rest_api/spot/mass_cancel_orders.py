#!/usr/bin/env python3
"""Quick script to mass cancel all orders on spot accounts."""

import asyncio
import sys

from dotenv import load_dotenv

load_dotenv()

from sdk.reya_rest_api import ReyaTradingClient, get_spot_config

SYMBOLS = ["WETHRUSD", "WBTCRUSD"]


async def mass_cancel_account(account_number: int, name: str):
    """Mass cancel all orders for an account."""
    config = get_spot_config(account_number=account_number)

    if config.account_id is None:
        print(f"❌ SPOT_ACCOUNT_ID_{account_number} not set")
        sys.exit(1)

    if config.private_key is None:
        print(f"❌ SPOT_PRIVATE_KEY_{account_number} not set")
        sys.exit(1)

    client = ReyaTradingClient(config)
    await client.start()

    try:
        for symbol in SYMBOLS:
            try:
                result = await client.mass_cancel(symbol=symbol, account_id=config.account_id)
                print(f"✅ {name} ({config.account_id}): Mass cancelled {result.cancelled_count} orders for {symbol}")
            except Exception as e:
                if "No orders" in str(e) or "no active" in str(e).lower():
                    print(f"ℹ️  {name} ({config.account_id}): No orders to cancel for {symbol}")
                else:
                    print(f"❌ {name} ({config.account_id}): Error cancelling {symbol}: {e}")
    finally:
        await client.close()


async def main():
    print("=" * 60)
    print("MASS CANCEL ALL SPOT ORDERS")
    print("=" * 60)

    await mass_cancel_account(1, "Maker")
    await mass_cancel_account(2, "Taker")

    print("=" * 60)
    print("Done!")


if __name__ == "__main__":
    asyncio.run(main())
