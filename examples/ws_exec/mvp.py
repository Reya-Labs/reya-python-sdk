"""ws-exec MVP test script.

End-to-end exercises every supported operation and variant of the ws-exec
WebSocket order-entry service, plus the highest-signal error modes.

Happy paths (11):
  0.  Application ping/pong probe
  1.  Spot createOrder (LIMIT GTC, far-out price — rests)
  2.  Spot cancelOrder by orderId
  3.  Spot createOrder + cancelOrder by clientOrderId (alternative cancel path)
  4.  Spot cancelAll, symbol-scoped (opens N orders, mass-cancels them)
  5.  Spot cancelAll, account-wide (no symbol scope)
  6.  Perp createOrder (LIMIT GTC conditional, rests) + cancel
  7.  Perp createOrder (TP) + cancel
  8.  Perp createOrder (SL) + cancel
  9.  Perp createOrder (LIMIT IOC, opens a small long via the pool)
  10. Perp createOrder (LIMIT IOC, reduceOnly=true, closes the long from #9)

Error paths (6):
  E1. DUPLICATE_REQUEST_ID         (framing) — same `id` in flight twice
  E2. MALFORMED_JSON               (framing) — non-JSON frame
  E3. UNKNOWN_TYPE                 (framing) — `type: "foobar"`
  E4. INVALID_NONCE_ERROR          (per-op) — reused nonce
  E5. ORDER_DEADLINE_PASSED_ERROR  (per-op) — past expiresAfter
  E6. UNAUTHORIZED_SIGNATURE_ERROR (per-op) — signer/signerWallet mismatch

Run with:
    poetry shell
    python -m examples.ws_exec.mvp

Requires .env populated with SPOT_*_1 + PERP_*_1 credentials and CHAIN_ID, and
the configured chain's ws-exec relayer EOA funded with native gas (perp IOC
flows settle on-chain via OrdersGateway::execute).
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
# (qty = market.min_qty = 0.01 ETH ≈ $20-30 of notional). The test proves the
# wire end-to-end; the resulting position is intentional and tiny.
PERP_LIMIT_PX = Decimal("40000")

# Perp LIMIT GTC conditional order — limit price far below the current mark
# so the conditional rests in OrdersGateway indefinitely (we cancel it next).
PERP_GTC_LIMIT_PX = Decimal("100")

# Perp IOC reduce-only sell limit — limit is the worst-acceptable for a sell,
# so $1 means "accept any price >= $1", and the pool fills at the current mark.
# Used by the reduce-only close that unwinds the long opened by the IOC buy.
PERP_REDUCE_ONLY_LIMIT_PX = Decimal("1")

# Trigger prices for TP / SL conditional orders. Set well outside any plausible
# fill range so they never fire during the test window — we just need to verify
# the create/cancel wire works.
PERP_TP_TRIGGER_PX = Decimal("1000000")  # take-profit: never reached upward
PERP_SL_TRIGGER_PX = Decimal("1")  # stop-loss:   never reached downward

DEFAULT_WS_EXEC_URL = "wss://ws-exec-testnet.reya.xyz"
DEFAULT_API_URL = "https://api-cronos.reya.xyz/v2"
GTC_DEADLINE_S = 86_400  # 24h for the resting spot GTC
SHORT_DEADLINE_S = 60  # 60s for spot cancel + perp IOC
RECV_TIMEOUT_S = 15.0

# Sentinel deadline used in the EIP-712 signature for ALL perp conditional
# orders (LIMIT GTC / TP / SL). The on-chain conditional doesn't carry a
# real expiry; the wire payload omits `expiresAfter` and the server
# reconstructs the typed-data hash with this exact constant. Mirrors
# CONDITIONAL_ORDER_DEADLINE in sdk/reya_rest_api/client.py.
CONDITIONAL_ORDER_DEADLINE = 10**18


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


def _assert_error(frame: dict, expected_code: str | tuple[str, ...], op_label: str) -> dict:
    """Symmetric to _assert_ok for negative tests. Asserts the response is a
    per-operation error (`ok=false`) with `error.error` matching `expected_code`.
    `expected_code` may be a tuple to accept any-of (e.g. when the server-side
    validator chain can surface different codes for the same root cause)."""
    if frame.get("ok"):
        raise RuntimeError(f"[{op_label}] expected error {expected_code!r}, got ok=true payload={frame.get('payload')!r}")
    err = frame.get("error", {}) or {}
    actual = err.get("error")
    accepted = (expected_code,) if isinstance(expected_code, str) else expected_code
    if actual not in accepted:
        raise RuntimeError(f"[{op_label}] expected one of {accepted!r}, got error={actual!r} message={err.get('message')!r}")
    return err


def _send_raw(ws: WebSocket, raw: str) -> None:
    """Send a raw string over the socket without any JSON encoding. Used for the
    MALFORMED_JSON framing-error test where we deliberately send non-JSON."""
    ws.send(raw)


def _recv_top_level_error(ws: WebSocket, expected_code: str, op_label: str, timeout_s: float = RECV_TIMEOUT_S) -> dict:
    """Read frames until one is a top-level `{type: "error"}` envelope with the
    expected framing-layer error code. Used for MALFORMED_JSON / UNKNOWN_TYPE /
    DUPLICATE_REQUEST_ID — these don't carry per-op response shape (no `ok`)
    and may or may not echo an `id`."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        ws.settimeout(max(0.1, deadline - time.time()))
        try:
            raw = ws.recv()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"recv timed out waiting for top-level error {expected_code!r}: {exc}") from exc
        if not raw:
            continue
        frame = json.loads(raw)
        if frame.get("type") == "ping":
            pong = {"type": "pong"}
            if frame.get("id") is not None:
                pong["id"] = frame["id"]
            ws.send(json.dumps(pong))
            continue
        if frame.get("type") != "error":
            # Unrelated frame (e.g. a delayed per-op response). Keep waiting.
            continue
        err = frame.get("error", {}) or {}
        actual = err.get("error")
        if actual != expected_code:
            raise RuntimeError(f"[{op_label}] expected top-level error {expected_code!r}, got {actual!r} message={err.get('message')!r}")
        return err
    raise RuntimeError(f"[{op_label}] timed out waiting for top-level error {expected_code!r}")


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


