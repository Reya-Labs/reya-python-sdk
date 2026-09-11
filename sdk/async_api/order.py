from __future__ import annotations
from typing import Any, Dict, Optional
from pydantic import model_serializer, model_validator, BaseModel, Field
from sdk.async_api.side import Side
from sdk.async_api.order_type import OrderType
from sdk.async_api.time_in_force import TimeInForce
from sdk.async_api.order_status import OrderStatus
from sdk.async_api.cancel_reason import CancelReason
class Order(BaseModel): 
  exchange_id: int = Field(alias='''exchangeId''')
  symbol: str = Field(description='''Trading symbol (e.g., BTCRUSDPERP, WETHRUSD)''')
  account_id: int = Field(alias='''accountId''')
  order_id: str = Field(alias='''orderId''')
  sequence_number: Optional[int] = Field(default=None, alias='''sequenceNumber''')
  client_order_id: Optional[str] = Field(description='''Client-provided order ID, as a decimal string (`uint64`). Present when the order has a non-zero client id; omitted otherwise.''', default=None, alias='''clientOrderId''')
  qty: Optional[str] = Field(default=None)
  exec_qty: Optional[str] = Field(default=None, alias='''execQty''')
  cum_qty: Optional[str] = Field(default=None, alias='''cumQty''')
  first_fill_id: Optional[str] = Field(description='''Identifier of the first fill this update represents. Together with fillCount it identifies the fills as a contiguous ID range [firstFillId, firstFillId + fillCount - 1]. For a taker update, the first fill of its matching round; for a maker update, its single fill. Present only on fill updates; absent for non-fill updates and resting-order snapshots.''', default=None, alias='''firstFillId''')
  fill_count: Optional[int] = Field(default=None, alias='''fillCount''')
  side: Side = Field(description='''Order side (B = Buy/Bid, A = Ask/Sell)''')
  limit_px: str = Field(alias='''limitPx''')
  order_type: OrderType = Field(description='''Order type aligned with the on-chain `OrderDetails.orderType` enum: LIMIT = limit order, STOP_LOSS = stop-loss trigger order, TAKE_PROFIT = take-profit trigger order.''', alias='''orderType''')
  trigger_px: Optional[str] = Field(default=None, alias='''triggerPx''')
  triggered: Optional[bool] = Field(description='''For protective stops, false means armed and true means fired; never true for LIMIT orders. Fired children are cancel-only. Omitted on orderHistory, where absence means unknown: use openOrders or walletOrderChanges to distinguish phases. On those two surfaces, omission on older deployments can be treated as false. See POST /v2/createOrder for firing and child-order behavior.''', default=None)
  time_in_force: Optional[TimeInForce] = Field(description='''Order time in force (IOC = Immediate or Cancel, GTC = Good Till Cancel, GTT = Good Till Time)''', default=None, alias='''timeInForce''')
  expires_after: Optional[int] = Field(default=None, alias='''expiresAfter''')
  reduce_only: Optional[bool] = Field(description='''Whether this is a reduce-only order, exclusively used for LIMIT IOC orders. `STOP_LOSS` / `TAKE_PROFIT` orders and the children they fire into are reduce-only by construction — they close the position and can never open one — yet still report `false` here, because this field mirrors the signed on-chain `OrderDetails.reduceOnly`. Do not read `false` on a stop as permission to open a position.''', default=None, alias='''reduceOnly''')
  post_only: Optional[bool] = Field(description='''Whether this is a post-only (maker-only) order. Mirrors `CreateOrderRequest.postOnly`; updated by `modifyOrder`.''', default=None, alias='''postOnly''')
  status: OrderStatus = Field(description='''OPEN includes partially filled resting orders, armed protective stops, and their resting children. FILLED and CANCELLED are terminal states. Use Order.triggered to distinguish armed stops from fired children. Requests rejected before order creation return errors, not order-status rows.''')
  created_at: int = Field(alias='''createdAt''')
  last_update_at: int = Field(alias='''lastUpdateAt''')
  cancel_reason: Optional[CancelReason] = Field(description='''Machine-readable cancellation reason. Present only on CANCELLED orders and may be omitted when unavailable. See Cancellation reasons in the REST API Order Entry section for per-code meanings and handling.''', default=None, alias='''cancelReason''')
  cancel_reason_message: Optional[str] = Field(description='''Human-readable explanation of `cancelReason`. Present only when `cancelReason` is present.''', default=None, alias='''cancelReasonMessage''')
  additional_properties: Optional[dict[str, Any]] = Field(default=None, exclude=True)

  @model_serializer(mode='wrap')
  def custom_serializer(self, handler):
    serialized_self = handler(self)
    additional_properties = getattr(self, "additional_properties")
    if additional_properties is not None:
      for key, value in additional_properties.items():
        # Never overwrite existing values, to avoid clashes
        if not key in serialized_self:
          serialized_self[key] = value

    return serialized_self

  @model_validator(mode='before')
  @classmethod
  def unwrap_additional_properties(cls, data):
    if not isinstance(data, dict):
      data = data.model_dump()
    json_properties = list(data.keys())
    known_object_properties = ['exchange_id', 'symbol', 'account_id', 'order_id', 'sequence_number', 'client_order_id', 'qty', 'exec_qty', 'cum_qty', 'first_fill_id', 'fill_count', 'side', 'limit_px', 'order_type', 'trigger_px', 'triggered', 'time_in_force', 'expires_after', 'reduce_only', 'post_only', 'status', 'created_at', 'last_update_at', 'cancel_reason', 'cancel_reason_message', 'additional_properties']
    unknown_object_properties = [element for element in json_properties if element not in known_object_properties]
    # Ignore attempts that validate regular models, only when unknown input is used we add unwrap extensions
    if len(unknown_object_properties) == 0: 
      return data
  
    known_json_properties = ['exchangeId', 'symbol', 'accountId', 'orderId', 'sequenceNumber', 'clientOrderId', 'qty', 'execQty', 'cumQty', 'firstFillId', 'fillCount', 'side', 'limitPx', 'orderType', 'triggerPx', 'triggered', 'timeInForce', 'expiresAfter', 'reduceOnly', 'postOnly', 'status', 'createdAt', 'lastUpdateAt', 'cancelReason', 'cancelReasonMessage', 'additionalProperties']
    additional_properties = data.get('additional_properties', {})
    for obj_key in unknown_object_properties:
      if not known_json_properties.__contains__(obj_key):
        additional_properties[obj_key] = data.pop(obj_key, None)
    data['additional_properties'] = additional_properties
    return data

