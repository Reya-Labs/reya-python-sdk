"""Async ws-exec client implementation.

The client is asyncio-native (so it composes with other ``await``ed code in
SDK consumers) but uses the synchronous ``websocket-client`` library under
the hood with a dedicated reader thread, mirroring the pattern already
established in :mod:`sdk.reya_websocket`.

Threading / loop model:

* The user's asyncio event loop sends frames via ``WebSocket.send`` (a quick,
  blocking syscall).
* A daemon reader thread blocks on ``WebSocket.recv``, parses each frame,
  and hands it back to the event loop with ``loop.call_soon_threadsafe`` so
  the in-flight ``Future`` resolves cleanly in the loop that awaits it.

This means application code only ever sees coroutines, while the underlying
synchronous library is fully isolated to the reader thread.
"""

from typing import Any, Optional

import asyncio
import json
import logging
import ssl
import threading
import uuid

from websocket import (  # type: ignore[attr-defined]  # pylint: disable=no-name-in-module
    WebSocket,
    WebSocketException,
    create_connection,
)

from sdk.async_exec_api.cancel_all_after_request import CancelAllAfterRequest as WsCancelAllAfterRequest
from sdk.async_exec_api.cancel_all_after_response import CancelAllAfterResponse as WsCancelAllAfterResponse
from sdk.async_exec_api.cancel_order_request import CancelOrderRequest as WsCancelOrderRequest
from sdk.async_exec_api.cancel_order_response import CancelOrderResponse as WsCancelOrderResponse
from sdk.async_exec_api.create_order_request import CreateOrderRequest as WsCreateOrderRequest
from sdk.async_exec_api.create_order_response import CreateOrderResponse as WsCreateOrderResponse
from sdk.async_exec_api.mass_cancel_request import MassCancelRequest as WsMassCancelRequest
from sdk.async_exec_api.mass_cancel_response import MassCancelResponse as WsMassCancelResponse
from sdk.async_exec_api.modify_order_request import ModifyOrderRequest as WsModifyOrderRequest
from sdk.async_exec_api.modify_order_response import ModifyOrderResponse as WsModifyOrderResponse
from sdk.reya_rest_api.client import ReyaTradingClient
from sdk.reya_rest_api.models.orders import LimitOrderParameters, ModifyOrderParameters, TriggerOrderParameters

logger = logging.getLogger("reya.ws_exec")

DEFAULT_RESPONSE_TIMEOUT_S = 15.0
"""Per-request deadline waiting for the server to reply on the WebSocket."""

_ENVELOPE_ID_LEN = 12


class WsExecOperationError(RuntimeError):
    """Raised when the server returns a per-operation error (``ok: false``).

    Carries the structured error envelope so callers can branch on
    ``RequestErrorCode`` without re-parsing the message string.
    """

    def __init__(self, code: str, message: str, request_id: str) -> None:
        super().__init__(f"[{request_id}] {code}: {message}")
        self.code = code
        self.message = message
        self.request_id = request_id


class WsExecProtocolError(RuntimeError):
    """Raised on framing-layer errors: ``MALFORMED_JSON``, ``UNKNOWN_TYPE``,
    ``DUPLICATE_REQUEST_ID``, ``INTERNAL``, or unexpected frame shape."""

    def __init__(self, code: str, message: str, request_id: Optional[str]) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.request_id = request_id


