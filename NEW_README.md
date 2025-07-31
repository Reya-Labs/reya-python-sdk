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

To create the shell dedicated to running the examples, run this from the root of the repository:
```bash
cd examples && poetry shell
poetry install --no-root
cd ..
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

### Backward Compatibility

The SDK maintains backward compatibility with the legacy implementation:

```python
# Legacy implementation
from reya_data_feed import LegacyReyaSocket

# Use the legacy implementation as before
ws = LegacyReyaSocket(url="wss://ws.reya.xyz/", on_message=on_message)
```

### Basic Usage

Here's how to use the new resource-oriented WebSocket API:

```python
from reya_data_feed import ReyaSocket

def on_message(ws, message):
    if message["type"] == "connected":
        print("Connected!")
        
        # Subscribe to all markets data
        ws.market.all_markets.subscribe()
        
        # Subscribe to a specific market
        ws.market.market_data("BTCUSDMARK").subscribe()
        
        # Subscribe to wallet positions
        wallet_address = "0x123..."
        ws.wallet.positions(wallet_address).subscribe()
        
    elif message["type"] == "channel_data":
        print(f"Received data for {message['channel']}:")
        print(message["contents"])

# Create and connect the socket
socket = ReyaSocket(on_message=on_message)
socket.connect()
```

### Resource-Based API Structure

The API is organized around resources:

```
ReyaSocket
├── market
│   ├── all_markets
│   │   ├── subscribe()
│   │   └── unsubscribe()
│   ├── market_data(market_id)
│   │   ├── subscribe()
│   │   └── unsubscribe()
│   └── market_orders(market_id)
│       ├── subscribe()
│       └── unsubscribe()
└── wallet
    ├── positions(address)
    │   ├── subscribe()
    │   └── unsubscribe()
    ├── orders(address)
    │   ├── subscribe()
    │   └── unsubscribe()
    └── balances(address)
        ├── subscribe()
        └── unsubscribe()
```

### Environment-Specific Configuration

You can easily connect to different environments:

```python
from reya_data_feed import ReyaSocket

# Connect to mainnet (default)
mainnet_socket = ReyaSocket(environment="mainnet")

# Connect to testnet
testnet_socket = ReyaSocket(environment="testnet")

# Connect to local development environment
local_socket = ReyaSocket(environment="local")
```

### Custom Configuration

You can customize the WebSocket behavior:

```python
from reya_data_feed import ReyaSocket
from reya_data_feed.config import WebSocketConfig

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

### WebSocket Examples

- `examples/websocket_v2/basic_market_data.py` - Basic subscription to market data
- `examples/websocket_v2/wallet_monitoring.py` - Monitoring wallet positions and orders

### Running Examples

To run the examples, use Python from the project root with the Poetry environment activated:

```bash
# Activate the Poetry environment (if not already activated)
poetry shell

# Run an example
python3 -m examples.websocket_v2.basic_market_data

# Run another example
python3 -m examples.trade_execution
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
    environment=None         # Environment name (mainnet, testnet, local)
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
