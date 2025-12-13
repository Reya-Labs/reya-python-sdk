#!/usr/bin/env python3
"""
Example script showing how to get assets information using the Reya Trading SDK.

Before running this example, ensure you have a .env file with the following variables:
- API_URL: (optional) The API URL to use
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
