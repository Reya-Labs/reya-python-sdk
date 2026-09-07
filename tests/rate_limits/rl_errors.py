"""Model-agnostic extraction of venue verdicts from REST and ws-exec.

Every venue verdict is an HTTP 400 carrying ``{error, message, retryAfterMs?}``
— the same code and the same hint the order-entry WebSocket puts on its
``{ok:false, error}`` envelope. The status says only that the venue refused, so
nothing here reads a retry decision out of it; the code is the contract.

Why raw dicts instead of the generated models:

* ``RequestErrorCode`` (sdk/open_api/models/request_error_code.py) is generated
  from the CURRENT tagged spec, which predates most of the Rate-Limit v1 codes.
  That enum is open-vocabulary — ``_missing_`` resolves an unrecognized member
  to ``UNKNOWN`` rather than raising — so parsing a ``NOT_WHITELISTED_ERROR``
  body through ``RequestError`` succeeds but LOSES the code. 400 IS in the
  generated response map, so ``ApiException.data`` is populated and would be
  preferred by default; the widening guard below is what keeps a code this SDK
  predates from being reported as ``UNKNOWN``.
* ``RequestError`` has no ``retryAfterMs`` field yet. It DOES carry an
  ``additional_properties`` bag, so the value survives a ``from_dict`` round
  trip — but reading the raw payload works both before and after regeneration.

TODO(post-regen): once the Rate-Limit v1 spec is tagged and the SDK models are
regenerated, tighten these helpers to prefer ``ApiException.data`` (a typed
``RequestError`` with ``retry_after_ms``) and keep the raw-dict path only as
the fallback.
"""

from __future__ import annotations

from typing import Any

import json
import re
from dataclasses import dataclass

from sdk.open_api.exceptions import ApiException
from tests.rate_limits.rl_config import HTTP_VENUE_VERDICT

#: The reason string that accompanies a ``4029`` inbound-message-rate close.
#: Both WebSocket surfaces emit it byte-for-byte — the ws-exec relayer and the
#: market-data socket — which is the point: one close code AND one reason
#: grammar means a client needs a single branch rather than a per-surface case.
#: The captured group is the advisory backoff a prompt reconnect waits out.
MSG_RATE_CLOSE_REASON_PATTERN = re.compile(r"^MSG_RATE_EXCEEDED retry_after_ms=(\d+)$")

#: What an open-vocabulary generated enum resolves an unrecognized member to.
#: Written by scripts/postprocess-openapi.py, so it survives regeneration.
UNKNOWN_ENUM_MEMBER = "UNKNOWN"


def _header(headers: Any, name: str) -> str | None:
    """Case-insensitive header read that tolerates ``None``, ``dict`` and the
    aiohttp ``CIMultiDictProxy`` ``ApiException`` actually carries."""
    if headers is None:
        return None
    getter = getattr(headers, "get", None)
    if callable(getter):
        value = getter(name)
        if value is not None:
            return str(value)
    items = getattr(headers, "items", None)
    if callable(items):
        for key, value in items():
            if str(key).lower() == name.lower():
                return str(value)
    return None


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _raw_payload(exc: ApiException) -> dict[str, Any] | None:
    # ``ApiException.body`` is the raw response text — the only place the
    # payload survives for statuses the generated response map does not list.
    # ``json.loads`` accepts str or bytes, so no decoding branch is needed.
    raw = exc.body
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _code_from_payload(payload: dict[str, Any] | None) -> str | None:
    if not payload:
        return None
    code = payload.get("error")
    if code is None:
        return None
    # A regenerated model hands back the enum; a raw body hands back a string.
    return str(getattr(code, "value", code))


def _widened_to_unknown(typed: dict[str, Any], raw: dict[str, Any] | None) -> bool:
    """Did the generated enum swallow a code the wire actually named?"""
    typed_code = _code_from_payload(typed)
    raw_code = _code_from_payload(raw)
    return typed_code == UNKNOWN_ENUM_MEMBER and raw_code not in (None, UNKNOWN_ENUM_MEMBER)


