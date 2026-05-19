"""ws-exec MVP test script.

Exercises the four primary flows of the new ws-exec WebSocket order-entry
service against cronos:

  1. Spot createOrder (LIMIT GTC, far-out price so it rests in the book)
  2. Spot cancelOrder (cancels the order from step 1)
  3. Spot cancelAll (opens N more orders, then mass-cancels them)
  4. Perp createOrder (IOC, fills at current mark with loose limit)

Run with:
    poetry shell
    python -m examples.ws_exec.mvp

Requires .env populated with SPOT_*_1 + PERP_*_1 credentials and CHAIN_ID.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from dotenv import load_dotenv
from websocket import WebSocket  # type: ignore[attr-defined]

from sdk.async_exec_api.cancel_order_request import CancelOrderRequest
from sdk.async_exec_api.create_order_request import CreateOrderRequest
from sdk.async_exec_api.mass_cancel_request import MassCancelRequest
from sdk.async_exec_api.order_type import OrderType
from sdk.async_exec_api.time_in_force import TimeInForce
from sdk.reya_rest_api.auth.signatures import SignatureGenerator
from sdk.reya_rest_api.config import TradingConfig
from sdk.reya_rest_api.constants.enums import OrdersGatewayOrderType

# Symbols we test against. Market IDs and min-qty constraints are resolved at
# script startup via the public REST market-definitions endpoints, so the
# script self-heals if cronos renumbers markets.
SPOT_SYMBOL = "WETHRUSD"
PERP_SYMBOL = "ETHRUSDPERP"

# Spot uses a far-out limit ($1) on an ETH-priced book so the GTC just rests
# until we explicitly cancel it. Reya's spot matching engine doesn't enforce a
# distance-from-mark cap, so the order sits safely below any real seller.
SPOT_LIMIT_PX = Decimal("1")

# Perp IOC has different semantics: it matches against the pool, and the
# on-chain `Prices.sol::checkPriceLimit` reverts with `UnacceptableOrderPrice`
# if the executed price exceeds the limit (for a buy) or falls below it (for a
# sell). A $1 buy would never fill — it would revert on-chain inside
# `OrdersGateway:execute`, surfaced as a `FailedOrderBytes` event. So the IOC
# limit must be loose enough that the pool *can* fill within it. A $40k buy
# limit fills at the current mark (~$2-3k), opening a min-size long position
# (qty = market.min_qty = 0.01 ETH ≈ $30 of notional on cronos). The test
# proves the wire end-to-end; the resulting position is intentional and tiny.
PERP_LIMIT_PX = Decimal("40000")

DEFAULT_WS_EXEC_URL = "wss://ws-exec-testnet.reya.xyz"
DEFAULT_API_URL = "https://api-cronos.reya.xyz/v2"
GTC_DEADLINE_S = 86_400  # 24h for the resting spot GTC
SHORT_DEADLINE_S = 60  # 60s for spot cancel + perp IOC
RECV_TIMEOUT_S = 15.0


# ---- Market resolution ------------------------------------------------------


@dataclass(frozen=True)
class MarketInfo:
    market_id: int
    min_qty: Decimal


def _http_get_json(url: str, timeout_s: float = 10.0) -> Any:
    with urllib.request.urlopen(url, timeout=timeout_s) as resp:
        return json.loads(resp.read())


def _resolve_markets(api_url: str) -> dict[str, MarketInfo]:
    """Resolve symbols -> (marketId, minOrderQty) from the public REST endpoints.
    Avoids hardcoded IDs that break the EIP-712 signature whenever cronos
    renumbers markets."""
    api_url = api_url.rstrip("/")
    perp = _http_get_json(f"{api_url}/marketDefinitions")
    spot = _http_get_json(f"{api_url}/spotMarketDefinitions")

    out: dict[str, MarketInfo] = {}
    for entry in (perp or []) + (spot or []):
        symbol = entry.get("symbol") or entry.get("ticker")
        market_id = entry.get("marketId") or entry.get("id")
        min_qty = entry.get("minOrderQty") or entry.get("minimumQty") or "0"
        if symbol is None or market_id is None:
            continue
        out[str(symbol)] = MarketInfo(
            market_id=int(market_id),
            min_qty=Decimal(str(min_qty)),
        )
    return out


# ---- Wire helpers -----------------------------------------------------------


def _next_id() -> str:
    return uuid.uuid4().hex[:12]


_LAST_NONCE = 0


def _next_nonce() -> int:
    """Monotonic uint64-fitting nonce. Microseconds since epoch, but stepped
    forward by at least 1 each call so two requests in the same microsecond
    don't collide on the server side."""
    global _LAST_NONCE
    candidate = int(time.time() * 1_000_000)
    if candidate <= _LAST_NONCE:
        candidate = _LAST_NONCE + 1
    _LAST_NONCE = candidate
    return candidate