# ---- Flow 0 — Application ping/pong ----------------------------------------


def flow_app_ping_pong(ws: WebSocket) -> None:
    """Send a client-initiated `{type:"ping", id}` and assert the server replies
    with `{type:"pong", id}` echoing our id. Pure JSON-layer probe; doesn't
    consume any in-flight request slot (ping is not in REQUEST_TYPES on the
    server side)."""
    env_id = _next_id()
    ws.send(json.dumps({"type": "ping", "id": env_id}))

    # Use a fresh recv loop here rather than _recv_response because pong frames
    # don't carry an `ok` field and the existing helper is keyed off it.
    deadline = time.time() + RECV_TIMEOUT_S
    while time.time() < deadline:
        ws.settimeout(max(0.1, deadline - time.time()))
        try:
            raw = ws.recv()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"timed out waiting for pong id={env_id}: {exc}") from exc
        if not raw:
            continue
        frame = json.loads(raw)
        if frame.get("type") == "pong" and frame.get("id") == env_id:
            print(f"  [ping] pong ✓ id={env_id} echoed")
            return
        # Tolerate unrelated frames if any.
    raise RuntimeError(f"no pong received for id={env_id}")


# ---- Spot cancel-by-clientOrderId ------------------------------------------


def flow_spot_cancel_by_client_order_id(
    ws: WebSocket,
    config: TradingConfig,
    signer: SignatureGenerator,
    market_id: int,
    qty: Decimal,
) -> None:
    """Create a spot order with an explicit clientOrderId, then cancel it using
    ONLY the clientOrderId (orderId omitted from the request). Exercises the
    alternative cancel path enabled by the spot OrderCancel EIP-712 schema."""
    cl_id = _next_nonce()
    order_id = flow_spot_create_order(ws, config, signer, market_id, qty, client_order_id=cl_id)
    print(f"  [spot] created order with clientOrderId={cl_id} (orderId={order_id})")

    nonce = _next_nonce()
    deadline = int(time.time()) + SHORT_DEADLINE_S

    # When cancelling by clientOrderId, the signed EIP-712 message uses orderId=0
    # because the request payload won't carry an orderId field. Server reconstructs
    # the typed-data hash from the request and verifies signature recovery.
    signature = signer.sign_cancel_order_spot(
        account_id=config.account_id,
        market_id=market_id,
        order_id=0,
        client_order_id=cl_id,
        nonce=nonce,
        deadline=deadline,
    )

    req = CancelOrderRequest(
        clientOrderId=cl_id,
        accountId=config.account_id,
        symbol=SPOT_SYMBOL,
        signature=signature,
        nonce=str(nonce),
        expiresAfter=deadline,
    )
    env_id = _next_id()
    _send_envelope(ws, "cancelOrder", env_id, _payload_dict(req))

    resp = _recv_response(ws, env_id)
    payload = _assert_ok(resp, "spot cancelOrder by clientOrderId")
    print(
        f"  [spot] cancelOrder (by clientOrderId) ✓ ok=true "
        f"status={payload.get('status')} orderId={payload.get('orderId')}"
    )