def _payload_from_exception(exc: ApiException) -> dict[str, Any] | None:
    """Best-effort JSON body: typed ``data`` first, then the raw ``body``.

    The typed payload is skipped when its enum widened the code to
    ``UNKNOWN`` while the raw body still names it. The generated enums resolve
    an unrecognized member to that sentinel instead of raising, so a typed
    ``data`` no longer proves the SDK knew the code it hands back — preferring
    it unconditionally would report every code this SDK predates as
    ``UNKNOWN``.
    """
    raw = _raw_payload(exc)
    to_dict = getattr(exc.data, "to_dict", None)
    if callable(to_dict):
        typed = to_dict()
        if isinstance(typed, dict) and not _widened_to_unknown(typed, raw):
            return typed
    return raw


@dataclass(frozen=True)
class RestReject:
    """One REST rejection, flattened to the fields the wire contract defines.

    ``code`` and ``retry_after_ms`` both come from the BODY, which is the whole
    contract: the status is 400 for every venue verdict and carries no retry
    information. ``retry_after_header`` is read only so an assertion can prove
    a ``Retry-After`` header is ABSENT — the venue does not use one, and a
    deployment that grew one would be advertising a second, unspecified hint.
    """

    status: int | None
    code: str | None
    message: str | None
    retry_after_header: str | None
    retry_after_ms: int | None
    payload: dict[str, Any] | None

    @property
    def retry_after_s(self) -> float | None:
        """The body's ``retryAfterMs`` in seconds, or ``None`` when absent.

        Callers sleep on this, so ``None`` must mean "the rejection carried no
        hint" and never read as 0 — a zero-length backoff would retry straight
        back into the same verdict.
        """
        return None if self.retry_after_ms is None else self.retry_after_ms / 1000

    def describe(self) -> str:
        return (
            f"status={self.status} code={self.code!r} retryAfterMs={self.retry_after_ms!r} "
            f"retryAfterHeader={self.retry_after_header!r} message={self.message!r}"
        )


def rest_reject(exc: ApiException) -> RestReject:
    """Flatten an ``ApiException`` into a :class:`RestReject`."""
    payload = _payload_from_exception(exc)
    message = payload.get("message") if payload else None
    retry_after_ms = None
    if payload:
        retry_after_ms = _as_int(payload.get("retryAfterMs"))
        if retry_after_ms is None:
            retry_after_ms = _as_int(payload.get("retry_after_ms"))

    return RestReject(
        status=exc.status,
        code=_code_from_payload(payload),
        message=str(message) if message is not None else None,
        retry_after_header=_header(exc.headers, "Retry-After"),
        retry_after_ms=retry_after_ms,
        payload=payload,
    )


async def capture_rest_reject(awaitable: Any, label: str) -> RestReject:
    """Await ``awaitable``, requiring it to fail with an ``ApiException``.

    Returns the flattened reject. Fails the test (via ``AssertionError``) when
    the call unexpectedly succeeds, so a silently-accepted order can never read
    as a passing rate-limit assertion.
    """
    try:
        result = await awaitable
    except ApiException as exc:
        return rest_reject(exc)
    raise AssertionError(f"[{label}] expected a rejection, got a successful response: {result!r}")


@dataclass(frozen=True)
class WsReject:
    """One ws-exec per-operation error envelope: ``{ok:false, error:{...}}``."""

    code: str | None
    message: str | None
    retry_after_ms: int | None

    def describe(self) -> str:
        return f"code={self.code!r} retryAfterMs={self.retry_after_ms!r} message={self.message!r}"


def ws_reject(frame: dict[str, Any], label: str) -> WsReject:
    """Flatten a ws-exec error envelope, asserting it IS an error envelope.

    Read straight off the frame rather than via ``WsExecOperationError``: the
    relayer's envelope is what these tests assert on, so nothing here depends on
    the client's parsing. (``WsExecOperationError`` does carry ``retry_after_ms``
    now — see the suite README's findings — but it is pinned separately.)
    """
    if frame.get("ok"):
        raise AssertionError(f"[{label}] expected ok=false, got payload={frame.get('payload')!r}")
    error = frame.get("error") or {}
    if not isinstance(error, dict):
        raise AssertionError(f"[{label}] malformed error envelope: {frame!r}")
    retry_after_ms = _as_int(error.get("retryAfterMs"))
    if retry_after_ms is None:
        retry_after_ms = _as_int(error.get("retry_after_ms"))
    message = error.get("message")
    return WsReject(
        code=str(error["error"]) if error.get("error") is not None else None,
        message=str(message) if message is not None else None,
        retry_after_ms=retry_after_ms,
    )


