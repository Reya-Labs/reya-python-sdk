"""Offline: does the SDK surface Rate-Limit v1 rejections usefully?

No network, no deployment — these run everywhere (``pytest -m offline``) and
pin the client-side contract the live modules depend on:

* a 429 / 503 / 403 response must reach the caller as an ``ApiException``
  carrying the HTTP status, the response HEADERS (so ``Retry-After`` is
  readable), and the raw JSON body (so the error code and ``retryAfterMs``
  are readable);
* extraction must work whether or not the generated models know the code —
  the Rate-Limit v1 spec is not tagged yet, so the SDK is regenerated later.

Findings pinned here (see tests/rate_limits/README.md § Findings):

1. The generated ``_response_types_map`` for the order-entry endpoints lists
   only ``200`` / ``400`` / ``500``. A 429 / 503 / 403 is therefore never
   deserialized and ``ApiException.data`` is ``None`` — the payload survives
   ONLY on ``ApiException.body``. Nothing is lost (headers and body are both
   intact), so this is a spec gap, not an SDK bug: no client hacking here.
2. ``RequestErrorCode`` predates the v1 codes, so typed parsing of e.g.
   ``NOT_WHITELISTED_ERROR`` raises. ``RequestError`` also has no
   ``retryAfterMs`` field, though its ``additional_properties`` bag preserves
   the value. Both are fixed by regeneration, and the helpers under test here
   are written to work identically before and after.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import asyncio
import json
import struct

import pytest
from websocket import (  # type: ignore[attr-defined]  # pylint: disable=no-name-in-module
    ABNF,
    WebSocketConnectionClosedException,
    WebSocketTimeoutException,
)

from sdk.open_api.api_client import ApiClient
from sdk.open_api.configuration import Configuration
from sdk.open_api.exceptions import ApiException, ForbiddenException, ServiceException
from sdk.open_api.models.request_error import RequestError
from sdk.open_api.models.request_error_code import RequestErrorCode
from sdk.reya_ws_exec import (
    WS_CLOSE_MSG_RATE_EXCEEDED,
    ReyaWsExecClient,
    WsExecConnectionClosedError,
    WsExecOperationError,
    WsExecProtocolError,
)
from sdk.reya_ws_exec.client import _parse_close_payload
from tests.rate_limits.rl_config import (
    ACCOUNT_SUSPENDED_ERROR,
    CAPACITY_LIMITED_ERROR,
    HTTP_CAPACITY_LIMITED,
    HTTP_FORBIDDEN,
    HTTP_RATE_LIMITED,
    NOT_WHITELISTED_ERROR,
    OPEN_ORDER_COUNT_EXCEEDED_ERROR,
    OPEN_ORDER_NOTIONAL_EXCEEDED_ERROR,
    RATE_LIMITED_ERROR,
)
from tests.rate_limits.rl_errors import (
    assert_msg_rate_close_reason,
    assert_no_retry_after,
    assert_retry_after_plausible,
    msg_rate_retry_after_s,
    rest_reject,
    ws_reject,
)

pytestmark = [pytest.mark.offline, pytest.mark.rate_limits]

#: The map the generated order-entry endpoints pass today.
CURRENT_RESPONSE_TYPES_MAP = {"200": "CreateOrderResponse", "400": "RequestError", "500": "ServerError"}

#: What the map becomes once the Rate-Limit v1 spec documents the new statuses.
#: Both must produce the same ``RestReject``, which is what makes the live
#: modules regeneration-proof.
POST_REGEN_RESPONSE_TYPES_MAP = {
    **CURRENT_RESPONSE_TYPES_MAP,
    "403": "RequestError",
    "429": "RequestError",
    "503": "RequestError",
}

RESPONSE_MAPS = pytest.mark.parametrize(
    "response_types_map",
    [
        pytest.param(CURRENT_RESPONSE_TYPES_MAP, id="current-spec"),
        pytest.param(POST_REGEN_RESPONSE_TYPES_MAP, id="post-regen-spec"),
    ],
)


class _FakeRestResponse:
    """Minimal stand-in for ``sdk.open_api.rest.RESTResponse``."""

    def __init__(self, status: int, payload: dict[str, Any], headers: dict[str, str] | None = None) -> None:
        self.status = status
        self.reason = "fake"
        self.data = json.dumps(payload).encode("utf-8")
        self._headers = {"Content-Type": "application/json", **(headers or {})}

    def getheaders(self) -> dict[str, str]:
        return self._headers

    def getheader(self, name: str, default: str | None = None) -> str | None:
        for key, value in self._headers.items():
            if key.lower() == name.lower():
                return value
        return default


def _raise_through_sdk(
    status: int,
    payload: dict[str, Any],
    headers: dict[str, str] | None = None,
    response_types_map: dict[str, str] | None = None,
) -> ApiException:
    """Drive a response through the real deserializer and return what it raises."""
    api_client = ApiClient(Configuration(host="https://invalid.example"))
    response = _FakeRestResponse(status, payload, headers)
    with pytest.raises(ApiException) as excinfo:
        api_client.response_deserialize(
            response_data=response,  # type: ignore[arg-type]
            response_types_map=response_types_map or CURRENT_RESPONSE_TYPES_MAP,
        )
    return excinfo.value


@RESPONSE_MAPS
def test_rate_limited_429_exposes_status_headers_and_code(response_types_map: dict[str, str]) -> None:
    """429 → status + Retry-After header + RATE_LIMITED_ERROR.

    The body is the REAL edge body: ``{error, message}``. On REST the backoff
    hint travels in the ``Retry-After`` header ONLY — ``retryAfterMs`` is the
    ws-exec envelope's field — so the header is what the live modules may
    assert and the body's hint must read as absent here.
    """
    exc = _raise_through_sdk(
        HTTP_RATE_LIMITED,
        {"error": RATE_LIMITED_ERROR, "message": "too many creates"},
        headers={"Retry-After": "3"},
        response_types_map=response_types_map,
    )

    reject = rest_reject(exc)
    assert reject.status == HTTP_RATE_LIMITED
    assert reject.code == RATE_LIMITED_ERROR
    assert reject.message == "too many creates"
    assert reject.retry_after_ms is None
    assert assert_retry_after_plausible(reject, 60.0, "offline 429") == 3.0


def test_rest_body_retry_after_ms_is_a_defensive_fallback_not_a_contract() -> None:
    """Document the body read: it works, but no REST contract produces it.

    The edge builds its error body as ``{error, message}`` and puts the hint in
    ``Retry-After``. ``rest_reject`` still reads ``retryAfterMs`` off the body so
    a deployment that starts echoing the ws-exec field is not silently ignored —
    but this is the ONLY place that behaviour is exercised, deliberately, so no
    live REST assertion can come to depend on it.
    """
    exc = _raise_through_sdk(
        HTTP_RATE_LIMITED,
        {"error": RATE_LIMITED_ERROR, "message": "synthetic body", "retryAfterMs": 2500},
        headers={"Retry-After": "3"},
    )
    assert rest_reject(exc).retry_after_ms == 2500


@RESPONSE_MAPS
def test_capacity_limited_503_is_a_service_exception(response_types_map: dict[str, str]) -> None:
    """503 → ServiceException; Retry-After is OPTIONAL on the capacity path."""
    exc = _raise_through_sdk(
        HTTP_CAPACITY_LIMITED,
        {"error": CAPACITY_LIMITED_ERROR, "message": "engine at high watermark"},
        response_types_map=response_types_map,
    )

    assert isinstance(exc, ServiceException)
    reject = rest_reject(exc)
    assert reject.status == HTTP_CAPACITY_LIMITED
    assert reject.code == CAPACITY_LIMITED_ERROR
    assert reject.retry_after_header is None
    assert reject.retry_after_s is None


@RESPONSE_MAPS
def test_not_whitelisted_403_is_a_forbidden_exception(response_types_map: dict[str, str]) -> None:
    """403 → ForbiddenException, so callers can branch on the class alone."""
    exc = _raise_through_sdk(
        HTTP_FORBIDDEN,
        {"error": NOT_WHITELISTED_ERROR, "message": "wallet is not whitelisted"},
        response_types_map=response_types_map,
    )

    assert isinstance(exc, ForbiddenException)
    reject = rest_reject(exc)
    assert reject.status == HTTP_FORBIDDEN
    assert reject.code == NOT_WHITELISTED_ERROR


@pytest.mark.parametrize(
    "code",
    [
        RATE_LIMITED_ERROR,
        OPEN_ORDER_COUNT_EXCEEDED_ERROR,
        OPEN_ORDER_NOTIONAL_EXCEEDED_ERROR,
        CAPACITY_LIMITED_ERROR,
        ACCOUNT_SUSPENDED_ERROR,
        NOT_WHITELISTED_ERROR,
    ],
)
def test_every_v1_code_extracts_without_the_generated_enum(code: str) -> None:
    """All six v1 codes extract from the raw payload, enum coverage or not.

    FIVE of the six are missing from ``RequestErrorCode`` today — only
    ``RATE_LIMITED_ERROR`` is present — which is what makes the live modules
    safe to write before the spec is tagged.
    """
    exc = _raise_through_sdk(HTTP_RATE_LIMITED, {"error": code, "message": "m"})
    assert rest_reject(exc).code == code


def test_the_generated_enum_is_missing_exactly_the_five_documented_codes() -> None:
    """Pin the enum gap the suite's plain-string codes work around.

    Stated as an exact set rather than a count so a partial regeneration is
    caught: the moment the spec is tagged this flips to an empty set and the
    ``TODO(post-regen)`` in ``rl_errors`` becomes actionable.
    """
    v1_codes = {
        RATE_LIMITED_ERROR,
        OPEN_ORDER_COUNT_EXCEEDED_ERROR,
        OPEN_ORDER_NOTIONAL_EXCEEDED_ERROR,
        CAPACITY_LIMITED_ERROR,
        ACCOUNT_SUSPENDED_ERROR,
        NOT_WHITELISTED_ERROR,
    }
    known = {member.value for member in RequestErrorCode}
    missing = v1_codes - known
    assert missing in (
        {
            NOT_WHITELISTED_ERROR,
            ACCOUNT_SUSPENDED_ERROR,
            CAPACITY_LIMITED_ERROR,
            OPEN_ORDER_COUNT_EXCEEDED_ERROR,
            OPEN_ORDER_NOTIONAL_EXCEEDED_ERROR,
        },
        set(),
    ), f"unexpected RequestErrorCode coverage; missing v1 codes: {sorted(missing)}"


def test_retry_after_assertion_is_not_vacuous() -> None:
    """A 429 with NO Retry-After must fail the assertion, not pass silently."""
    exc = _raise_through_sdk(HTTP_RATE_LIMITED, {"error": RATE_LIMITED_ERROR, "message": "no header"})
    with pytest.raises(AssertionError, match="must carry a Retry-After header"):
        assert_retry_after_plausible(rest_reject(exc), 60.0, "offline missing-header")


def test_403_no_retry_after_assertion_is_not_vacuous() -> None:
    """A 403 that DOES carry Retry-After must fail the assertion.

    The live gate and eject modules assert that access-control rejects carry no
    ``Retry-After`` — a 403 answers "not you", never "not yet". That assertion is
    only worth anything if a header would actually trip it.
    """
    clean = _raise_through_sdk(HTTP_FORBIDDEN, {"error": NOT_WHITELISTED_ERROR, "message": "no header"})
    assert_no_retry_after(rest_reject(clean), "offline 403 clean")

    with_header = _raise_through_sdk(
        HTTP_FORBIDDEN,
        {"error": NOT_WHITELISTED_ERROR, "message": "wrongly retryable"},
        headers={"Retry-After": "5"},
    )
    with pytest.raises(AssertionError, match="must carry no Retry-After"):
        assert_no_retry_after(rest_reject(with_header), "offline 403 with header")


def _request_error_has_typed_retry_after() -> bool:
    """Has ``RequestError`` been regenerated with a real ``retryAfterMs`` field?

    ``model_fields`` is keyed by the PYTHON attribute name, and
    openapi-generator emits ``retry_after_ms`` with ``alias="retryAfterMs"``.
    Probing ``model_fields`` for the wire name would therefore stay false
    forever — vacuously "pre-regen" — so both the snake_case name and the
    declared aliases are checked.
    """
    return any(
        name == "retry_after_ms" or field.alias == "retryAfterMs" for name, field in RequestError.model_fields.items()
    )


def test_request_error_model_gap_matches_the_helper_strategy() -> None:
    """Pin the generated-model gap the helpers work around.

    Pre-regen ``retryAfterMs`` has no field on ``RequestError`` and survives in
    ``additional_properties``; post-regen ``from_dict`` routes it into the
    typed field instead and the bag is empty, because a key only lands in
    ``additional_properties`` when it is absent from ``__properties``. Both
    halves are written conditionally so regeneration flips them rather than
    breaking them — which is exactly what ``rl_errors`` already tolerates.
    """
    known = RequestError.from_dict({"error": RATE_LIMITED_ERROR, "message": "m", "retryAfterMs": 1234})
    assert known is not None

    if _request_error_has_typed_retry_after():
        # Read dynamically: the attribute does not exist on the pre-regen model,
        # so a static access would not type-check until the spec is tagged.
        assert getattr(known, "retry_after_ms") == 1234
        assert known.to_dict()["retryAfterMs"] == 1234
    else:
        assert "retry_after_ms" not in RequestError.model_fields
        assert known.additional_properties.get("retryAfterMs") == 1234

    unknown_payload = {"error": NOT_WHITELISTED_ERROR, "message": "m"}
    if NOT_WHITELISTED_ERROR in {member.value for member in RequestErrorCode}:
        parsed = RequestError.from_dict(unknown_payload)
        assert parsed is not None and parsed.error.value == NOT_WHITELISTED_ERROR
    else:
        with pytest.raises(Exception):  # pylint: disable=broad-exception-caught
            RequestError.from_dict(unknown_payload)


def test_ws_exec_error_envelope_extraction() -> None:
    """The ws-exec envelope carries the code and the optional ``retryAfterMs``."""
    frame = {
        "id": "abc123",
        "ok": False,
        "error": {"error": RATE_LIMITED_ERROR, "message": "slow down", "retryAfterMs": 750},
    }
    reject = ws_reject(frame, "offline ws envelope")
    assert reject.code == RATE_LIMITED_ERROR
    assert reject.retry_after_ms == 750

    # The hint is set ONLY alongside RATE_LIMITED: waiting does not free a cap
    # slot and never clears an access-control verdict, so both must read absent.
    for code in (OPEN_ORDER_COUNT_EXCEEDED_ERROR, ACCOUNT_SUSPENDED_ERROR, NOT_WHITELISTED_ERROR):
        without_hint = ws_reject({"ok": False, "error": {"error": code, "message": "nope"}}, f"offline ws {code}")
        assert without_hint.code == code
        assert without_hint.retry_after_ms is None


def test_ws_exec_ok_envelope_is_not_mistaken_for_a_reject() -> None:
    with pytest.raises(AssertionError, match="expected ok=false"):
        ws_reject({"ok": True, "payload": {"orderId": "1"}}, "offline ws ok")


def test_ws_exec_operation_error_carries_the_retry_hint() -> None:
    """``WsExecOperationError`` exposes ``retry_after_ms`` off the envelope.

    The hand-written ws-exec client is not regenerated from the spec, so this
    accessor is the only thing that stops a ws-exec caller from having to
    re-parse the frame the client already parsed.
    """
    limited = ReyaWsExecClient._extract_ok_payload  # pylint: disable=protected-access

    with pytest.raises(WsExecOperationError) as excinfo:
        limited(
            {"ok": False, "error": {"error": RATE_LIMITED_ERROR, "message": "slow down", "retryAfterMs": 750}},
            "req-1",
        )
    assert excinfo.value.code == RATE_LIMITED_ERROR
    assert excinfo.value.retry_after_ms == 750

    with pytest.raises(WsExecOperationError) as no_hint:
        limited({"ok": False, "error": {"error": ACCOUNT_SUSPENDED_ERROR, "message": "suspended"}}, "req-2")
    assert no_hint.value.retry_after_ms is None


def test_close_frame_payload_yields_the_4029_code_and_reason() -> None:
    """A close frame's status code and reason are both recoverable.

    ``WebSocket.recv`` collapses a close frame into ``""``, which is why the
    reader reads at the frame layer instead: the per-connection message-rate
    cap is expressed purely as a close code, so swallowing it would make the
    control invisible to a client.
    """
    body = struct.pack("!H", WS_CLOSE_MSG_RATE_EXCEEDED) + b"MSG_RATE_EXCEEDED retry_after_ms=1000"
    assert _parse_close_payload(body) == (WS_CLOSE_MSG_RATE_EXCEEDED, "MSG_RATE_EXCEEDED retry_after_ms=1000")

    # RFC 6455 allows an empty body and a code-only body.
    assert _parse_close_payload(b"") == (None, None)
    assert _parse_close_payload(struct.pack("!H", 1000)) == (1000, None)


def test_the_4029_close_reason_grammar_is_shared_and_not_vacuous() -> None:
    """One reason grammar across BOTH WebSocket surfaces, and it rejects near-misses.

    The ws-exec relayer and the market-data socket emit the same string
    byte-for-byte, which is what lets a client key recovery on one branch. Both
    live floods assert it through the same helper, so the only thing that can
    make those assertions vacuous is a parser that accepts anything — pinned
    here, with the other close reasons a market-data client actually sees
    (``1013`` slow consumer, ``1012`` feed resync) as the near-misses.
    """
    assert assert_msg_rate_close_reason("MSG_RATE_EXCEEDED retry_after_ms=1500", 60.0, "offline 4029") == 1.5

    for near_miss in (
        None,
        "",
        "MSG_RATE_EXCEEDED",
        "MSG_RATE_EXCEEDED retry_after_ms=",
        "MSG_RATE_EXCEEDED retry_after_ms=abc",
        "msg_rate_exceeded retry_after_ms=1000",
        "slow consumer — resubscribe for fresh snapshot",
        "feed resync — resubscribe for fresh snapshot",
    ):
        assert msg_rate_retry_after_s(near_miss) is None, f"{near_miss!r} must not parse as a rate-shed hint"
        with pytest.raises(AssertionError, match="must carry the backoff hint"):
            assert_msg_rate_close_reason(near_miss, 60.0, "offline 4029 near-miss")

    with pytest.raises(AssertionError, match="implausible retry_after_ms"):
        assert_msg_rate_close_reason("MSG_RATE_EXCEEDED retry_after_ms=600000", 60.0, "offline 4029 implausible")


class _StubWebSocket:
    """Replays a scripted sequence, then behaves like a dead socket.

    An entry is either an ``(opcode, frame)`` tuple to return or an exception
    instance to raise, so a script can interleave recv timeouts with frames.

    ``write_error`` is what a socket the server is tearing down does to the
    reader's answering writes — the close echo and the pong. It is the whole
    point of the two regression tests below: those writes must never be able to
    cost the reader a frame it already has.
    """

    def __init__(self, script: list[Any], write_error: BaseException | None = None) -> None:
        self._script = list(script)
        self._write_error = write_error
        self.echoed_close = 0
        self.pongs: list[bytes] = []

    def settimeout(self, _timeout: float) -> None:
        pass

    def recv_frame(self) -> Any:
        if not self._script:
            raise WebSocketConnectionClosedException("stub drained")
        step = self._script.pop(0)
        if isinstance(step, BaseException):
            raise step
        opcode, frame = cast(tuple[int, Any], step)
        return SimpleNamespace(opcode=opcode, data=frame.data, fin=getattr(frame, "fin", 1))

    def send_close(self) -> None:
        self.echoed_close += 1
        if self._write_error is not None:
            raise self._write_error

    def pong(self, payload: bytes) -> None:
        self.pongs.append(payload)
        if self._write_error is not None:
            raise self._write_error


async def test_reader_loop_fails_in_flight_requests_on_a_4029_close() -> None:
    """The close is read, recorded, and turned into indeterminate failures.

    Drives the real reader loop over a scripted close frame. Without this the
    4029 path would only be exercised on a live relayer: the previous reader
    caught a server close with the same ``except`` as its own recv timeout and
    continued, so in-flight requests hung to their own deadline and the code was
    never read at all.
    """
    client = ReyaWsExecClient(rest_client=cast(Any, None), ws_url="wss://invalid.example")
    close_body = struct.pack("!H", WS_CLOSE_MSG_RATE_EXCEEDED) + b"MSG_RATE_EXCEEDED retry_after_ms=1000"
    # pylint: disable=protected-access
    client._loop = asyncio.get_running_loop()
    client._ws = cast(Any, _StubWebSocket([(ABNF.OPCODE_CLOSE, SimpleNamespace(data=close_body))]))
    future = client._register("req-1")

    await asyncio.to_thread(client._reader_loop)

    with pytest.raises(WsExecConnectionClosedError) as excinfo:
        await asyncio.wait_for(future, timeout=5.0)
    assert excinfo.value.close_code == WS_CLOSE_MSG_RATE_EXCEEDED
    assert excinfo.value.request_id == "req-1"
    assert client.last_close_code == WS_CLOSE_MSG_RATE_EXCEEDED
    assert client.last_close_reason == "MSG_RATE_EXCEEDED retry_after_ms=1000"


async def test_a_close_whose_echo_fails_still_yields_its_code() -> None:
    """The code is recorded BEFORE the echo, so a dead-socket write cannot eat it.

    This is the shed's real timing: ws-exec tears the socket down within
    milliseconds of closing, so by the time the reader answers the close that
    write raises. Reading through ``recv_data_frame`` put the echo INSIDE the
    read — its ``OSError`` surfaced as an abrupt transport failure, and a 4029
    that had been received in full was recorded as ``None`` on roughly half of
    live runs, making the rate kill indistinguishable from an idle socket.
    """
    client = ReyaWsExecClient(rest_client=cast(Any, None), ws_url="wss://invalid.example")
    close_body = struct.pack("!H", WS_CLOSE_MSG_RATE_EXCEEDED) + b"MSG_RATE_EXCEEDED retry_after_ms=1000"
    stub = _StubWebSocket(
        [(ABNF.OPCODE_CLOSE, SimpleNamespace(data=close_body))],
        write_error=OSError("Broken pipe"),
    )
    # pylint: disable=protected-access
    client._loop = asyncio.get_running_loop()
    client._ws = cast(Any, stub)
    future = client._register("req-4")

    await asyncio.to_thread(client._reader_loop)

    assert stub.echoed_close == 1, "the echo is still attempted — just after the code is safely recorded"
    assert client.last_close_code == WS_CLOSE_MSG_RATE_EXCEEDED
    assert client.last_close_reason == "MSG_RATE_EXCEEDED retry_after_ms=1000"
    with pytest.raises(WsExecConnectionClosedError) as excinfo:
        await asyncio.wait_for(future, timeout=5.0)
    assert excinfo.value.close_code == WS_CLOSE_MSG_RATE_EXCEEDED


async def test_a_failed_pong_never_discards_the_frames_behind_it() -> None:
    """A ping the reader cannot answer must not end the read.

    The same hazard as the close echo, one frame earlier: ``recv_data_frame``
    pongs inside the read, so a pong raising on a dying socket would take the
    reply behind it — and the close code behind that — down with it.
    """
    client = ReyaWsExecClient(rest_client=cast(Any, None), ws_url="wss://invalid.example")
    close_body = struct.pack("!H", WS_CLOSE_MSG_RATE_EXCEEDED) + b"MSG_RATE_EXCEEDED retry_after_ms=1000"
    reply = json.dumps({"type": "pong", "id": "req-5"}).encode("utf-8")
    stub = _StubWebSocket(
        [
            (ABNF.OPCODE_PING, SimpleNamespace(data=b"keepalive")),
            (ABNF.OPCODE_TEXT, SimpleNamespace(data=reply)),
            (ABNF.OPCODE_CLOSE, SimpleNamespace(data=close_body)),
        ],
        write_error=OSError("Broken pipe"),
    )
    # pylint: disable=protected-access
    client._loop = asyncio.get_running_loop()
    client._ws = cast(Any, stub)
    future = client._register("req-5")

    await asyncio.to_thread(client._reader_loop)

    assert stub.pongs == [b"keepalive"], "the ping is still answered, best effort"
    assert await asyncio.wait_for(future, timeout=5.0) == {"type": "pong", "id": "req-5"}
    assert client.last_close_code == WS_CLOSE_MSG_RATE_EXCEEDED


async def test_a_fragmented_reply_is_reassembled_before_it_is_parsed() -> None:
    """Reading raw frames means owning reassembly; half a JSON reply parses as none.

    ws-exec answers in single unfragmented frames today, so this is the
    defensive half of the frame-layer read: were a reply ever split, dropping
    the fragments would look exactly like a server that never answered.
    """
    client = ReyaWsExecClient(rest_client=cast(Any, None), ws_url="wss://invalid.example")
    reply = json.dumps({"type": "pong", "id": "req-6"}).encode("utf-8")
    head, tail = reply[:9], reply[9:]
    # pylint: disable=protected-access
    client._loop = asyncio.get_running_loop()
    client._ws = cast(
        Any,
        _StubWebSocket(
            [
                (ABNF.OPCODE_TEXT, SimpleNamespace(data=head, fin=0)),
                (ABNF.OPCODE_CONT, SimpleNamespace(data=tail, fin=1)),
            ]
        ),
    )
    future = client._register("req-6")

    await asyncio.to_thread(client._reader_loop)

    assert await asyncio.wait_for(future, timeout=5.0) == {"type": "pong", "id": "req-6"}


async def test_sends_are_refused_once_the_connection_is_known_dead() -> None:
    """After the close, a NEW request is refused rather than silently buffered.

    A send into a dead socket can be accepted by the OS and go nowhere, leaving
    the caller waiting out its own 15 s deadline for a server that already hung
    up. Refusing immediately keeps the reconcile rule reachable.
    """
    client = ReyaWsExecClient(rest_client=cast(Any, None), ws_url="wss://invalid.example")
    close_body = struct.pack("!H", WS_CLOSE_MSG_RATE_EXCEEDED) + b"MSG_RATE_EXCEEDED retry_after_ms=1000"
    # pylint: disable=protected-access
    client._loop = asyncio.get_running_loop()
    client._ws = cast(Any, _StubWebSocket([(ABNF.OPCODE_CLOSE, SimpleNamespace(data=close_body))]))

    await asyncio.to_thread(client._reader_loop)

    with pytest.raises(WsExecConnectionClosedError) as excinfo:
        await client.ping()
    assert excinfo.value.close_code == WS_CLOSE_MSG_RATE_EXCEEDED


async def test_reader_loop_survives_its_own_recv_timeout() -> None:
    """An idle second is not a disconnect — the reader must keep reading.

    The inverse of the bug this file's other reader tests pin: the fix separated
    a server close from the 1 s recv timeout, and this is what stops the
    separation from being re-collapsed the other way. Were the timeout treated
    as a transport failure, every idle second would fail the in-flight requests
    and kill the thread, so the 4029 close would never be reached at all.
    """
    client = ReyaWsExecClient(rest_client=cast(Any, None), ws_url="wss://invalid.example")
    close_body = struct.pack("!H", WS_CLOSE_MSG_RATE_EXCEEDED) + b"idle then closed"
    pong = json.dumps({"type": "pong", "id": "req-3"}).encode("utf-8")
    # pylint: disable=protected-access
    client._loop = asyncio.get_running_loop()
    client._ws = cast(
        Any,
        _StubWebSocket(
            [
                WebSocketTimeoutException("idle"),
                WebSocketTimeoutException("still idle"),
                (ABNF.OPCODE_TEXT, SimpleNamespace(data=pong)),
                WebSocketTimeoutException("idle again"),
                (ABNF.OPCODE_CLOSE, SimpleNamespace(data=close_body)),
            ]
        ),
    )
    future = client._register("req-3")

    await asyncio.to_thread(client._reader_loop)

    # The pong arrived after two timeouts, so the request resolved rather than
    # being failed indeterminate by a reader that mistook idling for death.
    assert await asyncio.wait_for(future, timeout=5.0) == {"type": "pong", "id": "req-3"}
    assert client.last_close_code == WS_CLOSE_MSG_RATE_EXCEEDED


async def test_reader_loop_fails_in_flight_requests_on_an_abrupt_transport_failure() -> None:
    """A death with no close frame is indeterminate too, with no code to name."""
    client = ReyaWsExecClient(rest_client=cast(Any, None), ws_url="wss://invalid.example")
    # pylint: disable=protected-access
    client._loop = asyncio.get_running_loop()
    client._ws = cast(Any, _StubWebSocket([]))
    future = client._register("req-2")

    await asyncio.to_thread(client._reader_loop)

    with pytest.raises(WsExecConnectionClosedError) as excinfo:
        await asyncio.wait_for(future, timeout=5.0)
    assert excinfo.value.close_code is None
    assert client.last_close_code is None


def test_connection_closed_error_names_the_close_and_the_reconcile_rule() -> None:
    """The indeterminate error is loud about being indeterminate.

    A caller that sees this must reconcile, not re-send: the relayer may have
    forwarded the request to the engine before closing. Reporting it as a
    timeout would read as "never happened" and invite a duplicate order.
    """
    error = WsExecConnectionClosedError(WS_CLOSE_MSG_RATE_EXCEEDED, "MSG_RATE_EXCEEDED retry_after_ms=1000", "req-9")
    assert isinstance(error, WsExecProtocolError)
    assert error.close_code == WS_CLOSE_MSG_RATE_EXCEEDED
    assert error.request_id == "req-9"
    assert "indeterminate" in str(error)
    assert "openOrders" in str(error)
