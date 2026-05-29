"""ws-exec end-to-end tests (live, devnet-gated).

Exercises every supported operation and variant of the ws-exec WebSocket
order-entry service, plus the highest-signal error modes, against a live
deployment. These are *integration* tests: the whole module is skipped
unless `REYA_WS_EXEC_URL` (+ SPOT_*_1 / PERP_*_1 credentials) is set, so a
normal `pytest` run on a machine without ws-exec access collects-and-skips
cleanly. Point `REYA_WS_EXEC_URL` at the target relayer to run them, e.g.
`REYA_WS_EXEC_URL=wss://ws-exec-devnet.reya-cronos.network`.

Happy paths (11) — all driven via :class:`ReyaWsExecClient`:
    0. Application ping/pong probe
    1. Spot createOrder (LIMIT GTC, far-out price - rests)
    2. Spot cancelOrder by orderId
    3. Spot createOrder + cancelOrder by clientOrderId  -- xfail, see PRO-143
    4. Spot cancelAll, symbol-scoped (opens N orders, mass-cancels them)
    5. Spot cancelAll, account-wide (no symbol scope)
    6. Perp createOrder (LIMIT GTC conditional, rests) + cancel
    7. Perp createOrder (TP) + cancel
    8. Perp createOrder (SL) + cancel
    9/10. Perp IOC open long + reduce-only close (paired in one test so the
          position is always unwound even if an assertion fails).

Error paths (6) -- driven on a separate raw WebSocket so the harness can send
intentionally-malformed payloads without racing the high-level client's
in-flight dispatch map:
    E1. DUPLICATE_REQUEST_ID         (framing) -- same `id` in flight twice
    E2. MALFORMED_JSON               (framing) -- non-JSON frame
    E3. UNKNOWN_TYPE                 (framing) -- `type: "foobar"`
    E4. INVALID_NONCE_ERROR          (per-op) -- reused nonce
    E5. ORDER_DEADLINE_PASSED_ERROR  (per-op) -- past expiresAfter
    E6. UNAUTHORIZED_SIGNATURE_ERROR (per-op) -- signer/signerWallet mismatch

E5/E6 build their payloads via the unified `build_create_limit_order_payload`
(then tweak the deadline / signerWallet) — the legacy `sign_raw_order` /
`encode_inputs_limit_order` signing surface was removed in the 2.3.x unified
migration.

Requires the configured chain's ws-exec relayer EOA funded with native gas
(perp IOC flows settle on-chain via OrdersGateway::execute).
"""

from __future__ import annotations

import json
import os
import ssl
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal

import pytest
import pytest_asyncio
from dotenv import load_dotenv
from websocket import WebSocket, create_connection  # type: ignore[attr-defined]  # pylint: disable=no-name-in-module

from sdk.open_api.models import TimeInForce
from sdk.open_api.models.order_type import OrderType
from sdk.reya_rest_api import ReyaTradingClient
from sdk.reya_rest_api.config import TradingConfig
from sdk.reya_rest_api.models.orders import LimitOrderParameters, TriggerOrderParameters
from sdk.reya_ws_exec import ReyaWsExecClient

load_dotenv()

# Live ws-exec integration tests are gated on a relayer URL + signing creds.
# Without them the whole module collects-and-skips so CI stays green on
# machines that can't reach a ws-exec deployment.
_REQUIRED_ENV = (
    "REYA_WS_EXEC_URL",
    "PERP_PRIVATE_KEY_1",
    "PERP_ACCOUNT_ID_1",
    "SPOT_PRIVATE_KEY_1",
    "SPOT_ACCOUNT_ID_1",
)
_MISSING_ENV = [_k for _k in _REQUIRED_ENV if not os.environ.get(_k)]

pytestmark = pytest.mark.skipif(
    bool(_MISSING_ENV),
    reason=(
        "ws-exec live tests need " + ", ".join(_REQUIRED_ENV) + " in the environment "
        "(set REYA_WS_EXEC_URL + SPOT_*_1/PERP_*_1 to run); missing: " + ", ".join(_MISSING_ENV)
    ),
)

# ---- Test parameters --------------------------------------------------------

SPOT_SYMBOL = "WETHRUSD"
PERP_SYMBOL = "ETHRUSDPERP"

