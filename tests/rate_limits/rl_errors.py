"""Model-agnostic extraction of rate-limit rejects from REST and ws-exec.

Why raw dicts instead of the generated models:

* ``RequestErrorCode`` (sdk/open_api/models/request_error_code.py) is generated
  from the CURRENT tagged spec, which predates the Rate-Limit v1 codes. Parsing
  a ``NOT_WHITELISTED_ERROR`` body through ``RequestError`` raises a pydantic
  ``ValidationError`` today, so nothing typed can assert on it.
* ``RequestError`` has no ``retryAfterMs`` field yet. It DOES carry an
  ``additional_properties`` bag, so the value survives a ``from_dict`` round
  trip once the enum knows the code — but reading the raw payload works both
  before and after regeneration.
* The generated ``_response_types_map`` for the order-entry endpoints only
  lists ``200`` / ``400`` / ``500``, so a 429 / 503 / 403 response is never
  deserialized: ``ApiException.data`` is ``None`` and the JSON only survives on
  ``ApiException.body``.

TODO(post-regen): once the Rate-Limit v1 spec is tagged and the SDK models are
regenerated, tighten these helpers to prefer ``ApiException.data`` (a typed
``RequestError`` with ``retry_after_ms``) and keep the raw-dict path only as
the fallback.
"""

from __future__ import annotations

from typing import Any

import json
from dataclasses import dataclass

from sdk.open_api.exceptions import ApiException


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


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _payload_from_exception(exc: ApiException) -> dict[str, Any] | None:
    """Best-effort JSON body: typed ``data`` first, then the raw ``body``."""
    to_dict = getattr(exc.data, "to_dict", None)
    if callable(to_dict):
        typed = to_dict()
        if isinstance(typed, dict):
            return typed

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


@dataclass(frozen=True)
class RestReject:
    """One REST rejection, flattened to the fields the wire contract defines.

    ``retry_after_ms`` is a **defensive fallback, not a REST contract**: on REST
    the backoff hint travels in the ``Retry-After`` header only, and the edge
    builds the error body as ``{error, message}``. The body is read anyway so a
    deployment that starts echoing the ws-exec field is not silently ignored —
    but no REST assertion may require it.
    """

    status: int | None
    code: str | None
    message: str | None
    retry_after_header: str | None
    retry_after_ms: int | None
    payload: dict[str, Any] | None

    @property
    def retry_after_s(self) -> float | None:
        """``Retry-After`` in seconds, or ``None`` when absent/non-numeric.

        The contract specifies delta-seconds; an HTTP-date form would parse as
        ``None`` here and fail the caller's assertion loudly rather than
        silently reading as 0.
        """
        return _as_float(self.retry_after_header)

    def describe(self) -> str:
        return (
            f"status={self.status} code={self.code!r} retryAfter={self.retry_after_header!r} "
            f"retryAfterMs={self.retry_after_ms!r} message={self.message!r}"
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


def assert_no_retry_after(reject: RestReject, label: str) -> None:
    """Assert an access-control 403 carries NO ``Retry-After``.

    A 403 answers "not you", never "not yet": there is no wait that clears a
    whitelist or eject verdict, so advertising a backoff would invite a client
    to retry-loop against a decision that will not change.
    """
    assert reject.retry_after_header is None, (
        f"[{label}] a 403 access-control reject must carry no Retry-After (waiting never clears it); "
        f"got {reject.describe()}"
    )


def assert_retry_after_plausible(reject: RestReject, max_s: float, label: str) -> float:
    """Assert the mandatory 429 ``Retry-After`` is present and sane.

    Returns the parsed seconds so the caller can sleep exactly that long. The
    contract is a backoff FLOOR expressed in ceil-ed seconds, so any positive
    value up to ``max_s`` passes; the test never re-derives GCRA arithmetic.
    """
    assert (
        reject.retry_after_header is not None
    ), f"[{label}] 429 must carry a Retry-After header; got {reject.describe()}"
    seconds = reject.retry_after_s
    assert seconds is not None, f"[{label}] Retry-After must be delta-seconds; got {reject.retry_after_header!r}"
    assert (
        0 < seconds <= max_s
    ), f"[{label}] implausible Retry-After {seconds}s (expected 0 < x <= {max_s}); {reject.describe()}"

    if reject.retry_after_ms is not None:
        assert (
            0 < reject.retry_after_ms <= max_s * 1000
        ), f"[{label}] implausible retryAfterMs {reject.retry_after_ms}; {reject.describe()}"

    return seconds