# ---- Spot cancelAll, account-wide (no symbol scope) ------------------------


def flow_spot_cancel_all_account_wide(
    ws: WebSocket,
    config: TradingConfig,
    signer: SignatureGenerator,
    market_id: int,
    qty: Decimal,
    num_orders_to_open: int = 2,
) -> None:
    """Open N spot orders, then issue cancelAll WITHOUT a `symbol` field. The
    server-side branch (validateCancelAllV2) treats missing symbol as
    'cancel across all spot markets for this account'."""
    opened: list[str] = []
    for _ in range(num_orders_to_open):
        cl_id = _next_nonce()
        opened.append(flow_spot_create_order(ws, config, signer, market_id, qty, client_order_id=cl_id))
    print(f"  [spot] opened {len(opened)} orders for account-wide cancelAll")

    nonce = _next_nonce()
    deadline = int(time.time()) + SHORT_DEADLINE_S

    # sign_mass_cancel signs the typed-data with the symbol's market_id baked in.
    # When the request omits `symbol`, the server reconstructs typed data with
    # marketId=0 (account-wide). We mirror that on the signing side.
    signature = signer.sign_mass_cancel(
        account_id=config.account_id,
        market_id=0,
        nonce=nonce,
        deadline=deadline,
    )

    req = MassCancelRequest(
        accountId=config.account_id,
        # No `symbol` — account-wide branch.
        signature=signature,
        nonce=str(nonce),
        expiresAfter=deadline,
    )
    env_id = _next_id()
    _send_envelope(ws, "cancelAll", env_id, _payload_dict(req))

    resp = _recv_response(ws, env_id)
    payload = _assert_ok(resp, "spot cancelAll (account-wide)")
    cancelled = payload.get("cancelledCount")
    print(f"  [spot] cancelAll (account-wide) ✓ ok=true cancelledCount={cancelled}")
    if cancelled != num_orders_to_open:
        print(
            f"  [spot] WARN: expected cancelledCount={num_orders_to_open}, got {cancelled} "
            "(may be ok if other orders were open or the server counts differently)"
        )


# ---- Perp LIMIT GTC conditional create + cancel ----------------------------


def flow_perp_create_limit_gtc(
    ws: WebSocket,
    config: TradingConfig,
    signer: SignatureGenerator,
    market_id: int,
    qty: Decimal,
) -> str:
    """Create a perp LIMIT GTC conditional order. Far-out limit ($100, well below
    ~$2k mark) ensures it rests indefinitely on OrdersGateway until cancelled.
    Returns the orderId for the follow-up cancel."""
    timestamp_ms = int(time.time() * 1000)
    nonce = signer.create_orders_gateway_nonce(
        account_id=config.account_id,
        market_id=market_id,
        timestamp_ms=timestamp_ms,
    )
    # Perp GTC conditionals don't carry expiresAfter at the wire level
    # (validation rejects it). The signature uses the sentinel
    # CONDITIONAL_ORDER_DEADLINE so server-side typed-data reconstruction
    # matches.
    inputs = signer.encode_inputs_limit_order(
        is_buy=True,
        limit_px=PERP_GTC_LIMIT_PX,
        qty=qty,
    )
    signature = signer.sign_raw_order(
        account_id=config.account_id,
        market_id=market_id,
        exchange_id=config.dex_id,
        counterparty_account_ids=[config.pool_account_id],
        order_type=int(OrdersGatewayOrderType.LIMIT_ORDER),
        inputs=inputs,
        deadline=CONDITIONAL_ORDER_DEADLINE,
        nonce=nonce,
    )

    req = CreateOrderRequest(
        exchangeId=config.dex_id,
        symbol=PERP_SYMBOL,
        accountId=config.account_id,
        isBuy=True,
        limitPx=str(PERP_GTC_LIMIT_PX),
        qty=str(qty),
        orderType=OrderType.LIMIT,
        timeInForce=TimeInForce.GTC,
        signature=signature,
        nonce=str(nonce),
        signerWallet=signer.signer_wallet_address,
    )
    env_id = _next_id()
    _send_envelope(ws, "createOrder", env_id, _payload_dict(req))

    resp = _recv_response(ws, env_id)
    payload = _assert_ok(resp, "perp createOrder (LIMIT GTC)")
    order_id = payload.get("orderId")
    if not order_id:
        raise RuntimeError(f"perp LIMIT GTC OK but missing orderId in payload: {payload}")
    print(
        f"  [perp] createOrder (LIMIT GTC) ✓ ok=true orderId={order_id} "
        f"status={payload.get('status')}"
    )
    return str(order_id)