# Spot uses a far-out limit ($1) on an ETH-priced book so the GTC just rests
# until we explicitly cancel it. Reya's spot matching engine doesn't enforce a
# distance-from-mark cap, so the order sits safely below any real seller.
SPOT_LIMIT_PX = "1"

# Perp IOC has different semantics: it matches against the pool, and the
# on-chain `Prices.sol::checkPriceLimit` reverts with `UnacceptableOrderPrice`
# if the executed price exceeds the limit (for a buy) or falls below it (for a
# sell). A $1 buy would never fill -- it would revert on-chain. A $40k buy
# limit fills at the current mark (~$2-3k), opening a min-size long position
# (qty = market.min_qty = 0.01 ETH).
PERP_LIMIT_PX = "40000"

# Perp LIMIT GTC conditional order -- limit price far below the current mark
# so the conditional rests in OrdersGateway indefinitely (we cancel it next).
PERP_GTC_LIMIT_PX = "100"

# Perp IOC reduce-only sell limit -- $1 means "accept any price >= $1", and
# the pool fills at the current mark. Used by the reduce-only close that
# unwinds the long opened by the IOC buy.
PERP_REDUCE_ONLY_LIMIT_PX = "1"

# Trigger prices for TP / SL conditional orders. Set well outside any plausible
# fill range so they never fire during the test window.
PERP_TP_TRIGGER_PX = "1000000"
PERP_SL_TRIGGER_PX = "1"

DEFAULT_WS_EXEC_URL = "wss://ws-exec-testnet.reya.xyz"
RECV_TIMEOUT_S = 15.0


# ---- Happy-path flows (via the high-level client) ---------------------------


async def flow_ping(client: ReyaWsExecClient) -> None:
    """Application ping/pong probe. JSON-layer, not the RFC 6455 protocol-level
    ping/pong (which the underlying library handles transparently)."""
    await client.ping()
    print("  [ping] pong received")


async def flow_spot_create_order(
    client: ReyaWsExecClient,
    qty: Decimal,
    client_order_id: int | None = None,
) -> str:
    """Place a single LIMIT GTC spot order. Returns the orderId."""
    resp = await client.create_limit_order(
        LimitOrderParameters(
            symbol=SPOT_SYMBOL,
            is_buy=True,
            limit_px=SPOT_LIMIT_PX,
            qty=str(qty),
            time_in_force=TimeInForce.GTC,
            client_order_id=client_order_id,
        )
    )
    if resp.order_id is None:
        raise RuntimeError(f"spot createOrder OK but missing orderId: {resp}")
    print(f"  [spot] createOrder OK orderId={resp.order_id} " f"status={resp.status} clientOrderId={client_order_id}")
    return resp.order_id


async def flow_spot_cancel_order(client: ReyaWsExecClient, order_id: str) -> None:
    resp = await client.cancel_order(
        order_id=order_id,
        symbol=SPOT_SYMBOL,
        account_id=client.rest_client.config.account_id,
    )
    print(f"  [spot] cancelOrder OK status={resp.status} orderId={resp.order_id}")


async def flow_spot_create_and_cancel_by_client_order_id(
    client: ReyaWsExecClient,
    qty: Decimal,
) -> None:
    """Create a spot order with an explicit clientOrderId, then cancel it
    using ONLY the clientOrderId. Exercises the alternative cancel path
    enabled by the spot OrderCancel EIP-712 schema."""
    cl_id = _new_client_order_id()
    order_id = await flow_spot_create_order(client, qty=qty, client_order_id=cl_id)
    print(f"  [spot] created order with clientOrderId={cl_id} (orderId={order_id})")
    resp = await client.cancel_order(
        client_order_id=cl_id,
        symbol=SPOT_SYMBOL,
        account_id=client.rest_client.config.account_id,
    )
    print(f"  [spot] cancelOrder (by clientOrderId) OK status={resp.status} orderId={resp.order_id}")


async def flow_spot_cancel_all(
    client: ReyaWsExecClient,
    qty: Decimal,
    num_orders_to_open: int = 3,
) -> None:
    opened: list[str] = []
    for _ in range(num_orders_to_open):
        opened.append(await flow_spot_create_order(client, qty=qty, client_order_id=_new_client_order_id()))
    print(f"  [spot] opened {len(opened)} orders to be cancelled by cancelAll")
    resp = await client.mass_cancel(symbol=SPOT_SYMBOL)
    _assert_cancelled_count(expected=num_orders_to_open, actual=resp.cancelled_count, label="cancelAll")
    print(f"  [spot] cancelAll OK cancelledCount={resp.cancelled_count}")