class ReyaWsExecClient:
    """High-level async ws-exec client.

    Composes a :class:`ReyaTradingClient` for EIP-712 signing, payload
    construction, market-id resolution, and per-wallet nonce coordination.
    The same ``ReyaTradingClient`` instance can be used concurrently for
    REST calls; the nonce manager is class-level on ``ReyaTradingClient``,
    so REST and WS-exec calls share the same monotonic stream.

    Example:
        >>> async with ReyaTradingClient(config) as rest, \\
        ...           ReyaWsExecClient(rest_client=rest, ws_url="wss://...") as ws:
        ...     await rest.start()                       # loads market defs
        ...     await ws.connect()
        ...     resp = await ws.create_limit_order(params)
    """

    def __init__(
        self,
        rest_client: ReyaTradingClient,
        ws_url: str,
        *,
        response_timeout_s: float = DEFAULT_RESPONSE_TIMEOUT_S,
    ) -> None:
        self._rest = rest_client
        self._ws_url = ws_url
        self._response_timeout_s = response_timeout_s

        self._ws: Optional[WebSocket] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._reader_stop = threading.Event()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._send_lock = threading.Lock()

        self._pending: dict[str, asyncio.Future[dict]] = {}
        self._pending_lock = threading.Lock()

    @property
    def rest_client(self) -> ReyaTradingClient:
        """The composed REST client. Public so callers can reach the shared
        signing / market-resolution / config surface without poking at
        a private attribute."""
        return self._rest

    # ---- lifecycle -----------------------------------------------------

    async def connect(self) -> None:
        """Open the WebSocket and start the reader thread.

        Idempotent — calling twice is a no-op. The wrapped REST client must
        already have :meth:`ReyaTradingClient.start` run successfully so
        market definitions are loaded; otherwise symbol-to-market-id
        resolution will fail on the first order.
        """
        if self._ws is not None:
            return

        self._loop = asyncio.get_running_loop()

        sslopt = {"cert_reqs": ssl.CERT_REQUIRED}
        # ``create_connection`` is blocking; offload it so callers in async
        # code paths don't pay the TLS handshake on the event loop thread.
        self._ws = await asyncio.to_thread(
            create_connection,
            self._ws_url,
            sslopt=sslopt,
        )
        self._reader_stop.clear()
        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            name="reya-ws-exec-reader",
            daemon=True,
        )
        self._reader_thread.start()
        logger.info("ws-exec connected: %s", self._ws_url)

    async def close(self) -> None:
        """Close the WebSocket, stop the reader thread, fail any pending requests."""
        if self._ws is None:
            return

        self._reader_stop.set()
        try:
            self._ws.close()
        except (OSError, WebSocketException):
            # ``close()`` is best-effort: the socket may already be torn down
            # (OSError on EBADF / ECONNRESET) or the library may flag a state
            # error (WebSocketException). Either way, we're shutting down.
            logger.debug("ws-exec close() raised; ignoring", exc_info=True)

        if self._reader_thread is not None:
            self._reader_thread.join(timeout=2.0)
        self._reader_thread = None
        self._ws = None

        # Fail any in-flight requests so awaiters don't hang.
        with self._pending_lock:
            pending = list(self._pending.items())
            self._pending.clear()
        for req_id, fut in pending:
            if not fut.done():
                fut.set_exception(WsExecProtocolError("CLOSED", "client closed before response", req_id))

        logger.info("ws-exec closed")

    async def __aenter__(self) -> "ReyaWsExecClient":
        await self.connect()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()

    # ---- public order-entry surface ------------------------------------

    async def create_limit_order(self, params: LimitOrderParameters) -> WsCreateOrderResponse:
        """Send a createOrder (LIMIT IOC/GTC) request and await the response."""
        payload, _nonce = self._rest.build_create_limit_order_payload(params)
        req = WsCreateOrderRequest(**payload)
        envelope = await self._send_and_await("createOrder", req)
        return WsCreateOrderResponse.model_validate(envelope)

    async def create_trigger_order(self, params: TriggerOrderParameters) -> WsCreateOrderResponse:
        """Send a createOrder (TP/SL) request and await the response."""
        await self._rest.refresh_trigger_market_state(params.trigger_type)
        payload, _nonce = self._rest.build_create_trigger_order_payload(params)
        req = WsCreateOrderRequest(**payload)
        envelope = await self._send_and_await("createOrder", req)
        return WsCreateOrderResponse.model_validate(envelope)

    async def cancel_order(
        self,
        order_id: Optional[str] = None,
        symbol: Optional[str] = None,
        account_id: Optional[int] = None,
        client_order_id: Optional[int] = None,
    ) -> WsCancelOrderResponse:
        """Cancel a resting order. Same arg semantics as
        :meth:`ReyaTradingClient.cancel_order`."""
        payload = self._rest.build_cancel_order_payload(
            order_id=order_id,
            symbol=symbol,
            account_id=account_id,
            client_order_id=client_order_id,
        )
        req = WsCancelOrderRequest(**payload)
        envelope = await self._send_and_await("cancelOrder", req)
        return WsCancelOrderResponse.model_validate(envelope)

    async def mass_cancel(
        self,
        symbol: Optional[str],
        account_id: Optional[int] = None,
    ) -> WsMassCancelResponse:
        """Mass-cancel all resting orders for a symbol (or account-wide when ``symbol`` is ``None``)."""
        payload = self._rest.build_mass_cancel_payload(symbol=symbol, account_id=account_id)
        req = WsMassCancelRequest(**payload)
        envelope = await self._send_and_await("cancelAll", req)
        return WsMassCancelResponse.model_validate(envelope)

    async def modify_order(self, params: ModifyOrderParameters) -> WsModifyOrderResponse:
        """Modify a resting order in place. Same arg semantics as
        :meth:`ReyaTradingClient.modify_order`."""
        await self._rest.refresh_trigger_market_state(params.order_type)
        payload, _nonce = self._rest.build_modify_order_payload(params)
        req = WsModifyOrderRequest(**payload)
        envelope = await self._send_and_await("modifyOrder", req)
        return WsModifyOrderResponse.model_validate(envelope)

    async def cancel_all_after(
        self,
        timeout_ms: int,
        account_id: Optional[int] = None,
        deadline: Optional[int] = None,
        nonce: Optional[int] = None,
    ) -> WsCancelAllAfterResponse:
        """Arm, refresh, or disarm the account-wide cancel-all-after countdown
        (dead-man's-switch). Same arg semantics as
        :meth:`ReyaTradingClient.cancel_all_after`."""
        payload = self._rest.build_cancel_all_after_payload(
            timeout_ms=timeout_ms,
            account_id=account_id,
            deadline=deadline,
            nonce=nonce,
        )
        req = WsCancelAllAfterRequest(**payload)
        envelope = await self._send_and_await("cancelAllAfter", req)
        return WsCancelAllAfterResponse.model_validate(envelope)

    async def ping(self) -> None:
        """Send a JSON-layer ping and await the matching pong.

        Distinct from RFC 6455 protocol pings which the underlying library
        handles transparently — this exercises the ws-exec application
        ping/pong path used as a liveness probe.
        """
        env_id = self._new_envelope_id()
        future = self._register(env_id)
        self._send_raw_frame({"type": "ping", "id": env_id})
        try:
            await asyncio.wait_for(future, timeout=self._response_timeout_s)
        finally:
            self._unregister(env_id)

    # ---- private: send / receive ---------------------------------------

    async def _send_and_await(self, msg_type: str, request_model: Any) -> dict:
        """Send a typed request envelope and return the payload of the
        ok-response. Raises :class:`WsExecOperationError` on a per-op
        error, :class:`WsExecProtocolError` on a framing-layer error or
        timeout."""
        if self._ws is None:
            raise WsExecProtocolError("NOT_CONNECTED", "call connect() first", None)

        env_id = self._new_envelope_id()
        future = self._register(env_id)

        wire_payload = request_model.model_dump(by_alias=True, exclude_none=True, mode="json")
        frame = {"type": msg_type, "id": env_id, "payload": wire_payload}

        try:
            self._send_raw_frame(frame)
            response = await asyncio.wait_for(future, timeout=self._response_timeout_s)
        except asyncio.TimeoutError as exc:
            raise WsExecProtocolError(
                "TIMEOUT",
                f"no response within {self._response_timeout_s}s for {msg_type}",
                env_id,
            ) from exc
        finally:
            self._unregister(env_id)

        return self._extract_ok_payload(response, env_id)

    def _send_raw_frame(self, frame: dict) -> None:
        assert self._ws is not None
        encoded = json.dumps(frame)
        # ``websocket-client``'s send is not thread-safe across concurrent
        # callers, but the asyncio loop only sends from one thread at a
        # time. Lock anyway in case the loop is multi-threaded (default
        # asyncio is single-threaded, but ProactorEventLoop on Windows can
        # surprise us).
        with self._send_lock:
            self._ws.send(encoded)

    @staticmethod
    def _extract_ok_payload(envelope: dict, env_id: str) -> dict:
        # Framing-layer (top-level) error envelopes carry ``type: "error"``.
        if envelope.get("type") == "error":
            err = envelope.get("error", {}) or {}
            raise WsExecProtocolError(
                err.get("error", "UNKNOWN"),
                err.get("message", "(no message)"),
                envelope.get("id"),
            )
        # Per-operation envelopes carry ``ok: bool``.
        if envelope.get("ok") is False:
            err = envelope.get("error", {}) or {}
            raise WsExecOperationError(
                err.get("error", "UNKNOWN"),
                err.get("message", "(no message)"),
                env_id,
            )
        payload = envelope.get("payload")
        if not isinstance(payload, dict):
            raise WsExecProtocolError(
                "MALFORMED_RESPONSE",
                f"ok=true envelope missing payload dict; got {payload!r}",
                env_id,
            )
        return payload

    # ---- private: in-flight bookkeeping --------------------------------

    def _new_envelope_id(self) -> str:
        return uuid.uuid4().hex[:_ENVELOPE_ID_LEN]

    def _register(self, env_id: str) -> "asyncio.Future[dict]":
        assert self._loop is not None
        future: asyncio.Future[dict] = self._loop.create_future()
        with self._pending_lock:
            if env_id in self._pending:
                # Defensive: uuid4 collisions are astronomically unlikely,
                # but the server rejects duplicate ids — bail before sending.
                raise WsExecProtocolError(
                    "DUPLICATE_REQUEST_ID",
                    f"envelope id {env_id} is already in-flight",
                    env_id,
                )
            self._pending[env_id] = future
        return future

    def _unregister(self, env_id: str) -> None:
        with self._pending_lock:
            self._pending.pop(env_id, None)

    # ---- private: reader thread ----------------------------------------

    def _reader_loop(self) -> None:
        """Run on the reader thread. Dispatches frames to pending futures.

        Frames without a matching pending future are logged at debug and
        dropped — the matching ``_send_and_await`` has either already
        timed out (in which case the future was cancelled) or never
        existed (server-pushed notifications, which ws-exec does not
        currently emit outside ping/pong).
        """
        assert self._ws is not None
        ws = self._ws
        ws.settimeout(1.0)
        while not self._reader_stop.is_set():
            try:
                raw = ws.recv()
            except (OSError, WebSocketException, TimeoutError):
                # Both the 1s recv timeout and a transport-level disconnect
                # surface here; treat them uniformly and re-check the stop
                # flag so close() can unblock the loop cleanly.
                if self._reader_stop.is_set():
                    return
                continue
            if not raw:
                continue
            try:
                frame = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("ws-exec dropped non-JSON frame: %r", raw[:200])
                continue
            self._dispatch_frame(frame)

    def _dispatch_frame(self, frame: dict) -> None:
        frame_type = frame.get("type")

        # JSON-layer ping requires a JSON-layer pong; respond inline.
        if frame_type == "ping":
            pong: dict = {"type": "pong"}
            if frame.get("id") is not None:
                pong["id"] = frame["id"]
            try:
                self._send_raw_frame(pong)
            except (OSError, WebSocketException):
                logger.debug("ws-exec failed to send pong", exc_info=True)
            return

        env_id = frame.get("id")
        if env_id is None:
            # Framing-layer errors that couldn't be matched to an id
            # (e.g. MALFORMED_JSON on an unparseable request) — surface
            # via logging since no caller is waiting.
            logger.warning("ws-exec received frame with no id: %r", frame)
            return

        assert self._loop is not None
        with self._pending_lock:
            future = self._pending.get(env_id)
        if future is None:
            # Late arrival after timeout — caller's already given up.
            logger.debug("ws-exec frame for unknown id=%s dropped", env_id)
            return

        # Resolve the future on the loop thread so the awaiter sees a clean wake-up.
        self._loop.call_soon_threadsafe(self._set_future, future, frame)

    @staticmethod
    def _set_future(future: "asyncio.Future[dict]", value: dict) -> None:
        if not future.done():
            future.set_result(value)


# ---- Re-exports used in code-rabbit-style helper signatures -----------

__all__ = [
    "DEFAULT_RESPONSE_TIMEOUT_S",
    "ReyaWsExecClient",
    "WsExecOperationError",
    "WsExecProtocolError",
]
