from __future__ import annotations
from typing import Any, Dict, Optional
from pydantic import model_serializer, model_validator, BaseModel, Field
from sdk.async_exec_api.order_status import OrderStatus
from sdk.async_exec_api.cancel_reason import CancelReason
class CreateOrderResponse(BaseModel): 
  status: OrderStatus = Field(description='''OPEN includes partially filled resting orders, armed protective stops, and their resting children. FILLED and CANCELLED are terminal states. Use Order.triggered to distinguish armed stops from fired children. Requests rejected before order creation return errors, not order-status rows.''')
  exec_qty: Optional[str] = Field(default=None, alias='''execQty''')
  cum_qty: Optional[str] = Field(default=None, alias='''cumQty''')
  order_id: str = Field(description='''Reya-assigned order ID, generated for all order types including IOC. A no-cross IOC still receives an ID and is returned with status CANCELLED (it never rests).''', alias='''orderId''')
  client_order_id: Optional[str] = Field(description='''Client-provided order ID echoed back from the request, as a decimal string (`uint64`).''', default=None, alias='''clientOrderId''')
  cancel_reason: Optional[CancelReason] = Field(description='''Machine-readable cancellation reason. Present only on CANCELLED orders and may be omitted when unavailable. See Cancellation reasons in the REST API Order Entry section for per-code meanings and handling.''', default=None, alias='''cancelReason''')
  cancel_reason_message: Optional[str] = Field(description='''Human-readable explanation of `cancelReason`. Present only when `cancelReason` is present.''', default=None, alias='''cancelReasonMessage''')
  first_fill_id: Optional[str] = Field(description='''First fill ID produced by this request. With fillCount, identifies [firstFillId, firstFillId + fillCount - 1]. Omitted if no fill occurred. See POST /v2/createOrder, Responses and fill correlation, for joining responses to streamed executions.''', default=None, alias='''firstFillId''')
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

