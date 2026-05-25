# ws-exec MVP test script

Quick end-to-end smoke test for the new ws-exec WebSocket order-entry service.
Hits cronos by default and exercises the four primary flows:

| # | Flow | Market | What it proves |
|---|---|---|---|
| 1 | `createOrder` (LIMIT GTC) | spot (`WETHRUSD`) | the order-creation wire works; the order rests in the book |
| 2 | `cancelOrder` | spot | the cancel wire works against the order from step 1 |
| 3 | `cancelAll` | spot | mass-cancel after opening 3 fresh resting orders |
| 4 | `createOrder` (IOC) | perp (`ETHRUSDPERP`) | the on-chain settlement path works (fills at mark) |

The spot orders rest at $1 — well below any realistic seller — so they never
match and stay safely in the book until step 2/3 cancels them. The perp IOC
uses a `$40,000` buy limit, loose enough that the pool can fill at the current
mark (~$2-3k on cronos). A far-out price like `$1` would revert on-chain with
`UnacceptableOrderPrice` because `Prices.sol::checkPriceLimit` enforces that
the executed price never exceeds the limit on a buy; this differs from spot,
where the order can rest below the book without ever needing to match. Flow 4
will therefore open a min-size long position (qty = `market.min_qty` = 0.01
ETH ≈ $30 of notional) — the test proves the wire end-to-end and the resulting
position is intentional.

## Prerequisites

Populate `.env` (already in the repo template):

```
CHAIN_ID=89346162                                       # cronos testnet
REYA_API_URL=https://api-cronos.reya.xyz/v2

# Spot test account (used for flows 1-3)
SPOT_ACCOUNT_ID_1=<int>
SPOT_PRIVATE_KEY_1=0x<64-hex>
SPOT_WALLET_ADDRESS_1=0x...

# Perp test account (used for flow 4)
PERP_ACCOUNT_ID_1=<int>
PERP_PRIVATE_KEY_1=0x<64-hex>
PERP_WALLET_ADDRESS_1=0x...

# Optional override; defaults to wss://ws-exec-testnet.reya.xyz
REYA_WS_EXEC_URL=wss://ws-exec-testnet.reya.xyz
```

Both accounts must be funded on cronos. Flow 4's perp IOC pays gas through
the ws-exec relayer wallet on the server side, so the account needs collateral
but not gas directly.

**Operational prereq for flow 4 (perp IOC):** the ws-exec per-pod relayer
wallet (e.g. `cronos-ws-exec-0` -> `0x744b23B8E86Af45b686E9BBf7cF463e6ED79a984`)
must be in the OrdersGateway's `conditional_orders` feature-flag allowlist on
the target chain. Without it, the on-chain call reverts with
`FeatureUnavailable("conditional_orders")` and the response comes back as
`CREATE_ORDER_OTHER_ERROR: Failed transaction.` Spot flows (1-3) don't hit
this path because they go through the matching engine, not the OrdersGateway.

## Run

```bash
poetry shell
python -m examples.ws_exec.mvp
```

Expected output on success:

```
Connecting to wss://ws-exec-testnet.reya.xyz
Spot account #1 loaded: accountId=... signer=0x...
Perp account #1 loaded: accountId=... signer=0x...

--- Flow 1: spot createOrder ---
  [spot] createOrder ✓ ok=true orderId=... status=OPEN clientOrderId=...

--- Flow 2: spot cancelOrder ---
  [spot] cancelOrder ✓ ok=true status=CANCELLED orderId=...

--- Flow 3: spot cancelAll ---
  [spot] createOrder ✓ ok=true orderId=...
  [spot] createOrder ✓ ok=true orderId=...
  [spot] createOrder ✓ ok=true orderId=...
  [spot] opened 3 orders to be cancelled by cancelAll
  [spot] cancelAll ✓ ok=true cancelledCount=3

--- Flow 4: perp createOrder (IOC) ---
  [perp] createOrder (IOC) ✓ ok=true status=CANCELLED execQty=0 orderId=...

✓ all flows passed
```

## How to adapt this to other markets

Edit the constants near the top of `mvp.py`:

```python
SPOT_SYMBOL = "WETHRUSD"
SPOT_MARKET_ID = 19
PERP_SYMBOL = "ETHRUSDPERP"
PERP_MARKET_ID = 1
```

The matching engine resolves `market_id` from `symbol` on its side, but the
EIP-712 signature includes `market_id` so the client must use the same value
the server will. If the wrong `market_id` is sent, the server's signature
recovery returns a different signer and the request fails with
`UNAUTHORIZED_SIGNATURE_ERROR`.

## Out of scope

- pytest / regression-gate integration (port to `tests/test_ws_exec/` later)
- reconnection, backpressure handling, jittered backoff
- perp GTC / TP / SL flows (those go through the `conditionalOrders` Postgres
  path with a different cancel-signature scheme — separate exercise)
- mainnet / staging variants (cronos only)