async def flow_spot_cancel_all_account_wide(
    client: ReyaWsExecClient,
    qty: Decimal,
    num_orders_to_open: int = 2,
) -> None:
    opened: list[str] = []
    for _ in range(num_orders_to_open):
        opened.append(await flow_spot_create_order(client, qty=qty, client_order_id=_new_client_order_id()))
    print(f"  [spot] opened {len(opened)} orders for account-wide cancelAll")
    resp = await client.mass_cancel(symbol=None)
    _assert_cancelled_count(expected=num_orders_to_open, actual=resp.cancelled_count, label="cancelAll (account-wide)")
    print(f"  [spot] cancelAll (account-wide) OK cancelledCount={resp.cancelled_count}")


async def flow_perp_create_limit_gtc_and_cancel(
    client: ReyaWsExecClient,
    qty: Decimal,
) -> None:
    """Create a perp LIMIT GTC conditional order, then cancel it."""
    resp = await client.create_limit_order(
        LimitOrderParameters(
            symbol=PERP_SYMBOL,
            is_buy=True,
            limit_px=PERP_GTC_LIMIT_PX,
            qty=str(qty),
            time_in_force=TimeInForce.GTC,
        )
    )
    if resp.order_id is None:
        raise RuntimeError(f"perp LIMIT GTC OK but missing orderId: {resp}")
    print(f"  [perp] createOrder (LIMIT GTC) OK orderId={resp.order_id} status={resp.status}")
    cancel = await client.cancel_order(order_id=resp.order_id, symbol=PERP_SYMBOL)
    print(f"  [perp] cancelOrder OK status={cancel.status} orderId={cancel.order_id}")


async def flow_perp_create_trigger_and_cancel(
    client: ReyaWsExecClient,
    trigger_type: OrderType,
    trigger_px: str,
    qty: Decimal,
    label: str,
) -> None:
    resp = await client.create_trigger_order(
        TriggerOrderParameters(
            symbol=PERP_SYMBOL,
            is_buy=False,
            qty=str(qty),
            trigger_px=trigger_px,
            trigger_type=trigger_type,
        )
    )
    if resp.order_id is None:
        raise RuntimeError(f"perp {label} OK but missing orderId: {resp}")
    print(f"  [perp] createOrder ({label}) OK orderId={resp.order_id} status={resp.status} triggerPx={trigger_px}")
    cancel = await client.cancel_order(order_id=resp.order_id, symbol=PERP_SYMBOL)
    print(f"  [perp] cancelOrder OK status={cancel.status} orderId={cancel.order_id}")


async def flow_perp_ioc_open(client: ReyaWsExecClient, qty: Decimal) -> None:
    """Perp IOC. Settles on-chain via OrdersGateway::execute and fills at the
    current pool price, opening a min-size long position."""
    resp = await client.create_limit_order(
        LimitOrderParameters(
            symbol=PERP_SYMBOL,
            is_buy=True,
            limit_px=PERP_LIMIT_PX,
            qty=str(qty),
            time_in_force=TimeInForce.IOC,
            reduce_only=False,
        )
    )
    print(f"  [perp] createOrder (IOC) OK status={resp.status} " f"execQty={resp.exec_qty} orderId={resp.order_id}")


async def flow_perp_ioc_close(client: ReyaWsExecClient, qty: Decimal) -> None:
    """Reduce-only IOC sell -- closes the long opened by flow_perp_ioc_open."""
    resp = await client.create_limit_order(
        LimitOrderParameters(
            symbol=PERP_SYMBOL,
            is_buy=False,
            limit_px=PERP_REDUCE_ONLY_LIMIT_PX,
            qty=str(qty),
            time_in_force=TimeInForce.IOC,
            reduce_only=True,
        )
    )
    print(
        f"  [perp] createOrder (IOC reduceOnly close) OK status={resp.status} "
        f"execQty={resp.exec_qty} orderId={resp.order_id}"
    )


# ---- Error flows (driven on a separate raw WebSocket) -----------------------
#
# The error-path tests intentionally send malformed payloads that the
# high-level client either rejects locally (e.g. spot GTC ``expires_after``
# validation) or that would race with the dispatch map (e.g. DUPLICATE_
# REQUEST_ID, where the server sends *both* a top-level error envelope AND
# the OK reply for the original request under the same envelope id). To keep
# the harness explicit, error flows open their own short-lived raw WebSocket
# connection, do the one negative probe, and close.


