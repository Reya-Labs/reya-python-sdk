from __future__ import annotations
from typing import Any, Dict, Optional
from pydantic import model_serializer, model_validator, BaseModel, Field

class MarketSummary(BaseModel): 
  symbol: str = Field(description='''Trading symbol (e.g., BTCRUSDPERP, WETHRUSD)''')
  updated_at: int = Field(alias='''updatedAt''')
  oi_qty: str = Field(alias='''oiQty''')
  funding_rate: str = Field(alias='''fundingRate''')
  long_funding_value: str = Field(alias='''longFundingValue''')
  short_funding_value: str = Field(alias='''shortFundingValue''')
  volume24h: str = Field()
  px_change24h: Optional[str] = Field(default=None, alias='''pxChange24h''')
  mark_price: Optional[str] = Field(default=None, alias='''markPrice''')
  throttled_mid_price: Optional[str] = Field(default=None, alias='''throttledMidPrice''')
  prices_updated_at: Optional[int] = Field(default=None, alias='''pricesUpdatedAt''')
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
    known_object_properties = ['symbol', 'updated_at', 'oi_qty', 'funding_rate', 'long_funding_value', 'short_funding_value', 'volume24h', 'px_change24h', 'mark_price', 'throttled_mid_price', 'prices_updated_at', 'additional_properties']
    unknown_object_properties = [element for element in json_properties if element not in known_object_properties]
    # Ignore attempts that validate regular models, only when unknown input is used we add unwrap extensions
    if len(unknown_object_properties) == 0: 
      return data
  
    known_json_properties = ['symbol', 'updatedAt', 'oiQty', 'fundingRate', 'longFundingValue', 'shortFundingValue', 'volume24h', 'pxChange24h', 'markPrice', 'throttledMidPrice', 'pricesUpdatedAt', 'additionalProperties']
    additional_properties = data.get('additional_properties', {})
    for obj_key in unknown_object_properties:
      if not known_json_properties.__contains__(obj_key):
        additional_properties[obj_key] = data.pop(obj_key, None)
    data['additional_properties'] = additional_properties
    return data

