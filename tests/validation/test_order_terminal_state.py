"""The waiter's terminal-state error, and the order-status normalisation.

Offline. `for_order_state` used to collapse every unexpected outcome into a
`RuntimeError` carrying only the two status strings, so a cleanup order that a
risk gate cancelled and one the user cancelled produced the same message — and
the next test inherited an opaque failure from the previous one. The reason is
a typed field on both `Order` models; carrying it is what makes the failure
attributable at the point it happens.
"""

from __future__ import annotations

from typing import Any

import pytest

from sdk.async_api.cancel_reason import CancelReason as AsyncCancelReason
from sdk.async_api.order_status import OrderStatus as AsyncOrderStatus
from sdk.async_exec_api.order_status import OrderStatus as ExecOrderStatus
from sdk.open_api.models.cancel_reason import CancelReason
from sdk.open_api.models.order_status import OrderStatus
from tests.helpers.reya_tester.order_state import (
    OrderTerminalStateError,
    cancel_reason_of,
    order_status_value,
)

pytestmark = pytest.mark.offline


class _OrderPayload:
    """Minimal stand-in carrying only the fields the waiter reads."""

    def __init__(
        self,
        status: Any = None,
        cancel_reason: Any = None,
        cancel_reason_message: str | None = None,
    ) -> None:
        self.status = status
        self.cancel_reason = cancel_reason
        self.cancel_reason_message = cancel_reason_message


# --- status normalisation --------------------------------------------------


def test_the_order_status_enums_agree_on_values() -> None:
    """Distinct generated types, one value space.

    They are separate classes, so a comparison that crosses them evaluates
    false rather than failing — mislabelling an outcome instead of reporting
    one. Everything routes through `order_status_value`; this asserts the two
    value spaces cannot drift apart underneath it.
    """
    rest = {m.name: m.value for m in OrderStatus}
    assert {m.name: m.value for m in AsyncOrderStatus} == rest
    assert {m.name: m.value for m in ExecOrderStatus} == rest


def _equal_as_objects(left: object, right: object) -> bool:
    """Compare without the static types in view, which is how the runtime sees it."""
    return left == right


def test_the_enums_are_not_interchangeable_by_identity() -> None:
    """Why the normalisation exists, stated as a test rather than a comment.

    mypy rejects the crossed comparison outright as non-overlapping — the same
    fact asserted here at runtime. It only stays silent where the types are not
    statically visible, and that is exactly where a crossed comparison
    evaluates false instead of failing.
    """
    assert OrderStatus.__module__ != AsyncOrderStatus.__module__
    assert not _equal_as_objects(OrderStatus.OPEN, AsyncOrderStatus.OPEN)
    assert not _equal_as_objects(OrderStatus.OPEN, ExecOrderStatus.OPEN)
    assert order_status_value(ExecOrderStatus.OPEN) == order_status_value(OrderStatus.OPEN)


def test_order_status_value_accepts_either_enum_and_a_string() -> None:
    assert order_status_value(OrderStatus.CANCELLED) == "CANCELLED"
    assert order_status_value(AsyncOrderStatus.CANCELLED) == "CANCELLED"
    assert order_status_value("CANCELLED") == "CANCELLED"


def test_order_status_value_rejects_a_non_status() -> None:
    with pytest.raises(TypeError):
        order_status_value(object())  # type: ignore[arg-type]


# --- cancel reason ---------------------------------------------------------


def test_cancel_reason_read_off_a_websocket_order() -> None:
    reason, message = cancel_reason_of(
        _OrderPayload(
            status=AsyncOrderStatus.CANCELLED,
            cancel_reason=AsyncCancelReason.GTT_EXPIRED,
            cancel_reason_message="Order cancelled because its good-till-time expiry was reached.",
        )
    )
    assert reason == "GTT_EXPIRED"
    assert message is not None and "good-till-time" in message


def test_cancel_reason_read_off_a_rest_order() -> None:
    reason, _ = cancel_reason_of(_OrderPayload(status=OrderStatus.CANCELLED, cancel_reason=CancelReason.MASS_CANCEL))
    assert reason == "MASS_CANCEL"


def test_cancel_reason_absent_on_a_non_cancel() -> None:
    """Both fields are present only on a genuine cancellation."""
    reason, message = cancel_reason_of(_OrderPayload(status=AsyncOrderStatus.FILLED))
    assert reason is None and message is None


def test_every_cancel_reason_survives_normalisation() -> None:
    """No reason the API can emit is dropped on the way into the error.

    Both enums, because the waiter reads a websocket order (async) while the
    tests name the REST members.
    """
    assert {m.name: m.value for m in AsyncCancelReason} == {m.name: m.value for m in CancelReason}
    for member in (*CancelReason, *AsyncCancelReason):
        reason, _ = cancel_reason_of(_OrderPayload(cancel_reason=member))
        assert reason == member.value


# --- the error itself ------------------------------------------------------


def test_terminal_state_error_is_a_runtime_error() -> None:
    """Every existing waiter call site handles RuntimeError; the subclass keeps
    them working without an audit of all of them."""
    assert isinstance(OrderTerminalStateError("42", OrderStatus.CANCELLED, OrderStatus.FILLED), RuntimeError)


def test_terminal_state_error_carries_the_observed_state() -> None:
    error = OrderTerminalStateError("42", AsyncOrderStatus.CANCELLED, OrderStatus.FILLED)
    assert error.order_id == "42"
    assert error.observed_status == "CANCELLED"
    assert error.expected_status == "FILLED"
    assert "CANCELLED" in str(error) and "FILLED" in str(error)


def test_terminal_state_error_surfaces_the_cancel_reason() -> None:
    error = OrderTerminalStateError(
        "42",
        OrderStatus.CANCELLED,
        OrderStatus.FILLED,
        cancel_reason=CancelReason.SELF_TRADE_PREVENTION,
        cancel_reason_message="Order cancelled to prevent matching your own resting order.",
    )
    assert error.cancel_reason == "SELF_TRADE_PREVENTION"
    assert "SELF_TRADE_PREVENTION" in str(error)
    assert "your own resting order" in str(error)


def test_terminal_state_error_without_a_reason_reads_cleanly() -> None:
    """A FILLED-instead-of-CANCELLED outcome has no reason to report; the
    message must not imply one is missing."""
    error = OrderTerminalStateError("42", OrderStatus.FILLED, OrderStatus.CANCELLED)
    assert error.cancel_reason is None
    assert "cancelReason" not in str(error)


def test_two_cancellations_with_different_reasons_are_distinguishable() -> None:
    """The failure this error exists to prevent: two cancels that used to
    produce identical messages."""
    reaped = OrderTerminalStateError(
        "1", OrderStatus.CANCELLED, OrderStatus.FILLED, cancel_reason=CancelReason.GTT_EXPIRED
    )
    swept = OrderTerminalStateError(
        "1", OrderStatus.CANCELLED, OrderStatus.FILLED, cancel_reason=CancelReason.CANCEL_ALL_AFTER
    )
    assert str(reaped) != str(swept)
    assert reaped.cancel_reason != swept.cancel_reason