def _raw_connect(url: str) -> WebSocket:
    sslopt = {"cert_reqs": ssl.CERT_REQUIRED}
    return create_connection(url, sslopt=sslopt)


def _raw_send_envelope(ws: WebSocket, msg_type: str, env_id: str, payload: dict) -> None:
    # Drop None-valued fields so a raw frame matches what the high-level OpenAPI
    # client puts on the wire (it serializes with exclude_none). Notably the
    # ws-exec server rejects a *present* null `reduceOnly` on spot with
    # INPUT_VALIDATION ("reduceOnly field is not supported for spot markets").
    clean = {k: v for k, v in payload.items() if v is not None}
    ws.send(json.dumps({"type": msg_type, "id": env_id, "payload": clean}))


def _raw_recv_until(
    ws: WebSocket,
    predicate,
    timeout_s: float = RECV_TIMEOUT_S,
) -> dict:
    """Read frames until ``predicate(frame)`` returns True. Times out cleanly."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        ws.settimeout(max(0.1, deadline - time.time()))
        try:
            raw = ws.recv()
        except Exception as exc:  # noqa: BLE001 — surface as RuntimeError below
            raise RuntimeError(f"raw recv failed: {exc}") from exc
        if not raw:
            continue
        frame: dict = json.loads(raw)
        if frame.get("type") == "ping":
            pong: dict = {"type": "pong"}
            if frame.get("id") is not None:
                pong["id"] = frame["id"]
            ws.send(json.dumps(pong))
            continue
        if predicate(frame):
            return frame
    raise RuntimeError("raw recv timed out before predicate matched")


def _assert_top_level_error(frame: dict, expected_code: str, op_label: str) -> None:
    if frame.get("type") != "error":
        raise RuntimeError(f"[{op_label}] expected type=error, got {frame!r}")
    err = frame.get("error", {}) or {}
    actual = err.get("error")
    if actual != expected_code:
        raise RuntimeError(f"[{op_label}] expected {expected_code!r}, got {actual!r} message={err.get('message')!r}")


def _assert_per_op_error(frame: dict, expected_codes: tuple[str, ...], op_label: str) -> dict:
    if frame.get("ok"):
        raise RuntimeError(f"[{op_label}] expected error, got ok=true payload={frame.get('payload')!r}")
    err = frame.get("error", {}) or {}
    actual = err.get("error")
    if actual not in expected_codes:
        raise RuntimeError(
            f"[{op_label}] expected one of {expected_codes!r}, got error={actual!r} message={err.get('message')!r}"
        )
    return err


async def flow_err_duplicate_request_id(
    ws_url: str,
    rest_client: ReyaTradingClient,
    qty: Decimal,
) -> None:
    """Send two well-formed createOrder frames back-to-back with the same envelope
    id. The second trips the per-connection in-flight uniqueness check; the
    first continues processing and lands as a normal ok response."""
    shared_env_id = uuid.uuid4().hex[:12]

    payload_a, _ = rest_client.build_create_limit_order_payload(
        LimitOrderParameters(
            symbol=SPOT_SYMBOL,
            is_buy=True,
            limit_px=SPOT_LIMIT_PX,
            qty=str(qty),
            time_in_force=TimeInForce.GTC,
        )
    )
    payload_b, _ = rest_client.build_create_limit_order_payload(
        LimitOrderParameters(
            symbol=SPOT_SYMBOL,
            is_buy=True,
            limit_px=SPOT_LIMIT_PX,
            qty=str(qty),
            time_in_force=TimeInForce.GTC,
        )
    )

    ws = _raw_connect(ws_url)
    try:
        _raw_send_envelope(ws, "createOrder", shared_env_id, payload_a)
        _raw_send_envelope(ws, "createOrder", shared_env_id, payload_b)

        err_frame = _raw_recv_until(ws, lambda f: f.get("type") == "error")
        _assert_top_level_error(err_frame, "DUPLICATE_REQUEST_ID", "duplicate request id")
        print("  [err] DUPLICATE_REQUEST_ID OK")

        # The first send's response still comes back ok=true under shared_env_id.
        ok_frame = _raw_recv_until(ws, lambda f: f.get("id") == shared_env_id and "ok" in f)
        leaked_order_id = (ok_frame.get("payload") or {}).get("orderId")
    finally:
        ws.close()

    # Clean up via the high-level client to avoid leaking a 24h resting order.
    # ``is not None`` (not truthy-check) so orderId=0 is still cleaned up.
    if leaked_order_id is not None:
        async with await _connect_ws_exec_client(rest_client, ws_url) as cleanup_client:
            await cleanup_client.cancel_order(
                order_id=str(leaked_order_id),
                symbol=SPOT_SYMBOL,
                account_id=rest_client.config.account_id,
            )


def flow_err_malformed_json(ws_url: str) -> None:
    """Send a non-JSON string. Server returns MALFORMED_JSON at the framing layer."""
    ws = _raw_connect(ws_url)
    try:
        ws.send("this-is-not-json")
        err_frame = _raw_recv_until(ws, lambda f: f.get("type") == "error")
        _assert_top_level_error(err_frame, "MALFORMED_JSON", "malformed json")
        print("  [err] MALFORMED_JSON OK")
    finally:
        ws.close()


def flow_err_unknown_type(ws_url: str) -> None:
    """Send a frame with an unknown `type`. Server returns UNKNOWN_TYPE."""
    ws = _raw_connect(ws_url)
    try:
        env_id = uuid.uuid4().hex[:12]
        ws.send(json.dumps({"type": "foobar", "id": env_id, "payload": {}}))
        err_frame = _raw_recv_until(ws, lambda f: f.get("type") == "error")
        _assert_top_level_error(err_frame, "UNKNOWN_TYPE", "unknown type")
        print("  [err] UNKNOWN_TYPE OK")
    finally:
        ws.close()


async def flow_err_invalid_nonce(
    ws_url: str,
    rest_client: ReyaTradingClient,
    qty: Decimal,
) -> None:
    """Replay a nonce: submit a createOrder twice with the SAME nonce. The first
    succeeds (we cancel inline); the second is rejected as INVALID_NONCE_ERROR."""
    payload, _nonce = rest_client.build_create_limit_order_payload(
        LimitOrderParameters(
            symbol=SPOT_SYMBOL,
            is_buy=True,
            limit_px=SPOT_LIMIT_PX,
            qty=str(qty),
            time_in_force=TimeInForce.GTC,
        )
    )

    leaked_order_id: str | None = None
    ws = _raw_connect(ws_url)
    try:
        env_id_1 = uuid.uuid4().hex[:12]
        _raw_send_envelope(ws, "createOrder", env_id_1, payload)
        ok = _raw_recv_until(ws, lambda f: f.get("id") == env_id_1 and "ok" in f)
        if not ok.get("ok"):
            raise RuntimeError(f"invalid-nonce setup failed: {ok!r}")
        leaked_order_id = (ok.get("payload") or {}).get("orderId")

        env_id_2 = uuid.uuid4().hex[:12]
        _raw_send_envelope(ws, "createOrder", env_id_2, payload)
        err = _raw_recv_until(ws, lambda f: f.get("id") == env_id_2 and "ok" in f)
        _assert_per_op_error(err, ("INVALID_NONCE_ERROR",), "invalid nonce replay")
        print("  [err] INVALID_NONCE_ERROR OK")
    finally:
        ws.close()

    if leaked_order_id is not None:
        async with await _connect_ws_exec_client(rest_client, ws_url) as cleanup_client:
            await cleanup_client.cancel_order(
                order_id=str(leaked_order_id),
                symbol=SPOT_SYMBOL,
                account_id=rest_client.config.account_id,
            )


def flow_err_order_deadline_passed(ws_url: str, rest_client: ReyaTradingClient, qty: Decimal) -> None:
    """Submit a createOrder with `expiresAfter`/`deadline` in the past. The
    validator chain catches it as ORDER_DEADLINE_PASSED_ERROR or
    INPUT_VALIDATION_ERROR.

    Built via the unified `build_create_limit_order_payload` with an explicit
    past `deadline` (which the builder also mirrors into `expiresAfter`), so
    the payload is correctly signed but dead-on-arrival."""
    payload, _nonce = rest_client.build_create_limit_order_payload(
        LimitOrderParameters(
            symbol=SPOT_SYMBOL,
            is_buy=True,
            limit_px=SPOT_LIMIT_PX,
            qty=str(qty),
            time_in_force=TimeInForce.GTC,
            deadline=int(time.time()) - 60,  # 60s in the past
        )
    )

    ws = _raw_connect(ws_url)
    try:
        env_id = uuid.uuid4().hex[:12]
        _raw_send_envelope(ws, "createOrder", env_id, payload)
        resp = _raw_recv_until(ws, lambda f: f.get("id") == env_id and "ok" in f)
        err = _assert_per_op_error(
            resp,
            ("ORDER_DEADLINE_PASSED_ERROR", "INPUT_VALIDATION_ERROR"),
            "expired deadline",
        )
        print(f"  [err] expired deadline rejected OK code={err.get('error')!r}")
    finally:
        ws.close()


def flow_err_unauthorized_signature(
    ws_url: str,
    rest_client: ReyaTradingClient,
    other_rest_client: ReyaTradingClient,
    qty: Decimal,
) -> None:
    """Sign the payload with one account's key but declare the OTHER wallet
    as ``signerWallet``. Server recovers the actual signer and rejects.

    Built via the unified `build_create_limit_order_payload` (signed by
    ``rest_client``), then the `signerWallet` field is overwritten with the
    other account's wallet so recovery-vs-declared diverge."""
    payload, _nonce = rest_client.build_create_limit_order_payload(
        LimitOrderParameters(
            symbol=SPOT_SYMBOL,
            is_buy=True,
            limit_px=SPOT_LIMIT_PX,
            qty=str(qty),
            time_in_force=TimeInForce.GTC,
        )
    )
    # Declared as the OTHER wallet -> recovered signer vs declared mismatch.
    payload["signerWallet"] = other_rest_client.signer_wallet_address

    ws = _raw_connect(ws_url)
    try:
        env_id = uuid.uuid4().hex[:12]
        _raw_send_envelope(ws, "createOrder", env_id, payload)
        resp = _raw_recv_until(ws, lambda f: f.get("id") == env_id and "ok" in f)
        err = _assert_per_op_error(
            resp,
            ("UNAUTHORIZED_SIGNATURE_ERROR", "CREATE_ORDER_OTHER_ERROR"),
            "signer mismatch",
        )
        print(f"  [err] signer mismatch rejected OK code={err.get('error')!r}")
    finally:
        ws.close()


