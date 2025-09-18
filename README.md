# Reya Python SDK

This repository contains a Python SDK for interacting with the Reya ecosystem. It provides tools for subscribing to the Reya WebSocket for market data updates and executing on-chain actions via RPC.

## Versioning

The SDK uses a 4-digit semantic versioning scheme: `X.Y.Z.W`

- **X.Y.Z**: Matches the first three digits of the API specifications tag (major.minor.patch)
- **W**: Build number that increments automatically for SDK-only changes

For example, if the API specs are at version `2.0.3`, the SDK versions will be `2.0.3.0`, `2.0.3.1`, `2.0.3.2`, etc. When the API specs update to `2.0.4`, the SDK will reset to `2.0.4.0`.

This ensures the SDK version always reflects compatibility with the underlying API specifications while allowing for independent SDK improvements and bug fixes.

## Features

### REST API Client

- **Wallet Resource**
    - Get wallet accounts via `/v2/wallet/{address}/accounts`
    - Get wallet positions via `/v2/wallet/{address}/positions`
    - Get open orders via `/v2/wallet/{address}/openOrders`
    - Get perpetual executions via `/v2/wallet/{address}/perpExecutions`
    - Get spot executions via `/v2/wallet/{address}/spotExecutions`
    - Get configuration via `/v2/wallet/{address}/configuration`

- **Order Entry Resource**
    - Create orders via `/v2/createOrder` (IOC, GTC, SL, TP)
    - Cancel orders via `/v2/cancelOrder`

- **Market Data Resource**
    - Get all markets summary via `/v2/markets/summary`
    - Get market summary via `/v2/market/{symbol}/summary`
    - Get market perpetual executions via `/v2/market/{symbol}/perpExecutions`
    - Get historical candles via `/v2/candleHistory/{symbol}/{resolution}`

- **Reference Data Resource**
    - Get market definitions via `/v2/marketDefinitions`
    - Get asset definitions via `/v2/assetDefinitions`
    - Get liquidity parameters via `/v2/liquidityParameters`
    - Get global fee parameters via `/v2/globalFeeParameters`
    - Get fee tiers via `/v2/feeTiers`

- **Prices Resource**
    - Get all prices via `/v2/prices`
    - Get price by symbol via `/v2/prices/{symbol}`

### WebSocket API Client (Resource-Oriented)

- **Market Resources**
    - Subscribe to all markets summary via `/v2/markets/summary`
    - Subscribe to specific market summary via `/v2/market/{symbol}/summary`
    - Monitor market perpetual executions via `/v2/market/{symbol}/perpExecutions`

- **Wallet Resources**
    - Track wallet positions via `/v2/wallet/{address}/positions`
    - Monitor wallet open orders via `/v2/wallet/{address}/orderChanges`
    - Monitor wallet perpetual executions via `/v2/wallet/{address}/perpExecutions`

- **Price Resources**
    - Track prices for all markets via `/v2/prices`
    - Track prices for specific market via `/v2/prices/{symbol}`

## API Specifications

This SDK is built from official API specifications that define the V2 endpoints:

### OpenAPI Specification
- **Location**: `specs/openapi-trading-v2.yaml`
- **Version**: OpenAPI 3.1.1
- **Purpose**: Defines all REST API endpoints, request/response models, and authentication
- **Auto-generation**: The `sdk/open_api/` module is automatically generated from this specification

### AsyncAPI Specification  
- **Location**: `specs/asyncapi-trading-v2.yaml`
- **Version**: AsyncAPI 2.6.0
- **Purpose**: Defines WebSocket message schemas and real-time data structures
- **Auto-generation**: The `sdk/async_api/` module is automatically generated from this specification

### Code Generation
The SDK uses automated code generation to ensure type safety and API compatibility:
- **REST API Generation**: `scripts/generate-api.sh` - generates Python client from OpenAPI spec
- **WebSocket Generation**: `scripts/generate-ws.sh` - generates Pydantic models from AsyncAPI spec

These specifications ensure the SDK stays in sync with the latest Reya V2 API endpoints and provide the foundation for the high-level, user-friendly interfaces in `sdk/reya_rest_api/` and `sdk/reya_websocket/`.

## Installation

Dependencies are managed with Poetry. To install Poetry, use the following command:

```bash
pipx install poetry
```

