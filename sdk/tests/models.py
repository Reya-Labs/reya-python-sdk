from typing import Any

from dataclasses import dataclass

from sdk.open_api.models.order_status import OrderStatus
from sdk.open_api.models.order_type import OrderType


@dataclass(frozen=True)
class OrderDetails:
    """Order parameters."""

    account_id: int
    symbol: str
    is_buy: bool
    price: str
    order_type: OrderType
    trigger_price: str | None = None
    status: OrderStatus = OrderStatus.OPEN
    qty: str = "0"  # default for SLTP orders

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "symbol": self.symbol,
            "is_buy": self.is_buy,
            "price": self.price,
            "qty": self.qty,
            "trigger_price": self.trigger_price,
            "status": self.status,
            "order_type": self.order_type,
        }