def flow_perp_cancel_order(
    ws: WebSocket,
    signer: SignatureGenerator,
    order_id: str,
) -> None:
    """Cancel a perp resting order by orderId. Perp cancel uses EIP-191
    personal_sign (different from spot's EIP-712 OrderCancel) and the request
    carries just {orderId, signature} — no accountId/nonce/expiresAfter."""
    signature = signer.sign_cancel_order_perps(order_id=order_id)

    req = CancelOrderRequest(
        orderId=order_id,
        symbol=PERP_SYMBOL,
        signature=signature,
    )
    env_id = _next_id()
    _send_envelope(ws, "cancelOrder", env_id, _payload_dict(req))

    resp = _recv_response(ws, env_id)
    payload = _assert_ok(resp, "perp cancelOrder")
    print(f"  [perp] cancelOrder ✓ ok=true status={payload.get('status')} orderId={payload.get('orderId')}")


# ---- Perp TP / SL conditional create + cancel ------------------------------


def _flow_perp_create_trigger(
    ws: WebSocket,
    config: TradingConfig,
    signer: SignatureGenerator,
    market_id: int,
    order_type_enum: OrderType,
    gateway_order_type: OrdersGatewayOrderType,
    trigger_px: Decimal,
    label: str,
) -> str:
    """Shared body for TP / SL conditional creates. TP and SL only differ in
    `orderType` (TP vs SL) and the on-chain `OrdersGatewayOrderType` int. Both
    use `encode_inputs_trigger_order`, both omit qty / timeInForce / reduceOnly,
    and both carry `triggerPx` in the request."""
    timestamp_ms = int(time.time() * 1000)
    nonce = signer.create_orders_gateway_nonce(
        account_id=config.account_id,
        market_id=market_id,
        timestamp_ms=timestamp_ms,
    )

    # TP/SL trigger orders use a separate input encoding (bool isBuy + trigger + limit).
    # `limit_px` here is the worst-acceptable execution price once the trigger fires —
    # we set it equal to the trigger to make the order a "stop-market" equivalent.
    inputs = signer.encode_inputs_trigger_order(
        is_buy=False,  # take-profit / stop-loss on a long position is a SELL
        trigger_px=trigger_px,
        limit_px=trigger_px,
    )
    # Conditional orders (TP/SL/perp-GTC) all use the sentinel deadline.
    signature = signer.sign_raw_order(
        account_id=config.account_id,
        market_id=market_id,
        exchange_id=config.dex_id,
        counterparty_account_ids=[config.pool_account_id],
        order_type=int(gateway_order_type),
        inputs=inputs,
        deadline=CONDITIONAL_ORDER_DEADLINE,
        nonce=nonce,
    )

    req = CreateOrderRequest(
        exchangeId=config.dex_id,
        symbol=PERP_SYMBOL,
        accountId=config.account_id,
        isBuy=False,
        limitPx=str(trigger_px),
        orderType=order_type_enum,
        triggerPx=str(trigger_px),
        signature=signature,
        nonce=str(nonce),
        signerWallet=signer.signer_wallet_address,
    )
    env_id = _next_id()
    _send_envelope(ws, "createOrder", env_id, _payload_dict(req))

    resp = _recv_response(ws, env_id)
    payload = _assert_ok(resp, f"perp createOrder ({label})")
    order_id = payload.get("orderId")
    if not order_id:
        raise RuntimeError(f"perp {label} OK but missing orderId in payload: {payload}")
    print(
        f"  [perp] createOrder ({label}) ✓ ok=true orderId={order_id} "
        f"status={payload.get('status')} triggerPx={trigger_px}"
    )
    return str(order_id)


