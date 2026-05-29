# ws-exec

The ws-exec service is a low-latency alternative to the REST `/v2/orders`
endpoints. It accepts the same EIP-712-signed payloads over a persistent
WebSocket connection and replies with per-request envelopes keyed by an
opaque request id.

The canonical runnable user-facing sample lives next to this README at
[`ws_exec.py`](./ws_exec.py); the full end-to-end harness (every operation +
every error mode) lives at [`tests/ws_exec/mvp.py`][mvp]. The SDK ships a
high-level async client — `sdk.reya_ws_exec.ReyaWsExecClient` — that hides
the wire envelope, the EIP-712 signing pipeline, and the in-flight dispatch
map; the example file shows the canonical usage shape.

[mvp]: ../../../tests/ws_exec/mvp.py

## Quickstart

```python
import asyncio
from sdk.open_api.models import TimeInForce
from sdk.reya_rest_api import ReyaTradingClient
from sdk.reya_rest_api.config import TradingConfig
from sdk.reya_rest_api.models.orders import LimitOrderParameters
from sdk.reya_ws_exec import ReyaWsExecClient


async def main():
    rest = ReyaTradingClient(TradingConfig.from_env_spot(account_number=1))
    await rest.start()  # loads market definitions

    async with ReyaWsExecClient(rest_client=rest, ws_url="wss://ws-exec-testnet.reya.xyz") as ws:
        resp = await ws.create_limit_order(
            LimitOrderParameters(
                symbol="WETHRUSD",
                is_buy=True,
                limit_px="1",
                qty="0.001",
                time_in_force=TimeInForce.GTC,
            )
        )
        await ws.cancel_order(
            order_id=resp.order_id,
            symbol="WETHRUSD",
            account_id=rest.config.account_id,
        )

    await rest.close()


asyncio.run(main())
```

The same `ReyaTradingClient` instance can be used concurrently for REST calls
— the per-wallet nonce manager is class-level on `ReyaTradingClient`, so REST
and WS-exec calls share a single monotonic stream and cannot collide.

## End-to-end test harness

[`tests/ws_exec/mvp.py`][mvp] exercises every supported operation and the
highest-signal error modes against a live testnet (Cronos) deployment.

### Happy paths (11) — driven via `ReyaWsExecClient`

| # | Flow | Market | What it proves |
|---|---|---|---|
| 0 | application `ping` / `pong` | n/a | JSON-layer liveness probe (distinct from RFC 6455 protocol pings, which the transport handles transparently) |
| 1 | `createOrder` (LIMIT GTC) | spot (`WETHRUSD`) | order-creation wire works; order rests |
| 2 | `cancelOrder` by `orderId` | spot | cancel-by-id wire works |
| 3 | `createOrder` + `cancelOrder` by `clientOrderId` | spot | alternative cancel path (spot EIP-712 schema only) |
| 4 | `cancelAll` (symbol-scoped) | spot | mass-cancel after opening 3 resting orders |
| 5 | `cancelAll` (account-wide, `symbol=None`) | spot | account-wide branch with `marketId=0` typed-data reconstruction |
| 6 | `createOrder` (LIMIT GTC conditional) + cancel | perp (`ETHRUSDPERP`) | conditional resting + cancel wire |
| 7 | `createOrder` (TP) + cancel | perp | take-profit conditional path |
| 8 | `createOrder` (SL) + cancel | perp | stop-loss conditional path |
| 9 | `createOrder` (LIMIT IOC) | perp | on-chain settlement (`OrdersGateway::execute`); opens a min-size long |
| 10 | `createOrder` (LIMIT IOC, `reduceOnly=true`) | perp | closes the long from flow 9 — paired with 9 via `try`/`finally` |

The spot orders rest at $1 — well below any realistic seller — so they
never match and stay safely in the book until cancelled. The perp IOC uses
a `$40,000` buy limit, loose enough that the pool can fill at the current
mark (~$2–3k on cronos). Flow 9 opens a min-size long position
(qty = `market.min_order_qty` ≈ 0.01 ETH) which Flow 10 unwinds; the pair
is wrapped in a `try`/`finally` so the close always runs even if a later
assertion raises.

### Error paths (6) — driven on raw WebSockets

Negative tests open their own short-lived raw connection so the test harness
can send intentionally-malformed payloads without interfering with the
high-level client's in-flight dispatch map.

| # | Flow | Layer | What it proves |
|---|---|---|---|
| E1 | `DUPLICATE_REQUEST_ID` | framing | same envelope `id` in flight twice triggers the per-connection uniqueness check |
| E2 | `MALFORMED_JSON` | framing | non-JSON frame rejected at the framing layer |
| E3 | `UNKNOWN_TYPE` | framing | unknown envelope `type` rejected |
| E4 | `INVALID_NONCE_ERROR` | per-op | replayed nonce rejected by the uniqueness check |
| E5 | `ORDER_DEADLINE_PASSED_ERROR` | per-op | past `expiresAfter` rejected by the validator chain |
| E6 | `UNAUTHORIZED_SIGNATURE_ERROR` | per-op | signing key vs declared `signerWallet` mismatch is rejected |

## Prerequisites

Populate `.env`:

```
CHAIN_ID=89346162                                       # cronos testnet
REYA_API_URL=https://api-cronos.reya.xyz/v2

# Spot test account (used for flows 1-5 + E1, E4, E5)
SPOT_ACCOUNT_ID_1=<int>
SPOT_PRIVATE_KEY_1=0x<64-hex>
SPOT_WALLET_ADDRESS_1=0x...

# Perp test account (used for flows 6-10)
PERP_ACCOUNT_ID_1=<int>
PERP_PRIVATE_KEY_1=0x<64-hex>
PERP_WALLET_ADDRESS_1=0x...

# Optional: second spot account, only used for flow E6 (UNAUTHORIZED_SIGNATURE_ERROR)
SPOT_ACCOUNT_ID_2=<int>
SPOT_PRIVATE_KEY_2=0x<64-hex>
SPOT_WALLET_ADDRESS_2=0x...

# Optional override; defaults to wss://ws-exec-testnet.reya.xyz
REYA_WS_EXEC_URL=wss://ws-exec-testnet.reya.xyz
```

All accounts must be funded on cronos. Flow 9 (perp IOC) pays gas through the
ws-exec relayer wallet on the server side, so the account needs collateral
but not gas directly.

**Operational prereq for perp IOC + conditional flows (6–10):** the ws-exec
per-pod relayer wallet (e.g. `cronos-ws-exec-0` →
`0x744b23B8E86Af45b686E9BBf7cF463e6ED79a984`) must be in the OrdersGateway's
`conditional_orders` feature-flag allowlist on the target chain. Without it,
the on-chain call reverts with `FeatureUnavailable("conditional_orders")`
and the response comes back as `CREATE_ORDER_OTHER_ERROR: Failed
transaction.` Spot flows (1–5, E1, E4, E5) don't hit this path because they
go through the matching engine, not the OrdersGateway.

## Run

```bash
poetry shell
python -m tests.ws_exec.mvp
```

Expected output on success ends with `all flows passed`.

## Out of scope

- pytest / regression-gate integration (this is a standalone harness, not a
  pytest test — even though it lives under `tests/`)
- reconnection, backpressure handling, jittered backoff
- mainnet / staging variants (cronos only)