> **Note**: If `pipx` is not installed on your system, follow the [official installation guide](https://pipx.pypa.io/stable/installation/).

### Setting up with Poetry

> **Note**: If you want `poetry` to create the virtual environment in the project directory, run `poetry config virtualenvs.in-project true`. This tells Poetry to create the .venv/ folder inside each project directory instead of the default global cache.

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

3. Activate the virtual environment:

```bash
source $(poetry env info --path)/bin/activate
```

This will create and activate a virtual environment with all required dependencies installed.

### Running Examples

To run the examples, make sure you have activated the virtual environment first:

```bash
source $(poetry env info --path)/bin/activate
python -m examples.rest_api.order_entry  # Run a specific example
```

## Finding Your Account ID

Before setting up your environment, you'll need to find your Reya account ID:

1. Replace `<your_wallet_address>` with your Ethereum wallet address in this URL:
   ```
   https://api.reya.xyz/v2/wallet/<your_wallet_address>/accounts
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
REYA_API_BASE_URL=https://api.reya.xyz/v2  # Use https://api-test.reya.xyz/v2 for testnet
WALLET_ADDRESS=your_wallet_address
```
### Resource-Based API Structure

#### REST API Structure

The REST API client is organized around the following resources:

```
ReyaTradingClient
├── wallet                           # Wallet resource
│   ├── get_accounts()               # /v2/wallet/{address}/accounts
│   ├── get_positions()              # /v2/wallet/{address}/positions
│   ├── get_open_orders()            # /v2/wallet/{address}/openOrders
│   ├── get_perp_executions()        # /v2/wallet/{address}/perpExecutions
│   ├── get_spot_executions()        # /v2/wallet/{address}/spotExecutions
│   └── get_configuration()          # /v2/wallet/{address}/configuration
├── orders                           # Order Entry resource
│   ├── create_order()               # /v2/createOrder (IOC, GTC, SL, TP)
│   └── cancel_order()               # /v2/cancelOrder
├── markets                          # Market Data resource
│   ├── get_markets_summary()        # /v2/markets/summary
│   ├── get_market_summary()         # /v2/market/{symbol}/summary
│   ├── get_market_perp_executions() # /v2/market/{symbol}/perpExecutions
│   └── get_candles()                # /v2/candleHistory/{symbol}/{resolution}
├── reference                        # Reference Data resource
│   ├── get_market_definitions()     # /v2/marketDefinitions
│   ├── get_asset_definitions()      # /v2/assetDefinitions
│   ├── get_liquidity_parameters()   # /v2/liquidityParameters
│   ├── get_global_fee_parameters()  # /v2/globalFeeParameters
│   └── get_fee_tiers()              # /v2/feeTiers
└── prices                           # Prices resource
    ├── get_prices()                 # /v2/prices
    └── get_price()                  # /v2/prices/{symbol}
```

#### WebSocket API Structure

The WebSocket API client is organized around resources:

```
ReyaSocket
├── market
│   ├── all_markets_summary             # /v2/markets/summary
│   │   ├── subscribe()
│   │   └── unsubscribe()
│   ├── market_summary(symbol)          # /v2/market/{symbol}/summary
│   │   ├── subscribe()
│   │   └── unsubscribe()
│   └── market_perp_executions(symbol)  # /v2/market/{symbol}/perpExecutions
│       ├── subscribe()
│       └── unsubscribe()
├── wallet
│   ├── positions(address)              # /v2/wallet/{address}/positions
│   │   ├── subscribe()
│   │   └── unsubscribe()
│   ├── order_changes(address)            # /v2/wallet/{address}/orderChanges
│   │   ├── subscribe()
│   │   └── unsubscribe()
│   └── perp_executions(address)        # /v2/wallet/{address}/perpExecutions
│       ├── subscribe()
│       └── unsubscribe()
└── ping                                # /ping (heartbeat)
    ├── send_pong()
    └── receive_ping()
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

```

## Examples

The repository includes example scripts demonstrating how to use the SDK:

### Available Examples

- **REST API Examples**
    - `examples/rest_api/wallet_example.py` - Comprehensive example of all wallet endpoints
    - `examples/rest_api/markets_example.py` - Using markets-related endpoints
    - `examples/rest_api/assets_example.py` - Retrieving assets information
    - `examples/rest_api/prices_example.py` - Getting price information
    - `examples/rest_api/order_entry.py` - Creating various order types
    - `examples/rest_api/account_info.py` - Getting open orders for a wallet

- **WebSocket Data Feed Examples**
    - `examples/websocket/market_monitoring.py` - Basic subscription to market data
    - `examples/websocket/prices_monitoring.py` - Monitoring real-time prices for an asset pair
    - `examples/websocket/wallet_monitoring.py` - Monitoring wallet positions and orders
    - `examples/consume_data_feed.py` - Working with the WebSocket data feed

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

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Support

For any questions or support, please open a ticket on [Discord](https://discord.com/invite/reyaxyz).
