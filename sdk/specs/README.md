# Reya DEX Trading API v2 Specifications

This directory contains OpenAPI and AsyncAPI specifications for the new user-friendly Reya DEX Trading API v2.

## Files

- `openapi-trading-v2.yaml` - OpenAPI 3.0.3 specification for REST endpoints
- `asyncapi-trading-v2.yaml` - AsyncAPI 2.6.0 specification for WebSocket endpoints

## Key Changes from v1 API

The new v2 API implements all the requirements specified in the [new-endpoints-plan.md](../new-endpoints-plan.md):

### 1. Removed Blockchain-Specific Fields
- ❌ `block_number`, `transaction_hash`, `block_timestamp` - No longer exposed to users
- ✅ `timestamp` - Renamed from `block_timestamp`, uses milliseconds as JSON numbers

### 2. Execution Type Changes
- ❌ `AutoExchanges` - Rebranded as `SpotExecutions`
- ✅ `SpotExecution.type` - Always set to `LIQUIDATION` for former AutoExchanges
- ✅ `PerpExecution` - Renamed from `Trade` (orders table rows)

### 3. Symbol-Based Identifiers
- ❌ `AssetPairId`, `MarketId` - Removed from user-facing endpoints
- ✅ `symbol` - New ticker format:
  - Spot/Collateral: `ETHRUSD`
  - Perp markets: `ETHRUSDPERP`

### 4. Removed unique_id
- ❌ `unique_id` - No longer provided to prevent user dependency
- ✅ Users must rely on timestamps, sequence numbers for uniqueness

### 5. Price and Size Formatting
- ✅ All prices and sizes as JSON strings with real values (no E18 encoding)
- ✅ All sizes as unsigned integers with separate `side` field (BID/ASK)
- ✅ `qty` field replaces absolute value of `base`
- ✅ `side` field indicates BID or ASK

### 6. Timestamp Format
- ✅ JSON numbers for timestamps (milliseconds since epoch)
- ✅ Compatible with microsecond precision if needed in future

### 7. Lean Message Format
- ✅ Abbreviated field names where appropriate (`qty` vs `quantity`)
- ✅ Removed non-essential fields to optimize for performance
- ✅ Field additions are non-breaking, removals avoided

### 8. Derivative Fields Strategy
- ✅ Kept essential calculated fields that would be expensive for frontends
- ✅ Removed fields available in major exchanges (following Binance patterns)
- ℹ️ Future consideration: Separate endpoints for frontend vs programmatic traders

## Endpoint Mapping

### REST Endpoints (OpenAPI)

| v2 Endpoint | Purpose | Key Changes |
|-------------|---------|-------------|
| `GET /markets/summary` | Market trading data | New aggregated format, removed basic `/markets` endpoint |
| `GET /market/{symbol}/summary` | Single market data | Symbol-based routing |
| `GET /market/{symbol}/perpExecutions` | Market executions | New execution format |
| `GET /wallet/{address}/positions` | User positions | Simplified position data |
| `GET /wallet/{address}/openOrders` | Pending orders | Clean order format |
| `GET /wallet/{address}/accountBalances` | Account balances | Cleaner balance format, simplified endpoint path |
| `GET /wallet/{address}/perpExecutions` | User perp trades | New execution format |
| `GET /wallet/{address}/spotExecutions` | User spot trades | Renamed from AutoExchanges |
| `GET /prices` | All prices | Simplified price data |
| `GET /prices/{symbol}` | Symbol price | Symbol-based routing |
| `GET /assetDefinitions` | Asset config | Cleaner asset definitions, simplified endpoint path |
| `GET /candles/{symbol}/{resolution}` | OHLCV data | Symbol-based routing |
| `GET /feeTiers` | Fee parameters | Simplified fee structure |

### WebSocket Endpoints (AsyncAPI)

| v2 Channel | Purpose | Key Changes |
|------------|---------|-------------|
| `/v2/markets/summary` | Real-time market data | New aggregated format |
| `/v2/market/{symbol}/summary` | Single market updates | Symbol-based subscriptions |
| `/v2/market/{symbol}/perpExecutions` | Market execution feed | New execution format |
| `/v2/wallet/{address}/positions` | Position updates | Simplified position data |
| `/v2/wallet/{address}/openOrders` | Order updates | Clean order format |
| `/v2/wallet/{address}/accountBalances` | Account balance updates | Cleaner balance format, simplified channel path |
| `/v2/wallet/{address}/perpExecutions` | User perp execution feed | New execution format |
| `/v2/wallet/{address}/spotExecutions` | User spot execution feed | Renamed from AutoExchanges |
| `/v2/prices` | Price feed | Simplified price updates |
| `/v2/prices/{symbol}` | Symbol price feed | Symbol-based subscriptions |

## Data Type Changes

### Execution Types
- `ORDER_MATCH` - Normal trade execution
- `LIQUIDATION` - Liquidation execution (includes former AutoExchanges)
- `ADL` - Auto-deleveraging execution

### Side Enumeration
- `B` - Bid (Buy)
- `A` - Ask (Sell)

### Order Status
- `PENDING` - Order pending execution
- `FILLED` - Order completely filled
- `CANCELLED` - Order cancelled
- `REJECTED` - Order rejected

### Account Status
- `OPEN` - Active account
- `CLOSED` - Closed account

## Usage Examples

### REST API
```bash
# Get market summary for Bitcoin perpetual
curl "https://api.reya.xyz/v2/market/BTCRUSDPERP/summary"

# Get user positions
curl "https://api.reya.xyz/v2/wallet/0x6c51275fd01d5dbd2da194e92f920f8598306df2/positions"

# Get historical candles
curl "https://api.reya.xyz/v2/candles/BTCRUSDPERP/1h?from=1747804140&to=1747804380"
```

### WebSocket API
```javascript
const ws = new WebSocket('wss://ws.reya.xyz');

// Subscribe to Bitcoin perpetual market summary
ws.send(JSON.stringify({
  type: 'subscribe',
  channel: '/v2/market/BTCRUSDPERP/summary'
}));

// Subscribe to user positions
ws.send(JSON.stringify({
  type: 'subscribe', 
  channel: '/v2/wallet/0x6c51275fd01d5dbd2da194e92f920f8598306df2/positions'
}));
```

## Code Generation

These specifications are designed for code generation using:
- **OpenAPI Generator** - For REST client SDKs
- **AsyncAPI Generator** - For WebSocket client SDKs

Supported languages:
- TypeScript/JavaScript
- Python
- Go
- Rust
- Java

## Validation

Both specifications have been validated for:
- ✅ Syntax compliance (OpenAPI 3.0.3, AsyncAPI 3.0.0)
- ✅ Schema consistency between REST and WebSocket
- ✅ Alignment with requirements in new-endpoints-plan.md
- ✅ Data type compatibility with trading-public-types.ts
- ✅ Proper error response definitions
- ✅ Comprehensive examples and documentation

## Next Steps

1. Generate client SDKs using these specifications
2. Implement server-side endpoints based on these contracts
3. Set up automated validation in CI/CD pipeline
4. Create integration tests based on these specifications
