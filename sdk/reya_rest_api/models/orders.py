from typing import Any, Optional

from dataclasses import dataclass

from sdk.open_api.models import time_in_force
from sdk.open_api.models.order_type import OrderType


@dataclass(frozen=True)
class LimitOrderParameters:
    """Limit order parameters."""

    symbol: str
    is_buy: bool
    limit_px: str
    qty: str
    time_in_force: time_in_force.TimeInForce
    reduce_only: Optional[bool] = None
    expires_after: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "is_buy": self.is_buy,
            "limit_px": self.limit_px,
            "qty": self.qty,
            "reduce_only": self.reduce_only,
            "expires_after": self.expires_after,
            "time_in_force": self.time_in_force,
        }


@dataclass(frozen=True)
class TriggerOrderParameters:
    """Trigger order parameters."""

    symbol: str
    is_buy: bool
    trigger_px: str
    trigger_type: OrderType

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "is_buy": self.is_buy,
            "trigger_px": self.trigger_px,
            "trigger_type": self.trigger_type,
        }
