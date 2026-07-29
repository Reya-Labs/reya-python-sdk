"""Order-status normalisation and the terminal-state error the waiters raise.

Three generated `OrderStatus` enums exist — `sdk.open_api.models.order_status`
(a `str` enum, what the tests and REST models use), `sdk.async_api.order_status`
(a plain `Enum`, what the websocket order payload deserialises into), and
`sdk.async_exec_api.order_status`. They carry the same members but are distinct
types, so `open_api.OrderStatus.OPEN == async_api.OrderStatus.OPEN` is False.
Any comparison that crosses them evaluates false instead of failing, which
mislabels an outcome rather than reporting one. `order_status_value` collapses
all of them onto the wire value — the only representation the surfaces share.
"""

from typing import Optional, Union

from enum import Enum

from sdk.async_api.cancel_reason import CancelReason as AsyncCancelReason
from sdk.async_api.order_status import OrderStatus as AsyncOrderStatus
from sdk.open_api.models.cancel_reason import CancelReason
from sdk.open_api.models.order_status import OrderStatus

OrderStatusLike = Union[OrderStatus, AsyncOrderStatus, str]
CancelReasonLike = Union[CancelReason, AsyncCancelReason, str]


def order_status_value(status: OrderStatusLike) -> str:
    """Return the wire value of an order status from any of the enums, or a string."""
    if isinstance(status, Enum):
        value = status.value
        if not isinstance(value, str):  # pragma: no cover - the generator would have to change
            raise TypeError(f"order status enum {status!r} does not carry a string value")
        return value
    if isinstance(status, str):
        return status
    raise TypeError(f"not an order status: {status!r}")


def cancel_reason_of(order: object) -> tuple[Optional[str], Optional[str]]:
    """Read `(cancelReason, cancelReasonMessage)` off an order payload.

    Both are typed optional fields on the REST and websocket `Order` models,
    present only on a genuine cancellation.
    """
    reason = getattr(order, "cancel_reason", None)
    message = getattr(order, "cancel_reason_message", None)
    return (reason.value if isinstance(reason, Enum) else reason, message)


class OrderTerminalStateError(RuntimeError):
    """An order settled in a terminal state other than the awaited one.

    Subclasses `RuntimeError` so every existing waiter call site keeps the
    exception type it already handles. The attributes exist so the cause is
    available without parsing the message: a reaped GTT, a mass-cancelled
    order and one killed by self-trade prevention all surface as `CANCELLED`,
    and `cancel_reason` is the only thing that separates them.
    """

    def __init__(
        self,
        order_id: str,
        observed_status: OrderStatusLike,
        expected_status: OrderStatusLike,
        cancel_reason: Optional[CancelReasonLike] = None,
        cancel_reason_message: Optional[str] = None,
    ) -> None:
        self.order_id = order_id
        self.observed_status = order_status_value(observed_status)
        self.expected_status = order_status_value(expected_status)
        self.cancel_reason = cancel_reason.value if isinstance(cancel_reason, Enum) else cancel_reason
        self.cancel_reason_message = cancel_reason_message

        message = (
            f"Order {order_id} reached {self.observed_status} state via WS, " f"but expected {self.expected_status}"
        )
        if self.cancel_reason:
            message += f" (cancelReason={self.cancel_reason}"
            if cancel_reason_message:
                message += f": {cancel_reason_message}"
            message += ")"
        super().__init__(message)
