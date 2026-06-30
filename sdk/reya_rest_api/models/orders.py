from typing import Optional

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
    # Maker-only intent: the order must rest, never cross as a taker. Valid on
    # GTC; rejected on IOC (immediate-or-cancel is taker-only). Maps to on-chain
    # `OrderDetails.postOnly`. None == not requested (treated as False).
    post_only: Optional[bool] = None
    expires_after: Optional[int] = None
    client_order_id: Optional[int] = None
    deadline: Optional[int] = None


@dataclass(frozen=True)
class ModifyOrderParameters:
    """Parameters for modifying a resting order in place (spot or perp).

    Target by `order_id` or `client_order_id` (`client_order_id=0` is not a
    valid target). When targeting by `order_id`, pass `client_order_id` too if
    the resting order has a non-zero client id; omit it only when the resting
    order has none. The modifiable fields — `limit_px`, `qty`, `post_only`,
    `expires_after`, and `trigger_px` for trigger orders — carry the COMPLETE
    post-modify state (no omitted-means-inherited shorthand). `qty` is the TOTAL
    order quantity, not the remaining, and must exceed the filled amount.

    The EIP-712 signature covers the full post-modify state: the modifiable
    fields at their new values plus the immutables restated from the resting
    order — `is_buy` (quantity sign), `time_in_force` (the resting order's TIF),
    `reduce_only`, and the resting order's non-zero client id (via
    `client_order_id` or `resting_client_order_id`) signed into
    `OrderDetails.clientOrderId`. LIMIT modifies omit `trigger_px`; native SL/TP
    trigger repricing depends on matching-engine trigger-order support.
    """

    symbol: str
    is_buy: bool
    limit_px: str
    qty: str
    post_only: bool
    expires_after: Optional[int]
    time_in_force: TimeInForce
    order_id: Optional[int] = None
    client_order_id: Optional[int] = None
    trigger_px: Optional[str] = None
    reduce_only: bool = False
    resting_client_order_id: int = 0
    deadline: Optional[int] = None
    nonce: Optional[int] = None


@dataclass(frozen=True)
class TriggerOrderParameters:
    """Parameters for a STOP_LOSS or TAKE_PROFIT trigger order on a perp market.

    `qty` is the signed quantity to execute when the trigger fires — it must be
    set explicitly. There is no safe default: signing a smaller-than-expected qty
    silently produces a partial close, which can leave the user with the wrong
    risk after a stop hits.

    `limit_px` is the worst-acceptable execution price after the trigger fires;
    if omitted the client signs a sentinel — a very high value for buys, a very
    low non-zero value for sells — so the order executes at any price available
    after the trigger.
    """

    symbol: str
    is_buy: bool
    qty: str
    trigger_px: str
    trigger_type: OrderType
    limit_px: Optional[str] = None
    reduce_only: Optional[bool] = None
    client_order_id: Optional[int] = None
    deadline: Optional[int] = None
