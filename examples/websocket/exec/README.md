# ws-exec

The ws-exec service is the persistent-WebSocket alternative to the REST order
entry endpoints. It accepts the same EIP-712-signed request bodies and returns
id-correlated response envelopes.

The canonical runnable sample is [`ws_exec.py`](./ws_exec.py). It demonstrates
one intentionally small lifecycle: create a resting spot GTC order and cancel
it by `orderId`. The high-level
`sdk.reya_ws_exec.ReyaWsExecClient` also exposes trigger creation, modify,
symbol/account-wide cancel, and cancel-on-disconnect operations; those are not
all exercised by this quickstart.

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

    async with ReyaWsExecClient(
        rest_client=rest,
        ws_url="wss://ws-exec-devnet.reya-cronos.network",
    ) as ws:
        response = await ws.create_limit_order(
            LimitOrderParameters(
                symbol="WETHRUSD",
                is_buy=True,
                limit_px="1",
                qty="0.001",
                time_in_force=TimeInForce.GTC,
            )
        )
        await ws.cancel_order(
            order_id=response.order_id,
            symbol="WETHRUSD",
            account_id=rest.config.account_id,
        )

    await rest.close()


asyncio.run(main())
```

The same `ReyaTradingClient` instance can be used concurrently for REST calls.
REST and ws-exec share its per-wallet monotonic nonce stream.

## Prerequisites

Populate `.env` with a funded devnet spot account:

```dotenv
CHAIN_ID=89346162
REYA_API_URL=https://api-devnet.reya-cronos.network/v2
REYA_WS_EXEC_URL=wss://ws-exec-devnet.reya-cronos.network
REYA_ORDERS_GATEWAY=0x7Ec89E555c771D2B5939aBE5C4E4291852633D4D

SPOT_ACCOUNT_ID_1=<int>
SPOT_PRIVATE_KEY_1=0x<64-hex>
SPOT_WALLET_ADDRESS_1=0x...
```

`REYA_WS_EXEC_URL` is optional for the sample; it defaults to the current
devnet value above. The account needs sufficient collateral for the operation
being submitted. The server-side relayer handles protocol transaction gas.

## Run the sample

```bash
poetry run python -m examples.websocket.exec.ws_exec
```

The sample creates and then cancels one spot order. It does not claim to test
the whole protocol surface.

## Live regression coverage

The actual live pytest suite is
[`tests/ws_exec/test_ws_exec.py`](../../../tests/ws_exec/test_ws_exec.py). It
covers ping, spot/perp create, cancel by order/client id, symbol/account-wide
cancel, IOC behavior, and high-signal protocol errors. Dedicated engine suites
cover operations omitted from that file:

- [`tests/engine/test_modify_ws_exec.py`](../../../tests/engine/test_modify_ws_exec.py)
  for modify;
- [`tests/engine/test_cod_ws_exec.py`](../../../tests/engine/test_cod_ws_exec.py)
  for `cancelAllAfter`;
- [`tests/engine/test_gtt_ws_exec.py`](../../../tests/engine/test_gtt_ws_exec.py)
  for GTT expiry;
- [`tests/engine/test_post_only_ws_exec.py`](../../../tests/engine/test_post_only_ws_exec.py)
  for post-only behavior.

Run the main ws-exec file with:

```bash
poetry run pytest tests/ws_exec/test_ws_exec.py -ra --tb=short
```

These are live integration tests. They require the environment and credentials
described by `.env.example`; missing prerequisites fail explicitly rather than
silently skipping coverage.

## Deadline versus order lifetime

`deadline` is the short EIP-712 signature-validity window checked when the
request enters the API. `expiresAfter` is a separate resting-order lifetime:
it is omitted for IOC/GTC, and a GTT order must set it later than `deadline`.
Neither field should be copied into the other.

Perp IOC fills are matched by the matching engine and finalized through the
deployed settlement pipeline. Integrators do not call the retired direct
gateway settlement entrypoint themselves.
