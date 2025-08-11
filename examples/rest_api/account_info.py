#!/usr/bin/env python3
"""
Example script showing how to get open orders for a wallet address using the Reya Trading SDK.

Before running this example, ensure you have a .env file with the following variables:
- PRIVATE_KEY: Your Ethereum private key
- ACCOUNT_ID: Your Reya account ID
- CHAIN_ID: The chain ID (1729 for mainnet, 89346162 for testnet)
"""
import asyncio

from dotenv import load_dotenv

from sdk.reya_rest_api import ReyaTradingClient


async def main():
    """Run the example to get open orders asynchronously."""
    # Load environment variables
    load_dotenv()

    # Create a client instance with configuration from environment variables
    client = ReyaTradingClient()

    # Check if we have a wallet address (either directly or derived from private key)
    if not client.wallet_address:
        print("Error: No wallet address found in environment variables.")
        print("Please set either WALLET_ADDRESS or PRIVATE_KEY in your .env file.")
        return

    # Show the wallet address we're using
    print(f"Using wallet address: {client.wallet_address}")

    # Get open orders for the wallet
    print("\n--- Getting open orders ---")

    # Get all open orders for the wallet
    accounts = await client.get_accounts()

    if accounts:
        print(f"Found {len(accounts)} accounts:\n")

        for i, account in enumerate(accounts):
            print(f"Account {i + 1}:")
            print(f" {account}")
            print("  ---------------")
    else:
        print("No open orders found for this wallet address.")


if __name__ == "__main__":
    asyncio.run(main())
