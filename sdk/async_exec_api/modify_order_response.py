from __future__ import annotations
from typing import Any, Dict, Optional
from pydantic import model_serializer, model_validator, BaseModel, Field
from sdk.async_exec_api.order_status import OrderStatus
class ModifyOrderResponse(BaseModel): 
  status: OrderStatus = Field(description='''Order status''')
  exec_qty: Optional[str] = Field(default=None, alias='''execQty''')
  cum_qty: Optional[str] = Field(default=None, alias='''cumQty''')
  order_id: str = Field(description='''Modified order ID — unchanged by the modification.''', alias='''orderId''')
  client_order_id: Optional[int] = Field(default=None, alias='''clientOrderId''')
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
    known_object_properties = ['status', 'exec_qty', 'cum_qty', 'order_id', 'client_order_id', 'additional_properties']
    unknown_object_properties = [element for element in json_properties if element not in known_object_properties]
    # Ignore attempts that validate regular models, only when unknown input is used we add unwrap extensions
    if len(unknown_object_properties) == 0: 
      return data
  
    known_json_properties = ['status', 'execQty', 'cumQty', 'orderId', 'clientOrderId', 'additionalProperties']
    additional_properties = data.get('additional_properties', {})
    for obj_key in unknown_object_properties:
      if not known_json_properties.__contains__(obj_key):
        additional_properties[obj_key] = data.pop(obj_key, None)
    data['additional_properties'] = additional_properties
    return data