def msg_rate_retry_after_s(reason: str | None) -> float | None:
    """The ``retry_after_ms`` carried in a 4029 close reason, in seconds.

    ``None`` when the reason is absent or does not follow the grammar — the
    AsyncAPI descriptions call the reason advisory and tell clients to key
    recovery on the CODE and fall back to backoff-with-jitter, so an
    unparseable reason must read as "no hint", never as 0.
    """
    match = MSG_RATE_CLOSE_REASON_PATTERN.match(reason) if reason else None
    return int(match.group(1)) / 1000 if match else None


def assert_msg_rate_close_reason(reason: str | None, max_s: float, label: str) -> float:
    """Assert a 4029 close reason follows the shared grammar; return its hint.

    The value is deployment-sized, so only the grammar and plausibility are
    pinned — never the number.
    """
    seconds = msg_rate_retry_after_s(reason)
    assert seconds is not None, (
        f"[{label}] the 4029 close must carry the backoff hint in its reason "
        f"(expected {MSG_RATE_CLOSE_REASON_PATTERN.pattern}); got {reason!r}"
    )
    assert 0 < seconds <= max_s, f"[{label}] implausible retry_after_ms in {reason!r} (expected 0 < x <= {max_s}s)"
    return seconds


def assert_venue_verdict(reject: RestReject, label: str) -> None:
    """Assert the rejection arrived as a venue verdict: HTTP 400 with a code.

    Every code in the enum is a 400, so this is the one status assertion the
    suite makes. It exists to catch a deployment that answers a venue verdict
    with 429 / 503 / 403 — a status a client would branch on before ever
    reading the code, and one that would hide the code from a generated SDK
    whose response map lists only 200 / 400 / 500.
    """
    assert reject.status == HTTP_VENUE_VERDICT, (
        f"[{label}] every venue verdict is HTTP {HTTP_VENUE_VERDICT} (the code is the contract, not the status); "
        f"got {reject.describe()}"
    )
    assert reject.code is not None, f"[{label}] a venue verdict must name its code in the body; got {reject.describe()}"


def assert_no_retry_hint(reject: RestReject, label: str) -> None:
    """Assert a rejection advertises NO wait — neither in the body nor a header.

    Two families land here and both mean "waiting changes nothing": the access
    decisions answer "not you", never "not yet", and the open-order caps clear
    only when resting orders are cancelled or filled. Advertising a backoff
    would invite a client to retry-loop against a verdict that will not move.

    The header half is not a second contract — the venue never sends
    ``Retry-After`` — it is there so a deployment that grew one is caught
    rather than quietly believed.
    """
    assert (
        reject.retry_after_ms is None
    ), f"[{label}] this reject must carry no retryAfterMs (waiting never clears it); got {reject.describe()}"
    assert reject.retry_after_header is None, (
        f"[{label}] the venue never answers with a Retry-After header; the hint is the body's retryAfterMs; "
        f"got {reject.describe()}"
    )


def assert_retry_after_plausible(reject: RestReject, max_s: float, label: str) -> float:
    """Assert the mandatory ``retryAfterMs`` hint is present and sane.

    Returns the hint in seconds so the caller can sleep exactly that long. The
    contract is a backoff FLOOR in milliseconds, so any positive value up to
    ``max_s`` passes; the test never re-derives GCRA arithmetic.

    The header is asserted ABSENT in the same breath: a rejection carrying both
    would leave a client two hints to reconcile, and only one of them is
    specified.
    """
    assert (
        reject.retry_after_ms is not None
    ), f"[{label}] a retryable verdict must carry retryAfterMs on the body; got {reject.describe()}"
    assert 0 < reject.retry_after_ms <= max_s * 1000, (
        f"[{label}] implausible retryAfterMs {reject.retry_after_ms} "
        f"(expected 0 < x <= {max_s * 1000}); {reject.describe()}"
    )
    assert (
        reject.retry_after_header is None
    ), f"[{label}] the hint travels on the body only, never a Retry-After header; got {reject.describe()}"

    seconds = reject.retry_after_s
    assert seconds is not None
    return seconds
