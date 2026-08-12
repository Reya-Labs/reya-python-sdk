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
    ABNF,
    WebSocket,
    WebSocketException,
    WebSocketTimeoutException,
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

WS_CLOSE_MSG_RATE_EXCEEDED = 4029
"""Close code the relayer uses when a connection exceeds its inbound message-rate cap."""

_ENVELOPE_ID_LEN = 12
_READER_RECV_TIMEOUT_S = 1.0

#: RFC 6455 §5.5 caps a control frame's payload at 125 bytes.
_MAX_CONTROL_PAYLOAD_LEN = 125


class WsExecOperationError(RuntimeError):
    """Raised when the server returns a per-operation error (``ok: false``).

    Carries the structured error envelope so callers can branch on
    ``RequestErrorCode`` without re-parsing the message string.
    """

    def __init__(self, code: str, message: str, request_id: str, retry_after_ms: Optional[int] = None) -> None:
        super().__init__(f"[{request_id}] {code}: {message}")
        self.code = code
        self.message = message
        self.request_id = request_id
        self.retry_after_ms = retry_after_ms


class WsExecProtocolError(RuntimeError):
    """Raised on framing-layer errors: ``MALFORMED_JSON``, ``UNKNOWN_TYPE``,
    ``DUPLICATE_REQUEST_ID``, ``INTERNAL``, or unexpected frame shape."""

    def __init__(self, code: str, message: str, request_id: Optional[str]) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.request_id = request_id


class WsExecConnectionClosedError(WsExecProtocolError):
    """Raised on every in-flight request when the connection dies under it.

    The outcome of those requests is **indeterminate**: the server may have
    applied them before closing. Per the ws-exec AsyncAPI description the
    caller must reconnect and reconcile against
    ``GET /v2/wallet/{address}/openOrders`` rather than assume either outcome.

    Subclasses :class:`WsExecProtocolError` so existing handlers keep working.
    """

    CODE = "CONNECTION_CLOSED"

    def __init__(
        self,
        close_code: Optional[int],
        close_reason: Optional[str],
        request_id: Optional[str],
    ) -> None:
        super().__init__(
            self.CODE,
            (
                f"connection closed (code={close_code}, reason={close_reason!r}) with the request in flight; "
                "its outcome is indeterminate — reconnect and reconcile against GET /v2/wallet/{address}/openOrders"
            ),
            request_id,
        )
        self.close_code = close_code
        self.close_reason = close_reason


def _retry_after_ms(error: Any) -> Optional[int]:
    """Read the optional backoff hint off a ws-exec error object."""
    if not isinstance(error, dict):
        return None
    raw = error.get("retryAfterMs", error.get("retry_after_ms"))
    if raw is None or isinstance(raw, bool):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _parse_close_payload(payload: Any) -> tuple[Optional[int], Optional[str]]:
    """Split a close frame's body into ``(status_code, reason)``.

    RFC 6455 allows an empty body (no code, no reason) and a 2-byte body (code
    with no reason), so every field is optional.
    """
    if not isinstance(payload, (bytes, bytearray)) or len(payload) < 2:
        return None, None
    code = int.from_bytes(payload[:2], "big")
    reason = bytes(payload[2:]).decode("utf-8", errors="replace") or None
    return code, reason


def _echo_close(ws: WebSocket) -> None:
    """Echo a received close frame, best effort.

    Only ever called AFTER the close code has been read off the frame and
    recorded. The relayer tears the socket down within milliseconds of a rate
    shed, so this write routinely raises on an already-dead socket — and the
    library's own ``recv_data_frame`` ran exactly this echo BEFORE handing the
    frame back, which is how a fully received
    :data:`WS_CLOSE_MSG_RATE_EXCEEDED` could be lost and reported as a codeless
    transport death. Capture first, then echo: the write is then free to fail.
    """
    try:
        ws.send_close()
    except (OSError, WebSocketException):
        logger.debug("ws-exec close echo failed; the close code is already recorded", exc_info=True)