def _send_envelope(ws: WebSocket, msg_type: str, env_id: str, payload: dict) -> None:
    frame = {"type": msg_type, "id": env_id, "payload": payload}
    ws.send(json.dumps(frame))


def _recv_response(ws: WebSocket, expected_id: str, timeout_s: float = RECV_TIMEOUT_S) -> dict:
    """Read frames until one matches `expected_id`. Defensive: reply to any
    server-initiated JSON `{type:"ping"}` frames inline, even though the
    current ws-exec server only sends RFC 6455 protocol-level pings (handled
    transparently by the underlying WebSocket library). Raises on timeout."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        ws.settimeout(max(0.1, deadline - time.time()))
        try:
            raw = ws.recv()
        except Exception as exc:  # noqa: BLE001 — surface the timeout cleanly
            raise RuntimeError(f"recv timed out waiting for id={expected_id}: {exc}") from exc
        if not raw:
            continue
        frame = json.loads(raw)
        if frame.get("type") == "ping":
            pong = {"type": "pong"}
            if frame.get("id") is not None:
                pong["id"] = frame["id"]
            ws.send(json.dumps(pong))
            continue
        if frame.get("id") == expected_id:
            return frame
        # Otherwise: unrelated frame (shouldn't happen on a fresh conn), keep waiting.
    raise RuntimeError(f"Timed out waiting for response id={expected_id}")


def _assert_ok(frame: dict, op_label: str) -> dict:
    if not frame.get("ok"):
        err = frame.get("error", {}) or {}
        raise RuntimeError(f"[{op_label}] failed: error={err.get('error')!r} message={err.get('message')!r}")
    return frame.get("payload", {}) or {}


def _payload_dict(model: Any) -> dict:
    """Serialise a generated Pydantic model into the wire shape (camelCase aliases,
    omit None fields)."""
    return model.model_dump(by_alias=True, exclude_none=True, mode="json")


# ---- Flow 1 — Spot createOrder ---------------------------------------------


def flow_spot_create_order(
    ws: WebSocket,
    config: TradingConfig,
    signer: SignatureGenerator,
    market_id: int,
    qty: Decimal,
    client_order_id: int,
) -> str:
    """Place a single LIMIT GTC spot order. Returns the orderId."""
    nonce = _next_nonce()
    deadline = int(time.time()) + GTC_DEADLINE_S

    inputs = signer.encode_inputs_limit_order(
        is_buy=True,
        limit_px=SPOT_LIMIT_PX,
        qty=qty,
    )
    signature = signer.sign_raw_order(
        account_id=config.account_id,
        market_id=market_id,
        exchange_id=config.dex_id,
        counterparty_account_ids=[],
        order_type=int(OrdersGatewayOrderType.LIMIT_ORDER_SPOT),
        inputs=inputs,
        deadline=deadline,
        nonce=nonce,
    )

    req = CreateOrderRequest(
        exchangeId=config.dex_id,
        symbol=SPOT_SYMBOL,
        accountId=config.account_id,
        isBuy=True,
        limitPx=str(SPOT_LIMIT_PX),
        qty=str(qty),
        orderType=OrderType.LIMIT,
        timeInForce=TimeInForce.GTC,
        signature=signature,
        nonce=str(nonce),
        signerWallet=signer.signer_wallet_address,
        expiresAfter=deadline,
        clientOrderId=client_order_id,
    )
    env_id = _next_id()
    _send_envelope(ws, "createOrder", env_id, _payload_dict(req))

    resp = _recv_response(ws, env_id)
    payload = _assert_ok(resp, "spot createOrder")
    order_id = payload.get("orderId")
    if not order_id:
        raise RuntimeError(f"spot createOrder OK but missing orderId in payload: {payload}")
    print(
        f"  [spot] createOrder ✓ ok=true orderId={order_id} "
        f"status={payload.get('status')} clientOrderId={client_order_id}"
    )
    return str(order_id)


# ---- Flow 2 — Spot cancelOrder ---------------------------------------------


def flow_spot_cancel_order(
    ws: WebSocket,
    config: TradingConfig,
    signer: SignatureGenerator,
    market_id: int,
    order_id: str,
    client_order_id: int,
) -> None:
    nonce = _next_nonce()
    deadline = int(time.time()) + SHORT_DEADLINE_S

    signature = signer.sign_cancel_order_spot(
        account_id=config.account_id,
        market_id=market_id,
        order_id=int(order_id),
        client_order_id=client_order_id,
        nonce=nonce,
        deadline=deadline,
    )

    req = CancelOrderRequest(
        orderId=order_id,
        clientOrderId=client_order_id,
        accountId=config.account_id,
        symbol=SPOT_SYMBOL,
        signature=signature,
        nonce=str(nonce),
        expiresAfter=deadline,
    )
    env_id = _next_id()
    _send_envelope(ws, "cancelOrder", env_id, _payload_dict(req))

    resp = _recv_response(ws, env_id)
    payload = _assert_ok(resp, "spot cancelOrder")
    print(f"  [spot] cancelOrder ✓ ok=true status={payload.get('status')} orderId={payload.get('orderId')}")


# ---- Flow 3 — Spot cancelAll -----------------------------------------------


def flow_spot_cancel_all(
    ws: WebSocket,
    config: TradingConfig,
    signer: SignatureGenerator,
    market_id: int,
    qty: Decimal,
    num_orders_to_open: int = 3,
) -> None:
    # Open N spot orders first so cancelAll has something to cancel.
    opened: list[str] = []
    for i in range(num_orders_to_open):
        cl_id = _next_nonce()  # unique per order
        opened.append(flow_spot_create_order(ws, config, signer, market_id, qty, client_order_id=cl_id))
    print(f"  [spot] opened {len(opened)} orders to be cancelled by cancelAll")

    nonce = _next_nonce()
    deadline = int(time.time()) + SHORT_DEADLINE_S

    signature = signer.sign_mass_cancel(
        account_id=config.account_id,
        market_id=market_id,
        nonce=nonce,
        deadline=deadline,
    )

    req = MassCancelRequest(
        accountId=config.account_id,
        symbol=SPOT_SYMBOL,
        signature=signature,
        nonce=str(nonce),
        expiresAfter=deadline,
    )
    env_id = _next_id()
    _send_envelope(ws, "cancelAll", env_id, _payload_dict(req))

    resp = _recv_response(ws, env_id)
    payload = _assert_ok(resp, "spot cancelAll")
    cancelled = payload.get("cancelledCount")
    print(f"  [spot] cancelAll ✓ ok=true cancelledCount={cancelled}")
    if cancelled != num_orders_to_open:
        print(
            f"  [spot] WARN: expected cancelledCount={num_orders_to_open}, got {cancelled} "
            "(may be ok if other orders were open or the server counts differently)"
        )


# ---- Flow 4 — Perp createOrder (IOC) ---------------------------------------


def flow_perp_create_order_ioc(
    ws: WebSocket,
    config: TradingConfig,
    signer: SignatureGenerator,
    market_id: int,
    qty: Decimal,
) -> None:
    """Perp IOC. The order is submitted on-chain via OrdersGateway::execute and
    fills at the current pool price (with `PERP_LIMIT_PX` as a loose buy cap),
    opening a min-size long position. See the module-level comment on
    `PERP_LIMIT_PX` for why we can't reuse the spot $1 no-fill strategy here."""
    timestamp_ms = int(time.time() * 1000)
    nonce = signer.create_orders_gateway_nonce(
        account_id=config.account_id,
        market_id=market_id,
        timestamp_ms=timestamp_ms,
    )
    deadline = int(time.time()) + SHORT_DEADLINE_S

    inputs = signer.encode_inputs_limit_order(
        is_buy=True,
        limit_px=PERP_LIMIT_PX,
        qty=qty,
    )
    signature = signer.sign_raw_order(
        account_id=config.account_id,
        market_id=market_id,
        exchange_id=config.dex_id,
        counterparty_account_ids=[config.pool_account_id],
        order_type=int(OrdersGatewayOrderType.MARKET_ORDER),
        inputs=inputs,
        deadline=deadline,
        nonce=nonce,
    )

    req = CreateOrderRequest(
        exchangeId=config.dex_id,
        symbol=PERP_SYMBOL,
        accountId=config.account_id,
        isBuy=True,
        limitPx=str(PERP_LIMIT_PX),
        qty=str(qty),
        orderType=OrderType.LIMIT,
        timeInForce=TimeInForce.IOC,
        reduceOnly=False,
        signature=signature,
        nonce=str(nonce),
        signerWallet=signer.signer_wallet_address,
        expiresAfter=deadline,
    )
    env_id = _next_id()
    _send_envelope(ws, "createOrder", env_id, _payload_dict(req))

    # Perp IOC settles on-chain — give it a longer ceiling.
    resp = _recv_response(ws, env_id, timeout_s=30.0)
    payload = _assert_ok(resp, "perp createOrder (IOC)")
    print(
        f"  [perp] createOrder (IOC) ✓ ok=true status={payload.get('status')} "
        f"execQty={payload.get('execQty')} orderId={payload.get('orderId')}"
    )


