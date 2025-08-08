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
    client = ReyaTradingClient()

    # Get all assets
    print("\n--- Getting all assets ---")

    try:
        assets = await client.assets.get_assets()
        print(f"Assets data: {assets}")

        # If the response contains a list of assets, print the count and sample
        if isinstance(assets, list):
            print(f"Found {len(assets)} assets")
            # Print a few sample assets if available
            if assets:
                print(f"Sample assets (first 3): {assets[:3]}")

    except Exception as e:
        print(f"Error retrieving assets information: {e}")


if __name__ == "__main__":
    asyncio.run(main())
