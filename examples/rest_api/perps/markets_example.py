#!/usr/bin/env python3
"""
Example script showing how to get markets information using the Reya Trading SDK.

Before running this example, ensure you have a .env file with the following variables:
- API_URL: (optional) The API URL to use
"""

import asyncio

from dotenv import load_dotenv

from sdk.reya_rest_api import ReyaTradingClient


async def main():
    """Run the example to get markets information asynchronously."""
    # Load environment variables
    load_dotenv()

    # Create a client instance with configuration from environment variables
    async with ReyaTradingClient() as client:
        # Get all markets
        print("\n--- Getting all markets ---")

        # Get markets configuration
        print("\n--- Getting markets configuration ---")
        config = await client.reference.get_market_definitions()
        print(f"Markets configuration: {config}")

        symbol = "ETHRUSDPERP"

        if config:
            print(f"\n--- Getting trades for market {symbol} ---")
            trades = await client.wallet.get_wallet_perp_executions(address=client.owner_wallet_address or "")
            print(f"Market trades: {trades}")

            print(f"\n--- Getting trackers for market {symbol} ---")
            trackers = await client.markets.get_market_summary(symbol)
            print(f"Market trackers: {trackers}")


if __name__ == "__main__":
    asyncio.run(main())
