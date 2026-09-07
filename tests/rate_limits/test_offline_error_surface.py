"""Offline: does the SDK surface Rate-Limit v1 rejections usefully?

No network, no deployment — these run everywhere (``pytest -m offline``) and
pin the client-side contract the live modules depend on:

* **every venue verdict is an HTTP 400.** Throttling, the open-order caps, load
  shedding and the access decisions all share that status, so the status says
  only "the venue refused" and the body's ``error`` code says why. The retry
  strategy is keyed on the code, and the wait — where a wait helps — is the
  body's ``retryAfterMs``. Nothing here reads a ``Retry-After`` header, and a
  429 is infrastructure (per-IP) and never an account-level verdict;
* extraction must work whether or not the generated models know the code —
  the Rate-Limit v1 spec is not tagged yet, so the SDK is regenerated later.

Findings pinned here (see tests/rate_limits/README.md § Findings):

1. ``RequestErrorCode`` predates most of the v1 codes and is open-vocabulary, so
   typed parsing of e.g. ``NOT_WHITELISTED_ERROR`` does not raise — it widens
   the code to ``UNKNOWN``. Since 400 IS in the generated response map,
   ``ApiException.data`` is a typed ``RequestError`` that a helper would
   otherwise prefer, so the widening guard in ``rl_errors`` is what keeps the
   raw body as the faithful source for the code.
2. ``RequestError`` has no ``retryAfterMs`` field, though its
   ``additional_properties`` bag preserves the value. Both gaps are fixed by
   regeneration, and the helpers under test here are written to work
   identically before and after.
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
from sdk.open_api.exceptions import ApiException, BadRequestException
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
    HTTP_INFRASTRUCTURE_RATE_LIMITED,
    HTTP_VENUE_VERDICT,
    NOT_WHITELISTED_ERROR,
    OPEN_ORDER_COUNT_EXCEEDED_ERROR,
    OPEN_ORDER_NOTIONAL_EXCEEDED_ERROR,
    RATE_LIMITED_ERROR,
    UNAVAILABLE_ACCOUNT_OWNER_ERROR,
    UNAVAILABLE_MATCHING_ENGINE_ERROR,
    RetryPolicy,
    retry_policy,
)
from tests.rate_limits.rl_errors import (
    UNKNOWN_ENUM_MEMBER,
    assert_msg_rate_close_reason,
    assert_no_retry_hint,
    assert_retry_after_plausible,
    assert_venue_verdict,
    msg_rate_retry_after_s,
    rest_reject,
    ws_reject,
)

pytestmark = [pytest.mark.offline, pytest.mark.rate_limits]

#: The map the generated order-entry endpoints pass. It has always listed
#: exactly these three, which is why a 400-only verdict contract needs no
#: regeneration to be deserializable: the venue's own statuses are already in it
#: and a 429 (infrastructure, per-IP) deliberately is not.
ORDER_ENTRY_RESPONSE_TYPES_MAP = {"200": "CreateOrderResponse", "400": "RequestError", "500": "ServerError"}

#: Every Rate-Limit v1 code, with the retry strategy the enum's description
#: assigns it. The codes are what a client branches on; the status never is.
V1_CODES = (
    RATE_LIMITED_ERROR,
    OPEN_ORDER_COUNT_EXCEEDED_ERROR,
    OPEN_ORDER_NOTIONAL_EXCEEDED_ERROR,
    CAPACITY_LIMITED_ERROR,
    ACCOUNT_SUSPENDED_ERROR,
    NOT_WHITELISTED_ERROR,
    UNAVAILABLE_ACCOUNT_OWNER_ERROR,
)

#: The codes that carry no ``retryAfterMs``: no wait clears a cap or an access
#: decision, and a request that was never evaluated is retried unchanged.
NO_HINT_CODES = (
    OPEN_ORDER_COUNT_EXCEEDED_ERROR,
    OPEN_ORDER_NOTIONAL_EXCEEDED_ERROR,
    ACCOUNT_SUSPENDED_ERROR,
    NOT_WHITELISTED_ERROR,
    UNAVAILABLE_ACCOUNT_OWNER_ERROR,
    UNAVAILABLE_MATCHING_ENGINE_ERROR,
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
            response_types_map=response_types_map or ORDER_ENTRY_RESPONSE_TYPES_MAP,
        )
    return excinfo.value


def test_rate_limited_is_a_400_carrying_the_code_and_the_hint_on_the_body() -> None:
    """RATE_LIMITED_ERROR → 400 + ``error`` + ``retryAfterMs``, no header.

    This is the shape the whole contract turns on: the status is the same 400 an
    input-validation failure gets, so a client that branched on it would learn
    nothing, and the exact wait it must honour is a body field rather than a
    ``Retry-After`` header the venue never sends.
    """
    exc = _raise_through_sdk(
        HTTP_VENUE_VERDICT,
        {"error": RATE_LIMITED_ERROR, "message": "too many creates", "retryAfterMs": 3000},
    )

    assert isinstance(exc, BadRequestException)
    reject = rest_reject(exc)
    assert_venue_verdict(reject, "offline rate limited")
    assert reject.code == RATE_LIMITED_ERROR
    assert reject.message == "too many creates"
    assert reject.retry_after_header is None
    assert reject.retry_after_ms == 3000
    assert assert_retry_after_plausible(reject, 60.0, "offline rate limited") == 3.0
    assert retry_policy(reject.code) is RetryPolicy.AFTER_HINT


def test_capacity_limited_is_a_400_that_asks_for_a_back_off() -> None:
    """CAPACITY_LIMITED_ERROR is a venue verdict, NOT a 5xx.

    Load shedding used to arrive as a 503, which a generic client treats as an
    outage and a generic retry helper treats as transient infrastructure (see
    ``tests/helpers/reya_tester/retry.py``, which retries 502/503/504). On 400 it
    reaches the caller as a decision with a code and a hint, and the back-off is
    the client's to honour rather than a server-error retry loop's.
    """
    exc = _raise_through_sdk(
        HTTP_VENUE_VERDICT,
        {"error": CAPACITY_LIMITED_ERROR, "message": "engine at high watermark", "retryAfterMs": 250},
    )

    assert isinstance(exc, BadRequestException)
    reject = rest_reject(exc)
    assert_venue_verdict(reject, "offline capacity limited")
    assert reject.code == CAPACITY_LIMITED_ERROR
    assert reject.retry_after_ms == 250
    assert retry_policy(reject.code) is RetryPolicy.BACK_OFF


@pytest.mark.parametrize("code", [NOT_WHITELISTED_ERROR, ACCOUNT_SUSPENDED_ERROR])
def test_access_decisions_are_400s_with_no_hint(code: str) -> None:
    """An access decision answers "not you", never "not yet".

    There is no wait that clears a whitelist or a suspension, so the rejection
    must advertise none — a hint would invite a client to retry-loop against a
    decision that will not change.
    """
    exc = _raise_through_sdk(HTTP_VENUE_VERDICT, {"error": code, "message": "no"})

    reject = rest_reject(exc)
    assert_venue_verdict(reject, f"offline {code}")
    assert reject.code == code
    assert_no_retry_hint(reject, f"offline {code}")
    assert retry_policy(reject.code) is RetryPolicy.NOT_RETRYABLE


@pytest.mark.parametrize("code", [OPEN_ORDER_COUNT_EXCEEDED_ERROR, OPEN_ORDER_NOTIONAL_EXCEEDED_ERROR])
def test_the_open_order_caps_are_400s_with_no_hint(code: str) -> None:
    """A cap is a standing state, not a burst: waiting cannot clear it.

    Both caps clear only when resting orders are cancelled or filled, so neither
    carries ``retryAfterMs`` on either transport — the remedy is to free
    capacity, and a backoff hint would name a wait that resolves nothing.
    """
    exc = _raise_through_sdk(HTTP_VENUE_VERDICT, {"error": code, "message": "cap reached"})

    reject = rest_reject(exc)
    assert_venue_verdict(reject, f"offline {code}")
    assert reject.code == code
    assert_no_retry_hint(reject, f"offline {code}")
    assert retry_policy(reject.code) is RetryPolicy.NOT_RETRYABLE


@pytest.mark.parametrize("code", [UNAVAILABLE_MATCHING_ENGINE_ERROR, UNAVAILABLE_ACCOUNT_OWNER_ERROR])
def test_the_retry_unchanged_family_survives_the_widening_guard(code: str) -> None:
    """A request that was never evaluated: retry it unchanged, after a short delay.

    ``UNAVAILABLE_ACCOUNT_OWNER_ERROR`` is the newer member — the venue's edge
    could not resolve the account's owner, so the allowlist gate never ran and
    nothing about the request reached a verdict. It is deliberately NOT
    ``CAPACITY_LIMITED_ERROR``: a shed asks for a back-off, while a lookup
    failure asks for an immediate retry where a different outcome is likely.
    The two are parametrized together because the pinned enum knows one of them
    and not the other, and both must extract identically — the widening guard is
    the only thing that makes that true.
    """
    exc = _raise_through_sdk(HTTP_VENUE_VERDICT, {"error": code, "message": "not evaluated"})

    reject = rest_reject(exc)
    assert_venue_verdict(reject, f"offline {code}")
    assert reject.code == code, "the raw body's code must survive an enum that widened it to UNKNOWN"
    assert_no_retry_hint(reject, f"offline {code}")
    assert retry_policy(reject.code) is RetryPolicy.UNCHANGED
    assert retry_policy(CAPACITY_LIMITED_ERROR) is not RetryPolicy.UNCHANGED


def test_the_new_account_owner_code_is_widened_by_the_pinned_enum() -> None:
    """The widening guard is load-bearing here, not incidental.

    ``UNAVAILABLE_ACCOUNT_OWNER_ERROR`` post-dates the pinned spec, so typed
    parsing resolves it to ``UNKNOWN`` and a helper that trusted
    ``ApiException.data`` would report the sentinel. Pinned in both directions
    so regeneration flips it rather than breaking it.
    """
    exc = _raise_through_sdk(
        HTTP_VENUE_VERDICT,
        {"error": UNAVAILABLE_ACCOUNT_OWNER_ERROR, "message": "owner lookup failed"},
    )

    typed = exc.data
    assert typed is not None, "400 is in the response map, so the SDK does hand back a typed RequestError"
    if UNAVAILABLE_ACCOUNT_OWNER_ERROR in {member.value for member in RequestErrorCode}:
        assert typed.error.value == UNAVAILABLE_ACCOUNT_OWNER_ERROR
    else:
        assert typed.error.value == UNKNOWN_ENUM_MEMBER
    assert rest_reject(exc).code == UNAVAILABLE_ACCOUNT_OWNER_ERROR


@pytest.mark.parametrize("code", V1_CODES)
def test_every_v1_code_extracts_without_the_generated_enum(code: str) -> None:
    """Every v1 code extracts from the raw payload, enum coverage or not.

    Six of the seven are missing from ``RequestErrorCode`` today — only
    ``RATE_LIMITED_ERROR`` is present — which is what makes the live modules
    safe to write before the spec is tagged.
    """
    exc = _raise_through_sdk(HTTP_VENUE_VERDICT, {"error": code, "message": "m"})
    assert rest_reject(exc).code == code


def test_the_generated_enum_is_missing_exactly_the_six_documented_codes() -> None:
    """Pin the enum gap the suite's plain-string codes work around.

    Stated as an exact set rather than a count so a partial regeneration is
    caught: the moment the spec is tagged this flips to an empty set and the
    ``TODO(post-regen)`` in ``rl_errors`` becomes actionable.
    """
    known = {member.value for member in RequestErrorCode}
    missing = set(V1_CODES) - known
    assert missing in (
        {
            NOT_WHITELISTED_ERROR,
            ACCOUNT_SUSPENDED_ERROR,
            CAPACITY_LIMITED_ERROR,
            OPEN_ORDER_COUNT_EXCEEDED_ERROR,
            OPEN_ORDER_NOTIONAL_EXCEEDED_ERROR,
            UNAVAILABLE_ACCOUNT_OWNER_ERROR,
        },
        set(),
    ), f"unexpected RequestErrorCode coverage; missing v1 codes: {sorted(missing)}"


def test_a_429_is_infrastructure_and_never_a_venue_verdict() -> None:
    """429 is reserved for per-IP limits in front of the API.

    The venue does not emit it, so it carries no ``RequestErrorCode`` and means
    nothing about an account. Pinned as a REFUSAL: were a deployment to answer a
    throttle with 429, ``assert_venue_verdict`` has to fail rather than wave it
    through on the strength of the code in the body.
    """
    exc = _raise_through_sdk(
        HTTP_INFRASTRUCTURE_RATE_LIMITED,
        {"error": RATE_LIMITED_ERROR, "message": "wrong status for a venue verdict"},
    )

    reject = rest_reject(exc)
    assert reject.status == HTTP_INFRASTRUCTURE_RATE_LIMITED
    with pytest.raises(AssertionError, match=f"every venue verdict is HTTP {HTTP_VENUE_VERDICT}"):
        assert_venue_verdict(reject, "offline infrastructure 429")


def test_retry_after_assertion_is_not_vacuous() -> None:
    """A retryable verdict with NO hint must fail, not pass silently.

    The live bucket tests sleep on the value this returns, so a rejection that
    forgot ``retryAfterMs`` has to be loud — the alternative is a suite that
    quietly stops waiting and then reports the next verdict as a bucket that
    never refilled.
    """
    no_hint = _raise_through_sdk(HTTP_VENUE_VERDICT, {"error": RATE_LIMITED_ERROR, "message": "no hint"})
    with pytest.raises(AssertionError, match="must carry retryAfterMs"):
        assert_retry_after_plausible(rest_reject(no_hint), 60.0, "offline missing hint")

    implausible = _raise_through_sdk(
        HTTP_VENUE_VERDICT,
        {"error": RATE_LIMITED_ERROR, "message": "an hour", "retryAfterMs": 3_600_000},
    )
    with pytest.raises(AssertionError, match="implausible retryAfterMs"):
        assert_retry_after_plausible(rest_reject(implausible), 60.0, "offline implausible hint")


def test_a_stray_retry_after_header_is_caught_rather_than_believed() -> None:
    """The hint lives on the body; a header alongside it is two contracts.

    The venue never sends ``Retry-After``, so a rejection carrying one is a
    deployment drifting from the spec — and a client reading the header would be
    honouring a number nothing defines. Both assertions reject it.
    """
    with_header = _raise_through_sdk(
        HTTP_VENUE_VERDICT,
        {"error": RATE_LIMITED_ERROR, "message": "two hints", "retryAfterMs": 3000},
        headers={"Retry-After": "3"},
    )
    with pytest.raises(AssertionError, match="never a Retry-After header"):
        assert_retry_after_plausible(rest_reject(with_header), 60.0, "offline header + body")

    gated_with_header = _raise_through_sdk(
        HTTP_VENUE_VERDICT,
        {"error": NOT_WHITELISTED_ERROR, "message": "wrongly retryable"},
        headers={"Retry-After": "5"},
    )
    with pytest.raises(AssertionError, match="never answers with a Retry-After header"):
        assert_no_retry_hint(rest_reject(gated_with_header), "offline access decision with header")


def test_no_retry_hint_assertion_is_not_vacuous() -> None:
    """An access decision that DOES carry ``retryAfterMs`` must fail.

    The gate, eject and cap modules all assert the hint is absent; that is only
    worth anything if a hint would actually trip it.
    """
    clean = _raise_through_sdk(HTTP_VENUE_VERDICT, {"error": NOT_WHITELISTED_ERROR, "message": "no hint"})
    assert_no_retry_hint(rest_reject(clean), "offline access decision clean")

    hinted = _raise_through_sdk(
        HTTP_VENUE_VERDICT,
        {"error": NOT_WHITELISTED_ERROR, "message": "wrongly retryable", "retryAfterMs": 5000},
    )
    with pytest.raises(AssertionError, match="must carry no retryAfterMs"):
        assert_no_retry_hint(rest_reject(hinted), "offline access decision with hint")


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

    A code the enum does not carry never raises: ``RequestErrorCode`` is
    open-vocabulary, so ``from_dict`` succeeds and widens it to ``UNKNOWN``.
    That is precisely why ``rl_errors`` reads the code off the raw body rather
    than trusting a typed payload.
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
    parsed = RequestError.from_dict(unknown_payload)
    assert parsed is not None
    if NOT_WHITELISTED_ERROR in {member.value for member in RequestErrorCode}:
        assert parsed.error.value == NOT_WHITELISTED_ERROR
    else:
        assert parsed.error.value == UNKNOWN_ENUM_MEMBER


def test_ws_exec_error_envelope_extraction() -> None:
    """The ws-exec envelope carries the code and the optional ``retryAfterMs``.

    The envelope contract is unchanged by the 400-only REST rule, and that is
    the point of the rule: the same code and the same hint reach a client on
    either transport, so REST's status was the only thing that ever differed
    and it now says nothing a client needs.
    """
    frame = {
        "id": "abc123",
        "ok": False,
        "error": {"error": RATE_LIMITED_ERROR, "message": "slow down", "retryAfterMs": 750},
    }
    reject = ws_reject(frame, "offline ws envelope")
    assert reject.code == RATE_LIMITED_ERROR
    assert reject.retry_after_ms == 750

    # The hint accompanies only the codes a wait actually helps: it never frees
    # a cap slot, never clears an access decision, and a request that was never
    # evaluated is retried unchanged rather than after a stated delay.
    for code in NO_HINT_CODES:
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
