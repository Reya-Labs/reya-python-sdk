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
    load_dotenv()

    client = ReyaTradingClient()

    if not client.wallet_address:
        print("Error: No wallet address found in environment variables.")
        print("Please set either WALLET_ADDRESS or PRIVATE_KEY in your .env file.")
        return

    print(f"Using wallet address: {client.wallet_address}")

    print("\n--- Getting accounts orders ---")

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