# ---- Helpers ---------------------------------------------------------------


def _new_client_order_id() -> int:
    """Spot clientOrderId. Microseconds-since-epoch, monotonic-ish; the
    server enforces actual uniqueness."""
    return int(time.time() * 1_000_000)


def _assert_cancelled_count(expected: int, actual: int | None, label: str) -> None:
    """Hard-assert cancelledCount instead of just printing a WARN — the script
    claims 'all flows passed' at the end, and a silent count mismatch
    contradicts that."""
    if actual != expected:
        raise RuntimeError(
            f"[{label}] cancelledCount mismatch: expected {expected}, got {actual}. "
            "If other orders were open on the account, narrow the test scope and re-run."
        )


async def _connect_ws_exec_client(
    rest_client: ReyaTradingClient,
    ws_url: str,
) -> ReyaWsExecClient:
    """Construct + connect a ws-exec client. Returned in connected state, ready
    to be used as a regular client OR as an ``async with`` context manager."""
    client = ReyaWsExecClient(rest_client=rest_client, ws_url=ws_url)
    await client.connect()
    return client


async def _build_rest_client(
    *,
    perp: bool,
    account_number: int = 1,
) -> ReyaTradingClient:
    """Build + start a REST client. ``start()`` loads market definitions."""
    config = TradingConfig.from_env() if perp else TradingConfig.from_env_spot(account_number=account_number)
    if not config.private_key or config.account_id is None:
        kind = "PERP" if perp else f"SPOT_*_{account_number}"
        raise RuntimeError(f"{kind} private key + account id must be set in .env")
    client = ReyaTradingClient(config)
    await client.start()
    return client


