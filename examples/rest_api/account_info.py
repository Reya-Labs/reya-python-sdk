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
    
    try:
        # Get all open orders for the wallet
        open_orders = await client.get_open_orders()
        
        if open_orders:
            print(f"Found {len(open_orders)} open orders:\n")
            
            for i, order in enumerate(open_orders):
                # Extract order details
                account_id = order.get("account_id", "unknown")
                order_id = order.get("id", "unknown")
                market_id = order.get("market_id", "unknown")
                order_type = order.get("order_type", "unknown")
                is_long = order.get("is_long", True)
                trigger_price = order.get("trigger_price", 0)
                order_base = order.get("order_base", "0")
                status = order.get("status", "unknown")
                created_at = order.get("creation_timestamp_ms", "unknown")
                
                # Determine side based on is_long flag
                side = "BUY" if is_long else "SELL"
                
                # Print order details
                print(f"Order {i+1}:")
                print(f"  Account ID: {account_id}")
                print(f"  ID: {order_id}")
                print(f"  Market ID: {market_id}")
                print(f"  Type: {order_type}")
                print(f"  Side: {side}")
                print(f"  Trigger Price: {trigger_price}")
                print(f"  Size: {order_base}")
                print(f"  Status: {status}")
                print(f"  Created: {created_at}")
                print()
        else:
            print("No open orders found for this wallet address.")
            
    except Exception as e:
        print(f"Error retrieving open orders: {e}")


if __name__ == "__main__":
    asyncio.run(main())