def flow_perp_create_tp(
    ws: WebSocket,
    config: TradingConfig,
    signer: SignatureGenerator,
    market_id: int,
) -> str:
    return _flow_perp_create_trigger(
        ws, config, signer, market_id,
        order_type_enum=OrderType.TP,
        gateway_order_type=OrdersGatewayOrderType.TAKE_PROFIT,
        trigger_px=PERP_TP_TRIGGER_PX,
        label="TP",
    )


def flow_perp_create_sl(
    ws: WebSocket,
    config: TradingConfig,
    signer: SignatureGenerator,
    market_id: int,
) -> str:
    return _flow_perp_create_trigger(
        ws, config, signer, market_id,
        order_type_enum=OrderType.SL,
        gateway_order_type=OrdersGatewayOrderType.STOP_LOSS,
        trigger_px=PERP_SL_TRIGGER_PX,
        label="SL",
    )


# ---- Perp IOC reduceOnly sell (closes the long from flow_perp_create_order_ioc) ----


def flow_perp_create_order_ioc_reduce_only_close(
    ws: WebSocket,
    config: TradingConfig,
    signer: SignatureGenerator,
    market_id: int,
    qty: Decimal,
) -> None:
    """Perp IOC sell with reduceOnly=true, sized to the previous IOC buy. Closes
    the long opened by flow_perp_create_order_ioc, leaving the perp account flat.
    Uses OrdersGatewayOrderType.REDUCE_ONLY_MARKET_ORDER on the on-chain side and
    sets `reduceOnly=true` on the wire so the server-side validator accepts it."""
    timestamp_ms = int(time.time() * 1000)
    nonce = signer.create_orders_gateway_nonce(
        account_id=config.account_id,
        market_id=market_id,
        timestamp_ms=timestamp_ms,
    )
    deadline = int(time.time()) + SHORT_DEADLINE_S

    inputs = signer.encode_inputs_limit_order(
        is_buy=False,
        limit_px=PERP_REDUCE_ONLY_LIMIT_PX,
        qty=qty,
    )
    signature = signer.sign_raw_order(
        account_id=config.account_id,
        market_id=market_id,
        exchange_id=config.dex_id,
        counterparty_account_ids=[config.pool_account_id],
        order_type=int(OrdersGatewayOrderType.REDUCE_ONLY_MARKET_ORDER),
        inputs=inputs,
        deadline=deadline,
        nonce=nonce,
    )

    req = CreateOrderRequest(
        exchangeId=config.dex_id,
        symbol=PERP_SYMBOL,
        accountId=config.account_id,
        isBuy=False,
        limitPx=str(PERP_REDUCE_ONLY_LIMIT_PX),
        qty=str(qty),
        orderType=OrderType.LIMIT,
        timeInForce=TimeInForce.IOC,
        reduceOnly=True,
        signature=signature,
        nonce=str(nonce),
        signerWallet=signer.signer_wallet_address,
        expiresAfter=deadline,
    )
    env_id = _next_id()
    _send_envelope(ws, "createOrder", env_id, _payload_dict(req))

    resp = _recv_response(ws, env_id, timeout_s=30.0)
    payload = _assert_ok(resp, "perp createOrder (IOC reduceOnly close)")
    print(
        f"  [perp] createOrder (IOC reduceOnly close) ✓ ok=true status={payload.get('status')} "
        f"execQty={payload.get('execQty')} orderId={payload.get('orderId')}"
    )


# ---- Error flows -----------------------------------------------------------