# ---- Pytest fixtures -------------------------------------------------------


@dataclass
class _WsExecHarness:
    """Shared, module-scoped state for the ws-exec flows."""

    ws_url: str
    spot_rest: ReyaTradingClient
    perp_rest: ReyaTradingClient
    spot_rest_2: ReyaTradingClient | None
    spot_qty: Decimal
    perp_qty: Decimal


@pytest_asyncio.fixture(loop_scope="session", scope="module")
async def harness():
    """Build REST clients, resolve market min-qtys, expose the ws-exec URL.

    Module-scoped: one set of clients shared across every ws-exec flow. The
    module-level skipif guarantees the required env is present before we get
    here, so a missing-creds path would be a real error, not a skip."""
    ws_url = os.environ.get("REYA_WS_EXEC_URL", DEFAULT_WS_EXEC_URL)
    spot_rest = await _build_rest_client(perp=False, account_number=1)
    perp_rest = await _build_rest_client(perp=True)
    try:
        spot_rest_2: ReyaTradingClient | None = await _build_rest_client(perp=False, account_number=2)
    except (RuntimeError, ValueError):
        spot_rest_2 = None  # SPOT_*_2 absent -> E6 signer-mismatch test skips.

    try:
        spot_markets = {m.symbol: m for m in await spot_rest.reference.get_spot_market_definitions()}
        perp_markets = {m.symbol: m for m in await perp_rest.reference.get_market_definitions()}
        if SPOT_SYMBOL not in spot_markets:
            raise RuntimeError(f"{SPOT_SYMBOL} not found in /spotMarketDefinitions")
        if PERP_SYMBOL not in perp_markets:
            raise RuntimeError(f"{PERP_SYMBOL} not found in /marketDefinitions")
        yield _WsExecHarness(
            ws_url=ws_url,
            spot_rest=spot_rest,
            perp_rest=perp_rest,
            spot_rest_2=spot_rest_2,
            spot_qty=Decimal(str(spot_markets[SPOT_SYMBOL].min_order_qty)),
            perp_qty=Decimal(str(perp_markets[PERP_SYMBOL].min_order_qty)),
        )
    finally:
        await spot_rest.close()
        await perp_rest.close()
        if spot_rest_2 is not None:
            await spot_rest_2.close()


