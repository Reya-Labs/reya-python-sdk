from __future__ import annotations
from typing import Any, Dict, Optional
from pydantic import model_serializer, model_validator, BaseModel, Field
from sdk.async_api.side import Side
from sdk.async_api.order_type import OrderType
from sdk.async_api.time_in_force import TimeInForce
from sdk.async_api.order_status import OrderStatus
class Order(BaseModel): 
  exchange_id: int = Field(alias='''exchangeId''')
  symbol: str = Field(description='''Trading symbol (e.g., BTCRUSDPERP, ETHRUSD)''')
  account_id: int = Field(alias='''accountId''')
  order_id: str = Field(alias='''orderId''')
  qty: Optional[str] = Field(default=None)
  exec_qty: Optional[str] = Field(default=None, alias='''execQty''')
  side: Side = Field(description='''Order side (B = Buy/Bid, A = Ask/Sell)''')
  limit_px: str = Field(alias='''limitPx''')
  order_type: OrderType = Field(description='''Order type, (LIMIT = Limit, TP = Take Profit, SL = Stop Loss)''', alias='''orderType''')
  trigger_px: Optional[str] = Field(default=None, alias='''triggerPx''')
  time_in_force: Optional[TimeInForce] = Field(description='''Order time in force (IOC = Immediate or Cancel, GTC = Good Till Cancel)''', default=None, alias='''timeInForce''')
  reduce_only: Optional[bool] = Field(description='''Whether this is a reduce-only order, exclusively used for LIMIT IOC orders.''', default=None, alias='''reduceOnly''')
  status: OrderStatus = Field(description='''Order status''')
  created_at: int = Field(alias='''createdAt''')
  last_update_at: int = Field(alias='''lastUpdateAt''')
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
    known_object_properties = ['exchange_id', 'symbol', 'account_id', 'order_id', 'qty', 'exec_qty', 'side', 'limit_px', 'order_type', 'trigger_px', 'time_in_force', 'reduce_only', 'status', 'created_at', 'last_update_at', 'additional_properties']
    unknown_object_properties = [element for element in json_properties if element not in known_object_properties]
    # Ignore attempts that validate regular models, only when unknown input is used we add unwrap extensions
    if len(unknown_object_properties) == 0: 
      return data
  
    known_json_properties = ['exchangeId', 'symbol', 'accountId', 'orderId', 'qty', 'execQty', 'side', 'limitPx', 'orderType', 'triggerPx', 'timeInForce', 'reduceOnly', 'status', 'createdAt', 'lastUpdateAt', 'additionalProperties']
    additional_properties = data.get('additional_properties', {})
    for obj_key in unknown_object_properties:
      if not known_json_properties.__contains__(obj_key):
        additional_properties[obj_key] = data.pop(obj_key, None)
    data['additional_properties'] = additional_properties
    return data