def _pong(ws: WebSocket, payload: bytes) -> None:
    """Answer a protocol-level ping, best effort.

    Wrapped for the same reason as :func:`_echo_close`: a pong that cannot be
    written must not take the reader down with it, because the frames already
    received — a close code above all — are what the caller is waiting for.
    """
    if len(payload) > _MAX_CONTROL_PAYLOAD_LEN:
        # An over-long ping is the peer's protocol error; the library refuses to
        # send an over-long pong. Skipping the answer keeps the connection's
        # remaining frames readable instead of raising in the reader.
        logger.debug("ws-exec skipped pong for an over-long ping (%d bytes)", len(payload))
        return
    try:
        ws.pong(payload)
    except (OSError, WebSocketException):
        logger.debug("ws-exec pong failed", exc_info=True)


def _reassemble(frame: Any, buffered: Optional[bytearray]) -> tuple[Optional[bytes], Optional[bytearray]]:
    """Fold one data frame into the message under assembly.

    Returns ``(payload, buffered)``; ``payload`` is non-``None`` only once a FIN
    frame completes a message. ws-exec answers in single unfragmented frames, so
    the second branch is the whole live path — the rest exists because reading
    raw frames means the library's own reassembler no longer runs, and a client
    that silently dropped a fragmented reply would look like a server that never
    answered.
    """
    if buffered is None and frame.opcode == ABNF.OPCODE_CONT:
        # A continuation with nothing to continue: half a message is worse than none.
        return None, None
    if buffered is None and frame.fin:
        return frame.data, None
    if frame.opcode != ABNF.OPCODE_CONT:
        # A fresh data frame while a fragment is pending is illegal; the newer
        # message supersedes the fragment rather than being concatenated onto it.
        buffered = None
    accumulated = bytearray() if buffered is None else buffered
    accumulated += frame.data
    if not frame.fin:
        return None, accumulated
    return bytes(accumulated), None


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

        self._close_lock = threading.Lock()
        self._connection_lost = threading.Event()
        self._last_close_code: Optional[int] = None
        self._last_close_reason: Optional[str] = None

    @property
    def rest_client(self) -> ReyaTradingClient:
        """The composed REST client. Public so callers can reach the shared
        signing / market-resolution / config surface without poking at
        a private attribute."""
        return self._rest

    @property
    def last_close_code(self) -> Optional[int]:
        """Status code of the most recent close observed on this client.

        ``None`` until a close frame arrives (an abrupt transport failure with
        no close frame leaves it ``None`` too). Retained across reconnects so a
        caller can branch on it *after* reconnecting — which is what the
        :data:`WS_CLOSE_MSG_RATE_EXCEEDED` reconcile rule requires.
        """
        with self._close_lock:
            return self._last_close_code

    @property
    def last_close_reason(self) -> Optional[str]:
        """Reason string of the most recent close, if the server sent one."""
        with self._close_lock:
            return self._last_close_reason

    # ---- lifecycle -----------------------------------------------------

    async def connect(self) -> None:
        """Open the WebSocket and start the reader thread.

        Idempotent — calling twice is a no-op. The wrapped REST client must
        already have :meth:`ReyaTradingClient.start` run successfully so
        market definitions are loaded; otherwise symbol-to-market-id
        resolution will fail on the first order.

        After a server-initiated close (see :attr:`last_close_code`) the
        socket handle is still held, so reconnecting is :meth:`close` then
        ``connect`` — not ``connect`` alone.
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
        self._connection_lost.clear()
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
        self._require_live_connection(None)
        env_id = self._new_envelope_id()
        future = self._register(env_id)
        try:
            self._send_raw_frame({"type": "ping", "id": env_id})
            await asyncio.wait_for(future, timeout=self._response_timeout_s)
        finally:
            self._unregister(env_id)

    # ---- private: send / receive ---------------------------------------

    def _require_live_connection(self, env_id: Optional[str]) -> None:
        """Refuse to send once the connection is known dead.

        Sends into a dead socket can be buffered and silently succeed, which
        would leave the caller waiting out its own response deadline for a
        server that is gone. Reconnect with :meth:`close` then :meth:`connect`.
        """
        if self._ws is None:
            raise WsExecProtocolError("NOT_CONNECTED", "call connect() first", env_id)
        if self._connection_lost.is_set():
            raise WsExecConnectionClosedError(self.last_close_code, self.last_close_reason, env_id)

    async def _send_and_await(self, msg_type: str, request_model: Any) -> dict:
        """Send a typed request envelope and return the payload of the
        ok-response. Raises :class:`WsExecOperationError` on a per-op
        error, :class:`WsExecProtocolError` on a framing-layer error or
        timeout."""
        self._require_live_connection(None)

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
                retry_after_ms=_retry_after_ms(err),
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

        Reads RAW frames (``recv_frame``) rather than through
        ``WebSocket.recv`` or ``WebSocket.recv_data_frame``. ``recv`` collapses
        a close frame into an empty string, which is indistinguishable from an
        empty text frame and throws away the status code. The relayer's
        per-connection message-rate cap is expressed purely as a close code
        (:data:`WS_CLOSE_MSG_RATE_EXCEEDED`), so it has to be read off the
        frame.

        ``recv_data_frame`` keeps the code but answers control frames as a side
        effect of the read — it echoes the close (and pongs a ping) BEFORE
        returning the frame. Against a socket the relayer is tearing down
        milliseconds after the shed, that write raises, and the exception
        surfaced from the read as an abrupt transport failure: the 4029 was
        fully received and then discarded, leaving :attr:`last_close_code`
        intermittently ``None``. Owning the control-frame handling here is what
        makes the capture unconditional — a received close ALWAYS yields its
        real code, whatever the echo afterwards does.
        """
        assert self._ws is not None
        ws = self._ws
        ws.settimeout(_READER_RECV_TIMEOUT_S)
        buffered: Optional[bytearray] = None
        while not self._reader_stop.is_set():
            try:
                frame = ws.recv_frame()
            except WebSocketTimeoutException:
                continue
            except (OSError, WebSocketException):
                # The transport died without a close frame (reset, EOF mid-frame).
                if self._reader_stop.is_set():
                    return
                logger.info("ws-exec transport failed; failing in-flight requests", exc_info=True)
                self._record_close(None, None)
                self._fail_pending_indeterminate(None, None)
                return
            if not frame:
                continue
            opcode = frame.opcode
            if opcode == ABNF.OPCODE_CLOSE:
                # Parse and record BEFORE the echo — see the note above.
                code, reason = _parse_close_payload(frame.data)
                self._record_close(code, reason)
                _echo_close(ws)
                logger.info("ws-exec closed by server: code=%s reason=%r", code, reason)
                if not self._reader_stop.is_set():
                    self._fail_pending_indeterminate(code, reason)
                return
            if opcode == ABNF.OPCODE_PING:
                _pong(ws, frame.data)
                continue
            if opcode not in (ABNF.OPCODE_TEXT, ABNF.OPCODE_BINARY, ABNF.OPCODE_CONT):
                continue
            raw, buffered = _reassemble(frame, buffered)
            if not raw:
                continue
            try:
                parsed = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                logger.warning("ws-exec dropped non-JSON frame: %r", raw[:200])
                continue
            self._dispatch_frame(parsed)

    def _record_close(self, code: Optional[int], reason: Optional[str]) -> None:
        with self._close_lock:
            self._last_close_code = code
            self._last_close_reason = reason
        self._connection_lost.set()

    def _fail_pending_indeterminate(self, code: Optional[int], reason: Optional[str]) -> None:
        """Fail every in-flight request with an explicit indeterminate-outcome error.

        Without this a server-initiated close is invisible to awaiters: they
        would hang to their own response deadline and then report a TIMEOUT,
        which reads as "the server never answered" when the truth is "the
        server may have applied the request and then hung up".
        """
        with self._pending_lock:
            pending = list(self._pending.items())
            self._pending.clear()
        if not pending:
            return
        loop = self._loop
        for req_id, future in pending:
            error = WsExecConnectionClosedError(code, reason, req_id)
            if loop is None:
                if not future.done():
                    future.set_exception(error)
                continue
            loop.call_soon_threadsafe(self._set_future_exception, future, error)

    @staticmethod
    def _set_future_exception(future: "asyncio.Future[dict]", error: BaseException) -> None:
        if not future.done():
            future.set_exception(error)

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
    "WS_CLOSE_MSG_RATE_EXCEEDED",
    "ReyaWsExecClient",
    "WsExecConnectionClosedError",
    "WsExecOperationError",
    "WsExecProtocolError",
]
