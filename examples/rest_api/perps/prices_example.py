#!/usr/bin/env python3
"""
Example script showing how to get price information using the Reya Trading SDK.

Requirements:
- CHAIN_ID: The chain ID (1729 for mainnet, 89346162 for testnet)
- PERP_WALLET_ADDRESS_1: Your wallet address

Usage:
    python -m examples.rest_api.perps.prices_example
"""
import asyncio

from dotenv import load_dotenv

from sdk.reya_rest_api import ReyaTradingClient


async def main():
    """Run the example to get asset oracle price information asynchronously."""
    # Load environment variables
    load_dotenv()

    # Create a client instance with configuration from environment variables
    async with ReyaTradingClient() as client:
        print("\n--- Getting asset oracle prices ---")

        prices = await client.markets.get_asset_oracle_prices()
        print(f"Retrieved {len(prices)} asset oracle price entries")

        # Print some sample price entries
        prices_dict = {}
        if prices:
            print("\nSample price entries:")
            for price in prices:
                prices_dict[price.asset] = price
                print(f"{price.asset}: {price.oracle_price}")

        eth_price = prices_dict.get("ETH") or prices_dict.get("WETH")
        sample_price = eth_price or next(iter(prices_dict.values()), None)
        if sample_price is not None:
            print(f"\n--- {sample_price.asset} oracle price ---")
            print(f"Oracle price data for {sample_price.asset}: {sample_price}")
            oracle_price = float(sample_price.oracle_price)
            print(f"Oracle price in USD: ${oracle_price:.2f}")

        # Legacy /v2/prices remains available for existing consumers but is deprecated.
        legacy_prices = await client.markets.get_prices()
        print(f"\n--- Deprecated legacy prices feed returned {len(legacy_prices)} entries ---")


if __name__ == "__main__":
    asyncio.run(main())