@pytest_asyncio.fixture(loop_scope="session", scope="module")
async def spot_ws(harness):  # pylint: disable=redefined-outer-name
    """A connected ws-exec client authenticated as the spot account."""
    async with await _connect_ws_exec_client(harness.spot_rest, harness.ws_url) as client:
        yield client


@pytest_asyncio.fixture(loop_scope="session", scope="module")
async def perp_ws(harness):  # pylint: disable=redefined-outer-name
    """A connected ws-exec client authenticated as the perp account."""
    async with await _connect_ws_exec_client(harness.perp_rest, harness.ws_url) as client:
        yield client


# ---- Happy-path tests ------------------------------------------------------


async def test_ping(spot_ws):  # pylint: disable=redefined-outer-name
    """Flow 0: application ping/pong probe."""
    await flow_ping(spot_ws)


async def test_spot_create_and_cancel_by_order_id(spot_ws, harness):  # pylint: disable=redefined-outer-name
    """Flows 1-2: spot createOrder (GTC) then cancel by orderId."""
    order_id = await flow_spot_create_order(spot_ws, qty=harness.spot_qty, client_order_id=_new_client_order_id())
    resp = await spot_ws.cancel_order(
        order_id=order_id,
        symbol=SPOT_SYMBOL,
        account_id=spot_ws.rest_client.config.account_id,
    )
    print(f"  [spot] cancelOrder OK orderId={order_id} status={resp.status}")


@pytest.mark.xfail(
    reason="PRO-143: ws-exec cancelOrder by clientOrderId returns no orderId, but "
    "CancelOrderResponse marks orderId required (spec-vs-server mismatch)",
    strict=False,
)
async def test_spot_cancel_by_client_order_id(spot_ws, harness):  # pylint: disable=redefined-outer-name
    """Flow 3: create + cancel by clientOrderId. xfail until PRO-143 is resolved."""
    await flow_spot_create_and_cancel_by_client_order_id(spot_ws, qty=harness.spot_qty)


async def test_spot_cancel_all_symbol_scoped(spot_ws, harness):  # pylint: disable=redefined-outer-name
    """Flow 4: open N spot orders, cancelAll scoped to the symbol."""
    await flow_spot_cancel_all(spot_ws, qty=harness.spot_qty, num_orders_to_open=3)


async def test_spot_cancel_all_account_wide(spot_ws, harness):  # pylint: disable=redefined-outer-name
    """Flow 5: open N spot orders, cancelAll account-wide (no symbol scope)."""
    await flow_spot_cancel_all_account_wide(spot_ws, qty=harness.spot_qty, num_orders_to_open=2)


