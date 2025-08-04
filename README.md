# Reya Python SDK

This repository contains a Python SDK for interacting with the Reya ecosystem. It provides tools for subscribing to the Reya WebSocket for market data updates and executing on-chain actions via RPC.

## Features

### WebSocket API Client (Resource-Oriented)

The new resource-oriented WebSocket API client offers an intuitive, object-oriented interface for consuming real-time data from Reya:

- **Market Resources**
  - Access all markets data via `/api/trading/markets/data`
  - Subscribe to specific market data via `/api/trading/market/:marketId/data`
  - Monitor market orders via `/api/trading/market/:marketId/orders`

- **Wallet Resources**
  - Track wallet positions via `/api/trading/wallet/:address/positions`
  - Monitor wallet orders via `/api/trading/wallet/:address/orders`
  - Access account balances via `/api/trading/wallet/:address/accounts/balances`

- **Clean, Maintainable API Design**
  - Resource-based organization
  - Fluent interface for building subscriptions
  - Strong typing and extensive documentation

## Installation

Dependencies are managed with Poetry. To install Poetry, use the following command:

```bash
pipx install poetry
```

> **Note**: If `pipx` is not installed on your system, follow the [official installation guide](https://pipx.pypa.io/stable/installation/).

### Setting up with Poetry

Follow these steps to set up your development environment:

1. Clone the repository and navigate to the project directory:

```bash
git clone https://github.com/Reya-Labs/reya-python-sdk.git
cd reya-python-sdk
```

2. Create a virtual environment and install dependencies using Poetry:

```bash
poetry install
```

3. Activate the Poetry shell (virtual environment):

```bash
poetry shell
```

This will create and activate a virtual environment with all required dependencies installed.

### Running Examples

To run the examples, make sure you have activated the Poetry shell first:

```bash
poetry shell
python -m examples.trading.order_entry  # Run a specific example
```

## Finding Your Account ID

Before setting up your environment, you'll need to find your Reya account ID:

1. Replace `<your_wallet_address>` with your Ethereum wallet address in this URL:
   ```
   https://api.reya.xyz/api/trading/wallet/<your_wallet_address>/accounts
   ```

2. Open the URL in your browser. You'll see a JSON response containing your account information.

3. Note the `account_id` field from the response. This is your account ID needed for the environment setup.

For example, the response might look like:
```json
[
  {
    "account_id": "123456",
    "name": "Margin Account 1",
    "status": "OPEN",
    "updated_timestamp_ms": "1753799229000",
    "source": "reya"
  }
]
```

## Environment Setup

Create a `.env` file in the project root with the following variables:

```
ACCOUNT_ID=your_account_id
PRIVATE_KEY=your_private_key
CHAIN_ID=1729                   # Use 89346162 for testnet
REYA_WS_URL=wss://ws.reya.xyz/  # Use wss://websocket-testnet.reya.xyz/ for testnet
```

## WebSocket API Usage

### Basic Usage

Here's how to use the new resource-oriented WebSocket API:

```python
import os
import json
import logging
from dotenv import load_dotenv
from reya_data_feed import ReyaSocket

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("reya.example")

def on_open(ws):
    """Handle WebSocket connection open event."""
    logger.info("Connection established, subscribing to market data")
    # Subscribe to market data for market ID 1
    ws.market.market_data(1).subscribe()

def on_message(ws, message):
    """Handle WebSocket messages."""
    message_type = message.get("type")
    
    if message_type == "subscribed":
        channel = message.get("channel", "unknown")
        logger.info(f"Successfully subscribed to {channel}")
        
    elif message_type == "channel_data":
        channel = message.get("channel", "unknown")
        logger.info(f"Received data from {channel}")
        logger.info(f"Data: {message}")
    
    elif message_type == "error":
        logger.error(f"Error: {message.get('message', 'unknown error')}")

def main():
    # Load environment variables
    load_dotenv()
    
    # Get WebSocket URL from environment
    ws_url = os.environ.get("REYA_WS_URL", "wss://ws.reya.xyz/")
    
    # Create the WebSocket
    ws = ReyaSocket(
        url=ws_url,
        on_open=on_open,
        on_message=on_message,
    )
    
    # Connect to the WebSocket server - this is a blocking call
    logger.info("Connecting to WebSocket and starting event loop")
    logger.info("Press Ctrl+C to exit")
    
    try:
        # This will run forever until interrupted
        ws.connect()
    except KeyboardInterrupt:
        logger.info("Exiting gracefully")
    except Exception as e:
        logger.error(f"Error: {e}")
    finally:
        logger.info("WebSocket connection closed")

if __name__ == "__main__":
    main()
```

### Resource-Based API Structure

The API is organized around resources:

```
ReyaSocket
├── market
│   ├── all_markets                 # /api/trading/markets/data
│   │   ├── subscribe()
│   │   └── unsubscribe()
│   ├── market_data(market_id)      # /api/trading/market/:marketId/data
│   │   ├── subscribe()
│   │   └── unsubscribe()
│   └── market_orders(market_id)    # /api/trading/market/:marketId/orders
│       ├── subscribe()
│       └── unsubscribe()
└── wallet
    ├── positions(address)          # /api/trading/wallet/:address/positions
    │   ├── subscribe()
    │   └── unsubscribe()
    ├── orders(address)             # /api/trading/wallet/:address/orders
    │   ├── subscribe()
    │   └── unsubscribe()
    ├── balances(address)           # /api/trading/wallet/:address/accounts/balances
    │   ├── subscribe()
    │   └── unsubscribe()
    └── conditional_orders(address) # /api/trading/wallet/:address/conditionalOrders
        ├── subscribe()
        └── unsubscribe()
```

### Configuration via Environment Variables

All configuration is now handled via environment variables, making it easier to deploy and maintain:

```bash
# WebSocket URL (defaults to mainnet if not specified)
REYA_WS_URL="wss://ws.reya.xyz/"

# Connection parameters
REYA_WS_PING_INTERVAL=30     # Send ping every 30 seconds
REYA_WS_PING_TIMEOUT=10      # Wait 10 seconds for pong response
REYA_WS_CONNECTION_TIMEOUT=30 # Connection timeout in seconds
REYA_WS_RECONNECT_ATTEMPTS=3  # Number of reconnection attempts
REYA_WS_RECONNECT_DELAY=5     # Delay between reconnection attempts
REYA_WS_ENABLE_COMPRESSION=true # Enable WebSocket compression
REYA_WS_SSL_VERIFY=true       # Verify SSL certificate

# API prefix
REYA_TRADING_API_PREFIX="/api/trading/"
```

### Custom Configuration

You can customize the WebSocket behavior by passing parameters directly or loading from environment:

```python
from reya_data_feed import ReyaSocket
from reya_data_feed.config import WebSocketConfig

# Load config from environment variables
config = WebSocketConfig.from_env()

# Or create custom config
custom_config = WebSocketConfig(
    url="wss://custom-websocket.example.com/",
    ping_interval=15,
    ping_timeout=5,
    ssl_verify=True,
    trading_api_prefix="/api/v2/trading/"
)

socket = ReyaSocket(config=custom_config)
```

## Examples

The repository includes example scripts demonstrating how to use the SDK:

### Available Examples

- **Data Feed Examples**
  - `examples/basic_market_data.py` - Basic subscription to market data
  - `examples/wallet_monitoring.py` - Monitoring wallet positions and orders
  - `examples/consume_data_feed.py` - Working with the WebSocket data feed

- **Trading Examples**
  - `examples/trading/order_entry.py` - Creating various order types
  - `examples/trading/account_info.py` - Retrieving account information

- **Action Examples**
  - `examples/bridge_in_and_deposit.py` - Bridge in and deposit funds
  - `examples/withdraw_and_bridge_out.py` - Withdraw and bridge out funds
  - `examples/update_oracle_prices.py` - Update oracle prices

### Running Examples

To run the examples, use Python from the project root with the Poetry environment activated:

```bash
# Activate the Poetry environment (if not already activated)
poetry shell

# Run an example (use the module format)
python -m examples.basic_market_data
python -m examples.trading.order_entry
```

## API Reference

### WebSocket Client

#### `ReyaSocket`

The main WebSocket client for connecting to the Reya API.

```python
ReyaSocket(
    url=None,                # WebSocket URL (defaults to config)
    on_open=None,            # Connection open callback
    on_message=None,         # Message callback
    on_error=None,           # Error callback
    on_close=None,           # Connection close callback
    config=None,             # Custom configuration
)
```

**Methods:**

- `connect(sslopt=None)` - Connect to the WebSocket server
- `send_subscribe(channel, **kwargs)` - Send a subscription message
- `send_unsubscribe(channel, **kwargs)` - Send an unsubscription message

### Market Resources

#### `MarketResource`

Container for all market-related WebSocket resources.

**Properties:**

- `all_markets` - Access all markets data

**Methods:**

- `market_data(market_id)` - Get market data for a specific market
- `market_orders(market_id)` - Get orders for a specific market

### Wallet Resources

#### `WalletResource`

Container for all wallet-related WebSocket resources.

**Methods:**

- `positions(address)` - Get positions for a specific wallet address
- `orders(address)` - Get orders for a specific wallet address
- `balances(address)` - Get account balances for a specific wallet address

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Support

For any questions or support, please open a ticket on [Discord](https://discord.com/invite/reyaxyz).
