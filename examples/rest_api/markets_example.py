#!/usr/bin/env python3
"""
Example script showing how to get markets information using the Reya Trading SDK.

Before running this example, ensure you have a .env file with the following variables:
- API_URL: (optional) The API URL to use
"""
from typing import Any

import asyncio

from dotenv import load_dotenv

from sdk.reya_rest_api import ReyaTradingClient


async def main():
    """Run the example to get markets information asynchronously."""
    # Load environment variables
    load_dotenv()

    # Create a client instance with configuration from environment variables
    client = ReyaTradingClient()

    # Get all markets
    print("\n--- Getting all markets ---")

    markets: list[dict[str, Any]] = await client.markets.get_markets()
    print(f"Markets response: {markets}")

    # Get markets configuration
    print("\n--- Getting markets configuration ---")
    config = await client.markets.get_markets_configuration()
    print(f"Markets configuration: {config}")

    # Get markets data
    print("\n--- Getting markets data ---")
    data = await client.markets.get_markets_data()
    print(f"Markets data: {data}")

    market_id = markets[0].get("id")
    if market_id:
        print(f"\n--- Getting information for market {market_id} ---")
        market = await client.markets.get_market(market_id)
        print(f"Market information: {market}")

        print(f"\n--- Getting trades for market {market_id} ---")
        trades = await client.markets.get_market_trades(market_id)
        print(f"Market trades: {trades}")

        print(f"\n--- Getting data for market {market_id} ---")
        market_data = await client.markets.get_market_data(market_id)
        print(f"Market data: {market_data}")

        print(f"\n--- Getting trackers for market {market_id} ---")
        trackers = await client.markets.get_market_trackers(market_id)
        print(f"Market trackers: {trackers}")


if __name__ == "__main__":
    asyncio.run(main())
