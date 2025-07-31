"""Example of monitoring wallet positions, orders, and balances.

This example connects to the Reya WebSocket API and subscribes to all
wallet-related data streams for a specific wallet address.
"""

import os
import logging
from dotenv import load_dotenv

# Import the new resource-oriented WebSocket client
from reya_data_feed import ReyaSocket

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

def on_message(ws, message):
    """Handle WebSocket messages."""
    message_type = message.get("type")
    
    if message_type == "connected":
        print(f"Connected with ID: {message.get('connection_id', 'unknown')}")
        
        # Get wallet address from environment
        wallet_address = os.environ.get("WALLET_ADDRESS")
        if not wallet_address:
            print("ERROR: WALLET_ADDRESS environment variable not set")
            return
        
        print(f"Monitoring wallet: {wallet_address}")
        
        # Subscribe to wallet positions
        ws.wallet.positions(wallet_address).subscribe()
        
        # Subscribe to wallet orders
        ws.wallet.orders(wallet_address).subscribe()
        
        # Subscribe to wallet account balances
        ws.wallet.balances(wallet_address).subscribe()
        
    elif message_type == "subscription_succeeded":
        channel = message.get("channel", "unknown")
        print(f"Successfully subscribed to {channel}")
        
    elif message_type == "channel_data":
        channel = message.get("channel", "unknown")
        contents = message.get("contents", {})
        
        if "positions" in channel:
            print("\n--- POSITIONS UPDATE ---")
            for position in contents:
                market_id = position.get("marketId", "unknown")
                size = position.get("size", "0")
                entry_price = position.get("entryPrice", "0")
                market_price = position.get("marketPrice", "0")
                pnl = position.get("unrealizedPnl", "0")
                print(f"Market: {market_id}, Size: {size}, Entry: {entry_price}, Current: {market_price}, PnL: {pnl}")
        
        elif "orders" in channel:
            print("\n--- ORDERS UPDATE ---")
            for order in contents:
                order_id = order.get("id", "unknown")
                market_id = order.get("marketId", "unknown")
                side = order.get("side", "unknown")
                size = order.get("size", "0")
                price = order.get("price", "0")
                status = order.get("status", "unknown")
                print(f"Order {order_id}: Market {market_id}, {side.upper()} {size} @ {price}, Status: {status}")
        
        elif "balances" in channel:
            print("\n--- BALANCES UPDATE ---")
            for balance in contents:
                account_id = balance.get("accountId", "unknown")
                token = balance.get("token", "unknown")
                amount = balance.get("amount", "0")
                print(f"Account {account_id}: {amount} {token}")
    
    elif message_type == "error":
        print(f"Error: {message.get('message', 'unknown error')}")
        
def main():
    """Main entry point for the example."""
    # Load environment variables
    load_dotenv()
    
    # Get WebSocket URL from environment
    ws_url = os.environ.get("REYA_WS_URL", "wss://ws.reya.xyz/")
    
    # Check if wallet address is set
    if not os.environ.get("WALLET_ADDRESS"):
        print("Please set the WALLET_ADDRESS environment variable")
        print("Add WALLET_ADDRESS=0x... to your .env file")
        return
    
    print(f"Connecting to {ws_url}")
    
    # Create and connect the WebSocket
    ws = ReyaSocket(
        url=ws_url,
        on_message=on_message,
    )
    
    # Connect to the WebSocket server
    # This call is blocking and will run until interrupted
    ws.connect()

if __name__ == "__main__":
    main()