def flow_err_duplicate_request_id(
    ws: WebSocket,
    config: TradingConfig,
    signer: SignatureGenerator,
    market_id: int,
    qty: Decimal,
) -> None:
    """Send two well-formed createOrder frames back-to-back with the same `id`.
    The second one trips the per-connection in-flight `id` uniqueness check and
    surfaces as a top-level `{type:"error", error.error=DUPLICATE_REQUEST_ID}`.
    The first request continues to be processed normally; we wait for and
    discard its response after collecting the error."""
    shared_env_id = _next_id()

    # Build two payloads with DIFFERENT nonces (so they're both individually valid),
    # but reuse the same envelope `id` to trigger the duplicate check.
    def build_payload() -> dict:
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
        )
        return _payload_dict(req)

    _send_envelope(ws, "createOrder", shared_env_id, build_payload())
    _send_envelope(ws, "createOrder", shared_env_id, build_payload())

    # The duplicate-id error fires as a top-level error envelope. The first
    # request keeps processing and lands as a normal createOrder response.
    _recv_top_level_error(ws, "DUPLICATE_REQUEST_ID", "duplicate request id")
    print("  [err] DUPLICATE_REQUEST_ID ✓ received as expected")

    # Wait for the first request's response and cancel the resulting order so
    # we don't leak state. The response should be ok=true with an orderId.
    first_resp = _recv_response(ws, shared_env_id, timeout_s=RECV_TIMEOUT_S)
    payload = _assert_ok(first_resp, "duplicate-id first-request follow-up")
    leaked_order_id = payload.get("orderId")
    if leaked_order_id:
        flow_spot_cancel_order(
            ws, config, signer,
            market_id=market_id,
            order_id=str(leaked_order_id),
            client_order_id=0,
        )


def flow_err_malformed_json(ws: WebSocket) -> None:
    """Send a non-JSON string. Server returns MALFORMED_JSON at the framing layer."""
    _send_raw(ws, "this-is-not-json")
    _recv_top_level_error(ws, "MALFORMED_JSON", "malformed json")
    print("  [err] MALFORMED_JSON ✓ received as expected")


def flow_err_unknown_type(ws: WebSocket) -> None:
    """Send a frame with an unknown `type`. Server returns UNKNOWN_TYPE."""
    env_id = _next_id()
    ws.send(json.dumps({"type": "foobar", "id": env_id, "payload": {}}))
    _recv_top_level_error(ws, "UNKNOWN_TYPE", "unknown type")
    print("  [err] UNKNOWN_TYPE ✓ received as expected")


def flow_err_invalid_nonce(
    ws: WebSocket,
    config: TradingConfig,
    signer: SignatureGenerator,
    market_id: int,
    qty: Decimal,
) -> None:
    """Replay a nonce: submit a createOrder twice with the SAME nonce. The first
    succeeds (and we cancel it inline so we don't leak state); the second is
    rejected by the nonce-uniqueness check as INVALID_NONCE_ERROR."""
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
    req_kwargs = dict(
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
    )

    # First submission — should succeed.
    env_id_1 = _next_id()
    _send_envelope(ws, "createOrder", env_id_1, _payload_dict(CreateOrderRequest(**req_kwargs)))
    resp1 = _recv_response(ws, env_id_1)
    payload1 = _assert_ok(resp1, "invalid-nonce setup (first submit)")
    leaked_order_id = payload1.get("orderId")

    # Second submission with the SAME nonce — should fail with INVALID_NONCE_ERROR.
    env_id_2 = _next_id()
    _send_envelope(ws, "createOrder", env_id_2, _payload_dict(CreateOrderRequest(**req_kwargs)))
    resp2 = _recv_response(ws, env_id_2)
    _assert_error(resp2, "INVALID_NONCE_ERROR", "invalid nonce replay")
    print("  [err] INVALID_NONCE_ERROR ✓ received as expected")

    # Cleanup the resting order from the first (successful) submission.
    if leaked_order_id:
        flow_spot_cancel_order(
            ws, config, signer,
            market_id=market_id,
            order_id=str(leaked_order_id),
            client_order_id=0,
        )