# All perp order entry over ws-exec currently returns INTERNAL server-side on
# devnet1 (spot works; the same perp orders succeed over REST). Tracked in
# PRO-149 — xfail (non-strict) so the suite stays green and auto-flags (xpass)
# the moment the ws-exec perp handler is fixed.
_PERP_WS_EXEC_XFAIL = pytest.mark.xfail(
    reason="PRO-149: perp order entry over ws-exec returns INTERNAL on devnet1 "
    "(server-side 'Handler threw'); spot + REST-perp work",
    strict=False,
)


@_PERP_WS_EXEC_XFAIL
async def test_perp_limit_gtc_and_cancel(perp_ws, harness):  # pylint: disable=redefined-outer-name
    """Flow 6: perp LIMIT GTC conditional rests, then cancel."""
    await flow_perp_create_limit_gtc_and_cancel(perp_ws, qty=harness.perp_qty)


@_PERP_WS_EXEC_XFAIL
async def test_perp_trigger_take_profit_and_cancel(perp_ws, harness):  # pylint: disable=redefined-outer-name
    """Flow 7: perp TAKE_PROFIT trigger order, then cancel."""
    await flow_perp_create_trigger_and_cancel(
        perp_ws, OrderType.TAKE_PROFIT, PERP_TP_TRIGGER_PX, harness.perp_qty, "TP"
    )


@_PERP_WS_EXEC_XFAIL
async def test_perp_trigger_stop_loss_and_cancel(perp_ws, harness):  # pylint: disable=redefined-outer-name
    """Flow 8: perp STOP_LOSS trigger order, then cancel."""
    await flow_perp_create_trigger_and_cancel(perp_ws, OrderType.STOP_LOSS, PERP_SL_TRIGGER_PX, harness.perp_qty, "SL")


@_PERP_WS_EXEC_XFAIL
async def test_perp_ioc_open_and_close(perp_ws, harness):  # pylint: disable=redefined-outer-name
    """Flows 9-10 (paired): perp IOC opens a min-size long, reduce-only IOC
    closes it. The close always runs in ``finally`` so a failed assertion never
    leaks a position."""
    await flow_perp_ioc_open(perp_ws, qty=harness.perp_qty)
    try:
        pass  # placeholder for future inter-9-and-10 checks; keep paired
    finally:
        await flow_perp_ioc_close(perp_ws, qty=harness.perp_qty)


# ---- Error-path tests ------------------------------------------------------


async def test_err_duplicate_request_id(harness):  # pylint: disable=redefined-outer-name
    """E1: two createOrder frames with the same envelope id -> DUPLICATE_REQUEST_ID."""
    await flow_err_duplicate_request_id(harness.ws_url, harness.spot_rest, qty=harness.spot_qty)


async def test_err_malformed_json(harness):  # pylint: disable=redefined-outer-name
    """E2: non-JSON frame -> MALFORMED_JSON."""
    flow_err_malformed_json(harness.ws_url)


async def test_err_unknown_type(harness):  # pylint: disable=redefined-outer-name
    """E3: unknown envelope type -> UNKNOWN_TYPE."""
    flow_err_unknown_type(harness.ws_url)


async def test_err_invalid_nonce(harness):  # pylint: disable=redefined-outer-name
    """E4: replayed nonce -> INVALID_NONCE_ERROR."""
    await flow_err_invalid_nonce(harness.ws_url, harness.spot_rest, qty=harness.spot_qty)


async def test_err_order_deadline_passed(harness):  # pylint: disable=redefined-outer-name
    """E5: past expiresAfter/deadline -> ORDER_DEADLINE_PASSED_ERROR / INPUT_VALIDATION_ERROR."""
    flow_err_order_deadline_passed(harness.ws_url, harness.spot_rest, qty=harness.spot_qty)


async def test_err_unauthorized_signature(harness):  # pylint: disable=redefined-outer-name
    """E6: declared signerWallet != recovered signer -> rejected. Requires SPOT_*_2."""
    if harness.spot_rest_2 is None:
        pytest.skip("SPOT_*_2 not set in .env; signer-mismatch test needs a second account")
    flow_err_unauthorized_signature(harness.ws_url, harness.spot_rest, harness.spot_rest_2, qty=harness.spot_qty)
