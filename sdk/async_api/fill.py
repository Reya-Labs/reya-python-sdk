from __future__ import annotations
from typing import Any, Dict, Optional
from pydantic import model_serializer, model_validator, BaseModel, Field
from sdk.async_api.side import Side
class Fill(BaseModel): 
  exchange_id: int = Field(alias='''exchangeId''')
  symbol: str = Field(description='''Trading symbol (e.g., BTCRUSDPERP, WETHRUSD)''')
  fill_id: str = Field(description='''Stable fill identifier. The settled perpExecutions or spotExecutions row and any executionBusts row for this fill carry the same `fillId`, and it falls inside the `[firstFillId, firstFillId + fillCount - 1]` range of the orderChanges updates that reported the fill.''', alias='''fillId''')
  taker_account_id: int = Field(alias='''takerAccountId''')
  maker_account_id: int = Field(alias='''makerAccountId''')
  taker_order_id: str = Field(description='''Order ID for the taker''', alias='''takerOrderId''')
  maker_order_id: str = Field(description='''Order ID for the maker''', alias='''makerOrderId''')
  side: Side = Field(description='''Order side (B = Buy/Bid, A = Ask/Sell)''')
  qty: str = Field()
  price: str = Field()
  taker_fee: Optional[str] = Field(default=None, alias='''takerFee''')
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
    known_object_properties = ['exchange_id', 'symbol', 'fill_id', 'taker_account_id', 'maker_account_id', 'taker_order_id', 'maker_order_id', 'side', 'qty', 'price', 'taker_fee', 'timestamp', 'additional_properties']
    unknown_object_properties = [element for element in json_properties if element not in known_object_properties]
    # Ignore attempts that validate regular models, only when unknown input is used we add unwrap extensions
    if len(unknown_object_properties) == 0: 
      return data
  
    known_json_properties = ['exchangeId', 'symbol', 'fillId', 'takerAccountId', 'makerAccountId', 'takerOrderId', 'makerOrderId', 'side', 'qty', 'price', 'takerFee', 'timestamp', 'additionalProperties']
    additional_properties = data.get('additional_properties', {})
    for obj_key in unknown_object_properties:
      if not known_json_properties.__contains__(obj_key):
        additional_properties[obj_key] = data.pop(obj_key, None)
    data['additional_properties'] = additional_properties
    return data

