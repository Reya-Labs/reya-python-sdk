"""Enums the server can widen must not break the SDK that reads them.

Offline (no devnet). The matching engine allocates new ``CancelReason`` and
``RequestErrorCode`` members without an SDK release. Before the ``_missing_``
hook, the first frame carrying an unfamiliar member raised a pydantic
``ValidationError``, and neither transport surfaced it usefully:

- read-side market data turned it into a ``WebSocketDataError`` raised inside
  the on-message wrapper, which websocket-client catches and routes to
  ``on_error``; the default handler only logs, so the frame was SILENTLY
  DROPPED with the connection still up. The frame most likely to carry an
  unfamiliar reason is exactly the "your protection is gone, re-arm it"
  cancellation.
- ws-exec raised in the caller's ``await`` AFTER the server had already acted:
  order placed, response unrecoverable.

The hook is written by ``scripts/postprocess-openapi.py`` and
``scripts/postprocess-ws-models.py`` rather than by hand, so it survives the
next regeneration.

The order-entry vocabularies are the deliberate exception: ``OrderType`` and
``TimeInForce`` are what the CLIENT sends, so a value the SDK cannot encode has
to keep raising — ``sdk.reya_rest_api.client`` turns that into a named
``ValueError`` before a nonce is claimed.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import TypeAdapter

from sdk.async_api.cancel_reason import CancelReason as WsCancelReason
from sdk.async_api.execution_type import ExecutionType as WsExecutionType
from sdk.async_api.order_status import OrderStatus as WsOrderStatus
from sdk.async_exec_api.order_status import OrderStatus as WsExecOrderStatus
from sdk.async_exec_api.request_error_code import RequestErrorCode as WsExecRequestErrorCode
from sdk.async_exec_api.ws_exec_error_code import WsExecErrorCode
from sdk.open_api.models.account_type import AccountType
from sdk.open_api.models.cancel_reason import CancelReason
from sdk.open_api.models.execution_type import ExecutionType
from sdk.open_api.models.order_status import OrderStatus
from sdk.open_api.models.order_type import OrderType
from sdk.open_api.models.request_error_code import RequestErrorCode
from sdk.open_api.models.server_error_code import ServerErrorCode
from sdk.open_api.models.tier_type import TierType
from sdk.open_api.models.time_in_force import TimeInForce

pytestmark = pytest.mark.offline

UNKNOWN_WIRE_VALUE = "A_MEMBER_THIS_SDK_HAS_NEVER_SEEN"

SERVER_OWNED = [
    pytest.param(AccountType, id="open_api.AccountType"),
    pytest.param(CancelReason, id="open_api.CancelReason"),
    pytest.param(ExecutionType, id="open_api.ExecutionType"),
    pytest.param(OrderStatus, id="open_api.OrderStatus"),
    pytest.param(RequestErrorCode, id="open_api.RequestErrorCode"),
    pytest.param(ServerErrorCode, id="open_api.ServerErrorCode"),
    pytest.param(TierType, id="open_api.TierType"),
    pytest.param(WsCancelReason, id="async_api.CancelReason"),
    pytest.param(WsExecutionType, id="async_api.ExecutionType"),
    pytest.param(WsOrderStatus, id="async_api.OrderStatus"),
    pytest.param(WsExecOrderStatus, id="async_exec_api.OrderStatus"),
    pytest.param(WsExecRequestErrorCode, id="async_exec_api.RequestErrorCode"),
    pytest.param(WsExecErrorCode, id="async_exec_api.WsExecErrorCode"),
]

CLIENT_OWNED = [
    pytest.param(OrderType, id="open_api.OrderType"),
    pytest.param(TimeInForce, id="open_api.TimeInForce"),
]


@pytest.mark.parametrize("enum_type", SERVER_OWNED)
def test_a_member_the_sdk_has_never_seen_resolves_to_unknown(enum_type: Any) -> None:
    assert enum_type(UNKNOWN_WIRE_VALUE) is enum_type.UNKNOWN


@pytest.mark.parametrize("enum_type", SERVER_OWNED)
def test_an_unknown_member_survives_pydantic_validation(enum_type: Any) -> None:
    """The observed failure was a pydantic ``ValidationError``, not a bare call:
    every one of these enums is read through a generated model."""
    assert TypeAdapter(enum_type).validate_python(UNKNOWN_WIRE_VALUE) is enum_type.UNKNOWN


@pytest.mark.parametrize("enum_type", SERVER_OWNED)
def test_the_members_the_sdk_does_know_are_untouched(enum_type: Any) -> None:
    """Falsifiability: a hook that swallowed everything would pass the two tests
    above while destroying every branch a caller writes on these values."""
    known = [member for member in enum_type if member is not enum_type.UNKNOWN]
    assert known, f"{enum_type.__name__} has no members besides UNKNOWN"
    for member in known:
        assert enum_type(member.value) is member


@pytest.mark.parametrize("enum_type", CLIENT_OWNED)
def test_the_order_entry_vocabularies_still_fail_loudly(enum_type: Any) -> None:
    """The payload builders depend on this: a value the client cannot encode must
    raise so the refusal lands before the per-wallet nonce is claimed. Giving
    these an UNKNOWN member would sign an order type the caller never chose."""
    assert not hasattr(enum_type, "UNKNOWN")
    with pytest.raises(ValueError):
        enum_type(UNKNOWN_WIRE_VALUE)
