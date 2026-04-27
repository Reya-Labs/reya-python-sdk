from typing import Any, Optional

from dataclasses import dataclass

from sdk.open_api.models.order_type import OrderType
from sdk.open_api.models.time_in_force import TimeInForce


@dataclass(frozen=True)
class LimitOrderParameters:
    """Parameters for a LIMIT order on either spot or perp markets."""

    symbol: str
    is_buy: bool
    limit_px: str
    qty: str
    time_in_force: TimeInForce
    reduce_only: Optional[bool] = None
    expires_after: Optional[int] = None
    client_order_id: Optional[int] = None
    deadline: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "is_buy": self.is_buy,
            "limit_px": self.limit_px,
            "qty": self.qty,
            "reduce_only": self.reduce_only,
            "expires_after": self.expires_after,
            "time_in_force": self.time_in_force,
            "client_order_id": self.client_order_id,
            "deadline": self.deadline,
        }


@dataclass(frozen=True)
class TriggerOrderParameters:
    """Parameters for a STOP_LOSS or TAKE_PROFIT trigger order on a perp market.

    `qty` is the signed quantity to execute when the trigger fires (defaults to
    "0.01"). `limit_px` is the worst-acceptable execution price after the trigger
    fires; if omitted the client signs a sentinel — a very high value for buys, a
    very low non-zero value for sells — so the order executes at any price
    available after the trigger.
    """

    symbol: str
    is_buy: bool
    trigger_px: str
    trigger_type: OrderType
    qty: str = "0.01"
    limit_px: Optional[str] = None
    reduce_only: Optional[bool] = None
    client_order_id: Optional[int] = None
    deadline: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "is_buy": self.is_buy,
            "qty": self.qty,
            "trigger_px": self.trigger_px,
            "limit_px": self.limit_px,
            "trigger_type": self.trigger_type,
            "reduce_only": self.reduce_only,
            "client_order_id": self.client_order_id,
            "deadline": self.deadline,
        }
