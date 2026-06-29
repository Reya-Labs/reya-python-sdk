from __future__ import annotations
from typing import Any, Dict, Optional
from pydantic import model_serializer, model_validator, BaseModel, Field
from sdk.async_exec_api.order_status import OrderStatus
from sdk.async_exec_api.cancel_reason import CancelReason
class CreateOrderResponse(BaseModel): 
  status: OrderStatus = Field(description='''Order status''')
  exec_qty: Optional[str] = Field(default=None, alias='''execQty''')
  cum_qty: Optional[str] = Field(default=None, alias='''cumQty''')
  order_id: Optional[str] = Field(description='''Engine-assigned order ID, generated for all order types including IOC. A no-cross IOC still receives an ID and is returned with status CANCELLED (it never rests).''', default=None, alias='''orderId''')
  client_order_id: Optional[str] = Field(description='''Client-provided order ID echoed back from the request, as a decimal string (`uint64`).''', default=None, alias='''clientOrderId''')
  cancel_reason: Optional[CancelReason] = Field(description='''Why an order reached a terminal `CANCELLED` status. Present only when `status` is `CANCELLED`. `NO_LIQUIDITY` (IOC found no resting liquidity) and `IOC_REMAINDER` (IOC partially filled, remainder cancelled) are returned on `createOrder` responses; `GTT_EXPIRED`, `USER_CANCEL`, `MASS_CANCEL` and `CANCEL_ALL_AFTER` are delivered on the `walletOrderChanges` stream. `SELF_TRADE_PREVENTION` (the order would have matched your own resting order) appears on **both**: on the response when a self-crossing IOC or modify cancels the taker, and on the stream when a resting order is cancelled by an incoming self-cross.''', default=None, alias='''cancelReason''')
  cancel_reason_message: Optional[str] = Field(description='''Human-readable explanation of `cancelReason`. Present only when `cancelReason` is present.''', default=None, alias='''cancelReasonMessage''')
  first_fill_id: Optional[str] = Field(description='''Matching-engine fill nonce of the first fill this order produced on entry. Together with fillCount it identifies the fills as a contiguous nonce range [firstFillId, firstFillId + fillCount - 1]. Absent if the order did not fill on entry. For a non-IOC (resting) taker, the same first nonce also appears on the order's taker update in the orderChanges channel; an IOC taker is not published to orderChanges, so this response is the only place its fill range is delivered.''', default=None, alias='''firstFillId''')
  fill_count: Optional[int] = Field(default=None, alias='''fillCount''')
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
    known_object_properties = ['status', 'exec_qty', 'cum_qty', 'order_id', 'client_order_id', 'cancel_reason', 'cancel_reason_message', 'first_fill_id', 'fill_count', 'additional_properties']
    unknown_object_properties = [element for element in json_properties if element not in known_object_properties]
    # Ignore attempts that validate regular models, only when unknown input is used we add unwrap extensions
    if len(unknown_object_properties) == 0: 
      return data
  
    known_json_properties = ['status', 'execQty', 'cumQty', 'orderId', 'clientOrderId', 'cancelReason', 'cancelReasonMessage', 'firstFillId', 'fillCount', 'additionalProperties']
    additional_properties = data.get('additional_properties', {})
    for obj_key in unknown_object_properties:
      if not known_json_properties.__contains__(obj_key):
        additional_properties[obj_key] = data.pop(obj_key, None)
    data['additional_properties'] = additional_properties
    return data

