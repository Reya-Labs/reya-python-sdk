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

from typing import Any

import json

import pytest

from sdk.open_api.api_client import ApiClient
from sdk.open_api.configuration import Configuration
from sdk.open_api.exceptions import ApiException, ForbiddenException, ServiceException
from sdk.open_api.models.request_error import RequestError
from sdk.open_api.models.request_error_code import RequestErrorCode
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
from tests.rate_limits.rl_errors import assert_retry_after_plausible, rest_reject, ws_reject

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
    """429 → status + Retry-After header + RATE_LIMITED_ERROR + retryAfterMs."""
    exc = _raise_through_sdk(
        HTTP_RATE_LIMITED,
        {"error": RATE_LIMITED_ERROR, "message": "too many creates", "retryAfterMs": 2500},
        headers={"Retry-After": "3"},
        response_types_map=response_types_map,
    )

    reject = rest_reject(exc)
    assert reject.status == HTTP_RATE_LIMITED
    assert reject.code == RATE_LIMITED_ERROR
    assert reject.message == "too many creates"
    assert reject.retry_after_ms == 2500
    assert assert_retry_after_plausible(reject, 60.0, "offline 429") == 3.0


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

    Three of them are not in ``RequestErrorCode`` today; this is what makes the
    live modules safe to write before the spec is tagged.
    """
    exc = _raise_through_sdk(HTTP_RATE_LIMITED, {"error": code, "message": "m", "retryAfterMs": 1})
    assert rest_reject(exc).code == code


def test_retry_after_assertion_is_not_vacuous() -> None:
    """A 429 with NO Retry-After must fail the assertion, not pass silently."""
    exc = _raise_through_sdk(HTTP_RATE_LIMITED, {"error": RATE_LIMITED_ERROR, "message": "no header"})
    with pytest.raises(AssertionError, match="must carry a Retry-After header"):
        assert_retry_after_plausible(rest_reject(exc), 60.0, "offline missing-header")


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
    """The ws-exec envelope carries the code and the optional ``retryAfterMs``.

    Read straight off the frame: ``WsExecOperationError`` only exposes
    ``code`` / ``message`` / ``request_id`` and drops ``retryAfterMs``.
    """
    frame = {
        "id": "abc123",
        "ok": False,
        "error": {"error": RATE_LIMITED_ERROR, "message": "slow down", "retryAfterMs": 750},
    }
    reject = ws_reject(frame, "offline ws envelope")
    assert reject.code == RATE_LIMITED_ERROR
    assert reject.retry_after_ms == 750

    forbidden = ws_reject(
        {"ok": False, "error": {"error": NOT_WHITELISTED_ERROR, "message": "nope"}}, "offline ws gate"
    )
    assert forbidden.code == NOT_WHITELISTED_ERROR
    assert forbidden.retry_after_ms is None


def test_ws_exec_ok_envelope_is_not_mistaken_for_a_reject() -> None:
    with pytest.raises(AssertionError, match="expected ok=false"):
        ws_reject({"ok": True, "payload": {"orderId": "1"}}, "offline ws ok")
