from typing import Any, Optional

from dataclasses import dataclass

from reya_v2_api.models import time_in_force
from reya_v2_api.models.order_type import OrderType


@dataclass(frozen=True)
class LimitOrderParameters:
    """Limit order parameters."""

    symbol: str
    market_id: int
    is_buy: bool
    price: str
    qty: str
    time_in_force: time_in_force.TimeInForce
    reduce_only: Optional[bool] = None
    expires_after: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "market_id": self.market_id,
            "is_buy": self.is_buy,
            "price": self.price,
            "qty": self.qty,
            "reduce_only": self.reduce_only,
            "expires_after": self.expires_after,
            "time_in_force": self.time_in_force,
        }


@dataclass(frozen=True)
class TriggerOrderParameters:
    """Trigger order parameters."""

    symbol: str
    market_id: int
    is_buy: bool
    trigger_price: str
    trigger_type: OrderType

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "market_id": self.market_id,
            "is_buy": self.is_buy,
            "trigger_price": self.trigger_price,
            "trigger_type": self.trigger_type,
        }
