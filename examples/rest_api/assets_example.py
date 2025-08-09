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

        # If the response contains a list of assets in data field, print the count and sample
        assets_list = assets.get("data", []) if isinstance(assets.get("data"), list) else []
        if assets_list:
            print(f"Found {len(assets_list)} assets")
            # Print a few sample assets if available
            print(f"Sample assets (first 3): {assets_list[:3]}")

    except Exception as e:
        print(f"Error retrieving assets information: {e}")


if __name__ == "__main__":
    asyncio.run(main())