def flow_err_order_deadline_passed(
    ws: WebSocket,
    config: TradingConfig,
    signer: SignatureGenerator,
    market_id: int,
    qty: Decimal,
) -> None:
    """Submit a createOrder with `expiresAfter` in the past. Validation rejects
    with ORDER_DEADLINE_PASSED_ERROR."""
    nonce = _next_nonce()
    deadline = int(time.time()) - 60  # 60s in the past

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
    )
    env_id = _next_id()
    _send_envelope(ws, "createOrder", env_id, _payload_dict(req))
    resp = _recv_response(ws, env_id)
    # The validator chain catches a past `expiresAfter` at the input-validation
    # stage on most servers (INPUT_VALIDATION_ERROR with message
    # "expiresAfter must be a future timestamp"), while the dedicated
    # ORDER_DEADLINE_PASSED_ERROR fires when the deadline passes between
    # request acceptance and on-chain settlement. Either is a legitimate
    # rejection of an expired deadline.
    err = _assert_error(resp, ("ORDER_DEADLINE_PASSED_ERROR", "INPUT_VALIDATION_ERROR"), "expired deadline")
    print(f"  [err] expired deadline rejected ✓ code={err.get('error')!r}")


def flow_err_unauthorized_signature(
    ws: WebSocket,
    config: TradingConfig,
    spot_signer: SignatureGenerator,
    other_signer: SignatureGenerator,
    market_id: int,
    qty: Decimal,
) -> None:
    """Sign the payload with the spot account's key but declare the OTHER wallet
    as `signerWallet`. Server recovers the spot key's address from the signature,
    sees it doesn't match `signerWallet`, and rejects with
    UNAUTHORIZED_SIGNATURE_ERROR."""
    nonce = _next_nonce()
    deadline = int(time.time()) + GTC_DEADLINE_S

    inputs = spot_signer.encode_inputs_limit_order(
        is_buy=True,
        limit_px=SPOT_LIMIT_PX,
        qty=qty,
    )
    # Sign with spot signer's key …
    signature = spot_signer.sign_raw_order(
        account_id=config.account_id,
        market_id=market_id,
        exchange_id=config.dex_id,
        counterparty_account_ids=[],
        order_type=int(OrdersGatewayOrderType.LIMIT_ORDER_SPOT),
        inputs=inputs,
        deadline=deadline,
        nonce=nonce,
    )
    # … but claim the OTHER signer's wallet address. Server will recover the
    # spot signer's address and reject for mismatch.
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
        signerWallet=other_signer.signer_wallet_address,
        expiresAfter=deadline,
    )
    env_id = _next_id()
    _send_envelope(ws, "createOrder", env_id, _payload_dict(req))
    resp = _recv_response(ws, env_id)
    # The recovered signer is ALMOST CERTAINLY some unrelated address (because
    # the server's typed-data reconstruction uses our declared `signerWallet`,
    # which differs from what we actually signed with). Two codes are
    # legitimate rejections here, depending on which validator catches it:
    #   - UNAUTHORIZED_SIGNATURE_ERROR — recovered signer not permissioned
    #     for the account
    #   - CREATE_ORDER_OTHER_ERROR     — generic "Invalid signature" path
    # We accept either as confirmation that mismatched signing is rejected.
    err = _assert_error(resp, ("UNAUTHORIZED_SIGNATURE_ERROR", "CREATE_ORDER_OTHER_ERROR"), "signer mismatch")
    print(f"  [err] signer mismatch rejected ✓ code={err.get('error')!r} message={err.get('message')!r}")


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

        # Spot account #2 — only used as a SECOND signer for the
        # UNAUTHORIZED_SIGNATURE_ERROR error flow (we sign with one key but
        # declare the other wallet, so the server's recovery-vs-declared check
        # rejects). If SPOT_*_2 isn't set, that single error flow is skipped.
        spot_config_2: TradingConfig | None = None
        spot_signer_2: SignatureGenerator | None = None
        try:
            candidate = TradingConfig.from_env_spot(account_number=2)
            if candidate.private_key and candidate.account_id is not None:
                spot_config_2 = candidate
                spot_signer_2 = SignatureGenerator(candidate)
                print(
                    f"Spot account #2 loaded: accountId={candidate.account_id} "
                    f"signer={spot_signer_2.signer_wallet_address} (used for signer-mismatch test)"
                )
        except Exception:  # noqa: BLE001
            pass

        print("\n--- Flow 0: application ping/pong ---")
        flow_app_ping_pong(ws)

        print("\n--- Flow 1: spot createOrder (LIMIT GTC) ---")
        spot_order_id = flow_spot_create_order(
            ws,
            spot_config,
            spot_signer,
            market_id=spot_market.market_id,
            qty=spot_market.min_qty,
            client_order_id=_next_nonce(),
        )

        print("\n--- Flow 2: spot cancelOrder by orderId ---")
        flow_spot_cancel_order(
            ws,
            spot_config,
            spot_signer,
            market_id=spot_market.market_id,
            order_id=spot_order_id,
            client_order_id=0,
        )

        print("\n--- Flow 3: spot create + cancel by clientOrderId ---")
        flow_spot_cancel_by_client_order_id(
            ws,
            spot_config,
            spot_signer,
            market_id=spot_market.market_id,
            qty=spot_market.min_qty,
        )

        print("\n--- Flow 4: spot cancelAll (symbol-scoped) ---")
        flow_spot_cancel_all(
            ws,
            spot_config,
            spot_signer,
            market_id=spot_market.market_id,
            qty=spot_market.min_qty,
            num_orders_to_open=3,
        )

        print("\n--- Flow 5: spot cancelAll (account-wide) ---")
        flow_spot_cancel_all_account_wide(
            ws,
            spot_config,
            spot_signer,
            market_id=spot_market.market_id,
            qty=spot_market.min_qty,
            num_orders_to_open=2,
        )

        print("\n--- Flow 6: perp createOrder (LIMIT GTC) + cancel ---")
        perp_gtc_order_id = flow_perp_create_limit_gtc(
            ws,
            perp_config,
            perp_signer,
            market_id=perp_market.market_id,
            qty=perp_market.min_qty,
        )
        flow_perp_cancel_order(ws, perp_signer, order_id=perp_gtc_order_id)

        print("\n--- Flow 7: perp createOrder (TP) + cancel ---")
        perp_tp_order_id = flow_perp_create_tp(
            ws,
            perp_config,
            perp_signer,
            market_id=perp_market.market_id,
        )
        flow_perp_cancel_order(ws, perp_signer, order_id=perp_tp_order_id)

        print("\n--- Flow 8: perp createOrder (SL) + cancel ---")
        perp_sl_order_id = flow_perp_create_sl(
            ws,
            perp_config,
            perp_signer,
            market_id=perp_market.market_id,
        )
        flow_perp_cancel_order(ws, perp_signer, order_id=perp_sl_order_id)

        print("\n--- Flow 9: perp createOrder (IOC) — opens long ---")
        flow_perp_create_order_ioc(
            ws,
            perp_config,
            perp_signer,
            market_id=perp_market.market_id,
            qty=perp_market.min_qty,
        )

        print("\n--- Flow 10: perp createOrder (IOC, reduceOnly) — closes long ---")
        flow_perp_create_order_ioc_reduce_only_close(
            ws,
            perp_config,
            perp_signer,
            market_id=perp_market.market_id,
            qty=perp_market.min_qty,
        )

        print("\n=== Error flows ===")

        print("\n--- Flow E1: DUPLICATE_REQUEST_ID ---")
        flow_err_duplicate_request_id(
            ws,
            spot_config,
            spot_signer,
            market_id=spot_market.market_id,
            qty=spot_market.min_qty,
        )

        print("\n--- Flow E2: MALFORMED_JSON ---")
        flow_err_malformed_json(ws)

        print("\n--- Flow E3: UNKNOWN_TYPE ---")
        flow_err_unknown_type(ws)

        print("\n--- Flow E4: INVALID_NONCE_ERROR ---")
        flow_err_invalid_nonce(
            ws,
            spot_config,
            spot_signer,
            market_id=spot_market.market_id,
            qty=spot_market.min_qty,
        )

        print("\n--- Flow E5: ORDER_DEADLINE_PASSED_ERROR ---")
        flow_err_order_deadline_passed(
            ws,
            spot_config,
            spot_signer,
            market_id=spot_market.market_id,
            qty=spot_market.min_qty,
        )

        if spot_signer_2 is not None:
            print("\n--- Flow E6: UNAUTHORIZED_SIGNATURE_ERROR ---")
            flow_err_unauthorized_signature(
                ws,
                spot_config,
                spot_signer,
                spot_signer_2,
                market_id=spot_market.market_id,
                qty=spot_market.min_qty,
            )
        else:
            print("\n--- Flow E6: UNAUTHORIZED_SIGNATURE_ERROR (skipped: SPOT_*_2 not set in .env) ---")

        print("\n✓ all flows passed")
        return 0

    finally:
        try:
            ws.close()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    sys.exit(main())
