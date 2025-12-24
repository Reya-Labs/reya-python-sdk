from __future__ import annotations
from typing import Any, Dict, Optional
from pydantic import model_serializer, model_validator, BaseModel, Field
from sdk.async_api.side import Side
from sdk.async_api.execution_type import ExecutionType
class SpotExecution(BaseModel): 
  exchange_id: Optional[int] = Field(default=None, alias='''exchangeId''')
  symbol: str = Field(description='''Trading symbol (e.g., BTCRUSDPERP, WETHRUSD)''')
  account_id: int = Field(alias='''accountId''')
  maker_account_id: int = Field(alias='''makerAccountId''')
  order_id: Optional[str] = Field(description='''Order ID for the taker''', default=None, alias='''orderId''')
  maker_order_id: Optional[str] = Field(description='''Order ID for the maker''', default=None, alias='''makerOrderId''')
  side: Side = Field(description='''Order side (B = Buy/Bid, A = Ask/Sell)''')
  qty: str = Field()
  price: str = Field()
  fee: str = Field()
  type: ExecutionType = Field(description='''Type of execution''')
  timestamp: int = Field()
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
    known_object_properties = ['exchange_id', 'symbol', 'account_id', 'maker_account_id', 'order_id', 'maker_order_id', 'side', 'qty', 'price', 'fee', 'type', 'timestamp', 'additional_properties']
    unknown_object_properties = [element for element in json_properties if element not in known_object_properties]
    # Ignore attempts that validate regular models, only when unknown input is used we add unwrap extensions
    if len(unknown_object_properties) == 0: 
      return data
  
    known_json_properties = ['exchangeId', 'symbol', 'accountId', 'makerAccountId', 'orderId', 'makerOrderId', 'side', 'qty', 'price', 'fee', 'type', 'timestamp', 'additionalProperties']
    additional_properties = data.get('additional_properties', {})
    for obj_key in unknown_object_properties:
      if not known_json_properties.__contains__(obj_key):
        additional_properties[obj_key] = data.pop(obj_key, None)
    data['additional_properties'] = additional_properties
    return data

