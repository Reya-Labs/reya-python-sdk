#!/usr/bin/env python3
"""Example script showing how to get price information using the Reya Trading SDK.

Before running this example, ensure you have a .env file with the following variables:
- API_URL: (optional) The API URL to use
"""
import asyncio

from dotenv import load_dotenv

from sdk.reya_rest_api import ReyaTradingClient


async def main():
    """Run the example to get price information asynchronously."""
    # Load environment variables
    load_dotenv()

    # Create a client instance with configuration from environment variables
    async with ReyaTradingClient() as client:
        # Get all prices
        print("\n--- Getting all prices ---")

        prices = await client.markets.get_prices()
        print(f"Retrieved {len(prices)} price entries")

        # Print some sample price entries
        prices_dict = {}
        if prices:
            print("\nSample price entries:")
            for price in prices:
                prices_dict[price.symbol] = price
                print(f"{price.symbol}: {price.oracle_price}")

        # Extract a specific price from the prices response instead of making a separate API call
        # Choose an existing key from the response
        if "ETHRUSDPERP" in prices_dict:
            eth_price = prices_dict["ETHRUSDPERP"]
            print("\n--- ETH/USD Mark Price ---")
            print(f"Price data for ETHRUSDPERP: {eth_price}")
            if eth_price.oracle_price:
                # Convert from string to float and adjust decimal places if needed
                oracle_price_wei = eth_price.oracle_price
                oracle_price = float(oracle_price_wei) / 10**18  # Assuming 18 decimals
                print(f"Oracle price in USD: ${oracle_price:.2f}")


if __name__ == "__main__":
    asyncio.run(main())
