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

    Target by `order_id` or `client_order_id`. When targeting by `order_id`,
    pass `client_order_id` too if the resting order has a non-zero client id;
    omit it when the resting order has none. The modifiable fields — `limit_px`, `qty`, `post_only`,
    `expires_after`, and `trigger_px` for trigger orders — carry the COMPLETE
    post-modify state (no omitted-means-inherited shorthand). `qty` is the TOTAL
    order quantity, not the remaining, and must exceed the filled amount.

    The EIP-712 signature covers the full post-modify state: the modifiable
    fields at their new values plus the immutables restated from the resting
    order — `is_buy` (quantity sign), `order_type` (the resting order's type),
    `time_in_force` (the resting order's TIF), `reduce_only`, and the resting
    order's client id (via `client_order_id`) signed into
    `OrderDetails.clientOrderId`. `order_type` defaults to LIMIT; a STOP_LOSS /
    TAKE_PROFIT modify reprices a trigger order and requires `trigger_px`. LIMIT
    modifies omit `trigger_px`.

    `qty` is REQUIRED for a LIMIT modify and FORBIDDEN for a STOP_LOSS /
    TAKE_PROFIT modify: a trigger modify signs the ±int256.max full-position
    sentinel (sign from `is_buy`; protect the whole position) and omits `qty`
    from the wire, exactly like a trigger create.

    On a trigger modify only `limit_px` and `trigger_px` move; `time_in_force`
    and `expires_after` are restated immutables, so changing the fired child's
    TIF or the shared expiry means cancelling the trigger and creating a new one.
    """

    symbol: str
    is_buy: bool
    limit_px: str
    qty: Optional[str]
    post_only: bool
    expires_after: Optional[int]
    time_in_force: TimeInForce
    order_id: Optional[int] = None
    client_order_id: Optional[int] = None
    trigger_px: Optional[str] = None
    reduce_only: bool = False
    deadline: Optional[int] = None
    nonce: Optional[int] = None
    order_type: OrderType = OrderType.LIMIT


@dataclass(frozen=True)
class TriggerOrderParameters:
    """Parameters for a STOP_LOSS or TAKE_PROFIT trigger order on a perp market.

    Trigger orders omit `qty` on the REST/WS JSON contract and sign
    `OrderDetails.quantity = ±int256.max` (the full-position sentinel; sign
    carries the close side per reya-network #738); executable size is derived
    from the live position when the trigger fires. The deprecated `qty` field
    is retained only so older callers receive a targeted client-side error
    instead of a server 400.

    `limit_px` is the worst-acceptable execution price of the child the trigger
    fires into — not a pad on `trigger_px`. It is REQUIRED: the venue enforces a
    per-market band, `|limit_px - trigger_px| <= trigger_px * band`, so there is
    no price that "always executes". The band is a matching-engine setting and
    is not published, so a limit outside it comes back as
    TRIGGER_LIMIT_OUTSIDE_BAND_ERROR rather than being caught locally.

    `time_in_force` is what the stop BECOMES when it fires: the fired child rests
    as GTC, rests-with-expiry as GTT, or takes-or-cancels as IOC. GTT requires a
    future `expires_after` — one deadline covering both the armed trigger's
    lifetime and the fired child's settlement — while GTC and IOC must omit it.

    `is_buy` is the side the stop CLOSES with, not the side of the position it
    protects — it is the sole input to the signed sentinel's sign, and the SDK
    never reads your position, so it cannot catch an inverted value. Protecting a
    LONG means selling to close (`is_buy=False`); protecting a SHORT means buying
    to close (`is_buy=True`). Together with `trigger_type` it also fixes the
    crossing direction (a trigger fires on a RISE exactly when `is_buy` and
    "is a STOP_LOSS" agree), so an inverted `is_buy` arms a stop that fires the
    wrong way AND closes the wrong way.
    """

    symbol: str
    is_buy: bool
    trigger_px: str
    trigger_type: OrderType
    limit_px: str
    time_in_force: TimeInForce
    expires_after: Optional[int] = None
    qty: Optional[str] = None
    reduce_only: Optional[bool] = None
    client_order_id: Optional[int] = None
    deadline: Optional[int] = None
