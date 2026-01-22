#!/usr/bin/env python3
"""
Example script showing how to use wallet-related endpoints using the Reya Trading SDK.

This script demonstrates:
- Getting open orders
- Getting positions
- Getting account balances
- Getting account configuration
- Getting trades
- Getting accounts
- Getting leverages
- Getting auto exchange settings
- Getting wallet stats

Requirements:
- CHAIN_ID: The chain ID (1729 for mainnet, 89346162 for testnet)
- PERP_ACCOUNT_ID_1: Your Reya account ID
- PERP_PRIVATE_KEY_1: Your Ethereum private key
- PERP_WALLET_ADDRESS_1: Your wallet address

Usage:
    python -m examples.rest_api.perps.wallet_example
"""
import asyncio

from dotenv import load_dotenv

from sdk.reya_rest_api import ReyaTradingClient


async def main():
    """Run the example to demonstrate wallet-related endpoints."""
    # Load environment variables
    load_dotenv()

    # Create a client instance with configuration from environment variables
    async with ReyaTradingClient() as client:
        # Check if we have a wallet address (either directly or derived from private key)
        if not client.owner_wallet_address:
            print("Error: No wallet address found in environment variables.")
            print("Please set either WALLET_ADDRESS or PRIVATE_KEY in your .env file.")
            return

        # Show the wallet address we're using
        print(f"Using wallet address: {client.owner_wallet_address}")

        # Get open orders for the wallet
        print("\n--- Getting open orders ---")
        open_orders = await client.get_open_orders()
        print(f"Open orders: {open_orders}")

        # Get positions for the wallet
        print("\n--- Getting positions ---")
        positions = await client.get_positions()
        print(f"Positions: {positions}")

        # Get account configuration
        print("\n--- Getting account configuration ---")
        config = await client.get_configuration()
        print(f"Account configuration: {config}")

        # Get trades
        print("\n--- Getting perpetual executions ---")
        trades = await client.get_perp_executions()
        print(f"Trades: {trades}")

        # Get accounts
        print("\n--- Getting accounts ---")
        accounts = await client.get_accounts()
        print(f"Accounts: {accounts}")

        # Get account balances
        print("\n--- Getting account balances ---")
        balances = await client.get_account_balances()
        print(f"Account balances: {balances}")


if __name__ == "__main__":
    asyncio.run(main())
