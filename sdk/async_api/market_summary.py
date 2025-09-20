from __future__ import annotations
from typing import Any, Dict, Optional
from pydantic import model_serializer, model_validator, BaseModel, Field

class MarketSummary(BaseModel): 
  symbol: str = Field(description='''Trading symbol (e.g., BTCRUSDPERP, ETHRUSD)''')
  updated_at: int = Field(alias='''updatedAt''')
  long_oi_qty: str = Field(alias='''longOiQty''')
  short_oi_qty: str = Field(alias='''shortOiQty''')
  oi_qty: str = Field(alias='''oiQty''')
  funding_rate: str = Field(alias='''fundingRate''')
  long_funding_value: str = Field(alias='''longFundingValue''')
  short_funding_value: str = Field(alias='''shortFundingValue''')
  funding_rate_velocity: str = Field(alias='''fundingRateVelocity''')
  volume24h: str = Field()
  px_change24h: Optional[str] = Field(default=None, alias='''pxChange24h''')
  throttled_oracle_price: Optional[str] = Field(default=None, alias='''throttledOraclePrice''')
  throttled_pool_price: Optional[str] = Field(default=None, alias='''throttledPoolPrice''')
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
    known_object_properties = ['symbol', 'updated_at', 'long_oi_qty', 'short_oi_qty', 'oi_qty', 'funding_rate', 'long_funding_value', 'short_funding_value', 'funding_rate_velocity', 'volume24h', 'px_change24h', 'throttled_oracle_price', 'throttled_pool_price', 'prices_updated_at', 'additional_properties']
    unknown_object_properties = [element for element in json_properties if element not in known_object_properties]
    # Ignore attempts that validate regular models, only when unknown input is used we add unwrap extensions
    if len(unknown_object_properties) == 0: 
      return data
  
    known_json_properties = ['symbol', 'updatedAt', 'longOiQty', 'shortOiQty', 'oiQty', 'fundingRate', 'longFundingValue', 'shortFundingValue', 'fundingRateVelocity', 'volume24h', 'pxChange24h', 'throttledOraclePrice', 'throttledPoolPrice', 'pricesUpdatedAt', 'additionalProperties']
    additional_properties = data.get('additional_properties', {})
    for obj_key in unknown_object_properties:
      if not known_json_properties.__contains__(obj_key):
        additional_properties[obj_key] = data.pop(obj_key, None)
    data['additional_properties'] = additional_properties
    return data

