#!/usr/bin/env python3
"""
Example script showing how to get assets information using the Reya Trading SDK.

Requirements:
- CHAIN_ID: The chain ID (1729 for mainnet, 89346162 for testnet)
- PERP_WALLET_ADDRESS_1: Your wallet address

Usage:
    python -m examples.rest_api.perps.assets_example
"""
import asyncio

from dotenv import load_dotenv

from sdk.reya_rest_api import ReyaTradingClient


async def main():
    """Run the example to get assets information asynchronously."""
    # Load environment variables
    load_dotenv()

    # Create a client instance with configuration from environment variables
    async with ReyaTradingClient() as client:
        # Get all assets
        print("\n--- Getting all assets ---")

        assets = await client.reference.get_asset_definitions()
        print(f"Assets data: {assets}")

        if assets:
            print(f"Found {len(assets)} assets")


if __name__ == "__main__":
    asyncio.run(main())