# ---- Bootstrap -------------------------------------------------------------


def main() -> int:
    load_dotenv()

    api_url = os.environ.get("REYA_API_URL", DEFAULT_API_URL)
    print(f"Resolving market IDs from {api_url}")
    markets = _resolve_markets(api_url)
    if SPOT_SYMBOL not in markets:
        raise RuntimeError(f"{SPOT_SYMBOL} not found in {api_url}/spotMarketDefinitions")
    if PERP_SYMBOL not in markets:
        raise RuntimeError(f"{PERP_SYMBOL} not found in {api_url}/marketDefinitions")
    spot_market = markets[SPOT_SYMBOL]
    perp_market = markets[PERP_SYMBOL]
    print(f"  {SPOT_SYMBOL}: marketId={spot_market.market_id} minQty={spot_market.min_qty}")
    print(f"  {PERP_SYMBOL}: marketId={perp_market.market_id} minQty={perp_market.min_qty}")

    ws_exec_url = os.environ.get("REYA_WS_EXEC_URL", DEFAULT_WS_EXEC_URL)
    print(f"Connecting to {ws_exec_url}")
    ws = WebSocket()
    ws.connect(ws_exec_url)

    try:
        # Spot account
        spot_config = TradingConfig.from_env_spot(account_number=1)
        if not spot_config.private_key or spot_config.account_id is None:
            raise RuntimeError("SPOT_PRIVATE_KEY_1 and SPOT_ACCOUNT_ID_1 must be set in .env")
        spot_signer = SignatureGenerator(spot_config)
        print(
            f"Spot account #1 loaded: accountId={spot_config.account_id} " f"signer={spot_signer.signer_wallet_address}"
        )

        # Perp account
        perp_config = TradingConfig.from_env()
        if not perp_config.private_key or perp_config.account_id is None:
            raise RuntimeError("PERP_PRIVATE_KEY_1 and PERP_ACCOUNT_ID_1 must be set in .env")
        perp_signer = SignatureGenerator(perp_config)
        print(
            f"Perp account #1 loaded: accountId={perp_config.account_id} " f"signer={perp_signer.signer_wallet_address}"
        )

        print("\n--- Flow 1: spot createOrder ---")
        spot_order_id = flow_spot_create_order(
            ws,
            spot_config,
            spot_signer,
            market_id=spot_market.market_id,
            qty=spot_market.min_qty,
            client_order_id=_next_nonce(),
        )

        print("\n--- Flow 2: spot cancelOrder ---")
        flow_spot_cancel_order(
            ws,
            spot_config,
            spot_signer,
            market_id=spot_market.market_id,
            order_id=spot_order_id,
            client_order_id=0,
        )

        print("\n--- Flow 3: spot cancelAll ---")
        flow_spot_cancel_all(
            ws,
            spot_config,
            spot_signer,
            market_id=spot_market.market_id,
            qty=spot_market.min_qty,
            num_orders_to_open=3,
        )

        print("\n--- Flow 4: perp createOrder (IOC) ---")
        flow_perp_create_order_ioc(
            ws,
            perp_config,
            perp_signer,
            market_id=perp_market.market_id,
            qty=perp_market.min_qty,
        )

        print("\n✓ all flows passed")
        return 0

    finally:
        try:
            ws.close()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    sys.exit(main())
